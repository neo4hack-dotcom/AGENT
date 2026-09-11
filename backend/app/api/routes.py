"""The public API: everything the main screen needs, all under /api."""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.api.stream import run_stream
from app.deps import container as c
from app.security import admin_state
from app.store import new_id, now

router = APIRouter(prefix="/api")

IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
UPLOAD_DIRNAME = "uploads"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


# --------------------------------------------------------------------- health
@router.get("/health")
async def health() -> dict:
    return {"ok": True, "app": c.env.app_name}


@router.get("/bootstrap")
async def bootstrap(request: Request) -> dict:
    """One call the UI makes on load: what is connected, what works, what does not."""
    llm = await c.llm.healthcheck()
    caps = await c.llm.capabilities()
    workspace = c.workspace()
    return {
        "app": c.env.app_name,
        "model": {**llm, "capabilities": caps, "fast_model": c.get("fast_model") or ""},
        "mcp": c.mcp.summary(),
        "tools": tool_surface(),
        "admin": admin_state(c, request),
        "prefs": {"approval_mode": c.get("approval_mode")},
        "workspace": str(workspace),
        "memory_count": len(c.store.memory()),
    }


def tool_surface() -> list[dict]:
    """Everything the agent can call right now, for the UI's capability sheet."""
    from app.agent import builtin

    specs = builtin.build_registry(c.settings, c.memory, c.workspace(), lambda _s: None)
    out = [{"name": name, "group": spec.group, "kind": "builtin", "write": spec.write,
            "description": " ".join(spec.description.split())[:220]}
           for name, spec in specs.items()]
    out += [{"name": t["qualified_name"], "group": t["server_name"], "kind": "mcp",
             "write": t["write"], "description": " ".join((t["description"] or "").split())[:220]}
            for t in c.mcp.tools()]
    return out


# -------------------------------------------------------------- conversations
@router.get("/conversations")
async def list_conversations() -> list[dict]:
    return c.store.conversation_list()


@router.post("/conversations")
async def create_conversation() -> dict:
    return c.store.create_conversation()


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str) -> dict:
    conv = c.store.conversation(conv_id)
    if conv is None:
        raise HTTPException(404, "No such conversation.")
    active = c.runner.active_for(conv_id)
    messages = conv.get("messages") or []
    if not active:
        # A message left "running" with no run behind it means the process died mid-answer.
        # Saying so is the honest reading; leaving a spinner turning forever is not.
        for message in messages:
            if message.get("role") == "assistant" and message.get("status") == "running":
                message["status"] = "failed"
                message["error"] = ("This answer was cut short when the server stopped. "
                                    "Ask again.")
                c.store.touch()
    return {**conv, "active_run_id": active}


class RenameBody(BaseModel):
    title: str = Field(max_length=120)


@router.patch("/conversations/{conv_id}")
async def rename_conversation(conv_id: str, body: RenameBody) -> dict:
    conv = c.store.conversation(conv_id)
    if conv is None:
        raise HTTPException(404, "No such conversation.")
    conv["title"] = body.title.strip()
    conv["updated_at"] = now()
    await c.store.save()
    return conv


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str) -> dict:
    if c.store.conversations().pop(conv_id, None) is None:
        raise HTTPException(404, "No such conversation.")
    await c.store.save()
    return {"ok": True}


# ----------------------------------------------------------------------- chat
class ChatBody(BaseModel):
    conversation_id: str = ""
    text: str
    attachments: list[str] = Field(default_factory=list)


@router.post("/chat")
async def chat(body: ChatBody) -> dict:
    text = (body.text or "").strip()
    if not text and not body.attachments:
        raise HTTPException(400, "Nothing to send.")
    conv_id = body.conversation_id or c.store.create_conversation()["id"]

    images, notes = [], []
    for upload_id in body.attachments[:8]:
        record = _upload_record(upload_id)
        if record is None:
            continue
        if record["mime"] in IMAGE_MIMES:
            images.append({"name": record["name"], "mime": record["mime"],
                           "data": Path(record["abs_path"]).read_bytes()})
            notes.append(f"[image attached: {record['name']}]")
        else:
            notes.append(f"[file attached: {record['rel_path']} — it is in your workspace; "
                         f"read it with workspace_read or run_python]")
    if notes:
        text = (text + "\n\n" + "\n".join(notes)).strip()

    return await c.runner.start(conv_id, text, images)


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str, request: Request, since: int = 0):
    ctx = c.runner.get(run_id)
    if ctx is None:
        raise HTTPException(404, "This run has finished and its live stream is gone. "
                                 "Reload the conversation to see the saved answer.")
    return run_stream(c, ctx, request, since)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict:
    if not c.runner.cancel(run_id):
        raise HTTPException(404, "No such run.")
    return {"ok": True}


class ApprovalBody(BaseModel):
    call_id: str
    approved: bool


@router.post("/runs/{run_id}/approve")
async def approve_call(run_id: str, body: ApprovalBody) -> dict:
    if not c.runner.approve(run_id, body.call_id, body.approved):
        raise HTTPException(404, "That approval is no longer pending.")
    return {"ok": True}


# -------------------------------------------------------------------- uploads
def _upload_dir() -> Path:
    path = c.workspace() / UPLOAD_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _upload_record(upload_id: str) -> dict | None:
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,80}", upload_id or ""):
        return None
    for candidate in _upload_dir().iterdir():
        if candidate.name.startswith(upload_id):
            mime = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            return {"id": upload_id, "name": candidate.name.split("__", 1)[-1],
                    "mime": mime, "abs_path": str(candidate),
                    "rel_path": f"{UPLOAD_DIRNAME}/{candidate.name}"}
    return None


@router.post("/uploads")
async def upload(file: UploadFile = File(...)) -> dict:
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"That file is {len(raw) // 1024 // 1024} MB; the ceiling is "
                                 f"{MAX_UPLOAD_BYTES // 1024 // 1024} MB.")
    safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", Path(file.filename or "file").name)[:80] or "file"
    upload_id = new_id("u")
    target = _upload_dir() / f"{upload_id}__{safe}"
    target.write_bytes(raw)
    mime = file.content_type or mimetypes.guess_type(safe)[0] or "application/octet-stream"
    return {"id": upload_id, "name": safe, "mime": mime, "size": len(raw),
            "kind": "image" if mime in IMAGE_MIMES else "file",
            "path": f"{UPLOAD_DIRNAME}/{target.name}"}


# --------------------------------------------------------------------- memory
@router.get("/memory")
async def list_memory() -> list[dict]:
    return sorted(c.store.memory(), key=lambda m: m.get("updated_at", 0), reverse=True)


class MemoryBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


@router.post("/memory")
async def add_memory(body: MemoryBody) -> dict:
    entry = c.memory.add(body.text, source="user")
    await c.store.save()
    return entry


@router.delete("/memory/{memory_id}")
async def forget(memory_id: str) -> dict:
    if not c.memory.forget(memory_id):
        raise HTTPException(404, "No such memory.")
    await c.store.save()
    return {"ok": True}
