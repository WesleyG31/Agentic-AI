"""FastAPI surface for the Kompass agent.

Three endpoints over the same durable graph the demo script drives:
POST /chat starts (or continues) a thread, POST /resume feeds reviewer
decisions into a paused HITL run, GET /runs/{thread_id} inspects a thread.
The agent and its configured durable checkpointer are built once at startup, so a run
paused by one request can be resumed by another — or by a different surface
entirely — via the shared thread_id.

Run:  uvicorn kompass.api.app:app --port 8000   (or `make api`)
"""

import asyncio
import json
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import AIMessageChunk
from langgraph.types import Command
from pydantic import BaseModel, Field

from kompass.config import settings
from kompass.graph.agent import build_agent
from kompass.models import cache
from kompass.observability import agent_trace, score_trace, shutdown, token_usage
from kompass.persistence import (
    ExecutionCapacityExceeded,
    ExecutionLimiter,
    checkpoint_saver,
    register_workflow_thread,
)
from kompass.prompts import prompt_manifest
from kompass.runtime import RuntimeContext, runtime_scope, tenant_scoped_id
from kompass.security.identity import TOOL_SCOPES
from kompass.security.oidc import (
    OIDCAuthenticationError,
    OIDCConfig,
    OIDCVerifier,
    get_verified_principal,
    verified_principal_scope,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with checkpoint_saver() as saver:
        app.state.agent = await build_agent(saver)
        app.state.execution_limiter = ExecutionLimiter(
            settings.execution_max_concurrency, settings.execution_queue_capacity
        )
        try:
            yield
        finally:
            shutdown()


app = FastAPI(title="Kompass API", lifespan=lifespan)


@lru_cache(maxsize=8)
def _oidc_verifier(
    issuer: str,
    audience: str,
    discovery_url: str,
    jwks_url: str,
    timeout_seconds: float,
    clock_skew_seconds: int,
    allow_http: bool,
) -> OIDCVerifier:
    return OIDCVerifier(
        OIDCConfig(
            issuer=issuer,
            audience=audience,
            discovery_url=discovery_url,
            jwks_url=jwks_url,
            timeout_seconds=timeout_seconds,
            clock_skew_seconds=clock_skew_seconds,
            allow_http=allow_http,
        )
    )


def _requires_identity(path: str) -> bool:
    return path in {"/chat", "/chat/stream", "/resume", "/feedback"} or path.startswith(
        "/runs/"
    )


def _security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def ingress_security(request: Request, call_next):
    """Bound request size, verify OIDC at ingress, and add API security headers."""
    response = None
    try:
        raw_length = request.headers.get("content-length")
        if raw_length is not None:
            try:
                content_length = int(raw_length)
            except ValueError:
                return _security_headers(
                    JSONResponse(status_code=400, content={"detail": "invalid content length"})
                )
            if content_length > settings.max_request_bytes:
                return _security_headers(
                    JSONResponse(status_code=413, content={"detail": "request body too large"})
                )
        if settings.auth_mode == "oidc" and _requires_identity(request.url.path):
            authorization = request.headers.get("authorization", "")
            scheme, separator, token = authorization.partition(" ")
            if scheme.casefold() != "bearer" or not separator or not token.strip():
                raise OIDCAuthenticationError("bearer token required")
            verifier = _oidc_verifier(
                settings.oidc_issuer_url,
                settings.oidc_audience,
                settings.oidc_discovery_url,
                settings.oidc_jwks_url,
                settings.oidc_timeout_seconds,
                settings.oidc_clock_skew_seconds,
                settings.environment != "production",
            )
            principal = await verifier.verify(token.strip())
            async with verified_principal_scope(principal):
                response = await call_next(request)
        else:
            response = await call_next(request)
    except (OIDCAuthenticationError, ValueError):
        response = JSONResponse(
            status_code=401,
            content={"detail": "authentication failed"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _security_headers(response)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=32_000)
    thread_id: str | None = Field(default=None, min_length=1, max_length=256)
    user_id: str | None = Field(default=None, min_length=1, max_length=256)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=128)
    correlation_id: str | None = Field(default=None, min_length=1, max_length=256)
    timezone: str | None = Field(default=None, min_length=1, max_length=128)
    locale: str | None = Field(default=None, min_length=2, max_length=32)


class EditedAction(BaseModel):
    name: str
    args: dict


class Decision(BaseModel):
    type: Literal["approve", "edit", "reject"]
    edited_action: EditedAction | None = None
    message: str | None = None


class ResumeRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=256)
    decisions: list[Decision] = Field(min_length=1, max_length=16)
    user_id: str | None = Field(default=None, min_length=1, max_length=256)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=128)
    correlation_id: str | None = Field(default=None, min_length=1, max_length=256)
    timezone: str | None = Field(default=None, min_length=1, max_length=128)
    locale: str | None = Field(default=None, min_length=2, max_length=32)


class FeedbackRequest(BaseModel):
    trace_id: str = Field(min_length=1, max_length=256)
    rating: Literal["positive", "negative"]
    comment: str | None = Field(default=None, max_length=2_000)


