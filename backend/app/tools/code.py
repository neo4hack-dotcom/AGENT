"""Running Python the model wrote, with real ceilings.

An agent that can compute is a different class of tool from one that can only talk, but
"exec whatever the model produced" is not something to hand-wave. Three things hold here:

* a **separate process**, so a crash, a segfault or an `os._exit` kills only the child;
* a **wall-clock watchdog that kills the process group**, because the CPU rlimit alone
  does not stop a child that is asleep on a socket, and on macOS `RLIMIT_AS` is silently
  ineffective — the limit that actually holds on this platform is the watchdog (on
  Windows, a job object carries the ceilings and the kill: see winjob.py);
* a **working directory pinned to the workspace**, so relative paths land somewhere
  bounded and inspectable rather than wherever the API happens to have been started.

This is a guard against runaway and accident, not a security sandbox: code that runs here
runs with the same rights as the Agent process. That is stated plainly in the admin UI,
and the whole tool can be switched off there.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

try:
    import resource   # POSIX only: CPU, memory and process-count ceilings
except ImportError:   # Windows — a job object holds those ceilings there (winjob.py)
    resource = None

WINDOWS = sys.platform == "win32"

PRELUDE = (
    "import sys, os, json, math, statistics, re, datetime, itertools, collections, pathlib\n"
)

# When the parent offers a bridge, the child gets the other tools as ordinary functions.
# This is the mechanism that turns a five-round-trip plan into a single generation: the
# model writes the whole procedure — fetch, filter, join, write — and the loop between the
# steps runs in the interpreter instead of through the model. See tools/bridge_prelude.py.
BRIDGE_PRELUDE = (Path(__file__).parent / "bridge_prelude.py").read_text(encoding="utf-8")


def _wrappers(names: list[str]) -> str:
    """One named function per tool, so the model writes `sqlite__read_query(query=...)`, not a string."""
    return "\n".join(
        f"def {name}(**kw):\n    return call_tool({name!r}, **kw)\n"
        for name in names if name.isidentifier())

SANDBOX = "/usr/bin/sandbox-exec"

# Where there is no kernel sandbox (Windows, Linux), the child refuses non-local sockets
# itself. Not a security boundary — code can undo it — but the difference between an
# air-gapped deployment and one where `pd.read_csv("https://…")` quietly reaches out.
NET_GUARD = """import socket as _agent_socket
_agent_orig = {_n: getattr(_agent_socket.socket, _n) for _n in ("connect", "connect_ex", "sendto")}
_agent_orig_gai = _agent_socket.getaddrinfo
def _agent_local(address):
    host = address[0] if isinstance(address, tuple) else address
    return str(host).split("%")[0].lower() in ("127.0.0.1", "::1", "localhost")
def _agent_refuse(address):
    raise PermissionError("Network access is disabled for code the agent runs (%r): read data "
                          "through the connected sources' tools." % (address,))
def _agent_connect(self, address, *rest):
    if not _agent_local(address): _agent_refuse(address)
    return _agent_orig["connect"](self, address, *rest)
def _agent_connect_ex(self, address, *rest):
    if not _agent_local(address): _agent_refuse(address)
    return _agent_orig["connect_ex"](self, address, *rest)
def _agent_sendto(self, data, *rest):
    if rest and not _agent_local(rest[-1]): _agent_refuse(rest[-1])
    return _agent_orig["sendto"](self, data, *rest)
def _agent_getaddrinfo(host, *rest, **kw):
    if host not in (None, "localhost", "127.0.0.1", "::1"): _agent_refuse(host)
    return _agent_orig_gai(host, *rest, **kw)
_agent_socket.socket.connect = _agent_connect
_agent_socket.socket.connect_ex = _agent_connect_ex
_agent_socket.socket.sendto = _agent_sendto
_agent_socket.getaddrinfo = _agent_getaddrinfo
"""

# Windows reaches the network through file paths too: `open(r"\\host\share\x")` goes out over
# SMB (or WebDAV) with the user's credentials, and never touches a Python socket. Same
# standing as the socket guard — against accident, not against code set on getting out.
UNC_GUARD = r"""import builtins as _agent_builtins, io as _agent_io, os as _agent_os
def _agent_path_check(target):
    try:
        text = _agent_os.fspath(target)
    except TypeError:
        return
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    if text[:2] in ("\\\\", "//", "\\/", "/\\"):
        raise PermissionError("Network paths are refused for code the agent runs (%r): read data "
                              "through the connected sources' tools." % (text[:80],))
def _agent_guarded(fn):
    def wrapped(target, *rest, **kw):
        _agent_path_check(target)
        return fn(target, *rest, **kw)
    return wrapped
