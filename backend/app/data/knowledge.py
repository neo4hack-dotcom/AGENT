"""What each connected source holds, written down once.

A tool schema says how to call a server. It says nothing about what is inside: that
`status` takes four values and only one of them is revenue, that the data stops on 1 July,
that `amount_eur` includes VAT, that six orders are duplicated. An agent finds those out
the slow way — five describe calls per question — or does not find them out at all, and
answers with a number that is arithmetically perfect and means the wrong thing.

So every source can carry two things, both optional, both edited in Admin:

- a **description** — plain prose: what this source is, what it is good for, what it is
  not. The one thing the user asked for by name.
- a **model** — YAML, in the shape Snowflake's semantic models and Databricks Genie's
  instructions converged on: tables and the facts about their columns, the joins between
  them, metrics defined once with their exact formula, caveats, and questions whose SQL
  someone has already checked.

The profiler drafts the structural half from the data itself; a person — or the model,
on request — writes the half that needs judgement. The agent then gets all of it in the
system prompt, where it costs a few hundred tokens once instead of five calls every time.
"""

from __future__ import annotations

import re
import time
from typing import Any

import yaml

# How much of one source's model rides in every system prompt. Past this the catalogue
# carries a summary and `source_info` hands over the rest in one call — still one call
# instead of a describe per table.
PROMPT_BUDGET = 2600

_WORD = re.compile(r"[\w']+", re.UNICODE)
_STOP = {"the", "and", "for", "with", "from", "that", "this", "what", "which", "how", "are",
         "les", "des", "une", "pour", "dans", "avec", "que", "qui", "sur", "est", "par",
         "quel", "quels", "quelle", "quelles", "donne", "moi", "fais", "tout", "tous"}

EXAMPLE_YAML = """\
# Everything here is optional. Run "Profile" to have the structure filled in from the data.
tables:
  - name: orders
    description: One row per customer order.
    columns:
      - name: status
        description: Lifecycle state. Only 'shipped' is revenue.
        values: [shipped, pending, refunded, cancelled]
      - name: amount_eur
        description: Order value in euros, VAT included.
joins:
  - orders.customer_id = customers.id
metrics:
  - name: revenue
    synonyms: [CA, chiffre d'affaires, sales]
    definition: SUM(orders.amount_eur) WHERE orders.status = 'shipped'
    description: Revenue recognised on shipped orders, VAT included.
caveats:
  - amount_eur includes VAT; divide by (1 + rate) for net figures.
verified_queries:
  - question: Revenue by region
    sql: >-
      SELECT c.region, SUM(o.amount_eur) AS revenue FROM orders o
      JOIN customers c ON c.id = o.customer_id WHERE o.status = 'shipped' GROUP BY c.region
"""


def _terms(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text or "")
            if len(w) > 2 and w.lower() not in _STOP}


def _clean(text: Any, limit: int = 600) -> str:
    return " ".join(str(text or "").split())[:limit]


# ------------------------------------------------------------------ validation

