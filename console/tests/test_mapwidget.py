"""``tabs/mapwidget.py`` 单测（Console 侧，2026-10-12）。

离屏跑（``QT_QPA_PLATFORM=offscreen``），不显示窗口、不碰原神。

重点守三件事：

1. **遮蔽鼠标事件不自递归** —— 早先的写法在 ``_on_press`` 里直接调
   ``self._view.mousePressEvent(event)``，而实例属性已经遮蔽了类方法，
   实测直接 ``RecursionError``。这里真的发一个按下事件，跑通才算数。
2. **命中半径按屏幕像素** —— 按场景像素算的话，视图缩到 0.45 时
   3 场景像素只剩 1.3 屏幕像素，实测**根本点不中**。
3. **底图缺失时降级**，而不是抛异常把 tab 拉崩。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt   # noqa: E402
from PySide6.QtGui import QMouseEvent                     # noqa: E402
from PySide6.QtWidgets import QApplication                # noqa: E402

from neko_console.tabs import mapdata as M                # noqa: E402
from neko_console.tabs import mapwidget as W              # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture
def picker(qapp):
    p = W.MapPicker()
    yield p
    p.deleteLater()


def _point(pid: int = 900001, x: float = 1000.0, z: float = 2000.0,
           name: str = "测试锚点", ptype: str = "Goddess") -> M.TeleportPoint:
    return M.TeleportPoint(
        id=pid, name=name, type=ptype, country="测试国", areas=("测试区",),
        position=(x, 0.0, z), tran_position=(x, 0.0, z))


# ==================================================================
# 鼠标事件遮蔽：踩过的坑
# ==================================================================

def _mouse(kind, x: float = 10.0, y: float = 10.0):
    """造一个鼠标事件。

    ★ 用带 ``globalPos`` 的新版签名：少 ``localPos`` 的那个在 PySide6 里
      已标记弃用，每次跑都吐一条 DeprecationWarning。
    """
    return QMouseEvent(kind, QPointF(x, y), QPointF(x, y),
                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)


def test_mouse_event_override_does_not_recurse(picker) -> None:
    """★ 真的发一次按下事件。存原方法这层一去掉，这里就是 RecursionError。

    「构造时把原方法存下来」是纯代码约定，读代码看不出来；
    只有真的走一遍调用链才会发现它被写反了。
    """
    picker._view.mousePressEvent(_mouse(QEvent.MouseButtonPress))   # 不抛 = 对
    picker._view.mouseMoveEvent(_mouse(QEvent.MouseMove, 50, 50))


def test_base_methods_were_captured_before_overriding(picker) -> None:
    """存下来的必须是**类方法**，不能是被自己遮蔽后那个。"""
    assert picker._base_mouse_press is not picker._on_press
    assert picker._base_mouse_move is not picker._on_move
    assert picker._view.mousePressEvent == picker._on_press
    assert picker._view.mouseMoveEvent == picker._on_move


def test_mouse_move_over_map_updates_the_readout(picker) -> None:
    """鼠标移到图上要显示世界坐标。"""
    got: list[tuple[float, float]] = []
    picker.picked.connect(lambda x, z: got.append((x, z)))
    picker._view.mouseMoveEvent(_mouse(QEvent.MouseMove, 50, 50))
    assert isinstance(got, list)          # 不抛即可；坐标由 _world_at 单独测


# ==================================================================
# 坐标反算与越界
# ==================================================================

def test_world_at_maps_thumb_pixels_to_world(picker) -> None:
    tx, ty = 123.0, 456.0
    got = picker._world_at(QPointF(tx, ty))
    assert got is not None
    want = M.thumb_to_world(tx, ty)
    assert got[0] == pytest.approx(want[0])
    assert got[1] == pytest.approx(want[1])


def test_world_at_outside_the_map_returns_none(picker) -> None:
    """★ 点到地图外面不能给出坐标 —— 那是「猜」，会让角色往反方向走。"""
    assert picker._world_at(QPointF(-1, 500)) is None
    assert picker._world_at(QPointF(500, -1)) is None
    assert picker._world_at(QPointF(W.THUMB_WIDTH, 500)) is None
    assert picker._world_at(QPointF(500, W.THUMB_HEIGHT)) is None


def test_round_trip_through_the_picker_is_lossless(picker) -> None:
    """世界 -> 缩略图像素 -> 世界。抄错系数的话这里最先炸。"""
    wx, wz = 4634.52, 4687.13
    tx, ty = M.world_to_thumb(wx, wz)
    back = picker._world_at(QPointF(tx, ty))
    assert back is not None
    assert back[0] == pytest.approx(wx, abs=1e-3)
    assert back[1] == pytest.approx(wz, abs=1e-3)


# ==================================================================
# 命中测试：半径按屏幕像素
# ==================================================================

def test_hit_radius_follows_the_zoom_level(picker) -> None:
    """★ 缩放多少，「7 屏幕像素内点中」的手感都一样。

    早先按场景像素给半径，视图 fitInView 缩到 0.45 时 3 场景像素只剩
    1.3 屏幕像素，鼠标坐标还原回场景还有 ±8px 抖动 —— 实测点不中。
    """
    pt = _point(x=1000.0, z=2000.0)
    picker.set_points([pt])
    picker.set_filter("")              # 空过滤走 self._points，见下方「已知怪癖」
    tx, ty = M.world_to_thumb(*pt.xz)

    for scale in (0.45, 1.0, 3.0):
        picker._view.resetTransform()
        if scale != 1.0:
            picker._view.scale(scale, scale)
        s = picker._view.transform().m11()
        assert s == pytest.approx(scale, rel=1e-6)

        # ★ 取 5px / 20px，不取正好 7px —— 7.0/s 落点正好压在半径边界上，
        #   浮点算出来的距离可能差 1e-13 就翻成「没命中」。那种边界断言
        #   测的是浮点，不是缩放不变性。
        near = QPointF(tx + 5.0 / s, ty)
        far = QPointF(tx + 20.0 / s, ty)
        assert picker._point_at(near) is not None, f"scale={scale} 时 5px 内点不中"
        assert picker._point_at(far) is None, f"scale={scale} 时 20px 外竟然点中了"


def test_hit_returns_the_closest_point_when_several_are_near(picker) -> None:
    """两个点都在半径内时，取离鼠标更近的那个。"""
    a = _point(pid=1, x=1000.0, z=2000.0, name="甲")
    b = _point(pid=2, x=1004.0, z=2000.0, name="乙")
    picker.set_points([a, b])
    picker.set_filter("")

    # 贴到 b 上 -> 必须命中 b，而不是「列表里第一个」
    tx_b, ty_b = M.world_to_thumb(*b.xz)
    got = picker._point_at(QPointF(tx_b, ty_b))
    assert got is not None and got.id == b.id


def test_hit_ignores_points_hidden_by_the_filter(picker) -> None:
    """普通传送锚点不进默认视图 -> 在它原来的位置上也点不中。"""
    showable = _point(pid=1, x=1000.0, z=2000.0, name="神像", ptype="Goddess")
    hidden = _point(pid=2, x=3000.0, z=3000.0, name="普通传送点",
                    ptype="TeleportWaypoint")
    picker.set_points([showable, hidden])
    picker.set_filter("")
    # ★ 必须先归一：控件从没 show 过，fitInView 得到的缩放小得出奇
    #   （实测 ≈0.058），命中半径会被放大到 120+ 缩略图像素，
    #   隔着一百多像素也「点得中」。那是离屏假象，不是产品行为。
    picker._view.resetTransform()
    visible_ids = {p.id for p in picker.selected_points()}
    assert showable.id in visible_ids
    assert hidden.id not in visible_ids

    tx, ty = M.world_to_thumb(*hidden.xz)
    assert picker._point_at(QPointF(tx, ty)) is None


# ==================================================================
# 过滤
# ==================================================================

def test_empty_filter_only_shows_the_distinguishable_types(picker) -> None:
    """普通传送锚点 777 个，全画出来是糊的。"""
    pts = (_point(pid=1, name="神像", ptype="Goddess"),
           _point(pid=2, name="秘境", ptype="BlessDomain"),
           _point(pid=3, name="一次性", ptype="OneTimeDomain"),
           _point(pid=4, name="普通传送点", ptype="TeleportWaypoint"))
    picker.set_points(pts)
    names = {p.name for p in picker.selected_points()}
    assert "普通传送点" not in names
    assert "神像" in names


@pytest.mark.skipif(not M.load_teleport_points(), reason='真实传送点数据不可用 —— BetterGI tp.json 衍生数据，GPL-3.0，不随包分发；自备指引见 docs/ASSETS.md')
@pytest.mark.skipif(not M.load_teleport_points(), reason='真实传送点数据不可用 —— BetterGI tp.json 衍生数据，GPL-3.0，不随包分发；自备指引见 docs/ASSETS.md')
def test_text_filter_searches_the_real_dataset(picker) -> None:
    """★ 已知怪癖，这里钉住现状而不是假装它按 ``set_points`` 过滤。

    ``set_filter(非空)`` 走的是全局 ``search_points()``，读的是那 936 条
    真实数据，**不看** ``set_points()`` 传进来的集合。生产路径下两者
    本来就是同一份（构造时就 ``set_points(load_teleport_points())``），
    所以没出事；但「给控件喂一套点、再按名字筛它们」是做不到的。
    哪天要支持这个，得让 set_filter 在集合非空时先查集合。
    """
    picker.set_points([_point(pid=1, name="根本不存在的名字")])
    picker.set_filter("风起地")
    names = [p.name for p in picker.selected_points()]
    assert names, "真实数据里应当有「风起地」"
    assert all("风起地" in n or "风" in n for n in names), names[:5]
    assert "根本不存在的名字" not in names


def test_filter_is_capped(picker) -> None:
    """画几千个点会卡；封顶是刻意的。"""
    many = tuple(_point(pid=i, name=f"点{i}") for i in range(1, 601))
    picker.set_points(many)
    assert len(picker.selected_points()) == 400


def test_set_filter_emits_the_visible_list(picker) -> None:
    got: list = []
    picker.anchors_filtered.connect(got.append)
    picker.set_points([_point(pid=1, name="甲")])
    picker.set_filter("")
    assert got and got[-1][0].name == "甲"


def test_repeated_filters_do_not_accumulate_items(picker) -> None:
    """每次都要先清干净，否则点会越叠越多（也拖慢）。"""
    picker.set_points([_point(pid=1, name="甲")])
    for _ in range(5):
        picker.set_filter("")
    assert len(picker._point_items) <= 1


# ==================================================================
# 选中
# ==================================================================

def test_select_point_updates_the_readout(picker) -> None:
    pt = _point(x=1234.5, z=6789.0, name="风起地")
    picker.set_points([pt])
    picker.set_filter("")
    picker.select_point(pt)
    text = picker._readout.text()
    assert "风起地" in text
    assert "1234.5" in text and "6789.0" in text


def test_select_point_recolors_only_that_one(picker) -> None:
    a = _point(pid=1, name="甲")
    b = _point(pid=2, name="乙")
    picker.set_points([a, b])
    picker.set_filter("")
    picker.select_point(a)
    # 选中项用绿色刷，未选中的保持橙色
    assert picker._point_items[a.id].brush().color().name() != \
        picker._point_items[b.id].brush().color().name()


# ==================================================================
# 缩放与降级
# ==================================================================

class _Wheel:
    """只带 ``angleDelta()`` / ``position()`` / ``accept()`` 的假滚轮事件。

    ★ 为什么要补 ``position()``
      2026-10-16 前 ``wheelEvent`` 只读 ``angleDelta()``，把滚轮事件里的
      鼠标位置整个丢掉了，导致缩放锚点全靠 Qt 的 ``lastKnownMousePos``，
      鼠标贴边时视图会跳。假事件不提供 ``position()`` 正好掩盖了这个洞，
      补上之后这条测试才是真的在测生产路径。
      锚点那批用例在 ``test_map_zoom_anchor.py``，用的是**真** QWheelEvent。
    """

    def __init__(self, angle: int, pos: QPointF | None = None) -> None:
        self._a = angle
        self._p = pos or QPointF(0, 0)

    def angleDelta(self):
        return QPoint(0, self._a)

    def position(self):
        return self._p

    def accept(self) -> None:
        pass


def test_wheel_zoom_is_clamped(picker) -> None:
    """缩放夹在 [MIN_ZOOM, MAX_ZOOM]，1x = 全图、8x = 放大 8 倍。

    ★ 为什么不再断言 ``transform().m11()`` 本身
      2026-10-16 前 ``MAX_SCALE=8.0`` 是个**绝对** transform 值，
      而 ``fitInView`` 算出来的绝对值依赖控件尺寸 —— 实测恒为 0.024。
      于是 "最大 8.0" 实际是全图的 **330 倍**，"最小 0.2" 是全图的 1/8，
      滚轮一路滚出去就是一片空白。
      现在倍率改用 :meth:`MapPicker.zoom_factor`（**相对全图**）表达，
      它与控件尺寸无关，断言才有意义。
    """
    ev_in = _Wheel(+120)
    for _ in range(60):
        picker.wheelEvent(ev_in)
    assert picker.zoom_factor() <= W.MAX_ZOOM + 1e-6

    ev_out = _Wheel(-120)
    for _ in range(60):
        picker.wheelEvent(ev_out)
    assert picker.zoom_factor() >= W.MIN_ZOOM - 1e-6


def test_missing_thumbnail_degrades_instead_of_crashing(qapp, monkeypatch) -> None:
    """底图读不到时给提示 + 深色底，**不能抛**（那会把整个 tab 拉崩）。"""
    monkeypatch.setattr(W, "MAP_THUMB", r"D:\根本不存在\teyvat.png")
    p = W.MapPicker()
    try:
        assert p._thumb_ok is False
        assert "底图缺失" in p._hint.text()
        # 没有底图时手动选点仍然能用（只是没图可看）
        assert p._world_at(QPointF(500, 500)) is not None
    finally:
        p.deleteLater()


def test_scene_rect_matches_the_thumbnail(picker) -> None:
    r = picker._scene.sceneRect()
    assert r.width() == W.THUMB_WIDTH
    assert r.height() == W.THUMB_HEIGHT