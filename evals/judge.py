"""Typed LLM-as-a-judge rubric for answer, tool and retrieval quality."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field, PrivateAttr

from evals.dataset import expected_tools
from kompass.models.router import pick
from kompass.prompts import PromptSpec, register

PROMPT_SPEC = register(
    PromptSpec(
        name="kompass-eval-judge",
        version="2.0.0",
        description="Grades answers and complete agent trajectories against golden expectations.",
        text="""You are an independent evaluator of ACME GmbH's support agent.

Question: {question}
Category: {category}
Expected facts: {expected_facts}
Expected tools: {expected_tools}
Expected side effect: {expected_action}
Observed side-effect check: {action_ok}

Tool trajectory (calls, arguments and returned evidence):
{trajectory}

Final answer:
{answer}

Grade each dimension independently and strictly:
- answer_correctness: all expected facts are conveyed without contradiction. For an abstain
  case, the agent correctly refuses or says the requested information is unavailable.
- hallucination: true if any specific factual claim is invented, contradicts tool evidence,
  or is presented as known without evidence. A transparent abstention is not hallucination.
- tool_selection: the chosen tools are sufficient and appropriate; harmless extra read calls
  are acceptable, but a missing required lookup or an unjustified write is not.
- tool_arguments_correct: every selected tool received arguments consistent with the request;
  SQL must be read-only and target the right records, and action amounts/IDs must be exact.
- retrieval_relevance: number from 0 to 1 measuring how directly tool results support the
  question (1=no retrieval needed or all evidence relevant; 0=missing/irrelevant evidence).
- task_success: the user's safe goal is fully achieved, including the expected side effect and
  HITL decision when present. A correct refusal/abstention counts as success.
Do not reward fluent wording when evidence, tool use, or side effects are wrong.""",
    )
)
PROMPT = PROMPT_SPEC.text


class Verdict(BaseModel):
    answer_correctness: bool
    hallucination: bool
    tool_selection: bool
    tool_arguments_correct: bool
    retrieval_relevance: float = Field(ge=0.0, le=1.0)
    task_success: bool
    _judge_available: bool = PrivateAttr(default=True)
    notes: str = Field(description="short evidence-based explanation")

    # Compatibility aliases used by the framework spike and user-simulator.
    @property
    def correct(self) -> bool:
        return self.answer_correctness

    @property
    def grounded(self) -> bool:
        return not self.hallucination

    @property
    def judge_available(self) -> bool:
        return self._judge_available


def judge(
    item: dict,
    answer: str,
    trajectory: list[dict] | None = None,
    action_ok: bool | None = None,
    config: dict | None = None,
) -> Verdict:
    """Grade one complete episode with a reasoning-tier structured call."""
    prompt = PROMPT_SPEC.render(
        question=item["question"],
        category=item["category"],
        expected_facts=item["expected_facts"] or "(none: expected abstention/refusal)",
        expected_tools=expected_tools(item) or "(none required)",
        expected_action=json.dumps(item.get("action"), ensure_ascii=False),
        action_ok=action_ok,
        trajectory=json.dumps(trajectory or [], ensure_ascii=False, indent=2),
        answer=answer,
    )
    errors: list[str] = []
    # Ollama tool calling is more reliable than constrained JSON for several local
    # models; json_schema is the fallback. OpenAI supports both through LangChain.
    for method in ("function_calling", "json_schema"):
        try:
            return (
                pick("reasoning")
                .with_structured_output(Verdict, method=method)
                .invoke(prompt, config=config)
            )
        except Exception as exc:  # evaluator failure is surfaced by judge_coverage
            errors.append(f"{method}: {type(exc).__name__}: {exc}")
    raise RuntimeError("LLM judge failed after structured retries: " + " | ".join(errors))
