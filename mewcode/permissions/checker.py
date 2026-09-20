# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from mewcode.permissions.dangerous import DangerousCommandDetector, is_safe_command
from mewcode.permissions.modes import DecisionEffect, PermissionMode, mode_decide
from mewcode.permissions.rules import Rule, RuleEngine, evaluate_rules, extract_content
from mewcode.permissions.sandbox import PathSandbox
from mewcode.tools.base import Tool

_PLAN_MODE_ALLOWED_TOOLS = frozenset({"Agent", "ToolSearch", "AskUserQuestion", "ExitPlanMode"})
# mcp_call 刻意不进 plan 白名单：ToolSearch 只是读 schema，而 mcp_call 会真的
# 打到外部服务、可能改状态，plan 模式下仍要按权限规则逐个确认。


@dataclass
class Decision:
    effect: DecisionEffect
    reason: str


class PermissionChecker:


    def __init__(
        self,
        detector: DangerousCommandDetector,
        sandbox: PathSandbox,
        rule_engine: RuleEngine,
        mode: PermissionMode = PermissionMode.DEFAULT,
        sandbox_enabled: bool = False,
    ) -> None:
        self.detector = detector
        self.sandbox = sandbox
        self.rule_engine = rule_engine
        self.mode = mode
        self.plan_file_path: str = ""
        # OS 级沙箱是否启用（开启后命令类工具可自动放行，因为内核会兜底）
        self.sandbox_enabled = sandbox_enabled

    @staticmethod
    def describe_tool_action(tool_name: str, arguments: dict[str, Any]) -> str:
        """为 HITL 确认生成人类可读的操作描述。"""
        content = extract_content(tool_name, arguments)
        if content:
            return content
        # 无法从标准字段提取时，拼接参数摘要
        parts = []
        for k, v in arguments.items():
            sv = str(v)
            if len(sv) > 80:
                sv = sv[:77] + "..."
            parts.append(f"{k}={sv}")
        return ", ".join(parts) if parts else tool_name


    def check(self, tool: Tool, arguments: dict[str, Any]) -> Decision:
        content = extract_content(tool.name, arguments)

        # 规则快照按需取一次：安全命令、危险命令这些在前面几层就返回，压根不必碰规则文件；
        # 复合命令逐条检查子命令时共用同一份快照，不重复读盘
        snapshot: list[Rule] | None = None

        def rules() -> list[Rule]:
            nonlocal snapshot
            if snapshot is None:
                snapshot = self.rule_engine.snapshot()
            return snapshot

        # Layer 0: Plan 模式例外放行
        if self.mode == PermissionMode.PLAN:
            if tool.name in _PLAN_MODE_ALLOWED_TOOLS:
                return Decision(effect="allow", reason="Plan mode: allowed tool")
            if tool.name in ("WriteFile", "EditFile") and content:
                if self._is_plan_file(content):
                    return Decision(effect="allow", reason="Plan mode: plan file write")

        # Layer 1: 安全的只读命令（自动放行）
        if tool.category == "command" and is_safe_command(content or ""):
            return Decision(effect="allow", reason="Safe read-only command")

        # Layer 1b: 危险命令黑名单（仅 Bash）
        if tool.category == "command":
            hit, reason = self.detector.detect(content)
            if hit:
                return Decision(effect="deny", reason=f"危险命令拦截: {reason}")

        # Layer 1c: OS 沙箱自动放行
        # 沙箱开启时，命令类工具通过了危险命令检查后直接放行——
        # 内核级隔离会阻止越权写入，无需再弹确认。
        # 拆分复合命令逐条检查，防止通过命令拼接绕过权限检查，
        # deny 规则和 ask 规则不受沙箱影响。
        if self.sandbox_enabled and tool.category == "command":
            import re
            subcommands = [s.strip() for s in re.split(r'\s*(?:&&|\|\||[;|])\s*', content) if s.strip()]
            if not subcommands:
                subcommands = [content]
            has_ask = False
            for sub in subcommands:
                rule_result = evaluate_rules(rules(), tool.name, sub)
                if rule_result == "deny":
                    return Decision(effect="deny", reason="权限规则拒绝")
                if rule_result == "ask":
                    has_ask = True
            if has_ask:
                return Decision(effect="ask", reason="权限规则要求确认")
            return Decision(effect="allow", reason="OS 沙箱自动放行")

        # Layer 2: 路径沙箱（仅文件类工具）
        if tool.category in ("read", "write") and content:
            # 受保护路径优先判定：写入权限配置或 Skill 定义一律拒绝，bypass 模式同样拦截
            if tool.category == "write":
                ok, reason = self.sandbox.check_deny_write(content)
                if not ok:
                    return Decision(effect="deny", reason=f"受保护路径: {reason}")
            ok, reason = self.sandbox.check(content)
            if not ok and self.mode != PermissionMode.BYPASS:
                return Decision(effect="ask", reason=f"路径沙箱拦截: {reason}")

        # Layer 3: 规则引擎匹配
        rule_result = evaluate_rules(rules(), tool.name, content)
        if rule_result == "allow":
            return Decision(effect="allow", reason="权限规则放行")
        if rule_result == "ask":
            return Decision(effect="ask", reason="权限规则要求确认")
        if rule_result == "deny":
            return Decision(effect="deny", reason="权限规则拒绝")

        # Layer 4: 权限模式兜底判定
        effect = mode_decide(self.mode, tool.category)
        if effect == "allow":
            return Decision(effect="allow", reason=f"权限模式 {self.mode.value} 放行")
        if effect == "deny":
            return Decision(effect="deny", reason=f"权限模式 {self.mode.value} 拒绝")

        # Layer 5: 触发人工确认（HITL）
        return Decision(effect="ask", reason="需要用户确认")


    def _is_plan_file(self, target_path: str) -> bool:
        if not self.plan_file_path or not target_path:
            return ".mewcode/plans/" in target_path
        try:
            abs_target = os.path.abspath(target_path)
            abs_plan = os.path.abspath(self.plan_file_path)
            if abs_target == abs_plan:
                return True
        except Exception:
            pass
        if os.path.basename(target_path) == os.path.basename(self.plan_file_path):
            return True
        return ".mewcode/plans/" in target_path
