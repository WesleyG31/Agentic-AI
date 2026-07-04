# Case 4 — NL2SQL Data-Analyst Agent (Police/SCOUT-flavored)

> A worked **[PACTEDR](../framework_PACTEDR.md)** case. Design the whole thing out loud in one 45-minute
> interview: an agent that turns a plain-English business question into **safe, schema-grounded SQL**,
> executes it read-only against a database of tens of millions of records, and hands back a number, a
> chart, and its own audit trail — pausing for a human only when the query touches sensitive personal data.

This is the case I reach for when the interviewer says *"walk me through an agentic system you'd actually
ship."* It is the closest of the four cases to my real production work: a **natural-language analytics
layer over ~40M structured records**. Here I skin it as **SCOUT**, a fictional public-safety
records/CAD (computer-aided-dispatch) analytics platform, because the police domain makes the hard parts
unavoidable — millions of rows, heavy PII, GDPR-grade access governance, and stakeholders (crime analysts,
duty commanders) who need answers in seconds, not a ticket to the BI team. Swap "incidents" for
"transactions" or "support tickets" and the same design ships for any enterprise.

---

## Scenario at a glance

| | |
|---|---|
| **Who asks** | Crime analysts, duty commanders, oversight/compliance officers — non-SQL users. |
| **What they ask** | *"Weekly residential burglaries in District 5, Q2 this year vs last year."* *"Median dispatch-to-arrival time by district for priority-1 calls last month."* |
| **Data** | `SCOUT` OLTP read-replica: **~40M incident rows**, 8 years of history, partitioned by year. Tables include heavy PII (`persons`, `arrests`, `officers`). |
| **Today (baseline)** | Analyst files a request → BI queue → SQL written by hand → validated → charted. **~30–45 min** per ad-hoc query, 1–2 day backlog. |
| **With Kompass** | Question → grounded SQL → guardrail-checked → executed read-only → chart + narrative. **~20–40 s**, self-serve, fully audited. |
| **Risky action** | Not *writing* (it is read-only) but **reading/exporting person-level PII**. That — and only that — is what pauses for human approval. |
| **Headline metric** | Analyst-hours saved + query **execution accuracy**, not "the demo looked smart." |

Inside Kompass this is the **Data Analyst worker** (a subgraph) that the
[Retrieval Router](../../docs/02_retrieval_strategies.md) selects whenever a question is *quantitative and
aggregate over structured data* rather than narrative over documents.

> **Interview soundbite:** *"The insight is that NL2SQL is a retrieval strategy, not a chatbot feature. The
> router picks it when the answer lives in rows and columns; RAG is for prose. Kompass treats 'translate to
> SQL and execute' as just another way to ground the model in truth."*

---

## P — Problem & Business Value

**The problem is throughput, not capability.** A senior analyst *can* answer any of these questions — but
each one costs 30–45 minutes of find-the-table, write-the-join, validate, chart, and there is a standing
backlog. The BI team is the bottleneck; commanders make decisions on stale numbers or gut feel.

Frame the value in the metric the business already tracks — **analyst-hours** — plus the one the model owns
— **query accuracy**:

| Metric | Baseline (human/BI queue) | Kompass target | Basis |
|---|---|---|---|
| Time per ad-hoc query | 30–45 min (+ backlog) | **20–40 s** self-serve | wall-clock, p50 |
| Self-serve deflection | 0% (all human) | **~70%** answered with no BI touch | eval + shadow traffic |
| Query **execution accuracy** | ~100% (but slow) | **≥90%** on golden set; **~85%** on hard multi-join | vs curated gold SQL |
| Human touch on remaining 30% | 30 min | **~5 min** (verify/approve SQL) | HITL card review |
| Unsafe PII exposure | policy + trust | **0** (HITL-gated, denylist) | audit log |

**Back-of-envelope.** 6 analysts × ~12 ad-hoc queries/day × 30 min = **36 analyst-hours/day** spent on
query mechanics. Deflect 70% and cut the rest from 30→5 min ≈ **~21 hours/day saved ≈ 2.5 FTE**, at an LLM
cost of roughly **$0.02–0.08 per query** (model-routed — see D). The rounding error on one analyst's salary
pays for the whole system.

