from __future__ import annotations

import logging
import os
from contextlib import AsyncExitStack
from typing import Any

import httpx2
from mcp import Client, types
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from personalcode.config import MCPServerConfig, build_child_env, resolve_env_vars

logger = logging.getLogger(__name__)

CLIENT_INFO = types.Implementation(name="personalcode", version="0.1.0")


class MCPClient:
    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.name = config.name
        self._client: Client | None = None
        self._stack: AsyncExitStack | None = None
        self._alive = False


    @property
    def is_alive(self) -> bool:
        return self._alive

    @property
    def instructions(self) -> str:
        """返回 MCP 服务器的 instructions。现代服务器来自 server/discover，老服务器来自 initialize。"""
        if self._client is not None and self._client.instructions:
            return self._client.instructions
        return ""

    @property
    def protocol_version(self) -> str | None:
        """协商出的协议版本，连接前为 None。"""
        return self._client.protocol_version if self._client is not None else None


    async def connect(self) -> None:
        if self._alive:
            return

        self._stack = AsyncExitStack()
        await self._stack.__aenter__()

        try:
            transport = self._stdio_transport() if self.config.is_stdio else await self._http_transport()
            # mode="auto" 由 SDK 协商协议版本：先发 server/discover 按 2026-07-28 无状态协议探测，
            # 服务器支持就直接用，之后每个请求在 _meta 里带协议版本和客户端能力；
            # discover 报错或 10 秒内没回应，就回退到 initialize 握手，按 2025-11-25 及更早的协议走
            self._client = await self._stack.enter_async_context(
                Client(transport, mode="auto", client_info=CLIENT_INFO)
            )
            self._alive = True
            logger.info(
                "MCP server '%s' connected (protocol %s)", self.name, self._client.protocol_version
            )
        except Exception:
            await self._cleanup_stack()
            raise


    def _stdio_transport(self) -> Any:
        assert self._stack is not None
        assert self.config.command is not None

        params = StdioServerParameters(
            command=self.config.command,
            args=self.config.args,
            env=build_child_env(self.config.env),
        )
        devnull = open(os.devnull, "w")
        self._stack.callback(devnull.close)
        return stdio_client(params, errlog=devnull)

    async def _http_transport(self) -> Any:
        assert self._stack is not None
        assert self.config.url is not None

        resolved_headers = {
            k: resolve_env_vars(v) for k, v in self.config.headers.items()
        }
        http_client = httpx2.AsyncClient(headers=resolved_headers)
        await self._stack.enter_async_context(http_client)
        return streamable_http_client(self.config.url, http_client=http_client)


    async def list_tools(self) -> list[types.Tool]:
        assert self._client is not None
        result = await self._client.list_tools()
        return list(result.tools)


    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> types.CallToolResult:
        assert self._client is not None
        return await self._client.call_tool(name, arguments)

    async def close(self) -> None:
        self._alive = False
        self._client = None
        await self._cleanup_stack()

    async def _cleanup_stack(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.__aexit__(None, None, None)
            except RuntimeError as e:
                if "cancel scope" in str(e):
                    logger.debug("Cancel scope cleanup (expected during shutdown): %s", e)
                else:
                    raise
            except Exception:
                logger.debug("Error closing stack for '%s'", self.name, exc_info=True)
            self._stack = None
