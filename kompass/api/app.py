"""FastAPI surface for the Kompass agent.

Three endpoints over the same durable graph the demo script drives:
POST /chat starts (or continues) a thread, POST /resume feeds reviewer
decisions into a paused HITL run, GET /runs/{thread_id} inspects a thread.
The agent and its SQLite checkpointer are built once at startup, so a run
paused by one request can be resumed by another — or by a different surface
entirely — via the shared thread_id.

Run:  uvicorn kompass.api.app:app --port 8000   (or `make api`)
"""

from contextlib import asynccontextmanager
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import BaseModel

from kompass.config import ROOT, settings
from kompass.graph.agent import build_agent


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncSqliteSaver.from_conn_string(str(ROOT / settings.sqlite_checkpoint)) as saver:
        app.state.agent = await build_agent(saver)
        yield


app = FastAPI(title="Kompass API", lifespan=lifespan)


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None


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
    state = await app.state.agent.ainvoke({"messages": [("user", req.message)]}, _config(thread_id))
    return _run_response(thread_id, state)


@app.post("/resume")
async def resume(req: ResumeRequest) -> dict:
    decisions = [d.model_dump(exclude_none=True) for d in req.decisions]
    state = await app.state.agent.ainvoke(
        Command(resume={"decisions": decisions}), _config(req.thread_id)
    )
    return _run_response(req.thread_id, state)


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
