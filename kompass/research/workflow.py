"""Bounded, provenance-preserving research orchestration.

Providers may use local fixtures, search APIs, databases, or read-only agents. The workflow
owns concurrency, deadlines, source limits, normalization, deduplication, contradictions,
and evidence checks so no provider can create an unbounded recursive research loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from kompass.runtime import runtime_now
from kompass.security.trust import ContentOrigin, TrustLevel


@dataclass(frozen=True)
class ResearchBudget:
    max_queries: int = 3
    max_sources: int = 8
    max_parallelism: int = 3
    timeout_seconds: float = 30.0
    max_source_chars: int = 12_000

    def __post_init__(self) -> None:
        if not 1 <= self.max_queries <= 8:
            raise ValueError("max_queries must be between 1 and 8")
        if not 1 <= self.max_sources <= 50:
            raise ValueError("max_sources must be between 1 and 50")
        if not 1 <= self.max_parallelism <= 8:
            raise ValueError("max_parallelism must be between 1 and 8")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True)
class ResearchSource:
    source_id: str
    title: str
    content: str
    provenance: str
    provider: str
    retrieved_at: str
    credibility: float
    trust_level: TrustLevel = TrustLevel.UNTRUSTED
    origin: ContentOrigin = ContentOrigin.WEB
    facts: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Disagreement:
    fact: str
    values: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class ResearchResult:
    query: str
    plan: tuple[str, ...]
    sources: tuple[ResearchSource, ...]
    disagreements: tuple[Disagreement, ...]
    answer: str
    citations_verified: bool
    stop_reason: str
    provider_errors: tuple[str, ...] = ()


class SourceProvider(Protocol):
    name: str

    async def search(self, query: str, limit: int) -> Sequence[ResearchSource]: ...


Synthesize = Callable[
    [str, Sequence[ResearchSource], Sequence[Disagreement]], Awaitable[str]
]


class ResearchWorkflow:
    def __init__(
        self,
        providers: Sequence[SourceProvider],
        *,
        budget: ResearchBudget | None = None,
        synthesize: Synthesize | None = None,
    ) -> None:
        if not providers:
            raise ValueError("at least one source provider is required")
        self._providers = tuple(providers)
        self._budget = budget or ResearchBudget()
        self._synthesize = synthesize

    async def run(self, query: str) -> ResearchResult:
        normalized = " ".join(query.split())
        if not normalized:
            raise ValueError("research query is required")
        plan = self._plan(normalized)
        semaphore = asyncio.Semaphore(self._budget.max_parallelism)

        async def gather(provider: SourceProvider, subquery: str):
            async with semaphore:
                try:
                    rows = await provider.search(subquery, self._budget.max_sources)
                    return list(rows), None
                except Exception as exc:
                    return [], f"{provider.name}: {type(exc).__name__}: {exc}"

        jobs = [gather(provider, subquery) for subquery in plan for provider in self._providers]
        try:
            async with asyncio.timeout(self._budget.timeout_seconds):
                gathered = await asyncio.gather(*jobs)
        except TimeoutError:
            return ResearchResult(
                query=normalized,
                plan=plan,
                sources=(),
                disagreements=(),
                answer="Research stopped because its deadline was reached.",
                citations_verified=True,
                stop_reason="deadline",
            )

        errors = tuple(error for _, error in gathered if error)
        sources = self._deduplicate(
            [source for rows, _ in gathered for source in rows]
        )[: self._budget.max_sources]
        disagreements = self._contradictions(sources)
        if self._synthesize is None:
            answer = self._deterministic_synthesis(sources, disagreements)
        else:
            answer = await self._synthesize(normalized, sources, disagreements)
        verified = self._verify_citations(answer, sources)
        if sources and not verified:
            answer += "\n\nEvidence index:\n" + "\n".join(
                f"- [{source.source_id}] {source.provenance}" for source in sources
            )
            verified = self._verify_citations(answer, sources)
        stop = "source_budget" if len(sources) >= self._budget.max_sources else "plan_complete"
        if not sources and errors:
            stop = "providers_failed"
        return ResearchResult(
            query=normalized,
            plan=plan,
            sources=tuple(sources),
            disagreements=tuple(disagreements),
            answer=answer,
            citations_verified=verified,
            stop_reason=stop,
            provider_errors=errors,
        )

    def _plan(self, query: str) -> tuple[str, ...]:
        # One query is the normal path. Explicit conjunctions or multiple questions justify
        # focused parallel gathering; recursion is impossible and the budget is a hard cap.
        candidates = [query]
        if query.count("?") > 1 or re.search(r"\b(and|versus|compare|as well as)\b", query, re.I):
            parts = [
                part.strip(" ,?.")
                for part in re.split(r"\?|\b(?:and|versus|as well as)\b", query, flags=re.I)
                if len(part.split()) >= 3
            ]
            candidates.extend(f"{query} -- focus: {part}" for part in parts)
        return tuple(dict.fromkeys(candidates))[: self._budget.max_queries]

    def _deduplicate(self, sources: Sequence[ResearchSource]) -> list[ResearchSource]:
        seen: set[str] = set()
        unique: list[ResearchSource] = []
        for source in sources:
            content = " ".join(source.content.split())[: self._budget.max_source_chars]
            key = hashlib.sha256(
                f"{source.provenance.casefold()}\0{content.casefold()}".encode()
            ).hexdigest()
            if key in seen:
                continue
            seen.add(key)
            unique.append(
                ResearchSource(
                    **{
                        **source.__dict__,
                        "content": content,
                        "credibility": max(0.0, min(1.0, source.credibility)),
                    }
                )
            )
        return unique

    @staticmethod
    def _contradictions(sources: Sequence[ResearchSource]) -> list[Disagreement]:
        facts: dict[str, dict[str, list[str]]] = {}
        for source in sources:
            for key, value in source.facts.items():
                facts.setdefault(key, {}).setdefault(str(value).casefold(), []).append(
                    source.source_id
                )
        return [
            Disagreement(
                fact=key,
                values={value: tuple(source_ids) for value, source_ids in values.items()},
            )
            for key, values in facts.items()
            if len(values) > 1
        ]

    @staticmethod
    def _deterministic_synthesis(
        sources: Sequence[ResearchSource], disagreements: Sequence[Disagreement]
    ) -> str:
        if not sources:
            return "No usable sources were available; the research workflow abstains."
        sections: list[str] = []
        if disagreements:
            sections.append(
                "Disagreement detected:\n"
                + "\n".join(
                    f"- {item.fact}: "
                    + "; ".join(
                        f"{value} [{', '.join(source_ids)}]"
                        for value, source_ids in item.values.items()
                    )
                    for item in disagreements
                )
            )
        sections.append(
            "Evidence:\n"
            + "\n\n".join(
                f"[{source.source_id}] {source.title}\n{source.content}" for source in sources
            )
        )
        return "\n\n".join(sections)

    @staticmethod
    def _verify_citations(answer: str, sources: Sequence[ResearchSource]) -> bool:
        return all(f"[{source.source_id}]" in answer for source in sources)


def source_id(provider: str, provenance: str) -> str:
    digest = hashlib.sha256(f"{provider}\0{provenance}".encode()).hexdigest()[:10]
    return f"src-{digest}"


def retrieved_at() -> str:
    return runtime_now().isoformat()
