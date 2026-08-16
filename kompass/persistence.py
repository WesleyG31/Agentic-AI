"""Persistence ports for checkpoints and durable execution control.

SQLite is the deterministic single-process adapter. Setting a PostgreSQL DSN selects the
official LangGraph PostgreSQL saver when its optional package is installed. The execution
store records idempotency, cancellation requests, retry disposition, and dead letters.
"""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from kompass.config import ROOT, settings
from kompass.runtime import RuntimeContext, get_runtime_context
from kompass.security.audit import audit


@asynccontextmanager
async def checkpoint_saver() -> AsyncIterator[Any]:
    """Yield the configured LangGraph saver behind one application-owned boundary."""
    if settings.checkpoint_postgres_dsn:
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "PostgreSQL checkpointing requires the optional "
                "langgraph-checkpoint-postgres package"
            ) from exc
        async with AsyncPostgresSaver.from_conn_string(settings.checkpoint_postgres_dsn) as saver:
            await saver.setup()
            yield saver
        return
    path = ROOT / settings.sqlite_checkpoint
    path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        yield saver


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class ExecutionCapacityExceeded(RuntimeError):
    """Raised before execution when bounded local admission cannot accept work."""


class ExecutionLimiter:
    """Process-local concurrency and bounded-wait admission control."""

    def __init__(self, max_concurrency: int, queue_capacity: int) -> None:
        if max_concurrency < 1 or queue_capacity < 0:
            raise ValueError("execution concurrency must be positive and queue non-negative")
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._queue_capacity = queue_capacity
        self._guard = asyncio.Lock()
        self._waiting = 0

    @asynccontextmanager
    async def slot(self, *, wait_seconds: float) -> AsyncIterator[None]:
        queued = False
        async with self._guard:
            if self._semaphore.locked():
                if self._waiting >= self._queue_capacity:
                    raise ExecutionCapacityExceeded("execution queue capacity exceeded")
                self._waiting += 1
                queued = True
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=wait_seconds)
        except TimeoutError as exc:
            raise ExecutionCapacityExceeded("execution queue wait deadline exceeded") from exc
        finally:
            if queued:
                async with self._guard:
                    self._waiting -= 1
        try:
            yield
        finally:
            self._semaphore.release()


@dataclass(frozen=True)
class ExecutionRecord:
    execution_id: str
    tenant_id: str
    user_id: str
    status: ExecutionStatus
    attempt: int
    payload_hash: str
    last_error: str | None
    updated_at: str


