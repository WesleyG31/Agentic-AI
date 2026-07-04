# What Agentic AI Actually Is — A Deep Dive

> Part of the **Kompass** documentation set. Kompass is an agentic Support & Operations
> assistant built on **LangGraph v1.0**. Its whole reason to exist is the distinction this
> document defines: Kompass does not merely *answer* a ticket, it *resolves* it — it plans,
> chooses tools, acts on external systems, and pauses for a human only when an action is risky.
> If you understand why that last sentence describes an **agent** and not a chatbot, you
> understand this document.

This is the foundational theory doc for the project. It answers, precisely and without hype,
the question every interviewer opens with: *"What do you mean by agentic AI?"* The goal is not
a marketing definition but an **engineering** one — a definition that changes what you build,
how you test it, and what it costs to run.

---

## 1. Definition: Who Decides the "How"?

An **agentic system is one where an LLM decides the control flow.** Given a goal, the model
itself plans the steps, chooses which tools to call, observes the results, and decides whether
to iterate, branch, retry, or stop — all in a loop, with autonomy over *how* the goal gets
achieved. The engineer specifies the **what** (the objective, the available tools, the
guardrails); the model owns the **how** (the sequence of actions taken to get there).

Contrast that with a **fixed script**, where a human developer wrote the control flow ahead of
time in code: "first retrieve, then summarize, then email." The LLM is a component *inside*
that script — a powerful text transformer — but it is not steering. The path is the same on
every run regardless of what the model sees.

The single load-bearing word is **autonomy over the "how"**:

```
             ┌─────────────────────────────────────────────────┐
  FIXED      │  human code decides the sequence of steps         │
  SCRIPT     │  LLM = a step (transform text)                    │
             │  path is identical every run                      │
             └─────────────────────────────────────────────────┘

             ┌─────────────────────────────────────────────────┐
  AGENT      │  LLM decides the sequence of steps at runtime     │
             │  tools + goal + guardrails = given                │
             │  path is data-dependent, discovered per run       │
             └─────────────────────────────────────────────────┘
```

A useful mental test: **if you can draw the exact flowchart before the request arrives, it is
not agentic.** In an agent, the flowchart is drawn *by the model, at runtime, in response to
what it observes.* Two identical-looking tickets can take completely different paths — one
resolves in a single lookup, the other spawns four tool calls and a human approval — because
the model chose differently based on what it found.

> **Interview soundbite:** "Agentic means the LLM decides the control flow. In a workflow, I
> write the sequence of steps; in an agent, the model chooses the next step at runtime based on
> what it observes. Autonomy over the *how* is the whole definition — everything else is detail."

A second implication people miss: agency is a **spectrum, not a boolean.** A system can be
"a little bit agentic" (the model picks between two retrievers) or "very agentic" (the model
plans a multi-step remediation, calls five tools, and self-corrects). Section 2 turns that
spectrum into the table interviewers actually ask you to draw.

---

## 2. The Agentic Spectrum (Levels 1–4)

This is the table to have memorized. When someone asks *"is RAG agentic?"* or *"where does your
project sit on the spectrum?"*, you draw this. The organizing question for every row is the same
one from Section 1: **who holds the control flow?**

| Level | Name | What it does | Who controls the flow | Agentic? |
|-------|------|--------------|-----------------------|----------|
| **1** | Single LLM call | Prompt in → completion out | The prompt author (fixed) | **No** — no control to speak of |
| **2** | RAG, single-shot | `question → embed → search → 1 LLM call → answer` | Fixed pipeline (human code) | **No** — flow is hard-coded |
| **3** | Workflow | Predefined steps orchestrating LLM + tools (branch/route/chain) | **Human code** (the graph you wrote) | **Partial** — LLM chooses *content*, not *path* |
| **4** | Agent | LLM directs its own process + tools in a loop | **The LLM** (chosen at runtime) | **Yes** — this is the real thing |

**Row-by-row:**

- **Level 1 — Single LLM call.** `"Summarize this text."` One request, one response. There is
  no control flow at all: no tools, no memory, no iteration. Extremely cheap and predictable.
  This is a *feature*, not an agent. Most "AI features" in products live here and should.

- **Level 2 — RAG, single-shot.** The canonical retrieval pipeline: embed the query, search a
  vector store, stuff the top-k chunks into the prompt, generate one answer. Enormously useful
  and correct for grounded Q&A — but **not agentic.** The path is fixed: it *always* retrieves
  exactly once and *always* generates exactly once. The LLM never decides *whether* to retrieve,
  *how much*, or *from where*. (Section 4 is dedicated to this because it is Wesley's own
  favorite trap question.)

