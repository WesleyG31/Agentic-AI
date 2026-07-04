"""GraphRAG tests — load the cached concept graph without hitting an LLM.

The graph is an LLM-built artifact cached to corpus/graph.json; build it once with
graphrag.build_graph() (done in the verification step). These tests only load that cache,
so they stay fast and API-key-free. They skip if the cache has not been built yet.
"""

import json

import networkx as nx
import pytest

from kompass.retrieval import graphrag


@pytest.fixture(scope="module")
def graph() -> nx.DiGraph:
    if not graphrag.GRAPH_PATH.exists():
        pytest.skip("corpus/graph.json not built — run graphrag.build_graph() first")
    return graphrag._graph()


def test_graph_loads_with_nodes_and_edges(graph):
    assert isinstance(graph, nx.DiGraph)
    assert graph.number_of_nodes() > 0
    assert graph.number_of_edges() > 0


def test_edges_carry_relation_and_source(graph):
    _, _, data = next(iter(graph.edges(data=True)))
    assert data["relation"]
    assert data["source"].endswith(".md")


def test_cache_spans_multiple_documents(graph):
    sources = {d["source"] for _, _, d in graph.edges(data=True)}
    assert len(sources) >= 2


def test_graph_json_holds_triples():
    if not graphrag.GRAPH_PATH.exists():
        pytest.skip("corpus/graph.json not built — run graphrag.build_graph() first")
    rows = json.loads(graphrag.GRAPH_PATH.read_text(encoding="utf-8"))
    assert rows
    assert set(rows[0]) == {"source", "subject", "relation", "object"}
