# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
"""决定 MCP 工具怎么进上下文。

三条路，会话启动连上 MCP 之后定一次：

    eager    —— MCP schema 总量小于上下文的 10%，全量放进 tools[]，不延迟。
                省下来的那点上下文不值得为它承担任何额外风险。
    native   —— 官方 Anthropic 端点。工具带 defer_loading 留在 tools[] 里但
                服务端不给模型看，ToolSearch 回 tool_reference 让服务端展开
                schema。tools 数组字节不变。
    dispatch —— 其他端点（国内厂商、各类代理网关）。这些端点不支持
                defer_loading / tool_reference，只能自己模拟：MCP 工具完全
                不进 tools[]，走 mcp_call 统一入口。

为什么要分这三条：tools 渲染在 system 之后、messages 之前，数组一变，它后面
的整段对话历史缓存全部失效。实测 2 万 token 历史下，往 tools 末尾加一个工具
的命中率从 99.4% 掉到 9.5%，等于把整段历史重算一遍。
"""
from __future__ import annotations

import os
from enum import Enum
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from mewcode.tools import ToolRegistry

# 低于上下文窗口这个比例就不延迟，直接全量加载
DEFAULT_EAGER_THRESHOLD_PERCENT = 10

# 拿不到真实 token 数时的估算比例。MCP 的 schema 是 JSON，符号密度高，
# 每 token 的字符数比自然语言低
CHARS_PER_TOKEN = 2.5

# 官方端点用的 beta header，defer_loading 和 tool_reference 都靠它开
NATIVE_TOOL_SEARCH_BETA = "advanced-tool-use-2025-11-20"

_OFFICIAL_HOSTS = frozenset({"api.anthropic.com"})

_ENV_OVERRIDE = "MEWCODE_MCP_LOADING"


class McpLoadingMode(str, Enum):
    EAGER = "eager"
    NATIVE = "native"
    DISPATCH = "dispatch"


def is_official_anthropic_endpoint(base_url: str) -> bool:
    """base_url 为空表示走 SDK 默认地址，也就是官方。"""
    if not base_url:
        return True
    host = urlparse(base_url).hostname or ""
    return host.lower() in _OFFICIAL_HOSTS


def estimate_schema_tokens(schema_chars: int) -> int:
    return int(schema_chars / CHARS_PER_TOKEN)


def decide_mode(
    base_url: str,
    context_window: int,
    mcp_schema_chars: int,
    threshold_percent: int = DEFAULT_EAGER_THRESHOLD_PERCENT,
) -> McpLoadingMode:
    override = os.environ.get(_ENV_OVERRIDE, "").strip().lower()
    if override in ("eager", "native", "dispatch"):
        return McpLoadingMode(override)

    if mcp_schema_chars <= 0:
        # 没有 MCP 工具，走哪条都一样，eager 最省事
        return McpLoadingMode.EAGER

    budget = context_window * threshold_percent / 100
    if estimate_schema_tokens(mcp_schema_chars) < budget:
        return McpLoadingMode.EAGER

    if is_official_anthropic_endpoint(base_url):
        return McpLoadingMode.NATIVE
    return McpLoadingMode.DISPATCH


def measure_mcp_schema_chars(registry: ToolRegistry) -> int:
    """MCP 工具 schema 序列化后的字符数，用来跟阈值比。"""
    import json

    from mewcode.mcp.tool_wrapper import MCP_TOOL_PREFIX

    total = 0
    for tool in registry.list_tools():
        if not tool.name.startswith(MCP_TOOL_PREFIX):
            continue
        try:
            total += len(json.dumps(tool.get_schema(), ensure_ascii=False))
        except (TypeError, ValueError):
            total += len(tool.name) + len(tool.description or "")
    return total


def apply_mode(registry: ToolRegistry, mode: McpLoadingMode) -> None:
    """把决定落到 registry 上。

    eager 下要把 MCP 工具的延迟标记摘掉，它们才会出现在 tools[] 里；另外两条
    路保持延迟。mcp_call 不在这里注册——它必须在 MCP 连接之前就在 tools[] 里，
    否则连上之后再加就是一次中途改动 tools 数组，缓存照样断。
    """
    from mewcode.mcp.tool_wrapper import MCP_TOOL_PREFIX

    registry.mcp_loading_mode = mode
    eager = mode is McpLoadingMode.EAGER
    for tool in registry.list_tools():
        if tool.name.startswith(MCP_TOOL_PREFIX):
            tool.should_defer = not eager

    # 检索和分发按模式决定发不发。eager 下所有工具都在 tools[] 里，没有可搜的
    # 对象、也不需要分发入口，两个都发过去只是白占 token。这两个开关在这里算
    # 一次就固定下来，整场会话不变，不会造成 tools[] 中途抖动。
    registry.expose_tool_search = not eager
    registry.expose_mcp_call = mode is McpLoadingMode.DISPATCH


def decide_and_apply(
    registry: ToolRegistry,
    base_url: str,
    context_window: int,
) -> McpLoadingMode:
    mode = decide_mode(
        base_url=base_url,
        context_window=context_window,
        mcp_schema_chars=measure_mcp_schema_chars(registry),
    )
    apply_mode(registry, mode)
    return mode