- **Level 3 — Workflow.** Now there are multiple steps, tools, and branches — a router that
  sends billing questions one way and technical questions another, a chain that extracts then
  validates then formats. The LLM makes *local* decisions ("classify this ticket as billing")
  and produces content, but the **overall path is authored by a human** as a graph or state
  machine. Predictable, testable, cheap to reason about. Partially agentic: the model influences
  the flow at decision points but does not own the loop. **Most production "agents" are actually
  well-engineered Level-3 workflows — and that is usually the right call.**

- **Level 4 — Agent.** The LLM runs a loop: *think → act (call a tool) → observe → decide again*,
  and it decides for itself when the goal is met and it can stop. The path is emergent and
  data-dependent. This is where you get real flexibility (handle the long tail of weird tickets)
  and real cost/risk (unbounded loops, unpredictable tool calls, harder evaluation). Kompass uses
  Level 4 selectively — a bounded ReAct loop for open-ended resolution — wrapped in Level-3
  scaffolding (routing, guardrails, human approval) so the autonomy is *contained*, not
  unleashed.

> **Interview soundbite (this is question #1):** "I think of four levels: a raw LLM call, single-shot
> RAG, a workflow, and an agent. The line that matters is between levels 3 and 4 — in a workflow
> *I* own the control flow; in an agent the *model* owns it at runtime. Levels 1 and 2 aren't
> agentic at all, and honestly most production systems should stop at level 3."

The architecture doc, [05_architecture.md](05_architecture.md), shows exactly how Kompass mixes
levels 3 and 4 in a single LangGraph — a deterministic outer graph with an autonomous inner loop.

---

## 3. Anthropic's "Building Effective Agents" Framing

Anthropic's *Building Effective Agents* (Dec 2024) gives the industry-standard vocabulary, and
it maps cleanly onto the spectrum above. It draws one primary distinction:

| | **Workflows** | **Agents** |
|---|---|---|
| Control flow | Predefined **code paths** | LLM **dynamically directs** itself |
| Predictability | High | Low(er) |
| Cost / latency | Low, bounded | High, variable |
| Debuggability | Easy (fixed graph) | Hard (emergent traces) |
| Best for | Well-understood, decomposable tasks | Open-ended tasks where the steps can't be predicted |
| Maps to | Level 3 | Level 4 |

> Workflows are systems where LLMs and tools are orchestrated through **predefined code paths**.
> Agents are systems where LLMs **dynamically direct their own processes and tool usage**,
> maintaining control over how they accomplish tasks.

The framing's most quoted line is a design philosophy, and it is the single most important
sentence for an interview:

> **The golden rule: start with the simplest thing that works, and add agentic complexity
> *only* when the added flexibility measurably improves outcomes on your evals.**

Agents trade predictability, latency, and cost for flexibility. You pay that price on **every
request**, so you only pay it where the flexibility earns its keep. Concretely, Anthropic's
guidance (and the stance Kompass takes):

1. **Don't build an agent if a workflow suffices.** Most tasks are decomposable enough that a
   fixed graph is more reliable, cheaper, and easier to debug.
2. **Don't build a framework-heavy agent if direct API calls suffice.** Add abstraction only
   when it removes real pain.
3. **Justify every increment of autonomy with data.** If your eval numbers don't move when you
   add a loop, delete the loop.

Kompass follows this literally. The escalation ladder in Section 7 *is* the golden rule turned
into an engineering decision procedure: reach for the simplest tier, and climb only when the
evals in [`evals/`](../evals) say the extra autonomy pays for itself.

> **Interview soundbite:** "Anthropic's framing is 'workflows vs agents,' and the golden rule is
> *start simple, add complexity only when the data justifies it.* Agents buy flexibility with
> predictability, latency, and cost — and you pay that on every single request, so I make the
> system prove it needs autonomy before I grant it."

---

## 4. Why RAG Alone Is *Not* Agentic

This is the question Wesley likes to pose, because it separates people who *use* the buzzwords
from people who *understand* them. The short answer: **pure RAG is a fixed pipeline with zero
decisions and zero actions.**

Trace the canonical single-shot RAG path — nothing here is chosen by the model:

```
  user question
       │
       ▼
  [embed query]          ← always, exactly once
       │
       ▼
  [vector search top-k]  ← always the same store, same k
       │
       ▼
  [stuff context + prompt]
       │
       ▼
  [1 LLM call] ──► answer   ← always exactly one generation, then STOP
```

Every arrow is drawn by a human before the request arrives. The LLM appears **once**, at the
very end, purely to phrase an answer from context that was already fetched for it. It never
decides *whether* to retrieve, *how much*, *from where*, or *what to do next*. There is no loop,
no tool choice, no action on the world, no memory across turns. By the Section 1 test — *can you
draw the flowchart in advance?* — the answer is unambiguously yes. **RAG is Level 2: not agentic.**

That is not a criticism. Single-shot RAG is the correct, cheap, predictable choice for a huge
class of grounded-Q&A problems, and Kompass uses plain RAG for exactly those. The point is
taxonomic honesty.

### When RAG *becomes* agentic

RAG crosses the line into agency the moment the **model** starts making retrieval decisions
instead of executing a fixed pipeline. Concretely, RAG becomes **agentic RAG** when the LLM:

| Capability | Fixed RAG (Level 2) | Agentic RAG (Level 4) |
|---|---|---|
| **How much to retrieve** | Always top-k, once | Model decides *whether* and *how many hops* (multi-hop) |
| **Where to retrieve from** | One store | Model routes among sources/tools (docs vs SQL vs API) |
| **Query formulation** | Uses the raw question | Model rewrites / decomposes into sub-queries |
| **Quality control** | Trusts what it got | Model grades results, re-retrieves if weak (self-correction / CRAG) |
| **Acting on results** | Just answers | Model takes an *action* (with HITL) — refund, ticket, reset |
| **Continuity** | Stateless | Remembers prior turns / prior resolutions (memory) |

So the transition is: **multi-hop** (decide what and how much to fetch) + **tool/source routing**
(choose among retrievers and non-retrieval tools) + **self-correction** (grade and retry) +
**acting** (do something in the world, gated by human approval) + **remembering** (carry state).
Add those and retrieval stops being a pipeline and becomes one *tool* an agent chooses to use.

> **Interview soundbite:** "RAG alone isn't agentic — it's a fixed pipeline: embed, search, one
> LLM call, done. No decisions, no actions. It *becomes* agentic when the model decides what and
> how much to retrieve, routes across sources, grades its own results and re-retrieves, and then
> *acts* on the answer with a human in the loop. That last part — acting, not just answering — is
> the whole thesis of Kompass."

The retrieval spectrum — naive → hybrid → reranked → multi-hop → agentic — is covered in depth in
[02_retrieval_strategies.md](02_retrieval_strategies.md).

---

## 5. The Five Components of an Agent

If Level 4 is "the LLM runs the loop," these are the five parts that make the loop *work*. Every
serious agent — Kompass included — is some concrete implementation of these five.

```
        ┌───────────────────────────────────────────────┐
        │                   AGENT                         │
        │                                                 │
        │   ┌──────────┐        ┌──────────────────┐      │
        │   │ PLANNING │◄──────►│  MEMORY           │      │
        │   └────┬─────┘        │  short + long     │      │
        │        │              └──────────────────┘      │
        │        ▼                                         │
        │   ┌──────────┐   act   ┌──────────────────┐      │
        │   │ AUTONOMY │────────►│  TOOL USE (MCP)  │      │
        │   │ LOOP     │◄────────│  external world  │      │
        │   │ (ReAct)  │ observe └──────────────────┘      │
        │   └────┬─────┘                                    │
        │        │ reflect                                  │
        │        ▼                                          │
        │   ┌──────────────────┐                            │
        │   │ REFLECTION /      │                            │
        │   │ SELF-CORRECTION   │                            │
        │   └──────────────────┘                            │
        └───────────────────────────────────────────────┘
```

### 5.1 Planning
The agent decomposes a goal into steps and decides an order — either up front ("plan-then-execute")
or incrementally ("decide the next step each turn," as in ReAct). Planning is what lets an agent
handle a request it has never seen: instead of a hard-coded path, it *reasons out* a path. In
Kompass, planning is where a vague ticket ("my invoice looks wrong") becomes a concrete sequence:
look up the account → fetch recent invoices → diff against the plan → decide refund vs. escalate.

### 5.2 Tool Use (via MCP)
Tools are how the model touches the world beyond text — search a knowledge base, query SQL, call
an API, send an email, open a ticket. Kompass exposes tools through **MCP (Model Context
Protocol)**, the open standard for connecting models to tools and data. MCP matters because it
**decouples the agent from the integrations**: each capability is an independent MCP server with a
typed contract, so tools can be added, versioned, sandboxed, and permissioned without touching the
agent's reasoning code. (Wesley runs MCP in production at VW/CARIAD; in Kompass it is the tool
layer.) A tool call is the *"act"* in the loop — and the point at which the human-approval gate
fires for risky actions (see [04_hitl_patterns.md](04_hitl_patterns.md)).

