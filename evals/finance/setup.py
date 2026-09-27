"""Connect the CIB testbed to a running instance: twelve servers, about fifty tools.

    python evals/finance/data.py                       # the world
    python evals/finance/setup.py [--base http://localhost:3047] [--remove]

Market data, reference data and risk are tool servers; the trade store is SQL; eight more
are the rest of a bank's estate. Every server is registered as offline ("local"), so each
runs inside the loopback-only sandbox like any server would in the air-gapped deployment.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PYTHON = str(Path(sys.executable))
SERVERS = [
    ("market_data", "Market data", "market"),
    ("refdata", "Reference data", "refdata"),
    ("risk", "Market risk", "risk"),
    ("hr", "People directory", "hr"),
    ("it_helpdesk", "IT helpdesk", "it"),
    ("procurement", "Procurement", "procurement"),
    ("facilities", "Workplace & facilities", "facilities"),
    ("travel", "Business travel", "travel"),
    ("esg", "ESG research", "esg"),
    ("compliance", "Compliance library", "compliance"),
    ("marketing", "Marketing campaigns", "marketing"),
]


def call(base: str, method: str, path: str, body: dict | None = None):
    request = urllib.request.Request(
        f"{base}/api/admin{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:3047")
    parser.add_argument("--data", default="/tmp/agent-finance")
    parser.add_argument("--remove", action="store_true")
    parser.add_argument("--estate", action="store_true",
                        help="Also connect files, dataframes, memory, time, Sales DB and CRM.")
    args = parser.parse_args()
    existing = {s["name"]: s for s in call(args.base, "GET", "/servers")}
    wanted = [(slug, name) for slug, name, _ in SERVERS] + [("trades_db", "Trade store")]
    if args.remove:
        for _slug, name in wanted:
            if name in existing:
                call(args.base, "DELETE", f"/servers/{existing[name]['id']}")
                print(f"removed {name}")
        return
    for slug, name, role in SERVERS:
        if name in existing:
            continue
        server = call(args.base, "POST", "/servers", {
            "name": name, "transport": "stdio", "command": PYTHON, "network": "local",
            "args": [str(HERE / "servers.py"), "--role", role, "--data", f"{args.data}/finance.json"],
            "connect": True})
        print(f"{name}: {server.get('status')} · {server.get('tool_count')} tools · {server.get('network', '')}")
    if args.estate:
        # The rest of a realistic estate: files, dataframes, memory, time, and the sales and
        # CRM databases of the first testbed — so routing is tested among ~20 servers.
        for catalog_id, values in (("filesystem", {"root_path": f"{args.data}"}), ("pandas-frames", {}),
                                   ("memory", {}), ("time", {})):
            if not any(s.get("catalog_id") == catalog_id for s in existing.values()):
                server = call(args.base, "POST", "/servers", {"catalog_id": catalog_id, "values": values,
                                                              "connect": True})
                print(f"{server.get('name')}: {server.get('status')} · {server.get('tool_count')} tools")
        for name, db in (("Sales DB", "analytics.db"), ("CRM", "crm.db")):
            if name not in existing:
                server = call(args.base, "POST", "/servers", {
                    "name": name, "transport": "stdio", "command": "uvx", "network": "local",
                    "args": ["--with", "mcp<1.10", "mcp-server-sqlite", "--db-path",
                             f"/tmp/agent-testbed/data/{db}"], "connect": True})
                print(f"{name}: {server.get('status')} · {server.get('tool_count')} tools")
    if "Trade store" not in existing:
        server = call(args.base, "POST", "/servers", {
            "name": "Trade store", "transport": "stdio", "command": "uvx", "network": "local",
            "args": ["--with", "mcp<1.10", "mcp-server-sqlite", "--db-path", f"{args.data}/trades.db"],
            "connect": True})
        print(f"Trade store: {server.get('status')} · {server.get('tool_count')} tools · {server.get('error') or ''}")


if __name__ == "__main__":
    main()
