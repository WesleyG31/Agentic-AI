"""Adaptive retrieval: classify the query, dispatch the cheapest sufficient strategy.

One fast-model call decides the route — and already writes the SQL when the answer
lives in the database. This is the programmatic entry point used by evals and the
baseline; inside the agent the same trade-off is made by the model choosing tools.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from kompass.models.router import pick
from kompass.retrieval import cag, graphrag, rag
from kompass.retrieval.nl2sql import SCHEMA, run_sql

CLASSIFY = f"""Route a query over ACME GmbH's knowledge to a retrieval strategy:
- sql: facts about specific orders, tickets, employees, refunds, or aggregates. \
Write ONE SQLite SELECT for this schema (dataset "today" is 2026-07-04):
{SCHEMA}
- rag: a specific question answered by a policy/FAQ section (rules, prices, deadlines).
- graph: multi-hop/relational questions spanning multiple policies or roles — \
process + approver + timeline chained together (e.g. "for a damaged item over €500, \
what's the refund process, who approves it, and the payout timeline?").
- cag: broad or multi-document questions ("summarize all policies", comparisons)."""


class Route(BaseModel):
    """Retrieval routing decision."""

    strategy: Literal["sql", "rag", "graph", "cag"]
    sql: str | None = Field(default=None, description="the SELECT statement, iff strategy=sql")


@dataclass
class RetrievalResult:
    strategy: str
    context: str
    citations: list[str] = field(default_factory=list)


def retrieve(query: str, k: int = 4) -> RetrievalResult:
    """Classify the query and return grounded context ready for synthesis."""
    route: Route = (
        pick("fast")
        .with_structured_output(Route)
        .invoke([("system", CLASSIFY), ("user", query)])
    )

    if route.strategy == "sql":
        rows = run_sql(route.sql)
        return RetrievalResult(
            strategy="sql",
            context=f"SQL: {route.sql}\nRows ({len(rows)}): {rows}",
            citations=[f"acme.db ({route.sql})"],
        )
    if route.strategy == "rag":
        chunks = rag.search(query, k=k)
        return RetrievalResult(
            strategy="rag",
            context="\n\n".join(f"{c.citation}\n{c.text}" for c in chunks),
            citations=[c.citation for c in chunks],
        )
    if route.strategy == "graph":
        context = graphrag.search(query, k=k)
        return RetrievalResult(
            strategy="graph",
            context=context,
            citations=list(dict.fromkeys(re.findall(r"\[[^\]]+\]", context))),
        )
    return RetrievalResult(
        strategy="cag",
        context=cag.full_corpus(),
        citations=["corpus (full, cache-augmented)"],
    )
