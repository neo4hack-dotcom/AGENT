"""The public API: everything the main screen needs, all under /api."""

from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.api.stream import run_stream
from app.deps import container as c
from app.tools import files as file_tool
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
        # What direct mode can offer: every connected server, by name.
        "servers": [{"id": s["id"], "name": s["name"], "slug": s.get("slug") or "",
                     "connected": s.get("status") == "connected", "tool_count": s.get("tool_count", 0),
                     "role": s.get("role") or "source"}
                    for s in c.mcp.list_servers() if s.get("enabled", True)],
        "tools": tool_surface(),
        "admin": admin_state(c, request),
        "prefs": {"approval_mode": c.get("approval_mode")},
        "workspace": str(workspace),
        "memory": c.memory.stats(),
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
    # "agent" (the full loop) or "direct": the model with the chosen MCP servers' tools and
    # nothing on top — no routing, critic, reflection or composed answer.
    mode: str = Field(default="agent", pattern="^(agent|direct)$")
    servers: list[str] = Field(default_factory=list, max_length=50)


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

    return await c.runner.start(conv_id, text, images, mode=body.mode, servers=body.servers)


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


@router.get("/charts/theme")
async def chart_theme() -> dict:
    """The house chart style, from the one place it is defined — so the chart on screen and
    the chart in the PDF can never drift apart."""
    from app.data.charts import locale_of, theme
    name = str(c.get("chart_locale") or "fr-FR")
    defined = locale_of(name)
    return {"light": theme(False), "dark": theme(True),
            "locale": {"name": name, "format": defined["format"], "time": defined["time"]}}


class AnswerBody(BaseModel):
    call_id: str
    answer: str = Field(min_length=1, max_length=2000)


@router.post("/runs/{run_id}/answer")
async def answer_question(run_id: str, body: AnswerBody) -> dict:
    """The reader's answer to a clarifying question the agent is waiting on."""
    if not c.runner.answer(run_id, body.call_id, body.answer.strip()):
        raise HTTPException(404, "That question is no longer waiting for an answer.")
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
    # Read one byte past the ceiling, never the whole body: a 5 GB upload must not be held
    # in memory just to be refused.
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"That file is larger than the "
                                 f"{MAX_UPLOAD_BYTES // 1024 // 1024} MB ceiling.")
    safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", Path(file.filename or "file").name)[:80] or "file"
    upload_id = new_id("u")
    target = _upload_dir() / f"{upload_id}__{safe}"
    target.write_bytes(raw)
    mime = file.content_type or mimetypes.guess_type(safe)[0] or "application/octet-stream"
    return {"id": upload_id, "name": safe, "mime": mime, "size": len(raw),
            "kind": "image" if mime in IMAGE_MIMES else "file",
            "path": f"{UPLOAD_DIRNAME}/{target.name}"}


# ------------------------------------------------------------------ artifacts
@router.get("/artifacts")
async def artifacts() -> list[dict]:
    """What the agent has produced, newest first.

    The workspace is where files land; without a list of them, "I saved it to report.md"
    is a claim the reader has to go and check in a terminal.
    """
    root = c.workspace()
    found: list[dict] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name.startswith("."):
            continue
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] in ("uploads", ".results"):
            continue
        stat = path.stat()
        found.append({"path": str(relative), "name": path.name, "bytes": stat.st_size,
                      "modified": stat.st_mtime,
                      "kind": path.suffix.lstrip(".").lower() or "file"})
    return sorted(found, key=lambda f: f["modified"], reverse=True)[:400]


