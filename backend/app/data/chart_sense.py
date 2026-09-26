"""What each column of a chart means, so the chart can say it.

A chart of `year` and `revenue_eur` drawn literally is a line over "2.021k, 2.022k" and an
axis called "revenue_eur". Nothing is wrong with the data; the chart simply does not know
that 2021 is a year, that the values are euros, or what a reader calls either. This module
works that out, in two passes:

1. **From the data**, instantly: a column of 4-digit years, "2025-Q3" quarters, month
   names, ISO dates, shares, amounts, counts — recognised by their values first and their
   names second, because a name is a hint and a value is evidence.
2. **By the local model**, once per chart: a reader's label in the reader's language, the
   unit, and a title that describes what is shown. The model may refine the first pass,
   never contradict the data — a column cannot become a year unless its values are years.

The result travels with the chart (`usermeta.fields`), so a revision of the same chart
reuses it instead of asking again.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

KINDS = ("currency", "percent", "count", "number", "year", "quarter", "month", "month_name",
         "weekday", "date", "category")

_YEAR_NAME = re.compile(r"(^|_)(year|years|annee|année|an|yr|fy|fiscal_year|exercice|millesime|millésime|vintage)($|_)", re.I)
_MONTH_NAME_FIELD = re.compile(r"(^|_)(month|mois|mth)($|_)", re.I)
_QUARTER = re.compile(r"^\s*(?:(\d{4})\s*[-_ ]?\s*[QT]([1-4])|[QT]([1-4])\s*[-_ /]?\s*(\d{4}))\s*$", re.I)
_YEARMONTH = re.compile(r"^\d{4}-\d{2}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$")
_PERCENT = re.compile(r"((^|_)(pct|percent|percentage|share|part|parts|taux|margin|marge|growth|croissance|evolution|évolution|variation)($|_)|%)", re.I)
_CURRENCY = re.compile(r"(revenue|revenu|sales|ventes?($|_)|(^|_)ca($|_)|chiffre|amount|montant|price|prix|cost|cout|coût|pnl|p&l|notional|nominal|exposure|exposition|(^|_)mtm($|_)|market_value|(^|_)mv($|_)|balance|solde|budget|spend|depense|dépense|(^|_)fees?($|_)|frais|commission|(^|_)aov($|_)|panier|(^|_)(eur|usd|gbp|chf|jpy)($|_))", re.I)
_COUNT = re.compile(r"(^|_)(count|nb|nombre|qty|quantity|quantite|quantité|units|unites|unités|orders|commandes|clients|customers|trades|deals|n)($|_)", re.I)
_CCY = re.compile(r"(?:^|_)(eur|usd|gbp|chf|jpy|cad|aud|cny|hkd|sgd)(?:$|_)", re.I)
_ACRONYMS = {"ca", "pnl", "var", "fx", "eur", "usd", "gbp", "chf", "jpy", "id", "aov", "kpi",
             "ytd", "mtd", "qtd", "yoy", "mom", "cib", "mtm", "irr", "roe", "rwa", "cva", "bp",
             "bps", "otc", "etf", "isin", "lei", "esg", "nav", "sku", "b2b", "b2c", "h1", "h2",
             "q1", "q2", "q3", "q4"}

MONTHS = {
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
           "septembre", "octobre", "novembre", "décembre"],
    "fr_short": ["janv", "févr", "mars", "avr", "mai", "juin", "juil", "août", "sept", "oct",
                 "nov", "déc"],
    "en": ["january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december"],
    "en_short": ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                 "dec"],
}
WEEKDAYS = {
    "fr": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],
    "fr_short": ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"],
    "en": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
    "en_short": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
}


def _norm(value: Any) -> str:
    return str(value).strip().lower().rstrip(".")


def _position(value: Any, table: dict[str, list[str]]) -> int | None:
    word = _norm(value)
    for names in table.values():
        for i, name in enumerate(names):
            if word == name or (len(word) >= 3 and name.startswith(word)):
                return i
    return None


def _is_year(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer()):
        return 1900 <= int(value) <= 2100
    return isinstance(value, str) and bool(re.fullmatch(r"(19|20)\d{2}", value.strip()))


def quarter_key(value: Any) -> tuple[int, int] | None:
    match = _QUARTER.match(str(value))
    if not match:
        return None
    year = int(match.group(1) or match.group(4))
    quarter = int(match.group(2) or match.group(3))
    return year, quarter


def humanize(field: str) -> str:
    words = [w for w in re.split(r"[_\s]+", field.strip()) if w]
    if not words:
        return field
    out = [w.upper() if w.lower() in _ACRONYMS else w for w in words]
    first = out[0]
    out[0] = first if first.isupper() else first[:1].upper() + first[1:]
    return " ".join(out)


def infer(rows: list[dict]) -> dict[str, dict]:
    """A first reading of every column, from its values first and its name second."""
    samples: dict[str, list] = {}
    for row in rows[:500]:
        for key, value in row.items():
            if value is not None and value != "":
                samples.setdefault(key, []).append(value)
    out: dict[str, dict] = {}
    for field in {k for row in rows[:500] for k in row}:
        values = samples.get(field, [])
        out[field] = _infer_one(field, values)
    return out


def _infer_one(field: str, values: list) -> dict:
    label = humanize(field)
    numeric = bool(values) and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                   for v in values)
    strings = bool(values) and all(isinstance(v, str) for v in values)
    distinct = len({json.dumps(v, default=str) for v in values})
    info: dict[str, Any] = {"label": label, "unit": "", "kind": "category" if not numeric else "number",
                            "distinct": distinct}
    if not values:
        return info
    if all(_is_year(v) for v in values) and (strings or _YEAR_NAME.search(field)):
        info["kind"] = "year"
    elif strings and all(quarter_key(v) for v in values):
        info["kind"] = "quarter"
    elif strings and all(_YEARMONTH.match(v) for v in values):
        info["kind"] = "month"
    elif strings and all(_ISO_DATE.match(v.strip()) for v in values):
        info["kind"] = "date"
    elif strings and all(_position(v, MONTHS) is not None for v in values) and distinct <= 12:
        info["kind"] = "month_name"
    elif strings and all(_position(v, WEEKDAYS) is not None for v in values) and distinct <= 7:
        info["kind"] = "weekday"
    elif numeric and _MONTH_NAME_FIELD.search(field) and all(float(v).is_integer() and 1 <= v <= 12 for v in values):
        info["kind"] = "month_name"
        info["month_numbers"] = True
    elif numeric:
        if _PERCENT.search(field):
            info["kind"] = "percent"
            info["fraction"] = all(-1.5 <= float(v) <= 1.5 for v in values)
        elif _CURRENCY.search(field):
            info["kind"] = "currency"
            ccy = _CCY.search(field)
            info["unit"] = ccy.group(1).upper() if ccy else ""
            if ccy:   # the unit goes in brackets, not in the name: "Revenue (EUR)"
                info["label"] = humanize(_CCY.sub("_", field).strip("_")) or label
        elif _COUNT.search(field) and all(float(v).is_integer() for v in values):
            info["kind"] = "count"
    if numeric:
        magnitudes = [abs(float(v)) for v in values]
        info["max"] = max(magnitudes)
        info["integer"] = all(float(v).is_integer() for v in values)
    return info


def merge(inferred: dict[str, dict], reviewed: dict[str, dict] | None) -> dict[str, dict]:
    """The model's reading over the data's — where the data allows it."""
    out = {k: dict(v) for k, v in inferred.items()}
    for field, proposal in (reviewed or {}).items():
        if field not in out or not isinstance(proposal, dict):
            continue
        base = out[field]
        label = str(proposal.get("label") or "").strip()
        if label and len(label) <= 60:
            base["label"] = label
        unit = str(proposal.get("unit") or "").strip()
        if len(unit) <= 12:
            base["unit"] = unit or base.get("unit", "")
        kind = str(proposal.get("kind") or "").strip()
        if kind in KINDS and _compatible(kind, base):
            base["kind"] = kind
            if kind == "percent" and "fraction" not in base:
                base["fraction"] = base.get("max", 2) <= 1.5
    return out


def _compatible(kind: str, base: dict) -> bool:
    """Whether the data can bear the kind the model proposes."""
    numeric = base.get("kind") in ("number", "currency", "percent", "count") or "max" in base
    temporal = base.get("kind") in ("year", "quarter", "month", "date", "month_name", "weekday")
    if kind in ("currency", "percent", "count", "number"):
        return numeric and not temporal
    if kind in ("year", "quarter", "month", "date", "month_name", "weekday"):
        return base.get("kind") == kind      # the data decides what is a date
    return not numeric or base.get("kind") == "category"


# --------------------------------------------------------------- the model's pass

REVIEW_SYSTEM = """You are a chart editor at an investment bank. You receive a chart spec and a \
profile of its columns, and return how each column should be named for a business reader.

