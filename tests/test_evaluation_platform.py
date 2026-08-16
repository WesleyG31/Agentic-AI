"""Offline contracts for the golden suite, trajectory metrics and prompt versions."""

import json

from langchain_core.messages import AIMessage, ToolMessage

import kompass.graph.agent  # noqa: F401 - registers agent prompts
import kompass.graph.critic  # noqa: F401 - registers critic prompt
import kompass.guardrails.safety  # noqa: F401 - registers guardrail prompt
from evals.dataset import expected_tools, load_golden, tool_trajectory
from evals.judge import Verdict
from evals.run import regression_failures
from kompass.config import ROOT
from kompass.prompts import all_prompts, prompt_manifest


def test_golden_dataset_is_interview_sized_and_balanced():
    items = load_golden()
    assert len(items) == 60
    categories = {
        name: sum(item["category"] == name for item in items)
        for name in {"rag", "sql", "multi", "action", "abstain"}
    }
    assert all(count >= 5 for count in categories.values())
    assert all(isinstance(expected_tools(item), list) for item in items)


def test_tool_trajectory_preserves_calls_arguments_and_results():
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"name": "query_database", "args": {"sql": "SELECT 1"}, "id": "c1"}],
        ),
        ToolMessage(content='[{"1": 1}]', tool_call_id="c1", name="query_database"),
    ]
    assert tool_trajectory(messages) == [
        {"name": "query_database", "args": {"sql": "SELECT 1"}, "result": '[{"1": 1}]'}
    ]


def test_prompt_registry_has_explicit_versions_and_fingerprints():
    prompts = all_prompts()
    assert len(prompts) >= 5
    assert len({prompt.name for prompt in prompts}) == len(prompts)
    assert all(prompt.version and len(prompt.fingerprint) == 12 for prompt in prompts)
    assert set(prompt_manifest()) >= {
        "kompass-agent-system",
        "kompass-planning",
        "kompass-injection-guard",
        "kompass-grounding-critic",
        "kompass-eval-judge",
    }


def test_judge_availability_is_harness_metadata_not_llm_output():
    assert "judge_available" not in Verdict.model_json_schema()["properties"]
    verdict = Verdict(
        answer_correctness=True,
        hallucination=False,
        tool_selection=True,
        tool_arguments_correct=True,
        retrieval_relevance=1.0,
        task_success=True,
        notes="ok",
    )
    assert verdict.judge_available is True


def test_regression_gate_checks_every_required_metric():
    baseline = json.loads((ROOT / "evals" / "regression_baseline.json").read_text(encoding="utf-8"))
    metrics = {**baseline["minimum"], **baseline["maximum"]}
    assert regression_failures(metrics) == []
    metrics["hallucination_rate"] = 1.0
    assert any("hallucination_rate" in failure for failure in regression_failures(metrics))