### 5.3 Memory (short-term + long-term)
- **Short-term (working) memory** = the current task's state: the conversation, intermediate
  observations, the scratchpad of what's been tried. In LangGraph this is the graph **state** plus
  the thread checkpoint.
- **Long-term memory** = knowledge that persists across sessions: user preferences, past
  resolutions, entity facts, learned playbooks. Typically a store (vector or key-value) the agent
  reads from and writes to.

Without memory an agent is amnesiac — it re-derives everything every turn and can't learn from a
resolution it performed yesterday. Memory is what makes Kompass improve with use rather than start
from zero on every ticket.

### 5.4 Reflection / Self-Correction
The agent evaluates its own intermediate output and revises: "the retrieved docs don't actually
answer this — re-query"; "this SQL returned zero rows — my join was wrong — fix it"; "my draft
reply contradicts policy — rewrite." This is the *evaluator-optimizer* pattern (Section 6) applied
internally. Reflection is the single biggest lever on **reliability**: it turns a confident-but-wrong
first attempt into a checked, corrected answer, at the cost of extra LLM calls.

### 5.5 Autonomy Loop (ReAct)
The engine that ties the other four together. **ReAct** = *Reason + Act*: the model alternates
between a **Thought** (reason about what to do next), an **Action** (call a tool), and an
**Observation** (read the result), repeating until it decides the goal is met.

