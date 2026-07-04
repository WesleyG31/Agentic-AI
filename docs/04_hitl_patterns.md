# Human-in-the-Loop — Done Right (Declarative, Durable, Resumable)

Kompass does not stop at answering — it **acts**: it issues refunds, updates tickets, sends
customer emails, resets credentials. Autonomy is the value, but autonomy over irreversible actions
is also the risk. The engineering answer is **Human-in-the-Loop (HITL)**: the agent works
autonomously right up to a risky action, then **pauses**, surfaces exactly what it wants to do, and
waits for a human to **approve / edit / reject**. Done wrong, HITL is a pile of brittle state flags
and polling. Done right on **LangGraph v1**, it is a *declarative*, *durable*, *resumable* pattern
that survives page refreshes, reviewer hand-offs, and process crashes.

> **Interview soundbite:** "The point of an agent isn't to answer — it's to resolve. HITL is what
> lets you ship autonomy on irreversible actions without betting the company on the model being
> right every time."

This doc covers the two LangGraph primitives (dynamic vs declarative), the gotchas that separate a
demo from production, Kompass's end-to-end approval flow, and where a checkpointer runs out of road
and you reach for durable execution (Temporal). It pairs with the
[framework decision](03_framework_decision.md) (why LangGraph won partly *on* this feature), the
[system architecture](05_architecture.md) (where the middleware sits), and the worked
[customer-support case](../entrevista/casos/caso_02_customer_support.md).

---

## 1. The Problem: Risky Actions Need a Human, but Naive HITL Is Brittle Plumbing

An action agent that can call `issue_refund`, `update_ticket`, or `send_email` cannot be turned
loose unattended. Some actions are cheap and reversible (post an internal note); some are expensive
and irreversible (refund €400, email 10,000 customers). The naive fix — "ask the LLM to be careful"
— is not a control. You need a **hard gate** in the control flow.

The naive *engineering* fix is just as bad. Before frameworks made this a first-class feature, teams
hand-rolled an approval protocol:

- a boolean/enum in state (`waiting_for = "approval"`),
- a list of what the human is allowed to do (`allowed_actions`),
- a `response_builder` that returned a **discriminated union** so the frontend knew whether to render
  a chat bubble or an approval card,
- the graph would set the flag, hit `END`, and the app would **poll** or re-invoke with the decision
  merged back into state.

That works, but it is a bespoke state machine you now own forever: manual routing, manual
re-hydration, and — the silent killer — manual **idempotency**. Every new risky tool means touching
the union, the router, and the UI contract. This is exactly the plumbing I built at Crehana before
v1 existed; the value of LangGraph v1 is that the **middleware now does this for you**.

> **Interview soundbite:** "Naive HITL is a hand-rolled state machine — `waiting_for`,
> `allowed_actions`, a discriminated-union response builder. It works, but you own it forever. The
> v1 middleware turns that bespoke plumbing into one declarative config line."

---

## 2. Two Primitives: Dynamic `interrupt()` vs Declarative HITL Middleware

LangGraph v1 gives you **two** ways to pause for a human. They are not competitors — the declarative
one is built *on top of* the dynamic one.

### 2.1 The dynamic runtime primitive: `interrupt()`

`interrupt()` is the low-level building block. You call it **inside a node**. It snapshots state to
the checkpointer, throws a `GraphInterrupt`, and stops the graph. Later you resume with
`Command(resume=<value>)`, and the node **re-runs from its top**, with `interrupt()` now returning
the value you passed.

```python
from langgraph.types import interrupt, Command

def approval_node(state: State) -> dict:
    # Execution pauses HERE; state is checkpointed. Nothing below runs yet.
    decision = interrupt({
        "action": "issue_refund",
        "amount": state["amount"],
        "customer_id": state["customer_id"],
        "reason": state["draft_reason"],
    })
    if decision["type"] == "approve":
        return {"result": issue_refund(state["customer_id"], state["amount"])}
    if decision["type"] == "reject":
        return {"result": None, "note": decision.get("message", "rejected by reviewer")}
    ...

# Resume from anywhere, any time, any reviewer — keyed by thread_id:
graph.invoke(
    Command(resume={"type": "approve"}),
    config={"configurable": {"thread_id": "ticket-8842"}},
)
```

Use `interrupt()` when the pause is **conditional and dynamic** — you only stop *sometimes*, based
on runtime state (e.g. refund > €100, or low retrieval confidence). It is maximally flexible and
maximally your-responsibility.

### 2.2 The declarative middleware: `interrupt_on` with standard decisions (v1)

