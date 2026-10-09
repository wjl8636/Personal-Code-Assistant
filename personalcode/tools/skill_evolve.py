"""skill_evolve 工具：把明确的、可复用的用户纠正或工作流偏好沉淀进已有 skill。

写入前自动快照并 bump patch 版本（见 skills/evolution.py::evolve_skill_file），
保证每次演化可回滚、可追踪。与 skill_create 一样是受控的显式路径。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from personalcode.skills.evolution import evolve_skill_file
from personalcode.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from personalcode.skills.loader import SkillLoader


class SkillEvolveParams(BaseModel):
    skill_name: str = Field(description="The registered skill name to evolve")
    lesson: str = Field(description="Durable reusable rule to add to the skill")
    rationale: str = Field(default="", description="Why this lesson should affect future similar tasks")
    target: Literal["active", "project", "user"] = Field(default="active", description="Which skill file to update")
    instructions: str = Field(default="", description="Optional full replacement instructions")
    description: str = Field(default="", description="Optional updated description")
    when_to_use: str = Field(default="", description="Optional updated trigger condition")


class SkillEvolveTool(Tool):
    name = "skill_evolve"
    description = (
        "Persist an explicit reusable user correction or workflow preference into "
        "an existing skill. Creates a version snapshot before editing."
    )
    params_model = SkillEvolveParams
    category = "write"

    def __init__(self) -> None:
        self._loader: SkillLoader | None = None
        self._work_dir: str = ""

    def set_loader(self, loader: SkillLoader) -> None:
        self._loader = loader

    def set_work_dir(self, work_dir: str) -> None:
        self._work_dir = work_dir

    async def execute(self, params: BaseModel) -> ToolResult:
        assert isinstance(params, SkillEvolveParams)

        if self._loader is None or not self._work_dir:
            return ToolResult(
                output="Error: SkillEvolveTool not properly initialized",
                is_error=True,
            )

        result = evolve_skill_file(
            work_dir=self._work_dir,
            skill_name=params.skill_name,
            lesson=params.lesson,
            rationale=params.rationale,
            target=params.target,
            instructions=params.instructions,
            description=params.description,
            when_to_use=params.when_to_use,
            preserve_existing=True,
        )
        if not result.get("ok"):
            return ToolResult(output=f"Error: {result.get('error')}", is_error=True)

        # 刷新 catalog
        try:
            self._loader.reload()
        except Exception:
            pass

        return ToolResult(
            output=(
                f"Evolved skill {params.skill_name!r} to version "
                f"{result['version']} ({result['file']})."
            )
        )
