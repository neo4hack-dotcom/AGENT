"""Draft a source's model by measuring the source.

The structural half of a semantic model is not a matter of judgement: how many rows, which
values a status column takes, where the dates start and stop, which column points at which
table, whether some rows are duplicates. Those are facts, and asking a person to type them
is asking for them to be stale. So the app measures them — through the source's own MCP
tools, the same way the agent would, read-only — and writes them into the model for the
person to annotate.

Everything goes through tools the server already exposes: its own `list_tables` and
`describe_table` when it has them (they know the dialect; this code does not), and plain
portable SELECTs through its query tool for everything else. One aggregate query per table
measures every column at once, so profiling a ten-table database is a few dozen calls, not
hundreds.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from app.data.rows import rows_from_text

_QUERY_PARAM = re.compile(r"^(sql|query|statement|q|sql_query)$", re.I)
_QUERY_NAME = re.compile(r"(query|sql|select|run|execute)", re.I)
_WRITE_NAME = re.compile(r"(write|insert|update|delete|drop|create|alter|merge|truncate)", re.I)
_LIST_NAME = re.compile(r"(^|_)(list_tables|tables|list_table)$", re.I)
_DESCRIBE_NAME = re.compile(r"(describe|table_info|get_table|table_schema|columns)", re.I)
_DB_NAME = re.compile(r"(^|_)(list_databases|databases)$", re.I)
_AMOUNT = re.compile(r"(amount|price|total|revenue|value|montant|cost|qty|quantity|prix|ca_)", re.I)
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_TEXTY = re.compile(r"(char|text|string|clob|enum|varchar|nvarchar)", re.I)

MAX_TABLES = 25
MAX_COLUMNS = 60
VALUE_LIST_MAX = 20


class ProfileError(RuntimeError):
    pass


def _q(identifier: str) -> str:
    """Quote an identifier the source itself reported. Double quotes work in SQLite,
    Postgres, Oracle and ClickHouse alike."""
    return '"' + str(identifier).replace('"', '""') + '"'


def _is_texty(column: dict) -> bool:
    return bool(_TEXTY.search(column.get("type") or "")) or not column.get("type")


class Profiler:
    def __init__(self, registry, timeout_s: float = 150.0) -> None:
        self.registry = registry
        self.timeout_s = timeout_s

    # ------------------------------------------------------------- entry point
    async def profile(self, server: dict) -> tuple[dict, dict]:
        started = time.time()
        stats = {"queries": 0, "errors": []}
        try:
            model, info = await asyncio.wait_for(self._profile(server, stats), self.timeout_s)
        except asyncio.TimeoutError as exc:
            raise ProfileError(f"Profiling took longer than {int(self.timeout_s)}s and was "
                               f"stopped. Tables measured so far are not kept; try again "
                               f"when the source is less busy.") from exc
        profile = {**info, "queries": stats["queries"], "errors": stats["errors"][:20],
                   "elapsed_ms": int((time.time() - started) * 1000)}
        return profile, model

    async def _profile(self, server: dict, stats: dict) -> tuple[dict, dict]:
        tools = [t for t in self.registry.tools() if t["server_id"] == server["id"]]
        if not tools:
            raise ProfileError("This server is not connected, so there is nothing to measure.")
        plan = self._detect(tools)
        if not plan["query"]:
            names = ", ".join(t["name"] for t in tools[:20])
            raise ProfileError(f"No read-only SQL query tool was found on this server "
                               f"(tools: {names}). Profiling needs one that takes a SELECT.")
        runner = _Runner(self.registry, plan, stats)

        database = ""
        if plan["needs_database"]:
            database = await runner.first_database()
            runner.database = database

        tables = await runner.list_tables()
        if not tables:
            raise ProfileError("The source reported no tables.")
        measured = []
        for name in tables[:MAX_TABLES]:
            try:
                measured.append(await self._measure(runner, name))
            except ProfileError as exc:
                stats["errors"].append(f"{name}: {exc}")
        joins, caveats = await self._relationships(runner, measured)
        caveats = [*caveats, *self._quality_notes(measured)]
        for table in measured:
            if table["name"] in runner.view_names:
                table["view"] = True
        model = {"tables": [self._table_entry(t) for t in measured]}
        if joins:
            model["joins"] = joins
        if caveats:
            model["caveats"] = caveats
        info = {"dialect": runner.dialect, "database": database,
                "tables_seen": len(tables), "tables_measured": len(measured),
                "tools": {k: v for k, v in plan.items() if isinstance(v, str) and v}}
        if len(tables) > MAX_TABLES:
            info["note"] = f"{len(tables)} tables found; the first {MAX_TABLES} were measured."
        return model, info

    # --------------------------------------------------------------- detection
    def _detect(self, tools: list[dict]) -> dict:
        plan: dict[str, Any] = {"query": "", "sql_param": "", "list": "", "describe": "",
                                "describe_param": "", "databases": "", "needs_database": False,
                                "db_param": ""}
        candidates = []
        for tool in tools:
            if _WRITE_NAME.search(tool["name"]) or tool.get("write"):
                continue
            schema = tool.get("input_schema") or {}
            props = schema.get("properties") or {}
            required = schema.get("required") or []
            sql_params = [p for p in props if _QUERY_PARAM.match(p)
                          and (props[p] or {}).get("type", "string") == "string"]
            if sql_params and _QUERY_NAME.search(tool["name"]):
                others = [r for r in required if r not in sql_params]
                score = (2 if "read" in tool["name"] else 0) + (1 if not others else 0)
                candidates.append((score, tool, sql_params[0], others))
            if _LIST_NAME.search(tool["name"]) and not plan["list"]:
                plan["list"] = tool["qualified_name"]
            elif _DESCRIBE_NAME.search(tool["name"]) and not plan["describe"]:
                table_param = next((p for p in props if re.search(r"table|name", p, re.I)), "")
                if table_param:
                    plan["describe"], plan["describe_param"] = tool["qualified_name"], table_param
            if _DB_NAME.search(tool["name"]) and not plan["databases"]:
                plan["databases"] = tool["qualified_name"]
        if candidates:
            candidates.sort(key=lambda c: c[0], reverse=True)
            _, tool, sql_param, others = candidates[0]
            plan["query"], plan["sql_param"] = tool["qualified_name"], sql_param
            db_params = [o for o in others if re.search(r"database|db|source|connection", o, re.I)]
            if db_params:
                plan["needs_database"], plan["db_param"] = True, db_params[0]
            elif others:
                plan["query"] = ""  # a required parameter this cannot guess
        return plan

    # --------------------------------------------------------------- measuring
    async def _measure(self, runner: "_Runner", table: str) -> dict:
        columns = await runner.columns(table)
        if not columns:
            raise ProfileError("no columns reported")
        columns = columns[:MAX_COLUMNS]
        parts = ["COUNT(*) AS n__"]
        for i, column in enumerate(columns):
            c = _q(column["name"])
            parts += [f"COUNT(DISTINCT {c}) AS d_{i}", f"COUNT({c}) AS c_{i}",
                      f"MIN({c}) AS lo_{i}", f"MAX({c}) AS hi_{i}"]
        rows = await runner.query_or_none(f"SELECT {', '.join(parts)} FROM {_q(table)}")
        if rows:
            agg = rows[0]
            total = int(agg.get("n__") or 0)
            for i, column in enumerate(columns):
                column.update(rows=total, distinct=agg.get(f"d_{i}"),
                              nulls=total - int(agg.get(f"c_{i}") or 0),
                              lo=agg.get(f"lo_{i}"), hi=agg.get(f"hi_{i}"))
        else:
            # A column type the aggregate cannot take (a CLOB, a JSON column) sinks the
            # whole statement. Fall back to a count, and measure column by column.
            count = await runner.query_or_none(f"SELECT COUNT(*) AS n FROM {_q(table)}")
            total = int((count or [{}])[0].get("n") or 0)
            for column in columns:
                c = _q(column["name"])
                one = await runner.query_or_none(
                    f"SELECT COUNT(DISTINCT {c}) AS d, COUNT({c}) AS c FROM {_q(table)}")
                if one:
                    column.update(rows=total, distinct=one[0].get("d"),
                                  nulls=total - int(one[0].get("c") or 0))
        for column in columns:
            distinct = column.get("distinct")
            if isinstance(distinct, (int, float)) and 0 < distinct <= VALUE_LIST_MAX and (
                    _is_texty(column) or distinct <= 8):
                c = _q(column["name"])
                values = await runner.query_or_none(
                    f"SELECT {c} AS v, COUNT(*) AS k FROM {_q(table)} "
                    f"GROUP BY {c} ORDER BY COUNT(*) DESC")
                if values:
                    column["values"] = [str(r.get("v")) for r in values if r.get("v") is not None]
        return {"name": table, "rows": total, "columns": columns}

    async def _relationships(self, runner: "_Runner", tables: list[dict]) -> tuple[list, list]:
        """Joins by the one convention nearly every schema follows — `customer_id` points at
        `customers.id` — and, for each, how many rows point at nothing."""
        by_name = {t["name"].lower(): t for t in tables}
        joins, caveats = [], []
        for table in tables:
            for column in table["columns"]:
                name = column["name"]
                if not name.lower().endswith("_id") or name.lower() == "id":
                    continue
                stem = name[:-3].lower()
                target = next((by_name[c] for c in (stem, stem + "s", stem + "es",
                                                     stem[:-1] + "ies" if stem.endswith("y") else "")
                               if c and c in by_name), None)
                if not target or not any(c["name"].lower() == "id" for c in target["columns"]):
                    continue
                column["references"] = f"{target['name']}.id"
                joins.append(f"{table['name']}.{name} = {target['name']}.id")
                orphans = await runner.query_or_none(
                    f"SELECT COUNT(*) AS n FROM {_q(table['name'])} WHERE {_q(name)} IS NOT NULL "
                    f"AND {_q(name)} NOT IN (SELECT {_q('id')} FROM {_q(target['name'])})")
                count = int((orphans or [{}])[0].get("n") or 0)
                if count:
                    caveats.append(f"{count} {table['name']} row(s) have a {name} that matches no "
                                   f"{target['name']}.id — they drop out of any join.")
        for table in tables:
            others = [c for c in table["columns"] if c["name"].lower() != "id"]
            has_id = len(others) < len(table["columns"])
            if not has_id or not others or len(others) > 20 or table["rows"] > 2_000_000:
                continue
            keys = ", ".join(_q(c["name"]) for c in others)
            dup = await runner.query_or_none(
                f"SELECT COUNT(*) AS g, SUM(k) AS total FROM (SELECT COUNT(*) AS k FROM "
                f"{_q(table['name'])} GROUP BY {keys} HAVING COUNT(*) > 1) dup_groups")
            if dup and int(dup[0].get("g") or 0):
                groups = int(dup[0]["g"])
                extra = int(dup[0].get("total") or 0) - groups
                # Worded as a question, not an instruction: rows identical in every column
                # but the key are sometimes a double import and sometimes two genuine events
                # the table has no column to tell apart. The data cannot say which.
                caveats.append(f"{table['name']}: {extra} row(s) are identical to another row in "
                               f"every column except id ({groups} group(s)) — check whether they "
                               f"are double entries before counting or summing them.")
        for table in tables:
            for column in table["columns"]:
                lo = column.get("lo")
                if (_AMOUNT.search(column["name"]) and isinstance(lo, (int, float)) and lo < 0):
                    neg = await runner.query_or_none(
                        f"SELECT COUNT(*) AS n FROM {_q(table['name'])} WHERE {_q(column['name'])} < 0")
                    count = int((neg or [{}])[0].get("n") or 0)
                    if count:
                        caveats.append(f"{table['name']}.{column['name']} has {count} negative "
                                       f"value(s), lowest {lo} — decide whether they are "
                                       f"corrections or errors before summing.")
        return joins, caveats

    def _quality_notes(self, tables: list[dict]) -> list[str]:
        notes = []
        for table in tables:
            for column in table["columns"]:
                nulls, rows = column.get("nulls") or 0, table["rows"]
                is_dimension = column.get("values") or column.get("references")
                if nulls and rows and nulls < rows and is_dimension:
                    notes.append(f"{table['name']}.{column['name']} is empty in {nulls} of {rows} "
                                 f"row(s) — those fall into no group when you break down by it.")
                if rows and nulls == rows:
                    notes.append(f"{table['name']}.{column['name']} is always empty.")
        return notes

    def _table_entry(self, table: dict) -> dict:
        columns = []
        for column in table["columns"]:
            entry: dict[str, Any] = {"name": column["name"]}
            if column.get("type"):
                entry["type"] = column["type"]
            if column.get("references"):
                entry["references"] = column["references"]
            values = _safe_values(column.get("values") or [])
            if values:
                entry["values"] = values
            elif column["name"].lower() == "id" or column.get("references"):
                pass  # the range of a key says nothing a question could use
            else:
                lo, hi = column.get("lo"), column.get("hi")
                if lo is not None and hi is not None and lo != hi:
                    if isinstance(lo, str) and _ISO_DATE.match(lo) and _ISO_DATE.match(str(hi)):
                        entry["range"] = [lo[:19], str(hi)[:19]]
                    elif isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
                        entry["range"] = [_num(lo), _num(hi)]
            if column.get("nulls"):
                entry["nulls"] = column["nulls"]
            if isinstance(column.get("distinct"), (int, float)):
                entry["distinct"] = int(column["distinct"])
            columns.append(entry)
        entry = {"name": table["name"], "rows": table["rows"], "columns": columns}
        if table.get("view"):
            entry["description"] = "View."
        return entry


def _safe_values(values: list[str]) -> list[str]:
    """Column values bound for the system prompt, where they are read with authority.

    Everything else the agent reads from a source is fenced as untrusted. These values are
    not: they sit in the notes an administrator is taken to have written. So a value that
    is long, or that is shaped like an instruction, is dropped here — a status column
    holds `shipped`, not a paragraph addressed to the model.
    """
    from app.agent import trust
    kept = []
    for value in values:
        text = str(value)
        if len(text) > 60 or "\n" in text or trust.scan_for_injection(text):
            continue
        kept.append(text)
    return kept


def _num(value: float) -> str:
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.2f}"
    return str(int(value))


class _Runner:
    """Calls the server's own tools, parses whatever shape they answer in, counts calls."""

    def __init__(self, registry, plan: dict, stats: dict) -> None:
        self.registry, self.plan, self.stats = registry, plan, stats
        self.database = ""
        self.dialect = "unknown"
        self.view_names: set[str] = set()

    def _args(self, extra: dict) -> dict:
        if self.plan["needs_database"] and self.database:
            return {self.plan["db_param"]: self.database, **extra}
        return extra

    async def _call(self, name: str, args: dict) -> list[dict] | None:
        self.stats["queries"] += 1
        result = await self.registry.call(name, args)
        if not result.get("ok"):
            raise ProfileError((result.get("error") or "call failed")[:200])
        text = result.get("text") or ""
        if re.match(r"^\s*(database )?error\b", text, re.I):
            raise ProfileError(text[:200])
        return rows_from_text(text)

    async def query(self, sql: str) -> list[dict]:
        rows = await self._call(self.plan["query"], self._args({self.plan["sql_param"]: sql}))
        if rows is None:
            raise ProfileError("the query tool did not return rows")
        return rows

    async def query_or_none(self, sql: str) -> list[dict] | None:
        try:
            return await self.query(sql)
        except ProfileError as exc:
            self.stats["errors"].append(f"{sql[:80]}… → {exc}")
            return None

    async def first_database(self) -> str:
        if not self.plan["databases"]:
            raise ProfileError("The query tool needs a database name and the server offers no "
                               "tool to list them.")
        rows = await self._call(self.plan["databases"], {}) or []
        names = [str(r.get("name") or r.get("database") or next(iter(r.values()), ""))
                 for r in rows if r]
        if not names:
            raise ProfileError("The server listed no databases.")
        return names[0]

    async def list_tables(self) -> list[str]:
        if self.plan["list"]:
            try:
                rows = await self._call(self.plan["list"], self._args({})) or []
                names = [_name_of(r, ("name", "table_name", "table", "TABLE_NAME")) for r in rows]
                names = [n for n in names if n and not n.startswith("sqlite_")]
                if names:
                    self.dialect = "server tools"
                    # A server's own table list usually leaves views out — and a clean view
                    # is exactly what a well-prepared source asks the agent to query.
                    return names + [v for v in await self.views() if v not in names]
            except ProfileError as exc:
                self.stats["errors"].append(f"list_tables → {exc}")
        for dialect, sql in (
            ("sqlite", "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
                       "AND name NOT LIKE 'sqlite_%' ORDER BY name"),
            ("information_schema", "SELECT table_name AS name FROM information_schema.tables "
                                   "WHERE table_schema NOT IN ('information_schema','pg_catalog',"
                                   "'system','INFORMATION_SCHEMA') ORDER BY table_name"),
        ):
            rows = await self.query_or_none(sql)
            if rows:
                self.dialect = dialect
                return [_name_of(r, ("name", "table_name")) for r in rows]
        return []

    async def views(self) -> list[str]:
        for sql in ("SELECT name FROM sqlite_master WHERE type = 'view' ORDER BY name",
                    "SELECT table_name AS name FROM information_schema.views WHERE table_schema "
                    "NOT IN ('information_schema','pg_catalog','system','INFORMATION_SCHEMA')"):
            try:
                rows = await self.query(sql)
            except ProfileError:
                continue
            names = [_name_of(r, ("name", "table_name")) for r in rows]
            self.view_names.update(n for n in names if n)
            return [n for n in names if n]
        return []

    async def columns(self, table: str) -> list[dict]:
        if self.plan["describe"]:
            try:
                rows = await self._call(self.plan["describe"],
                                        self._args({self.plan["describe_param"]: table})) or []
                cols = [{"name": _name_of(r, ("name", "column_name", "column", "COLUMN_NAME")),
                         "type": str(r.get("type") or r.get("data_type") or r.get("DATA_TYPE") or "")}
                        for r in rows]
                cols = [c for c in cols if c["name"]]
                if cols:
                    return cols
            except ProfileError as exc:
                self.stats["errors"].append(f"describe {table} → {exc}")
        for dialect, sql in (
            ("sqlite", f"SELECT name, type FROM pragma_table_info('{table.replace(chr(39), chr(39) * 2)}')"),
            ("information_schema", "SELECT column_name AS name, data_type AS type FROM "
                                   f"information_schema.columns WHERE table_name = "
                                   f"'{table.replace(chr(39), chr(39) * 2)}' ORDER BY ordinal_position"),
        ):
            rows = await self.query_or_none(sql)
            if rows:
                self.dialect = dialect if self.dialect == "unknown" else self.dialect
                return [{"name": str(r.get("name")), "type": str(r.get("type") or "")} for r in rows]
        return []


def _name_of(row: Any, keys: tuple[str, ...]) -> str:
    if not isinstance(row, dict):
        return str(row or "")
    for key in keys:
        if row.get(key):
            return str(row[key])
    return str(next(iter(row.values()), "") or "")