def _runtime_context(req: ChatRequest | ResumeRequest) -> RuntimeContext:
    if settings.auth_mode == "oidc":
        principal = get_verified_principal()
        if principal is None:
            raise HTTPException(status_code=401, detail="verified identity required")
        if req.tenant_id is not None and req.tenant_id != principal.tenant_id:
            raise HTTPException(status_code=403, detail="tenant claim mismatch")
        if req.user_id is not None and req.user_id.casefold() != principal.subject.casefold():
            raise HTTPException(status_code=403, detail="subject claim mismatch")
        return RuntimeContext.create(
            tenant_id=principal.tenant_id,
            user_id=principal.subject,
            timezone_name=req.timezone or settings.default_timezone,
            locale=req.locale or settings.default_locale,
            correlation_id=req.correlation_id,
            scopes=principal.scopes,
            roles=principal.roles,
            principal_kind=principal.kind,
        )
    # Local mode is explicitly a development adapter and is never selected implicitly.
    return RuntimeContext.create(
        tenant_id=req.tenant_id or settings.default_tenant_id,
        user_id=req.user_id or "anonymous-local-user",
        timezone_name=req.timezone or settings.default_timezone,
        locale=req.locale or settings.default_locale,
        correlation_id=req.correlation_id,
        scopes=frozenset(TOOL_SCOPES.values()),
        roles={"local-support"},
    )


def _config(thread_id: str, context: RuntimeContext) -> dict:
    storage_thread_id = tenant_scoped_id(context, thread_id)
    register_workflow_thread(context, storage_thread_id)
    return {"configurable": {"thread_id": storage_thread_id}}


def _trace_metadata(context: RuntimeContext) -> dict:
    return {
        "prompt_versions": prompt_manifest(),
        "request_id": context.request_id,
        "correlation_id": context.correlation_id,
        "tenant_partition": tenant_scoped_id(context, "telemetry")[:12],
    }


def _run_response(thread_id: str, state: dict) -> dict:
    """Convert a graph state into the API response: paused runs surface their
    pending approval cards, finished runs surface the final answer."""
    pending = [
        {"name": req["name"], "args": req["args"], "description": req.get("description")}
        for interrupt in state.get("__interrupt__", ())
        for req in interrupt.value["action_requests"]
    ]
    if pending:
        return {
            "thread_id": thread_id,
            "status": "awaiting_approval",
            "answer": None,
            "pending_actions": pending,
        }
    return {
        "thread_id": thread_id,
        "status": "completed",
        "answer": state["messages"][-1].content,
        "pending_actions": None,
    }


@app.post("/chat")
async def chat(req: ChatRequest) -> dict:
    thread_id = req.thread_id or uuid4().hex
    context = _runtime_context(req)
    with runtime_scope(context), agent_trace(
        name="kompass-chat-turn",
        thread_id=tenant_scoped_id(context, thread_id),
        user_id=context.user_id,
        input={"message": req.message},
        tags=["api", "chat"],
        metadata=_trace_metadata(context),
    ) as trace:
        # Only a fresh standalone question is cacheable. A first-turn completed
        # run is read-only because write tools always pause at HITL.
        fresh = req.thread_id is None
        hit = cache.lookup(req.message, tenant_id=context.tenant_id) if fresh else None
        trace.event("semantic-cache", input=req.message, output={"hit": bool(hit)})
        if hit:
            resp = {
                "thread_id": thread_id,
                "status": "completed",
                "answer": hit,
                "pending_actions": None,
                "trace_id": trace.trace_id,
                "trace_url": trace.trace_url,
            }
            trace.set_output(resp, cache_hit=True, **token_usage([]))
            return resp

        config = trace.graph_config(_config(thread_id, context))
        try:
            async with (
                app.state.execution_limiter.slot(
                    wait_seconds=settings.execution_deadline_seconds
                ),
                asyncio.timeout(settings.execution_deadline_seconds),
            ):
                state = await app.state.agent.ainvoke(
                    {"messages": [("user", req.message)]}, config
                )
        except ExecutionCapacityExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(
                status_code=504, detail="agent execution deadline exceeded"
            ) from exc
        resp = _run_response(thread_id, state)
        resp.update({"trace_id": trace.trace_id, "trace_url": trace.trace_url})
        if resp["status"] == "awaiting_approval":
            trace.event("human-approval-required", output=resp["pending_actions"])
        cacheable = (
            fresh
            and resp["status"] == "completed"
            and cache.can_store(state.get("messages", []), resp["answer"])
        )
        trace.event("semantic-cache-store", output={"stored": cacheable})
        if cacheable:
            cache.store(req.message, resp["answer"], tenant_id=context.tenant_id)
        trace.set_output(resp, cache_hit=False, **token_usage(state.get("messages", [])))
        return resp


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """Streaming variant of /chat: SSE with a {"delta": ...} event per AI text
    chunk, then a final {"done": true, ...} event carrying the run outcome.
    If the run pauses for approval, the final event says awaiting_approval."""
    thread_id = req.thread_id or uuid4().hex
    context = _runtime_context(req)

    async def events():
        with runtime_scope(context), agent_trace(
            name="kompass-stream-turn",
            thread_id=tenant_scoped_id(context, thread_id),
            user_id=context.user_id,
            input={"message": req.message},
            tags=["api", "stream"],
            metadata=_trace_metadata(context),
        ) as trace:
            try:
                async with (
                    app.state.execution_limiter.slot(
                        wait_seconds=settings.execution_deadline_seconds
                    ),
                    asyncio.timeout(settings.execution_deadline_seconds),
                ):
                    async for chunk, meta in app.state.agent.astream(
                        {"messages": [("user", req.message)]},
                        trace.graph_config(_config(thread_id, context)),
                        stream_mode="messages",
                    ):
                        # Only the main model's text reaches the client; tool results and
                        # the critic's structured review remain visible in the trace.
                        if (
                            meta.get("langgraph_node") == "model"
                            and isinstance(chunk, AIMessageChunk)
                            and chunk.text
                        ):
                            yield f"data: {json.dumps({'delta': chunk.text})}\n\n"
            except (ExecutionCapacityExceeded, TimeoutError) as exc:
                trace.event("execution-rejected", output={"reason": type(exc).__name__})
                unavailable = {
                    "done": True,
                    "status": "failed",
                    "error": "execution unavailable",
                }
                yield f"data: {json.dumps(unavailable)}\n\n"
                return
            snapshot = await app.state.agent.aget_state(_config(thread_id, context))
            run = _run_response(
                thread_id, {**snapshot.values, "__interrupt__": snapshot.interrupts}
            )
            final = {
                "done": True,
                "thread_id": thread_id,
                "status": run["status"],
                "pending_actions": run["pending_actions"],
                "trace_id": trace.trace_id,
                "trace_url": trace.trace_url,
            }
            if run["status"] == "awaiting_approval":
                trace.event("human-approval-required", output=run["pending_actions"])
            trace.set_output(final, **token_usage(snapshot.values.get("messages", [])))
            yield f"data: {json.dumps(final)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.post("/resume")
