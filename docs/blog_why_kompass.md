# Why I built Kompass the way I did

Most "agent" demos are RAG in a trench coat. You type a question, the system retrieves a few
chunks, the model generates a paragraph, and the demo ends. It *looks* agentic because there
is a chat box, but nothing agentic happened: no plan, no tool that changes the world, no
moment where the system decides *not* to act. It answered. It didn't resolve anything.

I built Kompass to be the opposite. It's a Support & Operations assistant on LangGraph v1, and
its job is to **resolve and act** — verify an order, check the policy, draft a refund, and then
*stop* and ask a human before it moves any money. The interesting engineering isn't in the
retrieval; it's everything around the model call: what happens on turn five, after a crash,
and when the model wants to do something it shouldn't.

## The thesis: resolution with governance

The metric I care about isn't "was the answer good." It's **resolution**: did the request get
closed end to end, with no human touch and no follow-up escalation — and did nothing unsafe
happen along the way? A pretty answer the user doesn't trust (so they open a ticket anyway) is
a failure, not a success. That reframing pushes three properties to the front:

- **Grounding.** Every claim is checked against retrieved evidence, with mandatory citations.
  Ungrounded confidence is the failure mode that quietly destroys trust.
- **Human-in-the-loop on risky actions.** Reads are free; writes are gated. Every side effect
  flows through one Action Agent and a single approve/edit/reject checkpoint, so "zero unsafe
  actions" is an architectural invariant, not a prompt I'm hoping the model obeys.
- **Safety.** A dedicated middleware screens the inbound turn for prompt injection *before* any
  tool runs, and a versioned red-team suite keeps it honest in CI.

The canonical demo is a refund: a customer reports order 4471 arrived damaged (ticket 88012).
Kompass verifies the order and the refund policy over MCP tools, drafts a €189.99 refund, and
the HITL middleware **pauses** with an approve/edit/reject card. A reviewer approves, the run
resumes from the exact checkpoint, and the refund and ticket update land in the database. That
pause is the whole product.

## LangGraph v1, for the pause

If the defining feature is "a refund that pauses, waits for a human, and survives a redeploy,"
the framework question answers itself: I need durable, resumable execution with first-class
human-in-the-loop. LangGraph v1 gives me exactly that — a declarative
`HumanInTheLoopMiddleware(interrupt_on=…)` over a checkpointer, so the approve/edit/reject
contract is a few lines of declaration instead of the interrupt-contract plumbing I hand-rolled
pre-v1 at Crehana. State is checkpointed between nodes, so an approval can arrive hours later,
after a crash or a redeploy, and resume cleanly.

I didn't cargo-cult it — I pressure-tested it (below) and documented the ceiling: for a
workflow that must survive a crash *mid-step* and replay from an event log, Temporal, not
LangGraph's between-node checkpointing, is the right altitude. Kompass doesn't need that today,
but knowing where the ceiling is matters.

## Four decisions I measured — two I got wrong first

**Rejecting the cheap model.** The obvious cost win is to run the agent loop on the cheapest
model, `gpt-5.4-nano`. I tried it. It matched the balanced model on fact-correctness, so on a
naïve scorecard it "passed" — but its grounding rate dropped to 77%: more unsupported claims.
My grounding critic caught those and forced retries, so the "cheap" model produced *higher* net
latency *and* worse quality. I kept the balanced `gpt-5.4` for the loop. The lesson, now a
comment in the code: for a support agent, grounding is a **safety** metric, not a quality
nicety — you don't trade it for tokens.

**Scoping plan-and-execute to where it pays.** I added a plan-and-execute layer — a planner
that decomposes a goal into a checklist and replans on failure — because long multi-step tasks
drift without it. Then the full eval caught a regression: on trivial single-lookup queries the
planner multiplied turns, latency, and cost without improving accuracy. So I scoped it to
multi-agent mode only, where the supervisor genuinely has to coordinate a Researcher and the
action tools; the single agent answers a one-or-two-step task directly. That's the project's
own "use the simplest thing that works" thesis applied to itself — let the eval, not my
enthusiasm, decide when the fancy thing pays.

**A weakness the user-simulator found.** I run a τ-bench-style harness where an LLM plays the
customer with a hidden goal. It surfaced a real bug: for order 4462 — a change-of-mind return
well outside the 30-day window — the agent drafted a refund anyway and *leaned on the HITL
gate* to catch it. That's backwards; the gate is a safety net, not the primary control. I
hardened the prompt to verify eligibility *before* proposing an action and kept the scenario as
a regression test. The agent now refuses 4462 on cited policy grounds.

**The framework spike.** To prove the choice was judgment and not loyalty, I rebuilt the
Researcher in PydanticAI and ran both live against the same golden items. Quality was an honest
tie — 7/7 correct, 7/7 cited, both 100% grounded — and PydanticAI was genuinely leaner: about a
third of the dependency surface, ~20% fewer tokens, faster to build. But it's stateless by
default; the durable, resumable pause that defines Kompass is something you'd hand-build on top
of it. So LangGraph stays primary, and I can say exactly *why* — not *because it's popular*.
Full trade-off: [`../spike_frameworks/comparison.md`](../spike_frameworks/comparison.md).

## What the evidence shows

I don't defend this with vibes, so Kompass ships an eval harness that regenerates its own
numbers. On a 35-item golden set (LLM-as-judge on the reasoning tier, plus deterministic fact,
citation, and side-effect checks), against a naïve-RAG baseline:

- **Resolution: 97%** vs **11%** for the baseline.
- **Grounded: 97%** (baseline 14%); **citation discipline: 100%** (baseline 49%).
- **Unsafe actions** (a rejected action that still executed): **0**.
- ~**10s** and ~**$0.020** per case.

The safety layer is measured too: the prompt-injection red-team blocks **93%** of attacks at a
**0%** false-positive rate.

## What I'd do next in production

Kompass runs local-first by design. Three edges I'd harden before it touched real customers:

- **Postgres checkpointer.** The local SQLite checkpointer proves the mechanics; production
  HITL needs the durable Postgres saver so an approval survives a real redeploy and a real
  crash, with an auditable trail.
- **A real sandbox.** The Data Analyst's code execution is AST-allowlisted in a subprocess
  today. In production I'd move it into a container / microVM with no network egress and no
  secrets mounted — the AST allowlist is a speed bump, not an isolation boundary.
- **Real DLP for PII.** The PII redaction is a helper, not a policy engine. Proper GDPR work
  needs a data-loss-prevention layer on the output path, not a regex.

None of these change the architecture; they harden where local-first meets real-world stakes.
The shape of the system is what I'd defend in any interview: it resolves and acts, it cites its
evidence, and a human owns every risky action — and the eval history shows which tuning was
worth it.

The full architecture is in [`05_architecture.md`](05_architecture.md); the framework rationale,
with sources, is in [`03_framework_decision.md`](03_framework_decision.md).
