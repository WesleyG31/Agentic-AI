"""Eval harness: golden set → agent + baseline episodes → judge → metrics table.

Measures the full agent against the naïve-RAG baseline on the same golden set:
correctness (LLM judge + strict fact match), grounding, citation discipline,
end-to-end task completion (DB side effects for HITL actions), safety
(rejected actions must leave no trace), latency and LLM cost.

Usage:
  python -m evals.run                        # full run, both systems
  python -m evals.run --limit 6              # quick pass while iterating
  python -m evals.run --agent-only
  python -m evals.run --ci --min-score 0.75  # regression gate for CI
"""

import argparse
import asyncio
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from evals import baseline
from evals.judge import judge
from kompass.config import ROOT, settings
from kompass.graph.agent import build_agent
from kompass.retrieval.nl2sql import run_sql
from kompass.scripts.seed import build_db

GOLDEN = ROOT / "evals" / "golden_set.json"
RESULTS = ROOT / "evals" / "results"
MAX_RESUMES = 5  # a rejected tool may be re-proposed; bound the resume loop

# List prices July 2026, USD per 1M tokens (input, output). Both systems under
# test run on the balanced tier; judge cost is harness overhead, not counted.
PRICE_IN, PRICE_OUT = 2.50, 15.00  # gpt-5.4


def tokens_and_cost(messages) -> tuple[int, int, float]:
    usages = [m.usage_metadata for m in messages if getattr(m, "usage_metadata", None)]
    tin = sum(u["input_tokens"] for u in usages)
    tout = sum(u["output_tokens"] for u in usages)
    return tin, tout, (tin * PRICE_IN + tout * PRICE_OUT) / 1e6


async def agent_episode(agent, item: dict) -> dict:
    """One full agent run; plays the reviewer with the item's scripted decision."""
    config = {"configurable": {"thread_id": f"eval-{item['id']}-{uuid4().hex[:6]}"}}
    decision = (item.get("action") or {}).get("decision", "approve")
    t0 = time.monotonic()
    state = await agent.ainvoke({"messages": [("user", item["question"])]}, config)
    for _ in range(MAX_RESUMES):
        if not state.get("__interrupt__"):
            break
        n = sum(len(i.value["action_requests"]) for i in state["__interrupt__"])
        state = await agent.ainvoke(
            Command(resume={"decisions": [{"type": decision}] * n}), config
        )
    tin, tout, cost = tokens_and_cost(state["messages"])
    return {
        "answer": str(state["messages"][-1].content),
        "latency_s": round(time.monotonic() - t0, 1),
        "tokens": tin + tout,
        "cost_usd": round(cost, 4),
    }


def baseline_episode(item: dict) -> dict:
    t0 = time.monotonic()
    text = baseline.answer(item["question"])
    return {
        "answer": text,
        "latency_s": round(time.monotonic() - t0, 1),
        "tokens": None,  # single call; cost estimated from the answer path only
        "cost_usd": None,
    }


def verify_action(item: dict) -> bool:
    """Did the DB end up in the expected state? (count for refunds, status for tickets)"""
    rows = run_sql(item["action"]["verify_sql"])
    value = next(iter(rows[0].values())) if rows else 0
    return value == item["action"]["expect"]


def score(item: dict, episode: dict, action_ok: bool | None) -> dict:
    """Combine deterministic checks with the judge verdict into one scored record."""
    answer = episode["answer"]
    verdict = judge(item, answer)
    cited = item["must_cite"] is None or item["must_cite"] in answer
    facts = all(f.lower() in answer.lower() for f in item["expected_facts"])
    resolved = (
        verdict.correct and cited and (action_ok if item.get("action") else True)
    )
    unsafe = bool(
        item.get("action")
        and item["action"]["decision"] == "reject"
        and action_ok is False  # a rejected action left a trace in the DB
    )
    return {
        "id": item["id"],
        "category": item["category"],
        "correct": verdict.correct,
        "grounded": verdict.grounded,
        "cited": cited,
        "facts_strict": facts,
        "action_ok": action_ok,
        "resolved": resolved,
        "unsafe": unsafe,
        "notes": verdict.notes,
        **{k: episode[k] for k in ("latency_s", "tokens", "cost_usd")},
    }


async def run_agent_system(items: list[dict]) -> dict[str, dict]:
    """Agent episodes: knowledge items concurrently, action items sequentially
    (each action item gets a fresh DB so side-effect checks are isolated)."""
    episodes: dict[str, dict] = {}
    async with AsyncSqliteSaver.from_conn_string(
        str(ROOT / settings.sqlite_checkpoint)
    ) as saver:
        agent = await build_agent(saver)
        knowledge = [i for i in items if not i.get("action")]
        actions = [i for i in items if i.get("action")]

        sem = asyncio.Semaphore(4)

        async def one(it: dict) -> None:
            async with sem:
                episodes[it["id"]] = await agent_episode(agent, it)
                print(f"  agent {it['id']} done ({episodes[it['id']]['latency_s']}s)")

        await asyncio.gather(*(one(i) for i in knowledge))

        for it in actions:
            build_db()
            episodes[it["id"]] = await agent_episode(agent, it)
            episodes[it["id"]]["action_ok"] = verify_action(it)
            print(f"  agent {it['id']} done (action_ok={episodes[it['id']]['action_ok']})")
    return episodes


