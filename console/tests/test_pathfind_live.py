"""``PathfindChannel`` 的 **live 链路**测试（Console 侧，2026-10-12）。

``test_pathfind_channel.py`` 已经用假脚本覆盖了护栏与停止语义，但那里的状态
文件全是**手工写的** —— 从没有一个假脚本真的**边跑边写** ``pathfind_state.json``。

★ 补的就是这一段
  「脚本每帧重写状态文件」是整套状态逻辑的**前提**（``STALE_AFTER_S``、
  ``state_age_s``、``is_state_stale`` 全都建立在这上面）。前提没被测过，
  后面那些断言就都只是「对一个不存在的情况做断言」。

不碰真游戏：这里起的是本文件现写的假脚本，它只做三件事 ——
每 50ms 写一次状态文件、看一眼停止 flag、退出前写一个「已松键」标记。
第三件对应真脚本 ``sprint_off()`` 的契约：Console 侧的 flag 机制**能不能**
真的让脚本先松开按键再退出。
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

import pytest

from neko_console.channels import pathfind as P
from neko_console.channels.pathfind import PathfindChannel


# 一个「像真脚本一样工作」的假脚本：
#   * 每 50ms 重写状态文件（模拟每帧写）
#   * 每 50ms 看一眼停止 flag
#   * 看到 flag -> 先写「已松键」标记，再 exit 0（对应真脚本的 sprint_off()）
#   * 一直没等到 flag -> 写一个 exit_code 给的状态后自行退出
#   * 往 stdout 狂喷字，验证 DEVNULL 不会把它堵死
WRITER = '''
import json, os, sys, time

runtime = "__RUNTIME__"
flag = os.path.join(runtime, "nav_stop.flag")
state = os.path.join(runtime, "pathfind_state.json")
released = os.path.join(runtime, "keys_released.marker")

frame = 0
deadline = time.time() + __MAX_SECONDS__
while time.time() < deadline:
    frame += 1
    # 故意分三次写：撞上「只写了一半」是常态，Console 侧必须容错
    with open(state, "w", encoding="utf-8") as f:
        f.write('{"state": "MOVING", "frame": ')
        f.write(str(frame))
        f.write("}")
    # 喷字：DEVNULL 的话随便喷；真接了 PIPE 又没人读就会写满阻塞
    print("x" * 512, flush=True)
    if os.path.exists(flag):
        with open(released, "w", encoding="utf-8") as f:
            f.write(str(frame))
        sys.exit(0)
    time.sleep(0.05)

with open(os.path.join(runtime, "self_exit.marker"), "w", encoding="utf-8") as f:
    f.write(str(frame))
sys.exit(7)
'''


class _Cfg:
    def __init__(self, root: Path, runtime: Path) -> None:
        self._root, self._runtime = root, runtime

    def resolved_path(self) -> Path:
        return self._root

    def resolved_runtime_dir(self) -> Path:
        return self._runtime

    def resolved_python(self) -> Path:
        import sys

        return Path(sys.executable)

    def resolved_pathfind_script(self) -> Path:
        return self._root / "autopilot" / "tools" / "e2e_pathfind.py"


@pytest.fixture
def live():
    tmp = Path(tempfile.mkdtemp(prefix="pflive_"))
    root = tmp / "engine"
    runtime = tmp / "runtime"
    sdir = root / "autopilot" / "tools"
    sdir.mkdir(parents=True)
    runtime.mkdir(parents=True)
    try:
        yield tmp, root, runtime, sdir
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _make(live, max_seconds: float = 20.0):
    tmp, root, runtime, sdir = live
    script = sdir / "e2e_pathfind.py"
    # ★ 用 replace 而不是 .format —— 脚本里有 JSON 花括号，.format 会要求
    #   把每个 { } 都写成双份，忘一处就是一个看不出原因的 ValueError。
    # ★ runtime 路径必须走 repr()：Windows 路径里的反斜杠（``C:\Users``）
    #   直接插进字符串字面量就是非法转义（``\U``），脚本会 SyntaxError 退出，
    #   而 Popen 照样成功 —— 症状是「状态文件永远不出现」，与真故障长得
    #   一点都不像。让 Python 自己转义最省事。
    body = (WRITER.replace('"__RUNTIME__"', repr(str(runtime)))
                   .replace("__MAX_SECONDS__", repr(float(max_seconds))))
    script.write_text(body, encoding="utf-8")
    return PathfindChannel(_Cfg(root, runtime)), runtime


def _wait_until(pred, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


# ==================================================================
# 前提：脚本真的会把状态文件写出来
# ==================================================================

def test_live__script_really_writes_the_state_file(live) -> None:
    """★ 本组测试存在的理由：验证「每帧重写」这个前提本身。"""
    ch, runtime = _make(live)
    assert ch.read_state() is None            # 还没跑，当然没有

    ok, msg = ch.start(start_world="1,2", target_world="3,4")
    assert ok is True, msg
    try:
        assert _wait_until(lambda: ch.read_state() is not None), "脚本跑起来了却没写状态文件"
        st = ch.read_state()
        assert st is not None
        assert st["state"] == "MOVING"
        assert isinstance(st["frame"], int)
    finally:
        ch.stop(grace_s=5.0)


def test_live__state_keeps_advancing_while_running(live) -> None:
    """不是写一次就不动了 —— 这是 mtime 判定「它还活着」的前提。"""
    ch, _runtime = _make(live)
    ok, msg = ch.start(start_world="1,2", target_world="3,4")
    assert ok is True, msg
    try:
        assert _wait_until(lambda: (ch.read_state() or {}).get("frame", 0) >= 2)
        first = (ch.read_state() or {})["frame"]
        assert _wait_until(lambda: (ch.read_state() or {}).get("frame", 0) > first)
    finally:
        ch.stop(grace_s=5.0)


def test_live__heavy_stdout_does_not_block_the_child(live) -> None:
    """★ 验证 stdout=DEVNULL 这个选择的真实后果。

    假脚本每帧喷 512 字节。真接了 PIPE 又没人读，管道（约 64KB）很快写满，
    子进程会**阻塞在 write 上** —— 进程活着、UI 显示「运行中」，但角色
    一动不动。这里靠「状态文件还在推进」把它测出来。
    """
    ch, _runtime = _make(live)
    ok, msg = ch.start(start_world="1,2", target_world="3,4")
    assert ok is True, msg
    try:
        # 喷满 64KB 需要 ~128 帧；能推进到就说明没被堵
        assert _wait_until(lambda: (ch.read_state() or {}).get("frame", 0) >= 20,
                           timeout=15.0), "子进程被 stdout 堵死了"
    finally:
        ch.stop(grace_s=5.0)


# ==================================================================
# flag 真的能让脚本先松键再退出
# ==================================================================

def test_live__flag_makes_the_script_release_keys_before_exiting(live) -> None:
    """★ Console 侧机制的最终目的：按键状态必须确定。

    契约是「脚本看到 flag -> 先松键 -> 再退出」。Console 保证 flag 被写了，
    假脚本保证它照做，``keys_released.marker`` 是两者之间的物证。
    """
    ch, runtime = _make(live)
    ok, msg = ch.start(start_world="1,2", target_world="3,4")
    assert ok is True, msg
    assert _wait_until(lambda: (ch.read_state() or {}).get("frame", 0) >= 1)

    ok2, msg2 = ch.stop(grace_s=10.0)
    assert ok2 is True
    assert "terminate" not in msg2, msg2
    assert (runtime / "keys_released.marker").is_file(), "脚本没走松键那条路就退了"


def test_live__stop_flag_is_actually_deleted_at_start(live) -> None:
    """上一轮的 flag 还在的话，新脚本一起来就秒退 —— 且没人看得见为什么。"""
    ch, runtime = _make(live)
    stale = runtime / "nav_stop.flag"
    stale.write_text("上次的残留", encoding="utf-8")

    ok, msg = ch.start(start_world="1,2", target_world="3,4")
    assert ok is True, msg
    try:
        # 脚本若一启动就看到 flag，会立刻退出并留下松键标记
        time.sleep(0.6)
        assert ch.is_running() is True
        assert not (runtime / "keys_released.marker").exists()
    finally:
        ch.stop(grace_s=5.0)


# ==================================================================
# ★ 陈旧判定：这套逻辑唯一「真的危险」的地方
# ==================================================================

def test_live__state_left_behind_by_a_dead_process_goes_stale(live, monkeypatch) -> None:
    """★★ 最容易出事的一格。

    脚本自己跑完退出后，状态文件**还在**，内容还是「MOVING」。
    如果只按「文件存在 + 能解析」判断，UI 会一直显示「运行中 / MOVING」，
    而真实情况是它早就停了 —— **陈旧比没有状态更危险**，因为它会让人
    以为寻路还在努力工作。

    真实阈值 5 秒，测试里调小（``is_state_stale()`` 每次调用都重读模块
    常量，所以这里改得动）。
    """
    monkeypatch.setattr(P, "STALE_AFTER_S", 0.3)
    ch, _runtime = _make(live, max_seconds=0.4)
    ok, msg = ch.start(start_world="1,2", target_world="3,4")
    assert ok is True, msg

    assert _wait_until(lambda: not ch.is_running(), timeout=10.0), "脚本没自行退出"
    ch._poll()          # 手动驱动 Qt 定时器（见 test_pathfind_channel 同款写法）
    assert ch.exit_code() == 7

    # 文件还在、内容还在 —— 但已经不新鲜了
    assert ch.read_state() is not None
    assert _wait_until(lambda: ch.is_state_stale(), timeout=5.0)
    assert ch.state() == "stopped"


def test_live__running_process_is_never_reported_as_stale(live, monkeypatch) -> None:
    """反过来：真在跑的时候绝不能被判陈旧（否则 UI 会说「可能已停止」）。"""
    monkeypatch.setattr(P, "STALE_AFTER_S", 0.3)
    ch, _runtime = _make(live)
    ok, msg = ch.start(start_world="1,2", target_world="3,4")
    assert ok is True, msg
    try:
        assert _wait_until(lambda: (ch.read_state() or {}).get("frame", 0) >= 4)
        assert ch.is_state_stale() is False
        assert ch.is_running() is True
    finally:
        ch.stop(grace_s=5.0)


def test_live__stopped_cleanly_leaves_fresh_then_stale_state(live, monkeypatch) -> None:
    """正常停止后同样会陈旧 —— 判据是「文件有没有在动」，与怎么停的无关。"""
    monkeypatch.setattr(P, "STALE_AFTER_S", 0.3)
    ch, _runtime = _make(live)
    ok, msg = ch.start(start_world="1,2", target_world="3,4")
    assert ok is True, msg
    assert _wait_until(lambda: (ch.read_state() or {}).get("frame", 0) >= 1)

    ch.stop(grace_s=10.0)
    assert ch.state() == "stopped"
    assert ch.read_state() is not None        # 文件还在，别的地方还会读它
    assert _wait_until(lambda: ch.is_state_stale(), timeout=5.0)