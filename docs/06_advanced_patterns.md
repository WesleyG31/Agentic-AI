# Advanced Agentic Patterns (Frontier 2026)

> **Scope.** This document catalogues the **Tier 2 and Tier 3** capabilities that separate a
> production-grade agent from a demo. The [foundations](01_agentic_ai_deep_dive.md) (ReAct, tool
> use, memory), [retrieval](02_retrieval_strategies.md), the [framework
> decision](03_framework_decision.md) and [human-in-the-loop patterns](04_hitl_patterns.md) are the
> *table stakes*. The patterns below are the **differentiators** — the things that, in an interview,
> move you from "built a RAG chatbot" to "designed an autonomous, self-improving, interoperable
> operations platform." Every pattern is mapped to a concrete Kompass module so the claim is
> falsifiable, not hand-wavy.

Kompass is a support and operations assistant on **LangGraph v1.0** that *resolves and acts*, not
just answers (see [05_architecture.md](05_architecture.md) for the system view). The frontier
patterns here extend that core: they let Kompass talk to *other* agents, plan under uncertainty,
execute code safely, wake itself up on events, learn from its own transcripts, and prove its own
reliability at scale.

---

## Tier map — table stakes vs. differentiators

| Tier | Capability | Why it matters | Kompass module |
|------|-----------|----------------|----------------|
| 1 (baseline) | ReAct, tool calling, RAG, HITL approvals | Everyone has this | `kompass/graph`, `kompass/retrieval` |
| **2** | A2A interop, plan-and-execute + replanning, sandboxed code, event-driven triggers | Few candidates ship these | `kompass/a2a`, `kompass/graph`, `kompass/sandbox`, `kompass/triggers` |
| **2** | Self-improving loop, user-simulator evals, guardrail/red-team suite | Signals *engineering* maturity, not just prompting | `kompass/memory`, `evals/user_simulator`, `kompass/guardrails` |
| **3** (stretch) | Multi-modal ingest, debate/judge panel, saga/compensation, semantic cache + prompt compression | Frontier research made practical | see [§8](#8-tier-3-stretch-capabilities) |

> **Interview soundbite:** "Anyone can wire an LLM to a vector store. What I optimise for is the
> stuff that breaks in production — interop, replanning on failure, sandbox isolation, and a
> user-simulator harness that proves reliability with pass^k, not a single happy-path demo."

---

## 1. A2A vs MCP — the two-layer interoperability model

The single most under-appreciated architectural idea in 2026 agent design is that **interop is two
orthogonal axes**, and you need a different protocol for each:

```
              ┌──────────────────────────────────────────┐
   A2A  ◄────►│  Agent   ◄──────A2A (horizontal)─────►  Agent   │   peer-to-peer, agent<->agent
 (horizontal) │ (Kompass)│                             │ (Billing │   task delegation, negotiation
              │          │                             │  agent)  │
              └────┬─────┘                             └────┬─────┘
                   │ MCP (vertical)                         │ MCP
                   ▼                                        ▼
            ┌──────────────┐                        ┌──────────────┐
            │ Tools / APIs │                        │  Tools / DBs │   agent<->resource
            │ Jira, Zendesk│                        │  Postgres    │   capability access
            └──────────────┘                        └──────────────┘
```

- **MCP (Model Context Protocol) = vertical.** It connects *one* agent down to its **tools,
  resources and prompts** (Jira, the ticket DB, the NL2SQL server). It is the "USB-C for tools"
  layer. Kompass exposes and consumes MCP servers under `kompass/mcp_servers` — this is the
  production pattern Wesley already ships at VW/CARIAD.
- **A2A (Agent2Agent) = horizontal.** It connects *peer* agents to each other so they can
  **discover, delegate and negotiate tasks** without sharing memory or internal state. A2A treats
  the remote agent as an **opaque, autonomous peer**, not a callable function.

The two are **complementary, not competing** — the emerging consensus (see TrueFoundry and the
Zylos convergence survey below) is "MCP for tools, A2A for agents," with ACP as a third
communication-oriented variant. A mature platform speaks both.

### The A2A Agent Card — a signed capability manifest

Discovery in A2A is grounded in an **Agent Card**: a JSON manifest, conventionally served at a
well-known URI (`/.well-known/agent-card.json`), that advertises *what the agent can do* and *how to
reach it securely*. It can be **cryptographically signed (JWS)** so a consumer can verify
authenticity and integrity before delegating work — this is what turns ad-hoc HTTP calls into a
trust-bearing protocol.

```jsonc
// kompass/a2a/agent_card.json  (served at /.well-known/agent-card.json)
{
  "name": "kompass-ops-agent",
  "description": "Support & operations resolver: triages, resolves, and acts on tickets.",
  "url": "https://kompass.example.com/a2a",
  "version": "1.0.0",
  "capabilities": { "streaming": true, "pushNotifications": true },
  "defaultInputModes": ["text", "application/json"],
  "skills": [
    { "id": "triage_ticket", "name": "Ticket triage",
      "description": "Classify, prioritise, and route a new support ticket.",
      "tags": ["support", "triage"] },
    { "id": "resolve_refund", "name": "Refund resolution",
      "description": "Execute a refund within policy; HITL above threshold." }
  ],
  "securitySchemes": { "oauth2": { "type": "oauth2", "flows": { "clientCredentials": {} } } },
  "signatures": ["<JWS-detached-signature>"]   // integrity + authenticity
}
```

### Task delegation lifecycle

A2A models work as a **task** with an explicit state machine — so delegation is observable and
resumable, and long-running work maps cleanly onto Kompass's LangGraph checkpoints:

```
submitted ─► working ─► input-required ─► working ─► completed
                 │                                      ▲
                 └──────────────► failed / canceled ────┘
```

Messages carry parts (text, data, files); results come back as **artifacts**. Progress streams over
SSE; long jobs can fire **push notifications** back to the delegator. Under the hood a delegated
task can *itself* pause on a Kompass HITL interrupt (see [§2](#2-plan-and-execute-with-replanning)
and [04_hitl_patterns.md](04_hitl_patterns.md)) — the `input-required` state is the A2A-native way to
surface that.

### Why this is a portfolio differentiator

Almost nobody implementing agents today has actually wired up A2A. Shipping a **signed Agent Card +
discovery + a delegated task round-trip** demonstrates that you understand the *systems* dimension of
agentic AI — multi-agent orgs, trust boundaries, and interop standards — not just prompt
engineering. It is a direct signal for the kind of platform work German/EU AI-engineering teams (DHL
Data & AI, VW/CARIAD) are hiring for.

**Kompass module:** `kompass/a2a` (Agent Card, discovery endpoint, task-delegation client/server).

> **Interview soundbite:** "I think of interop as two axes: MCP is vertical — my agent down to its
> tools; A2A is horizontal — my agent talking to peer agents as opaque, autonomous services with a
> signed capability card. Kompass speaks both, so it can be embedded in a larger agent org."

---

## 2. Plan-and-Execute with replanning

Pure **ReAct** interleaves one thought and one action at a time — flexible, but myopic: it never
commits to a strategy, so it drifts, loops, and burns tokens on long multi-step ops. **Plan-and-
Execute** instead has an explicit **planner** produce a *versioned, inspectable plan* up front, an
**executor** run the steps, and — critically — a **replanner** that revises the plan when reality
diverges.

```
        ┌─────────┐   plan v1     ┌──────────┐  step result   ┌───────────┐
 goal ─►│ Planner │──────────────►│ Executor │───────────────►│ Replanner │
        └─────────┘               └────┬─────┘                └─────┬─────┘
             ▲                         │ per-step                   │
             │   plan v2 (revised)     ▼                            │
             └───────────── replan if: N failures OR ───────────────┘
                            result contradicts plan   │
                                                       ▼
                                          (critical step?) ──► HITL interrupt
```

**Replan triggers** (Kompass policy, encoded in the graph state):

| Trigger | Rule | Response |
|---------|------|----------|
| Repeated failure | ≥ **N** consecutive tool/step failures (default N=2) | Regenerate plan from current state, bump `plan_version` |
| Contradiction | Observed result contradicts a plan assumption | Replan and log the invalidated assumption |
| Critical step | Step tagged `risk: high` (refund, delete, external send) | **HITL** approve / edit / reject before execution |
| Budget | Step/token/time budget exceeded | Replan for a cheaper path or escalate to human |

### Contrast with ReAct

| Dimension | ReAct | Plan-and-Execute + Replan |
|-----------|-------|---------------------------|
| Strategy | Emergent, step-by-step | Explicit, versioned artifact |
| Long-horizon tasks | Drifts / loops | Stays on plan; replans deliberately |
| Auditability | Hard (reasoning is transient) | Plan is a first-class, diffable object |
| Cost | Many small LLM calls | Fewer, larger planning calls |
| **Security** | Untrusted tool output can hijack the *next* thought | Plan is fixed *before* untrusted data is ingested |

### Security: plan *then* execute

The **Secure Plan-then-Execute** line of work (arXiv 2509.08646, cited below) makes the key point:
if the agent commits to a plan **before** it is exposed to untrusted data (tool outputs, retrieved
documents, ticket bodies), then a **prompt injection buried in that data cannot rewrite the high-
level plan** — it can only, at worst, corrupt a single step's arguments, which the guardrail layer
([§7](#7-guardrail--safety-agent--prompt-injection-red-team-suite)) validates. Planning *constrains
the blast radius* of injection. This pairs naturally with LangGraph's control-flow and Kompass's
guardrails.

### LangGraph sketch

Kompass implements this as a planner/executor/replanner subgraph. HITL uses LangGraph v1.0's
**declarative `interrupt_on` middleware** on high-risk tools, backed by the dynamic runtime
`interrupt()` primitive — the same mechanism documented in [04_hitl_patterns.md](04_hitl_patterns.md)
and justified in [03_framework_decision.md](03_framework_decision.md).

```python
# kompass/graph/plan_execute.py  (LangGraph v1.0 pseudocode)
class PlanState(TypedDict):
    goal: str
    plan: list[Step]           # versioned, inspectable
    plan_version: int
    done: list[StepResult]
    failures: int

def planner(state):  ...       # -> {"plan": [...], "plan_version": 1}

def executor(state):
    step = next_pending(state["plan"])
    if step.risk == "high":
        # declarative HITL: approve / edit / reject before acting
        decision = interrupt({"action": step, "type": "approval"})
        step = apply_decision(step, decision)
    return run(step)

def replanner(state):
    if state["failures"] >= N or contradicts_plan(state):
        return {"plan": planner(state)["plan"],
                "plan_version": state["plan_version"] + 1, "failures": 0}
    return state  # keep executing

graph.add_conditional_edges("executor", route)  # -> replanner | executor | END
```

**Kompass module:** `kompass/graph` (planner / executor / replanner subgraph + replan policy).

> **Interview soundbite:** "ReAct is greedy and myopic; plan-and-execute commits to a versioned plan
> and only replans on a failure threshold or a contradiction. It's also a *security* control — the
> plan is fixed before we ingest untrusted data, so an injected instruction can't rewrite the
> strategy, only rattle one step that guardrails then validate."

---

## 3. Sandboxed code execution

For data analysis, format transforms, and ad-hoc computation, the most powerful tool an agent can
have is the ability to **write and run code**. But arbitrary code from an LLM is also the single
largest attack surface in the whole system, so it must run in a **hardened, isolated sandbox** with
**typed inputs and structured outputs**.

**What / why / when.** *What:* the agent emits code (usually Python) against a declared schema; the
sandbox runs it and returns a validated result object. *Why:* code beats a hundred bespoke tools for
open-ended data work (pivots, joins, chart data, regex cleanups). *When:* the task is computational
and the tool-catalogue would otherwise explode — never for actions that touch production systems
directly (those go through typed MCP tools with HITL).

```
 agent ─► code (str) ─► ┌───────────────── SANDBOX ──────────────────┐ ─► StructuredResult
                        │ • no network egress (deny by default)       │
                        │ • CPU / memory / wall-clock limits          │
                        │ • ephemeral FS, wiped per run               │
                        │ • non-root, seccomp; gVisor/Firecracker/    │
                        │   container or WASM isolate                 │
                        │ • no secrets / credentials mounted          │
                        └─────────────────────────────────────────────┘
```

**Structured contract** — inputs and outputs are schema-validated, so the model can't smuggle prose
where a number belongs, and downstream nodes get typed data:

```python
# kompass/sandbox/runner.py
class SandboxResult(BaseModel):
    stdout: str
    result: dict | None          # JSON-serialisable, schema-checked
    error: str | None
    elapsed_ms: int

def run_sandboxed(code: str, inputs: dict, timeout_s: int = 10) -> SandboxResult:
    # spawn isolated executor (Firecracker microVM / gVisor / WASM),
    # no net, capped RAM/CPU, ephemeral rootfs, inject `inputs`, capture result
    ...
```

**Security considerations (the interview checklist):**

| Threat | Mitigation |
|--------|-----------|
| Data exfiltration | Network **deny-by-default**; explicit allowlist only if truly needed |
| Resource abuse / DoS | CPU, memory, PID and **wall-clock** limits; kill on breach |
| Container escape | microVM (Firecracker) or gVisor; non-root; seccomp/AppArmor; read-only base |
| Secret theft | **No credentials mounted** in the sandbox — ever |
| Persistence / lateral move | **Ephemeral** filesystem, destroyed after each run |
| Malicious output | Validate output against schema before it re-enters the graph |

**Kompass module:** `kompass/sandbox` (isolated runner + `SandboxResult` schema + resource policy).

> **Interview soundbite:** "Code execution is the highest-leverage *and* highest-risk tool. I run it
> in an ephemeral, network-denied microVM with CPU/memory/wall-clock caps, no secrets mounted, and
> schema-validated I/O — so the model gets a Python REPL without getting a foothold in prod."

---

## 4. Proactive / event-driven autonomy

A reactive agent only acts when a human types. A **proactive** agent is woken by **events** and acts
*without a prompt* — this is the leap from "chatbot" to "operations platform," and it is exactly the
BUSINESS VALUE thesis of Kompass: it doesn't wait to be asked, it triages the ticket the moment it
arrives.

```
  webhook (new Zendesk ticket) ─┐
  cron   (nightly SLA sweep)   ─┼─► kompass/triggers ─► build initial state ─► LangGraph run
  queue  (Kafka / SQS event)   ─┘        (normalise)        (goal, context)     (plan→execute→HITL)
```

- **Webhook** — new ticket / PR / alert arrives → trigger triage subgraph immediately.
- **Cron** — scheduled sweeps (SLA breach checks, stale-ticket nudges, digest generation).
- **Queue** — decoupled, high-throughput ingestion (Kafka/SQS), with backpressure and retries.

Each trigger normalises the event into an initial LangGraph **state** and starts a **checkpointed
run** with its own `thread_id`, so a proactively-started task is a first-class, resumable, HITL-
capable run — identical machinery to a human-initiated one. When a proactive run hits a high-risk
step it still pauses for approval (and can notify a human via A2A push notification or a channel
message), so autonomy never means "unsupervised on dangerous actions."

**Reactive → proactive** is a maturity gradient:

| Level | Behaviour |
|-------|-----------|
| L0 Reactive | Answers when prompted |
| L1 Triggered | Starts on an event, runs a fixed flow |
| L2 Proactive | Decides *whether* and *how* to act on the event; escalates edge cases |
| L3 Autonomous ops | Runs continuous loops (sweeps, monitoring), self-schedules follow-ups |

**Kompass module:** `kompass/triggers` (webhook / cron / queue adapters → normalised run launcher).

> **Interview soundbite:** "The business value flips when the agent becomes proactive. Kompass is
> woken by a webhook, cron, or queue event, builds its own initial state, and triages the ticket
> before any human touches it — but a proactive run uses the exact same checkpointed, HITL-gated
> graph, so it still stops for approval on risky actions."

---

## 5. Self-improving loop

A static agent is as good on day 100 as day 1. A **self-improving** agent gets *better with use* by
persisting **reflections** on its own transcripts and feeding them back as **few-shot exemplars** or
**procedural memory** — closing the loop between outcomes and future behaviour.

```
 run ─► outcome (resolved? escalated? user 👍/👎 / correction)
   │
   ▼
 Reflector (LLM): "what worked / what failed / what to do next time"
   │
   ▼
 kompass/memory  ── store reflection ──►  retrieve top-k relevant reflections
   (episodic + procedural)                as few-shots on the NEXT similar task
```

- **Signal sources:** explicit feedback (thumbs, human edits to a proposed action, HITL rejections),
  and implicit signals (task success, escalation, retries, latency).
- **What gets stored:** distilled *reflections* ("refunds over €100 always need the manager tag") in
  a semantically searchable store — not raw transcripts. This is **procedural + episodic memory** on
  top of the memory foundations in [01_agentic_ai_deep_dive.md](01_agentic_ai_deep_dive.md).
- **How it's applied:** at task start, retrieve the top-k relevant reflections and inject them as
  few-shot exemplars / policy reminders into the planner prompt. Over time the agent's *default
  behaviour* improves without retraining a model.
- **Guardrail:** reflections are data, so they are **untrusted input** — they pass through the same
  validation as any retrieved content ([§7](#7-guardrail--safety-agent--prompt-injection-red-team-suite))
  to avoid a poisoned "lesson" becoming a persistent injection.

**Kompass module:** `kompass/memory` (reflection writer + semantic reflection store + few-shot
retriever).

> **Interview soundbite:** "Kompass persists *reflections*, not just transcripts. Every resolved or
> rejected task produces a distilled lesson that gets retrieved as a few-shot on the next similar
> ticket — so the agent improves with use, and I treat those lessons as untrusted input so a
> poisoned reflection can't become a permanent injection."

---

## 6. User-simulator eval harness (tau-bench style)

The hardest thing about agents is proving they *actually work*, repeatedly, not once in a demo.
Kompass ships a **τ-bench (tau-bench)–style** harness where an **LLM simulates the user** — playing
a persona with a goal and hidden information — and drives multi-turn conversations against the agent
inside a domain with real DB state and a policy the agent must obey.

```
 ┌──────────────┐  turn        ┌────────────┐  tool calls   ┌────────────┐
 │ LLM user-sim │◄────────────►│  Kompass   │──────────────►│ domain DB  │
 │ (persona +   │  turn        │  agent     │◄──────────────│ + policy   │
 │  hidden goal)│              └────────────┘   final state └────────────┘
 └──────┬───────┘                                                 │
        │  conversation ends → check DB end-state vs. expected ◄──┘
        ▼
   pass / fail  ── run K times ──►  pass^k  (reliability, not just accuracy)
```

- **Why it impresses.** It signals *evaluation rigor* — the thing most agent demos skip. You are
  measuring **task success against DB end-state** and **policy compliance**, across many personas and
  edge cases, automatically. This scales to hundreds of scenarios without human labellers.
- **pass^k, not pass@k.** The headline metric is **pass^k** — the probability the agent succeeds on
  the *same* task across all *k* independent trials. It punishes *flakiness* and directly measures
  the reliability that matters in ops. Reporting a pass^k curve is a strong, quantitative interview
  artifact (it pairs with the RAG-accuracy and NL2SQL metrics in Wesley's track record).
- **What the sim stresses:** ambiguous requests, users who withhold info, policy traps (refund over
  threshold), multi-turn context, and adversarial or confused personas.

**Kompass module:** `evals/user_simulator` (persona/goal generator, sim-user policy, DB end-state
checker, pass^k reporter). Case studies exercising this live under
[`../entrevista/casos/`](../entrevista/casos/caso_02_customer_support.md).

> **Interview soundbite:** "I don't demo the happy path — I run a tau-bench-style harness where an
> LLM plays the user with a hidden goal, and I score the agent on database end-state and policy
> compliance across many personas. My headline number is pass^k, because in ops what matters is
> whether it works *every* time, not once."

---

## 7. Guardrail / safety agent + prompt-injection red-team suite

Autonomy without a safety layer is negligence. Kompass runs a dedicated **guardrail layer** — a
combination of deterministic validators and an LLM **safety agent** — on both the input and output
side, plus a maintained **prompt-injection red-team suite** that runs in CI.

```
 input ─► [ input guardrails ] ─► agent/graph ─► [ output guardrails ] ─► action/response
             │  PII / policy / injection            │  action allowlist, schema,
             │  detection, jailbreak filters        │  policy re-check, PII redaction
             └──────────────► block / sanitise / escalate to HITL ◄──────┘
```

- **Input guardrails:** PII detection, jailbreak/injection classifiers, topic and policy filters. On
  a hit → sanitise, block, or route to a human.
- **Output guardrails:** every proposed action is checked against an **allowlist + schema + policy**
  before it executes — the last line of defence in front of the tool. High-risk actions still route
  through the LangGraph HITL interrupt ([04_hitl_patterns.md](04_hitl_patterns.md)).
- **Defence in depth:** guardrails complement (do not replace) the *plan-then-execute* security
  property from [§2](#2-plan-and-execute-with-replanning) — planning limits the blast radius, output
  guardrails validate the surviving step.
- **Prompt-injection red-team suite:** a versioned corpus of attacks (direct injection, indirect
  injection via retrieved docs / ticket bodies, tool-output poisoning, exfiltration attempts, role
  confusion). It runs as an automated test so a regression in defences **fails CI** — the same
  "prove it, in CI" discipline as the [user-simulator harness](#6-user-simulator-eval-harness-tau-bench-style).

**Kompass module:** `kompass/guardrails` (input/output validators + safety agent) and the red-team
suite under `evals/` / CI.

> **Interview soundbite:** "Guardrails are defence-in-depth: input filters for injection and PII, an
> output allowlist-and-schema check in front of every tool, and a versioned prompt-injection
> red-team suite that runs in CI so a regression in our defences fails the build."

---

## 8. Tier 3 stretch capabilities

Frontier research made practical. These are the "and one more thing" differentiators — signalled as
roadmap items in Kompass so the design headroom is explicit.

| Capability | What / why | When to reach for it | Kompass home |
|-----------|-----------|----------------------|--------------|
| **Multi-modal ingestion** | Ingest screenshots, PDFs, error images, voice notes attached to tickets → text/structured context. Support tickets are rarely pure text. | Ticket has an attachment the resolution depends on | `kompass/retrieval` + `kompass/models` |
| **Multi-agent debate / judge panel** | N agents argue a decision; a **judge** (or majority) adjudicates. Adversarial verification catches errors a single pass misses. | High-stakes / ambiguous decisions where one model is unreliable | `kompass/graph` (debate subgraph) |
| **Saga / compensation** | Multi-step actions get **compensating transactions** so a partial failure can **roll back** (refund issued but ticket-close fails → auto-reverse). | Any multi-write action across systems that must be all-or-nothing | `kompass/graph` (saga orchestration) |
| **Semantic caching + prompt compression** | Cache answers by **embedding similarity** (not exact match) and compress long contexts → big latency/cost wins at scale. | High-volume, repetitive queries; long histories | `kompass/retrieval` (semantic cache) |

**Notes that matter in an interview:**

- **Debate/judge** is *adversarial verification* — it's the multi-agent generalisation of the
  self-consistency idea, and it pairs with the [user-simulator evals](#6-user-simulator-eval-harness-tau-bench-style)
  to quantify when the extra cost is justified.
- **Saga/compensation** is the honest answer to "what happens when step 3 of 5 fails after step 2
  already sent money?" — you need *compensating actions*, not just retries. This is a database/
  distributed-systems concept applied to agent tool-calls, and it complements HITL: rollback where
  possible, escalate where not.
- **Semantic caching** must be **scoped per-tenant/per-policy** and invalidated on state change —
  otherwise you serve a stale or cross-tenant answer. Say that unprompted; it shows you've thought
  past the demo.

> **Interview soundbite:** "The stretch tier is where I show design headroom: a debate/judge panel
> for adversarial verification on high-stakes calls, saga-style compensation so a partial failure
> rolls back instead of leaving money in limbo, and semantic caching that's scoped per-tenant so it
> never leaks a stale or cross-customer answer."

---

## How the frontier patterns compose

None of these live in isolation — the value is in the composition, which is exactly the
[architecture](05_architecture.md) story:

```
 trigger (§4) ─► guardrails-in (§7) ─► planner (§2) ─► executor ─┬─► MCP tools
      ▲                                    │                     ├─► sandbox (§3)
      │                                    │  high-risk?         └─► A2A delegate (§1)
      │                                    ▼
 reflection (§5) ◄── outcome ◄── HITL interrupt (approve/edit/reject) ◄── guardrails-out (§7)
      │
      └──► evaluated continuously by user-simulator + red-team (§6, §7)
```

A proactive trigger starts a guarded, planned, replanning run that executes through tools / sandbox /
peer agents, pauses for approval on risk, rolls back on failure, learns from the outcome, and is
proven by an automated eval harness. That end-to-end loop — *resolve and act, safely, and improve* —
is Kompass's thesis.

---

## Sources / further reading

**External sources**

- *The Definitive Guide to Agentic Design Patterns in 2026* — SitePoint.
  <https://www.sitepoint.com/the-definitive-guide-to-agentic-design-patterns-in-2026/>
- *Secure Plan-then-Execute* (agent planning as an injection-mitigation control) — arXiv 2509.08646.
  <https://arxiv.org/pdf/2509.08646>
- *Agent Interoperability Protocols: MCP, A2A, ACP convergence* — Zylos.
  <https://zylos.ai/research/2026-03-26-agent-interoperability-protocols-mcp-a2a-acp-convergence/>
- *MCP vs A2A* — TrueFoundry. <https://www.truefoundry.com/blog/mcp-vs-a2a>

**Related Kompass docs**

- [05_architecture.md](05_architecture.md) — how these patterns compose into the running system.
- [03_framework_decision.md](03_framework_decision.md) — why LangGraph v1.0, and the HITL primitives
  (declarative `interrupt_on` middleware over dynamic `interrupt()`).
- [01_agentic_ai_deep_dive.md](01_agentic_ai_deep_dive.md) — ReAct, tool use, and the memory
  foundations the self-improving loop builds on.
- [02_retrieval_strategies.md](02_retrieval_strategies.md) · [04_hitl_patterns.md](04_hitl_patterns.md)
  — retrieval and human-in-the-loop details referenced throughout.

**Interview prep**

- [../entrevista/framework_PACTEDR.md](../entrevista/framework_PACTEDR.md) — the PACTEDR answer
  framework for narrating these patterns aloud.
- [../entrevista/banco_preguntas.md](../entrevista/banco_preguntas.md) — question bank, including
  interop and safety questions.
- [../entrevista/casos/caso_02_customer_support.md](../entrevista/casos/caso_02_customer_support.md)
  — a case study that exercises triggers, plan-execute, HITL, and the user-simulator harness.
