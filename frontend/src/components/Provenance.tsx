// What an answer rests on, and how it was obtained — one line, opened on demand.
//
// Citations say "this number came from #6". This says what #6 was: the source asked, the
// exact query or code, how many rows came back, when, what it used, and a fingerprint of
// the result — derived from the run itself, not from the answer's account of it. The
// method in plain words is written on request, from that chain; the audit trail is the
// whole of it as a document someone can file.

import { Check, ChevronRight, Download, FileSearch, GitBranch, Sparkles, TriangleAlert } from 'lucide-react';
import { useState } from 'react';
import { api } from '../api';
import type { LineageNode, Message } from '../types';
import { Markdown } from './Markdown';
import { cls } from './ui';

const KIND: Record<string, string> = {
  retrieval: 'fetched', computation: 'computed', output: 'produced', other: 'step', exploration: 'explored',
};

function when(ts?: number | null): string {
  if (!ts) return '';
  return new Date(ts * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function Step({ node }: { node: LineageNode }) {
  const [open, setOpen] = useState(false);
  const long = node.operation.length > 160 || node.operation.includes('\n');
  return (
    <li className="rounded-lg border px-2.5 py-2 hairline">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-2xs">
        <span className="rounded bg-brand-50 px-1 font-mono font-medium text-brand-800 ring-1 ring-brand-500/20 dark:bg-brand-500/10 dark:text-brand-300">{node.ref}</span>
        <span className="font-medium text-zinc-700 dark:text-zinc-200">{KIND[node.kind] ?? node.kind}</span>
        <span className="dimmer">{node.source || 'app'}</span>
        <span className="font-mono dimmer">{node.tool.split('__').pop()}</span>
        {node.rows !== undefined && <span className="dimmer">· {node.rows.toLocaleString()} rows</span>}
        {node.depends_on.length > 0 && <span className="dimmer">· {node.inputs_inferred ? 'after' : 'uses'} {node.depends_on.join(', ')}</span>}
        {node.output && <span className="dimmer">· {node.output}</span>}
        {node.inferred && <span className="text-amber-600 dark:text-amber-400" title="The computation read its inputs in a way that could not be traced exactly; this retrieval came before it.">· possible input</span>}
        <span className="ml-auto font-mono dimmer" title={`sha256 of the result, first 16 hex digits${node.at ? ` · ${new Date(node.at * 1000).toISOString()}` : ''}${node.audit ? ` · audit entry ${node.audit.slice(0, 16)}` : ''}`}>
          {when(node.at)} · {node.fingerprint.slice(0, 8)}
        </span>
      </div>
      <pre onClick={() => long && setOpen((v) => !v)}
        className={cls('mt-1.5 overflow-x-auto whitespace-pre-wrap break-words rounded-md bg-zinc-50 px-2 py-1.5 font-mono text-[10.5px] leading-relaxed dim dark:bg-black/25',
          long && 'cursor-pointer', !open && long && 'max-h-[4.6rem] overflow-hidden')}>
        {node.operation}
      </pre>
    </li>
  );
}

export function Provenance({ message, conversationId }: { message: Message; conversationId: string }) {
  const lineage = message.lineage;
  const [method, setMethod] = useState(message.method?.text ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  if (!lineage || (!lineage.nodes.length && !lineage.exploration.length)) return null;
  const checks = message.checks ?? [];
  const gap = checks.some((c) => /gap/i.test(c.result));
  const steps = lineage.nodes.filter((n) => n.kind !== 'exploration');

  const explain = async () => {
    setBusy(true); setError('');
    try { setMethod((await api.explainAnswer(conversationId, message.id)).text); }
    catch (e) { setError(String((e as Error).message)); }
    finally { setBusy(false); }
  };

  return (
    <details className="group/prov mt-2.5">
      <summary className="focus-ring inline-flex cursor-pointer list-none items-center gap-1.5 rounded-md px-1 py-0.5 text-2xs dimmer transition-colors hover:text-zinc-600 dark:hover:text-zinc-300">
        <GitBranch size={11} />
        {lineage.sources.length
          ? `based on ${lineage.sources.join(', ')}`
          : 'how this was obtained'}
        <span>· {steps.length} step{steps.length === 1 ? '' : 's'}</span>
        {checks.length > 0 && (gap
          ? <span className="flex items-center gap-0.5 text-amber-600 dark:text-amber-400"><TriangleAlert size={10} /> check flagged a gap</span>
          : <span className="flex items-center gap-0.5 text-brand-700 dark:text-brand-300"><Check size={10} strokeWidth={3} /> checked</span>)}
        <ChevronRight size={10} className="transition-transform group-open/prov:rotate-90" />
      </summary>
      <div className="mt-2 space-y-3 border-l-2 border-zinc-200 pl-3 animate-fade-in dark:border-white/10">
        <div className="flex flex-wrap items-center gap-1.5">
          {!method && (
            <button onClick={() => void explain()} disabled={busy}
              className="focus-ring flex items-center gap-1 rounded-lg border px-2 py-1 text-2xs font-medium hairline hover:bg-zinc-50 disabled:opacity-60 dark:hover:bg-white/[0.04]">
              <Sparkles size={11} /> {busy ? 'Explaining…' : 'Explain the method'}
            </button>
          )}
          <a href={api.trailUrl(conversationId, message.id)} download
            className="focus-ring flex items-center gap-1 rounded-lg border px-2 py-1 text-2xs font-medium hairline hover:bg-zinc-50 dark:hover:bg-white/[0.04]">
            <Download size={11} /> Audit trail
          </a>
          <a href={api.trailUrl(conversationId, message.id, 'json')} download
            className="focus-ring flex items-center gap-1 rounded-lg px-1.5 py-1 text-2xs dimmer hover:text-zinc-700 dark:hover:text-zinc-200">
            JSON
          </a>
          {error && <span className="text-2xs text-red-600 dark:text-red-400">{error}</span>}
        </div>

        {method && (
          <div className="rounded-lg bg-zinc-50 px-3 py-2 text-[12.5px] dark:bg-white/[0.03]">
            <Markdown text={method} />
          </div>
        )}

        {steps.length > 0 && (
          <ol className="space-y-1.5">
            {steps.map((node) => <Step key={node.ref} node={node} />)}
          </ol>
        )}

        {checks.length > 0 && (
          <ul className="space-y-0.5 text-2xs">
            {checks.map((c, i) => (
              <li key={i} className="flex gap-1.5">
                <FileSearch size={11} className="mt-px shrink-0 dimmer" />
                <span><b className="font-medium text-zinc-700 dark:text-zinc-200">{c.name}</b>
                  <span className="dim"> — {c.result}</span>
                  {c.detail && <span className="dimmer"> · {c.detail}</span>}</span>
              </li>
            ))}
          </ul>
        )}

        <p className="text-2xs dimmer">
          {lineage.implicit ? 'The answer cites no evidence, so every retrieval and computation is listed. ' : ''}
          {lineage.exploration.length > 0 && `${lineage.exploration.length} exploration call${lineage.exploration.length > 1 ? 's' : ''} (schemas, notes) shaped the queries. `}
          {lineage.failed.length > 0 && `${lineage.failed.length} call${lineage.failed.length > 1 ? 's' : ''} failed along the way. `}
          Fingerprints let a re-run be compared with this one.
        </p>
      </div>
    </details>
  );
}
