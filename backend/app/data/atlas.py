"""The atlas: what each tool has been seen to return, remembered across conversations.

With twenty servers connected, most of an agent's first calls in any question are
rediscovery: which tool gives prices, what its fields are called, that bond prices come in
percent of par, that the risk server only answers at month ends. The atlas keeps what those
calls taught — per tool, from real results — so the next question starts where the last
one finished.

It is a map, not a catalogue, and it says so. Three things keep a cache of past answers
from narrowing future ones:

- **Coverage is explicit.** Every server's tools are listed with how often each has been
  observed; a tool never called is shown as *unexplored*, not left out. "The atlas does not
  mention it" never reads as "the source does not have it".
- **Declared and observed stay separate.** What an administrator wrote about a source is
  stated as the source's description; what the atlas saw is stated as observation, with
  how many calls and how long ago.
- **It goes stale on change.** Each record carries a fingerprint of the tool's schema; when
  a server changes a tool, what was observed about the old one is marked for re-checking
  instead of being trusted.

Only structure is kept: field names, their kinds and units, a few short example values,
argument shapes that worked, and the gist of errors. Never free text from a result — a
cache that fed a sentence from a tool reply back into every future system prompt would be
a persistent injection channel, which is exactly what memory quarantine exists to prevent.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

MAX_COLUMNS = 24
MAX_EXAMPLES = 3
MAX_ARG_PATTERNS = 4
MAX_ERRORS = 3
MAX_QUESTIONS = 4
STALE_AFTER_S = 30 * 86400

_IDENT = re.compile(r"^[A-Za-z_][\w .%/()-]{0,47}$")
_SAFE_VALUE = re.compile(r"^[\w .,:/%+()€$£¥@&'’-]{1,32}$", re.UNICODE)


def schema_fingerprint(tool: dict) -> str:
    body = json.dumps({"d": tool.get("description") or "", "s": tool.get("input_schema") or {}},
                      sort_keys=True, default=str)
    return hashlib.sha1(body.encode()).hexdigest()[:12]


def _safe(value: Any) -> Any:
    """A value short and plain enough to be a fact, or None."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(value, 4) if isinstance(value, float) else value
    text = str(value).strip()
    return text if _SAFE_VALUE.match(text) else None


def _shape_of(text: str) -> dict:
    """The structure of one result: a table's columns, or an object's keys."""
    from app.data import chart_sense, rows as rows_lib

    try:
        rows = rows_lib.rows_from_text(text)
    except Exception:  # noqa: BLE001 - a partial preview refuses to be read as a table
        rows = None
    header: dict[str, Any] = {}
    body = (text or "").strip()
    if body[:1] == "{":
        try:
            value = json.loads(body)
        except ValueError:
            value = None
        if isinstance(value, dict):
            header = {k: v for k, v in value.items() if not isinstance(v, (list, dict))}
    if rows:
        kinds = chart_sense.infer(rows)
        columns = {}
        for name in list(rows[0].keys())[:MAX_COLUMNS]:
            if not isinstance(name, str) or not _IDENT.match(name):
                continue
            examples = []
            for row in rows[:50]:
                value = _safe(row.get(name))
                if value is not None and value not in examples:
                    examples.append(value)
                if len(examples) >= MAX_EXAMPLES:
                    break
            info = kinds.get(name, {})
            columns[name] = {"kind": info.get("kind", ""), "examples": examples}
            if info.get("distinct", 0) <= 12 and info.get("kind") == "category":
                columns[name]["values"] = sorted({str(v) for v in (r.get(name) for r in rows[:500])
                                                  if _safe(v) is not None})[:12]
        meta = {k: _safe(v) for k, v in header.items() if isinstance(k, str) and _IDENT.match(k)
                and k not in ("rows", "count") and k not in columns and _safe(v) is not None}
        return {"kind": "table", "columns": columns, "rows": len(rows), "meta": dict(list(meta.items())[:8])}
    if header:
        keys = {k: {"examples": [_safe(v)] if _safe(v) is not None else []}
                for k, v in list(header.items())[:MAX_COLUMNS] if isinstance(k, str) and _IDENT.match(k)}
        return {"kind": "object", "columns": keys}
    return {"kind": "text", "columns": {}, "chars": len(body)}


