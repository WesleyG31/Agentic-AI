"""Idempotent reconciliation for action receipts that were left in processing."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from kompass.actions.executor import ActionReceipt, ActionStatus, ReceiptStore
from kompass.config import ROOT, settings
from kompass.mcp_servers.ticketing import create_refund, update_ticket
from kompass.security.audit import audit

_REFUND_ID = re.compile(r"Refund\s+(\d+)\s+created", re.I)


@dataclass(frozen=True)
class ReconciliationReport:
    tenant_id: str
    inspected: int
    already_applied: int
    repaired: int
    failed: int


def _business_effect(idempotency_key: str) -> tuple[str, str, str] | None:
    conn = sqlite3.connect(ROOT / settings.acme_db)
    try:
        row = conn.execute(
            "SELECT action, payload_hash, result FROM business_effects "
            "WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return (str(row[0]), str(row[1]), str(row[2])) if row else None
    finally:
        conn.close()


def _verify_target(receipt: ActionReceipt, result: str) -> bool:
    arguments = receipt.request_payload or {}
    conn = sqlite3.connect(ROOT / settings.acme_db)
    conn.row_factory = sqlite3.Row
    try:
        if receipt.action == "create_refund":
            match = _REFUND_ID.search(result)
            if not match:
                return False
            row = conn.execute(
                "SELECT order_id, amount_eur, status, idempotency_key FROM refunds WHERE id = ?",
                (int(match.group(1)),),
            ).fetchone()
            return bool(
                row
                and row["order_id"] == int(arguments["order_id"])
                and abs(row["amount_eur"] - float(arguments["amount_eur"])) < 0.001
                and row["status"] in {"approved", "completed"}
                and row["idempotency_key"] == receipt.idempotency_key
            )
        if receipt.action == "update_ticket":
            row = conn.execute(
                "SELECT status FROM tickets WHERE id = ?", (int(arguments["ticket_id"]),)
            ).fetchone()
            return bool(row and row["status"] == arguments["status"])
        return False
    finally:
        conn.close()


def _repair(receipt: ActionReceipt) -> str:
    arguments: dict[str, Any] = receipt.request_payload or {}
    if receipt.action == "create_refund":
        return create_refund(
            order_id=int(arguments["order_id"]),
            amount_eur=float(arguments["amount_eur"]),
            reason=str(arguments["reason"]),
            idempotency_key=receipt.idempotency_key,
        )
    if receipt.action == "update_ticket":
        return update_ticket(
            ticket_id=int(arguments["ticket_id"]),
            status=str(arguments["status"]),
            note=str(arguments["note"]),
            idempotency_key=receipt.idempotency_key,
        )
    raise ValueError("action has no reconciliation handler")


def reconcile_receipts(
    store: ReceiptStore,
    tenant_id: str,
    *,
    older_than_seconds: float = 60.0,
    repair: bool = True,
) -> ReconciliationReport:
    """Verify or safely replay stale target-idempotent effects; repeat runs are no-ops."""
    if older_than_seconds < 0:
        raise ValueError("older_than_seconds cannot be negative")
    before = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
    receipts = store.reconcilable(tenant_id, before=before)
    already_applied = repaired = failed = 0
    for receipt in receipts:
        effect = _business_effect(receipt.idempotency_key)
        result: str | None = None
        was_applied = effect is not None
        if effect is not None:
            action, payload_hash, result = effect
            if action != receipt.action or payload_hash != receipt.request_hash:
                result = None
        elif repair:
            try:
                result = _repair(receipt)
            except (KeyError, TypeError, ValueError):
                result = None
        verified = bool(result is not None and _verify_target(receipt, result))
        if verified:
            status = ActionStatus.SUCCEEDED
            already_applied += int(was_applied)
            repaired += int(not was_applied)
            error = None
        else:
            status = ActionStatus.VERIFICATION_FAILED
            failed += 1
            error = "reconciliation could not verify target state"
        updated = ActionReceipt(
            **{
                **receipt.__dict__,
                "status": status,
                "attempts": receipt.attempts + int(not was_applied and repair),
                "result": result,
                "verified": verified,
                "error": error,
                "updated_at": datetime.now(UTC).isoformat(),
                "duplicate": False,
            }
        )
        store.put(updated)
        audit(
            "action.reconciliation",
            decision=status.value,
            reason=error or ("target already applied" if was_applied else "safe replay"),
            resource=f"action:{receipt.idempotency_key}",
            action=receipt.action,
            metadata={"repair": repair, "already_applied": was_applied},
        )
    return ReconciliationReport(
        tenant_id=tenant_id,
        inspected=len(receipts),
        already_applied=already_applied,
        repaired=repaired,
        failed=failed,
    )
