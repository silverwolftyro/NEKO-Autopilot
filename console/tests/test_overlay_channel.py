"""``OverlayManager`` 单测（Console 侧，2026-10-12 任务 A）。

只测纯逻辑：实时缩放通道、位置文件读写、路径体检。**不起 overlay 进程**
（那需要真 tkinter 窗口），不起 Qt 事件循环。

两处最值得守：

1. ``write_live_scale`` 的**静默失败** —— 拖动滑块时每秒调 20 次，
   磁盘抖一下就弹一次错误框，用户会以为程序崩了。
2. ``read_position`` 的 **None vs (0,0)** —— 读不到就显示「未定位」；
   返回 0,0 会让用户以为卡片跑到左上角了，而真相是「还没定位过」。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from neko_console.channels.overlay import (
    LIVE_SCALE_FILE_NAME,
    POS_FILE_NAME,
    OverlayManager,
)


class _FakeEngineConfig:
    def __init__(self, root: Path, runtime: Path) -> None:
        self._root = root
        self._runtime = runtime

    def resolved_path(self) -> Path:
        return self._root

    def resolved_runtime_dir(self) -> Path:
        return self._runtime

    def resolved_python(self, _engine_cfg=None) -> Path:
        return Path(sys.executable)


class _FakeOverlayConfig:
    """⚠️ ``runtime_dir`` 是从 **overlay_cfg** 上取的，而且**带 engine_cfg 参数**
    （``overlay.py:88``），不像 EngineManager 那样直接问 engine_cfg。
    写错这里会让 20 条用例全挂在构造期。"""

    def __init__(self, py: Path, runtime: Path) -> None:
        self._py = py
        self._runtime = runtime

    def resolved_runtime_dir(self, _engine_cfg=None) -> Path:
        return self._runtime

    def resolved_python(self, _engine_cfg=None) -> Path:
        return self._py


@pytest.fixture
def env():
    tmp = Path(tempfile.mkdtemp(prefix="ovchan_"))
    root = tmp / "engine"
    runtime = tmp / "runtime"
    (root / "autopilot" / "tools").mkdir(parents=True)
    runtime.mkdir(parents=True)
    try:
        yield tmp, root, runtime
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _mgr(env, *, py=None, make_overlay_py=True):
    _tmp, root, runtime = env
    if make_overlay_py:
        (root / "autopilot" / "tools" / "overlay.py").write_text(
            "# stub", encoding="utf-8")
    return OverlayManager(
        _FakeOverlayConfig(py or Path(sys.executable), runtime),
        _FakeEngineConfig(root, runtime))


# ==================================================================
# 初始状态 / 路径
# ==================================================================

def test_state__starts_stopped(env) -> None:
    assert _mgr(env).state() == "stopped"


def test_is_running__false_before_start(env) -> None:
    assert _mgr(env).is_running() is False


def test_exit_code__none_before_any_run(env) -> None:
    assert _mgr(env).exit_code() is None


def test_runtime_dir__comes_from_the_engine_config(env) -> None:
    _tmp, _root, runtime = env
    assert _mgr(env).runtime_dir() == runtime


def test_pos_file__sits_in_the_runtime_dir(env) -> None:
    _tmp, _root, runtime = env
    assert _mgr(env).overlay_pos_file() == runtime / POS_FILE_NAME


def test_live_scale_file__sits_in_the_runtime_dir(env) -> None:
    _tmp, _root, runtime = env
    assert _mgr(env).live_scale_file() == runtime / LIVE_SCALE_FILE_NAME


# ==================================================================
# 路径体检 —— 「返回而不抛」，UI 要显示问题不能让主控台起不来
# ==================================================================

def test_path_problems__empty_when_everything_is_fine(env) -> None:
    assert _mgr(env).path_problems() == []


def test_path_problems__reports_a_missing_interpreter(env) -> None:
    m = _mgr(env, py=env[0] / "没有这个python.exe")
    assert any("Python" in p for p in m.path_problems())


def test_path_problems__reports_a_missing_engine_dir(env) -> None:
    tmp = env[0]
    m = OverlayManager(
        _FakeOverlayConfig(Path(sys.executable), env[2]),
        _FakeEngineConfig(tmp / "不存在", env[2]))
    assert any("引擎目录" in p for p in m.path_problems())


def test_path_problems__reports_a_missing_overlay_entry(env) -> None:
    """引擎目录在但 overlay.py 不在 —— 分开报，别混成一条。"""
    m = _mgr(env, make_overlay_py=False)
    assert any("overlay.py" in p for p in m.path_problems())


def test_path_problems__never_raises(env) -> None:
    """UI 要把问题显示出来 —— 抛异常的话主控台自己就起不来了。"""
    m = _mgr(env, py=env[0] / "x", make_overlay_py=False)
    assert isinstance(m.path_problems(), list)


# ==================================================================
# 实时缩放通道
# ==================================================================

def test_write_live_scale__creates_the_file(env) -> None:
    _tmp, _root, runtime = env
    m = _mgr(env)
    assert m.write_live_scale(1.25) is True
    data = json.loads((runtime / LIVE_SCALE_FILE_NAME).read_text("utf-8"))
    assert data["scale"] == pytest.approx(1.25)
    assert data["ts_ms"] > 0


def test_write_live_scale__uses_a_tmp_file_then_replaces(env) -> None:
    """★ overlay 每 80ms 读一次，撞上「正在写」的半截会丢帧。"""
    _tmp, _root, runtime = env
    m = _mgr(env)
    m.write_live_scale(1.0)
    leftovers = [p.name for p in runtime.iterdir() if p.name.endswith(".tmp")]
    assert not leftovers, f"临时文件没清掉：{leftovers}"


def test_write_live_scale__failure_is_silent_and_returns_false(env) -> None:
    """★ 拖动时每秒调 20 次，磁盘抖一下就弹框 -> 用户以为程序崩了。"""
    _tmp, _root, runtime = env
    m = _mgr(env)
    # 目标变成目录 -> write_text/replace 必失败
    (runtime / LIVE_SCALE_FILE_NAME).mkdir()
    assert m.write_live_scale(1.5) is False      # 不抛就是过


def test_write_live_scale__overwrites_the_previous_value(env) -> None:
    _tmp, _root, runtime = env
    m = _mgr(env)
    m.write_live_scale(0.8)
    m.write_live_scale(1.2)
    data = json.loads((runtime / LIVE_SCALE_FILE_NAME).read_text("utf-8"))
    assert data["scale"] == pytest.approx(1.2)


def test_write_live_scale__rejects_a_non_numeric_scale(env) -> None:
    """传错类型时必须返回 False 而不是把一个坏 JSON 写给 overlay。"""
    assert _mgr(env).write_live_scale("不是数字") is False


def test_clear_live_scale__removes_the_file(env) -> None:
    _tmp, _root, runtime = env
    m = _mgr(env)
    m.write_live_scale(1.0)
    m.clear_live_scale()
    assert not (runtime / LIVE_SCALE_FILE_NAME).exists()


def test_clear_live_scale__missing_file_is_not_an_error(env) -> None:
    _mgr(env).clear_live_scale()              # 不抛就是过


def test_clear_live_scale__failure_does_not_raise(env) -> None:
    """这只是清理 —— 失败后果是下次启动多闪一帧，不该炸。"""
    _tmp, _root, runtime = env
    (runtime / LIVE_SCALE_FILE_NAME).mkdir()   # unlink 一个目录 -> OSError
    _mgr(env).clear_live_scale()              # 不抛就是过


# ==================================================================
# 位置文件 —— None vs (0, 0)
# ==================================================================

def test_read_position__missing_file_gives_none(env) -> None:
    assert _mgr(env).read_position() is None


def test_read_position__valid_position_is_returned(env) -> None:
    _tmp, _root, runtime = env
    (runtime / POS_FILE_NAME).write_text(
        json.dumps({"x": 1500, "y": 140, "alpha": 0.8}), encoding="utf-8")
    got = _mgr(env).read_position()
    assert got is not None and got["x"] == 1500 and got["y"] == 140


def test_read_position__zero_zero_is_a_real_position_not_none(env) -> None:
    """★ (0,0) 是**合法位置**（卡片贴左上角），不能被当成「没定位过」。"""
    _tmp, _root, runtime = env
    (runtime / POS_FILE_NAME).write_text(
        json.dumps({"x": 0, "y": 0}), encoding="utf-8")
    got = _mgr(env).read_position()
    assert got is not None and got["x"] == 0 and got["y"] == 0


def test_read_position__half_written_json_gives_none(env) -> None:
    _tmp, _root, runtime = env
    (runtime / POS_FILE_NAME).write_text('{"x": 15', encoding="utf-8")
    assert _mgr(env).read_position() is None


def test_read_position__non_dict_body_gives_none(env) -> None:
    _tmp, _root, runtime = env
    (runtime / POS_FILE_NAME).write_text("[1,2,3]", encoding="utf-8")
    assert _mgr(env).read_position() is None


def test_read_position__missing_fields_give_none(env) -> None:
    """字段缺失 -> None。返回 0,0 会让用户以为卡片跑左上角了。"""
    _tmp, _root, runtime = env
    (runtime / POS_FILE_NAME).write_text(
        json.dumps({"alpha": 0.8}), encoding="utf-8")
    assert _mgr(env).read_position() is None


def test_read_position__non_integer_coordinates_give_none(env) -> None:
    _tmp, _root, runtime = env
    (runtime / POS_FILE_NAME).write_text(
        json.dumps({"x": "1500", "y": 140}), encoding="utf-8")
    assert _mgr(env).read_position() is None


def test_read_position__float_coordinates_are_rejected(env) -> None:
    """⚠️ 坐标必须是 **int** —— ``isinstance(x, int)`` 不接受 ``1500.5``。

    记下来是因为它反直觉：缩放后的浮点位置会被当成「没定位过」，
    卡片莫名其妙跑到默认位置。踩到时先看这里。
    """
    _tmp, _root, runtime = env
    (runtime / POS_FILE_NAME).write_text(
        json.dumps({"x": 1500.5, "y": 140.25}), encoding="utf-8")
    assert _mgr(env).read_position() is None


def test_reset_position__removes_the_file(env) -> None:
    _tmp, _root, runtime = env
    (runtime / POS_FILE_NAME).write_text(json.dumps({"x": 1, "y": 2}),
                                        encoding="utf-8")
    assert _mgr(env).reset_position() is True
    assert not (runtime / POS_FILE_NAME).exists()


def test_reset_position__already_absent_still_reports_ok(env) -> None:
    """「重置位置」按钮可反复点 —— 目标状态（文件不存在）已达成就算成功。

    docstring 说「返回是否真的删了」，但实现是「达成目标状态即 True」，
    这里把实际语义钉住：调用方不能靠返回值判断「之前有没有位置」。
    """
    m = _mgr(env)
    assert m.reset_position() is True
    assert m.reset_position() is True


def test_reset_position__failure_returns_false(env) -> None:
    """unlink 一个目录会 OSError —— 必须返回 False 而不是崩。"""
    _tmp, _root, runtime = env
    (runtime / POS_FILE_NAME).mkdir()
    assert _mgr(env).reset_position() is False
