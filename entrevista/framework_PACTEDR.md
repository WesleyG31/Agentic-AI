# The P-A-C-T-E-D-R Framework for Agentic AI Business Cases

A repeatable reasoning structure for **any** agentic-AI system-design or business-case
interview question — the kind that starts with *"How would you design an agent that…?"*
The goal is not to recite buzzwords; it is to sound like an engineer who has shipped these
systems and can walk from **business problem → architecture → evaluation → production risk**
without dropping a thread.

P-A-C-T-E-D-R forces you to (a) anchor on business value before touching technology,
(b) justify *whether an agent is even the right tool*, and (c) close the loop on evaluation,
cost, and risk — the three areas where most candidates go quiet and where senior interviewers
probe hardest.

> **Interview soundbite:** "I run every agentic design question through the same seven lenses —
> Problem, Agent-or-not, Capabilities, Tech, Eval, Deploy, Risks — so I never ship a clever
> architecture that solves the wrong problem or can't be measured in production."

---

## The mnemonic

```
P — Problem & KPI      ── what business number moves, and by how much?
A — Agent-or-not       ── does this even need an agent? justify the agency.
C — Capabilities       ── retrieval / tools / actions / memory / multi-step / multi-agent
T — Tech design        ── architecture + pattern + retrieval strategy + tools/MCP
E — Evaluation         ── offline (golden set, RAGAS, LLM-judge) + online (A/B, feedback)
D — Deploy & cost      ── latency, streaming, caching, routing, scaling, durable execution
R — Risks & guardrails ── hallucination, injection, PII/GDPR, permissions, HITL, ROI
```

**Say it as a sentence:**
> **P**ractical **A**gents **C**reate **T**rustworthy, **E**valuated, **D**eployable **R**esults.

The ordering is deliberate and directional: each step constrains the next. You cannot pick a
pattern (**T**) before you know which capabilities (**C**) you need, and you can't scope
capabilities before deciding you even need an agent (**A**), which you can't decide before you've
reframed the problem and its KPI (**P**). Evaluation, deploy, and risk (**E-D-R**) are where you
prove the thing is *real* and not a demo.

---

## Cheat sheet

| Step | One question to *always* ask | One strong answer move |
|------|------------------------------|------------------------|
| **P** — Problem & KPI | "What business number moves, and what's the baseline vs. target?" | Convert to money/time: `deflection% × volume × cost-per-unit = $ saved`. |
| **A** — Agent-or-not | "Is the control flow known up front (workflow) or discovered at runtime (agent)?" | *Default to a workflow; earn the agent.* Justify agency by unpredictable steps + real actions. |
| **C** — Capabilities | "Read-only knowledge, or does it need to *act* and *remember*?" | Draw the capability checklist; split **read tools** (safe) from **write/act tools** (need HITL). |
| **T** — Tech design | "What's the simplest pattern that supports these capabilities?" | Sketch the graph as ASCII; name the pattern; justify retrieval from the *data shape*. |
| **E** — Evaluation | "What's the golden set, and how do you catch regressions before users do?" | Three tiers: component → end-to-end trajectory → live business KPI. *Eval is the product.* |
| **D** — Deploy & cost | "What's the latency budget and the cost per resolution vs. its value?" | The triad: **model routing + caching + durable execution**; quote a cost-per-ticket number. |
| **R** — Risks & guardrails | "What's the worst thing this agent can do if it's wrong or manipulated?" | Separate **content** (hallucination), **action** (bad write), **data** (PII/GDPR) risks; gate each risky action with HITL. |

---

## P — Problem & KPI

**Reframe the problem, then define the business metric.** Interviewers plant an ambiguous prompt
("build a support agent") to see whether you interrogate it or leap to LangChain diagrams. The
first move is always to *scope and quantify*.

**Key questions to ask:**
- Who is the user (end customer, internal employee, analyst) and what is the current process?
- What is painful today — cost, latency, backlog, error rate, agent burnout?
- What is the **business KPI**, its **baseline**, and a realistic **target**?
- What's the volume/scale (tickets/month, docs, queries/sec)? This decides everything downstream.
- What does **"resolved" actually mean** — an answer, or a completed action in a system of record?

