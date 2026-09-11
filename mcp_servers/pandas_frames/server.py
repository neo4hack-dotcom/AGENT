#!/usr/bin/env python3
"""Pandas Frames — dataframes for the agent, bounded and auditable.

Every operation runs inside a single long-lived worker process that has hard resource
limits applied to it at startup (`RLIMIT_AS` for address space, `RLIMIT_CPU` for CPU
seconds). The worker holds the frames, so operations are fast; the parent holds no data
and can terminate the worker at any moment, which is what makes "kill it from the app"
real rather than cooperative.

Three things are bounded independently: the size of anything loaded (1 GB by default and
never more), the memory the worker may address, and the CPU seconds any single operation
may burn. Every call is appended to an audit log with its timing, peak memory, and the
exact expression evaluated.

**On the sandbox, honestly.** Expressions are checked by an AST validator that rejects
imports, dunder access, and calls to anything not on an allow-list, and they run in a
process with no data of its own and hard limits. That is real defence in depth against
mistakes and runaway work. It is *not* a security boundary against a determined attacker
with arbitrary expression access — pandas is too large a surface for that claim to be
true. Point this server at data you are willing to have read, and do not expose it to
untrusted callers.
"""

from __future__ import annotations

import argparse
import ast
import json
import multiprocessing as mp
import os
import resource
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _mcp_stdio import McpServer, log  # noqa: E402

HARD_MAX_BYTES = 1024 ** 3           # 1 GB. Configurable downwards, never upwards.
DEFAULT_MEMORY_MB = 2048
DEFAULT_CPU_SECONDS = 60
DEFAULT_OP_TIMEOUT = 90
MAX_PREVIEW_ROWS = 100
MAX_AUDIT_RETURNED = 200

server = McpServer("pandas-frames", "1.0.0", "Pandas Frames")


class Settings:
    workspace = Path.cwd()
    audit_path = Path.cwd() / "pandas_frames_audit.jsonl"
    max_bytes = HARD_MAX_BYTES
    memory_mb = DEFAULT_MEMORY_MB
    cpu_seconds = DEFAULT_CPU_SECONDS
    op_timeout = DEFAULT_OP_TIMEOUT


CONFIG = Settings()


# ============================================================== the worker process

def _worker_main(request_q, response_q, memory_mb: int, cpu_seconds: int, max_bytes: int) -> None:
    """Runs in the child. Applies its own limits before importing anything heavy, so a
    limit that cannot be honoured fails here rather than silently not applying."""
    applied = {}
    try:
        soft_as = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (soft_as, soft_as))
        applied["address_space_mb"] = memory_mb
    except (ValueError, OSError) as exc:
        applied["address_space_error"] = str(exc)
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 5))
        applied["cpu_seconds"] = cpu_seconds
    except (ValueError, OSError) as exc:
        applied["cpu_error"] = str(exc)

    import numpy as np
    import pandas as pd

    frames: dict[str, "pd.DataFrame"] = {}
    response_q.put({"kind": "ready", "pid": os.getpid(), "limits": applied,
                    "pandas": pd.__version__, "numpy": np.__version__})

    def peak_memory_mb() -> float:
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS reports bytes, Linux kilobytes.
        return round(usage / (1024 ** 2 if sys.platform == "darwin" else 1024), 1)

    def frame_bytes(frame) -> int:
        return int(frame.memory_usage(deep=True).sum())

    def guard_size(name: str, frame) -> None:
        size = frame_bytes(frame)
        if size > max_bytes:
            del frame
            raise MemoryError(
                f"'{name}' would hold {size / 1024 ** 2:.0f} MiB in memory, over the "
                f"{max_bytes / 1024 ** 2:.0f} MiB ceiling. Filter or aggregate before "
                f"materialising it.")

    def describe(name: str, frame) -> dict:
        return {
            "name": name, "rows": int(len(frame)), "columns": list(map(str, frame.columns)),
            "bytes": frame_bytes(frame),
            "dtypes": {str(c): str(t) for c, t in frame.dtypes.items()},
        }

    while True:
        message = request_q.get()
        if message is None:
            break
        op, args, job_id = message["op"], message.get("args") or {}, message.get("job_id")
        started = time.time()
        try:
            result = _dispatch(op, args, frames, pd, np, guard_size, describe, max_bytes)
            payload = {"kind": "result", "job_id": job_id, "ok": True, "result": result}
        except Exception as exc:
            payload = {"kind": "result", "job_id": job_id, "ok": False,
                       "error": f"{type(exc).__name__}: {exc}"}
        payload["stats"] = {"elapsed_ms": int((time.time() - started) * 1000),
                            "peak_memory_mb": peak_memory_mb(),
                            "frames_held": len(frames)}
        response_q.put(payload)


