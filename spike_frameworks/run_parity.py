"""Parity run: the LangGraph Researcher vs. the PydanticAI one on the same golden items.

Runs rag-01..rag-05 + sql-01 + sql-02 through both implementations, scores each
answer with the shared LLM judge plus the deterministic fact/citation checks
(evals/run.py's score logic, minus actions/baseline), prints a parity table and
writes parity_results.json next to this file.

Token accounting: the PydanticAI side reports usage natively (result.usage());
the LangGraph side is measured from the runs.jsonl trace lines each episode
appends (see kompass/obs.py), which is why its episodes run sequentially.

Usage (repo root):  python -m spike_frameworks.run_parity
"""

import asyncio
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from evals.judge import judge
from kompass.config import ROOT
from kompass.graph.workers import research
from kompass.obs import TRACE_FILE
from spike_frameworks.researcher_pydantic_ai import researcher

ITEM_IDS = ["rag-01", "rag-02", "rag-03", "rag-04", "rag-05", "sql-01", "sql-02"]
GOLDEN = ROOT / "evals" / "golden_set.json"
OUT = Path(__file__).parent / "parity_results.json"

# List prices July 2026, USD per 1M tokens — same constants as evals/run.py.
PRICE_IN, PRICE_OUT = 2.50, 15.00  # gpt-5.4 (both researchers run on it)


def _episode(answer: str, t0: float, tin: int, tout: int) -> dict:
    return {
        "answer": answer,
        "latency_s": round(time.monotonic() - t0, 1),
        "tokens": tin + tout,
        "cost_usd": round((tin * PRICE_IN + tout * PRICE_OUT) / 1e6, 4),
    }


def _trace_tokens(offset: int) -> tuple[int, int]:
    """Sum input/output tokens of runs.jsonl lines appended after byte `offset`."""
    with TRACE_FILE.open(encoding="utf-8") as f:
        f.seek(offset)
        lines = [json.loads(ln) for ln in f if ln.strip()]
    return (
        sum(ln["input_tokens"] or 0 for ln in lines),
        sum(ln["output_tokens"] or 0 for ln in lines),
    )


async def langgraph_episode(question: str) -> dict:
    offset = TRACE_FILE.stat().st_size if TRACE_FILE.exists() else 0
    t0 = time.monotonic()
    answer = await research.ainvoke({"question": question})
    return _episode(answer, t0, *_trace_tokens(offset))


async def pydantic_ai_episode(question: str) -> dict:
    t0 = time.monotonic()
    result = await researcher.run(question)
    usage = result.usage  # property in pydantic-ai v2 (was a method in v1)
    return _episode(result.output, t0, usage.input_tokens, usage.output_tokens)


def score(item: dict, episode: dict) -> dict:
    """evals/run.py's score logic, simplified: judge verdict + deterministic checks."""
    answer = episode["answer"]
    verdict = judge(item, answer)
    cited = item["must_cite"] is None or item["must_cite"] in answer
    facts = all(f.lower() in answer.lower() for f in item["expected_facts"])
    return {
        "correct": verdict.correct,
        "grounded": verdict.grounded,
        "cited": cited,
        "facts_strict": facts,
        "notes": verdict.notes,
        **episode,
    }


def aggregate(scored: list[dict]) -> dict:
    n = len(scored)
    rate = lambda key: sum(1 for s in scored if s[key]) / n  # noqa: E731
    return {
        "n": n,
        "correct": rate("correct"),
        "grounded": rate("grounded"),
        "cited": rate("cited"),
        "facts_strict": rate("facts_strict"),
        "mean_latency_s": round(sum(s["latency_s"] for s in scored) / n, 1),
        "mean_tokens": round(sum(s["tokens"] for s in scored) / n),
        "mean_cost_usd": round(sum(s["cost_usd"] for s in scored) / n, 4),
    }


def yn(b: bool) -> str:
    return "yes" if b else "NO"


def table(items: list[dict], scored: dict[str, dict[str, dict]]) -> str:
    rows = [
        f"{'item':7} | {'LG correct/cited':16} | {'PAI correct/cited':17} "
        f"| {'LG s':>5} | {'PAI s':>5} | {'LG tok':>6} | {'PAI tok':>7}",
        "-" * 82,
    ]
    for it in items:
        lg, pai = scored["langgraph"][it["id"]], scored["pydantic_ai"][it["id"]]
        rows.append(
            f"{it['id']:7} | {yn(lg['correct']) + ' / ' + yn(lg['cited']):16} "
            f"| {yn(pai['correct']) + ' / ' + yn(pai['cited']):17} "
            f"| {lg['latency_s']:>5} | {pai['latency_s']:>5} "
            f"| {lg['tokens']:>6} | {pai['tokens']:>7}"
        )
    return "\n".join(rows)


async def main() -> None:
    golden = {i["id"]: i for i in json.loads(GOLDEN.read_text(encoding="utf-8"))}
    items = [golden[i] for i in ITEM_IDS]

    episodes: dict[str, dict[str, dict]] = {"langgraph": {}, "pydantic_ai": {}}
    for name, run in [("langgraph", langgraph_episode), ("pydantic_ai", pydantic_ai_episode)]:
        print(f"running {name} episodes…")
        for it in items:  # sequential: honest latencies + trace-file token attribution
            episodes[name][it["id"]] = await run(it["question"])
            print(f"  {name} {it['id']} done ({episodes[name][it['id']]['latency_s']}s)")

    print("judging…")
    with ThreadPoolExecutor(8) as pool:
        futures = {
            (name, it["id"]): pool.submit(score, it, episodes[name][it["id"]])
            for name in episodes
            for it in items
        }
        scored: dict[str, dict[str, dict]] = {"langgraph": {}, "pydantic_ai": {}}
        for (name, item_id), fut in futures.items():
            scored[name][item_id] = fut.result()

    out = {
        "date": "2026-07-04",
        "model": "openai:gpt-5.4",
        "items": {it["id"]: {n: scored[n][it["id"]] for n in scored} for it in items},
        "aggregate": {n: aggregate([scored[n][i] for i in ITEM_IDS]) for n in scored},
    }
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + table(items, scored))
    for name, agg in out["aggregate"].items():
        print(
            f"\n{name}: correct {agg['correct']:.0%}, cited {agg['cited']:.0%}, "
            f"grounded {agg['grounded']:.0%}, mean {agg['mean_latency_s']}s, "
            f"{agg['mean_tokens']} tok, ${agg['mean_cost_usd']:.4f}/item"
        )
    print(f"\nresults → {OUT}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
