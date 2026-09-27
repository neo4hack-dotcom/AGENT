"""A data-quality and shape profile of any result, computed here rather than by the model.

"Check the quality of this data" asks for the same dozen measurements every time: how many
rows, nulls, distinct values, duplicates, the range of each number and date, the values far
out of line, dates on weekends or holidays, and values that exist nowhere in the reference
data they point to. Left to improvised code, the model forgets half of them or gets one
wrong; done here, every audit starts from the complete set, and the model's job is to judge.

Outliers are robust (median and MAD, not mean and standard deviation, which one fat finger
drags along with it), and are also measured within groups — a 2.5 million quantity is
ordinary for a bond nominal and absurd for a share, so it only shows up per instrument.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_KEY = re.compile(r"(^|_)(id|key|ref|code)$", re.I)
_GROUP = re.compile(r"(instrument|isin|ticker|product|asset|security|symbol)", re.I)
ROBUST_Z = 7.0


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        if value.is_integer():
            return f"{int(value):,}"
        return f"{value:,.4g}" if abs(value) < 1e6 else f"{value:,.0f}"
    return str(value)


def _outliers(values, keys) -> list[tuple[Any, Any, float]]:
    import numpy as np
    x = np.asarray(values, dtype=float)
    if len(x) < 5:
        return []
    median = float(np.median(x))
    mad = float(np.median(np.abs(x - median)))
    found = []
    for value, key in zip(x, keys):
        if mad > 0:
            score = 0.6745 * abs(value - median) / mad
        elif median != 0:
            score = abs(value / median) if abs(value / median) >= 50 else 0.0
        else:
            score = 0.0
        if score >= ROBUST_Z:
            found.append((key, value, score))
    return sorted(found, key=lambda item: -item[2])


def profile(rows: list[dict], *, key: str = "", by: str = "", reference: dict[str, list] | None = None,
            holidays: set[str] | None = None, label: str = "") -> str:
    import pandas as pd

    frame = pd.DataFrame(rows)
    lines = [f"Profile of {label or 'the rows'}: {len(frame):,} rows × {len(frame.columns)} columns."]
    if frame.empty:
        return lines[0]
    key = key if key in frame.columns else next(
        (c for c in frame.columns if _KEY.search(str(c)) and frame[c].nunique() >= 0.5 * len(frame)), "")
    keys = frame[key].tolist() if key else list(range(len(frame)))
    duplicated = int(frame.astype(str).duplicated().sum())
    if duplicated:
        lines.append(f"- {duplicated} row(s) are exact duplicates of another row.")
    if key:
        repeated = frame[key][frame[key].duplicated(keep=False)]
        if len(repeated):
            sample = ", ".join(map(str, list(dict.fromkeys(repeated.tolist()))[:6]))
            lines.append(f"- key `{key}` repeats on {len(repeated)} rows (e.g. {sample}) — versions, or duplicates?")
    by = by if by in frame.columns else next(
        (c for c in frame.columns if _GROUP.search(str(c)) and 2 <= frame[c].nunique() <= 200), "")

    lines.append("")
    lines.append("Columns:")
    for column in frame.columns:
        series = frame[column]
        nulls = int(series.isna().sum())
        head = f"- `{column}`: {series.nunique(dropna=True):,} distinct" + (f", {nulls} null" if nulls else "")
        numeric = pd.to_numeric(series, errors="coerce")
        if numeric.notna().sum() >= 0.9 * max(1, series.notna().sum()) and series.notna().any() \
                and not series.astype(str).str.match(_DATE).all():
            valid = numeric.dropna()
            head += (f"; min {_fmt(float(valid.min()))}, median {_fmt(float(valid.median()))}, "
                     f"max {_fmt(float(valid.max()))}")
            lines.append(head)
            # With groups, the whole column mixes populations (bond nominals and share counts)
            # and "overall" outliers are just the other population: only groups are judged.
            overall = [] if (by and by != column) else _outliers(valid.tolist(), [keys[i] for i in valid.index])
            for k, value, score in overall[:5]:
                lines.append(f"    · outlier overall: {key or 'row'} {k} = {_fmt(value)} (robust z {score:.0f})")
            if by and by != column:
                grouped = []
                for group, part in frame.assign(_n=numeric).groupby(by):
                    part = part.dropna(subset=["_n"])
                    for k, value, score in _outliers(part["_n"].tolist(),
                                                     (part[key] if key else part.index).tolist()):
                        grouped.append((score, group, k, value))
                for score, group, k, value in sorted(grouped, key=lambda g: -g[0])[:5]:
                    lines.append(f"    · outlier within {by} = {group}: {key or 'row'} {k} = {_fmt(value)} "
                                 f"(robust z {score:.0f} against its group)")
            continue
        text = series.dropna().astype(str)
        if len(text) and text.str.match(_DATE).mean() > 0.9:
            days = pd.to_datetime(text.str[:10], errors="coerce")
            head += f"; from {days.min().date()} to {days.max().date()}"
            lines.append(head)
            weekend = days[days.dt.weekday >= 5]
            if len(weekend):
                sample = ", ".join(f"{keys[i]} ({days[i].date()})" for i in weekend.index[:5])
                lines.append(f"    · {len(weekend)} on a weekend: {sample}")
            if holidays:
                off = text[text.str[:10].isin(holidays)]
                if len(off):
                    sample = ", ".join(f"{keys[i]} ({off[i][:10]})" for i in off.index[:5])
                    lines.append(f"    · {len(off)} on a listed holiday: {sample}")
            continue
        distinct = text.nunique()
        if distinct <= 30:
            counts = text.value_counts().head(8)
            head += "; " + ", ".join(f"{v} ×{n}" for v, n in counts.items())
        lines.append(head)
        valid_values = (reference or {}).get(column)
        if valid_values is not None:
            known = {str(v) for v in valid_values}
            missing = text[~text.isin(known)]
            if len(missing):
                values = ", ".join(sorted(set(missing))[:10])
                sample = ", ".join(str(keys[i]) for i in missing.index[:5])
                lines.append(f"    · {len(missing)} row(s) point to values absent from the reference: {values} "
                             f"(e.g. {key or 'row'} {sample})")
            else:
                lines.append("    · every value exists in the reference")
    return "\n".join(lines)