def _dispatch(op: str, args: dict, frames: dict, pd, np, guard_size, describe, max_bytes: int):
    if op == "load":
        path, name = Path(args["path"]), args["name"]
        size = path.stat().st_size
        if size > max_bytes:
            raise MemoryError(f"{path.name} is {size / 1024 ** 2:.0f} MiB, over the "
                              f"{max_bytes / 1024 ** 2:.0f} MiB ceiling.")
        options = {k: v for k, v in (args.get("options") or {}).items()}
        suffix = path.suffix.lower()
        if suffix in (".xlsx", ".xlsm"):
            frame = pd.read_excel(path, **options)
        elif suffix == ".parquet":
            frame = pd.read_parquet(path, **options)
        elif suffix in (".json", ".ndjson"):
            frame = pd.read_json(path, lines=suffix == ".ndjson", **options)
        else:
            frame = pd.read_csv(path, **options)
        guard_size(name, frame)
        frames[name] = frame
        return describe(name, frame)

    if op == "list":
        return {"frames": [describe(n, f) for n, f in frames.items()]}

    if op == "describe":
        name = args["name"]
        if name not in frames:
            raise KeyError(f"No frame '{name}'. Loaded: {', '.join(frames) or 'none'}")
        frame = frames[name]
        stats = frame.describe(include="all").fillna("").astype(str).to_dict()
        return {**describe(name, frame),
                "statistics": {str(k): v for k, v in stats.items()},
                "nulls": {str(c): int(frame[c].isna().sum()) for c in frame.columns},
                "head": json.loads(frame.head(5).to_json(orient="records", date_format="iso"))}

    if op == "run":
        expression, store_as = args["expression"], args.get("store_as") or ""
        validate_expression(expression)
        # Everything goes in globals, not locals: a generator expression or comprehension
        # gets its own scope and resolves free names against globals only, so
        # `sum(np.sqrt(i) for i in ...)` would otherwise fail with "np is not defined".
        scope = {"__builtins__": SAFE_BUILTINS, "pd": pd, "np": np, **frames, "frames": frames}
        try:
            value = eval(compile(ast.parse(expression, mode="eval"), "<frames>", "eval"), scope)  # noqa: S307
        except NameError as exc:
            # A bare "name 'orders_eur_df' is not defined" tells the caller nothing about
            # what it could have used, and a model will cheerfully retry the same invented
            # name until the replan budget is gone. Name what exists.
            raise NameError(
                f"{exc}. Frames currently loaded: {', '.join(frames) or 'none'}. "
                f"Create one by passing store_as on the step that produces it, or load it first."
            ) from None
        except KeyError as exc:
            columns = {name: list(map(str, f.columns))[:40] for name, f in frames.items()}
            raise KeyError(
                f"{exc}. Columns available — "
                + "; ".join(f"{name}: {', '.join(cols)}" for name, cols in columns.items())
            ) from None
        if isinstance(value, pd.DataFrame):
            guard_size(store_as or "result", value)
            if store_as:
                frames[store_as] = value
            preview = min(int(args.get("preview") or 20), MAX_PREVIEW_ROWS)
            return {"type": "dataframe", "stored_as": store_as or None,
                    **describe(store_as or "(not stored)", value),
                    "preview": json.loads(value.head(preview).to_json(orient="records", date_format="iso"))}
        if isinstance(value, pd.Series):
            preview = min(int(args.get("preview") or 20), MAX_PREVIEW_ROWS)
            if store_as:
                frames[store_as] = value.to_frame()
            return {"type": "series", "stored_as": store_as or None, "length": int(len(value)),
                    "dtype": str(value.dtype),
                    "preview": json.loads(value.head(preview).to_json(date_format="iso"))}
        return {"type": type(value).__name__, "value": _jsonable(value)}

    if op == "export":
        name, path = args["name"], Path(args["path"])
        if name not in frames:
            raise KeyError(f"No frame '{name}'. Loaded: {', '.join(frames) or 'none'}")
        frame = frames[name]
        path.parent.mkdir(parents=True, exist_ok=True)
        suffix = path.suffix.lower()
        if suffix in (".xlsx", ".xlsm"):
            frame.to_excel(path, index=False)
        elif suffix == ".parquet":
            frame.to_parquet(path, index=False)
        else:
            frame.to_csv(path, index=False)
        return {"ok": True, "path": str(path), "rows": int(len(frame)), "bytes": path.stat().st_size}

    if op == "drop":
        name = args["name"]
        frames.pop(name, None)
        return {"dropped": name, "remaining": list(frames)}

    raise ValueError(f"Unknown operation '{op}'")


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)[:4000]


