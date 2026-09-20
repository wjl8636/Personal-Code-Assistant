# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from personalcode.teams import protocol
from personalcode.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from personalcode.teams.manager import TeamManager

log = logging.getLogger(__name__)


class SendMessageParams(BaseModel):
    to: str
    content: str
    type: str = "text"
    # 结构化消息用：request_id 让应答对上请求，approve 是表态
    request_id: str = ""
    approve: bool | None = None


VALID_MESSAGE_TYPES = protocol.VALID_MESSAGE_TYPES


class SendMessageTool(Tool):
    name = "SendMessage"
    description = (
        "Send a message to a teammate by name or agent ID. "
        "Use to='*' to broadcast to all teammates. "
        "Set type='shutdown_request' to ask a teammate to wrap up; it replies with "
        "shutdown_response. Set type='plan_approval_response' together with request_id "
        "and approve to answer a teammate's plan; when rejecting, put your feedback in content."
    )
    params_model = SendMessageParams
    category = "command"


    def __init__(
        self,
        team_manager: TeamManager,
        team_name: str = "",
        from_agent_id: str = "",
        from_agent_name: str = "lead",
    ) -> None:
        self._team_manager = team_manager
        self._team_name = team_name
        self._from_agent_id = from_agent_id
        self._from_agent_name = from_agent_name

    def _resolve_sender(self) -> tuple[str, str]:
        """定位发信方所属的团队和它在邮箱里的键。

        队员在构造时就知道自己属于哪个团队，直接用传进来的值。Lead 不一样：
        它的工具在启动时就注册好了，那时候团队还没建，而且 Lead 不登记在花名册里，
        所以留空由这里在运行时取当前团队，发信方的键取该团队记录的 lead_agent_id。
        """
        if self._team_name:
            return self._team_name, self._from_agent_id
        names = self._team_manager.list_teams()
        if not names:
            return "", ""
        team_name = names[0]
        team = self._team_manager.get_team(team_name)
        return team_name, (team.lead_agent_id if team else "")


    async def execute(self, params: BaseModel) -> ToolResult:
        p: SendMessageParams = params  # type: ignore[assignment]

        if p.type not in VALID_MESSAGE_TYPES:
            return ToolResult(
                output=f"Invalid type '{p.type}'. Must be one of: {', '.join(sorted(VALID_MESSAGE_TYPES))}",
                is_error=True,
            )

        from personalcode.teams.mailbox import create_message
        from personalcode.teams.registry import AgentNameRegistry

        team_name, from_agent_id = self._resolve_sender()
        if not team_name:
            return ToolResult(output="No team is active", is_error=True)

        team = self._team_manager.get_team(team_name)
        if team is None:
            return ToolResult(output=f"Team '{team_name}' not found", is_error=True)

        mailbox = self._team_manager.get_mailbox(team_name)
        if mailbox is None:
            return ToolResult(output=f"Mailbox not found for team '{team_name}'", is_error=True)

        sender = self._from_agent_name or from_agent_id

        # 结构化消息要带 request_id 和表态，拼进正文的话收件方还得从自然语言里猜，
        # 那就退回到「靠理解措辞来协调」了。
        request_id = ""
        if p.type != protocol.TEXT:
            if p.type == protocol.PLAN_APPROVAL_RESPONSE and (
                not p.request_id or p.approve is None
            ):
                return ToolResult(
                    output="plan_approval_response requires both 'request_id' and 'approve'.",
                    is_error=True,
                )
            if p.type == protocol.SHUTDOWN_RESPONSE and p.approve is None:
                return ToolResult(
                    output="shutdown_response requires 'approve'.", is_error=True
                )
            request_id = p.request_id or protocol.new_request_id()

        content = p.content
        if p.type == protocol.SHUTDOWN_REQUEST:
            # 带上文本前缀，旧版本拉起的窗格队友也能认出来
            content = f"{protocol.SHUTDOWN_PREFIX} {p.content}"

        msg = create_message(
            from_agent=sender,
            text=content,
            message_type=p.type,
            request_id=request_id,
            approve=p.approve,
        )

        registry = AgentNameRegistry.instance()

        if p.to == "*":
            member_ids = [
                m.agent_id for m in team.members
                if m.agent_id != from_agent_id
            ]
            if team.lead_agent_id != from_agent_id:
                member_ids.append(team.lead_agent_id)
            mailbox.broadcast(member_ids, msg, exclude=from_agent_id)
            self._wake_pane_members(team, member_ids)
            return ToolResult(output=f"Message broadcast to {len(member_ids)} teammates.")

        target_id = registry.resolve(p.to)
        if target_id is None:
            return ToolResult(
                output=f"Cannot resolve recipient '{p.to}'. Check the name or agent ID.",
                is_error=True,
            )

        mailbox.write(target_id, msg)
        self._wake_pane(target_id)

        return ToolResult(output=f"Message sent to '{p.to}'.")


    def _wake_pane(self, agent_id: str) -> None:
        pane_id = self._team_manager.get_pane_id(agent_id)
        if pane_id is None:
            return
        try:
            from personalcode.teams.spawn_tmux import send_keys_to_pane
            send_keys_to_pane(pane_id, "")
        except Exception:
            pass

    def _wake_pane_members(self, team: Any, agent_ids: list[str]) -> None:
        for aid in agent_ids:
            self._wake_pane(aid)
