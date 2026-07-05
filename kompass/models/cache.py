"""Semantic answer cache: skip the model when a paraphrase was already answered.

A dedicated Chroma collection (cosine space, same embedder as the corpus index) maps
past questions to their answers; a new question that embeds close enough to a stored one
returns the cached answer with no LLM call. The threshold is deliberately tight so only
genuine paraphrases hit.

Correctness rule: only READ-only answers may be cached — never an answer that involved an
action or any DB state change, since that state moves. The caller decides when to `store`.
"""

from uuid import uuid4

import chromadb

from kompass.config import ROOT, settings

COLLECTION = "answer_cache"


def _collection() -> chromadb.Collection:
    client = chromadb.PersistentClient(path=str(ROOT / settings.chroma_path))
    return client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})


def lookup(question: str, threshold: float = 0.2) -> str | None:
    """Return a cached answer for a semantically-equivalent question, or None.

    `threshold` is a cosine distance (0 = identical); only matches at or below it hit.
    Kept tight on purpose: distinct-but-similar questions (e.g. express vs standard
    shipping times) must NOT collide, since they have different correct answers. A miss
    just recomputes — the safe failure mode — so we favour precision over recall here.
    """
    col = _collection()
    if col.count() == 0:
        return None
    hit = col.query(query_texts=[question], n_results=1)
    if hit["distances"][0][0] <= threshold:
        return hit["metadatas"][0][0]["answer"]
    return None


def store(question: str, answer: str) -> None:
    """Cache a READ-only answer keyed by its question. Caller guarantees no state change."""
    _collection().add(ids=[uuid4().hex], documents=[question], metadatas=[{"answer": answer}])


def clear() -> None:
    """Drop the cache if it exists (used by tests and demos)."""
    client = chromadb.PersistentClient(path=str(ROOT / settings.chroma_path))
    if any(c.name == COLLECTION for c in client.list_collections()):
        client.delete_collection(COLLECTION)
