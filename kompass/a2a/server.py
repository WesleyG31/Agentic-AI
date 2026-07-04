"""A2A server: Kompass as a discoverable, invokable specialist research agent.

The horizontal counterpart to MCP — where MCP wires this agent to its tools,
A2A lets other agents discover Kompass (GET /.well-known/agent.json, signed
card) and delegate work to it (POST /a2a, JSON-RPC 2.0 ``tasks/send``). Tasks
run the same ``research`` worker the multi-agent supervisor delegates to:
read-only MCP tools by construction, so this surface needs no HITL gate.

Inbound payloads cross a trust boundary and are Pydantic-validated; malformed
params come back as JSON-RPC -32602 rather than a stack trace.

Run:  python -m kompass.a2a.server   (port from KOMPASS_A2A_PORT)
"""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, ValidationError

from kompass.a2a.card import agent_card, sign
from kompass.config import settings
from kompass.graph.workers import research

app = FastAPI(title="Kompass A2A")


class Part(BaseModel):
    text: str


class Message(BaseModel):
    role: Literal["user"]
    parts: list[Part]


class TaskSendParams(BaseModel):
    id: str
    message: Message


class RpcRequest(BaseModel):
    jsonrpc: Literal["2.0"]
    id: int | str
    method: str
    params: dict = {}


def _error(req_id: int | str, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


@app.get("/.well-known/agent.json")
def card() -> dict:
    """The Agent Card with its signature embedded (computed over the card without it)."""
    unsigned = agent_card()
    return {**unsigned, "signature": sign(unsigned)}


@app.post("/a2a")
async def rpc(req: RpcRequest) -> dict:
    if req.method != "tasks/send":
        return _error(req.id, -32601, f"Method not found: {req.method}")
    try:
        task = TaskSendParams.model_validate(req.params)
    except ValidationError as e:
        return _error(req.id, -32602, f"Invalid params: {e}")
    question = " ".join(part.text for part in task.message.parts)
    answer = await research.ainvoke({"question": question})
    return {
        "jsonrpc": "2.0",
        "id": req.id,
        "result": {
            "id": task.id,
            "status": {"state": "completed"},
            "artifacts": [{"parts": [{"text": answer}]}],
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.a2a_port)
