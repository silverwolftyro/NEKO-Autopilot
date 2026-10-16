"""``tabs/home.py`` 单测（Console 侧，2026-10-12）。

离屏跑，用假 manager（信号 + is_running + 可选 _snapshot），不碰真引擎。

★ 重点守两件事

1. **引擎没跑时绝不显示旧数据** —— 首页会一直显示引擎最后一次运行留下的
   快照（5.0 Hz / 30ms / 空转 25…），用户会以为引擎还活着。
   ``_on_state_changed`` 和 ``_on_snapshot`` 各有一道守卫，这两条都要测。
2. **空状态用文字而不是横杠** —— 「已停止」是准确描述，「等待引擎」暗示
   「等一下就有了」，而「—」只是占位符。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal          # noqa: E402
from PySide6.QtWidgets import QApplication         # noqa: E402

from neko_console.tabs import home as H             # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _FakeReader:
    def __init__(self, snap=None) -> None:
        self.snap = snap

    def read(self):
        return self.snap


class _FakeManager(QObject):
    snapshot_updated = Signal(dict)
    state_changed = Signal(str)

    def __init__(self, running: bool = False, reader=None) -> None:
        super().__init__()
        self._running = running
        if reader is not None:
            self._snapshot = reader

    def is_running(self) -> bool:
        return self._running


@pytest.fixture
def mgr():
    return _FakeManager()


@pytest.fixture
def home(qapp, mgr):
    h = H.HomeTab(mgr)
    yield h
    h.shutdown()
    h.deleteLater()


def _value(h) -> str:
    return h._c_state._value.text()


def _rate(h) -> str:
    return h._c_rate._value.text()


def _intent(h) -> str:
    return h._c_intent._value.text()


# ==================================================================
# 纯函数
# ==================================================================

def test_zh__known_states_are_translated() -> None:
    assert H._zh("RUNNING") == "运行中"
    assert H._zh("PAUSED") == "已暂停"
    assert H._zh("STOPPING") == "停止中"
    assert H._zh("STOPPED") == "已停止"
    assert H._zh("IDLE") == "空闲"


def test_zh__unknown_state_is_passed_through() -> None:
    """未知状态原样显示，不要糊成「已停止」—— 那是在说谎。"""
    assert H._zh("WEIRD_STATE") == "WEIRD_STATE"


def test_state_color__covers_every_translated_state() -> None:
    for s in ("RUNNING", "PAUSED", "STOPPING", "STOPPED", "IDLE"):
        assert s in H.STATE_COLOR, s
        assert H.STATE_COLOR[s].startswith("#")


def test_empty_text_uses_words_not_dashes() -> None:
    """★ 状态卡是「已停止」，数据卡是「等待引擎」，没有一个是「—」。"""
    for key, text in H._EMPTY_TEXT.items():
        assert text and text != "—", key
    assert H._EMPTY_TEXT["engine"] == "已停止"
    assert H._EMPTY_TEXT["metrics"] == "等待引擎"


# ==================================================================
# 初始空状态
# ==================================================================

def test_home__starts_in_empty_state(home) -> None:
    """构造完就该是空状态，不能让 _Card 的默认「—」闪一下。"""
    assert home._c_state._value.text() == H._EMPTY_TEXT["engine"]
    assert home._c_rate._value.text() == H._EMPTY_TEXT["metrics"]
    assert home._c_scene._value.text() == H._EMPTY_TEXT["scene"]
    assert home._c_intent._value.text() == H._EMPTY_TEXT["intent"]


def test_home__empty_hint_is_visible(home) -> None:
    assert home._empty.isVisible() or not home._empty.isHidden()


def test_clear__hint_overrides_the_default_text(home) -> None:
    home._clear(hint="自定义提示")
    assert home._empty.text() == "自定义提示"


# ==================================================================
# 引擎存活判定
# ==================================================================

def test_engine_alive__exception_means_not_alive(qapp) -> None:
    """★ 读不到就当没跑 —— 不能因为 manager 抽风就让主控台崩。"""

    class _Broken(QObject):
        snapshot_updated = Signal(dict)
        state_changed = Signal(str)

        def is_running(self):
            raise RuntimeError("IPC 断了")

    h = H.HomeTab(_Broken())
    try:
        assert h._engine_alive() is False
    finally:
        h.shutdown()
        h.deleteLater()


def test_refresh__clears_when_engine_is_not_running(home) -> None:
    home._refresh()
    assert _value(home) == H._EMPTY_TEXT["engine"]


def test_refresh__missing_reader_returns_without_crashing(qapp) -> None:
    """manager 没有 _snapshot 属性（还没起引擎）时不能抛。"""
    h = H.HomeTab(_FakeManager(running=True))
    try:
        h._refresh()          # 不抛即可
    finally:
        h.shutdown()
        h.deleteLater()


def test_refresh__applies_the_cached_snapshot(qapp) -> None:
    reader = _FakeReader({"state": "RUNNING", "tick_hz": 5.0,
                          "tick_p95_ms": 30.0})
    h = H.HomeTab(_FakeManager(running=True, reader=reader))
    try:
        h._refresh()
        assert _value(h) == "运行中"
        assert _rate(h) == "5.0 Hz / 30 ms"
    finally:
        h.shutdown()
        h.deleteLater()


# ==================================================================
# ★ 旧数据守卫
# ==================================================================

def test_snapshot_while_engine_dead_is_dropped(qapp) -> None:
    """★★ 双保险第一道：引擎没跑，即使收到快照信号也只清空。

    少了这条，首页会一直显示引擎最后一次运行留下的数字。
    """
    reader = _FakeReader({"state": "RUNNING", "tick_hz": 5.0,
                          "tick_p95_ms": 30.0})
    m = _FakeManager(running=False, reader=reader)
    h = H.HomeTab(m)
    try:
        m.snapshot_updated.emit({"state": "RUNNING", "tick_hz": 5.0,
                                 "tick_p95_ms": 30.0})
        assert _value(h) == H._EMPTY_TEXT["engine"]
        assert _rate(h) == H._EMPTY_TEXT["metrics"]
    finally:
        h.shutdown()
        h.deleteLater()


def test_state_changed_to_stopped_clears_immediately(qapp) -> None:
    """★★ 双保险第二道：引擎一停就清空，不必等下一个 500ms tick。"""
    reader = _FakeReader({"state": "RUNNING", "tick_hz": 5.0,
                          "tick_p95_ms": 30.0})
    m = _FakeManager(running=True, reader=reader)
    h = H.HomeTab(m)
    try:
        h._apply({"state": "RUNNING", "tick_hz": 5.0, "tick_p95_ms": 30.0})
        assert _value(h) == "运行中"

        m._running = False
        m.state_changed.emit("stopped")
        assert _value(h) == H._EMPTY_TEXT["engine"]
        assert _rate(h) == H._EMPTY_TEXT["metrics"]
    finally:
        h.shutdown()
        h.deleteLater()


@pytest.mark.parametrize("state", ["stopped", "paused", "stopping", "error"])
def test_state_changed_to_any_non_running_clears(qapp, state) -> None:
    m = _FakeManager(running=True, reader=_FakeReader(
        {"state": "RUNNING", "tick_hz": 5.0}))
    h = H.HomeTab(m)
    try:
        h._apply({"state": "RUNNING", "tick_hz": 5.0})
        m._running = False
        m.state_changed.emit(state)
        assert _value(h) == H._EMPTY_TEXT["engine"]
    finally:
        h.shutdown()
        h.deleteLater()


def test_state_changed_to_running_does_not_clear(home) -> None:
    home._apply({"state": "RUNNING", "tick_hz": 5.0})
    home._on_state_changed("running")
    assert _value(home) == "运行中"


# ==================================================================
# _apply 的各分支
# ==================================================================

def test_apply__unknown_state_shows_raw_text_in_grey(home) -> None:
    home._apply({"state": "WHATEVER"})
    assert _value(home) == "WHATEVER"
    assert "#6c7086" in home._c_state._value.styleSheet()


def test_apply__missing_state_shows_dash(home) -> None:
    home._apply({})
    assert _value(home) == "—"


def test_apply__rate_formats_both_numbers(home) -> None:
    home._apply({"state": "RUNNING", "tick_hz": 12.34, "tick_p95_ms": 56.7})
    assert _rate(home) == "12.3 Hz / 57 ms"


def test_apply__rate_handles_missing_p95(home) -> None:
    home._apply({"state": "RUNNING", "tick_hz": 5.0})
    assert _rate(home) == "5.0 Hz / 0 ms"


@pytest.mark.parametrize("bad", [0, -1, None, "5.0", [1]])
def test_apply__non_positive_or_non_numeric_hz_shows_dash(home, bad) -> None:
    """hz 不可信时显示「—」，不要显示一个编出来的数字。"""
    home._apply({"state": "RUNNING", "tick_hz": bad})
    assert _rate(home) == "—"


def test_apply__scene_shows_value_or_dash(home) -> None:
    home._apply({"state": "RUNNING", "scene": "WORLD"})
    assert home._c_scene._value.text() == "WORLD"
    home._apply({"state": "RUNNING", "scene": ""})
    assert home._c_scene._value.text() == "—"


def test_apply__intent_count_only(home) -> None:
    home._apply({"state": "RUNNING", "intent": {"count": 3}})
    assert _intent(home) == "3"


def test_apply__intent_idle_streak_is_highlighted(home) -> None:
    """★ 空转是「没在干活」，颜色要和正常情况区分开。"""
    home._apply({"state": "RUNNING",
                 "intent": {"count": 3, "idle_streak": 25}})
    assert _intent(home) == "3（空转 25）"
    assert "#f9e2af" in home._c_intent._value.styleSheet()


@pytest.mark.parametrize("intent", [None, {}])
def test_apply__empty_intent_shows_dash(home, intent) -> None:
    home._apply({"state": "RUNNING", "intent": intent})
    assert _intent(home) == "—"


def test_apply__intent_count_zero_shows_zero_not_a_dash(home) -> None:
    """★ ``{"count": 0}`` 是「有数据且为 0」，不是「没有数据」。

    两者语义不同：前者是引擎报上来的事实，后者是快照里压根没这个字段。
    把 0 显示成「—」会掩盖「引擎认为当前没有任何意图」这件事。
    """
    home._apply({"state": "RUNNING", "intent": {"count": 0}})
    assert _intent(home) == "0"


def test_apply__hides_the_empty_hint_when_data_arrives(home) -> None:
    assert home._empty.isVisible() or not home._empty.isHidden()
    home._apply({"state": "RUNNING", "tick_hz": 5.0})
    assert home._empty.isHidden()


def test_apply__tolerates_a_completely_empty_snapshot(home) -> None:
    """快照缺字段是常态（不同引擎版本字段会变），不能崩。"""
    home._apply({})
    assert home._c_state._value.text() == "—"


# ==================================================================
# 生命周期
# ==================================================================

def test_refresh_interval_is_500ms(home) -> None:
    assert home._timer.interval() == 500 == H.HomeTab.REFRESH_MS


def test_shutdown_stops_the_timer(home) -> None:
    home.shutdown()
    assert home._timer.isActive() is False


def test_snapshot_applies_when_engine_alive(qapp) -> None:
    """正向：引擎在跑时快照是要显示的（别把守卫写成什么都清）。"""
    m = _FakeManager(running=True)
    h = H.HomeTab(m)
    try:
        m.snapshot_updated.emit({"state": "RUNNING", "tick_hz": 5.0,
                                 "tick_p95_ms": 30.0, "scene": "WORLD"})
        assert _value(h) == "运行中"
        assert h._c_scene._value.text() == "WORLD"
    finally:
        h.shutdown()
        h.deleteLater()