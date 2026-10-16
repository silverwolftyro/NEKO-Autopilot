"""``tabs/engine.py`` 单测（Console 侧，2026-10-12）。

离屏跑，用假 manager，不起真引擎子进程、不打开资源管理器。

★ 重点守两件事

1. **日志着色只给「需要用户行动」的行** —— 引擎退出那一行是整页里唯一
   告诉用户下一步做什么的东西，必须一眼可见；而 ``code=0`` 的退出是
   正常收尾，**不该**染成吓人的红色。
2. **按钮可用性与状态严格对应** —— 运行中不能点启动，没运行时不该能停。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal          # noqa: E402
from PySide6.QtWidgets import QApplication         # noqa: E402

from neko_console.tabs import engine as E           # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _FakeCfg:
    def __init__(self, p) -> None:
        self._p = p

    def resolved_path(self):
        return self._p


class _FakeManager(QObject):
    state_changed = Signal(str)
    log_line = Signal(str)

    def __init__(self, root, problems=None, running=False, exit_code=None) -> None:
        super().__init__()
        self.cfg = _FakeCfg(root)
        self._rt = root / "_runtime"
        self._problems = list(problems or [])
        self._running = running
        self._exit = exit_code
        self.calls: list[str] = []

    def runtime_dir(self):
        return self._rt

    def path_problems(self):
        return list(self._problems)

    def is_running(self):
        return self._running

    def state(self):
        return "running" if self._running else "stopped"

    def exit_code(self):
        return self._exit

    def start(self, *, blocked_reason=None):
        # ★ 签名与真实 EngineManager.start 对齐（2026-10-16 加 blocked_reason）。
        #   刻意**记下来**而不是忽略 —— 引擎页的互斥护栏要靠它，
        #   互斥行为本身由 tests/test_engine_start_guard.py 断言。
        self.calls.append(f"start:{blocked_reason}" if blocked_reason
                          else "start")
        return True

    def stop(self):
        self.calls.append("stop")
        return True

    def kill(self):
        self.calls.append("kill")


@pytest.fixture
def mgr(tmp_path):
    return _FakeManager(tmp_path)


@pytest.fixture
def tab(qapp, mgr):
    t = E.EngineTab(mgr)
    yield t
    t.deleteLater()


def _log(t) -> str:
    return t._log.toPlainText()


# ==================================================================
# line_color（纯函数）
# ==================================================================

def test_line_color__clean_exit_is_not_alarming() -> None:
    """★ ``code=0`` 是正常收尾，染红会让人以为出错了。"""
    assert E.line_color("引擎退出（code=0）：正常结束") is None


def test_line_color__error_exit_is_red() -> None:
    assert E.line_color("引擎退出（code=2）：找不到窗口") == "#f38ba8"


def test_line_color__cross_mark_is_red() -> None:
    assert E.line_color("✗ 启动失败") == "#f38ba8"


def test_line_color__warning_is_yellow() -> None:
    assert E.line_color("⚠ 超时后被强制终止") == "#f9e2af"


def test_line_color__checkmark_is_green() -> None:
    assert E.line_color("✓ 已停止") == "#a6e3a1"


def test_line_color__ordinary_event_is_uncoloured() -> None:
    """★ 满屏彩色反而看不清重点。"""
    assert E.line_color("[2026-10-12] 场景识别 -> WORLD") is None


def test_line_color__prefix_must_be_at_the_start() -> None:
    """中途出现符号不算 —— 否则时间戳里带个 ✓ 也会被染色。"""
    assert E.line_color("[2026-10-12] ✓ 某事件") is None


def test_line_color__empty_line() -> None:
    assert E.line_color("") is None


# ==================================================================
# 状态与按钮
# ==================================================================

def test_state_style_covers_the_three_states() -> None:
    """状态集合（2026-10-16 起是**四个**）。

    ★ 为什么从三个变成四个（集成审计 F5）
      引擎拉起来之后要 import 一大堆东西、还要等游戏窗口（最长 30s），
      这段时间以前被谎报成 "running"。现在有了独立的 "starting"，
      UI 才能说清「它在等，不是在跑但也没坏」。
    """
    assert set(E.STATE_STYLE) == {"stopped", "starting", "running", "crashed"}


def test_starting_is_styled_as_waiting_not_as_failure() -> None:
    """"starting" 用黄色（等待），不是红色（失败）。

    红色会让用户以为引擎崩了，然后去翻日志找根本不存在的问题。
    """
    text, color = E.STATE_STYLE["starting"]
    assert text.endswith("…"), f"文案应当表示'还没完'，实际是 {text!r}"
    assert color == "#f9e2af"
    assert color != E.STATE_STYLE["crashed"][1]


def test_alive_states_cover_starting_and_running() -> None:
    """决定「停止/强杀」按钮亮不亮的是**进程活着**，不是跑完没有。

    启动中必须能停 —— 用户等不及想关掉是常态，而以前那个窗口里
    停止按钮是灰的，只能干等或者去任务管理器。
    """
    assert set(E.ALIVE_STATES) == {"starting", "running"}


def test_apply_state__starting_enables_stop_and_kill(tab) -> None:
    tab._apply_state("starting")
    assert tab._btn_start.isEnabled() is False
    assert tab._btn_stop.isEnabled() is True, "启动中必须能点停止"
    assert tab._btn_kill.isEnabled() is True, "启动中必须能点强杀"
    assert tab._state_lbl.text() == E.STATE_STYLE["starting"][0]


def test_apply_state__running_enables_stop_and_kill_only(tab) -> None:
    tab._apply_state("running")
    assert tab._btn_start.isEnabled() is False
    assert tab._btn_stop.isEnabled() is True
    assert tab._btn_kill.isEnabled() is True


def test_apply_state__stopped_enables_start_only(tab) -> None:
    tab._apply_state("stopped")
    assert tab._btn_start.isEnabled() is True
    assert tab._btn_stop.isEnabled() is False
    assert tab._btn_kill.isEnabled() is False


def test_apply_state__crashed_is_shown_in_red(tab) -> None:
    tab._apply_state("crashed")
    assert tab._state_lbl.text() == "已崩溃"
    assert "#f38ba8" in tab._state_lbl.styleSheet()


def test_apply_state__unknown_state_shows_itself(tab) -> None:
    """未知状态别糊成「未启动」。"""
    tab._apply_state("half-dead")
    assert tab._state_lbl.text() == "half-dead"


def test_exit_label_shows_the_code_only_when_there_is_one(tab) -> None:
    tab._exit_lbl.setText("exit=7")
    tab._apply_state("stopped")
    assert tab._exit_lbl.text() == ""
    tab._exit_lbl.setText("exit=7")
    mgr_exit = 2
    tab.mgr._exit = mgr_exit
    tab._apply_state("crashed")
    assert tab._exit_lbl.text() == "exit=2"


# ==================================================================
# 路径与告警
# ==================================================================

def test_refresh_paths__warning_is_hidden_when_everything_is_fine(tab) -> None:
    tab._refresh_paths()
    assert tab._warn.isHidden()


def test_refresh_paths__problems_disable_start_and_are_listed(qapp, tmp_path) -> None:
    m = _FakeManager(tmp_path, problems=["Python 无效", "找不到脚本"])
    t = E.EngineTab(m)
    try:
        assert t._btn_start.isEnabled() is False
        assert not t._warn.isHidden()
        text = t._warn.text()
        assert "Python 无效" in text and "找不到脚本" in text
        assert text.startswith("⚠ ")
    finally:
        t.deleteLater()


def test_refresh_paths__missing_runtime_dir_is_not_an_error(qapp, tmp_path) -> None:
    """runtime 目录是引擎启动时才建的 —— 不存在只是提示，不是错。"""
    m = _FakeManager(tmp_path, problems=[])
    t = E.EngineTab(m)
    try:
        assert t._btn_start.isEnabled() is True
        assert "尚不存在" in t._runtime_lbl.text()
    finally:
        t.deleteLater()


def test_refresh_paths__labels_are_selectable(tab) -> None:
    """路径要能复制走。"""
    from PySide6.QtCore import Qt

    assert tab._path_lbl.textInteractionFlags() & Qt.TextSelectableByMouse
    assert tab._runtime_lbl.textInteractionFlags() & Qt.TextSelectableByMouse


# ==================================================================
# 事件流
# ==================================================================

def test_append__plain_line_goes_in(tab) -> None:
    tab._append("普通事件")
    assert "普通事件" in _log(tab)


def test_append__colored_line_is_marked(tab) -> None:
    """★ 关键行必须带颜色，否则在几十行灰色里找不到。"""
    tab._append("✗ 启动失败，见日志")
    assert _first_char_color(tab) == "#f38ba8"


def test_append__clean_exit_is_not_marked(tab) -> None:
    """``code=0`` 是正常收尾，不该染红。"""
    tab._append("引擎退出（code=0）：正常结束")
    assert _first_char_color(tab) != "#f38ba8"


def _first_char_color(t) -> str:
    """取第一行第一个字符的前景色。

    ★ 不能用 ``block.charFormat()`` —— insertText 带格式插入时，
      格式挂在**文本片段**上而不是整块，所以块级读回来是默认色。
    """
    from PySide6.QtGui import QTextCursor

    doc = t._log.document()
    cur = QTextCursor(doc.findBlockByNumber(0))
    cur.movePosition(QTextCursor.MoveOperation.Right,
                     QTextCursor.MoveMode.KeepAnchor)
    return cur.charFormat().foreground().color().name()


def test_log_has_a_block_cap_so_memory_cannot_grow_forever(tab) -> None:
    from neko_console.channels.engine import MAX_LINES

    assert tab._log.maximumBlockCount() == MAX_LINES
    assert MAX_LINES > 0


def test_append__scrolls_to_the_bottom(tab) -> None:
    for i in range(50):
        tab._append(f"第 {i} 行")
    bar = tab._log.verticalScrollBar()
    assert bar.value() == bar.maximum()


# ==================================================================
# 动作按钮
# ==================================================================

def test_on_start__logs_both_steps(tab, mgr) -> None:
    tab._on_start()
    assert "start" in mgr.calls
    text = _log(tab)
    assert "请求启动引擎" in text and "已启动" in text


def test_on_start__reports_failure(tab, mgr, monkeypatch) -> None:
    # ★ lambda 要吃下 blocked_reason 关键字 —— 真实 start() 有这个形参。
    monkeypatch.setattr(mgr, "start", lambda *, blocked_reason=None: False)
    tab._on_start()
    assert "启动失败" in _log(tab)


def test_on_stop__logs_success(tab, mgr) -> None:
    tab._on_stop()
    assert "stop" in mgr.calls
    assert "已停止" in _log(tab)


def test_on_stop__warns_when_it_had_to_force(tab, mgr, monkeypatch) -> None:
    """★ 走 terminate 兜底时必须如实说 —— 按键状态可能不明。"""
    monkeypatch.setattr(mgr, "stop", lambda: False)
    tab._on_stop()
    assert "强制终止" in _log(tab)


def test_on_kill__always_tells_the_user_to_restart_the_game(tab, mgr) -> None:
    tab._on_kill()
    assert "kill" in mgr.calls
    assert "重启游戏" in _log(tab)


# ==================================================================
# 打开目录（绝不能在测试里真的弹资源管理器）
# ==================================================================

def test_open_dir__missing_directory_warns_instead_of_exploding(qapp, tmp_path) -> None:
    """★ 目录不存在时只提示，**绝不**去开资源管理器。"""
    ghost = tmp_path / "根本不存在的目录"
    t = E.EngineTab(_FakeManager(ghost))
    try:
        t._open_dir("path")
        assert "目录不存在" in t._log.toPlainText()
    finally:
        t.deleteLater()


def test_open_dir__does_not_call_startfile_when_missing(qapp, tmp_path,
                                                       monkeypatch) -> None:
    """本机是真 Windows —— 这条是在确认「打不开也绝不调 startfile」，
    否则测试会在 CI 上真的弹出资源管理器。"""
    called: list = []
    monkeypatch.setattr(E.os, "startfile", lambda p: called.append(p))
    t = E.EngineTab(_FakeManager(tmp_path / "幽灵"))
    try:
        t._open_dir("path")
        assert called == []
    finally:
        t.deleteLater()


def test_open_dir__failure_is_reported_not_raised(tab, monkeypatch, tmp_path) -> None:
    """打不开目录不能把主控台带崩。"""
    (tmp_path / "_runtime").mkdir()
    monkeypatch.setattr(E.os, "startfile",
                        lambda p: (_ for _ in ()).throw(OSError("拒绝访问")))
    tab._open_dir("path")
    assert "打开目录失败" in _log(tab)
    assert "拒绝访问" in _log(tab)