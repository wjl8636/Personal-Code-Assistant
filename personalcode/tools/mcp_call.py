"""MCP 工具的统一调用入口。

MCP 工具不进入 tools[]，模型先用 ToolSearch 读到 schema，再通过 mcp_call
把工具名和参数传进来。这样 tools 数组在整场会话里字节不变，prompt cache
的前缀不会被打断——工具排在 system 之后、messages 之前，数组一变，它后面
的整段历史都要重算。

代价是参数由模型自由生成，没有接口层的 schema 约束，偶尔会写错 JSON 类型。下面
的 coerce_by_schema 按目标工具的完整 schema 逐层修正，修正规则四个语言
必须逐条一致：

    schema 声明        模型给的               修正为
    string            int / float（非 bool）  str(v)
    integer / number  数字形字符串            int / float
    boolean           "true" / "false"        True / False
    array             单键对象且值是数组       拆出内层数组
    array             逗号分隔字符串          按逗号切分去空白
    object            对象                    按 properties 递归
    array             数组                    按 items 递归每个元素

修正不了的原样往下传，交给 MCP 服务器报它自己的错——服务器的域内错误比
本地类型错误对模型更有指导性。
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from personalcode.mcp.tool_wrapper import (
    MCP_NAME_SEP,
    MCP_TOOL_PREFIX,
    build_mcp_tool_name,
    sanitize_name,
)
from personalcode.tools.base import MCP_CALL_TOOL_NAME, Tool, ToolResult

# 数字形状：整串都得是数字，integer 还不许有小数部分。
# 这两条正则四个语言必须一致。
_INT_SHAPE = re.compile(r"^[+-]?\d+$")
_NUM_SHAPE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")

if TYPE_CHECKING:
    from personalcode.tools import ToolRegistry


def _coerce_scalar(value: Any, want: str) -> Any:
    # bool 是 int 的子类，先排掉，否则 True 会被写成字符串 "True"
    if want == "string" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if want in ("integer", "number") and isinstance(value, str):
        text = value.strip()
        # 先按形状挡一道：int() 认 "1_000" 这种下划线写法，float() 认 "inf"、"1e9"，
        # 另外三个语言都不认，不挡就会转出别的语言转不出来的值
        shape = _INT_SHAPE if want == "integer" else _NUM_SHAPE
        if not shape.match(text):
            return value
        try:
            return int(text) if want == "integer" else float(text)
        except ValueError:
            return value
    if want == "boolean" and isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "false"):
            return low == "true"
    return value


def coerce_by_schema(value: Any, schema: Any) -> Any:
    """按 JSON schema 递归修正参数。只做确定性修正，含糊的一律不动。"""
    if not isinstance(schema, dict):
        return value
    want = schema.get("type")

    if want == "object" and isinstance(value, dict):
        props = schema.get("properties") or {}
        return {
            key: (coerce_by_schema(item, props[key]) if key in props else item)
            for key, item in value.items()
        }

    if want == "array":
        item_schema = schema.get("items") or {}
        # 模型常把数组包成 {"item": [...]} 这类单键对象
        if isinstance(value, dict) and len(value) == 1:
            inner = next(iter(value.values()))
            if isinstance(inner, list):
                value = inner
        # 也常拼成逗号分隔的字符串
        elif isinstance(value, str):
            value = [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(value, list):
            return [coerce_by_schema(item, item_schema) for item in value]
        return value

    if isinstance(want, str):
        return _coerce_scalar(value, want)
    return value


def permission_content(server: str, tool: str) -> str:
    """权限规则匹配用的 content：归一化成 `server__tool`。

    不带 mcp__ 前缀，也不受各语言 wrapper 命名差异影响，四个语言的
    permissions.yaml 写法因此完全一致：mcp_call(linear__create_issue)。

    两段都要过一遍 sanitize。模型可能传短名也可能传全名，全名里的段是
    wrapper 已经处理过的，短名是模型原样给的——不统一处理的话，同一个调用
    传短名和传全名会算出不同的 content，规则就会漏匹配。
    """
    if tool.startswith(MCP_TOOL_PREFIX):
        rest = tool[len(MCP_TOOL_PREFIX):]
        sep = rest.find(MCP_NAME_SEP)
        if sep >= 0:
            # 全名里已经带了服务器段，用它，避免拼出 linear__linear__x
            return sanitize_name(rest[:sep]) + MCP_NAME_SEP + sanitize_name(
                rest[sep + len(MCP_NAME_SEP):]
            )
    return sanitize_name(server) + MCP_NAME_SEP + sanitize_name(tool)


class McpCallParams(BaseModel):
    server: str = Field(description="MCP server name, e.g. 'linear'.")
    tool: str = Field(
        description="Full tool name as returned by ToolSearch, e.g. 'mcp__linear__create_issue'."
    )
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "The target tool's arguments. Must match that tool's input_schema exactly, "
            "including JSON types: bare numbers for integer fields, bare true/false for "
            "boolean fields, quoted strings for string fields, and plain JSON arrays for "
            "array fields."
        ),
    )


class McpCallTool(Tool):
    name = MCP_CALL_TOOL_NAME
    description = (
        "Invoke a tool on a connected MCP server. Call ToolSearch first to load the "
        "tool's schema, then pass its arguments here exactly as that schema requires, "
        "using the same JSON types."
    )
    params_model = McpCallParams
    category = "command"
    # 自己必须留在 tools[] 里，否则模型没有入口
    should_defer = False

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def _resolve(self, server: str, tool: str) -> tuple[Any, str]:
        """全名 / server+短名 / 短名后缀唯一匹配，依次尝试。

        模型很常只传短名（实测约三成调用），所以这里必须容错，否则会白白
        换来一轮重试。
        """
        for candidate in (tool, build_mcp_tool_name(server, tool)):
            found = self._registry.get(candidate)
            if found is not None:
                return found, candidate

        # 短名也要过 sanitize：注册表里的名字横杠、点都换成了下划线，
        # 拿模型原样给的 take.snapshot 去比后缀一条也匹配不上
        suffix = MCP_NAME_SEP + sanitize_name(tool)
        matches = [
            t for t in self._registry.list_tools()
            if t.name.startswith(MCP_TOOL_PREFIX) and t.name.endswith(suffix)
        ]
        if len(matches) == 1:
            return matches[0], matches[0].name
        return None, tool

    def _available_names(self) -> list[str]:
        return [
            t.name for t in self._registry.list_tools()
            if t.name.startswith(MCP_TOOL_PREFIX)
        ]

    async def execute(self, params: BaseModel) -> ToolResult:
        assert isinstance(params, McpCallParams)
        target, resolved = self._resolve(params.server, params.tool)
        if target is None:
            names = self._available_names()
            hint = ", ".join(names) if names else "(none connected)"
            return ToolResult(
                output=(
                    f"Unknown MCP tool '{params.tool}' on server '{params.server}'. "
                    f"Available tools: {hint}"
                ),
                is_error=True,
            )

        schema = getattr(target, "mcp_input_schema", None)
        arguments = params.arguments or {}
        if schema:
            arguments = coerce_by_schema(arguments, schema)

        # 走 execute_raw：参数已按完整 schema 修正过，不必再过一遍只认顶层
        # 类型的 params_model
        execute_raw = getattr(target, "execute_raw", None)
        if execute_raw is not None:
            return await execute_raw(arguments)
        return await target.execute(target.params_model(**arguments))
