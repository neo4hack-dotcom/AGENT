"""Charts: one Vega-Lite spec, the real rows, rendered the same on screen and on paper.

Vega-Lite because it is a grammar, not a gallery. Bar, line, area, scatter, pie and donut,
heatmap, boxplot, combined bar-and-line, small multiples, labelled and annotated variants
are all the same dozen words recombined — which is also why a model writes it well, and why
"make it stacked" or "split by channel" is an edit to one line of a spec rather than a new
chart. The spec is the chart; the browser draws it with vega-embed and the PDF with
vl-convert, from the identical document.

The data never travels through the model. A chart names where its rows come from — `#4`,
another chart, a workspace file — and is filled from the real result. The spec is then
checked against those rows: a field the model invented is refused with the list of columns
that exist, before anything is drawn. A chart of a column that does not exist is not an
error a reader can see; it is an empty axis that looks like a finding.
"""

from __future__ import annotations

import asyncio
import copy
import difflib
import json
import re
import time
from pathlib import Path
from typing import Any

MAX_CHART_ROWS = 5000

# ------------------------------------------------------------------ the theme

PRIMARY = "#00A383"
CATEGORY = ["#00A383", "#4E79A7", "#F28E2B", "#E15759", "#B07AA1", "#EDC948",
            "#76B7B2", "#9C755F", "#FF9DA7", "#BAB0AC"]
FONT = "Inter, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"


def theme(dark: bool = False) -> dict:
    """A restrained house style: left-aligned titles, quiet grids, one accent colour.

    Tableau's ten-colour palette for categories — the one most business readers already
    know how to read — led by the app's own green.
    """
    ink, muted, grid, domain = (("#e4e4e7", "#a1a1aa", "#3f3f46", "#52525b") if dark
                                else ("#18181b", "#71717a", "#e4e4e7", "#d4d4d8"))
    primary = "#00D4AA" if dark else PRIMARY
    category = [primary, *CATEGORY[1:]]
    return {
        "background": None if dark else "#ffffff",
        "font": FONT,
        "padding": 12,
        "view": {"stroke": None},
        "title": {"anchor": "start", "fontSize": 14, "fontWeight": 600, "color": ink,
                  "subtitleColor": muted, "subtitleFontSize": 11.5, "offset": 14,
                  "subtitlePadding": 4},
        "axis": {"labelColor": muted, "titleColor": muted, "labelFontSize": 11,
                 "titleFontSize": 11, "titleFontWeight": 500, "gridColor": grid,
                 "domainColor": domain, "tickColor": domain, "labelPadding": 6,
                 "titlePadding": 10, "labelLimit": 180},
        "axisX": {"grid": False},
        # Horizontal category labels, thinned rather than rotated when crowded: nobody
        # reads a month sideways, and a chart with many long categories should be a
        # horizontal bar anyway.
        "axisXDiscrete": {"labelAngle": 0, "labelOverlap": "greedy", "labelLimit": 140},
        "axisY": {"domain": False, "ticks": False},
        "axisQuantitative": {"format": "~s", "tickCount": 6},
        "legend": {"labelColor": muted, "titleColor": muted, "labelFontSize": 11,
                   "titleFontSize": 11, "symbolType": "circle", "symbolSize": 70,
                   "orient": "bottom", "direction": "horizontal", "titleOrient": "left",
                   "labelLimit": 160},
        "header": {"labelColor": ink, "titleColor": muted, "labelFontSize": 11.5,
                   "labelFontWeight": 500},
        "range": {"category": category, "ordinal": {"scheme": "greens"},
                  "ramp": {"scheme": "tealblues"}},
        "numberFormat": ",.2~f",
        "bar": {"color": primary, "cornerRadiusEnd": 2, "discreteBandSize": {"band": 0.72}},
        "line": {"color": primary, "strokeWidth": 2.25, "strokeCap": "round"},
        "area": {"color": primary, "opacity": 0.85, "line": False},
        "point": {"color": primary, "filled": True, "size": 55},
        "circle": {"color": primary},
        "rect": {"color": primary},
        "rule": {"color": muted},
        "arc": {"stroke": None if dark else "#ffffff", "strokeWidth": 1.5},
        "text": {"color": ink, "fontSize": 11},
        "boxplot": {"box": {"color": primary}, "median": {"color": ink}},
    }


# --------------------------------------------------------------- sanitising

_BLOCKED_MARKS = {"image"}
_CHANNELS_BLOCKED = {"href", "url"}


class ChartError(ValueError):
    pass


