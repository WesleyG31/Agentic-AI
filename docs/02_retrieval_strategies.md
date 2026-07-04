# Retrieval Strategies — Beyond RAG

> Wesley's question, answered directly: *"Is there another method besides RAG?"* — **Yes, several.** RAG is one point on a spectrum of ways to get the right knowledge in front of an LLM. Production systems in 2026 rarely pick one; they run an **adaptive router** that classifies each query and dispatches it to the cheapest strategy that can answer it correctly. Kompass ships exactly that router, so the honest answer to "beyond RAG?" is a working demo, not a slide.

This document maps the full retrieval landscape, goes deep on the two trade-offs interviewers probe hardest (CAG vs RAG, and GraphRAG vs chunk similarity), shows how Kompass implements adaptive retrieval, and closes with how we measure that any of it actually works.

---

## 1. Retrieval is a spectrum, not a binary

The core problem is always the same: an LLM has a fixed context window and frozen weights, but your knowledge is large, fresh, private, and often structured. "Retrieval" is any mechanism that bridges that gap. The mistake juniors make is treating **RAG = retrieval**. RAG is *one* bridge — dense/sparse search over chunks — and it is the right bridge for a specific corpus shape (large, fresh, per-tenant, citable). Change the corpus shape and a different bridge wins.

Think of the design space along a few axes:

```
                     KNOWLEDGE LOCATION
   in the weights  ──────────────────────────────►  in an external store
   (fine-tuning)      (long-context / CAG)              (RAG / GraphRAG / NL2SQL)

                     RETRIEVAL CONTROL
   none (stuff it)  ──────────────────────────────►  agent decides, multi-step
   (long-context)      (single-shot RAG)                (agentic / adaptive RAG)

                     DATA SHAPE
   unstructured text ────────────────────────────►  relational / tabular
   (RAG, CAG)          (GraphRAG)                       (NL2SQL, structured retrieval)
```

No single point dominates. The 2026 production pattern is an **adaptive router**: a cheap classifier looks at the query, decides *which* axis it lives on, and routes it — FAQ-style questions to a sub-second cached path, long-tail questions to full hybrid RAG, relational questions to a knowledge graph, quantitative questions to SQL. That is the pattern Kompass implements (Section 4).

> **Interview soundbite:** "RAG isn't the goal, it's one tool. The goal is grounding — and in production I route each query to the cheapest grounding strategy that answers it: a prompt-cache hot path for common questions, hybrid RAG for the long tail, a graph for multi-hop, and NL2SQL for anything quantitative."

For where retrieval sits inside the broader agent loop (plan → retrieve → act → verify), see [Agentic AI deep dive](01_agentic_ai_deep_dive.md).

---

## 2. The strategy map

| Method | What it is | When it wins | Cost / latency notes |
|---|---|---|---|
| **RAG (hybrid)** | Dense embeddings (ANN over a vector index) **+** BM25 lexical **+** fusion (RRF) **+** a cross-encoder reranker over top-k chunks, then generate with citations | Large, **fresh**, **per-tenant**, **citable** corpora — millions of chunks that change often | Two retrievers + rerank + generation. ~300 ms–1.5 s retrieval; vector-DB storage cost; tiny per-query embedding cost. Freshness = re-index, no retraining |
| **CAG (Cache-Augmented Generation)** | Put the *whole* corpus in the prompt once, precompute the **KV / prompt cache**, then answer every query against the warm cache — **no retrieval step** | **Small, stable, shared, heavily-queried** corpora (policy handbook, product FAQ, a runbook set) | Sub-second answers after warm-up. Pay the full input once to write the cache (1.25× for 5-min TTL, 2× for 1-h); every hit reads at ~**0.1×**. Invalidates on any corpus edit |
| **Long-context** | Pass the documents directly into the context window each call — no index, no cache | Corpus **fits** the window and you query it too rarely to bother caching, or it changes every call | Full input token cost **every** call; latency grows with context length; "lost-in-the-middle" recall degradation on very long inputs |
| **GraphRAG** | Extract entities + relations into a **knowledge graph**, run community detection + summaries; traverse the graph (or use community summaries) at query time | **Relational / multi-hop** questions, corpus-wide **synthesis**, compliance ("what connects X to Y?") | Expensive to *build* (LLM extraction over every chunk) and to maintain; query itself can be cheap. Overkill for simple lookup |
| **NL2SQL / structured retrieval** | Translate natural language → SQL (or an API filter), execute against the DB, return exact rows/aggregates | **Structured / tabular** data; counts, sums, filters, joins, time-series — anything needing *exact* numbers | Schema-linking + SQL-validation overhead; latency = LLM generation + query execution. Injection/guardrail surface must be handled |
| **Fine-tuning** | Adjust the model's weights (LoRA / full) on domain data | **Style, format, tone, domain vocabulary** — teaching the model *how* to respond, a fixed skill | Training + eval cost; **stale the moment knowledge changes**; no citations. Complements retrieval, does **not** replace it for dynamic facts |
| **Agentic RAG** | The agent *decides* retrieval: reformulate the query, decompose it, choose sources, re-retrieve, self-critique | **Complex** questions needing planning or multiple sources ("compare our refund policy to what we told this customer last month") | Multiple LLM turns → higher latency and token cost. Use only when a single-shot retrieval can't answer |
| **Adaptive RAG (router)** | Classify the query, then dispatch to the strategy above that fits — CAG hot-path vs RAG cold-path vs GraphRAG vs NL2SQL | The **default production posture in 2026** — matches cost to query difficulty | Adds a cheap classifier hop (a few ms with a small model or heuristic). Pays for itself by keeping most traffic on the cheapest path |

