"""``PathfindChannel`` 单测（Console 侧）。

全部用**假脚本 + 假 EngineConfig**，不碰真游戏、不动真引擎。

重点守三件事：
1. **与引擎并行的拒绝路径** —— 这是整个设计存在的理由
2. **flag 停止 vs terminate 兜底** —— 兜底必须**如实说**按键状态可能不明
3. **状态文件读坏的容错** —— 脚本每帧都在写，撞上半截是常态
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

import pytest

from neko_console.channels.pathfind import PathfindChannel

# 一个只看停止 flag 的假脚本 —— 用来验证优雅停止。
# ★ flag 路径**烘进脚本体**：真脚本是从模块常量 ``NAV_STOP_FILE`` 读的，
#   通道并不通过 argv 传这个路径 —— 保持一致，否则这条用例测的是一个
#   真脚本不具备的调用方式。
POLITE = """
import os, sys, time
flag = {flag!r}
print("polite started", flush=True)
deadline = time.time() + 30
while time.time() < deadline:
    if os.path.exists(flag):
        print("flag seen, releasing", flush=True)
        sys.exit(0)
    time.sleep(0.02)
sys.exit(3)
"""

# 一个无视停止 flag 的假脚本 —— 用来验证 terminate 兜底。
STUBBORN = """
import time
print("stubborn started", flush=True)
while True:
    time.sleep(0.05)
