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


def compute_mining(root: Path = Path("/tmp/agent-finance")) -> dict:
    """Ground truth for the data-mining scenarios (M1–M10)."""
    import math
    import statistics

    world = json.loads((root / "finance.json").read_text())
    con = sqlite3.connect(root / "trades.db")
    con.row_factory = sqlite3.Row
    ref = world["refdata"]
    px, fx = world["prices"], world["fx"]
    bonds = {b["isin"]: b for b in ref["bonds"]}
    equities = {e["isin"]: e for e in ref["equities"]}
    live = [dict(r) for r in con.execute("""
        SELECT t.* FROM trades t JOIN (SELECT trade_id, MAX(version) v FROM trades GROUP BY trade_id) l
          ON l.trade_id = t.trade_id AND l.v = t.version WHERE t.status != 'CANCELLED'""")]
    t: dict = {}

    # M1 — trades executed more than 3% away from that day's close.
    off = []
    for tr in live:
        close = px.get(tr["instrument_id"], {}).get(tr["trade_date"])
        if close and tr["price"]:
            gap = tr["price"] / close - 1
            if abs(gap) > 0.03:
                off.append({"trade_id": tr["trade_id"], "gap_pct": round(gap * 100, 2)})
    t["M1"] = off

    # M2 — the planted data-quality defects.
    t["M2"] = {"holiday": "T09001", "orphan": "T09002", "orphan_cp": "CP099", "fat_finger": "T09003"}

    # M3/M4 — correlation and volatility of equities over H1.
    days = world["days"]
    returns = {}
    for isin in equities:
        series = [px[isin][d] for d in days]
        returns[isin] = [series[i] / series[i - 1] - 1 for i in range(1, len(series))]

    def corr(a, b):
        ma, mb = statistics.fmean(a), statistics.fmean(b)
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        return cov / math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))

    pairs = sorted(((corr(returns[a], returns[b]), a, b) for i, a in enumerate(equities)
                    for b in list(equities)[i + 1:]), reverse=True)
    best = pairs[0]
    t["M3"] = {"pair": [equities[best[1]]["name"], equities[best[2]]["name"]], "corr": round(best[0], 3)}
    vols = sorted(((statistics.stdev(r) * math.sqrt(252), isin) for isin, r in returns.items()), reverse=True)
    t["M4"] = {"top": equities[vols[0][1]]["name"], "top_vol_pct": round(vols[0][0] * 100, 1),
               "ranking": [equities[i]["name"] for _, i in vols]}

    # M5 — trader whose tickets are most often amended or cancelled.
    rates = []
    for row in con.execute("SELECT trader, COUNT(DISTINCT trade_id) n, "
                           "COUNT(DISTINCT CASE WHEN version > 1 THEN trade_id END) changed "
                           "FROM trades GROUP BY trader"):
        rates.append((row["changed"] / row["n"], row["trader"]))
    rates.sort(reverse=True)
    t["M5"] = {"trader": rates[0][1], "rate_pct": round(rates[0][0] * 100, 1)}

    # M6 — share of long bond nominal (EUR at the 30 June fixing) held on the top 3 issuers.
    day = "2026-06-30"
    by_issuer: dict[str, float] = {}
    for r in con.execute("SELECT instrument_id, SUM(quantity) q FROM positions_eod WHERE asof_date = ? "
                         "GROUP BY instrument_id", (day,)):
        bond = bonds.get(r["instrument_id"])
        if not bond or r["q"] <= 0:
            continue
        eur = r["q"] if bond["currency"] == "EUR" else r["q"] / fx[f"EUR{bond['currency']}"][day]
        by_issuer[bond["issuer"]] = by_issuer.get(bond["issuer"], 0) + eur
    ranked = sorted(by_issuer.items(), key=lambda kv: -kv[1])
    t["M6"] = {"top3": [k for k, _ in ranked[:3]],
               "share_pct": round(100 * sum(v for _, v in ranked[:3]) / sum(by_issuer.values()), 1)}

    # M7 — live trades per desk per month.
    desk_of = {r["book_id"]: r["desk"] for r in con.execute("SELECT book_id, desk FROM books")}
    counts: dict[tuple[str, str], int] = {}
    for tr in live:
        key = (desk_of[tr["book_id"]], tr["trade_date"][:7])
        counts[key] = counts.get(key, 0) + 1
    t["M7"] = {"total": sum(counts.values()), "cells": len(counts)}

    # M8 — counterparties active in the trade store.
    t["M8"] = {"counterparties": sorted({tr["counterparty_id"] for tr in live})}

    # M9 — the off-market extract.
    t["M9"] = {"rows": len(off)}

    # M10 — P&L impact of +50 bp on the Rates desk from DV01 at 30 June.
    dv01 = world["risk"]["dv01_by_book"][day]
    total = dv01["RAT-EUR"] + dv01["RAT-USD"]
    t["M10"] = {"dv01": round(total, 2), "impact": round(-50 * total, 2)}
    return t

