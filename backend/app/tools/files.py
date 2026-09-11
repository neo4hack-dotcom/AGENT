"""Files, confined to one workspace directory.

Every path the model gives is resolved and then checked to be inside the workspace root.
Resolution happens first on purpose: `../` and a symlink both only show their true target
after resolving, and checking the string before that is the classic way this goes wrong.
"""

from __future__ import annotations

from pathlib import Path

TEXT_SUFFIXES = {".txt", ".md", ".json", ".csv", ".tsv", ".yml", ".yaml", ".py", ".js",
                 ".ts", ".tsx", ".jsx", ".html", ".css", ".sql", ".sh", ".toml", ".ini",
                 ".cfg", ".xml", ".log", ".env", ".rst", ".go", ".rs", ".java", ".rb"}


class OutsideWorkspace(ValueError):
    pass


def resolve(workspace: Path, path: str) -> Path:
    workspace = workspace.resolve()
    candidate = (workspace / (path or "").lstrip("/")).resolve()
    if candidate != workspace and workspace not in candidate.parents:
        raise OutsideWorkspace(
            f"'{path}' resolves outside the workspace. Files live under {workspace}; "
            f"connect the Filesystem MCP server to reach anywhere else.")
    return candidate


def list_files(workspace: Path, path: str = "") -> dict:
    try:
        target = resolve(workspace, path)
    except OutsideWorkspace as exc:
        return {"ok": False, "error": str(exc)}
    if not target.exists():
        return {"ok": False, "error": f"'{path or '.'}' does not exist in the workspace."}
    if target.is_file():
        stat = target.stat()
        return {"ok": True, "entries": [{"name": target.name, "type": "file", "size": stat.st_size}]}
    entries = []
    for child in sorted(target.iterdir(), key=lambda c: (c.is_file(), c.name.lower())):
        if child.name.startswith("."):
            continue
        entries.append({
            "name": child.name,
            "type": "dir" if child.is_dir() else "file",
            "size": child.stat().st_size if child.is_file() else 0,
        })
    return {"ok": True, "path": str(target.relative_to(workspace.resolve())) or ".",
            "entries": entries[:500], "count": len(entries)}


def read_file(workspace: Path, path: str, max_bytes: int = 400_000) -> dict:
    try:
        target = resolve(workspace, path)
    except OutsideWorkspace as exc:
        return {"ok": False, "error": str(exc)}
    if not target.is_file():
        return {"ok": False, "error": f"'{path}' is not a file in the workspace."}
    raw = target.read_bytes()[:max_bytes]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        if target.suffix.lower() not in TEXT_SUFFIXES:
            return {"ok": False,
                    "error": f"'{path}' is binary ({target.stat().st_size} bytes) — read it with "
                             f"run_python instead, where you can parse it properly."}
        text = raw.decode("utf-8", errors="replace")
    truncated = target.stat().st_size > max_bytes
    return {"ok": True, "path": path, "size": target.stat().st_size,
            "text": text + ("\n[truncated]" if truncated else "")}


def import_file(workspace: Path, source: str, name: str, granted: list[str],
                max_bytes: int = 200_000_000) -> dict:
    """Copy a file into the workspace byte for byte.

    This exists because of one observed failure, and it is worth stating plainly: asked to
    move a six-row CSV into a directory a pandas server could read, the agent read it,
    retyped it through a `content` argument — and filled in fifteen years of data that were
    never in the file, in perfectly regular increments. It then ran genuine pandas on the
    invented file and reported the results as fact. Every guard downstream was satisfied,
    because everything downstream *was* real.

    Data that passes through a language model as text can be completed, abbreviated or
    tidied. So here it does not pass through at all: the model names a source and a
    destination, and the bytes are copied by this function.

    The source must sit inside a directory the user has explicitly granted to a connected
    server. That is the user's own grant, honoured — not a new privilege.
    """
    origin = Path(source or "").expanduser()
    if not origin.is_absolute():
        return {"ok": False, "error": f"'{source}' must be an absolute path."}
    try:
        origin = origin.resolve()
    except OSError as exc:
        return {"ok": False, "error": f"Cannot resolve '{source}': {exc}"}
    allowed = [Path(root).expanduser().resolve() for root in granted if root]
    if not any(origin == root or root in origin.parents for root in allowed):
        where = ", ".join(str(root) for root in allowed) or "no directory"
        return {"ok": False,
                "error": f"'{source}' is outside every directory this agent has been granted. "
                         f"Granted: {where}. Connect a server rooted where the file lives, or "
                         f"copy it there yourself."}
    if not origin.is_file():
        return {"ok": False, "error": f"'{source}' is not a file."}
    size = origin.stat().st_size
    if size > max_bytes:
        return {"ok": False,
                "error": f"'{source}' is {size / 1e6:.0f} MB; the import ceiling is "
                         f"{max_bytes / 1e6:.0f} MB."}
    try:
        target = resolve(workspace, name.strip() or origin.name)
    except OutsideWorkspace as exc:
        return {"ok": False, "error": str(exc)}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(origin.read_bytes())
    return {"ok": True, "name": target.name, "bytes": size, "source": str(origin)}


def write_file(workspace: Path, path: str, content: str) -> dict:
    try:
        target = resolve(workspace, path)
    except OutsideWorkspace as exc:
        return {"ok": False, "error": str(exc)}
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    target.write_text(content or "", encoding="utf-8")
    return {"ok": True, "path": path, "bytes": len(content or ""),
            "action": "overwrote" if existed else "created"}
