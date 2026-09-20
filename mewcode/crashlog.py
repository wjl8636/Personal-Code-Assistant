# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
from __future__ import annotations

import atexit
import faulthandler
import os
import traceback
from datetime import datetime
from pathlib import Path
from typing import IO

CRASH_LOG_PATH = Path(".mewcode") / "crash.log"

# faulthandler 要求文件句柄在进程存活期间一直有效，用模块级变量持有，避免被回收
_fault_file: IO[str] | None = None


def record(text: str) -> None:
    """往崩溃日志追加一行带时间戳的记录。

    诊断本身不能反过来把进程搞挂，所以写失败一律静默跳过。
    """
    try:
        CRASH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CRASH_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {text}\n")
    except OSError:
        pass


def record_exception(context: str, error: BaseException) -> None:
    """记录一次异常，带完整调用栈。context 用来区分现场来自哪一层。"""
    stack = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )
    record(f"crash [{context}] {type(error).__name__}: {error}\n{stack}")


def install() -> None:
    """安装崩溃诊断，进程启动时调用一次。

    留下三类痕迹：start 行标记本次运行开始；exit 行由 atexit 在进程自行退出时
    写出；faulthandler 负责解释器级的硬崩溃（段错误、栈溢出），这类崩溃来不及
    走 Python 的异常机制。三者组合起来即可判定退出方式：start 后有 crash 有
    exit 是崩溃退出，只有 start 和 exit 是正常退出，只有 start 说明进程是被
    外部结束的，日志里不会留下任何自身痕迹。
    """
    global _fault_file
    record(f"start pid={os.getpid()}")
    try:
        _fault_file = open(CRASH_LOG_PATH, "a", encoding="utf-8")
        faulthandler.enable(file=_fault_file)
    except OSError:
        _fault_file = None
    atexit.register(record, f"exit pid={os.getpid()}")
