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
        "id": "filesystem", "name": "Système de fichiers", "vendor": "Model Context Protocol",
        "category": "Fichiers", "accent": "amber",
        "description": "Lire, écrire et chercher des fichiers dans un seul dossier que vous choisissez. "
                       "Tout ce qui est en dehors reste invisible pour l'agent.",
        "transport": "stdio", "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "{root_path}"],
        "params": [_p("root_path", "Dossier à exposer", placeholder="/Users/vous/Documents",
                      help="Le seul dossier que ce serveur peut voir.")],
        "tags": ["read", "write", "local"],
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
    },
    {
        "id": "git", "name": "Git", "vendor": "Model Context Protocol",
        "category": "Fichiers", "accent": "amber",
        "description": "Inspecter un dépôt local : statut, diffs, journal, branches, commits.",
        "transport": "stdio", "command": "uvx",
        "args": ["mcp-server-git", "--repository", "{repo_path}"],
        "params": [_p("repo_path", "Chemin du dépôt", placeholder="/Users/vous/code/projet")],
        "tags": ["read", "local", "vcs"],
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/git",
    },
    # -------------------------------------------------------------------- data
    {
        "id": "sqlite", "name": "SQLite", "vendor": "Model Context Protocol",
        "category": "Données", "accent": "emerald",
        "description": "Interroger et explorer une base SQLite locale, schéma compris.",
        "transport": "stdio", "command": "uvx",
        "args": ["mcp-server-sqlite", "--db-path", "{db_path}"],
        "params": [_p("db_path", "Fichier de base", placeholder="/Users/vous/data/app.db")],
        "tags": ["sql", "read", "local"],
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/sqlite",
    },
    {
        "id": "postgres", "name": "PostgreSQL", "vendor": "Model Context Protocol",
        "category": "Données", "accent": "emerald",
        "description": "Accès SQL en lecture seule à une base Postgres, avec introspection du schéma.",
        "transport": "stdio", "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-postgres", "{connection_string}"],
        "params": [_p("connection_string", "Chaîne de connexion", secret=True,
                      placeholder="postgresql://user:pass@localhost:5432/db")],
        "tags": ["sql", "read"],
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/postgres",
    },
    # --------------------------------------------------------------------- web
    {
        "id": "fetch", "name": "Web Fetch", "vendor": "Model Context Protocol",
        "category": "Web", "accent": "sky",
        "description": "Récupérer une URL et la convertir en markdown propre. Lumen a déjà un outil natif ; "
                       "celui-ci ajoute le respect de robots.txt et la lecture par morceaux.",
        "transport": "stdio", "command": "uvx",
        "args": ["mcp-server-fetch"],
        "params": [],
        "tags": ["read", "web"],
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/fetch",
    },
    {
        "id": "playwright", "name": "Navigateur Playwright", "vendor": "Microsoft",
        "category": "Web", "accent": "sky",
        "description": "Piloter un vrai navigateur : naviguer, cliquer, remplir des formulaires, lire l'arbre "
                       "d'accessibilité, capturer l'écran. Pour les pages qu'un simple fetch ne rend pas.",
        "transport": "stdio", "command": "npx",
        "args": ["-y", "@playwright/mcp@latest", "--headless"],
        "params": [],
        "tags": ["browser", "write", "automation"],
        "docs": "https://github.com/microsoft/playwright-mcp",
    },
    {
        "id": "context7", "name": "Documentation Context7", "vendor": "Upstash",
        "category": "Web", "accent": "sky",
        "description": "Documentation et exemples à jour pour des milliers de bibliothèques, récupérés à la "
                       "demande plutôt que restitués de mémoire.",
        "transport": "stdio", "command": "npx",
        "args": ["-y", "@upstash/context7-mcp"],
        "params": [],
        "tags": ["read", "docs"],
        "docs": "https://github.com/upstash/context7",
    },
    {
        "id": "brave-search", "name": "Brave Search", "vendor": "Brave",
        "category": "Web", "accent": "sky",
        "description": "Recherche web et locale via l'API Brave Search. Nécessite une clé gratuite.",
        "transport": "stdio", "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-brave-search"],
        "env": {"BRAVE_API_KEY": "{api_key}"},
        "params": [_p("api_key", "Clé d'API Brave", secret=True,
                      help="Offre gratuite sur brave.com/search/api")],
        "tags": ["search", "read"],
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/brave-search",
    },
    # ----------------------------------------------------------------- thinking
    {
        "id": "memory", "name": "Mémoire en graphe", "vendor": "Model Context Protocol",
        "category": "Raisonnement", "accent": "violet",
        "description": "Un graphe de connaissances persistant : entités et relations que l'agent construit et "
                       "interroge d'une conversation à l'autre.",
        "transport": "stdio", "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"],
        "params": [],
        "tags": ["memory", "write"],
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
    },
    {
        "id": "sequential-thinking", "name": "Raisonnement séquentiel",
        "vendor": "Model Context Protocol", "category": "Raisonnement", "accent": "violet",
        "description": "Un brouillon structuré pour les longues chaînes de raisonnement, capables de bifurquer "
                       "et de se corriger.",
        "transport": "stdio", "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
        "params": [],
        "tags": ["reasoning"],
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking",
    },
    {
        "id": "time", "name": "Heure & fuseaux", "vendor": "Model Context Protocol",
        "category": "Raisonnement", "accent": "violet",
        "description": "Heure courante partout dans le monde et conversions entre fuseaux.",
        "transport": "stdio", "command": "uvx",
        "args": ["mcp-server-time"],
        "params": [],
        "tags": ["read"],
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/time",
    },
    # -------------------------------------------------------------------- work
    {
        "id": "github", "name": "GitHub", "vendor": "GitHub",
        "category": "Travail", "accent": "rose",
        "description": "Issues, pull requests, recherche de code et fichiers de dépôt sur GitHub, via "
                       "l'endpoint MCP hébergé par GitHub.",
        "transport": "http", "url": "https://api.githubcopilot.com/mcp/",
        "headers": {"Authorization": "Bearer {token}"},
        "params": [_p("token", "Jeton d'accès personnel GitHub", secret=True,
                      placeholder="ghp_…",
                      help="github.com → Settings → Developer settings → Personal access tokens")],
        "tags": ["read", "write", "vcs"],
        "docs": "https://github.com/github/github-mcp-server",
    },
    {
        "id": "slack", "name": "Slack", "vendor": "Model Context Protocol",
        "category": "Travail", "accent": "rose",
        "description": "Lire les canaux et publier des messages en tant que bot Slack.",
        "transport": "stdio", "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-slack"],
        "env": {"SLACK_BOT_TOKEN": "{bot_token}", "SLACK_TEAM_ID": "{team_id}"},
        "params": [_p("bot_token", "Jeton OAuth du bot", secret=True, placeholder="xoxb-…"),
                   _p("team_id", "Identifiant d'équipe", placeholder="T01234567")],
        "tags": ["read", "write", "chat"],
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/slack",
    },
    {
        "id": "notion", "name": "Notion", "vendor": "Notion",
        "category": "Travail", "accent": "rose",
        "description": "Chercher, lire et écrire des pages et bases Notion.",
        "transport": "stdio", "command": "npx",
        "args": ["-y", "@notionhq/notion-mcp-server"],
        "env": {"NOTION_TOKEN": "{token}"},
        "params": [_p("token", "Jeton d'intégration Notion", secret=True, placeholder="ntn_…",
                      help="notion.so/my-integrations — puis partagez-lui les pages.")],
        "tags": ["read", "write", "docs"],
        "docs": "https://github.com/makenotion/notion-mcp-server",
    },
    # ------------------------------------------------------------------ testing
    {
        "id": "everything", "name": "Everything (serveur de référence)",
        "vendor": "Model Context Protocol", "category": "Test", "accent": "zinc",
        "description": "Le serveur de référence du protocole : tous les types d'outils, ressources et prompts "
                       "au même endroit. Utile pour vérifier la plomberie MCP de Lumen de bout en bout.",
        "transport": "stdio", "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-everything"],
        "params": [],
        "tags": ["test"],
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/everything",
    },
]

CATALOG_BY_ID: dict[str, dict[str, Any]] = {entry["id"]: entry for entry in CATALOG}

CATEGORIES = ["Fichiers", "Données", "Web", "Raisonnement", "Travail", "Test"]


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
