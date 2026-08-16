"""Langfuse integration at the agent boundary.

The root observation represents one user turn (or one HITL resume).  LangChain's
callback is added only to the graph invocation, so model, tool, middleware and
worker runs become children of that observation.  When observability is disabled
the same API is a no-op, keeping local development and tests deterministic.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Protocol

from kompass.config import settings
from kompass.runtime import get_runtime_context, tenant_scoped_id

logger = logging.getLogger("kompass.telemetry")
_SENSITIVE_KEYS = ("authorization", "token", "secret", "password", "credential", "api_key")


def enabled() -> bool:
    """Return whether tracing is both requested and fully configured."""
    return bool(
        settings.langfuse_enabled and settings.langfuse_public_key and settings.langfuse_secret_key
    )


@lru_cache(maxsize=1)
def client():
    """Build the process-wide Langfuse client lazily."""
    if not enabled():
        return None
    from langfuse import Langfuse

    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        base_url=settings.langfuse_base_url,
        environment=settings.langfuse_environment,
        release=settings.langfuse_release,
        mask=_mask_payload if settings.langfuse_mask_pii else None,
    )


def _mask_payload(data: Any, **_: Any) -> Any:
    """Recursively redact obvious email/phone PII before trace export."""
    from kompass.guardrails.safety import redact_pii

    if isinstance(data, str):
        return redact_pii(data)
    if isinstance(data, dict):
        return {key: _mask_payload(item) for key, item in data.items()}
    if isinstance(data, list):
        return [_mask_payload(item) for item in data]
    if isinstance(data, tuple):
        return tuple(_mask_payload(item) for item in data)
    return data


def _compact_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Fit propagation attributes into OpenTelemetry's small baggage limits."""
    compact: dict[str, Any] = {}
    for key, value in metadata.items():
        if any(marker in key.casefold() for marker in _SENSITIVE_KEYS):
            compact[key] = "[redacted]"
            continue
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if len(encoded) <= 180:
            compact[key] = value
        else:
            compact[f"{key}_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()[:12]
    return compact


def _export_payload(value: Any) -> Any:
    """Export raw model/tool content only under an explicit operator policy."""
    if settings.langfuse_capture_content:
        return _mask_payload(value) if settings.langfuse_mask_pii else value
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return {
        "content_type": type(value).__name__,
        "content_sha256": hashlib.sha256(encoded.encode()).hexdigest()[:16],
        "content_bytes": len(encoded.encode()),
    }


def _safe_attributes(attributes: dict[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in (attributes or {}).items():
        if any(marker in key.casefold() for marker in _SENSITIVE_KEYS):
            result[key] = "[redacted]"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value[:180] if isinstance(value, str) else value
        else:
            result[key] = type(value).__name__
    return result


@dataclass(frozen=True)
class MetricPoint:
    """Vendor-neutral metric event suitable for an OpenTelemetry adapter."""

    name: str
    value: float
    unit: str
    recorded_at: str
    request_id: str | None
    correlation_id: str | None
    tenant_partition: str | None
    attributes: dict[str, Any] = field(default_factory=dict)


class TelemetrySink(Protocol):
    def record(self, point: MetricPoint) -> None: ...


class LoggingTelemetrySink:
    def record(self, point: MetricPoint) -> None:
        logger.info("metric", extra={"metric": point})


class InMemoryTelemetrySink:
    def __init__(self) -> None:
        self.points: list[MetricPoint] = []

    def record(self, point: MetricPoint) -> None:
        self.points.append(point)


_TELEMETRY: ContextVar[TelemetrySink | None] = ContextVar("kompass_telemetry", default=None)


@contextmanager
def telemetry_scope(sink: TelemetrySink) -> Iterator[TelemetrySink]:
    token = _TELEMETRY.set(sink)
    try:
        yield sink
    finally:
        _TELEMETRY.reset(token)


def record_metric(
    name: str,
    value: int | float,
    *,
    unit: str = "1",
    attributes: dict[str, Any] | None = None,
) -> MetricPoint:
    context = get_runtime_context(required=False)
    point = MetricPoint(
        name=name,
        value=float(value),
        unit=unit,
        recorded_at=(context.current_time if context else datetime.now(UTC)).isoformat(),
        request_id=context.request_id if context else None,
        correlation_id=context.correlation_id if context else None,
        tenant_partition=(tenant_scoped_id(context, "telemetry")[:12] if context else None),
        attributes=_safe_attributes(attributes),
    )
    (_TELEMETRY.get() or LoggingTelemetrySink()).record(point)
    return point


@dataclass
class AgentTrace:
    """Mutable handle yielded while an agent turn is executing."""

    trace_id: str | None = None
    trace_url: str | None = None
    callback: Any = None
    span: Any = None
    output: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def graph_config(
        self,
        base: dict,
        *,
        run_name: str = "kompass-agent",
        tags: list[str] | None = None,
    ) -> dict:
        """Attach one callback at a LangChain root while preserving its config."""
        config = {**base, "run_name": run_name}
        if self.callback is not None:
            config["callbacks"] = [self.callback]
        config["tags"] = ["kompass", *(tags or ["agent-turn"])]
        config["metadata"] = {
            "kompass.agent_mode": settings.agent_mode,
            "kompass.llm_provider": settings.llm_provider,
            **_compact_metadata(self.metadata),
        }
        return config

    def set_output(self, output: Any, **metadata: Any) -> None:
        self.output = output
        self.metadata.update(metadata)

    def event(
        self,
        name: str,
        *,
        input: Any = None,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
    ) -> None:
        if self.span is not None:
            self.span.create_event(
                name=name,
                input=_export_payload(input),
                output=_export_payload(output),
                metadata=metadata,
                level=level,
            )


@contextmanager
def agent_trace(
    *,
    name: str,
    thread_id: str,
    user_id: str | None,
    input: Any,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    observation_type: str = "agent",
) -> Iterator[AgentTrace]:
    """Create a single hierarchical trace for a request, or yield a no-op handle."""
    handle = AgentTrace(metadata=dict(metadata or {}))
    lf = client()
    if lf is None:
        yield handle
        return

    from langfuse import propagate_attributes
    from langfuse.langchain import CallbackHandler

    try:
        with (
            propagate_attributes(
                trace_name=name,
                session_id=thread_id,
                user_id=user_id,
                tags=["kompass", *(tags or [])],
            metadata=_compact_metadata(handle.metadata),
                environment=settings.langfuse_environment,
                version=settings.langfuse_release,
            ),
            lf.start_as_current_observation(
                name=name,
                as_type=observation_type,
                input=_export_payload(input),
                metadata=handle.metadata,
            ) as span,
        ):
            handle.span = span
            handle.trace_id = span.trace_id
            handle.trace_url = lf.get_trace_url(trace_id=span.trace_id)
            handle.callback = CallbackHandler(public_key=settings.langfuse_public_key)
            try:
                yield handle
            except Exception as exc:
                span.update(
                    level="ERROR",
                    status_message=f"{type(exc).__name__}: {exc}",
                    metadata=handle.metadata,
                )
                raise
            else:
                span.update(output=_export_payload(handle.output), metadata=handle.metadata)
    finally:
        # Export is asynchronous. Flushing at the HTTP boundary makes a trace
        # visible immediately in a local demo and avoids losing short-lived CLI runs.
        lf.flush()


def score_trace(
    trace_id: str,
    *,
    name: str,
    value: float | str,
    comment: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Attach user/evaluator feedback to a trace; false means tracing is off."""
    lf = client()
    if lf is None:
        return False
    lf.create_score(
        trace_id=trace_id,
        name=name,
        value=value,
        comment=comment,
        metadata=metadata,
    )
    lf.flush()
    return True


def shutdown() -> None:
    """Flush and close the background exporter during application shutdown."""
    lf = client()
    if lf is not None:
        lf.shutdown()


def token_usage(messages: list[Any]) -> dict[str, float | int]:
    """Aggregate provider-neutral token usage and estimate configured USD cost."""
    usages = [m.usage_metadata for m in messages if getattr(m, "usage_metadata", None)]
    input_tokens = sum(int(u.get("input_tokens", 0)) for u in usages)
    output_tokens = sum(int(u.get("output_tokens", 0)) for u in usages)
    cost = (
        input_tokens * settings.input_cost_per_million
        + output_tokens * settings.output_cost_per_million
    ) / 1_000_000
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cost_usd": round(cost, 6),
    }
