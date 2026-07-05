"""Semantic cache (paraphrase hit / unrelated miss) and the token-budget middleware.

The cache test uses live embeddings (no LLM); the budget test feeds a hand-built state.
"""

from langchain_core.messages import AIMessage

from kompass.graph.budget import TokenBudgetMiddleware
from kompass.models import cache


def _ai(total_tokens: int) -> AIMessage:
    usage = {"input_tokens": total_tokens, "output_tokens": 0, "total_tokens": total_tokens}
    return AIMessage(content="x", usage_metadata=usage)


def test_cache_paraphrase_hits_unrelated_misses():
    cache.clear()
    cache.store("How long is the refund window for returns?", "30 days from delivery.")
    assert cache.lookup("What's the return period for a refund?") == "30 days from delivery."
    assert cache.lookup("Who is the company CEO?") is None
    cache.clear()


def test_budget_jumps_to_end_over_cap():
    mw = TokenBudgetMiddleware(cap=100)
    over = mw.after_model({"messages": [_ai(60), _ai(60)]}, None)
    assert over is not None and over["jump_to"] == "end"


def test_budget_passes_under_cap():
    mw = TokenBudgetMiddleware(cap=100)
    assert mw.after_model({"messages": [_ai(40)]}, None) is None
