"""Tests for the saga / compensation runner -- pure in-memory, no DB, no LLM.

Fake steps append markers to a shared log, so we can assert both that forward
actions run and that compensations run in reverse order on failure.
"""

import pytest

from kompass.graph.saga import Step, run_saga


def _fake_step(name: str, log: list[str], fail: bool = False) -> Step:
    def do() -> None:
        if fail:
            raise RuntimeError(f"{name} failed")
        log.append(f"do:{name}")

    def undo() -> None:
        log.append(f"undo:{name}")

    return Step(name=name, do=do, undo=undo)


def test_full_success_runs_all_do_and_no_undo():
    log: list[str] = []
    result = run_saga([_fake_step(n, log) for n in ("a", "b", "c")])

    assert result.ok is True
    assert result.completed == ["a", "b", "c"]
    assert result.compensated == []
    assert result.error is None
    assert log == ["do:a", "do:b", "do:c"]  # every do() ran, no undo()


def test_failure_at_step_3_compensates_2_then_1_in_reverse():
    log: list[str] = []
    steps = [
        _fake_step("s1", log),
        _fake_step("s2", log),
        _fake_step("s3", log, fail=True),
    ]
    result = run_saga(steps)

    assert result.ok is False
    assert result.completed == ["s1", "s2"]  # s3's do() raised -> never completed
    assert result.compensated == ["s2", "s1"]  # rolled back in reverse order
    assert result.error == "s3 failed"
    assert log == ["do:s1", "do:s2", "undo:s2", "undo:s1"]  # no undo:s3


def test_failed_compensation_propagates():
    # A do() raising is the expected path and is caught; an undo() raising is a real
    # incident and must propagate out of run_saga rather than be swallowed.
    def s1_do() -> None:
        pass

    def s1_undo() -> None:
        raise RuntimeError("compensation failed")

    def s2_do() -> None:
        raise RuntimeError("forward failed")

    steps = [Step("s1", s1_do, s1_undo), Step("s2", s2_do, lambda: None)]
    with pytest.raises(RuntimeError, match="compensation failed"):
        run_saga(steps)
