"""Saga / compensation runner: make a multi-step action all-or-nothing.

A sequence like create-refund -> resolve-ticket -> notify-customer touches several
systems with no shared transaction. The saga pattern gives every forward step a
compensating action; if a later step fails, the runner rolls the completed steps
back in reverse order. This is pure orchestration -- no LLM, no agent state -- and
is the reliability pattern for agent actions that write to more than one system.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import BaseModel

from kompass.config import ROOT, settings


@dataclass
class Step:
    """One forward action (`do`) paired with the compensation that undoes it (`undo`)."""

    name: str
    do: Callable[[], Any]
    undo: Callable[[], None]


class SagaResult(BaseModel):
    """Outcome of a saga run: what completed, what was rolled back, and any error."""

    ok: bool
    completed: list[str]
    compensated: list[str]
    error: str | None = None


def run_saga(steps: list[Step]) -> SagaResult:
    """Run each step's `do()` in order; on the first failure, compensate in reverse.

    A `do()` raising is the expected failure path: it is caught, the already-completed
    steps are rolled back via their `undo()` in reverse order, and ok=False is returned
    with the error. An `undo()` raising is a failed compensation -- a real incident -- so
    it is left to propagate rather than swallowed. On full success ok=True.
    """
    completed: list[Step] = []
    try:
        for step in steps:
            step.do()
            completed.append(step)
    except Exception as err:
        compensated: list[str] = []
        for step in reversed(completed):
            step.undo()
            compensated.append(step.name)
        return SagaResult(
            ok=False,
            completed=[s.name for s in completed],
            compensated=compensated,
            error=str(err),
        )
    return SagaResult(ok=True, completed=[s.name for s in completed], compensated=[])


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(ROOT / settings.acme_db)
    conn.row_factory = sqlite3.Row
    return conn


def refund_saga(
    order_id: int, amount: float, ticket_id: int, notify_ok: bool = True
) -> list[Step]:
    """Build the ACME refund saga over the SQLite DB.

    Steps: (1) insert an approved refund row -- compensation deletes it; (2) mark the
    ticket 'resolved' -- compensation restores its prior status; (3) notify the customer
    -- forced to fail when `notify_ok` is False, which triggers rollback of steps 1-2.
    Per-step results (the new refund id, the ticket's prior status) are threaded to their
    compensations through the shared `state` dict, since `undo` takes no arguments.
    """
    state: dict = {}

    def create_refund_do() -> int:
        conn = _db()
        today = date.today().isoformat()
        cur = conn.execute(
            "INSERT INTO refunds (order_id, amount_eur, reason, status, requested_at,"
            " decided_at, approved_by) VALUES (?, ?, ?, 'approved', ?, ?, 'saga-runner')",
            (order_id, amount, "Order arrived damaged; full refund via saga.", today, today),
        )
        conn.commit()
        state["refund_id"] = cur.lastrowid
        conn.close()
        return state["refund_id"]

    def create_refund_undo() -> None:
        conn = _db()
        conn.execute("DELETE FROM refunds WHERE id = ?", (state["refund_id"],))
        conn.commit()
        conn.close()

    def resolve_ticket_do() -> None:
        conn = _db()
        row = conn.execute(
            "SELECT status, resolved_at FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        state["prev_status"] = row["status"]
        state["prev_resolved_at"] = row["resolved_at"]
        conn.execute(
            "UPDATE tickets SET status = 'resolved', resolved_at = ? WHERE id = ?",
            (date.today().isoformat(), ticket_id),
        )
        conn.commit()
        conn.close()

    def resolve_ticket_undo() -> None:
        conn = _db()
        conn.execute(
            "UPDATE tickets SET status = ?, resolved_at = ? WHERE id = ?",
            (state["prev_status"], state["prev_resolved_at"], ticket_id),
        )
        conn.commit()
        conn.close()

    def notify_customer_do() -> None:
        if not notify_ok:
            raise RuntimeError("notification gateway timed out")

    def notify_customer_undo() -> None:
        """Retract a sent notification. As the final step it is never compensated here,
        but a real pipeline would send a correction notice."""

    return [
        Step("create_refund", create_refund_do, create_refund_undo),
        Step("resolve_ticket", resolve_ticket_do, resolve_ticket_undo),
        Step("notify_customer", notify_customer_do, notify_customer_undo),
    ]


if __name__ == "__main__":
    from kompass.scripts.seed import build_db

    build_db()

    result = run_saga(refund_saga(4471, 189.99, 88012, notify_ok=False))
    print("SagaResult:", result.model_dump())

    conn = _db()
    refunds = conn.execute("SELECT id, status FROM refunds WHERE order_id = 4471").fetchall()
    ticket = conn.execute("SELECT status FROM tickets WHERE id = 88012").fetchone()
    conn.close()

    print(f"refunds for order 4471 after rollback: {[dict(r) for r in refunds]}")
    print(f"ticket 88012 status after rollback: {ticket['status']!r}")
