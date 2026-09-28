// Hand-mirrored from the backend's Pydantic models and the runner's block shapes.
// Edited in the same commit as the backend model it mirrors — that is the whole contract.

export type BlockType = 'text' | 'thinking' | 'tool';
export type ToolStatus =
  | 'running' | 'done' | 'error' | 'denied' | 'expired' | 'blocked' | 'awaiting_approval';

export interface Block {
  type: BlockType;
  index: number;
  text?: string;
  superseded?: boolean;
  // tool blocks only
  id?: string;
  name?: string;
  args?: Record<string, unknown>;
  server?: string;
  kind?: 'builtin' | 'mcp';
  status?: ToolStatus;
  ok?: boolean | null;
  summary?: string;
  ms?: number;
  cached?: boolean;
  by?: string;
  data?: unknown;
  /** Short evidence label (`#3`) the answer cites and the reader can follow back. */
  ref?: string;
  /** Manipulation patterns found in this result — flagged, never acted on. */
  injection?: string[];
  /** Workspace handle when the result was too large to keep in context. */
  offloaded?: string;
  redacted?: number;
  /** What the reader receives from this call: a chart, a file, an answered question. */
  chart?: ChartOut;
  file?: FileOut;
  ask?: AskOut;
}

/** A chart as the backend stored it: the Vega-Lite spec with its rows, no theme. */
export interface ChartOut {
  id: string;
  version: number;
  title: string;
  source: string;
  spec: Record<string, unknown>;
  data: Record<string, unknown>[];
  /** True when the rows were written into the call rather than named. */
  typed?: boolean;
}

export interface FileOut {
  path: string;
  name: string;
  /** csv, xlsx, json, pdf — or whatever extension a tool gave a file it wrote. */
  format: string;
  bytes: number;
  rows?: number;
  pages?: number;
  sheets?: string[];
}

export interface AskOut {
  question: string;
  options: string[];
  answer: string | null;
}

export interface AskRequest {
  call_id: string;
  question: string;
  options: string[];
  allow_other: boolean;
  expires_in_s: number;
}

/** One data source in Admin: what is written about it and how ready it is. */
export interface SourceSummary {
  id: string;
  name: string;
  slug: string;
  connected: boolean;
  /** Has a read-only SQL tool, so it can be profiled and given a model. */
  queryable: boolean;
  description: string;
  counts: { tables: number; metrics: number; caveats: number; verified: number };
  readiness: { score: number; of: number; checks: { key: string; ok: boolean; hint: string }[] };
  profiled_at: number | null;
  updated_at: number | null;
  /** What the agent has seen this source's tools return, across conversations. */
  observed?: SourceObserved;
}

export interface SourceNote {
  id: string;
  text: string;
  origin: string;
  status: 'proposed' | 'confirmed';
  seen: number;
  created_at: number;
  question?: string;
}

export interface SourceObserved {
  coverage: { seen: string[]; stale: string[]; unexplored: string[]; total: number };
  notes?: SourceNote[];
  calls: number;
  last_seen: number | null;
  tools: { name: string; calls: number; ok: number; failed: number; fields: string[]; last_seen: number | null }[];
}

export interface SourceDetail extends SourceSummary {
  model_yaml: string;
  example_yaml?: string;
  profile: Record<string, unknown>;
  errors: string[];
  checked?: number;
  rejected?: { question: string; sql: string; error: string }[];
  /** Tools called unprompted to draft a tool source's description (read-only, no arguments). */
  probed?: string[];
  /** From an import: tables the catalog documented, and descriptions added. */
  catalog_tables?: number;
  catalog_added?: number;
}

export interface PlanStep {
  index: number;
  title: string;
  status: 'pending' | 'active' | 'done' | 'skipped';
}

export interface Usage {
  llm_calls?: number;
  tokens_in?: number;
  tokens_out?: number;
  tool_calls?: number;
  /** What we sent versus what the provider actually re-read: the cache-hit signal. */
  prompt_sent?: number;
  prompt_evaluated?: number;
  /** Time to the first token of the first turn — what the wait actually feels like. */
  ttft_ms?: number;
  /** Characters recovered by masking old tool results. */
  masked_chars?: number;
  /** How full the window was on the last turn, not a running total. */
  context_tokens?: number;
  context_limit?: number;
  /** Where that window went on the last turn: paid-every-turn overhead versus conversation. */
  context_parts?: { system: number; tools: number; messages: number; reserve?: number };
}

export interface Artifact {
  path: string;
  name: string;
  bytes: number;
  modified: number;
  kind: string;
}

export interface SearchHit {
  conversation_id: string;
  title: string;
  role: string;
  updated_at: number;
  excerpt: string;
}

export interface Skill {
  id: string;
  name: string;
  trigger: string;
  body: string;
  source: string;
  uses: number;
  created_at: number;
}

