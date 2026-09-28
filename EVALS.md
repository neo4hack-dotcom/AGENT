# Cross-server evaluation

Ten tasks, each requiring the agent to combine **two or three different MCP servers** plus
its own tools, run against a real testbed: a SQLite shop database (5 customers, 15 orders),
a git repository with three commits, and a documents folder with a pricing policy and a VAT
table. Every case has a ground truth that can be checked to the cent.

The point was never the score. It was to find out *how* the agent fails when several
servers are in play, and to fix the causes rather than the symptoms.

## The cases

| # | Combines | Task |
|---|---|---|
| C01 | SQLite + Filesystem | Row counts per table → write a summary file |
| C02 | Git + Filesystem | Three commit messages → append to a changelog |
| C03 | SQLite + Filesystem + compute | Revenue per region, VAT applied from a CSV |
| C04 | Git + SQLite | Compare commit count against order count |
| C05 | Time + Memory graph + Filesystem | Two timezones, store an entity, write a note |
| C06 | Filesystem + SQLite | Apply a rule written in prose to database rows |
| C07 | Git + Filesystem | Do the VAT rates in the code match the CSV? |
| C08 | Memory graph | Build a small graph, then read it back |
| C09 | Web Fetch + Filesystem | Fetch a page, save its text, report the title |
| C10 | Git + SQLite + Filesystem | A status report combining all three |

## What the first run revealed

Fourteen failed tool calls across seven cases, in four clean classes:

1. **`git_log(repo_path=".")`**, three times. A server configured with a root — a git
   repository, a database file, a directory — knows its scope; the model does not, because
   that scope lives in the server's command line and appears in no tool schema. So it
   guessed.
2. **Built-in `read_file`/`list_files` used for paths belonging to a connected server.**
   The names were generic and the error was a dead end: *"docs does not exist in the
   workspace."*
3. **Five consecutive schema-validation failures on one memory server**, each error naming
   one missing field, each fix producing the next complaint.
4. **A plan left at 0/N** while the work was visibly done.

Two deeper defects surfaced in the artefacts rather than in the logs, and they matter more
than all of the above:

- A file written as `#` followed by sixteen non-breaking spaces and `...` — the model had
  **abbreviated its own content inside a tool argument** — while the answer displayed the
  full table as though it had been written.
- A status report described in the answer, titled with its filename, that **no write tool
  had ever been called to produce**.

## What changed

| Fix | Class it removes |
|---|---|
| Each server's configured root is stated in the prompt, as a fact and nothing more | 1 |
| Built-in file tools renamed `workspace_read/write/list`, and their errors name the servers that cover other roots | 2 |
| A validation failure returns the tool's actual JSON Schema | 3 |
| A missing-path failure returns the server's own listing tool and says not to guess again | 1, 2 |
| Any tool argument ending in a spaced ellipsis is refused before dispatch | elision |
| The reminder injected after every tool batch says to report only what the results prove | fabricated artefacts |
| "Every arithmetic operation on a number from a tool goes through `run_python`" — a bright line, replacing "beyond trivial mental math" | a €1 error in C03 |
| A plan the agent stopped updating says so, instead of showing 0/N | 4 |
| The run's wall clock pauses while a human decides on an approval | a 631 s case |
| An approval that expired is reported as expired, never as refused | honesty |

## Result

Both columns are a full ten-case run, the first on the original code and the second on the
code as it stands:

| | Before | After |
|---|---|---|
| Tool calls across the ten cases | 86 | 56 |
| Failed calls | 14 | 8 |
| Cases with no failure at all | 3 | 4 |
| Input tokens | 637 k | 380 k |
| Wall clock | 300 s | 274 s |
| Artefacts written correctly | 3 of 5 | 5 of 5 |
| Answers matching ground truth | 9/10 | 9/10 |

The score did not move, and that is the honest reading: the agent was already getting the
right answer most of the time. What changed is how much it had to spend to get there — a
third fewer calls, 40 % fewer input tokens — and, far more importantly, **what it says about
work it did not do.**

