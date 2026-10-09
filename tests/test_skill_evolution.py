"""Skill 自进化落盘与审计的测试 —— create/evolve/版本快照/审计 jsonl。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalcode.skills.evolution import (
    create_skill_file,
    evolve_skill_file,
    get_evolution_dir,
    load_skill_stats,
    record_online_skill_provenance,
    resolve_skill_file,
)
from personalcode.skills.parser import parse_skill_file


def _skills_root(tmp_path: Path) -> Path:
    return tmp_path / ".personalcode" / "skills"


def _skill_dir(tmp_path: Path, name: str) -> Path:
    return _skills_root(tmp_path) / name


def _skill_file(tmp_path: Path, name: str) -> Path:
    return _skill_dir(tmp_path, name) / "SKILL.md"


# ---------------------------------------------------------------------------
# create_skill_file
# ---------------------------------------------------------------------------

class TestCreateSkillFile:
    def test_create_project_skill(self, tmp_path: Path) -> None:
        result = create_skill_file(
            work_dir=str(tmp_path),
            name="markdown-table-parser",
            description="Parse markdown tables to JSON",
            instructions="Split on | and -, strip values, return list[dict].",
            when_to_use="user asks to parse a markdown table",
        )
        assert result["ok"] is True
        assert result["event"] == "create"
        assert result["skill"] == "markdown-table-parser"

        skill_file = _skill_file(tmp_path, "markdown-table-parser")
        assert skill_file.is_file()

        # 写入的 SKILL.md 必须能被 parse_skill_file 重新解析
        skill = parse_skill_file(skill_file)
        assert skill.name == "markdown-table-parser"
        assert "list[dict]" in skill.prompt_body
        assert skill.mode == "inline"

        # usage.jsonl 有 create 事件
        usage = (get_evolution_dir(str(tmp_path)) / "usage.jsonl")
        assert usage.is_file()
        rows = [json.loads(line) for line in usage.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert any(r["event"] == "create" and r["skill"] == "markdown-table-parser" for r in rows)

    def test_create_duplicate_rejected(self, tmp_path: Path) -> None:
        create_skill_file(
            work_dir=str(tmp_path),
            name="dup-skill",
            description="First",
            instructions="Do x",
        )
        result = create_skill_file(
            work_dir=str(tmp_path),
            name="dup-skill",
            description="Second",
            instructions="Do y",
        )
        assert result["ok"] is False
        assert "already exists" in result["error"]

    def test_create_invalid_name(self, tmp_path: Path) -> None:
        result = create_skill_file(
            work_dir=str(tmp_path),
            name="Bad Name!",
            description="x",
            instructions="y",
        )
        assert result["ok"] is False
        assert "invalid skill name" in result["error"]

    def test_create_missing_fields(self, tmp_path: Path) -> None:
        assert create_skill_file(work_dir=str(tmp_path), name="", description="x", instructions="y")["ok"] is False
        assert create_skill_file(work_dir=str(tmp_path), name="foo", description="", instructions="y")["ok"] is False

    def test_create_user_skill(self, tmp_path: Path, monkeypatch) -> None:
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        monkeypatch.setattr("personalcode.skills.evolution.Path.home", lambda: home)
        result = create_skill_file(
            work_dir=str(tmp_path),
            name="user-skill",
            description="User-level",
            instructions="z",
            target="user",
        )
        assert result["ok"] is True
        assert (home / ".personalcode" / "skills" / "user-skill" / "SKILL.md").is_file()


# ---------------------------------------------------------------------------
# evolve_skill_file
# ---------------------------------------------------------------------------

class TestEvolveSkillFile:
    def _setup(self, tmp_path: Path) -> None:
        create_skill_file(
            work_dir=str(tmp_path),
            name="report-style",
            description="Report writing style",
            instructions="Lead with conclusion, then expand.",
            when_to_use="user asks to write a report",
        )

    def test_evolve_appends_note_and_bumps_version(self, tmp_path: Path) -> None:
        self._setup(tmp_path)
        result = evolve_skill_file(
            work_dir=str(tmp_path),
            skill_name="report-style",
            lesson="Always include key data points, not just concepts.",
            rationale="User prefers quantified reporting.",
        )
        assert result["ok"] is True
        assert result["event"] == "evolve"
        assert result["version"] == "0.1.1"

        skill_file = _skill_file(tmp_path, "report-style")
        skill = parse_skill_file(skill_file)
        assert "## Evolution Notes" in skill.prompt_body
        assert "key data points" in skill.prompt_body
        # version bump 写进 frontmatter
        assert "version: 0.1.1" in skill_file.read_text(encoding="utf-8")

        # history 快照记录了 evolve 前的完整内容
        history = get_evolution_dir(str(tmp_path)) / "history" / "report-style.jsonl"
        assert history.is_file()
        snap = json.loads(history.read_text(encoding="utf-8").splitlines()[0])
        assert snap["event"] == "snapshot"
        assert snap["version"] == "0.1.0"

    def test_evolve_not_found(self, tmp_path: Path) -> None:
        result = evolve_skill_file(
            work_dir=str(tmp_path),
            skill_name="missing",
            lesson="x",
        )
        assert result["ok"] is False
        assert "not found" in result["error"]

    def test_evolve_with_instructions_merges(self, tmp_path: Path) -> None:
        self._setup(tmp_path)
        result = evolve_skill_file(
            work_dir=str(tmp_path),
            skill_name="report-style",
            lesson="New guidance",
            instructions="Structure: title, context, findings, next steps.",
            preserve_existing=True,
        )
        assert result["ok"] is True
        skill = parse_skill_file(_skill_file(tmp_path, "report-style"))
        assert "Structure: title, context" in skill.prompt_body
        assert "Lead with conclusion" in skill.prompt_body  # 原正文保留
        assert "## Additional guidance from merge" in skill.prompt_body

    def test_evolve_legacy_context_inline_file(self, tmp_path: Path) -> None:
        """兼容旧的 context: inline 格式：evolve 写回后仍能被 parse_skill_file 解析。"""
        skills = _skills_root(tmp_path)
        skill_dir = skills / "legacy-skill"
        skill_dir.mkdir(parents=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(
            "---\n"
            "name: legacy-skill\n"
            "description: Legacy skill\n"
            "context: inline\n"
            "---\n"
            "Legacy body\n",
            encoding="utf-8",
        )
        result = evolve_skill_file(
            work_dir=str(tmp_path),
            skill_name="legacy-skill",
            lesson="Added lesson",
        )
        assert result["ok"] is True
        # 写回后必须可解析，且 mode 被规范化成 inline
        skill = parse_skill_file(skill_file)
        assert skill.mode == "inline"
        assert skill.context == "full"
        assert "Added lesson" in skill.prompt_body


# ---------------------------------------------------------------------------
# resolve_skill_file 与 provenance
# ---------------------------------------------------------------------------

class TestResolveAndProvenance:
    def test_resolve_active_prefers_project(self, tmp_path: Path) -> None:
        create_skill_file(work_dir=str(tmp_path), name="foo", description="d", instructions="i")
        path = resolve_skill_file(str(tmp_path), "foo", target="active")
        assert path is not None
        assert path.name == "SKILL.md"

    def test_provenance_records(self, tmp_path: Path) -> None:
        record_online_skill_provenance(
            work_dir=str(tmp_path),
            action="add",
            skill_name="foo",
            result={"ok": True, "version": "0.1.0"},
            messages=[{"role": "user", "content": "do x"}, {"role": "assistant", "content": "ok"}],
        )
        log = get_evolution_dir(str(tmp_path)) / "online_provenance.jsonl"
        assert log.is_file()
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(rows) == 1
        assert rows[0]["action"] == "add"
        assert rows[0]["skill"] == "foo"
        assert rows[0]["ok"] is True

        # 索引聚合
        index = get_evolution_dir(str(tmp_path)) / "online_skill_provenance.json"
        data = json.loads(index.read_text(encoding="utf-8"))
        assert data["foo"]["last_action"] == "add"
        assert data["foo"]["current_version"] == "0.1.0"

    def test_stats(self, tmp_path: Path) -> None:
        self._create_and_evolve(tmp_path)
        stats = load_skill_stats(str(tmp_path))
        assert "report-style" in stats
        assert stats["report-style"]["created"] == 1
        assert stats["report-style"]["evolutions"] == 1
        assert stats["report-style"]["snapshots"] == 1

    def _create_and_evolve(self, tmp_path: Path) -> None:
        create_skill_file(work_dir=str(tmp_path), name="report-style", description="d", instructions="i")
        evolve_skill_file(work_dir=str(tmp_path), skill_name="report-style", lesson="l")
