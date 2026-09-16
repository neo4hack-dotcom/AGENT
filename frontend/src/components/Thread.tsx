// The conversation: user turns, and everything the agent did to answer them.
//
// The design rule here is "quiet until it matters". A tool call is one line you can
// ignore; the moment something fails, needs you, or is worth reading, it grows. The
// alternative — a log pane that shows everything at full size — is what makes agent UIs
// feel like watching a build run instead of reading an answer.

import {
  AlertTriangle, Ban, Brain, Check, ChevronRight, Clock, FileText, FolderOpen, Globe,
  Layers, ListChecks, Pencil, Plug, RotateCcw, Save, Search, ShieldAlert, ShieldQuestion,
  Sparkles, Terminal, X, type LucideIcon,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { Block, Message, PlanStep, Usage } from '../types';
import { Markdown } from './Markdown';
import { Badge, Button, Spinner, cls } from './ui';

const TOOL_ICONS: Record<string, LucideIcon> = {
  web_search: Search, web_fetch: Globe, run_python: Terminal, workspace_read: FileText,
  workspace_write: Save, workspace_import: Save, workspace_list: FolderOpen,
  remember: Brain, recall: Brain, plan: ListChecks, current_time: Clock,
  find_tools: Search,
};

function toolIcon(block: Block): LucideIcon {
  return TOOL_ICONS[block.name ?? ''] ?? (block.kind === 'mcp' ? Plug : Sparkles);
}

function duration(ms?: number): string {
  if (!ms) return '';
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`;
}

function argSummary(args: Record<string, unknown> | undefined): string {
  if (!args) return '';
  const entries = Object.entries(args);
  if (entries.length === 0) return '';
  const [, first] = entries[0];
  const text = typeof first === 'string' ? first : JSON.stringify(first);
  return (text ?? '').replace(/\s+/g, ' ').slice(0, 110);
}

/* ------------------------------------------------------------------- plan */

export function PlanCard({ steps, live }: { steps: PlanStep[]; live?: boolean }) {
  const done = steps.filter((s) => s.status === 'done').length;
  // A finished run whose plan is still all-pending did the work without reporting it. The
  // counter is the literal truth and stays as it is — checking boxes the agent never
  // checked would be fabricating progress, which is the one thing this app does not do —
  // but "0/3" alone reads as "nothing happened", so the card says which it is.
  const unreported = !live && done < steps.length;
  return (
    <div className="my-3 overflow-hidden rounded-xl border hairline bg-zinc-50/60 dark:bg-white/[0.03] animate-fade-up">
      <div className="flex items-center gap-2 border-b px-3.5 py-2 hairline">
        <ListChecks size={13} className="text-brand-500" />
        <span className="text-2xs font-semibold uppercase tracking-wider dim">Plan</span>
        <span className="ml-auto font-mono text-2xs dimmer">{done}/{steps.length}</span>
      </div>
      <ol className="divide-y divide-zinc-200/70 dark:divide-white/[0.06]">
        {steps.map((step) => (
          <li key={step.index} className="flex items-start gap-2.5 px-3.5 py-2">
            <span className={cls('mt-[3px] grid h-3.5 w-3.5 shrink-0 place-items-center rounded-full border text-[8px] transition-colors',
              step.status === 'done' && 'border-emerald-500 bg-emerald-500 text-white',
              step.status === 'active' && 'border-brand-500 bg-brand-500/15',
              step.status === 'skipped' && 'border-zinc-300 dark:border-white/15',
              step.status === 'pending' && 'hairline')}>
              {step.status === 'done' && <Check size={9} strokeWidth={3.5} />}
              {step.status === 'active' && <span className="h-1.5 w-1.5 rounded-full bg-brand-500 animate-breathe" />}
            </span>
            <span className={cls('text-[13px] leading-snug',
              step.status === 'done' && 'dim line-through decoration-zinc-300',
              step.status === 'skipped' && 'dimmer line-through',
              step.status === 'active' && 'font-medium text-zinc-900 dark:text-white')}>
              {step.title}
            </span>
          </li>
        ))}
      </ol>
      {unreported && (
        <p className="border-t px-3.5 py-2 text-2xs leading-relaxed hairline dimmer">
          The agent stopped updating this plan partway. What it actually did is the tool
          calls below, not this list.
        </p>
      )}
    </div>
  );
}

/* -------------------------------------------------------------- thinking */

function ThinkingBlock({ block, live }: { block: Block; live: boolean }) {
  const [open, setOpen] = useState(false);
  const text = block.text ?? '';
  if (!text.trim()) return null;
  const tail = text.replace(/\s+/g, ' ').trim().slice(-90);
  return (
    <div className="my-2">
      <button onClick={() => setOpen((v) => !v)}
        className="focus-ring group flex w-full items-center gap-2 rounded-lg py-1 text-left">
        <Brain size={13} className={cls('shrink-0', live ? 'text-brand-500 animate-breathe' : 'dimmer')} />
        <span className="text-2xs font-medium uppercase tracking-wider dim">
          {live ? 'Thinking' : 'Reasoning'}
        </span>
        {!open && (
          <span className="min-w-0 flex-1 truncate text-2xs italic dimmer">{tail}</span>
        )}
        <ChevronRight size={13} className={cls('ml-auto shrink-0 dimmer transition-transform', open && 'rotate-90')} />
      </button>
      {open && (
        <div className="mt-1 whitespace-pre-wrap border-l-2 border-brand-400/25 pl-3 font-mono text-[11.5px] leading-relaxed dim animate-fade-in">
          {text}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ tools */

/**
 * A query returned rows; show them as rows.
 *
 * Tool results arrive as JSON text, and a fifty-row SELECT rendered as raw JSON is
 * unreadable at exactly the moment the reader most wants to check the agent's arithmetic.
 * Strictly opt-in: anything that is not a clean rectangle of scalars falls back to the
 * JSON, because a half-parsed table is worse than none.
 */
function asTable(raw: string): { columns: string[]; rows: unknown[][] } | null {
  if (raw.length > 400_000) return null;
  let value: unknown;
  try { value = JSON.parse(raw); } catch { return null; }
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    for (const key of ['rows', 'records', 'data', 'results']) {
      const inner = (value as Record<string, unknown>)[key];
      if (Array.isArray(inner)) { value = inner; break; }
    }
  }
  if (!Array.isArray(value) || value.length === 0 || value.length > 500) return null;
  const scalar = (v: unknown) => v === null || ['string', 'number', 'boolean'].includes(typeof v);
  if (!value.every((row) => row && typeof row === 'object' && !Array.isArray(row)
                   && Object.values(row as object).every(scalar))) return null;
  const columns: string[] = [];
  for (const row of value as Record<string, unknown>[]) {
    for (const key of Object.keys(row)) if (!columns.includes(key)) columns.push(key);
  }
  if (columns.length === 0 || columns.length > 24) return null;
  return { columns, rows: (value as Record<string, unknown>[]).map((r) => columns.map((c) => r[c])) };
}

function ResultTable({ table }: { table: { columns: string[]; rows: unknown[][] } }) {
  const [all, setAll] = useState(false);
  const rows = all ? table.rows : table.rows.slice(0, 50);
  return (
    <div>
      <div className="max-h-80 overflow-auto rounded-lg border hairline">
        <table className="w-full border-collapse text-[11px]">
          <thead className="sticky top-0 bg-zinc-100/95 backdrop-blur dark:bg-zinc-900/95">
            <tr>
              {table.columns.map((column) => (
                <th key={column}
                  className="whitespace-nowrap border-b px-2.5 py-1.5 text-left font-mono font-semibold hairline dim">
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="even:bg-zinc-50/60 dark:even:bg-white/[0.02]">
                {row.map((cell, j) => (
                  <td key={j} className={cls('max-w-[22rem] truncate px-2.5 py-1 font-mono dim',
                    typeof cell === 'number' && 'text-right tabular-nums')}
                    title={cell === null ? '' : String(cell)}>
                    {cell === null ? <span className="dimmer">null</span> : String(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-1 flex items-center gap-2 text-2xs dimmer">
        <span>{table.rows.length} row{table.rows.length > 1 ? 's' : ''} × {table.columns.length} columns</span>
        {table.rows.length > 50 && (
          <button onClick={() => setAll((v) => !v)}
            className="focus-ring rounded px-1 hover:text-zinc-700 dark:hover:text-zinc-200">
            {all ? 'Show first 50' : `Show all ${table.rows.length}`}
          </button>
        )}
      </div>
    </div>
  );
}

function ToolBlock({ block, cite }: { block: Block; cite?: string }) {
  const [open, setOpen] = useState(false);
  const Icon = toolIcon(block);
  const running = block.status === 'running' || block.status === 'awaiting_approval';
  const failed = block.ok === false;
  const denied = block.status === 'denied' || block.status === 'expired';
  const blocked = block.status === 'blocked';
  const flagged = (block.injection?.length ?? 0) > 0;
  const table = useMemo(
    () => (open && !failed ? asTable((block.text || '').trim()) : null),
    [open, failed, block.text]);

  return (
    <div className="my-1.5" id={cite && block.ref ? `${cite}${block.ref.slice(1)}` : undefined}>
      <button onClick={() => setOpen((v) => !v)}
        className={cls('focus-ring group flex w-full items-center gap-2 rounded-lg border px-2.5 py-1.5 text-left transition-colors',
          blocked ? 'border-amber-300/70 bg-amber-50/60 dark:border-amber-500/25 dark:bg-amber-500/[0.07]'
          : failed ? 'border-red-300/60 bg-red-50/50 dark:border-red-500/25 dark:bg-red-500/[0.07]'
                   : 'hairline hover:bg-zinc-50 dark:hover:bg-white/[0.04]')}>
        <span className={cls('grid h-5 w-5 shrink-0 place-items-center rounded-md',
          failed ? 'bg-red-100 text-red-600 dark:bg-red-500/15 dark:text-red-400'
                 : denied ? 'bg-zinc-100 text-zinc-500 dark:bg-white/[0.07]'
                 : 'bg-brand-100 text-brand-800 dark:bg-brand-500/12 dark:text-brand-300')}>
          {running ? <Spinner size={11} /> : denied ? <Ban size={11} />
            : failed ? <X size={11} strokeWidth={3} /> : <Icon size={11} />}
        </span>
        {block.ref && (
          <span className="shrink-0 font-mono text-2xs text-brand-800/70 dark:text-brand-300/70">
            {block.ref}
          </span>
        )}
        <span className="shrink-0 font-mono text-2xs font-medium text-zinc-700 dark:text-zinc-200">
          {block.name}
        </span>
        {block.by === 'critic' && <Badge tone="brand">critic</Badge>}
        {block.cached && <Badge>already fetched</Badge>}
        {/* The document tried to give the agent orders. It did not get them — but you
            should know it tried, because that is a fact about the source. */}
        {flagged && (
          <Badge tone="warn" className="gap-1">
            <ShieldAlert size={9} /> injection blocked
          </Badge>
        )}
        {block.offloaded && <Badge className="gap-1"><Layers size={9} /> saved in full</Badge>}
        {!!block.redacted && <Badge tone="warn">secret removed</Badge>}
        <span className="min-w-0 flex-1 truncate text-2xs dimmer">
          {running ? argSummary(block.args) : (block.summary || argSummary(block.args))}
        </span>
        {!!block.ms && <span className="shrink-0 font-mono text-2xs dimmer">{duration(block.ms)}</span>}
        <ChevronRight size={12} className={cls('shrink-0 dimmer transition-transform', open && 'rotate-90')} />
      </button>
      {open && (
        <div className="mt-1 space-y-2 rounded-lg border px-3 py-2.5 hairline bg-zinc-50/60 dark:bg-black/20 animate-fade-in">
          <div>
            <div className="mb-1 text-2xs font-semibold uppercase tracking-wider dimmer">
              Call{block.server ? ` · ${block.server}` : ''}
            </div>
            <pre className="overflow-x-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed dim">
              {JSON.stringify(block.args ?? {}, null, 2)}
            </pre>
          </div>
          {flagged && (
            <div className="rounded-lg border border-amber-300/70 bg-amber-50/60 px-2.5 py-2 text-2xs leading-relaxed text-amber-900 dark:border-amber-500/25 dark:bg-amber-500/[0.07] dark:text-amber-200">
              This content contains text shaped like instructions to the agent
              ({block.injection!.join(', ')}). It was fenced as data and reported, not obeyed.
            </div>
          )}
          {block.offloaded && (
            <p className="text-2xs dimmer">
              Full result saved to <code className="font-mono">{block.offloaded}</code> —
              the agent kept an excerpt in context and can read the rest on demand.
            </p>
          )}
          {(block.text || block.summary) && (
            <div>
              <div className="mb-1 text-2xs font-semibold uppercase tracking-wider dimmer">
                {failed ? 'Error' : 'Result'}
              </div>
              {table ? <ResultTable table={table} /> : (
                <pre className={cls('max-h-72 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed',
                  failed ? 'text-red-600 dark:text-red-400' : 'dim')}>
                  {(block.text || block.summary || '').slice(0, 6000)}
                </pre>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* --------------------------------------------------------------- approval */

export interface ApprovalRequest {
  call_id: string;
  name: string;
  args: Record<string, unknown>;
  server: string;
  reason: string;
  expires_in_s: number;
  /** Set when the decision is about reaching a host, not about running a tool. */
  host?: string;
}

export function ApprovalCard({
  request, onResolve, busy,
}: { request: ApprovalRequest; onResolve: (approved: boolean) => void; busy?: boolean }) {
  // The run's own clock is paused while this sits here, so the only thing counting down is
  // this request. Saying so beats a card that silently stops mattering.
  const [left, setLeft] = useState(request.expires_in_s ?? 0);
  useEffect(() => {
    setLeft(request.expires_in_s ?? 0);
    const id = setInterval(() => setLeft((v) => Math.max(0, v - 1)), 1000);
    return () => clearInterval(id);
  }, [request.call_id, request.expires_in_s]);
  return (
    <div className="my-3 overflow-hidden rounded-xl border border-amber-300/70 bg-amber-50/70 dark:border-amber-500/25 dark:bg-amber-500/[0.07] animate-fade-up">
      <div className="flex items-start gap-2.5 px-3.5 py-3">
        <ShieldQuestion size={15} className="mt-0.5 shrink-0 text-amber-600 dark:text-amber-400" />
        <div className="min-w-0 flex-1">
          {/* Name the thing being decided. For a fetch that is the host, not the tool —
              nobody weighs "allow web_fetch", they weigh "allow this site". */}
          <p className="text-xs font-semibold text-amber-900 dark:text-amber-200">
            {request.host ? <>Fetch <span className="font-mono">{request.host}</span>?</> : <>
              Allow <span className="font-mono">{request.name}</span>
              {request.server && <span className="font-normal opacity-70"> · {request.server}</span>} ?
            </>}
          </p>
          <p className="mt-0.5 text-2xs leading-relaxed text-amber-800/80 dark:text-amber-200/70">
            {request.reason}
            {left > 0 && <> · expires in {Math.floor(left / 60)}:{String(left % 60).padStart(2, '0')}</>}
          </p>
          <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-white/70 p-2 font-mono text-[11px] leading-relaxed text-amber-900 dark:bg-black/25 dark:text-amber-100">
            {JSON.stringify(request.args ?? {}, null, 2)}
          </pre>
          <div className="mt-2.5 flex gap-2">
            <Button size="xs" busy={busy} onClick={() => onResolve(true)} icon={Check}>Allow</Button>
            <Button size="xs" variant="outline" disabled={busy} onClick={() => onResolve(false)} icon={X}>Deny</Button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- message */

function Blocks({ blocks, live, cite }: { blocks: Block[]; live: boolean; cite?: string }) {
  const lastIndex = blocks.length - 1;
  return (
    <>
      {blocks.map((block, i) => {
        if (block.type === 'thinking') {
          return <ThinkingBlock key={`b${block.index}`} block={block} live={live && i === lastIndex} />;
        }
        if (block.type === 'tool') {
          return <ToolBlock key={`b${block.index}`} block={block} cite={cite} />;
        }
        if (!(block.text || '').trim()) return null;
        return (
          <div key={`b${block.index}`} className={cls(block.superseded && 'hidden')}>
            <Markdown text={block.text ?? ''} cite={cite} />
          </div>
        );
      })}
    </>
  );
}

const PHASES: Record<string, string> = {
  starting: 'Starting',
  thinking: 'Thinking',
  checking: 'Checking',
  compacting: 'Compressing context',
  writing: 'Writing',
  cancelled: 'Stopped',
  done: '',
};

/**
 * One line, only when there is something to say: this answer read things from outside.
 *
 * Not a warning — reading the web is the job. It is provenance, in the place where a
 * reader decides how much weight to give an answer, and it expands into exactly which
 * sources and whether any of them tried to give the agent orders.
 */
function TrustLine({ trust }: { trust: NonNullable<Message['trust']> }) {
  const [open, setOpen] = useState(false);
  const flagged = trust.injections.length > 0;
  if (!trust.sources.length && !flagged && !trust.compactions) return null;
  return (
    <div className="mt-2.5">
      <button onClick={() => setOpen((v) => !v)}
        className={cls('focus-ring inline-flex items-center gap-1.5 rounded-md px-1 py-0.5 text-2xs transition-colors',
          flagged ? 'text-amber-600 dark:text-amber-400' : 'dimmer hover:text-zinc-600 dark:hover:text-zinc-300')}>
        {flagged ? <ShieldAlert size={11} /> : <ShieldQuestion size={11} />}
        {flagged
          ? `${trust.injections.length} source tried to instruct the agent`
          : `read ${trust.sources.length} outside source${trust.sources.length > 1 ? 's' : ''}`}
        <ChevronRight size={10} className={cls('transition-transform', open && 'rotate-90')} />
      </button>
      {open && (
        <div className="mt-1.5 space-y-1 border-l-2 border-zinc-200 pl-3 text-2xs leading-relaxed dim dark:border-white/10 animate-fade-in">
          {trust.sources.length > 0 && (
            <p>Content entered this answer from: <b>{trust.sources.join(', ')}</b>. It was
              fenced as data — the agent could read it, not take orders from it.</p>
          )}
          {trust.injections.map((hit, i) => (
            <p key={i} className="text-amber-700 dark:text-amber-400">
              <b>{hit.tool}</b> returned text shaped like instructions ({hit.patterns.join(', ')}).
              Reported, not obeyed.
            </p>
          ))}
          {trust.compactions > 0 && (
            <p>The transcript was compressed {trust.compactions}× to fit the context window;
              the question and the standing rules were carried through verbatim.</p>
          )}
        </div>
      )}
    </div>
  );
}

export function AssistantTurn({
  message, live, phase, approval, onApprove, approving, notices, error,
}: {
  message: Message;
  live?: boolean;
  phase?: string;
  approval?: ApprovalRequest | null;
  onApprove?: (approved: boolean) => void;
  approving?: boolean;
  notices?: string[];
  error?: string | null;
}) {
  const blocks = message.blocks ?? [];
  return (
    <div className="animate-fade-up">
      {(message.plan?.length ?? 0) > 0 && <PlanCard steps={message.plan!} live={live} />}
      <Blocks blocks={blocks} live={!!live} cite={`ev-${message.id}-`} />

      {live && !blocks.length && (
        <div className="flex items-center gap-2 py-2 text-xs dim">
          <Spinner size={13} className="text-brand-500" />
          <span className="animate-pulse">{PHASES[phase ?? 'starting'] ?? 'Working'}…</span>
        </div>
      )}

      {approval && onApprove && (
        <ApprovalCard request={approval} onResolve={onApprove} busy={approving} />
      )}

      {(notices ?? []).map((notice, i) => (
        <p key={i} className="my-2 flex items-start gap-1.5 text-2xs leading-relaxed dim">
          <AlertTriangle size={12} className="mt-px shrink-0 text-amber-500" />
          {notice}
        </p>
      ))}

      {error && (
        <div className="my-3 flex items-start gap-2 rounded-xl border border-red-300/70 bg-red-50/70 px-3.5 py-3 text-xs leading-relaxed text-red-700 dark:border-red-500/25 dark:bg-red-500/[0.07] dark:text-red-300">
          <AlertTriangle size={14} className="mt-px shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {!live && message.trust && <TrustLine trust={message.trust} />}
      {!live && <TurnFooter message={message} />}
    </div>
  );
}

function TurnFooter({ message }: { message: Message }) {
  const usage: Usage = message.usage ?? {};
  const answer = (message.blocks ?? [])
    .filter((b) => b.type === 'text' && !b.superseded)
    .map((b) => b.text ?? '').join('\n\n') || message.content;
  const interrupted = message.status === 'cancelled' || message.status === 'failed';
  // Cached prefixes are the difference between a 2-second and a 30-second turn, and the
  // provider reports them nowhere else: what we sent, minus what it re-read.
  const reuse = usage.prompt_sent && usage.prompt_evaluated && usage.prompt_sent > 400
    ? Math.max(0, Math.round((1 - usage.prompt_evaluated / usage.prompt_sent) * 100))
    : null;
  if (!answer.trim() && !interrupted) return null;
  return (
    <div className={cls('mt-2.5 flex items-center gap-3 text-2xs dimmer transition-opacity duration-200',
      // A finished answer hides its metadata until you look for it; an interrupted one
      // must say so without being hovered — that is the whole point of saying it.
      interrupted ? 'opacity-100' : 'opacity-0 group-hover/turn:opacity-100 focus-within:opacity-100')}>
      {message.status === 'cancelled' && (
        <span className="flex items-center gap-1 text-amber-600 dark:text-amber-400">
          <Ban size={11} /> Answer interrupted
        </span>
      )}
      {answer.trim() && <CopyAnswer text={answer} />}
      {message.model && <span className="font-mono">{message.model}</span>}
      {!!usage.tool_calls && <span>{usage.tool_calls} tool{usage.tool_calls > 1 ? 's' : ''}</span>}
      {!!usage.tokens_out && <span>{usage.tokens_in}→{usage.tokens_out} tok</span>}
      {!!usage.ttft_ms && <span title="Time to the first token">{(usage.ttft_ms / 1000).toFixed(1)}s to first word</span>}
      {reuse !== null && (
        <span title={`${usage.prompt_evaluated} of ${usage.prompt_sent} prompt tokens were re-read; the rest came from the provider's cache.`}>
          {reuse}% prompt reused
        </span>
      )}
      {!!usage.masked_chars && (
        <span title="Old tool results collapsed to their summaries to keep the window open.">
          {Math.round(usage.masked_chars / 1000)}k chars masked
        </span>
      )}
      <ContextMeter usage={usage} />
    </div>
  );
}

