# Agentic AI Interview Question Bank

A working set of 55 questions I use to prepare for German/European AI-engineering interviews (DHL Data & AI, VW/CARIAD, and similar). Every answer is written the way I would actually say it in a room: senior, concrete, and anchored to systems I have shipped — MCP servers in production at VW/CARIAD, a RAG pipeline that took answer accuracy past 90%, multi-agent LangGraph orchestration at Crehana, and NL2SQL over 40M+ records. This is the same material that documents the **Kompass** reference project, so the theory links point at the design docs that back each claim.

## How to use this bank with PACTEDR

Short factual questions ("what is MCP?", "hybrid vs dense retrieval?") you answer directly — the entries below are calibrated to 3–5 sentences, which is the right length to sound precise without rambling. Open-ended design questions ("design a support agent that can issue refunds") you answer with a **framework**, not a brain-dump. That is what [`framework_PACTEDR.md`](framework_PACTEDR.md) is for: it gives a repeatable arc — start from business value and the problem, move to approach and components, name the tradeoffs, then evaluation, deployment/durability, and risks/guardrails — so you never freeze on a blank whiteboard.

The workflow I recommend: read the relevant theory doc in [`../docs/`](../docs/) to get the model straight in your head, drill the Q&A here until the answers are muscle memory, then rehearse a full case out loud using PACTEDR and the four worked cases in [`casos/`](casos/). Where an answer maps to a deeper treatment, I link it — e.g. retrieval decisions live in [`../docs/02_retrieval_strategies.md`](../docs/02_retrieval_strategies.md) and the framework choice is defended in [`../docs/03_framework_decision.md`](../docs/03_framework_decision.md).

> **Interview soundbite:** "I don't answer design questions with a list of technologies — I answer with business value first, then architecture, tradeoffs, and how I'd evaluate and de-risk it. That's the PACTEDR arc."

---

## 1. Fundamentals

**Q1: What actually makes a system "agentic"?**

A system is agentic when an LLM controls the flow of the program — it decides which action to take next, executes tools, observes the result, and loops until a goal is met, rather than following a path I hard-coded. The three ingredients are autonomy over control flow, the ability to act on the world through tools, and a feedback loop that lets it self-correct from observations. A single LLM call that returns text is not agentic; a system that reads a ticket, queries a database, drafts a fix, and re-checks its own work is. The key mental shift is that I hand over control of *what happens next* to the model, which is powerful and also exactly why guardrails and observability stop being optional. See [`../docs/01_agentic_ai_deep_dive.md`](../docs/01_agentic_ai_deep_dive.md) for the full treatment.

> **Interview soundbite:** "Agentic means the LLM controls the flow — it chooses the next action, acts through tools, and loops on the result. That autonomy is the whole value and the whole risk."

**Q2: Agent vs. workflow vs. plain RAG — where's the line?**

A workflow is orchestration where I define the steps and the LLM fills in slots at fixed points — predictable, cheap, easy to test. Plain RAG is a specific workflow: retrieve, stuff context, generate — one pass, no decisions about what to do next. An agent is the case where the model decides the steps at runtime, including how many times to loop and which tool to call. The honest framing I use in interviews is that these are a spectrum of increasing autonomy, and most production value sits in *workflows with a few agentic escape hatches*, not fully autonomous agents. This spectrum is exactly why Kompass is built as a controlled graph, not an open-ended ReAct loop.

**Q3: Walk me through the autonomy spectrum.**

I use the same five-rung escalation ladder as [`../docs/01_agentic_ai_deep_dive.md`](../docs/01_agentic_ai_deep_dive.md): (1) a single LLM call, (2) RAG single-shot, (3) a workflow of predefined steps (chain / route / parallelize), (4) a single agent that runs a bounded ReAct loop over a tool set with a step cap and HITL, and (5) multi-agent — specialized agents collaborating under a supervisor. Cost, latency, and failure surface grow with every rung; testability and predictability shrink. The discipline is to climb down only when an eval delta forces it and to sit at the *lowest* rung that clears the bar — most support and ops tasks live at rung 3 or 4. Kompass sits primarily at rung 4 (a LangGraph state machine with a bounded tool set and a hard recursion limit) and reaches into rung 5 only for the handful of tasks that genuinely need distinct expert workers under the supervisor.

**Q4: When should you NOT use an agent?**

When the task is deterministic and well-specified, an agent is strictly worse — you pay more latency, more tokens, and more nondeterminism for no benefit; a SQL query or a rules engine wins. Avoid agents when errors are unrecoverable and unmonitored (moving money with no approval step), when latency budgets are tight (sub-200ms), or when you can't afford the evaluation cost of a nondeterministic system. I've turned down "let's make it an agent" more than once: if a fixed pipeline plus one routing decision covers 95% of traffic, that's the answer. The agentic part should be reserved for the genuinely open-ended long tail.

> **Interview soundbite:** "The best agent is often the one you didn't build. If a deterministic pipeline plus one router covers the traffic, adding autonomy just buys you latency, cost, and nondeterminism."

---

## 2. Retrieval / RAG and alternatives

**Q5: Why hybrid retrieval instead of pure vector search?**

Dense (vector) retrieval captures semantic similarity but is famously weak on exact matches — product SKUs, error codes, function names, rare acronyms — because those get smeared in embedding space. Sparse retrieval (BM25) nails lexical matches but misses paraphrase. Hybrid runs both and fuses the results, typically with Reciprocal Rank Fusion, which is robust because it needs no score calibration between the two systems. On my RAG work the single biggest accuracy jump came from adding BM25 back alongside embeddings — the "+90% accuracy" milestone was hybrid retrieval plus reranking, not a fancier embedding model. More in [`../docs/02_retrieval_strategies.md`](../docs/02_retrieval_strategies.md).

