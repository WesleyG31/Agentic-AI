from kompass.runtime import RuntimeContext, runtime_scope
from kompass.security.audit import InMemoryAuditSink, audit_scope
from kompass.security.identity import (
    AuthorizationRequest,
    ClaimsIdentityAdapter,
    LocalPolicyEngine,
    Principal,
)


def _request(principal: Principal, **changes) -> AuthorizationRequest:
    values = {
        "principal": principal,
        "action": "execute",
        "resource": "refund",
        "tenant_id": "tenant-a",
        "tool": "create_refund",
        "agent_capabilities": frozenset({"create_refund"}),
    }
    values.update(changes)
    return AuthorizationRequest(**values)


def test_authorized_access_and_structured_audit():
    principal = Principal("support-1", "tenant-a", scopes=frozenset({"refunds:create"}))
    context = RuntimeContext.create(
        tenant_id="tenant-a", user_id="support-1", scopes=principal.scopes
    )
    sink = InMemoryAuditSink()
    with runtime_scope(context), audit_scope(sink):
        decision = LocalPolicyEngine().decide(_request(principal))

    assert decision.allowed is True
    assert sink.events[0].tenant_id == "tenant-a"
    assert sink.events[0].decision == "allow"


def test_missing_scope_wrong_tenant_and_agent_privilege_escalation_are_denied():
    engine = LocalPolicyEngine()
    no_scope = Principal("support-1", "tenant-a")
    assert engine.decide(_request(no_scope)).reason == "missing required scope: refunds:create"

    wrong_tenant = Principal(
        "support-2", "tenant-b", scopes=frozenset({"refunds:create"})
    )
    assert engine.decide(_request(wrong_tenant)).reason == "cross-tenant access denied"

    read_agent = Principal(
        "researcher", "tenant-a", kind="agent", scopes=frozenset({"refunds:create"})
    )
    decision = engine.decide(
        _request(read_agent, agent_capabilities=frozenset({"search_docs", "query_database"}))
    )
    assert decision.allowed is False
    assert "capability" in decision.reason


def test_only_verified_claims_are_adapted_and_tenant_is_required():
    adapter = ClaimsIdentityAdapter()
    principal = adapter.from_verified_claims(
        {
            "sub": "service-1",
            "tenant_id": "tenant-a",
            "scope": "operations:read tickets:read",
            "client_id": "client-1",
            "iss": "https://issuer.example",
        }
    )
    assert principal.kind == "service"
    assert principal.scopes == frozenset({"operations:read", "tickets:read"})

