"""FastAPI surface for the Kompass agent.

Three endpoints over the same durable graph the demo script drives:
POST /chat starts (or continues) a thread, POST /resume feeds reviewer
decisions into a paused HITL run, GET /runs/{thread_id} inspects a thread.
The agent and its SQLite checkpointer are built once at startup, so a run
paused by one request can be resumed by another — or by a different surface
entirely — via the shared thread_id.

Run:  uvicorn kompass.api.app:app --port 8000   (or `make api`)
"""

import json
from contextlib import asynccontextmanager
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessageChunk
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import BaseModel

from kompass.config import ROOT, settings
from kompass.graph.agent import build_agent
from kompass.models import cache
from kompass.observability import agent_trace, score_trace, shutdown, token_usage
from kompass.prompts import prompt_manifest


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncSqliteSaver.from_conn_string(str(ROOT / settings.sqlite_checkpoint)) as saver:
        app.state.agent = await build_agent(saver)
        try:
            yield
        finally:
            shutdown()


app = FastAPI(title="Kompass API", lifespan=lifespan)


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None
    user_id: str | None = None


class EditedAction(BaseModel):
    name: str
    args: dict


class Decision(BaseModel):
    type: Literal["approve", "edit", "reject"]
    edited_action: EditedAction | None = None
    message: str | None = None


class ResumeRequest(BaseModel):
    thread_id: str
    decisions: list[Decision]
    user_id: str | None = None


class FeedbackRequest(BaseModel):
    trace_id: str
    rating: Literal["positive", "negative"]
    comment: str | None = None


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


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
    with agent_trace(
        name="kompass-chat-turn",
        thread_id=thread_id,
        user_id=req.user_id,
        input={"message": req.message},
        tags=["api", "chat"],
        metadata={"prompt_versions": prompt_manifest()},
    ) as trace:
        # Only a fresh standalone question is cacheable. A first-turn completed
        # run is read-only because write tools always pause at HITL.
        fresh = req.thread_id is None
        hit = cache.lookup(req.message) if fresh else None
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

        config = trace.graph_config(_config(thread_id))
        state = await app.state.agent.ainvoke({"messages": [("user", req.message)]}, config)
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
            cache.store(req.message, resp["answer"])
        trace.set_output(resp, cache_hit=False, **token_usage(state.get("messages", [])))
        return resp


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """Streaming variant of /chat: SSE with a {"delta": ...} event per AI text
    chunk, then a final {"done": true, ...} event carrying the run outcome.
    If the run pauses for approval, the final event says awaiting_approval."""
    thread_id = req.thread_id or uuid4().hex

    async def events():
        with agent_trace(
            name="kompass-stream-turn",
            thread_id=thread_id,
            user_id=req.user_id,
            input={"message": req.message},
            tags=["api", "stream"],
            metadata={"prompt_versions": prompt_manifest()},
        ) as trace:
            async for chunk, meta in app.state.agent.astream(
                {"messages": [("user", req.message)]},
                trace.graph_config(_config(thread_id)),
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
            snapshot = await app.state.agent.aget_state(_config(thread_id))
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
    with agent_trace(
        name="kompass-hitl-resume",
        thread_id=req.thread_id,
        user_id=req.user_id,
        input={"decisions": decisions},
        tags=["api", "hitl", "resume"],
        metadata={"prompt_versions": prompt_manifest()},
    ) as trace:
        trace.event("human-review-decision", input=decisions)
        state = await app.state.agent.ainvoke(
            Command(resume={"decisions": decisions}),
            trace.graph_config(_config(req.thread_id), run_name="kompass-agent-resume"),
        )
        resp = _run_response(req.thread_id, state)
        resp.update({"trace_id": trace.trace_id, "trace_url": trace.trace_url})
        trace.set_output(resp, **token_usage(state.get("messages", [])))
        return resp


@app.post("/feedback")
async def feedback(req: FeedbackRequest) -> dict:
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
async def get_run(thread_id: str) -> dict:
    snapshot = await app.state.agent.aget_state(_config(thread_id))
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