> **Interview soundbite:** "Pure vector search quietly fails on error codes and SKUs. Hybrid — BM25 fused with embeddings via RRF — is the cheapest reliability win in RAG, and it's what pushed my accuracy past 90%."

**Q6: What does a reranker actually buy you?**

First-stage retrieval optimizes recall — get the right chunk into the top 50 cheaply. A cross-encoder reranker then reads the query and each candidate *together* (not as separate vectors) and scores true relevance, so you can hand the LLM a tight, high-precision top-5 instead of a noisy top-20. That precision matters because irrelevant context doesn't just waste tokens — it actively degrades generation and invites hallucination. The cost is latency and a second model, so I cap the rerank candidate set and cache aggressively. Rule of thumb: retrieve wide and cheap, rerank narrow and smart.

**Q7: What is CAG (cache-augmented generation) and when do you prefer it over RAG?**

CAG skips runtime retrieval entirely: you preload the entire relevant corpus into the model's context (and, crucially, into the KV cache) so every query reuses the cached prefix. It wins when the knowledge base is small and stable enough to fit the context window — a single product manual, a policy handbook — because you eliminate retrieval latency, retrieval errors, and the whole vector-DB operational burden. It loses the moment the corpus is large, changes frequently, or needs per-user filtering, and it's expensive if prompt caching isn't available. My decision rule: if it fits in context and rarely changes, CAG; otherwise RAG. It pairs beautifully with prompt caching to make the cached prefix nearly free after the first call.

**Q8: When is GraphRAG worth the complexity?**

GraphRAG builds a knowledge graph of entities and relationships and retrieves subgraphs, which shines on *multi-hop* and *global* questions — "which incidents share a root cause with ticket X?" or "summarize the themes across all Q3 outages" — that flat chunk retrieval simply can't answer because the answer isn't in any single chunk. The cost is real: graph construction is an expensive LLM-heavy indexing step, and it adds serious operational complexity. So I only reach for it when queries are genuinely relational or require corpus-wide synthesis; for lookup-style Q&A it's over-engineering. Often the pragmatic middle ground is hybrid RAG plus a reranker, with GraphRAG reserved for a specific class of analytical queries.

**Q9: How do you decide between RAG and NL2SQL for a question?**

They answer different shapes of question. RAG is for unstructured knowledge — "how do I reset a device?" — where the answer is text living in documents. NL2SQL is for structured, aggregate, computational questions — "what was refund volume by region last quarter?" — where the answer must be *computed* over rows and RAG would just hallucinate numbers. I built NL2SQL over 40M+ records exactly because no amount of chunk retrieval can SUM a column correctly. The best support agents route: a classifier or the agent itself decides "is this a knowledge lookup or a data question?" and dispatches accordingly — that's adaptive routing.

> **Interview soundbite:** "RAG retrieves facts; NL2SQL computes them. If the true answer is a SUM or a GROUP BY over millions of rows, retrieval will confidently hallucinate — you need NL2SQL, which is why I built it over 40M records."

**Q10: Adaptive/self-routing retrieval — what problem does it solve and what's the risk?**

Not every query needs the same pipeline: a greeting needs no retrieval, a factual lookup needs one retrieval pass, a hard analytical question needs multi-hop or NL2SQL. Adaptive routing puts a lightweight decision up front (a small classifier or a cheap LLM call) that picks the retrieval strategy — or skips it — per query, which saves latency and tokens on the easy majority and reserves the heavy machinery for the long tail. The risk is misrouting: send a data question to RAG and you get a confident hallucination. So I always evaluate the router as its own component with its own golden set, and I make "no retrieval" and "escalate to NL2SQL" first-class routes, not afterthoughts.

**Q11: How do you evaluate a retrieval system — separately from the LLM?**

I evaluate retrieval and generation independently, because bundling them hides where the failure is. For retrieval I use a golden set of query→relevant-doc pairs and measure recall@k, precision@k, MRR, and nDCG — these tell me if the right chunk even made it into context. For generation given that context I use RAGAS-style metrics: faithfulness (is the answer grounded in the retrieved context?), answer relevance, and context precision/recall. The discipline that matters: if faithfulness is low but retrieval recall is high, the bug is in generation, not search — and I've seen teams waste weeks tuning the wrong half. Chunking strategy gets evaluated the same empirical way — I A/B chunk sizes against the golden set rather than arguing about it.

---

## 3. Architecture / multi-agent

**Q12: Supervisor vs. swarm — how do you choose?**

In a supervisor (orchestrator) pattern one coordinator owns routing: it receives the task, delegates to specialist workers, and collects results — control always returns to the center. In a swarm, agents hand off directly to each other peer-to-peer with no central coordinator. Supervisor wins on observability, control, and debuggability because there's one place to reason about state and enforce policy; swarm wins on flexibility and avoiding a bottleneck but is far harder to trace and bound. For anything touching business actions I default to supervisor — that's what I run in Kompass and what I built at Crehana — because "where did this decision get made?" must have a single answer. See [`../docs/05_architecture.md`](../docs/05_architecture.md).

> **Interview soundbite:** "I default to a supervisor architecture for anything that acts on the business. Swarms are elegant but when an auditor asks 'where was this decision made?', a supervisor gives one answer and a swarm gives a shrug."

**Q13: What is the orchestrator-workers pattern good for, and how does it differ from routing?**

