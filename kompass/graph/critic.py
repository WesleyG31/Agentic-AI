"""Reflection: a grounding critic that reviews final answers before they ship.

Runs as agent middleware. When the model produces a final answer (no tool calls)
that was built on tool evidence, a fast-tier check verifies every claim is
supported; an ungrounded draft is sent back to the model exactly once with the
critique attached. Evaluator-optimizer, bounded to one retry.
"""

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import SystemMessage, ToolMessage
from pydantic import BaseModel, Field

from kompass.models.router import pick

MARKER = "[critic]"

PROMPT = """Review a support assistant's drafted answer against the evidence its tools returned.
Flag ONLY factual claims (numbers, dates, statuses, policy rules) that the evidence does not
support. Citations, phrasing and judgment calls are fine.

Evidence:
{evidence}

Draft answer:
{answer}"""


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
        review: Review = (
            pick("fast")
            .with_structured_output(Review)
            .invoke(PROMPT.format(evidence="\n\n".join(evidence), answer=last.content))
        )
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
