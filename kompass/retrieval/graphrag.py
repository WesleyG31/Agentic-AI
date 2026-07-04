"""GraphRAG: multi-hop retrieval over a concept graph built from the policy/FAQ corpus.

Plain chunk similarity answers single-section questions well but fumbles relational,
multi-hop ones — "for a damaged item over €500, what's the refund process, who approves
it, and the payout timeline?" chains a damaged-item rule, a €500 approval threshold, a
refund method, and a payout window that live in different sections and documents. We
extract a subject-relation-object graph (entities = policies, rules, thresholds, roles,
deadlines; edges = requires / approved_by / within / cross_references) once with an LLM,
cache it to corpus/graph.json (reproducible, no cost on later runs), then answer by
pulling the relevant subgraph and grounding it in the cited source sections.
"""

import json
from functools import lru_cache

import networkx as nx
from pydantic import BaseModel

from kompass.config import ROOT
from kompass.models.router import pick
from kompass.retrieval import rag

GRAPH_PATH = ROOT / "corpus" / "graph.json"

EXTRACT = """Extract a concept graph from one ACME GmbH policy/FAQ document as \
subject-relation-object triples. Entities are policies, rules, monetary thresholds, \
roles, and deadlines; relations are short labels like requires, approved_by, within, \
refunded_to, applies_to, cross_references. Keep entity names short and canonical \
(e.g. "refund over €500", "supervisor", "5-10 business days", "damaged item"). Emit \
every relation the document states; prefer many precise triples over few vague ones."""

QUERY_ENTITIES = """List the key entities in this question — the policies, rules, \
monetary thresholds, roles, and deadlines it asks about — as short noun phrases."""


class Triple(BaseModel):
    subject: str
    relation: str
    object: str


class Triples(BaseModel):
    """All subject-relation-object triples extracted from one document."""

    triples: list[Triple]


class Entities(BaseModel):
    """The key entities named in a query."""

    entities: list[str]


def _build_from_rows(rows: list[dict]) -> nx.DiGraph:
    """Assemble a DiGraph from cached triple rows; each edge carries its relation + source."""
    g = nx.DiGraph()
    for r in rows:
        g.add_edge(r["subject"], r["object"], relation=r["relation"], source=r["source"])
    return g


def build_graph() -> nx.DiGraph:
    """Extract triples from the corpus in one LLM pass, cache to graph.json, return the graph."""
    corpus = ROOT / "corpus"
    docs = sorted(corpus.glob("policies/*.md")) + sorted(corpus.glob("faq/*.md"))
    model = pick("balanced").with_structured_output(Triples)
    rows: list[dict] = []
    for d in docs:
        source = f"{d.parent.name}/{d.name}"
        out: Triples = model.invoke([("system", EXTRACT), ("user", d.read_text(encoding="utf-8"))])
        rows += [
            {"source": source, "subject": t.subject, "relation": t.relation, "object": t.object}
            for t in out.triples
        ]
    GRAPH_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    _graph.cache_clear()
    return _build_from_rows(rows)


@lru_cache
def _graph() -> nx.DiGraph:
    """Load the cached concept graph, building it once if graph.json is missing."""
    if not GRAPH_PATH.exists():
        build_graph()
    return _build_from_rows(json.loads(GRAPH_PATH.read_text(encoding="utf-8")))


def search(question: str, k: int = 4) -> str:
    """Answer a multi-hop question from the concept graph.

    A fast LLM names the query's key entities; we match them to graph nodes
    (case-insensitive substring), pull the radius-2 neighborhood subgraph, and return its
    triples plus the grounding source sections (fetched via rag.search) tagged with
    citations — the same context shape the other strategies produce.
    """
    g = _graph()
    named: Entities = (
        pick("fast")
        .with_structured_output(Entities)
        .invoke([("system", QUERY_ENTITIES), ("user", question)])
    )
    seeds = list(
        dict.fromkeys(
            n
            for n in g.nodes
            for e in named.entities
            if e.lower() in n.lower() or n.lower() in e.lower()
        )
    )

    # Walk the undirected view so relations chain in both directions (radius 2 = multi-hop).
    ug = g.to_undirected()
    reach: set[str] = set(seeds)
    for s in seeds:
        reach |= set(nx.single_source_shortest_path_length(ug, s, cutoff=2))

    sub = g.subgraph(reach)
    triples = "\n".join(
        f"{u} --{d['relation']}--> {v}   (from {d['source']})" for u, v, d in sub.edges(data=True)
    )
    sources = sorted({d["source"] for _, _, d in sub.edges(data=True)})
    grounding = "\n\n".join(f"{c.citation}\n{c.text}" for c in rag.search(question, k=k))

    return (
        f"Concept subgraph ({sub.number_of_edges()} relations across {len(sources)} documents "
        f"{sources}):\n{triples}\n\nGrounding sections:\n{grounding}"
    )