Before, one run wrote a file containing `# ` and an ellipsis and then displayed the full
table as though it had written it; another titled its answer with the name of a file no tool
had created. Neither happens now. In the final run every artefact exists with the right
content, and the single case that misses its ground truth (C07) misses it by saying *"the
repository contains no CSV to compare against"* — which is true. Across every run, including
the ones where it could not find a file, the agent said so. It never once invented the VAT
rates it certainly knows by heart.

## The one that mattered most, found later

Adding a bundled pandas server created a directory boundary — the server can only open
files inside its own workspace — and with it a step that had not existed before: getting a
file *across*. Asked to analyse a six-row CSV that lived elsewhere, the agent read it,
retyped it through a `content` argument, and **filled in fifteen years of data that were
never in the file**, in perfectly regular increments of 50 000. It then ran genuine pandas
on the invented file, reported the results as fact, and cited the expressions it had run.

Every guard held. The pandas run was real, the audit log was clean, the numbers were
internally consistent — because everything after the copy *was* real. The fabrication
happened at the one step nothing was watching: data passing back out through a tool
argument, where a language model can complete it, tidy it, or extend the trend.

The fix is not a better check. It is removing the step: `workspace_import` copies bytes,
so the model names a source and a destination and the contents never pass through it at
all. The source must sit inside a directory the user has already granted to a connected
server — the user's own grant honoured, not a new privilege. The same task now runs in
seven calls instead of thirteen, and the imported file is byte-for-byte identical to the
original.

The general rule this leaves behind: **anywhere data leaves a tool and re-enters one
through the model, treat it as lost.** Copy it, or regenerate it with code — never retype
it.

## What the remaining failures are

Almost all of them are one thing: the Filesystem server is rooted at `/tmp/agent-testbed`
and the Git server at `/tmp/agent-testbed/repo`, so a task that says "write it to
`docs/CHANGELOG.md`" is **genuinely ambiguous** — both roots could have a `docs/`. The agent
picks one, says which, and is right about what it did. That ambiguity is in the task as much
as in the agent, and it is worth knowing that overlapping server roots produce it.

The rest are a memory server whose schema is strict enough to cost one corrected call, and
one `fetch` call rejected for asking for more bytes than the server allows — both surfaced
with the server's own message, both recovered from on the next turn.

## Reproducing

`/tmp/agent-testbed` holds the SQLite database, the git repository and the documents; the
harness runs each case in a fresh conversation and records the full event stream. Results
vary run to run — the model is sampled, not deterministic — so the mechanics (calls, failed
calls, artefacts, fabrications) are the signal, not any single score. Numbers here are from
one full run of each, not a best-of.

---

# CIB campaign: eighteen servers, ten questions

`evals/finance/` builds a small investment-banking world and connects it next to the rest of
an estate: market data, reference data and risk as tool servers (bond prices in % of par,
FX fixed EURxxx, no fixing on TARGET holidays, VaR at month ends only), a versioned trade
store over SQL (amendments, cancellations), eight look-alike corporate servers (HR
"positions", facilities "desks", procurement "ratings" and "limits"), plus files,
dataframes, memory, time, sales and CRM — 18 servers, 89 tools. Ten graded questions cross
them. Model: `gpt-oss:120b-cloud` for both lanes; runs that hit a model-host 5xx are rerun.

| Pass | What changed | Passed |
|---|---|---|
| Baseline | tool names in the prompt, lexical tool selection | 6/10 |
| Routed | source map, routing by meaning, atlas, notes on empty results | 6/10 |
| + fixes | batch_call, tolerant rows(), evidence digest, Critic for data traps, read fence | 6/10 |
| Prepared | the four markets sources drafted with AI and saved unread | **8/10** |

What moved, and why:
- **Distractors never chosen**, in any pass: job vacancies, office desks, supplier ratings
  and spending limits stayed out of every markets answer.
