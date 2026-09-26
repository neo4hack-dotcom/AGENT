// The admin area. Discreet by design — one keyboard shortcut and one small control in the
// corner — because on a personal agent this is a place you visit twice and then forget.
//
// Everything here is the *effective* configuration: what Admin sets overrides the
// environment, and every panel says which one a value is currently coming from. A
// settings screen that cannot tell you why a value is what it is, is a settings screen
// people stop trusting.

import {
  Activity, Brain, Check, Cpu, Database, Eye, FileLock2, GraduationCap, KeyRound, Plug, Settings2,
  ShieldAlert, ShieldCheck, Sparkles, Trash2, Wrench, X, Zap,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { api, setAdminToken } from '../api';
import type {
  AdminState, AuditReport, Diagnostics, MemoryEntry, ModelOption, RunMetrics, Skill,
  SkillStats, ToolInfo,
} from '../types';
import { McpLibrary } from './McpLibrary';
import { SourcesPanel } from './Sources';
import {
  Badge, Button, Dot, Empty, Field, IconButton, Input, Switch, cls, useConfirm,
  useToast,
} from './ui';

type Tab = 'model' | 'mcp' | 'sources' | 'tools' | 'guardrails' | 'identity' | 'memory'
  | 'audit' | 'diagnostics';

const TABS: { id: Tab; label: string; icon: typeof Cpu }[] = [
  { id: 'model', label: 'Model', icon: Cpu },
  { id: 'mcp', label: 'MCP servers', icon: Plug },
  { id: 'sources', label: 'Data sources', icon: Database },
  { id: 'tools', label: 'Tools', icon: Wrench },
  { id: 'guardrails', label: 'Guardrails', icon: ShieldCheck },
  { id: 'identity', label: 'Identity', icon: Sparkles },
  { id: 'memory', label: 'Memory', icon: Brain },
  { id: 'audit', label: 'Audit', icon: FileLock2 },
  { id: 'diagnostics', label: 'Diagnostics', icon: Activity },
];

export function Admin({
  state, onClose, onChanged, initialTab,
}: { state: AdminState; onClose: () => void; onChanged: () => void; initialTab?: string }) {
  const [tab, setTab] = useState<Tab>(
    (TABS.some((t) => t.id === initialTab) ? initialTab : 'model') as Tab);
  // Follow the prop when it changes, not only on mount: the palette can name a tab while
  // this panel is already open, and an initial-value-only state would silently ignore it.
  useEffect(() => {
    if (initialTab && TABS.some((t) => t.id === initialTab)) setTab(initialTab as Tab);
  }, [initialTab]);
  const [authenticated, setAuthenticated] = useState(state.authenticated);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-40 flex flex-col bg-white dark:bg-[#0b0b0f] animate-fade-in">
      <header className="flex shrink-0 items-center gap-3 border-b px-5 py-3 hairline">
        <Settings2 size={15} className="text-brand-500" />
        <h1 className="text-sm font-semibold tracking-tight">Admin</h1>
        <Badge tone={state.mode === 'password' ? 'good' : 'neutral'}>
          {state.mode === 'password' ? 'password protected' : 'local access only'}
        </Badge>
        <IconButton icon={X} label="Close" className="ml-auto" onClick={onClose} />
      </header>

      {!authenticated ? (
        <LoginGate mode={state.mode} onDone={() => { setAuthenticated(true); onChanged(); }} />
      ) : (
        <div className="flex min-h-0 flex-1">
          <nav className="w-48 shrink-0 space-y-0.5 overflow-y-auto border-r p-2 hairline">
            {TABS.map(({ id, label, icon: Icon }) => (
              <button key={id} onClick={() => setTab(id)}
                className={cls('focus-ring flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-xs font-medium transition-colors',
                  tab === id
                    ? 'bg-brand-100 text-brand-800 dark:bg-brand-500/12 dark:text-brand-300'
                    : 'dim hover:bg-zinc-100 dark:hover:bg-white/[0.06]')}>
                <Icon size={14} strokeWidth={1.9} /> {label}
              </button>
            ))}
          </nav>
          <main className="min-w-0 flex-1 overflow-y-auto">
            <div className="mx-auto max-w-3xl p-6">
              {tab === 'model' && <ModelPanel onChanged={onChanged} />}
              {tab === 'mcp' && <McpLibrary onChanged={onChanged} />}
              {tab === 'sources' && <SourcesPanel />}
              {tab === 'tools' && <ToolsPanel />}
              {tab === 'guardrails' && <GuardrailsPanel onChanged={onChanged} />}
              {tab === 'identity' && <IdentityPanel />}
              {tab === 'memory' && <MemoryPanel />}
              {tab === 'audit' && <AuditPanel />}
              {tab === 'diagnostics' && <DiagnosticsPanel />}
            </div>
          </main>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ login */

function LoginGate({ mode, onDone }: { mode: string; onDone: () => void }) {
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  if (mode !== 'password') {
    return (
      <div className="flex flex-1 items-center justify-center p-8">
        <div className="max-w-md text-center">
          <ShieldCheck size={22} className="mx-auto mb-3 dimmer" />
          <p className="text-sm font-medium">Admin is limited to this machine</p>
          <p className="mt-2 text-xs leading-relaxed dim">
            No password is set, so admin is reachable only from the computer running this app.
            To administer it from elsewhere, set
            <code className="mx-1 rounded bg-zinc-100 px-1 py-0.5 font-mono dark:bg-white/[0.07]">AGENT_ADMIN_PASSWORD</code>
            and restart.
          </p>
        </div>
      </div>
    );
  }

  const submit = async () => {
    setBusy(true); setError('');
    try {
      const { token } = await api.login(password);
      setAdminToken(token);
      onDone();
    } catch (e) {
      setError(String((e as Error).message));
    } finally { setBusy(false); }
  };

  return (
    <div className="flex flex-1 items-center justify-center p-8">
      <div className="w-full max-w-xs space-y-4">
        <div className="text-center">
          <KeyRound size={20} className="mx-auto mb-2 dimmer" />
          <p className="text-sm font-medium">Admin password</p>
        </div>
        <Input type="password" autoFocus value={password} placeholder="••••••••"
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') void submit(); }} />
        {error && <p className="text-center text-xs text-red-600 dark:text-red-400">{error}</p>}
        <Button className="w-full" size="md" busy={busy} onClick={() => void submit()}>Enter</Button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ model */

function ModelPanel({ onChanged }: { onChanged: () => void }) {
  const toast = useToast();
  const [data, setData] = useState<{ models: ModelOption[]; selected: string; fast_selected: string;
                                     ok: boolean; error: string | null } | null>(null);
  const [baseUrl, setBaseUrl] = useState('');
  const [saving, setSaving] = useState('');

  const load = async () => {
    const [models, prefs] = await Promise.all([api.models(), api.getPrefs()]);
    setData(models);
    setBaseUrl(String(prefs.effective.ollama_base_url ?? ''));
  };
  useEffect(() => { void load().catch((e) => toast(String(e.message), 'error')); }, []);

  const choose = async (name: string, which: 'model' | 'fast_model') => {
    setSaving(name + which);
    try {
      await api.setPrefs({ [which]: name });
      await load();
      onChanged();
      toast(which === 'model' ? `Active model: ${name}` : `Fast model: ${name}`);
    } finally { setSaving(''); }
  };

  if (!data) return <PanelSkeleton />;

  return (
    <div className="space-y-6">
      <section>
        <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider dim">Ollama server</h3>
        <p className="mb-3 text-2xs leading-relaxed dimmer">
          Every model runs through this endpoint. A model tagged <b>remote</b> is hosted by
          Ollama rather than by this machine — the status badge in the header always says which.
        </p>
        <div className="flex gap-2">
          <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="http://localhost:11434" className="font-mono !text-xs" />
          <Button variant="outline" onClick={async () => {
            await api.setPrefs({ ollama_base_url: baseUrl });
            await load(); onChanged(); toast('Endpoint saved');
          }}>Save</Button>
        </div>
        {data.error && (
          <p className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs leading-relaxed text-red-700 dark:bg-red-500/10 dark:text-red-300">
            {data.error}
          </p>
        )}
      </section>

      <section>
        <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider dim">Reasoning model</h3>
        <p className="mb-3 text-2xs leading-relaxed dimmer">
          Capabilities come from the server, never guessed from the model's name. Without
          <b> tools</b>, the agent cannot call anything — it only answers from memory.
        </p>
        <div className="space-y-1.5">
          {data.models.map((model) => {
            const active = model.name === data.selected;
            const usable = model.capabilities.tools;
            return (
              <div key={model.name}
                className={cls('flex items-center gap-3 rounded-xl border px-3 py-2.5 transition-colors',
                  active ? 'border-brand-500 bg-brand-50 dark:border-brand-500/40 dark:bg-brand-500/[0.08]' : 'hairline')}>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-mono text-xs font-medium">{model.name}</span>
                    {!model.local && <Badge tone="warn">remote</Badge>}
                    {model.refused && <span title={model.refused}><Badge tone="bad">leaves the network</Badge></span>}
                    {model.parameters && <span className="text-2xs dimmer">{model.parameters}</span>}
                    {model.size_gb > 0 && <span className="text-2xs dimmer">· {model.size_gb} GB</span>}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    <CapabilityBadge on={model.capabilities.tools} icon={Wrench} label="tools" />
                    <CapabilityBadge on={model.capabilities.thinking} icon={Brain} label="thinking" />
                    <CapabilityBadge on={model.capabilities.vision} icon={Eye} label="vision" />
                    {model.capabilities.context_length > 0 && (
                      <Badge>{Math.round(model.capabilities.context_length / 1024)}k ctx</Badge>
                    )}
                  </div>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1">
                  <Button size="xs" variant={active ? 'subtle' : 'outline'} disabled={active || !!model.refused}
                    title={model.refused || undefined}
                    busy={saving === model.name + 'model'}
                    onClick={() => void choose(model.name, 'model')}>
                    {active ? <><Check size={11} /> active</> : usable ? 'Use' : 'Use anyway'}
                  </Button>
                  <button disabled={!!model.refused}
                    onClick={() => void choose(model.name, 'fast_model')}
                    className={cls('focus-ring rounded px-1 text-2xs transition-colors',
                      model.name === data.fast_selected ? 'text-brand-800 dark:text-brand-300' : 'dimmer hover:text-zinc-600')}>
                    {model.name === data.fast_selected ? '★ fast model' : 'set as fast model'}
                  </button>
                </div>
              </div>
            );
          })}
          {data.models.length === 0 && (
            <Empty icon={Cpu} title="No model available"
              hint={<>Pull one: <code className="font-mono">ollama pull qwen3.5:4b</code></>} />
          )}
        </div>
        <p className="mt-3 text-2xs leading-relaxed dimmer">
          The <b>fast model</b> handles the critic, the final gap check and conversation titles —
          never the answer. A smaller one there saves real time at no cost in quality.
        </p>
      </section>
    </div>
  );
}

function CapabilityBadge({ on, icon: Icon, label }: { on: boolean; icon: typeof Wrench; label: string }) {
  return (
    <span className={cls('inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-2xs font-medium',
      on ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/12 dark:text-emerald-300'
         : 'bg-zinc-100 text-zinc-400 line-through dark:bg-white/[0.05] dark:text-zinc-600')}>
      <Icon size={9} /> {label}
    </span>
  );
}

/* ------------------------------------------------------------------ tools */

function ToolsPanel() {
  const [tools, setTools] = useState<ToolInfo[]>([]);
  useEffect(() => { void api.bootstrap().then((b) => setTools(b.tools)); }, []);
  const groups = tools.reduce<Record<string, ToolInfo[]>>((acc, tool) => {
    (acc[tool.group] ??= []).push(tool);
    return acc;
  }, {});
  return (
    <div className="space-y-5">
      <p className="text-xs leading-relaxed dim">
        Everything the agent can call right now — {tools.length} tool(s). Tools marked
        <Zap size={10} className="mx-1 inline text-amber-500" /> can change something outside this
        app, and go through approval depending on the guardrail setting.
      </p>
      {Object.entries(groups).map(([group, list]) => (
        <section key={group}>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider dim">{group}</h3>
          <div className="space-y-1">
            {list.map((tool) => (
              <div key={tool.name} className="flex items-start gap-2.5 rounded-lg border px-3 py-2 hairline">
                <span className="mt-0.5 shrink-0 font-mono text-2xs font-medium text-brand-800 dark:text-brand-300">
                  {tool.name}
                </span>
                {tool.write && <Zap size={11} className="mt-0.5 shrink-0 text-amber-500" />}
                <span className="min-w-0 text-2xs leading-relaxed dim">{tool.description}</span>
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------- guardrails */

const APPROVAL_MODES = [
  { value: 'never', label: 'Never', hint: 'The agent acts alone, MCP writes included.' },
  { value: 'writes', label: 'For writes', hint: 'Recommended: an MCP tool that changes something asks you first.' },
  { value: 'always', label: 'Always', hint: 'Every tool needs approval, local Python execution included.' },
];

const NUMERIC: { key: string; label: string; hint: string; min: number; max: number }[] = [
  { key: 'max_iterations', label: 'Max tool turns', min: 1, max: 60,
    hint: 'Ceiling on model → tools round trips within a single answer.' },
  { key: 'run_timeout_s', label: 'Max duration (s)', min: 30, max: 7200,
    hint: 'Past this, the agent answers with what it gathered instead of continuing.' },
  { key: 'tool_timeout_s', label: 'Per-tool timeout (s)', min: 5, max: 900, hint: '' },
  { key: 'parallel_max_fanout', label: 'Parallel calls', min: 1, max: 12,
    hint: 'Independent tools fired at once within one turn.' },
  { key: 'stagnation_limit', label: 'Stagnation limit', min: 1, max: 6,
    hint: 'Identical failures before the guard refuses another attempt.' },
  { key: 'python_timeout_s', label: 'Python timeout (s)', min: 5, max: 600, hint: '' },
  { key: 'num_ctx', label: 'Context window (tokens)', min: 0, max: 262144,
    hint: '0 = taken from the model, capped at 16k. Wider keeps more evidence in mind; too wide pushes the model off the GPU and cuts speed several-fold.' },
];

function GuardrailsPanel({ onChanged }: { onChanged: () => void }) {
  const toast = useToast();
  const [prefs, setPrefs] = useState<Record<string, unknown> | null>(null);
  const [overridden, setOverridden] = useState<string[]>([]);

  const load = async () => {
    const data = await api.getPrefs();
    setPrefs(data.effective);
    setOverridden(data.overridden);
  };
  useEffect(() => { void load(); }, []);

  const update = async (values: Record<string, unknown>) => {
    setPrefs((p) => ({ ...(p ?? {}), ...values }));
    await api.setPrefs(values);
    await load();
    onChanged();
  };

  if (!prefs) return <PanelSkeleton />;

  return (
    <div className="space-y-7">
      <section>
        <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider dim">Approval</h3>
        <p className="mb-3 text-2xs leading-relaxed dimmer">
          Built-in tools only ever write inside the workspace, so they are gated in “always” mode
          alone. MCP servers can have real-world effects.
        </p>
        <div className="space-y-1.5">
          {APPROVAL_MODES.map((mode) => (
            <button key={mode.value} onClick={() => void update({ approval_mode: mode.value })}
              className={cls('focus-ring flex w-full items-start gap-2.5 rounded-xl border px-3 py-2.5 text-left transition-colors',
                prefs.approval_mode === mode.value
                  ? 'border-brand-500 bg-brand-50 dark:border-brand-500/40 dark:bg-brand-500/[0.08]'
                  : 'hairline hover:bg-zinc-50 dark:hover:bg-white/[0.04]')}>
              <span className={cls('mt-0.5 grid h-3.5 w-3.5 shrink-0 place-items-center rounded-full border',
                prefs.approval_mode === mode.value ? 'border-brand-500 bg-brand-500' : 'hairline')}>
                {prefs.approval_mode === mode.value && <Check size={9} className="text-zinc-950" strokeWidth={3.5} />}
              </span>
              <span>
                <span className="block text-xs font-medium">{mode.label}</span>
                <span className="mt-0.5 block text-2xs leading-relaxed dim">{mode.hint}</span>
              </span>
            </button>
          ))}
        </div>
      </section>

      <section className="space-y-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider dim">Capabilities</h3>
        <Switch checked={!!prefs.enable_python_tool} label="Python execution"
          hint="Separate process, working directory bounded to the workspace, killed past the timeout. This is not a security sandbox: the code runs with this app's own rights."
          onChange={(v) => void update({ enable_python_tool: v })} />
      </section>

      <section>
        <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider dim">Budgets</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          {NUMERIC.map((setting) => (
            <Field key={setting.key} label={setting.label} hint={setting.hint}>
              <div className="flex items-center gap-2">
                <Input type="number" min={setting.min} max={setting.max}
                  value={String(prefs[setting.key] ?? '')}
                  onChange={(e) => setPrefs((p) => ({ ...(p ?? {}), [setting.key]: Number(e.target.value) }))}
                  onBlur={(e) => void update({ [setting.key]: Number(e.target.value) })}
                  className="font-mono !text-xs" />
                {overridden.includes(setting.key) && (
                  <button title="Fall back to the environment value"
                    onClick={() => void update({ [setting.key]: null }).then(() => toast('Reset to environment'))}
                    className="focus-ring shrink-0 rounded p-1 dimmer hover:text-zinc-700">
                    <Trash2 size={12} />
                  </button>
                )}
              </div>
            </Field>
          ))}
        </div>
        <p className="mt-3 text-2xs leading-relaxed dimmer">
          A value set here overrides the one in <code className="font-mono">.env</code>.
          The bin icon hands it back to the environment.
        </p>
      </section>
    </div>
  );
}

/* ----------------------------------------------------------------- memory */

/**
 * Who the agent is, and what it has learned to do.
 *
 * Two things live here because they are the same thing at two time scales. Identity is
 * what you tell it once and never again; skills are what it works out for itself after
 * doing the same job enough times to be sure of the shape.
 */
function IdentityPanel() {
  const toast = useToast();
  const confirm = useConfirm();
  const [soul, setSoul] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [stats, setStats] = useState<SkillStats | null>(null);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const [trigger, setTrigger] = useState('');
  const [body, setBody] = useState('');

  const loadSkills = async () => {
    const data = await api.skills();
    setSkills(data.skills);
    setStats(data.stats);
  };
  useEffect(() => {
    void (async () => {
      const [identity] = await Promise.all([api.soul(), loadSkills()]);
      setSoul(identity.text);
      setDraft(identity.text);
    })();
  }, []);

  if (soul === null) return <PanelSkeleton />;
  const dirty = draft !== soul;

  return (
    <div className="space-y-6">
      <section>
        <h3 className="mb-1 text-xs font-semibold">Standing instructions</h3>
        <p className="mb-2.5 text-xs leading-relaxed dim">
          The first thing in every system prompt, before the tools and before the memory.
          Yours alone — nothing the agent reads can change it, which is what makes it the
          one place a preference can be stated once instead of retyped every conversation.
        </p>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={7}
          maxLength={4000}
          placeholder={'Answer in French unless I write in English.\nPrefer SQL over pandas for anything a database can do.\nWhen you are unsure, say so in one line instead of hedging for a paragraph.'}
          className="focus-ring w-full resize-y rounded-xl border bg-white px-3 py-2.5 font-mono text-[11px] leading-relaxed hairline outline-none dark:bg-black/20" />
        <div className="mt-2 flex items-center gap-2">
          <Button size="sm" busy={saving} disabled={!dirty}
            onClick={async () => {
              setSaving(true);
              try {
                const saved = await api.setSoul(draft);
                setSoul(saved.text); setDraft(saved.text); toast('Identity saved');
              } catch (e) { toast(String((e as Error).message), 'error'); }
              finally { setSaving(false); }
            }}>Save</Button>
          {dirty && (
            <button onClick={() => setDraft(soul)}
              className="focus-ring rounded px-1.5 py-1 text-2xs dim hover:text-zinc-700 dark:hover:text-zinc-200">
              Revert
            </button>
          )}
          <span className="ml-auto text-2xs dimmer">{draft.length}/4000</span>
        </div>
      </section>

      <section>
        <div className="mb-1 flex items-center gap-2">
          <h3 className="text-xs font-semibold">Skills</h3>
          {stats && stats.learned > 0 && (
            <Badge tone="brand">{stats.learned} learned on its own</Badge>
          )}
          <Button size="xs" variant="ghost" className="ml-auto"
            onClick={() => setAdding((v) => !v)}>{adding ? 'Cancel' : 'Add'}</Button>
        </div>
        <p className="mb-2.5 text-xs leading-relaxed dim">
          Procedures, not facts — <em>how</em> to do a job this agent does often. It writes
          these itself after finishing the same shape of work three times; the recipe is
          then recalled the next time a question looks like that one.
        </p>

        {adding && (
          <div className="mb-3 space-y-2 rounded-xl border p-3 hairline animate-fade-in">
            <Field label="Name">
              <Input value={name} onChange={(e) => setName(e.target.value)}
                placeholder="Weekly sales report" />
            </Field>
            <Field label="Use it when" hint="A sentence describing the questions this applies to.">
              <Input value={trigger} onChange={(e) => setTrigger(e.target.value)}
                placeholder="Asked for revenue or order totals over a period" />
            </Field>
            <Field label="Procedure">
              <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={5}
                placeholder={'1. Read orders from shop.db with sqlite__read_query.\n2. Group by week, sum total_eur, exclude cancelled.\n3. Chart it with pandas and save the PNG to the workspace.'}
                className="focus-ring w-full resize-y rounded-xl border bg-white px-3 py-2.5 font-mono text-[11px] leading-relaxed hairline outline-none dark:bg-black/20" />
            </Field>
            <Button size="sm" disabled={!name.trim() || !body.trim()}
              onClick={async () => {
                try {
                  await api.addSkill({ name: name.trim(), trigger: trigger.trim(), body: body.trim() });
                  setName(''); setTrigger(''); setBody(''); setAdding(false);
                  await loadSkills(); toast('Skill added');
                } catch (e) { toast(String((e as Error).message), 'error'); }
              }}>Save skill</Button>
          </div>
        )}

        {skills.length === 0 ? (
          <Empty icon={GraduationCap} title="No skills yet"
            hint="Give the agent the same kind of job a few times and it will write the procedure down by itself." />
        ) : (
          <div className="space-y-1.5">
            {skills.map((skill) => (
              <details key={skill.id}
                className="group rounded-xl border px-3 py-2 hairline [&[open]]:bg-zinc-50/60 dark:[&[open]]:bg-white/[0.03]">
                <summary className="flex cursor-pointer list-none items-center gap-2">
                  <GraduationCap size={12} className="shrink-0 dimmer" />
                  <span className="min-w-0 flex-1 truncate text-xs font-medium">{skill.name}</span>
                  {skill.source === 'learned' && <Badge tone="brand">learned</Badge>}
                  {skill.uses > 0 && <span className="text-2xs dimmer">used {skill.uses}×</span>}
                  <IconButton icon={Trash2} label="Forget" size={12} className="h-6 w-6"
                    onClick={async (e) => {
                      e.preventDefault();
                      if (await confirm({ title: `Forget “${skill.name}”?`, danger: true,
                                          confirmLabel: 'Forget' })) {
                        await api.forgetSkill(skill.id); await loadSkills();
                      }
                    }} />
                </summary>
                {skill.trigger && (
                  <p className="mt-1.5 text-2xs leading-relaxed dimmer">When: {skill.trigger}</p>
                )}
                <pre className="mt-1.5 whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed dim">
                  {skill.body}
                </pre>
              </details>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function MemoryPanel() {
  const confirm = useConfirm();
  const toast = useToast();
  const [entries, setEntries] = useState<MemoryEntry[]>([]);
  const [draft, setDraft] = useState('');
  const load = async () => setEntries(await api.listMemory());
  useEffect(() => { void load(); }, []);

  const quarantined = entries.filter((e) => e.status === 'quarantined');
  const trusted = entries.filter((e) => e.status !== 'quarantined');

  return (
    <div className="space-y-5">
      <p className="text-xs leading-relaxed dim">
        What the agent has kept about you, from one conversation to the next. Searched
        full-text, so it stays useful as it grows.
      </p>

      {quarantined.length > 0 && (
        <section className="rounded-xl border border-amber-300/70 bg-amber-50/60 p-3.5 dark:border-amber-500/25 dark:bg-amber-500/[0.07]">
          <h3 className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-amber-900 dark:text-amber-200">
            <ShieldAlert size={13} /> Waiting for you ({quarantined.length})
          </h3>
          {/* Memory is the one part of an agent that outlives the conversation, which makes
              it the part worth attacking: persuade it to remember something once and the
              instruction returns, trusted, in every later run. So a fact learned while
              untrusted content was in context waits here. */}
          <p className="mb-3 text-2xs leading-relaxed text-amber-800/80 dark:text-amber-200/70">
            The agent learned these while it had content from outside in context — a web page,
            a file, a server reply. They are not recalled and never reach a prompt until you
            say they are true.
          </p>
          <div className="space-y-1.5">
            {quarantined.map((entry) => (
              <div key={entry.id} className="flex items-start gap-2 rounded-lg bg-white/70 px-3 py-2 dark:bg-black/25">
                <span className="min-w-0 flex-1 text-xs leading-relaxed">{entry.text}</span>
                <Button size="xs" icon={Check} onClick={async () => {
                  await api.confirmMemory(entry.id); await load(); toast('Kept');
                }}>Keep</Button>
                <Button size="xs" variant="ghost" icon={Trash2} onClick={async () => {
                  await api.forgetMemory(entry.id); await load();
                }}>Discard</Button>
              </div>
            ))}
          </div>
        </section>
      )}

      <div className="flex gap-2">
        <Input value={draft} onChange={(e) => setDraft(e.target.value)}
          placeholder="One fact worth keeping, in a sentence…"
          onKeyDown={async (e) => {
            if (e.key === 'Enter' && draft.trim()) { await api.addMemory(draft.trim()); setDraft(''); await load(); }
          }} />
        <Button variant="outline" disabled={!draft.trim()}
          onClick={async () => { await api.addMemory(draft.trim()); setDraft(''); await load(); }}>
          Add
        </Button>
      </div>

      {trusted.length === 0 ? (
        <Empty icon={Brain} title="Nothing remembered yet" />
      ) : (
        <div className="space-y-1.5">
          {trusted.map((entry) => (
            <div key={entry.id} className="group flex items-start gap-2.5 rounded-xl border px-3 py-2.5 hairline">
              <span className="min-w-0 flex-1 text-xs leading-relaxed">{entry.text}</span>
              {entry.kind === 'identity' && <Badge tone="brand">identity</Badge>}
              <Badge>{entry.source === 'user' ? 'you' : 'agent'}</Badge>
              <button onClick={async () => {
                if (await confirm({ title: 'Forget this?', danger: true, confirmLabel: 'Forget' })) {
                  await api.forgetMemory(entry.id); await load();
                }
              }} className="focus-ring rounded p-1 dimmer opacity-0 transition-opacity hover:text-red-600 group-hover:opacity-100">
                <Trash2 size={12} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function AuditPanel() {
  const [report, setReport] = useState<AuditReport | null>(null);
  useEffect(() => { void api.audit(300).then(setReport); }, []);
  if (!report) return <PanelSkeleton />;
  const { verified } = report;
  return (
    <div className="space-y-4">
      <p className="text-xs leading-relaxed dim">
        Every action the agent took, in order. Each line carries the hash of the one before
        it — the log cannot stop someone editing it, but it cannot hide that they did.
      </p>
      <div className={cls('flex items-start gap-2.5 rounded-xl border px-3.5 py-3 text-xs',
        verified.ok ? 'border-emerald-300/60 bg-emerald-50/50 dark:border-emerald-500/25 dark:bg-emerald-500/[0.07]'
                    : 'border-red-300/70 bg-red-50/60 dark:border-red-500/25 dark:bg-red-500/[0.07]')}>
        <FileLock2 size={14} className={cls('mt-px shrink-0',
          verified.ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600')} />
        <span className={verified.ok ? 'text-emerald-900 dark:text-emerald-200' : 'text-red-800 dark:text-red-300'}>
          {verified.ok
            ? `Chain intact across ${verified.entries} entries.`
            : `Chain broken at entry ${verified.broken_at}: ${verified.reason}. Everything after that point was written after the log was altered.`}
        </span>
      </div>
      <div className="overflow-hidden rounded-xl border hairline">
        <table className="w-full text-left text-2xs">
          <thead className="bg-zinc-50 dark:bg-white/[0.04]">
            <tr>
              <th className="px-3 py-2 font-semibold">When</th>
              <th className="px-3 py-2 font-semibold">Event</th>
              <th className="px-3 py-2 font-semibold">Detail</th>
            </tr>
          </thead>
          <tbody>
            {[...report.entries].reverse().slice(0, 120).map((entry) => (
              <tr key={entry.hash} className="border-t hairline align-top">
                <td className="whitespace-nowrap px-3 py-1.5 font-mono dimmer">
                  {new Date(entry.ts * 1000).toLocaleTimeString()}
                </td>
                <td className="whitespace-nowrap px-3 py-1.5">
                  <span className="font-mono">{entry.event}</span>
                  {entry.tainted && <Badge tone="warn" className="ml-1.5">tainted</Badge>}
                  {entry.injection && <Badge tone="bad" className="ml-1.5">injection</Badge>}
                </td>
                <td className="px-3 py-1.5 font-mono dim">
                  {[entry.tool, entry.status, entry.verdict, entry.args, entry.reason]
                    .filter(Boolean).join(' · ').slice(0, 140)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-2xs dimmer">Written to <code className="font-mono">{report.path}</code>.</p>
    </div>
  );
}

/* ------------------------------------------------------------ diagnostics */

function DiagnosticsPanel() {
  const [data, setData] = useState<Diagnostics | null>(null);
  useEffect(() => { void api.diagnostics().then(setData); }, []);
  if (!data) return <PanelSkeleton />;
  return (
    <div className="space-y-5">
      {data.warnings.length > 0 && (
        <section className="space-y-1.5">
          {data.warnings.map((warning, i) => (
            <p key={i} className="rounded-xl border border-amber-300/70 bg-amber-50/70 px-3.5 py-2.5 text-xs leading-relaxed text-amber-900 dark:border-amber-500/25 dark:bg-amber-500/[0.07] dark:text-amber-200">
              {warning}
            </p>
          ))}
        </section>
      )}
      <RunPanel metrics={data.runs} />
      {data.network && (
        <Row label="Network"
          tone={data.network.airgapped && !data.network.cloud_model_allowed ? 'good' : 'warn'}
          value={data.network.airgapped
            ? `Air-gapped${data.network.cloud_model_allowed ? ' — except the model (AGENT_ALLOW_CLOUD_MODEL, testing only)' : ''}`
            : 'Open — set AGENT_AIRGAPPED=true to close every path out'}
          extra={[
            data.network.kernel_sandbox ? 'Local MCP servers run in a loopback-only kernel sandbox' : 'No kernel sandbox on this OS: MCP servers rely on the firewall',
            data.network.internal_domains.length ? `internal domains: ${data.network.internal_domains.join(', ')}` : 'internal = loopback and private addresses',
          ].join(' · ')} />
      )}
      <Row label="Active model" value={data.model.model || '—'}
        tone={data.model.ok ? 'good' : 'bad'} extra={data.model.capabilities.source} />
      <Row label="MCP servers"
        value={`${data.mcp.servers_connected}/${data.mcp.servers_total} connected · ${data.mcp.tools_available} tools`}
        tone={data.mcp.servers_error ? 'warn' : 'good'} />
      {Object.values(data.runtimes).map((runtime) => (
        <Row key={runtime.name} label={runtime.label} tone={runtime.available ? 'good' : 'warn'}
          value={runtime.available ? runtime.path : 'not on PATH'}
          extra={runtime.available ? runtime.why : `${runtime.why} — ${runtime.install}`} />
      ))}
      <Row label="Model execution"
        tone={!data.runtime?.loaded ? 'warn' : (data.runtime.gpu_percent ?? 0) >= 95 ? 'good' : 'warn'}
        value={data.runtime?.loaded
          ? `${data.runtime.gpu_percent}% on GPU — ${data.runtime.vram_gb} GB of ${data.runtime.size_gb} GB`
          : 'not loaded right now'}
        extra={`Context window requested: ${data.context_window} tokens`} />
      <Row label="Python modules available" value={data.python_modules.join(', ') || 'standard library only'} tone="good" />
      <Row label="Workspace" value={data.workspace} tone="good" />
      <Row label="Store" value={data.store} tone="good" />
    </div>
  );
}

/**
 * What the last sixty answers actually cost.
 *
 * Averages, not a live feed: the useful question is never "how is this turn going" — you
 * are watching that already — but "is it getting slower, and where did the slowness go".
 */
function RunPanel({ metrics }: { metrics: RunMetrics }) {
  if (!metrics?.runs) return null;
  const seconds = (ms?: number) => (ms ? `${(ms / 1000).toFixed(1)}s` : '—');
  // One significant step per magnitude: a nine-digit token count read as digits tells you
  // nothing a rounded "2.4M" does not, and takes four times the width to say it.
  const compact = (n?: number) => {
    if (!n) return '0';
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 10_000) return `${Math.round(n / 1000)}k`;
    if (n >= 1_000) return `${(n / 1000).toFixed(1)}k`;
    return String(n);
  };
  return (
    <section className="rounded-xl border p-3.5 hairline">
      <h3 className="mb-0.5 text-xs font-semibold">Last {metrics.runs} answers</h3>
      <p className="mb-3 text-2xs leading-relaxed dimmer">
        Read back from the saved conversations, so these numbers cannot drift from what
        actually happened.
      </p>
      <div className="grid grid-cols-2 gap-x-4 gap-y-2.5 sm:grid-cols-4">
        <Stat label="First word" value={seconds(metrics.ttft_median_ms)}
          hint={metrics.ttft_p90_ms ? `p90 ${seconds(metrics.ttft_p90_ms)}` : 'median'} />
        <Stat label="Prompt reused" value={`${metrics.cache_hit ?? 0}%`}
          hint="served from cache" tone={(metrics.cache_hit ?? 0) > 40 ? 'good' : undefined} />
        <Stat label="Model calls" value={String(metrics.calls_per_run ?? 0)} hint="per answer" />
        <Stat label="Tools run" value={String(metrics.tool_calls ?? 0)} hint="in total" />
        <Stat label="Tokens in" value={compact(metrics.tokens_in)} hint="read by the model" />
        <Stat label="Tokens out" value={compact(metrics.tokens_out)} hint="written" />
        <Stat label="Peak context" value={compact(metrics.peak_context)}
          hint={metrics.masked_chars ? `${compact(metrics.masked_chars)} chars masked` : 'high-water mark'} />
        <Stat label="Did not finish" value={String(metrics.failed ?? 0)}
          hint="failed or stopped" tone={metrics.failed ? 'warn' : undefined} />
      </div>
    </section>
  );
}

function Stat({ label, value, hint, tone }: {
  label: string; value: string; hint?: string; tone?: 'good' | 'warn';
}) {
  return (
    <div>
      <p className={cls('font-mono text-base leading-none tracking-tight',
        tone === 'good' ? 'text-brand-700 dark:text-brand-400'
        : tone === 'warn' ? 'text-amber-600 dark:text-amber-400' : '')}>
        {value}
      </p>
      <p className="mt-1 text-2xs font-medium">{label}</p>
      {hint && <p className="text-2xs dimmer">{hint}</p>}
    </div>
  );
}

function Row({ label, value, tone, extra }: {
  label: string; value: string; tone: 'good' | 'warn' | 'bad'; extra?: string;
}) {
  return (
    <div className="flex items-start gap-3 rounded-xl border px-3.5 py-2.5 hairline">
      <Dot tone={tone} />
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium">{label}</p>
        <p className="mt-0.5 break-all font-mono text-2xs dim">{value}</p>
        {extra && <p className="mt-0.5 text-2xs leading-relaxed dimmer">{extra}</p>}
      </div>
    </div>
  );
}

function PanelSkeleton() {
  return (
    <div className="space-y-2">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="skeleton-line h-12 rounded-xl border hairline" />
      ))}
    </div>
  );
}

export function AdminDoor({ onOpen, warn }: { onOpen: () => void; warn: boolean }) {
  return (
    <IconButton icon={Settings2} label="Admin (⌘,)" onClick={onOpen}
      className={cls(warn && 'text-amber-500 hover:text-amber-600')} />
  );
}
