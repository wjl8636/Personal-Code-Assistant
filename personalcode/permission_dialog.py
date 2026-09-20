# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
from __future__ import annotations

from rich.markup import escape
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static

from personalcode.agent import PermissionReply, PermissionResponse


_PERM_OPTIONS = [
    ("Yes", PermissionResponse.ALLOW),
    ("Yes, and don't ask again for this pattern", PermissionResponse.ALLOW_ALWAYS),
    ("No", PermissionResponse.DENY),
]

# 「No」选项的位置，光标停在这里时键入的字符会进入反馈输入框
_DENY_IDX = len(_PERM_OPTIONS) - 1


class InlinePermissionWidget(Vertical, can_focus=True):
    """渲染在聊天区域内部的内联权限确认提示。

    工具名 + 描述 + 带编号的
    选项，支持方向键导航 + 回车确认。光标在「No」上时可以直接输入，
    告诉模型该怎么换个做法。
    """

    BINDINGS = [
        Binding("up", "cursor_up", "Up", priority=True),
        Binding("down", "cursor_down", "Down", priority=True),
        Binding("enter", "select", "Select", priority=True),
        Binding("escape", "deny", "Deny", priority=True),
    ]

    class Responded(Message):


        def __init__(self, reply: PermissionReply) -> None:
            super().__init__()
            self.reply = reply

    def __init__(self, tool_name: str, description: str, **kwargs) -> None:
        super().__init__(id="perm-inline", **kwargs)
        self._tool_name = tool_name
        self._description = description
        self._cursor = 0
        self._feedback = ""

    def compose(self) -> ComposeResult:
        yield Static(self._build_content(), id="perm-content")


    def on_mount(self) -> None:
        self.focus()

    def _build_content(self) -> str:
        lines = []
        lines.append(f"\n  [bold yellow]{self._tool_name} command[/bold yellow]\n")
        lines.append(f"    {self._description}\n")
        lines.append("  [dim]This command requires approval[/dim]\n")
        lines.append("  Do you want to proceed?\n")

        for i, (label, _resp) in enumerate(_PERM_OPTIONS):
            if i == self._cursor:
                line = f" [bold cyan]❯[/bold cyan] {i + 1}. [bold]{label}[/bold]"
                # 「No」选中时后面跟一个输入框，用户输入的内容要转义，避免被当成 markup
                if i == _DENY_IDX:
                    if self._feedback:
                        line += f"[bold], {escape(self._feedback)}█[/bold]"
                    else:
                        line += "[bold],[/bold] [dim]and tell PersonalCode what to do differently█[/dim]"
                lines.append(line)
            else:
                lines.append(f"   {i + 1}. [dim]{label}[/dim]")

        lines.append("  [dim]Esc to cancel[/dim]")
        return "\n".join(lines)


    def _refresh(self) -> None:
        content = self.query_one("#perm-content", Static)
        content.update(self._build_content())

    def action_cursor_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._refresh()

    def action_cursor_down(self) -> None:
        if self._cursor < len(_PERM_OPTIONS) - 1:
            self._cursor += 1
            self._refresh()

    def action_select(self) -> None:
        _, response = _PERM_OPTIONS[self._cursor]
        feedback = self._feedback if self._cursor == _DENY_IDX else ""
        self.post_message(self.Responded(PermissionReply(response, feedback)))


    def action_deny(self) -> None:
        self.post_message(self.Responded(PermissionReply(PermissionResponse.DENY)))

    def on_key(self, event: events.Key) -> None:
        # 只有光标在「No」上时才收集输入，其余选项上的字符键忽略
        if self._cursor != _DENY_IDX:
            return
        if event.key == "backspace":
            if self._feedback:
                self._feedback = self._feedback[:-1]
                self._refresh()
            event.stop()
            return
        if event.is_printable and event.character:
            self._feedback += event.character
            self._refresh()
            event.stop()

    def on_paste(self, event: events.Paste) -> None:
        if self._cursor != _DENY_IDX:
            return
        # 粘贴的多行文字压成一行，输入框只显示单行
        self._feedback += " ".join(event.text.splitlines())
        self._refresh()
        event.stop()