# ------------------------------------------------------------- expression validation

SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict, "divmod": divmod,
    "enumerate": enumerate, "filter": filter, "float": float, "int": int, "len": len,
    "list": list, "map": map, "max": max, "min": min, "range": range, "round": round,
    "set": set, "sorted": sorted, "str": str, "sum": sum, "tuple": tuple, "zip": zip,
}
BLOCKED_NAMES = {"eval", "exec", "compile", "open", "input", "globals", "locals", "vars",
                 "getattr", "setattr", "delattr", "__import__", "breakpoint", "exit", "quit"}


class ExpressionRejected(ValueError):
    """The expression contains something the validator will not allow through."""


def validate_expression(expression: str) -> None:
    """Reject the obvious escapes before evaluating.

    This stops mistakes and casual reach-outs — imports, dunder traversal to the object
    graph, direct calls to eval/open. It does not make arbitrary pandas expressions safe
    against someone determined; the process limits are what contain those.
    """
    if len(expression) > 4000:
        raise ExpressionRejected("Expression too long (limit 4000 characters).")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ExpressionRejected(f"Could not parse: {exc.msg}") from exc

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ExpressionRejected("Imports are not allowed.")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ExpressionRejected(f"Attribute '{node.attr}' is not allowed (underscore access).")
        if isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
            raise ExpressionRejected(f"'{node.id}' is not allowed.")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ExpressionRejected(f"'{node.id}' is not allowed.")


# ================================================================= the parent side