A quick decision heuristic:

```
Is the answer a number / aggregate over structured data?   ── yes ──►  NL2SQL
Does answering require chaining relations across entities?  ── yes ──►  GraphRAG
Is the corpus small, stable, and hammered by the same Qs?   ── yes ──►  CAG (cache)
Otherwise (large / fresh / per-tenant / needs citations)    ─────────►  Hybrid RAG
Still can't answer in one shot?                             ─────────►  Agentic RAG (loop)
```

> **Interview soundbite:** "Fine-tuning and RAG solve different problems. Fine-tuning changes *how* the model talks; retrieval changes *what* it knows right now. I fine-tune for format and tone, and retrieve for facts — using fine-tuning to inject changing knowledge is the classic anti-pattern."

---

## 3. CAG vs RAG — the trade-off interviewers love

This is the highest-signal comparison in the whole space, because CAG (Cache-Augmented Generation) is genuinely newer and forces you to reason about **cost, freshness, and corpus size** simultaneously.

### 3.1 The mechanism difference

**RAG** does work *at query time*: embed the query, search a vector index (+ BM25), fuse, rerank, then feed the top-k chunks to the model. The context the model sees is a small, query-specific slice.

**CAG** does the work *once, up front*: it loads the **entire** corpus into the prompt, lets the provider prefill and **cache** it, and then answers every subsequent question against that already-processed prefix. There is no retriever, no index, no top-k, no reranker — the model simply "already has the whole book open." On Claude, this is prompt caching: the first request writes the cache; every later request with the same prefix reads it at a fraction of the price and skips the prefill latency.

```
RAG  (work per query)                     CAG  (work once, then reuse)
─────────────────────                     ────────────────────────────
query ─► embed ─► ANN search ─┐           corpus ─► prompt ─► PREFILL+CACHE (once, 1.25×)
        └─► BM25 ─────────────┤                                     │
                       fuse (RRF)                                   ▼
                          │                query ─► [warm cache read ~0.1×] ─► answer  (<1s)
                       rerank
                          │
                     top-k chunks ─► LLM ─► answer  (~0.3–1.5s retrieval + gen)
```

### 3.2 Cache invalidation — the thing that kills CAG

CAG's superpower is a warm prefix; its Achilles heel is that **the cache is a prefix match**. Any byte change anywhere in the cached prefix invalidates everything after it, and the cache is model-scoped and TTL-bound (5-minute default, 1-hour option). Concretely:

- **Edit one line of the handbook** → the whole cached corpus prefix must be re-written (you pay the write premium again).
- **Reorder documents, inject a timestamp, or non-deterministically serialize JSON** into the prefix → silent invalidation; you pay full price every request and never get a hit.
- **TTL expiry during a quiet period** → the next request re-writes the cache cold.

So CAG is only viable when the corpus is **stable** (rarely edited, so the cache stays warm) and **shared** (many users hit the *same* prefix, so the one-time write cost amortizes across thousands of reads). RAG has the opposite freshness profile: to add a document you just index it — no prefix to invalidate, no retraining. That is why **freshness pushes you toward RAG and stability pulls you toward CAG.**

