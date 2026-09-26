"""Run the data-analyst scenarios against a running instance and grade them.

Each scenario is a question a data analyst would ask, sometimes followed by a second turn
in the same conversation. The grader checks the answer against ground truth computed by
evals/testbed.py — numbers to the cent, the right chart, the right file, a question asked
when one had to be — and records how long it took and how many calls it spent.

    python evals/testbed.py                 # fresh data and ground truth
    python evals/setup_sources.py           # prepared sources (or --bare for none)
    python evals/run.py [A1 A4 ...] [--label prepared]

Results land in evals/results/<label>.json; evals/report.py compares two labels.
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = "http://localhost:3047"
TRUTH = json.loads(Path("/tmp/agent-testbed/ground-truth.json").read_text()) \
    if Path("/tmp/agent-testbed/ground-truth.json").exists() else {}


# ------------------------------------------------------------------- driving

def _post(path: str, body: dict) -> dict:
    request = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def _get(path: str) -> dict | list:
    with urllib.request.urlopen(BASE + path, timeout=60) as response:
        return json.load(response)


def ask(question: str, conversation_id: str = "", answers: list[str] | None = None) -> dict:
    """One turn. Clarifying questions get the next scripted answer (or the first option);
    approvals are granted, as a person at the keyboard would."""
    started = _post("/api/chat", {"conversation_id": conversation_id, "text": question})
    run = started["run_id"]
    scripted = list(answers or [])
    pending: list[tuple[str, str, list[str]]] = []
    asked: list[dict] = []

    def responder() -> None:
        seen: set[str] = set()
        while True:
            time.sleep(0.25)
            for kind, call_id, options in list(pending):
                if call_id in seen:
                    continue
                seen.add(call_id)
                time.sleep(0.4)
                try:
                    if kind == "ask":
                        answer = scripted.pop(0) if scripted else (options[0] if options else "yes")
                        _post(f"/api/runs/{run}/answer", {"call_id": call_id, "answer": answer})
                    else:
                        _post(f"/api/runs/{run}/approve", {"call_id": call_id, "approved": True})
                except Exception:  # noqa: BLE001 - the run moved on
                    pass

    threading.Thread(target=responder, daemon=True).start()
    t0 = time.time()
    calls, charts, files, status, usage = [], [], [], "?", {}
    with urllib.request.urlopen(f"{BASE}/api/runs/{run}/stream?since=0", timeout=1500) as stream:
        for raw in stream:
            line = raw.decode().strip()
            if not line.startswith("data:"):
                continue
            event = json.loads(line[5:])
            kind = event["type"]
            if kind == "tool.start":
                calls.append({"name": event.get("name"), "args": event.get("args"), "ok": None})
            elif kind == "tool.end":
                for call in reversed(calls):
                    if call["ok"] is None:
                        call["ok"] = event.get("ok")
                        break
                if event.get("chart"):
                    charts.append(event["chart"])
                if event.get("file"):
                    files.append(event["file"])
            elif kind == "ask.request":
                asked.append({"question": event.get("question"), "options": event.get("options")})
                pending.append(("ask", event["call_id"], event.get("options") or []))
            elif kind == "approval.request":
                pending.append(("approve", event["call_id"], []))
            elif kind == "done":
                status, usage = event.get("status", "?"), event.get("usage") or {}
                break
    conv = _get(f"/api/conversations/{started['conversation_id']}")
    message = conv["messages"][-1]
    answer = "\n".join(b.get("text", "") for b in message.get("blocks") or []
                       if b["type"] == "text" and not b.get("superseded"))
    return {"conversation_id": started["conversation_id"], "status": status,
            "seconds": round(time.time() - t0, 1), "calls": calls, "charts": charts,
            "files": files, "asked": asked, "answer": answer,
            "llm_calls": usage.get("llm_calls"), "tool_calls": usage.get("tool_calls"),
            "failed_calls": sum(1 for c in calls if c["ok"] is False)}


# ------------------------------------------------------------------- grading

def _flat(text: str) -> str:
    return re.sub(r"[\s  ']", "", text or "")


def has_number(text: str, value: float, decimals: int = 2) -> bool:
    """82996.15 in any of the ways people and models write it: 82 996,15 · 82,996.15 · 82996.15,
    or rounded to the unit (82 996 €) — rounding a figure for reading is not an error."""
    if decimals and (_has_number(text, round(value), 0) or _has_number(text, int(value), 0)):
        return True
    return _has_number(text, value, decimals)


def _has_number(text: str, value: float, decimals: int) -> bool:
    flat = _flat(text)
    whole, frac = f"{abs(value):.{decimals}f}".split(".") if decimals else (f"{abs(value):.0f}", "")
    grouped = f"{int(whole):,}"
    forms = {whole, grouped, grouped.replace(",", ".")}
    if frac:
        forms = {f"{w}.{frac}" for w in (whole, grouped)} | {f"{w},{frac}" for w in (whole, grouped.replace(",", "."))}
        if frac.endswith("0"):
            forms |= {f.rstrip("0") for f in list(forms)}
    return any(form in flat for form in forms)


def chart_marks(chart: dict) -> set[str]:
    found: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            mark = node.get("mark")
            if mark:
                found.add(mark.get("type") if isinstance(mark, dict) else mark)
            for key in ("layer", "hconcat", "vconcat", "concat"):
                for child in node.get(key) or []:
                    walk(child)
            if isinstance(node.get("spec"), dict):
                walk(node["spec"])
    walk(chart.get("spec") or {})
    return found


def chart_rows(chart: dict) -> list[dict]:
    return chart.get("data") or ((chart.get("spec") or {}).get("data") or {}).get("values") or []


def measure_sum(rows: list[dict]) -> float:
    """Sum of the chart's measure: the numeric column that is not a key or a count."""
    if not rows:
        return 0.0
    numeric = [k for k in rows[0] if isinstance(rows[0][k], (int, float))
               and not re.search(r"(^|_)id$|count|orders|nb_|year", k, re.I)]
    key = next((k for k in numeric if re.search(r"rev|ca|amount|total|sales|montant", k, re.I)),
               numeric[0] if numeric else None)
    return sum(float(r.get(key) or 0) for r in rows) if key else 0.0