/**
 * How full the window was when this answer was written.
 *
 * Compaction is the moment an agent silently forgets things, and it arrives without
 * warning unless someone is watching this number. Shown only past half-full: below that
 * it is noise.
 */
function ContextMeter({ usage }: { usage: Usage }) {
  const limit = usage.context_limit ?? 0;
  const used = usage.context_tokens ?? 0;
  if (!limit || !used) return null;
  const share = Math.min(1, used / limit);
  if (share < 0.5) return null;
  return (
    <span className="flex items-center gap-1.5"
      title={`${used.toLocaleString()} of ${limit.toLocaleString()} tokens. Past ~90% the oldest turns are summarised away.`}>
      <span className="h-1 w-8 overflow-hidden rounded-full bg-zinc-200 dark:bg-white/10">
        <span className={cls('block h-full rounded-full transition-[width] duration-500',
          share > 0.9 ? 'bg-red-500' : share > 0.75 ? 'bg-amber-500' : 'bg-brand-500')}
          style={{ width: `${Math.round(share * 100)}%` }} />
      </span>
      {Math.round(share * 100)}% context
    </span>
  );
}

function CopyAnswer({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className="focus-ring rounded px-1 py-0.5 hover:text-zinc-700 dark:hover:text-zinc-200"
      onClick={() => {
        navigator.clipboard?.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1400);
        }).catch(() => { /* clipboard denied */ });
      }}
    >
      {copied ? 'Copied' : 'Copy answer'}
    </button>
  );
}