> **Interview soundbite:** "CAG trades freshness for latency. It's a prefix cache, so a single edit invalidates the whole corpus prefix — which is exactly why it only makes sense for small, stable, heavily-shared knowledge like an FAQ, and why anything that changes per-tenant or per-hour stays on RAG."

### 3.3 Corpus-size limits

CAG is bounded by the context window. Modern Claude models expose a **1M-token** window (Opus 4.8, Sonnet 5) — Haiku 4.5 is 200K — so "the whole corpus" can be surprisingly large. But two ceilings bite before the hard token limit:

1. **Cost scales with prefix size on every write.** A 400K-token corpus is cheap to *read* (0.1×) but you eat that size at 1.25–2× on every (re)write and every cache miss. Big + volatile = worst case.
2. **Recall degrades on very long contexts** ("lost in the middle"). Past a few hundred K tokens, a model's ability to use a fact buried mid-prompt drops — so a huge CAG prefix can *retrieve everything and use nothing*. RAG sidesteps this by only ever showing the model a tight, reranked slice.

Rule of thumb Kompass uses: if the corpus fits comfortably (say ≲150–200K tokens), is stable, and is hot, **CAG**. If it's large, sharded per-tenant, or fresh, **RAG**. In between, RAG with aggressive prompt-caching of the *system prompt and instructions* (not the corpus) gets you much of CAG's latency win without its freshness penalty.

### 3.4 The cost model, worked

Let base input price = **1×** (per token). Cache **read** ≈ 0.1×; cache **write** = 1.25× (5-min TTL) or 2× (1-h TTL). Take a **150K-token** stable corpus, heavily queried.

**CAG path** — pay to write the 150K prefix once, then read it on every query:

| | First query (cold) | Each subsequent query (warm) |
|---|---|---|
| Corpus tokens billed | 150K × **1.25** = 187.5K-equiv | 150K × **0.1** = 15K-equiv |
| + question / answer | small | small |
| Retrieval infra | none | none |
| Latency | one prefill | **sub-second**, no retrieval |

Break-even math straight from the cache economics: with 5-min TTL, **2 reads** already beat paying full price twice (1.25 + 0.1 = 1.35× vs 2×); with 1-h TTL you need **3** (2 + 0.2 = 2.2× vs 3×). A hot FAQ does thousands of reads per write — CAG is dramatically cheaper *and* faster there.

**RAG path** — never sends the whole corpus; sends ~8 reranked chunks (say ~4K tokens) per query:

| | Every query |
|---|---|
| Context tokens billed | ~4K × 1× (+ cache the *instructions*, not the corpus) |
| Retrieval infra | vector DB + reranker (fixed monthly + per-query compute) |
| Latency | ~300 ms–1.5 s retrieval + generation |

RAG's **per-query token bill is tiny** (it never pays for 150K tokens), but it carries **fixed infrastructure** and **retrieval latency** on every call, and it can't answer sub-second. CAG's per-query bill is a cheap cache read with **zero** retrieval infra and latency — but only while the corpus stays stable enough to keep the cache warm.

The punchline: **CAG wins on small-stable-hot; RAG wins on large-fresh-per-tenant-citable.** They are not competitors so much as different operating points — which is exactly why Kompass routes between them rather than choosing.

---

## 4. GraphRAG — when relations beat similarity

Vector similarity answers *"what text looks like this query?"*. It is blind to *structure*. Ask **"Which of our enterprise customers are affected by the incident in the payments service that the SRE flagged last Tuesday, and what SLA credits do their contracts entitle them to?"** and chunk similarity flails: the answer isn't in any single chunk — it's in the *relationships* between an incident, a service, customer accounts, and contract terms.

**GraphRAG** builds a knowledge graph from the corpus:

1. **Extract** entities and relations from each chunk with an LLM (`Incident → affects → Service`, `Customer → subscribes_to → Service`, `Contract → grants → SLA_Credit`).
2. **Detect communities** (e.g. Leiden clustering) and generate a **summary per community** so the graph can answer *global* ("what are the main themes across all incidents this quarter?") as well as *local* questions.
3. At query time, **traverse** the graph (multi-hop) or read the relevant community summaries, then hand the model a structured, connected subgraph instead of a bag of similar chunks.

```
Chunk-similarity RAG                     GraphRAG
────────────────────                     ────────
query ─► top-k chunks that                query ─► entities in query ─► traverse edges
        LOOK like the query                        (Incident)-[affects]->(Service)
        (no notion of how they                     (Service)<-[subscribes]-(Customer)
        relate to each other)                      (Customer)-[has]->(Contract)-[grants]->(Credit)
                                                    └─► connected subgraph ─► LLM
```