- **F1** (market value of the Credit desk's BBB+-or-lower bonds, in EUR) failed every bare
  pass and passed prepared, to the euro: the drafted descriptions say what `list_instruments`
  returns and that bond prices are in % of par, which is most of the question.
- **F5** (DV01 of the Rates desk) failed the baseline — `book="Rates"` returned nothing and
  the answer said the data did not exist. The empty-result note, with the book ids already
  observed, fixed it for good.
- **F7** (EQC-EU equities down >10 %) went from 78 s and 24 calls to 24 s, once `batch_call`
  fetched every price in one call.
- **F4** (a close on 1 May) exposed a reasoning error — the 30 April close presented as the
  1 May close — now a rule: a date with no value is an answer.
- **F3** computes the right figures to the cent but still names counterparties by id in
  some runs; **F8** (Société Générale: counterparty, issuer and share) still covers one role
  without saying so. Both are prompt-level; both remain open.

Found on the way, and fixed: run events stopped reaching the client once a long run
trimmed its buffer (sequence numbers went backwards; the UI waited forever); `run_python`
results were read as a one-row table of `elapsed_ms`; the model once opened the trade
store's SQLite file directly from code — reads outside the workspace are now refused by
the sandbox.

```bash
python evals/finance/data.py
python evals/finance/setup.py --base http://localhost:3045 --estate
python evals/finance/run.py --base http://localhost:3045 --label cib
python evals/finance/prepare.py --base http://localhost:3045     # then run again
```

---

# Data-mining campaign: ten analyst questions on the CIB world

The world now has what data mining needs: execution prices on trades with five executed
far from the close, a trade booked on a TARGET holiday, a counterparty id missing from
reference data, a fat-finger quantity, sector-correlated equities (French banks, European
tech), traders with different amendment rates. Ten questions (`--suite mining`), each
graded against ground truth *and* required to cite its evidence:

| | Question | Needs |
|---|---|---|
| M1 | trades executed > 3 % away from the day's close | versions, enrichment of 375 rows, a threshold |
| M2 | data-quality audit | holiday, orphan counterparty, fat finger |
| M3 | most correlated pair of shares | returns, a correlation matrix |
| M4 | shares ranked by annualised volatility, charted | whole universe, a chart |
| M5 | trader with the highest amendment/cancellation rate | count trades, not versions |
| M6 | top-3 issuer concentration of long bond nominal in EUR | FX direction (GBP ÷ EURGBP) |
| M7 | monthly trades per desk, charted | latest version, then status |
| M8 | counterparty segmentation in 3 groups | members named |
| M9 | Excel extract of the off-market trades | a file with its provenance sheet |
| M10 | +50 bp on the Rates desk, with method | DV01, stated assumptions |

| Pass | What changed before it | Mining | CIB (bare sources) |
|---|---|---|---|
| 1 | — | 4/10 | — |
| 2 | prompt rules (case, versions, members, dates, ask), deliverable check | 4/10 | — |
| 3 | SQL filter-value probe, `batch_call rows_from`, `profile_data`, valid parked JSON, ISIN names | 5/10 | — |
| 4 | flagged results blocked, version counts, FX/par conventions, router knows the built-ins | 4/10 | 6/10 |
| 5 | deterministic names, intent checks (quality → profile, segmentation → members), no routing to dataframe servers, today's-date note | **8/10** | **8/10** |
| 6 | (interrupted: the hosted model's monthly usage limit was reached) | — | — |

What the failures taught, in the order they were fixed:

- **A filter in the wrong case is silent.** `status <> 'cancelled'` against 'CANCELLED'
  excludes nothing; two different questions counted 392 trades instead of 375. Each quoted
  filter value is now checked against the source, and a result found wrong cannot feed a
  chart, an export or a computation — after the first fix, the agent re-ran the right
  query and then charted the wrong one.
- **Filtering before taking the latest version** resurrects cancelled trades; **COUNT(*)
  on a versioned table** counts versions (29.5 % instead of 41.9 %). Both are flagged.
- **Enriching every row by hand fails.** Asked for the close of 375 trade dates, the model
  typed 27 argument sets. `batch_call(rows_from='#N', arguments={param: column})` builds
  the calls from the rows; M1 then found all five off-market trades.
- **Data-quality reviews need the measurements done for them.** Improvised pandas found
  one defect in three; `profile_data` finds all three on its own.
- **Runner notes were corrupting parked JSON** (a .txt the model could not parse), and
  **the stdio reader died on any reply over 64 KB**, leaving the call to time out.
- **ISIN instead of names** survived every prompt rule; a deterministic "Name (ISIN)" at
  release, and name labels on chart axes, ended it.
- **"The day's close" read as today** — twice, then as "the last day in the data". M1
  tests that reading; M9's wording now states the trade date, so it tests the export.

Still open: F3 (right figures, names out of order or missing), F8 (a counterparty that is
also an issuer and a share, answered in one role, once with an absurd magnitude).

```bash
python evals/finance/run.py --suite mining --label mining   # or --suite all
```

# PDF reports with a local 4B model

Can a reader simply *ask* for a PDF — findings, a chart, the table — and get one, with the
model running on the laptop? Tested on the CIB world (eighteen servers) with `qwen3.5:4b`
through Ollama, a 16k window, the model partly on CPU: every call one to two minutes, so
each failure below cost real time before it was found.

| Step | What happened | What changed |
|---|---|---|
| 1 | cut off three tokens into its fourth turn; answered from a digest that showed a truncated schema ("`versio` is cut off") | the window budget counts instructions, source notes and schemas (10.8k of 16k here), keeps a reserve for the model's turn, squeezes the prompt when it does not fit, and retries a cut-off turn once in the smaller prompt |
| 2 | the chart refused by the air gap for `"$schema": "https://vega.github.io/…"` | render-only tools (`chart`, `create_report`, exports…) take URLs as data |
| 3 | arguments written inside the tool's name, `get_var({…})</parameter` → unknown tool ×3 | calls repaired in both providers |
| 4 | a spec wrapped in `{"title", "spec": {…}}`, then invalid JSON reported as "must be a Vega-Lite object" | wrapper unwrapped; invalid JSON reported as such, with the position |
| 5 | `print(rows)` in `run_python` — a Python literal — "did not return a table" | read with `ast.literal_eval` |
| 6 | `data={"name": "#7", "format": …}`, Vega-Lite's own shape | the reference inside is used |
| 7 | a report saying "the chart below" over an empty page: the chart named `chart_id`, no table | section aliases; a report missing the chart or table the reader asked for is refused with the ids and refs that exist; when the only possible table is the chart's own data, it is attached |
| 8 | **a two-page French PDF**: bar chart, the four desks' VaR, limit and usage, sources, and the Python that computed them | — |
| 9 | asked again in English, the model named the table `#1` — last answer's computation — and got "call #1 failed": `#1` of *this* question was the report call itself | evidence labels are numbered across the conversation; a running call is never a data source |
| 10 | **an English PDF on the first attempt** — whose summary said "Credit … 89.3 %", a figure found nowhere | figures in a report's prose are checked against every number the results hold; an unfound one sends the report back, and is marked *Check before use* if it survives two refusals |
| 11 | asked again in French: a report with neither chart nor table (refused once, then right), but its chart summed the four usage rates into one 251 % bar | the chart this run drew is placed when a report omits it; summed rates and rows collapsed into one mark are refused (1 of 13 stored charts flagged — that one) |

The tables and charts were right every time; the prose was not always — once "Equity
Derivatives above 70 %" at 66 % (a wrong comparison, which no figure check can see), once
an invented 89.3 % (which step 10 now catches). The **PDF** button under an answer builds the same kind of
document from the stored run without asking the model anything, which is the dependable
path on hardware like this.

# The CIB analyst on a laptop: four iterations with a local 4B model

Six cases of the CIB bench — F2 (VaR vs limit at a month-end), F4 (a close on a holiday),
F5 / M10 (DV01, and a +50 bp what-if), M5 (amendment rate by trader), M7 (monthly activity
charted), M9 (an Excel extract of off-market trades) — run with `qwen3.5:4b` on an 8 GB
laptop, 18 servers connected, the model partly on CPU: one to three minutes per model
turn. Each iteration ran on the fixes of the one before; every failure was read call by
call before anything was changed.

| Case | Baseline | It. 2 | It. 3 | It. 4 | It. 5 |
|---|---|---|---|---|---|
| F2 VaR vs limit, "fin mai" | ✗ corrected call blocked by the loop guard | ✓ | ✗ limits retyped wrong in code | ✓ 528 s, 4 calls | — |
| F4 close on 1 May (holiday) | — | — | ✗ "1 May 2026 is a Sunday" | ✗ holiday found; `instrument_id` for `identifier` | ✗ holiday found; critic asked for the holiday's own close |
| F5 DV01 by desk | ✓ | ✓ | — | — | — |
| M5 amendment rate by trader | ✗ wrong rate | ✗ gave up after the schema | ✗ fix written in PostgreSQL | ✗ **41.9 % right**, no citation | ✗ 32.9 % (wrong denominator) — flagged *6 figures unverified* |
| M7 monthly chart | ✓ | ✗ no query tool offered | ✓ | ✗ right counts, spec not JSON | ✗ chart drawn, versions counted (432) |
| M9 Excel extract (375 lookups) | ✗ run failed on one slow turn | ✗ parked result re-read 9× | ✗ history tool out of reach of code | ✗ 367 prices fetched, time ran out | — |
| M10 +50 bp what-if | ✗ invented DV01, gave up | ✓ | ✓ (538 s, from 1 220) | ✓ 552 s | — |

Read case by case rather than as a score: every failure of iterations 1–3 was the app's —
a guard, a timeout, a lost tool, a re-parked file — and each was removed. By iterations 4–5
the failures are the model's own reasoning: a denominator, versions counted as trades, a
date asked for after the right one was known. Those are what the checks make *visible*:
the wrong M5 rate shipped with "6 of 13 figures not found in the results" under it and the
figures underlined in amber. A 4B model on a laptop gets F2, F5, M10 right in under ten
minutes each and M5 right on a good run; M9 (375 lookups joined and exported) remains
beyond its time budget here.

Fixes after iteration 4, measured in iteration 5 where they applied: a misnamed argument
is renamed and retried (F4 — then the critic asked for the holiday's own close, so the
critic now gets the computed date facts, and a failed critic step gives the agent one
more turn instead of composing); an answer with figures and no citation gets a *Sources*
line from the trace (M5); a broken chart spec is salvaged or replaced by the rows' own
chart (M7 — drawn in iteration 5).

What each failure taught, and what changed:

- **The loop guard took a tool's "no" for an outage.** "VaR is computed at month ends
  only: …, 2026-05-29" three times made the guard refuse the corrected call with the right
  date. Only timeouts, disconnections, 5xx and rate limits count now.
- **Routing timed out silently.** 25 s suited a hosted model; the local one needs minutes,
  so every question fell back to all eighteen sources described in full. Routing now gets a
  quarter of the model timeout, and says when it is skipped. With routing back, the prompt
  fell from 11.1k to 8.8k tokens and M10 from 20 to 9 minutes.
- **One slow turn failed a 54-minute run** and published the model's plan as the answer:
  a turn that times out is now retried lighter, then the answer is composed from what was
  gathered.
- **Mental arithmetic, then invented inputs.** The figure check sent F2's first draft back
  (ratios computed in the model's head) and the model computed them in `run_python` —
  then, in the next iteration, typed a wrong limit into that code. Data-like numbers in
  code that no result holds are now refused before it runs.
- **Looking is not fetching.** Twice a run described tables and answered that the data was
  "not accessible". Such a draft is sent back naming the query tool; a described source
  keeps its query tools on offer; the give-up detector reads "je n'ai pas les
  informations" and "ne sont pas accessibles".
- **A parked result read back was parked again** — nine copies of the same 96 KB. It is now
  described (rows, columns, first rows) with the ways to use it outside the context.
- **Dates and dialects.** Weekdays and holidays of the dates a question names are computed
  into the prompt; SQL syntax errors come back with the fix in the source's dialect.
- **The schema digest cut a column list in half** ("only the field `versio` is visible");
  exploration steps are now summarised as column lists.

The deterministic parts were checked apart from the model: calendars against known
dates (Easter, substitute holidays, the risk server's own month-end list), the figure
check against 93 stored answers (17 flagged — mentally summed totals among them), the
typed-data guard against the code that failed F2, Excel formats, and the UI features in
the browser.