def sanitize(spec: dict) -> dict:
    """Strip anything that would make the chart reach outside, and any data it carried.

    A spec is written by a model that may have read hostile content. `data.url`, an image
    mark or an `href` channel would each have the reader's browser fetch or link to an
    address the spec chose — so none of them survive; rows come only from the resolved
    source.
    """
    spec = copy.deepcopy(spec)

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            node.pop("data", None)
            node.pop("usermeta", None)
            mark = node.get("mark")
            kind = mark.get("type") if isinstance(mark, dict) else mark
            if kind in _BLOCKED_MARKS:
                raise ChartError(f"The '{kind}' mark is not allowed: it loads content from a URL.")
            encoding = node.get("encoding")
            if isinstance(encoding, dict):
                for channel in list(encoding):
                    if channel in _CHANNELS_BLOCKED:
                        encoding.pop(channel)
            for key, value in list(node.items()):
                if key != "config":
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        return node

    walk(spec)
    spec.pop("$schema", None)
    spec.pop("datasets", None)
    return spec


# --------------------------------------------------------------- validation

_DERIVING = {"calculate", "aggregate", "bin", "timeUnit", "fold", "window", "joinaggregate",
             "stack", "flatten", "density", "regression", "loess", "quantile", "lookup",
             "pivot", "impute", "extent"}
_OPAQUE = {"pivot", "lookup", "flatten"}


_DATUM = re.compile(r"datum\.([A-Za-z_]\w*)|datum\[\s*['\"]([^'\"]+)['\"]\s*\]")


def _fields_used(node: Any, out: set[str]) -> None:
    """Every field name the spec reads: encodings, sorts, and each transform's inputs.

    Transform inputs matter most. `{"aggregate":[{"op":"sum","field":"revenu"}]}` over a
    column that does not exist does not fail — it sums nulls to zero and draws a chart of
    zeros, which is the most convincing wrong chart there is.
    """
    if isinstance(node, dict):
        field = node.get("field")
        if isinstance(field, str):
            out.add(field.replace("\\.", "."))
        for step in node.get("transform") or []:
            if not isinstance(step, dict):
                continue
            for key in ("groupby", "fold"):
                for name in step.get(key) or []:
                    if isinstance(name, str):
                        out.add(name)
            for key in ("aggregate", "window", "joinaggregate"):
                items = step.get(key)
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict) and isinstance(item.get("field"), str):
                            out.add(item["field"])
            for key in ("field", "timeUnit", "bin"):
                if key == "field" and isinstance(step.get("field"), str):
                    out.add(step["field"])
            for key in ("calculate", "filter"):
                expr = step.get(key)
                if isinstance(expr, str):
                    out.update(a or b for a, b in _DATUM.findall(expr))
                elif isinstance(expr, dict) and isinstance(expr.get("field"), str):
                    out.add(expr["field"])
        for key, value in node.items():
            if key in ("transform", "config", "params", "datum"):
                continue
            _fields_used(value, out)
    elif isinstance(node, list):
        for item in node:
            _fields_used(item, out)


def _fields_made(node: Any, out: set[str]) -> bool:
    """Fields the spec's transforms create. Returns False if a transform makes names that
    cannot be known without running it — then validation stands down rather than guess."""
    knowable = True
    if isinstance(node, dict):
        for step in node.get("transform") or []:
            if not isinstance(step, dict):
                continue
            kinds = set(step) & _DERIVING
            if kinds & _OPAQUE:
                knowable = False
            name = step.get("as")
            if isinstance(name, str):
                out.add(name)
            elif isinstance(name, list):
                out.update(n for n in name if isinstance(n, str))
            for key in ("aggregate", "window", "joinaggregate"):
                for item in step.get(key) or []:
                    if isinstance(item, dict) and isinstance(item.get("as"), str):
                        out.add(item["as"])
            if "fold" in step and not step.get("as"):
                out.update({"key", "value"})
            if "density" in step and not step.get("as"):
                out.update({"value", "density"})
            if "quantile" in step and not step.get("as"):
                out.update({"prob", "value"})
            if ("regression" in step or "loess" in step) and not step.get("as"):
                out.update({step.get("on", ""), step.get("regression") or step.get("loess") or ""})
        for key in ("layer", "hconcat", "vconcat", "concat"):
            for child in node.get(key) or []:
                knowable = _fields_made(child, out) and knowable
        if isinstance(node.get("spec"), dict):
            knowable = _fields_made(node["spec"], out) and knowable
    return knowable


