import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from kompass.api import app as api_module
from kompass.triggers import webhook


async def test_run_inspection_fails_closed_in_oidc_mode(monkeypatch):
    monkeypatch.setattr(api_module.settings, "auth_mode", "oidc")
    with pytest.raises(HTTPException) as raised:
        await api_module.get_run("thread-1")
    assert raised.value.status_code == 503


def test_webhook_is_disabled_without_token(monkeypatch):
    monkeypatch.setattr(webhook.settings, "trigger_bearer_token", "")
    with TestClient(webhook.app) as client:
        response = client.post(
            "/webhook/ticket",
            json={"customer_email": "a@example.com", "subject": "Help", "body": "Question"},
        )
    assert response.status_code == 503


def test_webhook_rejects_invalid_token_before_processing(monkeypatch):
    monkeypatch.setattr(webhook.settings, "trigger_bearer_token", "expected")
    with TestClient(webhook.app) as client:
        response = client.post(
            "/webhook/ticket",
            headers={"Authorization": "Bearer wrong"},
            json={"customer_email": "a@example.com", "subject": "Help", "body": "Question"},
        )
    assert response.status_code == 401
