"""A2A 1.0 server using the official SDK's task lifecycle and transports."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from a2a.auth.user import User
from a2a.helpers import (
    new_task_from_user_message,
    new_text_artifact_update_event,
    new_text_status_update_event,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    ServerCallContextBuilder,
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
    create_rest_routes,
)
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import TaskState
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from kompass.a2a.card import agent_card
from kompass.config import settings
from kompass.graph.workers import research
from kompass.runtime import RuntimeContext, runtime_scope
from kompass.security.audit import audit
from kompass.security.trust import ContentOrigin, TrustBoundary, TrustedContent


@dataclass(frozen=True)
class A2APrincipal:
    subject: str
    tenant_id: str
    scopes: frozenset[str]


class A2ATokenVerifier:
    """Local deterministic token adapter; production should validate external OIDC JWTs."""

    def __init__(self, token: str, principal: A2APrincipal) -> None:
        self._token = token
        self._principal = principal

    def verify(self, token: str) -> A2APrincipal | None:
        if not self._token or not hmac.compare_digest(token, self._token):
            return None
        return self._principal


class AuthenticatedA2AUser(User):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._name


class AuthenticatedContextBuilder(ServerCallContextBuilder):
    """Build tenant context only from middleware-verified identity, never request payloads."""

    def build(self, request: Request) -> ServerCallContext:
        principal: A2APrincipal | None = request.scope.get("a2a_principal")
        if principal is None:
            raise PermissionError("A2A authentication context missing")
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower()
            in {"a2a-version", "x-request-id", "x-correlation-id", "traceparent"}
        }
        return ServerCallContext(
            user=AuthenticatedA2AUser(principal.subject),
            tenant=principal.tenant_id,
            state={"principal": principal, "headers": headers},
        )


ResearchFn = Callable[[str], Awaitable[str]]


async def _default_research(question: str) -> str:
    return str(await research.ainvoke({"question": question}))


class KompassResearchExecutor(AgentExecutor):
    def __init__(self, research_fn: ResearchFn = _default_research) -> None:
        self._research = research_fn
        self._semaphore = asyncio.Semaphore(settings.a2a_max_concurrency)

    def _runtime(self, context: RequestContext) -> RuntimeContext:
        principal: A2APrincipal = context.call_context.state["principal"]
        headers = context.call_context.state.get("headers", {})
        return RuntimeContext.create(
            # The request protobuf also has a tenant field; it is attacker-controlled.
            # Only the verified principal may select the runtime/storage tenant.
            tenant_id=principal.tenant_id,
            user_id=principal.subject,
            request_id=headers.get("x-request-id"),
            correlation_id=headers.get("x-correlation-id"),
            timezone_name=settings.default_timezone,
            locale=settings.default_locale,
            scopes=principal.scopes,
            principal_kind="service",
        )

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.message is None:
            raise ValueError("A2A message is required")
        task = context.current_task or new_task_from_user_message(context.message)
        await event_queue.enqueue_event(task)
        await event_queue.enqueue_event(
            new_text_status_update_event(
                task.id, task.context_id, TaskState.TASK_STATE_WORKING, "Research in progress"
            )
        )
        question = context.get_user_input()
        runtime = self._runtime(context)
        with runtime_scope(runtime):
            trust = TrustBoundary().evaluate(
                TrustedContent(
                    question,
                    ContentOrigin.REMOTE_AGENT,
                    provenance=f"a2a:task:{task.id}",
                ),
                purpose="model",
            )
            if trust.suspicious:
                await event_queue.enqueue_event(
                    new_text_status_update_event(
                        task.id,
                        task.context_id,
                        TaskState.TASK_STATE_REJECTED,
                        "Remote content was rejected by trust policy",
                    )
                )
                return
            try:
                async with self._semaphore, asyncio.timeout(settings.a2a_timeout_seconds):
                    answer = await self._research(question)
            except TimeoutError:
                await event_queue.enqueue_event(
                    new_text_status_update_event(
                        task.id,
                        task.context_id,
                        TaskState.TASK_STATE_FAILED,
                        "Research deadline exceeded",
                    )
                )
                return
            except Exception as exc:
                audit(
                    "a2a.task",
                    decision="failed",
                    reason=type(exc).__name__,
                    resource=f"a2a:task:{task.id}",
                    action="research",
                )
                await event_queue.enqueue_event(
                    new_text_status_update_event(
                        task.id,
                        task.context_id,
                        TaskState.TASK_STATE_FAILED,
                        "Research provider failed",
                    )
                )
                return
            await event_queue.enqueue_event(
                new_text_artifact_update_event(
                    task.id,
                    task.context_id,
                    name="research-result",
                    text=answer,
                    last_chunk=True,
                )
            )
            await event_queue.enqueue_event(
                new_text_status_update_event(
                    task.id, task.context_id, TaskState.TASK_STATE_COMPLETED, "Research complete"
                )
            )
            audit(
                "a2a.task",
                decision="complete",
                reason="remote research completed",
                resource=f"a2a:task:{task.id}",
                action="research",
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        if not context.task_id or not context.context_id:
            raise ValueError("task_id and context_id are required for cancellation")
        await event_queue.enqueue_event(
            new_text_status_update_event(
                context.task_id,
                context.context_id,
                TaskState.TASK_STATE_CANCELED,
                "Task canceled",
            )
        )


def _owner_scope(context: ServerCallContext) -> str:
    value = f"{context.tenant}\0{context.user.user_name}".encode()
    return hashlib.sha256(value).hexdigest()


def build_app(
    *,
    executor: AgentExecutor | None = None,
    token_verifier: A2ATokenVerifier | None = None,
    base_url: str | None = None,
) -> FastAPI:
    card = agent_card(base_url)
    verifier = token_verifier or A2ATokenVerifier(
        settings.a2a_dev_token,
        A2APrincipal(
            settings.a2a_dev_subject,
            settings.a2a_dev_tenant,
            frozenset(settings.a2a_dev_scopes.split()),
        ),
    )
    task_store = InMemoryTaskStore(owner_resolver=_owner_scope)
    handler = DefaultRequestHandler(
        agent_executor=executor or KompassResearchExecutor(),
        task_store=task_store,
        agent_card=card,
    )
    app = FastAPI(title="Kompass A2A 1.0")

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if request.url.path in {"/.well-known/agent-card.json", "/health"}:
            return await call_next(request)
        authorization = request.headers.get("authorization", "")
        scheme, _, credential = authorization.partition(" ")
        principal = verifier.verify(credential) if scheme.casefold() == "bearer" else None
        if principal is None or "research:read" not in principal.scopes:
            audit(
                "a2a.authentication",
                decision="deny",
                reason="invalid token or missing research:read scope",
                resource=request.url.path,
                action=request.method,
            )
            return JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        request.scope["a2a_principal"] = principal
        return await call_next(request)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "protocol": "A2A", "spec_version": "1.0"}

    context_builder = AuthenticatedContextBuilder()
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(
            handler, rpc_url="/a2a/jsonrpc", context_builder=context_builder
        ),
        rest_routes=create_rest_routes(
            handler, context_builder=context_builder, path_prefix="/a2a"
        ),
    )
    app.state.a2a_handler = handler
    app.state.a2a_task_store = task_store
    return app


app = build_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.a2a_port)
