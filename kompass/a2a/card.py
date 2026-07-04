"""Agent Card: Kompass's public A2A identity — who it is, where it listens,
what it can do. Peers fetch it from /.well-known/agent.json before delegating.

Integrity is an HMAC-SHA256 signature over the card's canonical JSON (sorted
keys, no whitespace) with the shared ``a2a_secret`` — stdlib-only, good enough
for a demo between trusted parties. Production would publish a JWS signature
with an asymmetric key so peers can verify without holding the secret.
"""

import hashlib
import hmac
import json

from kompass import __version__
from kompass.config import settings


def agent_card() -> dict:
    """The unsigned Agent Card, A2A-spec-shaped."""
    return {
        "name": "kompass-researcher",
        "description": (
            "Specialist research agent for ACME GmbH: answers policy/FAQ and "
            "operational-data questions with inline citations."
        ),
        "url": f"http://localhost:{settings.a2a_port}/a2a",
        "version": __version__,
        "capabilities": {"streaming": False},
        "skills": [
            {
                "id": "acme-research",
                "name": "ACME research",
                "description": (
                    "Cited answers over ACME's policies and FAQs plus its operational "
                    "database (orders, order_items, tickets, employees, refunds)."
                ),
                "tags": ["research", "rag", "sql", "citations"],
            }
        ],
    }


def sign(card: dict) -> str:
    """HMAC-SHA256 hex digest over the card's canonical JSON."""
    canonical = json.dumps(card, sort_keys=True, separators=(",", ":"))
    return hmac.new(settings.a2a_secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()


def verify(card: dict, signature: str) -> bool:
    """Constant-time check that ``signature`` was produced over ``card``."""
    return hmac.compare_digest(sign(card), signature)
