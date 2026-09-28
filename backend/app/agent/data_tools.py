"""The agent's analyst tools: draw a chart, ask the reader, hand over a file, write a report.

They share one rule, the one that separates an analysis from a transcription: data is
*named*, never retyped. `#4` is the result of call four in this conversation; `chart:c1`
is a chart's rows; anything else is a workspace file. The rows are fetched from where they
actually are, so what the reader sees in a chart, an extract or a PDF is exactly what the
query returned.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import time
import re
import uuid
from pathlib import Path
from typing import Any

from app.agent import builtin, trust
from app.data import chart_sense as sense, charts as chart_lib
from app.data import exports as export_lib
from app.data import report as report_lib
from app.data import rows as rows_lib

_REF_NUMBER = re.compile(r"#(\d{1,3})")
_ROWS_CALL = re.compile(r"""\brows\(\s*['"]((?:#\d{1,3})|(?:chart:[\w-]+)|(?:[^'"]+\.(?:csv|json|xlsx)))['"]\s*\)""")


_DEICTIC = re.compile(r"\b(ce|cet|le|ton|this|the|that|your)\s+(graph\w*|chart|diagramme|visuel|"
                      r"camembert|donut|histogramme)\b", re.IGNORECASE)
_LITERAL_ROWS = re.compile(r"\[\s*\{.{120,}?\}\s*,?\s*\]", re.DOTALL)
_NUMBER = re.compile(r"(?<![\w.])-?\d+\.\d{2}(?![\d])")


def _pasted_result(code: str, blocks: list[dict]) -> str:
    """The #ref whose rows this code has typed in as a literal, if any.

    Only a literal that matches an existing result counts: a list of constants the model
    genuinely wrote is left alone. The match is on decimal figures — three of them found
    together in one earlier result is not a coincidence.
    """
    for literal in _LITERAL_ROWS.findall(code):
        if literal.count("{") < 4:
            continue
        figures = list(dict.fromkeys(_NUMBER.findall(literal)))
        if len(figures) < 3:
            continue
        probe = figures[:6]
        for block in reversed(blocks):
            if block.get("type") != "tool" or not block.get("ok") or not block.get("ref"):
                continue
            if block.get("name") == "run_python":
                continue
            text = block.get("text") or ""
            if sum(1 for f in probe if f in text) >= min(3, len(probe)):
                return block["ref"]
    return ""


def pasted_result_in(arguments: dict, blocks: list[dict]) -> str:
    """The #ref whose rows appear retyped in any string argument of a call."""
    for value in (arguments or {}).values():
        text = value if isinstance(value, str) else (
            json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (list, dict)) else "")
        if len(text) > 120:
            ref = _pasted_result(text, blocks)
            if ref:
                return ref
    return ""


def _as_obj(value: Any) -> Any:
    """Models sometimes send a JSON object as a string. Accept both."""
    if isinstance(value, str) and value.strip()[:1] in "{[":
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


_CHART_KEYS = ("chart", "chart_id", "chart_ref", "figure", "graph", "graphique", "image")
_TABLE_KEYS = ("table", "table_ref", "table_id", "rows", "data", "source", "tableau")


def _section_aliases(section: dict) -> dict:
    """A report section in the words models actually use: `chart_id`, `figure`, `table_ref`,
    `rows` … — dropped silently, they left a report announcing "the chart below" over an
    empty page."""
    out = dict(section)
    if not out.get("chart"):
        for key in _CHART_KEYS[1:]:
            value = out.get(key)
            if isinstance(value, dict):
                value = value.get("id") or value.get("chart_id")
            if isinstance(value, str) and value.strip():
                out["chart"] = value.strip().removeprefix("chart:").strip()
                break
    if not out.get("table"):
        for key in _TABLE_KEYS[1:]:
            value = out.get(key)
            if isinstance(value, str) and re.fullmatch(r"\s*\[?(#\d+|chart:\S+)\]?\s*", value):
                out["table"] = value.strip().strip("[]")
                break
            if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
                out["table"] = value
                break
    return out


