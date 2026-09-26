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

_MONTH = re.compile(r"^\d{4}-\d{2}$")
_QUARTER = re.compile(r"^\d{4}-?Q[1-4]$", re.I)


def polish(spec: dict, rows: list[dict]) -> dict:
    """Small corrections a careful analyst would make, applied so the model need not.

    "2026-01" is a month. Encoded as a plain temporal field it is parsed as the first of
    the month and ticked every fortnight — "Jan 04, Jan 18, February…" — which reads as
    noise. So a field whose values are all year-months gets a yearmonth time unit, and on
    bars an ordinal scale, so each month is one clean band with one clean label.
    """
    samples: dict[str, list] = {}
    for row in rows[:200]:
        for key, value in row.items():
            samples.setdefault(key, []).append(value)

    def monthly(field: str) -> bool:
        values = [v for v in samples.get(field, []) if v is not None]
        return bool(values) and all(isinstance(v, str) and _MONTH.match(v) for v in values)

    def walk(node: Any, mark: str) -> None:
        if not isinstance(node, dict):
            return
        own = node.get("mark")
        kind = (own.get("type") if isinstance(own, dict) else own) or mark
        if not own and node.get("layer"):
            # A shared encoding above layers serves every layer; if any of them is a bar,
            # the axis must be bands, or the bars slide off the points drawn on them.
            kinds = [(l.get("mark", {}).get("type") if isinstance(l.get("mark"), dict)
                      else l.get("mark")) for l in node["layer"] if isinstance(l, dict)]
            kind = "bar" if any(k in ("bar", "rect", "boxplot") for k in kinds) else (kinds[0] if kinds else kind)
        if isinstance(own, dict) and own.get("type") in ("line", "area") and own.get("color") \
                and own.get("point") is True:
            # The points on a coloured line otherwise take the theme's default colour.
            own["point"] = {"color": own["color"], "filled": True}
        encoding = node.get("encoding")
        if isinstance(encoding, dict):
            for channel in ("x", "y", "x2", "y2", "color", "column", "row", "xOffset"):
                spec_ = encoding.get(channel)
                if not isinstance(spec_, dict) or not isinstance(spec_.get("field"), str):
                    continue
                if not monthly(spec_["field"]) or spec_.get("timeUnit"):
                    continue
                if spec_.get("type") in ("temporal", "ordinal", None):
                    spec_["timeUnit"] = "yearmonth"
                    # Otherwise Vega titles the axis "month (year-month)".
                    spec_.setdefault("title", spec_["field"].replace("_", " ").capitalize())
                    if kind in ("bar", "rect", "boxplot") and channel in ("x", "y"):
                        spec_["type"] = "ordinal"
                    else:
                        spec_["type"] = "temporal"
                    axis = spec_.setdefault("axis", {}) if channel in ("x", "y") else None
                    if isinstance(axis, dict):
                        axis.setdefault("format", "%b %Y")
                        if spec_["type"] == "temporal":
                            axis.setdefault("tickCount", {"interval": "month", "step": 1})
        for key in ("layer", "hconcat", "vconcat", "concat"):
            for child in node.get(key) or []:
                walk(child, kind)
        if isinstance(node.get("spec"), dict):
            walk(node["spec"], kind)

    walk(spec, "")
    return spec


def assemble(spec: dict, rows: list[dict], title: str = "", subtitle: str = "") -> dict:
    """The stored chart: the model's spec, the real rows, a title — and no theme.

    Theme is applied when drawn, so the same chart follows the reader into dark mode on
    screen and prints on white in the PDF.
    """
    full = polish(sanitize(spec), rows)
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


def _render_svg(spec: dict) -> str:
    import vl_convert as vlc
    return vlc.vegalite_to_svg(spec)


def render_png(spec: dict, width: int = 900, scale: float = 2.0) -> bytes:
    import vl_convert as vlc
    return vlc.vegalite_to_png(themed(spec, width=width), scale=scale)


async def check_renders(spec: dict) -> None:
    """Compile and draw it once, off-screen, so a broken spec fails here, with its message,
    instead of as an empty box in front of the reader."""
    try:
        await asyncio.to_thread(_render_svg, themed(spec, width=640))
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
