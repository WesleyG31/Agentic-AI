import json
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from kompass.api import app as api_module
from kompass.security.oidc import OIDCAuthenticationError, OIDCConfig, OIDCVerifier

ISSUER = "https://identity.test/realms/kompass"
AUDIENCE = "kompass-api"
KID = "test-key"


def _material():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    return private_key, jwk


def _verifier(jwk, *, discovery_issuer: str = ISSUER) -> OIDCVerifier:
    def metadata(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": discovery_issuer,
                    "jwks_uri": f"{ISSUER}/certs",
                    "id_token_signing_alg_values_supported": ["RS256"],
                },
            )
        return httpx.Response(200, json={"keys": [jwk]})

    return OIDCVerifier(
        OIDCConfig(issuer=ISSUER, audience=AUDIENCE, clock_skew_seconds=0),
        transport=httpx.MockTransport(metadata),
    )


def _token(private_key, **changes) -> str:
    now = datetime.now(UTC)
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "alice",
        "tenant_id": "tenant-a",
        "permissions": ["documents:read", "refunds:create"],
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(changes)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": KID})


async def test_oidc_discovery_jwks_and_claims_produce_principal():
    private_key, jwk = _material()
    principal = await _verifier(jwk).verify(_token(private_key))

    assert principal.subject == "alice"
    assert principal.tenant_id == "tenant-a"
    assert principal.scopes == frozenset({"documents:read", "refunds:create"})
    assert principal.issuer == ISSUER


@pytest.mark.parametrize(
    "changes",
    [
        {"aud": "wrong-api"},
        {"iss": "https://attacker.invalid"},
        {"exp": datetime.now(UTC) - timedelta(minutes=2)},
        {"tenant_id": ""},
    ],
)
async def test_oidc_rejects_invalid_audience_issuer_expiry_and_claims(changes):
    private_key, jwk = _material()
    with pytest.raises(OIDCAuthenticationError, match="invalid bearer token"):
        await _verifier(jwk).verify(_token(private_key, **changes))


async def test_oidc_rejects_tampered_token_and_discovery_issuer():
    private_key, jwk = _material()
    token = _token(private_key)
    parts = token.split(".")
    replacement = "A" if parts[2][0] != "A" else "B"
    parts[2] = replacement + parts[2][1:]
    with pytest.raises(OIDCAuthenticationError):
        await _verifier(jwk).verify(".".join(parts))
    with pytest.raises(OIDCAuthenticationError, match="issuer mismatch"):
        await _verifier(jwk, discovery_issuer="https://attacker.invalid").verify(token)


def test_ingress_requires_bearer_and_sets_security_headers(monkeypatch):
    monkeypatch.setattr(api_module.settings, "auth_mode", "oidc")
    response = TestClient(api_module.app).get("/runs/missing")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.json() == {"detail": "authentication failed"}


def test_ingress_rejects_oversized_body_before_authentication(monkeypatch):
    monkeypatch.setattr(api_module.settings, "auth_mode", "oidc")
    monkeypatch.setattr(api_module.settings, "max_request_bytes", 10)
    response = TestClient(api_module.app).post(
        "/chat", content=b"{}", headers={"Content-Length": "11"}
    )

    assert response.status_code == 413
    assert response.headers["cache-control"] == "no-store"
