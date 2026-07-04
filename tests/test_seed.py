"""Slice-2 tests: the ACME database builds from seed.sql with consistent demo data."""

import sqlite3

from kompass.scripts.seed import build_db


def test_build_db(tmp_path):
    db = tmp_path / "acme.db"
    counts = build_db(db)
    assert counts["employees"] == 6
    assert counts["orders"] == 12
    assert counts["tickets"] == 8
    assert counts["refunds"] == 2

    conn = sqlite3.connect(db)

    # Demo journey A: Jonas Weber has 17 vacation days left.
    total, used = conn.execute(
        "SELECT vacation_days_total, vacation_days_used FROM employees WHERE id = 'emp-1001'"
    ).fetchone()
    assert total - used == 17

    # Demo journey B: order 4471 is delivered, damaged, €189.99, with an open high-prio ticket.
    status, total_eur = conn.execute(
        "SELECT status, total_eur FROM orders WHERE id = 4471"
    ).fetchone()
    assert (status, total_eur) == ("delivered", 189.99)
    ticket_status, priority = conn.execute(
        "SELECT status, priority FROM tickets WHERE id = 88012"
    ).fetchone()
    assert (ticket_status, priority) == ("open", "high")

    # Data integrity: every order's total matches the sum of its item lines.
    mismatched = conn.execute(
        """
        SELECT o.id FROM orders o JOIN order_items i ON i.order_id = o.id
        GROUP BY o.id HAVING abs(o.total_eur - sum(i.qty * i.unit_price_eur)) > 0.001
        """
    ).fetchall()
    assert mismatched == []
