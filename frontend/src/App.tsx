// Lumen — one screen, one input.
//
// Two states, one layout: the composer is centred on an empty canvas and docked once a
// conversation exists. Everything else (history, admin, status) stays at the edges until
// it is asked for.

import {
  AlertTriangle, Moon, PanelLeft, Plus, Sun,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, streamRun } from './api';
import { Admin, AdminDoor } from './components/Admin';
import { Composer } from './components/Composer';
import { Sidebar } from './components/Sidebar';
import { AssistantTurn, UserTurn, useStickToBottom, type ApprovalRequest } from './components/Thread';
import { Badge, Dot, cls, useTheme, useToast } from './components/ui';
import type {
  Block, Bootstrap, Conversation, ConversationSummary, Message, PlanStep, StreamEvent, Usage,
} from './types';

interface Live {
  runId: string;
  conversationId: string;
  messageId: string;
  blocks: Map<number, Block>;
  plan: PlanStep[];
  usage: Usage;
  phase: string;
  notices: string[];
  approval: ApprovalRequest | null;
  error: string | null;
}

const LAST_CONVERSATION = 'lumen.conversation';

function rememberConversation(id: string | null): void {
  try {
    if (id) localStorage.setItem(LAST_CONVERSATION, id);
    else localStorage.removeItem(LAST_CONVERSATION);
  } catch { /* private browsing: the app simply opens on a blank canvas */ }
}

const emptyLive = (runId: string, conversationId: string, messageId: string): Live => ({
  runId, conversationId, messageId,
  blocks: new Map(), plan: [], usage: {}, phase: 'starting', notices: [],
  approval: null, error: null,
});

