"""skill_create / skill_evolve / compact_context 三个工具的测试。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalcode.skills.parser import parse_skill_file
from personalcode.tools.skill_create import SkillCreateParams, SkillCreateTool
from personalcode.tools.skill_evolve import SkillEvolveParams, SkillEvolveTool


# ---------------------------------------------------------------------------
# skill_create
# ---------------------------------------------------------------------------

class TestSkillCreateTool:
    def test_requires_init(self) -> None:
        from personalcode.tools.base import ToolResult

        tool = SkillCreateTool()
        result = __import__("asyncio").run(
            tool.execute(SkillCreateParams(name="foo", description="d", instructions="i"))
        )
        assert result.is_error
        assert "not properly initialized" in result.output

    @pytest.mark.asyncio
    async def test_create_success(self, tmp_path: Path) -> None:
        loader = MagicMock()
        tool = SkillCreateTool()
        tool.set_loader(loader)
        tool.set_work_dir(str(tmp_path))

        result = await tool.execute(
            SkillCreateParams(
                name="table-parser",
                description="Parse markdown tables",
                instructions="Split on | and -, strip values.",
            )
        )
        assert not result.is_error
        assert "table-parser" in result.output
        loader.reload.assert_called_once()

        skill_file = tmp_path / ".personalcode" / "skills" / "table-parser" / "SKILL.md"
        assert skill_file.is_file()
        skill = parse_skill_file(skill_file)
        assert skill.name == "table-parser"
        assert skill.mode == "inline"

    @pytest.mark.asyncio
    async def test_invalid_name(self, tmp_path: Path) -> None:
        tool = SkillCreateTool()
        tool.set_loader(MagicMock())
        tool.set_work_dir(str(tmp_path))
        result = await tool.execute(
            SkillCreateParams(name="Bad Name", description="d", instructions="i")
        )
        assert result.is_error
        assert "invalid skill name" in result.output


# ---------------------------------------------------------------------------
# skill_evolve
# ---------------------------------------------------------------------------

class TestSkillEvolveTool:
    @pytest.mark.asyncio
    async def test_evolve_success(self, tmp_path: Path) -> None:
        from personalcode.skills.evolution import create_skill_file

        create_skill_file(work_dir=str(tmp_path), name="style", description="d", instructions="body")

        loader = MagicMock()
        tool = SkillEvolveTool()
        tool.set_loader(loader)
        tool.set_work_dir(str(tmp_path))

        result = await tool.execute(
            SkillEvolveParams(skill_name="style", lesson="Add key data points")
        )
        assert not result.is_error
        assert "0.1.1" in result.output
        loader.reload.assert_called_once()

    @pytest.mark.asyncio
    async def test_evolve_not_found(self, tmp_path: Path) -> None:
        tool = SkillEvolveTool()
        tool.set_loader(MagicMock())
        tool.set_work_dir(str(tmp_path))
        result = await tool.execute(
            SkillEvolveParams(skill_name="missing", lesson="x")
        )
        assert result.is_error
        assert "not found" in result.output

    def test_categories(self) -> None:
        assert SkillCreateTool().category == "write"
        assert SkillEvolveTool().category == "write"


# ---------------------------------------------------------------------------
# compact_context
# ---------------------------------------------------------------------------

class TestCompactContextTool:
    @pytest.mark.asyncio
    async def test_not_initialized(self) -> None:
        from personalcode.tools.compact_context import CompactContextParams, CompactContextTool

        tool = CompactContextTool()
        result = await tool.execute(CompactContextParams(reason="long context"))
        assert result.is_error

    @pytest.mark.asyncio
    async def test_calls_manual_fold(self) -> None:
        from personalcode.tools.compact_context import CompactContextParams, CompactContextTool

        agent = MagicMock()

        async def fake_manual_fold(reason: str):
            return {"ok": True, "message": "structured session memory", "folded_memory_text": ""}

        agent.manual_fold = fake_manual_fold

        tool = CompactContextTool()
        tool.set_agent(agent)
        result = await tool.execute(CompactContextParams(reason="long"))
        assert not result.is_error
        assert "structured session memory" in result.output

    @pytest.mark.asyncio
    async def test_manual_fold_error(self) -> None:
        from personalcode.tools.compact_context import CompactContextParams, CompactContextTool

        agent = MagicMock()

        async def fake_manual_fold(reason: str):
            return {"ok": False, "error": "fold limit reached"}

        agent.manual_fold = fake_manual_fold

        tool = CompactContextTool()
        tool.set_agent(agent)
        result = await tool.execute(CompactContextParams(reason="long"))
        assert result.is_error
        assert "fold limit reached" in result.output

    def test_category_read(self) -> None:
        from personalcode.tools.compact_context import CompactContextTool

        assert CompactContextTool().category == "read"