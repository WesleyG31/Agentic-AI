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
from langchain_core.messages import ToolMessage

from kompass.config import ROOT, settings
from kompass.retrieval.chroma import local_chroma_settings

# v2 leaves the previous collection intact but makes its ungrounded entries
# unreachable. Bump this schema when cache correctness rules materially change.
COLLECTION = "answer_cache_v2"
CACHE_SCHEMA_VERSION = "2"
_CACHEABLE_TOOLS = {"search_docs"}
_UNRESOLVED_MARKERS = (
    "no encontré",
    "no encontre",
    "no tengo información",
    "no tengo informacion",
    "no hay información",
    "no hay informacion",
    "could not find",
    "couldn't find",
    "no information",
    "not documented",
)


def _collection() -> chromadb.Collection:
    client = chromadb.PersistentClient(
        path=str(ROOT / settings.chroma_path),
        settings=local_chroma_settings(),
    )
    return client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})


def lookup(question: str, *, tenant_id: str, threshold: float = 0.2) -> str | None:
    """Return a cached answer for a semantically-equivalent question, or None.

    `threshold` is a cosine distance (0 = identical); only matches at or below it hit.
    Kept tight on purpose: distinct-but-similar questions (e.g. express vs standard
    shipping times) must NOT collide, since they have different correct answers. A miss
    just recomputes — the safe failure mode — so we favour precision over recall here.
    """
    col = _collection()
    if col.count() == 0:
        return None
    hit = col.query(query_texts=[question], n_results=1, where={"tenant_id": tenant_id})
    if not hit["ids"][0]:
        return None
    if hit["distances"][0][0] <= threshold:
        return hit["metadatas"][0][0]["answer"]
    return None


def store(question: str, answer: str, *, tenant_id: str) -> None:
    """Cache a READ-only answer keyed by its question. Caller guarantees no state change."""
    _collection().add(
        ids=[uuid4().hex],
        documents=[question],
        metadatas=[
            {
                "answer": answer,
                "schema_version": CACHE_SCHEMA_VERSION,
                "tenant_id": tenant_id,
            }
        ],
    )


def can_store(messages: list, answer: str) -> bool:
    """Only cache grounded answers backed exclusively by immutable documents.

    SQL results can become stale, action results must never be replayed, and
    abstentions should be retried after retrieval improvements instead of becoming
    durable false negatives.
    """
    tool_names = {
        message.name
        for message in messages
        if isinstance(message, ToolMessage) and getattr(message, "name", None)
    }
    normalized_answer = answer.casefold()
    return (
        bool(tool_names)
        and tool_names <= _CACHEABLE_TOOLS
        and not any(marker in normalized_answer for marker in _UNRESOLVED_MARKERS)
    )


def clear() -> None:
    """Drop the cache if it exists (used by tests and demos)."""
    client = chromadb.PersistentClient(
        path=str(ROOT / settings.chroma_path),
        settings=local_chroma_settings(),
    )
    if any(
        (collection if isinstance(collection, str) else collection.name) == COLLECTION
        for collection in client.list_collections()
    ):
        client.delete_collection(COLLECTION)