> **Interview soundbite:** *"I never lead with tokens or latency. I lead with 'this frees ~2.5 analyst FTE
> and takes commanders from a two-day backlog to twenty seconds — at eight cents a question.' Accuracy is
> the guardrail on that number, not the headline."*

This maps directly to my real experience: an NL2SQL layer over **~40M records** where the win was exactly
this — collapsing the analyst→BI→SQL loop while keeping a hard accuracy floor and strict access control.

---

## A — Architecture

The Data Analyst is a **LangGraph v1.0 subgraph** with its own state, invoked by the Kompass supervisor.
It is a small plan-execute-reflect loop, not a single prompt.

```
            natural-language question ("weekly burglaries, D5, Q2 y/y")
                              │
                              ▼
                    ┌──────────────────┐
                    │  intake / clarify │  ambiguous? → ask 1 follow-up (bounded)
                    └────────┬─────────┘
                             ▼
        ┌──────────────────────────────────────────────┐
        │  SCHEMA-LINKING (retrieval)                    │  ← NL2SQL *is* retrieval
        │  • embed question → rank tables/columns        │    (see docs/02)
        │  • pull column descriptions + sample values    │
        │  • retrieve k similar (question, gold-SQL) pairs│
        └────────┬───────────────────────────────────────┘
                 ▼
        ┌──────────────────┐   grounded prompt (only real tables/cols + few-shots)
        │  SQL GENERATION   │   typed output: {sql, tables_used, touches_pii}  (Pydantic)
        │  (reasoning model)│
        └────────┬─────────┘
                 ▼
        ┌──────────────────────────────┐   parse AST (sqlglot) → REJECT if not read-only
        │  STATIC GUARDRAILS            │   force LIMIT · denylist PII cols · EXPLAIN cost cap
        └────────┬─────────────────────┘
                 ▼
        ┌──────────────────────────────┐
        │  HITL GATE (conditional)      │  touches_pii OR cost > τ → interrupt_on
        │  approve / edit / reject      │  (durable; resumes via checkpointer)
        └────────┬─────────────────────┘
                 ▼
        ┌──────────────────────────────┐   read-only role · statement_timeout · row cap
        │  SAFE EXECUTION (MCP sql srv) │   against read-replica
        └────────┬─────────────────────┘
                 ▼   error? → REPAIR LOOP (feed error back, max 2 retries) ──┐
        ┌──────────────────────────────┐                                    │
        │  CRITIC / result sanity check │  empty? implausible? re-plan ──────┘
        └────────┬─────────────────────┘
                 ▼
        ┌──────────────────────────────┐   render chart in locked-down SANDBOX (see docs/06)
        │  CHART + NARRATIVE            │   NL summary + numbers + provenance
        └────────┬─────────────────────┘
                 ▼
          typed AnalysisResult { answer, sql, chart_png, rows, tables_used }
```

State (LangGraph `TypedDict`) carries: the question, the linked schema slice, few-shot exemplars, the
candidate SQL, guardrail verdict, execution result / error, retry count, and the human decision. The
checkpointer (SQLite locally, **Postgres** in prod — see [config](../../docs/05_architecture.md)) makes the
whole thing **durable and resumable**, which is what lets the HITL pause survive a process restart.

```python
# illustrative LangGraph v1.0 subgraph (not the full source)
from langgraph.graph import StateGraph
from langgraph.types import interrupt, Command

def generate_sql(state: AnalystState) -> AnalystState:
    ctx = state["schema_slice"] + state["few_shots"]        # grounded context only
    out = reasoner.with_structured_output(SqlPlan).invoke(prompt(ctx, state["question"]))
    return {"sql": out.sql, "tables_used": out.tables_used, "touches_pii": out.touches_pii}

def hitl_gate(state: AnalystState) -> AnalystState:
    if state["touches_pii"] or state["est_cost"] > COST_TAU:
        decision = interrupt({                              # dynamic pause, durable
            "action": "execute_sql", "sql": state["sql"],
            "reason": "query reads person-level PII",
        })
        if decision["type"] == "reject": return {"status": "rejected"}
        if decision["type"] == "edit":   return {"sql": decision["edited_sql"]}
    return {}                                               # approve / low-risk → fall through

g = StateGraph(AnalystState)
# ... add_node for clarify, link_schema, generate_sql, guardrails, hitl_gate, execute, critic, chart ...
graph = g.compile(checkpointer=postgres_checkpointer)
```

