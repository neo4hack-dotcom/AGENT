// Tables an analyst can work with: sort, read, total, and take to Excel.
//
// Two kinds of table reach the reader. A step's result — rows exactly as the source
// returned them — and a table the agent wrote into its answer. Both end up in a
// spreadsheet sooner or later; the only question is whether that takes one click or a
// retyping session. Everything here runs in the browser except the full-result download,
// which asks the server for every row (a step shows an excerpt, a large result was parked
// whole on disk) and comes back with a Provenance sheet.

import { ArrowDown, ArrowUp, Check, ClipboardCopy, FileSpreadsheet, FileText, Search } from 'lucide-react';
import { createContext, useContext, useMemo, useState } from 'react';
import { adminToken } from '../api';
import { cls } from './ui';

export type Cell = string | number | boolean | null | undefined;

/** Where a step's full result can be downloaded from: set by the answer that holds it. */
export const ResultExport = createContext<{ conversationId: string; messageId: string } | null>(null);

/* ------------------------------------------------------------- formatting */

// Columns whose numbers are labels, not quantities: 2026 is a year, 10432 a trade id.
const LABEL_COLUMN = /(^|_)(id|ids|year|annee|année|code|cid|pk|version|isin|lei|cusip|sedol)$|date|_no$|number$/i;
// Columns a total would be meaningless for: rates, shares, prices, ratios.
const NO_TOTAL = /percent|pct|ratio|rate|taux|share|usage|price|prix|px_|spread|yield|vol|beta|corr|%/i;

const decimalSeparator = (() => {
  try { return (1.5).toLocaleString().charAt(1) || '.'; } catch { return '.'; }
})();

const numberFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 6 });

function isYearLike(values: Cell[]): boolean {
  const numbers = values.filter((v): v is number => typeof v === 'number');
  return numbers.length > 0 && numbers.every((n) => Number.isInteger(n) && n >= 1900 && n <= 2100);
}

export function displayCell(value: Cell, column: string, raw: boolean): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'number') {
    if (raw || LABEL_COLUMN.test(column)) return String(value);
    return numberFormat.format(value);
  }
  return String(value);
}

/** A value as Excel in this reader's locale will read it when pasted: numbers without
 *  thousands separators, with the local decimal mark; text with tabs and newlines removed. */
function forPaste(value: Cell): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'number') return String(value).replace('.', decimalSeparator);
  const text = String(value).replace(/[\t\r\n]+/g, ' ');
  // "1 800 909", "72,04 %", "1,95 M€" written by the agent: keep what Excel can parse.
  const figure = /^[-+−]?[\d\s  ]+([.,]\d+)?\s?%?$/.exec(text.trim());
  if (figure) {
    let clean = text.replace(/[\s  ]/g, '').replace('−', '-');
    clean = clean.replace(/[.,](?=\d+%?$)/, decimalSeparator);
    return clean;
  }
  return text;
}

export function toTsv(columns: string[], rows: Cell[][]): string {
  return [columns.join('\t'), ...rows.map((row) => row.map(forPaste).join('\t'))].join('\n');
}

