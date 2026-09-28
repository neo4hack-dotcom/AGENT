# AGENT

An autonomous data analyst with a single input on an empty screen, built to run inside a
private network with no path to the internet. Behind it: a bounded agentic loop, tools that
work with nothing configured, and the MCP servers that hold your data — databases, market
data services, reference data — connected from a discreet admin page and described once so
the agent knows what each one is for.

Models run through **Ollama**, or any **OpenAI-compatible** server (vLLM, LM Studio,
llama.cpp, LocalAI, TGI…), on this machine or on a server inside your network.

**Air-gapped by default.** No tool reaches the internet, and the paths out that
configuration could open are closed where they open: cloud models and model servers outside
the network are refused, MCP endpoints must be internal, package managers run offline, and on macOS
every local MCP server runs inside a kernel sandbox that allows the loopback interface and
nothing else. See [Air-gapped deployment](#air-gapped-deployment), and
[Locking it down](#locking-it-down) for the switches that remove what a deployment does not
need — code execution, the pandas server, custom programs, sign-in-free local access.

**Using it rather than running it?** The [user guide](docs/GUIDE-UTILISATEUR.md) (in French)
covers asking questions, reading the evidence, charts, Excel extracts and PDF reports.

---

## Getting started

Runs on macOS, Linux and [Windows](#windows). Requires **Python 3.12+**, **Ollama** with at
least one model that can call tools, and **Node 20+** to build the interface (not to run it:
production serves the built files).

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
make serve          # = build, then `python -m app` in backend/
```

`python -m app` reads where to listen from `backend/.env` — `AGENT_HOST` (loopback by
default), `AGENT_PORT`, and `AGENT_TLS_CERT` / `AGENT_TLS_KEY` for HTTPS — so a deployment
is described in one file, not split between it and a launch command.

### Windows

No `make` on Windows: three scripts at the root do the same, by double-click or from `cmd`.

| Script | Does |
|---|---|
| `install-windows.bat` | finds Python 3.12+ (`py -3`, then `python`), creates `backend\.venv`, installs both sides, copies `.env.example` to `backend\.env` |
| `start-windows.bat` | development: the API on `:3041` and the interface on `:3040`, each in its own window |
| `serve-windows.bat` | production: builds the interface, then one process (`python -m app`: host, port and TLS from `backend\.env`; `--no-build` skips the build) |

The installer does what long paths on Windows require: npm's cache moves to
`%SystemDrive%\npm-cache`, and `LongPathsEnabled` is switched on — which needs an
Administrator prompt once; without it the script says so and carries on. Offline, `pip` and
`npm` go through the internal mirrors declared in `pip.ini` and `.npmrc`, as on any other
machine.

What differs from macOS and Linux, and why:

- **The API runs without `--reload`.** On Windows, uvicorn's reloader switches asyncio to
  the selector loop, which cannot start processes: neither `run_python` nor any local MCP
  server would. The scripts leave it off; restart the API window after a backend change.
  Started by hand with `--reload`, the API says so at startup and in *Diagnostics*.
- **`run_python` ceilings come from a job object** — memory per process, CPU time, 32
  processes at most, and a kill that reaches everything the code started. The network is
  refused inside the interpreter rather than by the kernel (see [Human control](#human-control)).
- **Everything is read and written as UTF-8** — the store, `.env`, exports, and the pipes
  to every child process — rather than in the console's code page, which would break every
  accent in a result.
- **Network paths are refused before they are looked at.** On Windows, merely resolving
  `\\host\share\file` connects to that host over SMB and offers the user's NTLM
  credentials — before any check can say no. UNC and device paths (`\\…`, `//…`, `\\?\`,
  `\\.\`) and alternate data streams (`name:stream`) are therefore refused on the string,
  in the workspace tools, the bundled Files and Pandas servers, the download routes and any
  URL containing a backslash; inside `run_python`, `open()` and `os` refuse them too.
- **The data folder and `backend\.env` are narrowed to the account running the app** at
  every start (`icacls`, by SID: SYSTEM, Administrators and that account). A folder created
  under `C:\` otherwise lets every authenticated user of the machine read the conversations.
- `tzdata` is installed for exchange calendars (Windows ships no time-zone database),
  `npm run dev:alt` replaces the inline variables `cmd.exe` cannot run, the dev proxy targets
  `127.0.0.1` (Node may resolve `localhost` to `::1`), and export names avoid the device
  names Windows reserves (`nul.csv` would be the null device).

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

### A local model server that is not Ollama

Admin → *Model* → **OpenAI-compatible**, then the server's base URL (usually ending in
`/v1`) and its API key if it asks for one. Or in `backend/.env`:

```bash
AGENT_LLM_PROVIDER=openai
AGENT_OPENAI_BASE_URL=http://gpu-01.internal:8000/v1
AGENT_OPENAI_API_KEY=            # empty if the server needs none
AGENT_MODEL=Qwen/Qwen3-32B
```

Two buttons check it before you rely on it:

- **List models** — what `GET /models` returns, from the URL typed in the form, before saving.
- **Test connection** — reaches the server, asks the model for a one-word answer, then makes
  it call a test tool. Each step reports its latency and what came back, so a server that
  answers but ignores tools (vLLM without `--enable-auto-tool-choice`, a chat template with
  no tool support) is caught here rather than in the middle of a question.

This API does not declare capabilities as Ollama does: `tools` is assumed, thinking and
vision are guessed from the name, the context window is read from `/models` when the server
reports it — **Test connection** is what verifies tool calling. Streaming, reasoning
(`reasoning_content`), native tool calls, JSON schemas and images all go through the
standard chat-completions API; the key is sent as a bearer token, masked in Admin and
redacted from traces. The air gap applies to this URL exactly as to Ollama's.

### Direct LLM mode

The small icon beside the paperclip switches between **Agent** and **Direct LLM**. In direct
mode the question goes to the model with the tools of the MCP servers you tick in the same
menu — and nothing else: no routing, plan, critic, gap check or composed answer, no
built-in tools. One server, several, or none (the model alone). The choice is remembered in
the browser, a retried question keeps its mode, and the answer's footer says
`direct · <servers>`.

What does not switch off: approvals, the air gap, the fencing of tool output as data,
secret redaction, the audit trail (with the mode and servers of each run) and the lineage of
every tool call.

Direct mode has no built-in tools, so charts, extracts and PDF reports are Agent mode only
— the **PDF** button under an answer works in both.

---

## What it can do with nothing configured

| Tool | What it actually does |
|---|---|
| `run_python` | pandas/numpy in a separate process with the network denied; `rows('#4')` loads any earlier result |
| `chart` | a professional Vega-Lite chart, validated against the data, revisable in place |
| `export_data` / `create_report` | an Excel/CSV/JSON extract, or a PDF report with charts and numbered sources |
| `ask_user` | a question with options, when the request is ambiguous (which "Kerner"?) |
| `source_info` | what a connected source holds: its description, model, metrics and caveats, and what its tools were seen returning |
| `batch_call` | one read-only tool for many items at once — or for every row of an earlier result (`rows_from='#N'`) |
| `profile_data` | a data-quality profile of any result: nulls, duplicates, robust outliers per instrument, weekend/holiday dates, values missing from reference data |
| `note_source` | a convention or pitfall learned about a source, proposed for an administrator to confirm |
| `read_file` / `write_file` / `list_files` | the workspace, and nothing else |
| `remember` / `recall` | durable memory across conversations |
| `plan` | the checklist you watch tick over |
| `current_time` | the machine's date and time |
| `business_days` | business-day arithmetic from the calendars' rules — TARGET2 (default, also Euronext Paris), UK, US (NYSE): was a date a trading day, T+n, previous / next business day, days between, each month's last business day, a year's holidays |

### Working with the data behind an answer

Every step of an answer opens on what it did and what came back — built for someone who
will check the figures, not take them on trust:

- **The query as code**: SQL or Python shown formatted, with its own copy button.
- **The result as a table you can work with**: sort any column, filter rows, numbers
  formatted (or raw, one click), negatives in red, and a status line that totals the
  quantities (Σ) and averages the rates, with min / max / count on hover.
- **Copy for Excel**: tab-separated, numbers with this browser's decimal mark and no
  thousands separators, so a paste into a French Excel lands as numbers. The same button
  sits under every table the agent writes in its answer, with a CSV download.
- **Every row, not the excerpt**: *Excel* / *CSV* on a step fetch the whole result from the
  server — a large one parked on disk included — with a *Provenance* sheet naming the query.
- **Edit & run**: change the date or the filter in a step's query and run it yourself,
  against the same source, without the model — milliseconds instead of a model turn.
  Read-only tools only (a tool that changes data stays with the agent, behind its
  approval), the air gap applies to the arguments, secrets are stripped from the result,
  every run is audited, and none of it is added to the answer's evidence.
- **Save as checked query** (administrators): a query corrected with *Edit & run* is added
  to its source's model as a worked example — the agent reads the checked queries that
  resemble a question before writing SQL, so a definition fixed once ("count trades, not
  versions") stays fixed. Inserted into the model's text, so the administrator's comments
  and layout survive.
- **Ask about #N** puts a step's reference in the next question — *"chart #5 by desk"*.
- **Every figure points to its step.** Figures in an answer are underlined: hover for the
  step that returned the number, click to scroll to it; one no step returned is underlined
  in amber — computed or written by the model.
- A chart's **Data** panel is the same working table: its rows sorted, totalled, copied.
- **Figures verified**: the line under an answer says whether every figure it states was
  found in a step's result, or which were not (see below).

### Reports, extracts and charts to take away

Ask in plain words — *« fais-moi un rapport PDF sur … avec un graphique et le tableau »*,
*"export this to Excel"* — and the file arrives under the answer as a card with
**Download** (and **Open**, for a PDF); every file stays reachable in **⌘K** → *Workspace
files*.

| Asked for | Produced by | What is in it |
|---|---|---|
| a PDF, a report | `create_report` | title and scope, sections in order (markdown, charts, tables of up to 40 rows), `[#N]` citations turned into numbered **Sources**, a **Method and provenance** appendix with each query, its row count and SHA-256 |
| Excel, CSV, JSON | `export_data` | formatted, filterable workbook (several sheets on request), with a *Provenance* sheet — or a `.provenance.json` beside a CSV/JSON |
| a chart as an image | the chart's own buttons | PNG or SVG, drawn by the same engine as on screen |
| this answer, as it stands | **PDF** under the answer | the question, the answer, every chart it drew, its sources and the provenance appendix — assembled from the stored run, no model call, so it works whatever the model |

A report is assembled from what the conversation actually produced — charts drawn with
`chart`, rows returned by calls — never from figures retyped into it, and it is rendered on
the server (ReportLab, charts through `vl-convert`): nothing leaves the machine. Its own words
follow its language: a report written in French says *Page 2 sur 3*, *Méthode et
provenance*, *27 septembre 2026* and *1 234 567,89*; one written in English says it in
English (`language` forces either). Headings keep with what follows them, and column names
read as a reader would say them (`pnl_eur` → *P&L EUR*). Asking for a file and ending the run
without one is caught at runtime: the agent is sent back to produce it. A report that leaves
out what was asked for is refused the same way: asked for *a chart and the table*, a report
with neither is sent back with the chart ids and the results holding rows that exist (twice
at most; a third attempt goes through, with the gap stated). What is unambiguous is attached
rather than asked for again: the chart this very run drew for the request, under the
section that announces it, and — when the only table it can be is that chart's data — the
table. A local model
once wrote "the chart below" over an empty page, having named the chart `chart_id` — now
understood, along with `figure`, `table_ref` and the like.

**Figures in answers and reports are checked against the data** (`app/data/figures.py`).
In an answer, a figure no step returned sends the agent back once to take it from a result
or compute it with `run_python`; the outcome is shown under the answer (*figures verified*,
or *2 figures unverified* with the list). Run over the 93 stored answers of the evaluation
campaigns that state figures, it flagged 17 — among them a correct total the model had
summed in its head: right that time, unverifiable every time.
In a report: The
same model wrote *"Credit has the highest usage at 89.3 %"* above a table saying 65.03 — a
number that existed nowhere, in a document made to be forwarded. Every figure the text
states — decimals, or four digits and more that are not a year — must be found among the
numbers the conversation's results hold (rounding, a ratio written as a percentage, and
thousands or millions written short all count). One that is not sends the report back,
naming it and its section; a third attempt is built, with the figure marked *Check before
use* where it stands. Counts, days and confidence levels are left alone.

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

**A data catalog, if you have one.** Connect the enterprise catalog — DataLoom or any MCP
server that lists datasets, column definitions, a glossary and lineage — from *Library →
Data catalog* (an internal HTTP endpoint and its token), or tick *data catalog* on any
connected server; a server whose tools are plainly a catalog's is recognised on its own. A
catalog is kept apart from the data sources, and used **if and only if one is connected**:

- it is never routed to, queried for figures or listed among the sources; its tools are
  always offered, with a short paragraph telling the agent what the catalog is for —
  understanding a table, a column, a business term, a calculation, where data comes from;
- `source_info` adds what the catalog says about a source's tables;
- *Admin → Data sources* shows it, and each SQL source gets *Import from catalog*: its
  table and column definitions folded into the source's model under what is already
  written, as a proposal you read before saving. *Draft with AI* uses them too.

With no catalog connected, none of this appears anywhere — not a tool, not a line of prompt.

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

### Traceability

Every answer carries its lineage, derived from the run rather than from the model's account
of itself: the evidence it cites and everything that evidence depended on — each step with
its source, the exact SQL, code or arguments, the number of rows, the time, and a SHA-256
fingerprint of the result, so a re-run can be compared. Under the answer, *based on …*
opens the steps and the checks that ran (source routing, the Critic's verdict, SQL filter
values verified against the source, flags raised on results); *Explain the method* writes
the method in plain words from that chain; *Audit trail* downloads the whole of it as
Markdown or JSON. Excel extracts carry a *Provenance* sheet, CSV and JSON a
`.provenance.json` beside them, PDF reports a *Method and provenance* appendix.

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

`run_python` runs in a separate process with CPU, memory and wall-clock limits (a job
object on Windows). On macOS it also runs in a kernel sandbox: no network, writes only in
the workspace, and no reads of home directories or data folders beyond the workspace — data
arrives through the sources, never straight off the disk. Elsewhere there is no kernel
sandbox: the interpreter refuses sockets to anything but loopback, which stops an innocent
`pd.read_csv("https://…")` but not code set on getting out, and the code runs with this
app's own rights — treat it as a guard against runaway and accident there. The switch is in
*Guardrails*.

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
  security policy lets it load and connect to its own origin only — and run no `eval`:
  charts use Vega's expression interpreter, so a chart spec cannot become code.
- **A shared machine** — Remote Desktop, Citrix, a jump host — is "this machine" for
  everyone logged on to it. `AGENT_REQUIRE_SIGNIN=true` ends the implicit trust: a password
  for every request, loopback included. Diagnostics flags a remote session or a Windows
  Server edition that runs without it.
- **Over the network**, set `AGENT_TLS_CERT` and `AGENT_TLS_KEY`: passwords and answers
  then travel encrypted, and the session cookie is marked Secure. Sign-in failures are
  counted per client address — the real peer, never a forwarded header a client could
  choose — and across all addresses at once, and each one is written to the audit log.

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
| The model | `-cloud` models are refused, and so is any model server — Ollama or OpenAI-compatible — outside the private network (`AGENT_ALLOW_CLOUD_MODEL` lifts the first rule only) |
| MCP over HTTP | the endpoint must be internal — loopback, a private address, or a suffix in `AGENT_INTERNAL_DOMAINS` — and so must every redirect |
| MCP over stdio | `npx`/`uvx`/`pip` run offline; internet packages are refused by name; on macOS the process runs in a kernel sandbox allowing loopback only |
| MCP needing an internal host | a database client (detected from its configuration, or set to *Internal network* in the server form) keeps the network; the enterprise firewall is what holds it inside |
| `run_python` | macOS: kernel sandbox, network denied. Windows, Linux: sockets refused inside the interpreter except to loopback, and on Windows network file paths too — a guard, not a sandbox (`AGENT_ENABLE_PYTHON_TOOL=false` removes it) |
| Windows file paths | UNC, WebDAV and device paths refused before resolution, everywhere a path can come from the model or a URL — resolving one would already send credentials out |
| Secrets in child processes | a stdio server never inherits `AGENT_*` variables or anything named like a credential from the API's environment; what it needs is set in its own configuration |
| Tool arguments carrying a URL | refused unless the host is internal; cloud metadata addresses always refused (tools that only draw or write — charts, reports, exports — take URLs as data: nothing they are given is fetched) |
| The browser | CSP `connect-src 'self'`, no `unsafe-eval`, self-hosted fonts, no external asset of any kind |
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

## Locking it down

Every capability the agent has can be removed from the deployment, in `backend/.env`. These
are environment settings, not preferences: Admin can turn run_python off, never back on past
what the environment allows, and cannot touch the others at all. Restart after changing them.

| Setting | Default | Set to close |
|---|---|---|
| `AGENT_AIRGAPPED` | `true` | — keep it: models, MCP endpoints and packages stay inside the network |
| `AGENT_REQUIRE_SIGNIN` | `false` | `true`: no implicit trust for 127.0.0.1 — for shared machines (RDS, Citrix) |
| `AGENT_ADMIN_PASSWORD` · `AGENT_ACCESS_PASSWORD` | empty | long passwords (Diagnostics warns under 12 characters) |
| `AGENT_ENABLE_PYTHON_TOOL` | `true` | `false`: the model cannot run code at all |
| `AGENT_ENABLE_PANDAS` | `true` | `false`: the Pandas Frames server leaves the library, cannot be added, and a configured one does not start |
| `AGENT_ALLOW_CUSTOM_COMMANDS` | `true` | `false`: only the servers bundled with the app run as processes — Admin cannot launch another program, change a bundled server's interpreter, or set `PYTHONPATH`, `NODE_OPTIONS`, `LD_PRELOAD` and the like on it. HTTP servers inside the network remain |
| `AGENT_HOST` | `127.0.0.1` | keep loopback unless other machines need it — then with `AGENT_TLS_CERT` / `AGENT_TLS_KEY` |

The strictest profile, for a deployment where documents from outside meet the agent:

```ini
AGENT_AIRGAPPED=true
AGENT_REQUIRE_SIGNIN=true
AGENT_ADMIN_PASSWORD=<long, known to the administrators>
AGENT_ACCESS_PASSWORD=<long, given to the users>
AGENT_ENABLE_PYTHON_TOOL=false
AGENT_ENABLE_PANDAS=false
AGENT_ALLOW_CUSTOM_COMMANDS=false
```

What remains is reading through MCP servers you chose, drawing charts and writing extracts
and reports into the workspace. Whatever the switches, the data folder (conversations,
memory, audit log, workspace) and `backend/.env` are narrowed at every start to the account
running the app — owner-only on macOS and Linux; that account, SYSTEM and Administrators on
Windows. Admin → *Diagnostics* shows each switch as it is enforced,
and warns about what is open: a shared machine without `AGENT_REQUIRE_SIGNIN`, a network
listener without TLS, `run_python` without a kernel sandbox, a data folder other accounts
can read.

**What no setting in an application can promise**, said so it is not assumed: an
administrator of the machine can read anything the app stores; a model can be wrong or be
talked into a wrong answer (the trust layer makes that visible, not impossible); and a
database client MCP server reaches the host it is configured for — the enterprise firewall
is the boundary there, as for everything outside this process. "100 %" is the firewall, the
machine's own hardening and these switches together, not any one of them.

**Checking it.** The protections above have tests that run offline, on Windows as elsewhere:

```bash
cd backend && python -m unittest discover tests
```

`npm audit` and `pip-audit` found no known vulnerability in the dependencies at the time of
writing; the floors in `requirements.txt` sit at the releases that fixed the ones a
network-facing server cares about, and `defusedxml` keeps workbooks from outside from
expanding XML entities.

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
├── llm/provider.py      Ollama and OpenAI-compatible: streaming, reasoning, native tools,
│                        connection probe
├── mcp/                 hand-written JSON-RPC client (stdio + HTTP), registry, catalog
├── agent/
│   ├── runner.py        the loop, approvals, critic, synthesis; direct mode
│   ├── guard.py         the loop guard
│   ├── prompts.py       everything the model knows about itself
│   ├── builtin.py       the built-in tools
│   ├── data_tools.py    chart, ask_user, export_data, create_report
│   └── memory.py        long-term memory, weighted-overlap recall
├── data/                source knowledge, catalog bridge, atlas, profiler, drafting,
│                        charts, exports, PDF, figure checks
└── tools/               code execution, files
frontend/src/
├── App.tsx              the two states: empty canvas, then conversation
├── api.ts               the one typed client (+ a reconnecting SSE stream)
└── components/          ui, Markdown, Composer, ModeSwitch, Thread, Sidebar, Admin,
                         McpLibrary
docs/GUIDE-UTILISATEUR.md  the user guide (French)
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
- **The budget counts what every request pays up front.** With eighteen sources connected,
  instructions and source notes plus tool schemas took 10.8k of a 16k local window before
  the conversation began; a compaction trigger that looked at the conversation alone never
  fired, and the model was cut off three tokens into its turn. The window is now split three
  ways — that fixed overhead, the conversation, and a reserve for the model's own turn (a
  sixth of the window, 1.5k–8k) — and when they do not fit, schemas turn compact, fewer
  tools are offered in full (`find_tools` still reaches every one), older results are
  masked, and the middle is compacted. A turn cut off anyway is retried once in the smaller
  prompt before the run gives up and composes from what it has. Each turn's usage records
  the split (`context_parts`).
- **A slow local model degrades, it does not fail.** Timeouts sized for a hosted model
  broke a laptop one silently: source routing had 25 s, a local model takes longer than
  that to read the source map, so every question fell back to all eighteen sources
  described in full — the slowest prompt there is. Routing now gets a quarter of the model
  timeout, and says so when it is skipped. A turn that times out is retried once with a
  smaller prompt and no extended reasoning; a second one ends the loop and the answer is
  composed from what was gathered — where it used to fail the run after 54 minutes and
  publish the model's half-written plan.
- **Dates are computed, not remembered.** A 4B model answered that 1 May 2026 "is a
  Sunday" — a Friday, closed for Labour Day, whose answer was the close of 30 April. Every
  date a question names now arrives in the prompt with its weekday, whether it is a
  TARGET2 business day, the business days around it, and for "fin mai 2026" the month's
  last business day (29 May); `business_days` covers the rest.
- **Numbers typed into code are checked before it runs.** The answer's figures were
  checked; the computation's inputs were not — a model typed a VaR limit of 5 800 000
  where the source said 4 500 000, and the wrong ratio became evidence. `run_python` code
  whose data-like literals (five digits or more, or two decimals or more) appear in no
  result is refused with the way to load the real values (`rows('#N')`); a line marked
  `# constant` is the analyst's parameter.
- **SQL in the source's dialect.** A syntax error from a source comes back with the fix
  in its own dialect — SQLite, Oracle, ClickHouse, PostgreSQL, MySQL, SQL Server, told
  from the server's declared identity: `x::float` → `CAST(x AS REAL)`, `LIMIT n` →
  `FETCH FIRST n ROWS ONLY`, `DATE_TRUNC` → `strftime` / `TRUNC` / `toStartOfMonth`. The
  same help reaches the analyst's own *Edit & run*.
- **One misnamed argument is renamed, not refused.** When a call fails with exactly one
  unknown argument and exactly one required argument missing — `instrument_id` for
  `identifier`, `table` for `table_name` — the value is sent under the schema's name and
  the result says so. A run once ended on exactly that, the critic's own step having used
  the wrong name.
- **An answer with figures and no citation gets its sources written.** The trace knows
  which step returned each figure; when the model forgets every `[#N]`, a *Sources* line
  is added from it — M5 had the right trader and the right 41.9 %, and cited nothing.
- **A chart is not lost to its JSON.** A complete spec followed by debris is kept; a spec
  that cannot be read at all, with rows to draw, becomes a default chart from the rows'
  shape (time on x, the measure on y, a small category as colour), said to the model so it
  can revise. M7 had the right monthly counts and ran out of time rewriting 1 500
  characters of broken JSON. A crash on list-valued fields in a spec (`unhashable type`)
  is fixed, and Vega errors come back without their JavaScript stack.
- **Code can call every read-only tool of the routed sources**, not only the ones whose
  schemas fit in the prompt this turn: a function in the Python prelude costs no tokens.
- **A parked result is described, not re-read.** Reading a parked 96 KB result back
  parked the read — a copy the model then read, which was parked in turn: nine reads of the
  same data in one run, until the time budget ran out. A read of `.results/…` now returns
  the result's shape (rows, columns, first rows) and the ways to work on it that keep it
  out of the context: `rows('#4')` in `run_python`, `batch_call(rows_from='#4')`,
  `export_data(source='#4')`, `chart(data='#4')`.
- **Looking is not fetching.** A small model lists the tables, describes them, then
  answers that the data "is not accessible" — having never run a query. A draft that gives
  up after exploration alone is sent back naming the source's query tool, and a source
  whose tables were just described keeps its query tools on offer.
- **A file in the workspace is a snapshot.** A stale `trades.csv` from another conversation
  was once read in place of the trade store. A data file read from the workspace now comes
  with its age and a reminder that the source is where current data lives.
- **A tool that answers "no" is not down.** The loop guard counted three failures of a
  tool as an outage — including *"VaR is computed at month ends only: …, 2026-05-29"*, the
  tool telling the model exactly how to call it. The corrected call, with the right date,
  was then refused. Only timeouts, disconnections, 5xx and rate limits count now.
- **A small model's formatting slips are repaired, not punished.** Each of these cost a
  local 4B model its chart or its report during the PDF tests, on a call that was right in
  substance:
  - the arguments written inside the tool's *name* — `get_var({"date": "2026-04-30"})</parameter`,
    a chat template speaking XML — are split back into tool and arguments, in both providers;
  - `print(rows)` in `run_python` prints a Python literal, not JSON; it is read as rows
    (`ast.literal_eval`: data only, nothing looked up, nothing run);
  - a chart spec wrapped as `{"title": …, "spec": {mark, encoding}}` is unwrapped, and a
    spec that is not valid JSON is reported as such, with the position — "must be a
    Vega-Lite object" sent the model back to write the same broken object;
  - the `$schema` URL at the top of a Vega-Lite spec no longer trips the egress policy (see
    *Air-gapped deployment*).

  And one mistake that is not a slip is refused: a chart that renders but misleads — rates
  *summed* across rows (four desks' usage drawn as one 251 % bar), or several rows collapsed
  into a single mark because no channel splits them. Run over every chart the evaluation
  campaigns had drawn, the check flagged that one and none of the other twelve.
- **Answers cite their evidence.** Every tool result is labelled `#1`, `#2`… — numbered
  across the whole conversation, so a follow-up's "#7" is the last answer's #7 and nothing
  else (numbering restarted at #1 per question once made "the table, #1" resolve to the very
  call asking for it); the answer
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
- `run_python` is not a security sandbox (see above) — `AGENT_ENABLE_PYTHON_TOOL=false`
  where that matters.
- The JSON store assumes **a single process**. It holds up well for a personal agent; a real
  database is needed the day several instances write at once.
- Outside macOS there is no kernel sandbox for MCP processes: the air gap there rests on
  offline package managers, the checks above and your firewall — and Diagnostics says so.
- Windows support is written for and checked on macOS, not yet run on a Windows machine:
  report what breaks there, with *Diagnostics* → *Platform*. The tests in `backend/tests`
  are the first thing to run there.
- Memory recall uses weighted term overlap, not embeddings: inspectable, no second model to
  load, and enough for the handful of durable facts a personal agent accumulates.

---

## Ports

`3040` web · `3041` API. Fallback pair: `3042` / `3043` (`npm run dev:alt`). The dev server
listens on localhost only; `AGENT_FRONT_HOST=0.0.0.0` exposes it deliberately.