def parse_model(text: str) -> tuple[dict, list[str]]:
    """Read the YAML a person typed, and say precisely what is wrong with it.

    Lenient where leniency is harmless — a missing section is just absent, a scalar where
    a list was expected becomes a one-item list — and strict where a silent fix would
    change meaning: a metric without a definition is refused rather than kept, because a
    metric the agent cannot compute is worse than no metric.
    """
    errors: list[str] = []
    if not (text or "").strip():
        return {}, []
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        return {}, [f"Not valid YAML{where}: {getattr(exc, 'problem', None) or exc}"]
    if raw is None:
        return {}, []
    if not isinstance(raw, dict):
        return {}, ["The model must be a mapping with keys such as tables, metrics, caveats."]

    def as_list(key: str) -> list:
        value = raw.get(key)
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    model: dict[str, Any] = {}

    tables = []
    for i, table in enumerate(as_list("tables")):
        if not isinstance(table, dict) or not table.get("name"):
            errors.append(f"tables[{i}] needs a name.")
            continue
        columns = []
        for j, column in enumerate(table.get("columns") or []):
            if isinstance(column, str):
                column = {"name": column}
            if not isinstance(column, dict) or not column.get("name"):
                errors.append(f"tables[{i}].columns[{j}] needs a name.")
                continue
            entry = {"name": str(column["name"])}
            for key in ("type", "description", "unit", "references"):
                if column.get(key) not in (None, ""):
                    entry[key] = _clean(column[key], 300)
            if isinstance(column.get("values"), list):
                entry["values"] = [str(v) for v in column["values"]][:40]
            if isinstance(column.get("range"), list) and len(column["range"]) == 2:
                entry["range"] = [str(column["range"][0]), str(column["range"][1])]
            for key in ("nulls", "distinct"):
                if isinstance(column.get(key), (int, float)):
                    entry[key] = column[key]
            columns.append(entry)
        item = {"name": str(table["name"]), "columns": columns}
        for key in ("description", "grain"):
            if table.get(key):
                item[key] = _clean(table[key], 400)
        if isinstance(table.get("rows"), (int, float)):
            item["rows"] = int(table["rows"])
        tables.append(item)
    if tables:
        model["tables"] = tables

    joins = [_clean(j, 200) for j in as_list("joins") if str(j or "").strip()]
    if joins:
        model["joins"] = joins

    metrics = []
    for i, metric in enumerate(as_list("metrics")):
        if not isinstance(metric, dict) or not metric.get("name"):
            errors.append(f"metrics[{i}] needs a name.")
            continue
        definition = metric.get("definition") or metric.get("sql") or metric.get("formula")
        if not definition:
            errors.append(f"metric '{metric['name']}' has no definition — say exactly how it "
                          f"is computed, e.g. SUM(orders.amount) WHERE orders.status = 'paid'.")
            continue
        synonyms = metric.get("synonyms") or []
        if isinstance(synonyms, str):
            synonyms = [s.strip() for s in synonyms.split(",")]
        entry = {"name": str(metric["name"]), "definition": _clean(definition, 500),
                 "synonyms": [str(s) for s in synonyms if str(s).strip()][:12]}
        if metric.get("description"):
            entry["description"] = _clean(metric["description"], 300)
        metrics.append(entry)
    if metrics:
        model["metrics"] = metrics

    caveats = [_clean(c, 300) for c in as_list("caveats") if str(c or "").strip()]
    if caveats:
        model["caveats"] = caveats

    verified = []
    for i, pair in enumerate(as_list("verified_queries")):
        if not isinstance(pair, dict) or not pair.get("question") or not pair.get("sql"):
            errors.append(f"verified_queries[{i}] needs both a question and its sql.")
            continue
        verified.append({"question": _clean(pair["question"], 300),
                         "sql": " ".join(str(pair["sql"]).split())[:2000]})
    if verified:
        model["verified_queries"] = verified

    known = {"tables", "joins", "metrics", "caveats", "verified_queries"}
    for key in raw:
        if key not in known:
            errors.append(f"Unknown section '{key}' was ignored. Known sections: "
                          f"{', '.join(sorted(known))}.")
    return model, errors


class _Dumper(yaml.SafeDumper):
    """Mappings in block style, short lists of scalars inline — the shape people write."""


def _represent_list(dumper, data):
    inline = all(not isinstance(v, (dict, list)) for v in data) and len(str(data)) < 90
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=inline)


def _represent_dict(dumper, data):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items(), flow_style=False)


_Dumper.add_representer(list, _represent_list)
_Dumper.add_representer(dict, _represent_dict)


_ORDER = ("metrics", "caveats", "verified_queries", "joins", "tables")


def dump_model(model: dict) -> str:
    """The model back as YAML a person can read and edit.

    Judgement first, measurement last: metrics, caveats and checked questions are what a
    person writes and revisits; the table facts are long, measured, and rarely touched —
    at the top they pushed everything editable below the fold.
    """
    if not model:
        return ""
    ordered = {key: model[key] for key in _ORDER if model.get(key)}
    ordered.update({k: v for k, v in model.items() if k not in ordered and v})
    return yaml.dump(ordered, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)


# --------------------------------------------------------------------- storage

