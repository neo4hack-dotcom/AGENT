// Hand-mirrored from the backend's Pydantic models and the runner's block shapes.
// Edited in the same commit as the backend model it mirrors — that is the whole contract.

export type BlockType = 'text' | 'thinking' | 'tool';
export type ToolStatus = 'running' | 'done' | 'error' | 'denied' | 'awaiting_approval';

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
  memory_count: number;
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
  text: string;
  source: string;
  created_at: number;
  updated_at: number;
  hits: number;
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
      server: string; kind: 'builtin' | 'mcp'; by?: string }
  | { type: 'tool.end'; index: number; id: string; ok: boolean; status: ToolStatus;
      summary: string; ms: number; preview?: string; cached?: boolean }
  | { type: 'approval.request'; call_id: string; index: number; name: string;
      args: Record<string, unknown>; server: string; kind: string; reason: string }
  | { type: 'approval.resolved'; call_id: string; approved: boolean; reason?: string }
  | { type: 'critic'; tool: string; status: string; reason: string; advice: string }
  | { type: 'usage'; llm_calls: number; tokens_in: number; tokens_out: number; tool_calls: number }
  | { type: 'notice'; message: string }
  | { type: 'error'; message: string; kind: string }
  | { type: 'done'; status: string; usage: Usage; message_id: string };
