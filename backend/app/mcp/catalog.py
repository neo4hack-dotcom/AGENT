"""The MCP library: a curated shelf of servers connectable in two clicks.

Each entry is a *recipe*, not a connection — it names a published package or endpoint and
the parameters it needs. Nothing is bundled or vendored: the command runs against
whatever `npx`/`uvx` resolves on this machine at connect time, and a package that cannot
be fetched fails visibly on its card rather than silently doing nothing.

Adding an entry is data, not code. Anything not on this shelf is one "Custom server" form
away — the shelf is a convenience, never a restriction.
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
        "transport": "stdio", "command": "uvx",
        "args": ["mcp-server-sqlite", "--db-path", "{db_path}"],
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
    {
        "id": "context7", "name": "Context7 Docs", "vendor": "Upstash",
        "category": "Web", "accent": "sky",
        "description": "Up-to-date documentation and code examples for thousands of libraries, pulled on "
                       "demand instead of recalled from training data.",
        "transport": "stdio", "command": "npx",
        "args": ["-y", "@upstash/context7-mcp"],
        "params": [],
        "tags": ["read", "docs"],
        "docs": "https://github.com/upstash/context7",
    },
    {
        "id": "brave-search", "name": "Brave Search", "vendor": "Brave",
        "category": "Web", "accent": "sky",
        "description": "Web and local search through the Brave Search API. Needs a free API key.",
        "transport": "stdio", "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-brave-search"],
        "env": {"BRAVE_API_KEY": "{api_key}"},
        "params": [_p("api_key", "Brave API key", secret=True,
                      help="Free tier at brave.com/search/api")],
        "tags": ["search", "read"],
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/brave-search",
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
    {
        "id": "github", "name": "GitHub", "vendor": "GitHub",
        "category": "Work", "accent": "rose",
        "description": "Issues, pull requests, code search and repository files on GitHub, through "
                       "GitHub's own hosted MCP endpoint.",
        "transport": "http", "url": "https://api.githubcopilot.com/mcp/",
        "headers": {"Authorization": "Bearer {token}"},
        "params": [_p("token", "GitHub personal access token", secret=True,
                      placeholder="ghp_…",
                      help="github.com → Settings → Developer settings → Personal access tokens")],
        "tags": ["read", "write", "vcs"],
        "docs": "https://github.com/github/github-mcp-server",
    },
    {
        "id": "slack", "name": "Slack", "vendor": "Model Context Protocol",
        "category": "Work", "accent": "rose",
        "description": "Read channels and post messages as a Slack bot user.",
        "transport": "stdio", "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-slack"],
        "env": {"SLACK_BOT_TOKEN": "{bot_token}", "SLACK_TEAM_ID": "{team_id}"},
        "params": [_p("bot_token", "Bot user OAuth token", secret=True, placeholder="xoxb-…"),
                   _p("team_id", "Team ID", placeholder="T01234567")],
        "tags": ["read", "write", "chat"],
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/slack",
    },
    {
        "id": "notion", "name": "Notion", "vendor": "Notion",
        "category": "Work", "accent": "rose",
        "description": "Search, read and write Notion pages and databases.",
        "transport": "stdio", "command": "npx",
        "args": ["-y", "@notionhq/notion-mcp-server"],
        "env": {"NOTION_TOKEN": "{token}"},
        "params": [_p("token", "Notion integration token", secret=True, placeholder="ntn_…",
                      help="notion.so/my-integrations — then share the pages with it.")],
        "tags": ["read", "write", "docs"],
        "docs": "https://github.com/makenotion/notion-mcp-server",
    },
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

CATEGORIES = ["Files", "Data", "Web", "Reasoning", "Work", "Testing"]


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
