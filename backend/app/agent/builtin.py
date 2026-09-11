"""The tools Agent has before you connect anything.

A freshly installed Agent with zero MCP servers can already search, read a page, compute,
keep files and remember things. That matters for the same reason the rest of the app
starts clean: the empty state has to be genuinely useful, not a demo of what it would do
once configured.

Every handler returns the same normalized shape — `{ok, summary, text?, data?, error?}` —
so the agent loop, the critic and the UI need no special case for any one of them.
"""

from __future__ import annotations

import platform
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.tools import code as code_tool
from app.tools import files as file_tool
from app.tools import web as web_tool


class ToolSpec:
    def __init__(self, name: str, description: str, parameters: dict,
                 handler: Callable[..., Any], *, write: bool = False,
                 group: str = "Built-in") -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler
        self.write = write
        self.group = group

    def as_function(self) -> dict:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description,
                             "parameters": self.parameters}}


def _obj(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or []}


def normalize_plan(raw: Any) -> list[dict]:
    """Accept whatever shape the model produced and force it into one.

    Small local models write a plan as a list of strings about as often as a list of
    objects, and some send `{"steps": [...]}` nested one level too deep. Normalising here
    means a sloppy but perfectly clear plan renders instead of erroring — while an
    unrecognised status still degrades to `pending` rather than inventing progress.
    """
    if isinstance(raw, dict):
        raw = raw.get("steps") or raw.get("plan") or []
    if isinstance(raw, str):
        raw = [line.strip(" -*\t") for line in raw.splitlines() if line.strip()]
    if not isinstance(raw, list):
        return []
    steps: list[dict] = []
    for index, item in enumerate(raw[:20]):
        if isinstance(item, str):
            title, status = item.strip(), "pending"
        elif isinstance(item, dict):
            title = str(item.get("title") or item.get("step") or item.get("description") or "").strip()
            status = str(item.get("status") or "pending").strip().lower()
        else:
            continue
        if not title:
            continue
        # Models routinely write progress into the title — "Write the file (done)", "✓ Write
        # the file" — instead of the status field. Reading it there is not inventing
        # progress: the model said so, just in the wrong place. Leaving it unread is what
        # makes every plan sit at 0/N while the work is visibly finished.
        lead = re.match(r"^\s*(?:✓|✔|\[[xX]\])\s*", title)
        if lead:
            status, title = "done", title[lead.end():].strip()
        marker = re.search(r"[\s(\[]*(?:✓|✔|\bdone\b|\bcompleted\b|\bskipped\b)[\s)\]]*$",
                           title, re.I)
        if marker:
            word = marker.group(0).strip(" ()[]✓✔").lower()
            status = "skipped" if word.startswith("skip") else "done"
            title = title[:marker.start()].strip(" -–—:") or title
        if status not in ("pending", "active", "done", "skipped"):
            status = "pending"
        steps.append({"index": index, "title": title[:200], "status": status})
    return steps


