"""The data catalog: documentation about the data, kept apart from the data itself.

A catalog server (DataLoom, or any MCP that lists datasets, definitions, a glossary and
lineage) is not a source to query for figures. It is what the enterprise already wrote down
about its sources: what a table is for, what a column means and how it is calculated, which
business term means what, which table feeds which. Treated as one more data source, it is
routed to and queried like one, and its definitions compete with the figures. Treated as
what it is, it sharpens everything else — so it has its own role, and it is used if and only
if one is connected:

- the agent gets a short block about it and its tools on every question, outside routing;
- `source_info` adds what the catalog says about a source's tables;
- Admin can import a source's table and column definitions from the catalog into the
  source's model, as a proposal a person reads before saving; drafting uses them too.

Nothing here assumes a product. The catalog's tools are recognised by family — list,
search, schema, column definition, glossary, lineage — and called with the parameters their
own schemas declare.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

FAMILIES = {
    "list": r"^list_(datasets|assets|tables|data_products)$",
    "search": r"search_(catalog|datasets?|assets?|metadata)|catalog_search|^search$",
    "schema": r"^(get|describe)_(dataset|asset|table)(_schema|_metadata|_details)?$|dataset_schema",
    "column": r"column_definition|describe_column|get_column",
    "glossary": r"glossary",
    "lineage": r"lineage",
}


def tool_families(tools: list[dict]) -> dict[str, dict]:
    """family → the catalog's tool for it (qualified), for the families it has."""
    out: dict[str, dict] = {}
    for family, pattern in FAMILIES.items():
        for tool in tools:
            if re.search(pattern, str(tool.get("name") or ""), re.I):
                out.setdefault(family, tool)
    return out


def _first_param(tool: dict, *preferred: str) -> str:
    schema = tool.get("input_schema") or {}
    props = list((schema.get("properties") or {}).keys())
    required = schema.get("required") or props
    for name in preferred:
        if name in props:
            return name
    return required[0] if required else (props[0] if props else "")


def _payload(result: dict) -> Any:
    text = str(result.get("text") or "").strip()
    try:
        return json.loads(text)
    except ValueError:
        return text


def _rows(value: Any) -> list[dict]:
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    if isinstance(value, dict):
        for key in ("datasets", "hits", "results", "rows", "items", "assets", "tables"):
            if isinstance(value.get(key), list):
                return [v for v in value[key] if isinstance(v, dict)]
    return []


def _name_of(row: dict) -> str:
    return str(row.get("name") or row.get("table") or row.get("dataset") or row.get("id") or "")


def _matches(row: dict, table: str) -> bool:
    wanted = table.lower().split(".")[-1]
    for key in ("name", "table", "dataset", "id", "dataset_id"):
        value = str(row.get(key) or "").lower()
        if value and (value == wanted or value.endswith("." + wanted) or value.endswith("::" + wanted)
                      or re.search(rf"[.:/]{re.escape(wanted)}$", value)):
            return True
    return False


class Catalog:
    """One connected catalog server, called through the registry like any MCP server."""

    def __init__(self, registry, server: dict) -> None:
        self.registry = registry
        self.server = server
        self.tools = [t for t in registry.tools() if t["server_id"] == server["id"]]
        self.families = tool_families(self.tools)

    @property
    def name(self) -> str:
        return self.server.get("name") or "Data catalog"

    async def _call(self, family: str, arguments: dict, timeout: float = 20.0) -> Any:
        tool = self.families.get(family)
        if tool is None:
            return None
        try:
            result = await asyncio.wait_for(self.registry.call(tool["qualified_name"], arguments), timeout)
        except Exception:  # noqa: BLE001 - documentation that cannot be fetched is simply absent
            return None
        return _payload(result) if result.get("ok") else None

    async def find(self, table: str, listing: list[dict] | None = None) -> dict | None:
        """The catalog's entry for a table, by its listing or by search."""
        for row in listing or []:
            if _matches(row, table):
                return row
        if "search" in self.families:
            param = _first_param(self.families["search"], "query", "q", "text", "term")
            hits = _rows(await self._call("search", {param: table.split(".")[-1]}))
            for row in hits:
                if _matches(row, table):
                    return {**row, "id": row.get("dataset_id") or row.get("id")}
        return None

    async def document(self, tables: list[str], limit: int = 20) -> list[dict]:
        """What the catalog says about each table: definition, domain, column definitions."""
        listing = _rows(await self._call("list", {})) if "list" in self.families else []
        out = []
        for table in tables[:limit]:
            entry = await self.find(table, listing)
            if entry is None:
                continue
            doc = {"table": table, "catalog_id": entry.get("id") or entry.get("dataset_id") or _name_of(entry),
                   "definition": entry.get("definition") or entry.get("description") or "",
                   "domain": entry.get("domain") or "", "columns": {}}
            if "schema" in self.families and doc["catalog_id"]:
                param = _first_param(self.families["schema"], "dataset_id", "id", "table", "name")
                detail = await self._call("schema", {param: doc["catalog_id"]})
                if isinstance(detail, dict):
                    doc["definition"] = detail.get("definition") or detail.get("description") or doc["definition"]
                    doc["domain"] = detail.get("domain") or doc["domain"]
                    for column in _rows(detail.get("columns") or []):
                        name = column.get("name")
                        if not name:
                            continue
                        facts = {k: column.get(k) for k in ("definition", "calculation", "semantic_type",
                                                          "sensitivity", "description") if column.get(k)}
                        if facts:
                            doc["columns"][str(name)] = facts
            if doc["definition"] or doc["columns"]:
                out.append(doc)
        return out

    async def glossary(self, term: str) -> str:
        if "glossary" not in self.families:
            return ""
        param = _first_param(self.families["glossary"], "term", "name", "query")
        value = await self._call("glossary", {param: term})
        if isinstance(value, dict):
            return str(value.get("definition") or value.get("description") or "")
        return ""