Routing is a one-shot decision: classify the input, send it down one of N fixed branches, done. Orchestrator-workers is dynamic: the orchestrator decomposes a task into subtasks *at runtime* — the number and nature of which aren't known in advance — dispatches them to workers (often in parallel), and synthesizes the results. You use it when the work can't be pre-partitioned, like "research this topic across these sources" where the orchestrator decides how many sub-queries to spawn. The tradeoff versus routing is cost and nondeterminism, so I bound the fan-out and give the orchestrator a clear synthesis step rather than letting workers sprawl.

**Q14: How do you control loops and prevent runaway agents?**

Multiple layers, because a single guard always fails eventually. First, a hard recursion/step limit in the graph — LangGraph's `recursion_limit` will halt the graph rather than loop forever. Second, a token and wall-clock budget per request that trips a circuit breaker. Third, loop-detection: if the agent proposes the same tool call with the same args twice, that's a signal it's stuck, and I break to a fallback or human. Fourth, a supervisor that can decide "we're not converging, escalate." The mindset is that loops are a *when*, not an *if*, so the graceful-degradation path is part of the design, not an afterthought — see [`../docs/06_advanced_patterns.md`](../docs/06_advanced_patterns.md) and the failure-modes section below.

**Q15: How do you design agent state, and why does it matter so much in LangGraph?**

State is the single source of truth that flows between nodes, so I design it as an explicit typed schema — messages, retrieved context, plan, intermediate results, approval status, error accumulators — not a free-floating dict. In LangGraph you control how each field updates via reducers (e.g. append to the message list, overwrite the plan), which is what lets you do things like accumulate tool results without clobbering history. Good state design is what makes the graph resumable, checkpointable, and debuggable, because the checkpoint *is* the state. The mistake I coach people out of is stuffing everything into one giant message list; separating durable structured fields from the chat transcript is what makes HITL and durable execution actually work.

---

## 4. Tools / MCP and A2A

**Q16: What is MCP and why does it matter?**

The Model Context Protocol is an open standard (from Anthropic, now broadly adopted) that defines *how* an LLM application connects to external tools, data, and prompts — think of it as USB-C for AI: one protocol, so any MCP-compatible client can talk to any MCP server. It matters because it decouples tool development from agent development: I write a tool server once, and every agent framework can consume it without bespoke glue. At VW/CARIAD I ran MCP servers in production, and the operational payoff was exactly this decoupling — the tools team ships capabilities as servers with their own versioning and auth, and the agent teams consume them over a stable contract. It turns "integrations" from N×M custom code into N+M standardized endpoints. See [`../docs/01_agentic_ai_deep_dive.md`](../docs/01_agentic_ai_deep_dive.md).

> **Interview soundbite:** "MCP is USB-C for AI tools. I ran MCP servers in production at VW/CARIAD — the win is decoupling: write a tool server once, and every agent consumes it over a stable contract instead of N×M custom integrations."

**Q17: MCP vs. plain function calling — aren't they the same thing?**

Function calling is the *model capability* — the LLM emits a structured request to invoke a function. MCP is the *transport and packaging standard* around that — how the tool is discovered, described, authenticated, and served across a process or network boundary. You still use function calling under MCP; MCP just standardizes where the tools live and how they're delivered.

| Aspect | Raw function calling | MCP |
|---|---|---|
| Scope | In-process functions | Client/server, cross-process, remote |
| Reuse | Per-app, custom | Write once, any MCP client |
| Discovery | Hard-coded | Dynamic (server advertises tools/resources/prompts) |
| Auth & versioning | DIY per integration | Standardized at the server boundary |
| Best for | A handful of local tools | Shared, governed tool platforms |

The trap in interviews is treating them as competitors — they compose. I reach for MCP the moment tools need to be shared across teams or services; for two local helpers, raw function calling is fine and MCP is overkill.

**Q18: What are the three primitives an MCP server exposes?**

Tools (functions the model can call to *act* — the agentic surface), Resources (read-only data the client can load into context, like files or records — retrieval-ish), and Prompts (reusable templated instructions the server offers). Keeping tools and resources distinct matters for security and cost: resources are safe to expose broadly, tools are the ones that need permission scoping and approval. This separation is also why MCP plays nicely with HITL — you gate the *tools*, not the reads. In Kompass the MCP servers expose ops tools (refund, ticket update) as gated tools and the knowledge base as resources.

**Q19: What is A2A and how does it relate to MCP — competitors or layers?**

A2A (Agent-to-Agent) is a protocol for *agents talking to other agents* as peers — discovering each other's capabilities via "agent cards," delegating tasks, and exchanging results — whereas MCP is for an agent talking to *tools and data*. They're complementary layers, not rivals: MCP is the vertical axis (agent→tools), A2A is the horizontal axis (agent↔agent). A clean way to say it: MCP is how an agent uses a capability, A2A is how an agent asks another agent to handle something it can't. In a mature enterprise you'd expect both — internal specialist agents federating over A2A, each of them using tools over MCP.

> **Interview soundbite:** "MCP and A2A are two axes, not competitors. MCP is agent-to-tool, A2A is agent-to-agent. One is how an agent acts; the other is how agents delegate to each other."

**Q20: How do you secure MCP tools — what stops an agent from doing damage?**

Permission scoping at the server boundary: each tool declares what it can touch, and the server enforces auth independently of the agent — the agent never gets raw credentials, it gets a scoped capability. Destructive or costly tools (refunds, deletions, external sends) are marked as requiring human approval, which the graph enforces via HITL before the call executes. I also validate tool *arguments* with typed schemas so a hallucinated or injected argument fails closed rather than executing garbage. And everything is logged with the full tool-call trace for audit — at VW/CARIAD, "who called what with which args" being answerable was a hard requirement, not a nice-to-have. Security detail lives in [`../docs/06_advanced_patterns.md`](../docs/06_advanced_patterns.md).

---

## 5. Memory and self-improving

