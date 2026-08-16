"""Offline MCP 2 contract tests through the official in-memory transport."""

import asyncio

import pytest
from langchain_core.tools import ToolException
from mcp.server import MCPServer
from mcp.server.auth.provider import AccessToken

from kompass.mcp_client import MCPServerConfig, OfficialMCPClient
from kompass.mcp_servers.remote import ConfiguredTokenVerifier, build_remote_server
from kompass.mcp_servers.sql import mcp as sql_server
from kompass.runtime import get_runtime_context


def _client(server, timeout: float = 1.0) -> OfficialMCPClient:
    return OfficialMCPClient(
        {
            "test": MCPServerConfig(
                name="test",
                transport="in_memory",
                server=server,
            )
        },
        timeout_seconds=timeout,
    )


async def test_official_client_lists_and_calls_tools_and_reads_resources():
    client = _client(sql_server)
    tools = {tool.name: tool for tool in await client.get_tools()}

    assert set(tools) == {"get_schema", "query_database"}
    schema = await tools["get_schema"].ainvoke({})
    resource = await client.read_resource("test", "kompass://schema/acme")
    assert "orders(" in schema
    assert "refunds(" in resource


async def test_mcp_timeout_is_bounded_and_structured_as_tool_error():
    server = MCPServer("slow")

    @server.tool()
    async def wait_forever() -> str:
        await asyncio.sleep(1)
        return "late"

    [tool] = await _client(server, timeout=0.01).get_tools()
    with pytest.raises(ToolException, match="exceeded"):
        await tool.ainvoke({})


async def test_mcp_structured_error_is_not_reported_as_success():
    server = MCPServer("error")

    @server.tool()
    def fail() -> str:
        raise ValueError("fixture failure")

    [tool] = await _client(server).get_tools()
    with pytest.raises(ToolException, match="fixture failure"):
        await tool.ainvoke({})


async def test_remote_mcp_token_verifier_accepts_only_configured_token_and_claims():
    verifier = ConfiguredTokenVerifier(
        token="test-token",
        subject="service-1",
        tenant_id="tenant-a",
        scopes={"mcp:invoke", "operations:read"},
    )
    assert await verifier.verify_token("wrong-token") is None
    accepted = await verifier.verify_token("test-token")
    assert accepted is not None
    assert accepted.subject == "service-1"
    assert accepted.claims["tenant_id"] == "tenant-a"


async def test_remote_mcp_tool_and_resource_enforce_specific_scope(monkeypatch):
    observed = {}

    def context_aware_schema() -> str:
        context = get_runtime_context()
        observed.update(tenant=context.tenant_id, user=context.user_id)
        return "orders(id), refunds(id)"

    monkeypatch.setattr("kompass.mcp_servers.remote.get_schema", context_aware_schema)
    token = AccessToken(
        token="test-token",
        client_id="service-1",
        subject="service-1",
        scopes=["operations:read"],
        claims={"tenant_id": "tenant-a"},
    )
    verifier = ConfiguredTokenVerifier(
        token="test-token",
        subject="service-1",
        tenant_id="tenant-a",
        scopes={"operations:read"},
    )
    server = build_remote_server(verifier, access_token_resolver=lambda: token)
    client = _client(server)
    tools = {tool.name: tool for tool in await client.get_tools()}

    assert "orders(" in await tools["get_schema"].ainvoke({})
    assert observed == {"tenant": "tenant-a", "user": "service-1"}
    assert "refunds(" in await client.read_resource("test", "kompass://schema/acme")
    with pytest.raises(ToolException, match="tickets:read"):
        await tools["get_ticket"].ainvoke({"ticket_id": 1})
