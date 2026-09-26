"""Saved MCP servers, live connections, and one flat index of every tool they expose.

This is the only layer that knows MCP exists. Everything above it sees a list of tools
with JSON-Schema inputs and gets back the same normalized `{ok, summary, text, data,
error}` shape from every call, so no part of the agent needs a special case for a
particular server.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from app.agent import trust
from app.errors import McpError
from app.mcp.protocol import HttpTransport, McpClient, StdioTransport, flatten_tool_result
from app.store import new_id, now

SECRET_HINTS = ("token", "key", "secret", "password", "passwd", "credential",
                "connection_string", "dsn", "auth")

# A tool whose name suggests a real-world effect is gated even when nothing declared it
# so. Matching the *target* rather than the action is what catches `slack__post_message`
# arriving through a generic call path.
WRITE_HINTS = ("send", "post", "delete", "remove", "create", "update", "write", "insert",
               "drop", "notify", "block", "publish", "escalate", "merge", "push", "commit",
               "upload", "install", "kill", "restart", "move", "rename", "execute")


def is_write_tool(name: str) -> bool:
    return any(hint in (name or "").lower() for hint in WRITE_HINTS)


def _mask(value: str) -> str:
    if not value:
        return ""
    return value[:3] + "•" * max(4, len(value) - 6) + value[-2:] if len(value) > 8 else "•" * len(value)


def mask_secrets(cfg: dict) -> dict:
    """A copy safe to send to the browser: secret-looking values are masked, never echoed
    back in full."""
    out = json.loads(json.dumps(cfg, default=str))
    for field in ("env", "headers"):
        section = out.get(field) or {}
        for key in list(section):
            if any(hint in key.lower() for hint in SECRET_HINTS):
                section[key] = _mask(str(section[key]))
    out["args"] = [_mask(a) if ("://" in a and "@" in a) else a for a in (out.get("args") or [])]
    return out


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return slug[:24] or "server"


# A ceiling that exists so one runaway tool cannot exhaust memory, not to trim for
# the context window — that happens later, after the full result is on disk.
MAX_RESULT_CHARS = 2_000_000

# Tool names only a data catalog uses. Two or more of these families, and a server is a
# catalog: documentation about data (datasets, definitions, glossary, lineage), not data.
_CATALOG_FAMILIES = (
    r"search_(catalog|datasets?|assets?|metadata)|catalog_search",
    r"(get|describe)_(dataset|asset|table)(_schema|_metadata)?$|dataset_schema",
    r"glossary",
    r"lineage",
    r"list_(datasets|assets|datamarts|data_products)",
    r"(get_)?column_definition|business_definition",
)


def looks_like_catalog(tools: list[dict]) -> bool:
    import re as _re
    names = [str(t.get("name") or "").lower() for t in tools]
    families = sum(1 for pattern in _CATALOG_FAMILIES if any(_re.search(pattern, n) for n in names))
    return families >= 2


class Connection:
    """One live server: its client, what it exposes, and why it is (or is not) up."""

    def __init__(self, server_id: str) -> None:
        self.server_id = server_id
        self.client: McpClient | None = None
        self.status = "disconnected"  # disconnected | connecting | connected | error
        self.error: str | None = None
        self.tools: list[dict] = []
        self.resources: list[dict] = []
        self.prompts: list[dict] = []
        self.connected_at: float = 0.0
        self.call_count = 0
        # What network this server's process can reach, in words — shown in Admin, so the
        # air gap is something an administrator can read, not something they must trust.
        self.fence = ""

    def snapshot(self) -> dict:
        client = self.client
        return {
            "status": self.status,
            "error": self.error,
            "tool_count": len(self.tools),
            "resource_count": len(self.resources),
            "prompt_count": len(self.prompts),
            "connected_at": self.connected_at or None,
            "call_count": self.call_count,
            "network": self.fence,
            "server_info": client.server_info if client else {},
            "protocol_version": client.negotiated_version if client else "",
            "diagnostics": (client.transport.diagnostics[-12:] if client else []),
        }


class McpRegistry:
    def __init__(self, store, bus, settings) -> None:
        self.store = store
        self.bus = bus
        self.settings = settings
        self.connections: dict[str, Connection] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._ready = asyncio.Event()

    # ---------------------------------------------------------------- servers
    def _lock(self, server_id: str) -> asyncio.Lock:
        return self._locks.setdefault(server_id, asyncio.Lock())

    def _unique_slug(self, base: str, exclude_id: str = "") -> str:
        taken = {s.get("slug") for sid, s in self.store.mcp_servers().items() if sid != exclude_id}
        slug = slugify(base)
        if slug not in taken:
            return slug
        for n in range(2, 100):
            candidate = f"{slug}{n}"
            if candidate not in taken:
                return candidate
        return f"{slug}_{new_id()[:4]}"

    async def add_server(self, cfg: dict) -> dict:
        server_id = cfg.get("id") or new_id(cfg.get("catalog_id") or "srv")
        server = {
            "id": server_id,
            "slug": self._unique_slug(cfg.get("catalog_id") or cfg.get("name") or "server"),
            "name": cfg.get("name") or "Unnamed server",
            "catalog_id": cfg.get("catalog_id") or "",
            "transport": cfg.get("transport") or "stdio",
            "command": cfg.get("command") or "",
            "args": cfg.get("args") or [],
            "env": cfg.get("env") or {},
            "cwd": cfg.get("cwd") or "",
            "url": cfg.get("url") or "",
            "headers": cfg.get("headers") or {},
            "description": cfg.get("description") or "",
            "accent": cfg.get("accent") or "sky",
            "category": cfg.get("category") or "Custom",
            "docs": cfg.get("docs") or "",
            "enabled": bool(cfg.get("enabled", True)),
            "auto_approve": bool(cfg.get("auto_approve", False)),
            # "" = decided from the configuration, "local" = loopback only, "internal" = may
            # reach hosts inside the private network. See app/network.py.
            "network": cfg.get("network") if cfg.get("network") in ("local", "internal") else "",
            # "source" (data the agent queries) or "catalog" (documentation about that data).
            # Empty until someone decides — or until the server's tools make it plain.
            "role": cfg.get("role") if cfg.get("role") in ("source", "catalog") else "",
            "role_detected": False,
            "created_at": now(),
        }
        self.store.mcp_servers()[server_id] = server
        self.store.touch()
        self.connections.setdefault(server_id, Connection(server_id))
        self._emit()
        return server

    async def update_server(self, server_id: str, patch: dict) -> dict | None:
        server = self.store.mcp_servers().get(server_id)
        if server is None:
            return None
        editable = {"name", "args", "env", "headers", "url", "command", "cwd", "enabled",
                    "description", "auto_approve", "network", "role"}
        if "network" in patch and patch["network"] not in ("", "local", "internal"):
            patch.pop("network")
        if "role" in patch:
            if patch["role"] not in ("", "source", "catalog"):
                patch.pop("role")
            else:
                server["role_detected"] = False     # a person decided
        # A masked value coming back from the browser means "unchanged", never "set it to
        # bullets" — without this, opening the edit form and saving would destroy a token.
        for field in ("env", "headers"):
            incoming = patch.get(field)
            if isinstance(incoming, dict):
                current = server.get(field) or {}
                patch[field] = {k: (current.get(k, v) if "•" in str(v) else v)
                                for k, v in incoming.items()}
        server.update({k: v for k, v in patch.items() if k in editable})
        self.store.touch()
        self._emit()
        return server

    async def remove_server(self, server_id: str) -> bool:
        await self.disconnect(server_id)
        self.connections.pop(server_id, None)
        removed = self.store.mcp_servers().pop(server_id, None) is not None
        self.store.touch()
        self._emit()
        return removed

    def list_servers(self) -> list[dict]:
        out = []
        for server in self.store.mcp_servers().values():
            conn = self.connections.get(server["id"]) or Connection(server["id"])
            out.append({**mask_secrets(server), **conn.snapshot()})
        return sorted(out, key=lambda s: s.get("created_at") or 0)

    # ------------------------------------------------------------ connections
    async def connect(self, server_id: str) -> dict:
        server = self.store.mcp_servers().get(server_id)
        if server is None:
            raise McpError(f"Unknown server '{server_id}'")
        async with self._lock(server_id):
            conn = self.connections.setdefault(server_id, Connection(server_id))
            if conn.status == "connected":
                return conn.snapshot()
            await self._close(conn)
            conn.status, conn.error = "connecting", None
            self._emit()
            try:
                from app import network
                refused = network.check_mcp(server)
                if refused:
                    raise McpError(refused)
                if server["transport"] == "http":
                    if not server.get("url"):
                        raise McpError("This server has no URL configured.")
                    transport: Any = HttpTransport(server["url"], server.get("headers") or {})
                    conn.fence = "internal network (enforced by the enterprise firewall)"
                else:
                    if not server.get("command"):
                        raise McpError("This server has no command configured.")
                    command, args, env, conn.fence = network.stdio_launch(
                        server["command"], server.get("args") or [], server.get("env") or {}, server)
                    transport = StdioTransport(command, args, env, server.get("cwd") or None)
                client = McpClient(transport, self.settings.mcp_protocol_version,
                                   self.settings.mcp_startup_timeout_s,
                                   self.settings.mcp_call_timeout_s)
                await client.connect()
                conn.client = client
                conn.tools = await client.list_tools()
                conn.resources = await client.list_resources()
                conn.prompts = await client.list_prompts()
                conn.status = "connected"
                conn.connected_at = time.time()
                conn.error = None
                if not server.get("role") and looks_like_catalog(conn.tools):
                    # Recognised, not assumed: only tool names no data server would use.
                    server["role"], server["role_detected"] = "catalog", True
                    self.store.touch()
            except Exception as exc:
                conn.status = "error"
                conn.error = str(exc) if isinstance(exc, McpError) else f"{type(exc).__name__}: {exc}"
                await self._close(conn)
                conn.tools = conn.resources = conn.prompts = []
            self._emit()
            return conn.snapshot()

    async def disconnect(self, server_id: str) -> dict:
        conn = self.connections.get(server_id)
        if conn is None:
            return Connection(server_id).snapshot()
        async with self._lock(server_id):
            await self._close(conn)
            conn.status, conn.error = "disconnected", None
            conn.tools = conn.resources = conn.prompts = []
            self._emit()
            return conn.snapshot()

    @staticmethod
    async def _close(conn: Connection) -> None:
        if conn.client is not None:
            try:
                await conn.client.close()
            except Exception:
                pass  # a server that dies while we close it is already what we wanted
            conn.client = None

    async def connect_enabled(self) -> None:
        """Best-effort reconnect of everything marked enabled, at startup. One broken
        server records its error on its own card and never stops Agent from booting."""
        try:
            servers = [s for s in self.store.mcp_servers().values() if s.get("enabled")]
            if servers:
                await asyncio.gather(*(self._safe_connect(s["id"]) for s in servers))
        finally:
            self._ready.set()

    async def _safe_connect(self, server_id: str) -> None:
        try:
            await self.connect(server_id)
        except Exception as exc:
            conn = self.connections.setdefault(server_id, Connection(server_id))
            conn.status = "error"
            conn.error = f"{type(exc).__name__}: {exc}"

    async def wait_ready(self, timeout: float) -> bool:
        """Let a run wait for startup reconnection to settle — but never forever: a slow
        server should delay the first answer, not block it."""
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def shutdown(self) -> None:
        await asyncio.gather(*(self._close(c) for c in self.connections.values()),
                             return_exceptions=True)

    # -------------------------------------------------------------- tool index
    def tools(self) -> list[dict]:
        """Every tool of every connected server, as one flat, qualified list."""
        out: list[dict] = []
        for server in self.store.mcp_servers().values():
            conn = self.connections.get(server["id"])
            if conn is None or conn.status != "connected":
                continue
            slug = server.get("slug") or slugify(server["name"])
            for tool in conn.tools:
                name = tool.get("name") or ""
                if not name:
                    continue
                annotations = tool.get("annotations") or {}
                out.append({
                    "qualified_name": f"{slug}__{name}",
                    "name": name,
                    "title": tool.get("title") or annotations.get("title") or name,
                    "description": (tool.get("description") or "").strip(),
                    "input_schema": tool.get("inputSchema") or {"type": "object", "properties": {}},
                    "server_id": server["id"],
                    "server_name": server["name"],
                    "server_slug": slug,
                    "server_role": server.get("role") or "source",
                    "accent": server.get("accent", "sky"),
                    "auto_approve": bool(server.get("auto_approve")),
                    # Only the server can know whether a second call is the same as one.
                    # Anything that does not say so is treated as having an effect.
                    "read_only": bool(annotations.get("readOnlyHint")),
                    "write": bool(annotations.get("destructiveHint")) or (
                        not annotations.get("readOnlyHint") and is_write_tool(name)),
                    # Every MCP reply is content this app did not write, so every MCP tool
                    # reads from outside. Only the ones that change something get the
                    # capability that taint actually gates.
                    "capabilities": ([trust.FS_READ, trust.WORLD_WRITE]
                                     if (bool(annotations.get("destructiveHint"))
                                         or (not annotations.get("readOnlyHint")
                                             and is_write_tool(name)))
                                     else [trust.FS_READ]),
                })
        return out

    def catalogs(self) -> list[dict]:
        """Connected servers whose role is the data catalog — usually none, at most a few."""
        out = []
        for server in self.store.mcp_servers().values():
            conn = self.connections.get(server["id"])
            if server.get("role") == "catalog" and conn is not None and conn.status == "connected":
                out.append(server)
        return out

    def resolve(self, name: str) -> dict | None:
        """Resolve a tool reference to exactly one tool, or nothing.

        Accepts `server__tool`, `server.tool`, or a bare tool name when it is
        unambiguous. An ambiguous bare name resolves to nothing on purpose: guessing
        which of two identically-named tools the model meant is exactly the silent
        substitution that produces confidently wrong answers.
        """
        if not name:
            return None
        needle = name.strip().replace(".", "__")
        tools = self.tools()
        for tool in tools:
            if tool["qualified_name"] == needle:
                return tool
        bare = needle.split("__")[-1]
        matches = [t for t in tools if t["name"] == bare]
        if len(matches) == 1:
            return matches[0]
        lowered = [t for t in tools if t["name"].lower() == bare.lower()]
        return lowered[0] if len(lowered) == 1 else None

    def ollama_tools(self, subset: list[dict] | None = None) -> list[dict]:
        """The MCP surface rendered as OpenAI-style function definitions Ollama accepts.

        `subset` lets the caller offer part of the catalogue — see agent/context.py for why
        offering all sixty-odd every turn is not free.
        """
        out = []
        for tool in (self.tools() if subset is None else subset):
            schema = tool["input_schema"] or {}
            if schema.get("type") != "object":
                schema = {"type": "object", "properties": {}}
            description = " ".join((tool["description"] or tool["title"]).split())[:900]
            out.append({
                "type": "function",
                "function": {
                    "name": tool["qualified_name"],
                    "description": f"[{tool['server_name']}] {description}",
                    "parameters": {
                        "type": "object",
                        "properties": schema.get("properties") or {},
                        "required": schema.get("required") or [],
                    },
                },
            })
        return out

    def scopes(self) -> list[dict]:
        """What each connected server was actually pointed at.

        A server configured with a root — a filesystem directory, a git repository, a
        database file — knows its scope; the model does not, because that scope lives in
        the server's command line, not in any tool schema. So the model guesses: `git_log`
        with `repo_path="."`, `read_file` on a path relative to nothing. Three of the ten
        cross-server cases failed on exactly that, and every one of them is a guess this
        one line of prompt removes.

        Absolute paths are the signal: they are what a user types when they scope a server,
        and nothing else in an argv looks like one.
        """
        out: list[dict] = []
        for server in self.store.mcp_servers().values():
            conn = self.connections.get(server["id"])
            if conn is None or conn.status != "connected":
                continue
            # A bundled server's own script is an absolute path too; it is the launcher,
            # not the scope.
            paths = [a for a in (server.get("args") or [])
                     if isinstance(a, str) and a.startswith("/") and len(a) > 1
                     and not a.endswith(".py")]
            target = server.get("url") or ""
            if paths or target:
                out.append({"server": server["name"],
                            "slug": server.get("slug") or "",
                            "paths": paths, "url": target})
        return out

    def search(self, query: str, limit: int = 20) -> list[dict]:
        terms = [t for t in (query or "").lower().replace(",", " ").split() if len(t) > 2]
        scored: list[tuple[int, dict]] = []
        for tool in self.tools():
            haystack = f"{tool['name']} {tool['description']} {tool['server_name']}".lower()
            score = sum(3 if term in tool["name"].lower() else 1 for term in terms if term in haystack)
            if score:
                scored.append((score, tool))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [tool for _, tool in scored[:limit]]

    def summary(self) -> dict:
        servers = list(self.store.mcp_servers().values())
        statuses = [(self.connections.get(s["id"]) or Connection("")).status for s in servers]
        return {
            "servers_total": len(servers),
            "servers_connected": statuses.count("connected"),
            "servers_error": statuses.count("error"),
            "tools_available": len(self.tools()),
        }

    # ------------------------------------------------------------------ calls
    async def call(self, name: str, arguments: dict | None = None) -> dict:
        """Invoke one tool and normalize the outcome.

        Never raises for a tool-level failure: the agent needs a result it can reason
        about and show, not an exception that ends the turn.
        """
        tool = self.resolve(name)
        if tool is None:
            available = ", ".join(sorted({t["qualified_name"] for t in self.tools()})[:12]) or "none"
            return {"ok": False,
                    "error": f"No connected tool matches '{name}'. Available: {available}."}
        conn = self.connections.get(tool["server_id"])
        if conn is None or conn.status != "connected" or conn.client is None:
            return {"ok": False, "error": f"Server '{tool['server_name']}' is not connected."}

        started = time.time()
        try:
            raw = await conn.client.call_tool(tool["name"], arguments or {})
        except Exception as exc:
            conn.call_count += 1
            message = str(exc) if isinstance(exc, McpError) else f"{type(exc).__name__}: {exc}"
            return {"ok": False, "tool": tool["qualified_name"], "error": message,
                    "elapsed_ms": int((time.time() - started) * 1000)}

        conn.call_count += 1
        text, structured = flatten_tool_result(raw)
        elapsed_ms = int((time.time() - started) * 1000)
        if raw.get("isError"):
            return {"ok": False, "tool": tool["qualified_name"],
                    "error": text or "the tool reported an error without a message",
                    "elapsed_ms": elapsed_ms}
        return {
            "ok": True,
            "tool": tool["qualified_name"],
            "server": tool["server_name"],
            "summary": (text[:300] + "…") if len(text) > 300 else (text or "(empty result)"),
            # The whole thing. Cutting here was invisible and lossy in the worst way: a
            # 400-row query came back as 24,000 characters of half a JSON array, which the
            # caller then parked on disk as the "full result". Nothing downstream could
            # parse it, and the agent — with no error anywhere — retyped a fragment by hand
            # and answered from a twentieth of the data. Trimming for the model's context
            # is the caller's job, and it parks the original first.
            "text": text[:MAX_RESULT_CHARS],
            "truncated": len(text) > MAX_RESULT_CHARS,
            "data": structured,
            "elapsed_ms": elapsed_ms,
        }

    def _emit(self) -> None:
        self.bus.emit("system", {"type": "mcp.update", "summary": self.summary()})