export interface SkillStats {
  total: number;
  learned: number;
  ready: number;
}

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: number;
  blocks?: Block[];
  plan?: PlanStep[];
  usage?: Usage;
  status?: 'running' | 'completed' | 'failed' | 'cancelled';
  error?: string | null;
  model?: string;
  images?: { name: string; mime: string }[];
  /** Remarks the run made about itself; `quiet` ones belong in the log, not the answer. */
  notices?: { text: string; quiet?: boolean }[];
  /** Files the run left in the workspace that no output card already covers. */
  files?: FileOut[];
  /** What this answer rests on: outside sources read, manipulation attempts, compactions. */
  trust?: { sources: string[]; injections: { tool: string; patterns: string[] }[];
            compactions: number };
  /** What the answer rests on, derived from the run: see backend agent/lineage.py. */
  lineage?: Lineage;
  /** What was verified along the way: routing, the final check, flags raised on results. */
  checks?: { name: string; result: string; detail?: string }[];
  /** The method in plain words, written on request from the lineage. */
  method?: { text: string; model?: string; at?: number };
  /** Each figure the answer states, and the steps whose result holds it. */
  figures?: FigureTrace[];
  /** 'direct': the model with the chosen servers' tools, no agent layer. */
  mode?: 'agent' | 'direct';
  servers?: string[];
}

export interface LineageNode {
  ref: string;
  tool: string;
  source: string;
  kind: 'retrieval' | 'computation' | 'output' | 'exploration' | 'other';
  ok: boolean;
  at?: number | null;
  ms?: number;
  operation: string;
  summary?: string;
  fingerprint: string;
  /** Hash of this step's entry in the tamper-evident audit log. */
  audit?: string;
  depends_on: string[];
  rows?: number;
  columns?: string[];
  output?: string;
  /** A possible input of a computation whose inputs could not be traced exactly. */
  inferred?: boolean;
  /** Inputs could not be traced; depends_on lists the retrievals that came before. */
  inputs_inferred?: boolean;
}

export interface Lineage {
  cited: string[];
  implicit: boolean;
  nodes: LineageNode[];
  sources: string[];
  exploration: { ref: string; tool: string; source: string; summary: string }[];
  failed: { ref: string; tool: string; error: string }[];
  calls: number;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  messages: Message[];
  /** Set when a run is still working on this conversation, so a reload can re-attach. */
  active_run_id?: string;
}

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  message_count: number;
  preview: string;
}

export interface ModelCapabilities {
  tools: boolean;
  thinking: boolean;
  vision: boolean;
  audio?: boolean;
  context_length: number;
  declared?: string[];
  source: string;
}

export interface ModelState {
  ok: boolean;
  provider: string;
  label: string;
  model: string;
  /** False when the tag is Ollama-hosted (`:cloud`) rather than running on this machine. */
  local: boolean;
  base_url?: string;
  error: string | null;
  capabilities: ModelCapabilities;
  fast_model: string;
}

export interface McpSummary {
  servers_total: number;
  servers_connected: number;
  servers_error: number;
  tools_available: number;
}

export interface ToolInfo {
  name: string;
  group: string;
  kind: 'builtin' | 'mcp';
  write: boolean;
  description: string;
}

export interface AdminState {
  mode: 'local-only' | 'password';
  authenticated: boolean;
  local: boolean;
  signed_in?: boolean;
  remote?: 'password' | 'closed';
}

export interface FigureTrace {
  raw: string;
  refs: string[];
  found: boolean;
  asked?: boolean;
}

export interface DirectServer {
  id: string;
  name: string;
  slug: string;
  connected: boolean;
  tool_count: number;
  role: string;
}

export interface Bootstrap {
  app: string;
  model: ModelState;
  mcp: McpSummary;
  servers?: DirectServer[];
  tools: ToolInfo[];
  admin: AdminState;
  prefs: { approval_mode: string };
  workspace: string;
  memory: { total: number; pending: number };
}

export interface McpServer {
  id: string;
  slug: string;
  name: string;
  catalog_id: string;
  transport: 'stdio' | 'http';
  command: string;
  args: string[];
  env: Record<string, string>;
  url: string;
  headers: Record<string, string>;
  description: string;
  accent: string;
  category: string;
  docs?: string;
  enabled: boolean;
  auto_approve: boolean;
  created_at: number;
  status: 'disconnected' | 'connecting' | 'connected' | 'error';
  error: string | null;
  /** How this server's process is fenced from the network, once connected. */
  network?: string;
  /** 'catalog' for a data catalog (documentation about the data), else a data source. */
  role?: '' | 'source' | 'catalog';
  /** The role was recognised from the server's tools rather than set by a person. */
  role_detected?: boolean;
  tool_count: number;
  resource_count: number;
  prompt_count: number;
  connected_at: number | null;
  call_count: number;
  server_info: Record<string, unknown>;
  protocol_version: string;
  diagnostics: string[];
}

export interface CatalogParam {
  key: string;
  label: string;
  required: boolean;
  secret: boolean;
  placeholder: string;
  help: string;
}

export interface CatalogEntry {
  id: string;
  name: string;
  vendor: string;
  category: string;
  accent: string;
  description: string;
  transport: 'stdio' | 'http';
  command?: string;
  args?: string[];
  url?: string;
  params: CatalogParam[];
  tags: string[];
  docs: string;
}

