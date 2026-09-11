"""The MCP library: a curated shelf of servers connectable in two clicks.

**Everything on this shelf runs as a process on this machine and needs no third-party
account.** That is the selection rule, and it is the point: the shelf is what you can
connect without deciding to trust anyone, so browsing it never presents a signup as a
capability. A server that talks to a hosted API — GitHub, Slack, Notion, a search
provider — is still one "Custom server" form away, over stdio or HTTP. It is simply a
decision you make deliberately rather than one the shelf makes look routine.

Two entries here do reach the network *at your instruction* rather than on their own:
Web Fetch opens the URL you name, and Playwright drives a browser to the page you name.
Both run locally and hold no credentials.

Each entry is a *recipe*, not a connection — it names a published package and the
parameters it needs. Nothing is bundled or vendored: the command runs against whatever
`npx`/`uvx` resolves at connect time, and a package that cannot be fetched fails visibly
on its card rather than silently doing nothing.
"""

from __future__ import annotations

from typing import Any


def _p(key: str, label: str, *, required: bool = True, secret: bool = False,
       placeholder: str = "", help: str = "") -> dict[str, Any]:
    return {"key": key, "label": label, "required": required, "secret": secret,
            "placeholder": placeholder, "help": help}


CATALOG: list[dict[str, Any]] = [
    # ------------------------------------------------------------- files & local
    {
        "id": "filesystem", "name": "Filesystem", "vendor": "Model Context Protocol",
        "category": "Files", "accent": "amber",
        "description": "Read, write and search files inside one directory you choose. Everything outside "
                       "that path stays invisible to the agent.",
        "transport": "stdio", "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "{root_path}"],
        "params": [_p("root_path", "Directory to expose", placeholder="/Users/you/Documents",
                      help="The only folder this server can see.")],
        "tags": ["read", "write", "local"],
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
    },
    {
        "id": "git", "name": "Git", "vendor": "Model Context Protocol",
        "category": "Files", "accent": "amber",
        "description": "Inspect a local repository: status, diffs, log, branches, commits.",
        "transport": "stdio", "command": "uvx",
        "args": ["mcp-server-git", "--repository", "{repo_path}"],
        "params": [_p("repo_path", "Repository path", placeholder="/Users/you/code/project")],
        "tags": ["read", "local", "vcs"],
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/git",
    },
    # -------------------------------------------------------------------- data
    {
        "id": "sqlite", "name": "SQLite", "vendor": "Model Context Protocol",
        "category": "Data", "accent": "emerald",
        "description": "Query and explore a local SQLite database, schema included.",
        # The published server calls `Server.list_resources`, removed in mcp 1.10 — it
        # crashes on import against a current SDK. Pinning here is the difference between
        # a recipe that works and one that fails with an upstream traceback on its card.
        "transport": "stdio", "command": "uvx",
        "args": ["--with", "mcp<1.10", "mcp-server-sqlite", "--db-path", "{db_path}"],
        "params": [_p("db_path", "Database file", placeholder="/Users/you/data/app.db")],
        "tags": ["sql", "read", "local"],
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/sqlite",
    },
    {
        "id": "postgres", "name": "PostgreSQL", "vendor": "Model Context Protocol",
        "category": "Data", "accent": "emerald",
        "description": "Read-only SQL access to a Postgres database, with schema introspection.",
        "transport": "stdio", "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-postgres", "{connection_string}"],
        "params": [_p("connection_string", "Connection string", secret=True,
                      placeholder="postgresql://user:pass@localhost:5432/db")],
        "tags": ["sql", "read"],
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/postgres",
    },
    # --------------------------------------------------------------------- web
    {
        "id": "fetch", "name": "Web Fetch", "vendor": "Model Context Protocol",
        "category": "Web", "accent": "sky",
        "description": "Fetch any URL and convert the page to clean markdown. Agent has a built-in fetcher "
                       "too — this one adds robots.txt handling and chunked reads.",
        "transport": "stdio", "command": "uvx",
        "args": ["mcp-server-fetch"],
        "params": [],
        "tags": ["read", "web"],
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/fetch",
    },
    {
        "id": "playwright", "name": "Playwright Browser", "vendor": "Microsoft",
        "category": "Web", "accent": "sky",
        "description": "Drive a real browser: navigate, click, fill forms, read the accessibility tree, "
                       "screenshot. For pages a plain fetch cannot render.",
        "transport": "stdio", "command": "npx",
        "args": ["-y", "@playwright/mcp@latest", "--headless"],
        "params": [],
        "tags": ["browser", "write", "automation"],
        "docs": "https://github.com/microsoft/playwright-mcp",
    },
    # ----------------------------------------------------------------- thinking
    {
        "id": "memory", "name": "Knowledge Graph Memory", "vendor": "Model Context Protocol",
        "category": "Reasoning", "accent": "violet",
        "description": "A persistent knowledge graph of entities and relations the agent builds and queries "
                       "across conversations.",
        "transport": "stdio", "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"],
        "params": [],
        "tags": ["memory", "write"],
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
    },
    {
        "id": "sequential-thinking", "name": "Sequential Thinking",
        "vendor": "Model Context Protocol", "category": "Reasoning", "accent": "violet",
        "description": "A structured scratchpad for long chains of reasoning that can branch and revise "
                       "themselves.",
        "transport": "stdio", "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
        "params": [],
        "tags": ["reasoning"],
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking",
    },
    {
        "id": "time", "name": "Time & Timezones", "vendor": "Model Context Protocol",
        "category": "Reasoning", "accent": "violet",
        "description": "Current time anywhere, and conversions between timezones.",
        "transport": "stdio", "command": "uvx",
        "args": ["mcp-server-time"],
        "params": [],
        "tags": ["read"],
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/time",
    },
    # -------------------------------------------------------------------- work
    # ------------------------------------------------------------------ testing
    {
        "id": "everything", "name": "Everything (reference server)",
        "vendor": "Model Context Protocol", "category": "Testing", "accent": "zinc",
        "description": "The protocol's own reference server: every tool, resource and prompt type in one "
                       "place. Useful to verify the MCP plumbing end to end.",
        "transport": "stdio", "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-everything"],
        "params": [],
        "tags": ["test"],
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/everything",
    },
]

CATALOG_BY_ID: dict[str, dict[str, Any]] = {entry["id"]: entry for entry in CATALOG}

CATEGORIES = ["Files", "Data", "Web", "Reasoning", "Testing"]


def instantiate(catalog_id: str, values: dict[str, str]) -> dict[str, Any]:
    """Turn a recipe plus the user's answers into a concrete server config.

    A required parameter left blank raises instead of being substituted with its
    placeholder: a server silently pointed at an example path is worse than one that
    refuses to be created.
    """
    entry = CATALOG_BY_ID.get(catalog_id)
    if entry is None:
        raise KeyError(f"Unknown catalog entry '{catalog_id}'")
    missing = [p["label"] for p in entry.get("params", [])
               if p.get("required") and not (values.get(p["key"]) or "").strip()]
    if missing:
        raise ValueError(f"Missing required value(s): {', '.join(missing)}")

    def fill(text: str) -> str:
        for param in entry.get("params", []):
            text = text.replace("{" + param["key"] + "}", (values.get(param["key"]) or "").strip())
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