In LangGraph v1.0 the same pause can be declared instead of coded, via the **HITL middleware** — attach
`interrupt_on={"execute_sql": {"allowed_decisions": ["approve", "edit", "reject"]}}` to the execution tool
and the framework raises the interrupt for you. I use the **declarative middleware for the standard
approve/edit/reject gate** and drop to the **dynamic `interrupt()` primitive** only for the conditional,
cost-based pause above, where the trigger is computed at runtime. (Full treatment in
[docs/04_hitl_patterns.md](../../docs/04_hitl_patterns.md).)

---

## C — Context & Retrieval: NL2SQL and schema grounding

This is the intellectual core of the case, and where I connect it to
[retrieval strategies](../../docs/02_retrieval_strategies.md).

**Claim: NL2SQL is a retrieval strategy.** RAG retrieves *text chunks* to ground a prose answer; NL2SQL
retrieves *structured facts* by compiling the question into SQL and executing it. For "how many / trend /
median / top-N over 40M rows," RAG is hopeless — you cannot chunk your way to a `GROUP BY`. The
[adaptive router](../../docs/02_retrieval_strategies.md) sends aggregate/quantitative intents here and
narrative intents to RAG hybrid or CAG.

**The failure mode to design against is hallucinated columns.** A raw LLM will confidently write
`WHERE crime_type = 'burglary'` when the column is `offense_code` joined to `offense_codes.category`. On a
40M-row schema with dozens of tables, un-grounded generation is unusable. Two retrieval sub-steps fix it:

| Sub-step | What it retrieves | Why it matters |
|---|---|---|
| **Schema linking** | The *minimal relevant slice* of the catalog — table + column names, human descriptions, types, a few sample values — ranked by embedding similarity to the question | The model only ever sees columns that **actually exist**; the prompt stays small even though the DB is huge. Kills column hallucination. |
| **Few-shot exemplar retrieval** | k nearest `(question, gold-SQL)` pairs from a curated store | Teaches house dialect (date functions, partition columns, the canonical `incidents ⋈ offense_codes` join) by example. Biggest single accuracy lever I have seen. |

The generated SQL is a **typed Pydantic object** (`{sql, tables_used, touches_pii, rationale}`), not free
text — so downstream nodes can reason about *which* tables it hit without re-parsing.

**Worked example — the safe path (no PII):**

*"Weekly residential burglaries in District 5, Q2 2026 vs Q2 2025."* Schema linking surfaces
`incidents`, `offense_codes`, `districts`; few-shot supplies the `date_trunc`/partition idiom:

```sql
SELECT date_trunc('week', i.occurred_at) AS week,
       extract(year FROM i.occurred_at)  AS yr,
       count(*)                          AS n
FROM   incidents i
JOIN   offense_codes oc USING (offense_code)
WHERE  oc.category = 'BURGLARY' AND oc.subtype = 'RESIDENTIAL'
  AND  i.district_id = 5
  AND  i.occurred_at >= DATE '2025-04-01'
  AND  i.occurred_at <  DATE '2026-07-01'
GROUP BY 1, 2
ORDER BY 1
LIMIT 5000;                         -- injected by the guardrail layer
```

Only aggregate counts, no personal data → **no HITL pause**, straight to execution and chart.

**Worked example — the gated path (PII):** *"List the officers with the most use-of-force reports last
quarter."* Schema linking touches `officers` and `use_of_force` — both on the PII denylist. Generation sets
`touches_pii=True`, so the run **pauses** at the HITL gate before any row is read.

> **Interview soundbite:** *"I never let the model invent a column. It only ever sees a retrieved slice of
> the real schema plus a few worked examples. That single decision — schema-linking as retrieval — is what
> takes execution accuracy from the low-70s to the low-90s on my golden set."*

---

## T — Tools, Actions & Human-in-the-Loop

**Tools via MCP.** Execution is not a raw driver call; it goes through the Kompass **`sql` MCP server**
(sibling to `doc_search` and `ticketing`). The MCP boundary is where I concentrate the guardrails, so the
same protection applies no matter which agent calls it.

**Defense in depth — five layers, cheapest first:**

