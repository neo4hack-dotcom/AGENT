"""Build the data-analyst testbed, deterministically, and compute its ground truth.

Two databases behind two MCP servers, and three files in the workspace — the minimum for a
question that genuinely crosses sources:

  sales  (analytics.db)  customers, orders, refunds — with planted defects: six duplicated
                         orders, three orders for a customer that does not exist, two
                         negative amounts, one customer with no region.
  crm    (crm.db)        campaigns, leads (which customer each converted lead became),
                         account managers.
  workspace              regional-targets-2026.csv, vat-rates.csv,
                         partner-claimed-revenue.csv (which wrongly counts cancelled orders).

Every figure an evaluation checks is computed here, from the same rows, by plain SQL — so a
passing answer is one that matches arithmetic nobody had to trust the agent for.

    python evals/testbed.py [--dir /tmp/agent-testbed] [--workspace backend/data/workspace]

/tmp is purged by the OS from time to time, and the SQLite server quietly creates an empty
file at a missing path — so run this before every evaluation, not once.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import random
import sqlite3
from pathlib import Path


def build_sales(path: Path) -> None:
    random.seed(20260917)
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, region TEXT, segment TEXT, signup_date TEXT);
    CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, order_date TEXT,
                         amount_eur REAL, status TEXT, channel TEXT);
    CREATE TABLE refunds (order_id INTEGER, amount_eur REAL, reason TEXT, refunded_at TEXT);
    """)
    regions = ["FR", "DE", "ES", "IT", "UK"]
    segments = ["SMB", "Enterprise"]
    names = [f"{p} {s}" for p in ("Beaumont", "Nordwind", "Olivares", "Ferraro", "Lacroix",
                                  "Kerner", "Marchetti", "Duval", "Aalto", "Ibanez")
             for s in ("SARL", "GmbH", "SL", "SpA")]
    customers = []
    for i in range(1, 41):
        customers.append((i, names[i - 1], random.choice(regions), random.choice(segments),
                          (dt.date(2025, 1, 1) + dt.timedelta(days=random.randint(0, 500))).isoformat()))
    customers[7] = (8, customers[7][1], None, customers[7][3], customers[7][4])
    db.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", customers)

    orders, oid = [], 1
    start = dt.date(2026, 1, 1)
    for _ in range(400):
        day = start + dt.timedelta(days=random.randint(0, 181))
        orders.append((oid, random.randint(1, 40), day.isoformat(),
                       round(random.uniform(40, 4200), 2),
                       random.choices(["shipped", "pending", "refunded", "cancelled"],
                                      [.68, .13, .11, .08])[0],
                       random.choices(["web", "partner", "direct"], [.5, .3, .2])[0]))
        oid += 1
    for order in random.sample(orders, 6):
        orders.append((oid, *order[1:])); oid += 1
    for _ in range(3):
        orders.append((oid, 999, "2026-03-15", round(random.uniform(100, 900), 2), "shipped", "web")); oid += 1
    for _ in range(2):
        orders.append((oid, random.randint(1, 40), "2026-04-02", -round(random.uniform(50, 300), 2),
                       "shipped", "web")); oid += 1
    db.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?)", orders)
    refunded = [o for o in orders if o[4] == "refunded"]
    db.executemany("INSERT INTO refunds VALUES (?,?,?,?)",
                   [(o[0], o[3], random.choice(["damaged", "late", "wrong item", "duplicate charge"]),
                     o[2]) for o in refunded])
    # The data owner's decisions, made once at the source instead of in every query: the six
    # exact duplicates are double imports and count once; negative amounts are credit notes
    # and stay; orders for the unknown customer 999 are real sales and stay in totals. This
    # is step 3 of DATA.md — a clean view is the cheapest accuracy there is.
    db.executescript("""
    CREATE VIEW orders_clean AS
    SELECT MIN(id) AS id, customer_id, order_date, amount_eur, status, channel
    FROM orders GROUP BY customer_id, order_date, amount_eur, status, channel;
    """)
    db.commit()
    db.close()


