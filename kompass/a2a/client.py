"""A2A client: discover a peer agent by its signed card, then delegate a task.

Demo CLI (server must be running, see kompass/a2a/server.py):

    python -m kompass.a2a.client "What is ACME's refund window for damaged items?"
"""

import sys
from uuid import uuid4

import httpx

from kompass.a2a.card import verify
from kompass.config import settings


def discover(base_url: str) -> dict:
    """Fetch the peer's Agent Card and verify its signature; raise on mismatch."""
    card = httpx.get(f"{base_url}/.well-known/agent.json").raise_for_status().json()
    signature = card.pop("signature", "")
    if not verify(card, signature):
        raise ValueError(f"agent card signature mismatch for {base_url}")
    return card


def send_task(base_url: str, question: str) -> str:
    """JSON-RPC ``tasks/send`` round trip; returns the first artifact's text."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tasks/send",
        "params": {
            "id": uuid4().hex,
            "message": {"role": "user", "parts": [{"text": question}]},
        },
    }
    resp = httpx.post(f"{base_url}/a2a", json=payload, timeout=120).raise_for_status().json()
    if "error" in resp:
        raise RuntimeError(f"A2A error {resp['error']['code']}: {resp['error']['message']}")
    return resp["result"]["artifacts"][0]["parts"][0]["text"]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    base_url = f"http://localhost:{settings.a2a_port}"
    question = " ".join(sys.argv[1:]) or "What is ACME's refund window for damaged items?"
    card = discover(base_url)
    print(f"discovered {card['name']} v{card['version']} — skill: {card['skills'][0]['id']}\n")
    print(send_task(base_url, question))