**Q21: Short-term vs. long-term memory in an agent — what's the distinction?**

Short-term memory is the working context of a single session — the message history, intermediate results, the current plan — bounded by the context window and thrown away (or checkpointed) at session end. Long-term memory persists *across* sessions in an external store — a user's preferences, past resolutions, learned facts — and is retrieved into context when relevant. The engineering difference is that short-term is state management (reducers, trimming, summarization) while long-term is a retrieval problem (embed, store, fetch by relevance). Conflating them is a classic mistake; in Kompass short-term lives in the LangGraph checkpoint and long-term lives in a separate vector/document store.

**Q22: Explain semantic vs. episodic vs. procedural memory.**

Borrowing the cognitive-science split: semantic memory is facts ("this customer is on the enterprise plan"), episodic memory is specific past experiences ("last time we resolved a similar ticket, this fix worked"), and procedural memory is learned how-to (updated instructions or few-shot examples the agent uses to do the task better). They're retrieved differently and used differently: semantic grounds the answer, episodic enables case-based reasoning and few-shot from real history, procedural improves the *policy* itself. Most systems only implement semantic and stop there. The high-value move for a support agent is episodic — "have we seen this before and what worked?" — which turns every resolved ticket into future leverage.

**Q23: When does memory HURT?**

More often than people expect. Stale memory poisons answers — a cached preference that's no longer true is worse than no memory. Irrelevant retrieved memories bloat context and cause context rot, degrading the very reasoning you were trying to help. Memory is also a privacy and GDPR liability: storing user data long-term means you now owe deletion, retention limits, and consent. And it opens an attack surface — memory poisoning, where an attacker plants a false "fact" that resurfaces later. So I treat writing to long-term memory as a deliberate, filtered decision with TTLs, not a default; the bar to *write* is higher than the bar to *read*.

> **Interview soundbite:** "Memory is not free upside. Stale facts, context rot, GDPR deletion duties, and memory-poisoning attacks all say the same thing: the bar to *write* long-term memory should be higher than the bar to read it."

**Q24: How do you build a self-improving / feedback loop safely?**

The safe loop is offline and human-gated, not online and autonomous. I collect signals — thumbs up/down, human corrections during HITL, task-completion outcomes — into a dataset, then use them to improve the system deliberately: curate few-shot examples, refine prompts, fine-tune a router, or expand the golden eval set. The critical guardrail is that changes pass evaluation gates before shipping, so a feedback loop can't silently degrade the system (reward hacking and drift are real). Fully online self-modification is a research toy for anything that touches money or customers; the boring, auditable version — human corrections become tomorrow's few-shots and eval cases — is what actually compounds in production.

---

## 6. HITL / durable execution

**Q25: LangGraph's `interrupt()` vs. the `interrupt_on` middleware — what changed in v1.0?**

`interrupt()` is the original *dynamic* primitive: you call it inside a node at runtime, it pauses the graph, persists state to the checkpoint, and surfaces a payload to the client; you resume with a `Command(resume=...)`. LangGraph v1.0 (Oct 2025) added a *declarative* human-in-the-loop middleware — `interrupt_on` — that lets you configure which tools require approval up front, with standardized decision types (approve / edit / reject) instead of you hand-rolling that logic in every node. The dynamic primitive is still there for arbitrary mid-node pauses; the middleware is the ergonomic default for the common "gate these tool calls" case. Kompass uses the middleware for tool approval and reserves raw `interrupt()` for bespoke pauses. Full treatment in [`../docs/04_hitl_patterns.md`](../docs/04_hitl_patterns.md).

```python
# v1.0 declarative HITL: gate risky tools with standard decisions
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware

agent = create_agent(
    model, tools=[issue_refund, update_ticket, search_kb],
    middleware=[HumanInTheLoopMiddleware(
        interrupt_on={
            "issue_refund": {"allowed_decisions": ["approve", "edit", "reject"]},
            # search_kb / update_ticket omitted from the map → auto-run, no gate
        }
    )],
)
# The refund call pauses; a human approves, edits the args, or rejects.
```

> **Interview soundbite:** "v1.0 turned HITL from something I coded in every node into a declarative middleware — `interrupt_on` with approve/edit/reject. The dynamic `interrupt()` is still there for arbitrary pauses; the middleware is the default for gating tools."

**Q26: How do you guarantee idempotency when resuming after an interrupt?**

This is the subtle bug that bites everyone: when the graph resumes, the *entire node* that called `interrupt()` re-executes from the top, so any side effect before the interrupt happens twice unless you guard it. The rules I follow: put the interrupt as early in the node as possible, and never perform a side effect (charge a card, send an email) *before* the pause. For the actual action, use an idempotency key — a deterministic ID derived from the request — so the downstream system deduplicates a repeated call. And design tools to be idempotent where possible (upsert, not insert). Getting this wrong means an approval double-charges the customer, which is exactly the kind of failure HITL was supposed to prevent.

> **Interview soundbite:** "On resume, LangGraph re-runs the whole node from the top — so a side effect before `interrupt()` fires twice. I keep interrupts early, do the real action after, and use idempotency keys so a repeated call dedupes. That's how an approval doesn't double-charge."

**Q27: Why do checkpoints between nodes give you durable execution, and what are the limits?**

Because after every node LangGraph persists the full state to a checkpointer (in-memory for dev, Postgres/Redis for prod), the graph is resumable: if the process crashes, a request times out, or a human takes three days to approve, you reload the checkpoint and continue exactly where you stopped — no lost work, no re-running expensive upstream calls. That's what makes long-lived HITL (pause for days) and crash recovery practically free. The limit is granularity: durability is at node boundaries, so a long-running side effect *inside* a node isn't itself checkpointed — which is the idempotency problem above and also the boundary where you start thinking about a dedicated durable-execution engine.

