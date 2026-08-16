"""Validated, target-system-idempotent ticketing and refund MCP tools."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from mcp.server import MCPServer

from kompass.config import ROOT, settings
from kompass.runtime import runtime_date

mcp = MCPServer("acme-ticketing", log_level="WARNING")


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(ROOT / settings.acme_db, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _effect_hash(action: str, arguments: dict[str, Any]) -> str:
    encoded = json.dumps(
        {"action": action, "arguments": arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _begin_effect(
    conn: sqlite3.Connection,
    idempotency_key: str,
    action: str,
    arguments: dict[str, Any],
) -> tuple[str, str | None]:
    """Lock, bind key to payload, and return an already committed result on replay."""
    if not idempotency_key:
        return "", None
    if len(idempotency_key) > 256:
        raise ValueError("idempotency key is too long")
    digest = _effect_hash(action, arguments)
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        "SELECT action, payload_hash, result FROM business_effects "
        "WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    if row is None:
        return digest, None
    if row["action"] != action or row["payload_hash"] != digest:
        raise ValueError("idempotency key was reused with a different payload")
    return digest, str(row["result"])


def _finish_effect(
    conn: sqlite3.Connection,
    idempotency_key: str,
    action: str,
    digest: str,
    result: str,
) -> None:
    if idempotency_key:
        conn.execute(
            "INSERT INTO business_effects "
            "(idempotency_key, action, payload_hash, result, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (idempotency_key, action, digest, result, runtime_date()),
        )
    conn.commit()


@mcp.tool()
def get_ticket(ticket_id: int) -> str:
    """Fetch a support ticket by id."""
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        return str(dict(row)) if row else f"No ticket {ticket_id}"
    finally:
        conn.close()


@mcp.tool()
def create_refund(
    order_id: int,
    amount_eur: float,
    reason: str,
    idempotency_key: str = "",
) -> str:
    """Create one refund after approval; retries with the same key return one result."""
    conn = _db()
    arguments = {"order_id": order_id, "amount_eur": amount_eur, "reason": reason}
    try:
        digest, existing = _begin_effect(
            conn, idempotency_key, "create_refund", arguments
        )
        if existing is not None:
            conn.rollback()
            return existing
        order = conn.execute(
            "SELECT total_eur, status FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        if order is None:
            result = f"Rejected: order {order_id} does not exist"
        elif amount_eur <= 0:
            result = "Rejected: refund amount must be positive"
        elif amount_eur > order["total_eur"]:
            result = (
                f"Rejected: {amount_eur} exceeds the order total of {order['total_eur']}"
            )
        else:
            today = runtime_date()
            cursor = conn.execute(
                "INSERT INTO refunds (order_id, amount_eur, reason, status, requested_at, "
                "decided_at, approved_by, idempotency_key) "
                "VALUES (?, ?, ?, 'approved', ?, ?, 'human-reviewer', NULLIF(?, ''))",
                (order_id, amount_eur, reason, today, today, idempotency_key),
            )
            result = (
                f"Refund {cursor.lastrowid} created: €{amount_eur:.2f} "
                f"for order {order_id} (approved)"
            )
        _finish_effect(conn, idempotency_key, "create_refund", digest, result)
        return result
    except ValueError as exc:
        conn.rollback()
        return f"Rejected: {exc}"
    finally:
        conn.close()


@mcp.tool()
def update_ticket(
    ticket_id: int,
    status: str,
    note: str,
    idempotency_key: str = "",
) -> str:
    """Update one ticket; target-side idempotency prevents duplicate note appends."""
    conn = _db()
    arguments = {"ticket_id": ticket_id, "status": status, "note": note}
    try:
        digest, existing = _begin_effect(
            conn, idempotency_key, "update_ticket", arguments
        )
        if existing is not None:
            conn.rollback()
            return existing
        if conn.execute(
            "SELECT 1 FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone() is None:
            result = f"Rejected: ticket {ticket_id} does not exist"
        elif status not in {"open", "pending", "resolved"}:
            result = f"Rejected: invalid ticket status {status!r}"
        elif not note.strip() or len(note) > 4_000:
            result = "Rejected: ticket note must contain 1-4000 characters"
        else:
            resolved_at = runtime_date() if status == "resolved" else None
            conn.execute(
                "UPDATE tickets SET status = ?, resolved_at = COALESCE(?, resolved_at),"
                " body = body || char(10) || char(10) || '[Kompass] ' || ? WHERE id = ?",
                (status, resolved_at, note, ticket_id),
            )
            result = f"Ticket {ticket_id} updated to '{status}'"
        _finish_effect(conn, idempotency_key, "update_ticket", digest, result)
        return result
    except ValueError as exc:
        conn.rollback()
        return f"Rejected: {exc}"
    finally:
        conn.close()


if __name__ == "__main__":
    mcp.run()
