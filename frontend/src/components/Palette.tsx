// One key for everything.
//
// Every action in this app used to cost a hunt: open Admin, find the tab, find the
// control. The keyboard already knows where it is going — this gets out of its way. Type
// three letters, press Enter.

import {
  Activity, Brain, ChevronRight, Cpu, Database, Download, FileLock2, FolderOpen, GraduationCap,
  MessageSquare, Plug, Plus, Search, ShieldCheck, Sparkles, Sun, Wrench,
  type LucideIcon,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api';
import type { ConversationSummary, SearchHit, Skill } from '../types';
import { cls, shortcut } from './ui';

export interface Command {
  id: string;
  label: string;
  hint?: string;
  group: string;
  icon: LucideIcon;
  run: () => void;
}

export function Palette({
  open, onClose, conversations, onOpenConversation, onNew, onSend, onAdmin, onArtifacts,
  onExport, onTheme, onSidebar, canExport,
}: {
  open: boolean;
  onClose: () => void;
  conversations: ConversationSummary[];
  onOpenConversation: (id: string) => void;
  onNew: () => void;
  onSend: (text: string) => void;
  onAdmin: (tab: string) => void;
  onArtifacts: () => void;
  onExport: () => void;
  onTheme: () => void;
  onSidebar: () => void;
  canExport: boolean;
}) {
  const [query, setQuery] = useState('');
  const [cursor, setCursor] = useState(0);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [hits, setHits] = useState<SearchHit[]>([]);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setQuery(''); setCursor(0); setHits([]);
    input.current?.focus();
    // Skills are the whole point of the "run a procedure" row, and there are never many.
    api.skills().then((d) => setSkills(d.skills)).catch(() => setSkills([]));
  }, [open]);

  // Searching inside conversations is the one thing that cannot be done locally.
  useEffect(() => {
    const needle = query.trim();
    if (!open || needle.length < 2) { setHits([]); return; }
    let cancelled = false;
    const timer = setTimeout(() => {
      api.search(needle).then((found) => { if (!cancelled) setHits(found.slice(0, 5)); })
        .catch(() => { if (!cancelled) setHits([]); });
    }, 200);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [query, open]);

  const commands = useMemo<Command[]>(() => {
    const go = (fn: () => void) => () => { onClose(); fn(); };
    const base: Command[] = [
      { id: 'new', label: 'New chat', group: 'Do', icon: Plus, hint: shortcut('mod', 'shift', 'O'), run: go(onNew) },
      { id: 'files', label: 'Workspace files', group: 'Do', icon: FolderOpen, run: go(onArtifacts) },
      { id: 'history', label: 'All conversations', group: 'Do', icon: MessageSquare, run: go(onSidebar) },
      { id: 'theme', label: 'Switch theme', group: 'Do', icon: Sun, run: go(onTheme) },
    ];
    if (canExport) {
      base.splice(2, 0, { id: 'export', label: 'Export this conversation',
                          hint: 'Markdown', group: 'Do', icon: Download, run: go(onExport) });
    }
    for (const skill of skills) {
      base.push({
        id: `skill:${skill.id}`, label: skill.name, group: 'Run a skill',
        hint: skill.trigger || 'saved procedure', icon: GraduationCap,
        // The skill store already matches a question to a procedure. Sending the name is
        // enough to pull the whole recipe into the prompt — no separate wiring needed.
        run: go(() => onSend(skill.name)),
      });
    }
    const tabs: [string, string, LucideIcon][] = [
      ['model', 'Model', Cpu], ['mcp', 'MCP servers', Plug], ['sources', 'Data sources', Database],
      ['tools', 'Tools', Wrench],
      ['guardrails', 'Guardrails', ShieldCheck], ['identity', 'Identity & skills', Sparkles],
      ['memory', 'Memory', Brain], ['audit', 'Audit log', FileLock2],
      ['diagnostics', 'Diagnostics', Activity],
    ];
    for (const [id, label, icon] of tabs) {
      base.push({ id: `admin:${id}`, label, group: 'Settings', icon, run: go(() => onAdmin(id)) });
    }
    for (const conversation of conversations.slice(0, 40)) {
      base.push({
        id: `conv:${conversation.id}`, group: 'Conversations', icon: MessageSquare,
        label: conversation.title || conversation.preview || 'Untitled',
        run: go(() => onOpenConversation(conversation.id)),
      });
    }
    return base;
  }, [skills, conversations, canExport, onClose, onNew, onArtifacts, onSidebar, onTheme,
      onExport, onSend, onAdmin, onOpenConversation]);

  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const matched = needle
      ? commands.filter((c) => `${c.label} ${c.group} ${c.hint ?? ''}`.toLowerCase().includes(needle))
      : commands.filter((c) => c.group !== 'Conversations').concat(
          commands.filter((c) => c.group === 'Conversations').slice(0, 5));
    const deep: Command[] = hits
      .filter((h) => !matched.some((c) => c.id === `conv:${h.conversation_id}`))
      .map((h) => ({
        id: `hit:${h.conversation_id}`, label: h.title || 'Untitled', group: 'Found inside',
        hint: h.excerpt.slice(0, 80), icon: Search,
        run: () => { onClose(); onOpenConversation(h.conversation_id); },
      }));
    const all = [...matched, ...deep];
    // Asking something outright beats hunting for a command that is not there.
    if (needle && all.length === 0) {
      all.push({ id: 'ask', label: `Ask: “${query.trim()}”`, group: 'Do', icon: ChevronRight,
                 run: () => { onClose(); onSend(query.trim()); } });
    }
    return all;
  }, [commands, query, hits, onClose, onOpenConversation, onSend]);

  useEffect(() => { setCursor(0); }, [query]);
  useEffect(() => {
    list.current?.querySelector('[data-active="true"]')
      ?.scrollIntoView({ block: 'nearest' });
  }, [cursor, shown.length]);

  if (!open) return null;

  let lastGroup = '';
  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center bg-zinc-950/30 p-4 pt-[12vh] backdrop-blur-sm animate-fade-in"
         onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="surface w-full max-w-lg animate-fade-up overflow-hidden shadow-2xl">
        <div className="flex items-center gap-2 border-b px-3.5 hairline">
          <Search size={14} className="shrink-0 dimmer" />
          <input
            ref={input}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Run, open, ask…"
            className="w-full bg-transparent py-3 text-sm outline-none placeholder:text-zinc-400 dark:placeholder:text-zinc-600"
            onKeyDown={(e) => {
              if (e.key === 'Escape') { e.preventDefault(); onClose(); }
              if (e.key === 'ArrowDown') { e.preventDefault(); setCursor((c) => (c + 1) % Math.max(1, shown.length)); }
              if (e.key === 'ArrowUp') { e.preventDefault(); setCursor((c) => (c - 1 + shown.length) % Math.max(1, shown.length)); }
              if (e.key === 'Enter') { e.preventDefault(); shown[cursor]?.run(); }
            }}
          />
          <kbd className="shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px] hairline dimmer">esc</kbd>
        </div>
        <div ref={list} className="max-h-[52vh] overflow-y-auto p-1.5">
          {shown.length === 0 && (
            <p className="px-3 py-6 text-center text-xs dimmer">Nothing matches.</p>
          )}
          {shown.map((command, i) => {
            const header = command.group !== lastGroup ? (lastGroup = command.group) : null;
            const Icon = command.icon;
            return (
              <div key={command.id}>
                {header && (
                  <p className="px-2.5 pb-1 pt-2 text-2xs font-semibold uppercase tracking-wider dimmer">
                    {header}
                  </p>
                )}
                <button
                  data-active={i === cursor}
                  onMouseEnter={() => setCursor(i)}
                  onClick={() => command.run()}
                  className={cls('flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors',
                    i === cursor ? 'bg-brand-100 dark:bg-brand-500/12' : 'hover:bg-zinc-100 dark:hover:bg-white/[0.05]')}>
                  <Icon size={13} className={cls('shrink-0',
                    i === cursor ? 'text-brand-800 dark:text-brand-300' : 'dimmer')} />
                  <span className="min-w-0 flex-1 truncate text-xs font-medium">{command.label}</span>
                  {command.hint && (
                    <span className="max-w-[45%] shrink-0 truncate text-2xs dimmer">{command.hint}</span>
                  )}
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
