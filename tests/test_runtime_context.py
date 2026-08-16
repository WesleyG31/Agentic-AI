from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from kompass.runtime import FrozenClock, RuntimeContext, get_runtime_context, runtime_scope


def test_frozen_clock_and_timezone_are_deterministic():
    clock = FrozenClock(datetime(2026, 8, 16, 10, 30, tzinfo=ZoneInfo("UTC")))
    context = RuntimeContext.create(
        tenant_id="tenant-a",
        user_id="user-1",
        clock=clock,
        timezone_name="Europe/Berlin",
        request_id="req-1",
    )

    assert context.current_time.isoformat() == "2026-08-16T12:30:00+02:00"
    assert context.current_date == "2026-08-16"
    assert "2026-08-16T12:30:00+02:00" in context.model_context()


def test_runtime_scope_does_not_leak_between_requests():
    context = RuntimeContext.create(tenant_id="tenant-a", user_id="user-1")
    with runtime_scope(context):
        assert get_runtime_context() is context
    with pytest.raises(RuntimeError, match="runtime context"):
        get_runtime_context()


def test_clock_must_be_timezone_aware():
    with pytest.raises(ValueError, match="timezone-aware"):
        FrozenClock(datetime(2026, 8, 16))

