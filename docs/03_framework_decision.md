# Framework Decision — Why LangGraph v1

> **Status:** Decided and final. Kompass is built on **LangGraph v1.0** (GA October 2025). This document
> records *why*, what the alternatives were, and where I would deliberately reach for something else. It is
> written to be defensible in a senior interview, not to be a fan post.

Kompass is an agentic support & operations assistant: it does not merely *answer*, it **resolves and acts**
end-to-end (issue refunds, reset credentials, file tickets, run NL2SQL over operational data) and pauses for
human approval only on risky actions. That mission dictates the framework requirements more than any hype
cycle does:

1. **Stateful, cyclic orchestration** — a support case is not a single LLM call; it loops (retrieve → reason
   → act → observe → re-plan) and branches.
2. **Durable, resumable execution** — an approval can arrive minutes or hours later; the process must survive
   a restart and pick up exactly where it paused.
3. **First-class human-in-the-loop (HITL)** — the *approve / edit / reject* contract is a product feature,
   not an afterthought.
4. **Employability** — this is a public portfolio anchored to European AI-engineering roles (DHL Data & AI,
   VW/CARIAD). The framework should be the one those job descriptions actually name.

The rest of this doc justifies the choice against the mid-2026 landscape, documents a deliberate
*comparative spike* in a second framework, and flags the HITL gotchas that separate people who have shipped
this from people who have read a blog post.

---

## The 2026 Production Landscape

By mid-2026 the "which agent framework" question has largely settled into a small set of tools that each own
a niche. The table below is the comparison I use to reason about the space and to answer the interview
question *"why not X?"*.

| Framework | Strong at | State / persistence | HITL | Production / who uses it |
|---|---|---|---|---|
| **LangGraph (v1.0)** | Complex **stateful** workflows, explicit control flow, **cycles** & branching | **Native checkpointer** (Postgres / Redis), durable & resumable | **Best-in-class**: dynamic `interrupt()` **+ declarative HITL middleware** (`interrupt_on`, approve/edit/reject) | Production consensus; ~400 companies incl. **Klarna, Uber, LinkedIn, JPMorgan, BlackRock, Cisco, Replit** |
| **OpenAI Agents SDK** | **Handoffs** between agents, minimal setup, **native MCP** | Basic sessions | Guardrails + handoffs | GPT-centric, very low friction; limited for complex orchestration |
| **PydanticAI** | **Type-safety**, validated structured outputs, FastAPI ergonomics, low cost | **Stateless by default** | HITL via your own code | Ideal for type-safe Python services; **no native multi-agent** |
| **CrewAI** | Fast **role-based** multi-agent prototypes | Unified memory (Chroma / SQLite) | Basic HITL | ~60% of Fortune 500 for **prototypes**; teams tend to outgrow it at scale |
| **Microsoft Agent Framework** | **Azure / .NET** native | Azure-backed state | HITL in workflows | **GA April 2026**; default inside Microsoft stacks |
| **Temporal (+ LLM)** | **Real durable execution** (beyond checkpoints), long-running jobs | **Event-sourced**; survives crashes *mid-step* | HITL = **`await signal`** (very clean) | Critical infra for **hours/days-long** workflows |
| **Vercel AI SDK / Mastra** | **Streaming** TS/React UIs | Manual / observational memory | Middleware-based HITL | TS-first, front-end-adjacent teams |

A few reads of this table that matter in conversation:

- **The axis that actually differentiates them is state + control flow, not "can it call an LLM."** Everything
  calls an LLM. The question is what happens on turn two, turn five, and after a crash.
- **OpenAI Agents SDK and CrewAI optimize for time-to-first-demo.** They are excellent for that. The moment
  you need explicit cyclic control, durable pauses, and audited human approvals, you are hand-building the
  parts LangGraph gives you natively — which is exactly the "teams outgrow it at scale" pattern.
- **PydanticAI is not a competitor so much as a complement**: it is the best answer when the *unit of work*
  is "call a model and get a validated object back," which is why I use it for the spike (below).
- **Temporal is a different layer entirely.** It is durable *execution*, not an agent framework. LangGraph
  checkpoints state between steps; Temporal event-sources the whole workflow and can survive a process dying
  mid-step. They compose rather than compete.

> **Interview soundbite:** "By 2026 the framework fight isn't about who can call an LLM — everyone can. It's
> about state, control flow, and durable human-in-the-loop. That's the column where LangGraph wins, and it's
> the column my use case lives in."

---

## The Decision: LangGraph v1.0 as Primary

**Primary framework = LangGraph v1.0.** It is simultaneously (a) what European job descriptions ask for, and
(b) what companies actually run in production for *stateful + HITL* agents. For a portfolio whose explicit
goal is employability, optimizing for the intersection of "demanded skill" and "correct engineering choice"
is the whole game — and here they coincide.

Two v1.0 capabilities do the heavy lifting for Kompass.

### 1. Declarative HITL middleware (`interrupt_on`)

