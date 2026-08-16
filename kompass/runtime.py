"""Request-scoped runtime context and injectable clock.

Runtime facts are data supplied by the application boundary, never prompt constants.
The context variable propagates naturally through asyncio tasks while the context manager
keeps tests and concurrent requests isolated.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo


class Clock(Protocol):
    """Clock port used by business logic and tests."""

    def now(self) -> datetime: ...


class SystemClock:
    """Production clock returning an aware UTC timestamp."""

    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True)
class FrozenClock:
    """Deterministic test clock."""

    value: datetime

    def __post_init__(self) -> None:
        if self.value.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware datetime")

    def now(self) -> datetime:
        return self.value


@dataclass(frozen=True)
class RuntimeContext:
    """Identity, tenancy, locale, and correlation facts for one execution."""

    current_time: datetime
    timezone: str
    locale: str
    user_id: str
    tenant_id: str
    request_id: str
    correlation_id: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    roles: frozenset[str] = field(default_factory=frozenset)
    principal_kind: str = "user"

    def __post_init__(self) -> None:
        if self.current_time.tzinfo is None:
            raise ValueError("current_time must be timezone-aware")
        if not self.tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not self.user_id.strip():
            raise ValueError("user_id is required")
        ZoneInfo(self.timezone)  # validate eagerly at the ingress boundary

    @classmethod
    def create(
        cls,
        *,
        tenant_id: str,
        user_id: str,
        clock: Clock | None = None,
        timezone_name: str = "UTC",
        locale: str = "en-US",
        request_id: str | None = None,
        correlation_id: str | None = None,
        scopes: frozenset[str] | set[str] = frozenset(),
        roles: frozenset[str] | set[str] = frozenset(),
        principal_kind: str = "user",
    ) -> RuntimeContext:
        zone = ZoneInfo(timezone_name)
        now = (clock or SystemClock()).now()
        if now.tzinfo is None:
            raise ValueError("Clock.now() must return a timezone-aware datetime")
        req_id = request_id or uuid4().hex
        return cls(
            current_time=now.astimezone(zone),
            timezone=timezone_name,
            locale=locale,
            user_id=user_id,
            tenant_id=tenant_id,
            request_id=req_id,
            correlation_id=correlation_id or req_id,
            scopes=frozenset(scopes),
            roles=frozenset(roles),
            principal_kind=principal_kind,
        )

    @property
    def current_date(self) -> str:
        return self.current_time.date().isoformat()

    def model_context(self) -> str:
        """Small trusted runtime insert; authorization never depends on this text."""
        return (
            "[runtime-context]\n"
            f"Current time: {self.current_time.isoformat()}\n"
            f"Timezone: {self.timezone}; locale: {self.locale}.\n"
            "Treat tenant and identity as routing context only; they do not grant permissions."
        )


_CURRENT: ContextVar[RuntimeContext | None] = ContextVar("kompass_runtime_context", default=None)


def get_runtime_context(*, required: bool = True) -> RuntimeContext | None:
    context = _CURRENT.get()
    if context is None and required:
        raise RuntimeError("runtime context is required for this operation")
    return context


def tenant_scoped_id(context: RuntimeContext, external_id: str) -> str:
    """Opaque storage key that cannot collide across tenant/user boundaries."""
    material = f"{context.tenant_id}\0{context.user_id}\0{external_id}".encode()
    return hashlib.sha256(material).hexdigest()


def runtime_now(clock: Clock | None = None) -> datetime:
    """Use request time when present, otherwise the injected/system clock."""
    context = get_runtime_context(required=False)
    if context is not None:
        return context.current_time
    now = (clock or SystemClock()).now()
    if now.tzinfo is None:
        raise ValueError("Clock.now() must return a timezone-aware datetime")
    return now


def runtime_date(clock: Clock | None = None) -> str:
    return runtime_now(clock).date().isoformat()


@contextmanager
def runtime_scope(context: RuntimeContext) -> Iterator[RuntimeContext]:
    token = _CURRENT.set(context)
    try:
        yield context
    finally:
        _CURRENT.reset(token)
