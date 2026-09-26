"""The tool servers of the CIB testbed, one process per role.

    python evals/finance/servers.py --role market --data /tmp/agent-finance/finance.json

Three are the data a markets question needs — market data, reference data, risk — each with
the conventions real services have and a model has to notice: bond prices in % of par, FX
fixed as EURxxx, no fixing on a TARGET holiday, VaR only at month ends. The others are the
rest of a bank's MCP estate, there to be *not* chosen: HR has "positions" (jobs),
facilities has "desks" (furniture), procurement has "ratings" and "limits" (suppliers). An
agent that routes on words rather than meaning walks straight into them.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mcp_servers"))
from _mcp_stdio import McpServer  # noqa: E402

WORLD: dict = {}


def _obj(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or []}


S = {"type": "string"}


def _day(value: str) -> date:
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise ValueError(f"'{value}' is not a date; use YYYY-MM-DD") from None


def _fixing_error(kind: str, day: date) -> dict:
    days = WORLD["days"]
    before = [d for d in days if d < day.isoformat()]
    reason = ("a TARGET holiday" if day.isoformat() in WORLD["refdata"]["holidays"]
              else "a weekend" if day.weekday() >= 5 else "outside the data history")
    return {"error": f"No {kind} on {day.isoformat()}: {reason}.",
            "previous_business_day": before[-1] if before else None}


def _instrument(identifier: str) -> tuple[dict, str] | tuple[None, None]:
    key = (identifier or "").strip().upper()
    for bond in WORLD["refdata"]["bonds"]:
        if bond["isin"] == key:
            return bond, "bond"
    for equity in WORLD["refdata"]["equities"]:
        if equity["isin"] == key or equity["ticker"].upper() == key or equity["ticker"].split()[0].upper() == key:
            return equity, "equity"
    return None, None


# --------------------------------------------------------------------- market
def market(server: McpServer) -> None:
    @server.tool("get_price", "Closing price of one instrument on one date. Bonds are quoted "
                 "clean, in percent of par; equities in their trading currency per share.",
                 _obj({"identifier": {**S, "description": "ISIN or Bloomberg-style ticker"},
                       "date": {**S, "description": "YYYY-MM-DD"}}, ["identifier", "date"]),
                 read_only=True)
    def get_price(identifier: str, date: str) -> dict:
        found, kind = _instrument(identifier)
        if not found:
            return {"error": f"Unknown identifier '{identifier}'."}
        day = _day(date)
        px = WORLD["prices"][found["isin"]].get(day.isoformat())
        if px is None:
            return _fixing_error("close", day)
        return {"isin": found["isin"], "date": day.isoformat(), "px_last": px,
                "quote": "PCT_OF_PAR" if kind == "bond" else "PER_SHARE", "currency": found["currency"]}

    @server.tool("get_price_history", "Daily closing prices of one instrument between two dates "
                 "(business days only). Same quoting conventions as get_price.",
                 _obj({"identifier": S, "start": S, "end": S}, ["identifier", "start", "end"]),
                 read_only=True)
    def get_price_history(identifier: str, start: str, end: str) -> dict:
        found, kind = _instrument(identifier)
        if not found:
            return {"error": f"Unknown identifier '{identifier}'."}
        a, b = _day(start).isoformat(), _day(end).isoformat()
        rows = [{"date": d, "px_last": p} for d, p in WORLD["prices"][found["isin"]].items() if a <= d <= b]
        return {"isin": found["isin"], "quote": "PCT_OF_PAR" if kind == "bond" else "PER_SHARE",
                "currency": found["currency"], "rows": rows}

    @server.tool("get_fx_rate", "ECB-style daily FX fixing. Pairs are quoted against EUR "
                 "(EURUSD = USD per 1 EUR): EURUSD, EURGBP, EURCHF, EURJPY.",
                 _obj({"pair": S, "date": S}, ["pair", "date"]), read_only=True)
    def get_fx_rate(pair: str, date: str) -> dict:
        code = pair.replace("/", "").replace(" ", "").upper()
        day = _day(date)
        inverse = code not in WORLD["fx"] and code[3:] + code[:3] in WORLD["fx"]
        series = WORLD["fx"].get(code[3:] + code[:3] if inverse else code)
        if series is None:
            return {"error": f"No fixing for {pair}. Available: {', '.join(WORLD['fx'])}."}
        rate = series.get(day.isoformat())
        if rate is None:
            return _fixing_error("FX fixing", day)
        if inverse:
            return {"pair": code, "date": day.isoformat(), "rate": round(1 / rate, 6),
                    "note": f"inverted from the {code[3:] + code[:3]} fixing"}
        return {"pair": code, "date": day.isoformat(), "rate": rate}

    @server.tool("get_fx_history", "FX fixings of one pair between two dates, business days only.",
                 _obj({"pair": S, "start": S, "end": S}, ["pair", "start", "end"]), read_only=True)
    def get_fx_history(pair: str, start: str, end: str) -> dict:
        code = pair.replace("/", "").replace(" ", "").upper()
        series = WORLD["fx"].get(code)
        if series is None:
            return {"error": f"No fixing for {pair}. Available: {', '.join(WORLD['fx'])}."}
        a, b = _day(start).isoformat(), _day(end).isoformat()
        return {"pair": code, "rows": [{"date": d, "rate": r} for d, r in series.items() if a <= d <= b]}

    @server.tool("get_yield_curve", "Government yield curve (percent) for EUR, USD or GBP at a "
                 "month-end date.", _obj({"currency": S, "date": S}, ["currency", "date"]),
                 read_only=True)
    def get_yield_curve(currency: str, date: str) -> dict:
        curves = WORLD["curves"].get(currency.upper())
        if curves is None:
            return {"error": f"No curve for {currency}. Available: EUR, USD, GBP."}
        curve = curves.get(_day(date).isoformat())
        if curve is None:
            return {"error": f"Curves are published at month ends only: {', '.join(curves)}."}
        return {"currency": currency.upper(), "date": date,
                "rows": [{"tenor": t, "yield_pct": y} for t, y in curve.items()]}


# -------------------------------------------------------------------- refdata
def refdata(server: McpServer) -> None:
    @server.tool("lookup_instrument", "Reference data of one instrument by ISIN or ticker: "
                 "issuer, country, currency, sector; for bonds also coupon, maturity and rating.",
                 _obj({"identifier": S}, ["identifier"]), read_only=True)
    def lookup_instrument(identifier: str) -> dict:
        found, kind = _instrument(identifier)
        return {"asset_class": kind, **found} if found else {"error": f"Unknown identifier '{identifier}'."}

    @server.tool("list_instruments", "All instruments of one asset class ('bond' or 'equity'), "
                 "optionally filtered by sector, country or currency.",
                 _obj({"asset_class": S, "sector": S, "country": S, "currency": S}, ["asset_class"]),
                 read_only=True)
    def list_instruments(asset_class: str, sector: str = "", country: str = "", currency: str = "") -> dict:
        pool = WORLD["refdata"]["bonds" if asset_class.lower().startswith("bond") else "equities"]
        rows = [r for r in pool
                if (not sector or r["sector"].lower() == sector.lower())
                and (not country or r["country"].lower() == country.lower())
                and (not currency or r["currency"].lower() == currency.lower())]
        return {"rows": rows}

    @server.tool("find_counterparty", "Counterparties whose name or id matches the text.",
                 _obj({"query": S}, ["query"]), read_only=True)
    def find_counterparty(query: str) -> dict:
        q = query.lower()
        return {"rows": [c for c in WORLD["refdata"]["counterparties"]
                         if q in c["name"].lower() or q == c["counterparty_id"].lower()]}

    @server.tool("list_counterparties", "Every counterparty with its type, country and credit rating.",
                 _obj({"type": S, "country": S}), read_only=True)
    def list_counterparties(type: str = "", country: str = "") -> dict:
        return {"rows": [c for c in WORLD["refdata"]["counterparties"]
                         if (not type or c["type"].lower() == type.lower())
                         and (not country or c["country"].lower() == country.lower())]}

    @server.tool("rating_scale", "The credit rating scale in use, best to worst, with the "
                 "investment-grade boundary.", _obj({}), read_only=True)
    def rating_scale() -> dict:
        scale = WORLD["refdata"]["rating_scale"]
        return {"scale_best_to_worst": scale, "investment_grade_lowest": "BBB-",
                "note": "BB+ and below is high yield."}

    @server.tool("market_calendar", "TARGET market holidays in the data period.", _obj({}),
                 read_only=True)
    def market_calendar() -> dict:
        return {"holidays": WORLD["refdata"]["holidays"], "weekends": "closed"}


# ----------------------------------------------------------------------- risk
def risk_server(server: McpServer) -> None:
    risk = lambda: WORLD["risk"]  # noqa: E731

    @server.tool("get_var", "Value-at-Risk, 99% one-day, in EUR, by desk. Computed at month-end "
                 "dates only.", _obj({"date": S, "desk": S}, ["date"]), read_only=True)
    def get_var(date: str, desk: str = "") -> dict:
        by_date = risk()["var_99_1d_eur"]
        values = by_date.get(_day(date).isoformat())
        if values is None:
            return {"error": f"VaR is computed at month ends only: {', '.join(by_date)}."}
        rows = [{"desk": d, "var_99_1d_eur": v} for d, v in values.items()
                if not desk or d.lower() == desk.lower()]
        return {"date": date, "rows": rows}

    @server.tool("get_var_limits", "The VaR limit of every desk, in EUR.", _obj({}), read_only=True)
    def get_var_limits() -> dict:
        return {"rows": [{"desk": d, "var_limit_eur": v} for d, v in risk()["var_limits_eur"].items()]}

    @server.tool("get_dv01", "Interest-rate sensitivity (DV01, EUR per basis point) by book, at "
                 "month-end dates.", _obj({"date": S, "book": S}, ["date"]), read_only=True)
    def get_dv01(date: str, book: str = "") -> dict:
        by_date = risk()["dv01_by_book"]
        values = by_date.get(_day(date).isoformat())
        if values is None:
            return {"error": f"DV01 is computed at month ends only: {', '.join(by_date)}."}
        return {"date": date, "rows": [{"book_id": b, "dv01_eur": v} for b, v in values.items()
                                       if not book or b.lower() == book.lower()]}


# ---------------------------------------------------------------- distractors
DISTRACTORS: dict[str, tuple[str, dict]] = {
    "hr": ("People directory", {
        "search_employees": ("Find employees by name; returns title, department and manager.",
                             {"query": S}, [{"name": "Claire Dubois", "title": "Head of Rates Trading", "department": "Global Markets"},
                                            {"name": "Marco Bianchi", "title": "Head of Credit Trading", "department": "Global Markets"}]),
        "list_open_positions": ("Open job positions (vacancies) by department.", {"department": S},
                                [{"position": "Quant Developer", "department": "Global Markets", "location": "Paris", "open_since": "2026-03-02"},
                                 {"position": "Risk Analyst", "department": "Risk", "location": "London", "open_since": "2026-05-11"}]),
        "org_chart": ("Direct reports of a manager.", {"manager": S},
                      [{"name": "A. Moreau", "reports_to": "Claire Dubois"}]),
        "get_leave_balance": ("Remaining paid leave of an employee, in days.", {"employee": S},
                              [{"employee": "A. Moreau", "days_left": 12.5}]),
    }),
    "it": ("IT helpdesk", {
        "list_tickets": ("Helpdesk tickets by status (open, pending, closed).", {"status": S},
                         [{"id": "INC-4471", "status": "open", "title": "Bloomberg terminal login loop", "priority": "P2"},
                          {"id": "INC-4480", "status": "open", "title": "VPN drops every hour", "priority": "P3"},
                          {"id": "INC-4492", "status": "pending", "title": "Excel add-in crash on pricing sheet", "priority": "P2"}]),
        "get_ticket": ("One ticket with its history.", {"id": S}, [{"id": "INC-4471", "history": ["opened", "assigned to L2"]}]),
        "ticket_stats": ("Ticket volumes and resolution times by month.", {"month": S},
                         [{"month": "2026-06", "opened": 214, "closed": 198, "median_hours": 6.5}]),
    }),
    "procurement": ("Procurement", {
        "list_suppliers": ("Approved suppliers by category.", {"category": S},
                           [{"supplier": "Refinitiv", "category": "Market data"}, {"supplier": "Dell", "category": "Hardware"}]),
        "supplier_rating": ("Internal performance rating of a supplier (A to E).", {"supplier": S},
                            [{"supplier": "Refinitiv", "rating": "B", "reviewed": "2026-02-01"}]),
        "spending_limits": ("Approval limits for purchase orders by department, in EUR.", {"department": S},
                            [{"department": "Global Markets", "limit_eur": 250000}]),
        "purchase_orders": ("Purchase orders by status.", {"status": S},
                            [{"po": "PO-88121", "supplier": "Dell", "amount_eur": 48200, "status": "approved"}]),
    }),
    "facilities": ("Workplace & facilities", {
        "list_rooms": ("Meeting rooms of a site with capacity and equipment.", {"site": S},
                       [{"room": "Arche 12", "site": "La Défense", "capacity": 10}]),
        "desk_occupancy": ("Office desk occupancy rate by site and date (hot-desking).", {"site": S, "date": S},
                           [{"site": "La Défense", "date": "2026-06-30", "occupancy_pct": 71}]),
        "report_incident": ("Report a building incident (write).", {"description": S}, [{"ok": True, "ref": "FAC-221"}]),
    }),
    "travel": ("Business travel", {
        "list_trips": ("Trips booked by an employee.", {"employee": S},
                       [{"employee": "Claire Dubois", "destination": "London", "date": "2026-07-08"}]),
        "travel_policy": ("The travel policy: classes, hotel caps per city.", {},
                          [{"rule": "Economy under 4h", "hotel_cap_london_gbp": 320}]),
    }),
    "esg": ("ESG research", {
        "esg_score": ("ESG score (0-100) and controversy level of a company.", {"company": S},
                      [{"company": "TotalEnergies SE", "esg_score": 58, "controversy": "High"},
                       {"company": "Renault SA", "esg_score": 64, "controversy": "Moderate"}]),
        "controversies": ("Recent ESG controversies of a company.", {"company": S},
                          [{"company": "TotalEnergies SE", "topic": "Climate litigation", "date": "2026-04-14"}]),
    }),
    "compliance": ("Compliance library", {
        "search_policies": ("Search internal policies and procedures.", {"query": S},
                            [{"id": "POL-017", "title": "Best execution policy"}, {"id": "POL-031", "title": "Market abuse surveillance"}]),
        "get_policy": ("Full text of one policy.", {"id": S},
                       [{"id": "POL-017", "text": "Traders must seek the best possible result for clients..."}]),
    }),
    "marketing": ("Marketing campaigns", {
        "list_campaigns": ("Marketing campaigns with budget and channel.", {},
                           [{"campaign": "Private banking spring", "budget_eur": 120000, "channel": "web"}]),
        "campaign_performance": ("Leads and conversions of a campaign.", {"campaign": S},
                                 [{"campaign": "Private banking spring", "leads": 830, "conversions": 41}]),
    }),
}


def distractor(server: McpServer, role: str) -> None:
    _title, tools = DISTRACTORS[role]
    for name, (description, props, rows) in tools.items():
        def handler(_rows=rows, **_kwargs):
            return {"rows": _rows}
        server.tool(name, description, _obj(props), read_only=name != "report_incident")(handler)


# ------------------------------------------------------------------- catalog
CATALOG_DOCS = {
    "trades": {"domain": "Trading", "definition": "Every trade ticket booked by the front office, one row per "
               "version: an amendment adds version 2, a cancellation adds a CANCELLED version. The latest "
               "version of each trade_id is the trade.",
               "columns": {"trade_id": "Front-office ticket id; stable across versions.",
                           "version": "Revision number; the highest version is the current state of the trade.",
                           "quantity": "Nominal for bonds, number of shares for equities, in the trade currency.",
                           "price": "Execution price: percent of par for bonds, per share for equities.",
                           "status": "NEW, AMENDED or CANCELLED (upper case). A CANCELLED last version means the trade never happened.",
                           "counterparty_id": "Counterparty code, as in the reference data (CPnnn)."}},
    "positions_eod": {"domain": "Trading", "definition": "Net end-of-day position per book and instrument, at "
                      "month-end dates only, derived from live trades.",
                      "columns": {"quantity": "Net nominal (bonds) or shares (equities); negative is short.",
                                  "asof_date": "Month-end business day of the snapshot."}},
    "books": {"domain": "Organisation", "definition": "Trading books and the desk that owns each.",
              "columns": {"desk": "Owning desk: Rates, Credit, Cash Equities, Equity Derivatives."}},
    "desks": {"domain": "Organisation", "definition": "Trading desks with their head and region.", "columns": {}},
}
GLOSSARY = {"exposure": "Market value of positions, in EUR, at the valuation date's closing prices and fixings.",
            "notional": "Face value of a bond position (quantity), converted to EUR at the relevant fixing.",
            "live trade": "The latest version of a trade whose status is not CANCELLED."}


def catalog(server: McpServer) -> None:
    @server.tool("list_datasets", "List every dataset documented in the catalog, with domain and definition.",
                 _obj({}), read_only=True)
    def list_datasets() -> dict:
        return {"datasets": [{"id": f"trades_db::{name}", "name": name, "domain": doc["domain"],
                              "definition": doc["definition"]} for name, doc in CATALOG_DOCS.items()]}

    @server.tool("get_dataset_schema", "Columns of one dataset with their business definitions.",
                 _obj({"dataset_id": S}, ["dataset_id"]), read_only=True)
    def get_dataset_schema(dataset_id: str) -> dict:
        name = dataset_id.split("::")[-1]
        doc = CATALOG_DOCS.get(name)
        if doc is None:
            return {"error": f"dataset not found: {dataset_id}"}
        return {"id": dataset_id, "name": name, "domain": doc["domain"], "definition": doc["definition"],
                "columns": [{"name": c, "definition": d} for c, d in doc["columns"].items()]}

    @server.tool("search_catalog", "Search dataset and column names and definitions.", _obj({"query": S}, ["query"]),
                 read_only=True)
    def search_catalog(query: str) -> dict:
        q = query.lower()
        hits = []
        for name, doc in CATALOG_DOCS.items():
            if q in name or q in doc["definition"].lower():
                hits.append({"dataset_id": f"trades_db::{name}", "dataset": name, "definition": doc["definition"]})
            for column, text in doc["columns"].items():
                if q in column or q in text.lower():
                    hits.append({"dataset_id": f"trades_db::{name}", "dataset": name, "column": column,
                                 "definition": text})
        return {"hits": hits[:25]}

    @server.tool("get_glossary_term", "Business definition of a glossary term.", _obj({"term": S}, ["term"]),
                 read_only=True)
    def get_glossary_term(term: str) -> dict:
        text = GLOSSARY.get(term.lower())
        return {"term": term, "definition": text} if text else {"error": f"term not found: {term}"}

    @server.tool("get_lineage", "Upstream and downstream datasets of one dataset.", _obj({"dataset_id": S}, ["dataset_id"]),
                 read_only=True)
    def get_lineage(dataset_id: str) -> dict:
        name = dataset_id.split("::")[-1]
        edges = {"positions_eod": [{"from": "trades_db::trades", "to": "trades_db::positions_eod"}]}
        return {"dataset_id": dataset_id, "edges": edges.get(name, [])}


ROLES = {"market": ("Market data", market), "refdata": ("Reference data", refdata),
         "risk": ("Market risk", risk_server), "catalog": ("Data catalog", catalog)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True)
    parser.add_argument("--data", default="/tmp/agent-finance/finance.json")
    args = parser.parse_args()
    WORLD.update(json.loads(Path(args.data).read_text()))
    if args.role in ROLES:
        title, build = ROLES[args.role]
        server = McpServer(f"cib-{args.role}", "1.0.0", title)
        build(server)
    else:
        title = DISTRACTORS[args.role][0]
        server = McpServer(f"corp-{args.role}", "1.0.0", title)
        distractor(server, args.role)
    server.run()


if __name__ == "__main__":
    main()
