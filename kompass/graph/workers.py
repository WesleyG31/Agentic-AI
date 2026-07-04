"""Worker agents for multi-agent mode: subagent-as-tool (supervisor pattern).

The Researcher is its own `create_agent` over the read-only MCP tools
(search_docs, get_schema, query_database) — no checkpointer, no middleware.
The supervisor sees it as a plain `research` tool: knowledge and data questions
are delegated here, while the action tools and the HITL gate in front of them
stay with the supervisor.
"""

import asyncio

from langchain.agents import create_agent
from langchain_core.tools import tool

from kompass.models.router import pick
from kompass.retrieval.nl2sql import SCHEMA

READ_TOOLS = {"search_docs", "get_schema", "query_database"}

RESEARCHER_PROMPT = f"""You are ACME GmbH's research specialist. Today is 2026-07-04.

You answer research questions, nothing else — never take actions or promise them.
search_docs answers policy/FAQ questions. query_database answers questions about orders,
order_items, tickets, employees, refunds — one SELECT per call, schema:
{SCHEMA}

Cite every claim inline exactly as the tool results provide it, e.g.
[policies/refund_policy.md § Damaged or Defective Items] for documents, or the SQL you ran.
Keep answers under 200 words."""

_worker = None
_lock = asyncio.Lock()


async def _build_researcher():
    from kompass.graph.agent import mcp_client  # here to avoid a circular import

    tools = [t for t in await mcp_client().get_tools() if t.name in READ_TOOLS]
    return create_agent(model=pick("balanced"), tools=tools, system_prompt=RESEARCHER_PROMPT)


@tool
async def research(question: str) -> str:
    """Delegate any knowledge or data research to the research specialist: policy/FAQ
    questions and lookups over orders, tickets, employees, refunds. Returns a summary
    with inline citations."""
    global _worker
    async with _lock:
        if _worker is None:
            _worker = await _build_researcher()
    result = await _worker.ainvoke({"messages": [("user", question)]})
    return result["messages"][-1].content
