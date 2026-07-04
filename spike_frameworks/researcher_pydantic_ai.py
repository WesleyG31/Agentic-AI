"""The Kompass Researcher rebuilt in PydanticAI — the framework comparison spike.

Same task spec as the LangGraph researcher (kompass/graph/workers.py): answer
policy and data questions over ACME's docs and database, cite every claim
inline, never take actions. The two tools wrap the exact same retrieval
functions the MCP servers expose — same citation tags, same row formatting —
so any eval delta comes from the framework, not the tooling.

What PydanticAI gives for free: the agent loop (model → tool calls → model →
final answer), tool schemas derived from plain function signatures and
docstrings, output validation with automatic retries, and per-run usage
accounting via ``result.usage()`` — no callback handler needed.

What you wire manually (LangGraph ships these): persistence — a run lives in
memory only, there is no checkpointer to resume from after a crash or a human
pause; HITL — no interrupt primitive or approval middleware, an approval gate
is code you write around ``agent.run``; and multi-agent orchestration — a
supervisor/worker pattern would be hand-composed from separate agents.
"""

from pydantic_ai import Agent

from kompass.retrieval import rag
from kompass.retrieval.nl2sql import SCHEMA, run_sql

# Same prompt as kompass/graph/workers.py, restated here so the spike stands alone.
RESEARCHER_PROMPT = f"""You are ACME GmbH's research specialist. Today is 2026-07-04.

You answer research questions, nothing else — never take actions or promise them.
search_docs answers policy/FAQ questions. query_database answers questions about orders,
order_items, tickets, employees, refunds — one SELECT per call, schema:
{SCHEMA}

Cite every claim inline exactly as the tool results provide it, e.g.
[policies/refund_policy.md § Damaged or Defective Items] for documents, or the SQL you ran.
Keep answers under 200 words."""


def search_docs(query: str) -> str:
    """Search ACME's policies and FAQs (hybrid semantic + keyword). Returns the top
    matching sections, each prefixed with its citation tag — cite these in answers."""
    return "\n\n".join(f"{c.citation}\n{c.text}" for c in rag.search(query))


def query_database(sql: str) -> str:
    """Run ONE read-only SELECT against the ACME database (orders, order_items,
    tickets, employees, refunds). Returns rows as a list of dicts, capped at 50."""
    rows = run_sql(sql)
    return f"{len(rows)} row(s): {rows}"


researcher = Agent(
    "openai:gpt-5.4",  # same model as the LangGraph researcher's "balanced" tier
    instructions=RESEARCHER_PROMPT,
    tools=[search_docs, query_database],
)


async def answer(question: str) -> str:
    """Run the researcher once and return its cited answer."""
    result = await researcher.run(question)
    return result.output
