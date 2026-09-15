// What the agent actually left behind.
//
// The workspace is the agent's only durable output: a script it wrote, a chart it saved, a
// CSV it exported. Without this panel, "I saved it to report.md" is a claim the reader has
// to go and verify in a terminal — which, for a browser app, means the file may as well
// not exist.

import { Download, FileText, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import type { Artifact } from '../types';
import { Empty, IconButton, Modal, Spinner, cls } from './ui';

const TEXTUAL = new Set([
  'md', 'txt', 'csv', 'tsv', 'json', 'jsonl', 'py', 'js', 'ts', 'tsx', 'sql', 'yaml', 'yml',
  'html', 'css', 'sh', 'log', 'xml', 'toml', 'ini',
]);

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10240 ? 1 : 0)} kB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

function when(timestamp: number): string {
  const seconds = Date.now() / 1000 - timestamp;
  if (seconds < 90) return 'just now';
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return new Date(timestamp * 1000).toLocaleDateString();
}

export function Artifacts({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [files, setFiles] = useState<Artifact[] | null>(null);
  const [preview, setPreview] = useState<{ path: string; text: string } | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try { setFiles(await api.artifacts()); } catch { setFiles([]); } finally { setLoading(false); }
  }, []);

  useEffect(() => { if (open) { void refresh(); setPreview(null); } }, [open, refresh]);

  return (
    <Modal open={open} onClose={onClose} wide title={
      <span className="flex items-center gap-2">
        Workspace
        {files && <span className="text-2xs font-normal dimmer">{files.length} files</span>}
        {loading ? <Spinner size={12} />
                 : <IconButton icon={RefreshCw} label="Refresh" size={12} className="h-6 w-6"
                     onClick={() => void refresh()} />}
      </span>
    }>
      {preview ? (
        <div className="animate-fade-in">
          <div className="flex items-center gap-2 border-b px-5 py-2 hairline">
            <button onClick={() => setPreview(null)}
              className="focus-ring rounded px-1 text-2xs dim hover:text-zinc-700 dark:hover:text-zinc-200">
              ← Back
            </button>
            <span className="min-w-0 flex-1 truncate font-mono text-2xs dimmer">{preview.path}</span>
            <a href={api.artifactUrl(preview.path)} download
              className="focus-ring flex items-center gap-1 rounded px-1 text-2xs dim hover:text-zinc-700 dark:hover:text-zinc-200">
              <Download size={11} /> Download
            </a>
          </div>
          <pre className="overflow-auto whitespace-pre-wrap break-words px-5 py-4 font-mono text-[11px] leading-relaxed dim">
            {preview.text.slice(0, 200_000)}
          </pre>
        </div>
      ) : !files ? (
        <div className="px-5 py-10 text-center"><Spinner /></div>
      ) : files.length === 0 ? (
        <Empty icon={FileText} title="Nothing produced yet"
          hint="Files the agent writes — scripts, exports, charts — appear here as soon as it saves them." />
      ) : (
        <div className="p-2">
          {files.map((file) => {
            const readable = TEXTUAL.has(file.kind) && file.bytes < 2_000_000;
            return (
              <div key={file.path}
                className="group flex items-center gap-2 rounded-lg px-3 py-2 transition-colors hover:bg-zinc-50 dark:hover:bg-white/[0.04]">
                <span className="grid h-6 w-6 shrink-0 place-items-center rounded-md bg-brand-100 font-mono text-[9px] font-semibold uppercase text-brand-800 dark:bg-brand-500/12 dark:text-brand-300">
                  {file.kind.slice(0, 3) || '–'}
                </span>
                <button
                  disabled={!readable}
                  onClick={async () => {
                    try { setPreview({ path: file.path, text: await api.artifactText(file.path) }); }
                    catch { /* binary or gone: the download link still works */ }
                  }}
                  className={cls('focus-ring min-w-0 flex-1 rounded text-left',
                    readable ? 'cursor-pointer' : 'cursor-default')}>
                  <span className="block truncate font-mono text-xs text-zinc-700 dark:text-zinc-200">
                    {file.path}
                  </span>
                  <span className="mt-0.5 block text-2xs dimmer">
                    {size(file.bytes)} · {when(file.modified)}
                  </span>
                </button>
                <a href={api.artifactUrl(file.path)} download title="Download"
                  className="focus-ring grid h-7 w-7 shrink-0 place-items-center rounded-lg opacity-0 transition-opacity dimmer group-hover:opacity-100 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-white/[0.07]">
                  <Download size={13} />
                </a>
              </div>
            );
          })}
        </div>
      )}
    </Modal>
  );
}
