"""The baseline Kompass is measured against: naïve single-shot RAG.

Dense-only top-4 retrieval + one LLM call. No hybrid ranking, no SQL access,
no actions, no citation contract, no abstention discipline — the typical
"RAG in a trench coat" demo. The eval table's delta column is this gap.
"""

from functools import lru_cache

import chromadb

from kompass.config import ROOT, settings
from kompass.models.router import pick

PROMPT = """Answer the user's question using the context below.

Context:
{context}

Question: {question}"""


@lru_cache
def _collection() -> chromadb.Collection:
    # One shared client: chromadb's client creation is not thread-safe.
    return chromadb.PersistentClient(path=str(ROOT / settings.chroma_path)).get_collection(
        "acme_docs"
    )


def answer(question: str) -> str:
    hits = _collection().query(query_texts=[question], n_results=4)
    context = "\n\n".join(hits["documents"][0])
    return pick("balanced").invoke(PROMPT.format(context=context, question=question)).content
