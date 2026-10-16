"""``tabs/pathfind.py`` 单测（Console 侧，2026-10-12）。

离屏跑（``QT_QPA_PLATFORM=offscreen``），用假 channel / 假 engine，不起子进程、
不碰原神。

★ 重点守两条

1. **拿不到坐标就说不** —— 起点解析失败必须给一句人话的错误，
   绝不能悄悄回一个 ``(0,0)``。猜错坐标 = 角色一路往反方向走。
2. **传送点用 ``tran_xz``（传送后落点），不是 ``xz``（图标所在格子）**
   —— 这两个不是同一个点。用错了角色会走到雕像格子而不是站位上。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Qt, Signal    # noqa: E402
from PySide6.QtWidgets import QApplication         # noqa: E402

from neko_console.tabs import mapdata as M          # noqa: E402
from neko_console.tabs import pathfind as T         # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _FakeChannel(QObject):
    """PathfindTab 用到的 channel 接口。"""

    state_changed = Signal(str)

    def __init__(self, state: str = "stopped") -> None:
        super().__init__()
        self._state = state

    def state(self) -> str:
        return self._state

    def is_running(self) -> bool:
        return self._state == "running"

    def exit_code(self):
        return None

    def last_error(self) -> str:
        return ""

    def path_problems(self) -> list[str]:
        return []

    def read_state(self):
        return None

    def read_recent_events(self, limit=12):
        return []

    def start(self, **kw):
        return True, "ok"

    def stop(self, **kw):
        return True, "ok"


class _FakeEngine:
    def __init__(self, running: bool = False) -> None:
        self._running = running

    def is_running(self) -> bool:
        return self._running


@pytest.fixture
def tab(qapp):
    t = T.PathfindTab(_FakeChannel(), _FakeEngine())
    yield t
    t.deleteLater()


def _anchor(x=1000.0, y=0.0, z=2000.0, tx=1010.0, tz=2010.0,
            name="风起地", pid=1) -> M.TeleportPoint:
    return M.TeleportPoint(id=pid, name=name, type="Goddess", country="蒙德",
                           areas=("风起地",), position=(x, y, z),
                           tran_position=(tx, y, tz))


# ==================================================================
# 纯函数
# ==================================================================

def test_text_hint__ambiguous_names_point_at_the_map() -> None:
    """同名/多候选时，提示应该是「自己在地图上挑」。"""
    spec = M.TargetSpec("unresolved", entity_type="有 6 个同名地点")
    assert "地图" in T.text_hint(spec)


def test_text_hint__generic_failure_says_what_is_supported() -> None:
    spec = M.TargetSpec("unresolved")
    assert "坐标" in T.text_hint(spec)


# ==================================================================
# 坐标校验
# ==================================================================

def test_parse_xy__empty_says_what_to_fill(tab) -> None:
    tab._target_edit.setText("   ")
    val, err = tab._parse_xy(tab._target_edit, "终点")
    assert val is None and "请填写终点" in err


def test_parse_xy__three_parts_is_rejected(tab) -> None:
    """★ 早先只取前两段，``"1,2,3"`` 会被静默当成 (1,2)。

    用户以为填了三样东西，实际按前两个跑 —— 「悄悄按错的坐标走」
    比直接报错糟得多。
    """
    tab._target_edit.setText("1,2,3")
    val, err = tab._parse_xy(tab._target_edit, "终点")
    assert val is None and "格式" in err


def test_parse_xy__non_numeric_is_rejected(tab) -> None:
    tab._target_edit.setText("abc,2")
    val, err = tab._parse_xy(tab._target_edit, "终点")
    assert val is None and "数字" in err


def test_parse_xy__chinese_comma_is_normalised_to_ascii(tab) -> None:
    """★ 用户打中文输入法出来的是全角逗号。

    必须**归一化后再发给脚本**：e2e_pathfind 的 ``_pair`` 是
    ``float(s.split(",")[0]), float(s.split(",")[1])``，全角逗号 split 出来
    只有一个元素 -> IndexError -> 脚本 exit 2。症状是「本机校验通过，
    点启动后进程 1 秒就没了」。
    """
    tab._target_edit.setText("4634.52，4687.13")
    val, err = tab._parse_xy(tab._target_edit, "终点")
    assert err == ""
    assert val == "4634.52,4687.13"          # 半角，脚本才认得
    assert "，" not in val


def test_parse_xy__spaces_are_stripped_losslessly(tab) -> None:
    tab._target_edit.setText("4634.52 , 4687.13")
    val, err = tab._parse_xy(tab._target_edit, "终点")
    assert err == "" and val == "4634.52,4687.13"


# ==================================================================
# 起点解析：拿不到就说不
# ==================================================================

def test_resolve_start__manual_valid(tab) -> None:
    tab._src_combo.setCurrentIndex(0)            # MANUAL
    tab._start_edit.setText("1,2")
    got, err = tab._resolve_start()
    assert err == ""
    assert got == "1.00, 2.00"


def test_resolve_start__manual_empty_explains_what_to_do(tab) -> None:
    tab._src_combo.setCurrentIndex(0)
    tab._start_edit.setText("")
    got, err = tab._resolve_start()
    assert got is None
    assert "填写起点" in err or "地图上点" in err


def test_resolve_start__anchor_source_without_pick(tab) -> None:
    """切到「从地图选点」但还没点 -> 说清楚，不给 (0,0)。"""
    tab._src_combo.setCurrentIndex(1)            # ANCHOR
    if hasattr(tab, "_pending_target_anchor"):
        del tab._pending_target_anchor
    got, err = tab._resolve_start()
    assert got is None and "点一个起点" in err


def test_resolve_start__unavailable_source_explains_why(tab) -> None:
    """预留来源要说明「为什么现在不行」，而不是静默失败。"""
    idx = tab._src_combo.findData(M.StartSource.LOCALIZATION)
    assert idx >= 0, "下拉里应当有 LOCALIZATION 这一项（置灰但可选中）"
    tab._src_combo.setCurrentIndex(idx)
    got, err = tab._resolve_start()
    assert got is None
    assert "定位服务" in err and "原点" in err


def test_resolve_start__manual_source_does_not_need_a_pick(tab) -> None:
    """手动来源时那个残留的锚点不该被拿来用。"""
    tab._src_combo.setCurrentIndex(0)
    tab._start_edit.setText("7,8")
    tab._pending_target_anchor = _anchor()
    got, err = tab._resolve_start()
    assert err == "" and got == "7.00, 8.00"


# ==================================================================
# 来源切换
# ==================================================================

def test_source_change_toggles_the_manual_field(tab) -> None:
    """选了非手动来源，手动输入框就该禁用 —— 别让人填了却不生效。"""
    tab._src_combo.setCurrentIndex(0)
    tab._on_source_changed()
    assert tab._start_edit.isEnabled() is True

    tab._src_combo.setCurrentIndex(1)
    tab._on_source_changed()
    assert tab._start_edit.isEnabled() is False
    assert "禁用" in tab._start_edit.toolTip()


def test_unavailable_sources_are_disabled_in_the_combo(tab) -> None:
    """预留来源要**看得见但点不动** —— 看不见用户不知道有这回事。"""
    model = tab._src_combo.model()
    for src in (M.StartSource.SCREENSHOT_OCR, M.StartSource.LOCALIZATION):
        idx = tab._src_combo.findData(src)
        assert idx >= 0, f"{src} 应当出现在下拉里"
        item = model.item(idx)
        assert item is not None, f"{src} 在下拉里没有对应的 item"
        assert not (item.flags() & Qt.ItemIsEnabled), \
            f"{src} 还没实现，应当是禁用状态"
        # 选中它也要能拿到原因（_resolve_start 靠这个给用户解释）
        assert src.unavailable_reason


# ==================================================================
# 地图交互
# ==================================================================

def test_map_pick__manual_source_fills_the_start_box(tab) -> None:
    tab._src_combo.setCurrentIndex(0)
    tab._on_map_pick(123.456, 789.0)
    assert tab._start_edit.text() == "123.46, 789.00"


def test_map_pick__non_manual_source_is_remembered(tab) -> None:
    tab._src_combo.setCurrentIndex(1)
    tab._on_map_pick(123.456, 789.0)
    assert getattr(tab, "_pending_pick", None) == (123.456, 789.0)


def test_map_anchor__uses_the_landing_spot_not_the_icon(tab) -> None:
    """★★ 用 ``tran_xz``，不是 ``xz``。

    传送点的图标格子和角色实际站的地方不是同一个点。写错了角色会
    走到雕像格子上，看着「到了」，实际站位是偏的。
    """
    pt = _anchor(x=1000.0, z=2000.0, tx=1010.0, tz=2010.0)
    assert pt.xz != pt.tran_xz                       # 前提：两者确实不同
    tab._on_map_anchor(pt)
    assert tab._target_edit.text() == "1010.00, 2010.00"


def test_search_with_no_query_says_showing_defaults(tab) -> None:
    tab._search_edit.setText("")
    tab._on_search()
    assert "默认" in tab._map._readout.text()


def test_search_with_no_match_says_so(tab) -> None:
    tab._search_edit.setText("绝对不存在的地名zzz")
    tab._on_search()
    assert "没匹配" in tab._map._readout.text()


# ==================================================================
# 目标描述（任务 C）
# ==================================================================

def test_desc__coordinate_goes_straight_in(tab) -> None:
    tab._desc_edit.setText("4634.52,4687.13")
    tab._on_desc_submit()
    assert tab._target_edit.text() == "4634.52, 4687.13"
    assert "✓" in tab._map._readout.text()


def test_desc__unique_anchor_uses_the_landing_spot(tab) -> None:
    """★ 走到 anchor 分支时，终点必须填 ``tran_xz``（传送后落点）。

    先在真实数据里找一个**确实唯一**的地名 —— 「七天神像-风」那种同名
    的会走 unresolved 分支，测不到这里。
    """
    unique = None
    for p in M.load_teleport_points()[:120]:
        spec = M.resolve_target_from_description(p.name)
        if spec.kind == "anchor" and spec.anchor is not None \
                and spec.anchor.id == p.id:
            unique = (p, spec)
            break
    if unique is None:
        pytest.skip("前 120 条里没找到唯一地名（数据集结构变了？）")
    p, spec = unique

    tab._desc_edit.setText(p.name)
    tab._on_desc_submit()
    assert tab._target_edit.text() == M.fmt_xz(*p.tran_xz)
    assert tab._target_edit.text() != M.fmt_xz(*p.xz) or p.xz == p.tran_xz


def test_desc__ambiguous_name_is_not_guessed(tab) -> None:
    """★ 同名必须交回用户挑，不能随便选一个。"""
    # 「七天神像-风」在真实数据里有 6 个同名
    if not M.load_teleport_points():
        pytest.skip('真实传送点数据不可用 —— BetterGI tp.json 衍生数据，GPL-3.0，不随包分发；自备指引见 docs/ASSETS.md')
    if not M.load_teleport_points():
        pytest.skip('真实传送点数据不可用 —— BetterGI tp.json 衍生数据，GPL-3.0，不随包分发；自备指引见 docs/ASSETS.md')
    spec = M.resolve_target_from_description("七天神像-风")
    if spec.kind != "unresolved":
        pytest.skip("本机数据里「七天神像-风」不再同名，跳过")
    tab._desc_edit.setText("七天神像-风")
    tab._on_desc_submit()
    assert "✗" in tab._map._readout.text()
    assert "地图" in tab._map._readout.text()          # 提示自己去挑


def test_apply_target__unresolved_leaves_the_box_alone(tab) -> None:
    """解析不出来时**不要**动终点框 —— 留着用户原来填的。"""
    tab._target_edit.setText("原来的值")
    tab._apply_target(M.TargetSpec("unresolved"))
    assert tab._target_edit.text() == "原来的值"


# ==================================================================
# 权限护栏
# ==================================================================

def test_start_button_is_disabled_without_admin(qapp) -> None:
    """★ 非管理员时 e2e_pathfind 的 UIPI 门禁会 3 秒 exit 2。

    症状只是「进程闪退」，不提前禁用的话没人能联想到权限。
    """
    t = T.PathfindTab(_FakeChannel(), _FakeEngine(), is_admin=False)
    try:
        assert t._btn_start.isEnabled() is False
        assert t._btn_start.toolTip() or t._guard_lbl.text()
    finally:
        t.deleteLater()


def test_start_button_is_enabled_with_admin(qapp) -> None:
    t = T.PathfindTab(_FakeChannel(), _FakeEngine(), is_admin=True)
    try:
        assert t._btn_start.isEnabled() is True
    finally:
        t.deleteLater()


def test_start_refused_when_the_engine_is_running(qapp) -> None:
    """护栏摆在按钮这一层：引擎在跑时根本不让点。"""
    t = T.PathfindTab(_FakeChannel(), _FakeEngine(running=True))
    try:
        t._refresh()
        assert t._btn_start.isEnabled() is False
    finally:
        t.deleteLater()