def check(cond: bool, label: str, notes: list[str]) -> bool:
    notes.append(("✓ " if cond else "✗ ") + label)
    return cond


def grade(sid: str, turns: list[dict]) -> tuple[bool, list[str]]:
    t = TRUTH
    notes: list[str] = []
    last = turns[-1]
    a = re.sub(r"[\u00a0\u202f\u2009]", " ", last["answer"] or "")
    ok = True
    if sid == "A1":
        ok &= check("DE" in a or "Allemagne" in a or "Germany" in a, "DE named below target", notes)
        de = t["shipped_by_region"]["DE"]
        # The question asks which regions and by how much: the shortfall is the answer, the
        # revenue behind it is welcome but not required.
        ok &= check(has_number(a, 95000 - de), f"shortfall {95000 - de:,.2f}", notes)
        others = [r for r in ("FR", "ES", "IT", "UK") if re.search(rf"\b{r}\b[^\n|]*\b(sous|below|manque|déficit)", a)]
        ok &= check(not others, "no other region wrongly flagged", notes)
    elif sid == "A2":
        charts = last["charts"]
        ok &= check(bool(charts), "a chart was drawn", notes)
        if charts:
            rows = chart_rows(charts[-1])
            months = {str(r.get("month") or r.get("mois") or "")[:7] for r in rows}
            ok &= check(len([m for m in months if m.startswith("2026-0")]) >= 6, "six months in the data", notes)
            total = measure_sum(rows)
            expected = sum(t["monthly_shipped"].values())
            ok &= check(abs(total - expected) < 1, f"chart totals {total:,.2f} vs {expected:,.2f}", notes)
            ok &= check(any("channel" in json.dumps(charts[-1]["spec"].get("encoding", {})) or
                            "channel" in json.dumps(charts[-1]["spec"]) for _ in [0]), "split by channel", notes)
    elif sid == "A3":
        first, second = turns[0]["charts"], last["charts"]
        ok &= check(bool(second), "chart revised", notes)
        if first and second:
            ok &= check(second[-1]["id"] == first[-1]["id"], f"same chart id ({second[-1]['id']})", notes)
            ok &= check(second[-1].get("version", 1) >= 2, f"version {second[-1].get('version')}", notes)
            ok &= check("bar" in chart_marks(second[-1]), "now bars", notes)
            title = json.dumps(second[-1]["spec"].get("title", ""), ensure_ascii=False).lower()
            ok &= check("avril" in title or "april" in title or "04" in title, "title names the weakest month", notes)
    elif sid == "A4":
        ok &= check("Trade Fair Milan" in a, "top revenue campaign: Trade Fair Milan", notes)
        ok &= check("Spring Newsletter" in a, "best ROI: Spring Newsletter", notes)
        top = t["campaign_revenue"][0]
        ok &= check(has_number(a, top["revenue"]), f"its revenue {top['revenue']:,.2f}", notes)
        names = {c["name"].split("__")[0] for c in last["calls"]}
        ok &= check({"analytics_db", "crm"} <= names, "both servers queried", notes)
    elif sid == "A5":
        top = t["manager_revenue"][0]
        ok &= check(top["manager"] in a, f"top manager {top['manager']}", notes)
        ok &= check(has_number(a, top["revenue"]), f"{top['revenue']:,.2f}", notes)
        ok &= check(bool(last["charts"]), "chart drawn", notes)
        if last["charts"]:
            rows = chart_rows(last["charts"][-1])
            total = measure_sum(rows)
            expected = sum(m["revenue"] for m in t["manager_revenue"])
            ok &= check(abs(total - expected) < 1, f"chart data sums to {expected:,.2f}", notes)
    elif sid == "A6":
        ok &= check(bool(turns[0]["asked"]), "asked which Kerner", notes)
        if turns[0]["asked"]:
            options = " ".join(turns[0]["asked"][0].get("options") or [])
            ok &= check(sum(1 for s in ("SARL", "GmbH", "SL", "SpA") if s in options) >= 3,
                        "options list the Kerner companies", notes)
        files = [f for turn in turns for f in turn["files"]]
        ok &= check(bool(files), "file produced", notes)
        if files:
            f = files[-1]
            ok &= check(f.get("format") == "xlsx", "Excel format", notes)
            ok &= check(f.get("rows") in (9, 10), f"{f.get('rows')} rows (Kerner GmbH has 10, 9 shipped)", notes)
    elif sid == "A7":
        files = last["files"]
        pdf = [f for f in files if f.get("format") == "pdf"]
        ok &= check(bool(pdf), "PDF produced", notes)
        ok &= check(bool(last["charts"]) or any(t2["charts"] for t2 in turns), "a chart drawn for it", notes)
        if pdf:
            path = Path(__file__).resolve().parents[1] / "backend" / "data" / "workspace" / pdf[-1]["path"]
            ok &= check(path.exists() and path.stat().st_size > 20_000, f"{pdf[-1]['pages']} page(s), {pdf[-1]['bytes']:,} bytes", notes)
        ok &= check(has_number(a, t["shipped_by_region"]["DE"]) or "DE" in a, "answer names DE", notes)
    elif sid == "A8":
        for key, label in (("duplicat|doublon|double", "duplicates"),
                           ("orphel|n'existe|inexist|sans client|no customer|match|inconnu|correspond à aucun|999", "orphans"),
                           ("négati|negative", "negatives"), ("région|region", "missing region")):
            ok &= check(bool(re.search(key, a, re.I)), label + " mentioned", notes)
        ok &= check((last["tool_calls"] or 0) <= 6, f"{last['tool_calls']} tool calls (notes should make it quick)", notes)
    elif sid == "A9":
        for segment, value in t["revenue_q1_by_segment"].items():
            ok &= check(has_number(a, value), f"{segment} {value:,.2f}", notes)
        ok &= check((last["tool_calls"] or 0) <= 3, f"{last['tool_calls']} tool call(s)", notes)
    elif sid == "A10":
        ok &= check(not has_number(a, t["gross_all_rows"]) or bool(re.search(r"non|pas|not|no\b", a, re.I)),
                    "does not simply agree with the raw sum", notes)
        ok &= check(has_number(a, t["revenue_total"]), f"gives revenue {t['revenue_total']:,.2f}", notes)
    elif sid == "A11":
        ok &= check(bool(re.search(r"T3|Q3|juillet|July", a)), "addresses Q3", notes)
        ok &= check(bool(re.search(r"aucun|pas de|no data|ne contient|n'existe|absent|vide|0 ?€|zéro|only|seul", a, re.I)),
                    "says Q3 has no data", notes)
    elif sid == "A12":
        ok &= check(bool(last["charts"]), "chart drawn", notes)
        if last["charts"]:
            marks = chart_marks(last["charts"][-1])
            ok &= check(bool(marks & {"arc", "bar"}), f"marks {sorted(marks)}", notes)
            total = measure_sum(chart_rows(last["charts"][-1]))
            ok &= check(abs(total - t["revenue_total"]) < 1, f"shares sum to {t['revenue_total']:,.2f}", notes)
    return ok, notes


