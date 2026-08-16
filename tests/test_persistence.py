import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from kompass import persistence
from kompass.runtime import FrozenClock, RuntimeContext, runtime_scope


def _context(tenant: str = "tenant-a") -> RuntimeContext:
    return RuntimeContext.create(
        tenant_id=tenant,
        user_id="worker",
        clock=FrozenClock(datetime(2026, 8, 16, 12, tzinfo=UTC)),
    )


async def test_execution_limiter_rejects_when_queue_is_full():
    limiter = persistence.ExecutionLimiter(max_concurrency=1, queue_capacity=0)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def occupy():
        async with limiter.slot(wait_seconds=1):
            entered.set()
            await release.wait()

    task = asyncio.create_task(occupy())
    await entered.wait()
    with pytest.raises(persistence.ExecutionCapacityExceeded, match="capacity"):
        async with limiter.slot(wait_seconds=1):
            pass
    release.set()
    await task


def test_execution_claim_is_idempotent_and_payload_bound(tmp_path):
    store = persistence.SQLiteExecutionStore(tmp_path / "executions.db")
    with runtime_scope(_context()):
        assert store.register("job-1", "same payload")
        assert not store.register("job-1", "same payload")
        with pytest.raises(ValueError, match="different payload"):
            store.register("job-1", "changed payload")
        assert store.claim("job-1")
        assert not store.claim("job-1")
        assert store.complete("job-1")


def test_cancellation_and_failure_records_are_tenant_scoped(tmp_path, monkeypatch):
    store = persistence.SQLiteExecutionStore(tmp_path / "executions.db")
    monkeypatch.setattr(persistence.settings, "execution_max_attempts", 1)
    with runtime_scope(_context("tenant-a")):
        store.register("cancel-me", "payload")
        assert store.request_cancel("cancel-me")
        assert store.cancellation_requested("cancel-me")
        store.register("fail-me", "payload")
        assert store.claim("fail-me")
        assert (
            store.fail("fail-me", TimeoutError("deadline"), retryable=True)
            == persistence.ExecutionStatus.DEAD_LETTER
        )
        assert [record.execution_id for record in store.failures()] == ["fail-me"]

    with runtime_scope(_context("tenant-b")):
        assert store.get("fail-me") is None
        assert store.failures() == []
        assert store.register("fail-me", "different tenant payload")


def test_stale_worker_claim_can_be_recovered_for_retry(tmp_path):
    store = persistence.SQLiteExecutionStore(tmp_path / "executions.db")
    first = _context()
    with runtime_scope(first):
        store.register("stale", "payload")
        assert store.claim("stale")

    later = RuntimeContext.create(
        tenant_id="tenant-a",
        user_id="worker",
        clock=FrozenClock(first.current_time + timedelta(minutes=5)),
    )
    with runtime_scope(later):
        assert store.recover_stale(lease_seconds=60) == 1
        assert store.claim("stale")
