"""The admin API: the model, the MCP library, the guardrails.

Everything here is behind `check_admin`. The split is not cosmetic — connecting an MCP
server means running a command on this machine with the environment you give it, which is
categorically different from asking the agent a question.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.deps import OVERRIDABLE, container as c
from app.errors import McpError
from app.mcp import runtimes
from app.mcp.catalog import CATALOG, CATEGORIES, instantiate
from app.security import admin_state, check_admin, login, logout
from app.tools.code import available_modules


async def require_admin(request: Request) -> None:
    check_admin(c, request)


router = APIRouter(prefix="/api/admin")
guarded = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


# ------------------------------------------------------------------- sign-in
class LoginBody(BaseModel):
    password: str = ""


@router.get("/state")
async def state(request: Request) -> dict:
    return admin_state(c, request)


@router.post("/login")
async def do_login(request: Request, body: LoginBody) -> dict:
    result = login(c, request, body.password)
    await c.store.save()
    return result


@router.post("/logout")
async def do_logout(request: Request) -> dict:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        logout(c, header[7:].strip())
        await c.store.save()
    return {"ok": True}


# --------------------------------------------------------------------- model
@guarded.get("/models")
async def models() -> dict:
    """Every model this Ollama server has, each with what it can actually do.

    The capabilities matter more than the names: a model without `tools` cannot drive the
    agent at all, and one without `vision` silently drops attachments. Showing that in the
    picker is the difference between choosing and guessing.
    """
    listing = await c.llm.list_models() if hasattr(c.llm, "list_models") else {"ok": False, "models": []}
    if not listing.get("ok"):
        from app.llm.provider import OllamaProvider

        listing = await OllamaProvider(c.get("ollama_base_url"), "").list_models()
    entries = listing.get("models") or []

    from app.llm.provider import describe_model

    semaphore = asyncio.Semaphore(8)

    async def enrich(entry: dict) -> dict:
        async with semaphore:
            try:
                caps = await asyncio.wait_for(
                    describe_model(c.get("ollama_base_url"), entry["name"]), timeout=12)
            except Exception:
                caps = {"tools": False, "thinking": False, "vision": False, "context_length": 0,
                        "source": "capabilities unavailable"}
        return {**entry, "capabilities": caps}

    enriched = await asyncio.gather(*(enrich(e) for e in entries)) if entries else []
    return {"ok": listing.get("ok", False), "error": listing.get("error"),
            "models": list(enriched), "selected": c.get("model") or "",
            "fast_selected": c.get("fast_model") or ""}


class PrefsBody(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


@guarded.get("/prefs")
async def get_prefs() -> dict:
    return {"effective": {key: c.get(key) for key in sorted(OVERRIDABLE)},
            "overridden": sorted(k for k in c.prefs() if k in OVERRIDABLE),
            "env_defaults": {key: getattr(c.env, key, None) for key in sorted(OVERRIDABLE)}}


@guarded.post("/prefs")
async def set_prefs(body: PrefsBody) -> dict:
    unknown = [key for key in body.values if key not in OVERRIDABLE]
    if unknown:
        raise HTTPException(400, f"Not settable from the UI: {', '.join(unknown)}")
    c.store.set_prefs(body.values)
    c.invalidate_llm()
    await c.store.save()
    return {"effective": {key: c.get(key) for key in sorted(OVERRIDABLE)}}


# ----------------------------------------------------------------------- MCP
@guarded.get("/catalog")
async def catalog() -> dict:
    return {"entries": CATALOG, "categories": CATEGORIES, "runtimes": runtimes.probe()}


@guarded.get("/servers")
async def servers() -> list[dict]:
    return c.mcp.list_servers()


class InstallBody(BaseModel):
    catalog_id: str = ""
    values: dict[str, str] = Field(default_factory=dict)
    # A server not on the shelf is described directly:
    name: str = ""
    transport: str = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    cwd: str = ""
    description: str = ""
    connect: bool = True


@guarded.post("/servers")
async def add_server(body: InstallBody) -> dict:
    if body.catalog_id:
        try:
            cfg = instantiate(body.catalog_id, body.values,
                              {"workspace": str(c.workspace())})
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
    else:
        if not body.name.strip():
            raise HTTPException(400, "A custom server needs a name.")
        if body.transport == "http" and not body.url.strip():
            raise HTTPException(400, "An HTTP server needs a URL.")
        if body.transport == "stdio" and not body.command.strip():
            raise HTTPException(400, "A stdio server needs a command.")
        cfg = {"name": body.name, "transport": body.transport, "command": body.command,
               "args": body.args, "env": body.env, "url": body.url, "headers": body.headers,
               "cwd": body.cwd, "description": body.description, "category": "Custom"}
    server = await c.mcp.add_server(cfg)
    await c.store.save()
    snapshot = {}
    if body.connect:
        snapshot = await c.mcp.connect(server["id"])
    return {**server, **snapshot}


class PatchBody(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    auto_approve: bool | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    headers: dict[str, str] | None = None
    url: str | None = None
    command: str | None = None
    cwd: str | None = None
    description: str | None = None


@guarded.patch("/servers/{server_id}")
async def patch_server(server_id: str, body: PatchBody) -> dict:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    server = await c.mcp.update_server(server_id, patch)
    if server is None:
        raise HTTPException(404, "No such server.")
    await c.store.save()
    return next(s for s in c.mcp.list_servers() if s["id"] == server_id)


@guarded.delete("/servers/{server_id}")
async def delete_server(server_id: str) -> dict:
    if not await c.mcp.remove_server(server_id):
        raise HTTPException(404, "No such server.")
    await c.store.save()
    return {"ok": True}


@guarded.post("/servers/{server_id}/connect")
async def connect_server(server_id: str) -> dict:
    try:
        snapshot = await c.mcp.connect(server_id)
    except McpError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": server_id, **snapshot}


@guarded.post("/servers/{server_id}/disconnect")
async def disconnect_server(server_id: str) -> dict:
    return {"id": server_id, **await c.mcp.disconnect(server_id)}


@guarded.get("/servers/{server_id}/tools")
async def server_tools(server_id: str) -> list[dict]:
    return [t for t in c.mcp.tools() if t["server_id"] == server_id]


class CallBody(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


@guarded.post("/tools/{tool_name}/call")
async def call_tool(tool_name: str, body: CallBody) -> dict:
    """Run one tool by hand. The fastest way to tell "the agent chose badly" apart from
    "the server is broken" — and the only way to see a tool's real output shape."""
    return await c.mcp.call(tool_name, body.arguments)


