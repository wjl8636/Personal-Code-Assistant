# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com

"""mcp_call 分发工具与 MCP 加载分流的测试。"""
from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from mewcode.client import needs_tool_search_beta
from mewcode.conversation import Message, ToolResultBlock
from mewcode.mcp.loading_strategy import (
    McpLoadingMode,
    apply_mode,
    decide_mode,
    is_official_anthropic_endpoint,
    measure_mcp_schema_chars,
)
from mewcode.mcp.tool_wrapper import build_mcp_tool_name, mcp_tool_name_prefix
from mewcode.permissions.rules import extract_content
from mewcode.serialization import build_anthropic_messages
from mewcode.tools import ToolRegistry, create_default_registry
from mewcode.tools.base import Tool, ToolResult
from mewcode.tools.mcp_call import (
    McpCallParams,
    McpCallTool,
    coerce_by_schema,
    permission_content,
)


class _FakeMcpTool(Tool):
    """够用的 MCP 工具替身：暴露 schema、记录收到的参数。"""

    params_model = BaseModel
    category = "command"
    should_defer = True

    def __init__(self, server: str, tool: str, schema: dict[str, Any] | None = None) -> None:
        self.name = build_mcp_tool_name(server, tool)
        self.description = f"{tool} on {server}"
        self.mcp_server_name = server
        self.mcp_tool_name = tool
        self.mcp_input_schema = schema or {"type": "object", "properties": {}}
        self.received: dict[str, Any] | None = None

    def get_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.mcp_input_schema,
        }

    async def execute(self, params: BaseModel) -> ToolResult:  # pragma: no cover
        raise AssertionError("mcp_call 应该走 execute_raw")

    async def execute_raw(self, arguments: dict[str, Any]) -> ToolResult:
        self.received = arguments
        return ToolResult(output="ok")


