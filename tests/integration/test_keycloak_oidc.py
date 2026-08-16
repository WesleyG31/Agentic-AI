import os

import httpx
import pytest
from fastapi.testclient import TestClient

from kompass.api import app as api_module
from kompass.security.oidc import OIDCAuthenticationError, OIDCConfig, OIDCVerifier

ISSUER = os.getenv("KOMPASS_TEST_OIDC_ISSUER", "")
CONNECT_BASE = os.getenv("KOMPASS_TEST_OIDC_CONNECT_BASE", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not ISSUER or not CONNECT_BASE,
        reason="local Keycloak integration endpoints are not configured",
    ),
]


async def _token(username: str, password: str) -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{CONNECT_BASE}/realms/kompass/protocol/openid-connect/token",
            data={
                "client_id": "kompass-api",
                "username": username,
                "password": password,
                "grant_type": "password",
            },
        )
        response.raise_for_status()
        return str(response.json()["access_token"])


def _verifier(audience: str = "kompass-api") -> OIDCVerifier:
    return OIDCVerifier(
        OIDCConfig(
            issuer=ISSUER,
            audience=audience,
            discovery_url=(
                f"{CONNECT_BASE}/realms/kompass/.well-known/openid-configuration"
            ),
            jwks_url=f"{CONNECT_BASE}/realms/kompass/protocol/openid-connect/certs",
            allow_http=True,
        )
    )


async def test_real_keycloak_discovery_jwks_tenant_and_permissions():
    alice = await _verifier().verify(await _token("alice", "local-alice-password"))
    bob = await _verifier().verify(await _token("bob", "local-bob-password"))

    assert alice.tenant_id == "tenant-a"
    assert "refunds:create" in alice.scopes
    assert bob.tenant_id == "tenant-b"
    assert "refunds:create" not in bob.scopes


async def test_real_keycloak_rejects_bad_credentials_and_wrong_audience():
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{CONNECT_BASE}/realms/kompass/protocol/openid-connect/token",
            data={
                "client_id": "kompass-api",
                "username": "alice",
                "password": "wrong",
                "grant_type": "password",
            },
        )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"

    token = await _token("alice", "local-alice-password")
    with pytest.raises(OIDCAuthenticationError):
        await _verifier("wrong-api").verify(token)


async def test_real_token_cannot_override_tenant_claim(monkeypatch):
    token = await _token("alice", "local-alice-password")
    monkeypatch.setattr(api_module.settings, "auth_mode", "oidc")
    monkeypatch.setattr(api_module.settings, "oidc_issuer_url", ISSUER)
    monkeypatch.setattr(
        api_module.settings,
        "oidc_discovery_url",
        f"{CONNECT_BASE}/realms/kompass/.well-known/openid-configuration",
    )
    monkeypatch.setattr(
        api_module.settings,
        "oidc_jwks_url",
        f"{CONNECT_BASE}/realms/kompass/protocol/openid-connect/certs",
    )
    monkeypatch.setattr(api_module.settings, "oidc_audience", "kompass-api")
    monkeypatch.setattr(api_module.settings, "environment", "development")
    api_module._oidc_verifier.cache_clear()

    response = TestClient(api_module.app).post(
        "/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": "hello", "tenant_id": "tenant-b"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "tenant claim mismatch"}
