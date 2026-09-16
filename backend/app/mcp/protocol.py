"""A self-contained MCP client: JSON-RPC 2.0 over stdio or streamable HTTP.

Written directly against the protocol rather than wrapped around an SDK so that Agent can
speak to any MCP server — a local npx/uvx process, a remote HTTP endpoint — with one code
path, and so that connection failures surface as readable diagnostics (including the
server's own stderr) instead of an opaque import-time error.
"""

from __future__ import annotations

import asyncio
import ast
import json
import os
import time
from collections import deque
from typing import Any

import httpx

from app.errors import McpError
from app.mcp.runtimes import install_hint

CLIENT_INFO = {"name": "lumen", "title": "Agent Super-Agent", "version": "1.0.0"}
CLIENT_CAPABILITIES: dict[str, Any] = {"roots": {"listChanged": False}}


class Transport:
    """Common surface: send a request and get a result, or fire a notification."""

    async def start(self) -> None:
        raise NotImplementedError

    async def request(self, method: str, params: dict | None, timeout: float) -> dict:
        raise NotImplementedError

    async def notify(self, method: str, params: dict | None) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError

    @property
    def diagnostics(self) -> list[str]:
        return []


class StdioTransport(Transport):
    """Spawns the server as a child process and exchanges newline-delimited JSON-RPC.

    The child's stderr is drained into a ring buffer instead of being discarded: when a
    server fails to start, its own error output is the only thing that explains why, and
    it is what the UI shows on the server card.
    """

    def __init__(self, command: str, args: list[str], env: dict[str, str] | None = None,
                 cwd: str | None = None) -> None:
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.cwd = cwd or None
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._stderr: deque[str] = deque(maxlen=100)
        self._tasks: list[asyncio.Task] = []
        self._write_lock = asyncio.Lock()

    async def start(self) -> None:
        # Inherit the real environment so `npx`/`uvx` resolve node/python the way they do
        # in the user's own shell; explicit vars from the server config win over it.
        env = {**os.environ, **{k: str(v) for k, v in self.env.items() if v != ""}}
        try:
            self._proc = await asyncio.create_subprocess_exec(
                self.command, *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env, cwd=self.cwd,
            )
        except FileNotFoundError as exc:
            # "Install it" is a dead end when the missing command is a launcher nobody
            # installs by that name — uvx ships with uv. Name the real remedy.
            hint = install_hint(self.command)
            remedy = (f" Install it with: {hint}" if hint
                      else " Install it, or point the server at a different executable.")
            raise McpError(f"'{self.command}' is not on this machine's PATH.{remedy}") from exc
        except OSError as exc:
            raise McpError(f"Could not start '{self.command}': {exc}") from exc
        self._tasks = [asyncio.create_task(self._read_stdout()), asyncio.create_task(self._read_stderr())]

    async def _read_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue  # some servers print banners on stdout; ignore non-JSON lines
            self._dispatch(msg)
        # The process died: fail everything still waiting rather than hanging forever.
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(McpError(self._exit_reason()))
        self._pending.clear()

    async def _read_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                self._stderr.append(text)

    def _exit_reason(self) -> str:
        code = self._proc.returncode if self._proc else None
        tail = " | ".join(list(self._stderr)[-4:])
        base = f"MCP server exited (code {code})" if code is not None else "MCP server closed its stream"
        return f"{base}{': ' + tail if tail else ''}"

    def _dispatch(self, msg: dict) -> None:
        msg_id = msg.get("id")
        if msg_id is None:
            return  # server-initiated notification: nothing here depends on them yet
        fut = self._pending.pop(msg_id, None)
        if fut is None or fut.done():
            return
        if "error" in msg:
            err = msg["error"] or {}
            fut.set_exception(McpError(f"{err.get('message', 'MCP error')} (code {err.get('code')})"))
        else:
            fut.set_result(msg.get("result") or {})

    async def _write(self, payload: dict) -> None:
        if not self._proc or not self._proc.stdin or self._proc.returncode is not None:
            raise McpError(self._exit_reason())
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            self._proc.stdin.write(data)
            await self._proc.stdin.drain()

    async def request(self, method: str, params: dict | None, timeout: float) -> dict:
        self._next_id += 1
        msg_id = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        await self._write({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}})
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(msg_id, None)
            raise McpError(f"'{method}' timed out after {timeout:.0f}s") from exc

    async def notify(self, method: str, params: dict | None) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks = []
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass

    @property
    def diagnostics(self) -> list[str]:
        return list(self._stderr)


