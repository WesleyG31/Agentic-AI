# Kompass Architecture — Two Interop Layers (MCP + A2A) and a Capability Model

Kompass is an **agentic Support & Operations assistant**: it does not merely answer a question, it **resolves and acts** — planning a path, choosing the right retrieval strategy per query, calling tools through MCP, drafting a side-effecting action, pausing for a human only when the action is risky, executing durably, and remembering the outcome for next time.

This document is the map of that machine. It covers (1) the full runtime architecture and a walk through every node; (2) the two interoperability layers that define a 2026-grade agent stack — **MCP (vertical, agent↔tools)** and **A2A (horizontal, agent↔agent)**; (3) the **capability model** as tiered checklists; (4) the **stack**; (5) **how real users use it** across surfaces and three end-to-end journeys; and (6) the **repo layout** mapped to capabilities.

Prerequisites and neighbours: the *why-agentic* framing lives in [What is agentic AI](01_agentic_ai_deep_dive.md); the per-query retrieval choices in [Retrieval strategies](02_retrieval_strategies.md); the framework rationale in [Why LangGraph v1](03_framework_decision.md); the pause/resume mechanics in [HITL patterns](04_hitl_patterns.md); and the Tier-2/3 depth (A2A, plan-and-execute, sandbox, proactive, self-improving) in [Advanced patterns](06_advanced_patterns.md). For interview delivery, see the [PACTEDR framework](../entrevista/framework_PACTEDR.md).

---

## Architecture at a glance

Kompass is a **LangGraph v1 state graph**: a set of nodes (agents) that read and write a shared, typed state, orchestrated by a supervisor, made durable by a checkpointer, and gated at the point of action by human-in-the-loop middleware.

```
 TRIGGER ─────────────────────────────────────────────────────────────
   user (chat / REST / Slack)  │  event · webhook · cron ──▶ proactive
                               │                             (Tier 2)
                               ▼
 ┌───────────────────────────────────────────────────────────────────┐
 │ PLANNER   plan-and-execute + replanning                (Tier 2)     │
 │           decompose goal → ordered steps; replan on failure /       │
 │           new evidence                                              │
 └───────────────────────────────┬───────────────────────────────────┘
                                  ▼
 ┌───────────────────────────────────────────────────────────────────┐
 │ SUPERVISOR   routes to workers · cuts loops · enforces step/token   │
 │              budgets · decides "done"                               │
 └──┬────────────┬────────────┬────────────┬────────────┬─────────────┘
    ▼            ▼            ▼            ▼            ▼
 ┌────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌──────────────┐
 │RETRIEVAL│ │RESEARCHER│ │  DATA   │  │ CRITIC /│  │ SAFETY AGENT │
 │ ROUTER │  │synthesis │  │ ANALYST │  │VERIFIER │  │ inj. / PII   │
 │per-query│ │+ citations│ │NL2SQL + │  │grounding│  │  (Tier 2)    │
 │strategy │ │(mandatory)│ │sandbox  │  │+ retry  │  └──────────────┘
 └───┬────┘  └─────────┘  │ (Tier 2)│  └─────────┘
     ▼                    └─────────┘
 ┌─────────────────────────────────┐
 │ RAG hybrid · CAG · GraphRAG ·    │
 │ NL2SQL   (chosen per query)      │
 └─────────────────────────────────┘

 ┌───────────────────────────────────────────────────────────────────┐
 │ ACTION AGENT ─[ MCP: tools ]─▶ HITL middleware                      │
 │               (approve · edit · reject) ─▶ execute (idempotent)     │
 └───────────────────────────────┬───────────────────────────────────┘
                                  └─[ A2A: signed Agent Card ]─▶ external
                                     specialist agent            (Tier 2)

 MEMORY        conversation (short) + per-user store (long)
               + self-improving loop (Tier 2)
 CROSS-CUTTING observability: Langfuse · typed outputs: Pydantic
               durable state: Postgres checkpointer
```

The flow reads top to bottom: a **trigger** starts a run; the **planner** turns a goal into steps; the **supervisor** dispatches each step to a specialist worker and loops until the goal is met or a budget is hit; **read** work (retrieval, research, analysis) is unrestricted; **write** work (any side effect) is funnelled through the **action agent → HITL gate → execute** path; a **critic** checks grounding before anything is shown or committed; **memory** and **observability** cut across the whole graph.

