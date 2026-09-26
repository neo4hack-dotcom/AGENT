"""Draft the judgement half of a source's model, and keep only what runs.

The profiler measures facts. What it cannot measure is meaning: that `status = 'shipped'`
is what finance calls revenue, that "CA" and "sales" are the same thing, which questions
people actually ask of this data. A model can propose those from the profile and a few
sample rows — but a proposed metric is a guess until someone accepts it, and a proposed
query is a guess until it runs. So every query the model drafts is executed against the
source before it is offered as "verified", and the draft is returned for a person to read,
never saved behind their back.
"""

from __future__ import annotations

import json
import re

from app.data.knowledge import dump_model, merge_models
from app.data.profiler import Profiler, ProfileError, _q, _Runner

DRAFT_SYSTEM = """You document a database for an analytics agent. You are given the measured \
profile of one data source — tables, row counts, column types, the values each low-cardinality \
column takes, date ranges, joins and data-quality caveats — plus three sample rows per table.

Return ONE JSON object, nothing else, with these keys:

- "description": 2-4 sentences of plain prose: what this source holds, what questions it can \
answer, its time coverage, and what it cannot answer.
- "tables": [{"name", "description", "columns": [{"name", "description"}]}] — describe only \
columns whose meaning is not obvious from the name, and say units and currencies.
- "metrics": 2 to 5 business metrics, each {"name", "synonyms": [the everyday words and \
abbreviations an English or French analyst types for it — for revenue that means "revenue", \
"sales", "CA", "chiffre d'affaires"], "definition": the exact SQL expression with its filter, \
e.g. "SUM(orders.amount_eur) WHERE orders.status = 'shipped'", "description"}. Only metrics \
this data can actually compute. When a status or type column exists, the definition MUST say \
which values count. Before combining two tables in one metric, check the profile that the \
rows are not already excluded by a filter — subtracting refunds from revenue that never \
included the refunded orders counts them twice.
- "caveats": short sentences about what is easy to get wrong with this data that the \
profile's caveats do not already say (units, VAT, what a status means).
- "verified_queries": 4 to 6 {"question", "sql"} pairs — realistic analyst questions, each \
answered by ONE read-only SELECT in the dialect of this source, using only the tables and \
columns in the profile. They will be executed; ones that fail are discarded.

Use only names that appear in the profile. Invent nothing about the business beyond what the \
data shows. When a "data_catalog" section is given, it is the enterprise's own documentation: \
reuse its table and column definitions and its calculations, and say where the data disagrees."""


_WRITES = re.compile(r"\b(insert|update|delete|drop|alter|create|truncate|merge|grant|revoke|"
                     r"replace|attach|detach|pragma|vacuum|copy|call|exec|execute)\b", re.IGNORECASE)


