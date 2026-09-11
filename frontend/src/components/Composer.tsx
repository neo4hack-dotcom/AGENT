// The one input. It is the whole interface on an empty screen, and it has to stay that
// way once a conversation is running — same component, same place, docked instead of
// centred.

import { ArrowUp, ImageIcon, Paperclip, Square, X } from 'lucide-react';
import {
  useCallback, useEffect, useLayoutEffect, useRef, useState, type ClipboardEvent, type DragEvent,
} from 'react';
import type { UploadResult } from '../types';
import { Spinner, cls } from './ui';

const MAX_HEIGHT = 260;

export interface ComposerProps {
  /** May be async; the composer stays disabled until it settles. */
  onSend: (text: string, attachments: string[]) => void | Promise<void>;
  onStop: () => void;
  onUpload: (file: File) => Promise<UploadResult>;
  running: boolean;
  disabled?: boolean;
  disabledReason?: string;
  centred: boolean;
  vision: boolean;
  placeholder?: string;
}

export function Composer({
  onSend, onStop, onUpload, running, disabled, disabledReason, centred, vision, placeholder,
}: ComposerProps) {
  const [text, setText] = useState('');
  const [files, setFiles] = useState<UploadResult[]>([]);
  const [uploading, setUploading] = useState(0);
  const [dragging, setDragging] = useState(false);
  // The parent's `running` only turns true after a network round trip. Between the
  // keystroke and that moment the input would otherwise accept a second Enter — which is
  // exactly how one question becomes two runs.
  const [submitting, setSubmitting] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  // Grown from scrollHeight rather than rows: the height has to track wrapped lines, not
  // newline count, or a single long paragraph stays one line tall.
  useLayoutEffect(() => {
    const node = area.current;
    if (!node) return;
    node.style.height = 'auto';
    node.style.height = `${Math.min(node.scrollHeight, MAX_HEIGHT)}px`;
    node.style.overflowY = node.scrollHeight > MAX_HEIGHT ? 'auto' : 'hidden';
  }, [text]);

  useEffect(() => {
    if (!running && !submitting) area.current?.focus();
  }, [running, submitting]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      // "/" focuses the input the way it does in a search-first product, but never while
      // the user is already typing somewhere else.
      const target = event.target as HTMLElement | null;
      const typing = target && /^(INPUT|TEXTAREA)$/.test(target.tagName);
      if (event.key === '/' && !typing && !event.metaKey && !event.ctrlKey) {
        event.preventDefault();
        area.current?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const attach = useCallback(async (incoming: File[]) => {
    for (const file of incoming.slice(0, 6)) {
      setUploading((n) => n + 1);
      try {
        const result = await onUpload(file);
        setFiles((current) => [...current, result]);
      } finally {
        setUploading((n) => n - 1);
      }
    }
  }, [onUpload]);

  const send = () => {
    const value = text.trim();
    if ((!value && files.length === 0) || running || disabled || submitting) return;
    setSubmitting(true);
    setText('');
    setFiles([]);
    void Promise.resolve(onSend(value, files.map((f) => f.id)))
      .finally(() => setSubmitting(false));
  };

  const onPaste = (event: ClipboardEvent) => {
    const pasted = Array.from(event.clipboardData?.files ?? []);
    if (pasted.length) { event.preventDefault(); void attach(pasted); }
  };

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    const dropped = Array.from(event.dataTransfer?.files ?? []);
    if (dropped.length) void attach(dropped);
  };

  const canSend = (text.trim().length > 0 || files.length > 0) && !running && !disabled && !submitting;

  return (
    <div className={cls('relative w-full', centred ? 'max-w-2xl' : 'max-w-3xl')}>
      {/* The light. It breathes only while the agent is working — ornament that carries
          state stops being ornament. */}
      <div aria-hidden
        className={cls('pointer-events-none absolute -inset-x-10 -inset-y-8 -z-10 aurora transition-opacity duration-700',
          running ? 'opacity-100 animate-breathe' : centred ? 'opacity-70' : 'opacity-0')} />

      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cls(
          'surface overflow-hidden transition-all duration-200',
          'shadow-[0_2px_18px_-6px_rgba(24,24,40,0.16)] dark:shadow-[0_2px_24px_-8px_rgba(0,0,0,0.7)]',
          dragging && 'ring-2 ring-brand-500/50',
          !disabled && 'focus-within:border-brand-400/60 focus-within:ring-[3px] focus-within:ring-brand-500/12')}
      >
        {files.length > 0 && (
          <div className="flex flex-wrap gap-1.5 border-b px-3 pb-2 pt-2.5 hairline">
            {files.map((file) => (
              <span key={file.id}
                className="group flex items-center gap-1.5 rounded-lg bg-zinc-100 py-1 pl-2 pr-1 text-2xs dark:bg-white/[0.07]">
                {file.kind === 'image' ? <ImageIcon size={11} className="dimmer" /> : <Paperclip size={11} className="dimmer" />}
                <span className="max-w-[16rem] truncate">{file.name}</span>
                {file.kind === 'image' && !vision && (
                  <span className="text-amber-600 dark:text-amber-400" title="The active model cannot read images">⚠</span>
                )}
                <button onClick={() => setFiles((c) => c.filter((f) => f.id !== file.id))}
                  className="focus-ring rounded p-0.5 dimmer hover:text-zinc-700 dark:hover:text-zinc-200">
                  <X size={11} />
                </button>
              </span>
            ))}
            {uploading > 0 && <span className="flex items-center gap-1.5 px-2 py-1 text-2xs dim"><Spinner size={11} /> uploading…</span>}
          </div>
        )}

        <div className="flex items-end gap-1.5 px-2.5 py-2">
          <button
            onClick={() => fileInput.current?.click()}
            disabled={disabled}
            title="Attach a file"
            className="focus-ring mb-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg dimmer transition-colors hover:bg-zinc-100 hover:text-zinc-600 disabled:opacity-40 dark:hover:bg-white/[0.07] dark:hover:text-zinc-200"
          >
            <Paperclip size={16} strokeWidth={1.9} />
          </button>
          <input ref={fileInput} type="file" multiple hidden
            onChange={(e) => { void attach(Array.from(e.target.files ?? [])); e.target.value = ''; }} />

          <textarea
            ref={area}
            rows={1}
            value={text}
            disabled={disabled}
            placeholder={disabled ? (disabledReason ?? 'Unavailable') : (placeholder ?? 'Ask anything')}
            onChange={(e) => setText(e.target.value)}
            onPaste={onPaste}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
            className="max-h-[260px] flex-1 resize-none self-center bg-transparent py-1.5 text-[15px] leading-relaxed outline-none placeholder:text-zinc-400 disabled:cursor-not-allowed dark:placeholder:text-zinc-600"
          />

          {running || submitting ? (
            <button onClick={onStop} disabled={submitting && !running} title="Stop"
              className="focus-ring mb-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-zinc-900 text-white transition-transform hover:scale-105 active:scale-95 dark:bg-zinc-100 dark:text-zinc-900">
              <Square size={12} fill="currentColor" />
            </button>
          ) : (
            <button onClick={send} disabled={!canSend} title="Send"
              className={cls('focus-ring mb-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg transition-all',
                canSend
                  ? 'bg-brand-500 text-zinc-950 shadow-sm hover:bg-brand-400 hover:scale-105 active:scale-95'
                  : 'bg-zinc-100 text-zinc-300 dark:bg-white/[0.06] dark:text-zinc-600')}>
              <ArrowUp size={16} strokeWidth={2.4} />
            </button>
          )}
        </div>
      </div>

    </div>
  );
}
