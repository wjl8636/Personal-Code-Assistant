# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com

"""拒绝授权时附带反馈的测试：Agent 如何把反馈交给模型，确认框如何收集输入。"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator

import pytest
from textual import events
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll

from personalcode.agent import (
    Agent,
    PermissionReply,
    PermissionRequest,
    PermissionResponse,
    ToolResultEvent,
)
from personalcode.client import LLMClient
from personalcode.config import ProviderConfig
from personalcode.conversation import ConversationManager
from personalcode.conversation_pairing import REJECTED_TOOL_RESULT, rejected_tool_result
from personalcode.permission_dialog import InlinePermissionWidget
from personalcode.permissions import DangerousCommandDetector, PathSandbox, PermissionChecker, PermissionMode, RuleEngine
from personalcode.tools import create_default_registry
from personalcode.tools.base import StreamEnd, StreamEvent, TextDelta, ToolCallComplete


class _ScriptedClient(LLMClient):
    def __init__(self, responses: list[list[StreamEvent]]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        if self.calls >= len(self._responses):
            yield TextDelta(text="done")
            yield StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)
            return
        events_ = self._responses[self.calls]
        self.calls += 1
        for e in events_:
            yield e


def _ask_agent(tmpdir: Path, target: Path) -> tuple[Agent, ConversationManager]:
    client = _ScriptedClient([
        [
            ToolCallComplete("t1", "WriteFile", {"file_path": str(target), "content": "x"}),
            StreamEnd("tool_use", input_tokens=10, output_tokens=20),
        ],
        [
            TextDelta("ok"),
            StreamEnd("end_turn", input_tokens=30, output_tokens=15),
        ],
    ])
    checker = PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(str(tmpdir)),
        rule_engine=RuleEngine(),
        mode=PermissionMode.DEFAULT,
    )
    agent = Agent(client, create_default_registry(), "anthropic", work_dir=str(tmpdir), permission_checker=checker)
    conv = ConversationManager()
    conv.add_user_message("write it")
    return agent, conv


def test_rejected_tool_result_helper() -> None:
    assert rejected_tool_result() == REJECTED_TOOL_RESULT
    assert rejected_tool_result("   ") == REJECTED_TOOL_RESULT
    assert rejected_tool_result(" 用 git mv ") == (
        REJECTED_TOOL_RESULT + " To tell you how to proceed, the user said:\n用 git mv"
    )


@pytest.mark.asyncio
async def test_deny_with_feedback_reaches_model() -> None:
    tmpdir = Path(tempfile.mkdtemp())
    target = Path(tempfile.mkdtemp()) / "outside.txt"  # 沙箱外的写入会触发确认
    agent, conv = _ask_agent(tmpdir, target)

    asked = False
    results: list[ToolResultEvent] = []
    async for e in agent.run(conv):
        if isinstance(e, PermissionRequest):
            asked = True
            e.future.set_result(PermissionReply(PermissionResponse.DENY, "写到 backup 目录去"))
        elif isinstance(e, ToolResultEvent):
            results.append(e)

    assert asked
    assert not target.exists(), "denied write must not happen"
    assert len(results) == 1 and results[0].is_error
    want = REJECTED_TOOL_RESULT + " To tell you how to proceed, the user said:\n写到 backup 目录去"
    assert results[0].output == want

    # 下一轮发给模型的对话里，tool_result 必须带上这段话
    contents = [
        block.content
        for msg in conv.history
        for block in (msg.tool_results or [])
    ]
    assert any("写到 backup 目录去" in str(c) for c in contents)


@pytest.mark.asyncio
async def test_deny_without_feedback_keeps_plain_rejection() -> None:
    tmpdir = Path(tempfile.mkdtemp())
    target = Path(tempfile.mkdtemp()) / "outside.txt"
    agent, conv = _ask_agent(tmpdir, target)

    output = None
    async for e in agent.run(conv):
        if isinstance(e, PermissionRequest):
            e.future.set_result(PermissionReply(PermissionResponse.DENY))
        elif isinstance(e, ToolResultEvent):
            output = e.output
    assert output == REJECTED_TOOL_RESULT


# ---------------------------------------------------------------------------
# 确认框组件
# ---------------------------------------------------------------------------


class _DialogApp(App):
    def __init__(self) -> None:
        super().__init__()
        self.replies: list[PermissionReply] = []

    def compose(self) -> ComposeResult:
        yield InlinePermissionWidget("Bash", "rm -rf build")

    def on_inline_permission_widget_responded(self, event: InlinePermissionWidget.Responded) -> None:
        self.replies.append(event.reply)


def _type(widget: InlinePermissionWidget, text: str) -> None:
    for ch in text:
        widget.on_key(events.Key(ch, ch))


@pytest.mark.asyncio
async def test_widget_deny_with_feedback() -> None:
    app = _DialogApp()
    async with app.run_test() as pilot:
        widget = app.query_one(InlinePermissionWidget)
        await pilot.press("down", "down")
        _type(widget, "别删 [bold]x")
        widget.on_key(events.Key("backspace", None))
        # 用户输入里的方括号要按原文显示，不能被当成 markup 吃掉
        assert "别删 [bold]" in str(widget.query_one("#perm-content").render())
        await pilot.press("enter")
        await pilot.pause()
    assert app.replies == [PermissionReply(PermissionResponse.DENY, "别删 [bold]")]


@pytest.mark.asyncio
async def test_widget_typing_ignored_outside_no() -> None:
    app = _DialogApp()
    async with app.run_test() as pilot:
        widget = app.query_one(InlinePermissionWidget)
        _type(widget, "hello")
        await pilot.press("enter")
        await pilot.pause()
    assert app.replies == [PermissionReply(PermissionResponse.ALLOW, "")]


@pytest.mark.asyncio
async def test_widget_feedback_dropped_when_leaving_no() -> None:
    app = _DialogApp()
    async with app.run_test() as pilot:
        widget = app.query_one(InlinePermissionWidget)
        await pilot.press("down", "down")
        _type(widget, "draft")
        widget.on_paste(events.Paste("more\nlines"))
        assert widget._feedback == "draftmore lines"
        await pilot.press("up", "enter")
        await pilot.pause()
    assert app.replies == [PermissionReply(PermissionResponse.ALLOW_ALWAYS, "")]


@pytest.mark.asyncio
async def test_widget_escape_denies() -> None:
    app = _DialogApp()
    async with app.run_test() as pilot:
        await pilot.press("escape")
        await pilot.pause()
    assert app.replies == [PermissionReply(PermissionResponse.DENY, "")]


@pytest.mark.asyncio
async def test_ctrl_c_removes_permission_widget(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from personalcode.app import PersonalCodeApp

    provider = ProviderConfig(
        name="test", protocol="anthropic", base_url="", model="claude-sonnet-5",
        api_key="test-key", context_window=200000,
    )
    app = PersonalCodeApp(providers=[provider])
    async with app.run_test() as pilot:
        await pilot.pause()
        future: asyncio.Future[PermissionReply] = asyncio.get_running_loop().create_future()
        await app._handle_permission_request(PermissionRequest("Bash", "rm -rf build", future))
        await pilot.pause()
        assert len(app.query("#perm-inline")) == 1

        app._streaming = True
        await app.action_handle_ctrl_c()
        await pilot.pause()

        assert len(app.query("#perm-inline")) == 0, "ctrl+c should remove the dialog"
        assert not app.query_one("#chat-input").disabled, "input should be usable again"
