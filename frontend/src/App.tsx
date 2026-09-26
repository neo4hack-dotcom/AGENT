// Agent — one screen, one input.
//
// Two states, one layout: the composer is centred on an empty canvas and docked once a
// conversation exists. Everything else (history, admin, status) stays at the edges until
// it is asked for.

import {
  AlertTriangle, Cloud, Command, MonitorSmartphone, Moon, Plus, Sparkles, Sun,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, streamRun } from './api';
import { Admin, AdminDoor } from './components/Admin';
import { Artifacts } from './components/Artifacts';
import { Palette } from './components/Palette';
import { Composer } from './components/Composer';
import { Sidebar } from './components/Sidebar';
import { AssistantTurn, UserTurn, useStickToBottom, type ApprovalRequest } from './components/Thread';
import { Badge, Dot, cls, useTheme, useToast } from './components/ui';
import type {
  AskRequest, Block, Bootstrap, FileOut, Conversation, ConversationSummary, Message, PlanStep, StreamEvent, Usage,
} from './types';

interface Live {
  runId: string;
  conversationId: string;
  messageId: string;
  blocks: Map<number, Block>;
  plan: PlanStep[];
  usage: Usage;
  phase: string;
  notices: { text: string; quiet?: boolean }[];
  approval: ApprovalRequest | null;
  ask: AskRequest | null;
  files: FileOut[];
  error: string | null;
  trust: { sources: string[]; injections: { tool: string; patterns: string[] }[];
           compactions: number };
}

const LAST_CONVERSATION = 'agent.conversation';

function rememberConversation(id: string | null): void {
  try {
    if (id) localStorage.setItem(LAST_CONVERSATION, id);
    else localStorage.removeItem(LAST_CONVERSATION);
  } catch { /* private browsing: the app simply opens on a blank canvas */ }
}

const emptyLive = (runId: string, conversationId: string, messageId: string): Live => ({
  runId, conversationId, messageId,
  blocks: new Map(), plan: [], usage: {}, phase: 'starting', notices: [],
  approval: null, ask: null, files: [], error: null,
  trust: { sources: [], injections: [], compactions: 0 },
});

