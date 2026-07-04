"""Build the reproducible ACME demo data: the SQLite database and the Chroma vector index.

Run from the repo root:  python -m kompass.scripts.seed
"""

import re
import sqlite3
from pathlib import Path

import chromadb

from kompass.config import ROOT, settings

CORPUS = ROOT / "corpus"
COLLECTION = "acme_docs"


def build_db(db_path: str | Path | None = None) -> dict[str, int]:
    """Create the ACME SQLite database from corpus/sql/seed.sql. Returns row counts per table."""
    path = Path(db_path) if db_path else ROOT / settings.acme_db
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript((CORPUS / "sql" / "seed.sql").read_text(encoding="utf-8"))
    conn.commit()
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}
    conn.close()
    return counts


def chunk(doc: Path) -> list[tuple[str, str]]:
    """Split a markdown document into (section_title, text) pairs, one per '## ' heading.

    The intro (H1 title + metadata line) is kept as its own chunk so document-level
    queries ("what policies exist?") still hit something.
    """
    head, *sections = re.split(r"\n(?=## )", doc.read_text(encoding="utf-8"))
    title = head.splitlines()[0].lstrip("# ").strip()
    return [(title, head)] + [(s.splitlines()[0].lstrip("# ").strip(), s) for s in sections]


def build_index(chroma_path: str | Path | None = None) -> int:
    """Index all corpus markdown into a fresh Chroma collection. Returns the chunk count."""
    client = chromadb.PersistentClient(path=str(chroma_path or settings.chroma_path))
    if any(c.name == COLLECTION for c in client.list_collections()):
        client.delete_collection(COLLECTION)
    collection = client.create_collection(COLLECTION)

    ids, texts, metas = [], [], []
    for doc in sorted(CORPUS.glob("policies/*.md")) + sorted(CORPUS.glob("faq/*.md")):
        for i, (section, text) in enumerate(chunk(doc)):
            ids.append(f"{doc.stem}::{i}")
            texts.append(text)
            metas.append({"source": f"{doc.parent.name}/{doc.name}", "section": section})
    collection.add(ids=ids, documents=texts, metadatas=metas)
    return len(ids)


def main() -> None:
    counts = build_db()
    print(f"SQLite  {settings.acme_db}:", ", ".join(f"{t}={n}" for t, n in sorted(counts.items())))
    n = build_index()
    print(f"Chroma  {settings.chroma_path}: {n} chunks in '{COLLECTION}'")


if __name__ == "__main__":
    main()
