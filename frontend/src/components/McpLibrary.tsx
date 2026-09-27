// The MCP side of the admin area: a shelf of recipes, the servers actually installed from
// it, and a bench for calling one tool by hand.
//
// The bench matters more than it looks. When the agent misuses a tool, the only fast way
// to tell "the model chose badly" from "the server is broken" is to make the call
// yourself and look at what comes back.

import {
  AlertTriangle, Check, Plug, PlugZap, Play, Plus, RefreshCw, Search, Terminal, Trash2,
  X, Zap,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import type { CatalogEntry, McpServer, RuntimeInfo } from '../types';
import {
  Badge, Button, Dot, Empty, Field, IconButton, Input, Modal, cls, useConfirm, useToast,
} from './ui';

const ACCENTS: Record<string, string> = {
  amber: 'from-amber-400/18 to-amber-500/5 text-amber-600 dark:text-amber-400',
  emerald: 'from-emerald-400/18 to-emerald-500/5 text-emerald-600 dark:text-emerald-400',
  sky: 'from-sky-400/18 to-sky-500/5 text-sky-600 dark:text-sky-400',
  violet: 'from-violet-400/18 to-violet-500/5 text-violet-600 dark:text-violet-400',
  rose: 'from-rose-400/18 to-rose-500/5 text-rose-600 dark:text-rose-400',
  zinc: 'from-zinc-400/18 to-zinc-500/5 text-zinc-600 dark:text-zinc-400',
};

const STATUS: Record<string, { tone: 'good' | 'warn' | 'bad' | 'idle'; label: string }> = {
  connected: { tone: 'good', label: 'connected' },
  connecting: { tone: 'warn', label: 'connecting…' },
  error: { tone: 'bad', label: 'error' },
  disconnected: { tone: 'idle', label: 'disconnected' },
};

export function McpLibrary({ onChanged }: { onChanged: () => void }) {
  const toast = useToast();
  const confirm = useConfirm();
  const [servers, setServers] = useState<McpServer[]>([]);
  const [entries, setEntries] = useState<CatalogEntry[]>([]);
  const [runtimes, setRuntimes] = useState<Record<string, RuntimeInfo>>({});
  const [query, setQuery] = useState('');
  const [installing, setInstalling] = useState<CatalogEntry | null>(null);
  const [custom, setCustom] = useState(false);
  const [busy, setBusy] = useState<string>('');
  const [bench, setBench] = useState<{ server: McpServer } | null>(null);

  const load = async () => {
    const [serverList, catalog] = await Promise.all([api.servers(), api.catalog()]);
    setServers(serverList);
    setEntries(catalog.entries);
    setRuntimes(catalog.runtimes);
  };

  useEffect(() => { void load().catch((e) => toast(String(e.message ?? e), 'error')); }, []);

  const act = async (id: string, action: 'connect' | 'disconnect') => {
    setBusy(id);
    try {
      await (action === 'connect' ? api.connectServer(id) : api.disconnectServer(id));
      await load();
      onChanged();
    } catch (e) {
      toast(String((e as Error).message), 'error');
    } finally { setBusy(''); }
  };

  const remove = async (server: McpServer) => {
    const ok = await confirm({
      title: `Remove “${server.name}”?`,
      body: 'The server is disconnected and its configuration erased. Any credentials you entered are lost.',
      confirmLabel: 'Remove', danger: true,
    });
    if (!ok) return;
    await api.deleteServer(server.id);
    await load();
    onChanged();
    toast('Server removed');
  };

  const installed = useMemo(() => new Set(servers.map((s) => s.catalog_id).filter(Boolean)), [servers]);
  const shelf = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return entries;
    return entries.filter((e) => `${e.name} ${e.description} ${e.category} ${e.tags.join(' ')}`
      .toLowerCase().includes(needle));
  }, [entries, query]);

  const missingRuntimes = Object.values(runtimes).filter((r) => !r.available);

  return (
    <div className="space-y-7">
      {missingRuntimes.length > 0 && (
        <div className="flex items-start gap-2.5 rounded-xl border border-amber-300/70 bg-amber-50/70 px-3.5 py-3 dark:border-amber-500/25 dark:bg-amber-500/[0.07]">
          <AlertTriangle size={15} className="mt-0.5 shrink-0 text-amber-600 dark:text-amber-400" />
          <div className="min-w-0 text-xs leading-relaxed text-amber-900 dark:text-amber-200">
            {missingRuntimes.map((runtime) => (
              <p key={runtime.name}>
                <b>{runtime.label}</b> is not on the PATH — it {runtime.why}. Install it:{' '}
                <code className="rounded bg-white/60 px-1 py-0.5 font-mono dark:bg-black/25">{runtime.install}</code>
              </p>
            ))}
          </div>
        </div>
      )}

      {/* ------------------------------------------------------- installed */}
      <section>
        <header className="mb-3 flex items-center justify-between gap-3">
          <h3 className="text-xs font-semibold uppercase tracking-wider dim">
            Connected servers <span className="dimmer">({servers.length})</span>
          </h3>
          <Button size="xs" variant="outline" icon={Plus} onClick={() => setCustom(true)}>
            Custom server
          </Button>
        </header>
        {servers.length === 0 ? (
          <Empty icon={Plug} title="No MCP server connected"
            hint="Pick one from the library below — or describe your own. The agent keeps its built-in tools either way." />
        ) : (
          <div className="space-y-2">
            {servers.map((server) => {
              const status = STATUS[server.status] ?? STATUS.disconnected;
              return (
                <div key={server.id} className="surface p-3.5">
                  <div className="flex items-start gap-3">
                    <div className={cls('grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-gradient-to-br',
                      ACCENTS[server.accent] ?? ACCENTS.sky)}>
                      <Plug size={14} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-medium">{server.name}</span>
                        <span className="flex items-center gap-1.5 text-2xs dim"><Dot tone={status.tone} />{status.label}</span>
                        {server.status === 'connected' && (
                          <Badge tone="brand">{server.tool_count} tool{server.tool_count > 1 ? 's' : ''}</Badge>
                        )}
                        {server.status === 'connected' && server.network && (
                          <span title={`Network: ${server.network}`}>
                            <Badge tone={server.network.startsWith('loopback') ? 'good' : 'neutral'}>
                              {server.network.startsWith('loopback') ? 'offline' : server.network.startsWith('internal') ? 'internal network' : 'network open'}
                            </Badge>
                          </span>
                        )}
                        <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-2xs dimmer dark:bg-white/[0.06]">
                          {server.slug}__
                        </code>
                      </div>
                      <p className="mt-1 text-2xs leading-relaxed dim">{server.description}</p>
                      {server.error && (
                        <p className="mt-1.5 rounded-lg bg-red-50 px-2 py-1.5 font-mono text-[11px] leading-relaxed text-red-700 dark:bg-red-500/10 dark:text-red-300">
                          {server.error}
                        </p>
                      )}
                      {server.diagnostics.length > 0 && server.status === 'error' && (
                        <details className="mt-1.5">
                          <summary className="cursor-pointer text-2xs dimmer">Server output</summary>
                          <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded-lg bg-zinc-50 p-2 font-mono text-[10.5px] dim dark:bg-black/25">
                            {server.diagnostics.join('\n')}
                          </pre>
                        </details>
                      )}
                      <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                        {server.status === 'connected' ? (
                          <>
                            <Button size="xs" variant="outline" icon={Terminal}
                              onClick={() => setBench({ server })}>Tools</Button>
                            <Button size="xs" variant="ghost" busy={busy === server.id}
                              onClick={() => void act(server.id, 'disconnect')}>Disconnect</Button>
                          </>
                        ) : (
                          <Button size="xs" icon={PlugZap} busy={busy === server.id}
                            onClick={() => void act(server.id, 'connect')}>Connect</Button>
                        )}
                        <label className="ml-1 flex cursor-pointer items-center gap-1.5 text-2xs dim">
                          <input type="checkbox" checked={server.auto_approve}
                            onChange={async (e) => {
                              await api.patchServer(server.id, { auto_approve: e.target.checked });
                              await load();
                            }}
                            className="h-3 w-3 accent-brand-600" />
                          auto-approve its writes
                        </label>
                        <IconButton icon={Trash2} label="Remove" className="ml-auto"
                          onClick={() => void remove(server)} />
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* --------------------------------------------------------- catalog */}
      <section>
        <header className="mb-3 flex items-center gap-3">
          <h3 className="text-xs font-semibold uppercase tracking-wider dim">Library</h3>
          <div className="relative ml-auto w-56">
            <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 dimmer" />
            <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search…"
              className="!py-1.5 !pl-7 !text-xs" />
          </div>
        </header>
        <div className="grid gap-2 sm:grid-cols-2">
          {shelf.map((entry) => (
            <button key={entry.id} onClick={() => setInstalling(entry)}
              className="focus-ring surface group flex items-start gap-3 p-3 text-left transition-all hover:-translate-y-px hover:shadow-md dark:hover:bg-white/[0.05]">
              <div className={cls('grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-gradient-to-br',
                ACCENTS[entry.accent] ?? ACCENTS.sky)}>
                <Plug size={14} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-[13px] font-medium">{entry.name}</span>
                  {installed.has(entry.id) && <Badge tone="good"><Check size={9} /> installed</Badge>}
                </div>
                <p className="mt-0.5 line-clamp-2 text-2xs leading-relaxed dim">{entry.description}</p>
                <div className="mt-1.5 flex flex-wrap items-center gap-1">
                  <span className="text-2xs dimmer">{entry.vendor}</span>
                  {entry.command && <code className="font-mono text-2xs dimmer">· {entry.command}</code>}
                </div>
              </div>
              <Plus size={14} className="mt-1 shrink-0 dimmer opacity-0 transition-opacity group-hover:opacity-100" />
            </button>
          ))}
        </div>
      </section>

      {installing && (
        <InstallDialog entry={installing} onClose={() => setInstalling(null)}
          onDone={async () => { setInstalling(null); await load(); onChanged(); }} />
      )}
      {custom && (
        <CustomDialog onClose={() => setCustom(false)}
          onDone={async () => { setCustom(false); await load(); onChanged(); }} />
      )}
      {bench && <ToolBench server={bench.server} onClose={() => setBench(null)} />}
    </div>
  );
}

/* ------------------------------------------------------------- install */

function InstallDialog({
  entry, onClose, onDone,
}: { entry: CatalogEntry; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  const submit = async () => {
    setBusy(true);
    setResult(null);
    try {
      const server = await api.addServer({ catalog_id: entry.id, values, connect: true });
      if (server.status === 'connected') {
        toast(`${entry.name} connected — ${server.tool_count} tool(s)`);
        onDone();
      } else {
        // The server was saved; it just could not start. Keeping the dialog open with the
        // real error beats a toast that vanishes before it can be read.
        setResult({ ok: false, message: server.error ?? 'Could not connect.' });
      }
    } catch (e) {
      setResult({ ok: false, message: String((e as Error).message) });
    } finally { setBusy(false); }
  };

  return (
    <Modal open onClose={onClose} title={`Connect ${entry.name}`}>
      <div className="space-y-4 p-5">
        <p className="text-xs leading-relaxed dim">{entry.description}</p>
        {entry.params.map((param) => (
          <Field key={param.key} label={param.label} hint={param.help}>
            <Input
              type={param.secret ? 'password' : 'text'}
              placeholder={param.placeholder}
              autoComplete="off"
              value={values[param.key] ?? ''}
              onChange={(e) => setValues((v) => ({ ...v, [param.key]: e.target.value }))}
            />
          </Field>
        ))}
        {entry.params.length === 0 && (
          <p className="rounded-lg bg-zinc-50 px-3 py-2 text-2xs dim dark:bg-white/[0.04]">
            Nothing to configure. The server is fetched and started on connect.
          </p>
        )}
        {result && !result.ok && (
          <p className="rounded-lg bg-red-50 px-3 py-2 font-mono text-[11px] leading-relaxed text-red-700 dark:bg-red-500/10 dark:text-red-300">
            {result.message}
          </p>
        )}
        <div className="flex items-center justify-between gap-2 pt-1">
          <a href={entry.docs} target="_blank" rel="noreferrer noopener"
             className="text-2xs dimmer underline underline-offset-2 hover:text-zinc-600">Documentation</a>
          <div className="flex gap-2">
            <Button variant="outline" onClick={onClose}>Cancel</Button>
            <Button busy={busy} onClick={() => void submit()} icon={PlugZap}>Connect</Button>
          </div>
        </div>
      </div>
    </Modal>
  );
}

/* -------------------------------------------------------------- custom */

function CustomDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [transport, setTransport] = useState<'stdio' | 'http'>('stdio');
  const [name, setName] = useState('');
  const [command, setCommand] = useState('npx');
  const [args, setArgs] = useState('');
  const [url, setUrl] = useState('');
  const [env, setEnv] = useState('');
  const [reach, setReach] = useState<'' | 'local' | 'internal'>('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const parseEnv = (raw: string): Record<string, string> =>
    Object.fromEntries(raw.split('\n').map((line) => line.split('=')).filter((p) => p.length >= 2)
      .map(([k, ...rest]) => [k.trim(), rest.join('=').trim()]));

  const submit = async () => {
    setBusy(true); setError('');
    try {
      const server = await api.addServer({
        name, transport, command: transport === 'stdio' ? command : '',
        // Split on whitespace outside quotes: a path with spaces is common enough that
        // a naive split would break the most likely custom server anyone adds.
        args: transport === 'stdio'
          ? (args.match(/"[^"]*"|'[^']*'|\S+/g) ?? []).map((a) => a.replace(/^["']|["']$/g, ''))
          : [],
        url: transport === 'http' ? url : '',
        env: parseEnv(env), network: transport === 'stdio' ? reach : '', connect: true,
      });
      if (server.status === 'connected') { toast(`${name} connected`); onDone(); }
      else setError(server.error ?? 'Could not connect.');
    } catch (e) {
      setError(String((e as Error).message));
    } finally { setBusy(false); }
  };

  return (
    <Modal open onClose={onClose} title="Custom MCP server">
      <div className="space-y-4 p-5">
        <div className="flex gap-1.5">
          {(['stdio', 'http'] as const).map((t) => (
            <button key={t} onClick={() => setTransport(t)}
              className={cls('focus-ring flex-1 rounded-lg border px-3 py-2 text-xs font-medium transition-colors',
                transport === t ? 'border-brand-500 bg-brand-100 text-brand-800 dark:bg-brand-500/12 dark:text-brand-300' : 'hairline dim')}>
              {t === 'stdio' ? 'Local process (stdio)' : 'HTTP endpoint'}
            </button>
          ))}
        </div>
        <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="My server" /></Field>
        {transport === 'stdio' ? (
          <>
            <Field label="Command"><Input value={command} onChange={(e) => setCommand(e.target.value)} placeholder="npx" /></Field>
            <Field label="Arguments" hint="Whitespace-separated; quote a path that contains spaces.">
              <Input value={args} onChange={(e) => setArgs(e.target.value)} placeholder="-y my-mcp-server --flag value" />
            </Field>
            <Field label="Network" hint="Offline servers run in a sandbox that allows this machine only. Choose internal for a server that queries a database or service elsewhere in your network.">
              <div className="flex gap-1.5">
                {([['', 'Automatic'], ['local', 'Offline'], ['internal', 'Internal network']] as const).map(([value, label]) => (
                  <button key={value} type="button" onClick={() => setReach(value)}
                    className={cls('focus-ring flex-1 rounded-lg border px-2.5 py-1.5 text-2xs font-medium transition-colors',
                      reach === value ? 'border-brand-500 bg-brand-100 text-brand-800 dark:bg-brand-500/12 dark:text-brand-300' : 'hairline dim')}>
                    {label}
                  </button>
                ))}
              </div>
            </Field>
          </>
        ) : (
          <Field label="URL"><Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="http://mcp.corp.internal:8080/mcp" /></Field>
        )}
        <Field label="Environment variables" hint="One per line, as KEY=value.">
          <textarea value={env} onChange={(e) => setEnv(e.target.value)} rows={3}
            placeholder="API_KEY=…"
            className="focus-ring w-full rounded-xl border bg-white px-3 py-2 font-mono text-xs hairline dark:bg-white/[0.04]" />
        </Field>
        {error && (
          <p className="rounded-lg bg-red-50 px-3 py-2 font-mono text-[11px] leading-relaxed text-red-700 dark:bg-red-500/10 dark:text-red-300">{error}</p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button busy={busy} disabled={!name.trim()} onClick={() => void submit()} icon={PlugZap}>Connect</Button>
        </div>
      </div>
    </Modal>
  );
}

/* ---------------------------------------------------------------- bench */

interface ServerTool {
  qualified_name: string;
  name: string;
  title: string;
  description: string;
  input_schema: Record<string, unknown>;
  write: boolean;
  read_only: boolean;
}

function ToolBench({ server, onClose }: { server: McpServer; onClose: () => void }) {
  const [tools, setTools] = useState<ServerTool[]>([]);
  const [selected, setSelected] = useState<ServerTool | null>(null);
  const [payload, setPayload] = useState('{}');
  const [result, setResult] = useState<string>('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void api.serverTools(server.id).then((list) => {
      setTools(list);
      if (list.length) select(list[0]);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [server.id]);

  const select = (tool: ServerTool) => {
    setSelected(tool);
    setResult('');
    // Seed the payload from the schema so the shape is right before anything is typed —
    // guessing argument names from a description is the slowest part of testing a tool.
    const properties = (tool.input_schema?.properties ?? {}) as Record<string, { type?: string }>;
    const required = (tool.input_schema?.required ?? []) as string[];
    const seed: Record<string, unknown> = {};
    for (const key of Object.keys(properties)) {
      if (required.length && !required.includes(key)) continue;
      const type = properties[key]?.type;
      seed[key] = type === 'number' || type === 'integer' ? 0 : type === 'boolean' ? false
        : type === 'array' ? [] : type === 'object' ? {} : '';
    }
    setPayload(JSON.stringify(seed, null, 2));
  };

  const run = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      const args = JSON.parse(payload || '{}');
      const response = await api.callTool(selected.qualified_name, args);
      setResult(JSON.stringify(response, null, 2));
    } catch (e) {
      setResult(`${(e as Error).name}: ${(e as Error).message}`);
    } finally { setBusy(false); }
  };

  return (
    <Modal open onClose={onClose} wide title={<span className="flex items-center gap-2">{server.name}
      <Badge tone="brand">{tools.length} tools</Badge></span>}>
      <div className="grid gap-0 sm:grid-cols-[13rem_1fr]">
        <div className="max-h-[60vh] overflow-y-auto border-b hairline sm:border-b-0 sm:border-r">
          {tools.map((tool) => (
            <button key={tool.qualified_name} onClick={() => select(tool)}
              className={cls('flex w-full items-center gap-1.5 border-b px-3 py-2 text-left text-2xs hairline transition-colors',
                selected?.qualified_name === tool.qualified_name
                  ? 'bg-brand-100 text-brand-800 dark:bg-brand-500/12 dark:text-brand-300'
                  : 'hover:bg-zinc-50 dark:hover:bg-white/[0.04]')}>
              <span className="min-w-0 flex-1 truncate font-mono">{tool.name}</span>
              {tool.write && <Zap size={10} className="shrink-0 text-amber-500" />}
            </button>
          ))}
        </div>
        <div className="min-w-0 space-y-3 p-4">
          {selected && (
            <>
              <p className="text-xs leading-relaxed dim">{selected.description || 'No description provided.'}</p>
              <Field label="Arguments (JSON)">
                <textarea value={payload} onChange={(e) => setPayload(e.target.value)} rows={6}
                  className="focus-ring w-full rounded-xl border bg-white px-3 py-2 font-mono text-[11.5px] hairline dark:bg-black/25" />
              </Field>
              <Button size="sm" icon={Play} busy={busy} onClick={() => void run()}>Call</Button>
              {result && (
                <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-xl border p-3 font-mono text-[11px] leading-relaxed hairline bg-zinc-50 dark:bg-black/25">
                  {result}
                </pre>
              )}
            </>
          )}
          {!selected && tools.length === 0 && (
            <Empty icon={X} title="This server exposes no tools" />
          )}
        </div>
      </div>
    </Modal>
  );
}

export function RefreshButton({ onClick }: { onClick: () => void }) {
  return <IconButton icon={RefreshCw} label="Refresh" onClick={onClick} />;
}