async def resume(req: ResumeRequest) -> dict:
    decisions = [d.model_dump(exclude_none=True) for d in req.decisions]
    context = _runtime_context(req)
    with runtime_scope(context), agent_trace(
        name="kompass-hitl-resume",
        thread_id=tenant_scoped_id(context, req.thread_id),
        user_id=context.user_id,
        input={"decisions": decisions},
        tags=["api", "hitl", "resume"],
        metadata=_trace_metadata(context),
    ) as trace:
        trace.event("human-review-decision", input=decisions)
        try:
            async with (
                app.state.execution_limiter.slot(
                    wait_seconds=settings.execution_deadline_seconds
                ),
                asyncio.timeout(settings.execution_deadline_seconds),
            ):
                state = await app.state.agent.ainvoke(
                    Command(resume={"decisions": decisions}),
                    trace.graph_config(
                        _config(req.thread_id, context), run_name="kompass-agent-resume"
                    ),
                )
        except ExecutionCapacityExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(
                status_code=504, detail="agent execution deadline exceeded"
            ) from exc
        resp = _run_response(req.thread_id, state)
        resp.update({"trace_id": trace.trace_id, "trace_url": trace.trace_url})
        trace.set_output(resp, **token_usage(state.get("messages", [])))
        return resp


@app.post("/feedback")
async def feedback(req: FeedbackRequest) -> dict:
    if settings.auth_mode != "local":
        raise HTTPException(
            status_code=503,
            detail="OIDC mode requires the verified-claims ingress adapter to be configured",
        )
    accepted = score_trace(
        req.trace_id,
        name="user_feedback",
        value=1.0 if req.rating == "positive" else 0.0,
        comment=req.comment,
        metadata={"source": "kompass-ui", "rating": req.rating},
    )
    if not accepted:
        raise HTTPException(status_code=503, detail="Langfuse observability is disabled")
    return {"accepted": True, "trace_id": req.trace_id}


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "llm_provider": settings.llm_provider,
        "agent_mode": settings.agent_mode,
        "langfuse_enabled": settings.langfuse_enabled,
        "langfuse_configured": bool(settings.langfuse_public_key and settings.langfuse_secret_key),
    }


@app.get("/runs/{thread_id}")
async def get_run(
    thread_id: str,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> dict:
    context = _runtime_context(
        ResumeRequest(
            thread_id=thread_id,
            decisions=[Decision(type="reject")],
            tenant_id=tenant_id,
            user_id=user_id,
        )
    )
    with runtime_scope(context):
        snapshot = await app.state.agent.aget_state(_config(thread_id, context))
    messages = snapshot.values.get("messages", [])
    if not messages:
        return {
            "thread_id": thread_id,
            "status": "not_found",
            "message_count": 0,
            "last_message": None,
            "pending_actions": None,
        }
    run = _run_response(thread_id, {**snapshot.values, "__interrupt__": snapshot.interrupts})
    return {
        "thread_id": thread_id,
        "status": run["status"],
        "message_count": len(messages),
        "last_message": messages[-1].content,
        "pending_actions": run["pending_actions"],
    }
