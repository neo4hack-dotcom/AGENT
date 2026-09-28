"""SQLite over MCP, with nothing but the standard library.

    python server.py --db-path /data/trades.db [--allow-write]

Bundled so that a SQLite source needs no download: the popular server is fetched from PyPI
on first run, which a private network cannot do. Same tool names (`list_tables`,
`describe_table`, `read_query`), so anything written against that one works here.

Read-only unless told otherwise, and enforced where it cannot be argued with: the database
is opened with SQLite's own `mode=ro`, so a write fails in the engine, not in a regex. The
statement check in front of it only exists to give a clear message first. Queries are cut
off after a time limit and a row ceiling, so one careless `SELECT *` cannot hang a turn.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _mcp_stdio import McpServer, log  # noqa: E402

MAX_ROWS = 50_000
TIME_LIMIT_S = 60.0
_READ = re.compile(r"^\s*(select|with|explain|pragma\s+table_info|pragma\s+index_list|values)\b", re.I)

server = McpServer("sqlite-db", "1.0.0", "SQLite")
STATE: dict = {"path": "", "write": False}


def _connect() -> sqlite3.Connection:
    path = STATE["path"]
    if STATE["write"]:
        con = sqlite3.connect(path, timeout=10)
    else:
        # as_uri(): `file:///C:/…` on Windows, and spaces, `?` or `#` in a path escaped.
        con = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    started = time.monotonic()
    # Abort a runaway query from inside the engine, every ~100k VM steps.
    con.set_progress_handler(lambda: int(time.monotonic() - started > TIME_LIMIT_S), 100_000)
    return con


def _single(sql: str) -> str:
    body = (sql or "").strip().rstrip(";").strip()
    if not body:
        raise ValueError("The query is empty.")
    if ";" in re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"", "", body):
        raise ValueError("One statement per call.")
    return body


def _rows(con: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    cursor = con.execute(sql, params)
    out = []
    for row in cursor:
        out.append(dict(row))
        if len(out) > MAX_ROWS:
            raise ValueError(f"More than {MAX_ROWS:,} rows. Aggregate in SQL (GROUP BY, SUM, COUNT) "
                             f"or add a WHERE clause.")
    return out


@server.tool("list_tables", "List the tables and views of the database.", {"type": "object", "properties": {}},
             read_only=True)
def list_tables() -> list[dict]:
    with _connect() as con:
        return _rows(con, "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') "
                          "AND name NOT LIKE 'sqlite_%' ORDER BY type, name")


@server.tool("describe_table", "Columns of one table or view: name, type, nullability, primary key.",
             {"type": "object", "properties": {"table_name": {"type": "string"}}, "required": ["table_name"]},
             read_only=True)
def describe_table(table_name: str) -> list[dict] | dict:
    with _connect() as con:
        known = {r["name"] for r in _rows(con, "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        if table_name not in known:
            return {"error": f"No table '{table_name}'. Tables: {', '.join(sorted(known))}."}
        return _rows(con, "SELECT cid, name, type, \"notnull\", dflt_value, pk FROM pragma_table_info(?)",
                     (table_name,))


@server.tool("read_query", "Run one read-only SELECT (or WITH … SELECT) and return its rows as JSON.",
             {"type": "object", "properties": {"query": {"type": "string", "description": "One SELECT statement."}},
              "required": ["query"]}, read_only=True)
def read_query(query: str) -> list[dict] | dict:
    try:
        sql = _single(query)
    except ValueError as exc:
        return {"error": str(exc)}
    if not _READ.match(sql):
        return {"error": "read_query runs SELECT statements only."}
    try:
        with _connect() as con:
            return _rows(con, sql)
    except sqlite3.OperationalError as exc:
        message = str(exc)
        if "interrupted" in message:
            message = f"The query ran past {TIME_LIMIT_S:.0f}s and was stopped. Narrow it or aggregate."
        return {"error": f"SQL error: {message}"}
    except ValueError as exc:
        return {"error": str(exc)}


def _register_writes() -> None:
    @server.tool("write_query", "Run one INSERT, UPDATE or DELETE.",
                 {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]})
    def write_query(query: str) -> dict:
        try:
            sql = _single(query)
        except ValueError as exc:
            return {"error": str(exc)}
        if not re.match(r"^\s*(insert|update|delete)\b", sql, re.I):
            return {"error": "write_query runs INSERT, UPDATE or DELETE only."}
        with _connect() as con:
            changed = con.execute(sql).rowcount
            con.commit()
        return {"ok": True, "rows_affected": changed}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--allow-write", action="store_true")
    args = parser.parse_args()
    path = Path(args.db_path).expanduser().resolve()
    if not path.is_file():
        log(f"No database at {path}")
        sys.exit(2)
    STATE.update(path=str(path), write=args.allow_write)
    if args.allow_write:
        _register_writes()
    server.run()


if __name__ == "__main__":
    main()