SCENARIOS: list[tuple[str, str, list[str], list[str]]] = [
    # id, label, turns, scripted answers to clarifying questions
    ("A1", "sources + targets", ["Quelles régions sont sous leur objectif H1 2026 (regional-targets-2026.csv dans le workspace), et de combien ?"], []),
    ("A2", "trend chart", ["Montre-moi l'évolution mensuelle du CA par canal."], []),
    ("A3", "chart revision", ["Montre-moi l'évolution mensuelle du CA par canal.",
                              "Passe ce graphique en barres empilées, et mets dans le titre quel mois est le plus faible."], []),
    ("A4", "two servers joined", ["Quelle campagne marketing a généré le plus de CA, et laquelle a le meilleur ROI ?"], []),
    ("A5", "two servers + chart", ["Quel CA a été apporté par chaque account manager ? Fais un graphique."], []),
    ("A6", "clarify, then export", ["Donne-moi un extract Excel des commandes du client Kerner."], ["Kerner GmbH"]),
    ("A7", "PDF report", ["Fais-moi un rapport PDF sur la performance régionale H1 2026 : CA par région comparé aux objectifs (regional-targets-2026.csv), avec un graphique."], []),
    ("A8", "data quality", ["Peut-on se fier au CA de la base Sales ? Qu'est-ce qui pourrait le fausser ?"], []),
    ("A9", "fast and exact", ["CA du T1 2026 par segment client ?"], []),
    ("A10", "leading question", ["Le CA total, c'est bien la somme de amount_eur dans orders, non ?"], []),
    ("A11", "missing period", ["Compare le CA du T3 2026 à celui du T1 2026."], []),
    ("A12", "share chart", ["Quelle est la répartition du CA entre les canaux ? Montre-la en graphique."],
     ["Les canaux de vente (web, partner, direct)"]),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("ids", nargs="*")
    parser.add_argument("--label", default="prepared")
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--repeat", type=int, default=1,
                        help="Run each scenario N times: one run of a sampling model is an anecdote.")
    args = parser.parse_args()
    globals()["BASE"] = args.base
    out_path = HERE / "results" / f"{args.label}.json"
    out_path.parent.mkdir(exist_ok=True)
    results = json.loads(out_path.read_text()) if out_path.exists() else {}
    for sid, label, questions, answers in SCENARIOS:
        if args.ids and sid not in args.ids:
            continue
        print(f"▶ {sid} {label}", flush=True)
        attempts = []
        for _ in range(max(1, args.repeat)):
            turns, conversation = [], ""
            for question in questions:
                try:
                    turn = ask(question, conversation, answers)
                except Exception as exc:  # noqa: BLE001 - one broken run must not stop the suite
                    turn = {"status": f"harness error: {exc}", "answer": "", "calls": [], "charts": [],
                            "files": [], "asked": [], "seconds": 0, "tool_calls": 0, "llm_calls": 0,
                            "failed_calls": 0, "conversation_id": conversation}
                conversation = turn["conversation_id"]
                turns.append(turn)
            passed, notes = grade(sid, turns)
            attempts.append({"passed": passed, "notes": notes, "turns": turns,
                             "seconds": sum(t["seconds"] for t in turns),
                             "tool_calls": sum(t["tool_calls"] or 0 for t in turns),
                             "failed_calls": sum(t["failed_calls"] for t in turns),
                             "llm_calls": sum(t["llm_calls"] or 0 for t in turns)})
            print(f"  {'PASS' if passed else 'FAIL'} · {attempts[-1]['seconds']:.0f}s · "
                  f"{attempts[-1]['tool_calls']} calls · " + " | ".join(notes), flush=True)
        n = len(attempts)
        results[sid] = {"label": label, "runs": n, "pass_count": sum(a["passed"] for a in attempts),
                        "passed": sum(a["passed"] for a in attempts) * 2 > n
                                  or (n == 1 and attempts[0]["passed"]),
                        "notes": attempts[-1]["notes"],
                        "seconds": round(sum(a["seconds"] for a in attempts) / n, 1),
                        "tool_calls": round(sum(a["tool_calls"] for a in attempts) / n, 1),
                        "failed_calls": round(sum(a["failed_calls"] for a in attempts) / n, 1),
                        "llm_calls": round(sum(a["llm_calls"] for a in attempts) / n, 1),
                        "attempts": attempts}
        out_path.write_text(json.dumps(results, indent=1, ensure_ascii=False, default=str))
    done = [r for r in results.values()]
    runs = sum(r.get("runs", 1) for r in done)
    wins = sum(r.get("pass_count", int(r["passed"])) for r in done)
    print(f"\n{wins}/{runs} runs passed ({100 * wins / max(1, runs):.0f}%) · "
          f"{sum(r['seconds'] for r in done):.0f}s per suite · "
          f"{sum(r['tool_calls'] for r in done):.0f} tool calls per suite")


if __name__ == "__main__":
    main()