Node-by-node:

| Node | Role | Reads / Writes | Tier |
|---|---|---|---|
| **Trigger** | Entry point. Synchronous (user, REST, Slack) or asynchronous (webhook/cron) for proactive runs. | Creates the initial state + `thread_id`. | 1 (user) / 2 (event) |
| **Planner** | Plan-and-execute: decompose the goal into an ordered, typed step list; **replan** when a step fails or new evidence contradicts the plan. In single-agent mode it degrades to "one step: answer". | Writes `plan`, `step_cursor`. | 2 |
| **Supervisor** | The orchestrator. Routes the current step to the right worker, **cuts loops** (max-iteration + no-progress detection), enforces per-run step/token budgets, and decides when the goal is `done`. | Reads `plan` + worker results; writes `next` route. | 1 |
| **Retrieval Router** | Classifies the query and picks the cheapest sufficient strategy: **RAG hybrid** (semantic + keyword + reranker) for open docs, **CAG** (cache-augmented) for a small hot corpus, **GraphRAG** for multi-hop relational questions, **NL2SQL** for structured facts. See [02](02_retrieval_strategies.md). | Writes `context`, `citations`. | 1 (GraphRAG stretch) |
| **Researcher** | Synthesises retrieved context into an answer with **mandatory inline citations**; refuses to assert what it cannot cite. | Writes `answer_draft`, `citations`. | 1 |
| **Data Analyst** | Answers quantitative questions: NL2SQL against the operational DB, then **sandboxed code** for aggregation/plots the SQL layer shouldn't do. | Writes `figures`, `tables`. | 2 (sandbox) |
| **Action Agent** | The only node allowed to cause side effects. Calls tools **over MCP**, drafts the action, and hands it to the HITL gate. | Writes `proposed_action`. | 1 |
| **HITL middleware** | Declarative pause before risky tools: surfaces an **approve / edit / reject** card, persists state, resumes on the reviewer's decision. See [04](04_hitl_patterns.md). | Interrupts; resumes with `decision`. | 1 |
| **Critic / Verifier** | Reflection: checks the draft is grounded in `context`, contains no unsupported claims, and matches the typed output schema; sends failures back for one bounded retry. | Reads `answer_draft` + `context`; writes `verdict`. | 1 |
| **Safety agent** | Dedicated guard: scans inbound content for prompt injection and outbound content for PII/GDPR leakage; can veto a run. | Writes `safety_flags`. | 2 |
| **A2A egress** | When a task needs an external specialist, delegates over **A2A** to a peer agent identified by a **signed Agent Card**. | Sends/receives A2A task. | 2 |
| **Memory** | Short-term conversation window + long-term per-user store; the self-improving loop writes distilled lessons back. | Read/write across nodes. | 1 (+2 self-improving) |

> **Interview soundbite:** "The graph has one hard rule — reads are free, writes are gated. Every side effect flows through a single Action Agent and a human-in-the-loop checkpoint, so 'zero unsafe actions' is an architectural invariant, not a prompt I'm hoping the model obeys."

Two design decisions carry most of the weight. First, **separation of read and write paths**: retrieval/research/analysis can run freely and in parallel because they cannot harm anything, while every mutation is serialised through one gated lane. Second, **the supervisor owns control flow, not the LLM's free will**: routing, loop-cutting, and budgets are graph edges and code, so the system is bounded and debuggable rather than an open-ended "let the agent decide forever" loop.

---

## The two interop layers: MCP (vertical) + A2A (horizontal)

A 2026 reference agent has **two** interoperability planes, and confusing them is a common interview trap. Kompass uses both, deliberately.

```
                 ┌─────────────────┐        A2A (horizontal, peer-to-peer)
                 │   KOMPASS agent  │◀──────────────────────────────────▶  external
                 └───────┬─────────┘   signed Agent Card, task delegation   specialist
                         │                                                   agent
              MCP (vertical, agent→tools)
                         │
        ┌────────────────┼─────────────────┐
        ▼                ▼                 ▼
   doc_search        sql server        ticketing
   MCP server        MCP server        MCP server
   (Chroma/Qdrant)   (ACME DB)         (refunds, tickets)
```

