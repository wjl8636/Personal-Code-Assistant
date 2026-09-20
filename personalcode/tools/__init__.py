# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
from __future__ import annotations

from typing import Any

from personalcode.tools.base import (
    MCP_CALL_TOOL_NAME,
    TOOL_SEARCH_TOOL_NAME,
    Tool,
)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._disabled: set[str] = set()
        self._discovered: set[str] = set()
        # MCP 工具的加载方式，由 mcp.loading_strategy 在连上服务器后写入。
        # ToolSearch 靠它决定回什么、client 靠它决定要不要发 defer_loading。
        # 没有 MCP 时保持 eager，行为等同于不延迟。
        from personalcode.mcp.loading_strategy import McpLoadingMode

        self.mcp_loading_mode: McpLoadingMode = McpLoadingMode.EAGER

        # 检索和分发这两个工具要不要发给模型，由 apply_mode 在会话启动时算一次。
        # 不每轮按「当前还有没有延迟工具」现算：工具可能被运行时禁用，现算会让
        # tools[] 中途少一个，那就是一次数组变动，缓存前缀照样断。
        self.expose_tool_search: bool = False
        self.expose_mcp_call: bool = False

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)


    def is_enabled(self, name: str) -> bool:
        return name in self._tools and name not in self._disabled

    def enable(self, name: str) -> None:
        self._disabled.discard(name)


    def disable(self, name: str) -> None:
        if name in self._tools:
            self._disabled.add(name)

    def enable_all(self) -> None:
        self._disabled.clear()


    def mark_discovered(self, name: str) -> None:
        self._discovered.add(name)

    def is_discovered(self, name: str) -> bool:
        return name in self._discovered


    def get_deferred_tool_names(self) -> list[str]:
        """还没被捞出来的延迟工具名，按字典序。

        排序不只是为了好看：调用方要靠比较这份清单判断工具池到底变没变，顺序
        飘的话同一批工具会拼出不同的文本，比较就失效了。
        """
        return sorted(
            name
            for name, tool in self._tools.items()
            if getattr(tool, "should_defer", False)
            and name not in self._discovered
            and name not in self._disabled
        )

    def search_deferred(
        self, query: str, max_results: int, protocol: str = "anthropic"
    ) -> list[dict[str, Any]]:
        query_lower = query.lower()
        scored: list[tuple[int, str, Tool]] = []
        for name, tool in self._tools.items():
            if not getattr(tool, "should_defer", False):
                continue
            if name in self._disabled:
                continue
            score = 0
            name_lower = name.lower()
            desc_lower = (tool.description or "").lower()
            if query_lower in name_lower:
                score += 10
            if query_lower in desc_lower:
                score += 5
            for word in query_lower.split():
                if word in name_lower:
                    score += 3
                if word in desc_lower:
                    score += 1
            if score > 0:
                scored.append((score, name, tool))
        scored.sort(key=lambda x: x[0], reverse=True)
        results: list[dict[str, Any]] = []
        for _, _name, tool in scored[:max_results]:
            base = tool.get_schema()
            if protocol in ("openai", "openai-compat"):
                results.append({
                    "type": "function",
                    "name": base["name"],
                    "description": base["description"],
                    "parameters": base["input_schema"],
                })
            else:
                results.append(base)
        return results

    def find_deferred_by_names(
        self, names: list[str], protocol: str = "anthropic"
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for name in names:
            tool = self._tools.get(name)
            if tool is None:
                continue
            if not getattr(tool, "should_defer", False):
                continue
            base = tool.get_schema()
            if protocol in ("openai", "openai-compat"):
                results.append({
                    "type": "function",
                    "name": base["name"],
                    "description": base["description"],
                    "parameters": base["input_schema"],
                })
            else:
                results.append(base)
        return results

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())


    def get_all_schemas(self, protocol: str = "anthropic") -> list[dict[str, Any]]:
        from personalcode.mcp.loading_strategy import McpLoadingMode

        # 官方端点走原生延迟：工具留在 tools[] 里但打上 defer_loading，由服务端
        # 决定给不给模型看。这样即使发现了新工具，tools 数组的字节也不变。
        # 其他端点只能把延迟工具整个藏起来，靠 mcp_call 兜。
        native = (
            self.mcp_loading_mode is McpLoadingMode.NATIVE
            and protocol == "anthropic"
        )
        schemas: list[dict[str, Any]] = []
        for name, tool in self._tools.items():
            if name in self._disabled:
                continue
            # 检索和分发只在用得上的模式里发。eager 下没有延迟工具可搜、也不需要
            # 分发，两个都发过去只是白占 token，还可能引诱模型去绕一圈。
            if name == TOOL_SEARCH_TOOL_NAME and not self.expose_tool_search:
                continue
            if name == MCP_CALL_TOOL_NAME and not self.expose_mcp_call:
                continue
            deferred = (
                getattr(tool, "should_defer", False) and name not in self._discovered
            )
            if deferred and not native:
                continue
            base = tool.get_schema()
            if protocol in ("openai", "openai-compat"):
                schemas.append({
                    "type": "function",
                    "name": base["name"],
                    "description": base["description"],
                    "parameters": base["input_schema"],
                })
            else:
                if deferred:
                    base = {**base, "defer_loading": True}
                schemas.append(base)
        return schemas


def create_default_registry(file_history: Any = None) -> ToolRegistry:
    from personalcode.tools.bash import Bash
    from personalcode.tools.edit_file import EditFile
    from personalcode.tools.file_state_cache import FileStateCache
    from personalcode.tools.glob import Glob
    from personalcode.tools.grep import Grep
    from personalcode.tools.read_file import ReadFile
    from personalcode.tools.write_file import WriteFile

    file_state_cache = FileStateCache()

    registry = ToolRegistry()
    registry.register(ReadFile(file_state_cache=file_state_cache))
    registry.register(WriteFile(file_history=file_history, file_state_cache=file_state_cache))
    registry.register(EditFile(file_history=file_history, file_state_cache=file_state_cache))
    registry.register(Bash())
    registry.register(Glob())
    registry.register(Grep())
    return registry
