"""Proactive trigger: inbound ticket webhooks are triaged by the agent unattended.

A standalone FastAPI app. POST /webhook/ticket files the ticket in the DB, has
the read-only Researcher classify it and draft a grounded, cited reply, then
appends a "[Kompass triage]" note to the ticket and parks it as 'pending' for
a human agent. The agent takes NO gated actions on this surface — only the
read-only research worker runs, and the DB insert/update are direct SQL
plumbing — so the HITL invariant is preserved: nothing side-effecting runs
unattended.

Run:  python -m kompass.triggers.webhook   (port from KOMPASS_TRIGGER_PORT)
"""

import sqlite3
from datetime import date
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field

from kompass.config import ROOT, settings
from kompass.graph.workers import research
from kompass.models.router import pick

TRIAGE_PROMPT = """An inbound support ticket just arrived. Triage it:
1. Classify it as exactly one of: question, refund_request, order_issue, other.
2. Draft a reply to the customer, grounded in the policy/FAQ documents and order
   data, with inline citations.
3. State whether a human agent needs to act on it (an action to execute, an
   escalation, or facts you could not verify).

From: {email}
Subject: {subject}

{body}"""

app = FastAPI(title="Kompass Triggers")


class TicketIn(BaseModel):
    """Inbound webhook payload — a trust boundary, hence the validation."""

    customer_email: str
    subject: str
    body: str


class Triage(BaseModel):
    """Structured triage fields extracted from the researcher's analysis."""

    classification: Literal["question", "refund_request", "order_issue", "other"]
    needs_human: bool = Field(description="a human agent must act on this ticket")
    draft: str = Field(description="the draft customer reply, verbatim, citations included")


@app.post("/webhook/ticket")
async def ticket_webhook(ticket: TicketIn) -> dict:
    """File the ticket, triage it with the read-only Researcher, park it as pending."""
    today = date.today().isoformat()
    conn = sqlite3.connect(ROOT / settings.acme_db)
    with conn:
        ticket_id = conn.execute("SELECT max(id) + 1 FROM tickets").fetchone()[0]
        conn.execute(
            "INSERT INTO tickets (id, customer_email, subject, body, status, priority, created_at)"
            " VALUES (?, ?, ?, ?, 'open', 'medium', ?)",
            (ticket_id, ticket.customer_email, ticket.subject, ticket.body, today),
        )

    question = TRIAGE_PROMPT.format(
        email=ticket.customer_email, subject=ticket.subject, body=ticket.body
    )
    analysis = await research.ainvoke({"question": question})
    triage = await (
        pick("fast")
        .with_structured_output(Triage)
        .ainvoke(f"Extract the triage fields from this support-ticket analysis:\n\n{analysis}")
    )

    with conn:
        conn.execute(
            "UPDATE tickets SET body = body || ?, status = 'pending' WHERE id = ?",
            (f"\n\n[Kompass triage] {triage.classification}: {triage.draft}", ticket_id),
        )
    conn.close()

    return {
        "ticket_id": ticket_id,
        "classification": triage.classification,
        "needs_human": triage.needs_human,
        "draft": triage.draft,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.trigger_port)