Rules:
- Labels in the language of the question, short (1-4 words), the way an analyst would say \
them: "Chiffre d'affaires", "Canal", "Mois", "Part du CA", "Nombre de commandes".
- unit: the currency code (EUR, USD…), "%" for shares and rates, "" for counts and categories. \
Only state a unit the column name, the values or the question support; if unsure, "".
- kind: currency, percent, count, number, year, quarter, month, month_name, weekday, date or \
category — what the values ARE.
- title: what the chart shows, descriptive, in the question's language, no conclusion that \
is not visible in the data. If the question asks for a specific title, keep it verbatim. \
subtitle: scope and unit (period, filter, currency), or "".
Return JSON only, exactly this shape, one entry per column listed:
{"title": "...", "subtitle": "...", "fields": {"<column>": {"label": "...", "unit": "...", "kind": "..."}}}"""

_SCHEMA = {"type": "object",
           "properties": {"title": {"type": "string"}, "subtitle": {"type": "string"},
                          "fields": {"type": "object", "additionalProperties": {
                              "type": "object",
                              "properties": {"label": {"type": "string"}, "unit": {"type": "string"},
                                             "kind": {"type": "string"}}}}},
           "required": ["fields"]}


def _profile_lines(rows: list[dict], inferred: dict[str, dict], fields: list[str]) -> str:
    lines = []
    for field in fields:
        info = inferred.get(field, {})
        values = [r.get(field) for r in rows[:200] if r.get(field) not in (None, "")]
        shown = list(dict.fromkeys(json.dumps(v, ensure_ascii=False, default=str) for v in values))[:6]
        extra = f", max {info['max']:,.2f}" if "max" in info else ""
        lines.append(f"- {field}: guessed {info.get('kind')}{extra}, {info.get('distinct', 0)} distinct; "
                     f"e.g. {', '.join(shown)}")
    return "\n".join(lines)


async def review(llm, question: str, spec: dict, rows: list[dict], inferred: dict[str, dict],
                 fields: list[str], title: str, timeout_s: float = 20.0) -> dict:
    """The local model's reading of the chart: labels, units, kinds, a title. {} on failure."""
    if not fields:
        return {}
    shape = {k: v for k, v in spec.items() if k not in ("data", "config", "usermeta")}
    prompt = (f"Question: {question.strip()[:600]}\n\n"
              f"Chart title proposed by the analyst: {title or '(none)'}\n\n"
              f"Spec (without data): {json.dumps(shape, ensure_ascii=False, default=str)[:2500]}\n\n"
              f"Columns used ({len(rows)} rows):\n{_profile_lines(rows, inferred, fields)}")
    try:
        result = await asyncio.wait_for(llm.chat(
            [{"role": "user", "content": prompt}], system=REVIEW_SYSTEM, temperature=0.0,
            think=False, json_schema=_SCHEMA), timeout=timeout_s)
        data = json.loads(result.content or "{}")
    except Exception:  # noqa: BLE001 - the chart is drawn either way, from the data's reading
        return {}
    if not isinstance(data, dict):
        return {}
    # Not every server enforces the schema: take the per-column object under whatever
    # name the model gave it ("fields", "columns"…), recognised by its shape.
    if not isinstance(data.get("fields"), dict):
        for key, value in list(data.items()):
            if isinstance(value, dict) and value and all(
                    isinstance(v, dict) and ("label" in v or "kind" in v) for v in value.values()):
                data["fields"] = value
                break
    return data