def process_rss_mb(pid: int) -> float | None:
    """Resident memory of a process, in MiB, without a third-party dependency.

    `ps` is used because `RLIMIT_AS` is not settable on macOS ("current limit exceeds
    maximum limit"), so the kernel will not enforce the ceiling for us. Polling and
    killing is less elegant than an rlimit and works on every platform this runs on —
    which matters more than elegance for a limit the operator was promised.
    """
    if not _PS:
        return None
    try:
        out = subprocess.run([_PS, "-o", "rss=", "-p", str(pid)], capture_output=True,
                             text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    value = out.stdout.strip()
    return round(int(value) / 1024, 1) if value.isdigit() else None


_PS = shutil.which("ps")


class Worker:
    """Owns the child process, the job table, the memory watchdog, and the audit log."""

    def __init__(self) -> None:
        self.process: mp.Process | None = None
        self.requests: Any = None
        self.responses: Any = None
        self.info: dict = {}
        self.jobs: dict[str, dict] = {}
        self.current: str | None = None
        self.restarts = 0
        self.breach: str | None = None
        self._watchdog: threading.Thread | None = None
        self._watching = threading.Event()
        self.peak_rss_mb: float = 0.0

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> dict:
        if self.process is not None and self.process.is_alive():
            return self.info
        context = mp.get_context("spawn")
        self.requests, self.responses = context.Queue(), context.Queue()
        self.process = context.Process(
            target=_worker_main,
            args=(self.requests, self.responses, CONFIG.memory_mb, CONFIG.cpu_seconds, CONFIG.max_bytes),
            daemon=True,
        )
        self.process.start()
        ready = self.responses.get(timeout=60)
        self.info = {**ready, "started_at": time.time()}
        log(f"worker up: pid {ready['pid']} · limits {ready['limits']}")
        return self.info

    def stop(self) -> None:
        if self.process is not None and self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=5)
        self.process = None

    def restart(self, reason: str) -> dict:
        self.stop()
        self.restarts += 1
        log(f"worker restart ({reason})")
        return self.start()

    # -- work --------------------------------------------------------------
    def call(self, op: str, args: dict, timeout: float | None = None) -> dict:
        self.start()
        job_id = uuid.uuid4().hex[:10]
        job = {"id": job_id, "op": op, "args": _redact(args), "status": "running",
               "started_at": time.time(), "pid": self.info.get("pid")}
        self.jobs[job_id] = job
        self.current = job_id
        deadline = timeout if timeout is not None else CONFIG.op_timeout

        self.breach = None
        self._start_watchdog()
        self.requests.put({"op": op, "args": args, "job_id": job_id})
        try:
            reply = self.responses.get(timeout=deadline)
        except Exception:
            reply = None
        finally:
            self._stop_watchdog()

        if reply is None:
            alive = self.process is not None and self.process.is_alive()
            breach = self.breach
            self.restart("memory" if breach else "timeout" if alive else "worker died")
            job.update(status="killed" if breach or not alive else "timeout",
                       finished_at=time.time(),
                       error=(breach if breach else
                              "The worker process died, most likely on a CPU-second or "
                              "allocation limit." if not alive else
                              f"No answer within {deadline:.0f}s; the worker was restarted."))
            self.current = None
            audit(job)
            return {"ok": False, "job_id": job_id, "error": job["error"], "status": job["status"]}

        job.update(status="done" if reply.get("ok") else "error",
                   finished_at=time.time(), stats=reply.get("stats") or {},
                   error=reply.get("error"))
        self.current = None
        audit(job)
        return {"ok": bool(reply.get("ok")), "job_id": job_id,
                "result": reply.get("result"), "error": reply.get("error"),
                "stats": reply.get("stats")}

    # -- the memory watchdog ------------------------------------------------
    def _start_watchdog(self) -> None:
        pid = self.info.get("pid")
        if not pid or not _PS:
            return
        self._watching.set()

        def watch() -> None:
            ceiling = CONFIG.memory_mb
            while self._watching.is_set():
                rss = process_rss_mb(pid)
                if rss is not None:
                    self.peak_rss_mb = max(self.peak_rss_mb, rss)
                    if rss > ceiling:
                        self.breach = (f"Killed: the worker reached {rss:.0f} MiB, over its "
                                       f"{ceiling} MiB ceiling. Work on a smaller slice, or "
                                       f"raise --memory-mb when connecting the server.")
                        log(self.breach)
                        if self.process is not None and self.process.is_alive():
                            self.process.kill()
                        return
                time.sleep(0.25)

        self._watchdog = threading.Thread(target=watch, daemon=True)
        self._watchdog.start()

    def _stop_watchdog(self) -> None:
        self._watching.clear()
        if self._watchdog is not None:
            self._watchdog.join(timeout=1)
            self._watchdog = None

    def cancel(self) -> dict:
        """Kill whatever is running. The worker is restarted empty — which loses the
        loaded frames, and says so, rather than pretending they survived."""
        job_id = self.current
        if self.process is None or not self.process.is_alive():
            return {"ok": False, "error": "No worker process is running."}
        if job_id is None:
            return {"ok": False, "error": "Nothing is running right now."}
        job = self.jobs.get(job_id, {})
        job.update(status="cancelled", finished_at=time.time(), error="Cancelled from the app.")
        audit(job)
        self.current = None
        self.restart("cancelled")
        return {"ok": True, "cancelled": job_id,
                "note": "The worker was restarted, so every loaded frame is gone. Reload what you need."}


