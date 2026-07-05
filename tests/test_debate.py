"""Deterministic test for the debate vote tally — no LLM, so CI stays fast/offline.

`tally` is the pure vote-counting half of the panel; the model calls (the lens opinions and
the judge) are exercised only by the module's live demo, never in CI.
"""

from kompass.graph.debate import Opinion, tally


def _op(position: str) -> Opinion:
    return Opinion(position=position, reason="")


def test_tally_counts_each_position():
    opinions = [_op("approve"), _op("deny"), _op("deny"), _op("escalate")]
    assert tally(opinions) == {"approve": 1, "deny": 2, "escalate": 1}


def test_tally_reports_all_positions_including_zeros():
    # Positions absent from the panel are still reported as 0, so the count is stable.
    assert tally([_op("approve"), _op("approve")]) == {"approve": 2, "deny": 0, "escalate": 0}


def test_tally_of_empty_panel_is_all_zero():
    assert tally([]) == {"approve": 0, "deny": 0, "escalate": 0}
