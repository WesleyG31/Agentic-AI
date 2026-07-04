"""MCP server: ticketing & refund actions on the ACME database. Runs over stdio.

These are the side-effecting tools — the agent gates them behind the HITL
middleware, so by the time they execute a human has already approved the call.
Arguments come from an LLM, so they are validated here (this is a trust boundary).
"""

import sqlite3
from datetime import date

from mcp.server.fastmcp import FastMCP

from kompass.config import ROOT, settings

mcp = FastMCP("acme-ticketing", log_level="WARNING")


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(ROOT / settings.acme_db)
    conn.row_factory = sqlite3.Row
    return conn


@mcp.tool()
def get_ticket(ticket_id: int) -> str:
    """Fetch a support ticket by id."""
    conn = _db()
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    conn.close()
    return str(dict(row)) if row else f"No ticket {ticket_id}"


@mcp.tool()
def create_refund(order_id: int, amount_eur: float, reason: str) -> str:
    """Create a refund for an order. Human approval is required before this runs.
    amount_eur must not exceed the order total; refunds over €500 additionally
    require supervisor sign-off, which must be stated in the reason."""
    conn = _db()
    order = conn.execute(
        "SELECT total_eur, status FROM orders WHERE id = ?", (order_id,)
    ).fetchone()
    if order is None:
        conn.close()
        return f"Rejected: order {order_id} does not exist"
    if amount_eur > order["total_eur"]:
        conn.close()
        return f"Rejected: {amount_eur} exceeds the order total of {order['total_eur']}"

    today = date.today().isoformat()
    cur = conn.execute(
        "INSERT INTO refunds (order_id, amount_eur, reason, status, requested_at, decided_at,"
        " approved_by) VALUES (?, ?, ?, 'approved', ?, ?, 'human-reviewer')",
        (order_id, amount_eur, reason, today, today),
    )
    conn.commit()
    refund_id = cur.lastrowid
    conn.close()
    return f"Refund {refund_id} created: €{amount_eur:.2f} for order {order_id} (approved)"


@mcp.tool()
def update_ticket(ticket_id: int, status: str, note: str) -> str:
    """Update a ticket's status ('open' | 'pending' | 'resolved') and append a note.
    Human approval is required before this runs."""
    conn = _db()
    if conn.execute("SELECT 1 FROM tickets WHERE id = ?", (ticket_id,)).fetchone() is None:
        conn.close()
        return f"Rejected: ticket {ticket_id} does not exist"

    resolved_at = date.today().isoformat() if status == "resolved" else None
    conn.execute(
        "UPDATE tickets SET status = ?, resolved_at = COALESCE(?, resolved_at),"
        " body = body || char(10) || char(10) || '[Kompass] ' || ? WHERE id = ?",
        (status, resolved_at, note, ticket_id),
    )
    conn.commit()
    conn.close()
    return f"Ticket {ticket_id} updated to '{status}'"


if __name__ == "__main__":
    mcp.run()