| Layer | Mechanism | Blocks |
|---|---|---|
| **1. Read-only role** | DB connection uses a `GRANT SELECT`-only role against a **read-replica** | Any write ever reaching prod (`INSERT/UPDATE/DELETE/DDL`) |
| **2. AST validation** | Parse with `sqlglot`; reject anything that is not a single `SELECT`; strip comments; block multi-statement | SQL injection, hidden DML, `pg_sleep`, stacked queries |
| **3. Resource caps** | Force/clamp `LIMIT`, `statement_timeout` (~15 s), `EXPLAIN` cost estimate vs threshold **before** running | Runaway `SELECT *` over 40M rows, cartesian joins |
| **4. PII policy** | Column **denylist** + masking; person-level tables flip `touches_pii` | Un-approved access to `persons`, `arrests`, `officers` (GDPR / oversight) |
| **5. HITL approval** | Declarative `interrupt_on` (approve/edit/reject) for PII/high-cost queries | The one genuinely risky action: **reading/exporting personal data** |

The reframing that lands in interviews: **for a read-only analytics agent the risky action is not a write,
it is the disclosure.** A commander pulling aggregate crime trends needs no gate; an analyst pulling
named-person records needs a data steward to approve/edit/reject the exact SQL — and that decision, plus the
SQL and the row count, is written to an immutable audit log. This is HITL as **governance**, not as a
babysitter for a flaky model.

**Sandboxed code execution for charts.** The chart is produced by generated Python (pandas + matplotlib)
run in a **locked-down sandbox** — no network, no filesystem, ephemeral, CPU/mem-capped — exactly as
described in [docs/06_advanced_patterns.md](../../docs/06_advanced_patterns.md). Generated code is never
trusted; it renders a PNG from the *already-safe* result set and returns bytes. This is the Tier-2
"sandboxed code execution" capability, scoped tightly so it can compute and plot but can never exfiltrate.

**Reflection / repair.** If execution errors (bad column, type mismatch), the DB error is fed back to the
generator for a **bounded repair loop (max 2 retries)**; a Critic node sanity-checks the result (empty set,
implausible magnitude) and can trigger a re-plan. Bounded, because an unbounded self-correction loop is a
cost and latency bomb.

> **Interview soundbite:** *"Read-only role, SELECT-only AST check, forced LIMIT, PII denylist, and a
> human approve/edit/reject gate — five layers, cheapest first. The model is the least-trusted component in
> its own pipeline, and the only thing that ever pauses for a human is disclosing personal data."*

---

## E — Evaluation

You cannot ship NL2SQL on vibes; the whole value prop is a hard accuracy floor. The eval harness lives in
[`evals/`](../../docs/05_architecture.md) and reports on a **golden set** of `(question, gold-SQL, expected
result)` triples — realistic SCOUT questions, tiered easy → hard multi-join.

| Metric | What it measures | Why not just one |
|---|---|---|
| **Execution accuracy** | Does the *result set* match gold (order-insensitive)? | The metric that matters — two different SQL strings can be equally correct. |
| **Exact/component match** | Structural overlap with gold SQL | Diagnoses *why* execution failed (missing join vs wrong filter). |
| **Valid-SQL rate** | Fraction that parses + runs without error | Catches regressions in schema linking. |
| **PII-gate recall** | Fraction of PII-touching queries correctly gated | A **safety** metric — a single miss is a compliance incident. |
| **Latency p50/p95 & $/query** | Wall-clock and cost | Guards the business case from a "smart but slow/expensive" regression. |

I benchmark against the public **Spider / BIRD** NL2SQL literature to set expectations (raw LLMs land in the
~50–70% execution-accuracy range on hard schemas; schema-linking + few-shot retrieval is what pushes into
the high-80s/90s). Targets: **≥90% execution accuracy** overall, **~85%** on the hard multi-join tier, and
**100% PII-gate recall** (non-negotiable). The eval also runs the **baseline** (naïve prompt, no schema
linking) so the README table can show the delta, not just the absolute number.

> **Interview soundbite:** *"Exact-match SQL is a trap — I score on execution accuracy, because two
> different queries can both be right. And I carry a separate PII-gate recall metric: 90% accuracy is a good
> day, but 99% gate recall is a bad day, because the miss is a compliance breach."*

---

## D — Deployment & MLOps