def _arg_pattern(arguments: dict) -> dict:
    out = {}
    for key, value in (arguments or {}).items():
        if not isinstance(key, str) or not _IDENT.match(key):
            continue
        safe = _safe(value) if not isinstance(value, (list, dict)) else None
        out[key] = safe if safe is not None else type(value).__name__
    return out


def _gist(error: str) -> str:
    text = " ".join((error or "").split())
    text = re.sub(r"<[^>]+>", "", text)
    return text[:140]


def _ago(ts: float | None) -> str:
    if not ts:
        return "never"
    seconds = time.time() - ts
    if seconds < 3600:
        return "within the hour"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    return f"{int(seconds // 86400)} d ago"


MAX_NOTES = 20


def _norm_note(text: str) -> str:
    return re.sub(r"[^\w]+", " ", (text or "").lower()).strip()


class Atlas:
    def __init__(self, store) -> None:
        self.store = store

    def _all(self) -> dict[str, dict]:
        return self.store.data.setdefault("atlas", {})

    def _notes(self) -> dict[str, list[dict]]:
        return self.store.data.setdefault("atlas_notes", {})

    # ---------------------------------------------------------------- notes
    # Interpretation, not structure: "get_dv01 takes a book id (RAT-EUR), not a desk name",
    # "bond prices are clean, in % of par". The agent proposes them — from a note it writes
    # or from a review of a run that had to correct itself — and a person confirms them.
    # Proposed notes are only ever shown inside a tool result, fenced like any other; only
    # confirmed ones reach the system prompt. A note that a hostile reply talked the model
    # into is then one click from deletion, never one conversation from being obeyed.
    def add_note(self, server_id: str, text: str, origin: str = "agent", question: str = "") -> dict | None:
        text = " ".join((text or "").split())[:280]
        if len(text) < 12:
            return None
        notes = self._notes().setdefault(server_id, [])
        key = _norm_note(text)
        for note in notes:
            if _norm_note(note["text"]) == key:
                note["seen"] = note.get("seen", 1) + 1
                note["last_seen"] = time.time()
                self.store.touch()
                return note
        note = {"id": hashlib.sha1(f"{server_id}{key}{time.time()}".encode()).hexdigest()[:10],
                "text": text, "origin": origin, "status": "proposed", "seen": 1,
                "created_at": time.time(), "last_seen": time.time(),
                "question": " ".join((question or "").split())[:160]}
        proposed = [n for n in notes if n["status"] == "proposed"]
        if len(proposed) >= MAX_NOTES:
            notes.remove(min(proposed, key=lambda n: (n.get("seen", 1), n["last_seen"])))
        notes.append(note)
        self.store.touch()
        return note

    def notes(self, server_id: str, status: str | None = None) -> list[dict]:
        return [n for n in self._notes().get(server_id, []) if status is None or n["status"] == status]

    def set_note(self, server_id: str, note_id: str, status: str) -> bool:
        notes = self._notes().get(server_id, [])
        for note in list(notes):
            if note["id"] == note_id:
                if status == "discarded":
                    notes.remove(note)
                else:
                    note["status"] = status
                self.store.touch()
                return True
        return False

    # ------------------------------------------------------------ recording
    def observe(self, tool: dict, arguments: dict, ok: bool, text: str, error: str = "",
                question: str = "") -> None:
        """Remember what one call to one tool taught."""
        key = tool["qualified_name"]
        now = time.time()
        record = self._all().setdefault(key, {
            "server_id": tool.get("server_id"), "server_slug": tool.get("server_slug"),
            "tool": tool.get("name"), "calls": 0, "ok": 0, "failed": 0, "first_seen": now,
            "args_ok": [], "errors": [], "questions": [], "shape": None})
        fingerprint = schema_fingerprint(tool)
        if record.get("schema") and record["schema"] != fingerprint:
            # The tool changed under us: what was learned about the old one is not evidence.
            record.update({"shape": None, "args_ok": [], "errors": [], "calls": 0, "ok": 0, "failed": 0})
        record["schema"] = fingerprint
        record["calls"] += 1
        record["last_seen"] = now
        if ok:
            record["ok"] += 1
            record["last_ok"] = now
            pattern = _arg_pattern(arguments)
            if pattern not in record["args_ok"]:
                record["args_ok"] = [pattern, *record["args_ok"]][:MAX_ARG_PATTERNS]
            shape = _shape_of(text)
            if shape["kind"] != "text" or not record.get("shape"):
                record["shape"] = _merge_shape(record.get("shape"), shape)
            asked = " ".join((question or "").split())[:140]
            if asked and asked not in record["questions"]:
                record["questions"] = [asked, *record["questions"]][:MAX_QUESTIONS]
        else:
            record["failed"] += 1
            gist = _gist(error or text)
            entry = {"args": _arg_pattern(arguments), "error": gist}
            if gist and all(e.get("error") != gist for e in record["errors"]):
                record["errors"] = [entry, *record["errors"]][:MAX_ERRORS]
        self.store.touch()

    def forget(self, server_id: str | None = None) -> int:
        """Clear observations. Confirmed notes stay: a person vouched for them."""
        records = self._all()
        doomed = [k for k, r in records.items() if server_id is None or r.get("server_id") == server_id]
        for key in doomed:
            records.pop(key, None)
        for sid, notes in self._notes().items():
            if server_id is None or sid == server_id:
                notes[:] = [n for n in notes if n["status"] == "confirmed"]
        self.store.touch()
        return len(doomed)

    # --------------------------------------------------------------- reading
    def records_for(self, server_id: str) -> dict[str, dict]:
        return {k: r for k, r in self._all().items() if r.get("server_id") == server_id}

    def coverage(self, server_id: str, tools: list[dict]) -> dict:
        """Which of a server's tools have been seen working, and which never have."""
        records = self.records_for(server_id)
        seen, stale, unexplored = [], [], []
        for tool in tools:
            record = records.get(tool["qualified_name"])
            if not record or not record.get("ok"):
                unexplored.append(tool["name"])
            elif record.get("schema") != schema_fingerprint(tool) or \
                    time.time() - (record.get("last_ok") or 0) > STALE_AFTER_S:
                stale.append(tool["name"])
            else:
                seen.append(tool["name"])
        return {"seen": seen, "stale": stale, "unexplored": unexplored, "total": len(tools)}

    def map_line(self, server_id: str, tools: list[dict], budget: int = 360) -> str:
        """One line of observed facts for the source map: the fields each tool returns."""
        records = self.records_for(server_id)
        parts = []
        for tool in tools:
            record = records.get(tool["qualified_name"])
            shape = (record or {}).get("shape") or {}
            columns = shape.get("columns") or {}
            if not record or not record.get("ok") or not columns:
                continue
            fields = []
            for name, info in list(columns.items())[:8]:
                label = name
                if info.get("values"):
                    label += "∈{" + ",".join(str(v) for v in info["values"][:4]) + ("…" if len(info["values"]) > 4 else "") + "}"
                fields.append(label)
            meta = shape.get("meta") or {}
            extra = f" [{', '.join(f'{k}={v}' for k, v in list(meta.items())[:2])}]" if meta else ""
            parts.append(f"{tool['name']}→{', '.join(fields)}{extra}")
        line = "; ".join(parts)
        return line if len(line) <= budget else line[: budget - 1] + "…"

    def full_text(self, server_id: str, tools: list[dict]) -> str:
        """Everything observed about one server's tools, for `source_info`."""
        records = self.records_for(server_id)
        cover = self.coverage(server_id, tools)
        out = [f"Observed by the agent across past conversations ({len(cover['seen'])} of "
               f"{cover['total']} tools seen working). This is what calls returned, not a "
               f"catalogue of what the source holds."]
        for tool in tools:
            record = records.get(tool["qualified_name"])
            if not record:
                continue
            lines = [f"## {tool['name']} — {record.get('ok', 0)} ok / {record.get('failed', 0)} failed, "
                     f"last {_ago(record.get('last_seen'))}"
                     + (" — tool changed since, re-check" if tool["name"] in cover["stale"] else "")]
            if record.get("args_ok"):
                lines.append("Worked with: " + " | ".join(json.dumps(a, ensure_ascii=False)
                                                           for a in record["args_ok"]))
            shape = record.get("shape") or {}
            if shape.get("columns"):
                fields = []
                for name, info in shape["columns"].items():
                    detail = info.get("kind") or ""
                    if info.get("values"):
                        detail += f" ∈ {info['values']}"
                    elif info.get("examples"):
                        detail += f" e.g. {', '.join(str(e) for e in info['examples'])}"
                    fields.append(f"{name} ({detail.strip()})" if detail.strip() else name)
                rows = f"{shape.get('rows')} rows; " if shape.get("rows") is not None else ""
                meta = shape.get("meta") or {}
                lines.append(f"Returns {rows}fields: " + "; ".join(fields)
                             + (f". Header: {json.dumps(meta, ensure_ascii=False)}" if meta else ""))
            for error in record.get("errors") or []:
                lines.append(f"Failed with {json.dumps(error['args'], ensure_ascii=False)}: {error['error']}")
            if record.get("questions"):
                lines.append("Used for: " + " · ".join(record["questions"]))
            out.append("\n".join(lines))
        if cover["unexplored"]:
            out.append("Never seen working (unexplored — they may hold what you need): "
                       + ", ".join(cover["unexplored"]))
        confirmed = self.notes(server_id, "confirmed")
        proposed = self.notes(server_id, "proposed")
        if confirmed:
            out.append("Confirmed notes:\n" + "\n".join(f"- {n['text']}" for n in confirmed))
        if proposed:
            out.append("Notes proposed from past work, not yet confirmed — check before relying on "
                       "them:\n" + "\n".join(f"- {n['text']}" for n in proposed[-10:]))
        return "\n\n".join(out)

    def summary(self, server_id: str, tools: list[dict]) -> dict:
        records = self.records_for(server_id)
        cover = self.coverage(server_id, tools)
        return {"coverage": cover, "notes": self.notes(server_id),
                "calls": sum(r.get("calls", 0) for r in records.values()),
                "last_seen": max((r.get("last_seen") or 0 for r in records.values()), default=None) or None,
                "tools": [{"name": t["name"], "calls": (records.get(t["qualified_name"]) or {}).get("calls", 0),
                           "ok": (records.get(t["qualified_name"]) or {}).get("ok", 0),
                           "failed": (records.get(t["qualified_name"]) or {}).get("failed", 0),
                           "fields": list(((records.get(t["qualified_name"]) or {}).get("shape") or {})
                                          .get("columns", {}).keys())[:12],
                           "last_seen": (records.get(t["qualified_name"]) or {}).get("last_seen")}
                          for t in tools]}

    def vocabulary(self, server_id: str) -> set[str]:
        """Words the server's results have been seen to carry: field names and category values."""
        words: set[str] = set()
        for record in self.records_for(server_id).values():
            for name, info in ((record.get("shape") or {}).get("columns") or {}).items():
                words.update(w.lower() for w in re.split(r"[_\W]+", name) if len(w) > 2)
                for value in info.get("values") or []:
                    words.update(w.lower() for w in re.split(r"[_\W]+", str(value)) if len(w) > 2)
            for asked in record.get("questions") or []:
                words.update(w.lower() for w in re.findall(r"\w{4,}", asked))
        return words


def _merge_shape(old: dict | None, new: dict) -> dict:
    """Keep the union of fields seen: one call's result is rarely the tool's whole shape."""
    if not old or old.get("kind") != new.get("kind"):
        return new
    columns = dict(old.get("columns") or {})
    for name, info in (new.get("columns") or {}).items():
        if name in columns:
            merged = dict(columns[name])
            merged["examples"] = list(dict.fromkeys([*(info.get("examples") or []),
                                                     *(merged.get("examples") or [])]))[:MAX_EXAMPLES]
            if info.get("values"):
                merged["values"] = sorted(set(merged.get("values") or []) | set(info["values"]))[:12]
            merged["kind"] = info.get("kind") or merged.get("kind", "")
            columns[name] = merged
        elif len(columns) < MAX_COLUMNS:
            columns[name] = info
    return {**new, "columns": columns, "meta": {**(old.get("meta") or {}), **(new.get("meta") or {})}}
