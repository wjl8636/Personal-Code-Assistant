"""skill_create 工具：从明确的、可复用的工作流沉淀一个 SKILL.md。

与 skill_evolve 一样是受控的显式路径（category=write → default 模式会 ask），
区别于后台 pending window 的自动沉淀。只沉淀稳定、未来同类任务仍适用的规则，
不沉淀一次性任务内容。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from personalcode.skills.evolution import create_skill_file
from personalcode.skills.parser import VALID_NAME_RE
from personalcode.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from personalcode.skills.loader import SkillLoader


class SkillCreateParams(BaseModel):
    name: str = Field(description="Concise reusable skill name (lowercase letters, digits, hyphens)")
    description: str = Field(description="One-sentence description of what the skill does and when to use it")
    instructions: str = Field(description="Reusable SKILL.md body. Focus on durable method, constraints, and workflow, not one-off task content.")
    when_to_use: str = Field(default="", description="Trigger condition for auto-invocation")
    target: Literal["project", "user"] = Field(default="project", description="Where to create the skill")
    context: Literal["inline", "fork"] = Field(default="inline", description="Skill execution mode")
    user_invocable: bool = Field(default=False, description="Whether users can invoke it manually with /<skill>")
    allowed_tools: str = Field(default="", description="Optional comma-separated allowed tools for fork mode")
    evidence: str = Field(default="", description="Short user-provided evidence showing why this is reusable")


class SkillCreateTool(Tool):
    name = "skill_create"
    description = (
        "Create a new reusable skill from explicit durable workflow guidance when "
        "no suitable existing skill exists."
    )
    params_model = SkillCreateParams
    category = "write"

    def __init__(self) -> None:
        self._loader: SkillLoader | None = None
        self._work_dir: str = ""

    def set_loader(self, loader: SkillLoader) -> None:
        self._loader = loader

    def set_work_dir(self, work_dir: str) -> None:
        self._work_dir = work_dir

    async def execute(self, params: BaseModel) -> ToolResult:
        assert isinstance(params, SkillCreateParams)

        if self._loader is None or not self._work_dir:
            return ToolResult(
                output="Error: SkillCreateTool not properly initialized",
                is_error=True,
            )

        if not VALID_NAME_RE.match(params.name):
            return ToolResult(
                output=(
                    f"Error: invalid skill name {params.name!r}. Use lowercase "
                    "letters, digits, and hyphens, starting with a letter."
                ),
                is_error=True,
            )

        allowed = [p.strip() for p in params.allowed_tools.split(",") if p.strip()] if params.allowed_tools else None

        result = create_skill_file(
            work_dir=self._work_dir,
            name=params.name,
            description=params.description,
            instructions=params.instructions,
            when_to_use=params.when_to_use,
            target=params.target,
            context=params.context,
            user_invocable=params.user_invocable,
            allowed_tools=allowed,
            evidence=params.evidence,
            actor="agent",
        )
        if not result.get("ok"):
            return ToolResult(output=f"Error: {result.get('error')}", is_error=True)

        # 刷新 catalog，让新 skill 立即可用
        try:
            self._loader.reload()
        except Exception:
            pass

        return ToolResult(
            output=(
                f"Created skill {params.name!r} in {result['file']}. "
                f"Now available via /{params.name} and LoadSkill."
            )
        )