**When GraphRAG wins:** multi-hop reasoning, corpus-wide synthesis, compliance/audit ("trace every path from this data element to an external processor"), and any domain where the *edges* carry the meaning (org charts, dependency graphs, case law, entitlements).

**When it doesn't:** simple fact lookup, where the build cost (an LLM call over every chunk to extract entities, plus graph maintenance as the corpus changes) is pure overhead. GraphRAG is the most expensive strategy to *stand up*; reserve it for questions hybrid RAG demonstrably can't answer.

> **Interview soundbite:** "I reach for GraphRAG when the answer lives in the relationships, not the text. Chunk similarity finds documents that look like the question; a graph lets me hop Incident → Service → Customer → Contract to answer a multi-hop compliance question that no single chunk contains."

---

## 5. NL2SQL / structured retrieval — go to the source of truth

When the answer is a **number** — "how many P1 tickets did team EU-Ops close last quarter, and what was the median resolution time?" — retrieving *text about* tickets is the wrong tool. The right tool is to query the database directly: translate the question to SQL, run it, return exact rows.

This is where I lean on production experience. On the **SCOUT / police-analytics** work I built NL2SQL over **~40M records**: the hard parts were never the SQL generation itself but everything around it —

- **Schema linking** — mapping fuzzy natural-language terms to the right tables/columns (and disambiguating synonyms) so the generated SQL targets reality, not a hallucinated schema.
- **Validation & safety** — parse and *validate* generated SQL against an allowlist (read-only, no `DROP`/`DELETE`, parameterized), enforce row limits, and sandbox execution to neutralize injection.
- **Grounding the answer** — return the executed query *and* the result set so the answer is auditable, and so the LLM narrates numbers it actually retrieved rather than inventing them.

For structured data, NL2SQL beats RAG on **exactness** (RAG can find a chunk that mentions a figure; only SQL can *compute* the current figure) and on **freshness** (the DB is the source of truth — no index to stale). Kompass routes any quantitative/aggregation query straight here. The full design, guardrails, and evals live in [Caso 04 — NL2SQL Analyst](../entrevista/casos/caso_04_nl2sql_analyst.md).

> **Interview soundbite:** "For structured data I don't retrieve text about the numbers — I query the numbers. On 40M police records the win wasn't SQL generation, it was schema linking and a read-only validation layer that made generated SQL safe and auditable."

---

## 6. How Kompass implements adaptive retrieval

Kompass treats retrieval as a **routed capability**, not a fixed pipeline. A lightweight classifier node inspects each query and dispatches it to one of four backends — `RAG-hybrid | CAG | GraphRAG | NL2SQL` — with an agentic fallback loop when a single shot isn't enough. This is the concrete answer to "beyond RAG": the router *is* "everything," and the demo shows it choosing per query.

The router is a node in the LangGraph state machine (see [Architecture](05_architecture.md) for how it wires into the full graph and where human-in-the-loop approval gates sit). Routing logic, in pseudocode:

```python
# LangGraph node: classify the query, then dispatch to a retrieval backend.
# The classifier is cheap (small model or rules); the backends do the heavy lifting.

def route_retrieval(state: KompassState) -> RetrievalPlan:
    q = state["query"]
    signals = classify(q)   # intent, data-shape, freshness, multi-hop, tenant

    # 1. Quantitative / aggregation over structured data -> query the DB directly.
    if signals.is_structured or signals.needs_aggregation:
        return RetrievalPlan(backend="nl2sql", table_hints=signals.tables)

    # 2. Relational / multi-hop / compliance synthesis -> knowledge graph.
    if signals.is_multi_hop or signals.needs_relational_synthesis:
        return RetrievalPlan(backend="graphrag", seed_entities=signals.entities)

    # 3. Common question against small, stable, shared knowledge -> CAG hot-path.
    #    (FAQ / policy / runbook already loaded into a warm prompt cache.)
    if signals.corpus == "faq" and cache_is_warm(signals.corpus):
        return RetrievalPlan(backend="cag", corpus_id=signals.corpus)

    # 4. Default: large / fresh / per-tenant / citable -> hybrid RAG cold-path.
    return RetrievalPlan(backend="rag_hybrid", tenant=state["tenant_id"], k=8)


def retrieve(plan: RetrievalPlan, state: KompassState) -> Context:
    ctx = DISPATCH[plan.backend](plan, state)     # run the chosen backend

    # Agentic fallback: if the cheap path didn't ground the answer, escalate.
    if not ctx.is_sufficient() and state["attempts"] < MAX_ATTEMPTS:
        state["attempts"] += 1
        return retrieve(reformulate(plan, ctx), state)   # re-plan / re-retrieve

    return ctx
```

