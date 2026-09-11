// Conversation history, kept out of the way.
//
// It is a drawer rather than a permanent column because the empty screen is the product:
// a list of past conversations sitting next to a single input makes it look like a chat
// app, which is exactly the impression this interface is built to avoid.

import { MessageSquare, Pencil, Plus, Search, Trash2, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { ConversationSummary } from '../types';
import { Button, Empty, IconButton, Input, cls, useConfirm } from './ui';

function relative(timestamp: number): string {
  const seconds = Date.now() / 1000 - timestamp;
  if (seconds < 90) return 'just now';
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  if (seconds < 604800) return `${Math.round(seconds / 86400)} d ago`;
  return new Date(timestamp * 1000).toLocaleDateString();
}

export function Sidebar({
  open, onClose, conversations, activeId, onSelect, onNew, onDelete, onRename,
}: {
  open: boolean;
  onClose: () => void;
  conversations: ConversationSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onRename: (id: string, title: string) => void;
}) {
  const confirm = useConfirm();
  const [query, setQuery] = useState('');
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  useEffect(() => { if (editing) input.current?.focus(); }, [editing]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return conversations;
    return conversations.filter((c) => `${c.title} ${c.preview}`.toLowerCase().includes(needle));
  }, [conversations, query]);

  return (
    <>
      <div onClick={onClose} aria-hidden
        className={cls('fixed inset-0 z-30 bg-zinc-950/20 backdrop-blur-[2px] transition-opacity duration-200 sm:hidden',
          open ? 'opacity-100' : 'pointer-events-none opacity-0')} />
      <aside className={cls(
        'fixed inset-y-0 left-0 z-30 flex w-72 flex-col border-r bg-white transition-transform duration-200 hairline dark:bg-[#0d0d12]',
        open ? 'translate-x-0' : '-translate-x-full')}>
        <header className="flex shrink-0 items-center gap-2 px-3 py-3">
          <Button size="sm" variant="outline" icon={Plus} className="flex-1" onClick={onNew}>
            New chat
          </Button>
          <IconButton icon={X} label="Close" onClick={onClose} />
        </header>
        <div className="px-3 pb-2">
          <div className="relative">
            <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 dimmer" />
            <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search…"
              className="!py-1.5 !pl-7 !text-xs" />
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
          {filtered.length === 0 ? (
            <Empty icon={MessageSquare} title={query ? 'No match' : 'No conversations yet'} />
          ) : filtered.map((conversation) => (
            <div key={conversation.id}
              className={cls('group relative mb-0.5 rounded-lg transition-colors',
                conversation.id === activeId
                  ? 'bg-brand-100 dark:bg-brand-500/12'
                  : 'hover:bg-zinc-100 dark:hover:bg-white/[0.05]')}>
              {editing === conversation.id ? (
                <input ref={input} value={draft} onChange={(e) => setDraft(e.target.value)}
                  onBlur={() => { onRename(conversation.id, draft); setEditing(null); }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') { onRename(conversation.id, draft); setEditing(null); }
                    if (e.key === 'Escape') setEditing(null);
                  }}
                  className="focus-ring w-full bg-transparent px-2.5 py-2 text-xs outline-none" />
              ) : (
                <button onClick={() => onSelect(conversation.id)}
                  className="focus-ring block w-full px-2.5 py-2 pr-14 text-left">
                  <span className={cls('block truncate text-xs font-medium',
                    conversation.id === activeId && 'text-brand-800 dark:text-brand-300')}>
                    {conversation.title || conversation.preview || 'Untitled'}
                  </span>
                  <span className="mt-0.5 block text-2xs dimmer">{relative(conversation.updated_at)}</span>
                </button>
              )}
              <div className="absolute right-1 top-1.5 flex opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                <IconButton icon={Pencil} label="Rename" size={12} className="h-6 w-6"
                  onClick={() => { setDraft(conversation.title); setEditing(conversation.id); }} />
                <IconButton icon={Trash2} label="Delete" size={12} className="h-6 w-6"
                  onClick={async () => {
                    if (await confirm({ title: 'Delete this conversation?', danger: true, confirmLabel: 'Delete' })) {
                      onDelete(conversation.id);
                    }
                  }} />
              </div>
            </div>
          ))}
        </div>
      </aside>
    </>
  );
}
