// Data sources: what each connected server holds, written down once.
//
// Two things per source, both optional. A description in plain words — what is in it, what
// it is good for, what it cannot answer — which is how the agent picks the right source.
// And, for a SQL source, a model: tables, the values columns take, joins, metrics defined
// once, caveats, and questions whose SQL has been checked. "Profile" measures the structural
// half from the data itself; "Draft" proposes the rest and runs every query it proposes. For
// a service of tools, "Draft" describes it from its schemas, from what the agent has seen it
// return, and from the calls that are safe to make. Nothing drafted is saved until someone
// reads it and presses Save.
//
// Below, what the agent has observed: every tool, how often it worked, the fields it
// returns, and the ones never explored. That is a cache the agent keeps, shown so it can be
// checked — and forgotten, if the source changed in a way its schemas do not show.

import { ArrowLeft, BookOpen, Check, Circle, Database, Eraser, Eye, FlaskConical, Import, Lightbulb, Ruler, Save, Sparkles, X } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import type { CatalogState, SourceDetail, SourceObserved, SourceSummary } from '../types';
import { Badge, Button, Dot, Empty, cls, useToast } from './ui';

function ago(ts: number | null): string {
  if (!ts) return 'never';
  const s = Date.now() / 1000 - ts;
  if (s < 90) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

function Readiness({ score, of }: { score: number; of: number }) {
  return (
    <span className="flex items-center gap-0.5" title={`${score} of ${of} ready`}>
      {Array.from({ length: of }, (_, i) => (
        <span key={i} className={cls('h-1.5 w-3 rounded-full',
          i < score ? 'bg-brand-500' : 'bg-zinc-200 dark:bg-white/10')} />
      ))}
    </span>
  );
}

function CatalogCard({ state }: { state: CatalogState | null }) {
  if (!state) return null;
  const live = state.catalogs.filter((c) => c.connected);
  return (
    <div className={cls('flex items-start gap-3 rounded-xl border px-3.5 py-3',
      live.length ? 'border-brand-300/60 bg-brand-50/40 dark:border-brand-500/25 dark:bg-brand-500/[0.05]' : 'hairline')}>
      <BookOpen size={15} className={cls('mt-0.5 shrink-0', live.length ? 'text-brand-700 dark:text-brand-300' : 'dimmer')} />
      <div className="min-w-0 text-2xs leading-relaxed">
        {live.length ? (
          <>
            <p className="text-[13px] font-medium text-zinc-800 dark:text-zinc-100">
              Data catalog: {live.map((c) => c.name).join(', ')}
            </p>
            <p className="dim">
              The agent reads it to understand the sources — {Array.from(new Set(live.flatMap((c) => c.families))).join(', ')} —
              and never queries it for figures. Open a source to import its table and column definitions.
            </p>
          </>
        ) : (
          <>
            <p className="text-[13px] font-medium dim">No data catalog connected</p>
            <p className="dimmer">
              Connect one in MCP servers (Library → Data catalog, or mark a server as “data catalog”). Until then the agent
              relies on these notes and on what it observes.
              {state.catalogs.length > 0 && ` ${state.catalogs.map((c) => c.name).join(', ')} is set as catalog but not connected.`}
            </p>
          </>
        )}
      </div>
    </div>
  );
}

export function SourcesPanel() {
  const [list, setList] = useState<SourceSummary[] | null>(null);
  const [catalog, setCatalog] = useState<CatalogState | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const load = useCallback(async () => {
    const [sources, state] = await Promise.all([api.sources(), api.dataCatalog().catch(() => null)]);
    setList(sources);
    setCatalog(state);
  }, []);
  useEffect(() => { void load(); }, [load]);

  if (open) return <SourceEditor id={open} catalogConnected={!!catalog?.connected} onBack={() => { setOpen(null); void load(); }} />;
  if (!list) return <p className="text-xs dimmer">Loading…</p>;
  const data = list.filter((s) => s.connected);
  return (
    <div className="space-y-4">
      <p className="text-xs leading-relaxed dim">
        Tell the agent what each source holds. A described and profiled source is answered from
        its notes — the right table, the right filter, the metric computed the way you define
        it — instead of five exploratory calls and a guess.
      </p>
      <CatalogCard state={catalog} />
      {data.length === 0 ? (
        <Empty icon={Database} title="No connected server" hint="Connect an MCP server first, in MCP servers." />
      ) : (
        <div className="space-y-1.5">
          {data.map((source) => (
            <button key={source.id} onClick={() => setOpen(source.id)}
              className="focus-ring flex w-full items-center gap-3 rounded-xl border px-3.5 py-3 text-left transition-colors hairline hover:bg-zinc-50 dark:hover:bg-white/[0.03]">
              <Dot tone={source.readiness.score >= 4 ? 'good' : source.readiness.score >= 2 ? 'warn' : 'idle'} />
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-2 text-[13px] font-medium">
                  {source.name}
                  <span className="font-mono text-2xs font-normal dimmer">{source.slug}</span>
                </p>
                <p className="mt-0.5 truncate text-2xs dimmer">
                  {source.description || 'No description yet.'}
                </p>
              </div>
              <div className="flex shrink-0 flex-col items-end gap-1">
                <Readiness score={source.readiness.score} of={source.readiness.of} />
                <span className="text-2xs dimmer">
                  {source.queryable
                    ? `${source.counts.tables} tables · ${source.counts.metrics} metrics · ${source.counts.verified} checked`
                    : `${(source.observed?.coverage.seen.length ?? 0) + (source.observed?.coverage.stale.length ?? 0)} of ${source.observed?.coverage.total ?? 0} tools explored`}
                </span>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function SourceEditor({ id, onBack, catalogConnected }: { id: string; onBack: () => void; catalogConnected?: boolean }) {
  const toast = useToast();
  const [source, setSource] = useState<SourceDetail | null>(null);
  const [description, setDescription] = useState('');
  const [yaml, setYaml] = useState('');
  const [busy, setBusy] = useState<'save' | 'profile' | 'draft' | 'forget' | 'catalog' | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [draftNote, setDraftNote] = useState<SourceDetail | null>(null);

  const take = (detail: SourceDetail) => {
    setSource(detail);
    setDescription(detail.description);
    setYaml(detail.model_yaml);
    setErrors(detail.errors ?? []);
  };
  useEffect(() => { void api.source(id).then(take); }, [id]);

  const run = async (kind: 'save' | 'profile' | 'draft' | 'forget' | 'catalog') => {
    setBusy(kind);
    try {
      if (kind === 'save') {
        const saved = await api.saveSource(id, { description, model_yaml: yaml });
        take(saved);
        setDraftNote(null);
        toast(saved.errors.length ? 'Saved, with problems to fix' : 'Saved', saved.errors.length ? 'error' : 'success');
      } else if (kind === 'profile') {
        const profiled = await api.profileSource(id);
        setSource(profiled);
        setYaml(profiled.model_yaml);
        const p = profiled.profile as { queries?: number; elapsed_ms?: number };
        toast(`Measured in ${((p.elapsed_ms ?? 0) / 1000).toFixed(1)}s (${p.queries ?? 0} queries)`);
      } else if (kind === 'catalog') {
        // Definitions from the catalog, under what is already written: a proposal to read.
        const imported = await api.importFromCatalog(id);
        setYaml(imported.model_yaml);
        toast(imported.catalog_added
          ? `${imported.catalog_added} definitions imported from ${imported.catalog_tables} catalogued tables — review, then Save`
          : 'The catalog has nothing to add to what is already written');
      } else if (kind === 'forget') {
        const fresh = await api.forgetObservations(id);
        setSource((current) => (current ? { ...current, ...fresh } : current));
        toast('Observations cleared — the agent relearns this source from use');
      } else {
        // A draft fills the editor and waits: it is a proposal until someone saves it.
        const draft = await api.draftSource(id);
        if (!source?.queryable) {
          // A tool source's draft is its description: offered in place of an empty one,
          // and beside a written one rather than over it.
          setDescription((current) => (current.trim() ? `${current.trim()}\n\n${draft.description}` : draft.description));
          setSource((current) => (current ? { ...current, observed: draft.observed } : current));
          toast(draft.probed?.length ? `Drafted — probed ${draft.probed.join(', ')}` : 'Drafted from the schemas and observations');
        } else {
          if (!description.trim()) setDescription(draft.description);
          setYaml(draft.model_yaml);
          setDraftNote(draft);
        }
      }
    } catch (e) {
      toast(String((e as Error).message), 'error');
    } finally { setBusy(null); }
  };

  if (!source) return <p className="text-xs dimmer">Loading…</p>;
  const dirty = description !== source.description || yaml !== source.model_yaml;
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2">
        <button onClick={onBack} className="focus-ring grid h-7 w-7 place-items-center rounded-lg dimmer hover:bg-zinc-100 dark:hover:bg-white/[0.06]">
          <ArrowLeft size={14} />
        </button>
        <h3 className="text-sm font-semibold">{source.name}</h3>
        <span className="font-mono text-2xs dimmer">{source.slug}</span>
        <span className="ml-auto text-2xs dimmer">
          {source.queryable ? `profiled ${ago(source.profiled_at)}` : `last used ${ago(source.observed?.last_seen ?? null)}`}
        </span>
      </div>

      <ul className={cls('grid grid-cols-1 gap-1.5', source.readiness.of > 1 && 'sm:grid-cols-5')}>
        {source.readiness.checks.map((check) => (
          <li key={check.key} title={check.hint}
            className={cls('flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-2xs capitalize hairline',
              check.ok ? 'text-zinc-700 dark:text-zinc-200' : 'dimmer')}>
            {check.ok ? <Check size={11} className="text-brand-600" strokeWidth={3} /> : <Circle size={9} />}
            {check.key}
          </li>
        ))}
      </ul>

      <section>
        <label className="mb-1 block text-xs font-semibold">What this source holds</label>
        <p className="mb-2 text-2xs leading-relaxed dimmer">
          In plain words: what is in it, what questions it answers, its time coverage, what it
          cannot answer, and how it relates to your other sources.
        </p>
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={source.queryable ? 4 : 7} maxLength={4000}
          placeholder={source.queryable
            ? 'Sales DB holds every order and refund since January 2026, in EUR with VAT. Use it for revenue by region, channel and segment. Campaigns are in the CRM source, joined on customer id.'
            : 'Market data: daily closes for bonds (clean, % of par) and equities (per share, trading currency), ECB-style FX fixings quoted EURxxx, government curves at month ends. No fixing on TARGET holidays. Identifiers: ISIN or ticker, as in the trade store.'}
          className="focus-ring w-full resize-y rounded-xl border bg-white px-3 py-2.5 text-[13px] leading-relaxed hairline outline-none dark:bg-black/20" />
      </section>

      {source.queryable && <section>
        <div className="mb-1 flex items-center gap-2">
          <label className="text-xs font-semibold">Model</label>
          <Badge>{source.counts.tables} tables · {source.counts.metrics} metrics · {source.counts.caveats} caveats · {source.counts.verified} checked queries</Badge>
          {!yaml.trim() && source.example_yaml && (
            <button onClick={() => setYaml(source.example_yaml ?? '')}
              className="focus-ring ml-auto rounded px-1 text-2xs dim hover:text-zinc-700 dark:hover:text-zinc-200">
              Show an example
            </button>
          )}
        </div>
        <p className="mb-2 text-2xs leading-relaxed dimmer">
          Tables and column values are measured by Profile. Metrics, caveats and checked
          questions are yours: a metric written here is computed that way in every answer.
        </p>
        {draftNote && (
          <div className="mb-2 rounded-lg border border-brand-300/60 bg-brand-50/50 px-3 py-2 text-2xs leading-relaxed dark:border-brand-500/25 dark:bg-brand-500/[0.06]">
            <p className="font-medium text-zinc-800 dark:text-zinc-100">
              Draft ready — {draftNote.checked ?? 0} proposed queries ran and were kept
              {(draftNote.rejected?.length ?? 0) > 0 && `, ${draftNote.rejected!.length} failed and were dropped`}.
              Read the metrics carefully before saving: a wrong definition is repeated in every answer.
            </p>
          </div>
        )}
        <textarea value={yaml} onChange={(e) => setYaml(e.target.value)} rows={18} spellCheck={false}
          className="focus-ring w-full resize-y rounded-xl border bg-zinc-50/60 px-3 py-2.5 font-mono text-[11.5px] leading-relaxed hairline outline-none dark:bg-black/25" />
        {errors.length > 0 && (
          <ul className="mt-2 space-y-1 rounded-lg border border-red-300/60 bg-red-50/60 px-3 py-2 text-2xs text-red-700 dark:border-red-500/25 dark:bg-red-500/[0.07] dark:text-red-300">
            {errors.map((e, i) => <li key={i}>{e}</li>)}
          </ul>
        )}
      </section>}

      <div className="flex flex-wrap items-center gap-2">
        <Button icon={Save} busy={busy === 'save'} disabled={!!busy || !dirty} onClick={() => void run('save')}>Save</Button>
        {source.queryable && (
          <Button variant="outline" icon={Ruler} busy={busy === 'profile'} disabled={!!busy} onClick={() => void run('profile')}
            title="Measure tables, values, ranges, joins and data-quality issues from the data itself.">
            Profile
          </Button>
        )}
        {source.queryable && catalogConnected && (
          <Button variant="outline" icon={Import} busy={busy === 'catalog'} disabled={!!busy} onClick={() => void run('catalog')}
            title="Bring this source's table and column definitions from the data catalog into the model, without overwriting what is written.">
            Import from catalog
          </Button>
        )}
        <Button variant="outline" icon={Sparkles} busy={busy === 'draft'} disabled={!!busy} onClick={() => void run('draft')}
          title={source.queryable
            ? 'Propose a description, metrics, caveats and checked questions. Every proposed query is run first.'
            : 'Describe this service from its tool schemas, what the agent has seen it return, and the read-only calls that need no arguments.'}>
          Draft with AI
        </Button>
        {source.queryable && (
          <span className="ml-auto flex items-center gap-1 text-2xs dimmer">
            <FlaskConical size={11} /> Read-only queries only
          </span>
        )}
      </div>

      {source.observed && source.observed.coverage.total > 0 && (
        <Observed observed={source.observed} busy={busy === 'forget'} onForget={() => void run('forget')}
          onNote={async (noteId, status) => {
            try {
              const fresh = await api.setSourceNote(id, noteId, status);
              setSource((current) => (current ? { ...current, observed: fresh.observed } : current));
            } catch (e) { toast(String((e as Error).message), 'error'); }
          }} />
      )}
    </div>
  );
}

function Observed({ observed, busy, onForget, onNote }: {
  observed: SourceObserved; busy: boolean; onForget: () => void;
  onNote: (noteId: string, status: 'confirmed' | 'discarded') => void;
}) {
  const { coverage } = observed;
  const notes = observed.notes ?? [];
  return (
    <section className="rounded-xl border p-3.5 hairline">
      <div className="mb-1 flex items-center gap-2">
        <Eye size={13} className="dimmer" />
        <h4 className="text-xs font-semibold">Observed by the agent</h4>
        <span className="text-2xs dimmer">
          {coverage.seen.length + coverage.stale.length} of {coverage.total} tools seen working · {observed.calls} calls
        </span>
        <button onClick={onForget} disabled={busy || !observed.calls}
          className="focus-ring ml-auto flex items-center gap-1 rounded px-1.5 py-0.5 text-2xs dim hover:text-zinc-800 disabled:opacity-40 dark:hover:text-zinc-100">
          <Eraser size={11} /> Forget
        </button>
      </div>
      <p className="mb-2.5 text-2xs leading-relaxed dimmer">
        Learned from real calls, kept across conversations, and shown to the agent as
        observation — never as a complete list of what the source holds. Tools not yet
        explored stay on its map.
      </p>
      {notes.length > 0 && (
        <div className="mb-3 space-y-1.5">
          <p className="flex items-center gap-1.5 text-2xs font-semibold">
            <Lightbulb size={11} className="text-amber-500" /> What the agent learned
            <span className="font-normal dimmer">— confirm what is true: confirmed notes reach every question</span>
          </p>
          {notes.map((note) => (
            <div key={note.id} className="flex items-start gap-2 rounded-lg border px-2.5 py-1.5 text-2xs hairline">
              <span className={cls('min-w-0 flex-1 leading-relaxed', note.status === 'confirmed' ? 'text-zinc-800 dark:text-zinc-100' : 'dim')}
                title={note.question ? `While answering: ${note.question}` : undefined}>
                {note.text}
                <span className="ml-1 dimmer">· {note.origin}{note.seen > 1 ? ` · seen ${note.seen}×` : ''}</span>
              </span>
              {note.status === 'confirmed' ? (
                <Badge tone="good">confirmed</Badge>
              ) : (
                <button onClick={() => onNote(note.id, 'confirmed')} title="Confirm: the agent will rely on it"
                  className="focus-ring grid h-5 w-5 place-items-center rounded text-brand-700 hover:bg-brand-50 dark:text-brand-300 dark:hover:bg-brand-500/10">
                  <Check size={11} strokeWidth={3} />
                </button>
              )}
              <button onClick={() => onNote(note.id, 'discarded')} title="Discard"
                className="focus-ring grid h-5 w-5 place-items-center rounded dimmer hover:bg-zinc-100 dark:hover:bg-white/[0.07]">
                <X size={11} />
              </button>
            </div>
          ))}
        </div>
      )}
      <ul className="space-y-1">
        {observed.tools.map((tool) => (
          <li key={tool.name} className="flex items-baseline gap-2 text-2xs">
            <span className={cls('font-mono', tool.ok ? 'text-zinc-700 dark:text-zinc-200' : 'dimmer')}>{tool.name}</span>
            {coverage.stale.includes(tool.name) && <Badge tone="warn">re-check</Badge>}
            <span className="dimmer">
              {tool.calls ? `${tool.ok} ok${tool.failed ? ` · ${tool.failed} failed` : ''} · ${ago(tool.last_seen)}` : 'not yet explored'}
            </span>
            {tool.fields.length > 0 && (
              <span className="min-w-0 truncate font-mono dimmer" title={tool.fields.join(', ')}>→ {tool.fields.join(', ')}</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
