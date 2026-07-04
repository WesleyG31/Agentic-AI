"""Drive the user-simulator eval: each persona converses with the real agent.

For every scenario we reset a private database, open a fresh agent thread, and
let the UserSimulator talk to the live Kompass agent turn by turn — resuming any
HITL pause with the scenario's scripted reviewer decision (approve/reject), the
same loop demo.py uses. When the simulator ends (or max_turns is hit) we check
the goal state (DB side effect or a substring of the final answer) and ask the
LLM judge for a quality read. Results print as a table with a task-completion
rate and are written to evals/results/user_sim.json.

DB isolation: point KOMPASS_ACME_DB at a private path before running. The MCP
tool subprocesses are spawned with a sanitized environment that would otherwise
drop that variable, so we add it to the inherited whitelist below — this keeps
the agent's own reads and writes on the private DB, safe for concurrent runs.

Run:  python -m evals.user_simulator.run_sim   (with KOMPASS_ACME_DB set + OPENAI_API_KEY)
"""

import asyncio
import json
import sys
from uuid import uuid4

import mcp.client.stdio as mcp_stdio
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from evals.judge import judge
from evals.user_simulator.simulator import END, UserSimulator
from kompass.config import ROOT, settings
from kompass.graph.agent import build_agent
from kompass.retrieval.nl2sql import run_sql
from kompass.scripts.seed import build_db

# Propagate the private-DB path to the MCP tool subprocesses (see module docstring).
mcp_stdio.DEFAULT_INHERITED_ENV_VARS.append("KOMPASS_ACME_DB")

SCENARIOS = ROOT / "evals" / "user_simulator" / "scenarios.json"
RESULTS = ROOT / "evals" / "results"
MAX_RESUMES = 5  # a rejected action may be re-proposed; bound the resume loop


def goal_met(scenario: dict, answer: str) -> bool:
    """Did the run reach the goal state? DB check for actions, substring for answers."""
    if "success_contains" in scenario:
        return scenario["success_contains"] in answer
    rows = run_sql(scenario["success_sql"])
    value = next(iter(rows[0].values())) if rows else 0
    return value == scenario["success_expect"]


async def run_scenario(agent, scenario: dict) -> dict:
    """Reset the private DB, then converse until the simulator ends or max_turns is hit."""
    build_db()  # honors KOMPASS_ACME_DB — reset to a clean, goal-unmet state
    config = {"configurable": {"thread_id": f"sim-{scenario['id']}-{uuid4().hex[:6]}"}}
    sim = UserSimulator(scenario)
    history: list[dict] = []
    answer = ""
    turns = 0

    for _ in range(scenario["max_turns"]):
        user_msg = sim.respond(history)
        if END in user_msg:
            break
        turns += 1
        print(f"  user> {user_msg}")
        history.append({"role": "user", "content": user_msg})

        state = await agent.ainvoke({"messages": [("user", user_msg)]}, config)
        for _ in range(MAX_RESUMES):
            if not state.get("__interrupt__"):
                break
            n = sum(len(i.value["action_requests"]) for i in state["__interrupt__"])
            print(f"  reviewer> {scenario['reviewer_decision']} ({n} action(s))")
            state = await agent.ainvoke(
                Command(resume={"decisions": [{"type": scenario["reviewer_decision"]}] * n}),
                config,
            )
        answer = str(state["messages"][-1].content)
        print(f"  kompass> {answer}")
        history.append({"role": "assistant", "content": answer})

    verdict = judge(
        {
            "question": scenario["goal"],
            "category": scenario["judge_category"],
            "expected_facts": scenario["judge_facts"],
        },
        answer,
    )
    return {
        "id": scenario["id"],
        "turns": turns,
        "goal_met": goal_met(scenario, answer),
        "judged_correct": verdict.correct,
        "grounded": verdict.grounded,
        "notes": verdict.notes,
        "final_answer": answer,
    }


def print_table(results: list[dict]) -> float:
    """Print the results table and return the task-completion rate."""
    print(f"\n{'scenario':<18}{'turns':>7}{'goal_met':>11}{'judged_correct':>17}")
    print("-" * 53)
    for r in results:
        print(f"{r['id']:<18}{r['turns']:>7}{str(r['goal_met']):>11}{str(r['judged_correct']):>17}")
    met = sum(1 for r in results if r["goal_met"])
    rate = met / len(results)
    print("-" * 53)
    print(f"task-completion rate: {rate:.0%}  ({met}/{len(results)})")
    return rate


async def main() -> int:
    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    print(f"user-sim: {len(scenarios)} scenarios on DB '{settings.acme_db}'\n")

    results = []
    async with AsyncSqliteSaver.from_conn_string(str(ROOT / settings.sqlite_checkpoint)) as saver:
        agent = await build_agent(saver)
        for scenario in scenarios:
            print(f"=== {scenario['id']} ({scenario['persona']}) ===")
            results.append(await run_scenario(agent, scenario))
            print()

    rate = print_table(results)

    RESULTS.mkdir(exist_ok=True)
    out = {"task_completion_rate": rate, "scenarios": results}
    (RESULTS / "user_sim.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nresults → {RESULTS / 'user_sim.json'}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    sys.exit(asyncio.run(main()))
