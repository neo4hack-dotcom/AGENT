"""The admin API: the model, the MCP library, the guardrails.

Everything here is behind `check_admin`. The split is not cosmetic — connecting an MCP
server means running a command on this machine with the environment you give it, which is
categorically different from asking the agent a question.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app import network
from app.deps import OVERRIDABLE, container as c
from app.errors import McpError
from app.mcp import runtimes
from app.mcp.catalog import CATALOG, CATEGORIES, instantiate
from app.security import COOKIE, admin_state, check_admin, login, logout, request_token
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
async def do_login(request: Request, body: LoginBody, response: Response) -> dict:
    result = login(c, request, body.password)
    await c.store.save()
    # The cookie carries the session where a header cannot: the event stream, downloads.
    response.set_cookie(COOKIE, result["token"], max_age=c.env.session_ttl_s, httponly=True,
                        samesite="strict", secure=request.url.scheme == "https", path="/")
    return result


@router.post("/logout")
async def do_logout(request: Request, response: Response) -> dict:
    token = request_token(request)
    if token:
        logout(c, token)
        await c.store.save()
    response.delete_cookie(COOKIE, path="/")
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
        # Listed, not hidden, when the air gap refuses it: a model that vanished from the
        # picker is a mystery, one marked "leaves the network" is an answer.
        return {**entry, "capabilities": caps,
                "refused": network.check_model(entry["name"], c.get("ollama_base_url")) or ""}

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
    network: str = ""
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
               "cwd": body.cwd, "description": body.description, "category": "Custom",
               "network": body.network}
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
    network: str | None = None


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


# ------------------------------------------------------------ identity & skills
class SoulBody(BaseModel):
    text: str = Field(default="", max_length=4000)


@guarded.get("/soul")
async def get_soul() -> dict:
    return {"text": c.skills.soul()}


@guarded.post("/soul")
async def set_soul(body: SoulBody) -> dict:
    """The agent's standing instructions, first in every system prompt.

    Yours, not the agent's: nothing it reads can edit this, which is what makes it the one
    place a preference can be stated once instead of retyped every conversation.
    """
    text = c.skills.set_soul(body.text)
    await c.store.save()
    return {"text": text}


class SkillBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    trigger: str = Field(default="", max_length=400)
    body: str = Field(min_length=1, max_length=4000)


@guarded.get("/skills")
async def list_skills() -> dict:
    return {"skills": c.skills.all(), "stats": c.skills.stats()}


@guarded.post("/skills")
async def add_skill(body: SkillBody) -> dict:
    return c.skills.add(body.name, body.trigger, body.body, source="user")


@guarded.delete("/skills/{skill_id}")
async def delete_skill(skill_id: str) -> dict:
    if not c.skills.forget(skill_id):
        raise HTTPException(404, "No such skill.")
    return {"ok": True}


# -------------------------------------------------------------- data sources
def _queryable(server: dict) -> bool:
    from app.data.profiler import Profiler
    tools = [t for t in c.mcp.tools() if t["server_id"] == server["id"]]
    return bool(tools) and bool(Profiler(c.mcp)._detect(tools)["query"])


def _source_summary(server: dict) -> dict:
    knowledge = c.knowledge.get(server["id"])
    model = knowledge["model"]
    conn = c.mcp.connections.get(server["id"])
    queryable = _queryable(server)
    return {
        "id": server["id"], "name": server["name"], "slug": server.get("slug") or "",
        "connected": bool(conn and conn.status == "connected"),
        "description": knowledge["description"],
        "counts": {"tables": len(model.get("tables") or []),
                   "metrics": len(model.get("metrics") or []),
                   "caveats": len(model.get("caveats") or []),
                   "verified": len(model.get("verified_queries") or [])},
        "queryable": queryable,
        "readiness": c.knowledge.readiness(server["id"], queryable),
        "profiled_at": knowledge["profiled_at"], "updated_at": knowledge["updated_at"],
    }


@guarded.get("/sources")
async def list_sources() -> list[dict]:
    """Every connected server with what has been written about it, and how ready it is."""
    return [_source_summary(s) for s in c.store.mcp_servers().values()]


def _source_or_404(server_id: str) -> dict:
    server = c.store.mcp_servers().get(server_id)
    if server is None:
        raise HTTPException(404, "No such server.")
    return server


@guarded.get("/sources/{server_id}")
async def get_source(server_id: str) -> dict:
    from app.data.knowledge import EXAMPLE_YAML
    server = _source_or_404(server_id)
    knowledge = c.knowledge.get(server_id)
    return {**_source_summary(server), "model_yaml": knowledge["model_yaml"],
            "profile": knowledge["profile"], "example_yaml": EXAMPLE_YAML, "errors": []}


class SourceBody(BaseModel):
    description: str | None = Field(default=None, max_length=4000)
    model_yaml: str | None = Field(default=None, max_length=200_000)


@guarded.put("/sources/{server_id}")
async def put_source(server_id: str, body: SourceBody) -> dict:
    server = _source_or_404(server_id)
    knowledge, errors = c.knowledge.update(server_id, description=body.description,
                                           model_yaml=body.model_yaml)
    await c.store.save()
    return {**_source_summary(server), "model_yaml": knowledge["model_yaml"],
            "profile": knowledge["profile"], "errors": errors}


@guarded.post("/sources/{server_id}/profile")
async def profile_source(server_id: str) -> dict:
    """Measure the source through its own tools and fold the facts into its model."""
    from app.data.profiler import Profiler, ProfileError
    server = _source_or_404(server_id)
    try:
        profile, model = await Profiler(c.mcp).profile(server)
    except ProfileError as exc:
        raise HTTPException(422, str(exc)) from exc
    knowledge = c.knowledge.set_profile(server_id, profile, model)
    await c.store.save()
    c.audit.record("source.profile", server=server["name"], queries=profile["queries"],
                   tables=profile.get("tables_measured"))
    return {**_source_summary(server), "model_yaml": knowledge["model_yaml"],
            "profile": profile, "errors": []}


@guarded.post("/sources/{server_id}/draft")
async def draft_source(server_id: str) -> dict:
    """Propose descriptions, metrics and checked queries. Returned, never saved: a person
    reads a draft before the agent trusts it."""
    from app.data.drafting import draft
    from app.data.profiler import Profiler, ProfileError
    server = _source_or_404(server_id)
    current = c.knowledge.get(server_id)
    try:
        if not current["model"].get("tables"):
            profile, model = await Profiler(c.mcp).profile(server)
            current = c.knowledge.set_profile(server_id, profile, model)
            await c.store.save()
        proposal = await draft(c.llm, c.mcp, server, current, current["model"])
    except ProfileError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {**_source_summary(server), **proposal}


# --------------------------------------------------------------- diagnostics
def run_metrics(sample: int = 60) -> dict:
    """What the last runs actually cost, read back from what was saved.

    Kept as a derivation rather than a counter: a counter drifts from the conversations it
    claims to describe, and the numbers are only worth reading if they cannot.
    """
    turns: list[dict] = []
    for conv in c.store.conversations().values():
        for message in conv.get("messages") or []:
            if message.get("role") == "assistant" and (message.get("usage") or {}).get("llm_calls"):
                turns.append({**message["usage"], "at": message.get("created_at", 0),
                              "status": message.get("status")})
    turns.sort(key=lambda t: t["at"], reverse=True)
    recent = turns[:sample]
    if not recent:
        return {"runs": 0}

    def total(field: str) -> int:
        return sum(int(t.get(field) or 0) for t in recent)

    ttfts = sorted(t["ttft_ms"] for t in recent if t.get("ttft_ms"))
    sent, evaluated = total("prompt_sent"), total("prompt_evaluated")
    windows = [t["context_tokens"] for t in recent if t.get("context_tokens")]
    return {
        "runs": len(recent),
        "tokens_in": total("tokens_in"),
        "tokens_out": total("tokens_out"),
        "llm_calls": total("llm_calls"),
        "tool_calls": total("tool_calls"),
        "calls_per_run": round(total("llm_calls") / len(recent), 1),
        # The fraction of the prompt the provider did not have to re-read. The single most
        # useful number for telling a context problem from a model problem.
        "cache_hit": round(100 * (1 - evaluated / sent)) if sent > evaluated > 0 else 0,
        "ttft_median_ms": ttfts[len(ttfts) // 2] if ttfts else 0,
        "ttft_p90_ms": ttfts[int(len(ttfts) * 0.9)] if ttfts else 0,
        "masked_chars": total("masked_chars"),
        "peak_context": max(windows) if windows else 0,
        "failed": sum(1 for t in recent if t.get("status") in ("failed", "cancelled")),
    }


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
        "network": network.summary(),
        "runs": run_metrics(),
        "mcp": c.mcp.summary(),
        "runtimes": runtimes.probe(),
        "runtime": runtime,
        "context_window": window,
        "python_modules": available_modules(),
        "workspace": str(c.workspace()),
        "store": str(c.env.db_path),
        "warnings": warnings,
    }
