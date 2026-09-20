# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com

import pytest

from mewcode import crashlog
from mewcode.config import ProviderConfig


def test_record_appends_with_timestamp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    crashlog.record("start pid=1")
    try:
        raise RuntimeError("boom")
    except RuntimeError as e:
        crashlog.record_exception("textual", e)

    log = (tmp_path / ".mewcode" / "crash.log").read_text(encoding="utf-8")
    assert "start pid=1" in log
    assert "crash [textual] RuntimeError: boom" in log
    assert "Traceback" in log
    # 追加写：后一条不能把前一条冲掉
    assert log.index("start pid=1") < log.index("crash [textual]")


@pytest.mark.asyncio
async def test_tui_unhandled_exception_is_recorded(tmp_path, monkeypatch):
    """TUI 事件循环里漏出来的异常要落进崩溃日志。

    Textual 拿到未处理异常后只把 traceback 画到终端就结束应用，终端一关现场
    就没了，这里跑的是真实的 App 生命周期，确认异常确实经过了落盘这一步。
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    from mewcode.app import MewCodeApp

    provider = ProviderConfig(
        name="test",
        protocol="anthropic",
        base_url="",
        model="claude-sonnet-5",
        api_key="test-key",
        # 显式给出窗口大小，避免启动时去 provider 拉取
        context_window=200000,
    )
    app = MewCodeApp(providers=[provider])

    def boom() -> None:
        raise RuntimeError("boom from tui")

    with pytest.raises(RuntimeError, match="boom from tui"):
        async with app.run_test() as pilot:
            await pilot.pause()
            app.call_next(boom)
            await pilot.pause()

    log = (tmp_path / ".mewcode" / "crash.log").read_text(encoding="utf-8")
    assert "crash [textual] RuntimeError: boom from tui" in log
