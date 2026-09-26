"""The MCP library: servers connectable in two clicks, all of them shipped with this app.

**Nothing on this shelf is downloaded.** Every recipe runs a server from `mcp_servers/`
on the interpreter already serving the API, written against the Python standard library
(pandas for the dataframe server, pinned in requirements.txt). A private network cannot
fetch `npx` or `uvx` packages at connect time, and a shelf that only works online is a
shelf that fails on the first day of a real deployment. Anything else — a governed SQL
gateway to Oracle or ClickHouse, a market-data service, a risk engine — is one "Custom
server" form away, over stdio or HTTP, inside the network.

`migrate_to_bundled` moves servers installed from the old download-on-demand recipes
(filesystem, SQLite, time) onto their bundled equivalents, keeping their paths.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

BUNDLED = Path(__file__).resolve().parents[3] / "mcp_servers"
PYTHON = sys.executable


def _p(key: str, label: str, *, required: bool = True, secret: bool = False,
       placeholder: str = "", help: str = "", default_from: str = "") -> dict[str, Any]:
    """`default_from` names a value the app supplies when the field is left blank —
    the agent's own workspace, say — so a server can have a useful default without the
    catalog importing the app's configuration."""
    return {"key": key, "label": label, "required": required, "secret": secret,
            "placeholder": placeholder, "help": help, "default_from": default_from}


def _script(name: str) -> str:
    return str(BUNDLED / name / "server.py")


CATALOG: list[dict[str, Any]] = [
    {
        "id": "filesystem", "name": "Files", "vendor": "Bundled with this app",
        "category": "Files", "accent": "amber",
        "description": "Read and search the files under one directory you choose — CSV, JSON, "
                       "reports, logs. Read-only: nothing outside that directory is visible, "
                       "and nothing inside it can be changed.",
        "transport": "stdio", "command": PYTHON,
        "args": [_script("files"), "{root_path}", "--read-only"],
        "params": [_p("root_path", "Directory to expose", placeholder="/data/reports",
                      help="The only folder this server can see.")],
        "tags": ["files", "read", "bundled"],
    },
    {
        "id": "sqlite", "name": "SQLite", "vendor": "Bundled with this app",
        "category": "Data", "accent": "emerald",
        "description": "Query a SQLite database: tables, schemas, SELECT. Opened read-only by "
                       "SQLite itself, with a time limit and a row ceiling on every query.",
        "transport": "stdio", "command": PYTHON,
        "args": [_script("sqlite_db"), "--db-path", "{db_path}"],
        "params": [_p("db_path", "Database file", placeholder="/data/trades.db")],
        "tags": ["sql", "read", "bundled"],
    },
    {
        "id": "pandas-frames", "name": "Pandas Frames", "vendor": "Bundled with this app",
        "category": "Data", "accent": "emerald",
        "description": "Load CSV, Excel and Parquet files into real pandas dataframes and let "
                       "the agent query them — joins, group-bys, statistics — through a bounded, "
                       "audited expression sandbox. Every operation runs in a killable worker "
                       "with memory and CPU ceilings, and every call is logged with what it ran.",
        "transport": "stdio", "command": PYTHON,
        "args": [_script("pandas_frames"), "--workspace", "{workspace}"],
        "params": [_p("workspace", "Data directory", required=False, default_from="workspace",
                      placeholder="leave blank to use this app's own workspace",
                      help="The only directory the server may read from or export into. Left "
                           "blank it uses the app's workspace — the same directory "
                           "workspace_write writes to, so data the agent fetches and saves is "
                           "immediately loadable here.")],
        "tags": ["pandas", "data", "bundled"],
    },
    {
        "id": "time", "name": "Time & time zones", "vendor": "Bundled with this app",
        "category": "Reasoning", "accent": "violet",
        "description": "Current time in any time zone, and conversions between them — from the "
                       "zone database Python ships with.",
        "transport": "stdio", "command": PYTHON,
        "args": [_script("clock")],
        "params": [],
        "tags": ["time", "read", "bundled"],
    },
]