def build_crm(path: Path) -> None:
    rng = random.Random(7)
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE campaigns (id INTEGER PRIMARY KEY, name TEXT, channel TEXT, start_date TEXT, budget_eur REAL);
    CREATE TABLE leads (id INTEGER PRIMARY KEY, campaign_id INTEGER, customer_id INTEGER,
                        created_at TEXT, converted INTEGER);
    CREATE TABLE account_managers (customer_id INTEGER PRIMARY KEY, manager TEXT, tier TEXT);
    """)
    campaigns = [(1, "Winter Webinar", "event", "2025-12-01", 18000.0),
                 (2, "Search Q1", "search", "2026-01-05", 42000.0),
                 (3, "LinkedIn ABM", "social", "2026-01-20", 30000.0),
                 (4, "Spring Newsletter", "email", "2026-03-01", 6000.0),
                 (5, "Trade Fair Milan", "event", "2026-04-10", 55000.0)]
    db.executemany("INSERT INTO campaigns VALUES (?,?,?,?,?)", campaigns)
    leads, lid = [], 1
    for customer in range(1, 41):
        # Every customer came from exactly one converted lead; some also left stray leads.
        campaign = rng.choice(campaigns)
        leads.append((lid, campaign[0], customer, campaign[3], 1)); lid += 1
        for _ in range(rng.randint(0, 2)):
            other = rng.choice(campaigns)
            leads.append((lid, other[0], None, other[3], 0)); lid += 1
    db.executemany("INSERT INTO leads VALUES (?,?,?,?,?)", leads)
    managers = ["Claire Martin", "Jonas Weber", "Lucia Rossi", "Tom Hughes"]
    db.executemany("INSERT INTO account_managers VALUES (?,?,?)",
                   [(c, rng.choice(managers), rng.choice(["gold", "silver", "bronze"]))
                    for c in range(1, 41)])
    db.commit()
    db.close()


def build_workspace(sales: Path, workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    targets = {"FR": 120000.0, "DE": 95000.0, "ES": 40000.0, "IT": 70000.0, "UK": 55000.0}
    with (workspace / "regional-targets-2026.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["region", "h1_target_eur"])
        for region, value in targets.items():
            writer.writerow([region, value])
    with (workspace / "vat-rates.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["region", "vat_rate"])
        for region, rate in {"FR": .20, "DE": .19, "ES": .21, "IT": .22, "UK": .20}.items():
            writer.writerow([region, rate])
    db = sqlite3.connect(sales)
    rows = db.execute("""SELECT c.region, SUM(o.amount_eur) FROM orders o
                         JOIN customers c ON c.id = o.customer_id
                         WHERE o.channel = 'partner' AND o.status IN ('shipped', 'cancelled')
                           AND c.region IS NOT NULL GROUP BY c.region""").fetchall()
    with (workspace / "partner-claimed-revenue.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["region", "claimed_partner_revenue_eur"])
        for region, total in sorted(rows):
            writer.writerow([region, round(total, 2)])


def ground_truth(sales: Path, crm: Path, workspace: Path) -> dict:
    db = sqlite3.connect(sales)
    db.execute(f"ATTACH DATABASE '{crm}' AS crm")
    db.row_factory = sqlite3.Row
    q = lambda sql: [dict(r) for r in db.execute(sql)]
    targets = {r["region"]: float(r["h1_target_eur"])
               for r in csv.DictReader((workspace / "regional-targets-2026.csv").open())}
    vat = {r["region"]: float(r["vat_rate"])
           for r in csv.DictReader((workspace / "vat-rates.csv").open())}
    shipped_by_region = {r["region"]: round(r["rev"], 2) for r in q(
        """SELECT c.region, SUM(o.amount_eur) rev FROM orders_clean o JOIN customers c ON c.id = o.customer_id
           WHERE o.status = 'shipped' AND c.region IS NOT NULL GROUP BY c.region""")}
    monthly = {r["m"]: round(r["rev"], 2) for r in q(
        """SELECT substr(order_date, 1, 7) m, SUM(amount_eur) rev FROM orders_clean
           WHERE status = 'shipped' GROUP BY m ORDER BY m""")}
    monthly_channel = q("""SELECT substr(order_date, 1, 7) m, channel, ROUND(SUM(amount_eur), 2) rev
                           FROM orders_clean WHERE status = 'shipped' GROUP BY m, channel ORDER BY m, channel""")
    campaign = q("""SELECT k.name campaign, k.budget_eur budget, ROUND(SUM(o.amount_eur), 2) revenue,
                           COUNT(DISTINCT l.customer_id) customers
                    FROM crm.leads l JOIN crm.campaigns k ON k.id = l.campaign_id
                    JOIN orders_clean o ON o.customer_id = l.customer_id
                    WHERE l.converted = 1 AND o.status = 'shipped'
                    GROUP BY k.id ORDER BY revenue DESC""")
    for row in campaign:
        row["roi"] = round(row["revenue"] / row["budget"], 2)
    managers = q("""SELECT a.manager, ROUND(SUM(o.amount_eur), 2) revenue FROM crm.account_managers a
                    JOIN orders_clean o ON o.customer_id = a.customer_id WHERE o.status = 'shipped'
                    GROUP BY a.manager ORDER BY revenue DESC""")
    refunded = q("""SELECT o.id, c.name customer, c.region, o.order_date, o.amount_eur, r.reason
                    FROM orders o JOIN customers c ON c.id = o.customer_id
                    JOIN refunds r ON r.order_id = o.id WHERE o.status = 'refunded' ORDER BY o.id""")
    return {
        "shipped_by_region": shipped_by_region,
        "missed_target": sorted(r for r, v in shipped_by_region.items() if v < targets[r]),
        "net_by_region": {r: round(v / (1 + vat[r]), 2) for r, v in shipped_by_region.items()},
        "monthly_shipped": monthly,
        "monthly_by_channel": monthly_channel,
        "weakest_month": min(monthly, key=monthly.get),
        "campaign_revenue": campaign,
        "best_campaign": campaign[0]["campaign"],
        "best_roi_campaign": max(campaign, key=lambda r: r["roi"])["campaign"],
        "manager_revenue": managers,
        "refunded_orders": refunded,
        "refunded_total": round(sum(r["amount_eur"] for r in refunded), 2),
        "gross_all_rows": round(q("SELECT SUM(amount_eur) v FROM orders")[0]["v"], 2),
        "revenue_total": round(q("SELECT SUM(amount_eur) v FROM orders_clean WHERE status = 'shipped'")[0]["v"], 2),
        "revenue_q1_by_segment": {r["segment"]: round(r["v"], 2) for r in q(
            """SELECT c.segment, SUM(o.amount_eur) v FROM orders_clean o JOIN customers c ON c.id = o.customer_id
               WHERE o.status = 'shipped' AND o.order_date BETWEEN '2026-01-01' AND '2026-03-31' GROUP BY c.segment""")},
        "channel_share": {r["channel"]: round(r["v"], 2) for r in q(
            "SELECT channel, SUM(amount_eur) v FROM orders_clean WHERE status = 'shipped' GROUP BY channel")},
        "anomalies": {
            "duplicate_rows": 6, "orphan_orders": 3, "negative_amounts": 2, "customers_without_region": 1},
        "data_range": q("SELECT MIN(order_date) first, MAX(order_date) last FROM orders")[0],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dir", default="/tmp/agent-testbed")
    parser.add_argument("--workspace", default=str(Path(__file__).resolve().parents[1]
                                                   / "backend" / "data" / "workspace"))
    args = parser.parse_args()
    root = Path(args.dir)
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "docs").mkdir(parents=True, exist_ok=True)
    sales, crm = root / "data" / "analytics.db", root / "data" / "crm.db"
    build_sales(sales)
    build_crm(crm)
    build_workspace(sales, Path(args.workspace))
    truth = ground_truth(sales, crm, Path(args.workspace))
    (root / "ground-truth.json").write_text(json.dumps(truth, indent=1, default=str))
    print(f"testbed ready in {root}: analytics.db, crm.db; workspace files in {args.workspace}")
    print(f"ground truth: {root / 'ground-truth.json'}")


if __name__ == "__main__":
    main()
