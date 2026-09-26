"""Files under one or more directories, over MCP, with the standard library only.

    python server.py /data/reports [/data/other …] [--read-only]

Bundled so that a folder of files needs no download. The tool names match the reference
filesystem server (`list_directory`, `read_text_file`, `search_files`, `get_file_info`,
`write_file`, `create_directory`, `list_allowed_directories`), so prompts and habits carry
over.

Every path is resolved — symlinks included — and must land inside one of the directories
given on the command line; `..` and a link pointing outside are refused alike. With
--read-only the write tools are not even offered.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _mcp_stdio import McpServer, log  # noqa: E402

MAX_READ_BYTES = 5_000_000
MAX_MATCHES = 500

server = McpServer("files", "1.0.0", "Files")
ROOTS: list[Path] = []


def _resolve(path: str) -> Path:
    raw = Path(path or ".").expanduser()
    candidate = (raw if raw.is_absolute() else (ROOTS[0] / raw)).resolve()
    for root in ROOTS:
        if candidate == root or root in candidate.parents:
            return candidate
    raise PermissionError(f"{path} is outside the allowed directories: {', '.join(map(str, ROOTS))}.")


def _obj(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or []}


S = {"type": "string"}


@server.tool("list_allowed_directories", "The directories this server may read.", _obj({}), read_only=True)
def list_allowed_directories() -> dict:
    return {"directories": [str(r) for r in ROOTS]}


@server.tool("list_directory", "Files and folders directly inside a directory, with sizes.",
             _obj({"path": S}, ["path"]), read_only=True)
def list_directory(path: str) -> dict:
    try:
        folder = _resolve(path)
    except PermissionError as exc:
        return {"error": str(exc)}
    if not folder.is_dir():
        return {"error": f"{path} is not a directory."}
    rows = []
    for entry in sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if entry.name.startswith("."):
            continue
        rows.append({"name": entry.name, "type": "dir" if entry.is_dir() else "file",
                     "bytes": entry.stat().st_size if entry.is_file() else None})
    return {"path": str(folder), "rows": rows}


@server.tool("read_text_file", "Read a text file (CSV, JSON, Markdown, logs…). head/tail read only the first or "
             "last N lines.", _obj({"path": S, "head": {"type": "integer"}, "tail": {"type": "integer"}}, ["path"]),
             read_only=True)
def read_text_file(path: str, head: int = 0, tail: int = 0) -> str | dict:
    try:
        target = _resolve(path)
    except PermissionError as exc:
        return {"error": str(exc)}
    if not target.is_file():
        return {"error": f"No file at {path}."}
    if target.stat().st_size > MAX_READ_BYTES and not (head or tail):
        return {"error": f"{path} is {target.stat().st_size:,} bytes; read it with head/tail, or load it "
                         f"into a dataframe."}
    text = target.read_text(encoding="utf-8", errors="replace")
    if head:
        return "\n".join(text.splitlines()[:head])
    if tail:
        return "\n".join(text.splitlines()[-tail:])
    return text


@server.tool("search_files", "Find files whose name matches a pattern (e.g. *.csv), under a directory.",
             _obj({"path": S, "pattern": S}, ["path", "pattern"]), read_only=True)
def search_files(path: str, pattern: str) -> dict:
    try:
        folder = _resolve(path)
    except PermissionError as exc:
        return {"error": str(exc)}
    wanted = pattern if any(c in pattern for c in "*?[") else f"*{pattern}*"
    matches = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if fnmatch.fnmatch(name.lower(), wanted.lower()):
                matches.append(str(Path(dirpath) / name))
                if len(matches) >= MAX_MATCHES:
                    return {"rows": [{"path": m} for m in matches], "truncated": True}
    return {"rows": [{"path": m} for m in matches]}


@server.tool("get_file_info", "Size, type and modification time of a file or directory.",
             _obj({"path": S}, ["path"]), read_only=True)
def get_file_info(path: str) -> dict:
    try:
        target = _resolve(path)
    except PermissionError as exc:
        return {"error": str(exc)}
    if not target.exists():
        return {"error": f"Nothing at {path}."}
    stat = target.stat()
    return {"path": str(target), "type": "dir" if target.is_dir() else "file", "bytes": stat.st_size,
            "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime))}


def _register_writes() -> None:
    @server.tool("write_file", "Create or overwrite a text file.", _obj({"path": S, "content": S}, ["path", "content"]))
    def write_file(path: str, content: str) -> dict:
        try:
            target = _resolve(path)
        except PermissionError as exc:
            return {"error": str(exc)}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(target), "bytes": target.stat().st_size}

    @server.tool("create_directory", "Create a directory (and its parents).", _obj({"path": S}, ["path"]))
    def create_directory(path: str) -> dict:
        try:
            target = _resolve(path)
        except PermissionError as exc:
            return {"error": str(exc)}
        target.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "path": str(target)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("roots", nargs="+")
    parser.add_argument("--read-only", action="store_true")
    args = parser.parse_args()
    for root in args.roots:
        path = Path(root).expanduser().resolve()
        if not path.is_dir():
            log(f"Not a directory: {path}")
            sys.exit(2)
        ROOTS.append(path)
    if not args.read_only:
        _register_writes()
    server.run()


if __name__ == "__main__":
    main()
