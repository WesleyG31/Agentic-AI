"""End-to-end demo of journey B: a refund request that pauses for human approval.

Flow: user asks to refund damaged order 4471 -> the agent verifies the order and
the refund policy via MCP tools -> drafts create_refund -> the HITL middleware
pauses the run -> this script plays the reviewer and approves -> the refund is
executed and verified in the database.

Run:  python -m kompass.scripts.demo   (requires `make seed` first and OPENAI_API_KEY)
"""

import asyncio
import sys
from uuid import uuid4

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from kompass.config import ROOT, settings
from kompass.graph.agent import build_agent
from kompass.retrieval.nl2sql import run_sql
from kompass.scripts.seed import build_db

USER_MSG = (
    "Hi, I'm Lena Fischer (lena.fischer@web.de). My order 4471 arrived damaged - the "
    "microphone housing is cracked and the monitor arm box was crushed. I reported it "
    "in ticket 88012. Please refund the order and update my ticket."
)

RULE = "=" * 72


def show_interrupt(interrupt) -> int:
    """Print the approval card(s) the way the UI would render them. Returns request count."""
    requests = interrupt.value["action_requests"]
    for req in requests:
        print(RULE)
        print("APPROVAL REQUIRED  (approve / edit / reject)")
        print(f"  tool: {req['name']}")
        for arg, val in req["args"].items():
            print(f"  {arg}: {val}")
        print(RULE)
    return len(requests)


async def main() -> int:
    build_db()  # reset demo data so reruns start clean
    print(f"user> {USER_MSG}\n")

    async with AsyncSqliteSaver.from_conn_string(
        str(ROOT / settings.sqlite_checkpoint)
    ) as saver:
        agent = await build_agent(saver)
        config = {"configurable": {"thread_id": f"demo-{uuid4().hex[:8]}"}}

        state = await agent.ainvoke({"messages": [("user", USER_MSG)]}, config)
        while state.get("__interrupt__"):
            n = sum(show_interrupt(i) for i in state["__interrupt__"])
            print("reviewer> approve\n")
            state = await agent.ainvoke(
                Command(resume={"decisions": [{"type": "approve"}] * n}), config
            )

        print(f"kompass> {state['messages'][-1].content}\n")

    refunds = run_sql(
        "SELECT id, amount_eur, status, approved_by FROM refunds WHERE order_id = 4471"
    )
    ticket = run_sql("SELECT status FROM tickets WHERE id = 88012")
    print(RULE)
    print(f"VERIFY  refund row: {refunds or 'MISSING'}")
    print(f"VERIFY  ticket 88012 status: {ticket[0]['status']}")
    print(RULE)
    return 0 if refunds else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    sys.exit(asyncio.run(main()))
