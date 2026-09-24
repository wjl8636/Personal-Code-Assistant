from __future__ import annotations

# 这两个工具是「按模式发不发」的，注册表按名字筛，名字集中在这里定义，
# 免得注册表反向 import 具体实现造成循环依赖。
TOOL_SEARCH_TOOL_NAME = "ToolSearch"
MCP_CALL_TOOL_NAME = "mcp_call"

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".tox", ".mypy_cache"}

# 单条工具结果进入对话历史前的溢写阈值：超过这个字符数就把完整内容写盘，
# 历史里只留预览和文件路径。定在 50000 而不是更小的值，是为了让模型一次
# 能看到足够多的内容，不必为了看全结果再发一轮 ReadFile。
MAX_OUTPUT_CHARS = 50000

ToolCategory = Literal["read", "write", "command"]


@dataclass
class ToolResult:
    output: str
    is_error: bool = False
    # 需要把工具结果发成结构化 content block（而不是纯文本）时用它。
    # 目前只有官方 Anthropic 端点下的 ToolSearch 会用到：它回的是
    # tool_reference 块，由服务端把 schema 展开进上下文。填了这个字段时
    # output 仍然保留一份人可读的等价文本，供 TUI 和日志展示。
    content_blocks: list[dict[str, Any]] | None = None


class Tool(ABC):
    name: str
    description: str
    params_model: type[BaseModel]
    category: ToolCategory = "read"

    # 要不要延迟加载。延迟的工具不出现在初始 tool list 里，模型得先用 ToolSearch
    # 把 schema 捞出来才能调。只有 MCP 工具覆盖成 True：MCP 是按项目配的，一个服务器
    # 动辄几十个工具，schema 又长，全塞进初始 tool list 会把上下文占掉一大块，而且
    # 大部分工具这次会话根本用不上。内建工具是固定的那几十个，数量可控，藏起来只会
    # 让模型多绕一次 ToolSearch，所以一律不延迟，直接给全量 schema。
    should_defer: bool = False

    @property
    def is_read_only(self) -> bool:
        return self.category == "read"

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        """这一次调用能不能跟别的并发跑，按实际参数判断而不是只看类别。

        默认按类别走：只读的可以并发，写和命令类不行。命令类工具可以覆写这个方法，
        因为一条命令是不是只读要看命令本身，ls 和 rm 都是 Bash，并发安全性完全不同。
        """
        return self.is_read_only


    def get_schema(self) -> dict[str, Any]:
        schema = self.params_model.model_json_schema()
        schema.pop("title", None)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
        }

    @abstractmethod
    async def execute(self, params: BaseModel) -> ToolResult: ...


# --- 流式事件 ---


@dataclass
class TextDelta:
    text: str


@dataclass
class ToolCallStart:
    tool_name: str
    tool_id: str


@dataclass
class ToolCallDelta:
    text: str


@dataclass
class ToolCallComplete:
    tool_id: str
    tool_name: str
    arguments: dict[str, Any]


@dataclass
class ThinkingDelta:
    text: str


@dataclass
class ThinkingComplete:
    thinking: str
    signature: str


@dataclass
class StreamEnd:
    stop_reason: str
    input_tokens: int = 0
    output_tokens: int = 0
    # API 返回的 prompt cache 用量。Anthropic 把缓存前缀 token 分为
    # "read"（cache 命中，按 10% 计费）和 "creation"（cache 写入）。
    # input_tokens 已排除这两部分，因此实际 prompt 大小 =
    # input + cache_read + cache_creation。OpenAI 系列只暴露
    # cache_read（通过 *_tokens_details.cached_tokens），没有 creation
    # 计数，所以 cache_creation 在那边始终为 0。
    cache_read: int = 0
    cache_creation: int = 0


StreamEvent = TextDelta | ThinkingDelta | ThinkingComplete | ToolCallStart | ToolCallDelta | ToolCallComplete | StreamEnd