class SQLiteExecutionStore:
    """Crash-visible local control plane; safe for one host, not a distributed queue."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or (ROOT / settings.execution_store_db))
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS executions ("
            "tenant_id TEXT NOT NULL, execution_id TEXT NOT NULL, user_id TEXT NOT NULL, "
            "status TEXT NOT NULL, attempt INTEGER NOT NULL, payload_hash TEXT NOT NULL, "
            "last_error TEXT, request_id TEXT NOT NULL, correlation_id TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, PRIMARY KEY(tenant_id, execution_id))"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_execution_failures "
            "ON executions(tenant_id, status, updated_at)"
        )
        return conn

    @staticmethod
    def payload_hash(payload: str | bytes) -> str:
        value = payload.encode() if isinstance(payload, str) else payload
        return hashlib.sha256(value).hexdigest()

    @staticmethod
    def _context() -> RuntimeContext:
        context = get_runtime_context(required=True)
        assert context is not None
        return context

    def register(self, execution_id: str, payload: str | bytes) -> bool:
        """Register once; replay with different input under the same key fails closed."""
        context = self._context()
        digest = self.payload_hash(payload)
        conn = self._db()
        row = conn.execute(
            "SELECT payload_hash FROM executions WHERE tenant_id = ? AND execution_id = ?",
            (context.tenant_id, execution_id),
        ).fetchone()
        if row is not None:
            conn.close()
            if row[0] != digest:
                raise ValueError("execution id was reused with a different payload")
            return False
        conn.execute(
            "INSERT INTO executions VALUES (?, ?, ?, ?, 0, ?, NULL, ?, ?, ?)",
            (
                context.tenant_id,
                execution_id,
                context.user_id.casefold(),
                ExecutionStatus.PENDING.value,
                digest,
                context.request_id,
                context.correlation_id,
                context.current_time.astimezone(UTC).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        return True

    def claim(self, execution_id: str) -> bool:
        """Atomically claim pending work; duplicate workers cannot both execute it."""
        context = self._context()
        conn = self._db()
        cursor = conn.execute(
            "UPDATE executions SET status = ?, attempt = attempt + 1, updated_at = ? "
            "WHERE tenant_id = ? AND execution_id = ? AND status = ?",
            (
                ExecutionStatus.RUNNING.value,
                context.current_time.astimezone(UTC).isoformat(),
                context.tenant_id,
                execution_id,
                ExecutionStatus.PENDING.value,
            ),
        )
        conn.commit()
        claimed = cursor.rowcount == 1
        conn.close()
        return claimed

    def request_cancel(self, execution_id: str) -> bool:
        context = self._context()
        conn = self._db()
        cursor = conn.execute(
            "UPDATE executions SET status = ?, updated_at = ? "
            "WHERE tenant_id = ? AND execution_id = ? AND status IN (?, ?)",
            (
                ExecutionStatus.CANCEL_REQUESTED.value,
                context.current_time.astimezone(UTC).isoformat(),
                context.tenant_id,
                execution_id,
                ExecutionStatus.PENDING.value,
                ExecutionStatus.RUNNING.value,
            ),
        )
        conn.commit()
        changed = cursor.rowcount == 1
        conn.close()
        return changed

    def recover_stale(self, *, lease_seconds: float) -> int:
        """Return abandoned running claims to pending after a worker lease expires."""
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        context = self._context()
        now = context.current_time.astimezone(UTC)
        cutoff = now - timedelta(seconds=lease_seconds)
        conn = self._db()
        cursor = conn.execute(
            "UPDATE executions SET status = ?, last_error = ?, updated_at = ? "
            "WHERE tenant_id = ? AND status = ? AND updated_at < ?",
            (
                ExecutionStatus.PENDING.value,
                "worker lease expired; eligible for bounded retry",
                now.isoformat(),
                context.tenant_id,
                ExecutionStatus.RUNNING.value,
                cutoff.isoformat(),
            ),
        )
        conn.commit()
        recovered = cursor.rowcount
        conn.close()
        return recovered

    def cancellation_requested(self, execution_id: str) -> bool:
        record = self.get(execution_id)
        return bool(record and record.status == ExecutionStatus.CANCEL_REQUESTED)

    def complete(self, execution_id: str) -> bool:
        return self._transition(execution_id, ExecutionStatus.COMPLETED, error=None)

    def fail(self, execution_id: str, error: Exception, *, retryable: bool) -> ExecutionStatus:
        context = self._context()
        record = self.get(execution_id)
        if record is None:
            raise KeyError(execution_id)
        if retryable and record.attempt < settings.execution_max_attempts:
            status = ExecutionStatus.PENDING
        elif retryable:
            status = ExecutionStatus.DEAD_LETTER
        else:
            status = ExecutionStatus.FAILED
        # Persist an error class, never arbitrary upstream text that may contain credentials.
        safe_error = f"{type(error).__name__}: execution failed"
        conn = self._db()
        conn.execute(
            "UPDATE executions SET status = ?, last_error = ?, updated_at = ? "
            "WHERE tenant_id = ? AND execution_id = ?",
            (
                status.value,
                safe_error,
                context.current_time.astimezone(UTC).isoformat(),
                context.tenant_id,
                execution_id,
            ),
        )
        conn.commit()
        conn.close()
        audit(
            "execution.failure",
            decision=status.value,
            reason=type(error).__name__,
            resource=f"execution:{execution_id}",
            action="retry" if status == ExecutionStatus.PENDING else "stop",
            metadata={"attempt": record.attempt, "retryable": retryable},
        )
        return status

    def get(self, execution_id: str) -> ExecutionRecord | None:
        context = self._context()
        conn = self._db()
        row = conn.execute(
            "SELECT execution_id, tenant_id, user_id, status, attempt, payload_hash, "
            "last_error, updated_at FROM executions WHERE tenant_id = ? AND execution_id = ?",
            (context.tenant_id, execution_id),
        ).fetchone()
        conn.close()
        return ExecutionRecord(
            row[0], row[1], row[2], ExecutionStatus(row[3]), row[4], row[5], row[6], row[7]
        ) if row else None

    def failures(self) -> list[ExecutionRecord]:
        context = self._context()
        conn = self._db()
        rows = conn.execute(
            "SELECT execution_id, tenant_id, user_id, status, attempt, payload_hash, "
            "last_error, updated_at FROM executions WHERE tenant_id = ? AND status IN (?, ?) "
            "ORDER BY updated_at",
            (
                context.tenant_id,
                ExecutionStatus.FAILED.value,
                ExecutionStatus.DEAD_LETTER.value,
            ),
        ).fetchall()
        conn.close()
        return [
            ExecutionRecord(
                row[0], row[1], row[2], ExecutionStatus(row[3]), row[4], row[5], row[6], row[7]
            )
            for row in rows
        ]

    def _transition(
        self, execution_id: str, status: ExecutionStatus, *, error: str | None
    ) -> bool:
        context = self._context()
        conn = self._db()
        cursor = conn.execute(
            "UPDATE executions SET status = ?, last_error = ?, updated_at = ? "
            "WHERE tenant_id = ? AND execution_id = ? AND status = ?",
            (
                status.value,
                error,
                context.current_time.astimezone(UTC).isoformat(),
                context.tenant_id,
                execution_id,
                ExecutionStatus.RUNNING.value,
            ),
        )
        conn.commit()
        changed = cursor.rowcount == 1
        conn.close()
        return changed
