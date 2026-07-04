"""A2A agent card: sign/verify round-trip and tamper detection. No server, no LLM."""

from kompass.a2a.card import agent_card, sign, verify


def test_card_sign_verify_and_tamper():
    card = agent_card()
    signature = sign(card)
    assert card["name"] == "kompass-researcher"
    assert card["skills"][0]["id"] == "acme-research"
    assert verify(card, signature)
    assert not verify({**card, "url": "http://evil.example/a2a"}, signature)