def validate_fields(spec: dict, columns: list[str]) -> None:
    used: set[str] = set()
    _fields_used(spec, used)
    made: set[str] = set()
    if not _fields_made(spec, made):
        return
    known = set(columns) | made
    missing = sorted(f for f in used if f and f not in known)
    if missing:
        hints = []
        for name in missing:
            close = difflib.get_close_matches(name, columns, n=1, cutoff=0.6)
            hints.append(f"'{name}'" + (f" (did you mean '{close[0]}'?)" if close else ""))
        raise ChartError(f"The spec uses {', '.join(hints)}, which the data does not have. "
                         f"Columns available: {', '.join(columns)}. Use those names exactly.")


# ------------------------------------------------------------------ building

# ------------------------------------------------------------------ locales

# d3 locale definitions, inline: a locale named rather than defined is fetched from a CDN
# by the browser, and nothing here may be fetched from anywhere.
LOCALES: dict[str, dict] = {
    "fr-FR": {
        "format": {"decimal": ",", "thousands": " ", "grouping": [3],
                   "currency": ["", " €"], "percent": " %"},
        "time": {"dateTime": "%A %e %B %Y à %X", "date": "%d/%m/%Y", "time": "%H:%M:%S",
                 "periods": ["AM", "PM"],
                 "days": ["dimanche", "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi"],
                 "shortDays": ["dim.", "lun.", "mar.", "mer.", "jeu.", "ven.", "sam."],
                 "months": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
                            "août", "septembre", "octobre", "novembre", "décembre"],
                 "shortMonths": ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.",
                                 "août", "sept.", "oct.", "nov.", "déc."]},
        "currency": "EUR", "billion": "Md",
    },
    "en-US": {
        "format": {"decimal": ".", "thousands": ",", "grouping": [3], "currency": ["$", ""]},
        "time": {"dateTime": "%x, %X %p", "date": "%-m/%-d/%Y", "time": "%-I:%M:%S %p",
                 "periods": ["AM", "PM"],
                 "days": ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"],
                 "shortDays": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
                 "months": ["January", "February", "March", "April", "May", "June", "July",
                            "August", "September", "October", "November", "December"],
                 "shortMonths": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
                                 "Oct", "Nov", "Dec"]},
        "currency": "USD", "billion": "B",
    },
    "en-GB": {
        "format": {"decimal": ".", "thousands": ",", "grouping": [3], "currency": ["£", ""]},
        "time": {"dateTime": "%a %e %b %X %Y", "date": "%d/%m/%Y", "time": "%H:%M:%S",
                 "periods": ["AM", "PM"],
                 "days": ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"],
                 "shortDays": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
                 "months": ["January", "February", "March", "April", "May", "June", "July",
                            "August", "September", "October", "November", "December"],
                 "shortMonths": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
                                 "Oct", "Nov", "Dec"]},
        "currency": "GBP", "billion": "bn",
    },
}


def locale_of(name: str) -> dict:
    return LOCALES.get(name) or LOCALES["fr-FR"]


# ------------------------------------------------------------------ building

_POSITION = ("x", "y", "xOffset", "yOffset", "column", "row", "facet")
_TOOLTIP_CHANNELS = ("x", "y", "color", "theta", "size", "xOffset", "column", "row", "shape",
                     "opacity", "detail")
_TEMPORAL_KINDS = ("year", "quarter", "month", "month_name", "weekday", "date")


def compact_expr(locale: str) -> str:
    """Axis labels a reader of financial figures expects: 850, 12 k, 1,5 M, 2 Md."""
    billion = locale_of(locale)["billion"]
    return ("abs(datum.value) >= 1e9 ? format(datum.value / 1e9, ',.1~f') + ' " + billion + "' : "
            "abs(datum.value) >= 1e6 ? format(datum.value / 1e6, ',.1~f') + ' M' : "
            "abs(datum.value) >= 1e4 ? format(datum.value / 1e3, ',.0f') + ' k' : "
            "format(datum.value, ',.2~f')")


def titled(info: dict) -> str:
    label = info.get("label") or ""
    unit = (info.get("unit") or "").strip()
    if info.get("kind") == "percent" and "%" not in label:
        return f"{label} (%)"
    if unit and unit != "%" and unit.lower() not in label.lower():
        return f"{label} ({unit})"
    return label