def _redact(args: dict) -> dict:
    return {k: (v[:300] if isinstance(v, str) else v) for k, v in args.items()}


WORKER = Worker()


def audit(job: dict) -> None:
    """One JSON line per operation. Append-only, and never allowed to break a run."""
    entry = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "job_id": job.get("id"), "op": job.get("op"), "status": job.get("status"),
        "duration_ms": int(((job.get("finished_at") or time.time()) - job["started_at"]) * 1000),
        "peak_memory_mb": (job.get("stats") or {}).get("peak_memory_mb"),
        "frames_held": (job.get("stats") or {}).get("frames_held"),
        "args": job.get("args"), "error": job.get("error"), "pid": job.get("pid"),
    }
    try:
        with CONFIG.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:
        log(f"audit write failed: {exc}")


def in_workspace(relative: str, must_exist: bool = True) -> Path:
    name = (relative or "").strip()
    if not name:
        raise ValueError("A path is required.")
    target = (CONFIG.workspace / name).resolve()
    if target != CONFIG.workspace and CONFIG.workspace not in target.parents:
        raise ValueError("Refusing to touch anything outside the workspace directory.")
    if must_exist and not target.is_file():
        raise ValueError(f"'{relative}' does not exist in the workspace.")
    return target


# ---------------------------------------------------------------------------- tools

@server.tool(
    "limits",
    """The ceilings in force and what the worker is doing right now. Read this first — it
    tells you how much data you may load and how long an operation may run.""",
    {"type": "object", "properties": {}, "additionalProperties": False},
    read_only=True,
)
def limits() -> dict:
    WORKER.start()  # so the reported limits are the ones actually applied, not the intent
    alive = WORKER.process is not None and WORKER.process.is_alive()
    return {
        "workspace": str(CONFIG.workspace),
        "max_data_bytes": CONFIG.max_bytes,
        "max_data_human": (f"{CONFIG.max_bytes / 1024 ** 3:.2f} GiB"
                           if CONFIG.max_bytes >= 1024 ** 3
                           else f"{CONFIG.max_bytes / 1024 ** 2:.0f} MiB"),
        "worker_memory_mb": CONFIG.memory_mb,
        "worker_cpu_seconds": CONFIG.cpu_seconds,
        "operation_timeout_s": CONFIG.op_timeout,
        "worker_alive": alive,
        "worker_pid": WORKER.info.get("pid") if alive else None,
        "limits_applied": WORKER.info.get("limits", {}),
        "memory_enforced_by": ("the operating system (RLIMIT_AS)"
                               if "address_space_mb" in WORKER.info.get("limits", {})
                               else "a polling watchdog in this server — RLIMIT_AS is not "
                                    "settable on this platform, so the ceiling is enforced "
                                    "by measuring the worker and killing it"),
        "worker_rss_mb": process_rss_mb(WORKER.info["pid"]) if alive and WORKER.info.get("pid") else None,
        "peak_rss_mb": WORKER.peak_rss_mb or None,
        "pandas": WORKER.info.get("pandas"),
        "restarts": WORKER.restarts,
        "audit_log": str(CONFIG.audit_path),
    }