Pre-v1, HITL meant hand-rolling the interrupt *contract* around the dynamic `interrupt()` primitive: you
wrote the `waiting_for` field, the `allowed_actions` list, and the discriminated union that decoded the
human's reply. LangGraph v1.0 ships a **declarative HITL middleware** that removes that plumbing. You declare
*which tools require approval* and *which decisions are allowed*, and the middleware enforces the
approve / edit / reject contract for you.

```python
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langgraph.checkpoint.postgres import PostgresSaver

checkpointer = PostgresSaver.from_conn_string(settings.postgres_dsn)

agent = create_agent(
    model="openai:gpt-5.4",                 # provider-agnostic; swap freely
    tools=[issue_refund, reset_password, escalate_to_human],
    middleware=[
        HumanInTheLoopMiddleware(
            interrupt_on={
                # risky money movement: full approve/edit/reject
                "issue_refund":  {"allowed_decisions": ["approve", "edit", "reject"]},
                # security action: approve or reject, no silent edits
                "reset_password": {"allowed_decisions": ["approve", "reject"]},
                # `escalate_to_human` is safe -> not listed -> runs freely
            }
        )
    ],
    checkpointer=checkpointer,   # <-- durability is not optional for HITL
)
```

The dynamic `interrupt()` primitive is still there underneath for the *bespoke* pauses the middleware can't
express (e.g. "pause to disambiguate which of three accounts the user means"). The right mental model:
**middleware for the standard approval gate, `interrupt()` for everything custom.**

```python
from langgraph.types import interrupt, Command

def disambiguate_account(state):
    choice = interrupt({
        "question": "Which account?",
        "options": state["candidate_accounts"],
    })
    return Command(update={"account_id": choice["account_id"]})
```

### 2. Postgres checkpointer (durable & resumable)

HITL only works if the pause can outlive the process. The native `PostgresSaver` persists graph state to
Postgres between every node, so an interrupt can wait indefinitely and resume — cleanly — after a redeploy,
a crash, or an approval that lands three hours later. Redis is available for lower-latency / ephemeral cases.
For Kompass, **Postgres is the checkpointer** because refunds and credential resets demand an auditable,
durable trail.

### 3. The comparative spike (one component, second framework)