def _tooltip_format(info: dict, definition: dict, locale: str) -> dict:
    kind = info.get("kind")
    if definition.get("timeUnit") == "yearmonth" or kind == "month" and definition.get("type") == "temporal":
        return {"format": "%B %Y"}
    if definition.get("type") == "temporal":
        return {"format": "%d %B %Y"}
    if definition.get("type") != "quantitative" or kind in _TEMPORAL_KINDS:
        return {}
    if definition.get("aggregate") == "count":
        return {"format": ",.0f"}
    if kind == "percent":
        return {"format": ".1%" if info.get("fraction") else ",.1f"}
    if kind == "currency":
        same = (info.get("unit") or "").upper() == locale_of(locale)["currency"]
        return {"format": "$,.2f" if same else ",.2f"}
    if kind == "count" or (info.get("integer") and definition.get("aggregate") in (None, "sum", "max", "min")):
        return {"format": ",.0f"}
    return {"format": ",.2~f"}


def polish(spec: dict, rows: list[dict], fields: dict[str, dict] | None = None,
           locale: str = "fr-FR") -> dict:
    """The corrections a careful analyst makes by hand, applied so the model need not.

    Years are categories on an axis, not quantities: 2021 as a number is drawn "2.021k",
    and as a date it is read as 2021 milliseconds after 1970. Quarters and month names sort
    in calendar order, not alphabetically. "2026-01" is a month, one clean band per month.
    Axes are titled the way a reader would say them, with the unit; amounts read 12 k,
    1,5 M; and every mark answers a hover — or a click — with what it is, formatted.
    """
    from app.data import chart_sense as sense

    fields = fields or sense.infer(rows)
    values_of: dict[str, list] = {}
    for row in rows[:2000]:
        for key, value in row.items():
            if value is not None:
                values_of.setdefault(key, []).append(value)

    def calendar_sort(field: str, kind: str, info: dict) -> list | None:
        distinct = list(dict.fromkeys(values_of.get(field, [])))
        if kind == "quarter":
            return sorted(distinct, key=lambda v: sense.quarter_key(v) or (0, 0))
        if kind == "month_name" and not info.get("month_numbers"):
            return sorted(distinct, key=lambda v: sense._position(v, sense.MONTHS) or 0)
        if kind == "weekday":
            return sorted(distinct, key=lambda v: sense._position(v, sense.WEEKDAYS) or 0)
        return None

    def fix_channel(channel: str, d: dict, mark: str) -> None:
        field = d.get("field")
        info = fields.get(field) if isinstance(field, str) else None
        if info is None:
            return
        kind = info.get("kind")
        derived = d.get("aggregate") or d.get("bin")
        if kind == "month" and not d.get("timeUnit") and not derived \
                and d.get("type") in ("temporal", "ordinal", "nominal", None):
            d["timeUnit"] = "yearmonth"
            d["type"] = "ordinal" if mark in ("bar", "rect", "boxplot") and channel in ("x", "y") else "temporal"
            axis = d.setdefault("axis", {}) if channel in ("x", "y") else None
            if isinstance(axis, dict):
                axis.setdefault("format", "%b %Y")
                if d["type"] == "temporal":
                    axis.setdefault("tickCount", {"interval": "month", "step": 1})
        elif kind in ("year", "quarter", "month_name", "weekday") and not derived:
            # A category with an order: never a quantity, never a timestamp.
            d.pop("timeUnit", None)
            if d.get("type") in ("quantitative", "temporal"):
                d.pop("scale", None)      # a zero-based or time scale means nothing on bands
            d["type"] = "ordinal"
            order = calendar_sort(field, kind, info)
            if order and "sort" not in d:
                d["sort"] = order
            if kind == "month_name" and info.get("month_numbers") and channel in ("x", "y"):
                months = locale_of(locale)["time"]["shortMonths"]
                d.setdefault("axis", {}).setdefault(
                    "labelExpr", f"{json.dumps(months, ensure_ascii=False)}[datum.value - 1]")
        elif kind == "date" and d.get("type") in ("nominal", "ordinal") and channel == "x" \
                and mark in ("line", "area", "point", "trail", "circle"):
            d["type"] = "temporal"
        # The reader's name for it, with the unit — unless the analyst named it on purpose.
        current = d.get("title")
        if current is None or (isinstance(current, str)
                               and current.strip().lower().replace(" ", "_") == field.lower()):
            d["title"] = titled(info)
        if d.get("type") == "quantitative" and kind not in _TEMPORAL_KINDS and channel in ("x", "y"):
            axis = d.get("axis")
            if axis is None:
                axis = d["axis"] = {}
            if isinstance(axis, dict) and "format" not in axis and "labelExpr" not in axis \
                    and not d.get("stack") == "normalize":
                if kind == "percent" and info.get("fraction"):
                    axis["format"] = ".0%"
                elif kind == "percent":
                    axis["labelExpr"] = "format(datum.value, ',.0~f') + ' %'"
                else:
                    axis["labelExpr"] = compact_expr(locale)

    def tooltip_for(encoding: dict) -> list[dict]:
        entries, seen = [], set()
        for channel in _TOOLTIP_CHANNELS:
            d = encoding.get(channel)
            if not isinstance(d, dict) or not (d.get("field") or d.get("aggregate") == "count"):
                continue
            key = (d.get("field"), d.get("aggregate"), json.dumps(d.get("timeUnit")), json.dumps(d.get("bin")))
            if key in seen:
                continue
            seen.add(key)
            entry = {k: d[k] for k in ("field", "type", "aggregate", "timeUnit", "bin") if k in d}
            info = fields.get(d.get("field") or "", {})
            entry["title"] = d.get("title") or (titled(info) if info else
                                                ("Count" if d.get("aggregate") == "count" else d.get("field")))
            entry.update(_tooltip_format(info, d, locale))
            if d.get("stack") == "normalize" and channel in ("x", "y"):
                entry.pop("format", None)
            entries.append(entry)
        return entries

    def mark_of(node: dict, inherited: str) -> str:
        own = node.get("mark")
        kind = (own.get("type") if isinstance(own, dict) else own) or inherited
        if not own and node.get("layer"):
            # A shared encoding above layers serves every layer; if any of them is a bar,
            # the axis must be bands, or the bars slide off the points drawn on them.
            kinds = [(l.get("mark", {}).get("type") if isinstance(l.get("mark"), dict)
                      else l.get("mark")) for l in node["layer"] if isinstance(l, dict)]
            kind = "bar" if any(k in ("bar", "rect", "boxplot") for k in kinds) else (kinds[0] if kinds else kind)
        return kind or ""

    def series_length(encoding: dict) -> int:
        color = encoding.get("color") if isinstance(encoding, dict) else None
        field = color.get("field") if isinstance(color, dict) else None
        groups = len({row.get(field) for row in rows}) if field else 1
        return len(rows) // max(1, groups)

    def walk(node: Any, inherited: str, has_tooltip_above: bool) -> None:
        if not isinstance(node, dict):
            return
        kind = mark_of(node, inherited)
        own = node.get("mark")
        if kind == "line" and own and "point" not in (own if isinstance(own, dict) else {}) \
                and series_length(node.get("encoding") or {}) <= 60:
            # A line is one shape: clicking or hovering it can only ever report its first
            # point. A dot per value is what makes each value readable, and clickable.
            node["mark"] = {**(own if isinstance(own, dict) else {"type": own}),
                            "point": {"filled": True, "size": 42}}
            own = node["mark"]
        if isinstance(own, dict) and own.get("type") in ("line", "area") and own.get("color") \
                and own.get("point") is True:
            # The points on a coloured line otherwise take the theme's default colour.
            own["point"] = {"color": own["color"], "filled": True}
        encoding = node.get("encoding")
        tooltip_here = has_tooltip_above
        if isinstance(encoding, dict):
            for channel, d in list(encoding.items()):
                if isinstance(d, dict) and channel not in ("tooltip",):
                    fix_channel(channel, d, kind)
            if "tooltip" not in encoding and not has_tooltip_above and kind not in ("rule", "text"):
                entries = tooltip_for(encoding)
                if entries:
                    encoding["tooltip"] = entries
            tooltip_here = tooltip_here or "tooltip" in encoding
        if isinstance(own, dict) and own.get("tooltip") is not None:
            tooltip_here = True
        for key in ("layer", "hconcat", "vconcat", "concat"):
            for child in node.get(key) or []:
                walk(child, kind, tooltip_here)
        if isinstance(node.get("spec"), dict):
            walk(node["spec"], kind, tooltip_here)
        facet = node.get("facet")
        if isinstance(facet, dict):
            for d in ([facet] if "field" in facet else [v for v in facet.values() if isinstance(v, dict)]):
                fix_channel("facet", d, kind)

    walk(spec, "", False)
    spec["usermeta"] = {"fields": {k: {kk: vv for kk, vv in v.items() if kk in ("label", "unit", "kind", "fraction", "month_numbers")}
                                   for k, v in fields.items()}}
    return spec


