"""Prepare the CIB sources the way an administrator would — minus the human reading.

    python evals/finance/prepare.py [--base http://localhost:3045] [--bare]

For each source that answers markets questions: Draft with AI (tool services: from their
schemas, the atlas and safe probes; the trade store: profiled, then drafted with every
query checked) and save the draft as is. In real use a person reads the draft before
saving — here the point is to measure what a prepared estate is worth against a bare one.

--bare clears the descriptions and models again (observations are kept; use Admin →
Forget to clear those).
"""

from __future__ import annotations

import argparse
import json
import urllib.request

SOURCES = ["market_data", "reference_data", "market_risk", "trade_store"]


def call(base: str, method: str, path: str, body: dict | None = None):
    request = urllib.request.Request(
        f"{base}/api/admin{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=900) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:3045")
    parser.add_argument("--bare", action="store_true")
    args = parser.parse_args()
    sources = {s["slug"]: s for s in call(args.base, "GET", "/sources")}
    for slug in SOURCES:
        source = sources.get(slug)
        if source is None:
            print(f"{slug}: not connected")
            continue
        if args.bare:
            call(args.base, "PUT", f"/sources/{source['id']}", {"description": "", "model_yaml": ""})
            print(f"{slug}: cleared")
            continue
        draft = call(args.base, "POST", f"/sources/{source['id']}/draft")
        body = {"description": draft.get("description", "")}
        if source["queryable"]:
            body["model_yaml"] = draft.get("model_yaml", "")
        saved = call(args.base, "PUT", f"/sources/{source['id']}", body)
        print(f"{slug}: readiness {saved['readiness']['score']}/{saved['readiness']['of']}"
              + (f" · probed {', '.join(draft.get('probed') or [])}" if draft.get("probed") else "")
              + (f" · {draft.get('checked', 0)} checked queries" if source["queryable"] else ""))
        print("   " + (body["description"][:400].replace("\n", " ")))


if __name__ == "__main__":
    main()