**Answer well by separating proxy metrics from business KPIs.** Model metrics (accuracy, F1,
faithfulness) are *diagnostics*; they are not the thing the business buys. Name the real one:

| Domain | Business KPI (what leadership tracks) |
|--------|----------------------------------------|
| Customer support | Auto-resolution / **deflection rate**, CSAT/NPS, cost per contact, first-contact resolution |
| IT helpdesk | **MTTR**, tickets auto-resolved, agent hours saved, backlog |
| HR / ops | Cycle time per request, self-service rate, compliance error rate |
| Analytics (NL2SQL) | Time-to-insight, self-serve query %, analyst hours reclaimed |

Then **convert to money or time** — that sentence is what makes you sound senior:

> *"5,000 IT tickets/month, ~15 min human handling each. If we auto-resolve 40%, that's
> 2,000 tickets × 15 min = 500 engineer-hours/month reclaimed — the KPI is deflection rate,
> and I'd hold generation quality as a guardrail so we don't buy deflection with wrong answers."*

> **Interview soundbite:** "I never optimize accuracy in a vacuum — I tie the model metric to a
> business KPI with a baseline and a target, because a 2% F1 gain that doesn't move deflection or
> MTTR isn't worth shipping."

---

## A — Agent-or-not

**Does this need an agent at all?** The most impressive answer is often *"this doesn't need one."*
Agency adds latency, cost, non-determinism, and a large attack/failure surface — you should have
to *earn* it. Place the problem on the spectrum (developed fully in
[the agentic AI deep dive](../docs/01_agentic_ai_deep_dive.md)):

```
single LLM call  →  RAG pipeline  →  router / chain  →  tool-calling agent  →  planning agent  →  multi-agent
   (cheapest,          (fixed          (branch on         (LLM picks tools      (LLM plans its      (specialised
    fully                workflow)       intent)            in a loop)            own steps)          sub-agents)
    determined)                                                                                    
◄──────────────── more predictable, cheaper, easier to eval ─────── more capable, riskier, harder to eval ──────────►
```

