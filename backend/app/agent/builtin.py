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
import datetime as dt
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.agent import trust
from app.tools import code as code_tool
from app.tools import files as file_tool


class ToolSpec:
    def __init__(self, name: str, description: str, parameters: dict,
                 handler: Callable[..., Any], *, write: bool = False,
                 group: str = "Built-in",
                 capabilities: tuple[str, ...] = ()) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler
        self.write = write
        self.group = group
        # What this tool can reach. The taint rules in agent/trust.py act on these, so a
        # tool that only reads the workspace is never gated by something a web page said.
        self.capabilities = frozenset(capabilities)

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
                   foreign_roots: Callable[[], str] | None = None,
                   granted_roots: Callable[[], list[str]] | None = None,
                   is_tainted: Callable[[], bool] | None = None,
                   find_tools: Callable[[str], dict] | None = None,
                   bridge=None, bridge_tools: Callable[[], list[str]] | None = None
                   ) -> dict[str, ToolSpec]:
    """Assemble the built-in tools that are actually enabled right now.

    A tool switched off in Admin is *absent* from the catalog the model sees, not present
    and failing — an agent told about a capability it cannot use wastes turns discovering
    that, and reports the failure as if the world were broken.
    """

    async def h_run_python(code: str = "", _setup: str = "", **_: Any) -> dict:
        callable_tools = bridge_tools() if bridge_tools else []
        result = await code_tool.run_python(code, workspace=workspace,
                                            timeout_s=settings.python_timeout_s,
                                            memory_mb=settings.python_memory_mb,
                                            bridge=bridge, bridge_tools=callable_tools,
                                            setup=_setup)
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error", "failed"),
                    "text": result.get("stdout", "")}
        out = result.get("stdout", "").strip()
        used = result.get("bridge_calls") or []
        return {"ok": True,
                "summary": (f"ran in {result['elapsed_ms']} ms — "
                            + (f"{len(out.splitlines())} line(s) of output" if out else "no output")
                            + (f", {len(used)} tool call(s)" if used else "")),
                # No structured `data`: what the code printed is the result. A metadata
                # object here was read as a one-row table, and every chart or export that
                # named this call got a table of `elapsed_ms` instead of the printed rows.
                "text": out or "(the code ran and printed nothing — print() what you need to see)"}

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
        text = result["text"]
        # A data file lying in the workspace is a snapshot someone saved, not a source: a
        # stale trades.csv from another conversation was once read in place of the trade
        # store and charted as the answer. Its age is stated where the model reads it.
        if re.search(r"\.(csv|tsv|xlsx?|json|parquet)$", path, re.I) and ".results/" not in path:
            try:
                target = file_tool.resolve(workspace, path)
                saved = datetime.fromtimestamp(target.stat().st_mtime)
                age_h = (datetime.now() - saved).total_seconds() / 3600
                if age_h > 1:
                    text = (f"[{path} is a file saved in the workspace on {saved:%Y-%m-%d %H:%M} "
                            f"({age_h:.0f} h ago) — a snapshot, not a live source. If the question is "
                            f"about current data, query the source instead.]\n{text}")
            except Exception:  # noqa: BLE001 - the note is a courtesy; the read already succeeded
                pass
        return {"ok": True, "summary": f"{path} — {result['size']} bytes", "text": text}

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

    async def h_import(source_path: str = "", name: str = "", **_: Any) -> dict:
        result = file_tool.import_file(workspace, source_path, name,
                                       granted_roots() if granted_roots else [])
        if not result.get("ok"):
            return result
        return {"ok": True,
                "summary": f"imported {result['name']} ({result['bytes']} bytes)",
                "text": f"Copied {result['source']} to {result['name']} in the workspace, "
                        f"{result['bytes']} bytes, byte for byte."}

    async def h_remember(fact: str = "", **_: Any) -> dict:
        tainted = bool(is_tainted and is_tainted())
        try:
            entry = memory.add(fact, source="agent", tainted=tainted)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if entry.get("status") == "quarantined":
            return {"ok": True, "summary": "held for confirmation",
                    "text": f"Stored, but held for the user to confirm: this run has read "
                            f"content from outside, so a fact learned now could have been "
                            f"suggested by it. It will not be recalled until they approve it."}
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

    async def h_find_tools(need: str = "", **_: Any) -> dict:
        if find_tools is None:
            return {"ok": False, "error": "No tool catalogue is available."}
        return find_tools(need)

    async def h_now(timezone_name: str = "", **_: Any) -> dict:
        local = datetime.now().astimezone()
        text = (f"Local time: {local.strftime('%A %d %B %Y, %H:%M:%S %Z')}\n"
                f"UTC: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Host: {platform.system()} {platform.release()}")
        return {"ok": True, "summary": local.strftime("%Y-%m-%d %H:%M %Z"), "text": text}

    async def h_business_days(operation: str = "check", date: str = "", dates: Any = None,
                              n: int = 0, end: str = "", year: int = 0, calendar: str = "TARGET2",
                              holidays: Any = None, **_: Any) -> dict:
        from app.data import calendars as cal_lib
        try:
            extra = holidays if isinstance(holidays, list) else (
                [h for h in str(holidays or "").replace(";", ",").split(",") if h.strip()])
            cal = cal_lib.Calendar(calendar, extra)
            op = (operation or "check").strip().lower()
            if op in ("check", "is_business_day"):
                days = dates if isinstance(dates, list) and dates else [date]
                lines = [cal_lib.describe(cal, cal_lib.parse(d)) for d in days[:60]]
            elif op in ("add", "offset", "settlement"):
                start = cal_lib.parse(date)
                result = cal.add(start, int(n))
                lines = [f"{start.isoformat()} {'+' if int(n) >= 0 else '-'} {abs(int(n))} business day(s) "
                         f"({cal.name}) = {result.isoformat()} ({result.strftime('%A')})"]
            elif op in ("previous", "prev", "roll_back"):
                day = cal.roll(cal_lib.parse(date) - dt.timedelta(days=0 if op == "roll_back" else 1), -1)
                lines = [f"{'Last business day on or before' if op == 'roll_back' else 'Previous business day before'} "
                         f"{date} ({cal.name}): {day.isoformat()} ({day.strftime('%A')})"]
            elif op in ("next", "roll_forward"):
                day = cal.roll(cal_lib.parse(date) + dt.timedelta(days=0 if op == "roll_forward" else 1), 1)
                lines = [f"{'First business day on or after' if op == 'roll_forward' else 'Next business day after'} "
                         f"{date} ({cal.name}): {day.isoformat()} ({day.strftime('%A')})"]
            elif op in ("between", "count"):
                start, stop = cal_lib.parse(date), cal_lib.parse(end)
                lines = [f"{cal.between(start, stop)} business day(s) ({cal.name}) after {start.isoformat()} "
                         f"up to and including {stop.isoformat()}"]
            elif op in ("month_ends", "month_end"):
                y = int(year or (cal_lib.parse(date).year if date else datetime.now().year))
                lines = [f"{cal.month_end(y, m).isoformat()}" for m in range(1, 13)]
                lines.insert(0, f"Last business day of each month of {y} ({cal.name}):")
            elif op in ("holidays", "list"):
                y = int(year or (cal_lib.parse(date).year if date else datetime.now().year))
                named = cal_lib.holidays(cal.name, y)
                lines = [f"{d.isoformat()} ({d.strftime('%A')}): {name}" for d, name in sorted(named.items())]
                lines.insert(0, f"{cal.name} holidays in {y}" + ("" if lines else ": none (weekends only)"))
            else:
                return {"ok": False, "error": "operation is one of check, add, previous, next, between, "
                                              "month_ends, holidays."}
        except (ValueError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}
        text = "\n".join(lines)
        return {"ok": True, "summary": lines[0][:160] if len(lines) == 1 else f"{len(lines)} line(s) — {cal.name}",
                "text": text}

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
        ToolSpec("business_days",
                 "Business-day arithmetic, computed from the calendar's rules — never from memory. "
                 "Use it for: was a date a trading/settlement day (check), T+n settlement (add), "
                 "previous/next business day, business days between two dates, the last business "
                 "day of each month (month_ends — the month-end dates risk and positions are "
                 "reported at), a year's holidays. Calendars: TARGET2 (euro, Euronext Paris — "
                 "default), UK, US (NYSE), WEEKDAYS. Extra holidays a source lists can be passed.",
                 _obj({"operation": {"type": "string",
                                     "enum": ["check", "add", "previous", "next", "between", "month_ends", "holidays"]},
                       "date": {"type": "string", "description": "YYYY-MM-DD."},
                       "dates": {"type": "array", "items": {"type": "string"}, "description": "Several dates to check at once."},
                       "n": {"type": "integer", "description": "Business days to add (negative to go back), for add."},
                       "end": {"type": "string", "description": "End date, for between."},
                       "year": {"type": "integer", "description": "For month_ends and holidays."},
                       "calendar": {"type": "string", "description": "TARGET2 (default), UK, US or WEEKDAYS."},
                       "holidays": {"type": "array", "items": {"type": "string"},
                                    "description": "Extra non-business days (YYYY-MM-DD), e.g. from a source's calendar."}},
                      ["operation"]),
                 h_business_days, group="Data"),
        ToolSpec("find_tools",
                 "Make tools callable that were not offered this turn. Give a source's slug "
                 "from the source map — \"market_risk\" — to get all of its tools, or describe "
                 "the capability in your own words — \"FX history\", \"read a spreadsheet\". "
                 "The matches stay callable for the rest of this task.",
                 _obj({"need": {"type": "string",
                                "description": "A source slug, or what you need to do in a few words."}},
                      ["need"]),
                 h_find_tools, group="Planning"),
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
            f"The working directory is the workspace, so relative paths are shared with the "
            f"file tools. Killed after {settings.python_timeout_s}s.\n\n"
            f"**The other tools are callable from inside your code** as ordinary functions — "
            f"`sqlite__read_query(query=...)`, `workspace_read(path=...)` — each returning the "
            f"result as text and raising `ToolError` on failure. Prefer this whenever a task "
            f"is several steps over the same data: one program that fetches, filters and "
            f"writes is one turn, where the same work as separate tool calls is five. Loops "
            f"and conditionals belong here too — asking the model to iterate is how iteration "
            f"goes wrong.",
            _obj({"code": {"type": "string", "description": "The Python source to run."}}, ["code"]),
            h_run_python, write=True, group="Compute",
            capabilities=(trust.EXEC, trust.FS_READ, trust.FS_WRITE)))
    specs += [
        ToolSpec("workspace_list", "List what is in this app's own workspace directory — NOT a directory exposed by a "
                 "connected MCP server, which has its own tools.",
                 _obj({"path": {"type": "string",
                                "description": "Sub-path inside the workspace. Empty for the root."}}),
                 h_list_files, group="Workspace", capabilities=(trust.FS_READ,)),
        ToolSpec("workspace_read", "Read a text file from this app's own workspace directory. For a path under a "
                 "connected MCP server's root, use that server's own read tool instead.",
                 _obj({"path": {"type": "string"}}, ["path"]), h_read_file, group="Workspace", capabilities=(trust.FS_READ,)),
        ToolSpec("workspace_write",
                 "Write a text file into this app's own workspace, creating folders as needed. "
                 "For a path under a connected MCP server's root, use that server's write tool.",
                 _obj({"path": {"type": "string"}, "content": {"type": "string"}},
                      ["path", "content"]),
                 h_write_file, write=True, group="Workspace",
                 capabilities=(trust.FS_WRITE,)),
        ToolSpec("workspace_import",
                 "Copy a file into this app's workspace without its contents passing through "
                 "you. Use this — never read-then-write — whenever a file needs to be where "
                 "another tool can open it: retyping data is how rows get invented. The "
                 "source must be inside a directory a connected server was granted.",
                 _obj({"source_path": {"type": "string",
                                       "description": "Absolute path of the file to copy."},
                       "name": {"type": "string",
                                "description": "Name to save it under. Defaults to the "
                                               "source's own filename."}}, ["source_path"]),
                 h_import, write=True, group="Workspace",
                 capabilities=(trust.FS_READ, trust.FS_WRITE)),
        ToolSpec("remember",
                 "Store one durable fact about the user or their work, so it is available in every "
                 "future conversation. Use it for stable preferences, names, and context worth "
                 "keeping — never for something true only inside this conversation.",
                 _obj({"fact": {"type": "string",
                                "description": "One self-contained sentence."}}, ["fact"]),
                 h_remember, group="Memory", capabilities=(trust.MEMORY_WRITE,)),
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


def _tool_label(tool: dict, width: int) -> str:
    if not width:
        return tool["name"]
    gist = " ".join((tool.get("description") or "").split())
    gist = re.split(r"(?<=[.;:])\s", gist, maxsplit=1)[0].rstrip(".;:")
    if len(gist) > width:
        gist = gist[:width].rsplit(" ", 1)[0] + "…"
    return f"{tool['name']} ({gist})" if gist else tool["name"]


def catalog_text(tools: dict[str, ToolSpec], mcp_tools: list[dict],
                 scopes: list[dict] | None = None, workspace: str = "",
                 notes: dict[str, list[str]] | None = None,
                 observed: dict[str, str] | None = None,
                 unexplored: dict[str, list[str]] | None = None,
                 focus: set[str] | None = None) -> str:
    """A compact index of the live tool surface for the system prompt.

    Names only, grouped. The full descriptions and JSON schemas already travel in the
    request's `tools` payload, and repeating them here costs around a thousand tokens of
    a context window the agent needs for actual work — with no benefit, since the model
    reads both. What this adds is the one thing the payload does not convey: which server
    a tool belongs to, so the model can reason about capability in groups.
    """
    lines = [f"Built-in: {', '.join(tools)}"]
    # Where "the workspace" actually is. Every MCP server below states the directory it was
    # configured for, and the built-in tools stated none — so a question about "the file in
    # the workspace" sent the agent to the only path it had been given, which belongs to
    # some other server. It then searched there, found nothing, and reported the file
    # missing. A path is the one fact that settles it.
    if workspace:
        lines.append(f"    ↳ workspace_* tools read and write: {workspace}"
                     f" — this is what \"the workspace\" means, and it is not any MCP "
                     f"server's directory unless one is listed with the same path below.")
    # Keyed by slug, not name. Two servers of the same kind carry the same name — two
    # "SQLite", two "Filesystem" — and keying by it merged them into one entry whose scope
    # was whichever happened to win. The agent then read one database while believing it
    # was reading the other. The slug is what the tool names are built from, so it is also
    # what distinguishes them here.
    scope_by_slug = {s["slug"]: s for s in (scopes or []) if s.get("slug")}
    if mcp_tools:
        by_server: dict[str, tuple[str, list[dict]]] = {}
        for tool in mcp_tools:
            slug = tool.get("server_slug") or tool["server_name"]
            by_server.setdefault(slug, (tool["server_name"], []))[1].append(tool)
        # The source map. With twenty servers the list of tool *names* stops being enough:
        # "positions" is a job vacancy in HR and a holding in the trade store, "desk" is a
        # trading desk and a piece of furniture. Each tool keeps the first words of its own
        # description, which is what tells them apart — trimmed harder as the map grows.
        crowded = len(mcp_tools) > 60
        lines.append(f"Sources — {len(by_server)} connected. Choose by what a source holds, "
                     f"not by a word it shares with the question. Tools not offered this turn "
                     f"are one `find_tools('<source>')` away.")
        for slug, (name, tools_of) in by_server.items():
            server = f"{name} ({slug})" if slug and slug != name else name
            # Once the question has been routed, the sources it needs are described in full
            # and the others are named only: with eighteen servers, every source's notes on
            # every turn took 7k of a 16k local window — for sources the question never used.
            aside = focus is not None and slug not in focus and name not in focus
            if aside:
                lines.append(f"- {server}: {', '.join(_tool_label(t, 0) for t in tools_of[:30])}"
                             + (f" (+{len(tools_of) - 30} more)" if len(tools_of) > 30 else ""))
                continue
            described = bool((notes or {}).get(slug))
            width = 0 if (crowded and described) else (42 if crowded else 70)
            shown = ", ".join(_tool_label(t, width) for t in tools_of[:30])
            more = f" (+{len(tools_of) - 30} more)" if len(tools_of) > 30 else ""
            scope = scope_by_slug.get(slug)
            where = ""
            if scope:
                target = ", ".join(scope["paths"]) or scope["url"]
                # A fact, and only a fact. The failure this removes is the model passing
                # "." or a guessed root to a server that was configured with a real one.
                # How each server wants its paths spelled is its own schema's business,
                # and an instruction here overrides that to everyone's cost.
                where = f"\n    ↳ this server is configured for: {target}"
                # The single most useful fact about a server rooted where the built-in
                # tools write: it means a file the agent saves is a file this server can
                # open. Without it stated, the agent reads data it cannot load and reports
                # the two directories as an impasse — which is how a perfectly possible
                # analysis becomes "I could not load the CSV".
                if workspace and workspace in scope["paths"]:
                    where += (" — the same directory workspace_write writes to, so anything "
                              "you save there is immediately loadable here")
            # What someone wrote about this source in Admin, then what past calls showed —
            # declared first, observed second, each labelled as what it is.
            known = "".join(f"\n    ↳ {line}" for line in (notes or {}).get(slug, []))
            seen = (observed or {}).get(slug)
            if seen:
                known += f"\n    ↳ Observed returning: {seen}"
            blank = (unexplored or {}).get(slug)
            if blank and len(blank) < len(tools_of):
                known += f"\n    ↳ Not yet explored: {', '.join(blank[:12])}"
            lines.append(f"- {server}: {shown}{more}{where}{known}")
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