_agent_builtins.open = _agent_io.open = _agent_guarded(_agent_io.open)
for _agent_name in ("open", "stat", "lstat", "listdir", "scandir", "mkdir", "makedirs", "remove",
                    "unlink", "rmdir", "startfile"):
    if hasattr(_agent_os, _agent_name):
        setattr(_agent_os, _agent_name, _agent_guarded(getattr(_agent_os, _agent_name)))
"""


def sandbox_available() -> bool:
    return sys.platform == "darwin" and Path(SANDBOX).exists()


def _profile(workspace: Path) -> str:
    """A Seatbelt profile: everything as before, minus the network and minus writes
    outside the workspace.

    `allow default` then subtractive denies, rather than an allow-list: an allow-list for
    a Python interpreter means enumerating every dylib, locale file and framework it
    touches, and the first one missed looks like a broken tool rather than a policy. The
    two things that must not happen are enumerable; everything else may proceed.

    The write allow-list is the workspace and the three device files a process needs to
    speak. Not the system temp directory: `TMPDIR` already points into the workspace, and
    leaving `/private/tmp` open was a hole wide enough to walk through — verified by
    walking through it.
    """
    root = str(workspace)
    # Reads are fenced too, for what the model must not reach around the sources: the
    # SQLite file behind a governed MCP server, the app's own store and `.env`, anyone's
    # home directory. Code reads the workspace, and the interpreter reads itself; data
    # arrives through the sources, where it is audited — never straight off the disk.
    # Last matching rule wins, so the re-allows come after the denies.
    readable = sorted({str(Path(p).resolve()) for p in (sys.prefix, sys.base_prefix, root)})
    allow_reads = " ".join(f'(subpath "{p}")' for p in readable)
    return f"""(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write*
    (subpath "{root}")
    (literal "/dev/null") (literal "/dev/stdout") (literal "/dev/stderr")
    (regex #"^/dev/tty"))
(deny file-read-data
    (subpath "/Users") (subpath "/private/tmp") (subpath "/tmp") (subpath "/Volumes")
    (subpath "/private/var/root") (subpath "/opt") (subpath "/srv") (subpath "/data"))
(allow file-read-data {allow_reads})
"""


def _limits(memory_mb: int, cpu_s: int):
    if resource is None:
        return None

    def apply() -> None:
        os.setsid()  # own process group, so the watchdog can kill children too
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 1))
        except (ValueError, OSError):
            pass
        try:
            # Honoured on Linux; accepted-and-ignored on macOS. Kept for the platforms
            # where it works, never relied on for the one where it does not.
            soft = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (soft, soft))
        except (ValueError, OSError):
            pass
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))
        except (ValueError, OSError, AttributeError):
            pass

    return apply


def _child_env(workspace: Path) -> dict[str, str]:
    """The child's whole environment: nothing of the API's own, its temp and home in the
    workspace. Windows needs a few system variables to start Python at all — without
    SYSTEMROOT it cannot even seed its random generator."""
    ws = str(workspace)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": ws, "PYTHONPATH": "", "TMPDIR": ws,
           "MPLBACKEND": "Agg", "MPLCONFIGDIR": str(workspace / ".mpl"),
           "PYTHONIOENCODING": "utf-8"}
    if WINDOWS:
        for key in ("SYSTEMROOT", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT",
                    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE"):
            if os.environ.get(key):
                env[key] = os.environ[key]
        env.update({"TEMP": ws, "TMP": ws, "USERPROFILE": ws, "APPDATA": ws, "LOCALAPPDATA": ws})
    else:
        env["LC_ALL"] = "C.UTF-8"
    return env


async def _kill_tree(proc, job=None) -> None:
    """Kill the child and whatever it started. POSIX: its process group. Windows: its job,
    or the tree through taskkill /T when there is no job — TerminateProcess alone would
    leave grandchildren running."""
    if WINDOWS:
        if job is not None:
            job.kill()
        else:
            try:
                await asyncio.to_thread(subprocess.run, ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                        capture_output=True, timeout=15)
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


def available_modules() -> list[str]:
    """Which useful third-party modules this interpreter actually has.

    Reported to the model instead of assumed: a plan built around pandas that then fails
    on ImportError costs a whole turn, and telling it the truth up front costs nothing.
    """
    import importlib.util

    found = []
    for name in ("numpy", "pandas", "openpyxl", "httpx", "PIL", "bs4", "matplotlib",
                 "scipy", "sklearn", "yaml", "lxml"):
        try:
            if importlib.util.find_spec(name) is not None:
                found.append("Pillow" if name == "PIL" else name)
        except (ImportError, ValueError):
            continue
    return found


async def run_python(code: str, *, workspace: Path, timeout_s: int, memory_mb: int,
                     allow_network: bool = False, bridge=None,
                     bridge_tools: list[str] | None = None,
                     max_bridge_calls: int = 40, setup: str = "") -> dict:
    if not (code or "").strip():
        return {"ok": False, "error": "No code to run."}
    workspace.mkdir(parents=True, exist_ok=True)
    started = time.time()

    prelude = PRELUDE
    if not allow_network and not sandbox_available():
        prelude += NET_GUARD + (UNC_GUARD if WINDOWS else "")
    pipes: tuple[int, int, int, int] | None = None
    extra_args: list[str] = []
    pass_fds: tuple[int, ...] = ()
    win_handles: list[int] = []
    if bridge is not None:
        child_out_r, child_out_w = os.pipe()
        child_in_r, child_in_w = os.pipe()
        pipes = (child_out_r, child_out_w, child_in_r, child_in_w)
        if WINDOWS:
            # A file descriptor number means nothing in another Windows process: the child
            # gets the pipes' OS handles, inherited explicitly, and reopens them as fds.
            import msvcrt
            win_handles = [msvcrt.get_osfhandle(child_out_w), msvcrt.get_osfhandle(child_in_r)]
            for handle in win_handles:
                os.set_handle_inheritable(handle, True)
            extra_args = [str(h) for h in win_handles]
        else:
            for fd in (child_out_w, child_in_r):
                os.set_inheritable(fd, True)
            pass_fds = (child_out_w, child_in_r)
            extra_args = [str(child_out_w), str(child_in_r)]
        prelude = prelude + BRIDGE_PRELUDE + _wrappers(bridge_tools or [])

    # `from __future__` must be the first statement in a file, and the prelude is now in
    # front of it. Hoisting is the fix; the alternative is a SyntaxError pointing at a line
    # of code the model never wrote.
    # Run-specific definitions (e.g. `rows()`) join the prelude, so tracebacks still count
    # lines from the model's own first line.
    prelude = prelude + setup
    future, body = _hoist_future(code)
    source = future + prelude + body
    offset = source[:len(future) + len(prelude)].count("\n")
    # -I isolates the child from PYTHON* variables, PYTHONIOENCODING included — so UTF-8 is
    # asked for with -X utf8, which -I does not strip. Without it a Windows child prints in
    # cp1252 and every accent in a result arrives broken.
    base = [sys.executable, "-I", "-X", "utf8", "-u"]
    script: Path | None = None
    if WINDOWS:
        # A Windows command line stops at 32 767 characters; prelude, bridge and code pass
        # that quickly. The source goes in a file, removed once the run ends.
        script = workspace / ".run" / f"{uuid.uuid4().hex}.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(source, encoding="utf-8")
        argv = [*base, str(script), *extra_args]
    else:
        argv = [*base, "-c", source, *extra_args]
    if sandbox_available() and not allow_network:
        profile = workspace / ".sandbox.sb"
        profile.write_text(_profile(workspace.resolve()), encoding="utf-8")
        argv = [SANDBOX, "-f", str(profile), *argv]
    options: dict = {"stdout": asyncio.subprocess.PIPE, "stderr": asyncio.subprocess.PIPE,
                     "cwd": str(workspace), "env": _child_env(workspace)}
    if WINDOWS:
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        if win_handles:
            startup = subprocess.STARTUPINFO()
            startup.lpAttributeList = {"handle_list": win_handles}
            options["startupinfo"] = startup
    else:
        options["preexec_fn"] = _limits(memory_mb, timeout_s)
        options["pass_fds"] = pass_fds
    try:
        proc = await asyncio.create_subprocess_exec(*argv, **options)
    except NotImplementedError:
        for fd in (pipes or ()):
            _close(fd)
        _remove(script)
        return {"ok": False, "error": "This server's event loop cannot start processes. On Windows, "
                                      "start the API without --reload (see README → Windows)."}
    except OSError as exc:
        for fd in (pipes or ()):
            _close(fd)
        _remove(script)
        return {"ok": False, "error": f"Could not start a Python process: {exc}"}

    # Windows: the memory, CPU and process ceilings `_limits` sets on POSIX, as a job object.
    job = None
    if WINDOWS:
        from . import winjob
        job = winjob.contain(proc.pid, memory_mb=memory_mb, cpu_s=timeout_s)

    pump: asyncio.Task | None = None
    bridge_calls: list[str] = []
    if pipes is not None:
        _close(pipes[1])
        _close(pipes[2])
        pump = asyncio.create_task(
            _serve_bridge(pipes[0], pipes[3], bridge, bridge_calls, max_bridge_calls))

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        await _kill_tree(proc, job)
        await proc.wait()
        await _stop(pump, pipes)
        if job is not None:
            job.close()
        _remove(script)
        return {"ok": False, "elapsed_ms": int((time.time() - started) * 1000),
                "error": f"Timed out after {timeout_s}s and was killed. Make the computation "
                         f"smaller, or do it in steps."}

    await _stop(pump, pipes)
    if job is not None:
        job.close()   # and with it anything the code left running in the background
    _remove(script)
    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    elapsed_ms = int((time.time() - started) * 1000)
    if len(out) > 20000:
        out = out[:20000] + f"\n[stdout truncated — {len(stdout)} bytes total]"
    if proc.returncode != 0:
        # Line numbers are renumbered to the code the model actually wrote. A traceback
        # pointing into a prelude it has never seen is a puzzle, not a diagnostic.
        detail = _renumber(err.strip(), offset)[-3000:] or f"exit code {proc.returncode}"
        if "Operation not permitted" in detail and sandbox_available():
            detail += ("\n\nThis process runs with the network denied by the kernel and "
                       "writes confined to the workspace. Read data through the connected "
                       "sources' tools and pass it in, or write inside the workspace.")
        if not WINDOWS and proc.returncode == -signal.SIGKILL:
            detail = ("killed — most likely it exceeded the memory or CPU ceiling. "
                      + detail)
        elif "MemoryError" in detail:
            detail = (f"it ran out of memory — the ceiling is {memory_mb} MB. Load fewer rows or "
                      f"columns, or aggregate in the source's query. " + detail)
        return {"ok": False, "elapsed_ms": elapsed_ms, "stdout": out, "error": detail,
                "bridge_calls": bridge_calls}
    return {"ok": True, "elapsed_ms": elapsed_ms, "stdout": out,
            "stderr": err.strip()[-2000:], "returncode": 0,
            "bridge_calls": bridge_calls,
            "sandboxed": sandbox_available() and not allow_network}


def _remove(path: Path | None) -> None:
    if path is not None:
        try:
            path.unlink()
        except OSError:
            pass


def _close(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass


async def _stop(pump, pipes) -> None:
    if pump is not None:
        pump.cancel()
        try:
            await pump
        except BaseException:  # noqa: BLE001 - cancellation and teardown errors are expected
            pass
    if pipes:
        _close(pipes[0])
        _close(pipes[3])


async def _serve_bridge(read_fd: int, write_fd: int, bridge, calls: list[str],
                        ceiling: int) -> None:
    """Answer the child's tool requests until it exits.

    Every request gets a complete JSON line back, failures included. The child blocks in
    `call_tool` until one arrives, so an unanswered request is a hang rather than an error —
    and a hang is the one failure mode a bridge like this must not have.
    """
    loop = asyncio.get_running_loop()
    buffer = b""
    while True:
        chunk = await loop.run_in_executor(None, lambda: os.read(read_fd, 65536))
        if not chunk:
            return
        buffer += chunk
        while b"\n" in buffer:
            line, _, buffer = buffer.partition(b"\n")
            if not line.strip():
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                reply = {"ok": False, "error": "malformed request"}
            else:
                name = str(request.get("tool") or "")
                if len(calls) >= ceiling:
                    reply = {"ok": False,
                             "error": f"this program has already made {ceiling} tool calls, "
                                      f"the ceiling for one run_python"}
                else:
                    calls.append(name)
                    try:
                        reply = await bridge(name, request.get("args") or {})
                    except Exception as exc:  # noqa: BLE001 - the child must always get a reply
                        reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            try:
                os.write(write_fd, (json.dumps(reply, default=str) + "\n").encode())
            except OSError:
                return


_FUTURE = re.compile(r"^\s*from\s+__future__\s+import\s+[^\n]+$", re.M)
# `<string>` from `-c`; the script path when the source was written to a file (Windows).
_TRACE_LINE = re.compile(r'File "(?:<string>|[^"]*[\\/]\.run[\\/][0-9a-f]{32}\.py)", line (\d+)')


def _hoist_future(code: str) -> tuple[str, str]:
    """Split out any `from __future__` lines so they can stay first."""
    futures = _FUTURE.findall(code)
    if not futures:
        return "", code
    return "\n".join(line.strip() for line in futures) + "\n", _FUTURE.sub("", code)


def _renumber(traceback_text: str, offset: int) -> str:
    def fix(match: re.Match[str]) -> str:
        line = int(match.group(1)) - offset
        return (f'File "your code", line {line}' if line > 0
                else 'File "the agent\'s prelude"')
    return _TRACE_LINE.sub(fix, traceback_text)
