import asyncio

from kompass.research.workflow import (
    ResearchBudget,
    ResearchSource,
    ResearchWorkflow,
)
from kompass.security.trust import ContentOrigin, TrustLevel


def _source(
    source_id: str,
    content: str,
    *,
    provenance: str | None = None,
    facts: dict[str, str] | None = None,
) -> ResearchSource:
    return ResearchSource(
        source_id=source_id,
        title=source_id,
        content=content,
        provenance=provenance or f"fixture://{source_id}",
        provider="fixture",
        retrieved_at="2026-08-16T12:00:00+00:00",
        credibility=0.8,
        trust_level=TrustLevel.UNTRUSTED,
        origin=ContentOrigin.WEB,
        facts=facts or {},
    )


class FixtureProvider:
    name = "fixture"

    def __init__(self, sources: list[ResearchSource]) -> None:
        self.sources = sources

    async def search(self, query: str, limit: int) -> list[ResearchSource]:
        return self.sources[:limit]


class BrokenProvider:
    name = "broken"

    async def search(self, query: str, limit: int) -> list[ResearchSource]:
        raise RuntimeError("fixture failure")


class SlowProvider:
    name = "slow"

    async def search(self, query: str, limit: int) -> list[ResearchSource]:
        await asyncio.sleep(0.05)
        return [_source("late", "late evidence")]


def test_research_deduplicates_and_exposes_disagreement():
    first = _source("a", "The limit is 30 days.", facts={"return_window": "30 days"})
    duplicate = _source(
        "b",
        "  The limit is 30 days. ",
        provenance=first.provenance,
        facts={"return_window": "30 days"},
    )
    contrary = _source("c", "The limit is 60 days.", facts={"return_window": "60 days"})
    workflow = ResearchWorkflow([FixtureProvider([first, duplicate, contrary])])

    result = asyncio.run(workflow.run("What is the return limit?"))

    assert [source.source_id for source in result.sources] == ["a", "c"]
    assert result.citations_verified
    assert "Disagreement detected" in result.answer
    assert "return_window" in result.answer
    assert "[a]" in result.answer and "[c]" in result.answer


def test_research_enforces_source_budget_and_stopping_condition():
    workflow = ResearchWorkflow(
        [FixtureProvider([_source(str(i), f"evidence {i}") for i in range(5)])],
        budget=ResearchBudget(max_sources=2),
    )

    result = asyncio.run(workflow.run("Compare the two policies and their limits"))

    assert len(result.sources) == 2
    assert len(result.plan) <= 3
    assert result.stop_reason == "source_budget"


def test_research_records_provider_errors_and_abstains():
    result = asyncio.run(ResearchWorkflow([BrokenProvider()]).run("Find evidence"))

    assert result.stop_reason == "providers_failed"
    assert result.sources == ()
    assert "abstains" in result.answer
    assert result.provider_errors[0].startswith("broken: RuntimeError")


def test_research_deadline_is_bounded():
    workflow = ResearchWorkflow(
        [SlowProvider()],
        budget=ResearchBudget(timeout_seconds=0.001),
    )

    result = asyncio.run(workflow.run("Find evidence"))

    assert result.stop_reason == "deadline"
    assert result.sources == ()
    assert result.citations_verified
