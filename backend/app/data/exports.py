"""Files the reader takes away: extracts people open in Excel, not dumps they have to clean.

An extract is judged in the first ten seconds after it is opened. So the header is frozen
and filterable, numbers are numbers with thousands separators, ISO dates are real dates
Excel can sort and pivot, columns are wide enough to read, and the workbook says what it
is in its properties. CSV is written with a byte-order mark because Excel on Windows still
reads UTF-8 without one as Latin-1 and turns "Société" into mojibake.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import re
from pathlib import Path

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?")


class ExportError(ValueError):
    pass


def safe_name(name: str, extension: str) -> str:
    stem = re.sub(r"[^\w\-. ]+", "", (name or "export").strip(), flags=re.UNICODE)
    stem = re.sub(r"\s+", "-", stem).strip(".-") or "export"
    stem = re.sub(rf"\.{extension}$", "", stem, flags=re.IGNORECASE)[:80]
    return f"{stem}.{extension}"


def unique_path(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    stem, suffix, n = target.stem, target.suffix, 2
    while target.exists():
        target = folder / f"{stem}-{n}{suffix}"
        n += 1
    return target


def _columns(rows: list[dict]) -> list[str]:
    seen: dict[str, None] = {}
    for row in rows:
        for key in row:
            seen.setdefault(str(key), None)
    return list(seen)


def write_csv(rows: list[dict], path: Path) -> None:
    columns = _columns(rows)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in columns})


def write_json(rows: list[dict], path: Path) -> None:
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def write_xlsx(sheets: list[tuple[str, list[dict]]], path: Path, title: str = "") -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    book = Workbook()
    book.remove(book.active)
    header_font = Font(bold=True, color="18181B")
    header_fill = PatternFill("solid", fgColor="EEF2F0")
    used_names: set[str] = set()
    for raw_name, rows in sheets:
        name = re.sub(r"[\[\]:*?/\\]", "", raw_name or "Data")[:31] or "Data"
        base, n = name, 2
        while name in used_names:
            name = f"{base[:28]}-{n}"
            n += 1
        used_names.add(name)
        sheet = book.create_sheet(name)
        columns = _columns(rows)
        sheet.append(columns)
        for cell in sheet[1]:
            cell.font, cell.fill = header_font, header_fill
            cell.alignment = Alignment(vertical="center")
        widths = [len(str(c)) for c in columns]
        for row in rows:
            values = []
            for i, column in enumerate(columns):
                value = row.get(column)
                if isinstance(value, str) and _ISO_DAY.match(value):
                    try:
                        value = dt.date.fromisoformat(value)
                    except ValueError:
                        pass
                elif isinstance(value, str) and _ISO_TIME.match(value):
                    try:
                        value = dt.datetime.fromisoformat(value.replace("Z", ""))
                    except ValueError:
                        pass
                values.append(value)
                widths[i] = max(widths[i], min(60, len(str(value)) if value is not None else 0))
            sheet.append(values)
        for i, column in enumerate(columns, start=1):
            letter = get_column_letter(i)
            sheet.column_dimensions[letter].width = min(62, max(9, widths[i - 1] + 2))
            sample = next((r.get(column) for r in rows if r.get(column) is not None), None)
            fmt = None
            if isinstance(sample, bool):
                continue
            if isinstance(sample, float):
                fmt = "#,##0.00"
            elif isinstance(sample, int) and not re.search(r"(^|_)id$|year|code", column, re.I):
                fmt = "#,##0"
            elif isinstance(sample, str) and _ISO_DAY.match(sample):
                fmt = "yyyy-mm-dd"
            if fmt:
                for cell in sheet[letter][1:]:
                    cell.number_format = fmt
        sheet.freeze_panes = "A2"
        if rows:
            sheet.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{len(rows) + 1}"
    book.properties.title = title or "AGENT export"
    book.properties.creator = "AGENT"
    book.save(path)


def export(sheets: list[tuple[str, list[dict]]], fmt: str, folder: Path, filename: str,
           title: str = "") -> dict:
    fmt = (fmt or "xlsx").lower().strip(".")
    if fmt not in ("csv", "xlsx", "json"):
        raise ExportError(f"Unknown format '{fmt}'. Use csv, xlsx or json.")
    if not sheets or not any(rows for _, rows in sheets):
        raise ExportError("There are no rows to export.")
    if fmt != "xlsx" and len(sheets) > 1:
        raise ExportError(f"A {fmt.upper()} file holds one table. Use xlsx for several sheets, "
                          f"or export them one at a time.")
    path = unique_path(folder, safe_name(filename or title or "export", fmt))
    if fmt == "csv":
        write_csv(sheets[0][1], path)
    elif fmt == "json":
        write_json(sheets[0][1], path)
    else:
        write_xlsx(sheets, path, title)
    return {"path": str(path), "name": path.name, "format": fmt, "bytes": path.stat().st_size,
            "rows": sum(len(rows) for _, rows in sheets),
            "sheets": [name for name, _ in sheets] if fmt == "xlsx" else []}