The headline v1 feature is **`HumanInTheLoopMiddleware`**. Instead of writing an approval node, you
*declare* which tools require review and which decisions are allowed. The middleware intercepts the
tool call **before it executes**, interrupts, and resumes the tool with (possibly edited) arguments.

```python
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware

agent = create_agent(
    model="openai:gpt-5.4",       # provider-agnostic; Kompass configures this in config.py
    tools=[issue_refund, update_ticket, send_email, search_kb],
    middleware=[
        HumanInTheLoopMiddleware(
            interrupt_on={
                # Risky, irreversible → full gate
                "issue_refund":  {"allowed_decisions": ["approve", "edit", "reject"]},
                "send_email":    {"allowed_decisions": ["approve", "edit", "reject"]},
                # Reversible → approve/reject only
                "update_ticket": {"allowed_decisions": ["approve", "reject"]},
                # search_kb is read-only → not listed → never interrupts
            },
            description_prefix="Kompass wants to run an action that needs review:",
        ),
    ],
    checkpointer=checkpointer,     # SQLite local / Postgres prod — see §3.3
)
```

The reviewer's answer comes back as a **decision** on resume. `approve` runs the tool as drafted;
`edit` runs it with modified args; `reject` skips it and feeds the rejection back to the model so it
can re-plan.

```python
# The approval card POSTed {"type": "edit", "args": {"amount": 250}}
agent.invoke(
    Command(resume=[{"type": "edit", "args": {"amount": 250}}]),
    config={"configurable": {"thread_id": "ticket-8842"}},
)
```

### 2.3 Choosing between them — and what the middleware replaced

| Aspect | Dynamic `interrupt()` | Declarative `interrupt_on` middleware |
|---|---|---|
| Granularity | Any point inside a node | Per-tool, before execution |
| Trigger | Arbitrary runtime condition you code | Tool is in `interrupt_on` map |
| Decision contract | You define it | **Standard** `approve` / `edit` / `reject` |
| Arg editing | You wire it by hand | Built-in (`edit` → new args) |
| Boilerplate | High (bespoke node + routing) | One config block |
| Best for | Conditional / non-tool pauses | The 80% case: gating tool calls |

The middleware is precisely the productization of the old hand-rolled contract. Map the concepts:

| Crehana hand-rolled (pre-v1) | LangGraph v1 middleware |
|---|---|
| `waiting_for = "approval"` flag in state | Automatic interrupt at the tool boundary |
| `allowed_actions: list[str]` | `allowed_decisions: ["approve","edit","reject"]` |
| `response_builder` returns discriminated union for the UI | Interrupt payload carries the pending action for the card |
| Re-invoke with decision merged into state | `Command(resume=[decision])` |
| Idempotency you remembered to add | Idempotency you *still* must add (see §3.1) |

> **Interview soundbite:** "`interrupt()` is the primitive; the declarative HITL middleware is the
> productized pattern on top of it. I used `interrupt_on` for the standard tool-gating case and drop
> to raw `interrupt()` only when the pause condition is dynamic — like 'only above €100'."

---

## 3. Gotchas (Interview Gold)

These are the things that look fine in a notebook and page you at 2 a.m. in production.

### 3.1 Idempotency: on resume the node RE-EXECUTES from the top

When you resume, the interrupted node runs **again from its first line** — `interrupt()` replays and
returns the resume value, but every statement *before* it runs a second time. Any side effect placed
before the interrupt is therefore executed **twice**.

```python
# ❌ BUG: create_ticket() runs on the first pass AND again on resume → duplicate ticket
def bad_node(state):
    ticket = crm.create_ticket(state["customer_id"])   # side effect BEFORE interrupt
    decision = interrupt({"ticket": ticket.id, ...})    # pause → replays from top on resume
    if decision["type"] == "approve":
        return {"ticket": ticket.id}

# ✅ FIX A: move side effects AFTER the interrupt
def good_node(state):
    decision = interrupt({"customer_id": state["customer_id"], ...})
    if decision["type"] == "approve":
        ticket = crm.create_ticket(state["customer_id"])   # runs exactly once, on resume
        return {"ticket": ticket.id}

# ✅ FIX B: make the write idempotent with a natural key
def issue_refund(customer_id, amount, thread_id):
    return stripe.Refund.create(
        amount=amount, customer=customer_id,
        idempotency_key=f"kompass-refund-{thread_id}",   # duplicate call → same result, no double charge
    )
```

The declarative middleware helps here because the interrupt fires **before** the tool runs, so the
tool body isn't the thing being replayed. But the *tool itself can still be re-invoked* on retries
and manual re-runs, so **every write tool in Kompass takes an idempotency key derived from the
`thread_id` + action**. Idempotency is not optional in a resumable system.

