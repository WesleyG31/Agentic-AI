"""Identity claims adaptation and deterministic authorization policy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from kompass.runtime import RuntimeContext
from kompass.security.audit import audit


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant_id: str
    kind: Literal["user", "service", "agent"] = "user"
    scopes: frozenset[str] = field(default_factory=frozenset)
    roles: frozenset[str] = field(default_factory=frozenset)
    issuer: str | None = None

    @classmethod
    def from_runtime(cls, context: RuntimeContext) -> Principal:
        kind = context.principal_kind
        if kind not in {"user", "service", "agent"}:
            raise ValueError(f"unsupported principal kind: {kind}")
        return cls(
            subject=context.user_id,
            tenant_id=context.tenant_id,
            kind=kind,  # type: ignore[arg-type]
            scopes=context.scopes,
            roles=context.roles,
        )


class ClaimsIdentityAdapter:
    """Maps already-verified OAuth/OIDC claims; it deliberately does not decode JWTs."""

    def from_verified_claims(self, claims: Mapping[str, Any]) -> Principal:
        subject = str(claims.get("sub", "")).strip()
        tenant = str(claims.get("tenant_id") or claims.get("tid") or "").strip()
        if not subject or not tenant:
            raise ValueError("verified claims require sub and tenant_id/tid")
        raw_scopes = claims.get("scope", claims.get("scp", ""))
        scopes = (
            frozenset(str(raw_scopes).split())
            if isinstance(raw_scopes, str)
            else frozenset(str(item) for item in raw_scopes or ())
        )
        raw_roles = claims.get("roles", ())
        roles = (
            frozenset(raw_roles.split())
            if isinstance(raw_roles, str)
            else frozenset(str(item) for item in raw_roles or ())
        )
        return Principal(
            subject=subject,
            tenant_id=tenant,
            kind="service" if claims.get("client_id") else "user",
            scopes=scopes,
            roles=roles,
            issuer=str(claims.get("iss")) if claims.get("iss") else None,
        )


@dataclass(frozen=True)
class AuthorizationRequest:
    principal: Principal
    action: str
    resource: str
    tenant_id: str
    tool: str | None = None
    environment: str = "development"
    agent_capabilities: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    reason: str
    policy_id: str = "kompass-local-policy-v1"
    required_scope: str | None = None


class PolicyEngine(Protocol):
    def decide(self, request: AuthorizationRequest) -> AuthorizationDecision: ...


TOOL_SCOPES = {
    "search_docs": "documents:read",
    "get_schema": "operations:read",
    "query_database": "operations:read",
    "get_ticket": "tickets:read",
    "research": "research:read",
    "analyze": "analytics:read",
    "save_memory": "memory:write",
    "recall_memories": "memory:read",
    "create_refund": "refunds:create",
    "update_ticket": "tickets:write",
}


class LocalPolicyEngine:
    """Small deny-by-default RBAC/scope engine for local and deterministic tests."""

    def __init__(self, tool_scopes: Mapping[str, str] | None = None) -> None:
        self._tool_scopes = dict(tool_scopes or TOOL_SCOPES)

    def decide(self, request: AuthorizationRequest) -> AuthorizationDecision:
        required = self._tool_scopes.get(request.tool or "")
        if request.principal.tenant_id != request.tenant_id:
            decision = AuthorizationDecision(
                False, "cross-tenant access denied", required_scope=required
            )
        elif request.tool and request.tool not in self._tool_scopes:
            decision = AuthorizationDecision(False, "tool has no authorization policy")
        elif request.agent_capabilities and request.tool not in request.agent_capabilities:
            decision = AuthorizationDecision(
                False, "tool is outside the agent capability set", required_scope=required
            )
        elif (
            required
            and required not in request.principal.scopes
            and "*" not in request.principal.scopes
        ):
            decision = AuthorizationDecision(
                False, f"missing required scope: {required}", required_scope=required
            )
        else:
            decision = AuthorizationDecision(True, "policy allowed", required_scope=required)
        audit(
            "authorization.decision",
            decision="allow" if decision.allowed else "deny",
            reason=decision.reason,
            resource=request.resource,
            action=request.action,
            metadata={"tool": request.tool or "", "policy_id": decision.policy_id},
        )
        return decision
