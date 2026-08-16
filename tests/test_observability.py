"""The Langfuse adapter must remain a safe no-op for offline tests."""

from datetime import UTC, datetime

from kompass import observability
from kompass.runtime import FrozenClock, RuntimeContext, runtime_scope


def test_disabled_trace_preserves_graph_config(monkeypatch):
    monkeypatch.setattr(observability.settings, "langfuse_enabled", False)
    observability.client.cache_clear()
    with observability.agent_trace(
        name="test",
        thread_id="thread-1",
        user_id="user-1",
        input={"message": "hello"},
    ) as trace:
        config = trace.graph_config({"configurable": {"thread_id": "thread-1"}})
        trace.set_output({"answer": "hi"})
    assert trace.trace_id is None
    assert "callbacks" not in config
    assert config["configurable"]["thread_id"] == "thread-1"
    assert config["metadata"]["kompass.llm_provider"] in {"ollama", "openai"}


def test_token_usage_includes_configured_cost(monkeypatch):
    class Message:
        usage_metadata = {"input_tokens": 100, "output_tokens": 20}

    monkeypatch.setattr(observability.settings, "input_cost_per_million", 2.0)
    monkeypatch.setattr(observability.settings, "output_cost_per_million", 10.0)
    assert observability.token_usage([Message()]) == {
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
        "cost_usd": 0.0004,
    }


def test_langfuse_mask_accepts_v4_keyword_and_redacts_pii():
    assert observability._mask_payload(data={"email": "person@example.com"}) == {
        "email": "[redacted-email]"
    }


def test_content_export_is_hash_only_unless_explicitly_enabled(monkeypatch):
    monkeypatch.setattr(observability.settings, "langfuse_capture_content", False)
    exported = observability._export_payload({"prompt": "private customer text"})
    assert set(exported) == {"content_type", "content_sha256", "content_bytes"}
    assert "private" not in str(exported)


def test_vendor_neutral_metric_correlates_and_redacts(monkeypatch):
    context = RuntimeContext.create(
        tenant_id="tenant-a",
        user_id="operator",
        clock=FrozenClock(datetime(2026, 8, 16, 12, tzinfo=UTC)),
        request_id="request-1",
        correlation_id="correlation-1",
    )
    sink = observability.InMemoryTelemetrySink()
    with runtime_scope(context), observability.telemetry_scope(sink):
        point = observability.record_metric(
            "kompass.tool.latency", 12.5, unit="ms", attributes={"tool": "search", "token": "x"}
        )

    assert point.request_id == "request-1"
    assert point.correlation_id == "correlation-1"
    assert point.tenant_partition and point.tenant_partition != "tenant-a"
    assert point.attributes == {"tool": "search", "token": "[redacted]"}
