# AGENT

An autonomous data analyst with a single input on an empty screen, built to run inside a
private network with no path to the internet. Behind it: a bounded agentic loop, tools that
work with nothing configured, and the MCP servers that hold your data — databases, market
data services, reference data — connected from a discreet admin page and described once so
the agent knows what each one is for.

Models run through **Ollama**, on this machine or on a server inside your network.

**Air-gapped by default.** No tool reaches the internet, and the paths out that
configuration could open are closed where they open: cloud models and external Ollama hosts
are refused, MCP endpoints must be internal, package managers run offline, and on macOS
every local MCP server runs inside a kernel sandbox that allows the loopback interface and
nothing else. See [Air-gapped deployment](#air-gapped-deployment).

---

## Getting started

Requires **Python 3.12+**, **Ollama** with at least one model that can call tools, and
**Node 20+** to build the interface (not to run it: production serves the built files).

```bash
make install
make api    # http://localhost:3041
make web    # http://localhost:3040
```

Defaults to `gpt-oss:20b`. Change it in **⌘,** → *Model* — anything reporting `tools`
works. Models tagged `-cloud` run at ollama.com and are listed as *leaves the network*: they
cannot be selected while the deployment is air-gapped. (`AGENT_ALLOW_CLOUD_MODEL=true`
lifts that one rule for testing on non-sensitive data; everything else stays enforced.)

Production is one process; the backend serves the built SPA from the same origin, so there
is no CORS at all:

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

`gpt-oss` reports tools and thinking but **not vision**, so image attachments need a
different model (`qwen3.5:4b` and `gemma4:e4b` both see).

---

## What it can do with nothing configured

| Tool | What it actually does |
|---|---|
| `run_python` | pandas/numpy in a separate process with the network denied; `rows('#4')` loads any earlier result |
| `chart` | a professional Vega-Lite chart, validated against the data, revisable in place |
| `export_data` / `create_report` | an Excel/CSV/JSON extract, or a PDF report with charts and numbered sources |
| `ask_user` | a question with options, when the request is ambiguous (which "Kerner"?) |
| `source_info` | what a connected source holds: its description, model, metrics and caveats |
| `read_file` / `write_file` / `list_files` | the workspace, and nothing else |
| `remember` / `recall` | durable memory across conversations |
| `plan` | the checklist you watch tick over |
| `current_time` | the machine's date and time |

Every connected **MCP** server adds its tools to the same index, namespaced by server
(`filesystem__read_file`), and the built-in file tools are named `workspace_*` so it is
never ambiguous which directory a call is about.

**The library ships with the app.** Files (read-only, scoped to one directory), SQLite
(read-only at the engine level, with a time limit and a row ceiling), Pandas Frames and
time zones are servers in `mcp_servers/`, written against the Python standard library
(pandas for the dataframes) and started on the app's own interpreter: nothing is fetched
from npm or PyPI at connect time. Servers installed from the old `npx`/`uvx` recipes are
moved onto them automatically; Diagnostics names any server that still fetches a package
when it starts.

**Pandas Frames** runs on the interpreter already serving the API, so there is no launcher
to install. It loads CSV,
Excel and Parquet files into real dataframes and lets the agent query them — joins,
group-bys, statistics — through an expression sandbox, inside a killable worker with memory
and CPU ceilings, with every call written to an append-only audit log. Its docstring is
honest about what that sandbox is: real defence against mistakes and runaway work, not a
security boundary against a determined attacker. Point it at data you are willing to have
read.

Your own servers — a risk engine, a market data service, a positions database — are one
"custom server" form away, over stdio or HTTP, as long as they live inside the network.

**Twenty servers, one question.** The prompt carries a source map — every source, what
its tools do, what they were seen returning, what is still unexplored — and past the tool
budget the fast model routes each question to the sources it needs, by meaning rather than
shared words. What calls return is remembered across conversations (the atlas), so the
next question starts where the last one finished; see [`DATA.md`](DATA.md).

**Describe each source once** (*Admin → Data sources*): what it holds and what it cannot
answer, in plain words, plus for a SQL source a model the app measures from the data itself —
tables, values, ranges, joins — and the metrics you define once and want computed the same
way in every answer. [`DATA.md`](DATA.md) explains the method.

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
that refuses anything outside the private network and by a kernel sandbox that denies
`run_python` the network outright. Facts the agent learns while reading are quarantined until you confirm
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

`run_python` runs in a separate process with CPU, memory and wall-clock limits. On macOS
it also runs in a kernel sandbox: no network, writes only in the workspace, and no reads of
home directories or data folders beyond the workspace — data arrives through the sources,
never straight off the disk. Elsewhere there is no kernel sandbox, and the code runs with
this app's own rights: treat it as a guard against runaway and accident there. The switch
is in *Guardrails*.

---

## Who can reach it

- **This machine** uses the app freely. With no `AGENT_ADMIN_PASSWORD`, admin is open here
  and nowhere else.
- **Other machines** see a sign-in screen: `AGENT_ACCESS_PASSWORD` opens the agent,
  `AGENT_ADMIN_PASSWORD` opens the agent and admin. With neither set, nothing is served
  beyond this machine. Failed attempts back off.
- **Other web pages** open in the same browser cannot use it: requests from another site
  are refused (`Sec-Fetch-Site`, `Origin`), the Host header must name this app (which stops
  DNS rebinding — add your own host names to `AGENT_ALLOWED_HOSTS`), and the page's content
  security policy lets it load and connect to its own origin only.

## Admin

Discreet: **⌘,** or the small icon top right.

Secrets you enter (tokens, connection strings) never come back to the browser in the clear:
they are masked, and sending a masked value back does not overwrite it.

Tabs: **Model** · **MCP servers** (library, connected servers, a bench to call one tool by
hand) · **Data sources** · **Tools** · **Guardrails** · **Memory** · **Diagnostics** (which
shows the air gap as it is enforced).

---

## Air-gapped deployment

`AGENT_AIRGAPPED=true` is the default and a property of the deployment, not a switch in the
UI: whoever reaches Admin cannot open a path out.

| Path out | How it is closed |
|---|---|
| The model | `-cloud` models and Ollama hosts outside the private network are refused |
| MCP over HTTP | the endpoint must be internal — loopback, a private address, or a suffix in `AGENT_INTERNAL_DOMAINS` — and so must every redirect |
| MCP over stdio | `npx`/`uvx`/`pip` run offline; internet packages are refused by name; on macOS the process runs in a kernel sandbox allowing loopback only |
| MCP needing an internal host | a database client (detected from its configuration, or set to *Internal network* in the server form) keeps the network; the enterprise firewall is what holds it inside |
| `run_python` | kernel sandbox, network denied |
| Tool arguments carrying a URL | refused unless the host is internal; cloud metadata addresses always refused |
| The browser | CSP `connect-src 'self'`, self-hosted fonts, no external asset of any kind |
| Charts rendered server-side | the renderer's URL allowlist is empty |

**Provisioning offline.** Nothing is downloaded at run time, so everything is installed
beforehand, from your internal mirrors:

```bash
pip install -r backend/requirements.txt       # via your PyPI mirror
cd frontend && npm ci && npm run build        # via your npm mirror, or ship frontend/dist
npm install -g @modelcontextprotocol/server-filesystem ...   # each stdio server you use
ollama pull gpt-oss:20b                       # on the Ollama host, or import the weights
```

`npx -y package` works offline only once the package is in the npm cache or installed
globally; a server that fails to start says so on its card, with its own stderr.

---

## Architecture

```
backend/app/
├── main.py              FastAPI; serves the built SPA in production
├── config.py            one env prefix, everything defaulting empty/off
├── deps.py              dependency container; `settings` is a *live* view
├── store.py             flat JSON, serialised and debounced writes
├── security.py          who may reach the app: local, sign-in, origin and host checks
├── network.py           the air gap: model, MCP endpoints, offline launch, kernel sandbox
├── llm/provider.py      Ollama: streaming, reasoning split out, native tools
├── mcp/                 hand-written JSON-RPC client (stdio + HTTP), registry, catalog
├── agent/
│   ├── runner.py        the loop, approvals, critic, synthesis
│   ├── guard.py         the loop guard
│   ├── prompts.py       everything the model knows about itself
│   ├── builtin.py       the built-in tools
│   ├── data_tools.py    chart, ask_user, export_data, create_report
│   └── memory.py        long-term memory, weighted-overlap recall
├── data/                source knowledge, profiler, drafting, charts, exports, PDF
└── tools/               code execution, files
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
- **Fonts are self-hosted** (Space Grotesk for the wordmark, Inter for everything read —
  72 KB together). A page that phones a font CDN on every load to draw its own name has no
  place in an air-gapped network.

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
- Outside macOS there is no kernel sandbox for MCP processes: the air gap there rests on
  offline package managers, the checks above and your firewall — and Diagnostics says so.
- Memory recall uses weighted term overlap, not embeddings: inspectable, no second model to
  load, and enough for the handful of durable facts a personal agent accumulates.

---

## Ports

`3040` web · `3041` API. Fallback pair: `3042` / `3043` (`npm run dev:alt`). The dev server
listens on localhost only; `AGENT_FRONT_HOST=0.0.0.0` exposes it deliberately.
