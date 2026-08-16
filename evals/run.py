"""Golden regression harness for the complete agent and a naive-RAG baseline.

It measures task success, answer correctness, hallucination, tool selection,
tool arguments, retrieval relevance, latency, tokens and configured USD cost.
Every agent episode and LLM-judge call is traceable in Langfuse when enabled.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from evals import baseline
from evals.dataset import expected_tools, load_golden, tool_trajectory
from evals.judge import judge
from kompass.config import ROOT, settings
from kompass.graph.agent import build_agent
from kompass.observability import agent_trace, client, token_usage
from kompass.prompts import prompt_manifest
from kompass.retrieval.nl2sql import run_sql
from kompass.scripts.seed import build_db

RESULTS = ROOT / "evals" / "results"
REGRESSION = ROOT / "evals" / "regression_baseline.json"
MAX_RESUMES = 5


async def agent_episode(agent, item: dict) -> dict:
    """Run one full graph episode, including scripted HITL decisions."""
    thread_id = f"eval-{item['id']}-{uuid4().hex[:8]}"
    base_config = {"configurable": {"thread_id": thread_id}}
    decision = (item.get("action") or {}).get("decision", "approve")
    t0 = time.monotonic()

    with agent_trace(
        name="kompass-evaluation-episode",
        thread_id=thread_id,
        user_id="golden-regression-suite",
        input={"case_id": item["id"], "question": item["question"]},
        tags=["evaluation", item["category"]],
        metadata={
            "golden_case_id": item["id"],
            "expected_tools": expected_tools(item),
            "prompt_versions": prompt_manifest(),
        },
    ) as trace:
        config = trace.graph_config(base_config)
        state = await agent.ainvoke({"messages": [("user", item["question"])]}, config)
        for attempt in range(MAX_RESUMES):
            if not state.get("__interrupt__"):
                break
            count = sum(len(i.value["action_requests"]) for i in state["__interrupt__"])
            trace.event(
                "human-review-decision",
                input={"decision": decision, "actions": count},
                metadata={"automated_eval_reviewer": True, "attempt": attempt + 1},
            )
            state = await agent.ainvoke(
                Command(resume={"decisions": [{"type": decision}] * count}), config
            )

        answer = str(state["messages"][-1].content)
        trajectory = tool_trajectory(state["messages"])
        usage = token_usage(state["messages"])
        trace.set_output(
            {"answer": answer, "tool_trajectory": trajectory},
            status="completed" if not state.get("__interrupt__") else "interrupted",
            **usage,
        )

    return {
        "answer": answer,
        "trajectory": trajectory,
        "thread_id": thread_id,
        "trace_id": trace.trace_id,
        "trace_url": trace.trace_url,
        "latency_s": round(time.monotonic() - t0, 3),
        "tokens": usage["total_tokens"],
        "cost_usd": usage["cost_usd"],
    }


def baseline_episode(item: dict) -> dict:
    t0 = time.monotonic()
    text = baseline.answer(item["question"])
    return {
        "answer": text,
        "trajectory": [],
        "thread_id": f"baseline-{item['id']}",
        "trace_id": None,
        "trace_url": None,
        "latency_s": round(time.monotonic() - t0, 3),
        "tokens": None,
        "cost_usd": None,
    }


def verify_action(item: dict) -> bool:
    rows = run_sql(item["action"]["verify_sql"])
    value = next(iter(rows[0].values())) if rows else 0
    return value == item["action"]["expect"]


def score(item: dict, episode: dict, action_ok: bool | None) -> dict:
    """Combine deterministic contracts with a multidimensional LLM judge."""
    with agent_trace(
        name="kompass-llm-judge",
        thread_id=episode["thread_id"],
        user_id="golden-regression-suite",
        input={"case_id": item["id"], "answer": episode["answer"]},
        tags=["evaluation", "llm-as-a-judge"],
        metadata={"judge_prompt": prompt_manifest().get("kompass-eval-judge")},
        observation_type="evaluator",
    ) as trace:
        try:
            verdict = judge(
                item,
                episode["answer"],
                episode["trajectory"],
                action_ok,
                config=trace.graph_config(
                    {}, run_name="kompass-eval-judge", tags=["evaluator"]
                ),
            )
        except Exception as exc:
            # Preserve the run artifact but fail closed: a missing judge cannot be
            # counted as quality. CI also enforces judge_coverage explicitly.
            selected = {call.get("name") for call in episode["trajectory"]}
            required = expected_tools(item)
            selection_ok = all(name in selected for name in required)
            verdict = judge_fallback(item, episode["answer"], selection_ok, action_ok, exc)
            trace.event(
                "judge-fallback",
                output={"error": f"{type(exc).__name__}: {exc}"},
                level="ERROR",
            )
        trace.set_output(
            {**verdict.model_dump(), "judge_available": verdict.judge_available}
        )

    answer = episode["answer"]
    cited = item["must_cite"] is None or item["must_cite"].lower() in answer.lower()
    facts_strict = all(str(f).lower() in answer.lower() for f in item["expected_facts"])
    selected = [str(call.get("name")) for call in episode["trajectory"]]
    normalized = set(selected)
    if "research" in normalized:  # multi-agent worker encapsulates both read tools
        normalized.update({"search_docs", "query_database"})
    required = expected_tools(item)
    selection_contract = all(name in normalized for name in required)
    tool_selection = verdict.tool_selection and selection_contract
    tool_arguments = verdict.tool_arguments_correct and tool_selection
    task_success = bool(
        verdict.task_success and verdict.answer_correctness and cited and (action_ok is not False)
    )
    unsafe = bool(
        item.get("action") and item["action"]["decision"] == "reject" and action_ok is False
    )
    return {
        "id": item["id"],
        "category": item["category"],
        "task_success": task_success,
        "judge_available": verdict.judge_available,
        "answer_correctness": verdict.answer_correctness,
        "hallucination": verdict.hallucination,
        "tool_selection": tool_selection,
        "tool_arguments_correct": tool_arguments,
        "retrieval_relevance": round(verdict.retrieval_relevance, 3),
        "cited": cited,
        "facts_strict": facts_strict,
        "action_ok": action_ok,
        "unsafe": unsafe,
        "selected_tools": selected,
        "notes": verdict.notes,
        **{
            key: episode[key]
            for key in ("latency_s", "tokens", "cost_usd", "trace_id", "trace_url")
        },
    }


async def run_agent_system(items: list[dict]) -> dict[str, dict]:
    """Run read cases concurrently and isolate every side-effect case with a fresh DB."""
    episodes: dict[str, dict] = {}
    async with AsyncSqliteSaver.from_conn_string(str(ROOT / settings.sqlite_checkpoint)) as saver:
        agent = await build_agent(saver)
        knowledge = [i for i in items if not i.get("action")]
        actions = [i for i in items if i.get("action")]
        semaphore = asyncio.Semaphore(4)

        async def one(item: dict) -> None:
            async with semaphore:
                episodes[item["id"]] = await agent_episode(agent, item)
                print(f"  agent {item['id']} done ({episodes[item['id']]['latency_s']}s)")

        await asyncio.gather(*(one(item) for item in knowledge))
        for item in actions:
            build_db()
            episodes[item["id"]] = await agent_episode(agent, item)
            episodes[item["id"]]["action_ok"] = verify_action(item)
            print(f"  agent {item['id']} done (action_ok={episodes[item['id']]['action_ok']})")
    return episodes


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def aggregate(scored: list[dict]) -> dict:
    n = len(scored)
    rate = lambda key: sum(bool(row[key]) for row in scored) / n  # noqa: E731
    latencies = [row["latency_s"] for row in scored]
    costs = [row["cost_usd"] for row in scored if row["cost_usd"] is not None]
    tokens = [row["tokens"] for row in scored if row["tokens"] is not None]
    return {
        "n": n,
        "task_success": rate("task_success"),
        "judge_coverage": rate("judge_available"),
        "answer_correctness": rate("answer_correctness"),
        "hallucination_rate": rate("hallucination"),
        "tool_selection": rate("tool_selection"),
        "tool_arguments_correct": rate("tool_arguments_correct"),
        "retrieval_relevance": round(sum(row["retrieval_relevance"] for row in scored) / n, 3),
        "citation_rate": rate("cited"),
        "unsafe_actions": sum(bool(row["unsafe"]) for row in scored),
        "mean_latency_s": round(sum(latencies) / n, 3),
        "p95_latency_s": round(_percentile(latencies, 0.95), 3),
        "mean_tokens": round(sum(tokens) / len(tokens), 1) if tokens else None,
        "mean_cost_usd": round(sum(costs) / len(costs), 6) if costs else None,
    }


def judge_fallback(
    item: dict,
    answer: str,
    selection_ok: bool,
    action_ok: bool | None,
    error: Exception,
):
    """Conservative deterministic record when the external judge is unavailable."""
    from evals.judge import Verdict

    facts_ok = all(str(fact).lower() in answer.lower() for fact in item["expected_facts"])
    if item["category"] == "abstain":
        lowered = answer.lower()
        facts_ok = any(
            marker in lowered
            for marker in ("can't", "cannot", "unavailable", "not found", "no puedo", "no existe")
        )
    retrieval = 1.0 if not expected_tools(item) else (1.0 if selection_ok else 0.0)
    verdict = Verdict(
        answer_correctness=facts_ok,
        hallucination=True,
        tool_selection=selection_ok,
        tool_arguments_correct=selection_ok,
        retrieval_relevance=retrieval,
        task_success=False,
        notes=f"LLM judge unavailable; conservative fallback: {type(error).__name__}",
    )
    verdict._judge_available = False
    return verdict


def publish_scores(scored: list[dict]) -> None:
    """Add eval scores to production traces for Langfuse dashboards."""
    lf = client()
    if lf is None:
        return
    for row in scored:
        if not row["trace_id"]:
            continue
        for name in (
            "task_success",
            "answer_correctness",
            "tool_selection",
            "tool_arguments_correct",
        ):
            lf.create_score(
                trace_id=row["trace_id"],
                name=name,
                value=1.0 if row[name] else 0.0,
                comment=row["notes"],
                metadata={"golden_case_id": row["id"]},
            )
        lf.create_score(
            trace_id=row["trace_id"],
            name="hallucination",
            value=1.0 if row["hallucination"] else 0.0,
            comment="Lower is better",
        )
        lf.create_score(
            trace_id=row["trace_id"],
            name="retrieval_relevance",
            value=row["retrieval_relevance"],
            comment=row["notes"],
        )
    lf.flush()


def regression_failures(metrics: dict, min_score: float | None = None) -> list[str]:
    thresholds = json.loads(REGRESSION.read_text(encoding="utf-8"))
    minimum = dict(thresholds["minimum"])
    maximum = dict(thresholds["maximum"])
    if min_score is not None:
        minimum["task_success"] = min_score
    failures = [
        f"{name} {metrics[name]:.3f} < {threshold:.3f}"
        for name, threshold in minimum.items()
        if metrics[name] < threshold
    ]
    failures.extend(
        f"{name} {metrics[name]:.3f} > {threshold:.3f}"
        for name, threshold in maximum.items()
        if metrics[name] > threshold
    )
    return failures


def readme_table(agent: dict, base: dict) -> str:
    pct = lambda value: f"{100 * value:.0f}%"  # noqa: E731
    rows = [
        ("Task success", "task_success", False),
        ("Answer correctness (LLM judge)", "answer_correctness", False),
        ("Hallucination rate", "hallucination_rate", True),
        ("Tool selection", "tool_selection", False),
        ("Tool arguments correct", "tool_arguments_correct", False),
        ("Retrieval relevance", "retrieval_relevance", False),
    ]
    body = []
    for label, key, lower_is_better in rows:
        delta = agent[key] - base[key]
        sign = "" if lower_is_better else "+"
        body.append(
            f"| {label} | {pct(base[key])} | **{pct(agent[key])}** | {sign}{100 * delta:.0f}pp |"
        )
    return (
        f"| Metric (n={agent['n']}) | Naive RAG baseline | Kompass | Delta |\n"
        "|---|---:|---:|---:|\n"
        + "\n".join(body)
        + f"\n| P95 latency | {base['p95_latency_s']}s | {agent['p95_latency_s']}s | - |"
    )


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
    parser.add_argument("--min-score", type=float)
    args = parser.parse_args()

    items = load_golden(args.limit)
    print(f"golden set: {len(items)} items")
    print("running agent episodes...")
    agent_eps = await run_agent_system(items)

    base_eps: dict[str, dict] = {}
    if not args.agent_only:
        print("running baseline episodes...")
        build_db()
        baseline._collection()
        with ThreadPoolExecutor(6) as pool:
            for item, episode in zip(items, pool.map(baseline_episode, items), strict=True):
                base_eps[item["id"]] = episode

    print("judging complete trajectories...")
    with ThreadPoolExecutor(4) as pool:
        agent_scored = list(
            pool.map(
                lambda item: score(
                    item, agent_eps[item["id"]], agent_eps[item["id"]].get("action_ok")
                ),
                items,
            )
        )
        base_scored = (
            list(
                pool.map(
                    lambda item: score(
                        item, base_eps[item["id"]], False if item.get("action") else None
                    ),
                    items,
                )
            )
            if base_eps
            else []
        )

    agent_agg = aggregate(agent_scored)
    publish_scores(agent_scored)
    print("\nAGENT:", json.dumps(agent_agg, indent=2))
    RESULTS.mkdir(exist_ok=True)
    output = {"agent": {"aggregate": agent_agg, "items": agent_scored}}
    if base_scored:
        base_agg = aggregate(base_scored)
        output["baseline"] = {"aggregate": base_agg, "items": base_scored}
        print("BASELINE:", json.dumps(base_agg, indent=2))
        if not args.limit:
            table = readme_table(agent_agg, base_agg)
            update_readme(table)
            print("\nREADME metrics table updated")
    result_path = RESULTS / "results.json"
    result_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"results -> {result_path}")

    if args.ci:
        failures = regression_failures(agent_agg, args.min_score)
        if failures:
            print("CI REGRESSION GATE FAILED:\n- " + "\n- ".join(failures))
            return 1
        print("CI regression gate passed")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(main()))
