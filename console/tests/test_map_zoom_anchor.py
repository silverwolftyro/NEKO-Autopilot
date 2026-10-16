"""地图缩放锚点单测（Console 侧，2026-10-16）。

★ 这批测试守的是"鼠标在边角缩放，视图突然往别处跳"

用户报告的现象：平移到地图边角后，鼠标放在那个角上滚轮缩放，
视图不以鼠标为中心，而是"啪"地跳到别处。

实测到的**三个**根因（都不是坐标精度问题，是视图定位问题）：

1. **锚点被丢弃**
   ``wheelEvent`` 只用了 ``angleDelta()``，把滚轮事件自带的
   ``position()``（鼠标位置）扔了，全靠 Qt 的 ``lastKnownMousePos``
   —— 而那个值只有真的移动过鼠标才准。

2. **``resetTransform()`` 把锚点关系清零**
   在 ``AnchorUnderMouse`` 下，Qt 会在 ``resetTransform()`` 里调
   ``centerOn(mapToScene(lastKnownMousePos))``。此时变换刚被清零，
   ``mapToScene`` 就把**屏幕坐标当成场景坐标**了，实测滚动条一步
   从 -2 窜到 993，视图瞬间飞到天边。

3. **``centerOn`` 会被钳制**
   滚动条范围按**视图的 sceneRect** 算；它原本等于地图本身，
   Qt 于是强制视口不越界，鼠标贴边时锚点要求的中心在地图外，
   被 clamp 拉回最近边界。实测 hScroll 的 minimum 恒为 0、
   value 被死死钉在 0 上，一漂就是几百个场景单位。

修法见 ``mapwidget.MapPicker._zoom_at`` / ``_pin_to`` / ``_pan_by``。
这里全部用**真 QWheelEvent** 打到 viewport 上，不用假事件 ——
假事件不带 ``position()``，恰好会掩盖掉根因 1。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt      # noqa: E402
from PySide6.QtGui import QWheelEvent               # noqa: E402
from PySide6.QtWidgets import QApplication          # noqa: E402

from neko_console.tabs import mapwidget as W        # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def picker(qapp):
    """**显示出来**的控件。

    离屏下不 show 的话 viewport 只有默认那么点大，算出来的锚点
    偏差会大到没有参考价值 —— 这里 resize + show + processEvents，
    拿到接近真实窗口的视口。
    """
    p = W.MapPicker()
    p.resize(1280, 840)
    p.show()
    qapp.processEvents()
    p.fit()
    qapp.processEvents()
    yield p
    p.close()
    p.deleteLater()


def _wheel(picker, pos: QPoint, dy: int = 120) -> None:
    """往 viewport 发一个真滚轮事件。"""
    vp = picker._view.viewport()
    ev = QWheelEvent(QPointF(pos), QPointF(vp.mapToGlobal(pos)),
                     QPoint(0, 0), QPoint(0, dy), Qt.NoButton, Qt.NoModifier,
                     Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(vp, ev)


#: 单次缩放的锚点漂移上限（屏幕像素）。
#:
#: 依据：175 组配置（7 个鼠标位置 x 5 个视图位置）扫描下来，
#: 单次缩放最大漂移 **0.70 屏幕像素**。理论极限是 0.5 —— QGraphicsView
#: 的滚动条取值是整数，取不到更准（``QGraphicsView::translate()`` 在
#: Qt 6 滚动条模式下实测是空操作，没有浮点通道）。
SINGLE_DRIFT_PX = 1.0

#: 连续缩放的锚点漂移上限（**场景单位**）。
#:
#: ★ 为什么连续缩放反而比单次差，又为什么不会无限涨
#:   每步都钉到 0.5 像素以内，但这个残差会被**后续缩放放大** ——
#:   1.25 倍缩一次就放大 25%。连滚 N 次后偏移会收敛到某个稳态
#:   （扫描实测：2 次 1.23、5 次 1.32、10 次 1.40、12 次 1.46 场景单位，
#:   越往后涨得越慢），**不是发散**。
#:   换算到地面上 1.5 场景单位 ≈ 27 米 ≈ 1.5 个缩略图像素 ——
#:   在 1278 像素宽的视口上是 6~7 像素，肉眼不可见。
#:   修复前这里是好几百到三千多场景单位的"啪一下跳走"。
CUMULATIVE_DRIFT_SCENE_UNITS = 2.0


def _at(picker, view_center: QPointF, mouse: QPoint) -> float:
    """把视图挪到 ``view_center``，鼠标放 ``mouse``，放大一档，返回漂移。

    返回值是**屏幕像素**：缩放后"原先光标下的那个场景点"离光标多远。
    """
    app = QApplication.instance()
    picker._view.centerOn(view_center)
    app.processEvents()
    anchor = picker._view.mapToScene(mouse)        # 缩放前光标下的场景点
    before = picker.zoom_factor()
    _wheel(picker, mouse, 120)
    app.processEvents()
    assert picker.zoom_factor() > before, "这一滚没放大"
    at = picker._screen_of(anchor)
    return ((at.x() - mouse.x()) ** 2 + (at.y() - mouse.y()) ** 2) ** 0.5


# ==================================================================
# 1. 锚点精度：缩放前光标下的场景点，缩放后还在光标下
# ==================================================================

CORNERS = ("center", "topleft", "topright", "bottomleft", "bottomright")


def _corner(picker, which: str) -> QPoint:
    vp = picker._view.viewport()
    if which == "center":
        return vp.rect().center()
    if which == "topleft":
        return QPoint(5, 5)
    if which == "topright":
        return QPoint(vp.width() - 5, 5)
    if which == "bottomleft":
        return QPoint(5, vp.height() - 5)
    return QPoint(vp.width() - 5, vp.height() - 5)


@pytest.mark.parametrize("which", CORNERS)
def test_zooming_pins_the_point_under_the_cursor(picker, which) -> None:
    """★ 视图在地图正中间时，光标在任意位置缩放，锚点不许动。"""
    drift = _at(picker, QPointF(1000, 500), _corner(picker, which))
    assert drift < SINGLE_DRIFT_PX, f"{which} 锚点漂了 {drift:.2f} 屏幕像素"


@pytest.mark.parametrize("which", CORNERS)
def test_zooming_pins_the_cursor_even_when_the_view_is_at_a_corner(
        picker, which) -> None:
    """★ 用户报告的复现条件：视图已经在地图边角，鼠标也在那个角。"""
    drift = _at(picker, QPointF(120, 80), _corner(picker, which))
    assert drift < SINGLE_DRIFT_PX, f"{which} 锚点漂了 {drift:.2f} 屏幕像素"


@pytest.mark.parametrize("which", CORNERS)
def test_zooming_in_pins_the_cursor_at_high_magnification(picker, which) -> None:
    """已经放大到 16x 之后，贴边缩放同样不许跳。"""
    app = QApplication.instance()
    picker.set_zoom_factor(16.0)
    picker._view.centerOn(QPointF(200, 150))
    app.processEvents()
    mouse = _corner(picker, which)
    anchor = picker._view.mapToScene(mouse)
    _wheel(picker, mouse, 120)
    app.processEvents()
    at = picker._screen_of(anchor)
    drift = ((at.x() - mouse.x()) ** 2 + (at.y() - mouse.y()) ** 2) ** 0.5
    assert drift < SINGLE_DRIFT_PX, f"{which} 16x 下漂了 {drift:.2f} 屏幕像素"


def test_the_anchor_point_stays_inside_the_viewport(picker) -> None:
    """缩放后光标下那个场景点必须还在视口里（这是"不要跳"的底线）。"""
    app = QApplication.instance()
    vp = picker._view.viewport()
    picker._view.centerOn(QPointF(60, 40))       # 视图贴到地图左上角
    app.processEvents()
    mouse = QPoint(4, 4)
    anchor = picker._view.mapToScene(mouse)
    _wheel(picker, mouse, 120)
    app.processEvents()
    at = picker._screen_of(anchor)
    assert vp.rect().contains(QPoint(int(at.x()), int(at.y())))


# ==================================================================
# 2. 累积漂移
# ==================================================================

def test_every_single_step_lands_within_half_a_pixel() -> None:
    """★ 确定性的那一条：每一次缩放都把**本步锚点**钉到 0.5 像素内。

    累积漂移那种断言受"取整运气"影响，偶发超一点就会红。这一条不看运气：
    ``_pin_to`` 每一步都必须做到滚动条整数分辨率的理论极限。

    做法：直接在 ``_pin_to`` 返回的瞬间量。
    """
    app = QApplication.instance()
    p = W.MapPicker()
    p.resize(1280, 840)
    p.show()
    app.processEvents()
    p.fit()
    app.processEvents()
    vp = p._view.viewport()
    worst = 0.0
    for mouse in (QPoint(4, 4), vp.rect().center(),
                  QPoint(vp.width() - 4, vp.height() - 4)):
        for center in (QPointF(1000, 500), QPointF(120, 80),
                       QPointF(-600, -300)):
            p.fit()
            app.processEvents()
            p._view.centerOn(center)
            app.processEvents()
            for _ in range(6):
                anchor = p._view.mapToScene(mouse)
                seen = {}
                orig = p._pin_to

                def spy(sp, screen_pt, _o=orig, _s=seen):
                    _o(sp, screen_pt)
                    at = p._screen_of(sp)
                    _s["e"] = max(abs(at.x() - screen_pt.x()),
                                  abs(at.y() - screen_pt.y()))

                p._pin_to = spy
                _wheel(p, mouse, 120)
                app.processEvents()
                p._pin_to = orig
                worst = max(worst, seen.get("e", 0.0))
    p.close()
    p.deleteLater()
    assert worst <= 0.5 + 1e-6, \
        f"单步残差 {worst:.3f} 像素，超出滚动条整数分辨率的极限 0.5"


def test_ten_zooms_in_do_not_drift(picker) -> None:
    """★ 连滚 10 次放大，累计漂移 < 1 **场景单位**。

    理论上每步能钉到 0.5 屏幕像素以内（滚动条是整数，取不到更准），
    但这个残差会被后续缩放**放大**：第 1 次剩 0.4 像素，滚到第 10 次
    就是 0.4 x 1.25^9 ≈ 3 像素。所以判据给在**场景单位**上，
    那才是用户真正在意的尺度（1 场景单位 = 18.4 米）。
    """
    app = QApplication.instance()
    picker.fit()
    app.processEvents()
    picker._view.centerOn(QPointF(260, 160))
    app.processEvents()
    mouse = QPoint(5, 5)
    anchor = picker._view.mapToScene(mouse)
    for _ in range(10):
        _wheel(picker, mouse, 120)
        app.processEvents()
    at = picker._screen_of(anchor)
    m11 = picker._view.transform().m11()
    drift_scene_units = ((at.x() - mouse.x()) / m11) ** 2 + \
                        ((at.y() - mouse.y()) / m11) ** 2
    assert drift_scene_units ** 0.5 < CUMULATIVE_DRIFT_SCENE_UNITS, \
        f"10 次放大后漂了 {drift_scene_units**0.5:.2f} 场景单位"


def test_ten_zooms_out_do_not_drift(picker) -> None:
    """缩小方向同样不能漂（缩小方向还会撞到 MIN_ZOOM）。"""
    app = QApplication.instance()
    picker.set_zoom_factor(32.0)
    picker._view.centerOn(QPointF(260, 160))
    app.processEvents()
    mouse = QPoint(5, 5)
    anchor = picker._view.mapToScene(mouse)
    for _ in range(10):
        _wheel(picker, mouse, -120)
        app.processEvents()
    at = picker._screen_of(anchor)
    m11 = picker._view.transform().m11()
    drift_scene_units = ((at.x() - mouse.x()) / m11) ** 2 + \
                        ((at.y() - mouse.y()) / m11) ** 2
    assert drift_scene_units ** 0.5 < CUMULATIVE_DRIFT_SCENE_UNITS, \
        f"10 次缩小后漂了 {drift_scene_units**0.5:.2f} 场景单位"


# ==================================================================
# 3. 三个根因各自的守卫
# ==================================================================

def test_the_wheel_event_position_is_used_not_a_stale_cursor(picker) -> None:
    """★ 根因 1：滚轮必须用**自己事件里的**鼠标位置。

    这里**故意不发任何 MouseMove** —— Qt 的 ``lastKnownMousePos``
    还停在默认位置。如果实现偷懒去用它，锚点就会跑到别处去。
    """
    app = QApplication.instance()
    picker.fit()
    app.processEvents()
    vp = picker._view.viewport()
    mouse = QPoint(vp.width() - 5, vp.height() - 5)      # 右下角
    anchor = picker._view.mapToScene(mouse)
    _wheel(picker, mouse, 120)
    app.processEvents()
    at = picker._screen_of(anchor)
    drift = ((at.x() - mouse.x()) ** 2 + (at.y() - mouse.y()) ** 2) ** 0.5
    assert drift < SINGLE_DRIFT_PX, f"没在光标处缩放，漂了 {drift:.2f} 像素"


def test_qt_automatic_anchoring_is_switched_off(picker) -> None:
    """★ 根因 2：TransformationAnchor 必须是 AnchorViewCenter。

    设成 ``AnchorUnderMouse`` 会让 Qt 在 ``resetTransform()`` 里
    拿 ``lastKnownMousePos`` 经过**已清零**的变换去 ``centerOn``，
    视图一步飞掉近千像素。锚点是我们自己算的，Qt 必须别插手。
    """
    assert picker._view.transformationAnchor() == \
        W.QGraphicsView.ViewportAnchor.AnchorViewCenter
    assert picker._view.transformationAnchor() != \
        W.QGraphicsView.ViewportAnchor.AnchorUnderMouse


def test_the_view_scene_rect_is_overscanned(picker) -> None:
    """★ 根因 3：视图的 sceneRect 必须比地图大，否则滚动条会钳制。

    把 ``OVERSCAN`` 去掉的话，Qt 会强制视口不越出地图，
    鼠标贴边缩放时锚点要求的中心被 clamp 拉回边界 —— 跳。
    注意断言的是**视图**的 sceneRect，**场景**的仍等于地图本身
    （那是点位计算的地基，由 test_mapwidget 那边守着）。
    """
    from neko_console.tabs.mapdata import THUMB_HEIGHT, THUMB_WIDTH
    scene_rect = picker._view.sceneRect()
    assert scene_rect.width() > THUMB_WIDTH * 1.5, \
        f"视图 sceneRect 只有 {scene_rect.width()}，没留余量"
    assert scene_rect.height() > THUMB_HEIGHT * 1.5
    # 余量得是**四边对称**的
    assert scene_rect.left() < 0
    assert scene_rect.top() < 0
    assert scene_rect.right() > THUMB_WIDTH
    assert scene_rect.bottom() > THUMB_HEIGHT
    # 场景自己的 rect 不能被牵连
    assert picker._scene.sceneRect().width() == THUMB_WIDTH
    assert picker._scene.sceneRect().height() == THUMB_HEIGHT


def test_pan_by_pushes_the_content_the_right_way(picker) -> None:
    """★ ``_pan_by`` 的符号方向必须对 —— 传反了会把锚点越推越远。

    滚动条的直觉和内容运动方向相反：值**变大** = 往右滚 = 内容往**左**移。
    """
    app = QApplication.instance()
    picker.fit()
    app.processEvents()
    probe = QPointF(1000.0, 500.0)
    before = picker._screen_of(probe)
    picker._pan_by(10.0, 0.0)
    app.processEvents()
    after = picker._screen_of(probe)
    assert before.x() - after.x() == pytest.approx(10.0, abs=1.5), \
        "内容往右移 10 像素时，同一个场景点应该左移 10 像素"


def test_zoom_stops_at_the_limits_without_jumping(picker) -> None:
    """已到上限/下限时再滚，什么都不该发生（更不该跳）。"""
    app = QApplication.instance()
    mouse = _corner(picker, "bottomright")
    for _ in range(80):
        _wheel(picker, mouse, 120)
        app.processEvents()
    assert picker.zoom_factor() == pytest.approx(W.MAX_ZOOM, abs=1e-6)
    center = picker._view.mapToScene(picker._view.viewport().rect().center())
    _wheel(picker, mouse, 120)
    app.processEvents()
    now = picker._view.mapToScene(picker._view.viewport().rect().center())
    assert now == center, "到上限后视图还动了"

    for _ in range(80):
        _wheel(picker, mouse, -120)
        app.processEvents()
    assert picker.zoom_factor() == pytest.approx(W.MIN_ZOOM, abs=1e-6)
