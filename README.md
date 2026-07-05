<div align="center">

# 🧭 Kompass

**Agentic Support & Operations Assistant** — a reference-grade agentic AI system built on **LangGraph v1**.

*Not "look how well it answers" — **"it resolved N% of requests end-to-end with no human, saving X min and €Y per case."***

[![ci](https://img.shields.io/badge/ci-pending-lightgrey)](.github/workflows/ci.yml)
[![eval score](https://img.shields.io/badge/eval-100%25%20resolved%20·%200%20unsafe%20(n%3D35)-brightgreen)](evals/)
[![framework](https://img.shields.io/badge/LangGraph-v1.0-1C3C3C)](docs/03_framework_decision.md)
[![python](https://img.shields.io/badge/python-3.11+-blue)](requirements.txt)

</div>

---

## Why Kompass exists

Most "agent" demos are RAG in a trench coat: retrieve, generate, done. Kompass is the opposite — it **plans, chooses the right retrieval strategy per query, calls tools via MCP, drafts an action, pauses for human approval only when the action is risky, executes, and remembers**. It is anchored to the #1 ROI use case for enterprises in 2026: **internal support & operations automation**.

It is deliberately **universal** (any company: customer support, IT helpdesk, HR, ops) and **reproducible** (synthetic "ACME" corpus, one-command demo, no proprietary data).

> Deep-dive docs for every design decision live in [`docs/`](docs/). Interview prep (framework + question bank + solved cases) lives in [`entrevista/`](entrevista/).

## Business value first (the headline)

<!-- EVAL:START -->
| Metric (n=35) | Naïve RAG baseline | Kompass | Δ |
|---|---|---|---|
| **Resolution / deflection rate** | 11% | **100%** | **+89pp** |
| Correct (LLM judge) | 60% | 100% | +40pp |
| Grounded / faithful | 17% | 94% | +77pp |
| Citation discipline | 49% | 100% | +51pp |
| Unsafe actions (rejected → executed) | n/a | **0** | — |
| Mean latency / case | 3.2s | 4.8s | — |
| Mean LLM cost / case | — | $0.009 | — |
<!-- EVAL:END -->

_Table regenerated from a live run by `make evals` (35-item golden set, LLM-as-judge on the reasoning tier + deterministic fact/citation/side-effect checks). See [evals/](evals/)._

## Architecture — two interop layers: MCP (vertical) + A2A (horizontal)

```
 trigger: user  |  event/webhook/cron (proactive autonomy, Tier 2)
        │
        ▼
   Planner (plan-and-execute + replanning, Tier 2) ─▶ Supervisor (routes, cuts loops)
   ├─▶ Retrieval Router → {RAG hybrid | CAG | GraphRAG | NL2SQL}
   ├─▶ Researcher (synthesis with mandatory citations)
   ├─▶ Data Analyst (NL2SQL + sandboxed code execution, Tier 2)
   ├─▶ Action Agent ──[MCP: tools]──▶ HITL middleware (approve/edit/reject) ─▶ execute
   ├─▶ Critic / Verifier (reflection: grounding check)
   └─▶ Safety agent (prompt-injection / PII, Tier 2)
        │
        └──[A2A: signed Agent Card]──▶ external specialist agent (Tier 2)
 memory: conversation + per-user store + self-improving (Tier 2)
 observability: Langfuse | typed outputs: Pydantic | durable: Postgres checkpointer
```

Full diagram and rationale: [`docs/05_architecture.md`](docs/05_architecture.md).

## Capability checklist (what's actually built)

### Tier 1 — Core
- [x] Adaptive retrieval (RAG hybrid + CAG + GraphRAG + NL2SQL, router per query)
- [x] Orchestration / planning — *supervisor mode live (`KOMPASS_AGENT_MODE=multi`); explicit planner is Tier 2*
- [x] Multi-agent workers (+ single-agent mode) — *Researcher worker; writes + HITL stay at the supervisor*
- [x] Tool use via **MCP** (doc-search, sql, ticketing servers over stdio)
- [x] Memory — short-term (per-thread checkpointer) + long-term per-user store (`save_memory`/`recall_memories`, cross-thread)
- [x] Reflection / self-correction — `GroundingCritic` middleware: final answers reviewed vs tool evidence, one bounded retry
- [x] **Human-in-the-loop** declarative + durable + resumable (HITL middleware, verified live)
- [x] Guardrails — citations, read-only SQL boundary, arg validation, `SafetyMiddleware` (93% injection block, 0% false-positive), PII redaction helper
- [x] Streaming + observability — SSE `/chat/stream` + per-call JSONL tracing (`runs.jsonl`); Langfuse behind `LANGFUSE_ENABLED`
- [x] Evaluation — 35-item golden set + LLM judge + naïve-RAG baseline + value metrics + CI regression gate (live table above)
- [x] Model routing (fast/balanced/reasoning tiers via config) + per-run token budget (`TokenBudgetMiddleware`)
- [x] Deploy/MLOps — Dockerfile (288 MB) + CI + compose infra; prompts versioned in git

### Tier 2 — Advanced
- [x] A2A protocol — signed Agent Card + JSON-RPC specialist endpoint (`kompass/a2a/`)
- [x] Plan-and-execute + replanning — `TodoListMiddleware` with a Kompass planning prompt
- [x] Sandboxed code execution — AST-allowlisted subprocess + timeout, Data Analyst tool (`kompass/sandbox/`)
- [x] Proactive / event-driven autonomy — ticket webhook triaged unattended by the read-only Researcher (`kompass/triggers/`)
- [x] Self-improving loop — distilled lessons injected into future runs (`kompass/memory/lessons.py`)
- [x] User-simulator eval harness (τ-bench style) — multi-turn goal-driven episodes (`evals/user_simulator/`)
- [x] Dedicated safety agent + prompt-injection red-team suite (`kompass/guardrails/`, `evals/red_team.py`)

### Tier 3 — Stretch
- [x] **Framework comparison spike** — Researcher in PydanticAI, live parity run ([`spike_frameworks/comparison.md`](spike_frameworks/comparison.md))
- [x] Semantic answer cache — cosine-matched paraphrase reuse on read-only queries (`kompass/models/cache.py`)
- [ ] Multi-modal ingestion · Multi-agent debate · Saga/compensation

## Quickstart

```bash
# 1. Install (Python 3.11+). A virtualenv is recommended.
python -m pip install -r requirements-dev.txt   # runtime + dev/eval
# (runtime only: python -m pip install -r requirements.txt)

# 2. Configure
cp .env.example .env        # add your OPENAI_API_KEY

# 3. Seed the reproducible ACME corpus (SQLite + local Chroma index)
make seed                   # or: python -m kompass.scripts.seed

# 4. Run the canonical end-to-end HITL demo
make demo                   # or: python -m kompass.scripts.demo

# 5. Chat UI / API
make ui                     # Streamlit chat with citations + HITL card
make api                    # FastAPI: POST /chat, POST /resume, GET /runs/{id}
```

> **Windows without `make`:** run the `python -m ...` command shown next to each target.
> **No Docker needed** for the core demo — it uses local Chroma + a SQLite checkpointer. `docker compose up -d` adds Qdrant, a durable Postgres checkpointer, and Langfuse.

## Repo layout

```
kompass/
├── docs/            # all theory in .md (agentic deep-dive, retrieval, framework decision, HITL, arch, advanced)
├── entrevista/      # interview prep: PACTEDR framework + question bank + solved cases
├── corpus/          # synthetic ACME docs + seed SQL (reproducible)
├── kompass/         # the package
│   ├── graph/       # planner + supervisor + workers + routers + HITL middleware
│   ├── retrieval/   # rag_hybrid, cag, nl2sql, router
│   ├── mcp_servers/ # doc_search, sql, ticketing (MCP — vertical layer)
│   ├── a2a/         # Agent Card + A2A server/client (horizontal layer, Tier 2)
│   ├── memory/  guardrails/  models/  api/  triggers/  sandbox/
├── spike_frameworks/  # Researcher reimplemented (PydanticAI/OpenAI SDK) + comparison.md
├── evals/  ui/  tests/  scripts/  .github/workflows/ci.yml
```

## Docs index
- [`01_agentic_ai_deep_dive.md`](docs/01_agentic_ai_deep_dive.md) — what agentic AI is, the spectrum, design patterns
- [`02_retrieval_strategies.md`](docs/02_retrieval_strategies.md) — alternatives to RAG (CAG, GraphRAG, NL2SQL, adaptive router)
- [`03_framework_decision.md`](docs/03_framework_decision.md) — why LangGraph v1 (research + trade-offs + sources)
- [`04_hitl_patterns.md`](docs/04_hitl_patterns.md) — declarative HITL, idempotency, durability, Temporal
- [`05_architecture.md`](docs/05_architecture.md) — full architecture + capability tiers
- [`06_advanced_patterns.md`](docs/06_advanced_patterns.md) — A2A vs MCP, plan-and-execute, sandbox, proactive, self-improving

## License
MIT — built as a public portfolio project.
