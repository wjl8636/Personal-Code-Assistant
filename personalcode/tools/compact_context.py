"""compact_context 工具：让模型在上下文变长时自主触发会话折叠。

与自动阈值触发（context/manager.py::auto_compact）互补：本工具是模型侧的自主动作，
把当前对话折叠成结构化 session memory、替换历史后继续执行。category=read，
default 模式自动放行（折叠不写外部文件，只是重排上下文）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from personalcode.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from personalcode.agent import Agent


class CompactContextParams(BaseModel):
    reason: str = Field(
        default="",
        description="Brief reason for compacting now, such as long context, many tool calls, repeated failures, or strategy change.",
    )


class CompactContextTool(Tool):
    name = "compact_context"
    description = (
        "Compact the current conversation context into structured session memory when "
        "the context is long, tool results are noisy, or a strategy reset is useful. "
        "Preserves task progress, current next steps, and tool-use experience, then "
        "continues from the folded memory."
    )
    params_model = CompactContextParams
    category = "read"

    def __init__(self) -> None:
        self._agent: Agent | None = None

    def set_agent(self, agent: Agent) -> None:
        self._agent = agent

    async def execute(self, params: BaseModel) -> ToolResult:
        assert isinstance(params, CompactContextParams)
        if self._agent is None or not hasattr(self._agent, "manual_fold"):
            return ToolResult(
                output="Error: compact_context not properly initialized",
                is_error=True,
            )

        manual_fold = getattr(self._agent, "manual_fold")
        result: dict[str, Any] = await manual_fold(params.reason)
        if result.get("ok"):
            return ToolResult(
                output=(
                    "Context compacted into structured session memory. "
                    "Continue from the folded memory now present in the conversation context."
                )
            )
        return ToolResult(output=f"Error: {result.get('error')}", is_error=True)