def assemble(spec: dict, rows: list[dict], title: str = "", subtitle: str = "",
             fields: dict[str, dict] | None = None, locale: str = "fr-FR") -> dict:
    """The stored chart: the model's spec, the real rows, a title — and no theme.

    Theme is applied when drawn, so the same chart follows the reader into dark mode on
    screen and prints on white in the PDF.
    """
    full = polish(sanitize(spec), rows, fields, locale)
    if title or subtitle:
        current = full.get("title")
        block = current if isinstance(current, dict) else ({"text": current} if current else {})
        if title:
            block["text"] = title
        if subtitle:
            block["subtitle"] = subtitle
        full["title"] = block
    full["data"] = {"values": rows}
    full.setdefault("width", "container")
    if not any(k in full for k in ("mark", "layer", "hconcat", "vconcat", "concat",
                                   "facet", "repeat")):
        raise ChartError("The spec has no mark. Give it at least a 'mark' and an 'encoding'.")
    return full


def label_codes(spec: dict, rows: list[dict], names: dict[str, str]) -> None:
    """Axes and legends show an instrument's name where the rows carry only its code.

    The data keeps the ISIN — it is the key — and the labels read the way the reader knows
    the instrument. Only codes whose name a result gave are relabelled.
    """
    if not names or not rows:
        return
    import json as _json

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        encoding = node.get("encoding")
        if isinstance(encoding, dict):
            for channel in ("x", "y", "color", "column", "row"):
                d = encoding.get(channel)
                if not isinstance(d, dict) or d.get("type") not in ("nominal", "ordinal") or not d.get("field"):
                    continue
                values = {str(r.get(d["field"])) for r in rows if r.get(d["field"]) is not None}
                mapping = {v: names[v] for v in values if v in names}
                if not mapping or len(mapping) < 0.8 * len(values):
                    continue
                expr = f"({_json.dumps(mapping, ensure_ascii=False)})[datum.value] || datum.value"
                target = "legend" if channel == "color" else ("header" if channel in ("column", "row") else "axis")
                block = d.get(target)
                if block is None or isinstance(block, dict):
                    block = block or {}
                    block.setdefault("labelExpr", expr)
                    d[target] = block
        for key in ("layer", "hconcat", "vconcat", "concat"):
            for child in node.get(key) or []:
                walk(child)
        if isinstance(node.get("spec"), dict):
            walk(node["spec"])

    walk(spec)