```
Thought → Action → Observation → Thought → Action → Observation → … → Final Answer
```

Pseudocode for the core loop (this is essentially what LangGraph's agent runtime does):

```python
state = {"goal": ticket, "scratchpad": [], "steps": 0}

while not done(state) and state["steps"] < MAX_STEPS:      # bounded autonomy
    thought, action = llm.decide(state)                    # Reason: pick next step
    if action.is_final:                                    # model decides to stop
        return action.answer
    if action.is_risky:                                    # HITL gate
        await human_approval(action)                       # interrupt → approve/edit/reject
    observation = tools.run(action)                        # Act + Observe
    state["scratchpad"].append((thought, action, observation))
    state["steps"] += 1
```

Two production-critical details live in that snippet: **`MAX_STEPS`** (bounded autonomy — never
let the loop run unbounded) and the **`is_risky` approval gate** (the model may *decide* to issue a
refund, but a human authorizes it). Those two lines are the difference between a demo and something
you would run against real customer accounts.

> **Interview soundbite:** "An agent is five things: planning, tool use, memory, reflection, and an
> autonomy loop — usually ReAct. Tools I expose over MCP so integrations stay decoupled; memory is
> short-term graph state plus a long-term store; reflection is what buys reliability; and the loop
> is always *bounded* with a human gate on risky actions. Those last two constraints are what make
> it safe to ship."

---

## 6. Design Patterns (Interview Vocabulary)

These are the named building blocks (from Anthropic's *Building Effective Agents* and the wider
literature). Knowing the vocabulary lets you say *"that's an orchestrator-workers problem"* in an
interview instead of hand-waving. One line each; the deep treatment with LangGraph code is in
[06_advanced_patterns.md](06_advanced_patterns.md).

| Pattern | One-line definition | Level |
|---|---|---|
| **Prompt chaining** | Decompose a task into a fixed sequence of LLM calls, each feeding the next (optionally with gate checks between). | Workflow (3) |
| **Routing** | A classifier LLM directs the input to the one specialized handler/prompt that fits it. | Workflow (3) |
| **Parallelization** | Run subtasks concurrently — **sectioning** (split into independent parts) or **voting** (run the same task N times and aggregate for reliability). | Workflow (3) |
| **Orchestrator-workers** | A lead LLM dynamically decomposes a task and delegates subtasks to worker LLMs, then synthesizes their results. | Agentic (3→4) |
| **Evaluator-optimizer (reflection)** | One LLM produces, a second critiques against criteria, and the producer revises — loop until good enough. | Agentic (4) |
| **ReAct (autonomous)** | Single agent loops Reason → Act → Observe, choosing tools until the goal is met. | Agent (4) |
| **Multi-agent** | Multiple specialized agents collaborate (supervisor/hierarchy or peer-to-peer), each owning a domain and its tools. | Agent (4) |

The progression down that table is also a progression in autonomy: the top rows have human-authored
control flow (workflows), the bottom rows hand more of the flow to the model (agents). Kompass is
built primarily as **routing + orchestrator-workers + a bounded ReAct core with evaluator-optimizer
reflection** — deliberately stopping short of a sprawling multi-agent system until the evals prove
one is needed (the golden rule again).

> **Interview soundbite:** "The pattern vocabulary I reach for: prompt chaining and routing for
> predictable flows; parallelization when subtasks are independent or I want voting for reliability;
> orchestrator-workers when I can't predict the decomposition; evaluator-optimizer for quality; and
> ReAct or multi-agent when the task is genuinely open-ended. I pick the *simplest* pattern that
> clears the eval bar."

---

## 7. When *Not* to Use Agents — The Escalation Ladder

The mark of an engineer rather than a hype-follower is knowing when **not** to build an agent.
Agents are the most expensive, least predictable, hardest-to-evaluate option on the shelf. Reach
for them last. Concrete "don't" conditions:

- **The task is deterministic or well-understood → use a workflow.** If you can enumerate the steps
  reliably, a fixed graph is more accurate, cheaper, and trivially debuggable. Handing that flow to
  an LLM only adds nondeterminism and cost for no benefit.
- **Latency or cost is critical and RAG (or a single call) already answers it → don't add a loop.**
  Every extra reasoning step is another LLM call — more money, more milliseconds, more failure
  surface. If single-shot RAG hits your accuracy bar, shipping an agent is a regression.
- **You cannot evaluate it or add guardrails → do not deploy autonomy.** Autonomy without an eval
  harness and guardrails is not a feature, it's an incident waiting to happen. If you can't measure
  whether the agent is right and can't constrain what it's allowed to do, you have no business
  giving it the control flow. (No evals + no guardrails = no agent. Full stop.)

### The escalation ladder

Climb this ladder from the top. Stop at the **first** rung that meets your accuracy bar on your
evals. Only descend to the next rung when the data proves the current one is insufficient — this is
the golden rule from Section 3 turned into an operating procedure.

```
   simplest / cheapest / most predictable
        │
        ▼
   1. Single LLM call        ── no context needed, pure transform
        │   (not enough? task needs grounded facts)
        ▼
   2. RAG (single-shot)      ── grounded Q&A over a corpus
        │   (not enough? task has multiple known steps)
        ▼
   3. Workflow               ── predefined steps: chain / route / parallelize
        │   (not enough? steps can't be predicted in advance)
        ▼
   4. Single agent (ReAct)   ── model directs its own loop + tools, bounded + HITL
        │   (not enough? distinct domains each need their own expert + tools)
        ▼
   5. Multi-agent            ── specialized agents collaborate under a supervisor
        │
        ▼
   most flexible / most expensive / least predictable
```

Each rung down buys flexibility and pays for it in cost, latency, and unpredictability. **Every
step down must be justified by an eval delta, not by ambition.** A Level-3 workflow that clears the
bar beats a Level-5 multi-agent system that's flashier, slower, pricier, and no more accurate —
every time.

Kompass is engineered as a **hybrid**, not a monolithic agent: a deterministic workflow shell
(routing, retrieval, guardrails, human-approval gates) wrapping a **bounded** agentic core (ReAct
loop + reflection) that is invoked *only* for the open-ended resolution work that genuinely needs
it. That is the escalation ladder applied honestly — autonomy where it earns its cost, determinism
everywhere else.

> **Interview soundbite:** "My default is the *lowest* rung that clears the eval bar: LLM call →
> RAG → workflow → single agent → multi-agent. I only climb down when the data forces me to, and I
> won't deploy autonomy I can't evaluate or guardrail. In Kompass that means a deterministic shell
> around a bounded agentic core — autonomy exactly where it pays for itself, and nowhere else."

---

## Related / Further Reading

- [02_retrieval_strategies.md](02_retrieval_strategies.md) — the retrieval spectrum from naive RAG
  to agentic multi-hop retrieval; expands on Section 4.
- [05_architecture.md](05_architecture.md) — how Kompass composes a deterministic LangGraph shell
  around a bounded agentic core; the concrete realization of Sections 5 and 7.
- [06_advanced_patterns.md](06_advanced_patterns.md) — deep dives with LangGraph code for the
  design patterns catalogued in Section 6.
- [../entrevista/banco_preguntas.md](../entrevista/banco_preguntas.md) — the interview question
  bank, including "is RAG agentic?" and "walk me through the agentic spectrum."