_SCHEMA = {
    "type": "object",
    "properties": {
        "issueId": {"type": "string"},
        "limit": {"type": "integer"},
        "ratio": {"type": "number"},
        "flag": {"type": "boolean"},
        "labels": {"type": "array", "items": {"type": "string"}},
        "ports": {"type": "array", "items": {"type": "integer"}},
        "config": {
            "type": "object",
            "properties": {
                "replicas": {"type": "integer"},
                "features": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    "required": ["issueId"],
}


# ===========================================================================
# 强转契约：这七条四个语言必须逐条一致
# ===========================================================================

class TestCoerceBySchema:
    @pytest.mark.parametrize("given,want", [
        # string ← 数字
        ({"issueId": 8891}, {"issueId": "8891"}),
        ({"issueId": 1.5}, {"issueId": "1.5"}),
        # integer / number ← 数字形字符串
        ({"limit": "5"}, {"limit": 5}),
        ({"ratio": " 1.5 "}, {"ratio": 1.5}),
        # boolean ← "true"/"false"，大小写不敏感
        ({"flag": "true"}, {"flag": True}),
        ({"flag": "FALSE"}, {"flag": False}),
        # array ← 单键对象拆包
        ({"labels": {"item": ["a", "b"]}}, {"labels": ["a", "b"]}),
        # array ← 逗号分隔字符串
        ({"labels": "a, b"}, {"labels": ["a", "b"]}),
        # array 按 items 递归
        ({"ports": ["8080", "9090"]}, {"ports": [8080, 9090]}),
        # object 按 properties 递归，且嵌套层同样适用上面各条
        (
            {"config": {"replicas": "4", "features": {"item": ["x"]}}},
            {"config": {"replicas": 4, "features": ["x"]}},
        ),
    ])
    def test_contract(self, given: dict, want: dict) -> None:
        assert coerce_by_schema(given, _SCHEMA) == want

    def test_bool_not_stringified(self) -> None:
        # bool 是 int 的子类，不能被当成数字转成 "True"
        assert coerce_by_schema({"issueId": True}, _SCHEMA) == {"issueId": True}

    def test_unconvertible_left_alone(self) -> None:
        # 转不了的原样往下传，交给 MCP 服务器报它自己的错
        assert coerce_by_schema({"limit": "many"}, _SCHEMA) == {"limit": "many"}
        assert coerce_by_schema({"flag": "yes"}, _SCHEMA) == {"flag": "yes"}
        assert coerce_by_schema({"limit": "5abc"}, _SCHEMA) == {"limit": "5abc"}

    # 各语言的字符串转数字各有各的宽松处，下面几条锁住四个语言都不接受的形状
    def test_integer_shape(self) -> None:
        assert coerce_by_schema({"limit": "5.7"}, _SCHEMA) == {"limit": "5.7"}
        assert coerce_by_schema({"limit": "1_000"}, _SCHEMA) == {"limit": "1_000"}
        assert coerce_by_schema({"limit": "1e3"}, _SCHEMA) == {"limit": "1e3"}
        assert coerce_by_schema({"limit": "+5"}, _SCHEMA) == {"limit": 5}

    def test_number_shape(self) -> None:
        assert coerce_by_schema({"ratio": "1e3"}, _SCHEMA) == {"ratio": 1000.0}
        assert coerce_by_schema({"ratio": "inf"}, _SCHEMA) == {"ratio": "inf"}
        assert coerce_by_schema({"ratio": "nan"}, _SCHEMA) == {"ratio": "nan"}

    def test_multi_key_object_for_array_left_alone(self) -> None:
        # 拆包只认单键对象，多键的猜不出意图，原样传下去让服务器报错
        given = {"labels": {"item": "metrics", "tracing": ""}}
        assert coerce_by_schema(given, _SCHEMA) == given

    def test_unknown_keys_preserved(self) -> None:
        assert coerce_by_schema({"extra": 1}, _SCHEMA) == {"extra": 1}

    def test_already_correct_untouched(self) -> None:
        good = {"issueId": "X-1", "limit": 3, "flag": False, "ports": [1, 2]}
        assert coerce_by_schema(good, _SCHEMA) == good

    def test_empty_schema_is_noop(self) -> None:
        assert coerce_by_schema({"a": "1"}, {}) == {"a": "1"}


# ===========================================================================
# 工具名解析
# ===========================================================================

class TestResolveToolName:
    def _registry(self) -> tuple[ToolRegistry, McpCallTool, _FakeMcpTool]:
        registry = create_default_registry()
        tool = _FakeMcpTool("linear", "create_issue", _SCHEMA)
        registry.register(tool)
        dispatcher = McpCallTool(registry)
        registry.register(dispatcher)
        return registry, dispatcher, tool

    async def _call(self, dispatcher: McpCallTool, **kwargs: Any) -> ToolResult:
        return await dispatcher.execute(McpCallParams(**kwargs))

    @pytest.mark.asyncio
    async def test_full_name(self) -> None:
        _, d, tool = self._registry()
        res = await self._call(d, server="linear", tool="mcp__linear__create_issue",
                               arguments={"issueId": "A"})
        assert not res.is_error
        assert tool.received == {"issueId": "A"}

    @pytest.mark.asyncio
    async def test_short_name_with_server(self) -> None:
        # 模型很常只传短名，这里必须容错，否则白白多一轮重试
        _, d, tool = self._registry()
        res = await self._call(d, server="linear", tool="create_issue",
                               arguments={"issueId": "A"})
        assert not res.is_error
        assert tool.received == {"issueId": "A"}

    @pytest.mark.asyncio
    async def test_short_name_wrong_server_falls_back_to_suffix(self) -> None:
        _, d, tool = self._registry()
        res = await self._call(d, server="typo", tool="create_issue",
                               arguments={"issueId": "A"})
        assert not res.is_error
        assert tool.received == {"issueId": "A"}

    @pytest.mark.asyncio
    async def test_ambiguous_suffix_reports_error(self) -> None:
        registry = create_default_registry()
        registry.register(_FakeMcpTool("linear", "create_issue"))
        registry.register(_FakeMcpTool("jira", "create_issue"))
        d = McpCallTool(registry)
        res = await self._call(d, server="nope", tool="create_issue", arguments={})
        assert res.is_error
        assert "Unknown MCP tool" in res.output
        # 错误信息里要列出可用工具，模型才知道怎么改
        assert "mcp__linear__create_issue" in res.output

    @pytest.mark.asyncio
    async def test_coercion_applied_before_dispatch(self) -> None:
        _, d, tool = self._registry()
        await self._call(d, server="linear", tool="create_issue",
                        arguments={"issueId": 8891, "ports": ["1"]})
        assert tool.received == {"issueId": "8891", "ports": [1]}


# ===========================================================================
# 三路分流
# ===========================================================================

class TestLoadingStrategy:
    def test_official_endpoint_detection(self) -> None:
        assert is_official_anthropic_endpoint("")
        assert is_official_anthropic_endpoint("https://api.anthropic.com")
        assert not is_official_anthropic_endpoint("https://api.minimaxi.com/anthropic")

    def test_small_config_goes_eager(self) -> None:
        # 200k 上下文的 10% 是 2 万 token，1000 字符远低于此
        assert decide_mode("https://proxy.example.com", 200_000, 1000) is McpLoadingMode.EAGER

    def test_official_endpoint_goes_native(self) -> None:
        assert decide_mode("", 200_000, 500_000) is McpLoadingMode.NATIVE

    def test_third_party_goes_dispatch(self) -> None:
        assert decide_mode(
            "https://api.minimaxi.com/anthropic", 200_000, 500_000
        ) is McpLoadingMode.DISPATCH

    def test_no_mcp_tools_goes_eager(self) -> None:
        assert decide_mode("https://proxy.example.com", 200_000, 0) is McpLoadingMode.EAGER

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MEWCODE_MCP_LOADING", "dispatch")
        # 小配置本该 eager，被环境变量强制成 dispatch
        assert decide_mode("", 200_000, 10) is McpLoadingMode.DISPATCH

    def test_measure_counts_only_mcp_tools(self) -> None:
        registry = create_default_registry()
        builtin_only = measure_mcp_schema_chars(registry)
        registry.register(_FakeMcpTool("linear", "create_issue", _SCHEMA))
        assert builtin_only == 0
        assert measure_mcp_schema_chars(registry) > 0


class TestApplyMode:
    def _registry(self) -> ToolRegistry:
        registry = create_default_registry()
        registry.register(_FakeMcpTool("linear", "create_issue", _SCHEMA))
        return registry

    def test_eager_puts_mcp_tools_in_schemas_without_defer_flag(self) -> None:
        registry = self._registry()
        apply_mode(registry, McpLoadingMode.EAGER)
        mcp = [s for s in registry.get_all_schemas("anthropic")
               if s["name"].startswith("mcp__")]
        assert len(mcp) == 1
        assert "defer_loading" not in mcp[0]

    def test_native_keeps_tool_with_defer_flag(self) -> None:
        registry = self._registry()
        apply_mode(registry, McpLoadingMode.NATIVE)
        mcp = [s for s in registry.get_all_schemas("anthropic")
               if s["name"].startswith("mcp__")]
        assert len(mcp) == 1
        assert mcp[0]["defer_loading"] is True

    def test_dispatch_hides_tool_entirely(self) -> None:
        registry = self._registry()
        apply_mode(registry, McpLoadingMode.DISPATCH)
        mcp = [s for s in registry.get_all_schemas("anthropic")
               if s["name"].startswith("mcp__")]
        assert mcp == []

    def test_native_not_applied_on_openai_protocol(self) -> None:
        # defer_loading 是 Anthropic 的字段，openai 协议下不能带出去
        registry = self._registry()
        apply_mode(registry, McpLoadingMode.NATIVE)
        names = [s["name"] for s in registry.get_all_schemas("openai")]
        assert not any(n.startswith("mcp__") for n in names)


# ===========================================================================
# 权限：规则 content 归一化成 server__tool
# ===========================================================================

class TestPermissionContent:
    @pytest.mark.parametrize("server,tool,want", [
        ("linear", "mcp__linear__create_issue", "linear__create_issue"),
        ("linear", "create_issue", "linear__create_issue"),
        ("chrome-2", "mcp__chrome_2__click", "chrome_2__click"),
        # 短名和全名必须算出同一个 content，否则规则会漏匹配
        ("chrome-devtools", "click", "chrome_devtools__click"),
        ("chrome-devtools", "mcp__chrome_devtools__click", "chrome_devtools__click"),
    ])
    def test_normalization(self, server: str, tool: str, want: str) -> None:
        assert permission_content(server, tool) == want

    def test_short_and_full_name_agree(self) -> None:
        # 同一个工具，模型传短名或全名，规则 content 必须一致
        short = permission_content("chrome-devtools", "take-snapshot")
        full = permission_content("chrome-devtools", "mcp__chrome_devtools__take_snapshot")
        assert short == full == "chrome_devtools__take_snapshot"

    def test_extract_content_routes_mcp_call(self) -> None:
        got = extract_content(
            "mcp_call", {"server": "linear", "tool": "mcp__linear__create_issue"}
        )
        assert got == "linear__create_issue"

    def test_extract_content_other_tools_unchanged(self) -> None:
        assert extract_content("Bash", {"command": "ls"}) == "ls"
        assert extract_content("mcp__linear__create_issue", {"title": "x"}) == ""

    def test_rule_matches_by_server_glob(self) -> None:
        from mewcode.permissions.rules import evaluate_rules, parse_rule

        rules = [parse_rule("mcp_call(linear__*)", "allow")]
        content = extract_content("mcp_call", {"server": "linear", "tool": "create_issue"})
        assert evaluate_rules(rules, "mcp_call", content) == "allow"

    def test_rule_matches_exact_tool(self) -> None:
        from mewcode.permissions.rules import evaluate_rules, parse_rule

        rules = [parse_rule("mcp_call(infra__deploy_service)", "deny")]
        deploy = extract_content("mcp_call", {"server": "infra", "tool": "deploy_service"})
        scale = extract_content("mcp_call", {"server": "infra", "tool": "scale_service"})
        assert evaluate_rules(rules, "mcp_call", deploy) == "deny"
        assert evaluate_rules(rules, "mcp_call", scale) is None


# ===========================================================================
# 命名与序列化
# ===========================================================================

class TestNaming:
    def test_double_underscore_separator(self) -> None:
        assert build_mcp_tool_name("linear", "create_issue") == "mcp__linear__create_issue"

    def test_sanitizes_dashes_and_dots(self) -> None:
        assert build_mcp_tool_name("chrome-devtools", "take.snapshot") == (
            "mcp__chrome_devtools__take_snapshot"
        )

    def test_prefix_helper_matches_built_name(self) -> None:
        # 按服务器筛工具的地方都该用 prefix helper，自己拼会漏 sanitize
        name = build_mcp_tool_name("chrome-2", "click")
        assert name.startswith(mcp_tool_name_prefix("chrome-2"))


class TestToolResultSerialization:
    def test_structured_blocks_replace_text(self) -> None:
        blocks = [{"type": "tool_reference", "tool_name": "mcp__linear__create_issue"}]
        msg = Message(role="user", content="", tool_results=[
            ToolResultBlock(tool_use_id="t1", content="等价文本", content_blocks=blocks),
        ])
        out = build_anthropic_messages([msg])
        assert out[0]["content"][0]["content"] == blocks

    def test_plain_text_unchanged(self) -> None:
        msg = Message(role="user", content="", tool_results=[
            ToolResultBlock(tool_use_id="t1", content="plain"),
        ])
        out = build_anthropic_messages([msg])
        assert out[0]["content"][0]["content"] == "plain"


class TestToolSearchBetaHeader:
    """beta header 的开关条件：只有工具真带了 defer_loading 才发。

    官方端点这条路没法拿第三方端点真机验证，这里只能盯住请求该长什么样：
    header 漏了，defer_loading 会被服务端直接拒；header 多发了，不认识它的
    端点也会拒。两头都是硬失败。
    """

    def test_no_tools(self) -> None:
        assert needs_tool_search_beta([]) is False

    def test_all_eager(self) -> None:
        assert needs_tool_search_beta([{"name": "Bash"}, {"name": "ToolSearch"}]) is False

    def test_one_deferred(self) -> None:
        tools = [{"name": "Bash"}, {"name": "mcp__linear__x", "defer_loading": True}]
        assert needs_tool_search_beta(tools) is True

    def test_defer_loading_false_does_not_count(self) -> None:
        assert needs_tool_search_beta([{"name": "x", "defer_loading": False}]) is False


class TestToolExposureByMode:
    """检索和分发只在用得上的模式里发给模型。

    eager 下 MCP 工具全在 tools[] 里，既没有可搜的对象也不需要分发入口，
    两个都发过去只是白占 token，还可能引诱模型绕一圈。
    """

    def _registry(self, mode: McpLoadingMode) -> ToolRegistry:
        from mewcode.tools.impl.tool_search import ToolSearchTool

        registry = ToolRegistry()
        registry.register(ToolSearchTool(registry))
        registry.register(McpCallTool(registry))
        registry.register(_FakeMcpTool("linear", "create_issue", _SCHEMA))
        apply_mode(registry, mode)
        return registry

    def _names(self, registry: ToolRegistry) -> list[str]:
        return [s.get("name") for s in registry.get_all_schemas("anthropic")]

    def test_eager_sends_neither(self) -> None:
        names = self._names(self._registry(McpLoadingMode.EAGER))
        assert "ToolSearch" not in names
        assert "mcp_call" not in names
        assert "mcp__linear__create_issue" in names

    def test_native_sends_tool_search_only(self) -> None:
        names = self._names(self._registry(McpLoadingMode.NATIVE))
        assert "ToolSearch" in names
        assert "mcp_call" not in names
        assert "mcp__linear__create_issue" in names

    def test_dispatch_sends_both(self) -> None:
        names = self._names(self._registry(McpLoadingMode.DISPATCH))
        assert "ToolSearch" in names
        assert "mcp_call" in names
        assert "mcp__linear__create_issue" not in names

    def test_no_mcp_at_all_sends_neither(self) -> None:
        # 没连 MCP 时 apply_mode 不会被调用，两个开关保持默认关闭
        from mewcode.tools.impl.tool_search import ToolSearchTool

        registry = ToolRegistry()
        registry.register(ToolSearchTool(registry))
        registry.register(McpCallTool(registry))
        assert self._names(registry) == []

    def test_exposure_is_stable_when_tools_get_disabled(self) -> None:
        # 开关在 apply_mode 时算死，运行时禁用工具不会让 tools[] 少掉检索入口，
        # 否则就是一次数组变动，缓存前缀会断
        registry = self._registry(McpLoadingMode.DISPATCH)
        registry.disable("mcp__linear__create_issue")
        assert registry.get_deferred_tool_names() == []
        assert "ToolSearch" in self._names(registry)
