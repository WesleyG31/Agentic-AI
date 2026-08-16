"""LangGraph middleware that enforces runtime, authorization, and tool trust policy."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage, ToolMessage

from kompass.runtime import get_runtime_context
from kompass.security.identity import (
    AuthorizationRequest,
    LocalPolicyEngine,
    PolicyEngine,
    Principal,
)
from kompass.security.trust import ContentOrigin, TrustBoundary, TrustedContent

_READ_ACTIONS = {
    "search_docs",
    "get_schema",
    "query_database",
    "get_ticket",
    "research",
    "analyze",
    "recall_memories",
}
_ORIGINS = {
    "search_docs": ContentOrigin.DOCUMENT,
    "research": ContentOrigin.REMOTE_AGENT,
    "recall_memories": ContentOrigin.MEMORY,
}


class RuntimeContextMiddleware(AgentMiddleware):
    """Inject request time/locale as trusted data without encoding policy in a prompt."""

    def before_model(self, state, runtime):
        context = get_runtime_context(required=False)
        if context is None:
            return None
        marker = f"request-id: {context.request_id}"
        if any(marker in str(message.content) for message in state["messages"]):
            return None
        return {"messages": [SystemMessage(f"{context.model_context()}\n{marker}")]}


class AuthorizationMiddleware(AgentMiddleware):
    """Deny tool calls unless identity, tenant, scope, and agent capability all allow them."""

    def __init__(
        self,
        *,
        capabilities: set[str] | frozenset[str],
        policy_engine: PolicyEngine | None = None,
        environment: str = "development",
    ) -> None:
        self._capabilities = frozenset(capabilities)
        self._policy = policy_engine or LocalPolicyEngine()
        self._environment = environment

    def _decision(self, request) -> tuple[bool, str]:
        context = get_runtime_context(required=False)
        if context is None:
            return False, "runtime identity context is missing"
        tool = str(request.tool_call.get("name", ""))
        decision = self._policy.decide(
            AuthorizationRequest(
                principal=Principal.from_runtime(context),
                action="read" if tool in _READ_ACTIONS else "execute",
                resource=f"tool:{tool}",
                tenant_id=context.tenant_id,
                tool=tool,
                environment=self._environment,
                agent_capabilities=self._capabilities,
            )
        )
        return decision.allowed, decision.reason

    @staticmethod
    def _denied(request, reason: str) -> ToolMessage:
        tool = str(request.tool_call.get("name", "unknown"))
        return ToolMessage(
            content=f"Authorization denied for {tool}: {reason}",
            tool_call_id=str(request.tool_call.get("id", "authorization-denied")),
            name=tool,
            status="error",
        )

    def wrap_tool_call(self, request, handler: Callable):
        allowed, reason = self._decision(request)
        return handler(request) if allowed else self._denied(request, reason)

    async def awrap_tool_call(self, request, handler: Callable):
        allowed, reason = self._decision(request)
        return await handler(request) if allowed else self._denied(request, reason)


class ToolTrustMiddleware(AgentMiddleware):
    """Preserve tool evidence while making its untrusted status explicit to the model."""

    def __init__(self, boundary: TrustBoundary | None = None) -> None:
        self._boundary = boundary or TrustBoundary()

    def _wrap(self, request, result: Any):
        if not isinstance(result, ToolMessage):
            return result
        tool = str(request.tool_call.get("name", result.name or "unknown"))
        origin = _ORIGINS.get(tool, ContentOrigin.MCP)
        decision = self._boundary.evaluate(
            TrustedContent(
                str(result.content),
                origin,
                provenance=f"tool:{tool}:call:{request.tool_call.get('id', 'unknown')}",
            ),
            purpose="model",
        )
        return result.model_copy(update={"content": decision.rendered})

    def wrap_tool_call(self, request, handler: Callable):
        return self._wrap(request, handler(request))

    async def awrap_tool_call(self, request, handler: Callable):
        return self._wrap(request, await handler(request))