@server.tool(
    "load",
    """Load a CSV, Excel, Parquet or JSON file into a named dataframe. Refused above the
    size ceiling — filter at source instead.""",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to the workspace."},
            "name": {"type": "string", "description": "Name to hold it under, e.g. 'orders'."},
            "options": {"type": "object", "description": "Extra reader options, e.g. {\"sep\": \";\"}."},
        },
        "required": ["path", "name"],
        "additionalProperties": False,
    },
)
def load(path: str, name: str, options: dict | None = None) -> dict:
    target = in_workspace(path)
    if not name.isidentifier():
        return {"error": f"'{name}' is not a usable frame name — use letters, digits and underscores."}
    outcome = WORKER.call("load", {"path": str(target), "name": name, "options": options or {}})
    return outcome["result"] if outcome["ok"] else outcome


@server.tool(
    "list_frames",
    "Every dataframe currently held, with its shape and memory footprint.",
    {"type": "object", "properties": {}, "additionalProperties": False},
    read_only=True,
)
def list_frames() -> dict:
    outcome = WORKER.call("list", {})
    return outcome["result"] if outcome["ok"] else outcome


@server.tool(
    "describe",
    "Shape, dtypes, null counts, summary statistics and the first rows of one dataframe.",
    {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    },
    read_only=True,
)
def describe(name: str) -> dict:
    outcome = WORKER.call("describe", {"name": name})
    return outcome["result"] if outcome["ok"] else outcome


@server.tool(
    "run",
    """Evaluate one pandas expression against the loaded frames and return the result.

    Loaded frames are in scope by name, along with `pd` and `np`. Examples:
      orders[orders.status == 'delivered'].groupby('country')['amount'].sum()
      orders.merge(customers, left_on='customer_id', right_on='id', how='left')
      orders.assign(month=pd.to_datetime(orders.ordered_at).dt.to_period('M'))

    One expression, not a script: no imports, no underscore attributes, no eval/open.
    Set `store_as` to keep a dataframe result for the next call.""",
    {
        "type": "object",
        "properties": {
            "expression": {"type": "string"},
            "store_as": {"type": "string", "description": "Keep a dataframe result under this name."},
            "preview": {"type": "integer", "description": "Rows returned (default 20, max 100)."},
            "timeout_s": {"type": "number", "description": "Override the operation timeout."},
        },
        "required": ["expression"],
        "additionalProperties": False,
    },
)
def run(expression: str, store_as: str = "", preview: int = 20, timeout_s: float | None = None) -> dict:
    try:
        validate_expression(expression)  # rejected here too, so nothing crosses the pipe
    except ExpressionRejected as exc:
        return {"error": str(exc)}
    if store_as and not store_as.isidentifier():
        return {"error": f"'{store_as}' is not a usable frame name."}
    outcome = WORKER.call("run", {"expression": expression, "store_as": store_as, "preview": preview},
                          timeout=timeout_s)
    if outcome["ok"]:
        return {**outcome["result"], "job_id": outcome["job_id"], "stats": outcome.get("stats")}
    return outcome


@server.tool(
    "export",
    "Write a dataframe to CSV, Excel or Parquet inside the workspace.",
    {
        "type": "object",
        "properties": {"name": {"type": "string"}, "path": {"type": "string"}},
        "required": ["name", "path"],
        "additionalProperties": False,
    },
)
def export(name: str, path: str) -> dict:
    target = in_workspace(path, must_exist=False)
    outcome = WORKER.call("export", {"name": name, "path": str(target)})
    return outcome["result"] if outcome["ok"] else outcome


@server.tool(
    "drop_frame",
    "Release one dataframe from memory.",
    {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    },
)
def drop_frame(name: str) -> dict:
    outcome = WORKER.call("drop", {"name": name})
    return outcome["result"] if outcome["ok"] else outcome


