# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Literal

import yaml

Effect = Literal["allow", "deny", "ask"]

_RULE_RE = re.compile(r"^(\w+)\((.+)\)$")

_CONTENT_FIELDS: dict[str, str] = {
    "Bash": "command",
    "ReadFile": "file_path",
    "WriteFile": "file_path",
    "EditFile": "file_path",
    "Glob": "pattern",
    "Grep": "pattern",
}


@dataclass(frozen=True)
class Rule:
    tool_name: str
    pattern: str
    effect: Effect


    def matches(self, tool_name: str, content: str) -> bool:
        if self.tool_name != tool_name:
            return False
        return fnmatch(content, self.pattern)


def parse_rule(raw: str, effect: Effect) -> Rule:
    m = _RULE_RE.match(raw.strip())
    if not m:
        raise ValueError(f"无效的规则语法: {raw}")
    return Rule(tool_name=m.group(1), pattern=m.group(2), effect=effect)


def extract_content(tool_name: str, arguments: dict[str, Any]) -> str:
    # mcp_call 的匹配对象不是某一个参数，而是「要调用哪个 MCP 工具」，
    # 由 server + tool 两个参数合成 server__tool。这样规则写成
    # mcp_call(linear__*) 就能按服务器或按工具做 allow/deny。
    if tool_name == "mcp_call":
        from mewcode.tools.mcp_call import permission_content

        return permission_content(
            str(arguments.get("server", "")), str(arguments.get("tool", ""))
        )
    field = _CONTENT_FIELDS.get(tool_name)
    if field is None:
        return ""
    return str(arguments.get(field, ""))


def _load_rules_file(path: Path) -> list[Rule]:
    if not path.is_file():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return []
    if not isinstance(raw, list):
        return []
    rules: list[Rule] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        rule_str = entry.get("rule", "")
        effect = entry.get("effect", "")
        if effect not in ("allow", "deny", "ask"):
            continue
        try:
            rules.append(parse_rule(rule_str, effect))
        except ValueError:
            continue
    return rules


class RuleEngine:


    def __init__(
        self,
        user_rules_path: Path | None = None,
        project_rules_path: Path | None = None,
        local_rules_path: Path | None = None,
    ) -> None:
        self._user_path = user_rules_path
        self._project_path = project_rules_path
        self._local_path = local_rules_path
        # 规则文件解析结果缓存：path -> ((mtime_ns, size), rules)
        self._cache: dict[Path, tuple[tuple[int, int], list[Rule]]] = {}

    def _rules_for(self, path: Path | None) -> list[Rule]:
        """读取单个规则文件，命中缓存时不读盘也不解析。

        以 mtime + size 作为文件是否变动的依据，只比 mtime 不够：
        同一秒内的连续改写在部分文件系统上时间戳可能不变。
        """
        if path is None:
            return []

        try:
            st = path.stat()
        except OSError:
            # 文件不存在或读不到，按空规则处理，同时清掉可能存在的旧缓存
            self._cache.pop(path, None)
            return []

        stamp = (st.st_mtime_ns, st.st_size)
        cached = self._cache.get(path)
        if cached is not None and cached[0] == stamp:
            return cached[1]

        rules = _load_rules_file(path)
        self._cache[path] = (stamp, rules)
        return rules

    def snapshot(self) -> list[Rule]:
        """取三份规则文件的合并快照。

        文件没变动时直接复用上次的解析结果，变动了才重新读盘，
        因此改完规则文件下次评估即刻生效，反复评估也不会重复解析。
        一次工具调用取一次快照，复合命令逐条检查子命令时共用它。
        """
        rules: list[Rule] = []
        for p in (self._user_path, self._project_path, self._local_path):
            rules.extend(self._rules_for(p))
        return rules


    def evaluate(self, tool_name: str, content: str) -> Effect | None:
        """把三份规则文件的规则合并成一个集合，返回命中规则中最严格的效果。

        优先级 deny > ask > allow：规则写在哪一层、写在文件第几行都不影响裁决，
        因此一条 deny 无法被其他层的 allow 抵消。没有任何规则命中时返回 None。
        """
        return evaluate_rules(self.snapshot(), tool_name, content)


    def append_local_rule(self, rule: Rule) -> None:
        if self._local_path is None:
            return
        self._local_path.parent.mkdir(parents=True, exist_ok=True)
        existing = _load_rules_file(self._local_path)
        existing.append(rule)
        entries = [{"rule": f"{r.tool_name}({r.pattern})", "effect": r.effect} for r in existing]
        self._local_path.write_text(yaml.dump(entries, allow_unicode=True), encoding="utf-8")


def evaluate_rules(rules: list[Rule], tool_name: str, content: str) -> Effect | None:
    """在给定规则集上裁决，优先级 deny > ask > allow。

    没有任何规则命中时返回 None。
    """
    hit: Effect | None = None
    for rule in rules:
        if not rule.matches(tool_name, content):
            continue
        if rule.effect == "deny":
            # deny 已是最严效果，不可能再被压过，直接返回
            return "deny"
        if rule.effect == "ask":
            hit = "ask"
        elif hit is None:
            # allow 最弱，只在还没命中更严的效果时记录
            hit = "allow"
    return hit