| | **MCP** — Model Context Protocol | **A2A** — Agent-to-Agent |
|---|---|---|
| Axis | **Vertical**: agent ↔ tools/data | **Horizontal**: agent ↔ agent (peer) |
| Question it answers | "How does *one* agent call *its* tools?" | "How do *independent* agents collaborate?" |
| Unit of exchange | Tool call / resource read (typed I/O) | A **task** delegated to a peer |
| Discovery / trust | Server manifest of tools | **Agent Card** (capabilities), **signed** for authenticity |
| Coupling | The agent *owns* the tool | Peers are *autonomous*, may be different vendors/frameworks |
| In Kompass | `doc_search`, `sql`, `ticketing` MCP servers (Tier 1) | delegate to an external specialist agent (Tier 2) |

The mental model: **MCP is the USB-C port for a single agent's tools; A2A is the phone call between two agents.** MCP standardises how Kompass reaches its doc-search, SQL, and ticketing capabilities behind a uniform typed contract, so tools can be swapped, versioned, and permissioned without touching graph logic. A2A standardises how Kompass hands an out-of-scope task (say, a billing-system specialist owned by another team) to a **peer agent** it does not control, using a **signed Agent Card** to discover the peer's capabilities and verify who it is talking to.

Why both, and why not fake A2A with MCP? You *could* wrap a remote agent as "just another tool," but that collapses an autonomous peer into a dumb function and loses the properties A2A is built for: capability discovery, identity/signature verification, long-running task semantics, and cross-framework interop. Kompass keeps them distinct so the boundary of ownership is explicit — inside the box is MCP (I own these tools), across the box is A2A (I coordinate with peers I don't own). Full A2A internals and the security model live in [Advanced patterns](06_advanced_patterns.md).

> **Interview soundbite:** "MCP is vertical — agent to its tools. A2A is horizontal — agent to peer agent. Kompass uses MCP for its own doc-search, SQL, and ticketing servers, and A2A with a signed Agent Card to delegate out-of-scope tasks to specialist agents it doesn't own. Wrapping a peer agent as an MCP tool throws away discovery, identity, and long-running-task semantics."

---

## The capability model, by tiers

Capabilities are grouped so the project can be **shipped and defended incrementally**: Tier 1 is the credible core, Tier 2 is what separates a portfolio from a tutorial, Tier 3 is stretch. (These mirror the live checklist in the repo `README.md`.)

### Tier 1 — Core (12)

- [ ] **Adaptive retrieval** — a router picks RAG-hybrid / CAG / GraphRAG / NL2SQL per query instead of one-size-fits-all RAG. → [02](02_retrieval_strategies.md)
- [ ] **Orchestration / planning** — a supervisor routes to workers, cuts loops, and enforces budgets.
- [ ] **Multi-agent workers** — specialist nodes (retrieval, researcher, analyst, action, critic), with a **single-agent mode** for simple queries.
- [ ] **Tool use via MCP** — `doc_search`, `sql`, and `ticketing` MCP servers behind typed contracts.
- [ ] **Memory** — short-term conversation window + long-term per-user store.
- [ ] **Reflection / self-correction** — a critic checks grounding and triggers one bounded retry.
- [ ] **Human-in-the-loop** — **declarative + durable + resumable** approve/edit/reject gate on risky actions. → [04](04_hitl_patterns.md)
- [ ] **Guardrails** — grounding + mandatory citations, prompt-injection defence, PII/GDPR handling, permissions, **typed (Pydantic) outputs**.
- [ ] **Streaming + observability** — token/step streaming to the UI; full tracing in **Langfuse**.
- [ ] **Evaluation** — golden set + RAGAS-style judge metrics (faithfulness/correctness) + task-completion + **business-value metrics** + a baseline + **CI regression gate**.
- [ ] **Model routing + caching + token budgets** — cheap model for routing, strong model for reasoning; prompt caching; hard budget caps.
- [ ] **Deploy / MLOps** — Docker, CI/CD, versioned prompts, durable (Postgres) checkpointer.

### Tier 2 — Advanced (7)

- [ ] **A2A protocol** — delegate tasks to peer agents via a **signed Agent Card**.
- [ ] **Plan-and-execute + replanning** — explicit plan, re-plan on failure or new evidence.
- [ ] **Sandboxed code execution** — the analyst runs untrusted code in an isolated sandbox.
- [ ] **Proactive / event-driven autonomy** — webhook/cron triggers a run with no human in the loop to start it.
- [ ] **Self-improving loop** — feedback and resolved cases distil into few-shots / memory.
- [ ] **User-simulator eval harness** — **τ-bench-style** simulated users drive multi-turn task completion.
- [ ] **Dedicated safety agent + prompt-injection red-team suite** — a standing adversarial test set.

### Tier 3 — Stretch

- [ ] **Multi-modal ingestion** · **Multi-agent debate** · **Saga / compensation** (undo committed side effects) · **Semantic caching + prompt compression** · **Framework comparison spike** (re-implement one worker in a second framework to justify the LangGraph choice).

> **Interview soundbite:** "I tiered the roadmap so every commit is defensible: Tier 1 is a production-shaped core, Tier 2 is the differentiators — A2A, sandboxed code, proactive autonomy, a τ-bench-style user simulator — and Tier 3 is stretch. It signals I can scope, not just build."

---

## The stack

Every choice is pinned in `kompass/config.py` and toggled by `.env`, so the same graph runs **local-first** (zero infra) or in a **durable production profile** (`docker compose up -d`).

| Layer | Choice | Why | Local default → Prod |
|---|---|---|---|
| **Framework** | **LangGraph v1.0** (Oct 2025) | Graph orchestration + **declarative HITL middleware** on top of the dynamic `interrupt()` primitive + durable checkpointing. → [03](03_framework_decision.md) | — |
| **Reasoning model** | **GPT-5.5** (`openai:gpt-5.5`) | Hardest planning / verification steps. | via model router |
| **Balanced model** | **GPT-5.4** (`openai:gpt-5.4`) | Default drafting / synthesis. | via model router |
| **Fast / routing model** | **GPT-5.4 nano** (`openai:gpt-5.4-nano`) | Cheap classification, the retrieval router, safety pre-screen → **model routing**. | via model router |
| **Vector store** | **Chroma** (local) → **Qdrant** (prod) | Hybrid dense+sparse search + reranker. | `.chroma` → `qdrant:6333` |
| **Graph retrieval** | **GraphRAG library** | Multi-hop relational questions (Tier 3-leaning). | — |
| **Tools** | **Own MCP servers** — `doc_search`, `sql`, `ticketing` | Typed, swappable, permissioned tool contracts (vertical layer). | — |
| **Observability** | **Langfuse** (self-hosted) | Full traces, cost, latency, eval scores. | disabled → `langfuse:3000` |
| **Typed outputs** | **Pydantic** | Structured, validated agent I/O; a guardrail by construction. | — |
| **Durable state** | **SQLite** (local) → **Postgres** checkpointer (prod) | Pause/resume + crash recovery for HITL. | `*.db` → `postgres:5432` |
| **UI** | **Streamlit** | Chat with citations + the HITL approve/edit/reject card. | — |
| **API** | **FastAPI** | `POST /chat`, `POST /resume`, `GET /runs/{id}`. | `:8000` |
| **Packaging / CI** | **Docker + docker-compose + GitHub Actions** | Reproducible build + CI eval-regression gate. | — |
| **Spike** | **Abstracted model client** | Lets one worker be re-implemented in a second framework for the comparison spike (Tier 3). | — |

The HITL gate in LangGraph v1 is worth showing, because it is the architectural centrepiece:

```python
# LangGraph / LangChain v1 — declarative human-in-the-loop (illustrative)
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command

action_agent = create_agent(
    model=router.pick("balanced"),               # GPT-5.4 for drafting
    tools=[mcp.ticketing, mcp.payments],         # tools exposed over MCP
    middleware=[
        # Declarative gate: pause BEFORE any side-effecting tool runs.
        HumanInTheLoopMiddleware(
            interrupt_on={
                "issue_refund": {"allowed_decisions": ["approve", "edit", "reject"]},
                "close_ticket": {"allowed_decisions": ["approve", "reject"]},
                # read-only tools omitted from the map → auto-run, no gate
            }
        )
    ],
    checkpointer=PostgresSaver.from_conn_string(settings.postgres_url),
)

# Run halts at the interrupt, PERSISTS full state, returns the draft to the UI.
# Later — possibly a different process, after a crash — resume with the decision
# (a list of approve / edit / reject decisions):
action_agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
```

`interrupt_on` is the v1 **declarative** middleware; underneath it still uses the dynamic `interrupt()` runtime primitive, but the standard **approve / edit / reject** decision types come for free. Because the state is checkpointed, the run is **durable** (survives a crash) and **resumable** (any surface can resume the same `thread_id`). Details, idempotency, and the Temporal comparison: [04](04_hitl_patterns.md).

---

## How users use it: surfaces and journeys

### Three user types

| User | Goal | Where they touch Kompass |
|---|---|---|
| **Requester** | Get a question answered or a request resolved. | Streamlit chat, Slack/Teams, or the REST `/chat` endpoint. |
| **Reviewer (HITL)** | Approve / edit / reject risky drafted actions. | The HITL card in the UI (or a Slack approval, Tier 2); calls `/resume`. |
| **Admin / operator** | Watch runs, budgets, and quality; tune prompts and thresholds. | Langfuse traces, `GET /runs/{id}`, config + versioned prompts. |

### Surfaces

- **Streamlit chat** — conversation with streamed tokens, inline citations, and the approve/edit/reject card. (Tier 1)
- **FastAPI REST** — `POST /chat` (start a run), `POST /resume` (submit a HITL decision), `GET /runs/{id}` (inspect state/history). (Tier 1)
- **Slack / Teams bot** — chat + approvals where the team already works. (Tier 2)
- **Proactive trigger** — a webhook/cron starts a run without a human initiating it. (Tier 2)
- **A2A endpoint** — Kompass exposes itself as a peer agent for other agents to delegate to. (Tier 2)

### Journey A — Knowledge question, zero human intervention

> *"How many vacation days do I have left, and can I carry them into next year?"*

```
requester ─▶ /chat ─▶ Planner ─▶ Supervisor ─▶ Retrieval Router
   ├─ NL2SQL   → queries the HR balance table  → "12.5 days remaining"
   └─ RAG      → carry-over section of the leave policy → cited passage
Researcher fuses both ─▶ Critic verifies grounding ─▶ answer
```

The router recognises a **hybrid** question — one structured fact (the balance) and one policy fact (carry-over rules) — and dispatches **NL2SQL** *and* **RAG** in parallel. The researcher composes a single answer: *"You have 12.5 days left. Up to 5 unused days carry over until 31 March next year [Leave Policy §4.2]."* The critic confirms the figure came from the query and the rule from the cited passage. **No side effect, no human** — pure read path.

### Journey B — Action with approval (the flagship)

> *"Refund order #4471 — it arrived damaged."*

```
requester ─▶ /chat ─▶ Planner ─▶ Supervisor ─▶ Action Agent
  1. verify order via MCP  → order #4471 exists, delivered, €89.00, eligible
  2. draft refund          → {order: 4471, amount: 89.00, reason: "damaged"}
  3. HITL middleware       ─▶ ⏸ PAUSE  (state checkpointed)
        reviewer sees a card: [approve] [edit amount/reason] [reject]
  4. reviewer edits to €89.00 + goodwill note, approves ─▶ /resume
  5. execute refund via MCP (idempotent)  ─▶ confirm to requester + log
```

This is the "resolves, not just answers" story. The action agent **verifies** through the ticketing/payments MCP server (never trusting the user's claim blindly), **drafts** a typed refund object, and hits the **HITL gate**, which **persists the run and stops**. A human reviewer approves, edits, or rejects; on approve/edit, the run **resumes from the checkpoint** and executes the refund **idempotently** (a retry can't double-refund), then confirms and logs. Risky money movement, zero unsafe autonomous action.

### Journey C — Proactive resolution (Tier 2)

> A new support ticket lands via **webhook** — no human asked Kompass to act.

```
webhook ─▶ Trigger ─▶ Planner ─▶ Supervisor
  classify intent + urgency ─▶ gather context (RAG policy + NL2SQL account)
  ├─ confidently resolvable & low-risk ─▶ auto-reply + close ticket (HITL if risky)
  └─ ambiguous / high-risk            ─▶ escalate to a human WITH a ready draft
```

Proactive autonomy closes the loop: Kompass classifies the incoming ticket, gathers context, and either **auto-resolves** the routine case (still routing any side effect through the HITL gate when the policy marks it risky) or **escalates with a fully drafted response**, so the human starts from 80%, not zero. This is where deflection-rate and time-per-case metrics move.

> **Interview soundbite:** "The demo is a refund: Kompass verifies the order over MCP, drafts the refund, and *stops* at a durable human-in-the-loop checkpoint. A reviewer approves or edits, the run resumes from the exact checkpoint, and the refund executes idempotently. That's the whole thesis — it resolves end-to-end, but a human owns the risky action."

---

## Repo layout mapped to capabilities

Each directory maps to a capability from the tiers above, so the codebase reads like the architecture.

```
kompass/
├── docs/            → theory (this doc = 05_architecture.md)
├── entrevista/      → interview prep (PACTEDR, question bank, solved cases)
├── corpus/          → reproducible synthetic "ACME" data
│   ├── faq/ policies/   → RAG/CAG source docs
│   └── sql/             → seed for the NL2SQL operational DB
├── kompass/         → the package
│   ├── graph/       → Planner + Supervisor + workers + routers + HITL middleware   [T1 orchestration, planning, HITL]
│   ├── retrieval/   → rag_hybrid · cag · nl2sql · router                           [T1 adaptive retrieval]
│   ├── mcp_servers/ → doc_search · sql · ticketing (MCP — vertical layer)          [T1 tool use via MCP]
│   ├── a2a/         → Agent Card + A2A server/client (horizontal layer)            [T2 A2A protocol]
│   ├── memory/      → short-term conversation + long-term per-user store            [T1 memory, +T2 self-improving]
│   ├── guardrails/  → grounding+citations · prompt-injection · PII/GDPR · perms     [T1 guardrails, +T2 safety agent]
│   ├── models/      → model router · caching · token budgets (abstracted client)    [T1 model routing, +T3 spike]
│   ├── sandbox/     → isolated code execution for the Data Analyst                  [T2 sandboxed code exec]
│   ├── triggers/    → webhook / cron entry points                                   [T2 proactive autonomy]
│   ├── api/         → FastAPI: /chat · /resume · /runs/{id}                          [T1 serving surface]
│   └── scripts/     → seed + demo (make seed / make demo)                            [reproducibility]
├── spike_frameworks/→ one worker re-implemented in a 2nd framework + comparison.md   [T3 framework spike]
├── evals/           → golden set · judge metrics · task-completion · value metrics · baseline[T1 evaluation]
│   └── user_simulator/ → τ-bench-style simulated-user harness                        [T2 eval harness]
├── ui/              → Streamlit chat + HITL card                                     [T1 streaming UI]
├── tests/           → unit + smoke                                                   [T1 CI]
└── .github/workflows/ci.yml → CI + eval-regression gate                              [T1 deploy/MLOps]
```

The mapping is deliberate: a reviewer can open any directory and name the capability it delivers, and every capability in the checklist has a home in the tree.

> **Interview soundbite:** "The directory tree is the architecture — `graph/` is orchestration and HITL, `retrieval/` is the adaptive router, `mcp_servers/` is the vertical tool layer, `a2a/` is the horizontal peer layer. There's no capability on my checklist that doesn't have a folder, and no folder that isn't a capability."

---

## Related

- [01 — What is agentic AI](01_agentic_ai_deep_dive.md) — the spectrum from workflow to agent, and the design patterns Kompass instantiates.
- [02 — Retrieval strategies](02_retrieval_strategies.md) — how the Retrieval Router chooses RAG-hybrid / CAG / GraphRAG / NL2SQL per query.
- [03 — Why LangGraph v1](03_framework_decision.md) — the framework decision, trade-offs, and sources.
- [04 — HITL patterns](04_hitl_patterns.md) — declarative + durable + resumable interrupts, idempotency, and the Temporal comparison.
- [06 — Advanced patterns](06_advanced_patterns.md) — A2A internals, plan-and-execute, the sandbox, proactive autonomy, and the self-improving loop.
- [Interview — PACTEDR framework](../entrevista/framework_PACTEDR.md) — how to narrate this architecture under interview pressure.
