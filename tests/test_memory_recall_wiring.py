# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com

"""记忆召回在 Agent Loop 里的接线测试。

召回结果只在工具执行后注入，且只有真正注入的记忆才记为已注入。
"""
from __future__ import annotations

import asyncio

import pytest

from personalcode.agent import Agent
from personalcode.conversation import ConversationManager
from personalcode.memory import RecallResult
from personalcode.tools import create_default_registry
from personalcode.tools.base import StreamEnd, TextDelta, ToolCallComplete

from test_agent import MockLLMClient

REMINDER = "## Memory: a.md"


async def _settled_recall_task() -> asyncio.Task:
    """造一个已经完成的召回 task，模拟 prefetch 在主调用期间已经跑完。"""

    async def _done() -> RecallResult:
        return RecallResult(reminder=REMINDER, paths=["/mem/a.md"])

    task = asyncio.ensure_future(_done())
    await task
    return task


def _reminder_in(conv: ConversationManager) -> bool:
    return any(REMINDER in (m.content or "") for m in conv.history)


@pytest.mark.asyncio
async def test_recall_injected_after_tools():
    """有工具调用的一轮：召回结果在工具结果之后注入，同时记为已注入。"""
    client = MockLLMClient([
        [
            ToolCallComplete("t1", "ReadFile", {"file_path": "PERSONALCODE.md"}),
            StreamEnd("end_turn", input_tokens=1, output_tokens=1),
        ],
        [
            TextDelta("done"),
            StreamEnd("end_turn", input_tokens=1, output_tokens=1),
        ],
    ])
    agent = Agent(client, create_default_registry(), "anthropic", work_dir=".")
    agent.memory_recall_task = await _settled_recall_task()
    agent._memory_recall_consumed = False
    conv = ConversationManager()
    conv.add_user_message("read it")

    async for _ in agent.run(conv):
        pass

    assert _reminder_in(conv), "召回结果应该在工具结果之后注入"
    assert "/mem/a.md" in agent.surfaced_memory_paths, "注入的记忆应该记为已注入"


@pytest.mark.asyncio
async def test_recall_not_surfaced_without_tools():
    """没有工具调用的一轮：召回结果没被消费，对应记忆不能记为已注入。"""
    client = MockLLMClient([
        [
            TextDelta("plain answer"),
            StreamEnd("end_turn", input_tokens=1, output_tokens=1),
        ],
    ])
    agent = Agent(client, create_default_registry(), "anthropic", work_dir=".")
    agent.memory_recall_task = await _settled_recall_task()
    agent._memory_recall_consumed = False
    conv = ConversationManager()
    conv.add_user_message("hi")

    async for _ in agent.run(conv):
        pass

    assert not _reminder_in(conv), "没有工具调用时不应该注入召回结果"
    assert not agent.surfaced_memory_paths, "没被消费的召回结果不能记为已注入"
