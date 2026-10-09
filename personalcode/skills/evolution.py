"""Skill 落盘与审计：从对话反馈沉淀可复用 SKILL.md，并记录每次沉淀的来源、版本与统计。

这条链路与 memory/auto_memory.py 的分工一致：Memory 记事实/偏好，Skill 沉淀可复用的
工作流、输出规范、判断标准与触发条件。Skill 只写「目录 + SKILL.md」形态（被
SkillLoader._scan_directory 扫描），单文件 .md 不参与自进化，避免两种形态混写。

所有写盘函数都按 personalcode 的注入式风格走参数（work_dir），不用全局 Path.cwd()。
审计产物统一落在 <work_dir>/.personalcode/skill-evolution/ 下，与 .personalcode/skills/
分离，避免污染 SkillLoader 的扫描根。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from personalcode.skills.parser import (
    VALID_NAME_RE,
    SkillParseError,
    parse_frontmatter,
    parse_skill_file,
)

# ---------------------------------------------------------------------------
# 目录与常量
# ---------------------------------------------------------------------------

EVOLUTION_DIR_NAME = "skill-evolution"
USAGE_LOG = "usage.jsonl"
ONLINE_PROVENANCE_LOG = "online_provenance.jsonl"
ONLINE_PROVENANCE_INDEX = "online_skill_provenance.json"
SKILL_USAGE_STATS = "skill_usage_stats.json"
HISTORY_DIR = "history"

SKILLS_DIR_NAME = "skills"


def get_evolution_dir(work_dir: str) -> Path:
    """Skill 自进化的审计目录：<work_dir>/.personalcode/skill-evolution/。"""
    return Path(work_dir) / ".personalcode" / EVOLUTION_DIR_NAME


def get_skills_root(work_dir: str, target: str = "project") -> Path:
    """返回项目级或用户级的 skill 根目录。"""
    if str(target or "project").strip().lower() == "user":
        return Path.home() / ".personalcode" / SKILLS_DIR_NAME
    return Path(work_dir) / ".personalcode" / SKILLS_DIR_NAME


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


# ---------------------------------------------------------------------------
# JSONL / JSON 读写
# ---------------------------------------------------------------------------


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# frontmatter 写盘（与 parser.parse_frontmatter 的 yaml.safe_load 兼容）
# ---------------------------------------------------------------------------


def format_skill_frontmatter(meta: dict[str, str], body: str) -> str:
    """把 meta 序列化成 yaml frontmatter + markdown body。

    与 parse_frontmatter（parser.py:34，yaml.safe_load）兼容——写入的 SKILL.md
    必须能被 parse_skill_file 重新解析。值里含冒号/特殊字符时交给 yaml 处理：
    这里用 json.dumps 兜底字符串引号，避免裸 auth 值破坏 frontmatter。
    """
    import yaml as _yaml

    lines = ["---"]
    for key, value in meta.items():
        # yaml.safe_dump 对单行字符串会带引号，这里直接拼 key: value，
        # 只有值含前导特殊字符或冒号时才显式加引号。
        if isinstance(value, str) and (
            ":" in value or value.startswith((" ", "-", "{", "["))
        ):
            value = _yaml.safe_dump(value, allow_unicode=True).strip()
        lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    lines.append(str(body or "").strip())
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 安全辅助
# ---------------------------------------------------------------------------


def _safe_skill_slug(name: str) -> str:
    """把 skill 名改写成安全的目录名（字母/数字/短横线/下划线，保留中文）。"""
    raw = str(name or "").strip()
    slug = re.sub(r"[^A-Za-z0-9_.\-一-鿿]+", "-", raw)
    slug = re.sub(r"-{2,}", "-", slug).strip("-.")
    if slug:
        return slug[:120].rstrip("-.") or slug[:120]
    if raw:
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
        return f"skill-{digest}"
    return "unknown"


def _preview(value: object, limit: int = 500) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _bump_patch(version: str | None) -> str:
    """版本号 patch 递增：0.1.0 -> 0.1.1。非 3 段数字时回退 0.1.1。"""
    raw = str(version or "0.1.0").strip()
    parts = raw.split(".")
    if len(parts) < 3 or not all(p.isdigit() for p in parts[:3]):
        return "0.1.1"
    parts[2] = str(int(parts[2]) + 1)
    return ".".join(parts[:3])


def _find_skill_file_by_name(base_dir: Path, skill_name: str) -> Path | None:
    """在 skill 根目录里按 frontmatter 的 name 定位同名 SKILL.md（而不是目录名）。

    用 parse_frontmatter 而非 parse_skill_file：兼容 context: inline 这类旧格式，
    完整校验会因字段不合法跳过真实存在的 skill。
    """
    if not base_dir.is_dir():
        return None
    wanted = str(skill_name or "").strip()
    if not wanted:
        return None
    for entry in base_dir.iterdir():
        if not entry.is_dir():
            continue
        skill_file = entry / "SKILL.md"
        if not skill_file.is_file():
            continue
        try:
            meta, _ = parse_frontmatter(skill_file.read_text(encoding="utf-8"))
        except (SkillParseError, OSError):
            continue
        if not isinstance(meta, dict):
            continue
        name = meta.get("name") or entry.name
        if name == wanted:
            return skill_file
    return None


def resolve_skill_file(work_dir: str, skill_name: str, *, target: str = "active", active_dir: str = "") -> Path | None:
    """定位已有 skill 的 SKILL.md 路径。

    target 语义对齐 BearCode：active 表示「项目级优先、其次用户级」；
    project / user 精确指定。active_dir 传入已加载 skill 的目录时优先用它。
    """
    target = (target or "active").strip().lower()
    if target == "active" and active_dir:
        skill_file = Path(active_dir) / "SKILL.md"
        if skill_file.is_file():
            return skill_file

    if target in ("project", "active"):
        found = _find_skill_file_by_name(get_skills_root(work_dir, "project"), skill_name)
        if found:
            return found

    if target in ("user", "active"):
        found = _find_skill_file_by_name(get_skills_root(work_dir, "user"), skill_name)
        if found:
            return found

    return None


# ---------------------------------------------------------------------------
# 使用/反馈记录
# ---------------------------------------------------------------------------


def record_skill_invocation(*, skill_name: str, source: str, context: str, args: object = "", work_dir: str = "") -> None:
    """记录一次 skill 调用，写入 usage.jsonl。

    work_dir 缺省时回退到当前目录（保持与旧调用方兼容）；新代码应显式传入，
    与模块内其他函数保持一致的全参数注入风格。
    """
    row = {
        "event": "invoke",
        "time": _utc_now(),
        "skill": skill_name,
        "source": source,
        "context": context,
        "args_preview": _preview(args),
    }
    _append_jsonl(get_evolution_dir(str(work_dir) or str(Path.cwd())) / USAGE_LOG, row)


def record_skill_feedback(*, skill_name: str, rating: str, note: str = "", work_dir: str = "") -> None:
    """记录一条用户对 skill 的反馈。"""
    row = {
        "event": "feedback",
        "time": _utc_now(),
        "skill": skill_name,
        "rating": str(rating or "").strip(),
        "note": _preview(note, 1200),
    }
    _append_jsonl(get_evolution_dir(str(work_dir) or str(Path.cwd())) / USAGE_LOG, row)


# ---------------------------------------------------------------------------
# SKILL.md 内容组装
# ---------------------------------------------------------------------------


def _normalize_mode(context: str | None) -> str:
    # 入参 context 表达「执行方式」（inline/fork），映射为 frontmatter 的 mode 字段。
    return "fork" if str(context or "").strip().lower() == "fork" else "inline"


_VALID_MODE_FIELD = {"inline", "fork"}


def _normalize_mode_field(meta: dict[str, Any]) -> None:
    """把 meta 里的 mode/context 字段规范成 parse_skill_file 能接受的形态。

    旧格式 context: inline/fork 会被转成 mode: inline/fork；非法的 context 值直接
    移除（让它回落默认 full）。原地修改 meta。
    """
    mode_val = str(meta.get("mode") or "").strip().lower()
    context_val = str(meta.get("context") or "").strip().lower()
    if mode_val in _VALID_MODE_FIELD:
        return
    if context_val in _VALID_MODE_FIELD:
        meta["mode"] = context_val
    meta.pop("context", None)


def _normalize_allowed_tools(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ",".join(str(item).strip() for item in value if str(item).strip())
    return ",".join(part.strip() for part in str(value).split(",") if part.strip())


def _skill_body(instructions: str, evidence: str = "") -> str:
    body = str(instructions or "").strip()
    if not body:
        body = (
            "# Goal\n\n"
            "Apply this reusable skill when the user's request matches the trigger.\n\n"
            "# Workflow\n\n"
            "1. Identify the user's concrete task and constraints.\n"
            "2. Follow the reusable rules captured in this skill.\n"
            "3. Produce the requested output directly.\n"
        )
    if not body.lstrip().startswith("#"):
        body = "# Skill Instructions\n\n" + body
    if evidence:
        body = body.rstrip() + "\n\n## Creation Evidence\n\n" + str(evidence).strip() + "\n"
    return body.rstrip() + "\n"


# ---------------------------------------------------------------------------
# create / evolve 落盘
# ---------------------------------------------------------------------------


def create_skill_file(
    *,
    work_dir: str,
    name: str,
    description: str,
    instructions: str,
    when_to_use: str = "",
    target: str = "project",
    context: str = "inline",
    user_invocable: bool = False,
    allowed_tools: object = None,
    evidence: str = "",
    actor: str = "agent",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """新建一个 SKILL.md（目录 + frontmatter + body）。

    成功返回 {"ok": True, **event}；失败返回 {"ok": False, "error": "..."}。
    写盘前用 parse_skill_file 校验能解析，杜绝脏写。
    """
    resolved_name = str(name or "").strip()
    if not resolved_name:
        return {"ok": False, "error": "skill name is required"}
    if not VALID_NAME_RE.match(resolved_name):
        return {"ok": False, "error": f"invalid skill name: {resolved_name!r} (use a-z 0-9 -)"}
    if not str(description or "").strip():
        return {"ok": False, "error": "description is required"}

    existing = resolve_skill_file(work_dir, resolved_name, target="active")
    if existing:
        return {"ok": False, "error": f"Skill already exists: {resolved_name}", "file": str(existing)}

    root = get_skills_root(work_dir, target)
    skill_dir = root / _safe_skill_slug(resolved_name)
    skill_file = skill_dir / "SKILL.md"
    if skill_file.exists():
        return {"ok": False, "error": f"Skill file already exists: {skill_file}"}

    meta = {
        "name": resolved_name,
        "description": re.sub(r"\s+", " ", str(description).strip()),
        "version": "0.1.0",
        "created-at": _utc_now(),
        "user-invocable": "true" if user_invocable else "false",
        # personalcode 的 parser 校验 context ∈ {full, recent, none}（携带多少上下文），
        # mode ∈ {inline, fork}（执行方式）。这里的 context 参数语义是「执行方式」，
        # 因此映射到 frontmatter 的 mode 字段；不带 context 字段让它回落到默认 full。
        "mode": _normalize_mode(context),
    }
    if when_to_use:
        meta["when-to-use"] = re.sub(r"\s+", " ", str(when_to_use).strip())
    if tags:
        meta["tags"] = ",".join(
            re.sub(r"\s+", "-", str(tag).strip()) for tag in tags if str(tag).strip()
        )
    tools = _normalize_allowed_tools(allowed_tools)
    if tools:
        meta["allowed-tools"] = tools

    try:
        skill_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        return {"ok": False, "error": f"skill directory already exists: {skill_dir}"}

    body = _skill_body(instructions, evidence)
    try:
        skill_file.write_text(format_skill_frontmatter(meta, body), encoding="utf-8")
        # 写盘后校验：必须能重新解析（防脏写）
        parse_skill_file(skill_file)
    except (OSError, SkillParseError) as e:
        # 校验失败时清理已写目录，不留残缺 skill
        try:
            shutil.rmtree(skill_dir, ignore_errors=True)
        except OSError:
            pass
        return {"ok": False, "error": f"write failed: {e}"}

    target_kind = "user" if str(target or "").strip().lower() == "user" else "project"
    event = {
        "event": "create",
        "time": _utc_now(),
        "actor": actor,
        "skill": resolved_name,
        "file": str(skill_file),
        "target": target_kind,
        "description": _preview(description, 1200),
        "when_to_use": _preview(when_to_use, 1200),
        "evidence": _preview(evidence, 1200),
    }
    _append_jsonl(get_evolution_dir(work_dir) / USAGE_LOG, event)
    return {"ok": True, **event}


def _append_evolution_note(body: str, lesson: str, rationale: str = "") -> str:
    """给 SKILL.md 正文追加一条 Evolution Note。"""
    lesson = re.sub(r"\s+", " ", str(lesson or "").strip())
    rationale = re.sub(r"\s+", " ", str(rationale or "").strip())
    if not lesson:
        raise ValueError("lesson is required")

    bullet = f"- {_today()}: {lesson}"
    if rationale:
        bullet += f" Reason: {rationale}"

    body = str(body or "").rstrip()
    marker = "## Evolution Notes"
    if marker in body:
        if lesson in body:
            return body + "\n"
        return body + "\n" + bullet + "\n"
    return body + "\n\n" + marker + "\n\n" + bullet + "\n"


def evolve_skill_file(
    *,
    work_dir: str,
    skill_name: str,
    lesson: str,
    rationale: str = "",
    target: str = "active",
    active_dir: str = "",
    actor: str = "agent",
    instructions: str = "",
    description: str = "",
    when_to_use: str = "",
    tags: list[str] | None = None,
    preserve_existing: bool = False,
) -> dict[str, Any]:
    """演化已有 SKILL.md：先快照到 history/，再 bump patch 版本并写回。

    合并语义：post 的 instructions 作为新指引，preserve_existing=True 时保留
    原正文并追加一条 merge 段落；否则以新 instructions 整替换并追加 Evolution Note。
    """
    skill_file = resolve_skill_file(work_dir, skill_name, target=target, active_dir=active_dir)
    if not skill_file:
        return {"ok": False, "error": f"Skill not found: {skill_name}"}

    raw = skill_file.read_text(encoding="utf-8")
    try:
        parsed_meta, body = parse_frontmatter(raw)
    except SkillParseError as e:
        return {"ok": False, "error": f"parse existing skill failed: {e}", "file": str(skill_file)}
    meta = dict(parsed_meta) if isinstance(parsed_meta, dict) else {}
    resolved_name = meta.get("name") or skill_file.parent.name

    # 快照（evolve 前完整内容）
    snapshot = {
        "time": _utc_now(),
        "event": "snapshot",
        "actor": actor,
        "skill": resolved_name,
        "file": str(skill_file),
        "version": meta.get("version", "0.1.0"),
        "lesson": _preview(lesson, 1200),
        "rationale": _preview(rationale, 1200),
        "content": raw,
    }
    history_path = get_evolution_dir(work_dir) / HISTORY_DIR / f"{_safe_skill_slug(resolved_name)}.jsonl"
    _append_jsonl(history_path, snapshot)

    meta["name"] = resolved_name
    meta["version"] = _bump_patch(meta.get("version"))
    meta["last-evolved"] = _utc_now()
    meta["evolution-count"] = str(int(str(meta.get("evolution-count", "0")) or "0") + 1)
    # 兼容旧格式：某些 skill 的 frontmatter 用 context: inline 表达执行方式，
    # 而 personalcode parser 只认 context ∈ {full, recent, none}。写回前统一
    # 转成 mode 字段（inline/fork），避免写回后 parse_skill_file 校验失败。
    _normalize_mode_field(meta)
    if description.strip():
        meta["description"] = re.sub(r"\s+", " ", str(description).strip())
    if when_to_use.strip():
        meta["when-to-use"] = re.sub(r"\s+", " ", str(when_to_use).strip())
    if tags:
        existing_tags = [part.strip() for part in str(meta.get("tags") or "").split(",") if part.strip()]
        merged_tags = existing_tags[:]
        for tag in tags:
            normalized = re.sub(r"\s+", "-", str(tag).strip())
            if normalized and normalized not in merged_tags:
                merged_tags.append(normalized)
        if merged_tags:
            meta["tags"] = ",".join(merged_tags[:12])

    if instructions.strip():
        new_body = _skill_body(instructions.strip())
        if preserve_existing and body.strip() and body.strip() not in new_body:
            new_body = body.rstrip() + "\n\n## Additional guidance from merge\n\n" + new_body.lstrip()
        note = _append_evolution_note("", lesson, rationale).strip()
        if note:
            new_body = new_body.rstrip() + "\n\n" + note + "\n"
    else:
        new_body = _append_evolution_note(body, lesson, rationale)

    try:
        skill_file.write_text(format_skill_frontmatter(meta, new_body), encoding="utf-8")
        parse_skill_file(skill_file)  # 校验能解析
    except (OSError, SkillParseError) as e:
        return {"ok": False, "error": f"write failed: {e}", "file": str(skill_file)}

    event = {
        "event": "evolve",
        "time": _utc_now(),
        "actor": actor,
        "skill": resolved_name,
        "file": str(skill_file),
        "version": meta["version"],
        "target": target,
        "lesson": _preview(lesson, 1200),
        "rationale": _preview(rationale, 1200),
        "history": str(history_path),
    }
    _append_jsonl(get_evolution_dir(work_dir) / USAGE_LOG, event)
    return {"ok": True, **event}


# ---------------------------------------------------------------------------
# provenance（在线沉淀的来源记录）
# ---------------------------------------------------------------------------


def record_online_skill_provenance(
    *,
    work_dir: str,
    action: str,
    skill_name: str = "",
    result: dict[str, Any] | None = None,
    messages: list[dict[str, Any]] | None = None,
    retrieved_reference: dict[str, Any] | None = None,
    decision: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    """记录一次在线沉淀的来源、决策与结果。

    无论 add/merge/discard/failed，都写入 online_provenance.jsonl，并按 skill
    聚合到 online_skill_provenance.json，保证「为什么写、怎么写、结果如何」可回放。
    """
    row = {
        "event": "online_ingest",
        "time": _utc_now(),
        "action": str(action or "none").strip() or "none",
        "skill": str(skill_name or "").strip(),
        "ok": bool((result or {}).get("ok")) if result is not None else not bool(error),
        "result": result or {},
        "messages": _compact_messages(list(messages or [])),
        "retrieved_reference": retrieved_reference or {},
        "decision": decision or {},
        "error": _preview(error, 1200),
    }
    _append_jsonl(get_evolution_dir(work_dir) / ONLINE_PROVENANCE_LOG, row)
    _update_online_provenance_index(work_dir, row)


def _compact_messages(messages: list[dict[str, Any]], *, max_messages: int = 12, max_chars: int = 4000) -> list[dict[str, str]]:
    """消息预览：只保留最近若干条 user/assistant 纯文本，超长截断。"""
    out: list[dict[str, str]] = []
    for item in list(messages or [])[-max_messages:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        out.append({"role": role, "content": _preview(content, max_chars)})
    return out


def _update_online_provenance_index(work_dir: str, row: dict[str, Any]) -> None:
    skill = str(row.get("skill") or "").strip()
    if not skill:
        return
    root = get_evolution_dir(work_dir)
    path = root / ONLINE_PROVENANCE_INDEX
    index = _read_json(path, {})
    item = index.setdefault(
        skill,
        {
            "skill": skill,
            "source_count": 0,
            "sources": [],
            "version_timeline": [],
            "usage": {},
        },
    )
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    item["current_version"] = result.get("version") or item.get("current_version", "")
    item["last_action"] = row.get("action")
    item["last_time"] = row.get("time")
    item["last_ok"] = row.get("ok")
    item["last_error"] = row.get("error", "")
    item["source_count"] = int(item.get("source_count", 0)) + 1
    sources = list(item.get("sources") or [])
    sources.append(
        {
            "time": row.get("time"),
            "action": row.get("action"),
            "ok": row.get("ok"),
            "messages": row.get("messages", []),
            "retrieved_reference": row.get("retrieved_reference", {}),
            "decision": row.get("decision", {}),
            "error": row.get("error", ""),
        }
    )
    item["sources"] = sources[-20:]
    if result.get("version"):
        timeline = list(item.get("version_timeline") or [])
        timeline.append({"time": row.get("time"), "version": result.get("version"), "action": row.get("action")})
        item["version_timeline"] = timeline[-50:]
    index[skill] = item
    _write_json(path, index)


# ---------------------------------------------------------------------------
# usage stats 与 stale prune
# ---------------------------------------------------------------------------


def record_skill_usage_judgments(*, work_dir: str, judgments: list[dict[str, Any]]) -> dict[str, Any]:
    """把「检索出的 skill 是否相关/被使用」的判断写进 skill_usage_stats.json。

    长期被检索但从未使用的 skill 会在满足门槛后归档到 <evolution_dir>/pruned/。
    """
    stats_path = get_evolution_dir(work_dir) / SKILL_USAGE_STATS
    stats = _read_json(stats_path, {})
    pruned: list[str] = []
    for judgment in judgments:
        skill = str(judgment.get("name") or judgment.get("skill") or "").strip()
        if not skill:
            continue
        item = stats.setdefault(
            skill,
            {
                "retrieved": 0,
                "relevant": 0,
                "used": 0,
                "last_retrieved": "",
                "last_used": "",
                "source": judgment.get("source", ""),
                "skill_dir": judgment.get("skill_dir", ""),
            },
        )
        item["retrieved"] = int(item.get("retrieved", 0)) + 1
        item["last_retrieved"] = _utc_now()
        item["source"] = judgment.get("source", item.get("source", ""))
        item["skill_dir"] = judgment.get("skill_dir", item.get("skill_dir", ""))
        if judgment.get("relevant"):
            item["relevant"] = int(item.get("relevant", 0)) + 1
        if judgment.get("used"):
            item["used"] = int(item.get("used", 0)) + 1
            item["last_used"] = _utc_now()
        item["last_reason"] = _preview(judgment.get("reason", ""), 500)
        item["last_score"] = judgment.get("score", 0)
        if _maybe_prune_stale_skill(work_dir, skill, item):
            pruned.append(skill)
    _write_json(stats_path, stats)
    _sync_usage_into_provenance(work_dir, stats)
    return {"ok": True, "judgments": len(judgments), "pruned": pruned}


def _maybe_prune_stale_skill(work_dir: str, skill_name: str, stats: dict[str, Any]) -> bool:
    """判断是否把长期未用的 skill 归档。默认保守：源非 user 级且未开开关时不 pruning。"""
    min_retrieved = _parse_int(os.environ.get("PERSONALCODE_SKILL_USAGE_PRUNE_MIN_RETRIEVED"), 40)
    max_used = _parse_int(os.environ.get("PERSONALCODE_SKILL_USAGE_PRUNE_MAX_USED"), 0)
    source = str(stats.get("source") or "").strip().lower()
    if source != "user" and os.environ.get("PERSONALCODE_SKILL_PRUNE_PROJECT", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    if int(stats.get("retrieved", 0)) < min_retrieved:
        return False
    if int(stats.get("used", 0)) > max_used:
        return False
    skill_dir = Path(str(stats.get("skill_dir") or ""))
    if not skill_dir.is_dir():
        return False
    if skill_dir.name.startswith("."):
        return False
    archive_root = get_evolution_dir(work_dir) / "pruned"
    archive_root.mkdir(parents=True, exist_ok=True)
    destination = archive_root / f"{_safe_skill_slug(skill_name)}-{int(time.time())}"
    try:
        shutil.move(str(skill_dir), str(destination))
    except Exception:
        return False
    event = {
        "event": "prune",
        "time": _utc_now(),
        "skill": skill_name,
        "from": str(skill_dir),
        "to": str(destination),
        "retrieved": stats.get("retrieved", 0),
        "used": stats.get("used", 0),
    }
    _append_jsonl(get_evolution_dir(work_dir) / USAGE_LOG, event)
    return True


def _parse_int(value: str | None, default: int = 0) -> int:
    try:
        return int(str(value or "").strip())
    except Exception:
        return default


def _sync_usage_into_provenance(work_dir: str, stats: dict[str, Any]) -> None:
    path = get_evolution_dir(work_dir) / ONLINE_PROVENANCE_INDEX
    index = _read_json(path, {})
    if not isinstance(index, dict):
        return
    changed = False
    for skill, usage in stats.items():
        if skill in index:
            index[skill]["usage"] = usage
            changed = True
    if changed:
        _write_json(path, index)


# ---------------------------------------------------------------------------
# 统计与展示
# ---------------------------------------------------------------------------


def load_skill_stats(work_dir: str) -> dict[str, dict[str, Any]]:
    """聚合 usage.jsonl + history 快照数 + skill_usage_stats.json 为 skill 维度统计。"""
    stats: dict[str, dict[str, Any]] = {}
    usage_path = get_evolution_dir(work_dir) / USAGE_LOG
    if usage_path.is_file():
        for line in usage_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            skill = str(row.get("skill") or "").strip()
            if not skill:
                continue
            item = stats.setdefault(skill, {"created": 0, "invocations": 0, "feedback": 0, "evolutions": 0})
            event = row.get("event")
            if event == "create":
                item["created"] = int(item.get("created", 0)) + 1
                item["created_at"] = row.get("time")
                item["file"] = row.get("file")
            elif event == "invoke":
                item["invocations"] = int(item.get("invocations", 0)) + 1
                item["last_invoked"] = row.get("time")
            elif event == "feedback":
                item["feedback"] = int(item.get("feedback", 0)) + 1
                item["last_feedback"] = row.get("time")
            elif event == "evolve":
                item["evolutions"] = int(item.get("evolutions", 0)) + 1
                item["last_evolved"] = row.get("time")
                item["version"] = row.get("version")
                item["file"] = row.get("file")

    history_root = get_evolution_dir(work_dir) / HISTORY_DIR
    if history_root.is_dir():
        for path in history_root.glob("*.jsonl"):
            count = len([line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()])
            skill = path.stem
            item = stats.setdefault(skill, {"created": 0, "invocations": 0, "feedback": 0, "evolutions": 0})
            item["snapshots"] = count

    usage_stats = _read_json(get_evolution_dir(work_dir) / SKILL_USAGE_STATS, {})
    if isinstance(usage_stats, dict):
        for skill, usage in usage_stats.items():
            if not isinstance(usage, dict):
                continue
            item = stats.setdefault(str(skill), {"created": 0, "invocations": 0, "feedback": 0, "evolutions": 0})
            item["retrieved"] = usage.get("retrieved", 0)
            item["relevant"] = usage.get("relevant", 0)
            item["used"] = usage.get("used", 0)
            item["last_retrieved"] = usage.get("last_retrieved", "")
            item["last_used"] = usage.get("last_used", "")
            item["pruned"] = usage.get("pruned", False)

    return stats


def format_skill_stats(work_dir: str) -> str:
    stats = load_skill_stats(work_dir)
    if not stats:
        return "No skill evolution events recorded yet."
    lines = ["Skill evolution stats:"]
    for name in sorted(stats):
        item = stats[name]
        parts = [
            f"created={item.get('created', 0)}",
            f"invoked={item.get('invocations', 0)}",
            f"feedback={item.get('feedback', 0)}",
            f"evolved={item.get('evolutions', 0)}",
            f"snapshots={item.get('snapshots', 0)}",
            f"retrieved={item.get('retrieved', 0)}",
            f"used={item.get('used', 0)}",
        ]
        if item.get("created_at"):
            parts.append(f"created_at={item['created_at']}")
        if item.get("version"):
            parts.append(f"version={item['version']}")
        if item.get("last_used"):
            parts.append(f"last_used={item['last_used']}")
        if item.get("pruned"):
            parts.append("pruned=true")
        lines.append(f"  {name}: " + ", ".join(parts))
    return "\n".join(lines)