To demonstrate *judgment* rather than *loyalty*, I re-implement **one component — the Researcher agent —** in
a second framework (**PydanticAI**, with OpenAI Agents SDK as the alternate) and document the trade-off. The
Researcher is the right choice for the spike because its job ("retrieve, reason over context, return a
*validated* structured answer") is exactly PydanticAI's sweet spot, so the comparison is fair rather than
rigged. The full write-up lives in
[`../spike_frameworks/comparison.md`](../spike_frameworks/comparison.md) *(produced in a later slice)*; the
headline trade-off:

| Dimension | LangGraph v1.0 | PydanticAI (spike) |
|---|---|---|
| **State** | Native, durable checkpointer | Stateless by default; you bring your own store |
| **HITL** | Declarative middleware + `interrupt()` | Hand-coded around your own loop |
| **Multi-agent orchestration** | First-class (graph, cycles, subgraphs) | Not native; compose manually |
| **Structured output / type-safety** | Good, via tool/schema binding | **Excellent** — validation is the core value prop |
| **Cost / footprint** | Heavier runtime | Lighter, leaner dependency tree |
| **Developer experience** | Explicit but more ceremony | FastAPI-like ergonomics, very fast to a validated result |

> **Interview soundbite:** "I built the same agent in two frameworks. LangGraph won for stateful,
> human-in-the-loop orchestration; PydanticAI was leaner and had better type-safety for a single validated
> call. Different tools, different jobs — and I can show you the trade-off table."

### 4. When I would escalate to Temporal (diagram only)

For the *long-running* case — a workflow that spans hours or days, must survive process death mid-step, and
needs event-sourced replay — LangGraph's between-node checkpointing is the wrong altitude and **Temporal** is
the right answer. Kompass does not need it today, but knowing *where the ceiling is* is exactly what a senior
interviewer probes.

```
   LangGraph checkpointing                      Temporal durable execution
   (state saved BETWEEN nodes)                  (event-sourced, replays whole history)

   ┌─────────┐  ckpt  ┌─────────┐               Workflow (deterministic)
   │ node A  │──────▶ │ node B  │                 │   await signal("approved")   <-- HITL
   └─────────┘        └────┬────┘                 │        │
        ▲                  │ crash mid-B          ▼        │  crash ANYWHERE
        │                  ▼                    Activities (side effects, retried)
     resume re-runs   node B restarts             │  replay from event history →
     from last ckpt   FROM ITS START              │  reconstructs exact state,
                      (idempotency matters!)       │  even mid-activity
```

The key distinction: LangGraph resumes from the **last checkpoint boundary** (so a half-finished node
re-executes from the top); Temporal reconstructs the **entire** workflow from an append-only event log and
can survive a crash *inside* a step. Different guarantees, different cost.

---

## Why This Is the Highest-Employability Choice

A senior interviewer probes framework choices at three levels. LangGraph-as-primary plus a spike plus a
Temporal escape hatch answers all three — which is why this is the *employability-maximizing* decision, not
just the technically-correct one.

| Level the interviewer probes | What they're really asking | How this decision answers it |
|---|---|---|
| **1. Can you use the demanded tool?** | "Do you know the thing our JD lists?" | Yes — LangGraph is the production consensus and the most-named framework in EU AI-eng roles. I built the whole product on it. |
| **2. Do you have judgment?** | "Or did you just cargo-cult the popular option?" | The **comparative spike**: I implemented the same agent in a second framework and can articulate the state / HITL / cost / DX trade-offs. |
| **3. Do you know the limits?** | "When does your favorite tool stop being the right one?" | I can name the ceiling — durable, multi-hour workflows — and the correct escalation (**Temporal**), with the architectural reason. |

Mastering the demanded tool covers level 1. The spike proves level 2. The Temporal escape hatch proves level
3. Most candidates stop at level 1.

> **Interview soundbite:** "I default to LangGraph because it's what the role needs and what production runs.
> But I chose it — I didn't cargo-cult it. I benchmarked a second framework and I know exactly when I'd swap
> in Temporal for durable execution."

---

## HITL Gotchas Worth Knowing

These are the details that reveal whether someone has actually shipped HITL. Kept brief here; the full
treatment — patterns, idempotency keys, resume flows, the discriminated-union contract — is in
[`04_hitl_patterns.md`](04_hitl_patterns.md).

- **Resume re-executes the node from its start, not from the `interrupt()` line.** When a human approves,
  LangGraph replays the interrupted node from the top; `interrupt()` returns the human's value on the second
  pass. **Consequence: any side effect *before* the interrupt runs twice unless you make it idempotent.** Put
  side effects *after* the approval, or guard them with an idempotency key.
- **Checkpoints save state BETWEEN nodes, not within them.** There is no snapshot mid-node. This is precisely
  why the re-execution rule above exists, and why long, effectful nodes should be split.
- **The execution is durable *and* resumable.** A pause can outlive the process; state lives in the
  checkpointer (Postgres for Kompass), so an approval can arrive after a redeploy and still resume cleanly.
- **`approve / edit / reject` is the standard decision contract.** *Approve* → run the action as proposed;
  *edit* → run a human-modified version of the arguments; *reject* → skip and route to a fallback. Design
  every risky tool around these three outcomes and the declarative middleware wires them for you.

> **Interview soundbite:** "The classic HITL bug: resume re-runs the whole node, so any side effect before
> the interrupt fires twice. Fix is idempotency — or just don't do side effects before the approval gate."

---

## An Honest Note on the Crehana Experience

For credibility, the honest framing of my prior LangGraph multi-agent work at Crehana: **the `interrupt()`
there was already the dynamic runtime primitive** — that part is not new. What we hand-built was the
**interrupt *contract*** around it: the `waiting_for` marker, the `allowed_actions` list, and the
**discriminated unions** that decoded and validated the human's reply into typed decisions.

That was **pre-v1 plumbing** — necessary then, and exactly the boilerplate the **v1.0 declarative HITL
middleware now removes**. So the accurate story is not "LangGraph couldn't pause" (it could); it's "we
manually built the approval-contract layer that v1.0 now ships declaratively." That is a stronger, more
honest interview answer than pretending the whole thing was magic — and it demonstrates I understand *what
changed* between the version I shipped on and the version Kompass targets.

> **Interview soundbite:** "At Crehana the interrupt was dynamic — that existed. What we hand-rolled was the
> approval contract: `waiting_for`, allowed actions, discriminated unions. LangGraph v1's HITL middleware is
> essentially that contract, productized — so Kompass gets it for free."

---

## Sources

- LangChain — *AI Agent Frameworks (2026)*: <https://www.langchain.com/resources/ai-agent-frameworks>
- LangChain — *LangChain & LangGraph 1.0*: <https://www.langchain.com/blog/langchain-langgraph-1dot0>
- Speakeasy — *AI Agent Framework Comparison*: <https://www.speakeasy.com/blog/ai-agent-framework-comparison/>
- alicelabs — *Best AI Agent Frameworks 2026*: <https://alicelabs.ai/en/insights/best-ai-agent-frameworks-2026>
- *LangGraph vs Temporal for AI Agents — Durable Execution Architecture Beyond For-Loops*:
  <https://medium.com/data-science-collective/langgraph-vs-temporal-for-ai-agents-durable-execution-architecture-beyond-for-loops-a1f640d35f02>
- LangChain — *Human-in-the-Loop docs*: <https://docs.langchain.com/oss/python/langchain/human-in-the-loop>

---

## Related

- [`04_hitl_patterns.md`](04_hitl_patterns.md) — full HITL treatment: contract, idempotency, resume flows.
- [`05_architecture.md`](05_architecture.md) — how the graph, checkpointer, and HITL gates fit the system.
- [`06_advanced_patterns.md`](06_advanced_patterns.md) — multi-agent orchestration, subgraphs, durability.
- [`../spike_frameworks/comparison.md`](../spike_frameworks/comparison.md) — the Researcher-agent framework
  spike and trade-off table *(produced in a later slice)*.
- [`../entrevista/framework_PACTEDR.md`](../entrevista/framework_PACTEDR.md) — interview framework for
  narrating this decision under pressure.
