"""Reliable execution boundary for externally visible side effects."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from time import monotonic
from typing import Any, Protocol

from kompass.security.audit import audit
from kompass.security.identity import AuthorizationRequest, PolicyEngine, Principal


class ActionStatus(StrEnum):
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
    duplicate: bool = False


class ReceiptStore(Protocol):
    def get(self, tenant_id: str, idempotency_key: str) -> ActionReceipt | None: ...

    def put(self, receipt: ActionReceipt) -> None: ...


class InMemoryReceiptStore:
    """Deterministic store for tests/local composition; inject a durable store in services."""

    def __init__(self) -> None:
        self._receipts: dict[tuple[str, str], ActionReceipt] = {}

    def get(self, tenant_id: str, idempotency_key: str) -> ActionReceipt | None:
        return self._receipts.get((tenant_id, idempotency_key))

    def put(self, receipt: ActionReceipt) -> None:
        self._receipts[(receipt.tenant_id, receipt.idempotency_key)] = receipt


class SQLiteReceiptStore:
    """Durable tenant-keyed receipt store used to survive worker/process restarts."""

    def __init__(self, path: str) -> None:
        self._path = path

    def _db(self) -> sqlite3.Connection:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS action_receipts ("
            "tenant_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, action TEXT NOT NULL, "
            "status TEXT NOT NULL, attempts INTEGER NOT NULL, result_json TEXT, "
            "verified INTEGER NOT NULL, authorization_reason TEXT NOT NULL, error TEXT, "
            "compensation_reference TEXT, PRIMARY KEY(tenant_id, idempotency_key))"
        )
        return conn

    def get(self, tenant_id: str, idempotency_key: str) -> ActionReceipt | None:
        conn = self._db()
        row = conn.execute(
            "SELECT action, status, attempts, result_json, verified, authorization_reason, "
            "error, compensation_reference FROM action_receipts "
            "WHERE tenant_id = ? AND idempotency_key = ?",
            (tenant_id, idempotency_key),
        ).fetchone()
        conn.close()
        if row is None:
            return None
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
        )

    def put(self, receipt: ActionReceipt) -> None:
        conn = self._db()
        conn.execute(
            "INSERT OR IGNORE INTO action_receipts "
            "(tenant_id, idempotency_key, action, status, attempts, result_json, verified, "
            "authorization_reason, error, compensation_reference) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                receipt.tenant_id,
                receipt.idempotency_key,
                receipt.action,
                receipt.status.value,
                receipt.attempts,
                json.dumps(receipt.result, ensure_ascii=False, default=str),
                int(receipt.verified),
                receipt.authorization_reason,
                receipt.error,
                receipt.compensation_reference,
            ),
        )
        conn.commit()
        conn.close()


@dataclass(frozen=True)
class ActionPolicy:
    requires_approval: bool = True
    retry_timeouts: bool = True
    preconditions: tuple[Callable[[ActionRequest], bool], ...] = field(default_factory=tuple)


class ActionExecutor:
    """Authorize, gate, execute, verify, and optionally compensate exactly once."""

    def __init__(self, policy_engine: PolicyEngine, receipts: ReceiptStore) -> None:
        self._policy_engine = policy_engine
        self._receipts = receipts

    def execute(
        self,
        request: ActionRequest,
        effect: Callable[[], Any],
        *,
        verifier: Callable[[Any], bool],
        policy: ActionPolicy | None = None,
        compensate: Callable[[Any], None] | None = None,
    ) -> ActionReceipt:
        existing = self._receipts.get(request.tenant_id, request.idempotency_key)
        if existing is not None:
            return ActionReceipt(**{**existing.__dict__, "duplicate": True})

        action_policy = policy or ActionPolicy()
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
            return self._record(request, ActionStatus.DENIED, authorization_reason=auth.reason)
        if action_policy.requires_approval and request.approval == ApprovalState.PENDING:
            return self._record(
                request, ActionStatus.APPROVAL_REQUIRED, authorization_reason=auth.reason
            )
        if request.approval == ApprovalState.REJECTED:
            return self._record(request, ActionStatus.REJECTED, authorization_reason=auth.reason)
        try:
            preconditions_met = all(check(request) for check in action_policy.preconditions)
        except Exception as exc:
            return self._record(
                request,
                ActionStatus.FAILED,
                authorization_reason=auth.reason,
                error=f"precondition error: {type(exc).__name__}",
            )
        if not preconditions_met:
            return self._record(
                request,
                ActionStatus.FAILED,
                authorization_reason=auth.reason,
                error="precondition failed",
            )

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
                        authorization_reason=auth.reason,
                        error=str(exc),
                    )
            except Exception as exc:
                return self._record(
                    request,
                    ActionStatus.FAILED,
                    attempts=attempts,
                    authorization_reason=auth.reason,
                    error=f"{type(exc).__name__}: action provider failed",
                )

        return self._finalize(
            request,
            result,
            attempts=attempts,
            authorization_reason=auth.reason,
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
        """Async variant with a real cancellation deadline around each effect attempt."""
        existing = self._receipts.get(request.tenant_id, request.idempotency_key)
        if existing is not None:
            return ActionReceipt(**{**existing.__dict__, "duplicate": True})
        action_policy = policy or ActionPolicy()
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
            return self._record(request, ActionStatus.DENIED, authorization_reason=auth.reason)
        if action_policy.requires_approval and request.approval == ApprovalState.PENDING:
            return self._record(
                request, ActionStatus.APPROVAL_REQUIRED, authorization_reason=auth.reason
            )
        if request.approval == ApprovalState.REJECTED:
            return self._record(request, ActionStatus.REJECTED, authorization_reason=auth.reason)
        try:
            preconditions_met = all(check(request) for check in action_policy.preconditions)
        except Exception as exc:
            return self._record(
                request,
                ActionStatus.FAILED,
                authorization_reason=auth.reason,
                error=f"precondition error: {type(exc).__name__}",
            )
        if not preconditions_met:
            return self._record(
                request,
                ActionStatus.FAILED,
                authorization_reason=auth.reason,
                error="precondition failed",
            )

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
                        authorization_reason=auth.reason,
                        error=str(exc) or "action deadline exceeded",
                    )
            except Exception as exc:
                return self._record(
                    request,
                    ActionStatus.FAILED,
                    attempts=attempts,
                    authorization_reason=auth.reason,
                    error=f"{type(exc).__name__}: action provider failed",
                )

        return self._finalize(
            request,
            result,
            attempts=attempts,
            authorization_reason=auth.reason,
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
                        f"{verification_error}; compensation failed: "
                        f"{type(exc).__name__}"
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

    def _record(self, request: ActionRequest, status: ActionStatus, **values: Any) -> ActionReceipt:
        receipt = ActionReceipt(
            action=request.name,
            idempotency_key=request.idempotency_key,
            tenant_id=request.tenant_id,
            status=status,
            compensation_reference=request.compensation_reference,
            **values,
        )
        self._receipts.put(receipt)
        audit(
            "action.outcome",
            decision=status.value,
            reason=receipt.error or receipt.authorization_reason or status.value,
            resource=request.resource,
            action=request.name,
            metadata={"attempts": receipt.attempts, "verified": receipt.verified},
        )
        return receipt