export function UserTurn({ message, onRetry, busy }: {
  message: Message;
  /** Ask again, optionally reworded. Everything after this question is discarded. */
  onRetry?: (text: string) => void;
  busy?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(message.content);
  const area = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!editing) return;
    setDraft(message.content);
    const handle = requestAnimationFrame(() => {
      const el = area.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
      el.style.height = `${el.scrollHeight}px`;
    });
    return () => cancelAnimationFrame(handle);
  }, [editing, message.content]);

  if (editing) {
    const submit = () => {
      const text = draft.trim();
      if (!text) return;
      setEditing(false);
      onRetry?.(text);
    };
    return (
      <div className="animate-fade-up rounded-xl border px-3 py-2.5 hairline bg-zinc-50/60 dark:bg-white/[0.03]">
        <textarea ref={area} value={draft}
          onChange={(e) => {
            setDraft(e.target.value);
            e.target.style.height = 'auto';
            e.target.style.height = `${e.target.scrollHeight}px`;
          }}
          onKeyDown={(e) => {
            if (e.key === 'Escape') setEditing(false);
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
          }}
          className="w-full resize-none bg-transparent text-[17px] font-medium leading-snug tracking-[-0.011em] outline-none text-zinc-900 dark:text-white" />
        <div className="mt-2 flex items-center justify-end gap-2">
          <button onClick={() => setEditing(false)}
            className="focus-ring rounded px-2 py-1 text-2xs dim hover:text-zinc-700 dark:hover:text-zinc-200">
            Cancel
          </button>
          <Button size="sm" onClick={submit} disabled={!draft.trim()}>Ask again</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="group/ask animate-fade-up">
      <p className="whitespace-pre-wrap text-[17px] font-medium leading-snug tracking-[-0.011em] text-zinc-900 dark:text-white">
        {message.content}
      </p>
      {onRetry && (
        <div className="mt-1 flex items-center gap-2 text-2xs dimmer opacity-0 transition-opacity duration-200 group-hover/ask:opacity-100 focus-within:opacity-100">
          {/* Rewording the question and trying again is the commonest thing anyone wants
              from a transcript — and starting a new conversation to do it throws away the
              context that made the question make sense. */}
          <button disabled={busy} onClick={() => setEditing(true)}
            className="focus-ring flex items-center gap-1 rounded px-1 py-0.5 hover:text-zinc-700 disabled:opacity-50 dark:hover:text-zinc-200">
            <Pencil size={10} /> Edit
          </button>
          <button disabled={busy} onClick={() => onRetry(message.content)}
            className="focus-ring flex items-center gap-1 rounded px-1 py-0.5 hover:text-zinc-700 disabled:opacity-50 dark:hover:text-zinc-200">
            <RotateCcw size={10} /> Retry
          </button>
        </div>
      )}
      {(message.images?.length ?? 0) > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {message.images!.map((img, i) => (
            <Badge key={i}>{img.name}</Badge>
          ))}
        </div>
      )}
    </div>
  );
}

/* ----------------------------------------------------------- auto-scroll */

/**
 * Follow the stream, but stop the moment the reader scrolls up.
 *
 * An answer that yanks you back to the bottom while you are reading what it wrote ten
 * seconds ago is the single most irritating thing a streaming UI does.
 */
export function useStickToBottom(deps: unknown[]) {
  const ref = useRef<HTMLDivElement>(null);
  const stuck = useRef(true);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const onScroll = () => {
      const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
      stuck.current = distance < 120;
    };
    node.addEventListener('scroll', onScroll, { passive: true });
    return () => node.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    const node = ref.current;
    if (node && stuck.current) node.scrollTop = node.scrollHeight;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return ref;
}