**Q28: When do you outgrow LangGraph checkpoints and reach for Temporal?**

LangGraph's checkpointing covers most agent durability needs — pause/resume, crash recovery, HITL — and it's the right default. I reach for Temporal (or a similar workflow engine) when the durability requirements exceed a single graph run: complex multi-service orchestration with sophisticated retry/backoff policies, workflows that span systems and must survive for weeks with strict exactly-once guarantees, or heavy fan-out with per-step compensation (sagas). The honest framing is they operate at different layers — you can even run a LangGraph agent *as a step inside* a Temporal workflow. So it's not "LangGraph vs. Temporal"; it's "LangGraph for the agent's reasoning loop, Temporal when the surrounding business process needs enterprise-grade durability." Defended in [`../docs/03_framework_decision.md`](../docs/03_framework_decision.md).

---

## 7. Plan-and-execute, replanning, sandbox, proactive autonomy

**Q29: How does replanning work and why not just plan once?**

Plan-once assumes the world matches your model of it, which fails the moment a tool returns something unexpected — a record isn't found, an API errors, an assumption was wrong. Replanning inserts a checkpoint after execution steps where the agent compares actual results against the plan and, if they diverge, generates a revised plan for the remaining work. This gives long-horizon robustness without paying for step-by-step ReAct reasoning on every action. The tradeoff is cost and potential thrash, so I bound the number of replans and treat "still failing after N replans" as an escalation signal. It's the difference between an agent that gives up on the first surprise and one that adapts.

**Q30: Why run agent-generated code in a sandbox, and what does the sandbox need?**

Any time an agent generates and executes code — the "code agent" pattern, which is often more token-efficient than chaining dozens of tool calls — you're executing untrusted, model-authored code, so it must run isolated from your infrastructure. The sandbox needs process/network isolation (containers or microVMs like Firecracker/gVisor), no ambient credentials, a filesystem scoped to the task, resource limits (CPU, memory, wall-clock) to stop runaway execution, and no egress unless explicitly allowed. The threat model isn't just bugs — it's prompt injection turning the code agent into a confused deputy. Kompass runs its code-execution path in a locked-down sandbox for exactly this reason; see [`../docs/06_advanced_patterns.md`](../docs/06_advanced_patterns.md).

**Q31: What is proactive autonomy and what's the risk?**

Most agents are reactive — they wait for a user message. A proactive agent is triggered by *events*: a new ticket lands, a metric crosses a threshold, a scheduled job fires, and the agent acts without a human initiating the turn. This is where a lot of ops value lives — triage a ticket the moment it arrives, draft a response before anyone reads it. The risk is that there's no human in the initiating loop, so the blast radius of a mistake is larger and it can act at machine speed on bad input; proactive agents therefore need *tighter* guardrails, mandatory HITL on any external action, and strong rate limits/circuit breakers. Proactive plus autonomous plus unmonitored is how you get an incident, so I make proactive agents draft-and-gate by default.

> **Interview soundbite:** "Proactive agents create value by acting on events instead of waiting to be asked — but with no human starting the turn, the blast radius grows. So proactive means *tighter* guardrails and draft-and-gate by default, not looser ones."

**Q32: How do you keep a plan-and-execute agent from executing a bad plan?**

Three levers. First, validate the plan before executing — a cheap check (or a human, for high-stakes tasks) that the steps are sane and within policy; a plan that proposes deleting production data should never reach execution. Second, execute with guards on each step — typed tool arguments, permission scoping, and HITL on risky actions — so even a flawed plan can't do damage. Third, replanning with a bounded budget so a wrong turn self-corrects instead of compounding. The framing I like: the plan is a hypothesis, execution is the experiment, and replanning plus guardrails are what keep a wrong hypothesis from becoming an incident.

---

## 8. Evaluation

**Q33: How do you build a golden set for an agentic system, and what goes in it?**

A golden set is a curated collection of representative inputs paired with expected outcomes, and for agents it must cover more than final answers: input, expected final response *and* the expected trajectory (which tools, in roughly what order), edge cases, adversarial/injection cases, and known past failures (regression cases). I seed it from real traffic and grow it every time production surfaces a new failure — that's the flywheel. Size matters less than coverage; 150 well-chosen cases beat 5,000 near-duplicates. Crucially I version it and treat it as code, because it's the contract every change is measured against. See the eval design in [`casos/caso_01_knowledge_assistant.md`](casos/caso_01_knowledge_assistant.md).

**Q34: Which RAGAS-style metrics do you use and what does each catch?**

Four core ones. Faithfulness: is the answer grounded in the retrieved context, or did the model make it up? — this catches hallucination. Answer relevance: does the answer actually address the question? — catches evasive or off-topic responses. Context precision: are the retrieved chunks relevant and well-ranked? — catches a noisy retriever. Context recall: did retrieval get all the info needed? — catches a retriever that missed the key chunk. The power is *diagnostic*: low faithfulness with high context recall points at generation; low context recall points at retrieval. Bundling them into one number throws away exactly the signal you need to fix things.

**Q35: LLM-as-judge — how do you make it trustworthy?**

LLM-as-judge scales evaluation of open-ended outputs where there's no single correct string, but a naive judge is noisy and biased (position bias, verbosity bias, self-preference). I make it trustworthy by: giving it a precise rubric with a discrete scale rather than "rate 1–10", using pairwise comparison where possible (more reliable than absolute scores), controlling for order, and — the non-negotiable — *validating the judge against human labels* on a sample so I know its agreement rate. A judge I haven't calibrated against humans is a random number generator with good grammar. I also keep a stronger model as judge than the one being judged, when cost allows.

