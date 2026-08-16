"""Hybrid retrieval: dense (Chroma) + lexical (BM25), fused with reciprocal rank fusion.

Dense search catches paraphrases ("time off" → vacation policy); BM25 catches exact
strings (order ids, error codes, "€500"). RRF combines both rankings without tuning.
"""

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

import chromadb
from rank_bm25 import BM25Okapi

from kompass.config import ROOT, settings

COLLECTION = "acme_docs"

# The corpus is authored in English, while interviewers commonly exercise the UI
# in Spanish. Expand common domain terms before dense and lexical retrieval so
# correctness does not depend entirely on the agent remembering to translate.
_SPANISH_TO_ENGLISH = {
    "ano": "year annual",
    "anual": "annual year",
    "contrasena": "password",
    "cuantos": "how many",
    "devolucion": "return refund",
    "devoluciones": "returns refunds",
    "dias": "days",
    "empleado": "employee",
    "empleados": "employees",
    "envio": "shipping delivery",
    "factura": "invoice receipt",
    "gastos": "expenses",
    "pedido": "order",
    "pedidos": "orders",
    "politica": "policy",
    "reembolso": "refund",
    "reembolsos": "refunds",
    "restantes": "remaining",
    "seguridad": "security",
    "ticket": "ticket",
    "vacacion": "vacation leave",
    "vacaciones": "vacation annual leave entitlement",
}
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
    normalized = unicodedata.normalize("NFKD", text.lower())
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.findall(r"[a-z0-9€]+", ascii_text)


def _expand_query(query: str) -> str:
    """Add English domain equivalents for Spanish terms in the English corpus."""
    translations = [
        _SPANISH_TO_ENGLISH[token]
        for token in _tokenize(query)
        if token in _SPANISH_TO_ENGLISH
    ]
    return " ".join([query, *translations]) if translations else query


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
    retrieval_query = _expand_query(query)

    dense = col.query(query_texts=[retrieval_query], n_results=min(10, len(ids)))
    dense_rank = {cid: r for r, cid in enumerate(dense["ids"][0])}

    bm25_scores = bm25.get_scores(_tokenize(retrieval_query))
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
