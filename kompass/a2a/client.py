"""Official A2A 1.0 client with auth, timeout, context, and trust handling."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import get_artifact_text, new_text_message
from a2a.types import CancelTaskRequest, GetTaskRequest, Role, SendMessageRequest, TaskState

from kompass.config import settings
from kompass.runtime import RuntimeContext
from kompass.security.trust import ContentOrigin, TrustBoundary, TrustedContent


@dataclass(frozen=True)
class A2AResult:
    task_id: str
    state: int
    text: str


async def send_task(
    base_url: str,
    question: str,
    *,
    token: str,
    context: RuntimeContext,
) -> A2AResult:
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Request-ID": context.request_id,
        "X-Correlation-ID": context.correlation_id,
    }
    async with httpx.AsyncClient(headers=headers, timeout=settings.a2a_timeout_seconds) as http:
        client = await ClientFactory(
            ClientConfig(streaming=True, httpx_client=http, accepted_output_modes=["text/plain"])
        ).create_from_url(base_url)
        message = new_text_message(question, role=Role.ROLE_USER)
        request = SendMessageRequest(tenant=context.tenant_id, message=message)
        task_id = ""
        state = TaskState.TASK_STATE_UNSPECIFIED
        artifacts = []
        try:
            async with asyncio.timeout(settings.a2a_timeout_seconds):
                async for chunk in client.send_message(request):
                    if chunk.HasField("task"):
                        task_id = chunk.task.id
                        state = chunk.task.status.state
                        artifacts.extend(chunk.task.artifacts)
                    elif chunk.HasField("status_update"):
                        state = chunk.status_update.status.state
                    elif chunk.HasField("artifact_update"):
                        artifacts.append(chunk.artifact_update.artifact)
        finally:
            await client.close()
    if not task_id:
        raise RuntimeError("A2A peer returned no task")
    text = "\n".join(get_artifact_text(artifact) for artifact in artifacts)
    safe = TrustBoundary().evaluate(
        TrustedContent(text, ContentOrigin.REMOTE_AGENT, provenance=f"a2a:task:{task_id}"),
        purpose="model",
    )
    return A2AResult(task_id, state, safe.rendered)


async def cancel_task(
    base_url: str,
    task_id: str,
    *,
    token: str,
    context: RuntimeContext,
):
    headers = {"Authorization": f"Bearer {token}", "X-Request-ID": context.request_id}
    async with httpx.AsyncClient(headers=headers, timeout=settings.a2a_timeout_seconds) as http:
        client = await ClientFactory(ClientConfig(httpx_client=http)).create_from_url(base_url)
        try:
            return await client.cancel_task(
                CancelTaskRequest(tenant=context.tenant_id, id=task_id)
            )
        finally:
            await client.close()


async def get_task(
    base_url: str,
    task_id: str,
    *,
    token: str,
    context: RuntimeContext,
):
    headers = {"Authorization": f"Bearer {token}", "X-Request-ID": context.request_id}
    async with httpx.AsyncClient(headers=headers, timeout=settings.a2a_timeout_seconds) as http:
        client = await ClientFactory(ClientConfig(httpx_client=http)).create_from_url(base_url)
        try:
            return await client.get_task(GetTaskRequest(tenant=context.tenant_id, id=task_id))
        finally:
            await client.close()


async def _main() -> None:
    base_url = settings.a2a_base_url
    question = " ".join(sys.argv[1:]) or "What is ACME's damaged-item refund policy?"
    if not settings.a2a_bearer_token:
        raise RuntimeError("KOMPASS_A2A_BEARER_TOKEN is required")
    context = RuntimeContext.create(
        tenant_id=settings.default_tenant_id,
        user_id="a2a-cli",
        scopes={"research:read"},
        principal_kind="service",
    )
    result = await send_task(
        base_url,
        question,
        token=settings.a2a_bearer_token,
        context=context,
    )
    print(result.text)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(_main())