> **Interview soundbite:** "An LLM judge you haven't calibrated against human labels is a random number generator with good grammar. Rubric, pairwise where possible, and a measured human-agreement rate — otherwise I don't trust the score."

**Q36: How do you measure task completion, and why isn't accuracy enough for agents?**

For a Q&A system, answer accuracy is fine; for an agent that *acts*, the question is "did it actually resolve the task end-to-end?" — which is a different, harder metric. I measure task-completion rate (did the refund get issued correctly, the ticket get routed, the record get updated?), plus trajectory quality (did it take a sane path or flail?), plus cost and latency per completed task, plus escalation rate. A support agent that answers eloquently but never resolves anything is a failure by the only metric that pays the bills. This is why Kompass's north-star metric is resolution rate, not answer quality — business value, not eloquence.

**Q37: What is tau-bench / user-simulator evaluation and CI gating?**

tau-bench-style evaluation uses an *LLM user simulator* to hold realistic multi-turn conversations with your agent against a set of tasks with programmatically checkable success criteria (was the DB left in the correct state?), which tests the whole loop — tool use, policy adherence, multi-turn coherence — not just single turns. I wire the golden set and a slice of these simulated tasks into CI as *gates*: a PR that drops task-completion below threshold, or regresses a known-failure case, or blows the cost budget, fails the build. This is what turns "we tested it once" into "every change is measured," and it's the single practice that most separates a demo from a production agent. The user-simulator harness for Kompass lives under [`../docs/06_advanced_patterns.md`](../docs/06_advanced_patterns.md) and the eval suite.

---

## 9. Guardrails / security / GDPR / prompt-injection

**Q38: What's your layered defense against prompt injection?**

There's no single fix, so it's defense-in-depth. Input side: treat all retrieved and tool-returned content as untrusted data, never as instructions — I delimit and label it clearly so the model knows "this is a document, not a command." Privilege side: the agent runs with least privilege and destructive tools require HITL, so even a successful injection can't move money without a human. Output side: validate and sanitize what the agent produces before it acts or reaches a user, and scan tool arguments for signs of hijack. Detection: log everything and flag anomalies. The mental model is the confused-deputy problem — assume injection *will* succeed sometimes and make sure it can't do irreversible damage; details in [`../docs/06_advanced_patterns.md`](../docs/06_advanced_patterns.md).

> **Interview soundbite:** "I assume prompt injection will eventually succeed. So the real defense is least privilege plus HITL on anything irreversible — a hijacked agent still can't issue a refund without a human. Everything retrieved is data, never instructions."

**Q39: How do you handle PII and GDPR in an agentic pipeline?**

GDPR isn't a feature you bolt on — it shapes the architecture. Data minimization: only pull the PII a task actually needs into context, and redact/pseudonymize where possible before it hits the LLM. Purpose limitation and consent: memory writes are governed, with a lawful basis and retention TTLs. Right to erasure: because I keep long-term memory in a separate store keyed by user, deletion is a real operation I can execute — you can't delete a user from a model's weights, which is exactly why you don't put PII there. Data residency: for European clients I keep processing in-region and prefer providers/models with EU data guarantees. And everything is logged for the accountability principle. This matters concretely for the German/EU roles I'm targeting.

**Q40: What are guardrails, concretely, and where do they sit in the graph?**

Guardrails are the deterministic checks around the nondeterministic model — they're code, not vibes. Input guardrails: block off-topic, unsafe, or injection-flavored inputs before they cost tokens. Output guardrails: enforce that responses meet format, safety, and grounding requirements before they leave. Action guardrails: permission scoping, HITL approval, and typed argument validation on tools. In LangGraph these are explicit nodes/middleware at the edges of the graph — an input-validation node, an output-validation node, and the `interrupt_on` middleware for actions — so they're testable in isolation and can't be skipped. The point is that guardrails are how you keep an inherently probabilistic system inside a deterministic safety envelope.

**Q41: Why do typed/structured outputs improve safety and reliability?**

When I constrain the model to emit a typed schema (Pydantic model, JSON schema) instead of free text, three good things happen: parsing can't silently fail, invalid outputs are rejected at the boundary rather than propagating, and downstream code gets a guaranteed shape to rely on. For tool calls this is a security control too — a typed argument schema means a hallucinated or injected argument fails validation instead of executing. It also makes the system testable, because "did it return valid structured output?" is a hard assertion, not a fuzzy judgment. I use structured outputs everywhere a machine consumes the model's result; free text is only for the final human-facing message.

**Q42: How do you scope permissions so an agent has least privilege?**

Every tool carries an explicit capability declaration — what it can read, what it can write, whether it's destructive — and the agent is granted only the tools its role needs, enforced at the MCP-server/tool boundary rather than trusting the prompt. Credentials live in the tool layer, never in the agent's context, so a leaked or injected prompt can't exfiltrate secrets. Destructive capabilities are additionally gated behind HITL. And I scope per-*session* where it matters — a support agent handling customer A shouldn't be able to touch customer B's data. The principle is that the guardrail lives in code at the boundary, because anything enforced only in the system prompt is one clever injection away from being bypassed.

---

## 10. Cost / latency / production

**Q43: How does prompt caching change your architecture and cost?**

Prompt caching stores the KV representation of a stable prefix so repeated calls that share it skip recomputation — with providers charging a fraction of the input price for cache hits. Architecturally this rewards putting the *stable* content first (system prompt, tool definitions, few-shots, and in CAG the whole corpus) and the *variable* content (the user turn) last, so the long prefix is cached across requests. It's what makes CAG economically viable and cuts both latency and cost dramatically on multi-turn or high-volume workloads. The discipline is prompt *stability* — reorder your prompt so the cache actually hits, and don't invalidate the prefix with per-request timestamps. This is one of the highest-leverage, lowest-effort production wins.

