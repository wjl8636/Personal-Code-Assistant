"""Skill 检索与在线自进化的测试 —— BM25 检索、Extractor、Maintainer、online_ingest。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalcode.skills.parser import SkillDef
from personalcode.skills.retrieval import (
    format_retrieved_skill_context,
    retrieve_relevant_skills,
)


def _skill(name: str, desc: str, body: str = "") -> SkillDef:
    return SkillDef(name=name, description=desc, prompt_body=body)


SKILLS = [
    _skill("markdown-table-parser", "Parse markdown tables to JSON", "split on | and -"),
    _skill("commit-message", "Write conventional commit messages", "type(scope): subject"),
    _skill("code-review", "Review code changes", "check for null pointers"),
]


# ---------------------------------------------------------------------------
# BM25 检索
# ---------------------------------------------------------------------------

class TestRetrieveRelevantSkills:
    def test_returns_matching(self) -> None:
        hits = retrieve_relevant_skills(SKILLS, "parse a markdown table", limit=3)
        assert hits
        assert hits[0]["name"] == "markdown-table-parser"
        assert hits[0]["score"] > 0

    def test_respects_limit(self) -> None:
        hits = retrieve_relevant_skills(SKILLS, "markdown table commit code review", limit=2)
        assert len(hits) <= 2

    def test_no_match_returns_empty(self) -> None:
        hits = retrieve_relevant_skills(SKILLS, "quantum physics homework", limit=3)
        assert hits == []

    def test_empty_query(self) -> None:
        assert retrieve_relevant_skills(SKILLS, "") == []

    def test_empty_skills(self) -> None:
        assert retrieve_relevant_skills([], "anything") == []


class TestFormatRetrievedSkillContext:
    def test_returns_context_and_ref(self) -> None:
        context, ref = format_retrieved_skill_context(SKILLS, "parse markdown table")
        assert context is not None and "<retrieved_skills>" in context
        assert ref is not None and ref["name"] == "markdown-table-parser"

    def test_no_match_returns_empty(self) -> None:
        context, ref = format_retrieved_skill_context(SKILLS, "astrophysics puzzle")
        assert context == ""
        assert ref is None


# ---------------------------------------------------------------------------
# online_ingest 各分支（mock side_query）
# ---------------------------------------------------------------------------

async def _side_query(system: str, user: str) -> str:
    # 默认返回空 skills（extract 阶段）
    if "Skill Extractor" in system:
        return '{"skills": []}'
    # maintainer 阶段
    if "Skill Set Manager" in system:
        return '{"action": "discard", "reason": "low value"}'
    return "{}"


class TestOnlineIngest:
    @pytest.mark.asyncio
    async def test_extract_none(self, tmp_path: Path) -> None:
        from personalcode.skills.evolution import get_evolution_dir
        from personalcode.skills.loader import SkillLoader
        from personalcode.skills.online_evolution import online_ingest

        loader = SkillLoader(str(tmp_path))
        result = await online_ingest(
            messages=[{"role": "user", "content": "do x"}, {"role": "assistant", "content": "ok"}],
            side_query=_side_query,
            loader=loader,
            work_dir=str(tmp_path),
        )
        assert result["ok"] is True
        assert result["action"] == "none"

        # 审计仍落盘
        log = get_evolution_dir(str(tmp_path)) / "online_provenance.jsonl"
        assert log.is_file()
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert rows[0]["action"] == "none"

    @pytest.mark.asyncio
    async def test_extract_adds_skill(self, tmp_path: Path) -> None:
        from personalcode.skills.evolution import get_evolution_dir
        from personalcode.skills.loader import SkillLoader
        from personalcode.skills.online_evolution import online_ingest

        async def extractor(system: str, user: str) -> str:
            if "Skill Extractor" in system:
                return json.dumps({
                    "skills": [{
                        "name": "table-parser",
                        "description": "Parse markdown tables",
                        "instructions": "split on | and -",
                        "evidence": "user corrected the parser",
                    }]
                })
            return '{"action": "add", "reason": "new capability"}'

        loader = SkillLoader(str(tmp_path))
        result = await online_ingest(
            messages=[{"role": "user", "content": "use this approach"}, {"role": "assistant", "content": "done"}],
            side_query=extractor,
            loader=loader,
            work_dir=str(tmp_path),
        )
        assert result["ok"] is True
        assert result["action"] == "add"
        assert (tmp_path / ".personalcode" / "skills" / "table-parser" / "SKILL.md").is_file()

    @pytest.mark.asyncio
    async def test_extract_merges_existing(self, tmp_path: Path) -> None:
        from personalcode.skills.evolution import create_skill_file
        from personalcode.skills.loader import SkillLoader
        from personalcode.skills.online_evolution import online_ingest

        # 先创建已有 skill
        create_skill_file(
            work_dir=str(tmp_path),
            name="table-parser",
            description="Parse markdown tables to JSON",
            instructions="old guidance",
        )

        async def merger(system: str, user: str) -> str:
            if "Skill Extractor" in system:
                return json.dumps({
                    "skills": [{
                        "name": "table-parser",
                        "description": "Parse markdown tables to JSON",
                        "instructions": "add strip() and headers",
                        "evidence": "new correction",
                    }]
                })
            return '{"action": "merge", "target_skill": "table-parser"}'

        loader = SkillLoader(str(tmp_path))
        result = await online_ingest(
            messages=[{"role": "user", "content": "add more rules"}],
            side_query=merger,
            loader=loader,
            work_dir=str(tmp_path),
        )
        assert result["ok"] is True
        assert result["action"] == "merge"
        # 版本已 bump
        assert result.get("version") == "0.1.1"

    @pytest.mark.asyncio
    async def test_confirm_write_denied(self, tmp_path: Path) -> None:
        from personalcode.skills.loader import SkillLoader
        from personalcode.skills.online_evolution import online_ingest

        async def extractor(system: str, user: str) -> str:
            if "Skill Extractor" in system:
                return json.dumps({
                    "skills": [{"name": "s1", "description": "d", "instructions": "i"}]
                })
            return '{"action": "add"}'

        async def deny(_: str) -> bool:
            return False

        loader = SkillLoader(str(tmp_path))
        result = await online_ingest(
            messages=[{"role": "user", "content": "x"}],
            side_query=extractor,
            loader=loader,
            work_dir=str(tmp_path),
            confirm_write=deny,
        )
        assert result["ok"] is False
        assert result["action"] == "add_denied"


class TestExactIdentityMatch:
    @pytest.mark.asyncio
    async def test_identity_forced_merge(self, tmp_path: Path) -> None:
        """name/description 完全一致时强制 merge，即使 LLM 判 add。"""
        from personalcode.skills.evolution import create_skill_file
        from personalcode.skills.loader import SkillLoader
        from personalcode.skills.online_evolution import maintain_online_skill_candidate, OnlineSkillCandidate

        create_skill_file(work_dir=str(tmp_path), name="foo", description="Durable rule", instructions="old")

        async def side(system: str, user: str) -> str:
            return '{"action": "add", "reason": "new"}'

        loader = SkillLoader(str(tmp_path))
        candidate = OnlineSkillCandidate(name="foo", description="Durable rule", instructions="new")
        result = await maintain_online_skill_candidate(
            candidate=candidate,
            side_query=side,
            loader=loader,
            work_dir=str(tmp_path),
        )
        assert result["action"] == "merge"