# Formats a browser can show on this origin without running anything the app did not
# write. HTML is deliberately absent: served inline here it would run with the app's cookies.
_INLINE = {".pdf": "application/pdf", ".png": "image/png", ".svg": "image/svg+xml",
           ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


@router.get("/artifacts/{path:path}")
async def artifact(path: str, inline: bool = False, download: bool = False):
    from fastapi.responses import FileResponse, PlainTextResponse
    try:
        target = file_tool.resolve(c.workspace(), path)
    except file_tool.OutsideWorkspace as exc:
        raise HTTPException(400, str(exc)) from exc
    if not target.is_file():
        raise HTTPException(404, "No such file in the workspace.")
    if download:
        return FileResponse(target, filename=target.name)
    if inline and target.suffix.lower() in _INLINE:
        media = _INLINE[target.suffix.lower()]
        headers = {"Content-Security-Policy": "script-src 'none'"} if media == "image/svg+xml" else {}
        return FileResponse(target, media_type=media, headers=headers)
    if target.stat().st_size > 2_000_000:
        return FileResponse(target, filename=target.name)
    try:
        return PlainTextResponse(target.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return FileResponse(target, filename=target.name)


# --------------------------------------------------------------------- search
@router.get("/search")
async def search(q: str, limit: int = 30) -> list[dict]:
    """Find a conversation by what was said in it.

    A scan, not an index: a personal agent accumulates hundreds of conversations, not
    millions, and a scan over hundreds is instant and cannot fall out of sync.
    """
    needle = (q or "").strip().lower()
    if len(needle) < 2:
        return []
    hits: list[dict] = []
    for conv in c.store.conversations().values():
        for message in conv.get("messages") or []:
            body = str(message.get("content") or "")
            position = body.lower().find(needle)
            if position < 0:
                continue
            start = max(0, position - 60)
            hits.append({"conversation_id": conv["id"],
                         "title": conv.get("title") or "",
                         "role": message.get("role"),
                         "updated_at": conv.get("updated_at"),
                         "excerpt": ("…" if start else "") + body[start:position + 140]})
            break
    return sorted(hits, key=lambda h: h.get("updated_at") or 0, reverse=True)[:limit]


# ---------------------------------------------------------------- export, retry
@router.get("/conversations/{conv_id}/export")
async def export_conversation(conv_id: str):
    """One Markdown document: the questions, the answers, and the evidence behind them."""
    from fastapi.responses import PlainTextResponse
    conv = c.store.conversation(conv_id)
    if conv is None:
        raise HTTPException(404, "No such conversation.")
    lines = [f"# {conv.get('title') or 'Conversation'}", ""]
    for message in conv.get("messages") or []:
        if message["role"] == "user":
            lines += ["---", "", f"## {message.get('content', '').strip()}", ""]
            continue
        for block in message.get("blocks") or []:
            if block["type"] == "text" and not block.get("superseded") and block.get("text"):
                lines += [block["text"].strip(), ""]
        evidence = [b for b in (message.get("blocks") or []) if b["type"] == "tool"]
        if evidence:
            lines += ["<details><summary>Evidence</summary>", ""]
            for block in evidence:
                mark = "ok" if block.get("ok") else "failed"
                lines.append(f"- `{block.get('ref', '')}` **{block.get('name')}** ({mark}) — "
                             f"{(block.get('summary') or '')[:200]}")
            lines += ["", "</details>", ""]
        trust_note = message.get("trust") or {}
        if trust_note.get("sources"):
            lines += [f"> Read from outside: {', '.join(trust_note['sources'])}.", ""]
    return PlainTextResponse("\n".join(lines), media_type="text/markdown")


def _message_pair(conv_id: str, message_id: str) -> tuple[dict, dict, str]:
    conv = c.store.conversation(conv_id)
    if conv is None:
        raise HTTPException(404, "No such conversation.")
    messages = conv.get("messages") or []
    index = next((i for i, m in enumerate(messages) if m["id"] == message_id), None)
    if index is None or messages[index]["role"] != "assistant":
        raise HTTPException(404, "No such answer.")
    question = next((m.get("content", "") for m in reversed(messages[:index]) if m["role"] == "user"), "")
    return conv, messages[index], question


def _answer_text(message: dict) -> str:
    return "\n".join(b.get("text") or "" for b in message.get("blocks") or []
                     if b["type"] == "text" and not b.get("superseded"))


def _lineage_of(message: dict) -> dict:
    from app.agent import lineage
    return message.get("lineage") or lineage.build(message.get("blocks") or [], _answer_text(message))


@router.get("/conversations/{conv_id}/messages/{message_id}/trail")
async def audit_trail(conv_id: str, message_id: str, format: str = "md"):
    """One answer's audit trail: question, answer, every query behind it, fingerprints, checks."""
    from fastapi.responses import JSONResponse, PlainTextResponse
    from app.agent import lineage
    _conv, message, question = _message_pair(conv_id, message_id)
    chain = _lineage_of(message)
    c.audit.record("answer.trail", conversation=conv_id, message=message_id, format=format)
    name = f"audit-trail-{message_id}"
    if format == "json":
        body = {"question": question, "answer": _answer_text(message), "model": message.get("model"),
                "created_at": message.get("created_at"), "status": message.get("status"),
                "usage": message.get("usage"), "lineage": chain, "checks": message.get("checks") or [],
                "method": message.get("method") or {}, "trust": message.get("trust") or {}}
        return JSONResponse(body, headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
    text = lineage.markdown(question, _answer_text(message), message, chain)
    return PlainTextResponse(text, media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="{name}.md"'})


class RerunBody(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


@router.post("/conversations/{conv_id}/messages/{message_id}/steps/{number}/rerun")
async def rerun_step(conv_id: str, message_id: str, number: int, body: RerunBody) -> dict:
    """Run one of the answer's steps again, with the reader's own edits — no model involved.

    The analyst's loop: read the query the agent wrote, change the date or the filter, run
    it, look. Waiting two minutes for a model to make a one-word edit is the wrong tool for
    that. Read-only tools only (one that changes data stays with the agent, behind its
    approval); the air gap applies to any URL in the arguments; secrets are stripped from
    the result; every run is in the audit log. Not added to the answer's lineage — it is the
    reader's exploration, not the agent's evidence.
    """
    import time as _time
    from app import network
    from app.agent import trust
    _conv, message, _question = _message_pair(conv_id, message_id)
    ref = f"#{number}"
    block = next((b for b in message.get("blocks") or []
                  if b.get("type") == "tool" and b.get("ref") == ref), None)
    if block is None:
        raise HTTPException(404, f"No step {ref} in this answer.")
    tool = c.mcp.resolve(str(block.get("name") or "")) if block.get("kind") == "mcp" else None
    if tool is None:
        raise HTTPException(409, "Only a step that called a connected source can be run again by hand.")
    if tool.get("write"):
        raise HTTPException(403, f"{tool['qualified_name']} can change data: ask the agent, which "
                                 f"runs it behind an approval.")
    blob = json.dumps(body.arguments, ensure_ascii=False, default=str)
    if len(blob) > 50_000:
        raise HTTPException(413, "Arguments too large.")
    for url in re.findall(r"https?://[^\s<>\"')\]]+", blob):
        host = trust.host_of(url)
        if network.airgapped() and host and not network.is_internal_host(host):
            raise HTTPException(403, f"{host} is outside the private network; this deployment is air-gapped.")
    started = _time.time()
    result = await c.mcp.call(tool["qualified_name"], body.arguments)
    text = str(result.get("text") or result.get("error") or "")
    text, redacted = trust.redact(text, c.secret_values())
    elapsed = int((_time.time() - started) * 1000)
    c.audit.record("step.rerun", conversation=conv_id, message=message_id, ref=ref,
                   tool=tool["qualified_name"], ok=bool(result.get("ok")), ms=elapsed,
                   args=trust.redact(blob[:300], c.secret_values())[0])
    return {"ok": bool(result.get("ok")), "text": text[:400_000], "truncated": len(text) > 400_000,
            "error": "" if result.get("ok") else text[:2000], "ms": elapsed, "redacted": redacted,
            "tool": tool["qualified_name"]}


@router.get("/conversations/{conv_id}/messages/{message_id}/results/{number}")
async def result_file(conv_id: str, message_id: str, number: int, format: str = "xlsx"):
    """Every row of one step's result, as a file — without asking the model.

    What the reader sees under a step is an excerpt (the first rows, or what fitted in the
    context); a result too large for the context was parked on disk whole. This reads the
    whole of it and writes it the way extracts are written: Excel with a Provenance sheet
    naming the query, or CSV with its provenance beside it.
    """
    import asyncio
    import time as _time
    from fastapi.responses import FileResponse
    from app.agent import lineage
    from app.data import exports as export_lib
    from app.data import rows as rows_lib
    if format not in ("xlsx", "csv"):
        raise HTTPException(400, "format is xlsx or csv.")
    _conv, message, question = _message_pair(conv_id, message_id)
    ref = f"#{number}"
    blocks = message.get("blocks") or []
    block = next((b for b in blocks if b.get("type") == "tool" and b.get("ref") == ref), None)
    if block is None:
        raise HTTPException(404, f"No step {ref} in this answer.")
    try:
        rows, label = rows_lib._rows_of_block(block, ref)
    except rows_lib.SourceError as exc:
        raise HTTPException(409, str(exc)) from exc
    provenance = {"question": question, "generated_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
                  "model": message.get("model") or "",
                  "tables": [{"sheet": f"Step {number}", "source": label,
                              "chain": lineage.for_ref(blocks, ref)}]}
    name = f"{block.get('name', 'result')}-step{number}-{message_id[-6:]}"
    try:
        info = await asyncio.to_thread(export_lib.export, [(f"Step {number}", rows)], format,
                                       c.workspace() / "exports", name, label, provenance, True)
    except export_lib.ExportError as exc:
        raise HTTPException(409, str(exc)) from exc
    c.audit.record("result.export", conversation=conv_id, message=message_id, ref=ref,
                   rows=len(rows), format=format)
    media = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if format == "xlsx"
             else "text/csv")
    return FileResponse(info["path"], filename=info["name"], media_type=media)


@router.get("/conversations/{conv_id}/messages/{message_id}/pdf")
async def answer_pdf(conv_id: str, message_id: str):
    """One answer as a PDF, without asking the model for anything.

    The question, the answer as written, every chart it drew, its numbered sources and the
    method-and-provenance appendix — assembled from what the run stored. `create_report` is
    the agent's way to a report; this is the reader's, and it works whatever the model.
    """
    import asyncio
    import datetime as dt
    from fastapi.responses import FileResponse
    from app.data import charts as chart_lib
    from app.data import exports as export_lib
    from app.data import report as report_lib
    conv, message, question = _message_pair(conv_id, message_id)
    answer = _answer_text(message).strip()
    if not answer:
        raise HTTPException(409, "This answer has no text to export yet.")
    blocks = message.get("blocks") or []
    tools = [b for b in blocks if b.get("type") == "tool" and b.get("ref")]
    charts: dict[str, dict] = {}
    store = chart_lib.ChartStore(c.workspace())
    for block in tools:
        drawn = block.get("chart")
        if not drawn or not block.get("ok"):
            continue
        key = str(drawn.get("id") or block["ref"])
        spec = drawn.get("spec") or (store.latest(conv_id, key) or {}).get("spec")
        if spec:
            charts[key] = {"id": key, "spec": spec, "source": drawn.get("source") or block["ref"]}
    sources: dict[int, str] = {}
    for block in tools:
        if not block.get("ok"):
            continue
        args = block.get("args") or {}
        query = next((str(v) for k, v in args.items() if k in ("query", "sql", "expression") and v), "")
        detail = f": {' '.join(query.split())[:220]}" if query else ""
        sources[int(block["ref"].lstrip("#"))] = f"{block.get('name', '')}{detail} — {str(block.get('summary') or '')[:120]}"
    chain = _lineage_of(message)
    lang = report_lib.detect_language(question, answer)
    # A chart the answer placed — `![c1]`, the way models write it — goes where it was
    # placed; the others follow the text.
    sections: list[dict] = []
    placed: set[str] = set()
    cursor = 0
    for match in re.finditer(r"!\[([^\]]*)\](?:\([^)]*\))?", answer):
        key = next((k for k in charts if k in {match.group(1).strip(), match.group(1).strip().removeprefix("chart:")}), None)
        if key is None or key in placed:
            continue
        if answer[cursor:match.start()].strip():
            sections.append({"text": answer[cursor:match.start()]})
        sections.append({"chart": key})
        placed.add(key)
        cursor = match.end()
    if answer[cursor:].strip():
        sections.append({"text": answer[cursor:]})
    sections += [{"chart": key} for key in charts if key not in placed]
    method = str((message.get("method") or {}).get("text") or "").strip()
    if method:
        # Written on request from the lineage (Explain the method): the plain-words account
        # a reader forwards the PDF for.
        sections.append({"heading": "Méthode" if lang == "fr" else "Method", "text": method})
    # The conversation's title when it says something the question does not; the question
    # itself otherwise, once.
    asked = " ".join(question.split())
    named = " ".join(str(conv.get("title") or "").split())
    if named and not asked.lower().startswith(named.lower().rstrip("…. ")):
        title, subtitle = named, asked
    else:
        title, subtitle = (asked or named or "Answer"), ""
    if len(title) > 120:
        title, subtitle = title[:119] + "…", asked
    subtitle = subtitle if len(subtitle) <= 300 else subtitle[:299] + "…"
    # One file per answer, rewritten on each export: the button is pressed again after a
    # retry or a new chart, and a workspace of -2, -3, -4 copies helps nobody.
    folder = c.workspace() / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    stem = export_lib.safe_name(title, "pdf")[:-4][:60].rstrip("-.")
    path = folder / f"{stem}-{message_id[-6:]}.pdf"
    try:
        info = await asyncio.to_thread(
            report_lib.build, path, title=title, subtitle=subtitle, sections=sections,
            sources=sources, used=set(), charts=charts, tables={},
            generated=dt.datetime.fromtimestamp(message.get("created_at") or 0) if message.get("created_at") else None,
            provenance=[n for n in chain.get("nodes") or [] if n.get("ok", True)], language=lang)
    except Exception as exc:  # noqa: BLE001 - a layout failure is reported, not a 500 with a trace
        raise HTTPException(500, f"The PDF could not be built: {type(exc).__name__}: {exc}") from exc
    c.audit.record("answer.pdf", conversation=conv_id, message=message_id, file=info["name"],
                   pages=info["pages"])
    return FileResponse(path, filename=info["name"], media_type="application/pdf")


@router.post("/conversations/{conv_id}/messages/{message_id}/explain")
async def explain_answer(conv_id: str, message_id: str) -> dict:
    """How this answer was produced, in plain words — written once, then kept with it.

    Built from the lineage (the exact queries and computations), never from the answer's
    own account of itself: an explanation that restates the answer's claims explains
    nothing. The fast model writes it; the facts it may use are the ones listed.
    """
    from app.agent import prompts
    _conv, message, question = _message_pair(conv_id, message_id)
    if (message.get("method") or {}).get("text"):
        return message["method"]
    chain = _lineage_of(message)
    facts = []
    for n in chain.get("nodes") or []:
        shape = f", {n['rows']} rows" if n.get("rows") is not None else ""
        uses = f", uses {', '.join(n['depends_on'])}" if n.get("depends_on") else ""
        facts.append(f"{n['ref']} [{n['kind']}] {n['tool']} on {n['source'] or 'app'}{shape}{uses}:\n{n['operation'][:900]}")
    checks = "\n".join(f"- {x.get('name')}: {x.get('result')} {x.get('detail') or ''}" for x in message.get("checks") or [])
    prompt = (f"Question: {question[:600]}\n\nAnswer given:\n{_answer_text(message)[:2500]}\n\n"
              f"Evidence chain (exact operations):\n" + "\n\n".join(facts)[:9000]
              + (f"\n\nChecks:\n{checks}" if checks else ""))
    try:
        result = await c.fast_llm.chat([{"role": "user", "content": prompt}], system=prompts.EXPLAIN_SYSTEM,
                                       temperature=0.1, think=False)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"The model could not write the explanation: {exc}") from exc
    method = {"text": (result.content or "").strip(), "model": getattr(c.fast_llm, "model", ""), "at": now()}
    message["method"] = method
    c.store.touch()
    await c.store.save()
    return method


class RetryBody(BaseModel):
    message_id: str
    text: str = ""


@router.post("/conversations/{conv_id}/retry")
async def retry(conv_id: str, body: RetryBody) -> dict:
    """Re-ask a question, optionally reworded, discarding what followed it.

    Editing the question and trying again is the commonest thing anyone wants from a
    transcript, and forking a new conversation to do it loses the context that made the
    question make sense.
    """
    conv = c.store.conversation(conv_id)
    if conv is None:
        raise HTTPException(404, "No such conversation.")
    messages = conv.get("messages") or []
    index = next((i for i, m in enumerate(messages) if m["id"] == body.message_id), -1)
    if index < 0 or messages[index]["role"] != "user":
        raise HTTPException(404, "That is not a question in this conversation.")
    question = (body.text or messages[index].get("content") or "").strip()
    # Asked again the way it was asked: a direct question stays direct, on the same servers.
    answer = messages[index + 1] if index + 1 < len(messages) else {}
    mode = "direct" if answer.get("mode") == "direct" else "agent"
    conv["messages"] = messages[:index]
    conv["updated_at"] = now()
    c.store.touch()
    await c.store.save()
    return await c.runner.start(conv_id, question, [], mode=mode, servers=list(answer.get("server_ids") or []))


# --------------------------------------------------------------------- memory
@router.get("/memory")
async def list_memory() -> list[dict]:
    return c.memory.all()


@router.post("/memory/{memory_id}/confirm")
async def confirm_memory(memory_id: str) -> dict:
    """Release one quarantined memory.

    A fact the agent learned while reading something foreign waits here. Confirming is the
    user saying "yes, that is true and worth keeping" — which is a judgement only they can
    make, because the alternative is trusting the page that suggested it.
    """
    if not c.memory.confirm(memory_id):
        raise HTTPException(404, "No memory is waiting under that id.")
    return {"ok": True}


class MemoryBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


@router.post("/memory")
async def add_memory(body: MemoryBody) -> dict:
    return c.memory.add(body.text, source="user")


@router.delete("/memory/{memory_id}")
async def forget(memory_id: str) -> dict:
    if not c.memory.forget(memory_id):
        raise HTTPException(404, "No such memory.")
    return {"ok": True}