> **Interview soundbite:** "Prompt caching pays you to structure prompts stable-part-first. Put the system prompt, tools, and corpus up front and the user turn last — the cached prefix hits every call, and cost and latency drop hard."

**Q44: How do you route between models to control cost without tanking quality?**

Not every step needs the frontier model. I use a cheap, fast model for the easy majority — routing, classification, simple extraction, short factual answers — and escalate to a stronger model only for hard reasoning, planning, or synthesis. The router itself is a small model or a heuristic. In a plan-and-execute agent this maps cleanly: strong model makes the plan, cheap model executes the steps. The guardrail is that model routing is a quality decision, so I evaluate each route on the golden set — a cheap model that misroutes costs more than it saves. Done well this cuts cost 5–10x on realistic traffic while keeping tail quality intact.

**Q45: Why does streaming matter for agents, and what do you stream?**

Streaming is a UX and trust lever: agents are slow (multi-step, multi-tool), and a user staring at a spinner for 20 seconds assumes it's broken. I stream tokens for the final answer, but for agents the more important thing is streaming *intermediate state* — "searching the knowledge base…", "found 3 matching tickets…", "drafting a refund for approval…" — so the user sees progress and reasoning. LangGraph supports streaming events at the node and token level, which lets me surface the trajectory live. Beyond UX, streaming intermediate steps is also observability the user can see, which builds trust in an autonomous system. For HITL, streaming up to the interrupt makes the pause feel intentional rather than hung.

**Q46: How do you set token budgets and stop cost blowups?**

Budgets are enforced, not hoped for. Per-request I set a max token and max step budget that trips a circuit breaker — the graph's recursion limit is the backstop against infinite loops. I trim and summarize context aggressively so the working set doesn't grow unbounded across a long conversation (context management is cost management). I cache (prompt caching, retrieval caching) and route to cheaper models per the previous answer. And I *instrument* cost per request and per completed task, alert on anomalies, and gate it in CI so a prompt change that doubles cost is caught before production. The recurring theme: an agent's cost is a distribution with a nasty tail, so you manage the tail, not the average.

**Q47: What breaks when you scale an agent from demo to production traffic?**

Several things at once. Concurrency: long-running, checkpointed, HITL-paused requests mean you're holding a lot of in-flight state, so the checkpointer (Postgres/Redis) and its connection pooling become the bottleneck, not the LLM. Rate limits: provider TPM/RPM limits bite under load, so you need queuing, backoff, and often multi-provider fallback. Cost variance: the tail-latency and tail-cost requests dominate your bill and your p99. Observability: without full tracing (LangSmith/OpenTelemetry) you can't debug nondeterministic failures at scale. And statefulness makes horizontal scaling non-trivial — sessions must resume on any worker, which is exactly why state lives in an external checkpointer, not in process memory.

---

## 11. Failure modes

**Q48: An agent gets stuck in a loop — diagnose and fix.**

First, the immediate containment already exists if I built it right: a recursion/step limit and token budget halt the loop before it burns the account. Diagnosis: pull the trace and look at *why* — usually it's one of a few patterns. The agent keeps calling the same tool with the same args (it's not getting the info it needs, so give it a different tool or an escalation path), or it oscillates between two states (the state design lets it "forget" progress — fix the reducers/state), or a tool keeps erroring and the agent retries blindly (add error handling that changes strategy after N failures). The durable fix is loop-detection plus a "not converging → escalate to human" branch, so a stuck agent degrades gracefully instead of spinning. Covered in [`../docs/06_advanced_patterns.md`](../docs/06_advanced_patterns.md).

**Q49: What is context rot and how do you fight it?**

Context rot is the degradation of model performance as the context window fills with accumulated history, tool outputs, and retrieved chunks — attention gets diluted, the model loses the thread, and quality drops even below the hard token limit. It's insidious because the system keeps *working*, just worse, so it's easy to miss. I fight it by managing context as a first-class resource: summarize/compact old turns, keep only the top-k reranked chunks rather than everything retrieved, separate durable structured state from the raw transcript, and scope what each sub-agent sees. The principle is that context is a scarce budget to *curate*, not a bucket to *fill* — more context is not more intelligence past a point.

> **Interview soundbite:** "Context rot is death by accumulation — the agent keeps working, just steadily worse, as the window fills. Context is a budget to curate, not a bucket to fill; more tokens past a point is less intelligence, not more."

**Q50: How do you handle hallucinated tool calls?**

A hallucinated tool call is the model inventing a tool that doesn't exist, or real tool with malformed/fabricated arguments. The first defense is structural: constrain tool calls to the actual registered schema so a nonexistent tool simply can't be emitted, and validate arguments against a typed schema so fabricated ones fail closed rather than executing. When a call fails validation, I feed the error back to the model as an observation so it can self-correct — often it fixes itself on the next turn. For arguments that pass schema but might still be wrong (a plausible-looking but incorrect ID), the defense is idempotency and HITL on anything consequential. The point is to make hallucinated calls *inert* by construction, not to hope the model doesn't hallucinate.

**Q51: What are cascading errors and how do you contain them?**

Cascading errors are when one bad output becomes the input to the next step and the mistake compounds — a wrong retrieval leads to a wrong plan leads to a wrong action — which is the multi-step agent's version of garbage-in-garbage-out, amplified. Containment is about breaking the chain: validation gates between steps (typed outputs, guardrail nodes) so a bad intermediate is caught before it propagates, replanning checkpoints so the agent notices divergence from reality, and HITL on the final consequential action as the last line of defense. I also design for *graceful degradation* — when confidence is low or checks fail, escalate to a human rather than pushing forward on a shaky chain. The systemic lesson is that in an agent, errors don't just occur, they *travel*, so you put firebreaks between steps.