The industry distinction (Anthropic's *Building Effective Agents*): **workflows** orchestrate LLMs
and tools through *predefined code paths*; **agents** let the LLM *dynamically direct its own
process and tool use*. Neither is "better" — the question is whether your control flow is known
ahead of time.

**Key questions to ask:**
- Is the sequence of steps knowable in advance (→ workflow), or must it be discovered per input (→ agent)?
- Does the task require **taking actions in the world**, or only producing text/answers?
- Is there genuine branching and multi-step reasoning, or is it retrieve-then-answer?
- What's the cost of a wrong autonomous action vs. the cost of being slightly less flexible?

**Answer well by defaulting down the spectrum and justifying each step up.** "Most of this volume
is FAQ-shaped, so a RAG workflow handles it deterministically and cheaply. The minority that
require *actions* across multiple systems with input-dependent steps is where I introduce a
tool-calling agent — and I'd route between them rather than making everything agentic." That is a
senior answer: it shows you optimize for reliability and cost, not novelty. See
[the framework decision doc](../docs/03_framework_decision.md) for why the substrate is
LangGraph v1.0 once you *do* need graph-structured agency.

> **Interview soundbite:** "I default to the simplest thing that works and earn my way up the
> spectrum — a workflow I can fully evaluate beats an agent I can't, and 'this is really a routed
> RAG pipeline, not an agent' is frequently the correct, more shippable answer."

---

## C — Capabilities

**Enumerate what the system must be *able to do*, independent of how you'll build it.** This is a
checklist you can literally recite; ticking boxes keeps you from over- or under-scoping.

| Capability | Question that turns it on | Notes |
|------------|---------------------------|-------|
| **Retrieval** | "What knowledge does it need that isn't in the prompt?" | KB, docs, tickets → drives the retrieval strategy in **T**. |
| **Read tools** | "What live system state must it look up?" | Account status, order, ticket, inventory. Safe — no side effects. |
| **Write / act tools** | "Does it change state in the world?" | Reset password, issue refund, grant access, file a ticket. **These need HITL and least-privilege.** |
| **Memory** | "Must it remember within a session? Across sessions?" | Short-term (thread state) vs. long-term (user prefs, past resolutions). |
| **Multi-step reasoning** | "Does solving this require planning/decomposition?" | If yes, lean toward a planning or ReAct pattern in **T**. |
| **Multi-agent** | "Are there distinct roles with different tools/policies?" | Only when a single agent's tool list or prompt becomes unmanageable — don't reach for it early. |

**Answer well by tying each capability back to the Problem and flagging the risky ones.** The
single most valuable distinction to voice here is **read vs. write**: read tools are cheap to
allow, write/act tools are exactly where **R** (permissions + HITL) will bite. Naming that split in
**C** shows the interviewer you're already thinking about the blast radius.

> **Interview soundbite:** "I scope capabilities as an explicit checklist and immediately separate
> read tools from state-changing action tools — because the write tools are where value *and* risk
> both live, and they're what forces human-in-the-loop into the design."

---

## T — Tech design

**Now, and only now, the architecture.** Four sub-decisions: orchestration **pattern**, the
**graph/architecture**, the **retrieval strategy**, and **tools/MCP**.

**Orchestration pattern** (choose the least powerful that fits **C**):

| Pattern | When |
|---------|------|
| Router / classify-then-branch | Mixed intents; cheap first hop to the right handler. |
| ReAct / tool-calling loop | Agent reasons, calls tools, observes, repeats until done. |
| Plan-and-execute | Task benefits from an explicit up-front plan before acting. |
| Reflection / self-critique | Output quality matters and can be self-checked (code, drafts). |
| Supervisor / multi-agent | Distinct roles with separate toolsets and policies. |

**The substrate is LangGraph v1.0** (decision final — see
[03_framework_decision.md](../docs/03_framework_decision.md)): a stateful graph of nodes and edges
with a **checkpointer** for durable state, the dynamic **`interrupt()`** primitive, and — new in
the Oct 2025 v1.0 release — **declarative human-in-the-loop middleware** (`interrupt_on`) exposing
standard **approve / edit / reject** decision types over your action tools
(see [04_hitl_patterns.md](../docs/04_hitl_patterns.md)).

```python
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langgraph.checkpoint.postgres import PostgresSaver

agent = create_agent(
    model=router_and_reasoner_models,          # small model routes, large model reasons
    tools=[search_kb, check_account,           # read tools — auto-run
           reset_password, grant_access],      # action tools — gated below
    middleware=[
        HumanInTheLoopMiddleware(
            interrupt_on={                      # declarative HITL (v1.0)
                "grant_access":   {"allowed_decisions": ["approve", "edit", "reject"]},
                "reset_password": {"allowed_decisions": ["approve", "edit", "reject"]},
                # read tools omitted from the map → auto-run, no gate
            }
        )
    ],
    checkpointer=PostgresSaver(...),            # durable: the paused run survives restarts
)
```

**Retrieval strategy** — justify it from the *shape of the data*, don't just say "vector search."
Pull the full toolkit from [02_retrieval_strategies.md](../docs/02_retrieval_strategies.md):
chunking policy, **hybrid search** (BM25 + dense) for keyword-heavy domains, **reranking** for
precision, **query rewriting / HyDE** for messy user phrasing, **metadata filtering** for
multi-tenant or product-scoped corpora, and **agentic/iterative retrieval** when one query isn't
enough. The answer move: *"Support tickets are keyword-heavy and product-scoped, so hybrid search
+ metadata filter on `product`, then a cross-encoder rerank — dense-only would miss error codes."*

**Tools & MCP** — standardize tool access behind **MCP servers** so ServiceNow / Jira / AD / Okta
are swappable, versioned, and independently permissioned (this is the pattern Wesley runs in
production at VW/CARIAD). Then sketch the graph as ASCII (see the worked example below).

> **Interview soundbite:** "I pick the least powerful orchestration pattern that fits the
> capabilities, build it on LangGraph v1.0 for durable state and declarative approve/edit/reject
> HITL, and expose external systems through MCP servers so tools are versioned and least-privileged
> rather than hard-wired."

---

## E — Evaluation

**Eval is the product, not an afterthought.** If you can't measure it, you can't ship it, and you
can't defend it in the interview. Structure the answer in three tiers.

**1. Offline — component level (fast, cheap, run in CI):**
- *Retrieval:* recall@k, MRR, nDCG against a labeled query→doc set. Bad retrieval caps everything.
- *Generation:* **RAGAS** — faithfulness (is the answer grounded in context?), answer relevance,
  context precision/recall.
- *LLM-as-judge* for open-ended quality, with a rubric and a human-calibrated sample.

**2. Offline — end-to-end / trajectory (for agents specifically):**
- Did the agent call the **right tools in a sensible order**? Trajectory/tool-call evaluation.
- Task success on a **golden set** of real historical cases with known correct resolutions.

**3. Online — in production:**
- **A/B tests** and **shadow mode** (run the agent silently, compare to human) before rollout.
- Thumbs up/down + free-text feedback, **guardrail trip rates**, escalation rate.
- Dashboards on the **business KPI** from **P** — deflection, MTTR, CSAT — segmented by cohort.

**Answer well by naming the golden set and the regression gate.** "I'd curate ~200 real tickets
with verified resolutions as a golden set, gate merges on RAGAS faithfulness + trajectory success
in CI, then shadow-run against live traffic and A/B on deflection with CSAT as a guardrail." That
sentence covers offline, online, and regression protection in one breath.

> **Interview soundbite:** "I evaluate at three tiers — component metrics like retrieval recall and
> RAGAS faithfulness, end-to-end trajectory success on a golden set in CI, and online A/B on the
> business KPI with feedback — because a demo that looks great and silently regresses is the most
> common way these projects die."

---

## D — Deploy & cost

**Make it fast enough, cheap enough, and resilient enough to survive real traffic and human
pauses.** Five levers:

| Lever | What to say |
|-------|-------------|
| **Latency** | Budget p50/p95. Perceived latency matters — an agent that acts in 8s can *feel* instant with streaming. |
| **Streaming** | Stream tokens **and** intermediate steps ("searching KB… checking your account…") so the user sees progress. |
| **Caching** | **Prompt caching** for the static system/policy prefix; **semantic cache** for repeated questions; cache retrieval results. |
| **Model routing** | Small/cheap model to classify & route and handle easy cases; large model only for hard reasoning. Biggest cost lever. |
| **Scaling** | Async I/O, bounded concurrency, queue for spikes; horizontal scale of stateless workers. |
| **Durable execution** | LangGraph **checkpointer** persists state → runs are **resumable** and survive crashes *and* HITL pauses (a run waiting on approval can wait hours without holding a process). |

**Answer well by quoting the cost math and the triad.** "The triad is model routing + caching +
durable execution. Routing 70% of traffic to a small model and prompt-caching the policy prefix
takes cost per resolution to roughly \$0.02–0.05 — trivially below the ~\$5 loaded cost of a human
touch, so the ROI is dominated by volume." Then note durable execution is *why* HITL is practical:
the paused run isn't a hung server thread, it's a checkpoint.

> **Interview soundbite:** "My deploy answer is always routing + caching + durable execution:
> route cheap traffic to a small model, cache the static prefix, and checkpoint state so a run
> paused on human approval can resume hours later — that's what makes cost-per-resolution beat the
> human baseline and makes HITL operationally real."

---

## R — Risks & guardrails

**Name the worst thing the agent can do — then show the control for it.** Separate three distinct
risk classes; conflating them is a junior tell.

| Risk class | Failure | Guardrail |
|------------|---------|-----------|
| **Content** | Hallucination, wrong/confident answer | Grounding + **citations**, "I don't know" / abstain path, confidence thresholds, faithfulness eval. |
| **Action** | Unauthorized or destructive write (grant, refund, delete) | **HITL** approve/edit/reject on risky tools, **least-privilege scoped tokens**, dry-run/undo, audit log. |
| **Data** | PII leakage, **GDPR** violation | PII detection/redaction, data residency (EU), right-to-erasure, minimize what's logged, DPA-compliant vendors. |
| **Adversarial** | **Prompt injection** via retrieved docs or tool output | Treat retrieved/tool content as **untrusted**; sanitize; don't let content escalate tool permissions; separate trusted instructions from data. |
| **Operational** | Infinite loops, tool errors, timeouts | Step/loop caps, retries with backoff, tool-error fallbacks, graceful human escalation. |

Two points that land especially well with a **German/EU** audience: (1) prompt injection is most
dangerous through the *retrieval and tool-output channel*, not just the user message — a poisoned
KB article can hijack an agent that has write tools, which is exactly why least-privilege + HITL on
actions matters; (2) **GDPR** is a first-class design constraint (PII minimization, EU data
residency, audit trails, erasure), not a compliance checkbox added at the end. Full patterns —
including where the LangGraph `interrupt_on` approve/edit/reject gates sit — are in
[04_hitl_patterns.md](../docs/04_hitl_patterns.md).

Close with **ROI as a risk lens**: the guardrail on the *business* is that autonomous actions have
bounded blast radius (per-action spend/permission limits, HITL above a risk threshold) so a bad
agent decision can't cost more than a human error would.

> **Interview soundbite:** "I split risk into content, action, and data classes: hallucination gets
> grounding and citations, state-changing tools get least-privilege plus approve/edit/reject HITL,
> and PII gets GDPR-grade handling with audit trails — and I treat retrieved content as untrusted
> because prompt injection usually rides in through the knowledge base, not the user."

---

## Worked mini-example: an employee IT-ticket agent

**Prompt:** *"Design an agent that handles employee IT tickets."* Here is the full seven-step pass,
compressed to interview pace.

**P — Problem & KPI.** Internal IT helpdesk, ~5,000 tickets/month, ~15 min avg human handling.
Pain: backlog and engineer time on repetitive resets/access requests. **KPI: auto-resolution
(deflection) rate**, secondary **MTTR** and **engineer-hours saved**; CSAT held as a guardrail.
Baseline deflection ~0%; target 40% → ~500 engineer-hours/month reclaimed. "Resolved" = ticket
closed *with the action actually performed*, not just an answer.

**A — Agent-or-not.** Hybrid. The long tail of "how do I…?" tickets is FAQ-shaped → a **RAG
workflow**, deterministic and cheap. The subset needing **actions** across systems (password
reset, VPN config, access request) with input-dependent steps → a **tool-calling agent**. So:
*route first*, don't make everything agentic.

**C — Capabilities.** Retrieval (IT knowledge base) · read tools (account status, ticket lookup) ·
**write/act tools** (reset password, grant access, file/close ticket) · memory (user context +
thread) · multi-step reasoning · multi-agent *not* needed (one agent with a router suffices).

**T — Tech design.** LangGraph v1.0 router-then-resolve graph; MCP servers front ServiceNow / AD /
Okta / Jira. Retrieval: **hybrid (BM25 + dense) + rerank + metadata filter on `product`** — IT KB
is error-code-heavy, so dense-only would miss exact strings. `interrupt_on` gates the privileged
action tools (approve/edit/reject); a Postgres checkpointer makes paused approvals durable.

```
                        ┌─────────────┐
   employee ticket ───► │  classify    │  (small model: intent + risk)
                        └──────┬───────┘
              ┌────────────────┼──────────────────┐
              ▼                ▼                   ▼
      ┌──────────────┐  ┌──────────────┐    ┌──────────────┐
      │  RAG answer   │  │ tool-calling │    │  escalate to │
      │ (how-to FAQ)  │  │  resolver     │    │  human queue │
      └──────────────┘  └──────┬───────┘    └──────────────┘
                               │
                     read tools│  (account/ticket lookup — auto)
                               ▼
                     ┌───────────────────────┐
                     │  action tool call      │
                     │  (reset_pw/grant_access)│
                     └──────────┬────────────┘
                                ▼
                     ┌───────────────────────┐
                     │ interrupt_on HITL gate │  ← approve / edit / reject
                     │ (only for privileged   │     (durable via checkpointer)
                     │  or destructive acts)  │
                     └───────────────────────┘
```

**E — Evaluation.** Golden set of ~200 historical tickets with known resolutions. Offline:
retrieval recall@k, RAGAS faithfulness on answers, **trajectory eval** (did it call the *right*
tool?). Online: shadow mode vs. human, then A/B on **deflection** with CSAT + escalation rate as
guardrails; CI gate blocks merges that regress faithfulness or trajectory success.

**D — Deploy & cost.** Small model classifies/routes; large model only on the resolver path.
Prompt-cache the system + IT-policy prefix; semantic-cache common questions. Stream progress
("checking your account…"). Durable checkpointer so a ticket paused on approval survives restarts.
Cost per auto-resolved ticket ≈ \$0.03 vs. ~\$5 loaded human cost.

**R — Risks & guardrails.** Prompt injection via ticket free-text (treat as untrusted; never let it
escalate tool scope). **GDPR**: employee data is PII — redact in logs, EU residency, audit every
action. **Least-privilege scoped tokens** per action tool. **HITL approve/edit/reject** on
privileged access grants and anything destructive; auto-run only read tools and low-risk resets.
Loop/step caps and a clean fallback to the human queue on tool errors or low confidence.

That is a complete, defensible design delivered in the same seven beats every time.

---

## How to run the framework in a live interview

- **Timebox it.** In a 30–45 min case, spend the first ~5 min on **P** and **A** (out loud —
  narrate the reframing), the bulk on **C-T**, and reserve the last third for **E-D-R**, which is
  where you separate from the pack. Never let the clock run out before **Risks**.
- **Narrate the funnel.** Say "let me place this on the workflow-vs-agent spectrum first" — the
  interviewer is scoring your *process*, not just the final diagram.
- **Draw one ASCII graph.** A single clear diagram beats three paragraphs.
- **Always quote one number.** A cost-per-ticket or hours-saved figure signals you think in ROI.
- **End on risk, not features.** Closing with GDPR + HITL + prompt-injection leaves a senior,
  EU-aware impression.

---

## Related & next steps

Apply this framework end-to-end on the four solved cases, then drill with the question bank:

- [Caso 01 — Enterprise Knowledge Assistant](casos/caso_01_knowledge_assistant.md)
- [Caso 02 — Customer Support Automation](casos/caso_02_customer_support.md)
- [Caso 03 — Document Processing at Scale](casos/caso_03_document_processing.md)
- [Caso 04 — NL2SQL Analytics Agent](casos/caso_04_nl2sql_analyst.md)
- [Banco de preguntas — the question bank](banco_preguntas.md)

Underlying theory (all in `docs/`):

- [01 — Agentic AI deep dive](../docs/01_agentic_ai_deep_dive.md) — the workflow↔agent spectrum behind **A**.
- [02 — Retrieval strategies](../docs/02_retrieval_strategies.md) — the retrieval toolkit behind **T**.
- [03 — Framework decision](../docs/03_framework_decision.md) — why the substrate is LangGraph v1.0.
- [04 — HITL patterns](../docs/04_hitl_patterns.md) — `interrupt()` and declarative `interrupt_on` for **R**.
- [05 — Architecture](../docs/05_architecture.md) — how Kompass wires these steps together.
- [06 — Advanced patterns](../docs/06_advanced_patterns.md) — multi-agent, reflection, planning.
