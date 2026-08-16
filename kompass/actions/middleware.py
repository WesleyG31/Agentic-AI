"""HITL-integrated transactional wrapper for Kompass's side-effecting tools."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from kompass.actions.executor import (
    ActionExecutor,
    ActionRequest,
    ActionStatus,
    ApprovalState,
    SQLiteReceiptStore,
)
from kompass.config import ROOT, settings
from kompass.runtime import get_runtime_context
from kompass.security.identity import LocalPolicyEngine, Principal

WRITE_TOOLS = {"create_refund", "update_ticket"}
_REFUND_ID = re.compile(r"Refund\s+(\d+)\s+created", re.I)


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(ROOT / settings.acme_db)
    conn.row_factory = sqlite3.Row
    return conn


def _snapshot(tool: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    if tool != "update_ticket":
        return None
    conn = _db()
    row = conn.execute(
        "SELECT status, body, resolved_at FROM tickets WHERE id = ?",
        (int(arguments["ticket_id"]),),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _verify(tool: str, arguments: dict[str, Any], result: str) -> bool:
    conn = _db()
    try:
        if tool == "create_refund":
            match = _REFUND_ID.search(result)
            if not match:
                return False
            row = conn.execute(
                "SELECT order_id, amount_eur, status FROM refunds WHERE id = ?",
                (int(match.group(1)),),
            ).fetchone()
            return bool(
                row
                and row["order_id"] == int(arguments["order_id"])
                and abs(row["amount_eur"] - float(arguments["amount_eur"])) < 0.001
                and row["status"] in {"approved", "completed"}
            )
        if tool == "update_ticket":
            row = conn.execute(
                "SELECT status FROM tickets WHERE id = ?", (int(arguments["ticket_id"]),)
            ).fetchone()
            return bool(row and row["status"] == arguments["status"])
        return False
    finally:
        conn.close()


def _compensate(
    tool: str,
    arguments: dict[str, Any],
    result: str,
    snapshot: dict[str, Any] | None,
) -> None:
    conn = _db()
    try:
        if tool == "create_refund":
            match = _REFUND_ID.search(result)
            if match:
                conn.execute("DELETE FROM refunds WHERE id = ?", (int(match.group(1)),))
        elif tool == "update_ticket" and snapshot is not None:
            conn.execute(
                "UPDATE tickets SET status = ?, body = ?, resolved_at = ? WHERE id = ?",
                (
                    snapshot["status"],
                    snapshot["body"],
                    snapshot["resolved_at"],
                    int(arguments["ticket_id"]),
                ),
            )
        conn.commit()
    finally:
        conn.close()


class ActionExecutionMiddleware(AgentMiddleware):
    """Runs only after the existing HITL middleware has released an approved tool call."""

    def __init__(
        self,
        *,
        executor: ActionExecutor | None = None,
        verifier: Callable[[str, dict[str, Any], str], bool] = _verify,
        compensator: Callable[
            [str, dict[str, Any], str, dict[str, Any] | None], None
        ] = _compensate,
    ) -> None:
        receipt_path = str(ROOT / settings.action_receipts_db)
        self._executor = executor or ActionExecutor(
            LocalPolicyEngine(), SQLiteReceiptStore(receipt_path)
        )
        self._verifier = verifier
        self._compensator = compensator

    async def awrap_tool_call(self, request, handler):
        tool = str(request.tool_call.get("name", ""))
        if tool not in WRITE_TOOLS:
            return await handler(request)
        context = get_runtime_context(required=False)
        if context is None:
            return ToolMessage(
                content="Action denied: runtime identity context is required",
                tool_call_id=str(request.tool_call.get("id", "action-denied")),
                name=tool,
                status="error",
            )
        arguments = dict(request.tool_call.get("args", {}))
        before = _snapshot(tool, arguments)
        original: ToolMessage | None = None

        async def effect() -> str:
            nonlocal original
            response = await handler(request)
            if not isinstance(response, ToolMessage):
                raise RuntimeError("side-effecting tool returned an unsupported response type")
            original = response
            if response.status == "error":
                raise RuntimeError(str(response.content))
            return str(response.content)

        action = ActionRequest(
            name=tool,
            arguments=arguments,
            idempotency_key=f"tool-call:{request.tool_call.get('id', '')}",
            correlation_id=context.correlation_id,
            tenant_id=context.tenant_id,
            principal=Principal.from_runtime(context),
            approval=ApprovalState.APPROVED,
            timeout_seconds=settings.action_timeout_seconds,
            max_attempts=settings.action_max_attempts,
            resource=f"tool:{tool}",
            compensation_reference=f"kompass:{tool}:v1",
        )
        receipt = await self._executor.execute_async(
            action,
            effect,
            verifier=lambda result: self._verifier(tool, arguments, result),
            compensate=lambda result: self._compensator(tool, arguments, result, before),
        )
        if receipt.status == ActionStatus.SUCCEEDED:
            if original is not None:
                return original
            return ToolMessage(
                content=str(receipt.result),
                tool_call_id=str(request.tool_call.get("id", "")),
                name=tool,
            )
        return ToolMessage(
            content=(
                f"Action {receipt.status.value}: "
                f"{receipt.error or receipt.authorization_reason}"
            ),
            tool_call_id=str(request.tool_call.get("id", "")),
            name=tool,
            status="error",
        )