Design notes that matter in an interview:

- **The classifier is the cheapest thing in the loop.** It can be a rules layer or a small model; being wrong is recoverable because of the agentic fallback, so we bias it toward the *cheap* backend and let escalation catch misses.
- **CAG is gated on a warm cache.** If the FAQ prompt cache is cold (TTL expired), the router can still fall back to RAG over the same corpus rather than pay a cold CAG write on a one-off query — the two share the underlying documents.
- **Every backend returns citations/provenance** (the SQL executed, the chunk IDs, the graph paths) so the answer is auditable — non-negotiable for a support/ops agent that *acts* on what it retrieves.
- **The router matches cost to difficulty.** Most traffic is common questions → CAG (sub-second, ~0.1× reads). The long tail → RAG. The rare multi-hop or quantitative query pays for the more expensive backend only when it's actually needed.

The choice to build this on LangGraph rather than a hand-rolled loop — a graph gives you the classify/dispatch/fallback control flow, checkpointing, and HITL interrupts for free — is argued in [Framework decision](03_framework_decision.md).

> **Interview soundbite:** "Kompass doesn't pick a retrieval strategy at design time — it routes per query. A cheap classifier sends FAQs to a cached hot-path, the long tail to hybrid RAG, relational questions to a graph, and numbers to SQL, with an agentic fallback that escalates when the cheap path comes up short."

---

## 7. Measuring retrieval quality (the evaluation angle)

You cannot claim "beyond RAG" without proving each backend actually grounds the answer. Kompass evaluates retrieval with the **RAGAS** metric family, which separates *retrieval* quality from *generation* faithfulness:

| Metric | Question it answers | What a bad score tells you |
|---|---|---|
| **Context precision** | Of the retrieved context, how much is actually relevant (and is the relevant stuff ranked high)? | Retriever/reranker is pulling noise → tune k, improve the reranker, tighten chunking |
| **Context recall** | Did retrieval fetch *all* the context needed to answer? | Missing evidence → chunking too coarse, index gaps, wrong backend routed |
| **Faithfulness** | Is every claim in the answer grounded in the retrieved context (no hallucination)? | Model is inventing beyond its evidence → constrain generation, cite spans |
| **Answer relevance** | Does the answer actually address the question? | Right facts, wrong focus → prompt/routing issue |

How this plugs into the router:

- **Per-backend eval sets.** RAG, CAG, GraphRAG, and NL2SQL each get their own labeled query set, because "good retrieval" means different things (chunk relevance for RAG; correct rows for NL2SQL; correct subgraph for GraphRAG).
- **Routing accuracy** is its own metric: did the classifier send the query to the backend that scores best on it? A query that GraphRAG nails but the router sent to RAG is a *routing* failure, not a retrieval failure — and the two are fixed differently.
- **Faithfulness is the safety gate.** For an agent that *acts*, a confident-but-ungrounded answer is worse than "I don't know." Faithfulness below threshold blocks the act and, for risky actions, escalates to the human-approval path.
- **Track it in CI.** Retrieval quality regresses silently (a re-index, a chunking tweak, a model swap). RAGAS scores run in the eval harness so a regression fails the build, not production.

> **Interview soundbite:** "I evaluate retrieval and generation separately with RAGAS — context precision and recall tell me if the retriever found the right evidence, faithfulness tells me if the model stayed grounded. For an agent that takes actions, faithfulness is a safety gate, not a vanity metric."

---

## Related

- [01 — Agentic AI deep dive](01_agentic_ai_deep_dive.md) — where retrieval sits inside the plan → retrieve → act → verify loop.
- [03 — Framework decision](03_framework_decision.md) — why the adaptive router is built on LangGraph rather than a hand-rolled loop.
- [05 — Architecture](05_architecture.md) — how the retrieval router wires into the full Kompass state machine and human-in-the-loop gates.
- [Caso 04 — NL2SQL Analyst](../entrevista/casos/caso_04_nl2sql_analyst.md) — the structured-retrieval backend in depth, grounded in the 40M-record SCOUT/police NL2SQL experience.
