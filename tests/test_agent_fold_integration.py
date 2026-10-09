"""会话记忆折叠的集成测试 —— auto_compact 折叠路径 + CompactBoundary 标志 + session 恢复。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalcode.conversation import ConversationManager
from personalcode.context.manager import CompactEvent, auto_compact


def _sample_memory() -> dict:
    return {
        "episode_memory": {
            "task_description": "Fix auth null pointer",
            "key_events": [{"step": "1", "description": "found bug", "outcome": "ok"}],
            "current_progress": "root cause found",
        },
        "working_memory": {
            "immediate_goal": "apply fix",
            "current_challenges": "none",
            "next_actions": [{"type": "tool_call", "description": "edit validate.py"}],
        },
        "tool_memory": {"tools_used": [], "derived_rules": []},
    }


class _FakeClient:
    """折叠路径的 side query 不调用主 client；这里仅占位满足 auto_compact 签名。"""

    async def stream(self, conversation, system="", tools=None):
        if False:
            yield  # pragma: no cover - 让函数成为 async generator
        return


async def _fake_fold_fn(transcript: str) -> dict:
    return _sample_memory()


class TestAutoCompactFoldPath:
    @pytest.mark.asyncio
    async def test_fold_replaces_history(self, tmp_path: Path) -> None:
        conv = ConversationManager()
        conv.add_user_message("help fix the auth bug")
        conv.add_assistant_message("found the null pointer")
        conv.add_user_message("keep digging")
        conv.add_assistant_message("root cause located")

        result = await auto_compact(
            conv,
            _FakeClient(),
            200_000,
            tmp_path,
            protocol="anthropic",
            manual=True,
            fold_memory_fn=_fake_fold_fn,
        )

        assert isinstance(result, CompactEvent)
        assert result.boundary is not None
        assert result.boundary.fold is True
        assert result.boundary.memory == _sample_memory()
        # 历史替换为单条折叠记忆 user 消息
        assert len(conv.history) == 1
        assert conv.history[0].role == "user"
        assert "<session-folded-memory>" in conv.history[0].content
        # 折叠后锚点清零（replace_history）
        assert conv.baseline_tokens == 0

    @pytest.mark.asyncio
    async def test_without_fold_keeps_summary(self, tmp_path: Path) -> None:
        """fold_memory_fn 为 None 时走原有摘要路径（保持兼容）。"""
        conv = ConversationManager()
        conv.add_user_message("task")
        conv.add_assistant_message("step done")
        conv.add_user_message("continue")
        conv.add_assistant_message("next step done")

        result = await auto_compact(
            conv,
            _FakeClient(),
            200_000,
            tmp_path,
            protocol="anthropic",
            manual=True,
            fold_memory_fn=None,
        )
        # 摘要路径需要 LLM 摘要；FakeClient 无 TextDelta，会返回失败串而非 CompactEvent
        assert not isinstance(result, CompactEvent)


class TestManualFoldOnAgent:
    @pytest.mark.asyncio
    async def test_manual_fold_disabled_without_ctx(self) -> None:
        from personalcode.agent import Agent

        client = _FakeClient()
        from personalcode.tools import create_default_registry

        agent = Agent(
            client=client,
            registry=create_default_registry(),
            protocol="anthropic",
            work_dir=".",
            context_window=200_000,
        )
        # 未设置 _fold_ctx：manual_fold 返回折叠禁用
        conv = ConversationManager()
        agent._current_conversation = conv
        result = await agent.manual_fold("test")
        assert result["ok"] is False
        assert "disabled" in result["error"]

    @pytest.mark.asyncio
    async def test_manual_fold_fold_ctx_set(self, tmp_path: Path) -> None:
        """设置了 _fold_ctx 后 manual_fold 走折叠路径（折叠函数返回固定记忆）。"""
        from personalcode.agent import Agent
        from personalcode.tools import create_default_registry

        class _Ctx:
            mode = "single_json"
            max_fold_limit = 3

        client = _FakeClient()
        agent = Agent(
            client=client,
            registry=create_default_registry(),
            protocol="anthropic",
            work_dir=str(tmp_path),
            context_window=200_000,
        )
        agent._fold_ctx = _Ctx()
        # 用假折叠函数替换，验证 manual_fold 调用 auto_compact 折叠路径
        async def fake_fold(transcript: str) -> dict:
            return _sample_memory()

        agent.generate_folded_session_memory = fake_fold

        conv = ConversationManager()
        conv.add_user_message("task")
        conv.add_assistant_message("step done")
        conv.add_user_message("continue")
        conv.add_assistant_message("next")
        conv.add_user_message("more")
        conv.add_assistant_message("done")
        agent._current_conversation = conv

        result = await agent.manual_fold("long context")
        assert result["ok"] is True
        assert "structured session memory" in result["message"]
        assert result["folded_memory_text"] is not None
        # 折叠计数已增加
        assert agent._fold_count == 1

    @pytest.mark.asyncio
    async def test_fold_limit_blocks(self) -> None:
        from personalcode.agent import Agent
        from personalcode.tools import create_default_registry

        class _Ctx:
            mode = "single_json"
            max_fold_limit = 1

        client = _FakeClient()
        agent = Agent(
            client=client,
            registry=create_default_registry(),
            protocol="anthropic",
            work_dir=".",
            context_window=200_000,
        )
        agent._fold_ctx = _Ctx()
        agent._fold_count = 1  # 已达上限
        conv = ConversationManager()
        conv.add_user_message("x")
        agent._current_conversation = conv
        result = await agent.manual_fold("x")
        assert result["ok"] is False
        assert "limit" in result["error"]


class TestSessionFoldedMemoryRecord:
    def test_make_and_parse_roundtrip(self) -> None:
        from personalcode.memory.session import (
            TYPE_FOLDED_MEMORY,
            make_folded_memory_record,
            parse_folded_memory_record,
        )

        record = make_folded_memory_record(_sample_memory(), trigger="auto", pre_fold_tokens=1234)
        assert record.type == TYPE_FOLDED_MEMORY
        memory = parse_folded_memory_record(record)
        assert memory == _sample_memory()
        assert record.is_folded_memory() is True

    def test_parse_corrupt_returns_none(self) -> None:
        from personalcode.memory.session import (
            SessionRecord,
            parse_folded_memory_record,
        )
        from datetime import datetime, timezone

        record = SessionRecord(role="system", content="not a dict", timestamp=datetime.now(timezone.utc), type="folded_memory")
        assert parse_folded_memory_record(record) is None

    def test_records_to_messages_restores_fold(self) -> None:
        from personalcode.memory.session import (
            make_folded_memory_record,
            records_to_messages,
        )

        record = make_folded_memory_record(_sample_memory())
        messages = records_to_messages([record])
        assert len(messages) == 1
        assert messages[0].role == "user"
        assert "<session-folded-memory>" in messages[0].content