# Old download-on-demand packages, and the bundled server that replaces each: the
# argument that carried the path is kept, the launcher is not.
_REPLACED = {
    "@modelcontextprotocol/server-filesystem": ("filesystem", "files"),
    "mcp-server-sqlite": ("sqlite", "sqlite_db"),
    "mcp-server-time": ("time", "clock"),
}


def migrate_to_bundled(servers: dict[str, dict]) -> list[str]:
    """Point servers installed from a download-on-demand recipe at the bundled one.

    Returns the names of the servers moved. Servers whose package has no bundled
    equivalent are left as they are — Diagnostics lists them, since they will not start
    without network access or an offline package cache.
    """
    moved = []
    for server in servers.values():
        if server.get("transport") != "stdio" or server.get("command") not in ("npx", "uvx"):
            continue
        args = [str(a) for a in server.get("args") or []]
        package = next((pkg for pkg in _REPLACED if any(a == pkg or a.startswith(pkg + "@") for a in args)), None)
        if package is None:
            continue
        catalog_id, folder = _REPLACED[package]
        if folder == "files":
            roots = [a for a in args if a.startswith("/")]
            new_args = [_script("files"), *roots] if roots else None
        elif folder == "sqlite_db":
            db = args[args.index("--db-path") + 1] if "--db-path" in args[:-1] else ""
            new_args = [_script("sqlite_db"), "--db-path", db] if db else None
        else:
            new_args = [_script("clock")]
        if new_args is None:
            continue
        server.update({"command": PYTHON, "args": new_args, "catalog_id": catalog_id,
                       "migrated_from": package})
        moved.append(server.get("name") or server.get("id", ""))
    return moved


def downloads_at_start(servers: dict[str, dict]) -> list[str]:
    """Servers that fetch their package when they start — they fail in a private network."""
    return [s.get("name") or s.get("id", "") for s in servers.values()
            if s.get("enabled", True) and s.get("transport") == "stdio"
            and s.get("command") in ("npx", "uvx", "pipx", "bunx", "pnpm", "yarn", "dlx")]


CATALOG_BY_ID: dict[str, dict[str, Any]] = {entry["id"]: entry for entry in CATALOG}

CATEGORIES = ["Files", "Data", "Reasoning"]


def instantiate(catalog_id: str, values: dict[str, str],
                context: dict[str, str] | None = None) -> dict[str, Any]:
    """Turn a recipe plus the user's answers into a concrete server config.

    A *required* parameter left blank raises instead of being substituted with its
    placeholder: a server silently pointed at an example path is worse than one that
    refuses to be created. An optional one may fall back to a value the app supplies
    through `context` — which is a real default, not a guess dressed up as one.
    """
    entry = CATALOG_BY_ID.get(catalog_id)
    if entry is None:
        raise KeyError(f"Unknown catalog entry '{catalog_id}'")
    context = context or {}
    resolved: dict[str, str] = {}
    for param in entry.get("params", []):
        given = (values.get(param["key"]) or "").strip()
        if not given and param.get("default_from"):
            given = (context.get(param["default_from"]) or "").strip()
        resolved[param["key"]] = given
    missing = [p["label"] for p in entry.get("params", [])
               if p.get("required") and not resolved.get(p["key"])]
    if missing:
        raise ValueError(f"Missing required value(s): {', '.join(missing)}")

    def fill(text: str) -> str:
        for param in entry.get("params", []):
            text = text.replace("{" + param["key"] + "}", resolved.get(param["key"], ""))
        return text

    return {
        "catalog_id": entry["id"],
        "name": entry["name"],
        "description": entry["description"],
        "category": entry["category"],
        "accent": entry["accent"],
        "transport": entry["transport"],
        "command": entry.get("command", ""),
        "args": [fill(a) for a in entry.get("args", [])],
        "env": {k: fill(v) for k, v in (entry.get("env") or {}).items()},
        "url": fill(entry.get("url", "")),
        "headers": {k: fill(v) for k, v in (entry.get("headers") or {}).items()},
        "docs": entry.get("docs", ""),
    }
