"""Reliable execution boundary for externally visible side effects."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from threading import RLock
from time import monotonic
from typing import Any, Protocol

from kompass.security.audit import audit
from kompass.security.identity import AuthorizationRequest, PolicyEngine, Principal


class ActionStatus(StrEnum):
    PROCESSING = "processing"
    DENIED = "denied"
    APPROVAL_REQUIRED = "approval_required"
    REJECTED = "rejected"
    SUCCEEDED = "succeeded"
    VERIFICATION_FAILED = "verification_failed"
    COMPENSATED = "compensated"
    FAILED = "failed"


class ApprovalState(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class IdempotencyConflict(ValueError):
    """An idempotency key was reused for a different action payload."""


@dataclass(frozen=True)
class ActionRequest:
    name: str
    arguments: Mapping[str, Any]
    idempotency_key: str
    correlation_id: str
    tenant_id: str
    principal: Principal
    approval: ApprovalState = ApprovalState.PENDING
    timeout_seconds: float = 30.0
    max_attempts: int = 1
    resource: str = "business-action"
    compensation_reference: str | None = None

    def __post_init__(self) -> None:
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        if self.max_attempts < 1 or self.max_attempts > 3:
            raise ValueError("max_attempts must be between 1 and 3")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            {"action": self.name, "arguments": self.arguments},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class ActionReceipt:
    action: str
    idempotency_key: str
    tenant_id: str
    status: ActionStatus
    attempts: int = 0
    result: Any = None
    verified: bool = False
    authorization_reason: str = ""
    error: str | None = None
    compensation_reference: str | None = None
    request_hash: str = ""
    request_payload: dict[str, Any] | None = None
    updated_at: str = ""
    duplicate: bool = False


class ReceiptStore(Protocol):
    def get(self, tenant_id: str, idempotency_key: str) -> ActionReceipt | None: ...

    def claim(
        self, request: ActionRequest, *, authorization_reason: str
    ) -> tuple[bool, ActionReceipt]: ...

    def put(self, receipt: ActionReceipt) -> None: ...

    def reconcilable(self, tenant_id: str, *, before: datetime) -> list[ActionReceipt]: ...


def _processing_receipt(request: ActionRequest, authorization_reason: str) -> ActionReceipt:
    return ActionReceipt(
        action=request.name,
        idempotency_key=request.idempotency_key,
        tenant_id=request.tenant_id,
        status=ActionStatus.PROCESSING,
        authorization_reason=authorization_reason,
        compensation_reference=request.compensation_reference,
        request_hash=request.fingerprint,
        request_payload=dict(request.arguments),
        updated_at=datetime.now(UTC).isoformat(),
    )


def _assert_same_request(existing: ActionReceipt, request_hash: str) -> None:
    if existing.request_hash and existing.request_hash != request_hash:
        raise IdempotencyConflict("idempotency key was reused with a different payload")


class InMemoryReceiptStore:
    """Thread-safe deterministic store for tests and dependency-free composition."""

    def __init__(self) -> None:
        self._receipts: dict[tuple[str, str], ActionReceipt] = {}
        self._lock = RLock()

    def get(self, tenant_id: str, idempotency_key: str) -> ActionReceipt | None:
        with self._lock:
            return self._receipts.get((tenant_id, idempotency_key))

    def claim(
        self, request: ActionRequest, *, authorization_reason: str
    ) -> tuple[bool, ActionReceipt]:
        key = (request.tenant_id, request.idempotency_key)
        with self._lock:
            existing = self._receipts.get(key)
            if existing is not None:
                _assert_same_request(existing, request.fingerprint)
                if existing.status == ActionStatus.APPROVAL_REQUIRED:
                    receipt = _processing_receipt(request, authorization_reason)
                    self._receipts[key] = receipt
                    return True, receipt
                return False, existing
            receipt = _processing_receipt(request, authorization_reason)
            self._receipts[key] = receipt
            return True, receipt

    def put(self, receipt: ActionReceipt) -> None:
        key = (receipt.tenant_id, receipt.idempotency_key)
        with self._lock:
            existing = self._receipts.get(key)
            if existing is not None:
                _assert_same_request(existing, receipt.request_hash)
            self._receipts[key] = receipt

    def reconcilable(self, tenant_id: str, *, before: datetime) -> list[ActionReceipt]:
        with self._lock:
            return [
                receipt
                for receipt in self._receipts.values()
                if receipt.tenant_id == tenant_id
                and receipt.status == ActionStatus.PROCESSING
                and (not receipt.updated_at or datetime.fromisoformat(receipt.updated_at) <= before)
            ]


class SQLiteReceiptStore:
    """Durable tenant-keyed receipt store used to survive local process restarts."""

    _COLUMNS = (
        "action, status, attempts, result_json, verified, authorization_reason, error, "
        "compensation_reference, request_hash, request_json, updated_at"
    )

    def __init__(self, path: str) -> None:
        self._path = path

    def _db(self) -> sqlite3.Connection:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, timeout=30, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS action_receipts ("
            "tenant_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, action TEXT NOT NULL, "
            "status TEXT NOT NULL, attempts INTEGER NOT NULL, result_json TEXT, "
            "verified INTEGER NOT NULL, authorization_reason TEXT NOT NULL, error TEXT, "
            "compensation_reference TEXT, request_hash TEXT NOT NULL DEFAULT '', "
            "request_json TEXT, updated_at TEXT NOT NULL DEFAULT '', "
            "PRIMARY KEY(tenant_id, idempotency_key))"
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(action_receipts)")}
        for name, definition in (
            ("request_hash", "TEXT NOT NULL DEFAULT ''"),
            ("request_json", "TEXT"),
            ("updated_at", "TEXT NOT NULL DEFAULT ''"),
        ):
            if name not in columns:
                conn.execute(f"ALTER TABLE action_receipts ADD COLUMN {name} {definition}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_action_reconcile "
            "ON action_receipts(tenant_id, status, updated_at)"
        )
        return conn

    @staticmethod
    def _from_row(
        tenant_id: str, idempotency_key: str, row: tuple[Any, ...]
    ) -> ActionReceipt:
        return ActionReceipt(
            action=row[0],
            idempotency_key=idempotency_key,
            tenant_id=tenant_id,
            status=ActionStatus(row[1]),
            attempts=int(row[2]),
            result=json.loads(row[3]) if row[3] is not None else None,
            verified=bool(row[4]),
            authorization_reason=row[5],
            error=row[6],
            compensation_reference=row[7],
            request_hash=row[8],
            request_payload=json.loads(row[9]) if row[9] is not None else None,
            updated_at=row[10],
        )

    def get(self, tenant_id: str, idempotency_key: str) -> ActionReceipt | None:
        conn = self._db()
        try:
            row = conn.execute(
                f"SELECT {self._COLUMNS} FROM action_receipts "  # noqa: S608
                "WHERE tenant_id = ? AND idempotency_key = ?",
                (tenant_id, idempotency_key),
            ).fetchone()
            return self._from_row(tenant_id, idempotency_key, row) if row else None
        finally:
            conn.close()

    def claim(
        self, request: ActionRequest, *, authorization_reason: str
    ) -> tuple[bool, ActionReceipt]:
        receipt = _processing_receipt(request, authorization_reason)
        conn = self._db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "INSERT OR IGNORE INTO action_receipts "
                "(tenant_id, idempotency_key, action, status, attempts, result_json, verified, "
                "authorization_reason, error, compensation_reference, request_hash, request_json, "
                "updated_at) VALUES (?, ?, ?, ?, 0, NULL, 0, ?, NULL, ?, ?, ?, ?)",
                (
                    receipt.tenant_id,
                    receipt.idempotency_key,
                    receipt.action,
                    receipt.status.value,
                    receipt.authorization_reason,
                    receipt.compensation_reference,
                    receipt.request_hash,
                    json.dumps(receipt.request_payload, ensure_ascii=False, sort_keys=True),
                    receipt.updated_at,
                ),
            )
            acquired = cursor.rowcount == 1
            row = conn.execute(
                f"SELECT {self._COLUMNS} FROM action_receipts "  # noqa: S608
                "WHERE tenant_id = ? AND idempotency_key = ?",
                (request.tenant_id, request.idempotency_key),
            ).fetchone()
            assert row is not None
            existing = self._from_row(request.tenant_id, request.idempotency_key, row)
            _assert_same_request(existing, request.fingerprint)
            if not acquired and existing.status == ActionStatus.APPROVAL_REQUIRED:
                cursor = conn.execute(
                    "UPDATE action_receipts SET status = ?, authorization_reason = ?, "
                    "request_hash = ?, request_json = ?, updated_at = ? "
                    "WHERE tenant_id = ? AND idempotency_key = ? AND status = ?",
                    (
                        ActionStatus.PROCESSING.value,
                        authorization_reason,
                        request.fingerprint,
                        json.dumps(dict(request.arguments), ensure_ascii=False, sort_keys=True),
                        receipt.updated_at,
                        request.tenant_id,
                        request.idempotency_key,
                        ActionStatus.APPROVAL_REQUIRED.value,
                    ),
                )
                acquired = cursor.rowcount == 1
                if acquired:
                    existing = receipt
            conn.execute("COMMIT")
            return acquired, existing
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def put(self, receipt: ActionReceipt) -> None:
        updated_at = receipt.updated_at or datetime.now(UTC).isoformat()
        conn = self._db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT request_hash FROM action_receipts "
                "WHERE tenant_id = ? AND idempotency_key = ?",
                (receipt.tenant_id, receipt.idempotency_key),
            ).fetchone()
            if row and row[0] and receipt.request_hash and row[0] != receipt.request_hash:
                raise IdempotencyConflict("idempotency key was reused with a different payload")
            conn.execute(
                "INSERT INTO action_receipts "
                "(tenant_id, idempotency_key, action, status, attempts, result_json, verified, "
                "authorization_reason, error, compensation_reference, request_hash, request_json, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(tenant_id, idempotency_key) DO UPDATE SET "
                "status=excluded.status, attempts=excluded.attempts, "
                "result_json=excluded.result_json, verified=excluded.verified, "
                "authorization_reason=excluded.authorization_reason, error=excluded.error, "
                "compensation_reference=excluded.compensation_reference, "
                "request_hash=CASE WHEN excluded.request_hash = '' THEN request_hash "
                "ELSE excluded.request_hash END, "
                "request_json=COALESCE(excluded.request_json, request_json), "
                "updated_at=excluded.updated_at",
                (
                    receipt.tenant_id,
                    receipt.idempotency_key,
                    receipt.action,
                    receipt.status.value,
                    receipt.attempts,
                    json.dumps(receipt.result, ensure_ascii=False, default=str)
                    if receipt.result is not None
                    else None,
                    int(receipt.verified),
                    receipt.authorization_reason,
                    receipt.error,
                    receipt.compensation_reference,
                    receipt.request_hash,
                    json.dumps(receipt.request_payload, ensure_ascii=False, sort_keys=True)
                    if receipt.request_payload is not None
                    else None,
                    updated_at,
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def reconcilable(self, tenant_id: str, *, before: datetime) -> list[ActionReceipt]:
        conn = self._db()
        try:
            rows = conn.execute(
                f"SELECT idempotency_key, {self._COLUMNS} FROM action_receipts "  # noqa: S608
                "WHERE tenant_id = ? AND status = ? AND updated_at <= ? ORDER BY updated_at",
                (tenant_id, ActionStatus.PROCESSING.value, before.astimezone(UTC).isoformat()),
            ).fetchall()
            return [self._from_row(tenant_id, row[0], row[1:]) for row in rows]
        finally:
            conn.close()


@dataclass(frozen=True)
class ActionPolicy:
    requires_approval: bool = True
    retry_timeouts: bool = True
    preconditions: tuple[Callable[[ActionRequest], bool], ...] = field(default_factory=tuple)


class ActionExecutor:
    """Authorize, atomically claim, execute, verify, and compensate exactly once."""

    def __init__(self, policy_engine: PolicyEngine, receipts: ReceiptStore) -> None:
        self._policy_engine = policy_engine
        self._receipts = receipts

    def _authorize_and_claim(
        self, request: ActionRequest, policy: ActionPolicy
    ) -> tuple[ActionReceipt | None, str]:
        auth = self._policy_engine.decide(
            AuthorizationRequest(
                principal=request.principal,
                action="execute",
                resource=request.resource,
                tenant_id=request.tenant_id,
                tool=request.name,
            )
        )
        if not auth.allowed:
            return self._record(
                request, ActionStatus.DENIED, authorization_reason=auth.reason
            ), auth.reason
        if policy.requires_approval and request.approval == ApprovalState.PENDING:
            return self._record(
                request, ActionStatus.APPROVAL_REQUIRED, authorization_reason=auth.reason
            ), auth.reason
        if request.approval == ApprovalState.REJECTED:
            return self._record(
                request, ActionStatus.REJECTED, authorization_reason=auth.reason
            ), auth.reason
        try:
            preconditions_met = all(check(request) for check in policy.preconditions)
        except Exception as exc:
            return self._record(
                request,
                ActionStatus.FAILED,
                authorization_reason=auth.reason,
                error=f"precondition error: {type(exc).__name__}",
            ), auth.reason
        if not preconditions_met:
            return self._record(
                request,
                ActionStatus.FAILED,
                authorization_reason=auth.reason,
                error="precondition failed",
            ), auth.reason
        try:
            acquired, receipt = self._receipts.claim(
                request, authorization_reason=auth.reason
            )
        except IdempotencyConflict:
            conflict = ActionReceipt(
                action=request.name,
                idempotency_key=request.idempotency_key,
                tenant_id=request.tenant_id,
                status=ActionStatus.FAILED,
                authorization_reason=auth.reason,
                error="idempotency key reused with a different payload",
                request_hash=request.fingerprint,
                request_payload=dict(request.arguments),
                duplicate=True,
            )
            self._audit(request, conflict)
            return conflict, auth.reason
        if not acquired:
            return replace(receipt, duplicate=True), auth.reason
        return None, auth.reason

    def execute(
        self,
        request: ActionRequest,
        effect: Callable[[], Any],
        *,
        verifier: Callable[[Any], bool],
        policy: ActionPolicy | None = None,
        compensate: Callable[[Any], None] | None = None,
    ) -> ActionReceipt:
        action_policy = policy or ActionPolicy()
        early, authorization_reason = self._authorize_and_claim(request, action_policy)
        if early is not None:
            return early
        attempts = 0
        result: Any = None
        started = monotonic()
        while attempts < request.max_attempts:
            attempts += 1
            try:
                result = effect()
                if monotonic() - started > request.timeout_seconds:
                    raise TimeoutError("action deadline exceeded")
                break
            except TimeoutError as exc:
                if not action_policy.retry_timeouts or attempts >= request.max_attempts:
                    return self._record(
                        request,
                        ActionStatus.FAILED,
                        attempts=attempts,
                        authorization_reason=authorization_reason,
                        error=str(exc),
                    )
            except Exception as exc:
                return self._record(
                    request,
                    ActionStatus.FAILED,
                    attempts=attempts,
                    authorization_reason=authorization_reason,
                    error=f"{type(exc).__name__}: action provider failed",
                )
        return self._finalize(
            request,
            result,
            attempts=attempts,
            authorization_reason=authorization_reason,
            verifier=verifier,
            compensate=compensate,
        )

    async def execute_async(
        self,
        request: ActionRequest,
        effect: Callable[[], Any],
        *,
        verifier: Callable[[Any], bool],
        policy: ActionPolicy | None = None,
        compensate: Callable[[Any], None] | None = None,
    ) -> ActionReceipt:
        """Async variant with a cancellation deadline around each effect attempt."""
        action_policy = policy or ActionPolicy()
        early, authorization_reason = self._authorize_and_claim(request, action_policy)
        if early is not None:
            return early
        attempts = 0
        result: Any = None
        while attempts < request.max_attempts:
            attempts += 1
            try:
                result = await asyncio.wait_for(effect(), timeout=request.timeout_seconds)
                break
            except TimeoutError as exc:
                if not action_policy.retry_timeouts or attempts >= request.max_attempts:
                    return self._record(
                        request,
                        ActionStatus.FAILED,
                        attempts=attempts,
                        authorization_reason=authorization_reason,
                        error=str(exc) or "action deadline exceeded",
                    )
            except Exception as exc:
                return self._record(
                    request,
                    ActionStatus.FAILED,
                    attempts=attempts,
                    authorization_reason=authorization_reason,
                    error=f"{type(exc).__name__}: action provider failed",
                )
        return self._finalize(
            request,
            result,
            attempts=attempts,
            authorization_reason=authorization_reason,
            verifier=verifier,
            compensate=compensate,
        )

    def _finalize(
        self,
        request: ActionRequest,
        result: Any,
        *,
        attempts: int,
        authorization_reason: str,
        verifier: Callable[[Any], bool],
        compensate: Callable[[Any], None] | None,
    ) -> ActionReceipt:
        verification_error = "end-state verification failed"
        try:
            verified = verifier(result)
        except Exception as exc:
            verified = False
            verification_error = f"verification error: {type(exc).__name__}"
        if verified:
            return self._record(
                request,
                ActionStatus.SUCCEEDED,
                attempts=attempts,
                result=result,
                verified=True,
                authorization_reason=authorization_reason,
            )
        if compensate is not None:
            try:
                compensate(result)
            except Exception as exc:
                return self._record(
                    request,
                    ActionStatus.VERIFICATION_FAILED,
                    attempts=attempts,
                    result=result,
                    authorization_reason=authorization_reason,
                    error=(
                        f"{verification_error}; compensation failed: {type(exc).__name__}"
                    ),
                )
            return self._record(
                request,
                ActionStatus.COMPENSATED,
                attempts=attempts,
                result=result,
                authorization_reason=authorization_reason,
                error=verification_error,
            )
        return self._record(
            request,
            ActionStatus.VERIFICATION_FAILED,
            attempts=attempts,
            result=result,
            authorization_reason=authorization_reason,
            error=verification_error,
        )

    def _record(
        self, request: ActionRequest, status: ActionStatus, **values: Any
    ) -> ActionReceipt:
        receipt = ActionReceipt(
            action=request.name,
            idempotency_key=request.idempotency_key,
            tenant_id=request.tenant_id,
            status=status,
            compensation_reference=request.compensation_reference,
            request_hash=request.fingerprint,
            request_payload=dict(request.arguments),
            updated_at=datetime.now(UTC).isoformat(),
            **values,
        )
        self._receipts.put(receipt)
        self._audit(request, receipt)
        return receipt

    @staticmethod
    def _audit(request: ActionRequest, receipt: ActionReceipt) -> None:
        audit(
            "action.outcome",
            decision=receipt.status.value,
            reason=receipt.error or receipt.authorization_reason or receipt.status.value,
            resource=request.resource,
            action=request.name,
            metadata={"attempts": receipt.attempts, "verified": receipt.verified},
        )