export default function App() {
  const toast = useToast();
  const [dark, toggleTheme] = useTheme();
  const [boot, setBoot] = useState<Bootstrap | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [live, setLive] = useState<Live | null>(null);
  const [sidebar, setSidebar] = useState(false);
  const [admin, setAdmin] = useState<string | null>(null);
  const [artifacts, setArtifacts] = useState(false);
  const [palette, setPalette] = useState(false);
  const [approving, setApproving] = useState(false);
  const stopStream = useRef<(() => void) | null>(null);
  const liveRef = useRef<Live | null>(null);
  // Set synchronously, before the request leaves. `running` cannot do this job: it derives
  // from `live`, which is only set once the server has answered — and two Enter presses
  // inside that round trip both pass the check, start two runs, and interleave two answers
  // into one conversation. A ref changes on the same tick as the keystroke.
  const sending = useRef(false);
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
    // StrictMode runs mount effects twice; without this the second attach opens a second
    // EventSource on the same run and every delta lands in the state twice.
    stopStream.current?.();
    setLive(emptyLive(runId, restored.id, last.id));
    stopStream.current = streamRun(runId, onEvent, () => { void finishRunRef.current?.(); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onEvent]);

  const finishRun = useCallback(async () => {
    stopStream.current = null;
    // Read the run from a ref rather than from inside a state updater: under StrictMode an
    // updater runs twice, and reloading the conversation twice is a visible flicker.
    const running = liveRef.current;
    liveRef.current = null;   // released here, not one commit later
    setLive(null);
    if (running) await reloadConversation(running.conversationId);
    void refreshConversations();
  }, [reloadConversation, refreshConversations]);

  useEffect(() => { finishRunRef.current = finishRun; }, [finishRun]);

  /* -------------------------------------------------------------- actions */
  const send = useCallback(async (text: string, attachments: string[]) => {
    if (sending.current || liveRef.current) return;
    sending.current = true;
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
      const fresh = emptyLive(started.run_id, started.conversation_id, started.message_id);
      setLive(fresh);
      liveRef.current = fresh;   // in effect before React commits, so the guard above holds
      // Never leave a stream behind: two EventSources on one run deliver every delta twice.
      stopStream.current?.();
      stopStream.current = streamRun(started.run_id, onEvent, (reason) => {
        if (reason === 'error') {
          toast('The live stream dropped. Reload the conversation to see the saved answer.', 'error');
          void finishRun();
        }
      });
    } catch (e) {
      toast(String((e as Error).message), 'error');
    } finally {
      sending.current = false;
    }
  }, [conversation?.id, onEvent, toast, finishRun]);

  /**
   * Ask a question again, optionally reworded.
   *
   * The server truncates the conversation at that question and starts a fresh run, so the
   * turns that followed the old answer do not survive into the new one — an answer built
   * on a question that is no longer there is the confusing part of a naive "retry".
   */
  const retry = useCallback(async (messageId: string, text: string) => {
    if (sending.current || liveRef.current || !conversation) return;
    sending.current = true;
    try {
      const started = await api.retry(conversation.id, messageId, text);
      const refreshed = await api.getConversation(started.conversation_id);
      setConversation(refreshed);
      const fresh = emptyLive(started.run_id, started.conversation_id, started.message_id);
      setLive(fresh);
      liveRef.current = fresh;
      stopStream.current?.();
      stopStream.current = streamRun(started.run_id, onEvent, (reason) => {
        if (reason === 'error') {
          toast('The live stream dropped. Reload the conversation to see the saved answer.', 'error');
          void finishRun();
        }
      });
    } catch (e) {
      toast(String((e as Error).message), 'error');
    } finally {
      sending.current = false;
    }
  }, [conversation, onEvent, toast, finishRun]);

  /** The whole conversation as one Markdown file, evidence included. */
  const exportConversation = useCallback(async () => {
    if (!conversation) return;
    try {
      const markdown = await api.exportConversation(conversation.id);
      const url = URL.createObjectURL(new Blob([markdown], { type: 'text/markdown' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = `${(conversation.title || 'conversation').replace(/[^\w.-]+/g, '-').slice(0, 60)}.md`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast(String((e as Error).message), 'error');
    }
  }, [conversation, toast]);

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

  const [answering, setAnswering] = useState(false);
  const answerQuestion = useCallback(async (answer: string) => {
    if (!live?.ask) return;
    setAnswering(true);
    try {
      await api.answer(live.runId, live.ask.call_id, answer);
      setLive((current) => (current ? { ...current, ask: null } : current));
    } catch (e) {
      toast(String((e as Error).message), 'error');
    } finally { setAnswering(false); }
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
      if (meta && event.key === ',') { event.preventDefault(); setAdmin('model'); }
      // ⌘K is the one key worth remembering: it reaches every action, every setting and
      // every past conversation. The drawer moved to ⌘⇧K, where managing them belongs.
      if (meta && event.shiftKey && event.key.toLowerCase() === 'k') {
        event.preventDefault(); setSidebar((v) => !v); return;
      }
      if (meta && event.key.toLowerCase() === 'k') { event.preventDefault(); setPalette((v) => !v); }
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
        <button onClick={() => setPalette(true)} title="Everything (⌘K)"
          className="focus-ring grid h-8 w-8 place-items-center rounded-lg dimmer transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-white/[0.07] dark:hover:text-zinc-200">
          <Command size={15} strokeWidth={1.9} />
        </button>
        {!empty && (
          <button onClick={newConversation} title="New chat (⌘⇧O)"
            className="focus-ring grid h-8 w-8 place-items-center rounded-lg dimmer transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-white/[0.07] dark:hover:text-zinc-200">
            <Plus size={16} strokeWidth={1.9} />
          </button>
        )}

        <div className="ml-auto flex items-center gap-1">
          <button onClick={() => (canSeeAdmin || boot?.admin.local ? setAdmin('model') : undefined)}
            title={boot?.model.error
              ?? (boot?.model.local === false
                ? `${boot.model.model} is hosted by Ollama, not by this machine — prompts leave it.`
                : boot?.model.capabilities.source ?? '')}
            className="focus-ring flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-2xs dim transition-colors hover:bg-zinc-100 dark:hover:bg-white/[0.06]">
            <Dot tone={!modelOk ? 'bad' : toolsCapable ? 'good' : 'warn'} />
            <span className="font-mono">{boot?.model.model || 'no model'}</span>
            {/* Where the model actually runs is the one fact this app must never blur. */}
            {boot?.model.ok && (boot.model.local
              ? <MonitorSmartphone size={11} className="shrink-0 opacity-60" />
              : <Cloud size={11} className="shrink-0 text-amber-500" />)}
            {boot && boot.mcp.servers_connected > 0 && (
              <Badge tone="brand">{boot.mcp.tools_available} tools</Badge>
            )}
          </button>
          {/* Back in the header after a spell in the palette only: light and dark is a
              setting people flip on a whim, several times a day, and two keystrokes is
              two too many for something the eye asks for rather than the mind. */}
          <button onClick={toggleTheme} title={dark ? 'Light theme' : 'Dark theme'}
            className="focus-ring grid h-8 w-8 place-items-center rounded-lg dimmer transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-white/[0.07] dark:hover:text-zinc-200">
            {dark ? <Sun size={15} strokeWidth={1.9} /> : <Moon size={15} strokeWidth={1.9} />}
          </button>
          {(boot?.admin.local || boot?.admin.mode === 'password') && (
            <AdminDoor onOpen={() => setAdmin('model')} warn={!modelOk || !toolsCapable} />
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
                if (message.role === 'user') {
                  return <UserTurn key={message.id} message={message} busy={!!live}
                    onRetry={(text) => void retry(message.id, text)} />;
                }
                const isLive = live?.messageId === message.id;
                const rendered: Message = isLive
                  ? { ...message, blocks: liveBlocks, plan: live!.plan, usage: live!.usage,
                      trust: live!.trust, files: live!.files }
                  : message;
                return (
                  <div key={message.id} className="group/turn">
                    <AssistantTurn
                      message={rendered}
                      live={isLive}
                      phase={live?.phase}
                      approval={isLive ? live!.approval : null}
                      onApprove={(approved) => void resolveApproval(approved)}
                      ask={isLive ? live!.ask : null}
                      onAnswer={(answer) => void answerQuestion(answer)}
                      answering={answering}
                      approving={approving}
                      notices={isLive ? live!.notices : message.notices}
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
                disabledReason={boot?.model.error ?? 'No model selected'}
                centred={false}
                vision={boot?.model.capabilities.vision ?? false}
              />
            </div>
          </div>
        </>
      )}

      <Artifacts open={artifacts} onClose={() => setArtifacts(false)} />

      <Palette
        open={palette}
        onClose={() => setPalette(false)}
        conversations={conversations}
        onOpenConversation={(id) => void openConversation(id)}
        onNew={newConversation}
        onSend={(text) => void send(text, [])}
        onAdmin={(tab) => setAdmin(tab)}
        onArtifacts={() => setArtifacts(true)}
        onExport={() => void exportConversation()}
        onTheme={toggleTheme}
        onSidebar={() => setSidebar(true)}
        canExport={!!conversation && messages.length > 0}
      />

      {admin && boot && (
        <Admin state={boot.admin} initialTab={admin} onClose={() => setAdmin(null)}
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
  const [showSuggestions, setShowSuggestions] = useState(false);
  const ready = boot?.model.ok ?? false;

  return (
    <main className="flex min-h-0 flex-1 flex-col items-center justify-center px-5 pb-24">
      {/* The whole identity, in five letters. No mark, no tagline: on a screen whose only
          job is to be typed into, everything else is something to read first. */}
      {/* The gradient runs dark-to-deep on light and bright-to-mint on dark, because the
          accent at its own luminance is 1.9:1 on white — a wordmark nobody can read is not
          a brand. Same hue family either way; only the end of the scale changes. */}
      <h1 className="wordmark mb-9 select-none bg-gradient-to-br from-brand-700 via-brand-800 to-brand-950 bg-clip-text text-[30px] text-transparent animate-fade-up dark:from-brand-200 dark:via-brand-400 dark:to-brand-600 sm:text-[34px]">
        Agent
      </h1>

      <Composer
        onSend={onSend}
        onStop={onStop}
        onUpload={api.upload}
        running={running}
        disabled={!ready}
        disabledReason={boot?.model.error ?? 'No model selected'}
        centred
        vision={boot?.model.capabilities.vision ?? false}
      />

      {/* Examples stay behind one click. They are useful once — on the first run — and
          clutter on every run after it. */}
      <div className="mt-4 flex flex-col items-center">
        <button
          onClick={() => setShowSuggestions((v) => !v)}
          aria-expanded={showSuggestions}
          className={cls('focus-ring flex items-center gap-1.5 rounded-full px-2.5 py-1 text-2xs transition-all',
            showSuggestions
              ? 'text-brand-800 dark:text-brand-300'
              : 'dimmer hover:text-zinc-600 dark:hover:text-zinc-300')}
        >
          <Sparkles size={12} strokeWidth={1.8} />
          {showSuggestions ? 'Hide examples' : 'Examples'}
        </button>

        {showSuggestions && (
          <div className="mt-3 flex max-w-2xl flex-wrap justify-center gap-1.5 animate-fade-up">
            {suggestions.map((suggestion) => (
              <button key={suggestion.text} onClick={() => onSend(suggestion.text, [])} disabled={!ready}
                className="focus-ring rounded-full border px-3 py-1.5 text-2xs hairline dim transition-all hover:-translate-y-px hover:border-brand-300 hover:text-zinc-800 disabled:opacity-40 dark:hover:border-brand-500/40 dark:hover:text-zinc-100">
                {suggestion.label}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* The one piece of text that earns its place: it only appears when the chosen model
          cannot do the thing this app exists to do. */}
      {ready && !boot?.model.capabilities.tools && (
        <p className="mt-6 flex max-w-sm items-start gap-1.5 text-center text-2xs leading-relaxed text-amber-600 dark:text-amber-400">
          <AlertTriangle size={12} className="mt-px shrink-0" />
          <span>{boot?.model.model} cannot call tools — it will answer from memory alone. Pick a model that reports “tools” in Admin.</span>
        </p>
      )}
    </main>
  );
}

/**
 * Suggestions built from what is actually connected right now.
 *
 * A fixed list would advertise capabilities this install may not have — a web example on
 * a machine with web access switched off is a promise the agent then has to break.
 */
function buildSuggestions(boot: Bootstrap | null): { label: string; text: string }[] {
  if (!boot) return [];
  const names = new Set(boot.tools.map((t) => t.name));
  const out: { label: string; text: string }[] = [];
  if (names.has('web_search')) {
    out.push({ label: 'Research something',
      text: 'Research what has shipped recently around the Model Context Protocol: search, open the pages that matter, and give me a sourced summary.' });
  }
  if (names.has('run_python')) {
    out.push({ label: 'Compute something real',
      text: 'Actually run the code to work out how many working days are left this year, and what share of the year has already passed.' });
  }
  if (names.has('write_file')) {
    out.push({ label: 'Produce a file',
      text: 'Write a CSV into the workspace listing the Python modules available on this machine with their versions.' });
  }
  const mcp = boot.tools.filter((t) => t.kind === 'mcp');
  if (mcp.length) {
    const server = mcp[0].group;
    out.push({ label: `Use ${server}`, text: `Show me what you can do with ${server}, by actually calling one of its tools.` });
  } else {
    out.push({ label: 'What can you do?',
      text: 'List exactly which tools you have right now and what each one lets you actually do.' });
  }
  return out.slice(0, 4);
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
        server: event.server, kind: event.kind, by: event.by, ref: event.ref,
        status: 'running', ok: null, summary: '', text: '', ms: 0,
      });
      break;
    case 'taint':
      live.trust = { ...live.trust, sources: event.sources };
      break;
    case 'injection': {
      const flagged = live.blocks.get(event.index);
      if (flagged) live.blocks.set(event.index, { ...flagged, injection: event.patterns });
      live.trust = { ...live.trust,
                     injections: [...live.trust.injections,
                                  { tool: event.tool, patterns: event.patterns }] };
      break;
    }
    case 'offload': {
      const big = live.blocks.get(event.index);
      if (big) live.blocks.set(event.index, { ...big, offloaded: event.handle });
      break;
    }
    case 'compaction':
      live.trust = { ...live.trust, compactions: event.count };
      break;
    case 'tool.end': {
      const block = live.blocks.get(event.index);
      live.blocks.set(event.index, {
        ...(block ?? { index: event.index, type: 'tool', name: '' }),
        status: event.status, ok: event.ok, summary: event.summary, ms: event.ms,
        cached: event.cached, text: event.preview ?? '',
        ...(event.chart ? { chart: event.chart } : {}),
        ...(event.file ? { file: event.file } : {}),
        ...(event.ask ? { ask: event.ask } : {}),
      } as Block);
      break;
    }
    case 'ask.request':
      live.ask = {
        call_id: event.call_id, question: event.question, options: event.options ?? [],
        allow_other: event.allow_other ?? true, expires_in_s: event.expires_in_s ?? 0,
      };
      break;
    case 'ask.resolved':
      live.ask = null;
      break;
    case 'files':
      live.files = event.files;
      break;
    case 'approval.request':
      live.approval = {
        call_id: event.call_id, name: event.name, args: event.args,
        server: event.server, reason: event.reason, host: event.host,
        expires_in_s: event.expires_in_s ?? 0,
      };
      break;
    case 'approval.resolved':
      live.approval = null;
      break;
    case 'usage': {
      const { type: _type, ...usage } = event;
      live.usage = usage;
      break;
    }
    case 'notice':
      live.notices = [...live.notices, { text: event.message, quiet: event.quiet }];
      break;
    case 'error':
      live.error = event.message;
      break;
    default:
      break;
  }
}
