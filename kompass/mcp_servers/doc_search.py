"""MCP server: hybrid search over ACME's policy/FAQ corpus. Runs over stdio."""

from mcp.server.fastmcp import FastMCP

from kompass.retrieval import rag

mcp = FastMCP("acme-doc-search", log_level="WARNING")


@mcp.tool()
def search_docs(query: str, k: int = 4) -> str:
    """Search ACME's policies and FAQs (hybrid semantic + keyword). Returns the top
    matching sections, each prefixed with its citation tag — cite these in answers."""
    chunks = rag.search(query, k=k)
    return "\n\n".join(f"{c.citation}\n{c.text}" for c in chunks)


if __name__ == "__main__":
    mcp.run()
