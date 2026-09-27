"""Figures written in prose, checked against the figures the tools actually returned.

A report is prose around data, and the prose is the model's. In testing, a 4B model wrote
"Credit has the highest utilisation at 89.3 %" above a table that said 65.03 — a number
that existed nowhere, in a document made to be forwarded. The table was right; the sentence
was invented; nothing stood between the two.

The check is deliberately narrow: only what reads as a *figure* — a number with decimals,
or one of four digits or more that is not a year — must be found among the numbers the
conversation's results contain, allowing for rounding, for a ratio written as a percentage,
and for thousands or millions written short. Counts, days and confidence levels ("99 %")
are left alone: they are as often knowledge as data, and a check that cries wolf is one
the model learns to talk past.
"""

from __future__ import annotations

import math
import re

# 1 800 909 · 1,800,909 · 1.800.909 · 72,04 · 72.04 · -3.5 — with an optional % after.
_FIGURE = re.compile(
    r"(?<![\w#.,/-])([-+−]?\d{1,3}(?:(?:[   ]\d{3})+|(?:,\d{3})+|(?:\.\d{3}){2,})"
    r"(?:[.,]\d+)?|[-+−]?\d+(?:[.,]\d+)?)(?![\w/-]|\.\d)")
_ANY_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")
_CITATION = re.compile(r"[\[【]#\d+[\]】]")


def _parse(raw: str) -> tuple[float, int] | None:
    """(value, decimals) of a figure as written, in either convention."""
    text = raw.replace("−", "-").replace(" ", " ").replace(" ", " ")
    text = text.replace(" ", "")
    if text.count(",") >= 1 and text.count(".") == 0:
        head, _, tail = text.rpartition(",")
        if len(tail) == 3 and text.count(",") >= 1 and re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+", text):
            text = text.replace(",", "")          # 1,800,909
        else:
            text = text.replace(",", ".")          # 72,04
    elif text.count(".") >= 2:
        text = text.replace(".", "")              # 1.800.909
    elif "," in text and "." in text:
        text = text.replace(",", "")              # 1,800,909.50
    try:
        value = float(text)
    except ValueError:
        return None
    decimals = len(text.split(".", 1)[1]) if "." in text else 0
    return value, decimals


def figures_in(text: str) -> list[tuple[str, float, int]]:
    """The figures a reader would check: decimals, or four digits and more that are not years."""
    clean = _CITATION.sub(" ", _DATE.sub(" ", text or ""))
    out = []
    for match in _FIGURE.finditer(clean):
        raw = match.group(1)
        parsed = _parse(raw)
        if parsed is None:
            continue
        value, decimals = parsed
        if decimals == 0 and (abs(value) < 1000 or (1900 <= value <= 2100 and float(value).is_integer())):
            continue
        out.append((raw.strip(), value, decimals))
    return out


def evidence_numbers(texts: list[str]) -> list[float]:
    found: set[float] = set()
    for text in texts:
        for match in _ANY_NUMBER.finditer(text or ""):
            try:
                found.add(float(match.group(0)))
            except ValueError:
                continue
    return sorted(found)


def _matches(value: float, decimals: int, evidence: list[float]) -> bool:
    tolerance = 0.5 * 10 ** (-decimals) + 1e-9
    for candidate in evidence:
        for scaled in (candidate, candidate * 100, candidate / 1e3, candidate / 1e6, candidate / 1e9):
            if math.isfinite(scaled) and abs(abs(scaled) - abs(value)) <= tolerance:
                return True
    return False


def ungrounded(text: str, evidence: list[float]) -> list[str]:
    """The figures in `text` that no result of the conversation contains."""
    missing = []
    for raw, value, decimals in figures_in(text):
        if not _matches(value, decimals, evidence) and raw not in missing:
            missing.append(raw)
    return missing
