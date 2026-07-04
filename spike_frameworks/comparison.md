# Framework Comparison Spike — LangGraph v1 vs. PydanticAI

> **Status: completed 2026-07-04.** The Researcher agent was rebuilt in **PydanticAI 2.5**
> ([`researcher_pydantic_ai.py`](researcher_pydantic_ai.py)) and both implementations ran live
> against the same golden items ([`run_parity.py`](run_parity.py), raw scores in
> [`parity_results.json`](parity_results.json)). The interview line this earns:
> *"I built the same agent in two frameworks — here is the state / HITL / cost / DX trade-off."*

## Goal

Kompass's primary framework decision is **LangGraph v1** — see
[`../docs/03_framework_decision.md`](../docs/03_framework_decision.md). This spike does **not**
change that decision; it demonstrates judgment by rebuilding one bounded component
(the Researcher: retrieve → synthesize with mandatory citations) in an alternative and
comparing honestly.

## The identical task spec

Both implementations satisfy the same contract:

- **Prompt:** ACME research specialist — research only, never act, cite every claim inline,
  under 200 words. Same text in both.
- **Tools:** `search_docs` (hybrid RAG over `kompass.retrieval.rag.search`, chunks prefixed
  with their citation tags) and `query_database` (read-only SELECT via
  `kompass.retrieval.nl2sql.run_sql`). Same functions, same output formatting.
