"""Worker agents for multi-agent mode: subagent-as-tool (supervisor pattern).

The Researcher is its own `create_agent` over the read-only MCP tools
(search_docs, get_schema, query_database) — no checkpointer, no middleware.
The supervisor sees it as a plain `research` tool: knowledge and data questions
are delegated here, while the action tools and the HITL gate in front of them
stay with the supervisor.
"""

import asyncio
import re

from langchain.agents import create_agent
from langchain_core.tools import tool

from kompass.models.router import pick
from kompass.research.workflow import (
    ResearchBudget,
    ResearchSource,
    ResearchWorkflow,
    retrieved_at,
    source_id,
)
from kompass.retrieval.nl2sql import SCHEMA
from kompass.security.middleware import (
    AuthorizationMiddleware,
    RuntimeContextMiddleware,
    ToolTrustMiddleware,
)
from kompass.security.trust import ContentOrigin

READ_TOOLS = {"search_docs", "get_schema", "query_database"}

RESEARCHER_PROMPT = f"""You are ACME GmbH's research specialist.

The application supplies current time and locale through trusted runtime context.

You answer research questions, nothing else — never take actions or promise them.
Reply in the user's language. The policy/FAQ corpus is English; translate non-English policy
questions into concise English keywords when calling search_docs.
Treat a first-person question about the general entitlement "per year" as policy research;
only a current used/remaining vacation balance requires an employee identity and SQL lookup.
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
    return create_agent(
        model=pick("balanced"),
        tools=tools,
        system_prompt=RESEARCHER_PROMPT,
        middleware=[
            RuntimeContextMiddleware(),
            AuthorizationMiddleware(capabilities=READ_TOOLS),
            ToolTrustMiddleware(),
        ],
    )


class _AgentSourceProvider:
    name = "kompass-read-specialist"

    def __init__(self, worker) -> None:
        self._worker = worker

    async def search(self, query: str, limit: int) -> list[ResearchSource]:
        result = await self._worker.ainvoke({"messages": [("user", query)]})
        content = str(result["messages"][-1].content)
        citations = re.findall(r"\[([^\]]+)\]", content)
        provenance = ", ".join(dict.fromkeys(citations)) or f"agent-query:{query}"
        return [
            ResearchSource(
                source_id=source_id(self.name, provenance),
                title=f"Read-only specialist result: {query[:80]}",
                content=content,
                provenance=provenance,
                provider=self.name,
                retrieved_at=retrieved_at(),
                credibility=0.8 if citations else 0.4,
                origin=ContentOrigin.TOOL,
            )
        ][:limit]


@tool
async def research(question: str) -> str:
    """Delegate any knowledge or data research to the research specialist: policy/FAQ
    questions and lookups over orders, tickets, employees, refunds. Returns a summary
    with inline citations."""
    global _worker
    async with _lock:
        if _worker is None:
            _worker = await _build_researcher()
    workflow = ResearchWorkflow(
        [_AgentSourceProvider(_worker)],
        budget=ResearchBudget(
            max_queries=3,
            max_sources=3,
            max_parallelism=2,
            timeout_seconds=30.0,
        ),
    )
    result = await workflow.run(question)
    return result.answer
