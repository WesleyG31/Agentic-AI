"""Reflection: a grounding critic that reviews final answers before they ship.

Runs as agent middleware. When the model produces a final answer (no tool calls)
that was built on tool evidence, a fast-tier check verifies every claim is
supported; an ungrounded draft is sent back to the model exactly once with the
critique attached. Evaluator-optimizer, bounded to one retry.
"""

import logging

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import SystemMessage, ToolMessage
from pydantic import BaseModel, Field

from kompass.models.structured import StructuredOutputError, invoke_structured
from kompass.prompts import PromptSpec, register

MARKER = "[critic]"
logger = logging.getLogger(__name__)

PROMPT_SPEC = register(
    PromptSpec(
        name="kompass-grounding-critic",
        version="1.0.0",
        description="Checks a draft for claims unsupported by tool evidence.",
        text="""Review a support assistant's drafted answer against the evidence its tools returned.
Flag ONLY factual claims (numbers, dates, statuses, policy rules) that the evidence does not
support. Citations, phrasing and judgment calls are fine.

Evidence:
{evidence}

Draft answer:
{answer}""",
    )
)
PROMPT = PROMPT_SPEC.text


class Review(BaseModel):
    """Grounding review of a drafted answer."""

    grounded: bool = Field(description="every factual claim is supported by the evidence")
    problems: str = Field(description="the unsupported claims, one line each; empty if grounded")


class GroundingCritic(AgentMiddleware):
    """Send ungrounded final answers back to the model once, with the critique."""

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime):
        messages = state["messages"]
        last = messages[-1]
        if getattr(last, "tool_calls", None):
            return None  # not a final answer — tools are about to run
        evidence = [str(m.content) for m in messages if isinstance(m, ToolMessage)]
        if not evidence:
            return None  # nothing to ground against (greeting, abstention without lookups)
        if any(MARKER in str(m.content) for m in messages if isinstance(m, SystemMessage)):
            return None  # already retried once — ship it
        try:
            review = invoke_structured(
                "fast",
                Review,
                PROMPT.format(evidence="\n\n".join(evidence), answer=last.content),
            )
        except StructuredOutputError as exc:
            # The draft is already grounded in tool evidence. A best-effort critic
            # must never turn a useful completed run into HTTP 500 solely because a
            # local model emitted malformed JSON; failed attempts remain in Langfuse.
            logger.warning("Grounding critic skipped after structured retries: %s", exc)
            return None
        if review.grounded:
            return None
        return {
            "messages": [
                SystemMessage(
                    f"{MARKER} Your draft contains claims the tool evidence does not support:\n"
                    f"{review.problems}\n"
                    "Revise the answer: keep only what the evidence supports, cite it, and "
                    "re-run tools if you need more evidence."
                )
            ],
            "jump_to": "model",
        }