def fields_used(spec: dict) -> list[str]:
    """The columns the chart encodes, in the order a reader meets them."""
    used: set[str] = set()
    _fields_used({k: v for k, v in spec.items() if k != "data"}, used)
    return sorted(f for f in used if f)


def themed(spec: dict, *, dark: bool = False, width: int | None = None) -> dict:
    """The spec with the house style underneath the spec's own config."""
    out = copy.deepcopy(spec)
    config = theme(dark)
    for key, value in (spec.get("config") or {}).items():
        config[key] = {**config[key], **value} if isinstance(config.get(key), dict) \
            and isinstance(value, dict) else value
    out["config"] = config
    if width is not None:
        if out.get("width") == "container" or "width" not in out:
            out["width"] = width
        out.setdefault("autosize", {"type": "fit", "contains": "padding"})
    return out


# The renderer is a JavaScript engine that will fetch a `data.url` if a spec has one. The
# sanitizer already strips them; this makes the engine itself refuse, whatever gets past.
NO_NETWORK: list[str] = []


def _locale_args(locale: str) -> dict:
    defined = locale_of(locale)
    return {"format_locale": defined["format"], "time_format_locale": defined["time"]}


def _render_svg(spec: dict, locale: str = "fr-FR") -> str:
    import vl_convert as vlc
    return vlc.vegalite_to_svg(spec, allowed_base_urls=NO_NETWORK, **_locale_args(locale))


def render_png(spec: dict, width: int = 900, scale: float = 2.0, locale: str = "fr-FR") -> bytes:
    import vl_convert as vlc
    return vlc.vegalite_to_png(themed(spec, width=width), scale=scale, allowed_base_urls=NO_NETWORK,
                               **_locale_args(locale))


# Names that say "a rate" — used when the column's meaning did not already say percent.
# Not "shares" (a quantity, summed legitimately) nor "rated".
_RATE_NAME = re.compile(r"(percent|pct|ratio|(?:^|_)rate(?:$|_)|market_share|share_(?:pct|of)|"
                        r"utili[sz]ation|usage_(?:pct|percent|rate)|taux|yield)", re.I)
