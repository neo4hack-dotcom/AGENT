// The conversation: user turns, and everything the agent did to answer them.
//
// The design rule here is "quiet until it matters". A tool call is one line you can
// ignore; the moment something fails, needs you, or is worth reading, it grows. The
// alternative — a log pane that shows everything at full size — is what makes agent UIs
// feel like watching a build run instead of reading an answer.

import {
  AlertTriangle, Ban, Brain, Check, ChevronRight, Clock, FileText, FolderOpen, Globe,
  ListChecks, Plug, Save, Search, ShieldQuestion, Sparkles, Terminal, X, type LucideIcon,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { Block, Message, PlanStep, Usage } from '../types';
import { Markdown } from './Markdown';
import { Badge, Button, Spinner, cls } from './ui';

const TOOL_ICONS: Record<string, LucideIcon> = {
  web_search: Search, web_fetch: Globe, run_python: Terminal, read_file: FileText,
  write_file: Save, list_files: FolderOpen, remember: Brain, recall: Brain,
  plan: ListChecks, current_time: Clock,
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

export function PlanCard({ steps }: { steps: PlanStep[] }) {
  const done = steps.filter((s) => s.status === 'done').length;
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
          {live ? 'Réflexion' : 'Raisonnement'}
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

function ToolBlock({ block }: { block: Block }) {
  const [open, setOpen] = useState(false);
  const Icon = toolIcon(block);
  const running = block.status === 'running' || block.status === 'awaiting_approval';
  const failed = block.ok === false;
  const denied = block.status === 'denied';

  return (
    <div className="my-1.5">
      <button onClick={() => setOpen((v) => !v)}
        className={cls('focus-ring group flex w-full items-center gap-2 rounded-lg border px-2.5 py-1.5 text-left transition-colors',
          failed ? 'border-red-300/60 bg-red-50/50 dark:border-red-500/25 dark:bg-red-500/[0.07]'
                 : 'hairline hover:bg-zinc-50 dark:hover:bg-white/[0.04]')}>
        <span className={cls('grid h-5 w-5 shrink-0 place-items-center rounded-md',
          failed ? 'bg-red-100 text-red-600 dark:bg-red-500/15 dark:text-red-400'
                 : denied ? 'bg-zinc-100 text-zinc-500 dark:bg-white/[0.07]'
                 : 'bg-brand-50 text-brand-600 dark:bg-brand-500/12 dark:text-brand-300')}>
          {running ? <Spinner size={11} /> : denied ? <Ban size={11} />
            : failed ? <X size={11} strokeWidth={3} /> : <Icon size={11} />}
        </span>
        <span className="shrink-0 font-mono text-2xs font-medium text-zinc-700 dark:text-zinc-200">
          {block.name}
        </span>
        {block.by === 'critic' && <Badge tone="brand">critique</Badge>}
        {block.cached && <Badge>déjà obtenu</Badge>}
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
              Appel{block.server ? ` · ${block.server}` : ''}
            </div>
            <pre className="overflow-x-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed dim">
              {JSON.stringify(block.args ?? {}, null, 2)}
            </pre>
          </div>
          {(block.text || block.summary) && (
            <div>
              <div className="mb-1 text-2xs font-semibold uppercase tracking-wider dimmer">
                {failed ? 'Erreur' : 'Résultat'}
              </div>
              <pre className={cls('max-h-72 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed',
                failed ? 'text-red-600 dark:text-red-400' : 'dim')}>
                {(block.text || block.summary || '').slice(0, 6000)}
              </pre>
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
}

export function ApprovalCard({
  request, onResolve, busy,
}: { request: ApprovalRequest; onResolve: (approved: boolean) => void; busy?: boolean }) {
  return (
    <div className="my-3 overflow-hidden rounded-xl border border-amber-300/70 bg-amber-50/70 dark:border-amber-500/25 dark:bg-amber-500/[0.07] animate-fade-up">
      <div className="flex items-start gap-2.5 px-3.5 py-3">
        <ShieldQuestion size={15} className="mt-0.5 shrink-0 text-amber-600 dark:text-amber-400" />
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold text-amber-900 dark:text-amber-200">
            Autoriser <span className="font-mono">{request.name}</span>
            {request.server && <span className="font-normal opacity-70"> · {request.server}</span>} ?
          </p>
          <p className="mt-0.5 text-2xs leading-relaxed text-amber-800/80 dark:text-amber-200/70">{request.reason}</p>
          <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-white/70 p-2 font-mono text-[11px] leading-relaxed text-amber-900 dark:bg-black/25 dark:text-amber-100">
            {JSON.stringify(request.args ?? {}, null, 2)}
          </pre>
          <div className="mt-2.5 flex gap-2">
            <Button size="xs" busy={busy} onClick={() => onResolve(true)} icon={Check}>Autoriser</Button>
            <Button size="xs" variant="outline" disabled={busy} onClick={() => onResolve(false)} icon={X}>Refuser</Button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- message */

function Blocks({ blocks, live }: { blocks: Block[]; live: boolean }) {
  const lastIndex = blocks.length - 1;
  return (
    <>
      {blocks.map((block, i) => {
        if (block.type === 'thinking') {
          return <ThinkingBlock key={`b${block.index}`} block={block} live={live && i === lastIndex} />;
        }
        if (block.type === 'tool') return <ToolBlock key={`b${block.index}`} block={block} />;
        if (!(block.text || '').trim()) return null;
        return (
          <div key={`b${block.index}`} className={cls(block.superseded && 'hidden')}>
            <Markdown text={block.text ?? ''} />
          </div>
        );
      })}
    </>
  );
}

const PHASES: Record<string, string> = {
  starting: 'Démarrage',
  thinking: 'Réflexion',
  checking: 'Vérification',
  writing: 'Rédaction',
  cancelled: 'Arrêté',
  done: '',
};

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
      {(message.plan?.length ?? 0) > 0 && <PlanCard steps={message.plan!} />}
      <Blocks blocks={blocks} live={!!live} />

      {live && !blocks.length && (
        <div className="flex items-center gap-2 py-2 text-xs dim">
          <Spinner size={13} className="text-brand-500" />
          <span className="animate-pulse">{PHASES[phase ?? 'starting'] ?? 'Travail en cours'}…</span>
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
  if (!answer.trim() && !interrupted) return null;
  return (
    <div className={cls('mt-2.5 flex items-center gap-3 text-2xs dimmer transition-opacity duration-200',
      // A finished answer hides its metadata until you look for it; an interrupted one
      // must say so without being hovered — that is the whole point of saying it.
      interrupted ? 'opacity-100' : 'opacity-0 group-hover/turn:opacity-100 focus-within:opacity-100')}>
      {message.status === 'cancelled' && (
        <span className="flex items-center gap-1 text-amber-600 dark:text-amber-400">
          <Ban size={11} /> Réponse interrompue
        </span>
      )}
      {answer.trim() && <CopyAnswer text={answer} />}
      {message.model && <span className="font-mono">{message.model}</span>}
      {!!usage.tool_calls && <span>{usage.tool_calls} outil{usage.tool_calls > 1 ? 's' : ''}</span>}
      {!!usage.tokens_out && <span>{usage.tokens_in}→{usage.tokens_out} tok</span>}
    </div>
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
      {copied ? 'Copié' : 'Copier la réponse'}
    </button>
  );
}

export function UserTurn({ message }: { message: Message }) {
  return (
    <div className="animate-fade-up">
      <p className="whitespace-pre-wrap text-[17px] font-medium leading-snug tracking-[-0.011em] text-zinc-900 dark:text-white">
        {message.content}
      </p>
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
