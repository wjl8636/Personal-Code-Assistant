"""在线 Skill 自进化：从「对话 + 下一轮用户反馈」中抽取候选、决策、沉淀。

参照 BearCode online_skill_evolution.py 的四闭环设计，但适配 personalcode 的
注入式风格：所有路径显式传 work_dir；side_query 与 recall.SelectorFn 同型
（Callable[[system, user_message], Awaitable[str]]），由消费方构造（app.py / agent）。

链路：
    pending window（当前轮 + 下一轮反馈）
      -> extract_online_skill_candidate()   抽取最多一个候选
      -> maintain_online_skill_candidate()  add / merge / discard 决策
      -> create_skill_file / evolve_skill_file 落盘
      -> record_online_skill_provenance()   审计
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable

from personalcode.skills.evolution import (
    create_skill_file,
    evolve_skill_file,
    record_online_skill_provenance,
)
from personalcode.skills.loader import SkillLoader

SideQuery = Callable[[str, str], Awaitable[str]]
ConfirmWrite = Callable[[str], Awaitable[bool]]


@dataclass
class OnlineSkillCandidate:
    name: str
    description: str
    when_to_use: str = ""
    instructions: str = ""
    evidence: str = ""
    tags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# JSON 容错解析
# ---------------------------------------------------------------------------


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except Exception:
            return {}
    return {}


def _normalize_identity(text: str) -> str:
    raw = re.sub(r"[^a-zA-Z0-9一-鿿]+", " ", str(text or "").lower())
    return re.sub(r"\s+", " ", raw).strip()


def _candidate_search_text(candidate: OnlineSkillCandidate) -> str:
    return "\n".join(
        [
            candidate.name,
            candidate.description,
            candidate.when_to_use,
            candidate.instructions,
            " ".join(candidate.tags),
        ]
    )


def _coerce_candidate(obj: dict[str, Any]) -> OnlineSkillCandidate | None:
    name = str(obj.get("name") or "").strip()
    description = str(obj.get("description") or "").strip()
    instructions = str(obj.get("instructions") or obj.get("prompt") or "").strip()
    if not name or not description or not instructions:
        return None
    tags_raw = obj.get("tags") or []
    if isinstance(tags_raw, str):
        tags = [part.strip() for part in re.split(r"[,，]", tags_raw) if part.strip()]
    elif isinstance(tags_raw, list):
        tags = [str(part).strip() for part in tags_raw if str(part).strip()]
    else:
        tags = []
    return OnlineSkillCandidate(
        name=name,
        description=description,
        when_to_use=str(obj.get("when_to_use") or obj.get("when-to-use") or "").strip(),
        instructions=instructions,
        evidence=str(obj.get("evidence") or "").strip(),
        tags=tags[:8],
    )


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


async def extract_online_skill_candidate(
    *,
    messages: list[dict[str, Any]],
    side_query: SideQuery,
    retrieved_reference: dict[str, Any] | None = None,
    hint: str = "",
) -> OnlineSkillCandidate | None:
    system = (
        "You are PersonalCode's online Skill Extractor.\n"
        "Extract at most ONE reusable skill candidate from a live conversation window.\n"
        "Output ONLY strict JSON: {\"skills\": []} or {\"skills\": [{...}]}.\n\n"
        "Candidate fields: name, description, when_to_use, instructions, evidence, tags.\n\n"
        "Rules:\n"
        "- USER turns are the primary evidence. Assistant turns are context only.\n"
        "- A next user feedback turn may confirm, reject, or refine the prior assistant behavior.\n"
        "- Do not extract assistant-only guesses, weak confirmations, one-off task payload, "
        "secrets, project facts, URLs, account IDs, exact dates, or temporary parameters.\n"
        "- Extract only durable workflow, output policy, implementation preference, correction, "
        "or repeated constraint likely useful for future similar tasks.\n"
        "- Remove entity names and runtime-specific payload; use placeholders where needed.\n"
        "- retrieved_reference is identity context only; never treat it as new user evidence.\n"
        "- If evidence is weak, generic, or low-value, return {\"skills\": []}.\n"
    )
    payload = {
        "messages": messages,
        "hint": hint,
        "retrieved_reference": retrieved_reference or None,
    }
    parsed = _parse_json_object(await side_query(system, json.dumps(payload, ensure_ascii=False)))
    skills = parsed.get("skills")
    if not isinstance(skills, list) or not skills:
        return None
    first = skills[0]
    if not isinstance(first, dict):
        return None
    return _coerce_candidate(first)


# ---------------------------------------------------------------------------
# Maintainer
# ---------------------------------------------------------------------------


def _exact_identity_match(candidate: OnlineSkillCandidate, skills: list[Any]) -> str:
    """name / description / when_to_use 归一化后交叠即视为同一个 skill，返回其 name。"""
    candidate_ids = {
        _normalize_identity(candidate.name),
        _normalize_identity(candidate.description),
        _normalize_identity(candidate.when_to_use),
    }
    candidate_ids.discard("")
    for skill in skills:
        skill_ids = {
            _normalize_identity(getattr(skill, "name", "")),
            _normalize_identity(getattr(skill, "description", "")),
            _normalize_identity(getattr(skill, "when_to_use", "") or ""),
        }
        skill_ids.discard("")
        if candidate_ids & skill_ids:
            return getattr(skill, "name", "")
    return ""


def _existing_skills_as_dicts(loader: SkillLoader) -> list[dict[str, Any]]:
    skills: list[dict[str, Any]] = []
    for name, description in loader.get_catalog():
        skills.append(
            {
                "name": name,
                "description": description,
                "when_to_use": "",
                "source": loader.get_source_label(name),
                "instructions": _skill_prompt_preview(loader, name),
            }
        )
    return skills


def _skill_prompt_preview(loader: SkillLoader, name: str, limit: int = 6000) -> str:
    skill = loader.get(name)
    if skill is None:
        return ""
    return (skill.prompt_body or "")[:limit]


async def maintain_online_skill_candidate(
    *,
    candidate: OnlineSkillCandidate,
    side_query: SideQuery,
    loader: SkillLoader,
    work_dir: str,
    retrieved_reference: dict[str, Any] | None = None,
    confirm_write: ConfirmWrite | None = None,
    target: str = "project",
) -> dict[str, Any]:
    """决策 add / merge / discard 并把结果落盘。返回 {"ok", "action", ...}。"""
    skills = loader.load_all()
    skill_objs = list(skills.values())
    exact_target = _exact_identity_match(candidate, skill_objs)

    # 相似度检索用 BM25（skills/retrieval.py）
    from personalcode.skills.retrieval import retrieve_relevant_skills

    similar_hits = retrieve_relevant_skills(skill_objs, _candidate_search_text(candidate), limit=8, min_score=0.03)
    top_reference_name = str((retrieved_reference or {}).get("name") or "").strip()

    system = (
        "You are PersonalCode's online Skill Set Manager.\n"
        "Decide whether a candidate should add a new skill, merge into an existing skill, or be discarded.\n"
        "Output ONLY strict JSON.\n\n"
        "Schema:\n"
        "{\"action\":\"add|merge|discard\",\"target_skill\":\"existing name for merge\","
        "\"reason\":\"short reason\",\"merged_description\":\"optional\","
        "\"merged_when_to_use\":\"optional\",\"merged_instructions\":\"optional full merged SKILL.md body\"}\n\n"
        "Rules:\n"
        "- Prefer merge over add when the same capability already exists.\n"
        "- Discard if the candidate duplicates an existing skill and adds no durable improvement.\n"
        "- If merging, synthesize a complete merged instruction body, preserving useful existing "
        "guidance and adding only durable new guidance.\n"
        "- Do not preserve one-off payload, secrets, transient project facts, URLs, exact dates, "
        "or assistant-only claims.\n"
    )
    payload = {
        "candidate": asdict(candidate),
        "exact_identity_target": exact_target,
        "retrieved_reference": retrieved_reference or None,
        "similar_skills": similar_hits,
        "existing_skills": _existing_skills_as_dicts(loader),
    }

    decision = _parse_json_object(await side_query(system, json.dumps(payload, ensure_ascii=False)))
    action = str(decision.get("action") or "").strip().lower()
    target_skill = str(decision.get("target_skill") or "").strip()

    if exact_target:
        action = "merge"
        target_skill = exact_target
    elif action == "add" and similar_hits:
        top = similar_hits[0]
        if float(top.get("score", 0.0)) >= 0.55:
            action = "merge"
            target_skill = str(top.get("name") or "")
    elif action == "merge" and not target_skill:
        target_skill = top_reference_name

    if action not in {"add", "merge", "discard"}:
        action = "discard"

    if action == "discard":
        return {"ok": True, "action": "discard", "skill": "", "decision": decision}

    write_summary = f"online skill evolution: {action} {target_skill or candidate.name}"
    if confirm_write is not None and not await confirm_write(write_summary):
        return {
            "ok": False,
            "action": f"{action}_denied",
            "skill": target_skill or candidate.name,
            "error": "permission denied",
            "decision": decision,
        }

    if action == "merge":
        target_skill = target_skill or top_reference_name
        if not target_skill:
            return {"ok": False, "action": "merge", "error": "missing target_skill", "decision": decision}
        result = evolve_skill_file(
            work_dir=work_dir,
            skill_name=target_skill,
            lesson=candidate.evidence or candidate.description,
            rationale=str(decision.get("reason") or "Online maintainer merge"),
            target="active",
            instructions=str(decision.get("merged_instructions") or candidate.instructions),
            description=str(decision.get("merged_description") or ""),
            when_to_use=str(decision.get("merged_when_to_use") or candidate.when_to_use),
            tags=candidate.tags,
            preserve_existing=True,
        )
        return {"action": "merge", "candidate": asdict(candidate), "decision": decision, **result}

    result = create_skill_file(
        work_dir=work_dir,
        name=candidate.name,
        description=candidate.description,
        instructions=candidate.instructions,
        when_to_use=candidate.when_to_use,
        target=target,
        context="inline",
        user_invocable=False,
        evidence=candidate.evidence,
        actor="online",
        tags=candidate.tags,
    )
    return {"action": "add", "candidate": asdict(candidate), "decision": decision, **result}


# ---------------------------------------------------------------------------
# online_ingest
# ---------------------------------------------------------------------------


async def online_ingest(
    *,
    messages: list[dict[str, Any]],
    side_query: SideQuery,
    loader: SkillLoader,
    work_dir: str,
    retrieved_reference: dict[str, Any] | None = None,
    hint: str = "",
    confirm_write: ConfirmWrite | None = None,
    target: str = "project",
) -> dict[str, Any]:
    """统一在线沉淀入口：抽取 -> 决策 -> 落盘 -> 审计。"""
    try:
        candidate = await extract_online_skill_candidate(
            messages=messages,
            side_query=side_query,
            retrieved_reference=retrieved_reference,
            hint=hint,
        )
    except Exception as exc:
        result = {"ok": False, "action": "failed", "error": str(exc)}
        record_online_skill_provenance(
            work_dir=work_dir,
            action="failed",
            result=result,
            messages=messages,
            retrieved_reference=retrieved_reference,
            error=str(exc),
        )
        return result

    if candidate is None:
        result = {"ok": True, "action": "none"}
        record_online_skill_provenance(
            work_dir=work_dir,
            action="none",
            result=result,
            messages=messages,
            retrieved_reference=retrieved_reference,
        )
        return result

    try:
        result = await maintain_online_skill_candidate(
            candidate=candidate,
            side_query=side_query,
            loader=loader,
            work_dir=work_dir,
            retrieved_reference=retrieved_reference,
            confirm_write=confirm_write,
            target=target,
        )
    except Exception as exc:
        result = {"ok": False, "action": "failed", "skill": candidate.name, "error": str(exc)}

    record_online_skill_provenance(
        work_dir=work_dir,
        action=str(result.get("action") or "none"),
        skill_name=str(result.get("skill") or candidate.name),
        result=result,
        messages=messages,
        retrieved_reference=retrieved_reference,
        decision=result.get("decision") if isinstance(result.get("decision"), dict) else None,
        error="" if result.get("ok") else str(result.get("error") or ""),
    )
    return result


# ---------------------------------------------------------------------------
# usage judge
# ---------------------------------------------------------------------------


async def judge_retrieved_skill_usage(
    *,
    hits: list[dict[str, Any]],
    user_message: str,
    assistant_text: str,
    side_query: SideQuery | None = None,
) -> list[dict[str, Any]]:
    """判断检索出的 skills 是否与请求相关、是否被 assistant 回复实际遵循。"""
    if not hits:
        return []
    if side_query is None:
        assistant_lower = assistant_text.lower()
        return [
            {
                "name": hit.get("name", ""),
                "source": hit.get("source", ""),
                "skill_dir": hit.get("skill_dir", ""),
                "retrieved": True,
                "relevant": False,
                "used": str(hit.get("name", "")).lower() in assistant_lower,
                "score": float(hit.get("score", 0.0)),
                "reason": "heuristic fallback",
            }
            for hit in hits
        ]

    system = (
        "Judge whether retrieved skills were relevant to the user request and actually "
        "used in the assistant reply.\n"
        "Output ONLY strict JSON: {\"judgments\":[{\"name\":\"...\",\"relevant\":true|false,"
        "\"used\":true|false,\"reason\":\"short\"}]}.\n"
        "A skill is used only if the reply follows its distinctive workflow or policy, "
        "not merely because it was retrieved."
    )
    payload = {"user_message": user_message, "assistant_reply": assistant_text, "retrieved_skills": hits}
    parsed = _parse_json_object(await side_query(system, json.dumps(payload, ensure_ascii=False)))
    raw_judgments = parsed.get("judgments") if isinstance(parsed.get("judgments"), list) else []
    by_name = {str(item.get("name") or ""): item for item in raw_judgments if isinstance(item, dict)}
    judgments: list[dict[str, Any]] = []
    for hit in hits:
        name = str(hit.get("name") or "")
        raw = by_name.get(name, {})
        judgments.append(
            {
                "name": name,
                "source": hit.get("source", ""),
                "skill_dir": hit.get("skill_dir", ""),
                "retrieved": True,
                "relevant": bool(raw.get("relevant")),
                "used": bool(raw.get("used")),
                "score": float(hit.get("score", 0.0)),
                "reason": str(raw.get("reason") or ""),
            }
        )
    return judgments