// What an answer hands over: files to download, and — while the agent waits — a question.
//
// Both live in the answer, never in the folded work: a file is part of what was asked for,
// and a question waiting on the reader must not need a click to be found.

import { Check, Download, ExternalLink, FileJson, FileSpreadsheet, FileText, MessageCircleQuestion, Send } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { AskRequest, FileOut } from '../types';
import { Button, cls } from './ui';

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(bytes < 10240 ? 1 : 0)} kB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

const LABEL: Record<string, string> = { xlsx: 'Excel', csv: 'CSV', json: 'JSON', pdf: 'PDF', md: 'Markdown', png: 'Image', svg: 'Image' };

const url = (file: FileOut, mode: 'download' | 'inline') =>
  `/api/artifacts/${file.path.split('/').map(encodeURIComponent).join('/')}?${mode}=1`;

export function FileCard({ file }: { file: FileOut }) {
  const Icon = file.format === 'pdf' ? FileText : file.format === 'json' ? FileJson : FileSpreadsheet;
  const meta = [
    LABEL[file.format] ?? file.format.toUpperCase(),
    file.pages ? `${file.pages} page${file.pages > 1 ? 's' : ''}` : '',
    file.rows !== undefined && file.format !== 'pdf' ? `${file.rows.toLocaleString()} rows` : '',
    (file.sheets?.length ?? 0) > 1 ? `${file.sheets!.length} sheets` : '',
    size(file.bytes),
  ].filter(Boolean).join(' · ');
  return (
    <div className="my-3 flex items-center gap-3 rounded-xl border px-3.5 py-3 hairline bg-white dark:bg-white/[0.02] animate-fade-up">
      <span className={cls('grid h-10 w-10 shrink-0 place-items-center rounded-lg',
        file.format === 'pdf' ? 'bg-red-50 text-red-600 dark:bg-red-500/10 dark:text-red-400'
                              : 'bg-brand-100 text-brand-800 dark:bg-brand-500/12 dark:text-brand-300')}>
        <Icon size={18} strokeWidth={1.8} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13px] font-medium text-zinc-800 dark:text-zinc-100">{file.name}</p>
        <p className="mt-0.5 text-2xs dimmer">{meta}</p>
      </div>
      {file.format === 'pdf' && (
        <a href={url(file, 'inline')} target="_blank" rel="noreferrer"
          className="focus-ring flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-2xs font-medium dim transition-colors hover:bg-zinc-100 dark:hover:bg-white/[0.06]">
          <ExternalLink size={12} /> Open
        </a>
      )}
      <a href={url(file, 'download')} download={file.name}
        className="focus-ring flex items-center gap-1 rounded-lg bg-zinc-900 px-3 py-1.5 text-2xs font-medium text-white transition-colors hover:bg-zinc-800 dark:bg-white dark:text-zinc-900 dark:hover:bg-zinc-200">
        <Download size={12} /> Download
      </a>
    </div>
  );
}

export function AskCard({ request, onAnswer, busy }: {
  request: AskRequest; onAnswer: (answer: string) => void; busy?: boolean;
}) {
  const [other, setOther] = useState('');
  const [picked, setPicked] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { if (!request.options.length) input.current?.focus(); }, [request.call_id, request.options.length]);

  const send = (answer: string) => {
    const value = answer.trim();
    if (!value || busy) return;
    setPicked(value);
    onAnswer(value);
  };

  return (
    <div className="my-3 overflow-hidden rounded-xl border border-brand-300/60 bg-brand-50/50 dark:border-brand-500/25 dark:bg-brand-500/[0.06] animate-fade-up">
      <div className="flex items-start gap-2.5 px-3.5 py-3">
        <MessageCircleQuestion size={16} className="mt-0.5 shrink-0 text-brand-700 dark:text-brand-300" />
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-medium leading-snug text-zinc-900 dark:text-zinc-100">
            {request.question}
          </p>
          {request.options.length > 0 && (
            <div className="mt-2.5 flex flex-wrap gap-1.5">
              {request.options.map((option) => (
                <button key={option} disabled={busy} onClick={() => send(option)}
                  className={cls('focus-ring flex items-center gap-1 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-60',
                    picked === option
                      ? 'border-brand-500 bg-brand-500 text-white'
                      : 'border-brand-300/70 bg-white text-zinc-800 hover:border-brand-500 hover:bg-brand-50 dark:border-brand-500/30 dark:bg-white/[0.04] dark:text-zinc-100 dark:hover:bg-brand-500/10')}>
                  {picked === option && <Check size={11} strokeWidth={3} />}
                  {option}
                </button>
              ))}
            </div>
          )}
          {request.allow_other && (
            <form className="mt-2.5 flex gap-1.5"
              onSubmit={(e) => { e.preventDefault(); send(other); }}>
              <input ref={input} value={other} onChange={(e) => setOther(e.target.value)} disabled={busy}
                placeholder={request.options.length ? 'Or answer in your own words…' : 'Your answer…'}
                className="focus-ring min-w-0 flex-1 rounded-lg border bg-white px-2.5 py-1.5 text-xs hairline outline-none dark:bg-black/20" />
              <Button size="xs" icon={Send} disabled={!other.trim() || busy} busy={busy && picked === other.trim()}>
                Send
              </Button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
