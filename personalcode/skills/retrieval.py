"""Skill 轻量检索：BM25 相关性排序 + <retrieved_skills> 上下文注入。

参照 BearCode skills.py 的检索理念：skill 的 name/description/when-to-use 属于
「元数据」，触发词匹配价值高于正文，因此加权；正文也参与匹配增加召回。中英文
都做基础 token 化。返回 top-k 命中，供消费方决定是否注入对话。

与 SkillLoader 的关系：本模块是纯函数，skills 列表由调用方传入（保持 personalcode
的注入式风格）；目前 personalcode 用 set_skill_catalog 给模型全量清单，<retrieved_skills>
检索注入是可选的增强。online_evolution 的 Maintainer 判相似度也复用本模块的 BM25。
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from personalcode.skills.parser import SkillDef

# 中英文 token 化：ASCII 单词 + 中文 2-gram
_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+|[一-鿿]{1,2}")
_STOP_TOKENS = {
    "请帮",
    "帮我",
    "我做",
    "做一",
    "一次",
    "一下",
    "这个",
    "那个",
    "一个",
    "用户",
    "问题",
    "回答",
    "生成",
    "使用",
    "需要",
}


def _token_list(text: str) -> list[str]:
    raw = str(text or "").lower().replace("_", " ").replace("-", " ")
    tokens = [m.group(0) for m in _TOKEN_RE.finditer(raw)]
    for chunk in re.findall(r"[一-鿿]+", raw):
        if len(chunk) >= 2:
            tokens.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
    expanded: list[str] = []
    for token in tokens:
        if not token.strip() or token in _STOP_TOKENS:
            continue
        expanded.append(token)
        if len(token) > 3 and token.endswith("s"):
            expanded.append(token[:-1])
    return expanded


def _skill_search_text(skill: SkillDef) -> str:
    return "\n".join(
        [
            skill.name,
            skill.description,
            skill.prompt_body[:2500],
        ]
    )


def retrieve_relevant_skills(
    skills: list[SkillDef],
    query: str,
    *,
    limit: int = 3,
    min_score: float = 0.08,
    k1: float = 1.4,
    b: float = 0.75,
) -> list[dict[str, Any]]:
    """在当前 skills 列表里做 BM25 检索，返回打分排序的命中（含 skills 元数据）。"""
    query_terms = _token_list(query)
    query_tokens = set(query_terms)
    if not query_tokens:
        return []

    docs: list[tuple[SkillDef, list[str]]] = []
    document_frequency: Counter[str] = Counter()
    for skill in skills:
        meta_terms = _token_list("\n".join([skill.name, skill.description]))
        body_terms = _token_list(skill.prompt_body[:2500])
        terms = (meta_terms * 3) + body_terms  # 元数据权重 3x
        if not terms:
            continue
        docs.append((skill, terms))
        document_frequency.update(set(terms))
    if not docs:
        return []

    # when_to_use 只在可访问时参与索引；SkillDef 无该字段，正文已覆盖触发条件。
    avg_doc_len = sum(len(terms) for _, terms in docs) / max(1, len(docs))
    doc_count = len(docs)
    hits: list[dict[str, Any]] = []
    for skill, terms in docs:
        term_counts = Counter(terms)
        overlap = query_tokens & set(term_counts)
        if not overlap:
            continue
        raw_score = 0.0
        doc_len = max(1, len(terms))
        for token in overlap:
            tf = term_counts[token]
            idf = math.log(1 + (doc_count - document_frequency[token] + 0.5) / (document_frequency[token] + 0.5))
            denom = tf + k1 * (1 - b + b * doc_len / max(1.0, avg_doc_len))
            raw_score += idf * (tf * (k1 + 1)) / max(denom, 0.0001)
        name_bonus = 0.15 if skill.name.lower() in str(query or "").lower() else 0.0
        score = min(1.0, (raw_score / max(3.0, len(query_tokens))) + name_bonus)
        if score < float(min_score):
            continue
        hits.append(
            {
                "score": float(score),
                "name": skill.name,
                "description": skill.description,
                "source": "project" if skill.source_path else "unknown",
                "context": skill.context,
                "skill_dir": str(skill.source_path) if skill.source_path else "",
            }
        )

    hits.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
    return hits[: max(1, int(limit or 1))]


def format_retrieved_skill_context(skills: list[SkillDef], query: str, *, limit: int = 3) -> tuple[str, dict[str, Any] | None]:
    """渲染 <retrieved_skills> 块，返回 (context, top_ref)。

    top_ref 是「最相关那条 skill」的引用，供 online_evolution 的 Maintainer
    做 merge/discard 判断；消费方拿到它后可作为 retrieved_reference 传入。
    """
    hits = retrieve_relevant_skills(skills, query, limit=limit)
    if not hits:
        return "", None
    lines = [
        "<retrieved_skills>",
        "These skills were retrieved for the current user request. Use a skill only if it directly matches the user's intent; otherwise ignore this block.",
    ]
    for idx, hit in enumerate(hits, start=1):
        lines.append(
            f"{idx}. {hit['name']} (score={float(hit['score']):.3f}): {hit['description']}"
        )
    lines.append("</retrieved_skills>")
    top = dict(hits[0])
    top["all_hits"] = hits
    return "\n".join(lines), top