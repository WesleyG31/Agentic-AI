"""The Kompass agent: LangGraph v1 `create_agent` over MCP tools with a durable HITL gate.

Reads are free (search_docs, query_database, get_ticket); writes (create_refund,
update_ticket) pause at the HumanInTheLoop middleware for an approve/edit/reject
decision. State is checkpointed in SQLite, so a paused run survives restarts and
can be resumed by any surface (demo script, API, UI) via the same thread_id.
"""

import sys

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_mcp_adapters.client import MultiServerMCPClient

from kompass.config import ROOT
from kompass.graph.critic import GroundingCritic
from kompass.memory.store import recall_memories, save_memory
from kompass.models.router import pick
from kompass.retrieval.nl2sql import SCHEMA

SYSTEM_PROMPT = f"""You are Kompass, ACME GmbH's support & operations assistant. \
Today is 2026-07-04.

You resolve requests end-to-end: answer questions about policies and operational data, and
execute actions (refunds, ticket updates) when justified.

Rules:
- Ground every factual claim in tool results. Cite sources inline exactly as returned, e.g.
  [policies/refund_policy.md § Damaged or Defective Items] for documents, or the SQL you ran.
- search_docs answers policy/FAQ questions. query_database answers questions about orders,
  order_items, tickets, employees, refunds — one SELECT per call, schema:
{SCHEMA}
- Before any action, verify the facts: fetch the order/ticket, check the relevant policy
  (return window, refund conditions), and only then call the action tool.
- create_refund and update_ticket are gated: calling them pauses the run and a human
  reviewer approves, edits, or rejects the call before it executes. That gate IS the
  confirmation step — never ask the user for confirmation in chat; call the tool directly
  once the facts check out.
- Refunds over €500 require supervisor approval — state this in the refund reason.
- Memory: when a user identifies themselves, recall_memories for them; save_memory when
  they state a durable preference or standing instruction worth keeping across conversations.
- If a request cannot be resolved with your tools, say what is missing and escalate;
  never invent data or promise actions you cannot perform."""

INTERRUPT_ON = {
    "create_refund": {"allowed_decisions": ["approve", "edit", "reject"]},
    "update_ticket": {"allowed_decisions": ["approve", "reject"]},
}


def _server(module: str) -> dict:
    return {
        "command": sys.executable,
        "args": ["-m", module],
        "transport": "stdio",
        "cwd": str(ROOT),
    }


def mcp_client() -> MultiServerMCPClient:
    """Client for Kompass's three MCP servers, spawned as stdio subprocesses."""
    return MultiServerMCPClient(
        {
            "doc_search": _server("kompass.mcp_servers.doc_search"),
            "acme_sql": _server("kompass.mcp_servers.sql"),
            "ticketing": _server("kompass.mcp_servers.ticketing"),
        }
    )


async def build_agent(checkpointer):
    """Assemble the agent: balanced model + MCP and memory tools + critic + HITL gate."""
    tools = await mcp_client().get_tools() + [save_memory, recall_memories]
    return create_agent(
        model=pick("balanced"),
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        middleware=[
            GroundingCritic(),
            HumanInTheLoopMiddleware(interrupt_on=INTERRUPT_ON),
        ],
        checkpointer=checkpointer,
    )
