# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from mewcode.mcp.loading_strategy import McpLoadingMode
from mewcode.mcp.tool_wrapper import MCP_TOOL_PREFIX
from mewcode.tools.base import TOOL_SEARCH_TOOL_NAME, Tool, ToolResult

if __import__("typing").TYPE_CHECKING:
    from mewcode.tools import ToolRegistry


class ToolSearchParams(BaseModel):
    query: str
    max_results: int = 5


class ToolSearchTool(Tool):
    name = TOOL_SEARCH_TOOL_NAME
    description = (
        "Search for and load additional tools that are not immediately available. "
        "Use query 'select:<name>[,<name>...]' to load specific tools by name, "
        "or provide keywords to search by relevance."
    )
    params_model = ToolSearchParams
    category = "read"
    should_defer = False  # ToolSearch 自身永远不延迟加载


    def __init__(
        self,
        registry: ToolRegistry,
        protocol: str = "anthropic",
    ) -> None:
        self._registry = registry
        self._protocol = protocol


    def get_schema(self) -> dict[str, Any]:
        schema = self.params_model.model_json_schema()
        schema.pop("title", None)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
        }


    async def execute(self, params: BaseModel) -> ToolResult:
        assert isinstance(params, ToolSearchParams)
        query = params.query
        max_results = params.max_results

        if query.startswith("select:"):
            names = [n.strip() for n in query[7:].split(",")]
            schemas = self._registry.find_deferred_by_names(names, self._protocol)
        else:
            schemas = self._registry.search_deferred(
                query, max_results, self._protocol
            )

        if not schemas:
            deferred_names = self._registry.get_deferred_tool_names()
            return ToolResult(
                output=(
                    f'No matching deferred tools for "{query}". '
                    f'Available: {", ".join(deferred_names)}'
                )
            )

        # 非 MCP 的延迟工具没有 mcp_call 这条入口，只能照旧标记成已发现、
        # 让它进下一轮的 tools[]
        for s in schemas:
            name = s.get("name", "")
            if name and not name.startswith(MCP_TOOL_PREFIX):
                self._registry.mark_discovered(name)

        mcp_names = [
            s["name"] for s in schemas
            if s.get("name", "").startswith(MCP_TOOL_PREFIX)
        ]
        mode = getattr(self._registry, "mcp_loading_mode", McpLoadingMode.EAGER)

        # 官方端点：回 tool_reference，让服务端把 schema 展开进上下文。
        # tools 数组不动，缓存前缀因此不断。
        if mcp_names and mode is McpLoadingMode.NATIVE and self._protocol == "anthropic":
            return ToolResult(
                output=(
                    f"Loaded {len(mcp_names)} tool(s): {', '.join(mcp_names)}. "
                    f"You can call them directly now."
                ),
                content_blocks=[
                    {"type": "tool_reference", "tool_name": name} for name in mcp_names
                ],
            )

        # 其他端点：schema 原文给模型看，调用走 mcp_call。
        # 这段文本落在 messages 末尾，属于追加，不影响缓存前缀。
        suffix = ""
        if mcp_names:
            suffix = (
                "\n\nTo invoke any of the tools above, call mcp_call with that tool's "
                "full name and an `arguments` object matching its input_schema exactly, "
                "using the same JSON types."
            )
        return ToolResult(
            output=(
                f"Found {len(schemas)} tool(s). Their full schemas are below:\n\n"
                f"{json.dumps(schemas, indent=2, ensure_ascii=False)}{suffix}"
            )
        )
