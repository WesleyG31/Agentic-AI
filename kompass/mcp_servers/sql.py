"""MCP server: read-only SQL plus an explicit schema resource."""

from mcp.server import MCPServer

from kompass.retrieval.nl2sql import SCHEMA, run_sql

mcp = MCPServer("acme-sql", log_level="WARNING")


@mcp.tool()
def get_schema() -> str:
    """The ACME database schema (orders, tickets, employees, refunds)."""
    return SCHEMA


@mcp.tool()
def query_database(sql: str) -> str:
    """Run ONE read-only SELECT against the ACME database (orders, order_items,
    tickets, employees, refunds). Returns rows as a list of dicts, capped at 50."""
    rows = run_sql(sql)
    return f"{len(rows)} row(s): {rows}"


@mcp.resource("kompass://schema/acme", mime_type="text/plain")
def schema_resource() -> str:
    """The operational schema exposed through the MCP resources capability."""
    return SCHEMA


if __name__ == "__main__":
    mcp.run()
