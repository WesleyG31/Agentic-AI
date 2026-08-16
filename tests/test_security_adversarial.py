import sqlite3

import pytest
from fastapi.testclient import TestClient

from kompass.api import app as api_module
from kompass.mcp_servers.ticketing import create_refund, get_ticket
from kompass.retrieval.nl2sql import run_sql
from kompass.scripts.seed import build_db


def test_ticket_identifier_sql_injection_is_bound_as_data(tmp_path, monkeypatch):
    database = tmp_path / "acme.db"
    build_db(database)
    monkeypatch.setattr("kompass.mcp_servers.ticketing.settings.acme_db", str(database))

    result = get_ticket("88012 OR 1=1")

    assert result == "No ticket 88012 OR 1=1"
    assert "customer_email" not in result


def test_refund_text_sql_injection_does_not_change_schema(tmp_path, monkeypatch):
    database = tmp_path / "acme.db"
    build_db(database)
    monkeypatch.setattr("kompass.mcp_servers.ticketing.settings.acme_db", str(database))
    payload = "damage'); DROP TABLE refunds; --"

    result = create_refund(4471, 10.0, payload, idempotency_key="security-sql-text")

    assert "created" in result
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT count(*) FROM refunds").fetchone()[0] == 3


def test_generated_sql_rejects_writes_and_stacked_statements(tmp_path, monkeypatch):
    database = tmp_path / "acme.db"
    build_db(database)
    monkeypatch.setattr("kompass.retrieval.nl2sql.settings.acme_db", str(database))

    with pytest.raises(ValueError, match="only SELECT"):
        run_sql("ATTACH DATABASE 'other.db' AS stolen")
    with pytest.raises(ValueError, match="exactly one"):
        run_sql("SELECT 1; DROP TABLE tickets")
    assert run_sql("SELECT count(*) AS n FROM tickets;") == [{"n": 8}]


def test_invalid_payload_xss_and_security_headers_are_bounded(monkeypatch):
    monkeypatch.setattr(api_module.settings, "auth_mode", "local")
    client = TestClient(api_module.app)
    invalid = client.post("/chat", json={"message": ""})
    health = client.get("/health", params={"value": "<script>alert(1)</script>"})

    assert invalid.status_code == 422
    assert health.status_code == 200
    assert health.headers["content-type"].startswith("application/json")
    assert health.headers["content-security-policy"].startswith("default-src 'none'")
    assert "<script>" not in health.text
