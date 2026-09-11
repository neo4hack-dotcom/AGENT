// The admin area. Discreet by design — one keyboard shortcut and one small control in the
// corner — because on a personal agent this is a place you visit twice and then forget.
//
// Everything here is the *effective* configuration: what Admin sets overrides the
// environment, and every panel says which one a value is currently coming from. A
// settings screen that cannot tell you why a value is what it is, is a settings screen
// people stop trusting.

import {
  Activity, Brain, Check, Cpu, Eye, KeyRound, Plug, Settings2, ShieldCheck, Trash2,
  Wrench, X, Zap,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { api, setAdminToken } from '../api';
import type { AdminState, Diagnostics, MemoryEntry, ModelOption, ToolInfo } from '../types';
import { McpLibrary } from './McpLibrary';
import {
  Badge, Button, Dot, Empty, Field, IconButton, Input, Switch, cls, useConfirm, useToast,
} from './ui';

type Tab = 'model' | 'mcp' | 'tools' | 'guardrails' | 'memory' | 'diagnostics';

const TABS: { id: Tab; label: string; icon: typeof Cpu }[] = [
  { id: 'model', label: 'Modèle', icon: Cpu },
  { id: 'mcp', label: 'Serveurs MCP', icon: Plug },
  { id: 'tools', label: 'Outils', icon: Wrench },
  { id: 'guardrails', label: 'Garde-fous', icon: ShieldCheck },
  { id: 'memory', label: 'Mémoire', icon: Brain },
  { id: 'diagnostics', label: 'Diagnostic', icon: Activity },
];

export function Admin({
  state, onClose, onChanged,
}: { state: AdminState; onClose: () => void; onChanged: () => void }) {
  const [tab, setTab] = useState<Tab>('model');
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
        <h1 className="text-sm font-semibold">Administration</h1>
        <Badge tone={state.mode === 'password' ? 'good' : 'neutral'}>
          {state.mode === 'password' ? 'protégé par mot de passe' : 'accès local uniquement'}
        </Badge>
        <IconButton icon={X} label="Fermer" className="ml-auto" onClick={onClose} />
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
                    ? 'bg-brand-50 text-brand-700 dark:bg-brand-500/12 dark:text-brand-300'
                    : 'dim hover:bg-zinc-100 dark:hover:bg-white/[0.06]')}>
                <Icon size={14} strokeWidth={1.9} /> {label}
              </button>
            ))}
          </nav>
          <main className="min-w-0 flex-1 overflow-y-auto">
            <div className="mx-auto max-w-3xl p-6">
              {tab === 'model' && <ModelPanel onChanged={onChanged} />}
              {tab === 'mcp' && <McpLibrary onChanged={onChanged} />}
              {tab === 'tools' && <ToolsPanel />}
              {tab === 'guardrails' && <GuardrailsPanel onChanged={onChanged} />}
              {tab === 'memory' && <MemoryPanel />}
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
          <p className="text-sm font-medium">Administration réservée à cette machine</p>
          <p className="mt-2 text-xs leading-relaxed dim">
            Aucun mot de passe n’est défini, donc l’administration n’est accessible que depuis
            l’ordinateur qui exécute Lumen. Pour y accéder à distance, définissez
            <code className="mx-1 rounded bg-zinc-100 px-1 py-0.5 font-mono dark:bg-white/[0.07]">LUMEN_ADMIN_PASSWORD</code>
            puis redémarrez.
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
          <p className="text-sm font-medium">Mot de passe administrateur</p>
        </div>
        <Input type="password" autoFocus value={password} placeholder="••••••••"
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') void submit(); }} />
        {error && <p className="text-center text-xs text-red-600 dark:text-red-400">{error}</p>}
        <Button className="w-full" size="md" busy={busy} onClick={() => void submit()}>Entrer</Button>
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
      toast(which === 'model' ? `Modèle actif : ${name}` : `Modèle rapide : ${name}`);
    } finally { setSaving(''); }
  };

  if (!data) return <PanelSkeleton />;

  return (
    <div className="space-y-6">
      <section>
        <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider dim">Serveur Ollama</h3>
        <p className="mb-3 text-2xs leading-relaxed dimmer">
          Lumen ne parle qu’à un modèle local. Rien de ce que vous écrivez ne quitte cette machine.
        </p>
        <div className="flex gap-2">
          <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="http://localhost:11434" className="font-mono !text-xs" />
          <Button variant="outline" onClick={async () => {
            await api.setPrefs({ ollama_base_url: baseUrl });
            await load(); onChanged(); toast('Adresse enregistrée');
          }}>Enregistrer</Button>
        </div>
        {data.error && (
          <p className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs leading-relaxed text-red-700 dark:bg-red-500/10 dark:text-red-300">
            {data.error}
          </p>
        )}
      </section>

      <section>
        <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider dim">Modèle de raisonnement</h3>
        <p className="mb-3 text-2xs leading-relaxed dimmer">
          Les capacités viennent du serveur, pas du nom du modèle. Sans <b>outils</b>, l’agent ne peut
          rien appeler — il répond seulement de mémoire.
        </p>
        <div className="space-y-1.5">
          {data.models.map((model) => {
            const active = model.name === data.selected;
            const usable = model.capabilities.tools;
            return (
              <div key={model.name}
                className={cls('flex items-center gap-3 rounded-xl border px-3 py-2.5 transition-colors',
                  active ? 'border-brand-400 bg-brand-50/60 dark:border-brand-500/40 dark:bg-brand-500/[0.08]' : 'hairline')}>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-mono text-xs font-medium">{model.name}</span>
                    {!model.local && <Badge tone="warn">distant</Badge>}
                    {model.parameters && <span className="text-2xs dimmer">{model.parameters}</span>}
                    {model.size_gb > 0 && <span className="text-2xs dimmer">· {model.size_gb} Go</span>}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    <CapabilityBadge on={model.capabilities.tools} icon={Wrench} label="outils" />
                    <CapabilityBadge on={model.capabilities.thinking} icon={Brain} label="raisonnement" />
                    <CapabilityBadge on={model.capabilities.vision} icon={Eye} label="vision" />
                    {model.capabilities.context_length > 0 && (
                      <Badge>{Math.round(model.capabilities.context_length / 1024)}k ctx</Badge>
                    )}
                  </div>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1">
                  <Button size="xs" variant={active ? 'subtle' : 'outline'} disabled={active}
                    busy={saving === model.name + 'model'}
                    onClick={() => void choose(model.name, 'model')}>
                    {active ? <><Check size={11} /> actif</> : usable ? 'Utiliser' : 'Utiliser quand même'}
                  </Button>
                  <button
                    onClick={() => void choose(model.name, 'fast_model')}
                    className={cls('focus-ring rounded px-1 text-2xs transition-colors',
                      model.name === data.fast_selected ? 'text-brand-600 dark:text-brand-300' : 'dimmer hover:text-zinc-600')}>
                    {model.name === data.fast_selected ? '★ modèle rapide' : 'définir comme rapide'}
                  </button>
                </div>
              </div>
            );
          })}
          {data.models.length === 0 && (
            <Empty icon={Cpu} title="Aucun modèle disponible"
              hint={<>Téléchargez-en un&nbsp;: <code className="font-mono">ollama pull qwen3.5:4b</code></>} />
          )}
        </div>
        <p className="mt-3 text-2xs leading-relaxed dimmer">
          Le <b>modèle rapide</b> sert au critique, à la vérification finale et aux titres — pas à la
          réponse. Un modèle plus petit y fait gagner du temps sans rien coûter en qualité.
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
        Tout ce que l’agent peut appeler en ce moment — {tools.length} outil(s). Les outils marqués
        <Zap size={10} className="mx-1 inline text-amber-500" /> peuvent modifier quelque chose hors de
        Lumen et passent par une approbation selon le réglage des garde-fous.
      </p>
      {Object.entries(groups).map(([group, list]) => (
        <section key={group}>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider dim">{group}</h3>
          <div className="space-y-1">
            {list.map((tool) => (
              <div key={tool.name} className="flex items-start gap-2.5 rounded-lg border px-3 py-2 hairline">
                <span className="mt-0.5 shrink-0 font-mono text-2xs font-medium text-brand-700 dark:text-brand-300">
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
  { value: 'never', label: 'Jamais', hint: 'L’agent agit seul, y compris pour les écritures MCP.' },
  { value: 'writes', label: 'Pour les écritures', hint: 'Recommandé : un outil MCP qui modifie quelque chose demande votre accord.' },
  { value: 'always', label: 'Toujours', hint: 'Chaque outil est soumis à approbation, y compris l’exécution Python locale.' },
];

const NUMERIC: { key: string; label: string; hint: string; min: number; max: number }[] = [
  { key: 'max_iterations', label: 'Tours d’outils maximum', min: 1, max: 60,
    hint: 'Plafond du nombre d’allers-retours modèle → outils pour une seule réponse.' },
  { key: 'run_timeout_s', label: 'Durée maximale (s)', min: 30, max: 7200,
    hint: 'Au-delà, l’agent répond avec ce qu’il a réuni plutôt que de continuer.' },
  { key: 'tool_timeout_s', label: 'Délai par outil (s)', min: 5, max: 900, hint: '' },
  { key: 'parallel_max_fanout', label: 'Appels parallèles', min: 1, max: 12,
    hint: 'Outils indépendants lancés simultanément dans un même tour.' },
  { key: 'stagnation_limit', label: 'Seuil de stagnation', min: 1, max: 6,
    hint: 'Échecs identiques avant que la garde n’interdise de réessayer.' },
  { key: 'python_timeout_s', label: 'Délai d’exécution Python (s)', min: 5, max: 600, hint: '' },
  { key: 'num_ctx', label: 'Fenêtre de contexte (tokens)', min: 0, max: 262144,
    hint: '0 = déduite du modèle, plafonnée à 16k. Plus large garde plus de preuves en mémoire ; trop large chasse le modèle du GPU et divise la vitesse.' },
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
        <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider dim">Approbation</h3>
        <p className="mb-3 text-2xs leading-relaxed dimmer">
          Les outils natifs n’écrivent que dans l’espace de travail de Lumen ; ils ne sont soumis à
          approbation qu’en mode « toujours ». Les serveurs MCP peuvent avoir des effets réels.
        </p>
        <div className="space-y-1.5">
          {APPROVAL_MODES.map((mode) => (
            <button key={mode.value} onClick={() => void update({ approval_mode: mode.value })}
              className={cls('focus-ring flex w-full items-start gap-2.5 rounded-xl border px-3 py-2.5 text-left transition-colors',
                prefs.approval_mode === mode.value
                  ? 'border-brand-400 bg-brand-50/60 dark:border-brand-500/40 dark:bg-brand-500/[0.08]'
                  : 'hairline hover:bg-zinc-50 dark:hover:bg-white/[0.04]')}>
              <span className={cls('mt-0.5 grid h-3.5 w-3.5 shrink-0 place-items-center rounded-full border',
                prefs.approval_mode === mode.value ? 'border-brand-500 bg-brand-500' : 'hairline')}>
                {prefs.approval_mode === mode.value && <Check size={9} className="text-white" strokeWidth={3.5} />}
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
        <h3 className="text-xs font-semibold uppercase tracking-wider dim">Capacités</h3>
        <Switch checked={!!prefs.enable_web_tools} label="Accès web"
          hint="web_search et web_fetch. Les seuls outils natifs qui sortent de cette machine."
          onChange={(v) => void update({ enable_web_tools: v })} />
        <Switch checked={!!prefs.enable_python_tool} label="Exécution de code Python"
          hint="Processus séparé, répertoire de travail limité, tué au-delà du délai. Ce n’est pas un bac à sable de sécurité : le code s’exécute avec les droits de Lumen."
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
                  <button title="Revenir à la valeur d’environnement"
                    onClick={() => void update({ [setting.key]: null }).then(() => toast('Valeur réinitialisée'))}
                    className="focus-ring shrink-0 rounded p-1 dimmer hover:text-zinc-700">
                    <Trash2 size={12} />
                  </button>
                )}
              </div>
            </Field>
          ))}
        </div>
        <p className="mt-3 text-2xs leading-relaxed dimmer">
          Une valeur modifiée ici remplace celle du fichier <code className="font-mono">.env</code>.
          L’icône corbeille rend la main à l’environnement.
        </p>
      </section>
    </div>
  );
}

/* ----------------------------------------------------------------- memory */

function MemoryPanel() {
  const confirm = useConfirm();
  const [entries, setEntries] = useState<MemoryEntry[]>([]);
  const [draft, setDraft] = useState('');
  const load = async () => setEntries(await api.listMemory());
  useEffect(() => { void load(); }, []);

  return (
    <div className="space-y-4">
      <p className="text-xs leading-relaxed dim">
        Ce que Lumen a retenu de vous, d’une conversation à l’autre. L’agent écrit ici lui-même
        quand il juge un fait durable ; vous pouvez ajouter et retirer librement.
      </p>
      <div className="flex gap-2">
        <Input value={draft} onChange={(e) => setDraft(e.target.value)}
          placeholder="Un fait à retenir, en une phrase…"
          onKeyDown={async (e) => {
            if (e.key === 'Enter' && draft.trim()) { await api.addMemory(draft.trim()); setDraft(''); await load(); }
          }} />
        <Button variant="outline" disabled={!draft.trim()}
          onClick={async () => { await api.addMemory(draft.trim()); setDraft(''); await load(); }}>
          Ajouter
        </Button>
      </div>
      {entries.length === 0 ? (
        <Empty icon={Brain} title="Rien en mémoire pour l’instant" />
      ) : (
        <div className="space-y-1.5">
          {entries.map((entry) => (
            <div key={entry.id} className="group flex items-start gap-2.5 rounded-xl border px-3 py-2.5 hairline">
              <span className="min-w-0 flex-1 text-xs leading-relaxed">{entry.text}</span>
              <Badge>{entry.source === 'user' ? 'vous' : 'agent'}</Badge>
              <button onClick={async () => {
                if (await confirm({ title: 'Oublier ce fait ?', danger: true, confirmLabel: 'Oublier' })) {
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
      <Row label="Modèle actif" value={data.model.model || '—'}
        tone={data.model.ok ? 'good' : 'bad'} extra={data.model.capabilities.source} />
      <Row label="Serveurs MCP"
        value={`${data.mcp.servers_connected}/${data.mcp.servers_total} connectés · ${data.mcp.tools_available} outils`}
        tone={data.mcp.servers_error ? 'warn' : 'good'} />
      {Object.values(data.runtimes).map((runtime) => (
        <Row key={runtime.name} label={runtime.label} tone={runtime.available ? 'good' : 'warn'}
          value={runtime.available ? runtime.path : 'absent du PATH'}
          extra={runtime.available ? runtime.why : `${runtime.why} — ${runtime.install}`} />
      ))}
      <Row label="Exécution du modèle"
        tone={!data.runtime?.loaded ? 'warn' : (data.runtime.gpu_percent ?? 0) >= 95 ? 'good' : 'warn'}
        value={data.runtime?.loaded
          ? `${data.runtime.gpu_percent}% sur GPU — ${data.runtime.vram_gb} Go sur ${data.runtime.size_gb} Go`
          : 'pas chargé en ce moment'}
        extra={`Fenêtre de contexte demandée : ${data.context_window} tokens`} />
      <Row label="Modules Python disponibles" value={data.python_modules.join(', ') || 'bibliothèque standard seulement'} tone="good" />
      <Row label="Espace de travail" value={data.workspace} tone="good" />
      <Row label="Stockage" value={data.store} tone="good" />
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
    <IconButton icon={Settings2} label="Administration (⌘,)" onClick={onOpen}
      className={cls(warn && 'text-amber-500 hover:text-amber-600')} />
  );
}
