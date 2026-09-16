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
(`filesystem__read_file`), and the built-in file tools are named `workspace_*` so it is
never ambiguous which directory a call is about.

**The library is local-only by selection.** Every one of its eleven recipes runs as a
process on your machine and needs no third-party account: **Pandas Frames** (bundled with
this repository), filesystem, Git, SQLite, Postgres, Web Fetch, Playwright, knowledge-graph
memory, sequential thinking, time, and the protocol's own reference server. Two of them
reach the network *at your instruction* — Web Fetch opens the URL you name, Playwright
drives a browser to the page you name — and neither holds a credential.

**Pandas Frames** is the one server that ships here rather than being fetched: it runs on
the interpreter already serving the API, so there is no launcher to install. It loads CSV,
Excel and Parquet files into real dataframes and lets the agent query them — joins,
group-bys, statistics — through an expression sandbox, inside a killable worker with memory
and CPU ceilings, with every call written to an append-only audit log. Its docstring is
honest about what that sandbox is: real defence against mistakes and runaway work, not a
security boundary against a determined attacker. Point it at data you are willing to have
read.

A server that talks to a hosted API is still one "custom server" form away, over stdio or
HTTP. It is simply a decision you make deliberately rather than one the shelf makes look
routine.

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

### The trust layer

Content the agent reads is not content the agent trusts. Every tool result is stripped of
known secrets, scanned for manipulation, marked as having come from outside, and fenced
with a per-run nonce the system prompt declares — so a page can be summarised without being
obeyed. Once a run has read something foreign, the one capability with irreversible reach
outside this machine stops being automatic; the rest are held precisely, by an egress policy
that knows whose idea each host was and by a kernel sandbox that denies `run_python` the
network outright. Facts the agent learns while reading are quarantined until you confirm
them, and every action lands in a hash-chained log that cannot hide being edited.

[`SECURITY.md`](SECURITY.md) is the whole model, including what it does not claim.

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
- **Context is engineered, not just filled.** Only the tools a turn plausibly needs are put
  in front of the model — the rest stay reachable through `find_tools` — and an oversized
  tool result is parked in the workspace with a handle left in its place. When the
  transcript still outgrows the window it is compacted, with the question and the standing
  rules copied through **verbatim**: measurements of long-horizon agents show safety
  constraints do not survive summarisation, because a compressor optimising for continuity
  has no reason to keep a rule competing for a shrinking budget.
- **Answers cite their evidence.** Every tool result is labelled `#1`, `#2`…; the answer
  puts the label after the value it produced, and clicking it scrolls to the call that
  established it. An uncited sentence is the agent's own claim, which is a useful thing to
  be able to see.
- **A reader with no hands.** `research` delegates a question to a sub-agent with its own
  context and only read-only tools. It keeps six pages of sources out of the conversation,
  and anything hostile in them is talking to something that cannot write, run, send or
  remember.
- **Fonts are self-hosted** (Space Grotesk for the wordmark, Inter for everything read —
  72 KB together). A page that phones a font CDN on every load to draw its own name is not a
  local-first app.

---

## How well it actually works

[`EVALS.md`](EVALS.md) records a ten-case campaign where every task needs two or three MCP
servers at once, run against a real SQLite database, a git repository and a document folder,
each with a ground truth checkable to the cent. It lists what broke, why, and what changed:
86 tool calls became 54, fourteen failures became five, and every artefact the agent claims
to have written now exists with the right content.

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
