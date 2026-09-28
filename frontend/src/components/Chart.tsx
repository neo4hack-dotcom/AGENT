// A chart the agent drew, drawn here from the same Vega-Lite spec the PDF uses.
//
// The spec arrives with its rows and without a theme; the house style and the number and
// date locale come from the backend (/api/charts/theme) — the one place they are defined —
// so the chart on screen and the chart on paper cannot drift apart. Vega is loaded on first
// use: most answers have no chart, and a megabyte of charting code is not something to make
// every page load pay for.
//
// Hovering a mark shows what it is; clicking pins that bubble, so a value can be read, and
// compared with another, without holding the mouse still.

import { DataTable } from './DataTable';
import { Code2, Download, Image as ImageIcon, PenLine, Table2, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import type { ChartOut } from '../types';
import { cls } from './ui';

type Theme = Awaited<ReturnType<typeof api.chartTheme>>;
let themePromise: Promise<Theme> | null = null;
const loadTheme = () => (themePromise ??= api.chartTheme().catch(() => ({ light: {}, dark: {} }) as Theme));

type Picked = { tooltip?: unknown; datum?: Record<string, unknown> };

/** The mark closest to a click, within reach — found through the drawing itself. */
function nearest(root: HTMLElement, x: number, y: number, reach = 36): Picked | null {
  let best: Picked | null = null;
  let bestDistance = reach;
  root.querySelectorAll<SVGGraphicsElement>('.mark-symbol path, .mark-rect path, .mark-arc path')
    .forEach((element) => {
      const bound = (element as unknown as { __data__?: Picked }).__data__;
      if (!bound?.tooltip) return;
      const r = element.getBoundingClientRect();
      const dx = Math.max(r.left - x, 0, x - r.right);
      const dy = Math.max(r.top - y, 0, y - r.bottom);
      const distance = Math.hypot(dx, dy);
      if (distance < bestDistance) { bestDistance = distance; best = bound; }
    });
  return best;
}

/** A pinned reading of one mark: where it was clicked, and what Vega says it is. */
interface Pin { x: number; y: number; entries: [string, string][] }

function readingOf(tooltip: unknown, datum: Record<string, unknown> | undefined): [string, string][] {
  if (tooltip && typeof tooltip === 'object' && !Array.isArray(tooltip)) {
    return Object.entries(tooltip as Record<string, unknown>)
      .filter(([, v]) => v !== undefined && v !== null && v !== '')
      .map(([k, v]) => [k, String(v)]);
  }
  if (typeof tooltip === 'string' || typeof tooltip === 'number') return [['', String(tooltip)]];
  // No tooltip on this mark: the row itself, minus Vega's own bookkeeping.
  return Object.entries(datum ?? {})
    .filter(([k, v]) => !k.startsWith('_') && !k.startsWith('Symbol') && v !== null && typeof v !== 'object')
    .slice(0, 8)
    .map(([k, v]) => [k, typeof v === 'number' ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(v)]);
}
const loadEmbed = () => import('vega-embed').then((m) => m.default);

function useDark(): boolean {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'));
  useEffect(() => {
    const observer = new MutationObserver(() =>
      setDark(document.documentElement.classList.contains('dark')));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    return () => observer.disconnect();
  }, []);
  return dark;
}

function merge(base: Record<string, unknown>, over: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = { ...base };
  for (const [key, value] of Object.entries(over ?? {})) {
    const current = out[key];
    out[key] = current && typeof current === 'object' && !Array.isArray(current)
      && value && typeof value === 'object' && !Array.isArray(value)
      ? merge(current as Record<string, unknown>, value as Record<string, unknown>)
      : value;
  }
  return out;
}

function download(url: string, name: string) {
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
}

export function ChartView({ chart }: { chart: ChartOut }) {
  const host = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLElement>(null);
  const view = useRef<{ toImageURL: (type: string, scale?: number) => Promise<string>; finalize: () => void } | null>(null);
  const dark = useDark();
  const [error, setError] = useState<string | null>(null);
  const [panel, setPanel] = useState<'none' | 'data' | 'spec'>('none');
  const [pin, setPin] = useState<Pin | null>(null);

  useEffect(() => {
    if (!pin) return;
    const close = (e: KeyboardEvent) => { if (e.key === 'Escape') setPin(null); };
    const outside = (e: MouseEvent) => { if (!frame.current?.contains(e.target as Node)) setPin(null); };
    window.addEventListener('keydown', close);
    window.addEventListener('mousedown', outside);
    return () => { window.removeEventListener('keydown', close); window.removeEventListener('mousedown', outside); };
  }, [pin]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [embed, theme] = await Promise.all([loadEmbed(), loadTheme()]);
        if (cancelled || !host.current) return;
        const spec = {
          ...chart.spec,
          config: merge((dark ? theme.dark : theme.light) ?? {},
            (chart.spec.config as Record<string, unknown>) ?? {}),
          background: dark ? null : '#ffffff00',
          autosize: chart.spec.autosize ?? { type: 'fit-x', contains: 'padding' },
        };
        // ast: expressions are interpreted, never compiled to JavaScript — a spec written from
        // data the agent read cannot become code, and the page's CSP needs no 'unsafe-eval'.
        const result = await embed(host.current, spec as never, {
          ast: true, actions: false, renderer: 'svg', tooltip: { theme: dark ? 'dark' : 'light' },
          ...(theme.locale ? { formatLocale: theme.locale.format, timeFormatLocale: theme.locale.time } : {}),
        } as never);
        if (cancelled) { result.finalize(); return; }
        view.current?.finalize();
        view.current = result.view as unknown as typeof view.current;
        result.view.addEventListener('click', (event, item) => {
          const box = frame.current?.getBoundingClientRect();
          const mouse = event as MouseEvent;
          let target = item as Picked | null | undefined;
          // A dot is seven pixels wide. A click near one means that one.
          if (!target?.tooltip && host.current) target = nearest(host.current, mouse.clientX, mouse.clientY) ?? target;
          const entries = target ? readingOf(target.tooltip, target.datum) : [];
          if (!box || !entries.length) { setPin(null); return; }
          setPin({ x: mouse.clientX - box.left, y: mouse.clientY - box.top, entries });
        });
        setError(null);
      } catch (e) {
        if (!cancelled) setError(String((e as Error).message || e));
      }
    })();
    return () => { cancelled = true; };
  }, [chart, dark]);

  useEffect(() => () => view.current?.finalize(), []);

  const save = async (type: 'png' | 'svg') => {
    if (!view.current) return;
    const url = await view.current.toImageURL(type, type === 'png' ? 2 : 1);
    download(url, `${chart.title || chart.id}.${type}`.replace(/[^\w.\- ]+/g, ''));
  };
  const columns = chart.data.length ? Object.keys(chart.data[0]) : [];

  return (
    <figure ref={frame} className="group/chart relative my-4 overflow-hidden rounded-xl border bg-white p-3 hairline dark:bg-white/[0.02]">
      <div className="absolute right-2 top-2 z-10 flex gap-0.5 rounded-lg border bg-white/90 p-0.5 opacity-0 shadow-sm backdrop-blur transition-opacity hairline group-hover/chart:opacity-100 focus-within:opacity-100 dark:bg-zinc-900/90">
        <ToolButton label="Modify this chart" icon={PenLine}
          onClick={() => window.dispatchEvent(new CustomEvent('agent:prefill',
            { detail: `Chart ${chart.id}: ` }))} />
        <ToolButton label="Data" icon={Table2} active={panel === 'data'}
          onClick={() => setPanel((p) => (p === 'data' ? 'none' : 'data'))} />
        <ToolButton label="Spec" icon={Code2} active={panel === 'spec'}
          onClick={() => setPanel((p) => (p === 'spec' ? 'none' : 'spec'))} />
        <ToolButton label="Download PNG" icon={ImageIcon} onClick={() => void save('png')} />
        <ToolButton label="Download SVG" icon={Download} onClick={() => void save('svg')} />
      </div>
      {error ? (
        <p className="px-2 py-6 text-center text-xs text-red-600 dark:text-red-400">
          This chart could not be drawn: {error}
        </p>
      ) : (
        <div ref={host} className="w-full [&_svg]:max-w-full" />
      )}
      {pin && <PinnedReading pin={pin} width={frame.current?.clientWidth ?? 600} onClose={() => setPin(null)} />}
      <figcaption className="mt-1.5 flex flex-wrap items-center gap-x-2 px-1 text-2xs dimmer">
        <span className="font-mono">{chart.id}{chart.version > 1 ? ` · v${chart.version}` : ''}</span>
        {chart.source && <span>· data: {chart.source}</span>}
        {chart.typed && (
          <span className="text-amber-600 dark:text-amber-400"
            title="These values were written into the call by the agent rather than taken from a query result.">
            · values not from a query
          </span>
        )}
      </figcaption>
      {panel === 'data' && (
        // The chart's own rows, as a table an analyst can sort, total and take to Excel.
        <div className="mt-2 animate-fade-in">
          <DataTable columns={columns}
            rows={chart.data.slice(0, 5000).map((row) => columns.map((c) => {
              const value = row[c];
              return value === undefined ? null : (value as string | number | boolean | null);
            }))}
            name={(chart.title || chart.id).replace(/[^\w.\- ]+/g, '')} />
        </div>
      )}
      {panel === 'spec' && (
        <pre className="mt-2 max-h-72 overflow-auto rounded-lg border px-3 py-2 font-mono text-[11px] leading-relaxed hairline dim animate-fade-in">
          {JSON.stringify({ ...chart.spec, data: { values: `${chart.data.length} rows` } }, null, 2)}
        </pre>
      )}
    </figure>
  );
}