class Knowledge:
    def __init__(self, store) -> None:
        self.store = store

    def _all(self) -> dict[str, dict]:
        return self.store.data.setdefault("knowledge", {})

    def get(self, server_id: str) -> dict:
        record = self._all().get(server_id) or {}
        return {"description": record.get("description", ""),
                "model": record.get("model") or {},
                "model_yaml": record.get("model_yaml", ""),
                "profile": record.get("profile") or {},
                "profiled_at": record.get("profiled_at"),
                "updated_at": record.get("updated_at")}

    def update(self, server_id: str, *, description: str | None = None,
               model_yaml: str | None = None) -> tuple[dict, list[str]]:
        record = self._all().setdefault(server_id, {})
        errors: list[str] = []
        if description is not None:
            record["description"] = description.strip()[:4000]
        if model_yaml is not None:
            model, errors = parse_model(model_yaml)
            # A model with errors is still saved as typed — losing someone's half-finished
            # edit to a validation message is worse than keeping it — but only the parts
            # that validated reach the agent.
            record["model_yaml"] = model_yaml
            record["model"] = model
        record["updated_at"] = time.time()
        self.store.touch()
        return self.get(server_id), errors

    def set_profile(self, server_id: str, profile: dict, model: dict) -> dict:
        """Store what the profiler found and merge it into the model without clobbering.

        Anything a person wrote — a description, a metric, a caveat — wins over what the
        profiler drafted. The profiler owns facts it measured: row counts, values, ranges.
        """
        record = self._all().setdefault(server_id, {})
        current = dict(record.get("model") or {})
        # Measured caveats are replaced, not accumulated: once the duplicates are fixed at
        # the source, "6 rows are duplicated" must stop being said. Only what the profiler
        # wrote last time is dropped; anything a person wrote stays.
        stale = set((record.get("profile") or {}).get("measured_caveats") or [])
        current["caveats"] = [c for c in current.get("caveats") or [] if c not in stale]
        measured = set(t["name"] for t in model.get("tables") or [])
        current["tables"] = [t for t in current.get("tables") or []
                             if t["name"] in measured
                             or (t.get("description") and t["description"] != "View.")]
        merged = merge_models({k: v for k, v in current.items() if v}, model)
        profile = {**profile, "measured_caveats": list(model.get("caveats") or [])}
        record["profile"] = profile
        record["model"] = merged
        record["model_yaml"] = dump_model(merged)
        record["profiled_at"] = record["updated_at"] = time.time()
        self.store.touch()
        return self.get(server_id)

    def forget(self, server_id: str) -> None:
        self._all().pop(server_id, None)
        self.store.touch()

    # ---------------------------------------------------------------- readiness
    def readiness(self, server_id: str, queryable: bool = True) -> dict:
        """How ready a source is for questions, as a short checklist.

        Not a grade for its own sake: each line is a specific failure it prevents. No
        description, and the agent picks the wrong source. No profile, and it spends its
        first calls rediscovering columns. No metric, and "revenue" means whatever the
        model decides. No verified question, and every query starts from nothing.
        """
        k = self.get(server_id)
        model = k["model"]
        checks = [
            ("description", bool(k["description"].strip()),
             "Say what this source holds, so the agent knows when to use it."),
            ("profiled", bool(model.get("tables")),
             "Profile it, so tables, values and date ranges are known up front."),
            ("metrics", bool(model.get("metrics")),
             "Define your key metrics, so 'revenue' is computed one way, every time."),
            ("caveats", bool(model.get("caveats")),
             "Write down what is tricky: VAT, duplicates, currencies, cut-off dates."),
            ("verified", len(model.get("verified_queries") or []) >= 2,
             "Add two or more questions with checked SQL, as worked examples."),
        ]
        if not queryable:
            # A web fetcher or a clock has no tables to profile and no metric to define; its
            # description is all there is to write, and all it needs.
            checks = checks[:1]
        done = sum(1 for _, ok, _ in checks if ok)
        return {"score": done, "of": len(checks),
                "checks": [{"key": key, "ok": ok, "hint": hint} for key, ok, hint in checks]}

    # ------------------------------------------------------------------- prompt
    def catalog_lines(self, server_id: str) -> list[str]:
        """The source, compressed for the system prompt. Stable across turns on purpose:
        it sits in the cacheable prefix, so it is paid for once per conversation."""
        k = self.get(server_id)
        model = k["model"]
        lines: list[str] = []
        if k["description"].strip():
            lines.append(f"About: {_clean(k['description'], 420)}")
        body = self._model_lines(model)
        if sum(len(line) for line in body) > PROMPT_BUDGET:
            tables = model.get("tables") or []
            names = ", ".join(f"{t['name']} ({t['rows']} rows)" if t.get("rows") else t["name"]
                              for t in tables[:40])
            body = [f"Tables: {names}"] + [line for line in body
                                           if line.startswith(("Metrics:", "Caveats:"))]
            body.append("Full column facts for this source: call `source_info` once rather "
                        "than describing tables one by one.")
        return lines + body

    def _model_lines(self, model: dict) -> list[str]:
        lines: list[str] = []
        for table in model.get("tables") or []:
            facts = []
            for column in table.get("columns") or []:
                fact = column["name"]
                if column.get("references"):
                    fact += f"→{column['references']}"
                if column.get("values"):
                    shown = column["values"][:12]
                    more = "…" if len(column["values"]) > 12 else ""
                    fact += " ∈ {" + ", ".join(shown) + more + "}"
                elif column.get("range"):
                    fact += f" [{column['range'][0]} … {column['range'][1]}]"
                if column.get("description"):
                    fact += f": {column['description'][:90]}"
                facts.append(fact)
            head = table["name"]
            if table.get("rows") is not None:
                head += f" ({table['rows']} rows)"
            if table.get("description"):
                head += f" — {table['description'][:120]}"
            lines.append(f"Table {head}: " + "; ".join(facts))
        if model.get("joins"):
            lines.append("Joins: " + "; ".join(model["joins"]))
        if model.get("metrics"):
            parts = []
            for metric in model["metrics"]:
                also = f" (also: {', '.join(metric['synonyms'][:6])})" if metric.get("synonyms") else ""
                parts.append(f"{metric['name']}{also} = {metric['definition']}")
            lines.append("Metrics — use these definitions, never your own: " + " | ".join(parts))
        if model.get("caveats"):
            lines.append("Caveats: " + " · ".join(model["caveats"]))
        return lines

    def full_text(self, server_id: str, name: str = "") -> str:
        """Everything known about one source, for `source_info`."""
        k = self.get(server_id)
        parts = []
        if name:
            parts.append(f"# {name}")
        if k["description"].strip():
            parts.append(k["description"].strip())
        body = self._model_lines(k["model"])
        if body:
            parts.append("\n".join(body))
        for pair in k["model"].get("verified_queries") or []:
            parts.append(f"Verified: {pair['question']}\n  {pair['sql']}")
        return "\n\n".join(parts) or "Nothing has been written about this source yet."

    def verified_for(self, question: str, servers: list[dict], limit: int = 3) -> list[dict]:
        """Checked SQL whose question resembles this one — the cheapest correct query is
        one somebody already wrote and ran."""
        asked = _terms(question)
        if not asked:
            return []
        scored = []
        for server in servers:
            model = self.get(server["id"])["model"]
            vocabulary = {}
            for metric in model.get("metrics") or []:
                for word in [metric["name"], *metric.get("synonyms", [])]:
                    for term in _terms(word):
                        vocabulary[term] = metric["name"]
            # "CA" asked, "revenue" written in the verified question: the synonym bridges
            # them, which is what a synonym list is for.
            expanded = set(asked)
            for term in asked:
                if term in vocabulary:
                    expanded |= _terms(vocabulary[term])
            for pair in model.get("verified_queries") or []:
                overlap = len(expanded & (_terms(pair["question"]) | _terms(pair["sql"])))
                if overlap:
                    scored.append((overlap, server.get("slug") or server["name"], pair))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [{"source": slug, **pair} for _, slug, pair in scored[:limit]]

    def metrics_mentioned(self, question: str, servers: list[dict]) -> list[dict]:
        """Defined metrics the question uses, by name or synonym, with their source."""
        asked = question.lower()
        found = []
        for server in servers:
            for metric in self.get(server["id"])["model"].get("metrics") or []:
                words = [metric["name"], *metric.get("synonyms", [])]
                if any(w and re.search(rf"(?<!\w){re.escape(w.lower())}(?!\w)", asked)
                       for w in words):
                    found.append({**metric, "source": server.get("slug") or server["name"]})
        return found


def merge_models(current: dict, measured: dict) -> dict:
    """Profiler facts into a hand-written model, the hand-written parts winning."""
    if not current:
        return measured
    out = {k: v for k, v in current.items()}
    by_name = {t["name"]: t for t in current.get("tables") or []}
    tables = []
    for table in measured.get("tables") or []:
        mine = by_name.pop(table["name"], None)
        if not mine:
            tables.append(table)
            continue
        columns = {c["name"]: c for c in mine.get("columns") or []}
        merged_cols = []
        for column in table.get("columns") or []:
            written = columns.pop(column["name"], {})
            merged_cols.append({**column, **{k: v for k, v in written.items()
                                             if k in ("description", "unit", "references")
                                             or k not in column}})
        merged_cols.extend(columns.values())
        tables.append({**table, **{k: v for k, v in mine.items() if k in ("description", "grain")},
                       "columns": merged_cols})
    tables.extend(by_name.values())
    if tables:
        out["tables"] = tables
    if measured.get("joins"):
        out["joins"] = list(dict.fromkeys([*(current.get("joins") or []), *measured["joins"]]))
    if measured.get("caveats"):
        out["caveats"] = list(dict.fromkeys([*(current.get("caveats") or []), *measured["caveats"]]))
    return out