@server.tool(
    "list_jobs",
    "Every operation this session, newest first, with status, duration and peak memory.",
    {
        "type": "object",
        "properties": {"limit": {"type": "integer"}},
        "additionalProperties": False,
    },
    read_only=True,
)
def list_jobs(limit: int = 50) -> dict:
    jobs = sorted(WORKER.jobs.values(), key=lambda j: j["started_at"], reverse=True)
    return {
        "running": WORKER.current,
        "worker_pid": WORKER.info.get("pid"),
        "count": len(jobs),
        "jobs": [
            {"id": j["id"], "op": j["op"], "status": j["status"],
             "duration_ms": int(((j.get("finished_at") or time.time()) - j["started_at"]) * 1000),
             "peak_memory_mb": (j.get("stats") or {}).get("peak_memory_mb"),
             "error": j.get("error"), "args": j.get("args")}
            for j in jobs[: max(1, min(int(limit), 500))]
        ],
    }


@server.tool(
    "cancel",
    """Kill the operation in flight. The worker is restarted, so every loaded frame is
    lost — that is the price of a real kill rather than a cooperative one.""",
    {"type": "object", "properties": {}, "additionalProperties": False},
)
def cancel() -> dict:
    return WORKER.cancel()


@server.tool(
    "audit_log",
    "The append-only record of every operation: what ran, how long, how much memory, and what failed.",
    {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Most recent entries (default 50)."},
            "status": {"type": "string", "description": "Filter: done, error, cancelled, killed, timeout."},
        },
        "additionalProperties": False,
    },
    read_only=True,
)
def audit_log(limit: int = 50, status: str = "") -> dict:
    if not CONFIG.audit_path.exists():
        return {"path": str(CONFIG.audit_path), "count": 0, "entries": []}
    entries = []
    for line in CONFIG.audit_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if status and entry.get("status") != status:
            continue
        entries.append(entry)
    limit = max(1, min(int(limit), MAX_AUDIT_RETURNED))
    return {"path": str(CONFIG.audit_path), "count": len(entries), "entries": entries[-limit:][::-1]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Pandas Frames MCP server")
    parser.add_argument("--workspace", required=True, help="Directory holding the data files")
    parser.add_argument("--memory-mb", type=int, default=DEFAULT_MEMORY_MB,
                        help="Address space ceiling for the worker process")
    parser.add_argument("--cpu-seconds", type=int, default=DEFAULT_CPU_SECONDS,
                        help="CPU seconds any one operation may burn")
    parser.add_argument("--max-data-mb", type=int, default=HARD_MAX_BYTES // (1024 * 1024),
                        help="Ceiling on any single dataset. Never exceeds 1024.")
    parser.add_argument("--timeout-s", type=int, default=DEFAULT_OP_TIMEOUT)
    parser.add_argument("--audit", default="", help="Audit log path (default: inside the workspace)")
    args = parser.parse_args()

    CONFIG.workspace = Path(args.workspace).expanduser().resolve()
    CONFIG.workspace.mkdir(parents=True, exist_ok=True)
    CONFIG.memory_mb = max(128, args.memory_mb)
    CONFIG.cpu_seconds = max(1, args.cpu_seconds)
    # The 1 GB rule is a ceiling on the ceiling: configurable downwards only.
    CONFIG.max_bytes = min(HARD_MAX_BYTES, max(1, args.max_data_mb) * 1024 * 1024)
    CONFIG.op_timeout = max(5, args.timeout_s)
    CONFIG.audit_path = (Path(args.audit).expanduser().resolve() if args.audit
                         else CONFIG.workspace / "pandas_frames_audit.jsonl")

    log(f"workspace {CONFIG.workspace} · data ceiling {CONFIG.max_bytes / 1e6:.0f} MB · "
        f"worker {CONFIG.memory_mb} MB / {CONFIG.cpu_seconds}s CPU · audit {CONFIG.audit_path.name}")
    server.run()


if __name__ == "__main__":
    mp.freeze_support()
    main()
