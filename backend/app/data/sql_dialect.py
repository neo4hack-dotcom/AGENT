"""Which SQL a source speaks, and what to write instead when a query used another dialect.

A model writes the SQL it has seen most — PostgreSQL. Against SQLite, `x::float` is a syntax
error; against Oracle, `LIMIT 10` is. In testing, a 4B model had just been told its count
was over versions, wrote the right fix in PostgreSQL, got "unrecognized token ':'" from
SQLite — and gave up on the fix, answering with the wrong figures it already had. The error
names the symptom; this names the cure, in the source's own dialect.
"""

from __future__ import annotations

import re

DIALECTS = ("sqlite", "postgres", "oracle", "clickhouse", "mysql", "duckdb", "sqlserver")

_NAMES = [("sqlite", r"sqlite"), ("clickhouse", r"clickhouse"), ("oracle", r"oracle|\bora-\d"),
          ("postgres", r"postgres|psql|pg_|redshift"), ("mysql", r"mysql|mariadb"),
          ("duckdb", r"duckdb"), ("sqlserver", r"sql ?server|mssql|t-sql")]

_SYNTAX_ERROR = re.compile(r"syntax|unrecognized token|near \"|parse|ora-009|unknown function|no such function|"
                           r"function .* does not exist|not supported|unexpected|missing keyword|"
                           r"invalid identifier|code: 62|code: 46", re.I)


def dialect_of(*texts: str) -> str:
    blob = " ".join(t for t in texts if t).lower()
    for name, pattern in _NAMES:
        if re.search(pattern, blob):
            return name
    return ""


def is_syntax_error(error: str) -> bool:
    return bool(_SYNTAX_ERROR.search(error or ""))


# (pattern in the failed SQL, the cure per dialect)
_RULES: list[tuple[str, dict[str, str]]] = [
    (r"::\s*\w+", {
        "sqlite": "`x::float` → `CAST(x AS REAL)` (or `1.0 * x`); `::int` → `CAST(x AS INTEGER)`",
        "oracle": "`x::float` → `CAST(x AS NUMBER)`",
        "mysql": "`x::float` → `CAST(x AS DECIMAL(20,6))`",
        "sqlserver": "`x::float` → `CAST(x AS FLOAT)`"}),
    (r"\bdate_trunc\s*\(", {
        "sqlite": "`DATE_TRUNC('month', d)` → `strftime('%Y-%m', d)`",
        "oracle": "`DATE_TRUNC('month', d)` → `TRUNC(d, 'MM')`",
        "clickhouse": "`DATE_TRUNC('month', d)` → `toStartOfMonth(d)`",
        "mysql": "`DATE_TRUNC('month', d)` → `DATE_FORMAT(d, '%Y-%m-01')`",
        "sqlserver": "`DATE_TRUNC('month', d)` → `DATEFROMPARTS(YEAR(d), MONTH(d), 1)`"}),
    (r"\bextract\s*\(", {
        "sqlite": "`EXTRACT(YEAR FROM d)` → `CAST(strftime('%Y', d) AS INTEGER)` (`%m` month, `%d` day)",
        "clickhouse": "`EXTRACT(YEAR FROM d)` → `toYear(d)` (`toMonth`, `toDayOfMonth`)",
        "sqlserver": "`EXTRACT(YEAR FROM d)` → `YEAR(d)`"}),
    (r"\bto_char\s*\(", {
        "sqlite": "`TO_CHAR(d, 'YYYY-MM')` → `strftime('%Y-%m', d)`",
        "clickhouse": "`TO_CHAR(d, 'YYYY-MM')` → `formatDateTime(d, '%Y-%m')`",
        "mysql": "`TO_CHAR(d, 'YYYY-MM')` → `DATE_FORMAT(d, '%Y-%m')`"}),
    (r"\bstrftime\s*\(", {
        "postgres": "`strftime('%Y-%m', d)` → `to_char(d, 'YYYY-MM')`",
        "oracle": "`strftime('%Y-%m', d)` → `TO_CHAR(d, 'YYYY-MM')`",
        "clickhouse": "`strftime('%Y-%m', d)` → `formatDateTime(d, '%Y-%m')`",
        "mysql": "`strftime('%Y-%m', d)` → `DATE_FORMAT(d, '%Y-%m')`"}),
    (r"\bilike\b", {
        "sqlite": "`ILIKE` → `LIKE` (case-insensitive for ASCII in SQLite)",
        "oracle": "`a ILIKE b` → `UPPER(a) LIKE UPPER(b)`",
        "mysql": "`ILIKE` → `LIKE` (case-insensitive by default collation)",
        "sqlserver": "`ILIKE` → `LIKE`"}),
    (r"\blimit\s+\d+", {
        "oracle": "`LIMIT n` → `FETCH FIRST n ROWS ONLY`",
        "sqlserver": "`LIMIT n` → `SELECT TOP n …`"}),
    (r"\bnow\s*\(\s*\)", {
        "sqlite": "`NOW()` → `datetime('now')` (`date('now')` for the date)",
        "oracle": "`NOW()` → `SYSDATE`"}),
    (r"\binterval\s+'", {
        "sqlite": "`d - INTERVAL '1 month'` → `date(d, '-1 month')`"}),
    (r"\bnulls\s+(first|last)\b", {
        "mysql": "`NULLS LAST` is not supported → `ORDER BY x IS NULL, x`"}),
    (r"\bfilter\s*\(\s*where\b", {
        "sqlite": "(supported from SQLite 3.30) — else `SUM(CASE WHEN … THEN 1 ELSE 0 END)`",
        "oracle": "`COUNT(*) FILTER (WHERE c)` → `SUM(CASE WHEN c THEN 1 ELSE 0 END)`",
        "mysql": "`COUNT(*) FILTER (WHERE c)` → `SUM(CASE WHEN c THEN 1 ELSE 0 END)`"}),
]


def hint(dialect: str, sql: str, error: str) -> str:
    """What to change in `sql` for `dialect`, when the error is a syntax error — or ""."""
    if not dialect or not sql or not is_syntax_error(error):
        return ""
    cures = [rules[dialect] for pattern, rules in _RULES
             if dialect in rules and re.search(pattern, sql, re.I)]
    names = {"sqlite": "SQLite", "postgres": "PostgreSQL", "oracle": "Oracle", "clickhouse": "ClickHouse",
             "mysql": "MySQL", "duckdb": "DuckDB", "sqlserver": "SQL Server"}
    if cures:
        return (f"[This source speaks {names[dialect]}. In your query: " + "; ".join(cures)
                + ". Fix those and run it again — the rest of the query can stay.]")
    return f"[This source speaks {names[dialect]}: write the query in {names[dialect]} syntax.]"
