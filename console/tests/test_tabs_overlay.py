"""``tabs/overlay.py`` 单测（Console 侧，2026-10-12）。

离屏跑，用假 manager / 假 cfg，不起 tkinter 子进程。

★★ 头等大事：**绝不能碰用户真实的 config.toml**
  ``_commit_scale`` / ``_on_follow_toggled`` 都会调 ``save_config(self.cfg)``，
  而那是直接写 ``<项目根>/config.toml``。autouse 夹具里统一替换成记录器，
  否则一次 pytest 就把用户的缩放和 follow 设置改了。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal          # noqa: E402
from PySide6.QtWidgets import QApplication         # noqa: E402

from neko_console.tabs import overlay as O          # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _FakeOverlayCfg:
    def __init__(self) -> None:
        self.scale = 1.0
        self.follow_engine = False
        self.args: list[str] = []


class _FakeCfg:
    def __init__(self) -> None:
        self.overlay = _FakeOverlayCfg()


class _FakeManager(QObject):
    state_changed = Signal(str)

    def __init__(self, problems=None, running=False, position=None) -> None:
        super().__init__()
        self._problems = list(problems or [])
        self._running = running
        self._pos = position
        self.calls: list[tuple] = []
        self.live_writes: list[float] = []
        self.clear_count = 0
        self.start_ok = True

    def state(self):
        return "running" if self._running else "stopped"

    def is_running(self):
        return self._running

    def path_problems(self):
        return list(self._problems)

    def exit_code(self):
        return None

    def python_path(self):
        return r"D:\fake\python.exe"

    def read_position(self):
        return self._pos

    def start(self):
        self.calls.append(("start",))
        self._running = self.start_ok
        return self.start_ok

    def stop(self, timeout_ms=None):
        self.calls.append(("stop", timeout_ms))
        self._running = False
        return True

    def reset_position(self):
        self.calls.append(("reset_position",))
        self._pos = None

    def write_live_scale(self, v):
        self.live_writes.append(v)

    def clear_live_scale(self):
        self.clear_count += 1


@pytest.fixture(autouse=True)
def _no_real_config_write(monkeypatch):
    """★ 拦下所有 save_config —— 它写的是用户真实的 config.toml。"""
    saved: list = []
    monkeypatch.setattr(O, "save_config", lambda cfg: saved.append(cfg) or True)
    return saved


@pytest.fixture
def mgr():
    return _FakeManager()


@pytest.fixture
def tab(qapp, mgr):
    t = O.OverlayTab(mgr, _FakeCfg())
    yield t
    t.shutdown()
    t.deleteLater()


# ==================================================================
# 缩放滑块文案
# ==================================================================

def test_scale_label__shows_the_number(tab) -> None:
    tab._update_scale_label(150)
    assert tab._scale_lbl.text() == "150%"


def test_scale_label__hundred_percent_is_the_default(tab) -> None:
    tab._update_scale_label(100)
    assert "系统默认" in tab._scale_lbl.toolTip()


def test_scale_label__bigger_warns_about_blocking_the_screen(tab) -> None:
    tab._update_scale_label(250)
    assert "挡" in tab._scale_lbl.toolTip()


def test_scale_label__smaller_warns_about_tiny_text(tab) -> None:
    tab._update_scale_label(60)
    assert "字更小" in tab._scale_lbl.toolTip()


def test_scale_label__tooltip_carries_the_multiplier(tab) -> None:
    tab._update_scale_label(125)
    assert "1.25 倍" in tab._scale_lbl.toolTip()


def test_slider__range_and_tracking(tab) -> None:
    """★ tracking 关掉的话拖动时 valueChanged 不发，文案会停在原地。"""
    assert tab._scale_slider.minimum() == O.SCALE_SLIDER_MIN
    assert tab._scale_slider.maximum() == O.SCALE_SLIDER_MAX
    assert tab._scale_slider.hasTracking() is True


def test_slider__initial_value_comes_from_config(qapp) -> None:
    cfg = _FakeCfg()
    cfg.overlay.scale = 1.75
    t = O.OverlayTab(_FakeManager(), cfg)
    try:
        assert t._scale_slider.value() == 175
    finally:
        t.shutdown()
        t.deleteLater()


# ==================================================================
# 拖动节流
# ==================================================================

def test_drag__label_updates_even_when_the_card_is_not_running(tab, mgr) -> None:
    """★ 标签是纯内存操作，不该跟着写文件一起被节流掉。"""
    tab._on_scale_dragged(140)
    assert tab._scale_lbl.text() == "140%"
    assert mgr.live_writes == []          # 没在跑就不写「实时」通道


def test_drag__writes_live_scale_when_the_card_is_running(qapp) -> None:
    m = _FakeManager(running=True)
    t = O.OverlayTab(m, _FakeCfg())
    try:
        t._on_scale_dragged(140)
        assert m.live_writes == [1.4]
    finally:
        t.shutdown()
        t.deleteLater()


def test_drag__live_write_is_throttled(qapp, monkeypatch) -> None:
    """★ 每秒 20 次 open+write+replace 会拖死磁盘。"""
    monkeypatch.setattr(O, "LIVE_WRITE_INTERVAL_MS", 50)
    m = _FakeManager(running=True)
    t = O.OverlayTab(m, _FakeCfg())
    try:
        t._on_scale_dragged(110)
        t._on_scale_dragged(120)      # 10ms 内，不该写
        t._on_scale_dragged(130)
        assert m.live_writes == [1.1]      # 只有第一次真的落盘
        assert t._scale_lbl.text() == "130%"   # 但标签每次都更新
    finally:
        t.shutdown()
        t.deleteLater()


# ==================================================================
# _commit_scale
# ==================================================================

def test_commit__unchanged_value_does_not_restart_the_card(qapp, _no_real_config_write) -> None:
    """★ 没变就别重启 —— 重启只会白闪一下。"""
    m = _FakeManager(running=True)
    t = O.OverlayTab(m, _FakeCfg())
    try:
        t._commit_scale(1.0, live=False)
        assert m.calls == []
        assert "没有变化" in t._hint.text()
        assert _no_real_config_write == []
    finally:
        t.shutdown()
        t.deleteLater()


def test_commit__live_path_never_restarts(qapp, _no_real_config_write) -> None:
    """★ 已经通过实时通道改过了，重启只会打断用户刚拖好的尺寸。"""
    m = _FakeManager(running=True)
    t = O.OverlayTab(m, _FakeCfg())
    try:
        t._commit_scale(1.5, live=True)
        assert m.calls == []
        assert t.cfg.overlay.scale == 1.5
        assert len(_no_real_config_write) == 1
        assert "已应用" in t._hint.text()
    finally:
        t.shutdown()
        t.deleteLater()


def test_commit__restarts_the_card_when_not_live(qapp) -> None:
    """overlay 只在启动时读一次配置，运行中改配置对它毫无影响。"""
    m = _FakeManager(running=True)
    t = O.OverlayTab(m, _FakeCfg())
    try:
        t._commit_scale(1.5, live=False)
        assert ("stop", 2000) in m.calls
        assert ("start",) in m.calls
        assert "已重启" in t._hint.text()
    finally:
        t.shutdown()
        t.deleteLater()


def test_commit__restart_failure_is_disclosed(qapp) -> None:
    """★ 重启失败必须说，不能留一句「已保存」让人以为生效了。"""
    m = _FakeManager(running=True)
    m.start_ok = False
    t = O.OverlayTab(m, _FakeCfg())
    try:
        t._commit_scale(1.5, live=False)
        assert "重启失败" in t._hint.text()
        assert "#f38ba8" in t._hint.styleSheet()
    finally:
        t.shutdown()
        t.deleteLater()


def test_commit__not_running_says_it_takes_effect_next_start(qapp) -> None:
    m = _FakeManager(running=False)
    t = O.OverlayTab(m, _FakeCfg())
    try:
        t._commit_scale(1.5, live=False)
        assert m.calls == []
        assert "下次启动" in t._hint.text()
    finally:
        t.shutdown()
        t.deleteLater()


@pytest.mark.parametrize("value", [0.01, -5.0, 99.0, 1e9])
def test_commit__value_is_clamped_to_the_legal_range(qapp, value) -> None:
    """★ 越界值必须收缩，不能带着 1e9 写进配置。"""
    t = O.OverlayTab(_FakeManager(running=False), _FakeCfg())
    try:
        t._commit_scale(value, live=True)
        assert O.OVERLAY_SCALE_MIN <= t.cfg.overlay.scale <= O.OVERLAY_SCALE_MAX
    finally:
        t.shutdown()
        t.deleteLater()


def test_commit__hint_survives_apply_state_overwriting_it(qapp) -> None:
    """★ ``_apply_state`` 会覆写 ``_hint``，所以必须**先**它、**后**写提示。

    顺序反了的话刚写的说明当场就被冲掉 —— 实测反馈就是「什么提示都没有」。
    """
    m = _FakeManager(running=True)
    t = O.OverlayTab(m, _FakeCfg())
    try:
        t._commit_scale(1.5, live=False)
        assert "已重启" in t._hint.text()      # 没被 _apply_state 冲掉
    finally:
        t.shutdown()
        t.deleteLater()


def test_reset__goes_back_to_100_percent(qapp) -> None:
    m = _FakeManager(running=True)
    t = O.OverlayTab(m, _FakeCfg())
    try:
        t._scale_slider.setValue(200)
        t._on_scale_reset()
        assert t._scale_slider.value() == 100
        assert t.cfg.overlay.scale == 1.0
    finally:
        t.shutdown()
        t.deleteLater()


# ==================================================================
# 位置显示
# ==================================================================

def test_pos__missing_file_shows_unlocated_not_zero(qapp) -> None:
    """★ 显示 (0,0) 会让用户以为卡片跑到屏幕左上角了。

    真相是「还没定位过」，两者含义完全不同。
    """
    t = O.OverlayTab(_FakeManager(position=None), _FakeCfg())
    try:
        t._refresh_pos()
        assert t._pos_lbl.text() == "未定位"
        assert "#6c7086" in t._pos_lbl.styleSheet()
    finally:
        t.shutdown()
        t.deleteLater()


def test_pos__shows_x_y_and_alpha(qapp) -> None:
    t = O.OverlayTab(_FakeManager(position={"x": 1500, "y": 140, "alpha": 0.85}),
                     _FakeCfg())
    try:
        t._refresh_pos()
        text = t._pos_lbl.text()
        assert "1500" in text and "140" in text and "0.85" in text
    finally:
        t.shutdown()
        t.deleteLater()


def test_pos__alpha_is_optional(qapp) -> None:
    t = O.OverlayTab(_FakeManager(position={"x": 1, "y": 2}), _FakeCfg())
    try:
        t._refresh_pos()
        assert "不透明度" not in t._pos_lbl.text()
    finally:
        t.shutdown()
        t.deleteLater()


def test_reset_pos__restarts_when_running_so_the_move_takes_effect(qapp) -> None:
    """★ overlay 只在启动时读一次位置；单纯删文件对正在跑的卡片无效。"""
    m = _FakeManager(running=True)
    t = O.OverlayTab(m, _FakeCfg())
    try:
        t._on_reset_pos()
        assert ("stop", 2000) in m.calls
        assert ("reset_position",) in m.calls
        assert ("start",) in m.calls
        assert "已重置" in t._hint.text()
    finally:
        t.shutdown()
        t.deleteLater()


def test_reset_pos__when_stopped_does_not_start_anything(qapp) -> None:
    m = _FakeManager(running=False)
    t = O.OverlayTab(m, _FakeCfg())
    try:
        t._on_reset_pos()
        assert m.calls == [("reset_position",)]
        assert "下次启动" in t._hint.text()
    finally:
        t.shutdown()
        t.deleteLater()


# ==================================================================
# 状态与按钮
# ==================================================================

def test_apply_state__problems_disable_start_and_are_listed(qapp) -> None:
    t = O.OverlayTab(_FakeManager(problems=["Python 无效", "找不到卡片脚本"]),
                     _FakeCfg())
    try:
        assert t._btn_start.isEnabled() is False
        assert "Python 无效" in t._hint.text()
    finally:
        t.shutdown()
        t.deleteLater()


def test_apply_state__running_enables_stop_only(tab) -> None:
    tab._apply_state("running")
    assert tab._btn_stop.isEnabled() is True
    assert tab._btn_start.isEnabled() is False


def test_apply_state__hint_explains_there_is_no_ipc_channel(tab) -> None:
    """没有 IPC 通道是本设计的硬约束，提示里要点明。"""
    tab._apply_state("stopped")
    assert "没有 IPC 通道" in tab._hint.text()


def test_follow_toggle__writes_back_to_config(qapp, _no_real_config_write) -> None:
    t = O.OverlayTab(_FakeManager(), _FakeCfg())
    try:
        t._on_follow_toggled(True)
        assert t.cfg.overlay.follow_engine is True
        assert len(_no_real_config_write) == 1
    finally:
        t.shutdown()
        t.deleteLater()


def test_shutdown_stops_the_timer(tab) -> None:
    tab.shutdown()
    assert tab._timer.isActive() is False