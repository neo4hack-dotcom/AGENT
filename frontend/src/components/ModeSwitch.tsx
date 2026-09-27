// Agent, or the model on its own with the MCP servers you pick.
//
// Deliberately quiet: one small icon beside the paperclip, coloured only when direct mode
// is on. Direct mode is for when you want the model and the tools and nothing else — no
// routing, plan, critic or composed answer — and the choice of servers is yours.

import { Check, Plug, Sparkles } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { DirectServer } from '../types';
import { cls } from './ui';

export type Mode = 'agent' | 'direct';

const MODE_KEY = 'agent.mode';
const SERVERS_KEY = 'agent.directServers';

export function loadModeChoice(): { mode: Mode; servers: string[] } {
  try {
    const mode = localStorage.getItem(MODE_KEY) === 'direct' ? 'direct' : 'agent';
    const servers = JSON.parse(localStorage.getItem(SERVERS_KEY) || '[]');
    return { mode, servers: Array.isArray(servers) ? servers.map(String) : [] };
  } catch { return { mode: 'agent', servers: [] }; }
}

export function saveModeChoice(mode: Mode, servers: string[]): void {
  try {
    localStorage.setItem(MODE_KEY, mode);
    localStorage.setItem(SERVERS_KEY, JSON.stringify(servers));
  } catch { /* private browsing: the choice lasts for this page */ }
}

export function ModeSwitch({ mode, servers, selected, onChange, onOpen, disabled }: {
  mode: Mode; servers: DirectServer[]; selected: string[];
  onChange: (mode: Mode, selected: string[]) => void; onOpen?: () => void; disabled?: boolean;
}) {
  // The popover lives in a portal: the composer clips its own overflow, and a menu cut in
  // half by the box it belongs to is worse than no menu.
  const [anchor, setAnchor] = useState<{ left: number; bottom: number; maxHeight: number } | null>(null);
  const open = anchor !== null;
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      const target = e.target as Node;
      if (!button.current?.contains(target) && !panel.current?.contains(target)) setAnchor(null);
    };
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') setAnchor(null); };
    const away = () => setAnchor(null);
    window.addEventListener('mousedown', close);
    window.addEventListener('keydown', key);
    window.addEventListener('resize', away);
    return () => {
      window.removeEventListener('mousedown', close);
      window.removeEventListener('keydown', key);
      window.removeEventListener('resize', away);
    };
  }, [open]);
  const toggleOpen = () => {
    if (open) { setAnchor(null); return; }
    const rect = button.current?.getBoundingClientRect();
    if (!rect) return;
    setAnchor({ left: Math.max(8, Math.min(rect.left, window.innerWidth - 296)),
                bottom: window.innerHeight - rect.top + 8, maxHeight: Math.max(160, rect.top - 16) });
    onOpen?.();
  };

  const usable = servers.filter((s) => s.connected);
  const active = selected.filter((id) => usable.some((s) => s.id === id));
  const toggle = (id: string) =>
    onChange(mode, selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);

  return (
    <div className="mb-0.5 shrink-0">
      <button ref={button} onClick={toggleOpen} disabled={disabled}
        title={mode === 'direct' ? `Direct LLM · ${active.length} MCP server(s)` : 'Mode: agent'}
        aria-label="Choose the mode"
        className={cls('focus-ring flex h-8 items-center gap-1 rounded-lg px-2 text-2xs transition-colors disabled:opacity-40',
          mode === 'direct'
            ? 'text-brand-700 hover:bg-brand-50 dark:text-brand-300 dark:hover:bg-brand-500/10'
            : 'dimmer hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-white/[0.07] dark:hover:text-zinc-200')}>
        {mode === 'direct' ? <Plug size={14} strokeWidth={1.9} /> : <Sparkles size={14} strokeWidth={1.9} />}
        {mode === 'direct' && <span className="font-medium">Direct · {active.length}</span>}
      </button>
      {anchor && createPortal(
        <div ref={panel} style={{ left: anchor.left, bottom: anchor.bottom, maxHeight: anchor.maxHeight }}
          className="fixed z-50 flex w-72 flex-col rounded-xl border bg-white p-2 shadow-lg hairline animate-fade-in dark:bg-zinc-900">
          {(['agent', 'direct'] as const).map((kind) => (
            <button key={kind} onClick={() => onChange(kind, selected)}
              className={cls('focus-ring flex w-full shrink-0 items-start gap-2 rounded-lg px-2.5 py-2 text-left transition-colors',
                mode === kind ? 'bg-brand-50 dark:bg-brand-500/10' : 'hover:bg-zinc-50 dark:hover:bg-white/[0.04]')}>
              <span className="mt-0.5">{kind === 'agent' ? <Sparkles size={13} /> : <Plug size={13} />}</span>
              <span className="min-w-0 flex-1">
                <span className="block text-xs font-medium">{kind === 'agent' ? 'Agent' : 'Direct LLM'}</span>
                <span className="block text-2xs leading-snug dimmer">
                  {kind === 'agent'
                    ? 'Chooses sources, plans, checks, charts, traces every figure.'
                    : 'The model and the MCP servers you pick — nothing on top.'}
                </span>
              </span>
              {mode === kind && <Check size={12} className="mt-0.5 text-brand-600" strokeWidth={3} />}
            </button>
          ))}
          {mode === 'direct' && (
            <div className="mt-1.5 flex min-h-0 flex-col border-t pt-1.5 hairline">
              <p className="px-2.5 pb-1 text-2xs font-medium dim">MCP servers the model may use</p>
              {usable.length === 0 && <p className="px-2.5 py-1 text-2xs dimmer">No server connected.</p>}
              <div className="max-h-56 min-h-0 overflow-y-auto">
                {usable.map((server) => (
                  <label key={server.id}
                    className="flex cursor-pointer items-center gap-2 rounded-md px-2.5 py-1.5 text-xs hover:bg-zinc-50 dark:hover:bg-white/[0.04]">
                    <input type="checkbox" checked={selected.includes(server.id)} onChange={() => toggle(server.id)}
                      className="h-3.5 w-3.5 accent-brand-600" />
                    <span className="min-w-0 flex-1 truncate">{server.name}</span>
                    <span className="text-2xs dimmer">{server.role === 'catalog' ? 'catalog' : `${server.tool_count} tools`}</span>
                  </label>
                ))}
              </div>
              {usable.length > 0 && (
                <div className="flex gap-2 px-2.5 pt-1 text-2xs">
                  <button className="dim hover:underline" onClick={() => onChange(mode, usable.map((s) => s.id))}>all</button>
                  <button className="dim hover:underline" onClick={() => onChange(mode, [])}>none</button>
                </div>
              )}
            </div>
          )}
        </div>,
        document.body,
      )}
    </div>
  );
}
