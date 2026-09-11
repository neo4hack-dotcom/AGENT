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
