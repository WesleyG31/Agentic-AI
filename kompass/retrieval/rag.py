"""Hybrid retrieval: dense (Chroma) + lexical (BM25), fused with reciprocal rank fusion.

Dense search catches paraphrases ("time off" → vacation policy); BM25 catches exact
strings (order ids, error codes, "€500"). RRF combines both rankings without tuning.
"""

import re
from dataclasses import dataclass
from functools import lru_cache

import chromadb
from rank_bm25 import BM25Okapi

from kompass.config import ROOT, settings

COLLECTION = "acme_docs"
RRF_K = 60  # standard damping constant; rank 0 contributes 1/60, rank 9 → 1/69


@dataclass
class Chunk:
    id: str
    text: str
    source: str
    section: str
    score: float

    @property
    def citation(self) -> str:
        return f"[{self.source} § {self.section}]"


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9€]+", text.lower())


@lru_cache
def _index() -> tuple[chromadb.Collection, BM25Okapi, list[str]]:
    """Load the Chroma collection once and build the BM25 index over the same chunks."""
    client = chromadb.PersistentClient(path=str(ROOT / settings.chroma_path))
    col = client.get_collection(COLLECTION)
    data = col.get()
    bm25 = BM25Okapi([_tokenize(d) for d in data["documents"]])
    return col, bm25, data["ids"]


def search(query: str, k: int = 4) -> list[Chunk]:
    """Return the top-k chunks for a query, hybrid-ranked (dense + BM25 via RRF)."""
    col, bm25, ids = _index()

    dense = col.query(query_texts=[query], n_results=min(10, len(ids)))
    dense_rank = {cid: r for r, cid in enumerate(dense["ids"][0])}

    bm25_scores = bm25.get_scores(_tokenize(query))
    bm25_rank = {
        ids[i]: r
        for r, i in enumerate(sorted(range(len(ids)), key=lambda i: -bm25_scores[i])[:10])
    }

    fused = sorted(
        set(dense_rank) | set(bm25_rank),
        key=lambda cid: -(
            (1 / (RRF_K + dense_rank[cid]) if cid in dense_rank else 0)
            + (1 / (RRF_K + bm25_rank[cid]) if cid in bm25_rank else 0)
        ),
    )[:k]

    docs = col.get(ids=fused)
    by_id = {
        cid: (doc, meta)
        for cid, doc, meta in zip(docs["ids"], docs["documents"], docs["metadatas"], strict=True)
    }
    return [
        Chunk(
            id=cid,
            text=by_id[cid][0],
            source=by_id[cid][1]["source"],
            section=by_id[cid][1]["section"],
            score=round(
                (1 / (RRF_K + dense_rank[cid]) if cid in dense_rank else 0)
                + (1 / (RRF_K + bm25_rank[cid]) if cid in bm25_rank else 0),
                5,
            ),
        )
        for cid in fused
    ]
