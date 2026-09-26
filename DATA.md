# Preparing data sources for AGENT

An agent pointed at a database with nothing but tool schemas spends its first calls
rediscovering the schema, then guesses what "revenue" means. The guess is the dangerous
part: a wrong metric survives every check downstream, because the arithmetic after it is
perfectly correct. Preparing a source removes the guess — and, as a side effect, most of
the exploratory calls, which is where the time goes.

This page is the method. The tooling is in **Admin → Data sources**.

## The method, in seven steps

### 1. Connect read-only

Give each MCP server a database account that can only `SELECT`. The agent never needs to
write to a source to analyse it. For Oracle and ClickHouse, put
[sqlguard](../sqlguard) in front: it validates and bounds every query — as defence in
depth behind the read-only account, not instead of it.

### 2. One server per business domain, named for people

`Sales DB`, `CRM`, `Finance` — not `sqlite2`. The name and the slug are how the agent
decides where to look. Two servers of the same kind are fine: each is listed with the
path or database it is configured for.

### 3. Fix data quality at the source, with views

The cheapest accuracy there is. If some rows are double imports, if test customers live
next to real ones, if one column mixes currencies — decide once, in a view, and point
everything at the view:

```sql
CREATE VIEW orders_clean AS
SELECT MIN(id) AS id, customer_id, order_date, amount_eur, status, channel
FROM orders
GROUP BY customer_id, order_date, amount_eur, status, channel;  -- double imports count once
```

Without the view, the agent sees "6 rows are duplicated" in the notes and either ignores
it or deduplicates on its own — silently, and not always the same way twice. With it,
every answer applies the same decision, and the raw table stays available for audits.

Also worth a view: one row per business grain (an order, not an order line, if that is
what people count), ISO dates, one currency, enumerations spelled consistently, and
aggregates for tables too large to scan.

### 4. Profile

**Profile** measures the structure from the data itself, through the server's own tools
and read-only SELECTs: every table and view, row counts, column types, the values each
low-cardinality column takes (`status ∈ {shipped, pending, refunded, cancelled}`), date and
numeric ranges, joins by naming convention, and data-quality findings — orphan foreign
keys, rows identical but for their id, negative amounts, empty dimension columns.

A ten-table database profiles in a few dozen queries and well under a second locally.
Re-profile after the data changes: measured findings are replaced, never accumulated,
and anything a person wrote is kept.

### 5. Describe the source

Three or four sentences in plain words, in the **Description** field:

- what it holds, and its time coverage;
- which questions it answers;
- what it cannot answer;
- how it joins to your other sources — the key, and which source holds what.

This is how the agent picks the right source, and how it knows a question needs two.

### 6. Write the model: metrics, caveats, checked questions

The **Model** is YAML. Profile fills `tables` and `joins`; the rest is judgement, and it is
yours:

```yaml
metrics:
  - name: revenue
    synonyms: [CA, chiffre d'affaires, sales, ventes]
    definition: SUM(orders_clean.amount_eur) WHERE orders_clean.status = 'shipped'
    description: Revenue recognised on shipped orders, EUR, VAT included.
caveats:
  - Refunded orders are outside revenue already — never subtract refunds from revenue.
  - Data ends on 2026-07-01.
verified_queries:
  - question: Revenue by region
    sql: SELECT c.region, SUM(o.amount_eur) AS revenue FROM orders_clean o
         JOIN customers c ON c.id = o.customer_id WHERE o.status = 'shipped' GROUP BY c.region
```

- **Metrics** — the exact formula *with its filter*, and the words people actually type
  for it, in every language they type it in. A question that uses a synonym gets the
  definition repeated next to it, and every query computing it must apply the filter.
- **Caveats** — decisions, not just warnings: "negative amounts are credit notes and
  stay", "customer 999 counts in totals but in no breakdown".
- **Checked questions** — three to six real questions with SQL that runs. They are shown
  to the agent as worked examples when a question resembles them.

**Draft with AI** proposes all three from the profile and sample rows. Every query it
proposes is executed first; the ones that fail are dropped. The draft fills the editor and
waits for **Save** — read the metrics before saving. In testing, a draft proposed "net
revenue = shipped − refunds", which double-counts, while its own caveat two lines further
down explained why. A wrong definition written here is repeated in every answer.

### 7. Check readiness, then ask three real questions

The five readiness marks — description, profiled, metrics, caveats, two checked questions
— each prevent a specific failure. Then ask the agent three questions you know the answers
to, and open the folded work under each answer: the queries should use your view, your
filters, and few calls.

