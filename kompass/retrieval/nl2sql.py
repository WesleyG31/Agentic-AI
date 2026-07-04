"""Structured retrieval: read-only SQL over the ACME database.

The LLM (agent or router) writes the SQL; this module is the trust boundary that
executes it safely: read-only connection, single statement, hard row cap.
"""

import sqlite3

from kompass.config import ROOT, settings

ROW_CAP = 50

SCHEMA = """\
employees(id, name, email, department, hire_date, vacation_days_total, vacation_days_used)
orders(id, customer_name, customer_email, order_date, delivery_date, status, total_eur, notes)
  -- status: processing|shipped|delivered|returned|cancelled
order_items(order_id, product, qty, unit_price_eur)
tickets(id, customer_email, subject, body, status, priority, created_at, resolved_at, order_id)
  -- status: open|pending|resolved; priority: low|medium|high
refunds(id, order_id, amount_eur, reason, status, requested_at, decided_at, approved_by)
  -- status: requested|approved|rejected|completed"""


def run_sql(sql: str) -> list[dict]:
    """Execute one SELECT against the ACME DB (read-only) and return rows as dicts."""
    if not sql.lstrip().lower().startswith("select"):
        raise ValueError("only SELECT statements are allowed")
    uri = f"file:{(ROOT / settings.acme_db).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql).fetchmany(ROW_CAP)
    conn.close()
    return [dict(r) for r in rows]