class HttpTransport(Transport):
    """Streamable HTTP: every JSON-RPC message is a POST, and the reply comes back either
    as plain JSON or as a one-shot SSE stream. Both shapes are handled."""

    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self.url = url
        self.headers = {k: v for k, v in (headers or {}).items() if v != ""}
        self.session_id: str | None = None
        self._client: httpx.AsyncClient | None = None
        self._next_id = 0
        self._log: deque[str] = deque(maxlen=100)

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=None, follow_redirects=True)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self.headers,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    async def _post(self, payload: dict, timeout: float) -> dict | None:
        if self._client is None:
            raise McpError("HTTP transport not started")
        try:
            resp = await self._client.post(self.url, json=payload, headers=self._headers(),
                                           timeout=timeout)
        except httpx.HTTPError as exc:
            raise McpError(f"HTTP transport error: {exc}") from exc
        session = resp.headers.get("mcp-session-id")
        if session:
            self.session_id = session
        if resp.status_code == 202:
            return None  # accepted notification
        if resp.status_code >= 400:
            self._log.append(f"HTTP {resp.status_code}: {resp.text[:200]}")
            raise McpError(f"HTTP {resp.status_code} — {resp.text[:200]}")
        ctype = resp.headers.get("content-type", "")
        if ctype.startswith("text/event-stream"):
            return self._parse_sse(resp.text)
        if not resp.text.strip():
            return None
        return resp.json()

    @staticmethod
    def _parse_sse(text: str) -> dict | None:
        """Return the first JSON-RPC response carried in an SSE body."""
        for raw in text.splitlines():
            if not raw.startswith("data:"):
                continue
            chunk = raw[5:].strip()
            if not chunk:
                continue
            try:
                msg = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict) and ("result" in msg or "error" in msg):
                return msg
        return None

    async def request(self, method: str, params: dict | None, timeout: float) -> dict:
        self._next_id += 1
        msg = await self._post(
            {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}},
            timeout,
        )
        if msg is None:
            raise McpError(f"'{method}' returned an empty response")
        if "error" in msg:
            err = msg["error"] or {}
            raise McpError(f"{err.get('message', 'MCP error')} (code {err.get('code')})")
        return msg.get("result") or {}

    async def notify(self, method: str, params: dict | None) -> None:
        await self._post({"jsonrpc": "2.0", "method": method, "params": params or {}}, 30)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def diagnostics(self) -> list[str]:
        return list(self._log)


class McpClient:
    """One live connection to one MCP server."""

    def __init__(self, transport: Transport, protocol_version: str,
                 startup_timeout_s: float, call_timeout_s: float) -> None:
        self.transport = transport
        self.protocol_version = protocol_version
        self.startup_timeout_s = startup_timeout_s
        self.call_timeout_s = call_timeout_s
        self.server_info: dict = {}
        self.capabilities: dict = {}
        self.negotiated_version: str = ""
        self.connected_at: float = 0.0

    async def connect(self) -> dict:
        await self.transport.start()
        result = await self.transport.request(
            "initialize",
            {"protocolVersion": self.protocol_version,
             "capabilities": CLIENT_CAPABILITIES,
             "clientInfo": CLIENT_INFO},
            timeout=self.startup_timeout_s,
        )
        self.server_info = result.get("serverInfo") or {}
        self.capabilities = result.get("capabilities") or {}
        self.negotiated_version = result.get("protocolVersion") or self.protocol_version
        await self.transport.notify("notifications/initialized", {})
        self.connected_at = time.time()
        return result

    async def list_tools(self) -> list[dict]:
        if "tools" not in self.capabilities:
            return []
        tools: list[dict] = []
        cursor: str | None = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = await self.transport.request("tools/list", params, timeout=self.startup_timeout_s)
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor or len(tools) >= 500:
                break
        return tools

    async def list_resources(self) -> list[dict]:
        if "resources" not in self.capabilities:
            return []
        result = await self.transport.request("resources/list", {}, timeout=self.startup_timeout_s)
        return result.get("resources") or []

    async def list_prompts(self) -> list[dict]:
        if "prompts" not in self.capabilities:
            return []
        result = await self.transport.request("prompts/list", {}, timeout=self.startup_timeout_s)
        return result.get("prompts") or []

    async def call_tool(self, name: str, arguments: dict) -> dict:
        return await self.transport.request(
            "tools/call", {"name": name, "arguments": arguments or {}},
            timeout=self.call_timeout_s,
        )

    async def ping(self) -> bool:
        try:
            await self.transport.request("ping", {}, timeout=10)
            return True
        except McpError:
            # `ping` is optional in the spec — a server that rejects the method is still
            # alive, so only a transport-level failure counts as down.
            return self.transport.diagnostics is not None

    async def close(self) -> None:
        await self.transport.close()


def flatten_tool_result(result: dict) -> tuple[str, Any]:
    """Turn an MCP tool result into (human-readable text, structured payload).

    Nothing is invented here: if a server returns only an image or a resource link, the
    text says exactly that rather than pretending prose was returned.
    """
    structured = result.get("structuredContent")
    chunks: list[str] = []
    for block in result.get("content") or []:
        kind = block.get("type")
        if kind == "text":
            chunks.append(block.get("text") or "")
        elif kind == "resource":
            resource = block.get("resource") or {}
            chunks.append(resource.get("text") or f"[resource {resource.get('uri', 'unknown')}]")
        elif kind == "resource_link":
            chunks.append(f"[resource link {block.get('uri', 'unknown')}]")
        elif kind == "image":
            chunks.append(f"[image {block.get('mimeType', 'unknown type')}]")
        elif kind == "audio":
            chunks.append(f"[audio {block.get('mimeType', 'unknown type')}]")
        else:
            chunks.append(f"[{kind or 'unknown'} content]")
    text = "\n".join(c for c in chunks if c).strip()
    if structured is not None and not text:
        text = json.dumps(structured, ensure_ascii=False)[:4000]
    return _as_json(text), structured


def _as_json(text: str) -> str:
    """Re-encode a Python repr as JSON, leaving everything else untouched.

    Plenty of servers build their reply with `str(rows)`, which yields `[{'id': 1,
    'paid': None}]` — single quotes, `None`, `True`. That is not JSON, so nothing
    downstream can parse it: not the reader's table view, and not the model, which then
    has to reconstruct the rows by eye. `literal_eval` reads literals only, never code,
    so this converts the encoding without trusting the source.
    """
    stripped = text.strip()
    if not (stripped[:1] in ("[", "{") and len(stripped) < 200_000):
        return text
    try:
        json.loads(stripped)
    except ValueError:
        pass
    else:
        return text  # already JSON
    try:
        value = ast.literal_eval(stripped)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return text
    if not isinstance(value, (list, dict)):
        return text
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return text