| Concern | Choice | Rationale |
|---|---|---|
| **Model routing** | `haiku` for schema-linking + trivial lookups, `sonnet` for standard queries, `opus` for hard multi-join / repair | Most queries are easy; paying Opus rates for all of them is how you blow the $/query budget. |
| **Data plane** | Dedicated **read-replica**, partitioned by year, indexes on `(district_id, occurred_at)` | Analytics never touches the OLTP primary; read-only is enforced at the role *and* the topology. |
| **Durability** | **Postgres checkpointer** (SQLite locally) | HITL pauses survive restarts; a PII approval can sit for hours and resume cleanly via `Command(resume=...)`. |
| **Observability** | **Langfuse** traces: question → schema slice → SQL → guardrail verdict → rows → decision | Every answer is fully reconstructable — essential for an auditable, PII-touching system. |
| **Provenance** | Result object always carries the exact SQL, `tables_used`, and row count | Analysts trust a number only if they can see the query behind it. |
| **Serving** | FastAPI `POST /chat` + `POST /resume`; Streamlit HITL card; Dockerized; CI in `.github/workflows` | Same serving surface as the rest of Kompass. |

Prompts and few-shot exemplar sets are **versioned** — a new exemplar pack is a deployable artifact scored
against the golden set before rollout, so accuracy changes are measured, not discovered in production.

---

## R — Risks & Roadmap

| Risk | Likelihood | Mitigation |
|---|---|---|
| **Hallucinated columns / wrong joins** | High without grounding | Schema-linking retrieval + few-shot + valid-SQL eval + repair loop |
| **PII leak / over-broad access** | Catastrophic if it happens | Read-only role, PII denylist + masking, HITL gate, audit log, PII-gate recall metric |
| **Runaway query on 40M rows** | Medium | `EXPLAIN` cost cap + `statement_timeout` + forced `LIMIT` + read-replica isolation |
| **Silently-wrong number** | Medium (worst kind — looks fine) | Critic sanity check, mandatory SQL provenance shown to user, execution-accuracy eval |
| **Ambiguous NL ("last quarter"?)** | High | Bounded clarify step; assumptions stated back in the narrative |
| **Prompt injection via data/question** | Low–medium | AST validation strips injected SQL; question routed through Kompass safety agent |

**Roadmap:** (1) a **semantic layer / metric store** so "response time" always compiles to one canonical
definition; (2) **self-improving few-shots** — approved analyst-verified queries flow back into the exemplar
store (the Tier-2 self-improving loop); (3) **caching** of schema-linking results and repeated aggregate
queries; (4) **multi-turn drill-down** ("now break that out by beat") with conversation memory.

> **Interview soundbite:** *"The scariest failure isn't a crash — it's a confident, wrong number. So every
> answer ships with the SQL that produced it, a critic checks it for sanity, and I score execution accuracy
> against a golden set. Trust is a feature, and provenance is how you build it."*

---

## Interview soundbites, collected

- *"NL2SQL is a retrieval strategy, not a chatbot trick — the router picks it when the answer lives in rows,
  not prose."*
- *"I lead with ~2.5 analyst FTE freed at eight cents a query, not with tokens."*
- *"The model never invents a column: it only sees a retrieved slice of the real schema plus a few worked
  examples."*
- *"For a read-only agent, the risky action is disclosure, not a write — so the only thing that pauses for a
  human is reading personal data."*
- *"90% execution accuracy is a good day; 99% PII-gate recall is a bad day, because that miss is a breach."*
- *"The scariest failure is a confident wrong number, so every answer carries the SQL that produced it."*

---

## Related

- **Framework:** [PACTEDR — the 7-step system-design framework](../framework_PACTEDR.md)
- **Retrieval:** [02 — Retrieval strategies (NL2SQL as retrieval, adaptive router)](../../docs/02_retrieval_strategies.md)
- **Advanced patterns:** [06 — Sandboxed code execution & self-improving loops](../../docs/06_advanced_patterns.md)
- **Human-in-the-loop:** [04 — Declarative HITL, durability, idempotency](../../docs/04_hitl_patterns.md)
- **Architecture:** [05 — Full architecture & capability tiers](../../docs/05_architecture.md)
- **Question bank:** [entrevista/banco_preguntas.md](../banco_preguntas.md)
- **Sibling cases:** [Case 1 — Knowledge Assistant](caso_01_knowledge_assistant.md) ·
  [Case 3 — Document Processing](caso_03_document_processing.md)
