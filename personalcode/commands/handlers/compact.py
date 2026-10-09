from __future__ import annotations

from personalcode.commands.registry import Command, CommandContext, CommandType


async def handle_compact(ctx: CommandContext) -> None:
    if ctx.agent is None:
        ctx.ui.add_system_message("Agent 未初始化")
        return


    input_tokens, _ = ctx.ui.get_token_count()
    if input_tokens < 5000:
        ctx.ui.add_system_message(f"当前 token 数 {input_tokens:,}，无需压缩")
        return

    from personalcode.agent import CompactNotification, ErrorEvent


    result = await ctx.agent.manual_compact(ctx.conversation)
    if isinstance(result, CompactNotification):
        # 持久化压缩/折叠记录，使后续 resume 可重建压缩后的状态。
        # manual_compact 已重写了 ctx.conversation；下一次 _send_message
        # 会重新捕获 history_cursor，所以这里无需手动重置。
        if ctx.session is not None and result.boundary is not None:
            if result.boundary.fold and result.boundary.memory is not None:
                from personalcode.memory.session import make_folded_memory_record

                ctx.session.append_record(
                    make_folded_memory_record(
                        result.boundary.memory,
                        trigger="manual",
                        pre_fold_tokens=result.before_tokens,
                    )
                )
            else:
                from personalcode.memory.session import make_compact_boundary

                ctx.session.append_record(
                    make_compact_boundary(result.boundary.summary, result.boundary.keep)
                )
        ctx.ui.add_system_message(result.message)
    elif isinstance(result, ErrorEvent):
        ctx.ui.add_system_message(f"压缩失败: {result.message}")


COMPACT_COMMAND = Command(
    name="compact",
    aliases=["c"],
    description="压缩上下文",
    usage="/compact [保留重点]",
    type=CommandType.LOCAL,
    handler=handle_compact,
)