---

## 12. Framework comparison

**Q52: LangGraph vs. PydanticAI vs. OpenAI Agents SDK vs. CrewAI vs. Temporal — pick by criterion.**

They optimize for different things, so the right answer is "depends on the requirement," and naming the criterion is what makes you sound senior.

| Framework | Core strength | Reach for it when | Weakness / limit |
|---|---|---|---|
| **LangGraph v1.0** | Explicit graph + state + checkpointing + declarative HITL | You need control, durability, HITL, and observability on a stateful multi-step agent | Steeper learning curve; you build more yourself |
| **PydanticAI** | Type-safe, Pythonic, structured-output-first | You want a clean, typed single-agent with great validation ergonomics | Less built-in for complex multi-agent orchestration/durability |
| **OpenAI Agents SDK** | Lightweight, fast to stand up, handoffs + guardrails built in | Quick agents, OpenAI-centric stacks, prototypes | Less graph-level control and portability; provider gravity |
| **CrewAI** | High-level role/crew abstractions, fast multi-agent demos | Rapid prototyping of role-playing multi-agent teams | Abstraction hides control; harder to production-harden/debug |
| **Temporal** | Battle-tested durable execution, retries, sagas | The surrounding business process needs enterprise-grade durability across services | Not an agent framework — it orchestrates, you bring the agent |

For Kompass I chose LangGraph v1.0 because the project's whole point is *acting* on the business with control, durability, and human oversight — its explicit state machine, checkpoint-based durability, and the new declarative `interrupt_on` HITL middleware map directly onto those requirements. The full decision, including why not the alternatives, is in [`../docs/03_framework_decision.md`](../docs/03_framework_decision.md).

> **Interview soundbite:** "I don't have a favorite framework, I have criteria. LangGraph when I need control, state, and durable HITL; PydanticAI for typed single agents; OpenAI SDK for quick OpenAI-native builds; CrewAI for demos; Temporal underneath when the business process itself must be durable. Kompass is LangGraph because it acts on the business."

**Q53: Isn't LangGraph just LangChain with extra steps? Defend the choice.**

No — and this is a question I want, because it lets me show I understand the layers. LangChain is the component library (model wrappers, retrievers, tool integrations); LangGraph is a separate low-level orchestration framework for building stateful, controllable agents as explicit graphs, and it's what reached v1.0 as production-grade in October 2025. I use LangChain components *inside* LangGraph nodes, but the control flow, state, checkpointing, and HITL are LangGraph's job. The reason I didn't just chain LangChain calls is precisely that agents need durable state, resumability, and human-in-the-loop — which is orchestration, not components. That layering (components vs. orchestration) is the crux of [`../docs/03_framework_decision.md`](../docs/03_framework_decision.md).

**Q54: When would you deliberately choose *no* framework and go raw API?**

When the task is simple enough that a framework is pure overhead — a single classification call, a one-shot extraction, a fixed two-step pipeline — reaching for LangGraph adds dependencies and cognitive load for zero benefit. Raw API calls plus a few typed Pydantic models are the right tool for a large share of "LLM feature" work that isn't actually agentic. I also go closer to the metal when I need maximum control over caching, streaming, and token budgeting and the framework abstractions get in the way. The senior move is matching the tool to the autonomy rung from Q3: frameworks earn their weight at rungs 4–5, not rungs 1–2. Choosing *not* to add a framework is as much an engineering decision as choosing one.

**Q55: How does the two-layer interop story (MCP + A2A) influence framework choice?**

Because MCP and A2A are open standards, they *reduce* framework lock-in, which is itself a selection criterion. If my tools are MCP servers and my agents federate over A2A, then the orchestration framework becomes a swappable implementation detail rather than a decade-long commitment — I can run LangGraph today and the tools/agents don't care. So I weight frameworks partly on how well they speak these standards: first-class MCP client support and A2A interop mean I'm buying into a protocol, not a walled garden. This is the same instinct that made MCP valuable at VW/CARIAD — standards decouple teams and let each layer evolve independently. When two frameworks are otherwise close, the one with better open-standard support wins on future-proofing.

> **Interview soundbite:** "MCP and A2A being open standards means my framework choice is reversible — tools and agent-to-agent contracts outlive any one orchestrator. So I weight first-class MCP/A2A support heavily: I'd rather buy into a protocol than a walled garden."

---

## Related

- Theory foundations: [`../docs/01_agentic_ai_deep_dive.md`](../docs/01_agentic_ai_deep_dive.md) · [`../docs/02_retrieval_strategies.md`](../docs/02_retrieval_strategies.md) · [`../docs/03_framework_decision.md`](../docs/03_framework_decision.md) · [`../docs/04_hitl_patterns.md`](../docs/04_hitl_patterns.md) · [`../docs/05_architecture.md`](../docs/05_architecture.md) · [`../docs/06_advanced_patterns.md`](../docs/06_advanced_patterns.md)
- Answering framework for open-ended design questions: [`framework_PACTEDR.md`](framework_PACTEDR.md)
- Worked cases to rehearse out loud: [`casos/caso_01_knowledge_assistant.md`](casos/caso_01_knowledge_assistant.md) · [`casos/caso_02_customer_support.md`](casos/caso_02_customer_support.md) · [`casos/caso_03_document_processing.md`](casos/caso_03_document_processing.md) · [`casos/caso_04_nl2sql_analyst.md`](casos/caso_04_nl2sql_analyst.md)
