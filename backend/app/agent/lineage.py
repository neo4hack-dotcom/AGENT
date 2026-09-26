"""Lineage: what an answer rests on, down to the query that fetched each number.

An answer cites its evidence as #refs. That is traceability for a reader who clicks; an
auditor needs the chain behind it written down: which source was asked, with which exact
query or arguments, when, what came back (how many rows, and a fingerprint of the bytes so
a later re-run can be compared), and which computation combined what. This module derives
that chain from the run's own blocks — deterministically, with no model involved — so it is
the same whether the answer was right or wrong, and it cannot be talked into anything.

The chain starts from the refs the answer cites and walks their dependencies: a computation
that read rows('#6') depends on #6, a chart drawn from data='#12' depends on #12, a batch
reads the source it batched. Exploration (schemas, notes, tool searches) is listed apart:
it shaped how the questions were asked, not what the answer says.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_CITED = re.compile(r"[\[【`]?#(\d{1,3})[\]】`]?")
_ROWS = re.compile(r"""\brows\(\s*['"](#\d{1,3}|chart:[\w-]+)['"]\s*\)""")
_EXPLORATION = re.compile(r"(list_tables|describe_table|get_schema|table_info|source_info|find_tools|"
                          r"note_source|workspace_list|^plan$|list_columns|list_allowed_directories)", re.I)
_SQL_KEYS = ("query", "sql", "statement")


def fingerprint(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()[:16]


def _chart_refs(blocks: list[dict]) -> dict[str, str]:
    """chart id → the ref of the call that drew its latest version."""
    out: dict[str, str] = {}
    for block in blocks:
        chart = block.get("chart")
        if chart and block.get("ref"):
            out[str(chart.get("id"))] = block["ref"]
    return out


_RESULT_FILE = re.compile(r"\.results/[\w.\-]+")


def _offloads(blocks: list[dict]) -> dict[str, str]:
    """Parked result file → the ref of the call whose result it is."""
    out: dict[str, str] = {}
    for block in blocks:
        path = block.get("offloaded")
        if path and block.get("ref"):
            out[str(path).split("/")[-1]] = block["ref"]
    return out


def depends_on(block: dict, charts: dict[str, str], offloads: dict[str, str] | None = None) -> list[str]:
    name = block.get("name") or ""
    args = block.get("args") or {}
    refs: list[str] = []
    # A result parked on disk and read back — by open() in code, a dataframe load, a
    # workspace read — is that call's result, whichever tool read it.
    for match in _RESULT_FILE.finditer(json.dumps(args, ensure_ascii=False, default=str)):
        ref = (offloads or {}).get(match.group(0).split("/")[-1])
        if ref:
            refs.append(ref)

    def take(value: Any) -> None:
        if isinstance(value, str):
            value = value.strip()
            if re.fullmatch(r"#\d{1,3}", value):
                refs.append(value)
            elif value.startswith("chart:") and value[6:] in charts:
                refs.append(charts[value[6:]])

    if name == "run_python":
        for match in _ROWS.finditer(str(args.get("code") or "")):
            take(match.group(1))
    elif name == "batch_call":
        take(args.get("rows_from"))
    elif name in ("profile_data",):
        take(args.get("source"))
        reference = args.get("reference")
        if isinstance(reference, dict):
            for value in reference.values():
                take(str(value).split(":")[0] if isinstance(value, str) else value)
        take(args.get("holidays"))
    elif name in ("chart", "export_data"):
        take(args.get("data"))
        take(args.get("source"))
        for sheet in args.get("sheets") or [] if isinstance(args.get("sheets"), list) else []:
            if isinstance(sheet, dict):
                take(sheet.get("source"))
    elif name == "create_report":
        sections = args.get("sections")
        if isinstance(sections, str):
            try:
                sections = json.loads(sections)
            except ValueError:
                sections = []
        for section in sections or []:
            if isinstance(section, dict):
                take(section.get("table"))
                if section.get("chart"):
                    take(f"chart:{section['chart']}")
    return list(dict.fromkeys(r for r in refs if r != block.get("ref")))


def _kind(block: dict) -> str:
    name = block.get("name") or ""
    if _EXPLORATION.search(name):
        return "exploration"
    if name in ("run_python", "profile_data"):
        return "computation"
    if name in ("chart", "export_data", "create_report"):
        return "output"
    if name == "batch_call":
        return "retrieval"
    if block.get("kind") == "mcp":
        return "retrieval"
    return "other"


def _operation(block: dict) -> str:
    args = block.get("args") or {}
    for key in _SQL_KEYS:
        if isinstance(args.get(key), str) and args[key].strip():
            return " ".join(args[key].split())[:1200]
    if block.get("name") == "run_python":
        return str(args.get("code") or "")[:2000]
    if block.get("name") == "batch_call":
        calls = args.get("calls") or []
        return f"{args.get('tool')} × {len(calls) if isinstance(calls, list) else '?'}: " \
               + json.dumps(calls[:3] if isinstance(calls, list) else calls, ensure_ascii=False, default=str)[:600]
    return json.dumps(args, ensure_ascii=False, default=str)[:800]


def _shape(block: dict) -> dict:
    from app.data.rows import SourceError, rows_from_text
    try:
        rows = rows_from_text(block.get("text") or "")
    except (SourceError, ValueError):
        rows = None
    if rows is None:
        return {}
    columns = list(rows[0].keys())[:20] if rows else []
    return {"rows": len(rows), "columns": columns}


def node(block: dict, charts: dict[str, str], offloads: dict[str, str] | None = None) -> dict:
    out = {"ref": block.get("ref", ""), "tool": block.get("name", ""),
           "source": block.get("server", ""), "kind": _kind(block),
           "ok": bool(block.get("ok")), "at": block.get("at"), "ms": block.get("ms", 0),
           "operation": _operation(block), "summary": str(block.get("summary") or "")[:240],
           "fingerprint": fingerprint(block.get("text") or ""),
           "depends_on": depends_on(block, charts, offloads)}
    out.update(_shape(block))
    if block.get("chart"):
        out["output"] = f"chart {block['chart'].get('id')} v{block['chart'].get('version', 1)}"
    if block.get("file"):
        out["output"] = f"file {block['file'].get('name')}"
    return out


def cited(answer: str) -> list[str]:
    return list(dict.fromkeys(f"#{m.group(1)}" for m in _CITED.finditer(answer or "")))


def build(blocks: list[dict], answer: str) -> dict:
    """The answer's lineage: the cited evidence, its dependencies, and the exploration apart."""
    tools = [b for b in blocks if b.get("type") == "tool" and b.get("ref")]
    by_ref = {b["ref"]: b for b in tools}
    charts = _chart_refs(tools)
    offloads = _offloads(tools)
    roots = [r for r in cited(answer) if r in by_ref]
    implicit = not roots
    if implicit:
        # Nothing cited: everything that fetched, computed or produced is what the answer
        # can have rested on.
        roots = [b["ref"] for b in tools if b.get("ok") and _kind(b) in ("retrieval", "computation", "output")]
    # Outputs the reader received (charts, files) are part of the answer even uncited.
    roots += [b["ref"] for b in tools if (b.get("chart") or b.get("file")) and b["ref"] not in roots]
    seen: list[str] = []
    inferred: set[str] = set()
    guessed: dict[str, list[str]] = {}
    stack = list(roots)
    while stack:
        ref = stack.pop(0)
        if ref in seen or ref not in by_ref:
            continue
        seen.append(ref)
        found = depends_on(by_ref[ref], charts, offloads)
        if not found and _kind(by_ref[ref]) in ("computation", "output"):
            # Inputs not traceable from the call itself: every retrieval before it is a
            # possible input, marked as inferred rather than passed off as known.
            position = tools.index(by_ref[ref])
            found = [b["ref"] for b in tools[:position] if b.get("ok") and _kind(b) == "retrieval"]
            inferred.update(found)
            guessed[ref] = found
        stack.extend(found)
    order = {b["ref"]: i for i, b in enumerate(tools)}
    nodes = [node(by_ref[r], charts, offloads) for r in sorted(seen, key=lambda r: order.get(r, 0))]
    for item in nodes:
        if item["ref"] in inferred:
            item["inferred"] = True
        if item["ref"] in guessed:
            item["depends_on"] = guessed[item["ref"]]
            item["inputs_inferred"] = True
    exploration = [{"ref": b["ref"], "tool": b["name"], "source": b.get("server", ""),
                    "summary": str(b.get("summary") or "")[:160]}
                   for b in tools if _kind(b) == "exploration"]
    failed = [{"ref": b["ref"], "tool": b["name"], "error": str(b.get("summary") or "")[:200]}
              for b in tools if not b.get("ok")]
    sources = sorted({n["source"] for n in nodes if n["kind"] == "retrieval" and n["source"]})
    return {"cited": cited(answer), "implicit": implicit, "nodes": nodes, "sources": sources,
            "exploration": exploration, "failed": failed, "calls": len(tools)}


def for_ref(blocks: list[dict], ref: str) -> list[dict]:
    """The chain behind one result — for the provenance sheet of an export."""
    tools = [b for b in blocks if b.get("type") == "tool" and b.get("ref")]
    by_ref = {b["ref"]: b for b in tools}
    charts = _chart_refs(tools)
    offloads = _offloads(tools)
    start = charts.get(ref[6:]) if ref.startswith("chart:") else ref
    chain, stack = [], [start]
    while stack:
        current = stack.pop(0)
        if not current or current in chain or current not in by_ref:
            continue
        chain.append(current)
        stack.extend(depends_on(by_ref[current], charts, offloads))
    return [node(by_ref[r], charts, offloads) for r in reversed(chain)]


def markdown(question: str, answer: str, message: dict, lineage: dict) -> str:
    """The audit trail of one answer, as a document someone can file."""
    from datetime import datetime
    when = datetime.fromtimestamp(message.get("created_at") or 0).isoformat(timespec="seconds")
    usage = message.get("usage") or {}
    lines = ["# Audit trail", "",
             f"- **Question:** {question.strip()}",
             f"- **Asked at:** {when}",
             f"- **Model:** {message.get('model') or '—'}",
             f"- **Status:** {message.get('status') or '—'} · {usage.get('tool_calls', 0)} tool calls · "
             f"{usage.get('llm_calls', 0)} model calls",
             f"- **Sources:** {', '.join(lineage.get('sources') or []) or '—'}", "",
             "## Answer", "", answer.strip() or "(no answer)", ""]
    method = message.get("method") or {}
    if method.get("text"):
        lines += ["## Method (explained)", "", method["text"].strip(), ""]
    lines += ["## Evidence the answer rests on", ""]
    for n in lineage.get("nodes") or []:
        lines.append(f"### {n['ref']} — {n['tool']} ({n['source'] or 'app'}, {n['kind']})")
        if n.get("at"):
            lines.append(f"- at {datetime.fromtimestamp(n['at']).isoformat(timespec='seconds')}, {n.get('ms', 0)} ms")
        if n.get("rows") is not None:
            lines.append(f"- {n['rows']} rows; columns: {', '.join(n.get('columns') or [])}")
        if n.get("depends_on"):
            lines.append(f"- uses {', '.join(n['depends_on'])}")
        if n.get("output"):
            lines.append(f"- produced {n['output']}")
        lines.append(f"- result fingerprint (sha256): `{n['fingerprint']}`")
        fence = "sql" if n["tool"].endswith(("read_query", "query")) else ("python" if n["tool"] == "run_python" else "json")
        lines += [f"```{fence}", n["operation"], "```", ""]
    if lineage.get("exploration"):
        lines += ["## Exploration (shaped the queries, not the figures)", ""]
        lines += [f"- {e['ref']} {e['tool']} ({e['source']}) — {e['summary']}" for e in lineage["exploration"]]
        lines.append("")
    if lineage.get("failed"):
        lines += ["## Calls that failed", ""]
        lines += [f"- {f['ref']} {f['tool']} — {f['error']}" for f in lineage["failed"]]
        lines.append("")
    checks = message.get("checks") or []
    if checks:
        lines += ["## Checks", ""]
        lines += [f"- {c.get('name')}: {c.get('result')}" + (f" — {c['detail']}" if c.get("detail") else "")
                  for c in checks]
        lines.append("")
    trust = message.get("trust") or {}
    if trust.get("injections"):
        lines += ["## Warnings", ""]
        lines += [f"- {i['tool']} returned instruction-like text ({', '.join(i['patterns'])}); reported, not obeyed."
                  for i in trust["injections"]]
    return "\n".join(lines)


_RAISED = [
    ("[No rows for", "Empty filtered result flagged"),
    ("[This aggregates", "Whole-table aggregate flagged"),
    ("[Several records match", "Ambiguous name flagged"),
    ("does not accept", "Ignored argument flagged"),
    ("also exists in", "Column shared across sources flagged"),
]


def notes_raised(blocks: list[dict]) -> list[dict]:
    """The runner's own warnings attached to results — each one a check that fired."""
    out = []
    for block in blocks:
        if block.get("type") != "tool":
            continue
        text = block.get("text") or ""
        for marker, name in _RAISED:
            if marker in text:
                out.append({"name": name, "result": "raised", "detail": f"{block.get('ref')} {block.get('name')}"})
        if block.get("injection"):
            out.append({"name": "Instruction-like content", "result": "reported, not obeyed",
                        "detail": f"{block.get('ref')} {block.get('name')}"})
    return out