export function toCsv(columns: string[], rows: Cell[][]): string {
  const quote = (v: Cell) => {
    const text = v === null || v === undefined ? '' : String(v);
    return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  // BOM: Excel reads UTF-8 without one as Latin-1, and "Société" becomes mojibake.
  return '﻿' + [columns.map(quote).join(','), ...rows.map((row) => row.map(quote).join(','))].join('\r\n');
}

export function download(name: string, content: string | Blob, type = 'text/csv;charset=utf-8') {
  const blob = typeof content === 'string' ? new Blob([content], { type }) : content;
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/** The text a Markdown cell stands for: emphasis, code marks and citations removed. */
export function plainCell(markdown: string): string {
  return markdown.replace(/\*\*|__|`/g, '').replace(/\s*[[【]#\d+[\]】]/g, '').trim();
}

/* ---------------------------------------------------------------- actions */

export function TableActions({ columns, rows, name, full }: {
  columns: string[]; rows: Cell[][]; name: string;
  /** Server-side export of every row, when the table is a step's (possibly partial) result. */
  full?: { href: (format: 'xlsx' | 'csv') => string; total?: number };
}) {
  const [state, setState] = useState<'' | 'copied' | 'busy' | string>('');
  const copy = () => {
    navigator.clipboard?.writeText(toTsv(columns, rows)).then(() => {
      setState('copied');
      setTimeout(() => setState(''), 1400);
    }).catch(() => setState('Clipboard unavailable'));
  };
  const fetchFull = async (format: 'xlsx' | 'csv') => {
    if (!full) return;
    setState('busy');
    try {
      const token = adminToken();
      const res = await fetch(full.href(format), {
        credentials: 'same-origin', headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      const disposition = res.headers.get('content-disposition') ?? '';
      const encoded = /filename\*=utf-8''([^;]+)/i.exec(disposition)?.[1];
      const file = decodeURIComponent(encoded ?? /filename="?([^";]+)"?/.exec(disposition)?.[1] ?? `${name}.${format}`);
      download(file, await res.blob());
      setState('');
    } catch (e) {
      setState((e as Error).message || 'Export failed');
    }
  };
  const failed = state && !['copied', 'busy'].includes(state);
  const button = 'focus-ring flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-white/[0.06] dark:hover:text-zinc-200 disabled:opacity-50';
  return (
    <span className="flex items-center gap-0.5 text-2xs dimmer">
      <button onClick={copy} className={button} title="Copy as tab-separated values — paste straight into Excel">
        {state === 'copied' ? <Check size={11} className="text-emerald-500" /> : <ClipboardCopy size={11} />}
        {state === 'copied' ? 'Copied' : 'Copy for Excel'}
      </button>
      {full ? (
        <>
          <button onClick={() => void fetchFull('xlsx')} disabled={state === 'busy'} className={button}
            title={`Every row${full.total ? ` (${full.total})` : ''} as an Excel workbook, with a Provenance sheet naming the query`}>
            <FileSpreadsheet size={11} /> Excel
          </button>
          <button onClick={() => void fetchFull('csv')} disabled={state === 'busy'} className={button}
            title="Every row as CSV, with its provenance beside it">
            <FileText size={11} /> CSV
          </button>
        </>
      ) : (
        <button onClick={() => download(`${name}.csv`, toCsv(columns, rows))} className={button}
          title="Download this table as CSV">
          <FileText size={11} /> CSV
        </button>
      )}
      {failed && <span className="text-red-600 dark:text-red-400" title={state}>{state.slice(0, 60)}</span>}
    </span>
  );
}

/* ------------------------------------------------------------------ table */

type Sort = { column: number; descending: boolean } | null;

function compare(a: Cell, b: Cell): number {
  const empty = (v: Cell) => v === null || v === undefined || v === '';
  if (empty(a) && empty(b)) return 0;
  if (empty(a)) return 1;          // blanks last, whichever way the column is sorted
  if (empty(b)) return -1;
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
}

export function DataTable({ columns, rows, name, full }: {
  columns: string[]; rows: Cell[][]; name: string;
  full?: { href: (format: 'xlsx' | 'csv') => string; total?: number };
}) {
  const [sort, setSort] = useState<Sort>(null);
  const [filter, setFilter] = useState('');
  const [raw, setRaw] = useState(false);
  const [all, setAll] = useState(false);
  const yearLike = useMemo(() => columns.map((_, i) => isYearLike(rows.map((r) => r[i]))), [columns, rows]);
  const numeric = useMemo(() => columns.map((_, i) => {
    const values = rows.map((r) => r[i]).filter((v) => v !== null && v !== undefined && v !== '');
    return values.length > 0 && values.every((v) => typeof v === 'number');
  }), [columns, rows]);

  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    let out = needle ? rows.filter((row) => row.some((v) => String(v ?? '').toLowerCase().includes(needle))) : rows;
    if (sort) {
      out = [...out].sort((a, b) => {
        const order = compare(a[sort.column], b[sort.column]);
        const blankA = a[sort.column] === null || a[sort.column] === undefined || a[sort.column] === '';
        const blankB = b[sort.column] === null || b[sort.column] === undefined || b[sort.column] === '';
        return blankA || blankB ? order : (sort.descending ? -order : order);
      });
    }
    return out;
  }, [rows, filter, sort]);

  // A spreadsheet's status bar: what a reader checks first — does it add up?
  const totals = useMemo(() => columns.map((column, i) => {
    if (!numeric[i] || yearLike[i] || LABEL_COLUMN.test(column)) return null;
    const values = shown.map((r) => r[i]).filter((v): v is number => typeof v === 'number');
    if (values.length < 2) return null;
    const sum = values.reduce((a, b) => a + b, 0);
    return { sum, avg: sum / values.length, min: Math.min(...values), max: Math.max(...values),
             count: values.length, summable: !NO_TOTAL.test(column) };
  }), [columns, shown, numeric, yearLike]);
  const hasTotals = totals.some(Boolean);
  const visible = all ? shown : shown.slice(0, 50);

  const toggleSort = (i: number) => setSort((current) =>
    current?.column !== i ? { column: i, descending: numeric[i] }
      : current.descending === numeric[i] ? { column: i, descending: !numeric[i] } : null);

  return (
    <div>
      <div className="max-h-96 overflow-auto rounded-lg border hairline">
        <table className="w-full border-collapse text-[11px]">
          <thead className="sticky top-0 z-[1] bg-zinc-100/95 backdrop-blur dark:bg-zinc-900/95">
            <tr>
              {columns.map((column, i) => (
                <th key={column}
                  className={cls('whitespace-nowrap border-b px-2.5 py-1.5 font-mono font-semibold hairline dim',
                    numeric[i] ? 'text-right' : 'text-left')}>
                  <button onClick={() => toggleSort(i)} title="Sort"
                    className="focus-ring inline-flex items-center gap-1 rounded hover:text-zinc-900 dark:hover:text-zinc-100">
                    {column}
                    {sort?.column === i && (sort.descending ? <ArrowDown size={10} /> : <ArrowUp size={10} />)}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((row, r) => (
              <tr key={r} className="even:bg-zinc-50/60 dark:even:bg-white/[0.02]">
                {row.map((cell, j) => (
                  <td key={j} className={cls('max-w-[22rem] truncate px-2.5 py-1 font-mono dim',
                    typeof cell === 'number' && 'text-right tabular-nums',
                    typeof cell === 'number' && cell < 0 && !raw && 'text-red-600 dark:text-red-400')}
                    title={cell === null || cell === undefined ? '' : String(cell)}>
                    {cell === null ? <span className="dimmer">null</span>
                      : displayCell(cell, yearLike[j] ? 'year' : columns[j], raw)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
          {hasTotals && (
            <tfoot className="sticky bottom-0 bg-zinc-100/95 backdrop-blur dark:bg-zinc-900/95">
              <tr>
                {totals.map((t, i) => (
                  <td key={i} className="whitespace-nowrap border-t px-2.5 py-1 text-right font-mono tabular-nums hairline dim"
                    title={t ? `count ${t.count} · min ${numberFormat.format(t.min)} · max ${numberFormat.format(t.max)} · avg ${numberFormat.format(t.avg)}` : ''}>
                    {t ? (t.summable ? <><span className="dimmer">Σ </span>{numberFormat.format(t.sum)}</>
                                     : <><span className="dimmer">avg </span>{numberFormat.format(t.avg)}</>)
                      : i === 0 ? <span className="dimmer">{filter ? 'filtered' : 'total'}</span> : ''}
                  </td>
                ))}
              </tr>
            </tfoot>
          )}
        </table>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-2xs dimmer">
        <span>
          {filter ? `${shown.length} of ${rows.length}` : rows.length} row{rows.length > 1 ? 's' : ''} × {columns.length} columns
          {full?.total && full.total > rows.length ? ` — ${full.total} in the full result` : ''}
        </span>
        {shown.length > 50 && (
          <button onClick={() => setAll((v) => !v)}
            className="focus-ring rounded px-1 hover:text-zinc-700 dark:hover:text-zinc-200">
            {all ? 'Show first 50' : `Show all ${shown.length}`}
          </button>
        )}
        {rows.length > 8 && (
          <label className="flex items-center gap-1 rounded border px-1.5 py-0.5 hairline">
            <Search size={10} />
            <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter"
              className="w-24 bg-transparent outline-none placeholder:text-zinc-400" />
          </label>
        )}
        <button onClick={() => setRaw((v) => !v)} title="Show numbers exactly as returned"
          className={cls('focus-ring rounded px-1 hover:text-zinc-700 dark:hover:text-zinc-200', raw && 'text-brand-700 dark:text-brand-300')}>
          {raw ? 'raw' : numberFormat.format(1234.5)}
        </button>
        <span className="ml-auto" />
        <TableActions columns={columns} rows={shown} name={name} full={full} />
      </div>
    </div>
  );
}

export function useResultExport() {
  return useContext(ResultExport);
}
