"""MCP server: hybrid search plus governed policy/FAQ resources."""

from pathlib import Path

from mcp.server import MCPServer

from kompass.config import ROOT
from kompass.retrieval import rag

mcp = MCPServer("acme-doc-search", log_level="WARNING")


@mcp.tool()
def search_docs(query: str, k: int = 4) -> str:
    """Search ACME's policies and FAQs (hybrid semantic + keyword). Returns the top
    matching sections, each prefixed with its citation tag — cite these in answers.
    The corpus is English: translate non-English questions into concise English search
    terms. Spanish domain terms are also expanded automatically as a fallback."""
    chunks = rag.search(query, k=k)
    return "\n\n".join(f"{c.citation}\n{c.text}" for c in chunks)


@mcp.resource("kompass://document/{name}", mime_type="text/markdown")
def read_document(name: str) -> str:
    """Read one allow-listed corpus document by filename."""
    allowed = {
        path.name: path
        for folder in (ROOT / "corpus" / "policies", ROOT / "corpus" / "faq")
        for path in folder.glob("*.md")
    }
    path: Path | None = allowed.get(name)
    if path is None:
        raise ValueError(f"unknown document resource: {name}")
    return path.read_text(encoding="utf-8")


if __name__ == "__main__":
    mcp.run()