_DISCRETE = {"nominal", "ordinal", "temporal"}


def _encodings(node: Any) -> list[dict]:
    """Every encoding block of a spec: the top level and each layer."""
    out = []
    if isinstance(node, dict):
        if isinstance(node.get("encoding"), dict):
            out.append(node["encoding"])
        for key in ("layer", "hconcat", "vconcat", "concat"):
            for child in node.get(key) or []:
                out += _encodings(child)
        if isinstance(node.get("spec"), dict):
            out += _encodings(node["spec"])
    return out


def degenerate(spec: dict, rows: list[dict], fields: dict[str, dict] | None = None) -> str:
    """Why a chart that renders would still mislead, or "".

    Two shapes seen from a small model, both drawn without error: the usage rates of four
    desks *summed* into one 251 % bar, and four rows collapsed into a single mark because
    the colour channel named no field. A chart the reader cannot tell is wrong is the worst
    kind.
    """
    fields = fields or {}
    blocks = _encodings(spec)
    if not blocks:
        return ""
    for encoding in blocks:
        for channel, definition in encoding.items():
            if not isinstance(definition, dict) or definition.get("aggregate") != "sum":
                continue
            field = str(definition.get("field") or "")
            kind = str((fields.get(field) or {}).get("kind") or "")
            if field and (kind == "percent" or _RATE_NAME.search(field)):
                return (f"'{field}' is a rate or a percentage: summing it across rows adds rates "
                        f"together (four desks at 72 %, 65 %, 66 % and 49 % make one 251 % bar). "
                        f"Chart each row as it is — no aggregate — with the category on the other axis.")
    if len(rows) > 1 and "facet" not in spec and "repeat" not in spec:
        split = False
        for encoding in blocks:
            for channel, definition in encoding.items():
                if channel in ("tooltip", "text", "detail") or not isinstance(definition, dict):
                    continue
                if definition.get("field") and (definition.get("type") in _DISCRETE
                                                or definition.get("timeUnit") or definition.get("bin")):
                    split = True
            if isinstance(encoding.get("detail"), dict) and encoding["detail"].get("field"):
                split = True
        aggregated = any(isinstance(d, dict) and d.get("aggregate")
                         for encoding in blocks for d in encoding.values())
        if not split and aggregated:
            return (f"All {len(rows)} rows collapse into a single mark: no channel splits them. Put "
                    f"the category (or the date) on the other axis — e.g. x = the name column, "
                    f"type nominal — and give colour a 'field' if it is meant to vary.")
    return ""


async def check_renders(spec: dict, locale: str = "fr-FR") -> None:
    """Compile and draw it once, off-screen, so a broken spec fails here, with its message,
    instead of as an empty box in front of the reader."""
    try:
        await asyncio.to_thread(_render_svg, themed(spec, width=640), locale)
    except Exception as exc:  # noqa: BLE001 - vl-convert raises plain exceptions
        text = " ".join(l.strip() for l in str(exc).splitlines() if l.strip())
        # Keep the error, drop the JavaScript stack frames after it.
        message = re.split(r"\s+at\s+\S*(?:https?://|\()", text)[0][:400] or type(exc).__name__
        raise ChartError(f"Vega-Lite could not draw this spec: {message}") from exc


def warm_up() -> None:
    """The first render starts a JavaScript engine (~0.5s). Pay it at boot, not mid-answer."""
    try:
        _render_svg({"data": {"values": [{"a": 1}]}, "mark": "point",
                     "encoding": {"x": {"field": "a", "type": "quantitative"}}})
    except Exception:  # noqa: BLE001 - a cold chart is slower, not broken
        pass


def describe(spec: dict, rows: list[dict]) -> str:
    """One line a model can check its work against."""
    def marks(node: Any) -> list[str]:
        if isinstance(node, dict):
            mark = node.get("mark")
            found = [mark.get("type") if isinstance(mark, dict) else mark] if mark else []
            for key in ("layer", "hconcat", "vconcat", "concat"):
                for child in node.get(key) or []:
                    found += marks(child)
            if isinstance(node.get("spec"), dict):
                found += marks(node["spec"])
            return [m for m in found if m]
        return []
    kinds = ", ".join(dict.fromkeys(marks(spec))) or "chart"
    return f"{kinds} over {len(rows)} row(s)"


# ------------------------------------------------------------------- storage

