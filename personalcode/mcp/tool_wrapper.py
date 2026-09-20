# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
from __future__ import annotations

import re
from typing import Any

from mcp import types as mcp_types
from pydantic import BaseModel, create_model

from personalcode.mcp.client import MCPClient
from personalcode.tools.base import Tool, ToolResult

_NON_ALNUM = re.compile(r"[^a-zA-Z0-9_]")

# MCP 工具的对外名字统一成 mcp__{服务器}__{工具}。分隔符用双下划线，
# 是为了让「哪一段是服务器名、哪一段是工具名」在名字里可逆——服务器名和
# 工具名自身允许带单下划线，单下划线做分隔符时就分不清边界了。
MCP_TOOL_PREFIX = "mcp__"
MCP_NAME_SEP = "__"


def sanitize_name(raw: str) -> str:
    """把服务器名/工具名里不合法的字符换成下划线，保证拼出来的工具名能过 API 校验。"""
    return _NON_ALNUM.sub("_", raw)


def mcp_tool_name_prefix(server_name: str) -> str:
    """某个服务器下所有工具名的公共前缀。按服务器筛工具的地方都该用它，
    自己拼字符串会漏掉 sanitize——服务器名里的横杠会被换成下划线。
    """
    return MCP_TOOL_PREFIX + sanitize_name(server_name) + MCP_NAME_SEP


def build_mcp_tool_name(server_name: str, tool_name: str) -> str:
    return mcp_tool_name_prefix(server_name) + sanitize_name(tool_name)


def _build_params_model(
    tool_name: str, input_schema: dict[str, Any]
) -> type[BaseModel]:
    properties = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))

    field_definitions: dict[str, Any] = {}
    for name, prop in properties.items():
        py_type = _json_type_to_python(prop.get("type", "string"))
        if name in required:
            field_definitions[name] = (py_type, ...)
        else:
            field_definitions[name] = (py_type | None, None)

    return create_model(f"{tool_name}Params", **field_definitions)


def _json_type_to_python(json_type: str) -> type:
    mapping: dict[str, type] = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "object": dict,
        "array": list,
    }
    return mapping.get(json_type, str)


def _extract_text(content: list[Any]) -> str:
    parts: list[str] = []
    for block in content:
        if isinstance(block, mcp_types.TextContent):
            parts.append(block.text)
        elif isinstance(block, mcp_types.ImageContent):
            parts.append(f"[image: {block.mime_type}]")
        elif isinstance(block, mcp_types.EmbeddedResource):
            resource = block.resource
            if hasattr(resource, "text"):
                parts.append(resource.text)
            else:
                parts.append(f"[binary resource: {resource.uri}]")
    return "\n".join(parts) if parts else "(no output)"


class MCPToolWrapper(Tool):
    def __init__(
        self,
        server_name: str,
        tool_def: mcp_types.Tool,
        client: MCPClient,
    ) -> None:
        self._server_name = server_name
        self._tool_def = tool_def
        self._client = client
        self.name = build_mcp_tool_name(server_name, tool_def.name)
        self.description = tool_def.description or tool_def.name
        self.category = "command"
        self.should_defer = True
        self.params_model = _build_params_model(
            tool_def.name, tool_def.input_schema
        )

    @property
    def mcp_tool_name(self) -> str:
        return self._tool_def.name

    @property
    def mcp_server_name(self) -> str:
        return self._server_name

    @property
    def mcp_input_schema(self) -> dict[str, Any]:
        """原始 JSON schema。参数强转要按它逐层走，不能用 params_model——
        后者只保留顶层类型（object 塌成 dict、array 塌成 list），嵌套结构和
        数组元素类型都丢了。
        """
        return self._tool_def.input_schema or {}


    def get_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self._tool_def.input_schema,
        }


    async def execute(self, params: BaseModel) -> ToolResult:
        return await self.execute_raw(params.model_dump(exclude_none=True))

    async def execute_raw(self, arguments: dict[str, Any]) -> ToolResult:
        """不经 params_model 直接发参数。mcp_call 走这条路：它已经按完整
        schema 逐层修正过参数，再套一层只认顶层类型的 pydantic 模型，会把
        本可以交给服务器判断的情况提前拦成类型错误。
        """
        if not self._client.is_alive:
            try:
                await self._client.connect()
            except Exception as e:
                return ToolResult(
                    output=f"MCP server '{self._server_name}' reconnect failed: {e}",
                    is_error=True,
                )

        try:
            result = await self._client.call_tool(
                self._tool_def.name, arguments
            )
        except Exception as e:
            self._client._alive = False
            return ToolResult(
                output=f"MCP tool call failed: {e}",
                is_error=True,
            )

        text = _extract_text(result.content)
        return ToolResult(output=text, is_error=bool(result.is_error))
