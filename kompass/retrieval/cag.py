"""Cache-augmented generation: ship the whole corpus in the prompt.

The ACME policy/FAQ corpus is small and stable — exactly the CAG sweet spot.
The full text rides as one static prompt block; the provider's prompt caching
makes repeat queries hit a cached prefix instead of a retrieval pipeline.
"""

from functools import lru_cache

from kompass.config import ROOT

CORPUS = ROOT / "corpus"


@lru_cache
def full_corpus() -> str:
    """The entire policy + FAQ corpus as one string, each doc tagged with its path."""
    docs = sorted(CORPUS.glob("policies/*.md")) + sorted(CORPUS.glob("faq/*.md"))
    return "\n\n".join(
        f"<document source='{d.parent.name}/{d.name}'>\n"
        f"{d.read_text(encoding='utf-8')}\n</document>"
        for d in docs
    )
