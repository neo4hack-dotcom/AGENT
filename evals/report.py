"""Compare evaluation runs side by side.

    python evals/report.py bare prepared

One line per scenario — pass or fail, seconds, tool calls — then the totals. The point of
a second label is the difference: the same questions, the same model, the same data, with
and without the sources prepared.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(label: str) -> dict:
    path = HERE / "results" / f"{label}.json"
    if not path.exists():
        raise SystemExit(f"No results for '{label}' — run evals/run.py --label {label} first.")
    return json.loads(path.read_text())


def main() -> None:
    labels = sys.argv[1:] or ["prepared"]
    runs = {label: load(label) for label in labels}
    ids = sorted({sid for run in runs.values() for sid in run}, key=lambda s: int(s[1:]))
    header = f"{'':5s}{'scenario':22s}" + "".join(f"{label:>26s}" for label in labels)
    print(header)
    print("-" * len(header))
    for sid in ids:
        label_of = next(run[sid]["label"] for run in runs.values() if sid in run)
        cells = []
        for label in labels:
            r = runs[label].get(sid)
            cells.append(f"{'—':>26s}" if r is None else
                         f"{r.get('pass_count', int(r['passed']))}/{r.get('runs', 1)} "
                         f"{r['seconds']:6.0f}s {r['tool_calls']:5.1f} calls")
        print(f"{sid:5s}{label_of[:22]:22s}" + "".join(f"{c:>26s}" for c in cells))
    print("-" * len(header))
    totals = []
    for label in labels:
        run = runs[label]
        wins = sum(r.get("pass_count", int(r["passed"])) for r in run.values())
        runs_ = sum(r.get("runs", 1) for r in run.values())
        seconds = sum(r["seconds"] for r in run.values())
        calls = sum(r["tool_calls"] for r in run.values())
        failed = sum(r.get("failed_calls", 0) for r in run.values())
        totals.append(f"{100 * wins / max(1, runs_):3.0f}% {seconds:5.0f}s {calls:5.0f} calls")
        print(f"{label}: {wins}/{runs_} runs passed ({100 * wins / max(1, runs_):.0f}%) · "
              f"{seconds:.0f}s per suite · {calls:.0f} tool calls ({failed:.0f} failed) · "
              f"{sum(r.get('llm_calls', 0) for r in run.values()):.0f} model turns")
    print(f"{'':5s}{'total':22s}" + "".join(f"{t:>26s}" for t in totals))


if __name__ == "__main__":
    main()
