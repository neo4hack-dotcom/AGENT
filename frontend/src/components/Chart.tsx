// A chart the agent drew, drawn here from the same Vega-Lite spec the PDF uses.
//
// The spec arrives with its rows and without a theme; the house style comes from the
// backend (/api/charts/theme) — the one place it is defined — so the chart on screen and the
// chart on paper cannot drift apart. Vega is loaded on first use: most answers have no chart,
// and a megabyte of charting code is not something to make every page load pay for.

import { Code2, Download, Image as ImageIcon, PenLine, Table2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import type { ChartOut } from '../types';
import { cls } from './ui';

type Theme = { light: Record<string, unknown>; dark: Record<string, unknown> };
let themePromise: Promise<Theme> | null = null;
const loadTheme = () => (themePromise ??= api.chartTheme().catch(() => ({ light: {}, dark: {} })));
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
  const view = useRef<{ toImageURL: (type: string, scale?: number) => Promise<string>; finalize: () => void } | null>(null);
  const dark = useDark();
  const [error, setError] = useState<string | null>(null);
  const [panel, setPanel] = useState<'none' | 'data' | 'spec'>('none');

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
        const result = await embed(host.current, spec as never, {
          actions: false, renderer: 'svg', tooltip: { theme: dark ? 'dark' : 'light' },
        });
        if (cancelled) { result.finalize(); return; }
        view.current?.finalize();
        view.current = result.view as unknown as typeof view.current;
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
    <figure className="group/chart relative my-4 overflow-hidden rounded-xl border bg-white p-3 hairline dark:bg-white/[0.02]">
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
      <figcaption className="mt-1.5 flex flex-wrap items-center gap-x-2 px-1 text-2xs dimmer">
        <span className="font-mono">{chart.id}{chart.version > 1 ? ` · v${chart.version}` : ''}</span>
        {chart.source && <span>· data: {chart.source}</span>}
        {chart.typed && (
          <span className="text-amber-600 dark:text-amber-400"
            title="These values were written into the call by the agent rather than taken from a query result.">
            · values typed by the agent
          </span>
        )}
      </figcaption>
      {panel === 'data' && (
        <div className="mt-2 max-h-72 overflow-auto rounded-lg border hairline animate-fade-in">
          <table className="w-full border-collapse text-[11px]">
            <thead className="sticky top-0 bg-zinc-100/95 dark:bg-zinc-900/95">
              <tr>{columns.map((c) => (
                <th key={c} className="whitespace-nowrap border-b px-2.5 py-1.5 text-left font-mono font-semibold hairline dim">{c}</th>
              ))}</tr>
            </thead>
            <tbody>
              {chart.data.slice(0, 500).map((row, i) => (
                <tr key={i} className="even:bg-zinc-50/60 dark:even:bg-white/[0.02]">
                  {columns.map((c) => {
                    const value = row[c];
                    return (
                      <td key={c} className={cls('px-2.5 py-1 font-mono dim',
                        typeof value === 'number' && 'text-right tabular-nums')}>
                        {value === null || value === undefined ? '' : typeof value === 'number'
                          ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(value)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
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
