"""PostgreSQL receipt adapter with transactional claims and tenant RLS context."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from kompass.actions.executor import (
    ActionReceipt,
    ActionRequest,
    ActionStatus,
    IdempotencyConflict,
)
from kompass.runtime import get_runtime_context


class PostgresReceiptStore:
    """Persistent action receipts backed by the migrated ``action_receipts`` table."""

    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        self._dsn = dsn

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ModuleNotFoundError as exc:  # pragma: no cover - deployment packaging guard
            raise RuntimeError("PostgreSQL receipts require psycopg") from exc
        return psycopg.connect(self._dsn, row_factory=dict_row)

    @staticmethod
    def _assert_runtime_tenant(tenant_id: str) -> None:
        context = get_runtime_context(required=False)
        if context is not None and context.tenant_id != tenant_id:
            raise PermissionError("cross-tenant receipt access denied")

    @staticmethod
    def _set_tenant(conn, tenant_id: str) -> None:
        conn.execute("SELECT set_config('app.current_tenant', %s, true)", (tenant_id,))

    @staticmethod
    def _from_row(row: dict[str, Any]) -> ActionReceipt:
        return ActionReceipt(
            action=row["action"],
            idempotency_key=row["idempotency_key"],
            tenant_id=row["tenant_id"],
            status=ActionStatus(row["status"]),
            attempts=row["attempts"],
            result=row["result_json"],
            verified=row["verified"],
            authorization_reason=row["authorization_reason"],
            error=row["error"],
            compensation_reference=row["compensation_reference"],
            request_hash=row["request_hash"],
            request_payload=row["request_json"],
            updated_at=row["updated_at"].astimezone(UTC).isoformat(),
        )

    def get(self, tenant_id: str, idempotency_key: str) -> ActionReceipt | None:
        self._assert_runtime_tenant(tenant_id)
        with self._connect() as conn, conn.transaction():
            self._set_tenant(conn, tenant_id)
            row = conn.execute(
                "SELECT * FROM action_receipts "
                "WHERE tenant_id = %s AND idempotency_key = %s",
                (tenant_id, idempotency_key),
            ).fetchone()
            return self._from_row(row) if row else None

    def claim(
        self, request: ActionRequest, *, authorization_reason: str
    ) -> tuple[bool, ActionReceipt]:
        self._assert_runtime_tenant(request.tenant_id)
        now = datetime.now(UTC)
        payload = dict(request.arguments)
        with self._connect() as conn, conn.transaction():
            self._set_tenant(conn, request.tenant_id)
            inserted = conn.execute(
                "INSERT INTO action_receipts "
                "(tenant_id, idempotency_key, action, request_hash, request_json, status, "
                "authorization_reason, compensation_reference, updated_at) "
                "VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s) "
                "ON CONFLICT (tenant_id, idempotency_key) DO NOTHING RETURNING *",
                (
                    request.tenant_id,
                    request.idempotency_key,
                    request.name,
                    request.fingerprint,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    ActionStatus.PROCESSING.value,
                    authorization_reason,
                    request.compensation_reference,
                    now,
                ),
            ).fetchone()
            if inserted:
                return True, self._from_row(inserted)
            row = conn.execute(
                "SELECT * FROM action_receipts "
                "WHERE tenant_id = %s AND idempotency_key = %s FOR UPDATE",
                (request.tenant_id, request.idempotency_key),
            ).fetchone()
            assert row is not None
            existing = self._from_row(row)
            if existing.request_hash and existing.request_hash != request.fingerprint:
                raise IdempotencyConflict(
                    "idempotency key was reused with a different payload"
                )
            if existing.status == ActionStatus.APPROVAL_REQUIRED:
                updated = conn.execute(
                    "UPDATE action_receipts SET status = %s, request_hash = %s, "
                    "request_json = %s::jsonb, authorization_reason = %s, updated_at = %s "
                    "WHERE tenant_id = %s AND idempotency_key = %s "
                    "AND status = %s RETURNING *",
                    (
                        ActionStatus.PROCESSING.value,
                        request.fingerprint,
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        authorization_reason,
                        now,
                        request.tenant_id,
                        request.idempotency_key,
                        ActionStatus.APPROVAL_REQUIRED.value,
                    ),
                ).fetchone()
                if updated:
                    return True, self._from_row(updated)
            return False, existing

    def put(self, receipt: ActionReceipt) -> None:
        self._assert_runtime_tenant(receipt.tenant_id)
        now = datetime.now(UTC)
        with self._connect() as conn, conn.transaction():
            self._set_tenant(conn, receipt.tenant_id)
            row = conn.execute(
                "SELECT request_hash FROM action_receipts "
                "WHERE tenant_id = %s AND idempotency_key = %s FOR UPDATE",
                (receipt.tenant_id, receipt.idempotency_key),
            ).fetchone()
            if (
                row
                and row["request_hash"]
                and receipt.request_hash
                and row["request_hash"] != receipt.request_hash
            ):
                raise IdempotencyConflict(
                    "idempotency key was reused with a different payload"
                )
            conn.execute(
                "INSERT INTO action_receipts "
                "(tenant_id, idempotency_key, action, request_hash, request_json, status, "
                "attempts, result_json, verified, authorization_reason, error, "
                "compensation_reference, updated_at) "
                "VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s, %s, %s, %s, %s) "
                "ON CONFLICT (tenant_id, idempotency_key) DO UPDATE SET "
                "status=excluded.status, attempts=excluded.attempts, "
                "result_json=excluded.result_json, verified=excluded.verified, "
                "authorization_reason=excluded.authorization_reason, error=excluded.error, "
                "compensation_reference=excluded.compensation_reference, "
                "request_hash=CASE WHEN excluded.request_hash = '' "
                "THEN action_receipts.request_hash ELSE excluded.request_hash END, "
                "request_json=COALESCE(excluded.request_json, action_receipts.request_json), "
                "updated_at=excluded.updated_at",
                (
                    receipt.tenant_id,
                    receipt.idempotency_key,
                    receipt.action,
                    receipt.request_hash,
                    json.dumps(receipt.request_payload, ensure_ascii=False, sort_keys=True)
                    if receipt.request_payload is not None
                    else None,
                    receipt.status.value,
                    receipt.attempts,
                    json.dumps(receipt.result, ensure_ascii=False, default=str)
                    if receipt.result is not None
                    else None,
                    receipt.verified,
                    receipt.authorization_reason,
                    receipt.error,
                    receipt.compensation_reference,
                    now,
                ),
            )

    def reconcilable(self, tenant_id: str, *, before: datetime) -> list[ActionReceipt]:
        self._assert_runtime_tenant(tenant_id)
        with self._connect() as conn, conn.transaction():
            self._set_tenant(conn, tenant_id)
            rows = conn.execute(
                "SELECT * FROM action_receipts WHERE tenant_id = %s AND status = %s "
                "AND updated_at <= %s ORDER BY updated_at",
                (tenant_id, ActionStatus.PROCESSING.value, before.astimezone(UTC)),
            ).fetchall()
            return [self._from_row(row) for row in rows]
