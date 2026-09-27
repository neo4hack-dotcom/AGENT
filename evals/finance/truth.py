"""Ground truth for the CIB scenarios, computed from the generated world itself."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def compute(root: Path = Path("/tmp/agent-finance")) -> dict:
    world = json.loads((root / "finance.json").read_text())
    con = sqlite3.connect(root / "trades.db")
    con.row_factory = sqlite3.Row
    ref = world["refdata"]
    bonds = {b["isin"]: b for b in ref["bonds"]}
    equities = {e["isin"]: e for e in ref["equities"]}
    scale = ref["rating_scale"]
    fx = world["fx"]
    px = world["prices"]
    t: dict = {}

    def to_eur(amount: float, ccy: str, day: str) -> float:
        if ccy == "EUR":
            return amount
        return amount / fx[f"EUR{ccy}"][day]

    # F1 — Credit desk, bonds rated BBB+ or lower, market value in EUR at 2026-06-30.
    day = "2026-06-30"
    rows = con.execute("SELECT p.book_id, p.instrument_id, p.quantity FROM positions_eod p "
                       "JOIN books b ON b.book_id = p.book_id WHERE b.desk = 'Credit' "
                       "AND p.asof_date = ?", (day,)).fetchall()
    total, lines = 0.0, []
    for r in rows:
        bond = bonds.get(r["instrument_id"])
        if not bond or scale.index(bond["rating"]) < scale.index("BBB+"):
            continue
        mv = to_eur(r["quantity"] * px[bond["isin"]][day] / 100, bond["currency"], day)
        total += mv
        lines.append((bond["issuer"], bond["rating"], round(mv, 2)))
    t["F1"] = {"total_eur": round(total, 2), "lines": lines}

    # F2 — VaR limit usage at end of May (2026-05-29, the last business day).
    var = world["risk"]["var_99_1d_eur"]["2026-05-29"]
    limits = world["risk"]["var_limits_eur"]
    usage = {d: var[d] / limits[d] for d in var}
    top = max(usage, key=usage.get)
    t["F2"] = {"desk": top, "usage": round(usage[top] * 100, 1), "var": var[top], "limit": limits[top]}

    # F3 — top 3 counterparties by bond notional traded in Q1 2026, in EUR at trade-date fixing.
    trades = con.execute("""
        SELECT t.* FROM trades t JOIN (SELECT trade_id, MAX(version) v FROM trades GROUP BY trade_id) l
          ON l.trade_id = t.trade_id AND l.v = t.version
        WHERE t.status != 'CANCELLED' AND t.trade_date BETWEEN '2026-01-01' AND '2026-03-31'""").fetchall()
    by_cp: dict[str, float] = {}
    for tr in trades:
        if tr["instrument_id"] not in bonds:
            continue
        by_cp[tr["counterparty_id"]] = by_cp.get(tr["counterparty_id"], 0) + to_eur(
            tr["quantity"], tr["currency"], tr["trade_date"])
    names = {c["counterparty_id"]: c["name"] for c in ref["counterparties"]}
    ranked = sorted(by_cp.items(), key=lambda kv: -kv[1])[:3]
    t["F3"] = [{"name": names[cp], "notional_eur": round(v, 2)} for cp, v in ranked]

    # F4 — TotalEnergies close on 2026-05-01 (holiday): none; last close 2026-04-30.
    tte = next(e for e in ref["equities"] if e["ticker"] == "TTE FP")
    t["F4"] = {"last_close": px[tte["isin"]]["2026-04-30"], "last_date": "2026-04-30"}

    # F5 — Rates desk DV01 at 2026-03-31.
    dv01 = world["risk"]["dv01_by_book"]["2026-03-31"]
    t["F5"] = {"dv01": round(dv01["RAT-EUR"] + dv01["RAT-USD"], 2)}

    # F7 — EQC-EU equities held at 2026-06-30 down more than 10% from 2026-01-02.
    held = [r["instrument_id"] for r in con.execute(
        "SELECT instrument_id FROM positions_eod WHERE book_id='EQC-EU' AND asof_date='2026-06-30'")]
    losers = []
    for isin in held:
        if isin in equities:
            change = px[isin]["2026-06-30"] / px[isin]["2026-01-02"] - 1
            if change < -0.10:
                losers.append((equities[isin]["name"], round(change * 100, 1)))
    t["F7"] = losers

    # F9 — the Rates desk's trading positions at the last date.
    t["F9"] = [r["instrument_id"] for r in con.execute(
        "SELECT p.instrument_id FROM positions_eod p JOIN books b ON b.book_id=p.book_id "
        "WHERE b.desk='Rates' AND p.asof_date='2026-06-30'")]

    # F10 — did Credit stay within its VaR limit at every month end?
    breaches = [d for d, v in world["risk"]["var_99_1d_eur"].items() if v["Credit"] > limits["Credit"]]
    t["F10"] = {"breaches": breaches, "limit": limits["Credit"]}
    return t


if __name__ == "__main__":
    print(json.dumps(compute(), indent=1, ensure_ascii=False))
