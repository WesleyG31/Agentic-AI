import asyncio
import os
import selectors
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import TypedDict

import psycopg
import pytest
from langgraph.graph import END, START, StateGraph

from kompass import persistence
from kompass.actions.executor import (
    ActionExecutor,
    ActionRequest,
    ActionStatus,
    ApprovalState,
)
from kompass.actions.postgres import PostgresReceiptStore
from kompass.runtime import RuntimeContext, runtime_scope, tenant_scoped_id
from kompass.security.identity import LocalPolicyEngine, Principal

ADMIN_DSN = os.getenv("KOMPASS_TEST_POSTGRES_ADMIN_DSN", "")
APP_DSN = os.getenv("KOMPASS_TEST_POSTGRES_DSN", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not ADMIN_DSN or not APP_DSN,
        reason="local PostgreSQL integration DSNs are not configured",
    ),
]


def _context(tenant: str) -> RuntimeContext:
    return RuntimeContext.create(tenant_id=tenant, user_id="integration-user")


def _request(tenant: str, key: str) -> ActionRequest:
    return ActionRequest(
        name="create_refund",
        arguments={"order_id": 4471, "amount_eur": 10.0},
        idempotency_key=key,
        correlation_id="postgres-integration",
        tenant_id=tenant,
        principal=Principal(
            "integration-user", tenant, scopes=frozenset({"refunds:create"})
        ),
        approval=ApprovalState.APPROVED,
    )


def _cleanup(prefix: str = "it-") -> None:
    with psycopg.connect(ADMIN_DSN) as conn:
        conn.execute(
            "DELETE FROM action_receipts WHERE idempotency_key LIKE %s", (f"{prefix}%",)
        )
        conn.execute(
            "DELETE FROM workflow_threads WHERE user_id = 'integration-user'"
        )


def test_migration_enables_force_rls_and_exact_app_role():
    with psycopg.connect(ADMIN_DSN) as conn:
        tables = conn.execute(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname IN ('action_receipts', 'workflow_threads') ORDER BY relname"
        ).fetchall()
        role = conn.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolbypassrls "
            "FROM pg_roles WHERE rolname = 'kompass_app'"
        ).fetchone()
        policies = conn.execute(
            "SELECT tablename, policyname FROM pg_policies "
            "WHERE tablename IN ('action_receipts', 'workflow_threads') ORDER BY tablename"
        ).fetchall()

    assert tables == [
        ("action_receipts", True, True),
        ("workflow_threads", True, True),
    ]
    assert role == (False, False, False, False, False)
    assert policies == [
        ("action_receipts", "action_receipts_tenant"),
        ("workflow_threads", "workflow_threads_tenant"),
    ]


def test_rls_allows_own_rows_denies_cross_tenant_and_requires_context():
    _cleanup()
    store = PostgresReceiptStore(APP_DSN)
    try:
        for tenant in ("tenant-a", "tenant-b"):
            with runtime_scope(_context(tenant)):
                request = _request(tenant, "it-same-key")
                acquired, receipt = store.claim(request, authorization_reason="policy allowed")
                assert acquired and receipt.tenant_id == tenant

        with psycopg.connect(APP_DSN) as conn, conn.transaction():
            conn.execute("SELECT set_config('app.current_tenant', 'tenant-a', true)")
            visible = conn.execute(
                "SELECT tenant_id FROM action_receipts "
                "WHERE idempotency_key = 'it-same-key' ORDER BY tenant_id"
            ).fetchall()
            assert visible == [("tenant-a",)]
            assert conn.execute(
                "SELECT count(*) FROM action_receipts WHERE tenant_id = 'tenant-b'"
            ).fetchone()[0] == 0

        with (
            psycopg.connect(APP_DSN) as conn,
            pytest.raises(psycopg.errors.InsufficientPrivilege),
        ):
            conn.execute("SELECT count(*) FROM action_receipts").fetchone()

        with psycopg.connect(APP_DSN) as conn:
            conn.execute("SELECT set_config('app.current_tenant', 'tenant-a', false)")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "INSERT INTO action_receipts "
                    "(tenant_id, idempotency_key, action, request_hash, status) "
                    "VALUES ('tenant-b', 'it-forbidden', 'create_refund', %s, 'processing')",
                    ("0" * 64,),
                )

        with psycopg.connect(ADMIN_DSN) as conn:
            assert conn.execute(
                "SELECT count(*) FROM action_receipts WHERE idempotency_key = 'it-same-key'"
            ).fetchone()[0] == 2
    finally:
        _cleanup()


def test_postgres_atomic_claim_prevents_concurrent_duplicate_effects():
    _cleanup()
    executor = ActionExecutor(LocalPolicyEngine(), PostgresReceiptStore(APP_DSN))
    entered = Event()
    release = Event()
    calls: list[str] = []

    def run():
        with runtime_scope(_context("tenant-a")):
            return executor.execute(
                _request("tenant-a", "it-concurrent"),
                effect,
                verifier=lambda _: True,
            )

    def effect():
        calls.append("effect")
        entered.set()
        release.wait(timeout=5)
        return {"refund_id": 1}

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(run)
            assert entered.wait(timeout=5)
            second = pool.submit(run)
            duplicate = second.result(timeout=5)
            release.set()
            completed = first.result(timeout=5)
        assert completed.status == ActionStatus.SUCCEEDED
        assert duplicate.duplicate
        assert calls == ["effect"]
    finally:
        release.set()
        _cleanup()


class CounterState(TypedDict):
    value: int


def test_official_postgres_checkpoint_setup_and_tenant_keys(monkeypatch):
    monkeypatch.setattr(persistence.settings, "checkpoint_postgres_dsn", ADMIN_DSN)
    context_a = _context("tenant-a")
    context_b = _context("tenant-b")
    key_a = tenant_scoped_id(context_a, "it-checkpoint")
    key_b = tenant_scoped_id(context_b, "it-checkpoint")
    builder = StateGraph(CounterState)
    builder.add_node("increment", lambda state: {"value": state["value"] + 1})
    builder.add_edge(START, "increment")
    builder.add_edge("increment", END)

    async def exercise():
        async with persistence.checkpoint_saver() as saver:
            graph = builder.compile(checkpointer=saver)
            config_a = {"configurable": {"thread_id": key_a}}
            config_b = {"configurable": {"thread_id": key_b}}
            result = await graph.ainvoke({"value": 1}, config_a)
            other = await graph.aget_state(config_b)
            return result, other

    with asyncio.Runner(
        loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
    ) as runner:
        result, other = runner.run(exercise())

    assert result["value"] == 2
    assert other.values == {}
    assert key_a != key_b