async def draft(llm, registry, server: dict, current: dict, profile_model: dict,
                catalog_notes: str = "") -> dict:
    """A proposal: description, model YAML, and which drafted queries ran or failed."""
    tools = [t for t in registry.tools() if t["server_id"] == server["id"]]
    profiler = Profiler(registry)
    plan = profiler._detect(tools)
    if not plan["query"]:
        raise ProfileError("This source has no read-only query tool, so drafts cannot be checked.")
    runner = _Runner(registry, plan, {"queries": 0, "errors": []})
    if plan["needs_database"]:
        runner.database = await runner.first_database()

    samples = {}
    for table in (profile_model.get("tables") or [])[:12]:
        rows = await runner.query_or_none(f"SELECT * FROM {_q(table['name'])} LIMIT 3")
        if rows:
            samples[table["name"]] = rows
    brief = {"source": server["name"], "profile": profile_model, "samples": samples,
             "existing_description": current.get("description", "")}
    if catalog_notes:
        # The enterprise's own definitions come first: a metric the catalog defines is
        # drafted the catalog's way, not re-invented from column names.
        brief["data_catalog"] = catalog_notes
    result = await llm.chat(
        [{"role": "user", "content": json.dumps(brief, ensure_ascii=False, default=str)[:24000]}],
        system=DRAFT_SYSTEM, temperature=0.2)
    proposal = _json_object(result.content)
    if proposal is None:
        raise ProfileError("The model did not return a usable draft. Try again, or write the "
                           "description and metrics by hand.")

    kept, rejected = [], []
    for pair in proposal.get("verified_queries") or []:
        sql = str((pair or {}).get("sql") or "").strip().rstrip(";")
        question = str((pair or {}).get("question") or "").strip()
        if not sql or not question or not re.match(r"^\s*(select|with)\b", sql, re.I):
            continue
        # A proposed query is run against the real source. Postgres lets a WITH clause hold
        # a DELETE, so starting with WITH proves nothing: any statement word that writes is
        # refused outright. The source's account should be read-only anyway (DATA.md, step
        # 1); this is the second lock, not the first.
        if _WRITES.search(sql) or ";" in sql.strip().rstrip(";"):
            rejected.append({"question": question, "sql": sql,
                             "error": "refused: not a single read-only statement"})
            continue
        try:
            rows = await runner.query(sql)
        except ProfileError as exc:
            rejected.append({"question": question, "sql": sql, "error": str(exc)[:200]})
            continue
        if not rows:
            rejected.append({"question": question, "sql": sql, "error": "returned no rows"})
            continue
        kept.append({"question": question, "sql": " ".join(sql.split())})

    drafted = {
        "tables": [{"name": t.get("name"), **({"description": t["description"]} if t.get("description") else {}),
                    "columns": [c for c in (t.get("columns") or []) if isinstance(c, dict) and c.get("name")]}
                   for t in (proposal.get("tables") or []) if isinstance(t, dict) and t.get("name")],
        "metrics": [m for m in (proposal.get("metrics") or [])
                    if isinstance(m, dict) and m.get("name") and m.get("definition")],
        "caveats": _new_caveats(profile_model.get("caveats") or [], proposal.get("caveats") or []),
        "verified_queries": kept,
    }
    # Measured facts from the profile, meaning from the draft, anything a person already
    # wrote on top of both.
    base = _annotated(profile_model, drafted)
    base["metrics"] = drafted["metrics"]
    base["caveats"] = list(dict.fromkeys([*(profile_model.get("caveats") or []), *drafted["caveats"]]))
    base["verified_queries"] = kept
    final = merge_models(current.get("model") or {}, base) if current.get("model") else base
    for key in ("metrics", "verified_queries"):
        mine = (current.get("model") or {}).get(key) or []
        names = {(m.get("name") or m.get("question")) for m in mine}
        final[key] = [*mine, *[m for m in base.get(key, [])
                               if (m.get("name") or m.get("question")) not in names]]
    return {"description": current.get("description") or str(proposal.get("description") or "").strip(),
            "model_yaml": dump_model({k: v for k, v in final.items() if v}),
            "checked": len(kept), "rejected": rejected}


def _annotated(profile_model: dict, drafted: dict) -> dict:
    """Profile tables with the draft's descriptions laid over them."""
    notes = {t["name"]: t for t in drafted["tables"]}
    tables = []
    for table in profile_model.get("tables") or []:
        note = notes.get(table["name"], {})
        described = {c["name"]: c.get("description") for c in note.get("columns", [])
                     if c.get("description")}
        columns = [{**c, **({"description": described[c["name"]]} if c["name"] in described else {})}
                   for c in table.get("columns") or []]
        entry = {"name": table["name"]}
        if note.get("description"):
            entry["description"] = note["description"]
        tables.append({**entry, **{k: v for k, v in table.items() if k not in ("name", "columns")},
                       "columns": columns})
    out = {k: v for k, v in profile_model.items() if k != "tables"}
    out["tables"] = tables
    return out


_CONCEPTS = {"duplicate": r"duplicat|doublon", "negative": r"negativ|négati",
             "orphan": r"orphan|matches no|match no|no matching|orphelin",
             "empty": r"\bempty\b|\bnull\b|missing|\bvide\b|manquant"}


def _concepts(text: str) -> set[str]:
    lowered = text.lower()
    return {name for name, pattern in _CONCEPTS.items() if re.search(pattern, lowered)}


