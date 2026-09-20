# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com

"""StreamingExecutor 的分批执行。

只读工具可以并发，写和命令类工具必须串行。收到就跑等于全部并发，模型在一轮里发
两个 EditFile 改同一个文件、或者 WriteFile 之后紧跟一条依赖它的 Bash，执行顺序
就不再是模型给出的那个顺序。

这里验三件事：写工具之间不重叠、只读工具确实并发、结果顺序等于提交顺序。
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from mewcode.agent import StreamingExecutor, _ToolExecResult
from mewcode.tools import ToolRegistry
from mewcode.tools.base import Tool, ToolResult


class _P(BaseModel):
    pass


class _Tool(Tool):
    """只读与否走 category 推导，跟真实工具同一条路径，不直接覆写 is_read_only。"""

    params_model = _P

    def __init__(self, name: str, category: str) -> None:
        self.name = name
        self.description = name
        self.category = category

    async def execute(self, params):  # pragma: no cover - 不走真实执行
        raise NotImplementedError


class _Call:
    """替代 ToolCallComplete 的最小体，只用到这三个字段。"""

    def __init__(self, tool_id: str, tool_name: str) -> None:
        self.tool_id = tool_id
        self.tool_name = tool_name
        self.arguments: dict = {}


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_Tool("ReadFile", category="read"))
    reg.register(_Tool("Grep", category="read"))
    reg.register(_Tool("EditFile", category="write"))
    reg.register(_Tool("Bash", category="command"))
    return reg


def _runner(log: list[str], hold: float = 0.02):
    """记录每个工具的进入和离开，用来判断有没有重叠执行。"""

    async def run_one(tc) -> _ToolExecResult:
        log.append(f"enter:{tc.tool_id}")
        await asyncio.sleep(hold)
        log.append(f"exit:{tc.tool_id}")
        return _ToolExecResult(
            tool_id=tc.tool_id,
            tool_name=tc.tool_name,
            result=ToolResult(output=tc.tool_id),
            elapsed=0.0,
        )

    return run_one


def _overlaps(log: list[str], a: str, b: str) -> bool:
    """a 还没 exit 就出现了 b 的 enter，说明两者重叠。"""
    ia, ib = log.index(f"enter:{a}"), log.index(f"enter:{b}")
    ea = log.index(f"exit:{a}")
    return ia < ib < ea or ib < ia < log.index(f"exit:{b}")


@pytest.mark.asyncio
async def test_write_tools_never_overlap():
    """两个写工具必须串行，这是这个类存在的理由。"""
    ex = StreamingExecutor()
    for i, name in enumerate(["EditFile", "EditFile", "Bash"]):
        ex.submit(_Call(f"t{i}", name))

    log: list[str] = []
    await ex.execute_all(_registry(), _runner(log))

    assert not _overlaps(log, "t0", "t1"), f"两个 EditFile 重叠了：{log}"
    assert not _overlaps(log, "t1", "t2"), f"EditFile 和 Bash 重叠了：{log}"
    assert log == [
        "enter:t0", "exit:t0", "enter:t1", "exit:t1", "enter:t2", "exit:t2",
    ]


@pytest.mark.asyncio
async def test_adjacent_read_tools_run_concurrently():
    """相邻的只读工具要并发，否则分批就白做了。"""
    ex = StreamingExecutor()
    for i, name in enumerate(["ReadFile", "Grep", "ReadFile"]):
        ex.submit(_Call(f"r{i}", name))

    log: list[str] = []
    await ex.execute_all(_registry(), _runner(log))

    # 三个都并发时，前三条一定全是 enter
    assert log[:3] == ["enter:r0", "enter:r1", "enter:r2"], log


@pytest.mark.asyncio
async def test_result_order_matches_submit_order():
    """读写混排时结果顺序必须等于提交顺序，否则 tool_result 配错 tool_use。"""
    ex = StreamingExecutor()
    names = ["ReadFile", "Grep", "EditFile", "ReadFile", "Bash"]
    for i, name in enumerate(names):
        ex.submit(_Call(f"x{i}", name))

    results = await ex.execute_all(_registry(), _runner([], hold=0.0))
    assert [r.tool_id for r in results] == [f"x{i}" for i in range(len(names))]


@pytest.mark.asyncio
async def test_failing_tool_keeps_its_tool_id():
    """单个工具抛异常时也要带上 tool_id，否则配不上 tool_use，下一轮请求会被拒。"""
    ex = StreamingExecutor()
    ex.submit(_Call("a", "ReadFile"))
    ex.submit(_Call("b", "EditFile"))

    async def boom(tc):
        if tc.tool_id == "b":
            raise RuntimeError("炸了")
        return _ToolExecResult(
            tool_id=tc.tool_id, tool_name=tc.tool_name,
            result=ToolResult(output="ok"), elapsed=0.0,
        )

    results = await ex.execute_all(_registry(), boom)
    assert [r.tool_id for r in results] == ["a", "b"]
    assert results[1].result.is_error
    assert "炸了" in results[1].result.output


@pytest.mark.asyncio
async def test_empty_executor():
    assert await StreamingExecutor().execute_all(_registry(), _runner([])) == []
    assert not StreamingExecutor().has_pending()


# ---------------------------------------------------------------------------
# 并发安全按参数算，不是只看工具类别
# ---------------------------------------------------------------------------

def test_bash_readonly_command_is_concurrency_safe():
    """ls、cat、git status 这类只读命令可以并发。"""
    from mewcode.tools.bash import Bash

    bash = Bash()
    for cmd in ["ls", "ls -la", "cat a.txt", "git status", "wc -l f", "pwd"]:
        assert bash.is_concurrency_safe({"command": cmd}), cmd


def test_bash_mutating_command_is_not_concurrency_safe():
    """会改东西的命令必须独占。"""
    from mewcode.tools.bash import Bash

    bash = Bash()
    for cmd in ["rm -rf build", "mv a b", "npm install", "git commit -m x",
                "echo hi > f", "ls | wc -l", "ls; rm x", "ls && rm x",
                "echo $(rm x)", "ls `rm x`"]:
        assert not bash.is_concurrency_safe({"command": cmd}), cmd


def test_bash_missing_or_bad_command_is_not_safe():
    """参数缺失或类型不对时按不安全处理，宁可串行也不能猜。"""
    from mewcode.tools.bash import Bash

    bash = Bash()
    assert not bash.is_concurrency_safe({})
    assert not bash.is_concurrency_safe({"command": None})
    assert not bash.is_concurrency_safe({"command": 123})


def test_default_predicate_follows_category():
    """没覆写的工具按类别走，行为跟以前一致。"""
    assert _Tool("ReadFile", category="read").is_concurrency_safe({})
    assert not _Tool("WriteFile", category="write").is_concurrency_safe({})
    assert not _Tool("Other", category="command").is_concurrency_safe({})


@pytest.mark.asyncio
async def test_readonly_bash_batches_with_read_tools():
    """只读的 Bash 要能跟 ReadFile 归到同一个并发批，这是这次改动的目的。"""
    from mewcode.agent import partition_tool_calls
    from mewcode.tools.bash import Bash

    reg = _registry()
    reg.register(Bash())

    calls = [
        _Call("a", "ReadFile"),
        _Call("b", "Bash"),
        _Call("c", "Grep"),
    ]
    calls[1].arguments = {"command": "git status"}

    batches = partition_tool_calls(calls, reg)
    assert len(batches) == 1, [ (b.concurrent, [c.tool_id for c in b.calls]) for b in batches ]
    assert batches[0].concurrent
    assert [c.tool_id for c in batches[0].calls] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_mutating_bash_breaks_the_batch():
    """会改东西的 Bash 必须把批次断开，前后各自成批。"""
    from mewcode.agent import partition_tool_calls
    from mewcode.tools.bash import Bash

    reg = _registry()
    reg.register(Bash())

    calls = [_Call("a", "ReadFile"), _Call("b", "Bash"), _Call("c", "Grep")]
    calls[1].arguments = {"command": "rm -rf build"}

    batches = partition_tool_calls(calls, reg)
    assert [(b.concurrent, [c.tool_id for c in b.calls]) for b in batches] == [
        (True, ["a"]), (False, ["b"]), (True, ["c"]),
    ]
