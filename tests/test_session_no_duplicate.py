# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com

"""会话记录不能重复写同一条消息。

用户那句话在发送时就落盘一次（中途崩了不能丢），这一轮跑完再刷盘时必须跳过它。
判断依据是消息上的标记，不能记下标：环境上下文和长期记忆都是 insert(0) 插到历史
最前面的，一插索引就整体后移，记下的下标会跟着错位，把已经写过的消息再写一遍。
首轮正好插两次，用户那句话和第一条提醒都会重复。
"""

from __future__ import annotations

from personalcode.conversation import ConversationManager, Message


def flush(conv: ConversationManager, sink: list[Message]) -> None:
    """复刻 app._flush_session 的逻辑：按标记跳过已写的。"""
    for msg in conv.history:
        if msg.persisted:
            continue
        sink.append(msg)
        msg.persisted = True


def test_first_message_written_once_despite_head_insertions():
    """首轮两处 insert(0) 之后，用户那句话不能被写第二遍。"""
    conv = ConversationManager()
    sink: list[Message] = []

    # 用户发消息：进历史，同时立刻落盘一次
    conv.add_user_message("你好 今天的天气怎么样？")
    conv.history[-1].persisted = True
    sink.append(conv.history[-1])

    # 首轮注入 MCP 提醒
    conv.add_system_reminder("# MCP Server Instructions")

    # agent.run() 开头把环境上下文和长期记忆插到最前面，历史索引整体后移两位
    conv.inject_environment("<env>cwd=/tmp</env>")
    conv.inject_long_term_memory("项目规范", "记忆", "")
    assert len(conv.history) == 4
    assert conv.history[2].content.startswith("你好")  # 已经不在下标 0 了

    # 这一轮跑完刷盘
    flush(conv, sink)

    hellos = [m for m in sink if m.content.startswith("你好")]
    assert len(hellos) == 1, f"用户那句话被写了 {len(hellos)} 次：{[m.content[:20] for m in sink]}"

    reminders = [m for m in sink if "MCP Server Instructions" in m.content]
    assert len(reminders) == 1, "MCP 提醒也不该重复"


def test_index_cursor_would_have_duplicated():
    """把旧的下标做法复刻一遍，确认它确实会重复，证明这个用例拦得住。"""
    conv = ConversationManager()
    sink: list[Message] = []

    conv.add_user_message("你好")
    sink.append(conv.history[-1])
    conv.add_system_reminder("MCP")
    cursor = len(conv.history)  # 旧做法：此刻记下下标

    conv.inject_environment("env")
    conv.inject_long_term_memory("指令", "记忆", "")

    # 旧做法按下标切片，插入之后就错位了
    sink.extend(conv.history[cursor:])

    hellos = [m for m in sink if m.content == "你好"]
    assert len(hellos) == 2, "旧做法本该重复，用例前提不成立了"


def test_later_turns_only_append_new_messages():
    """后续轮次没有 insert(0)，也不能把之前的重新写一遍。"""
    conv = ConversationManager()
    sink: list[Message] = []

    conv.add_user_message("第一轮")
    conv.inject_environment("env")
    flush(conv, sink)
    assert len(sink) == 2

    conv.add_assistant_message("回复一")
    conv.add_user_message("第二轮")
    flush(conv, sink)
    assert [m.content for m in sink] == ["env", "第一轮", "回复一", "第二轮"]


def test_persisted_flag_not_serialized():
    """标记不能进落盘内容，也不能影响相等判断。"""
    from personalcode.memory.session import SessionRecord

    m = Message(role="user", content="hi", persisted=True)
    record = SessionRecord.from_message(m)[0]
    assert "persisted" not in record.to_jsonl()
    assert Message(role="user", content="hi") == m