def describe_for_prompt(catalog: Catalog) -> str:
    """The block the agent reads about the catalog, when — and only when — one is connected."""
    tools = ", ".join(t["qualified_name"] for t in catalog.tools)
    kinds = ", ".join(sorted(catalog.families)) or "its tools"
    return (f"## Data catalog: {catalog.name}\n"
            f"A data catalog is connected. It documents the data behind the sources — dataset and "
            f"column definitions, how measures are calculated, the business glossary, lineage "
            f"({kinds}). It is documentation, not a source of figures: never answer a number from "
            f"it. Use it to understand before you query — a table or column you do not know, a "
            f"business term the question uses (glossary), a measure whose calculation matters, "
            f"where a table's data comes from (lineage). When the catalog and the data disagree, "
            f"trust the data and say so. Its tools, always available: {tools}.")


def notes_text(docs: list[dict], catalog_name: str, budget: int = 4000) -> str:
    """What the catalog says about a source's tables, for `source_info`."""
    if not docs:
        return ""
    lines = [f"From the data catalog ({catalog_name}) — documentation, check it against the data:"]
    for doc in docs:
        head = f"- {doc['table']}"
        if doc.get("domain"):
            head += f" [{doc['domain']}]"
        if doc.get("definition"):
            head += f": {' '.join(str(doc['definition']).split())[:240]}"
        lines.append(head)
        for column, facts in list(doc.get("columns", {}).items())[:25]:
            text = facts.get("definition") or facts.get("description") or ""
            if facts.get("calculation"):
                text += f" (calculated: {facts['calculation']})"
            if text.strip():
                lines.append(f"    · {column}: {' '.join(text.split())[:200]}")
    out = "\n".join(lines)
    return out if len(out) <= budget else out[: budget - 1] + "…"


def merge_into_model(model: dict, docs: list[dict]) -> tuple[dict, int]:
    """Catalog definitions folded into a source model — never over what a person wrote.

    Returns the merged model and how many descriptions were added.
    """
    merged = json.loads(json.dumps(model or {}))
    tables = merged.setdefault("tables", [])
    by_name = {str(t.get("name", "")).lower(): t for t in tables}
    added = 0
    for doc in docs:
        table = by_name.get(doc["table"].lower())
        if table is None:
            table = {"name": doc["table"], "columns": []}
            tables.append(table)
            by_name[doc["table"].lower()] = table
        if doc.get("definition") and not table.get("description"):
            table["description"] = " ".join(str(doc["definition"]).split())[:300]
            added += 1
        columns = {str(c.get("name", "")).lower(): c for c in table.setdefault("columns", [])}
        for name, facts in doc.get("columns", {}).items():
            text = facts.get("definition") or facts.get("description") or ""
            if facts.get("calculation"):
                text = f"{text} Calculated: {facts['calculation']}".strip()
            if not text.strip():
                continue
            column = columns.get(name.lower())
            if column is None:
                column = {"name": name}
                table["columns"].append(column)
                columns[name.lower()] = column
            if not column.get("description"):
                column["description"] = " ".join(text.split())[:240]
                added += 1
    return merged, added


def connected(registry) -> list[Catalog]:
    return [Catalog(registry, server) for server in registry.catalogs()]