"""


class _FakeEngineConfig:
    """只提供 PathfindChannel 用到的三个方法。"""

    def __init__(self, root: Path, runtime: Path) -> None:
        self._root = root
        self._runtime = runtime

    def resolved_path(self) -> Path:
        return self._root

    def resolved_runtime_dir(self) -> Path:
        return self._runtime

    def resolved_pathfind_script(self) -> Path:
        """本文件约定寻路脚本就在插件内（真实解析规则见
        ``EngineConfig.resolved_pathfind_script``）。"""
        return self._root / "autopilot" / "tools" / "e2e_pathfind.py"

    def resolved_python(self) -> Path:
        import sys

        return Path(sys.executable)


@pytest.fixture
def env():
    tmp = Path(tempfile.mkdtemp(prefix="pfchan_"))
    root = tmp / "engine"
    runtime = tmp / "runtime"
    script_dir = root / "autopilot" / "tools"
    script_dir.mkdir(parents=True)
    runtime.mkdir(parents=True)
    try:
        yield tmp, root, runtime, script_dir
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _ch(env, body: str = POLITE, **kw) -> tuple[PathfindChannel, Path]:
    tmp, root, runtime, script_dir = env
    script = script_dir / "e2e_pathfind.py"
    if body is POLITE:
        body = POLITE.format(flag=str(runtime / "nav_stop.flag"))
    script.write_text(body, encoding="utf-8")
    return PathfindChannel(_FakeEngineConfig(root, runtime), **kw), script


def _args(script: Path, rt: Path) -> list[str]:
    """POLITE 脚本需要知道 flag 路径 —— 用环境变量传最省事。"""
    return ["--start-world", "1,2", "--target-world", "3,4"]


# ==================================================================
# 护栏：与引擎并行
# ==================================================================

def test_start__refused_when_engine_running(env) -> None:
    """★ 整个设计存在的理由。

    引擎和寻路都用 Win32Backend 注入同一套键鼠，一方 release_all() 会
    松开对方按住的键。必须在**启动前**拒绝，而不是启动后发现。
    """
    ch, _ = _ch(env)
    ok, msg = ch.start(start_world="1,2", target_world="3,4",
                      engine_running=True)
    assert ok is False
    assert "引擎正在运行" in msg
    assert "不能与寻路并行" in msg


def test_start__refused_when_already_running(env) -> None:
    """已在跑时拒绝启动第二个 —— 两个寻路也抢键鼠。"""
    ch, _ = _ch(env)
    ok, msg = ch.start(start_world="1,2", target_world="3,4",
                      engine_running=False)
    assert ok is True, msg
    try:
        ok2, msg2 = ch.start(start_world="1,2", target_world="3,4",
                             engine_running=False)
        assert ok2 is False
        assert "已在运行" in msg2
    finally:
        ch.stop(grace_s=1.0)


def test_start__refused_on_path_problem(env) -> None:
    tmp, root, runtime, script_dir = env
    # missing_ok：这个目录本来就可能是空的（夹具只建目录，不建文件）
    script_dir.joinpath("e2e_pathfind.py").unlink(missing_ok=True)
    ch = PathfindChannel(_FakeEngineConfig(root, runtime))
    ok, msg = ch.start(start_world="1,2", target_world="3,4")
    assert ok is False
    assert "找不到寻路脚本" in msg


def test_start__clears_stale_stop_flag(env) -> None:
    """上次的 flag 不清掉的话，新实例一起来就秒停。"""
    ch, script = _ch(env)
    ch.stop_file().parent.mkdir(parents=True, exist_ok=True)
    ch.stop_file().touch()
    assert ch.stop_file().exists()

    ok, msg = ch.start(start_world="1,2", target_world="3,4",
                      engine_running=False)
    assert ok is True, msg
    time.sleep(0.3)
    assert ch.is_running() is True, "残留 flag 没清掉，实例一起来就停了"
    ch.stop(grace_s=2.0)


# ==================================================================
# 停止：flag vs terminate
# ==================================================================

def test_stop__flag_path_reports_clean_exit(env) -> None:
    """flag 生效时脚本自己退，exit=0，且说明里**不含** terminate。"""
    ch, _ = _ch(env)
    ok, msg = ch.start(start_world="1,2", target_world="3,4",
                      engine_running=False)
    assert ok is True, msg
    time.sleep(0.4)
    ok2, msg2 = ch.stop(grace_s=10.0)
    assert ok2 is True
    assert "terminate" not in msg2, msg2
    assert ch.state() == "stopped"


def test_stop__terminate_fallback_is_disclosed(env) -> None:
    """flag 无效时退到 terminate，且**必须**说明按键状态可能不明。

    假装干净 = 用户以为松开了，结果角色一直往前走，根因离症状极远。
    """
    ch, _ = _ch(env, STUBBORN)
    ok, msg = ch.start(start_world="1,2", target_world="3,4",
                      engine_running=False)
    assert ok is True, msg
    time.sleep(0.3)
    ok2, msg2 = ch.stop(grace_s=1.0)
    assert ok2 is True
    assert "terminate" in msg2, msg2
    assert "按键状态可能不明" in msg2, msg2


def test_stop__when_not_running_is_ok(env) -> None:
    ch, _ = _ch(env)
    ok, msg = ch.stop()
    assert ok is True
    assert "本来就没在跑" in msg


def test_stop__writes_flag_before_terminating(env) -> None:
    """停止的第一动作必须是写 flag —— 顺序反了就没意义了。"""
    ch, _ = _ch(env)
    ch.start(start_world="1,2", target_world="3,4", engine_running=False)
    time.sleep(0.3)
    seen = {}
    orig = Path.touch

    def spy(self, *a, **kw):
        if self.name == "nav_stop.flag":
            seen["touched"] = True
        return orig(self, *a, **kw)

    Path.touch = spy  # noqa: SLF001
    try:
        ch.stop(grace_s=3.0)
    finally:
        Path.touch = orig  # noqa: SLF001
    assert seen.get("touched") is True, "停止时没有写 nav_stop.flag"


# ==================================================================
# 状态读取容错
# ==================================================================

def test_read_state__missing_file_returns_none(env) -> None:
    ch, _ = _ch(env)
    assert ch.read_state() is None


def test_read_state__half_written_json_returns_none(env) -> None:
    """脚本每帧都在写，撞上「只写了一半」是常态 —— 不能当成失败。"""
    ch, _ = _ch(env)
    ch.state_file().write_text('{"state": "MOVI', encoding="utf-8")
    assert ch.read_state() is None


def test_read_state__non_dict_returns_none(env) -> None:
    ch, _ = _ch(env)
    ch.state_file().write_text("[1, 2, 3]", encoding="utf-8")
    assert ch.read_state() is None


def test_read_state__valid_returns_dict(env) -> None:
    ch, _ = _ch(env)
    ch.state_file().write_text(
        '{"state": "ARRIVED", "distance_m": 1.25}', encoding="utf-8")
    st = ch.read_state()
    assert st is not None
    assert st["state"] == "ARRIVED"
    assert st["distance_m"] == 1.25


def test_read_recent_events__survives_bad_lines(env) -> None:
    """事件流里混了半截行也要能读出其余的。"""
    tmp, root, runtime, script_dir = env
    ch, _ = _ch(env)
    (runtime / "run_current.txt").write_text("20261011_000000\n", encoding="utf-8")
    (runtime / "20261011_000000.stream.jsonl").write_text(
        '{"code": "A", "message": "x"}\n'
        '{"code": "BROKEN\n'
        '{"code": "B", "message": "y"}\n',
        encoding="utf-8")
    evs = ch.read_recent_events(limit=10)
    codes = [e.get("code") for e in evs]
    assert codes == ["A", "B"], codes


def test_read_recent_events__no_run_file_returns_empty(env) -> None:
    ch, _ = _ch(env)
    assert ch.read_recent_events() == []


def test_state_age__missing_file_is_inf(env) -> None:
    """读不到文件 = 最陈旧，调用方统一走「不可信」分支。"""
    ch, _ = _ch(env)
    assert ch.state_age_s() == float("inf")
    assert ch.is_state_stale() is True


def test_state_age__fresh_file_is_not_stale(env) -> None:
    """刚写的文件不该被判陈旧。"""
    ch, _ = _ch(env)
    ch.state_file().write_text('{"state": "MOVING"}', encoding="utf-8")
    assert ch.state_age_s() < 5.0
    assert ch.is_state_stale() is False


def test_state_age__old_file_is_stale(env) -> None:
    """★ 陈旧比没有状态更危险：文件留着最后一帧，UI 会一直显示「运行中」。

    所以判据是**文件 mtime**（脚本每帧重写），不是 JSON 里的时间字段。
    """
    import os

    ch, _ = _ch(env)
    ch.state_file().write_text('{"state": "MOVING"}', encoding="utf-8")
    old = time.time() - 30.0
    os.utime(ch.state_file(), (old, old))
    assert ch.state_age_s() > 5.0
    assert ch.is_state_stale() is True
    # 内容仍然读得到 —— 陈旧不等于不存在，UI 要显示「陈旧」而不是「—」
    assert ch.read_state() is not None


# ==================================================================
# 生命周期观察
# ==================================================================

def test_state_changes_when_process_exits(env) -> None:
    """脚本自己退了，_poll 应该把状态切回 stopped。"""
    ch, _ = _ch(env, "import sys; sys.exit(7)")
    ok, _msg = ch.start(start_world="1,2", target_world="3,4",
                        engine_running=False)
    assert ok is True
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        ch._poll()          # 手动驱动，不依赖 Qt 事件循环
        if not ch.is_running():
            break
        time.sleep(0.05)
    ch._poll()              # ★ 循环可能在 is_running() 已为 False 时就退出，
    #                          所以循环外必须再补一次，否则 exit_code 没落上
    assert ch.is_running() is False
    assert ch.exit_code() == 7
    assert ch.state() == "stopped"