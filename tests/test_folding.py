"""会话记忆折叠基础件的测试 —— transcript 构建 / parse / format / fallback。"""
from __future__ import annotations

import json

import pytest

from personalcode.context.folding import (
    MAX_TRANSCRIPT_CHARS,
    build_folding_user_prompt,
    build_transcript_from_history,
    fallback_folded_memory,
    format_folded_memory,
    parse_fold_component,
    parse_folded_memory,
)
from personalcode.conversation import (
    ConversationManager,
    Message,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)


def _sample_memory() -> dict:
    return {
        "episode_memory": {
            "task_description": "Fix the null pointer in auth",
            "key_events": [{"step": "1", "description": "Found bug", "outcome": "ok"}],
            "current_progress": "found root cause",
        },
        "working_memory": {
            "immediate_goal": "apply fix",
            "current_challenges": "none",
            "next_actions": [{"type": "tool_call", "description": "edit validate.py"}],
        },
        "tool_memory": {
            "tools_used": [{
                "tool_name": "EditFile",
                "effective_parameters": ["validate.py"], "common_errors": [], "response_pattern": "ok", "experience": "edit works",
            }],
            "derived_rules": ["always re-read before edit"],
        },
    }


# ---------------------------------------------------------------------------
# transcript 构建
# ---------------------------------------------------------------------------

class TestBuildTranscript:
    def test_basic_messages(self) -> None:
        conv = ConversationManager()
        conv.add_user_message("Read both files")
        conv.add_assistant_message("ok")
        text = build_transcript_from_history(conv.history)
        assert "## Message 0 (user)" in text
        assert "Read both files" in text
        assert "## Message 1 (assistant)" in text

    def test_skips_system_role(self) -> None:
        conv = ConversationManager()
        conv.history.append(Message(role="system", content="sys"))
        conv.add_user_message("hello")
        text = build_transcript_from_history(conv.history)
        assert "sys" not in text
        assert "hello" in text

    def test_renders_tool_blocks(self) -> None:
        conv = ConversationManager()
        conv.history.append(
            Message(
                role="assistant",
                content="reading",
                tool_uses=[ToolUseBlock(tool_use_id="tu1", tool_name="ReadFile", arguments={"file_path": "a.py"})],
                thinking_blocks=[ThinkingBlock(thinking="think about it", signature="sig")],
            )
        )
        conv.history.append(
            Message(
                role="user",
                content="",
                tool_results=[ToolResultBlock(tool_use_id="tu1", content="def f(): pass", is_error=False)],
            )
        )
        text = build_transcript_from_history(conv.history)
        assert "TOOL_USE id=tu1 name=ReadFile" in text
        assert 'file_path' in text and 'a.py' in text
        assert "TOOL_RESULT id=tu1" in text
        assert "def f()" in text

    def test_clips_oversized(self) -> None:
        conv = ConversationManager()
        conv.add_user_message("x" * 200_000)
        text = build_transcript_from_history(conv.history)
        assert len(text) <= MAX_TRANSCRIPT_CHARS

    def test_folding_prompt_wraps(self) -> None:
        prompt = build_folding_user_prompt("some transcript")
        assert "structured session memory JSON" in prompt
        assert "some transcript" in prompt


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------

class TestParseFoldedMemory:
    def test_parses_valid(self) -> None:
        result = parse_folded_memory(json.dumps(_sample_memory(), ensure_ascii=False))
        assert result["episode_memory"]["task_description"] == "Fix the null pointer in auth"
        assert result["working_memory"]["immediate_goal"] == "apply fix"
        assert result["tool_memory"]["derived_rules"] == ["always re-read before edit"]

    def test_parses_fenced_json(self) -> None:
        fenced = "```json\n" + json.dumps(_sample_memory(), ensure_ascii=False) + "\n```"
        result = parse_folded_memory(fenced)
        assert result["episode_memory"]["task_description"] == "Fix the null pointer in auth"

    def test_tolerates_missing_fields(self) -> None:
        """字段缺失给默认空值，不抛异常（对齐 parse_compact_boundary 降级哲学）。"""
        result = parse_folded_memory('{"episode_memory": {"task_description": "x"}}')
        assert result["episode_memory"]["key_events"] == []
        assert result["episode_memory"]["current_progress"] == ""
        assert result["working_memory"]["immediate_goal"] == ""
        assert result["tool_memory"]["tools_used"] == []

    def test_rejects_non_object(self) -> None:
        with pytest.raises(ValueError):
            parse_folded_memory("[1,2,3]")

    def test_parse_component_valid(self) -> None:
        raw = json.dumps({"tools_used": [], "derived_rules": ["rule"]})
        result = parse_fold_component("tool_memory", raw)
        assert result["derived_rules"] == ["rule"]

    def test_parse_component_missing_field(self) -> None:
        with pytest.raises(ValueError, match="missing fields"):
            parse_fold_component("tool_memory", '{"tools_used": []}')


# ---------------------------------------------------------------------------
# fallback 与 format
# ---------------------------------------------------------------------------

class TestFallbackAndFormat:
    def test_fallback_keeps_transcript(self) -> None:
        result = fallback_folded_memory("long transcript content")
        assert result["episode_memory"]["current_progress"] == "long transcript content"
        assert result["working_memory"]["next_actions"]

    def test_format_renders_tag(self) -> None:
        out = format_folded_memory(_sample_memory())
        assert "<session-folded-memory>" in out
        assert "</session-folded-memory>" in out
        assert "task_description" in out
        assert "Continue the task from this state" in out

    def test_format_roundtrip(self) -> None:
        text = format_folded_memory(_sample_memory())
        # 从渲染文本里剥掉标签重新 parse 应能恢复
        body = text.split("</session-folded-memory>")[0].split(">", 1)[1].strip()
        memory = parse_folded_memory(body)
        assert memory["episode_memory"]["task_description"] == "Fix the null pointer in auth"


# ---------------------------------------------------------------------------
# 与 ConversationManager 集成（replace_history 兼容）
# ---------------------------------------------------------------------------

class TestConversationIntegration:
    def test_folded_memory_replaces_history(self) -> None:
        conv = ConversationManager()
        conv.add_user_message("task")
        conv.add_assistant_message("step done")
        fold_text = format_folded_memory(_sample_memory())
        conv.replace_history([Message(role="user", content=fold_text)])
        assert len(conv.history) == 1
        assert conv.history[0].role == "user"
        assert "<session-folded-memory>" in conv.history[0].content
        # replace_history 清掉用量锚点，退化回字符估算
        assert conv.baseline_tokens == 0