export default function App() {
  const toast = useToast();
  const [dark, toggleTheme] = useTheme();
  const [boot, setBoot] = useState<Bootstrap | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [live, setLive] = useState<Live | null>(null);
  const [sidebar, setSidebar] = useState(false);
  const [admin, setAdmin] = useState(false);
  const [approving, setApproving] = useState(false);
  const stopStream = useRef<(() => void) | null>(null);
  const liveRef = useRef<Live | null>(null);
  const finishRunRef = useRef<(() => Promise<void>) | null>(null);

  /* ------------------------------------------------------------ loading */
  const refreshBoot = useCallback(async () => {
    try { setBoot(await api.bootstrap()); } catch (e) { toast(String((e as Error).message), 'error'); }
  }, [toast]);

  const refreshConversations = useCallback(async () => {
    try { setConversations(await api.listConversations()); } catch { /* the list is not critical */ }
  }, []);

  useEffect(() => { void refreshBoot(); void refreshConversations(); }, [refreshBoot, refreshConversations]);

  // Reopen whatever was on screen last, and re-attach to its run if one is still working.
  // Reloading the page mid-answer is common enough — a dropped connection, a hot reload,
  // a closed laptop — that losing the answer to it would be the app's worst moment.
  useEffect(() => {
    let cancelled = false;
    const restore = async () => {
      let id = '';
      try { id = localStorage.getItem(LAST_CONVERSATION) ?? ''; } catch { return; }
      if (!id) return;
      try {
        const restored = await api.getConversation(id);
        if (cancelled) return;
        setConversation(restored);
        if (restored.active_run_id) attach(restored);
      } catch {
        rememberConversation(null);
      }
    };
    void restore();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { liveRef.current = live; }, [live]);
  // Only ever *writes* here. Clearing on a null id would wipe the key on first mount,
  // before the restore effect below has had a chance to read it — the conversation is
  // forgotten instead by whoever deliberately leaves it.
  useEffect(() => { if (conversation?.id) rememberConversation(conversation.id); }, [conversation?.id]);
  useEffect(() => () => stopStream.current?.(), []);

  /* ----------------------------------------------------- event handling */
  // Deltas arrive faster than React should re-render markdown, so they are buffered and
  // flushed once per frame. Without this, a fast local model turns every token into a
  // full re-parse of the answer so far, and the UI janks exactly when it is most watched.
  const pending = useRef<StreamEvent[]>([]);
  const frame = useRef<number | null>(null);

  const flush = useCallback(() => {
    frame.current = null;
    const events = pending.current;
    pending.current = [];
    if (!events.length) return;
    setLive((current) => {
      if (!current) return current;
      const next: Live = { ...current, blocks: new Map(current.blocks) };
      for (const event of events) applyEvent(next, event);
      return next;
    });
  }, []);

  const onEvent = useCallback((event: StreamEvent) => {
    pending.current.push(event);
    if (event.type === 'done') {
      // The final event also carries the persisted message, so settle immediately rather
      // than waiting a frame and briefly showing a finished run as still running.
      if (frame.current) cancelAnimationFrame(frame.current);
      flush();
      void finishRun();
      return;
    }
    frame.current ??= requestAnimationFrame(flush);
  }, [flush]);

  const reloadConversation = useCallback(async (id: string) => {
    try { setConversation(await api.getConversation(id)); } catch { /* deleted mid-run */ }
  }, []);

  /** Subscribe to a run already in flight — from a reload, or from another tab. */
  const attach = useCallback((restored: Conversation) => {
    const runId = restored.active_run_id;
    if (!runId) return;
    const last = [...restored.messages].reverse().find((m) => m.role === 'assistant');
    if (!last) return;
    setLive(emptyLive(runId, restored.id, last.id));
    stopStream.current = streamRun(runId, onEvent, () => { void finishRunRef.current?.(); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onEvent]);

  const finishRun = useCallback(async () => {
    stopStream.current = null;
    // Read the run from a ref rather than from inside a state updater: under StrictMode an
    // updater runs twice, and reloading the conversation twice is a visible flicker.
    const running = liveRef.current;
    setLive(null);
    if (running) await reloadConversation(running.conversationId);
    void refreshConversations();
  }, [reloadConversation, refreshConversations]);

  useEffect(() => { finishRunRef.current = finishRun; }, [finishRun]);

  /* -------------------------------------------------------------- actions */
  const send = useCallback(async (text: string, attachments: string[]) => {
    try {
      const started = await api.chat({
        conversation_id: conversation?.id ?? '', text, attachments,
      });
      // Show the user's own turn immediately: waiting for the server round-trip to render
      // what someone just typed is the one latency nobody forgives.
      setConversation((current) => {
        const message: Message = {
          id: started.user_message_id, role: 'user', content: text, created_at: Date.now() / 1000,
        };
        const placeholder: Message = {
          id: started.message_id, role: 'assistant', content: '', created_at: Date.now() / 1000,
          blocks: [], status: 'running',
        };
        if (current && current.id === started.conversation_id) {
          return { ...current, messages: [...current.messages, message, placeholder] };
        }
        return {
          id: started.conversation_id, title: '', created_at: Date.now() / 1000,
          updated_at: Date.now() / 1000, messages: [message, placeholder],
        };
      });
      setLive(emptyLive(started.run_id, started.conversation_id, started.message_id));
      stopStream.current = streamRun(started.run_id, onEvent, (reason) => {
        if (reason === 'error') {
          toast('Le flux a été interrompu. Rechargez la conversation pour voir la réponse enregistrée.', 'error');
          void finishRun();
        }
      });
    } catch (e) {
      toast(String((e as Error).message), 'error');
    }
  }, [conversation?.id, onEvent, toast, finishRun]);

  const stop = useCallback(async () => {
    if (!live) return;
    try { await api.cancelRun(live.runId); } catch { /* already finished */ }
  }, [live]);

  const resolveApproval = useCallback(async (approved: boolean) => {
    if (!live?.approval) return;
    setApproving(true);
    try {
      await api.approve(live.runId, live.approval.call_id, approved);
      setLive((current) => (current ? { ...current, approval: null } : current));
    } catch (e) {
      toast(String((e as Error).message), 'error');
    } finally { setApproving(false); }
  }, [live, toast]);

  const openConversation = useCallback(async (id: string) => {
    stopStream.current?.();
    stopStream.current = null;
    setLive(null);
    setSidebar(false);
    await reloadConversation(id);
  }, [reloadConversation]);

  const newConversation = useCallback(() => {
    stopStream.current?.();
    stopStream.current = null;
    setLive(null);
    setConversation(null);
    rememberConversation(null);
    setSidebar(false);
  }, []);

  /* ---------------------------------------------------------- shortcuts */
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const meta = event.metaKey || event.ctrlKey;
      if (meta && event.key === ',') { event.preventDefault(); setAdmin(true); }
      if (meta && event.key.toLowerCase() === 'k') { event.preventDefault(); setSidebar((v) => !v); }
      if (meta && event.shiftKey && event.key.toLowerCase() === 'o') {
        event.preventDefault(); newConversation();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [newConversation]);

  /* ------------------------------------------------------------ derived */
  const messages = conversation?.messages ?? [];
  const liveBlocks = useMemo(
    () => (live ? [...live.blocks.values()].sort((a, b) => a.index - b.index) : []),
    [live]);
  const scroller = useStickToBottom([messages.length, liveBlocks, live?.phase]);
  const empty = messages.length === 0 && !live;
  const modelOk = boot?.model.ok ?? false;
  const toolsCapable = boot?.model.capabilities.tools ?? false;
  const canSeeAdmin = boot?.admin.authenticated || boot?.admin.mode === 'password';

  return (
    <div className="flex h-full flex-col">
      <Sidebar
        open={sidebar}
        onClose={() => setSidebar(false)}
        conversations={conversations}
        activeId={conversation?.id ?? null}
        onSelect={(id) => void openConversation(id)}
        onNew={newConversation}
        onDelete={async (id) => {
          await api.deleteConversation(id);
          if (conversation?.id === id) newConversation();
          void refreshConversations();
        }}
        onRename={async (id, title) => {
          if (!title.trim()) return;
          await api.renameConversation(id, title.trim());
          void refreshConversations();
        }}
      />

      <header className={cls('z-20 flex shrink-0 items-center gap-1 px-3 py-2.5 transition-[padding] duration-200',
        sidebar && 'sm:pl-[19rem]')}>
        <button onClick={() => setSidebar((v) => !v)} title="Conversations (⌘K)"
          className="focus-ring grid h-8 w-8 place-items-center rounded-lg dimmer transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-white/[0.07] dark:hover:text-zinc-200">
          <PanelLeft size={16} strokeWidth={1.9} />
        </button>
        {!empty && (
          <button onClick={newConversation} title="Nouvelle conversation (⌘⇧O)"
            className="focus-ring grid h-8 w-8 place-items-center rounded-lg dimmer transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-white/[0.07] dark:hover:text-zinc-200">
            <Plus size={16} strokeWidth={1.9} />
          </button>
        )}

        <div className="ml-auto flex items-center gap-1">
          <button onClick={() => canSeeAdmin || boot?.admin.local ? setAdmin(true) : undefined}
            title={boot?.model.error ?? boot?.model.capabilities.source ?? ''}
            className="focus-ring flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-2xs dim transition-colors hover:bg-zinc-100 dark:hover:bg-white/[0.06]">
            <Dot tone={!modelOk ? 'bad' : toolsCapable ? 'good' : 'warn'} />
            <span className="font-mono">{boot?.model.model || 'aucun modèle'}</span>
            {boot && boot.mcp.servers_connected > 0 && (
              <Badge tone="brand">{boot.mcp.tools_available} outils</Badge>
            )}
          </button>
          <button onClick={toggleTheme} title="Thème"
            className="focus-ring grid h-8 w-8 place-items-center rounded-lg dimmer transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-white/[0.07] dark:hover:text-zinc-200">
            {dark ? <Sun size={15} strokeWidth={1.9} /> : <Moon size={15} strokeWidth={1.9} />}
          </button>
          {(boot?.admin.local || boot?.admin.mode === 'password') && (
            <AdminDoor onOpen={() => setAdmin(true)} warn={!modelOk || !toolsCapable} />
          )}
        </div>
      </header>

      {empty ? (
        <EmptyState boot={boot} onSend={send} onStop={stop} running={false} />
      ) : (
        <>
          <main ref={scroller} className="min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto w-full max-w-3xl space-y-8 px-5 pb-10 pt-4">
              {messages.map((message) => {
                if (message.role === 'user') return <UserTurn key={message.id} message={message} />;
                const isLive = live?.messageId === message.id;
                const rendered: Message = isLive
                  ? { ...message, blocks: liveBlocks, plan: live!.plan, usage: live!.usage }
                  : message;
                return (
                  <div key={message.id} className="group/turn">
                    <AssistantTurn
                      message={rendered}
                      live={isLive}
                      phase={live?.phase}
                      approval={isLive ? live!.approval : null}
                      onApprove={(approved) => void resolveApproval(approved)}
                      approving={approving}
                      notices={isLive ? live!.notices : undefined}
                      error={isLive ? live!.error : message.error}
                    />
                  </div>
                );
              })}
            </div>
          </main>
          <div className="shrink-0 px-5 pb-4">
            <div className="mx-auto flex w-full max-w-3xl justify-center">
              <Composer
                onSend={send}
                onStop={() => void stop()}
                onUpload={api.upload}
                running={!!live}
                disabled={!modelOk}
                disabledReason={boot?.model.error ?? 'Aucun modèle sélectionné'}
                centred={false}
                vision={boot?.model.capabilities.vision ?? false}
              />
            </div>
          </div>
        </>
      )}

      {admin && boot && (
        <Admin state={boot.admin} onClose={() => setAdmin(false)}
          onChanged={() => { void refreshBoot(); }} />
      )}
    </div>
  );
}

/* -------------------------------------------------------------- empty state */

function EmptyState({
  boot, onSend, onStop, running,
}: { boot: Bootstrap | null; onSend: (t: string, a: string[]) => void; onStop: () => void; running: boolean }) {
  const suggestions = useMemo(() => buildSuggestions(boot), [boot]);

  return (
    <main className="flex min-h-0 flex-1 flex-col items-center justify-center px-5 pb-20">
      <div className="mb-7 flex flex-col items-center animate-fade-up">
        <Mark />
        <h1 className="mt-4 text-[27px] font-semibold tracking-[-0.03em] text-zinc-900 dark:text-white">Lumen</h1>
        <p className="mt-1.5 max-w-md text-center text-[13px] leading-relaxed dim">
          {boot?.model.ok
            ? <>Agent autonome, entièrement local. <span className="font-mono text-zinc-600 dark:text-zinc-400">{boot.model.model}</span> raisonne, appelle ses outils et vous répond — rien ne sort de cette machine.</>
            : <>Aucun modèle local n’est sélectionné. Ouvrez l’administration pour en choisir un.</>}
        </p>
      </div>

      <Composer
        onSend={onSend}
        onStop={onStop}
        onUpload={api.upload}
        running={running}
        disabled={!(boot?.model.ok ?? false)}
        disabledReason={boot?.model.error ?? 'Aucun modèle sélectionné'}
        centred
        vision={boot?.model.capabilities.vision ?? false}
        placeholder="Demandez n’importe quoi…"
      />

      {!boot?.model.capabilities.tools && boot?.model.ok && (
        <p className="mt-4 flex max-w-md items-start gap-1.5 text-center text-2xs leading-relaxed text-amber-600 dark:text-amber-400">
          <AlertTriangle size={12} className="mt-px shrink-0" />
          <span>{boot.model.model} ne sait pas appeler d’outils : il répondra uniquement de mémoire. Choisissez un modèle qui déclare « tools » dans l’administration.</span>
        </p>
      )}

      <div className="mt-7 flex max-w-2xl flex-wrap justify-center gap-1.5 animate-fade-in">
        {suggestions.map((suggestion) => (
          <button key={suggestion.text} onClick={() => onSend(suggestion.text, [])}
            disabled={!(boot?.model.ok ?? false)}
            className="focus-ring rounded-full border px-3 py-1.5 text-2xs hairline dim transition-all hover:-translate-y-px hover:border-brand-300 hover:text-zinc-800 disabled:opacity-40 dark:hover:border-brand-500/40 dark:hover:text-zinc-100">
            {suggestion.label}
          </button>
        ))}
      </div>
    </main>
  );
}

/**
 * Suggestions built from what is actually connected right now.
 *
 * A fixed list would advertise capabilities this install may not have — the web chip on a
 * machine with web access switched off is a promise the agent then has to break.
 */
function buildSuggestions(boot: Bootstrap | null): { label: string; text: string }[] {
  if (!boot) return [];
  const names = new Set(boot.tools.map((t) => t.name));
  const out: { label: string; text: string }[] = [];
  if (names.has('web_search')) {
    out.push({ label: 'Faire une veille sourcée',
      text: 'Fais une veille sur les sorties récentes autour du Model Context Protocol : cherche, ouvre les pages qui comptent, et donne-moi une synthèse sourcée.' });
  }
  if (names.has('run_python')) {
    out.push({ label: 'Calculer quelque chose de vrai',
      text: 'Calcule, en exécutant réellement le code, le nombre de jours ouvrés restants cette année et la part de l’année déjà écoulée.' });
  }
  if (names.has('write_file')) {
    out.push({ label: 'Produire un fichier',
      text: 'Génère un CSV de synthèse dans l’espace de travail avec les modules Python disponibles sur cette machine et leur version.' });
  }
  const mcp = boot.tools.filter((t) => t.kind === 'mcp');
  if (mcp.length) {
    const server = mcp[0].group;
    out.push({ label: `Utiliser ${server}`, text: `Montre-moi ce que tu peux faire avec ${server}, en appelant réellement un de ses outils.` });
  } else {
    out.push({ label: 'Ce que tu sais faire',
      text: 'Liste précisément les outils dont tu disposes en ce moment et ce que chacun te permet de faire concrètement.' });
  }
  return out.slice(0, 4);
}

function Mark() {
  return (
    <div className="relative grid h-14 w-14 place-items-center">
      <div aria-hidden className="absolute inset-0 rounded-full bg-brand-500/25 blur-xl animate-breathe" />
      <svg viewBox="0 0 32 32" className="relative h-12 w-12">
        <defs>
          <radialGradient id="lumen-mark" cx="50%" cy="45%" r="55%">
            <stop offset="0%" stopColor="#c7bfff" />
            <stop offset="55%" stopColor="#6d5efc" />
            <stop offset="100%" stopColor="#3f2fa8" />
          </radialGradient>
        </defs>
        <circle cx="16" cy="16" r="13" fill="url(#lumen-mark)" />
        <circle cx="16" cy="16" r="13" fill="none" stroke="currentColor" strokeOpacity=".18" strokeWidth="1.5"
          className="text-zinc-900 dark:text-white" />
        <circle cx="12" cy="12" r="3.2" fill="#fff" fillOpacity=".5" />
      </svg>
    </div>
  );
}

/* ------------------------------------------------------------ event reducer */

function blockAt(live: Live, index: number, seed: Partial<Block>): Block {
  const existing = live.blocks.get(index);
  if (existing) return existing;
  const created = { index, type: 'text', ...seed } as Block;
  live.blocks.set(index, created);
  return created;
}

function applyEvent(live: Live, event: StreamEvent): void {
  switch (event.type) {
    case 'status':
      live.phase = event.phase;
      break;
    case 'block.open':
      blockAt(live, event.index, { type: event.kind, text: '' });
      break;
    case 'text.delta':
    case 'thinking.delta': {
      const kind = event.type === 'text.delta' ? 'text' : 'thinking';
      const block = blockAt(live, event.index, { type: kind, text: '' });
      live.blocks.set(event.index, { ...block, type: kind, text: (block.text ?? '') + event.text });
      break;
    }
    case 'block.supersede': {
      const block = live.blocks.get(event.index);
      if (block) live.blocks.set(event.index, { ...block, superseded: true });
      break;
    }
    case 'plan':
      live.plan = event.steps;
      break;
    case 'tool.start':
      live.blocks.set(event.index, {
        index: event.index, type: 'tool', id: event.id, name: event.name, args: event.args,
        server: event.server, kind: event.kind, by: event.by, status: 'running', ok: null,
        summary: '', text: '', ms: 0,
      });
      break;
    case 'tool.end': {
      const block = live.blocks.get(event.index);
      live.blocks.set(event.index, {
        ...(block ?? { index: event.index, type: 'tool', name: '' }),
        status: event.status, ok: event.ok, summary: event.summary, ms: event.ms,
        cached: event.cached, text: event.preview ?? '',
      } as Block);
      break;
    }
    case 'approval.request':
      live.approval = {
        call_id: event.call_id, name: event.name, args: event.args,
        server: event.server, reason: event.reason,
      };
      break;
    case 'approval.resolved':
      live.approval = null;
      break;
    case 'usage':
      live.usage = { llm_calls: event.llm_calls, tokens_in: event.tokens_in,
                     tokens_out: event.tokens_out, tool_calls: event.tool_calls };
      break;
    case 'notice':
      live.notices = [...live.notices, event.message];
      break;
    case 'error':
      live.error = event.message;
      break;
    default:
      break;
  }
}
