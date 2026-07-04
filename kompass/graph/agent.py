"""The Kompass agent: LangGraph v1 `create_agent` over MCP tools with a durable HITL gate.

Reads are free (search_docs, query_database, get_ticket); writes (create_refund,
update_ticket) pause at the HumanInTheLoop middleware for an approve/edit/reject
decision. State is checkpointed in SQLite, so a paused run survives restarts and
can be resumed by any surface (demo script, API, UI) via the same thread_id.

In "multi" mode the agent becomes a supervisor: reads are delegated to the
Researcher worker (kompass/graph/workers.py) via the `research` tool, while the
write tools — and the HITL gate — stay here.
"""

import sys

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware, TodoListMiddleware
from langchain_mcp_adapters.client import MultiServerMCPClient

from kompass.config import ROOT, settings
from kompass.graph.critic import GroundingCritic
from kompass.graph.workers import research
from kompass.memory.store import recall_memories, save_memory
from kompass.models.router import pick
from kompass.retrieval.nl2sql import SCHEMA

SYSTEM_PROMPT = """You are Kompass, ACME GmbH's support & operations assistant. \
Today is 2026-07-04.

You resolve requests end-to-end: answer questions about policies and operational data, and
execute actions (refunds, ticket updates) when justified.

Rules:
- Ground every factual claim in tool results. Cite sources inline exactly as returned, e.g.
  [policies/refund_policy.md § Damaged or Defective Items] for documents, or the SQL you ran.
{research_rule}
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

RESEARCH_RULES = {
    "single": f"""\
- search_docs answers policy/FAQ questions. query_database answers questions about orders,
  order_items, tickets, employees, refunds — one SELECT per call, schema:
{SCHEMA}""",
    "multi": "- Delegate all policy/data research to the research tool; act only on evidence "
    "it returns.",
}

PLANNING_PROMPT = """## Planning with `write_todos`

Any request that takes several steps — combining data lookups, policy checks and/or actions —
is plan-and-execute: before acting, call `write_todos` with a short numbered plan (first step
in_progress), then work through it, updating statuses as steps land. If a step fails or the
evidence contradicts the plan, revise the todo list before continuing. Skip planning for
single-lookup or purely conversational requests. Deliver the final answer as a normal message
after the last `write_todos` call — the todo list tracks work, it is not the answer."""

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


async def build_agent(checkpointer, mode: str | None = None):
    """Assemble the agent: balanced model + MCP and memory tools + critic + HITL gate.

    mode "single" (default): all tools wired directly. mode "multi": reads go through
    the `research` worker; only the ticketing tools stay here, behind the HITL gate."""
    mode = mode or settings.agent_mode
    if mode == "multi":
        tools = [research, *await mcp_client().get_tools(server_name="ticketing")]
    else:
        tools = await mcp_client().get_tools()
    return create_agent(
        model=pick("balanced"),
        tools=tools + [save_memory, recall_memories],
        system_prompt=SYSTEM_PROMPT.format(research_rule=RESEARCH_RULES[mode]),
        middleware=[
            # Plan-and-execute: write_todos lets the model lay out a numbered plan
            # for multi-step requests and revise it as steps land or fail; the plan
            # lives in graph state ("todos"), so every surface can render progress.
            # The default prompt is too reluctant for support work, hence the override.
            TodoListMiddleware(system_prompt=PLANNING_PROMPT),
            GroundingCritic(),
            HumanInTheLoopMiddleware(interrupt_on=INTERRUPT_ON),
        ],
        checkpointer=checkpointer,
    )