function PinnedReading({ pin, width, onClose }: { pin: Pin; width: number; onClose: () => void }) {
  const [head, ...rest] = pin.entries;
  const left = Math.max(8, Math.min(pin.x + 14, width - 248));
  const above = pin.y > 150;
  return (
    <div role="dialog" aria-label="Value at this point"
      className="absolute z-20 w-[236px] rounded-xl border bg-white/95 px-3 py-2.5 text-[12px] shadow-lg backdrop-blur hairline animate-fade-in dark:bg-zinc-900/95"
      style={{ left, top: above ? undefined : pin.y + 14, bottom: above ? `calc(100% - ${pin.y - 10}px)` : undefined }}>
      <button onClick={onClose} aria-label="Close"
        className="focus-ring absolute right-1.5 top-1.5 grid h-5 w-5 place-items-center rounded dimmer hover:bg-zinc-100 dark:hover:bg-white/[0.07]">
        <X size={11} />
      </button>
      {head && (
        <p className="pr-5 leading-snug">
          {head[0] && <span className="block text-2xs dimmer">{head[0]}</span>}
          <span className="font-semibold text-zinc-900 dark:text-white">{head[1]}</span>
        </p>
      )}
      {rest.length > 0 && (
        <dl className="mt-1.5 space-y-1 border-t pt-1.5 hairline">
          {rest.map(([label, value]) => (
            <div key={label} className="flex items-baseline justify-between gap-3">
              <dt className="min-w-0 truncate text-2xs dim">{label}</dt>
              <dd className="shrink-0 font-medium tabular-nums text-zinc-800 dark:text-zinc-100">{value}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function ToolButton({ label, icon: Icon, onClick, active }: {
  label: string; icon: typeof Download; onClick: () => void; active?: boolean;
}) {
  return (
    <button onClick={onClick} title={label} aria-label={label}
      className={cls('focus-ring grid h-7 w-7 place-items-center rounded-md transition-colors',
        active ? 'bg-brand-100 text-brand-800 dark:bg-brand-500/15 dark:text-brand-300'
               : 'dimmer hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-white/[0.07] dark:hover:text-zinc-200')}>
      <Icon size={13} strokeWidth={1.9} />
    </button>
  );
}
