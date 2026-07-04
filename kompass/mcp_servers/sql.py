"""MCP server: read-only SQL access to the ACME operational database. Runs over stdio."""

from mcp.server.fastmcp import FastMCP

from kompass.retrieval.nl2sql import SCHEMA, run_sql

mcp = FastMCP("acme-sql", log_level="WARNING")


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


if __name__ == "__main__":
    mcp.run()
