"""Prepare the two data sources the way an administrator would, against a running instance.

For each source: profile it (the measured facts), then lay the curated notes from
evals/models/ over them (descriptions, metrics, extra caveats, checked queries), and check
that every checked query actually runs before saving. This is the "prepare your data"
procedure from DATA.md, scripted — and the only reason an evaluation run is reproducible.

    python evals/setup_sources.py [--base http://localhost:3047] [--bare]

--bare clears every note instead, to measure the agent with tool schemas alone.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
SOURCES = {
    "analytics_db": {"name": "Sales DB", "db": "analytics.db", "model": "sales"},
    "crm": {"name": "CRM", "db": "crm.db", "model": "crm"},
}


def call(base: str, method: str, path: str, body: dict | None = None, timeout: int = 600):
    request = urllib.request.Request(
        f"{base}/api/admin{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def ensure_servers(base: str, testbed: str) -> dict[str, dict]:
    servers = {s["slug"]: s for s in call(base, "GET", "/servers")}
    for slug, spec in SOURCES.items():
        if slug not in servers:
            call(base, "POST", "/servers", {
                "name": spec["name"], "slug": slug, "transport": "stdio", "command": "uvx",
                "args": ["--with", "mcp<1.10", "mcp-server-sqlite", "--db-path",
                         f"{testbed}/data/{spec['db']}"], "enabled": True})
    return {s["slug"]: s for s in call(base, "GET", "/servers")}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:3047")
    parser.add_argument("--testbed", default="/tmp/agent-testbed")
    parser.add_argument("--bare", action="store_true")
    args = parser.parse_args()
    servers = ensure_servers(args.base, args.testbed)
    for slug, spec in SOURCES.items():
        sid = servers[slug]["id"]
        if args.bare:
            call(args.base, "PUT", f"/sources/{sid}", {"description": "", "model_yaml": ""})
            print(f"{slug}: notes cleared")
            continue
        call(args.base, "PUT", f"/sources/{sid}", {"description": "", "model_yaml": ""})
        profiled = call(args.base, "POST", f"/sources/{sid}/profile")
        model = yaml.safe_load(profiled["model_yaml"]) or {}
        curated = yaml.safe_load((HERE / "models" / f"{spec['model']}.yaml").read_text()) or {}
        described = {t["name"]: t for t in curated.get("tables") or []}
        for table in model.get("tables") or []:
            if table["name"] in described:
                table["description"] = described[table["name"]]["description"]
        model["metrics"] = curated.get("metrics", [])
        model["caveats"] = list(dict.fromkeys([*(model.get("caveats") or []), *(curated.get("caveats") or [])]))
        model["verified_queries"] = curated.get("verified_queries", [])
        tools = {t["name"]: t for t in call(args.base, "GET", f"/servers/{sid}/tools")}
        query_tool = next(t["qualified_name"] for n, t in tools.items() if n == "read_query")
        for pair in model["verified_queries"]:
            result = call(args.base, "POST", f"/tools/{query_tool}/call",
                          {"arguments": {"query": pair["sql"]}})
            if not result.get("ok") or "error" in (result.get("text") or "")[:40].lower():
                raise SystemExit(f"{slug}: checked query failed — {pair['question']}: "
                                 f"{(result.get('text') or result.get('error'))[:200]}")
        description = (HERE / "models" / f"{spec['model']}.description.md").read_text().strip()
        saved = call(args.base, "PUT", f"/sources/{sid}",
                     {"description": description,
                      "model_yaml": yaml.safe_dump(model, sort_keys=False, allow_unicode=True)})
        if saved.get("errors"):
            raise SystemExit(f"{slug}: model rejected — {saved['errors']}")
        r = saved["readiness"]
        print(f"{slug}: {saved['counts']} — ready {r['score']}/{r['of']}")


if __name__ == "__main__":
    main()
