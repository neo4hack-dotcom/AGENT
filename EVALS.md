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
