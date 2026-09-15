// The single typed client. No component ever calls fetch directly, so every endpoint has
// exactly one call site to change when its shape changes — and TypeScript finds every
// caller that needs updating with it.

import type {
  AdminState, AuditReport, Bootstrap, CatalogEntry, Conversation, ConversationSummary,
  Artifact, Diagnostics, MemoryEntry, McpServer, ModelOption, RuntimeInfo, SearchHit, Skill,
  SkillStats, StreamEvent, UploadResult,
} from './types';

const TOKEN_KEY = 'agent.admin.token';

export function adminToken(): string {
  try { return localStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; }
}
export function setAdminToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch { /* private browsing: the session simply does not persist */ }
}

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = adminToken();
  const res = await fetch(`/api${path}`, {
    ...init,
    // Headers last: spreading init above would otherwise drop these defaults whenever a
    // caller passes its own.
    headers: {
      ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    let detail = await res.text();
    try { detail = JSON.parse(detail).detail ?? detail; } catch { /* plain-text body */ }
    throw new ApiError(res.status, detail.slice(0, 400) || `HTTP ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const body = (data: unknown) => JSON.stringify(data);

export const api = {
  // --- app ---------------------------------------------------------------
  bootstrap: () => request<Bootstrap>('/bootstrap'),

  // --- conversations -----------------------------------------------------
  listConversations: () => request<ConversationSummary[]>('/conversations'),
  getConversation: (id: string) => request<Conversation>(`/conversations/${id}`),
  createConversation: () => request<Conversation>('/conversations', { method: 'POST' }),
  renameConversation: (id: string, title: string) =>
    request<Conversation>(`/conversations/${id}`, { method: 'PATCH', body: body({ title }) }),
  deleteConversation: (id: string) =>
    request<{ ok: boolean }>(`/conversations/${id}`, { method: 'DELETE' }),

  // --- chat --------------------------------------------------------------
  chat: (payload: { conversation_id?: string; text: string; attachments?: string[] }) =>
    request<{ run_id: string; conversation_id: string; user_message_id: string; message_id: string }>(
      '/chat', { method: 'POST', body: body(payload) }),
  cancelRun: (runId: string) => request<{ ok: boolean }>(`/runs/${runId}/cancel`, { method: 'POST' }),
  approve: (runId: string, callId: string, approved: boolean) =>
    request<{ ok: boolean }>(`/runs/${runId}/approve`,
      { method: 'POST', body: body({ call_id: callId, approved }) }),

  upload: (file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<UploadResult>('/uploads', { method: 'POST', body: form });
  },

  // --- what came out of it -----------------------------------------------
  artifacts: () => request<Artifact[]>('/artifacts'),
  artifactUrl: (path: string) => `/api/artifacts/${path.split('/').map(encodeURIComponent).join('/')}`,
  artifactText: async (path: string) => {
    const res = await fetch(api.artifactUrl(path));
    if (!res.ok) throw new ApiError(res.status, await res.text());
    return res.text();
  },
  search: (q: string) => request<SearchHit[]>(`/search?q=${encodeURIComponent(q)}`),
  exportUrl: (id: string) => `/api/conversations/${id}/export`,
  exportConversation: async (id: string) => {
    const res = await fetch(api.exportUrl(id));
    if (!res.ok) throw new ApiError(res.status, await res.text());
    return res.text();
  },
  retry: (id: string, messageId: string, text: string) =>
    request<{ run_id: string; conversation_id: string; user_message_id: string; message_id: string }>(
      `/conversations/${id}/retry`, { method: 'POST', body: body({ message_id: messageId, text }) }),

  // --- memory ------------------------------------------------------------
  listMemory: () => request<MemoryEntry[]>('/memory'),
  addMemory: (text: string) => request<MemoryEntry>('/memory', { method: 'POST', body: body({ text }) }),
  forgetMemory: (id: string) => request<{ ok: boolean }>(`/memory/${id}`, { method: 'DELETE' }),
  confirmMemory: (id: string) =>
    request<{ ok: boolean }>(`/memory/${id}/confirm`, { method: 'POST' }),

  // --- admin -------------------------------------------------------------
  adminState: () => request<AdminState>('/admin/state'),
  login: (password: string) =>
    request<{ token: string; expires_at: number }>('/admin/login',
      { method: 'POST', body: body({ password }) }),
  logout: () => request<{ ok: boolean }>('/admin/logout', { method: 'POST' }),

  models: () => request<{ ok: boolean; error: string | null; models: ModelOption[];
                          selected: string; fast_selected: string }>('/admin/models'),
  getPrefs: () => request<{ effective: Record<string, unknown>; overridden: string[];
                            env_defaults: Record<string, unknown> }>('/admin/prefs'),
  setPrefs: (values: Record<string, unknown>) =>
    request<{ effective: Record<string, unknown> }>('/admin/prefs',
      { method: 'POST', body: body({ values }) }),

  catalog: () => request<{ entries: CatalogEntry[]; categories: string[];
                           runtimes: Record<string, RuntimeInfo> }>('/admin/catalog'),
  servers: () => request<McpServer[]>('/admin/servers'),
  addServer: (payload: Record<string, unknown>) =>
    request<McpServer>('/admin/servers', { method: 'POST', body: body(payload) }),
  patchServer: (id: string, patch: Record<string, unknown>) =>
    request<McpServer>(`/admin/servers/${id}`, { method: 'PATCH', body: body(patch) }),
  deleteServer: (id: string) =>
    request<{ ok: boolean }>(`/admin/servers/${id}`, { method: 'DELETE' }),
  connectServer: (id: string) =>
    request<Partial<McpServer>>(`/admin/servers/${id}/connect`, { method: 'POST' }),
  disconnectServer: (id: string) =>
    request<Partial<McpServer>>(`/admin/servers/${id}/disconnect`, { method: 'POST' }),
  serverTools: (id: string) =>
    request<{ qualified_name: string; name: string; title: string; description: string;
              input_schema: Record<string, unknown>; write: boolean; read_only: boolean }[]>(
      `/admin/servers/${id}/tools`),
  callTool: (name: string, args: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/admin/tools/${encodeURIComponent(name)}/call`,
      { method: 'POST', body: body({ arguments: args }) }),
  soul: () => request<{ text: string }>('/admin/soul'),
  setSoul: (text: string) =>
    request<{ text: string }>('/admin/soul', { method: 'POST', body: body({ text }) }),
  skills: () => request<{ skills: Skill[]; stats: SkillStats }>('/admin/skills'),
  addSkill: (payload: { name: string; trigger: string; body: string }) =>
    request<Skill>('/admin/skills', { method: 'POST', body: body(payload) }),
  forgetSkill: (id: string) =>
    request<{ ok: boolean }>(`/admin/skills/${id}`, { method: 'DELETE' }),

  diagnostics: () => request<Diagnostics>('/admin/diagnostics'),
  audit: (limit = 200) => request<AuditReport>(`/admin/audit?limit=${limit}`),
};

/**
 * Subscribe to a run.
 *
 * EventSource rather than a streamed fetch, for one reason that matters: it reconnects on
 * its own, and `since` makes that reconnection lossless — the server replays from the last
 * sequence number this client actually saw. Closing a laptop lid mid-answer is then a gap
 * in the stream, not a broken message.
 */
export function streamRun(
  runId: string,
  onEvent: (event: StreamEvent) => void,
  onClose: (reason: 'done' | 'error') => void,
): () => void {
  let source: EventSource | null = null;
  let lastSeq = -1;
  let closed = false;
  let retries = 0;

  const open = () => {
    if (closed) return;
    source = new EventSource(`/api/runs/${runId}/stream?since=${lastSeq + 1}`);
    source.onmessage = (message) => {
      let event: StreamEvent & { seq?: number };
      try { event = JSON.parse(message.data); } catch { return; }
      if (typeof event.seq === 'number') lastSeq = event.seq;
      retries = 0;
      onEvent(event);
      if (event.type === 'done') {
        closed = true;
        source?.close();
        onClose('done');
      }
    };
    source.onerror = () => {
      source?.close();
      if (closed) return;
      retries += 1;
      if (retries > 6) { closed = true; onClose('error'); return; }
      setTimeout(open, Math.min(4000, 300 * 2 ** retries));
    };
  };
  open();
  return () => { closed = true; source?.close(); };
}