## Services made of tools, not tables

Market data, reference data, a risk engine: most of a bank's MCP estate answers through
predefined tools rather than SQL. There is nothing to profile, and the description is
what matters — the one place the agent learns that bond prices are clean and in % of par,
that FX is fixed as EURxxx, that VaR exists at month ends only, that `book` means
`RAT-EUR` and not "Rates".

- **Draft with AI** writes that description from the tool schemas, from what the agent
  has already seen the tools return, and from the tools that are safe to call unprompted
  (read-only by the server's own declaration, no arguments). Read it, correct it, save.
- **Observed by the agent** lists every tool: how often it worked, the fields it returns,
  when it was last seen — and the tools never explored. This is the atlas (below).
- **What the agent learned** lists notes the agent proposed: conventions and pitfalls it
  had to investigate. Confirm the true ones — they then reach every question; discard the
  rest. Unconfirmed notes are only offered inside `source_info`, marked as such.

## The atlas: what the agent remembers between questions

Every MCP call teaches something about the tool: its fields, their kinds and units, the
arguments that worked, the errors. The atlas keeps that across conversations, so the tenth
question about market data does not start by rediscovering it. Three rules keep a cache of
past answers from narrowing future ones:

- **Coverage is explicit.** The source map shows, per source, which tools were never seen
  working. "Not in the atlas" never reads as "not in the source", and the agent is told to
  check unexplored tools before concluding that data does not exist.
- **Declared and observed stay separate**, each labelled as what it is.
- **A changed tool is re-checked.** Observations carry a fingerprint of the tool's schema;
  when the server changes it, what was learned about the old one stops being trusted.

Only structure is kept — never text from a reply (see SECURITY.md). **Forget** clears a
source's observations; the agent relearns from use.

## Twenty servers at once

The prompt carries a *source map*: every source, its tools with the first words of their
descriptions, the fields they were seen returning, what is unexplored. Past the tool
budget, one call to the fast model routes each question to the sources it needs — by
meaning, so a job "position" in HR or an office "desk" in facilities is not taken for a
trading one — and proposes a route when several sources must be crossed. Only those
sources' tools are offered in full; `find_tools('<source>')` brings in any other. The
routing, its reason and the route are in the folded work of every answer.

## Model reference

```yaml
tables:                     # measured by Profile; add descriptions by hand
  - name: orders_clean
    description: …
    grain: one row per order
    columns:
      - name: status
        description: …
        values: [shipped, pending, refunded, cancelled]   # measured
        range: [a, b]                                      # measured
        references: customers.id                           # measured or written
joins: [orders_clean.customer_id = customers.id]
metrics: [{name, synonyms, definition, description}]
caveats: [text, …]
verified_queries: [{question, sql}, …]
```

Unknown sections are reported and ignored. A metric without a definition, or a checked
question without SQL, is refused with the reason. Whatever validates reaches the agent;
the rest is kept as typed, so a half-finished edit is never lost.

## What the agent does with it

- The description and the model sit under the server in the tool catalogue — in the
  cacheable part of the prompt, paid once per conversation. A large model is summarised
  there, and `source_info` returns the whole of it in one call.
- Metrics the question names, by name or synonym, are repeated next to the question with
  their exact definition.
- Checked queries that resemble the question are offered as examples.
- A query that aggregates a whole table with no filter comes back with a note saying so; a
  name that matches several records comes back with a note to ask which.

## Measuring it

`evals/` holds the testbed used to build this: two databases behind two MCP servers, three
workspace files, planted defects, and twelve analyst questions graded against ground truth.

```bash
python evals/testbed.py                       # data + ground truth
python evals/setup_sources.py                 # prepared sources (--bare: none)
python evals/run.py --label prepared          # 12 scenarios, graded
python evals/report.py bare prepared          # side by side
```

`evals/finance/` is the CIB testbed: market data, reference data and risk as tool servers,
a versioned trade store over SQL, and eight look-alike corporate servers (HR "positions",
facilities "desks", procurement "ratings" and "limits") — eighteen servers and ninety tools
with the sales and CRM databases. Ten questions cross them: exposure by rating in EUR, VaR
limit usage, counterparties by notional at trade-date FX, a price on a TARGET holiday, an
ambiguous entity, a leading question.

```bash
python evals/finance/data.py                              # the world
python evals/finance/setup.py --base URL --estate         # connect it
python evals/finance/run.py --base URL --label cib         # 10 scenarios, graded
```
