# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com

"""缓存断点的落点。

该长什么样：落在最后一个非延迟工具上。一个工具同时带 defer_loading 和 cache_control
会被官方端点直接拒掉整个请求（400），而 MCP 工具在内建工具之后注册，列表尾部往往正是
延迟工具，所以不能简单地标记最后一个。
"""

from __future__ import annotations

from mewcode.client import _mark_last_tool_for_cache


def _marked(tools: list[dict]) -> list[str]:
    return [t["name"] for t in tools if "cache_control" in t]


def test_marker_skips_deferred_tail():
    tools = [
        {"name": "ReadFile"},
        {"name": "WriteFile"},
        {"name": "ToolSearch"},
        {"name": "mcp__linear__create_issue", "defer_loading": True},
        {"name": "mcp__sentry__resolve", "defer_loading": True},
    ]
    out = _mark_last_tool_for_cache(tools)
    assert _marked(out) == ["ToolSearch"]


def test_marker_on_last_when_none_deferred():
    tools = [{"name": "ReadFile"}, {"name": "Bash"}]
    assert _marked(_mark_last_tool_for_cache(tools)) == ["Bash"]


def test_marker_skips_deferred_in_middle():
    tools = [
        {"name": "Bash"},
        {"name": "mcp__a__x", "defer_loading": True},
        {"name": "Grep"},
        {"name": "mcp__z__y", "defer_loading": True},
    ]
    assert _marked(_mark_last_tool_for_cache(tools)) == ["Grep"]


def test_no_marker_when_all_deferred():
    # 官方要求至少有一个非延迟工具，真实注册表里内建工具永远非延迟，
    # 所以这是防御分支：宁可不缓存，也不能发出会被 400 的请求
    tools = [
        {"name": "mcp__a__x", "defer_loading": True},
        {"name": "mcp__b__y", "defer_loading": True},
    ]
    assert _marked(_mark_last_tool_for_cache(tools)) == []


def test_input_list_not_mutated():
    """注册表里的 schema 往往是共享对象，标记不能就地改。"""
    tools = [{"name": "ReadFile"}, {"name": "Bash"}]
    out = _mark_last_tool_for_cache(tools)
    assert "cache_control" not in tools[-1]
    assert "cache_control" in out[-1]


def test_empty_list():
    assert _mark_last_tool_for_cache([]) == []