class ChartStore:
    """Charts per conversation, every version kept, under the workspace's `.charts/`.

    Versions rather than overwrites: "go back to the bar version" is a request people make,
    and the PDF of last week's answer should still show last week's chart.
    """

    def __init__(self, workspace: Path) -> None:
        self.root = workspace / ".charts"

    def _dir(self, conversation_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", conversation_id or "none")
        return self.root / safe

    def _path(self, conversation_id: str, chart_id: str) -> Path:
        return self._dir(conversation_id) / f"{re.sub(r'[^A-Za-z0-9_-]', '_', chart_id)}.json"

    def next_id(self, conversation_id: str) -> str:
        folder = self._dir(conversation_id)
        taken = {p.stem for p in folder.glob("c*.json")} if folder.exists() else set()
        n = 1
        while f"c{n}" in taken:
            n += 1
        return f"c{n}"

    def save(self, conversation_id: str, chart_id: str, spec: dict, source: str) -> dict:
        path = self._path(conversation_id, chart_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = json.loads(path.read_text()) if path.exists() else {"id": chart_id, "versions": []}
        version = {"version": len(record["versions"]) + 1, "spec": spec, "source": source,
                   "created_at": time.time()}
        record["versions"].append(version)
        path.write_text(json.dumps(record, ensure_ascii=False, default=str))
        return self._view(chart_id, version)

    def latest(self, conversation_id: str, chart_id: str) -> dict | None:
        path = self._path(conversation_id, chart_id.strip())
        if not path.exists():
            return None
        record = json.loads(path.read_text())
        return self._view(record["id"], record["versions"][-1]) if record["versions"] else None

    def all_latest(self, conversation_id: str) -> list[dict]:
        folder = self._dir(conversation_id)
        if not folder.exists():
            return []
        out = []
        for path in sorted(folder.glob("c*.json"), key=lambda p: int(p.stem[1:] or 0)
                           if p.stem[1:].isdigit() else 0):
            chart = self.latest(conversation_id, path.stem)
            if chart:
                out.append(chart)
        return out

    @staticmethod
    def _view(chart_id: str, version: dict) -> dict:
        spec = version["spec"]
        title = spec.get("title")
        return {"id": chart_id, "version": version["version"], "spec": spec,
                "data": (spec.get("data") or {}).get("values") or [],
                "title": (title.get("text") if isinstance(title, dict) else title) or chart_id,
                "source": version.get("source", "")}


COOKBOOK = """Vega-Lite idioms (field names must be columns of the data):
- bar, sorted: {"mark":"bar","encoding":{"x":{"field":"region","type":"nominal","sort":"-y"},"y":{"field":"revenue","type":"quantitative"}}}
- horizontal bar: swap x and y; long category labels read better this way.
- stacked bar: add "color":{"field":"channel","type":"nominal"}; grouped instead: also "xOffset":{"field":"channel"}; 100% stacked: y "stack":"normalize".
- line over time: {"mark":{"type":"line","point":true},"encoding":{"x":{"field":"month","type":"temporal","timeUnit":"yearmonth"},"y":{"field":"revenue","type":"quantitative"},"color":{"field":"channel","type":"nominal"}}}
- area: "mark":"area"; stacked area with a color field.
- scatter: {"mark":"point","encoding":{"x":{...quantitative},"y":{...quantitative},"size":{...},"color":{...}}}; trend line: layer a line with "transform":[{"regression":"y_field","on":"x_field"}].
- pie / donut: {"mark":{"type":"arc","innerRadius":60},"encoding":{"theta":{"field":"revenue","type":"quantitative"},"color":{"field":"region","type":"nominal"}}}
- heatmap: {"mark":"rect","encoding":{"x":{...nominal},"y":{...nominal},"color":{"field":"value","type":"quantitative"}}}
- bar + line, two axes: {"layer":[{"mark":"bar","encoding":{...}},{"mark":"line","encoding":{...}}],"resolve":{"scale":{"y":"independent"}}}
- value labels: {"layer":[{"mark":"bar"},{"mark":{"type":"text","dy":-6},"encoding":{"text":{"field":"revenue","type":"quantitative","format":",.0f"}}}],"encoding":{shared x and y}}
- small multiples: add "facet":{"field":"region","type":"nominal","columns":3} with the chart under "spec".
- target line: layer {"mark":"rule","encoding":{"y":{"datum":95000}}}.
- aggregate inside the chart: "transform":[{"aggregate":[{"op":"sum","field":"amount","as":"total"}],"groupby":["region"]}].
- formats: axis {"format":",.0f"} or {"format":"~s"}; currency labels via "title":"Revenue (EUR)"."""
