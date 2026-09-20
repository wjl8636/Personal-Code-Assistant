# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com

"""验证 rewind 回滚到文件创建之前的快照时，新建的文件会被删除。"""
from __future__ import annotations

from pathlib import Path

from mewcode.filehistory.history import FileHistory


def test_rewind_deletes_file_created_after_target_snapshot(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    fh = FileHistory(str(tmp_path), "session-1")

    # 第一轮：没有任何文件改动，纯对话，打一个快照。
    fh.make_snapshot(msg_index=0, user_text="第一轮")

    # 第二轮：新建一个文件。track_edit 在写入前调用，此时文件还不存在。
    new_file = project_dir / "new_file.py"
    fh.track_edit(str(new_file))
    new_file.write_text("print('hello')", encoding="utf-8")
    fh.make_snapshot(msg_index=2, user_text="第二轮：新建文件")

    assert new_file.exists()

    # 回滚到第一轮的快照，也就是这个文件创建之前的状态。
    changed = fh.rewind(0)

    assert not new_file.exists(), "回滚到文件创建之前，文件应该被删除"
    assert str(new_file.resolve()) in changed


def test_rewind_restores_edit_on_existing_file(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    fh = FileHistory(str(tmp_path), "session-1")

    existing = project_dir / "existing.py"
    existing.write_text("original", encoding="utf-8")

    fh.track_edit(str(existing))
    fh.make_snapshot(msg_index=0, user_text="第一轮：修改前的快照")

    existing.write_text("modified", encoding="utf-8")
    fh.make_snapshot(msg_index=2, user_text="第二轮：改了内容")

    changed = fh.rewind(0)

    assert existing.read_text(encoding="utf-8") == "original"
    assert str(existing.resolve()) in changed


def test_rewind_to_latest_snapshot_keeps_created_file(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    fh = FileHistory(str(tmp_path), "session-1")

    fh.make_snapshot(msg_index=0, user_text="第一轮")

    new_file = project_dir / "new_file.py"
    fh.track_edit(str(new_file))
    new_file.write_text("print('hello')", encoding="utf-8")
    fh.make_snapshot(msg_index=2, user_text="第二轮：新建文件")

    # 回滚到文件创建之后的这个快照本身，文件应该保留（内容还原成当时写入的内容）。
    changed = fh.rewind(1)

    assert new_file.exists()
    assert new_file.read_text(encoding="utf-8") == "print('hello')"
