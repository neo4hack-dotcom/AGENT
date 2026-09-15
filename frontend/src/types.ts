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
  /** What this answer rests on: outside sources read, manipulation attempts, compactions. */
  trust?: { sources: string[]; injections: { tool: string; patterns: string[] }[];
            compactions: number };
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
}

export interface Bootstrap {
  app: string;
  model: ModelState;
  mcp: McpSummary;
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
  docs: string;
  enabled: boolean;
  auto_approve: boolean;
  created_at: number;
  status: 'disconnected' | 'connecting' | 'connected' | 'error';
  error: string | null;
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

export interface Diagnostics {
  model: ModelState;
  mcp: McpSummary;
  runtime: ModelRuntime;
  context_window: number;
  runtimes: Record<string, RuntimeInfo>;
  python_modules: string[];
  workspace: string;
  store: string;
  warnings: string[];
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
  | { type: 'text.delta'; index: number; text: string }
  | { type: 'thinking.delta'; index: number; text: string }
  | { type: 'plan'; steps: PlanStep[] }
  | { type: 'tool.start'; index: number; id: string; name: string; args: Record<string, unknown>;
      server: string; kind: 'builtin' | 'mcp'; by?: string; ref?: string }
  | { type: 'tool.end'; index: number; id: string; ok: boolean; status: ToolStatus;
      summary: string; ms: number; preview?: string; cached?: boolean }
  | { type: 'approval.request'; call_id: string; index: number; name: string;
      args: Record<string, unknown>; server: string; kind: string; reason: string;
      expires_in_s: number }
  | { type: 'approval.resolved'; call_id: string; approved: boolean; reason?: string }
  | { type: 'critic'; tool: string; status: string; reason: string; advice: string }
  | { type: 'taint'; source: string; sources: string[] }
  | { type: 'injection'; index: number; tool: string; patterns: string[] }
  | { type: 'offload'; index: number; handle: string; bytes: number }
  | { type: 'compaction'; turns: number; digest_chars: number; count: number }
  | { type: 'usage'; llm_calls: number; tokens_in: number; tokens_out: number; tool_calls: number }
  | { type: 'notice'; message: string }
  | { type: 'error'; message: string; kind: string }
  | { type: 'done'; status: string; usage: Usage; message_id: string };