def _new_caveats(measured: list[str], drafted: list) -> list[str]:
    """Drop drafted caveats that restate a measured one in other words.

    The profiler already counted the duplicates, the negatives and the orphans, precisely;
    the model's paraphrase of the same finding is a second, vaguer copy. Matching is by
    concept rather than wording, because the wording is exactly what differs.
    """
    covered = set().union(*(_concepts(m) for m in measured)) if measured else set()
    kept = []
    for caveat in (str(c).strip() for c in drafted):
        if caveat and not (_concepts(caveat) & covered):
            kept.append(caveat)
    return kept


def _json_object(text: str) -> dict | None:
    body = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", body, re.S)
    if fenced:
        body = fenced.group(1)
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


# ------------------------------------------------------------ tool-based sources

TOOLS_DRAFT_SYSTEM = """You document a tool-based data service for an analytics agent at a \
bank. You get its tools (names, descriptions, parameters), what past calls were observed \
returning (fields, kinds, example values, errors) and the output of a few safe calls.

Write the functional description the agent will read before choosing and calling this \
service. Plain text, at most 220 words, in this order:
1. One or two sentences: what the service holds and what questions it answers.
2. One line per tool: what it is for, the parameters that matter with their accepted values \
or formats, and what it returns — with units, currencies and quoting conventions.
3. Conventions and pitfalls: identifiers it expects, dates it has or lacks (holidays, \
month-ends), how values are quoted, anything a call can silently get wrong.
4. What it cannot answer, and the keys that connect it to other sources (ISIN, book_id…).

State only what the tools, observations and outputs show. Where something is uncertain, \
say "appears to". Return only the description."""


async def draft_tools(llm, registry, atlas, server: dict, probe_timeout_s: float = 20.0,
                      catalog_notes: str = "") -> dict:
    """A functional description of a tool-based source, from its schemas, from what the atlas
    has observed, and from a few calls that cannot change anything."""
    import asyncio

    tools = [t for t in registry.tools() if t["server_id"] == server["id"]]
    if not tools:
        raise ProfileError("This source is not connected, so there is nothing to describe.")
    # Safe to call unprompted: read-only by the server's own declaration, and nothing to
    # fill in. Anything else could have an effect, or needs arguments only a question gives.
    probes = [t for t in tools if t.get("read_only") and not t.get("write")
              and not (t.get("input_schema") or {}).get("required")][:5]
    samples = []
    for tool in probes:
        try:
            result = await asyncio.wait_for(registry.call(tool["qualified_name"], {}), probe_timeout_s)
        except Exception as exc:  # noqa: BLE001 - a failed probe is simply not a sample
            result = {"ok": False, "error": str(exc)}
        text = str(result.get("text") or result.get("error") or "")
        atlas.observe(tool, {}, bool(result.get("ok")), text, str(result.get("error") or ""),
                      "(probed from Admin to draft this source's description)")
        samples.append(f"### {tool['name']}()\n{text[:1200]}")
    listing = []
    for tool in tools:
        params = (tool.get("input_schema") or {}).get("properties") or {}
        required = set((tool.get("input_schema") or {}).get("required") or [])
        shown = ", ".join(f"{name}{'*' if name in required else ''}"
                          + (f": {' '.join(str(spec.get('description', '')).split())[:60]}"
                             if isinstance(spec, dict) and spec.get("description") else "")
                          for name, spec in params.items())
        listing.append(f"- {tool['name']}({shown}) — {' '.join((tool.get('description') or '').split())[:300]}")
    observed = atlas.full_text(server["id"], tools)
    prompt = (f"# Service: {server['name']}\n\n## Tools (* = required)\n" + "\n".join(listing)
              + f"\n\n## Observed\n{observed}"
              + ("\n\n## Outputs of safe calls\n" + "\n\n".join(samples) if samples else "")
              + (f"\n\n## What the enterprise data catalog says\n{catalog_notes}" if catalog_notes else ""))
    result = await llm.chat([{"role": "user", "content": prompt[:24000]}], system=TOOLS_DRAFT_SYSTEM,
                            temperature=0.2, think=False)
    description = (result.content or "").strip()
    if not description:
        raise ProfileError("The model returned nothing. Try again, or write the description by hand.")
    return {"description": description[:4000], "probed": [t["name"] for t in probes],
            "observed_tools": len(atlas.records_for(server["id"]))}