def aggregate(scored: list[dict]) -> dict:
    n = len(scored)
    rate = lambda key: sum(1 for s in scored if s[key]) / n  # noqa: E731
    with_cost = [s for s in scored if s["cost_usd"] is not None]
    return {
        "n": n,
        "resolved": rate("resolved"),
        "correct": rate("correct"),
        "grounded": rate("grounded"),
        "cited": rate("cited"),
        "facts_strict": rate("facts_strict"),
        "unsafe_actions": sum(1 for s in scored if s["unsafe"]),
        "mean_latency_s": round(sum(s["latency_s"] for s in scored) / n, 1),
        "mean_cost_usd": round(sum(s["cost_usd"] for s in with_cost) / len(with_cost), 4)
        if with_cost
        else None,
    }


def pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def readme_table(agent: dict, base: dict) -> str:
    delta = lambda k: f"+{100 * (agent[k] - base[k]):.0f}pp"  # noqa: E731
    cost = f"${agent['mean_cost_usd']:.3f}" if agent["mean_cost_usd"] else "—"
    resolved_row = (
        f"| **Resolution / deflection rate** | {pct(base['resolved'])} "
        f"| **{pct(agent['resolved'])}** | **{delta('resolved')}** |"
    )
    return f"""| Metric (n={agent["n"]}) | Naïve RAG baseline | Kompass | Δ |
|---|---|---|---|
{resolved_row}
| Correct (LLM judge) | {pct(base["correct"])} | {pct(agent["correct"])} | {delta("correct")} |
| Grounded / faithful | {pct(base["grounded"])} | {pct(agent["grounded"])} | {delta("grounded")} |
| Citation discipline | {pct(base["cited"])} | {pct(agent["cited"])} | {delta("cited")} |
| Unsafe actions (rejected → executed) | n/a | **{agent["unsafe_actions"]}** | — |
| Mean latency / case | {base["mean_latency_s"]}s | {agent["mean_latency_s"]}s | — |
| Mean LLM cost / case | — | {cost} | — |"""


def update_readme(table: str) -> None:
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    start, end = "<!-- EVAL:START -->", "<!-- EVAL:END -->"
    head, rest = text.split(start)
    _, tail = rest.split(end)
    readme.write_text(f"{head}{start}\n{table}\n{end}{tail}", encoding="utf-8")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    parser.add_argument("--agent-only", action="store_true")
    parser.add_argument("--ci", action="store_true")
    parser.add_argument("--min-score", type=float, default=0.75)
    args = parser.parse_args()

    items = json.loads(GOLDEN.read_text(encoding="utf-8"))[: args.limit]
    print(f"golden set: {len(items)} items")

    print("running agent episodes…")
    agent_eps = await run_agent_system(items)

    base_eps: dict[str, dict] = {}
    if not args.agent_only:
        print("running baseline episodes…")
        build_db()
        baseline._collection()  # warm the shared client before the thread pool
        with ThreadPoolExecutor(6) as pool:
            for it, ep in zip(items, pool.map(baseline_episode, items), strict=True):
                base_eps[it["id"]] = ep

    print("judging…")
    with ThreadPoolExecutor(8) as pool:
        agent_scored = list(
            pool.map(
                lambda it: score(it, agent_eps[it["id"]], agent_eps[it["id"]].get("action_ok")),
                items,
            )
        )
        base_scored = (
            list(
                pool.map(
                    lambda it: score(it, base_eps[it["id"]], False if it.get("action") else None),
                    items,
                )
            )
            if base_eps
            else []
        )

    agent_agg = aggregate(agent_scored)
    print("\nAGENT:", json.dumps(agent_agg, indent=2))

    RESULTS.mkdir(exist_ok=True)
    out = {"agent": {"aggregate": agent_agg, "items": agent_scored}}
    if base_scored:
        base_agg = aggregate(base_scored)
        print("BASELINE:", json.dumps(base_agg, indent=2))
        out["baseline"] = {"aggregate": base_agg, "items": base_scored}
        if not args.limit:  # only a full run may rewrite the README table
            table = readme_table(agent_agg, base_agg)
            update_readme(table)
            print("\nREADME table updated:\n" + table)
    (RESULTS / "results.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nresults → {RESULTS / 'results.json'}")

    if args.ci and agent_agg["resolved"] < args.min_score:
        print(f"CI GATE FAILED: resolved {agent_agg['resolved']:.2f} < {args.min_score}")
        return 1
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(main()))
