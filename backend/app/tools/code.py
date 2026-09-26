"""Running Python the model wrote, with real ceilings.

An agent that can compute is a different class of tool from one that can only talk, but
"exec whatever the model produced" is not something to hand-wave. Three things hold here:

* a **separate process**, so a crash, a segfault or an `os._exit` kills only the child;
* a **wall-clock watchdog that kills the process group**, because the CPU rlimit alone
  does not stop a child that is asleep on a socket, and on macOS `RLIMIT_AS` is silently
  ineffective — the limit that actually holds on this platform is the watchdog;
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
import resource
import signal
import sys
import time
from pathlib import Path

PRELUDE = (
    "import sys, os, json, math, statistics, re, datetime, itertools, collections, pathlib\n"
)

# When the parent offers a bridge, the child gets the other tools as ordinary functions.
# This is the mechanism that turns a five-round-trip plan into a single generation: the
# model writes the whole procedure — fetch, filter, join, write — and the loop between the
# steps runs in the interpreter instead of through the model. See tools/bridge_prelude.py.
BRIDGE_PRELUDE = (Path(__file__).parent / "bridge_prelude.py").read_text()


def _wrappers(names: list[str]) -> str:
    """One named function per tool, so the model writes `sqlite__read_query(query=...)`, not a string."""
    return "\n".join(
        f"def {name}(**kw):\n    return call_tool({name!r}, **kw)\n"
        for name in names if name.isidentifier())

SANDBOX = "/usr/bin/sandbox-exec"


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
    return f"""(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write*
    (subpath "{root}")
    (literal "/dev/null") (literal "/dev/stdout") (literal "/dev/stderr")
    (regex #"^/dev/tty"))
"""


def _limits(memory_mb: int, cpu_s: int):
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
    pipes: tuple[int, int, int, int] | None = None
    extra_args: list[str] = []
    pass_fds: tuple[int, ...] = ()
    if bridge is not None:
        child_out_r, child_out_w = os.pipe()
        child_in_r, child_in_w = os.pipe()
        for fd in (child_out_w, child_in_r):
            os.set_inheritable(fd, True)
        pipes = (child_out_r, child_out_w, child_in_r, child_in_w)
        pass_fds = (child_out_w, child_in_r)
        extra_args = [str(child_out_w), str(child_in_r)]
        prelude = PRELUDE + BRIDGE_PRELUDE + _wrappers(bridge_tools or [])

    # `from __future__` must be the first statement in a file, and the prelude is now in
    # front of it. Hoisting is the fix; the alternative is a SyntaxError pointing at a line
    # of code the model never wrote.
    # Run-specific definitions (e.g. `rows()`) join the prelude, so tracebacks still count
    # lines from the model's own first line.
    prelude = prelude + setup
    future, body = _hoist_future(code)
    source = future + prelude + body
    offset = source[:len(future) + len(prelude)].count("\n")
    argv = [sys.executable, "-I", "-u", "-c", source, *extra_args]
    if sandbox_available() and not allow_network:
        profile = workspace / ".sandbox.sb"
        profile.write_text(_profile(workspace.resolve()))
        argv = [SANDBOX, "-f", str(profile), *argv]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workspace),
            preexec_fn=_limits(memory_mb, timeout_s),
            pass_fds=pass_fds,
            env={"PATH": os.environ.get("PATH", ""), "HOME": str(workspace),
                 "PYTHONPATH": "", "TMPDIR": str(workspace),
                 "MPLBACKEND": "Agg", "LC_ALL": "C.UTF-8", "PYTHONIOENCODING": "utf-8"},
        )
    except OSError as exc:
        for fd in (pipes or ()):
            _close(fd)
        return {"ok": False, "error": f"Could not start a Python process: {exc}"}

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
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        await proc.wait()
        await _stop(pump, pipes)
        return {"ok": False, "elapsed_ms": int((time.time() - started) * 1000),
                "error": f"Timed out after {timeout_s}s and was killed. Make the computation "
                         f"smaller, or do it in steps."}

    await _stop(pump, pipes)
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
        if proc.returncode == -signal.SIGKILL:
            detail = ("killed — most likely it exceeded the memory or CPU ceiling. "
                      + detail)
        return {"ok": False, "elapsed_ms": elapsed_ms, "stdout": out, "error": detail,
                "bridge_calls": bridge_calls}
    return {"ok": True, "elapsed_ms": elapsed_ms, "stdout": out,
            "stderr": err.strip()[-2000:], "returncode": 0,
            "bridge_calls": bridge_calls,
            "sandboxed": sandbox_available() and not allow_network}


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
_TRACE_LINE = re.compile(r'File "<string>", line (\d+)')


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