- **Model:** `openai:gpt-5.4` (Kompass's "balanced" tier) in both.
- **Eval:** golden items rag-01..05 + sql-01..02, scored with the shared LLM judge
  (`evals/judge.py`) plus the deterministic fact/citation checks from `evals/run.py`.

The one structural difference: the LangGraph researcher (`kompass/graph/workers.py`) consumes
the tools **over MCP** (stdio subprocess per call), as it does in production Kompass; the
PydanticAI researcher registers the same Python functions **in-process**. That difference is
visible in the latency numbers and called out below.

## Parity run (live, 2026-07-04)

| Item | LangGraph correct / cited | PydanticAI correct / cited | LG latency | PAI latency | LG tokens | PAI tokens |
|---|---|---|---|---|---|---|
| rag-01 | yes / yes | yes / yes | 11.1s | 5.0s | 1,745 | 1,517 |
| rag-02 | yes / yes | yes / yes | 5.3s | 3.1s | 1,582 | 1,316 |
| rag-03 | yes / yes | yes / yes | 6.3s | 3.9s | 1,704 | 1,422 |
| rag-04 | yes / yes | yes / yes | 6.1s | 3.7s | 1,717 | 1,375 |
| rag-05 | yes / yes | yes / yes | 6.4s | 4.3s | 1,872 | 1,464 |
| sql-01 | yes / yes | yes / yes | 4.3s | 2.3s | 1,243 | 1,023 |
| sql-02 | yes / yes | yes / yes | 5.8s | 2.6s | 1,169 | 957 |
| **aggregate** | **7/7 correct, 7/7 cited** | **7/7 correct, 7/7 cited** | **6.5s mean** | **3.6s mean** | **1,576 mean** | **1,296 mean** |

Both systems also scored **100% grounded** by the judge. Mean LLM cost per item (gpt-5.4 list
prices): **$0.0060 (LangGraph)** vs **$0.0052 (PydanticAI)**.

**Read the deltas honestly:**

- **Quality parity is total.** Same model, same tools, same prompt → same answers, all cited.
  The framework does not make the agent smarter; it decides what happens *around* the calls.
- **The ~2× latency gap is mostly Kompass's MCP transport, not LangGraph.** Each LangGraph tool
  call round-trips through a spawned stdio subprocess; the PydanticAI spike calls the same
  functions in-process. Wiring LangGraph tools in-process would close most of that gap.
- **The ~20% token overhead is real but small.** The LangGraph researcher carries a third tool
  (`get_schema`) plus slightly heavier tool-calling scaffolding; per item that is ~280 tokens,
  ~$0.0008.

## Trade-off table (observations from building both)

| Axis | LangGraph v1 | PydanticAI 2.5 |
|---|---|---|
| State / persistence | **Native checkpointer** (SQLite here, Postgres in prod): a run survives restarts and resumes by `thread_id`. Kompass's eval harness replays paused runs this way. | **Stateless by default.** `result.all_messages_json()` hands you the history; persisting and reloading it is your code. Fine for request/response services, DIY for anything durable. |
| HITL ergonomics | **Declarative and durable**: `HumanInTheLoopMiddleware(interrupt_on=...)` + checkpointer gives approve/edit/reject pauses that survive a redeploy — Kompass's core product feature, ~5 lines. | Has the *hook* but not the *durability*: `requires_approval=True` / `ApprovalRequiredToolset` ends the run with `DeferredToolRequests`; you resume with `DeferredToolResults` + the saved history. The approval contract exists; keeping the pause alive across processes is on you. |
| Type-safety / validated outputs | Good — structured output via schema binding; but agent state is a message dict, and the final answer is `state["messages"][-1].content`. | **The core value prop.** `Agent[Deps, Output]` generics, `output_type=` any Pydantic model, validation failures auto-retry against the model. Tool schemas come straight from function signatures + docstrings. |
| Multi-agent orchestration | First-class: Kompass's multi mode wraps this same researcher as a supervisor's `research` tool; graphs, cycles, subgraphs are native. | Manual: an agent can call another agent inside a tool (delegation works, usage even aggregates), but there is no graph runtime, no cycles, no shared checkpointed state. |
| Streaming | Graph-level `astream` with modes (updates / messages / values) — streams across nodes and sub-agents. | `run_stream` with **partially-validated structured output** — you can stream a typed object as it fills in. Nicer for single-agent APIs; no cross-agent story. |
| Cost / token overhead | 1,576 tokens, $0.0060, 6.5s per item (through MCP). | 1,296 tokens, $0.0052, 3.6s per item (in-process tools). ~20% token savings intrinsic; most of the latency gap is transport, see above. |
| Developer experience | 53 lines (`kompass/graph/workers.py`) — but they lean on MCP servers for tools and an async build-and-cache dance for the client. More moving parts, more places to look. | 62 lines **fully self-contained** (prompt + both tool bodies + agent + entrypoint). `Agent(model, instructions, tools=[fn, fn])` and you are running. FastAPI-grade ergonomics; the fastest path to a working, testable agent I have used. |
| Dependency footprint | 63 packages in the runtime stack's transitive closure (langgraph, langchain, langchain-core, langchain-openai, checkpoint-sqlite, mcp-adapters). | 20 packages for `pydantic-ai-slim` (+ the OpenAI SDK via the `[openai]` extra, which Kompass already ships). Roughly a third of the surface. |
| Best fit | Stateful, cyclic, multi-agent systems where a **pause is a product feature**: approvals, audits, long-lived threads. | **Typed single-agent services**: request in, validated object out, minimal ceremony, minimal deps. |

## Conclusion — when I'd reach for each

**PydanticAI** is what I would pick for a *typed single-agent service*: an extraction endpoint,
a research/summarize API, an LLM step inside an existing FastAPI app. It was genuinely faster to
build — one self-contained file, plain functions as tools, a validated result object back — and
it matched the LangGraph researcher fact-for-fact and citation-for-citation at ~85% of the cost.
For that unit of work, LangGraph's graph/runtime machinery is ceremony you do not need.

**LangGraph v1 remains Kompass's primary**, and the reason is everything the parity table cannot
show: Kompass's defining feature is a refund that *pauses*, waits hours for a human decision,
survives a redeploy, and resumes — that is the checkpointer + `HumanInTheLoopMiddleware`
combination, which PydanticAI's deferred-tools hook only approximates if you build the
persistence layer yourself. The same applies to the supervisor pattern: Kompass's multi-agent
mode drops this exact researcher behind a `research` tool with no new plumbing. Durable HITL,
checkpointing, and graph orchestration are the axes Kompass lives on, and they are exactly the
axes where the two frameworks are not peers.

They compose rather than compete: the honest architecture note is that a PydanticAI agent could
serve *as a node inside* a LangGraph graph — typed leaf work in PydanticAI, orchestration and
durability in LangGraph.

## Reproducing

```bash
python -m spike_frameworks.run_parity   # live run; writes parity_results.json
```

## Related
- [`../docs/03_framework_decision.md`](../docs/03_framework_decision.md) — the primary decision + sources.
- [`../docs/05_architecture.md`](../docs/05_architecture.md) — where the Researcher sits in the graph.
