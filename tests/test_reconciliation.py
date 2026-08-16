import sqlite3

from kompass.actions.executor import (
    ActionRequest,
    ApprovalState,
    SQLiteReceiptStore,
)
from kompass.actions.reconciliation import reconcile_receipts
from kompass.mcp_servers.ticketing import create_refund
from kompass.runtime import RuntimeContext, runtime_scope
from kompass.scripts.seed import build_db
from kompass.security.identity import Principal


def _request(key: str) -> ActionRequest:
    return ActionRequest(
        name="create_refund",
        arguments={"order_id": 4471, "amount_eur": 189.99, "reason": "damaged"},
        idempotency_key=key,
        correlation_id="reconcile-test",
        tenant_id="tenant-a",
        principal=Principal(
            "support-1", "tenant-a", scopes=frozenset({"refunds:create"})
        ),
        approval=ApprovalState.APPROVED,
    )


def test_reconciliation_detects_committed_effect_and_is_repeatable(tmp_path, monkeypatch):
    business_db = tmp_path / "acme.db"
    build_db(business_db)
    monkeypatch.setattr("kompass.actions.reconciliation.settings.acme_db", str(business_db))
    monkeypatch.setattr("kompass.mcp_servers.ticketing.settings.acme_db", str(business_db))
    store = SQLiteReceiptStore(str(tmp_path / "receipts.db"))
    context = RuntimeContext.create(tenant_id="tenant-a", user_id="support-1")
    request = _request("partial-commit")

    with runtime_scope(context):
        acquired, _ = store.claim(request, authorization_reason="policy allowed")
        assert acquired
        result = create_refund(**request.arguments, idempotency_key=request.idempotency_key)
        assert "created" in result
        report = reconcile_receipts(store, "tenant-a", older_than_seconds=0)
        repeated = reconcile_receipts(store, "tenant-a", older_than_seconds=0)

    assert report.already_applied == 1
    assert report.failed == 0
    assert repeated.inspected == 0
    with sqlite3.connect(business_db) as conn:
        count = conn.execute(
            "SELECT count(*) FROM refunds WHERE idempotency_key = ?", (request.idempotency_key,)
        ).fetchone()[0]
    assert count == 1


def test_reconciliation_safely_repairs_missing_effect(tmp_path, monkeypatch):
    business_db = tmp_path / "acme.db"
    build_db(business_db)
    monkeypatch.setattr("kompass.actions.reconciliation.settings.acme_db", str(business_db))
    monkeypatch.setattr("kompass.mcp_servers.ticketing.settings.acme_db", str(business_db))
    store = SQLiteReceiptStore(str(tmp_path / "receipts.db"))
    context = RuntimeContext.create(tenant_id="tenant-a", user_id="support-1")
    request = _request("repair-missing")

    with runtime_scope(context):
        store.claim(request, authorization_reason="policy allowed")
        report = reconcile_receipts(store, "tenant-a", older_than_seconds=0)
        receipt = store.get("tenant-a", request.idempotency_key)

    assert report.repaired == 1
    assert report.failed == 0
    assert receipt is not None and receipt.verified
