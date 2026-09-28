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

import re
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
        "id": "data-catalog", "name": "Data catalog", "vendor": "Your catalog, e.g. DataLoom",
        "category": "Catalog", "accent": "violet",
        "description": "Connect the enterprise data catalog over MCP (HTTP, inside the network). It "
                       "is not queried for figures: the agent reads it to understand the sources — "
                       "dataset and column definitions, calculations, glossary, lineage — and Admin "
                       "can import its definitions into each source's notes.",
        "transport": "http", "url": "{url}",
        "headers": {"Authorization": "Bearer {token}"},
        "params": [_p("url", "MCP endpoint", placeholder="http://dataloom.corp.internal:3001/mcp",
                      help="The catalog's MCP URL, inside the private network."),
                   _p("token", "Access token", secret=True,
                      help="Issued by the catalog's admin (DataLoom: Admin → MCP → token).")],
        "role": "catalog",
        "tags": ["catalog", "metadata", "glossary", "lineage"],
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

# Variables that change what an interpreter loads before the server's own code runs. Under
# AGENT_ALLOW_CUSTOM_COMMANDS=false a bundled server keeps its script, and these would be a
# way to swap it: PYTHONPATH at a folder the agent can write to, NODE_OPTIONS=--require….
_LOADER_ENV = re.compile(r"^(PYTHON|LD_|DYLD_|PERL5|NODE_OPTIONS$|NODE_PATH$|PATH$|PATHEXT$|COMSPEC$|"
                         r"RUBYOPT$|RUBYLIB$|JAVA_TOOL_OPTIONS$|_JAVA_OPTIONS$|JDK_JAVA_OPTIONS$|"
                         r"BASH_ENV$|ENV$)", re.IGNORECASE)


def bundled_folder(server: dict) -> str:
    """The bundled server a stdio config runs (`files`, `pandas_frames`…), or ""."""
    args = server.get("args") or []
    if server.get("transport", "stdio") != "stdio" or not args:
        return ""
    try:
        script = Path(str(args[0])).resolve()
    except (OSError, ValueError):
        return ""
    if script.name == "server.py" and script.parent.parent == BUNDLED.resolve() and script.is_file():
        return script.parent.name
    return ""


def is_pandas(server: dict) -> bool:
    return server.get("catalog_id") == "pandas-frames" or bundled_folder(server) == "pandas_frames"


def deployment_refusal(server: dict, settings) -> str | None:
    """Why the deployment's own switches forbid this server, or None. Checked where a server
    is added, edited and — the one place that cannot be walked around — started."""
    name = server.get("name") or "This server"
    if is_pandas(server) and not settings.enable_pandas:
        return f"{name} is switched off for this deployment (AGENT_ENABLE_PANDAS=false)."
    if server.get("transport", "stdio") == "stdio" and not settings.allow_custom_commands:
        if not bundled_folder(server):
            return (f"{name}: this deployment runs only the servers bundled with the app "
                    f"(AGENT_ALLOW_CUSTOM_COMMANDS=false), and '{server.get('command') or '?'}' "
                    f"with these arguments is not one of them. Reach other sources over HTTP.")
        loaders = sorted(k for k in (server.get("env") or {}) if _LOADER_ENV.match(str(k)))
        if loaders:
            return (f"{name}: {', '.join(loaders)} would change what the interpreter loads, which "
                    f"this deployment does not allow (AGENT_ALLOW_CUSTOM_COMMANDS=false).")
    return None


def launch_command(server: dict, settings) -> str:
    """The program to start. Locked down, a bundled server always runs on this app's own
    interpreter — whatever command was saved with it."""
    if not settings.allow_custom_commands and bundled_folder(server):
        return PYTHON
    return str(server.get("command") or "")


def visible_catalog(settings) -> list[dict[str, Any]]:
    """The library as this deployment offers it."""
    return [entry for entry in CATALOG if entry["id"] != "pandas-frames" or settings.enable_pandas]

CATEGORIES = ["Files", "Data", "Catalog", "Reasoning"]


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
        "role": entry.get("role", ""),
    }
