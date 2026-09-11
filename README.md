# AGENT

An autonomous agent with a single input on an empty screen. Behind it: a bounded agentic
loop, a set of tools that work with nothing configured, and a library of MCP servers you
connect in two clicks from a discreet admin page.

Models run through **Ollama**. A model tagged `-cloud` is hosted by Ollama rather than by
your machine, and the interface says which on every screen — the status badge in the header
carries a monitor icon for a local model and a cloud icon for a hosted one. Nothing else
about a conversation leaves the machine.

---

## Getting started

Requires **Python 3.12+**, **Node 20+**, and **[Ollama](https://ollama.com)** with at least
one model that can call tools.

```bash
make install
make api    # http://localhost:3041
make web    # http://localhost:3040
```

Defaults to `gpt-oss:120b-cloud` for reasoning and `qwen3.5:4b` for the fast lane. Change
both in **⌘,** → *Model*. To run entirely on your own hardware, pick a local model there —
anything reporting `tools` works.

Production is one process; the backend serves the built SPA from the same origin, so there
is no CORS to configure:

```bash
make serve
```

### Choosing a model

Admin lists what your Ollama server offers with **the capabilities the server declares**,
never guessed from the name:

| Capability | Without it |
|---|---|
| `tools` | the agent cannot call **anything**; it only answers from memory |
| `thinking` | no reasoning trace is shown |
| `vision` | attached images are not sent — and the app tells you so |

`gpt-oss:120b-cloud` reports tools and thinking but **not vision**, so image attachments
need a different model (`qwen3.5:4b` and `gemma4:e4b` both see).

---

## What it can do with nothing configured

| Tool | What it actually does |
|---|---|
| `web_search` | DuckDuckGo search, no API key |
| `web_fetch` | fetches a URL and reads it (HTML → text, JSON, CSV) |
| `run_python` | runs Python in a separate process, killed past its timeout |
| `read_file` / `write_file` / `list_files` | the workspace, and nothing else |
| `remember` / `recall` | durable memory across conversations |
| `plan` | the checklist you watch tick over |
| `current_time` | the machine's date and time |

Every connected **MCP** server adds its tools to the same index, namespaced by server
(`filesystem__read_file`). The library ships fifteen ready recipes — filesystem, Git,
SQLite, Postgres, Playwright, GitHub, Slack, Notion, Context7 — and any other server is one
"custom server" form away, over stdio or HTTP.

---

## How the loop works

```
message ──► [ think ──► call tools ──► observe ]* ──► answer
                 ▲                  │
                 └────── critic ────┘
```

A bounded ReAct loop rather than a nine-node state machine: nine LLM calls before the first
word is a minute of silence to answer "hello". Rigour is attached where it pays for its
latency:

- **The loop guard** runs on every call, free. It fingerprints a call by its *structure*
  (tool + arguments), never its wording — a model told "try something different" reproposes
  the same idea rephrased with remarkable reliability. Search queries compare as word sets,
  so the same search in another order counts once.
- **The critic** only runs on a **failed** call, where its advice changes the next attempt.
  It is a separate LLM call that sees only the call and its error: the model that just
  failed is the worst placed to judge whether it succeeded.
- **The final gap check** runs once before answering, and does not merely suggest: it
  **names the missing tool call, and the orchestrator executes it**. A model told to open a
  page replies "I would need to open that page"; an orchestrator that opens it returns the
  data.
- **The final answer is composed from an evidence digest** — the question, then what each
  tool actually returned — never from the whole transcript. Replaying fifteen thousand
  tokens of tool output makes a small model answer the last page it read instead of the
  person, often in the page's language.
- **The plan is excluded from that evidence.** A planned step is not a completed step: a
  model shown its own plan as "evidence" reports every step as done, which is how a run that
  wrote no file ends up claiming it wrote *and verified* one.

### Honesty before completeness

The principle above all others: **a failure stays visible**. A tool that fails says so, a
server that will not start shows its own stderr on its card, a missing capability is
announced before it is needed. Nowhere does this app substitute a plausible value for a
result it did not get.

---

## Human control

Three levels, in *Guardrails*:

| Mode | Effect |
|---|---|
| Never | the agent acts alone |
| **For writes** (default) | an MCP tool that changes something asks you first, inline |
| Always | every tool needs approval, Python execution included |

Built-in tools only ever write inside the workspace, so they are gated in "always" mode
alone: asking to approve every `run_python` would make the agent useless exactly when it is
most useful. A trusted MCP server can be auto-approved, server by server.

`run_python` deserves to be named for what it is: a guard against runaway and accident —
separate process, bounded working directory, a watchdog that kills it past the timeout —
**not a security sandbox**. Code runs with this app's own rights. The switch is in
*Guardrails*.

---

## Admin

Discreet: **⌘,** or the small icon top right.

- **No password** (default) — reachable only from the machine running the app. The right
  setting for a personal agent: nothing to invent before configuring anything, and nothing
  exposed if the port is ever forwarded.
- **With `AGENT_ADMIN_PASSWORD`** — required everywhere, loopback included, in exchange for
  a token. Failed attempts back off.

Secrets you enter (tokens, connection strings) never come back to the browser in the clear:
they are masked, and sending a masked value back does not overwrite it.

Tabs: **Model** · **MCP servers** (library, connected servers, a bench to call one tool by
hand) · **Tools** · **Guardrails** · **Memory** · **Diagnostics**.

---

## Architecture

```
backend/app/
├── main.py              FastAPI; serves the built SPA in production
├── config.py            one env prefix, everything defaulting empty/off
├── deps.py              dependency container; `settings` is a *live* view
├── store.py             flat JSON, serialised and debounced writes
├── security.py          local-only, or password → token
├── llm/provider.py      Ollama: streaming, reasoning split out, native tools
├── mcp/                 hand-written JSON-RPC client (stdio + HTTP), registry, catalog
├── agent/
│   ├── runner.py        the loop, approvals, critic, synthesis
│   ├── guard.py         the loop guard
│   ├── prompts.py       everything the model knows about itself
│   ├── builtin.py       the built-in tools
│   └── memory.py        long-term memory, weighted-overlap recall
└── tools/               web, code execution, files
frontend/src/
├── App.tsx              the two states: empty canvas, then conversation
├── api.ts               the one typed client (+ a reconnecting SSE stream)
└── components/          ui, Markdown, Composer, Thread, Sidebar, Admin, McpLibrary
```

The MCP client is written **directly against the protocol** (JSON-RPC 2.0, `2025-06-18`)
with no SDK: one code path for a local process or a remote endpoint, and a connection
failure that surfaces as a readable diagnostic — the server's own stderr included — rather
than an opaque import error.

### Details that matter

- **The context window is sized from the model**, not left at Ollama's default. That default
  is 4096 tokens whatever the model; an agent's prompt — instructions plus tool schemas —
  takes most of it, and the model gets cut off mid-sentence, turn after turn, until the
  budget is gone. The app asks for the window the model declares, capped at **16k for a
  model running here** (past that the KV cache pushes it off the GPU) and **64k for one
  Ollama hosts**, where this machine's memory is not the constraint. Both adjustable in
  *Guardrails*.
- **A run outlives the tab.** It runs server-side; the SSE stream is a *view* of it,
  re-attachable, replaying whatever was missed since the last sequence number received.
  Closing the tab mid-answer loses nothing.
- **Deltas are batched per frame** before entering React state: a fast local model would
  otherwise turn every token into a full re-parse of the markdown so far.
- **Markdown rendering is dependency-free** and tolerates a document still being written — an
  unclosed code fence, a half-written table. No `dangerouslySetInnerHTML`, and hrefs are
  restricted to http(s)/mailto so a `javascript:` link repeated from a scraped page never
  reaches the DOM.
- **Fonts are self-hosted** (Space Grotesk for the wordmark, Inter for everything read —
  72 KB together). A page that phones a font CDN on every load to draw its own name is not a
  local-first app.

---

## Limits, said plainly

- A small model gets things wrong. The guardrails make its mistakes visible and
  recoverable; they do not remove them.
- `run_python` is not a security sandbox (see above).
- The JSON store assumes **a single process**. It holds up well for a personal agent; a real
  database is needed the day several instances write at once.
- Web search goes through DuckDuckGo's HTML endpoint, keyless. If it changes shape or rate
  limits, the tool **says so** instead of returning an empty list the model would read as
  "nothing exists about this".
- Memory recall uses weighted term overlap, not embeddings: inspectable, no second model to
  load, and enough for the handful of durable facts a personal agent accumulates.

---

## Ports

`3040` web · `3041` API. Fallback pair: `3050` / `3051` (`npm run dev:alt`).