@guarded.get("/audit")
async def audit(limit: int = 200, run_id: str = "") -> dict:
    """The action log, plus whether its hash chain still checks out.

    `verified.ok: false` means the file was edited after the fact — by a person, a script,
    or the agent itself. The log cannot stop that; it can refuse to hide it.
    """
    return {"entries": c.audit.read(limit=min(limit, 1000), run_id=run_id),
            "verified": c.audit.verify(), "path": str(c.env.audit_path)}


# --------------------------------------------------------------- diagnostics
@guarded.get("/diagnostics")
async def diagnostics() -> dict:
    llm = await c.llm.healthcheck()
    caps = await c.llm.capabilities()
    runtime = await c.llm.runtime_status() if hasattr(c.llm, "runtime_status") else {"loaded": False}
    window = await c.llm.context_window() if hasattr(c.llm, "context_window") else 0
    warnings: list[str] = []
    if runtime.get("loaded") and runtime.get("gpu_percent", 100) < 95:
        warnings.append(
            f"Only {runtime['gpu_percent']}% of the model fits on the GPU "
            f"({runtime['vram_gb']} GB of {runtime['size_gb']} GB) — the rest runs on the CPU, "
            f"several times slower. Lower the context window (currently {window}) in "
            f"Guardrails, or pick a smaller model.")
    if not llm.get("ok"):
        warnings.append(llm.get("error") or "No model selected.")
    elif not caps.get("tools"):
        warnings.append(
            f"{llm.get('model')} does not support tool calling, so the agent cannot use any "
            f"tool — it can only answer from what it already knows. Pick a model with the "
            f"'tools' capability.")
    missing = [r for r in runtimes.probe().values() if not r["available"]]
    for runtime in missing:
        warnings.append(f"{runtime['label']} is not on PATH — {runtime['why']}. "
                        f"Install it with: {runtime['install']}")
    return {
        "model": {**llm, "capabilities": caps},
        "mcp": c.mcp.summary(),
        "runtimes": runtimes.probe(),
        "runtime": runtime,
        "context_window": window,
        "python_modules": available_modules(),
        "workspace": str(c.workspace()),
        "store": str(c.env.db_path),
        "warnings": warnings,
    }