def build_registry(settings, memory, workspace: Path, on_plan,
                   foreign_roots: Callable[[], str] | None = None) -> dict[str, ToolSpec]:
    """Assemble the built-in tools that are actually enabled right now.

    A tool switched off in Admin is *absent* from the catalog the model sees, not present
    and failing — an agent told about a capability it cannot use wastes turns discovering
    that, and reports the failure as if the world were broken.
    """

    async def h_web_search(query: str = "", max_results: int = 6, **_: Any) -> dict:
        result = await web_tool.web_search(query, max_results=min(int(max_results or 6), 10),
                                           timeout_s=settings.web_timeout_s)
        if not result.get("ok"):
            return result
        lines = [f"{i + 1}. {r['title']}\n   {r['url']}\n   {r['snippet'][:240]}"
                 for i, r in enumerate(result["results"])]
        return {"ok": True, "summary": f"{len(result['results'])} result(s) for “{query}”",
                "text": "\n".join(lines), "data": result["results"]}

    async def h_web_fetch(url: str = "", **_: Any) -> dict:
        result = await web_tool.fetch_url(url, max_bytes=settings.web_fetch_max_bytes,
                                          timeout_s=settings.web_timeout_s)
        if not result.get("ok"):
            return result
        text = result["text"]
        return {"ok": True,
                "summary": f"{result['title'] or result['url']} — {len(text)} characters",
                "text": text[:24000] + ("\n\n[truncated at 24k characters]" if len(text) > 24000 else ""),
                "data": {"url": result["url"], "title": result["title"]}}

    async def h_run_python(code: str = "", **_: Any) -> dict:
        result = await code_tool.run_python(code, workspace=workspace,
                                            timeout_s=settings.python_timeout_s,
                                            memory_mb=settings.python_memory_mb)
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error", "failed"),
                    "text": result.get("stdout", "")}
        out = result.get("stdout", "").strip()
        return {"ok": True,
                "summary": (f"ran in {result['elapsed_ms']} ms — "
                            + (f"{len(out.splitlines())} line(s) of output" if out else "no output")),
                "text": out or "(the code ran and printed nothing — print() what you need to see)",
                "data": {"elapsed_ms": result["elapsed_ms"]}}

    def elsewhere(result: dict) -> dict:
        """Turn "not here" into "here is where it is".

        The built-in file tools see one directory; a connected Filesystem server sees
        another. A model that asks the wrong one gets a dead end unless the dead end
        names the alternative — which costs one line and saved two of the ten cases from
        spending four turns hunting for a file that was never in the workspace.
        """
        hint = foreign_roots() if foreign_roots else ""
        if hint and not result.get("ok"):
            result["error"] = f"{result.get('error', '')} {hint}".strip()
        return result

    async def h_list_files(path: str = "", **_: Any) -> dict:
        result = file_tool.list_files(workspace, path)
        if not result.get("ok"):
            return elsewhere(result)
        lines = [f"{'📁' if e['type'] == 'dir' else '📄'} {e['name']}"
                 + (f"  ({e['size']} bytes)" if e["type"] == "file" else "")
                 for e in result["entries"]]
        return {"ok": True, "summary": f"{result['count']} item(s) in {result['path']}",
                "text": "\n".join(lines) or "(empty)", "data": result["entries"]}

    async def h_read_file(path: str = "", **_: Any) -> dict:
        result = file_tool.read_file(workspace, path)
        if not result.get("ok"):
            return elsewhere(result)
        return {"ok": True, "summary": f"{path} — {result['size']} bytes",
                "text": result["text"]}

    async def h_write_file(path: str = "", content: str = "", **_: Any) -> dict:
        result = file_tool.write_file(workspace, path, content)
        if not result.get("ok"):
            return elsewhere(result)
        # The result echoes what actually landed on disk. A model that elided its own
        # content — "# Fibonacci ..." where twelve numbers were meant — sees the elision
        # in the very next turn instead of narrating the file it intended to write.
        written = content or ""
        excerpt = written[:240] + ("\n… (truncated in this echo)" if len(written) > 240 else "")
        return {"ok": True, "summary": f"{result['action']} {path} ({result['bytes']} bytes)",
                "text": f"{result['action']} {path} ({result['bytes']} bytes). "
                        f"Exactly this was written:\n---\n{excerpt}\n---"}

    async def h_remember(fact: str = "", **_: Any) -> dict:
        try:
            entry = memory.add(fact, source="agent")
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "summary": "remembered", "text": entry["text"]}

    async def h_recall(query: str = "", **_: Any) -> dict:
        found = memory.recall(query, limit=6)
        if not found:
            return {"ok": True, "summary": "nothing relevant remembered",
                    "text": "No stored memory matches that."}
        return {"ok": True, "summary": f"{len(found)} memor(y/ies)",
                "text": "\n".join(f"- {entry['text']}" for entry in found)}

    async def h_plan(steps: Any = None, **kwargs: Any) -> dict:
        normalized = normalize_plan(steps if steps is not None else kwargs)
        if not normalized:
            return {"ok": False,
                    "error": "No usable steps. Send steps as a list of short titles, or a list "
                             "of {title, status} objects."}
        on_plan(normalized)
        done = sum(1 for s in normalized if s["status"] == "done")
        return {"ok": True, "summary": f"plan: {done}/{len(normalized)} done",
                "text": "\n".join(
                    f"{'✓' if s['status'] == 'done' else '▸' if s['status'] == 'active' else '·'} "
                    f"{s['title']}" for s in normalized)}

    async def h_now(timezone_name: str = "", **_: Any) -> dict:
        local = datetime.now().astimezone()
        text = (f"Local time: {local.strftime('%A %d %B %Y, %H:%M:%S %Z')}\n"
                f"UTC: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Host: {platform.system()} {platform.release()}")
        return {"ok": True, "summary": local.strftime("%Y-%m-%d %H:%M %Z"), "text": text}

    specs: list[ToolSpec] = [
        ToolSpec("plan", _PLAN_DESC,
                 _obj({"steps": {"type": "array", "description":
                                 "The full list of steps, in order. Re-send the whole list every "
                                 "time you update it.",
                                 "items": {"type": "object", "properties": {
                                     "title": {"type": "string"},
                                     "status": {"type": "string",
                                                "enum": ["pending", "active", "done", "skipped"]}},
                                     "required": ["title"]}}}, ["steps"]),
                 h_plan, group="Planning"),
        ToolSpec("current_time", "The current date and time on this machine.",
                 _obj({}), h_now),
    ]
    if settings.enable_web_tools:
        specs += [
            ToolSpec("web_search",
                     "Search the web and get back titles, URLs and snippets. Use it whenever the "
                     "answer depends on anything current, specific or outside your training data. "
                     "Follow up with web_fetch on the URLs worth actually reading.",
                     _obj({"query": {"type": "string", "description": "What to search for."},
                           "max_results": {"type": "integer",
                                           "description": "1-10, default 6."}}, ["query"]),
                     h_web_search),
            ToolSpec("web_fetch",
                     "Fetch one URL and read it as text. Works on HTML pages, JSON, CSV and plain "
                     "text. Use it on search results, documentation, APIs and raw files.",
                     _obj({"url": {"type": "string", "description": "Full http(s) URL."}}, ["url"]),
                     h_web_fetch),
        ]
    if settings.enable_python_tool:
        modules = ", ".join(code_tool.available_modules()) or "the standard library only"
        specs.append(ToolSpec(
            "run_python",
            f"Run Python in a separate process and get its stdout back. This is how you do real "
            f"work: arithmetic, dates, parsing, statistics, reading and writing files, generating "
            f"documents. Never compute a number in your head when you can run it here. "
            f"print() everything you need to see — nothing else is returned. "
            f"Available beyond the standard library: {modules}. "
            f"The working directory is the Agent workspace, so relative paths are shared with the "
            f"file tools. Killed after {settings.python_timeout_s}s.",
            _obj({"code": {"type": "string", "description": "The Python source to run."}}, ["code"]),
            h_run_python, write=True, group="Compute"))
    specs += [
        ToolSpec("workspace_list", "List what is in this app's own workspace directory — NOT a directory exposed by a "
                 "connected MCP server, which has its own tools.",
                 _obj({"path": {"type": "string",
                                "description": "Sub-path inside the workspace. Empty for the root."}}),
                 h_list_files, group="Workspace"),
        ToolSpec("workspace_read", "Read a text file from this app's own workspace directory. For a path under a "
                 "connected MCP server's root, use that server's own read tool instead.",
                 _obj({"path": {"type": "string"}}, ["path"]), h_read_file, group="Workspace"),
        ToolSpec("workspace_write",
                 "Write a text file into this app's own workspace, creating folders as needed. "
                 "For a path under a connected MCP server's root, use that server's write tool.",
                 _obj({"path": {"type": "string"}, "content": {"type": "string"}},
                      ["path", "content"]),
                 h_write_file, write=True, group="Workspace"),
        ToolSpec("remember",
                 "Store one durable fact about the user or their work, so it is available in every "
                 "future conversation. Use it for stable preferences, names, and context worth "
                 "keeping — never for something true only inside this conversation.",
                 _obj({"fact": {"type": "string",
                                "description": "One self-contained sentence."}}, ["fact"]),
                 h_remember, group="Memory"),
        ToolSpec("recall", "Search everything previously remembered about the user.",
                 _obj({"query": {"type": "string"}}, ["query"]), h_recall, group="Memory"),
    ]
    return {spec.name: spec for spec in specs}