> **Interview soundbite:** "Resuming re-executes the node from the top, so anything before
> `interrupt()` runs twice. My rule: no non-idempotent side effect before an interrupt, and every
> write tool takes an idempotency key. That single rule prevents double refunds."

### 3.2 Checkpoints persist BETWEEN nodes, not WITHIN

The checkpointer snapshots state at **node boundaries** (LangGraph super-steps), not mid-node. If a
node makes three API calls and the process dies on the third, restart re-runs the **entire node**
from scratch — the effects of the first two calls were never checkpointed and are not "known" to the
graph.

```
Node boundary ──[checkpoint]── Node A ── (call1, call2, 💥crash on call3) ── [no checkpoint here]
Restart ────────────────────▶ Node A runs AGAIN from call1
```

Consequence: keep nodes **small and single-responsibility**, and if a single logical step is a
long-running, multi-call, must-not-lose-progress process, a graph checkpointer is the wrong tool —
that is the boundary where you reach for **durable execution** (§5).

> **Interview soundbite:** "LangGraph checkpoints *between* nodes, not *within* them. So half-done
> work inside a node is lost on a crash. That's the exact line where I stop leaning on the
> checkpointer and reach for Temporal."

### 3.3 Durable + resumable: how the checkpointer makes this real

Because state is persisted and keyed by **`thread_id`**, a paused run is not tied to a live socket,
a browser tab, or even the reviewer who triggered it:

- The user **refreshes** the page → the thread still exists; the approval card re-renders from
  persisted state.
- A **different reviewer** opens the card and approves → resume is just
  `Command(resume=...)` against the same `thread_id`.
- The **server restarts** → the pending interrupt is still on disk; resume works after redeploy.

| Backend | Class | Use in Kompass |
|---|---|---|
| SQLite | `SqliteSaver` | Local dev, single-process, demos |
| Postgres | `PostgresSaver` / `AsyncPostgresSaver` | Production — shared, concurrent, survives restarts |

The `thread_id` **is** the resume handle. Kompass returns it to the client on every interrupt so the
UI (and later, a totally different reviewer) can address the exact paused run.

> **Interview soundbite:** "The checkpointer plus a `thread_id` is what makes HITL *durable and
> resumable* — the run outlives the tab, the reviewer, and even a redeploy. SQLite locally,
> Postgres in prod, same code."

### 3.4 Decision types: `approve` / `edit` / `reject` is the standard contract

The whole reason to standardize is that the frontend, API, and agent all speak one vocabulary:

| Decision | Meaning | What the agent does next |
|---|---|---|
| `approve` | Run the action exactly as drafted | Execute tool with original args → confirm |
| `edit` | Change the arguments, then run | Execute tool with the human's new args → confirm |
| `reject` | Don't run it | Skip tool; feed the rejection (with reason) back to the model to re-plan or ask a question |

This three-verb contract is small enough to render as three buttons and expressive enough to cover
real review: rubber-stamp, correct-and-go, or veto-with-feedback.

---

## 4. Kompass's HITL Flow End-to-End

Here is a refund flowing through the whole system — Action Agent → middleware → approval card →
execute → confirm — and how it maps to the API surface and the Streamlit UI.

```
 User: "Refund my last order, it never arrived."
      │
      ▼
┌───────────────┐   ┌──────────────┐   ┌───────────────────────────┐
│ Router /      │──▶│ Retrieval /  │──▶│ Action Agent               │
│ Supervisor    │   │ RAG (policy) │   │ drafts issue_refund(€400)  │
└───────────────┘   └──────────────┘   └─────────────┬─────────────┘
                                                      │ tool call intercepted
                                                      ▼
                                     ┌──────────────────────────────────┐
                                     │ HumanInTheLoopMiddleware           │
                                     │ interrupt_on["issue_refund"]       │  ⏸  checkpoint saved
                                     └───────────────┬──────────────────┘     (thread_id persisted)
                                                     │ GraphInterrupt
                                                     ▼
                        API responds 200 { status: "interrupted",
                                            thread_id, pending_action }
                                                     │
                                                     ▼
                              ┌─────────────────────────────────────────┐
                              │ Streamlit Approval Card                   │
                              │  Action: issue_refund                     │
                              │  Amount: €400  Customer: 8842             │
                              │  [ Approve ]  [ Edit ]  [ Reject ]        │
                              └───────────────┬───────────────────────────┘
                                              │ POST /resume
                                              │ { thread_id, decision:{type:"edit", args:{amount:250}} }
                                              ▼
                              ┌─────────────────────────────────────────┐
                              │ Command(resume=[decision]) → tool runs    │
                              │ issue_refund(..., idempotency_key=tid)    │
                              └───────────────┬───────────────────────────┘
                                              ▼
                              "Done — €250 refunded to order #8842." + audit log
```

