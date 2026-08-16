from concurrent.futures import ThreadPoolExecutor
from threading import Event

from langchain.agents.middleware import ToolCallRequest
from langchain_core.messages import ToolMessage

from kompass.actions.executor import (
    ActionExecutor,
    ActionPolicy,
    ActionRequest,
    ActionStatus,
    ApprovalState,
    InMemoryReceiptStore,
)
from kompass.actions.middleware import ActionExecutionMiddleware
from kompass.runtime import RuntimeContext, runtime_scope
from kompass.security.identity import LocalPolicyEngine, Principal


def _request(**changes) -> ActionRequest:
    values = {
        "name": "create_refund",
        "arguments": {"order_id": 42, "amount_eur": 10.0},
        "idempotency_key": "refund-42",
        "correlation_id": "corr-1",
        "tenant_id": "tenant-a",
        "principal": Principal(
            "support-1", "tenant-a", scopes=frozenset({"refunds:create"})
        ),
        "approval": ApprovalState.APPROVED,
        "max_attempts": 2,
    }
    values.update(changes)
    return ActionRequest(**values)


def _executor() -> ActionExecutor:
    return ActionExecutor(LocalPolicyEngine(), InMemoryReceiptStore())


def test_duplicate_invocation_returns_receipt_without_repeating_effect():
    executor = _executor()
    calls: list[str] = []

    def effect():
        calls.append("effect")
        return {"refund_id": 1}

    first = executor.execute(_request(), effect, verifier=lambda result: True)
    duplicate = executor.execute(_request(), effect, verifier=lambda result: True)

    assert first.status == ActionStatus.SUCCEEDED
    assert duplicate.duplicate is True
    assert calls == ["effect"]


def test_concurrent_claims_execute_the_effect_once():
    executor = _executor()
    entered = Event()
    release = Event()
    calls: list[str] = []

    def effect():
        calls.append("effect")
        entered.set()
        release.wait(timeout=2)
        return {"refund_id": 1}

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(executor.execute, _request(), effect, verifier=lambda _: True)
        assert entered.wait(timeout=2)
        second = pool.submit(executor.execute, _request(), effect, verifier=lambda _: True)
        duplicate = second.result(timeout=2)
        release.set()
        completed = first.result(timeout=2)

    assert completed.status == ActionStatus.SUCCEEDED
    assert duplicate.duplicate is True
    assert calls == ["effect"]


def test_idempotency_key_is_bound_to_payload():
    executor = _executor()
    executor.execute(_request(), lambda: {"refund_id": 1}, verifier=lambda _: True)
    conflict = executor.execute(
        _request(arguments={"order_id": 42, "amount_eur": 11.0}),
        lambda: {"refund_id": 2},
        verifier=lambda _: True,
    )

    assert conflict.status == ActionStatus.FAILED
    assert conflict.duplicate is True
    assert conflict.error == "idempotency key reused with a different payload"


def test_timeout_is_retried_with_a_bound_and_then_verified():
    executor = _executor()
    attempts = 0

    def effect():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("upstream timed out")
        return {"refund_id": 2}

    receipt = executor.execute(_request(), effect, verifier=lambda result: result["refund_id"] == 2)
    assert receipt.status == ActionStatus.SUCCEEDED
    assert receipt.attempts == 2


def test_denied_pending_and_rejected_actions_never_execute():
    calls: list[str] = []

    def effect():
        calls.append("effect")

    denied = _executor().execute(
        _request(principal=Principal("support-1", "tenant-a")),
        effect,
        verifier=lambda result: True,
    )
    pending = _executor().execute(
        _request(idempotency_key="pending", approval=ApprovalState.PENDING),
        effect,
        verifier=lambda result: True,
    )
    rejected = _executor().execute(
        _request(idempotency_key="rejected", approval=ApprovalState.REJECTED),
        effect,
        verifier=lambda result: True,
    )
    assert [denied.status, pending.status, rejected.status] == [
        ActionStatus.DENIED,
        ActionStatus.APPROVAL_REQUIRED,
        ActionStatus.REJECTED,
    ]
    assert calls == []


def test_false_business_success_triggers_compensation():
    compensated: list[int] = []
    receipt = _executor().execute(
        _request(),
        lambda: {"refund_id": 7, "state": "missing"},
        verifier=lambda result: result["state"] == "recorded",
        compensate=lambda result: compensated.append(result["refund_id"]),
    )
    assert receipt.status == ActionStatus.COMPENSATED
    assert receipt.verified is False
    assert compensated == [7]


def test_precondition_failure_is_closed_without_effect():
    receipt = _executor().execute(
        _request(),
        lambda: {"should": "not run"},
        verifier=lambda result: True,
        policy=ActionPolicy(preconditions=(lambda request: False,)),
    )
    assert receipt.status == ActionStatus.FAILED
    assert receipt.error == "precondition failed"


def test_verifier_and_compensation_failure_become_failed_receipt():
    receipt = _executor().execute(
        _request(),
        lambda: {"refund_id": 9},
        verifier=lambda result: (_ for _ in ()).throw(RuntimeError("verify unavailable")),
        compensate=lambda result: (_ for _ in ()).throw(RuntimeError("rollback unavailable")),
    )

    assert receipt.status == ActionStatus.VERIFICATION_FAILED
    assert "verification error: RuntimeError" in receipt.error
    assert "compensation failed: RuntimeError" in receipt.error


def test_precondition_exception_fails_closed_without_effect():
    called = False

    def effect():
        nonlocal called
        called = True

    receipt = _executor().execute(
        _request(),
        effect,
        verifier=lambda result: True,
        policy=ActionPolicy(
            preconditions=(
                lambda request: (_ for _ in ()).throw(RuntimeError("sensitive detail")),
            )
        ),
    )

    assert receipt.status == ActionStatus.FAILED
    assert receipt.error == "precondition error: RuntimeError"
    assert not called


async def test_hitl_inner_middleware_executes_verified_action_once():
    calls: list[str] = []
    executor = _executor()
    middleware = ActionExecutionMiddleware(
        executor=executor,
        verifier=lambda tool, arguments, result: result == "business state recorded",
        compensator=lambda tool, arguments, result, snapshot: None,
    )
    request = ToolCallRequest(
        tool_call={
            "name": "create_refund",
            "args": {"order_id": 42, "amount_eur": 10.0},
            "id": "call-1",
            "type": "tool_call",
        },
        tool=None,
        state={},
        runtime=None,
    )

    async def handler(_request):
        calls.append("effect")
        return ToolMessage(
            content="business state recorded", tool_call_id="call-1", name="create_refund"
        )

    context = RuntimeContext.create(
        tenant_id="tenant-a",
        user_id="support-1",
        scopes={"refunds:create"},
        correlation_id="corr-1",
    )
    with runtime_scope(context):
        first = await middleware.awrap_tool_call(request, handler)
        duplicate = await middleware.awrap_tool_call(request, handler)

    assert first.status == "success"
    assert duplicate.status == "success"
    assert calls == ["effect"]
