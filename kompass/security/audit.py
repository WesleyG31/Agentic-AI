"""Structured, privacy-conscious security audit events."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from kompass.runtime import get_runtime_context

logger = logging.getLogger("kompass.security.audit")
_SENSITIVE_KEYS = ("authorization", "token", "secret", "password", "credential", "api_key")


def _safe_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in (metadata or {}).items():
        if any(marker in key.casefold() for marker in _SENSITIVE_KEYS):
            safe[key] = "[redacted]"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value[:256] if isinstance(value, str) else value
        else:
            safe[key] = str(type(value).__name__)
    return safe


@dataclass(frozen=True)
class AuditEvent:
    event_type: str
    decision: str
    reason: str
    occurred_at: str
    tenant_id: str | None = None
    principal_id: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    resource: str | None = None
    action: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class AuditSink(Protocol):
    def emit(self, event: AuditEvent) -> None: ...


class LoggingAuditSink:
    def emit(self, event: AuditEvent) -> None:
        logger.info("security_event", extra={"security_event": event.as_dict()})


class InMemoryAuditSink:
    """Explicit deterministic sink for tests; never used as process-global storage."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def emit(self, event: AuditEvent) -> None:
        self.events.append(event)


_SINK: ContextVar[AuditSink | None] = ContextVar("kompass_audit_sink", default=None)


@contextmanager
def audit_scope(sink: AuditSink) -> Iterator[AuditSink]:
    token = _SINK.set(sink)
    try:
        yield sink
    finally:
        _SINK.reset(token)


def audit(
    event_type: str,
    *,
    decision: str,
    reason: str,
    resource: str | None = None,
    action: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> AuditEvent:
    context = get_runtime_context(required=False)
    event = AuditEvent(
        event_type=event_type,
        decision=decision,
        reason=reason,
        occurred_at=(context.current_time if context else datetime.now(UTC)).isoformat(),
        tenant_id=context.tenant_id if context else None,
        principal_id=context.user_id if context else None,
        request_id=context.request_id if context else None,
        correlation_id=context.correlation_id if context else None,
        resource=resource,
        action=action,
        metadata=_safe_metadata(metadata),
    )
    (_SINK.get() or LoggingAuditSink()).emit(event)
    return event
