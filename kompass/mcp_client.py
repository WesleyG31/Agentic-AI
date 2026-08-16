"""Official MCP 2 client with a minimal LangChain tool bridge.

The bridge converts MCP's advertised JSON schemas to LangChain ``StructuredTool`` inputs;
all protocol, transport, error, and capability handling remains in the official SDK.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

import httpx2
from langchain_core.tools import StructuredTool, ToolException
from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from kompass.config import ROOT, settings
from kompass.runtime import get_runtime_context
from kompass.security.audit import audit


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    transport: Literal["stdio", "streamable_http", "in_memory"]
    command: str | None = None
    args: tuple[str, ...] = ()
    cwd: str | None = None
    url: str | None = None
    bearer_token: str | None = None
    env: dict[str, str] | None = None
    server: Any = None


class OfficialMCPClient:
    def __init__(
        self,
        servers: dict[str, MCPServerConfig],
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._servers = servers
        self._timeout = timeout_seconds

    @asynccontextmanager
    async def _open(self, config: MCPServerConfig) -> AsyncIterator[Client]:
        if config.transport == "in_memory":
            async with Client(config.server, read_timeout_seconds=self._timeout) as client:
                yield client
            return
        if config.transport == "stdio":
            if not config.command:
                raise ValueError(f"MCP stdio server {config.name} has no command")
            transport = stdio_client(
                StdioServerParameters(
                    command=config.command,
                    args=list(config.args),
                    cwd=config.cwd,
                    env=config.env,
                )
            )
            async with Client(transport, read_timeout_seconds=self._timeout) as client:
                yield client
            return
        if not config.url:
            raise ValueError(f"MCP HTTP server {config.name} has no URL")
        headers = {}
        if config.bearer_token:
            headers["Authorization"] = f"Bearer {config.bearer_token}"
        context = get_runtime_context(required=False)
        if context is not None:
            headers["X-Request-ID"] = context.request_id
            headers["X-Correlation-ID"] = context.correlation_id
        async with httpx2.AsyncClient(headers=headers, timeout=self._timeout) as http_client:
            transport = streamable_http_client(config.url, http_client=http_client)
            async with Client(transport, read_timeout_seconds=self._timeout) as client:
                yield client

    async def get_tools(self, server_name: str | None = None) -> list[StructuredTool]:
        names = [server_name] if server_name else list(self._servers)
        tools: list[StructuredTool] = []
        for name in names:
            if name not in self._servers:
                raise KeyError(f"unknown MCP server: {name}")
            config = self._servers[name]
            async with self._open(config) as client:
                advertised = (await client.list_tools()).tools
            for definition in advertised:
                tools.append(self._langchain_tool(config, definition))
        return tools

    async def read_resource(self, server_name: str, uri: str) -> str:
        config = self._servers[server_name]
        async with asyncio.timeout(self._timeout), self._open(config) as client:
            result = await client.read_resource(uri)
        parts = [getattr(item, "text", "") for item in result.contents]
        audit(
            "mcp.resource",
            decision="allow",
            reason="resource read completed",
            resource=uri,
            action="read",
            metadata={"server": server_name},
        )
        return "\n".join(part for part in parts if part)

    def _langchain_tool(self, config: MCPServerConfig, definition) -> StructuredTool:
        async def invoke(**arguments: Any) -> str:
            context = get_runtime_context(required=False)
            audit(
                "mcp.tool.request",
                decision="attempt",
                reason="authorized parent agent requested MCP tool",
                resource=f"mcp:{config.name}",
                action=definition.name,
                metadata={
                    "tenant": context.tenant_id if context else "missing",
                    "correlation_id": context.correlation_id if context else "missing",
                },
            )
            try:
                async with asyncio.timeout(self._timeout), self._open(config) as client:
                    result = await client.call_tool(
                        definition.name,
                        arguments,
                        read_timeout_seconds=self._timeout,
                    )
            except TimeoutError as exc:
                raise ToolException(
                    f"MCP tool {definition.name} exceeded {self._timeout}s"
                ) from exc
            if result.is_error:
                raise ToolException(self._render_result(result))
            return self._render_result(result)

        return StructuredTool(
            name=definition.name,
            description=definition.description or f"MCP tool {definition.name}",
            args_schema=definition.input_schema,
            coroutine=invoke,
            metadata={"mcp_server": config.name, "transport": config.transport},
        )

    @staticmethod
    def _render_result(result) -> str:
        if result.structured_content is not None:
            return json.dumps(result.structured_content, ensure_ascii=False, default=str)
        rendered: list[str] = []
        for block in result.content:
            if hasattr(block, "text"):
                rendered.append(str(block.text))
            elif hasattr(block, "resource"):
                rendered.append(str(block.resource))
            else:
                rendered.append(str(block))
        return "\n".join(rendered)


def local_mcp_client() -> OfficialMCPClient:
    def server(module: str, name: str) -> MCPServerConfig:
        return MCPServerConfig(
            name=name,
            transport="stdio",
            command=sys.executable,
            args=("-m", module),
            cwd=str(ROOT),
            env={
                "KOMPASS_ACME_DB": settings.acme_db,
                "KOMPASS_CHROMA_PATH": settings.chroma_path,
            },
        )

    if settings.mcp_remote_url:
        return OfficialMCPClient(
            {
                "enterprise": MCPServerConfig(
                    name="enterprise",
                    transport="streamable_http",
                    url=settings.mcp_remote_url,
                    bearer_token=settings.mcp_bearer_token or None,
                )
            },
            timeout_seconds=settings.mcp_timeout_seconds,
        )
    return OfficialMCPClient(
        {
            "doc_search": server("kompass.mcp_servers.doc_search", "doc_search"),
            "acme_sql": server("kompass.mcp_servers.sql", "acme_sql"),
            "ticketing": server("kompass.mcp_servers.ticketing", "ticketing"),
        },
        timeout_seconds=settings.mcp_timeout_seconds,
    )