**API surface.** The run endpoint returns `status: "interrupted"` plus the `thread_id` and the
`pending_action` payload instead of a final answer. The reviewer's verdict comes back through a
single **`POST /resume`** endpoint carrying `{ thread_id, decision }`; the server maps it to
`Command(resume=[decision])` and continues the exact paused run. Because everything is keyed by
`thread_id`, `/resume` is stateless from the client's perspective — anyone with the id can resolve
the card.

**UI surface.** The Streamlit **approval card** renders straight from `pending_action`: it shows the
tool, the arguments, and the three buttons. `Edit` opens the args for correction and POSTs an `edit`
decision; `Approve`/`Reject` POST their types. The card is regenerable from persisted state, so a
refresh or a hand-off to a senior agent just re-renders it.

See the full narrative — routing, retrieval, guardrails, and this approval gate together — in the
[customer-support case study](../entrevista/casos/caso_02_customer_support.md), and the component
layout in the [architecture doc](05_architecture.md).

> **Interview soundbite:** "The whole HITL loop collapses to one contract: the agent interrupts and
> returns a `thread_id` + pending action, the card renders three buttons, and `POST /resume` with an
> approve/edit/reject decision continues the exact same run. No polling, no bespoke state machine."

---

## 5. When a Checkpointer Isn't Enough: Durable Execution (Temporal)

A checkpointer is perfect when the pause is **minutes-to-hours** and the surrounding work is a set of
small graph nodes. It starts to strain when:

- a decision can legitimately take **days** (manager sign-off, legal review),
- a single logical step is a **long-running, multi-system** process with its own retries and SLAs
  (recall §3.2 — checkpoints don't protect *within* a node),
- you need **guaranteed** retries, timeouts, and compensation across external services.

At that point you want **durable execution**. In Temporal, the workflow *code itself* is durable:
it survives worker crashes and can literally `await` a human **signal** for as long as it takes,
while each external call is a separately-retried, separately-recorded **activity**.

```
┌──────────────────────────────────────────────────────────────┐
│ Temporal Workflow  (durable state machine, survives restarts)  │
│                                                                │
│   1. activity: run_langgraph_agent()      → drafts action      │
│                                                                │
│   2. await workflow.wait_condition(signal_received)  ⏳ days OK │
│            ▲                                                    │
│            │ signal("approval", decision)                      │
│                                                                │
│   3. activity: execute_action(decision)   → retried on failure │
│                (idempotency_key = workflow_id)                 │
│                                                                │
│   4. activity: send_confirmation()                             │
└──────────────────────────────────────────────────────────────┘
             ▲
             │ POST /resume  →  temporal_client.signal(workflow_id, decision)
        Reviewer (any time, any device)
```

The pattern is a clean superset of the checkpointer flow: the same `POST /resume` endpoint, but
instead of `Command(resume=...)` it sends a **signal** to a running workflow. The LangGraph agent
runs *inside* an activity for its reasoning; Temporal owns the long-lived, must-not-be-lost
orchestration and the "await signal" wait. Same idempotency discipline applies — the workflow id is
the idempotency key.

> **Interview soundbite:** "A LangGraph checkpointer resumes a paused run; Temporal *durably
> executes* a long-lived workflow that can `await` a human signal for days and retry each external
> call independently. Checkpointer for minutes-to-hours HITL, Temporal when a single step is
> long-running or approval takes days."

The trade-off — more infrastructure and a second mental model — is why Kompass **defaults to the
checkpointer** and treats Temporal as the documented escape hatch for genuinely long-running,
mission-critical actions rather than the everyday path.

---

## Related

- [03 — Framework Decision](03_framework_decision.md): why LangGraph v1 won, with durable HITL as a
  first-class deciding factor.
- [05 — Architecture](05_architecture.md): where the HITL middleware, checkpointer, `/resume`
  endpoint, and Streamlit card live in the system.
- [06 — Advanced Patterns](06_advanced_patterns.md): durable execution, multi-agent supervision, and
  the Temporal escape hatch in depth.
- [02 — Retrieval Strategies](02_retrieval_strategies.md): the RAG step that feeds the Action Agent's
  drafts.
- Case study — [Customer Support Automation](../entrevista/casos/caso_02_customer_support.md): the
  refund-approval flow narrated end-to-end.
- Interview framework — [PACTEDR](../entrevista/framework_PACTEDR.md): how to structure the HITL story
  in an interview.