def register(tools: dict[str, builtin.ToolSpec], runner, ctx) -> None:
    c = runner.c
    workspace = c.workspace()
    store = chart_lib.ChartStore(workspace)

    def history() -> list[list[dict]]:
        conv = c.store.conversation(ctx.conversation_id) or {}
        return [m.get("blocks") or [] for m in reversed(conv.get("messages") or [])
                if m.get("role") == "assistant" and m.get("id") != ctx.message_id]

    def resolve(source: Any) -> tuple[list[dict], str]:
        ref = str(source).strip() if isinstance(source, str) else ""
        reason = getattr(ctx, "flagged_refs", {}).get(ref)
        if reason:
            raise rows_lib.SourceError(f"{ref} was flagged as wrong when it came back: {reason} Use the "
                                       f"result of the corrected query instead.")
        return rows_lib.resolve(source, blocks=ctx.blocks, history=history(), workspace=workspace,
                                charts=store, conversation_id=ctx.conversation_id)

    def relative(path: str) -> str:
        try:
            return str(Path(path).resolve().relative_to(workspace.resolve()))
        except ValueError:
            return path

    def question() -> str:
        conv = c.store.conversation(ctx.conversation_id) or {}
        return next((m.get("content", "") for m in reversed(conv.get("messages") or [])
                     if m.get("role") == "user"), "")

    async def meaning(spec: dict, rows: list[dict], title: str,
                      previous: dict | None) -> tuple[dict[str, dict], dict]:
        """What each charted column means — from the data, then from the local model.

        A revision keeps the reading it already had for the columns it still uses; the
        model is asked only about columns it has not seen, so "make it stacked" costs no
        second opinion.
        """
        inferred = sense.infer(rows)
        known = ((previous or {}).get("spec", {}).get("usermeta") or {}).get("fields") or {}
        used = chart_lib.fields_used(spec)
        fresh = [f for f in used if f in inferred and f not in known]
        reviewed: dict = {}
        if fresh:
            ctx.emit({"type": "status", "phase": "labelling"})
            reviewed = await sense.review(c.fast_llm, question(), spec, rows, inferred,
                                          used, title)
        fields = sense.merge(inferred, {**{k: v for k, v in known.items() if k in inferred},
                                        **(reviewed.get("fields") or {})})
        return fields, reviewed

    def _block_of(ref: str) -> dict | None:
        label = ref.strip()
        for scope in [ctx.blocks, *history()]:
            for block in scope:
                if block.get("type") == "tool" and block.get("ref") == label:
                    return block
        return None

    def _json_of(ref: str) -> Any:
        block = _block_of(ref)
        if not block or not block.get("ok"):
            return None
        text = (block.get("text") or "").strip()
        if text[:1] not in "[{":
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None

    def _tabular_refs() -> str:
        """The calls of this run that do hold rows — what a wrong #ref should have been."""
        found = []
        for block in ctx.blocks:
            if block.get("type") != "tool" or not block.get("ok") or block.get("name") in ("plan", "run_python"):
                continue
            try:
                rows, _ = resolve(block.get("ref", ""))
            except rows_lib.SourceError:
                continue
            if rows:
                found.append(f"{block['ref']} {block['name']} ({len(rows)} rows)")
        return ("Calls in this run that hold rows: " + "; ".join(found[-12:]) + ".") if found else ""

    def _row_refs_everywhere() -> list[str]:
        """Results with rows, this run and earlier answers — what a table can be made of."""
        found: list[str] = []
        for scope in [ctx.blocks, *history()]:
            for block in scope:
                if block.get("type") != "tool" or not block.get("ok") or block.get("name") == "plan":
                    continue
                ref = block.get("ref", "")
                if not ref or any(f.startswith(ref + " ") for f in found):
                    continue
                try:
                    rows, _ = resolve(ref)
                except rows_lib.SourceError:
                    continue
                if rows:
                    found.append(f"{ref} {block.get('name', '')} ({len(rows)} rows: {', '.join(list(rows[0])[:5])})")
        return found[-10:]

    # What asks for a table ("un rapport détaillé" does not), and what names the section a
    # table belongs under.
    _TABLE_WORDS = "tableau|table|chiffres|figures"
    _TABLE_HEADING = "tableau|table|chiffres|détail|detail|figures"

    _CHART_WORDS = "graph|chart|courbe|diagramme|histogramme|barres|camembert|visuali"

    def _attach_run_chart(asked: str, sections: list[dict], charts: dict[str, dict],
                          used: set[int]) -> str:
        """The chart the reader asked for, when this very run drew it: a report asked for
        "with the chart", written right after drawing one, means that one."""
        lowered = (asked or "").lower()
        if any(sec.get("chart") for sec in sections) or not re.search(rf"\b(?:{_CHART_WORDS})", lowered) \
                or re.search(rf"\b(?:sans|without|no)\s+(?:le |la |les |de |d'|any |a )?(?:{_CHART_WORDS})", lowered):
            return ""
        drawn = [b["chart"] for b in ctx.blocks if b.get("type") == "tool" and b.get("ok")
                 and isinstance(b.get("chart"), dict) and b["chart"].get("id")]
        if not drawn:
            return ""
        key = str(drawn[-1]["id"])
        chart = store.latest(ctx.conversation_id, key)
        if chart is None:
            return ""
        target = next((sec for sec in sections if not sec.get("table") and re.search(
            rf"\b(?:{_CHART_WORDS})", f"{sec.get('heading', '')} {sec.get('text', '')}".lower())), None)
        if target is None:
            target = {}
            sections.insert(1 if sections else 0, target)
        target["chart"] = key
        charts[key] = chart
        used.update(int(n) for n in _REF_NUMBER.findall(chart.get("source", "")))
        return (f" The chart drawn for this request ({key}) was placed under "
                f"'{target.get('heading') or 'its own section'}'.")

    def _attach_chart_table(asked: str, sections: list[dict], charts: dict[str, dict],
                            tables: dict[str, tuple[list[dict], str]], used: set[int]) -> str:
        """The table the reader asked for, when there is exactly one it can be: the rows
        behind the report's chart. A 4B model told twice to add {"table": "#1"} sent the
        same sections a third time; the chart's own data is not a guess."""
        lowered = (asked or "").lower()
        if not re.search(rf"\b(?:{_TABLE_WORDS})", lowered) or re.search(
                rf"\b(?:sans|without|no)\s+(?:le |la |les |de |d'|any |a )?(?:{_TABLE_WORDS})", lowered):
            return ""
        if any(sec.get("table") for sec in sections) or any(
                re.search(r"^\s*\|.+\|\s*$", str(sec.get("text") or ""), re.M) for sec in sections):
            return ""
        sources = {m for chart in charts.values() for m in _REF_NUMBER.findall(chart.get("source", ""))}
        if len(sources) != 1:
            return ""
        ref = f"#{next(iter(sources))}"
        try:
            rows, label = resolve(ref)
        except rows_lib.SourceError:
            return ""
        if not rows or len(rows) > 200:
            return ""
        target = next((sec for sec in sections if not sec.get("chart") and re.search(
            rf"\b(?:{_TABLE_HEADING})", f"{sec.get('heading', '')} {sec.get('text', '')}".lower())), None)
        if target is None:
            target = {"heading": "Détail" if re.search(r"[éèàùç]|\b(le|la|les|des)\b", lowered) else "Detail"}
            sections.append(target)
        target["table"] = ref
        tables[ref] = (rows, label)
        used.update(int(n) for n in _REF_NUMBER.findall(label))
        return (f" The table of figures the reader asked for was added under "
                f"'{target.get('heading') or 'the last section'}': {ref}, the data of the chart.")

    def _evidence_for_figures(charts: dict[str, dict], tables: dict[str, tuple[list[dict], str]]) -> list[float]:
        """Every number the conversation's results hold — and the reader's own questions."""
        from app.data import figures as figures_lib
        conv = c.store.conversation(ctx.conversation_id) or {}
        asked = [str(m.get("content") or "") for m in conv.get("messages") or [] if m.get("role") == "user"]
        texts = figures_lib.evidence_texts([ctx.blocks, *history()], workspace, asked)
        texts += [json.dumps(chart.get("data") or [], default=str) for chart in charts.values()]
        texts += [json.dumps(rows, default=str) for rows, _ in tables.values()]
        return figures_lib.evidence_numbers(texts)

    def _ungrounded_in_report(title: str, subtitle: str, sections: list[dict],
                              charts: dict[str, dict], tables: dict[str, tuple[list[dict], str]]) -> dict[str, list[str]]:
        from app.data import figures as figures_lib
        evidence = _evidence_for_figures(charts, tables)
        found: dict[str, list[str]] = {}
        for index, sec in enumerate([{"heading": "title", "text": f"{title}\n{subtitle}"}, *sections]):
            missing = figures_lib.ungrounded(f"{sec.get('heading') or ''}\n{sec.get('text') or ''}", evidence)
            if missing:
                found[str(index - 1)] = missing
        return found

    def _missing_in_report(asked: str, sections: list[dict]) -> str:
        lowered = (asked or "").lower()
        def wanted(words: str) -> bool:
            return bool(re.search(rf"\b(?:{words})", lowered)) and not re.search(
                rf"\b(?:sans|without|no)\s+(?:le |la |les |de |d'|any |a )?(?:{words})", lowered)
        has_chart = any(sec.get("chart") for sec in sections)
        has_table = any(sec.get("table") for sec in sections) or any(
            re.search(r"^\s*\|.+\|\s*$", str(sec.get("text") or ""), re.M) for sec in sections)
        problems = []
        if wanted(_CHART_WORDS) and not has_chart:
            existing = [ch["id"] for ch in store.all_latest(ctx.conversation_id)]
            problems.append("The reader asked for a chart and no section has one: add "
                            "{\"chart\": \"<id>\"} to a section — "
                            + (f"charts in this conversation: {', '.join(existing)}."
                               if existing else "none is drawn yet: draw it with `chart` first."))
        if wanted(_TABLE_WORDS) and not has_table:
            refs = _row_refs_everywhere()
            problems.append("The reader asked for the table of figures and no section has one: add "
                            "{\"table\": \"#N\", \"table_title\": \"…\"} naming the result that "
                            "holds the rows" + (f" — results with rows: {'; '.join(refs)}." if refs
                                                 else " (compute it first, e.g. with run_python)."))
        return " ".join(problems)

    def revision_target() -> dict | None:
        """The chart the reader is pointing at, when they plainly point at one.

        "Passe ce graphique en barres empilées" is a revision even when the model gives the
        new version a new name — and the Modify button's "Chart c1: …" names it outright.
        Without this, "this chart" produced a second chart beside the first, and the
        report and the version history lost track of which was which.
        """
        conv = c.store.conversation(ctx.conversation_id) or {}
        asked = next((m.get("content", "") for m in reversed(conv.get("messages") or [])
                      if m.get("role") == "user"), "")
        named = re.match(r"^\s*chart\s+([\w-]+)\s*:", asked, re.IGNORECASE)
        if named:
            return store.latest(ctx.conversation_id, named.group(1))
        if not _DEICTIC.search(asked):
            return None
        for scope in history():
            ids = list(dict.fromkeys(b["chart"]["id"] for b in scope if b.get("chart")))
            if ids:
                return store.latest(ctx.conversation_id, ids[-1]) if len(ids) == 1 else None
        return None

    # ------------------------------------------------------------------ chart
    async def h_chart(spec: Any = None, data: Any = None, title: str = "", subtitle: str = "",
                      chart_id: str = "", **_: Any) -> dict:
        raw_spec = spec
        spec = _as_obj(spec)
        defaulted = ""
        salvaged = False
        if isinstance(raw_spec, str) and raw_spec.strip()[:1] == "{" and not isinstance(spec, dict):
            # A complete object followed by debris ("Extra data") is the object: keep it.
            try:
                spec, _end = json.JSONDecoder().raw_decode(raw_spec.strip())
                salvaged = True
            except ValueError:
                spec = raw_spec
        if isinstance(raw_spec, str) and raw_spec.strip()[:1] == "{" and not isinstance(spec, dict):
            # Unparseable. With rows to draw, a plain chart from their shape beats another
            # model turn spent rewriting 1 500 characters of JSON — the run that failed this
            # way had the right monthly counts in hand and ran out of time rewriting the spec.
            try:
                json.loads(raw_spec)
                error = "unknown"
            except ValueError as exc:
                error = str(exc)
            fallback = None
            if data not in (None, "", []):
                try:
                    fallback = chart_lib.default_spec(resolve(_as_obj(data))[0])
                except rows_lib.SourceError:
                    fallback = None
            if fallback is None:
                return {"ok": False, "error": f"spec is not valid JSON ({error}). Send a short spec — "
                                              f"'mark' and 'encoding' are enough; the app adds "
                                              f"the theme, sizes, axes and title."}
            spec = fallback
            defaulted = (f" The spec sent was not valid JSON ({error}), so this is a default chart "
                         f"drawn from the rows' shape; revise it with a short spec if it is not the "
                         f"one the reader asked for.")
        _COMPOSED = ("mark", "layer", "facet", "repeat", "concat", "hconcat", "vconcat")
        if isinstance(spec, dict) and isinstance(spec.get("spec"), dict) \
                and not any(k in spec for k in _COMPOSED):
            # {"title": …, "spec": {mark, encoding}}: a wrapper, not a facet — unwrap it and
            # keep its title.
            title = title or (spec.get("title") if isinstance(spec.get("title"), str) else "")
            subtitle = subtitle or (spec.get("subtitle") if isinstance(spec.get("subtitle"), str) else "")
            spec = spec["spec"]
        data = _as_obj(data)
        chart_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(chart_id or "").strip())[:40]
        # A name the model picked for a new chart is simply its name. Only an existing id
        # means "revise": refusing an unknown one sent the model round in circles.
        previous = store.latest(ctx.conversation_id, chart_id) if chart_id else None
        if previous is None:
            meant = revision_target()
            if meant:
                chart_id, previous = meant["id"], meant
        if not isinstance(spec, dict):
            if previous and (title or subtitle):
                spec = {k: v for k, v in previous["spec"].items() if k not in ("data", "title")}
            else:
                return {"ok": False, "error": "spec must be a Vega-Lite object: at least 'mark' "
                                              "and 'encoding'. " + chart_lib.COOKBOOK}
        typed = False
        try:
            if data not in (None, "", []):
                rows, source = resolve(data)
                typed = isinstance(data, list)
            elif isinstance((spec.get("data") or {}).get("values"), list):
                rows, source, typed = spec["data"]["values"], "values written by the agent", True
            elif previous:
                rows, source = previous["data"], previous["source"]
            else:
                return {"ok": False, "error": "Say where the rows come from: data='#4' for the "
                                              "result of call #4, 'chart:c1', or a workspace "
                                              "file. Only values the reader typed may be passed "
                                              "as a JSON array of objects."}
        except rows_lib.SourceError as exc:
            return {"ok": False, "error": f"{exc} {_tabular_refs()}".strip()}
        if typed and len(rows) > 60:
            return {"ok": False, "error": f"{len(rows)} rows were typed into the call. Name them "
                                          f"instead — data='#N' for the call that returned "
                                          f"them — so the chart shows what the query returned."}
        if len(rows) > chart_lib.MAX_CHART_ROWS:
            return {"ok": False, "error": f"{len(rows):,} rows is too many to chart legibly. "
                                          f"Aggregate first (GROUP BY in the query, or an "
                                          f"'aggregate' transform) and chart the result."}
        columns = rows_lib.columns_of(rows)
        locale = str(c.get("chart_locale") or "fr-FR")
        async def draw(chart_spec: dict) -> dict:
            try:
                return await draw_checked(chart_spec)
            except chart_lib.ChartError:
                raise
            except (TypeError, KeyError, ValueError, AttributeError) as exc:
                # A spec shaped in a way the polishing did not expect is a bad spec, said as
                # one — never a Python traceback, and never the end of the chart.
                raise chart_lib.ChartError(f"The spec could not be read ({type(exc).__name__}: {exc}). "
                                           f"Send a short one: 'mark' and 'encoding'.") from exc

        async def draw_checked(chart_spec: dict) -> dict:
            # Fields first, on the model's own spec: a column that does not exist is
            # refused before anyone is asked what it means.
            chart_lib.validate_fields(chart_lib.sanitize(chart_spec), columns)
            fields, reviewed = await meaning(chart_spec, rows, title, previous)
            # The editor's title for a new chart; a revision keeps the one it has unless
            # the analyst gives another.
            if previous:
                final_title, final_subtitle = title or previous.get("title", ""), subtitle
            else:
                final_title = (reviewed.get("title") or "").strip()[:140] or title
                final_subtitle = subtitle or (reviewed.get("subtitle") or "").strip()[:160]
            full = chart_lib.assemble(chart_spec, rows, final_title, final_subtitle, fields, locale)
            chart_lib.label_codes(full, rows, getattr(ctx, "code_names", {}) or {})
            chart_lib.validate_fields(full, columns)
            problem = chart_lib.degenerate(full, rows, fields)
            if problem:
                raise chart_lib.ChartError(problem)
            await chart_lib.check_renders(full, locale)
            return full

        try:
            full = await draw(spec)
        except chart_lib.ChartError as exc:
            # A spec salvaged from broken JSON that still does not draw: the rows' own chart.
            fallback = chart_lib.default_spec(rows) if salvaged else None
            if fallback is None:
                return {"ok": False, "error": str(exc)}
            try:
                full = await draw(fallback)
            except chart_lib.ChartError:
                return {"ok": False, "error": str(exc)}
            reason = str(exc).splitlines()[0].split(" at ")[0][:200]
            defaulted = (f" The spec sent was broken ({reason}); this is a default chart drawn from "
                         f"the rows' shape — revise it with a short spec if needed.")
        chart_id = chart_id or store.next_id(ctx.conversation_id)
        view = store.save(ctx.conversation_id, chart_id, full, source)
        shape = chart_lib.describe(full, rows)
        revised = f" (version {view['version']})" if view["version"] > 1 else ""
        return {"ok": True,
                "summary": f"chart {chart_id}{revised}: {view['title']} — {shape}",
                "text": (f"Chart {chart_id}{revised} is drawn under your answer: {shape}, rows "
                         f"from {source}. Columns: {', '.join(columns)}. Do not restate its "
                         f"numbers; say in a sentence what it shows. To change it, call chart "
                         f"with chart_id='{chart_id}' and the full revised spec.{defaulted}"),
                "chart": {**view, "typed": typed}}

    # ---------------------------------------------------------------- ask_user
    async def h_ask(question: str = "", options: Any = None, allow_other: bool = True,
                    **_: Any) -> dict:
        options = _as_obj(options)
        choices = [str(o).strip() for o in (options or []) if str(o).strip()] \
            if isinstance(options, list) else []
        choices = list(dict.fromkeys(choices))[:5]
        if not question.strip():
            return {"ok": False, "error": "Say what you need to know."}
        if len(choices) == 1:
            return {"ok": False, "error": "One option is not a choice. Give 2 to 5, or none to "
                                          "let the reader type an answer."}
        call_id = f"ask_{uuid.uuid4().hex[:10]}"
        timeout = float(c.settings.approval_timeout_s)
        answer = await ctx.request_input(call_id, {"question": question.strip(), "options": choices,
                                                   "allow_other": bool(allow_other) or not choices},
                                         timeout)
        if ctx.cancelled:
            return {"ok": False, "error": "The run was stopped while waiting for an answer."}
        if answer is None:
            return {"ok": True, "summary": f"no answer to: {question[:80]}",
                    "text": (f"The reader did not answer within {int(timeout // 60)} minutes. "
                             f"Go ahead with the option that is most standard for this data, "
                             f"and state that assumption in the first line of your answer."),
                    "ask": {"question": question, "options": choices, "answer": None}}
        return {"ok": True, "summary": f"{question[:80]} → {answer[:60]}",
                "text": f"The reader answered: {answer}",
                "ask": {"question": question, "options": choices, "answer": answer}}

    # ------------------------------------------------------------ export_data
    async def h_export(source: Any = None, format: str = "xlsx", filename: str = "",
                       sheets: Any = None, title: str = "", **_: Any) -> dict:
        sheets = _as_obj(sheets)
        wanted: list[tuple[str, Any]] = []
        if isinstance(sheets, list) and sheets:
            for i, sheet in enumerate(sheets):
                if isinstance(sheet, dict):
                    wanted.append((str(sheet.get("name") or f"Sheet{i + 1}"),
                                   sheet.get("source") or sheet.get("data")))
        elif source not in (None, ""):
            wanted.append((title or "Data", _as_obj(source)))
        if not wanted:
            return {"ok": False, "error": "Name the rows to export: source='#4' (or 'chart:c1', "
                                          "or a workspace file), or sheets=[{source, name}]."}
        resolved = []
        try:
            for name, ref in wanted:
                rows, _label = resolve(ref)
                resolved.append((name, rows))
        except rows_lib.SourceError as exc:
            return {"ok": False, "error": f"{exc} {_tabular_refs()}".strip()}
        # Where every exported table came from, down to the query — written into the file
        # itself, so the extract still explains itself once it has left this app.
        from app.agent import lineage as lineage_lib
        everything = [*[b for scope in reversed(history()) for b in scope], *ctx.blocks]
        provenance = {"question": question(), "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "model": getattr(c.llm, "model", ""),
                      "tables": [{"sheet": name, "source": str(ref),
                                  "chain": lineage_lib.for_ref(everything, str(ref))
                                  if isinstance(ref, str) and (ref.startswith("#") or ref.startswith("chart:")) else []}
                                 for name, ref in wanted]}
        try:
            info = await asyncio.to_thread(export_lib.export, resolved, format,
                                           workspace / "exports", filename, title, provenance)
        except export_lib.ExportError as exc:
            return {"ok": False, "error": str(exc)}
        info["path"] = relative(info["path"])
        detail = f"{info['rows']:,} rows" + (f", sheets {', '.join(info['sheets'])}"
                                             if len(info.get("sheets") or []) > 1 else "")
        return {"ok": True, "summary": f"{info['name']} — {detail}",
                "text": (f"Saved {info['name']} ({detail}, {info['bytes']:,} bytes). A download "
                         f"card is shown under your answer; mention the file in one line."),
                "file": info}

    # ------------------------------------------------------------ profile_data
    async def h_profile(source: Any = None, reference: Any = None, holidays: Any = None,
                        key: str = "", by: str = "", **_: Any) -> dict:
        from app.data import profiling
        try:
            rows, label = resolve(_as_obj(source))
        except rows_lib.SourceError as exc:
            return {"ok": False, "error": f"{exc} {_tabular_refs()}".strip()}
        references: dict[str, list] = {}
        reference = _as_obj(reference)
        if isinstance(reference, dict):
            for column, target in reference.items():
                ref, _, ref_column = str(target).partition(":") if not str(target).startswith("chart:") else (str(target), "", "")
                try:
                    ref_rows, _ = resolve(ref)
                except rows_lib.SourceError as exc:
                    return {"ok": False, "error": f"reference for {column}: {exc}"}
                pick = ref_column or (column if ref_rows and column in ref_rows[0] else
                                      next(iter(ref_rows[0]), "") if ref_rows else "")
                references[column] = [r.get(pick) for r in ref_rows if r.get(pick) is not None]
        holiday_set: set[str] = set()
        holidays = _as_obj(holidays)
        if isinstance(holidays, list):
            holiday_set = {str(h)[:10] for h in holidays}
        elif isinstance(holidays, str) and holidays.strip():
            try:
                found, _ = resolve(holidays)
                for row in found:
                    holiday_set.update(str(v)[:10] for v in row.values() if re.match(r"^\d{4}-\d{2}-\d{2}", str(v)))
            except rows_lib.SourceError:
                value = _json_of(holidays)
                if isinstance(value, dict):
                    for item in value.values():
                        if isinstance(item, list):
                            holiday_set.update(str(v)[:10] for v in item if re.match(r"^\d{4}-\d{2}-\d{2}", str(v)))
        text = await asyncio.to_thread(profiling.profile, rows, key=key, by=by, reference=references,
                                       holidays=holiday_set or None, label=label)
        return {"ok": True, "summary": f"profiled {len(rows):,} rows from {label}", "text": text}

    tools["profile_data"] = builtin.ToolSpec(
        "profile_data",
        "Profile the rows of an earlier result for data quality and shape: nulls, distinct "
        "values, duplicates and repeated keys, range of every number and date, robust outliers "
        "(overall and within each instrument or group), dates on weekends or on given holidays, "
        "and values missing from reference data. The first step of any data-quality review, "
        "anomaly search or unfamiliar dataset.",
        {"type": "object",
         "properties": {
             "source": {"type": "string", "description": "'#N' — the rows to profile."},
             "reference": {"type": "object", "description": "Column → '#M' (or '#M:column') whose rows list the valid values, e.g. {\"counterparty_id\": \"#7\"}."},
             "holidays": {"description": "'#M' or a list of YYYY-MM-DD dates to flag."},
             "key": {"type": "string", "description": "The id column (guessed when omitted)."},
             "by": {"type": "string", "description": "Group column for outliers (instrument-like column guessed when omitted)."}},
         "required": ["source"]},
        h_profile, group="Data", capabilities=(trust.FS_READ,))

    # ---------------------------------------------------------- create_report
    def source_notes() -> dict[int, str]:
        notes: dict[int, str] = {}
        for scope in [ctx.blocks, *history()]:
            for block in scope:
                if block.get("type") != "tool" or not block.get("ref") or not block.get("ok"):
                    continue
                number = int(block["ref"].lstrip("#"))
                if number in notes:
                    continue
                args = block.get("args") or {}
                query = next((str(v) for k, v in args.items()
                              if k in ("query", "sql", "expression") and v), "")
                detail = f": {' '.join(query.split())[:220]}" if query else ""
                notes[number] = f"{block.get('name', '')}{detail} — {block.get('summary', '')[:120]}"
        return notes

    async def h_report(title: str = "", sections: Any = None, subtitle: str = "",
                       filename: str = "", language: str = "", **_: Any) -> dict:
        sections = _as_obj(sections)
        if not title.strip() or not isinstance(sections, list) or not sections:
            return {"ok": False, "error": "A report needs a title and a list of sections: "
                                          "[{heading, text, chart, table, table_title}]."}
        charts_needed: dict[str, dict] = {}
        tables: dict[str, tuple[list[dict], str]] = {}
        used: set[int] = set()
        clean = []
        try:
            for section in sections:
                if not isinstance(section, dict):
                    continue
                section = _section_aliases(section)
                entry = {k: section.get(k) for k in ("heading", "text", "chart", "table",
                                                     "table_title", "columns") if section.get(k)}
                if entry.get("chart"):
                    chart_key = str(entry["chart"]).removeprefix("chart:").strip()
                    chart = store.latest(ctx.conversation_id, chart_key)
                    if chart is None:
                        existing = [ch["id"] for ch in store.all_latest(ctx.conversation_id)]
                        return {"ok": False, "error": f"No chart '{chart_key}' to include. Charts "
                                                      f"in this conversation: "
                                                      f"{', '.join(existing) or 'none'} — draw it "
                                                      f"with the chart tool first."}
                    entry["chart"] = chart_key
                    charts_needed[chart_key] = chart
                    used.update(int(n) for n in _REF_NUMBER.findall(chart.get("source", "")))
                if entry.get("table"):
                    key = str(entry["table"])
                    rows, label = resolve(_as_obj(entry["table"]))
                    tables[key] = (rows, label)
                    entry["table"] = key
                    used.update(int(n) for n in _REF_NUMBER.findall(label))
                clean.append(entry)
        except rows_lib.SourceError as exc:
            return {"ok": False, "error": f"{exc} {_tabular_refs()}".strip()}
        # A report that promises "the chart below" and has none is worse than no report: the
        # reader was asked for a chart and a table, and gets headings. Checked against the
        # question, and refused with the exact fix — the ids and refs that exist.
        attached = _attach_run_chart(question(), clean, charts_needed, used)
        attached += _attach_chart_table(question(), clean, charts_needed, tables, used)
        missing = _missing_in_report(question(), clean)
        # Figures in the prose that no result contains: the one defect a reader cannot see
        # from the report itself, since the table next to them looks like their source.
        invented = _ungrounded_in_report(title, subtitle, clean, charts_needed, tables)
        if invented:
            where = "; ".join(f"{', '.join(v)} (in {'the title' if k == '-1' else repr(clean[int(k)].get('heading') or f'section {int(k) + 1}')})"
                              for k, v in invented.items())
            missing = (missing + " " if missing else "") + (
                f"These figures in the report's text are in no result of this conversation: {where}. "
                f"Take each figure from the rows (compute it with run_python if it is derived) "
                f"or remove it.")
        # Twice at most: a model that cannot produce the chart (no rows to draw) must still be
        # able to hand over the report, with the gap said rather than hidden.
        refusals = ctx.report_refusals
        if missing and refusals < 2:
            ctx.report_refusals = refusals + 1
            return {"ok": False, "error": missing}
        if missing:
            attached += f" Still missing, say so in the answer: {missing}"
            # Built anyway, as the reader asked — but an unverified figure is marked where it
            # stands, in the report's own language.
            from app.data import report as report_mod
            lang = str(language or "").lower()[:2] or report_mod.detect_language(
                title, subtitle, *[str(sec.get("text") or "") for sec in clean])
            for key, values in invented.items():
                if key == "-1":
                    continue
                sec = clean[int(key)]
                flag = (f"**À vérifier :** *chiffre(s) introuvable(s) dans les données — {', '.join(values)}.*"
                        if lang == "fr" else
                        f"**Check before use:** *figure(s) not found in the data — {', '.join(values)}.*")
                sec["text"] = f"{sec.get('text') or ''}\n\n{flag}".strip()
        notes = source_notes()
        path = export_lib.unique_path(workspace / "reports",
                                      export_lib.safe_name(filename or title, "pdf"))
        from app.agent import lineage as lineage_lib
        everything = [*[b for scope in reversed(history()) for b in scope], *ctx.blocks]
        provenance: list[dict] = []
        for number in sorted(used):
            for node in lineage_lib.for_ref(everything, f"#{number}"):
                if node["ref"] not in {n["ref"] for n in provenance}:
                    provenance.append(node)
        try:
            info = await asyncio.to_thread(
                report_lib.build, path, title=title.strip(), subtitle=subtitle.strip(),
                sections=clean, sources=notes, used=used, charts=charts_needed, tables=tables,
                generated=dt.datetime.now(), provenance=provenance,
                language=str(language or "").lower()[:2])
        except Exception as exc:  # noqa: BLE001 - a layout failure must reach the model, not kill the run
            return {"ok": False, "error": f"The PDF could not be built: {type(exc).__name__}: {exc}"}
        info["path"] = relative(info["path"])
        return {"ok": True, "summary": f"{info['name']} — {info['pages']} page(s)",
                "text": (f"Saved {info['name']}: {info['pages']} page(s), {len(charts_needed)} "
                         f"chart(s), {len(tables)} table(s).{attached} A download card is shown under your "
                         f"answer — it is the link: write no path or URL to the file. In the answer itself, give the key finding in one or two "
                         f"sentences with its figures, then point to the report — the reader "
                         f"should not have to open the PDF to learn the headline."),
                "file": info}

    # ------------------------------------------------------- rows() in run_python
    python = tools.get("run_python")
    if python is not None:
        original = python.handler

        async def h_run_python(code: str = "", **kwargs: Any) -> dict:
            """Give the program the real rows of earlier calls, by name.

            Joining two servers means combining two results, and the only way the model had
            was to paste one of them into the code — the exact retyping this module exists
            to prevent. `rows("#3")` in the code is resolved here, before it runs, to the
            full result on disk; the program reads the data, the model never re-types it.
            """
            pasted = _pasted_result(code or "", [*ctx.blocks, *[b for scope in history() for b in scope]])
            if pasted:
                return {"ok": False,
                        "error": f"This code pastes the rows returned by call {pasted}. Use "
                                 f"rows('{pasted}') instead — rows typed into code are how rows "
                                 f"get lost, reordered or quietly changed. Same code otherwise."}
            refs = list(dict.fromkeys(m.group(1) for m in _ROWS_CALL.finditer(code or "")))
            setup = ""
            if refs:
                folder = workspace / ".results" / "rows"
                folder.mkdir(parents=True, exist_ok=True)
                files: dict[str, str] = {}
                for ref in refs:
                    try:
                        data, _label = resolve(ref)
                    except rows_lib.SourceError as exc:
                        # Not a table, but perhaps an object — a rating scale, a curve's header:
                        # the program gets the parsed JSON rather than an error to argue with.
                        data = _json_of(ref)
                        if data is None:
                            return {"ok": False, "error": f"rows({ref!r}): {exc} {_tabular_refs()}"}
                    path = folder / f"{ctx.run_id}-{re.sub(r'[^A-Za-z0-9]+', '_', ref)}.json"
                    path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
                    files[ref] = str(path)
                setup = (
                    "import json as _rows_json\n"
                    f"_ROWS_FILES = {json.dumps(files)}\n"
                    "def rows(ref):\n"
                    "    if ref not in _ROWS_FILES:\n"
                    "        raise KeyError(f'rows({ref!r}) was not loaded: write the reference as a literal string, e.g. rows(\"#3\")')\n"
                    "    with open(_ROWS_FILES[ref], encoding='utf-8') as _handle:\n"
                    "        return _rows_json.load(_handle)\n")
            return await original(code=code, _setup=setup, **kwargs)

        python.handler = h_run_python
        # Right after the first sentence, not at the end: under schema compression only the
        # first ~600 characters of a description survive, and this is the part that matters
        # most when combining sources.
        head, _, rest = python.description.partition(". ")
        python.description = (
            f"{head}. rows('#4') inside the code returns the full rows of call #4 as a list of "
            "dicts — or, for a result that is not a table, its parsed JSON object (also "
            "rows('chart:c1'), rows('file.csv')) — combine results from different "
            "servers with it; never paste data into the code. To chart or export what you "
            "computed, print JSON rows (print(df.to_json(orient='records'))) and use this "
            f"call's #ref. {rest}")

    # -------------------------------------------------------------- the specs
    tools["chart"] = builtin.ToolSpec(
        "chart",
        "Draw a chart for the reader from rows that already exist. `data` names the rows — "
        "'#4' for the result of call #4 (any call in this conversation), 'chart:c1' for another "
        "chart's rows, or a workspace file — never paste rows you could name. If you computed the "
        "rows yourself (run_python, pandas), have that call print them as JSON or CSV, then chart "
        "its #ref. Values the reader typed in the question have no call to name: pass them "
        "directly as a JSON array of objects in `data`. `spec` is a Vega-Lite spec without data. To change a chart the reader asked "
        "about, pass its chart_id with the complete revised spec (omit data to keep its rows); "
        "every version is kept. The chart appears under your answer.\n" + chart_lib.COOKBOOK,
        {"type": "object",
         "properties": {
             "data": {"description": "Where the rows come from: '#4', 'chart:c1', a workspace file path — or, only for values the reader typed, the rows as a JSON array of objects."},
             "spec": {"type": "object", "description": "Vega-Lite spec without data: mark, encoding, and optionally transform, layer, facet, resolve."},
             "title": {"type": "string", "description": "What the chart shows, as a reader would say it."},
             "subtitle": {"type": "string", "description": "Scope and units, e.g. 'H1 2026, EUR, VAT included'."},
             "chart_id": {"type": "string", "description": "Only to revise an existing chart, e.g. 'c1'."}},
         "required": ["spec", "title"]},
        h_chart, group="Data", capabilities=(trust.FS_READ,))

    tools["ask_user"] = builtin.ToolSpec(
        "ask_user",
        "Ask the reader one question and wait for the answer. Only when the request is "
        "ambiguous in a way that changes the result AND nothing settles it — no defined metric, "
        "no earlier answer, no query that could find out. Typical: which of two definitions they "
        "mean, which period, which source to trust when two disagree. Ask before doing the work "
        "it affects, at most once or twice per request; every question costs the reader a round "
        "trip. Give 2-4 short, mutually exclusive options.",
        {"type": "object",
         "properties": {
             "question": {"type": "string", "description": "One clear question, in the reader's language."},
             "options": {"type": "array", "items": {"type": "string"}, "description": "2 to 4 short choices."},
             "allow_other": {"type": "boolean", "description": "Let the reader type another answer (default true)."}},
         "required": ["question", "options"]},
        h_ask, group="Data")

    tools["export_data"] = builtin.ToolSpec(
        "export_data",
        "Give the reader a file of rows: an Excel workbook (xlsx, formatted, filterable), a CSV "
        "or JSON. `source` names the rows like chart's data ('#4', 'chart:c1', a workspace "
        "file). For several tables in one workbook pass `sheets` as [{source, name}]. Use it "
        "when the reader asks for an extract, the data, a file, or Excel — not to show numbers "
        "that belong in the answer.",
        {"type": "object",
         "properties": {
             "source": {"type": "string", "description": "'#4', 'chart:c1' or a workspace file."},
             "format": {"type": "string", "enum": ["xlsx", "csv", "json"]},
             "filename": {"type": "string", "description": "A short descriptive name, without folder."},
             "sheets": {"type": "array", "items": {"type": "object"}, "description": "For a multi-sheet workbook: [{\"source\": \"#4\", \"name\": \"By region\"}]."},
             "title": {"type": "string", "description": "What the file contains."}},
         "required": ["format"]},
        h_export, group="Data", capabilities=(trust.FS_READ,))

    tools["create_report"] = builtin.ToolSpec(
        "create_report",
        "Write a PDF report the reader can keep or forward, once the analysis is done. Sections "
        "run in order; each may have a heading, markdown text, a chart (by chart id, drawn "
        "beforehand with the chart tool) and a table (rows by reference, like '#4'). Lead with "
        "the findings, cite figures with their [#N] labels — they become numbered sources at the "
        "end of the PDF. Draw the charts first, then call this once.",
        {"type": "object",
         "properties": {
             "title": {"type": "string"},
             "subtitle": {"type": "string", "description": "Scope: sources, period, units."},
             "sections": {"type": "array", "items": {"type": "object"},
                          "description": "[{\"heading\": \"Key findings\", \"text\": \"markdown\"}, {\"heading\": \"Trend\", \"chart\": \"c1\"}, {\"heading\": \"Detail\", \"table\": \"#4\", \"table_title\": \"...\"}]"},
             "filename": {"type": "string"},
             "language": {"type": "string", "enum": ["fr", "en"],
                          "description": "The report's own words (page numbers, appendix, dates). Omit: read from its text."}},
         "required": ["title", "sections"]},
        h_report, group="Data", capabilities=(trust.FS_READ,))
