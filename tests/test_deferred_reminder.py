# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com

"""延迟工具清单提醒的注入时机。

这条提醒是 append 进历史的，发一次就一直在上下文里，所以每轮重发只是拿相同内容
占窗口，六十来个工具一份清单五百多 token。

该长什么样：首轮发一次；池子没变就不再发；池子变了补一次；compact 把历史压掉之
后重新发。
"""

from __future__ import annotations

from pydantic import BaseModel

from mewcode.agent import DEFERRED_REMINDER_MARKER, Agent
from mewcode.conversation import ConversationManager
from mewcode.tools import ToolRegistry
from mewcode.tools.base import Tool


class _DeferredTool(Tool):
    """占位的延迟工具，execute 不会被调用。"""

    params_model = BaseModel
    should_defer = True

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = name

    async def execute(self, params):  # pragma: no cover - 只测注入时机
        raise NotImplementedError


def _count(conv: ConversationManager) -> int:
    return sum(
        1
        for m in conv.history
        if m.role == "user" and DEFERRED_REMINDER_MARKER in (m.content or "")
    )


def _agent(registry: ToolRegistry) -> Agent:
    # 只走 _announce_deferred_tools，client 不会被碰到
    return Agent(client=None, registry=registry, protocol="anthropic")  # type: ignore[arg-type]


def test_deferred_reminder_injected_once():
    registry = ToolRegistry()
    for name in ["mcp__linear__create_issue", "mcp__sentry__resolve_issue"]:
        registry.register(_DeferredTool(name))

    agent = _agent(registry)
    conv = ConversationManager()

    # 第 1 轮：首次宣告
    agent._announce_deferred_tools(conv)
    assert _count(conv) == 1

    # 第 2~10 轮：池子没变，一条都不该再加
    for _ in range(9):
        conv.add_assistant_message("thinking")
        agent._announce_deferred_tools(conv)
    assert _count(conv) == 1, "池子没变时不该重发"

    # MCP 异步连上，池子多出一个工具：补一条
    registry.register(_DeferredTool("mcp__infra__scale_service"))
    agent._announce_deferred_tools(conv)
    assert _count(conv) == 2, "池子变化后该补一条"

    # compact 把历史压成摘要，原来那两条都没了：重新宣告
    conv.history.clear()
    conv.add_user_message("summary of earlier conversation")
    agent._announce_deferred_tools(conv)
    assert _count(conv) == 1, "compact 之后该重新宣告"


def test_deferred_names_sorted():
    """顺序必须稳定，否则调用方没法靠比较判断池子变没变。"""
    registry = ToolRegistry()
    for name in ["mcp__z__b", "mcp__a__c", "mcp__m__a"]:
        registry.register(_DeferredTool(name))
    names = registry.get_deferred_tool_names()
    assert names == sorted(names)
    assert names == registry.get_deferred_tool_names()


def test_no_reminder_without_deferred_tools():
    """eager 模式下没有延迟工具，这条提醒完全不该出现。"""
    agent = _agent(ToolRegistry())
    conv = ConversationManager()
    agent._announce_deferred_tools(conv)
    assert _count(conv) == 0