_PLAN_DESC = (
    "Lay out your plan for a task that needs several steps, and keep it updated as you go. "
    "The user sees this list live, so it is how they follow what you are doing. "
    "Call it once at the start of any multi-step task, then AGAIN after each step completes, "
    "re-sending the whole list with that step's status set to \"done\". A plan left at 0 done "
    "tells the user nothing happened. Skip it entirely for anything you can answer in one or "
    "two moves."
)


def catalog_text(tools: dict[str, ToolSpec], mcp_tools: list[dict],
                 scopes: list[dict] | None = None) -> str:
    """A compact index of the live tool surface for the system prompt.

    Names only, grouped. The full descriptions and JSON schemas already travel in the
    request's `tools` payload, and repeating them here costs around a thousand tokens of
    a context window the agent needs for actual work — with no benefit, since the model
    reads both. What this adds is the one thing the payload does not convey: which server
    a tool belongs to, so the model can reason about capability in groups.
    """
    lines = [f"Built-in: {', '.join(tools)}"]
    scope_by_server = {s["server"]: s for s in (scopes or [])}
    if mcp_tools:
        by_server: dict[str, list[str]] = {}
        for tool in mcp_tools:
            by_server.setdefault(tool["server_name"], []).append(tool["qualified_name"])
        for server, names in by_server.items():
            shown = ", ".join(names[:40])
            more = f" (+{len(names) - 40} more)" if len(names) > 40 else ""
            scope = scope_by_server.get(server)
            where = ""
            if scope:
                target = ", ".join(scope["paths"]) or scope["url"]
                # A fact, and only a fact. The failure this removes is the model passing
                # "." or a guessed root to a server that was configured with a real one.
                # How each server wants its paths spelled is its own schema's business,
                # and an instruction here overrides that to everyone's cost.
                where = f"\n    ↳ this server is configured for: {target}"
            lines.append(f"{server}: {shown}{more}{where}")
    return "\n".join(lines)


def truncate_for_model(text: str, limit: int = 6000) -> str:
    """Keep both ends of an over-long tool result.

    The head says what the result is; the tail is usually where the totals and the errors
    live. Cutting only the tail throws away the half that answers the question.
    """
    if len(text) <= limit:
        return text
    head, tail = int(limit * 0.6), int(limit * 0.35)
    return (f"{text[:head]}\n\n[… {len(text) - head - tail} characters cut from the middle …]\n\n"
            f"{text[-tail:]}")


def now_ms() -> int:
    return int(time.time() * 1000)
