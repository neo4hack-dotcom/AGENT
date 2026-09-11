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
runs with the same rights as the Lumen process. That is stated plainly in the admin UI,
and the whole tool can be switched off there.
"""

from __future__ import annotations

import asyncio
import os
import resource
import signal
import sys
import time
from pathlib import Path

PRELUDE = (
    "import sys, os, json, math, statistics, re, datetime, itertools, collections, pathlib\n"
)


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


async def run_python(code: str, *, workspace: Path, timeout_s: int, memory_mb: int) -> dict:
    if not (code or "").strip():
        return {"ok": False, "error": "No code to run."}
    workspace.mkdir(parents=True, exist_ok=True)
    started = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-u", "-c", PRELUDE + code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workspace),
            preexec_fn=_limits(memory_mb, timeout_s),
            env={"PATH": os.environ.get("PATH", ""), "HOME": str(workspace),
                 "PYTHONPATH": "", "TMPDIR": str(workspace),
                 "MPLBACKEND": "Agg", "LC_ALL": "C.UTF-8", "PYTHONIOENCODING": "utf-8"},
        )
    except OSError as exc:
        return {"ok": False, "error": f"Could not start a Python process: {exc}"}

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        await proc.wait()
        return {"ok": False, "elapsed_ms": int((time.time() - started) * 1000),
                "error": f"Timed out after {timeout_s}s and was killed. Make the computation "
                         f"smaller, or do it in steps."}

    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    elapsed_ms = int((time.time() - started) * 1000)
    if len(out) > 20000:
        out = out[:20000] + f"\n[stdout truncated — {len(stdout)} bytes total]"
    if proc.returncode != 0:
        detail = err.strip()[-3000:] or f"exit code {proc.returncode}"
        if proc.returncode == -signal.SIGKILL:
            detail = ("killed — most likely it exceeded the memory or CPU ceiling. "
                      + detail)
        return {"ok": False, "elapsed_ms": elapsed_ms, "stdout": out, "error": detail}
    return {"ok": True, "elapsed_ms": elapsed_ms, "stdout": out,
            "stderr": err.strip()[-2000:], "returncode": 0}
