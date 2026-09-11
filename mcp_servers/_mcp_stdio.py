"""A ~120-line MCP server over stdio, with no dependencies.

The bundled server sits on top of this. Hand-rolled rather than pulled from an SDK for
the same reason the client is: the protocol surface a tool server needs is small, and a
server that ships as one readable file has an attack surface you can actually read.

Protocol: newline-delimited JSON-RPC 2.0 on stdin/stdout. Anything written to stdout that
is not a response would corrupt the stream, so diagnostics go to stderr — which the app
captures and shows on the server card.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Callable

PROTOCOL_VERSION = "2025-06-18"


def log(message: str) -> None:
    """Diagnostics go to stderr. stdout is the protocol channel and nothing else."""
    print(message, file=sys.stderr, flush=True)


class McpServer:
    def __init__(self, name: str, version: str, title: str = "") -> None:
        self.name = name
        self.version = version
        self.title = title or name
        self._tools: dict[str, dict] = {}
        self._handlers: dict[str, Callable[..., Any]] = {}

    def tool(self, name: str, description: str, schema: dict,
             read_only: bool = False) -> Callable:
        """Register one tool.

        `read_only` publishes the protocol's readOnlyHint. It is not decoration: a caller
        may only safely reuse an earlier result for a tool that says calling it twice is
        the same as calling it once. Default false, because a tool that has not thought
        about it is not read-only.
        """
        def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
            self._tools[name] = {"name": name, "description": description.strip(),
                                 "inputSchema": schema,
                                 "annotations": {"readOnlyHint": read_only,
                                                 "title": name.replace("_", " ").title()}}
            self._handlers[name] = fn
            return fn
        return decorate

    # ------------------------------------------------------------------ protocol
    def _initialize(self, _params: dict) -> dict:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": self.name, "title": self.title, "version": self.version},
        }

    def _list_tools(self, _params: dict) -> dict:
        return {"tools": list(self._tools.values())}

    def _call_tool(self, params: dict) -> dict:
        name = params.get("name")
        handler = self._handlers.get(name)
        if handler is None:
            # A tool-level failure is reported as a result, not a JSON-RPC error: the
            # caller's critic needs something to judge, not an exception.
            return _error(f"Unknown tool '{name}'. Available: {', '.join(sorted(self._handlers))}")
        try:
            result = handler(**(params.get("arguments") or {}))
        except TypeError as exc:
            # Say what would have worked. "unexpected keyword argument 'limit'" tells a
            # caller what was wrong and nothing about what is right, so it guesses again.
            schema = (self._tools.get(name) or {}).get("inputSchema") or {}
            accepted = ", ".join((schema.get("properties") or {})) or "no arguments"
            required = ", ".join(schema.get("required") or []) or "none"
            return _error(f"Bad arguments for '{name}': {exc}. "
                          f"Accepted: {accepted}. Required: {required}.")
        except Exception as exc:
            log(traceback.format_exc())
            return _error(f"{type(exc).__name__}: {exc}")
        if isinstance(result, dict) and "content" in result:
            return result
        # A handler that returns {"error": ...} is reporting a failure. Sending it back as a
        # successful result made a caller treat "Could not parse: invalid syntax" as data and
        # carry on — the protocol has isError for exactly this, and not setting it turns a
        # clear failure into a silent one.
        failed = isinstance(result, dict) and bool(result.get("error")) and not result.get("ok")
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2)
        return {"content": [{"type": "text", "text": text}], "isError": failed}

    def run(self) -> None:
        routes = {"initialize": self._initialize, "tools/list": self._list_tools,
                  "tools/call": self._call_tool, "ping": lambda _p: {}}
        log(f"{self.title} {self.version} ready on stdio")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg_id = message.get("id")
            method = message.get("method") or ""
            if msg_id is None:
                continue  # a notification: nothing here needs to answer one
            route = routes.get(method)
            if route is None:
                _send({"jsonrpc": "2.0", "id": msg_id,
                       "error": {"code": -32601, "message": f"Method not found: {method}"}})
                continue
            try:
                result = route(message.get("params") or {})
            except Exception as exc:
                log(traceback.format_exc())
                _send({"jsonrpc": "2.0", "id": msg_id,
                       "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"}})
                continue
            _send({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _error(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()
