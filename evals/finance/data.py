"""A small CIB world, generated deterministically: the same numbers on every machine.

Instruments (bonds quoted in % of par, equities in their own currency), counterparties with
ratings, books and desks, versioned trades with the traps real trade stores have
(amendments, cancellations), month-end positions, daily prices and FX fixings on TARGET
business days only, VaR and limits, DV01 by book.

    python evals/finance/data.py [--out /tmp/agent-finance]

writes finance.json (read by the tool servers) and trades.db (read over SQL).
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

SEED = 20260630
START, END = date(2026, 1, 2), date(2026, 6, 30)
# TARGET2 closing days in H1 2026: New Year, Good Friday, Easter Monday, Labour Day.
HOLIDAYS = {date(2026, 1, 1), date(2026, 4, 3), date(2026, 4, 6), date(2026, 5, 1)}
MONTH_ENDS = [date(2026, 1, 30), date(2026, 2, 27), date(2026, 3, 31), date(2026, 4, 30),
              date(2026, 5, 29), date(2026, 6, 30)]

RATINGS = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-", "BB+", "BB",
           "BB-", "B+", "B", "B-", "CCC"]

DESKS = [
    {"desk": "Rates", "head": "Claire Dubois", "region": "EMEA"},
    {"desk": "Credit", "head": "Marco Bianchi", "region": "EMEA"},
    {"desk": "Cash Equities", "head": "Sophie Martin", "region": "EMEA"},
    {"desk": "Equity Derivatives", "head": "James Carter", "region": "AMER"},
]
BOOKS = [
    {"book_id": "RAT-EUR", "desk": "Rates", "currency": "EUR"},
    {"book_id": "RAT-USD", "desk": "Rates", "currency": "USD"},
    {"book_id": "CRD-IG", "desk": "Credit", "currency": "EUR"},
    {"book_id": "CRD-HY", "desk": "Credit", "currency": "EUR"},
    {"book_id": "EQC-EU", "desk": "Cash Equities", "currency": "EUR"},
    {"book_id": "EQC-US", "desk": "Cash Equities", "currency": "USD"},
    {"book_id": "EQD-EU", "desk": "Equity Derivatives", "currency": "EUR"},
]

BONDS = [
    # isin, issuer, country, ccy, coupon, maturity, rating, sector
    ("FR0014007L00", "République française", "FR", "EUR", 0.75, "2032-05-25", "AA-", "Sovereign"),
    ("DE0001102580", "Bundesrepublik Deutschland", "DE", "EUR", 1.00, "2031-08-15", "AAA", "Sovereign"),
    ("IT0005436693", "Repubblica Italiana", "IT", "EUR", 2.45, "2033-09-01", "BBB", "Sovereign"),
    ("US91282CJL54", "United States Treasury", "US", "USD", 4.50, "2033-11-15", "AA+", "Sovereign"),
    ("GB00BMV7TC88", "United Kingdom Gilt", "GB", "GBP", 4.25, "2034-07-31", "AA", "Sovereign"),
    ("FR0013508470", "TotalEnergies SE", "FR", "EUR", 1.62, "2030-05-18", "A+", "Energy"),
    ("FR0014003S56", "Renault SA", "FR", "EUR", 2.50, "2029-04-01", "BB+", "Automotive"),
    ("XS2310118893", "Volkswagen International Finance", "DE", "EUR", 3.75, "2030-03-28", "BBB+", "Automotive"),
    ("FR0013444759", "Société Générale SA", "FR", "EUR", 1.25, "2031-06-12", "A", "Banks"),
    ("XS2595418323", "Deutsche Bank AG", "DE", "EUR", 4.00, "2032-02-24", "BBB", "Banks"),
    ("US037833DV96", "Apple Inc", "US", "USD", 3.35, "2032-02-09", "AA+", "Technology"),
    ("US345370CZ16", "Ford Motor Co", "US", "USD", 6.10, "2032-08-19", "BB+", "Automotive"),
    ("XS2463450408", "Telecom Italia SpA", "IT", "EUR", 6.87, "2028-02-15", "B+", "Telecom"),
    ("FR0014004L86", "Orange SA", "FR", "EUR", 1.37, "2030-01-15", "BBB+", "Telecom"),
    ("XS2524143554", "Carnival Corp", "US", "EUR", 7.62, "2027-03-01", "B", "Leisure"),
    ("GB00BL6C8Y03", "Tesco PLC", "GB", "GBP", 5.50, "2031-01-13", "BBB-", "Retail"),
]
EQUITIES = [
    # isin, ticker, name, country, ccy, sector, start price
    ("FR0000120271", "TTE FP", "TotalEnergies SE", "FR", "EUR", "Energy", 58.0),
    ("FR0000131104", "BNP FP", "BNP Paribas SA", "FR", "EUR", "Banks", 64.0),
    ("FR0000130809", "GLE FP", "Société Générale SA", "FR", "EUR", "Banks", 27.5),
    ("FR0000121014", "MC FP", "LVMH Moët Hennessy Louis Vuitton", "FR", "EUR", "Consumer", 690.0),
    ("DE0007164600", "SAP GY", "SAP SE", "DE", "EUR", "Technology", 245.0),
    ("DE0007236101", "SIE GY", "Siemens AG", "DE", "EUR", "Industrials", 190.0),
    ("NL0010273215", "ASML NA", "ASML Holding NV", "NL", "EUR", "Technology", 710.0),
    ("FR0000125338", "CAP FP", "Capgemini SE", "FR", "EUR", "Technology", 165.0),
    ("US0378331005", "AAPL US", "Apple Inc", "US", "USD", "Technology", 235.0),
    ("US5949181045", "MSFT US", "Microsoft Corp", "US", "USD", "Technology", 430.0),
    ("US88160R1014", "TSLA US", "Tesla Inc", "US", "USD", "Automotive", 400.0),
    ("US46625H1005", "JPM US", "JPMorgan Chase & Co", "US", "USD", "Banks", 240.0),
]
COUNTERPARTIES = [
    # id, name, country, type, rating
    ("CP001", "Société Générale SA", "FR", "Bank", "A"),
    ("CP002", "Deutsche Bank AG", "DE", "Bank", "BBB+"),
    ("CP003", "Goldman Sachs International", "GB", "Bank", "A+"),
    ("CP004", "Citadel Advisors LLC", "US", "Hedge fund", "BBB"),
    ("CP005", "Amundi Asset Management", "FR", "Asset manager", "A+"),
    ("CP006", "AXA Investment Managers", "FR", "Asset manager", "AA-"),
    ("CP007", "Millennium Partners", "US", "Hedge fund", "BBB-"),
    ("CP008", "Allianz SE", "DE", "Insurer", "AA"),
    ("CP009", "Norges Bank Investment Management", "NO", "Sovereign fund", "AAA"),
    ("CP010", "Kerner Capital Partners", "LU", "Hedge fund", "BB"),
    ("CP011", "Banco Santander SA", "ES", "Bank", "A"),
    ("CP012", "Ville de Paris", "FR", "Public sector", "AA-"),
]
FX_START = {"EURUSD": 1.085, "EURGBP": 0.842, "EURCHF": 0.948, "EURJPY": 162.0}
VAR_LIMITS = {"Rates": 4_500_000, "Credit": 3_000_000, "Cash Equities": 2_500_000,
              "Equity Derivatives": 3_500_000}


def business_days() -> list[date]:
    days, day = [], START
    while day <= END:
        if day.weekday() < 5 and day not in HOLIDAYS:
            days.append(day)
        day += timedelta(days=1)
    return days


def walk(rng: random.Random, start: float, days: int, vol: float, drift: float = 0.0,
         low: float | None = None, high: float | None = None) -> list[float]:
    values, value = [], start
    for _ in range(days):
        value *= 1 + rng.gauss(drift, vol)
        if low is not None:
            value = max(low, value)
        if high is not None:
            value = min(high, value)
        values.append(value)
    return values


def build() -> dict:
    rng = random.Random(SEED)
    days = business_days()
    prices: dict[str, dict[str, float]] = {}
    for isin, *_rest in BONDS:
        start = rng.uniform(94, 103)
        series = walk(rng, start, len(days), 0.0022, 0.00005, 70, 115)
        prices[isin] = {d.isoformat(): round(p, 3) for d, p in zip(days, series)}
    # Equities: a few with a clear trend, so "lost more than 10%" has an answer.
    drifts = {"GLE FP": -0.0012, "TSLA US": -0.0016, "CAP FP": -0.0011, "ASML NA": 0.0012,
              "SAP GY": 0.0004, "MC FP": -0.0003}
    for isin, ticker, _n, _c, _ccy, _s, start in EQUITIES:
        series = walk(rng, start, len(days), 0.014, drifts.get(ticker, 0.0002))
        prices[isin] = {d.isoformat(): round(p, 2) for d, p in zip(days, series)}
    fx = {}
    for pair, start in FX_START.items():
        series = walk(rng, start, len(days), 0.0035)
        fx[pair] = {d.isoformat(): round(v, 5 if pair != "EURJPY" else 3) for d, v in zip(days, series)}
    curves = {}
    for ccy, base in (("EUR", 2.35), ("USD", 4.10), ("GBP", 4.05)):
        curves[ccy] = {}
        for d in MONTH_ENDS:
            shift = rng.gauss(0, 0.08)
            curves[ccy][d.isoformat()] = {t: round(base + shift + 0.18 * (i ** 0.8), 3)
                                          for i, t in enumerate(["3M", "6M", "1Y", "2Y", "5Y", "10Y", "30Y"])}
    return {"days": [d.isoformat() for d in days], "prices": prices, "fx": fx, "curves": curves}


def build_trades(rng: random.Random) -> list[dict]:
    """Versioned trades. The latest version of a trade is the trade; CANCELLED ones are not."""
    days = business_days()
    trades: list[dict] = []
    books_for = {"bond": ["RAT-EUR", "RAT-USD", "CRD-IG", "CRD-HY"],
                 "equity": ["EQC-EU", "EQC-US", "EQD-EU"]}
    traders = ["A. Moreau", "L. Schmidt", "P. Rossi", "K. Nguyen", "D. Walsh", "E. Laurent"]
    n = 0
    for _ in range(420):
        n += 1
        kind = "bond" if rng.random() < 0.55 else "equity"
        if kind == "bond":
            isin, issuer, _country, ccy, *_x, rating, _sector = rng.choice(BONDS)
            candidates = [b for b in books_for["bond"]
                          if (b == "RAT-USD") == (ccy == "USD")
                          and (not b.startswith("CRD") or issuer not in ("République française",
                               "Bundesrepublik Deutschland", "United States Treasury",
                               "United Kingdom Gilt", "Repubblica Italiana"))
                          and (b != "CRD-HY" or RATINGS.index(rating) >= RATINGS.index("BB+"))
                          and (b != "CRD-IG" or RATINGS.index(rating) <= RATINGS.index("BBB-"))
                          and (not b.startswith("RAT") or issuer in ("République française",
                               "Bundesrepublik Deutschland", "United States Treasury",
                               "United Kingdom Gilt", "Repubblica Italiana"))]
            if not candidates:
                continue
            book = rng.choice(candidates)
            quantity = rng.choice([1, 2, 2.5, 5, 10]) * 1_000_000
        else:
            isin, _t, _n, _country, ccy, _s, _p = rng.choice(EQUITIES)
            candidates = [b for b in books_for["equity"] if (b == "EQC-US") == (ccy == "USD")]
            book = rng.choice(candidates)
            quantity = rng.choice([500, 1000, 2500, 5000, 10000])
        day = rng.choice(days)
        side = "BUY" if rng.random() < 0.68 else "SELL"
        counterparty = rng.choice(COUNTERPARTIES)[0]
        trade = {"trade_id": f"T{n:05d}", "version": 1, "trade_date": day.isoformat(),
                 "book_id": book, "instrument_id": isin, "counterparty_id": counterparty,
                 "side": side, "quantity": quantity, "currency": ccy, "status": "NEW",
                 "trader": rng.choice(traders)}
        trades.append(trade)
        roll = rng.random()
        if roll < 0.10:        # amended: the quantity changed, version 2 is the trade
            trades.append({**trade, "version": 2, "status": "AMENDED",
                           "quantity": round(trade["quantity"] * rng.choice([0.5, 1.5, 2]), 2)})
        elif roll < 0.16:      # cancelled: version 2 says it never happened
            trades.append({**trade, "version": 2, "status": "CANCELLED"})
    return trades


def positions_from(trades: list[dict]) -> list[dict]:
    latest: dict[str, dict] = {}
    for t in trades:
        if t["trade_id"] not in latest or t["version"] > latest[t["trade_id"]]["version"]:
            latest[t["trade_id"]] = t
    live = [t for t in latest.values() if t["status"] != "CANCELLED"]
    rows = []
    for asof in MONTH_ENDS:
        totals: dict[tuple[str, str], float] = {}
        for t in live:
            if date.fromisoformat(t["trade_date"]) <= asof:
                sign = 1 if t["side"] == "BUY" else -1
                key = (t["book_id"], t["instrument_id"])
                totals[key] = totals.get(key, 0) + sign * t["quantity"]
        for (book, isin), qty in sorted(totals.items()):
            if abs(qty) > 1e-9:
                rows.append({"asof_date": asof.isoformat(), "book_id": book,
                             "instrument_id": isin, "quantity": qty})
    return rows


def risk(world: dict, positions: list[dict]) -> dict:
    rng = random.Random(SEED + 1)
    var: dict[str, dict[str, float]] = {}
    for d in MONTH_ENDS:
        var[d.isoformat()] = {}
        for desk in VAR_LIMITS:
            share = {"Rates": 0.62, "Credit": 0.71, "Cash Equities": 0.55, "Equity Derivatives": 0.66}[desk]
            var[d.isoformat()][desk] = round(VAR_LIMITS[desk] * min(1.08, max(0.2, rng.gauss(share, 0.14))))
    dv01: dict[str, dict[str, float]] = {}
    bonds = {b[0]: b for b in BONDS}
    for d in MONTH_ENDS:
        dv01[d.isoformat()] = {}
        for book in ("RAT-EUR", "RAT-USD", "CRD-IG", "CRD-HY"):
            total = 0.0
            for p in positions:
                if p["asof_date"] == d.isoformat() and p["book_id"] == book and p["instrument_id"] in bonds:
                    years = (date.fromisoformat(bonds[p["instrument_id"]][5]) - d).days / 365.25
                    total += p["quantity"] * years * 0.9 / 10_000
            dv01[d.isoformat()][book] = round(total, 2)
    return {"var_99_1d_eur": var, "var_limits_eur": VAR_LIMITS, "dv01_by_book": dv01}


def write(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    world = build()
    trades = build_trades(random.Random(SEED + 2))
    positions = positions_from(trades)
    world["risk"] = risk(world, positions)
    world["refdata"] = {
        "bonds": [dict(zip(["isin", "issuer", "country", "currency", "coupon", "maturity", "rating",
                            "sector"], b)) for b in BONDS],
        "equities": [dict(zip(["isin", "ticker", "name", "country", "currency", "sector"], e[:6]))
                     for e in EQUITIES],
        "counterparties": [dict(zip(["counterparty_id", "name", "country", "type", "rating"], c))
                           for c in COUNTERPARTIES],
        "rating_scale": RATINGS,
        "holidays": sorted(d.isoformat() for d in HOLIDAYS),
    }
    (out / "finance.json").write_text(json.dumps(world, ensure_ascii=False))
    db = out / "trades.db"
    if db.exists():
        db.unlink()
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE desks (desk TEXT PRIMARY KEY, head TEXT, region TEXT);
        CREATE TABLE books (book_id TEXT PRIMARY KEY, desk TEXT REFERENCES desks(desk), currency TEXT);
        CREATE TABLE trades (trade_id TEXT, version INTEGER, trade_date TEXT, book_id TEXT,
                             instrument_id TEXT, counterparty_id TEXT, side TEXT, quantity REAL,
                             currency TEXT, status TEXT, trader TEXT,
                             PRIMARY KEY (trade_id, version));
        CREATE TABLE positions_eod (asof_date TEXT, book_id TEXT, instrument_id TEXT, quantity REAL,
                                    PRIMARY KEY (asof_date, book_id, instrument_id));
    """)
    con.executemany("INSERT INTO desks VALUES (:desk, :head, :region)", DESKS)
    con.executemany("INSERT INTO books VALUES (:book_id, :desk, :currency)", BOOKS)
    con.executemany("INSERT INTO trades VALUES (:trade_id, :version, :trade_date, :book_id, "
                    ":instrument_id, :counterparty_id, :side, :quantity, :currency, :status, :trader)",
                    trades)
    con.executemany("INSERT INTO positions_eod VALUES (:asof_date, :book_id, :instrument_id, :quantity)",
                    positions)
    con.commit()
    con.close()
    return {"trades": len(trades), "positions": len(positions), "days": len(world["days"])}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/agent-finance")
    args = parser.parse_args()
    print(write(Path(args.out)))