export interface RuntimeInfo {
  name: string;
  label: string;
  why: string;
  available: boolean;
  path: string;
  install: string;
  url: string;
}

export interface ModelOption {
  name: string;
  size_gb: number;
  family: string;
  parameters: string;
  local: boolean;
  capabilities: ModelCapabilities;
  /** Why the air gap refuses this model; empty when it may be used. */
  refused?: string;
}

export interface MemoryEntry {
  id: string;
  kind: 'identity' | 'fact';
  text: string;
  source: string;
  origin: string;
  /** `quarantined` means it was learned while untrusted content was in context. */
  status: 'trusted' | 'quarantined';
  created_at: number;
  updated_at: number;
  hits: number;
}

export interface AuditEntry {
  ts: number;
  event: string;
  hash: string;
  prev: string;
  run_id?: string;
  tool?: string;
  args?: string;
  ok?: boolean;
  ms?: number;
  tainted?: boolean;
  status?: string;
  reason?: string;
  verdict?: string;
  injection?: string[] | null;
}

export interface AuditReport {
  entries: AuditEntry[];
  verified: { ok: boolean; entries: number; broken_at: number | null; reason?: string };
  path: string;
}

export interface ModelRuntime {
  loaded: boolean;
  size_gb?: number;
  vram_gb?: number;
  gpu_percent?: number;
  context?: number;
}

export interface RunMetrics {
  runs: number;
  tokens_in?: number;
  tokens_out?: number;
  llm_calls?: number;
  tool_calls?: number;
  calls_per_run?: number;
  cache_hit?: number;
  ttft_median_ms?: number;
  ttft_p90_ms?: number;
  masked_chars?: number;
  peak_context?: number;
  failed?: number;
}

export interface Diagnostics {
  model: ModelState;
  runs: RunMetrics;
  mcp: McpSummary;
  runtime: ModelRuntime;
  context_window: number;
  runtimes: Record<string, RuntimeInfo>;
  python_modules: string[];
  workspace: string;
  store: string;
  warnings: string[];
  network?: { airgapped: boolean; internal_domains: string[]; kernel_sandbox: boolean;
              cloud_model_allowed: boolean };
}

export interface UploadResult {
  id: string;
  name: string;
  mime: string;
  size: number;
  kind: 'image' | 'file';
  path: string;
}

// --- the run event stream -------------------------------------------------
export type StreamEvent =
  | { type: 'status'; phase: string }
  | { type: 'block.open'; kind: 'text' | 'thinking'; index: number }
  | { type: 'block.supersede'; index: number }
  | { type: 'draft.delta'; text: string }
  | { type: 'draft.clear' }
  | { type: 'text.delta'; index: number; text: string }
  | { type: 'thinking.delta'; index: number; text: string }
  | { type: 'plan'; steps: PlanStep[] }
  | { type: 'tool.start'; index: number; id: string; name: string; args: Record<string, unknown>;
      server: string; kind: 'builtin' | 'mcp'; by?: string; ref?: string }
  | { type: 'tool.end'; index: number; id: string; ok: boolean; status: ToolStatus;
      summary: string; ms: number; preview?: string; cached?: boolean;
      chart?: ChartOut; file?: FileOut; ask?: AskOut }
  | ({ type: 'ask.request' } & AskRequest)
  | { type: 'files'; files: FileOut[] }
  | { type: 'ask.resolved'; call_id: string; answer: string | null; reason?: string }
  | { type: 'approval.request'; call_id: string; index: number; name: string;
      args: Record<string, unknown>; server: string; kind: string; reason: string;
      expires_in_s: number; host?: string }
  | { type: 'approval.resolved'; call_id: string; approved: boolean; reason?: string }
  | { type: 'critic'; tool: string; status: string; reason: string; advice: string }
  | { type: 'taint'; source: string; sources: string[] }
  | { type: 'injection'; index: number; tool: string; patterns: string[] }
  | { type: 'offload'; index: number; handle: string; bytes: number }
  | { type: 'compaction'; turns: number; digest_chars: number; count: number }
  | ({ type: 'usage' } & Usage)
  | { type: 'notice'; message: string; quiet?: boolean }
  | { type: 'error'; message: string; kind: string }
  | { type: 'done'; status: string; usage: Usage; message_id: string };

export interface CatalogState {
  connected: boolean;
  catalogs: { id: string; name: string; slug: string; connected: boolean; detected: boolean;
              families: string[]; tools: string[] }[];
}

export interface ConnectionTest {
  provider: string;
  base_url: string;
  reachable: boolean;
  list_ms?: number;
  models?: string[];
  error?: string | null;
  model?: string;
  chat?: { ok: boolean; reply?: string; thinking?: boolean; ms?: number; tokens_out?: number; error?: string };
  tools?: { ok: boolean; native?: boolean; arguments?: Record<string, unknown>; ms?: number; error?: string | null };
}
