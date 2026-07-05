"""Token-budget guard: a backstop that halts a run before it burns an unbounded budget.

A misbehaving agent can loop — retrying tools, re-planning, arguing with the critic. The
recursion limit bounds steps; this bounds *cost*. after_model sums token usage so far and,
if a per-run cap is exceeded, ends the run cleanly instead of spending without limit.
"""

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage

BUDGET_DEFAULT = 200_000


class TokenBudgetMiddleware(AgentMiddleware):
    """End the run once cumulative token usage crosses `cap`."""

    def __init__(self, cap: int = BUDGET_DEFAULT):
        super().__init__()
        self.cap = cap

    @hook_config(can_jump_to=["end"])
    def after_model(self, state, runtime):
        used = sum(
            m.usage_metadata["total_tokens"]
            for m in state["messages"]
            if isinstance(m, AIMessage) and m.usage_metadata
        )
        if used <= self.cap:
            return None
        return {
            "messages": [
                AIMessage(
                    f"Token budget of {self.cap:,} exceeded ({used:,} used) — stopping this "
                    "run to bound cost. Please narrow the request or continue in a new run."
                )
            ],
            "jump_to": "end",
        }
