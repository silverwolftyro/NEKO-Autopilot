"""地图定位精度单测（Console 侧，2026-10-16）。

★ 这批测试守的是"用户截图里绿标记跟金点对不上"那个问题。

背景与口径（重要，别误读成"坐标不准"）
------------------------------------------
实测过 936 个传送点，:func:`world_to_pixel` **变换本身是对的**：

  * 越界 0 个（px 8266..28038 / 图宽 36864，py 766..17922 / 图高 18432）
  * 99.9% 落在陆地上（拿 ``P0_pyramid/L4_full.png`` 当海陆掩膜判定）
  * ``position`` 与 ``tranPosition`` 平均只差 7.42 米 = 0.40 缩略图像素

所以四个候选根因（矩阵不对 / 缩略图尺寸不匹配 / 用错 tranPosition / 轴搞反）
**一个都不成立**。真正让用户觉得"偏了"的是两件事：

1. **分辨率**：缩略图 2000px 覆盖 36864 世界像素 = **18.4 米/像素**。
   8x 放大只是把这 18.4 米的格子拉大，不是新信息 —— 于是"看着像偏了"。
   修法是分级加载（见 ``mapwidget._LevelPyramid``），不是改系数。
2. **绘制单位**：金点半径老代码写死 ``r=3.0`` **场景单位** = 55 米真实大小，
   选中标记 ``r=9.0`` = 166 米。低倍看不出来，放大后两者跟着 transform
   一起涨，32x 时绿环半径 288 屏幕像素把一堆金点全吞进去 —— 那才叫对不上。

下面 :func:`test_drawing_uses_screen_pixels_at_every_zoom` 和
:func:`test_marker_center_stays_locked_on_its_point` 守第 2 条，
:func:`test_every_teleport_point_lands_on_land` 守第 1 条（连带守住轴没搞反）。
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt                # noqa: E402
from PySide6.QtGui import QImage                      # noqa: E402
from PySide6.QtWidgets import QApplication             # noqa: E402

from neko_console.tabs import mapdata as M             # noqa: E402
from neko_console.tabs import mapwidget as W           # noqa: E402

#: 底图资产根（跨项目）。读不到就把依赖资产的测试 skip 掉，
#: 不让可选的地图资产变成 Console 跑测试的硬依赖。
_MAPS = Path(M.MAP_THUMB).parent
_L4 = _MAPS / "P0_pyramid" / "L4_full.png"

#: 金标准：世界坐标 -> 全分辨率像素。
#: 这是**实测值**（手算仿射公式核对过）。作用是钉死系数：
#: 哪天有人把 _A/_B/_C… 抄错、或者把 x/z 轴对调，这里立刻炸。
_GOLDEN_WORLD = (4634.52, 4687.13)
_GOLDEN_PIXEL = (18772.90952455237, 6613.968404798123)


# ==================================================================
# fixtures
# ==================================================================

@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def all_points() -> tuple[M.TeleportPoint, ...]:
    """真实数据集里的全部传送点（936 条）。

    ★ 没有就 skip，不要让"本机没自备数据"表现成"功能坏了"。
    """
    pts = M.load_teleport_points()
    if not pts:
        pytest.skip("真实传送点数据不可用 —— BetterGI tp.json 衍生数据，GPL-3.0，不随包分发；自备指引见 docs/ASSETS.md")
    return pts


@pytest.fixture
def picker(qapp):
    if not (_MAPS / "P0_pyramid" / "chunks_index.json").is_file():
        pytest.skip("底图资产不可用 —— 米游社版权 / 体量过大，不随包分发；自备指引见 docs/ASSETS.md")
    p = W.MapPicker()
    yield p
    p.deleteLater()


# ==================================================================
# 1. 仿射系数没被抄错、轴没被对调
# ==================================================================

def test_world_to_pixel_matches_the_golden_sample() -> None:
    """世界坐标 -> 像素必须落在实测金标准上（容差 1 微米）。"""
    got = M.world_to_pixel(*_GOLDEN_WORLD)
    assert got[0] == pytest.approx(_GOLDEN_PIXEL[0], abs=1e-6)
    assert got[1] == pytest.approx(_GOLDEN_PIXEL[1], abs=1e-6)


def test_the_two_axes_are_not_swapped() -> None:
    """★ 历史上踩过"底图相对世界坐标是转置的（px~z, py~x）"。

    提瓦特不是正方形地图，但仿射矩阵里 x 方向的平移量 23460 与
    z 方向的 11242 差了 2 倍以上 —— 一旦轴对调，这俩值会对调。
    这里钉住：原点映射到 (23460.21, 11242.58)，也就是 **x 大 y 小**。
    """
    px, py = M.world_to_pixel(0.0, 0.0)
    assert px == pytest.approx(23460.212244, abs=1e-6)
    assert py == pytest.approx(11242.579297, abs=1e-6)
    assert px > py, "x/z 轴被对调了"


def test_pixel_round_trip_is_lossless_for_every_point(all_points) -> None:
    """936 个点逐个 pixel -> world -> pixel 往返，误差 < 1 微米。

    往返无损 = 矩阵可逆 = 反算（点选、路径规划回读）不会越漂。
    """
    assert all_points, "传送点数据集是空的，测不了"
    worst = 0.0
    for p in all_points:
        px, py = M.world_to_pixel(*p.xz)
        wx, wz = M.pixel_to_world(px, py)
        worst = max(worst, abs(wx - p.xz[0]), abs(wz - p.xz[1]))
    assert worst < 1e-6, f"往返最大误差 {worst} 米"


def test_thumb_projection_is_a_uniform_scale_of_the_full_projection() -> None:
    """``world_to_thumb`` 必须正好是 ``world_to_pixel`` 除以 18.432。

    这条专治"缩略图是 1/4 还是 1/8 缩放、矩阵没跟着乘"那个根因 ——
    两个函数只要共用同一个仿射 + 一个常数，就不可能再对不上。
    """
    assert M.THUMB_TO_FULL == pytest.approx(18.432, abs=1e-9)
    for wx, wz in ((0.0, 0.0), (4634.52, 4687.13), (8000.0, 8000.0)):
        fx, fy = M.world_to_pixel(wx, wz)
        tx, ty = M.world_to_thumb(wx, wz)
        assert tx == pytest.approx(fx / M.THUMB_TO_FULL, abs=1e-9)
        assert ty == pytest.approx(fy / M.THUMB_TO_FULL, abs=1e-9)


# ==================================================================
# 2. 落点在底图上 —— 顺带守住"没转置、没平移错"
# ==================================================================

def test_no_teleport_point_falls_outside_the_map(all_points) -> None:
    """全部 936 个点都要落在 36864x18432 的底图范围内。"""
    outside = [p.name for p in all_points
               if not M.is_inside(*M.world_to_pixel(*p.xz))]
    assert not outside, f"{len(outside)} 个点落在图外，例如 {outside[:5]}"


@pytest.mark.skipif(not _L4.is_file(), reason="缺 L4 掩膜图，跳过海陆判定")
def test_every_teleport_point_lands_on_land(all_points) -> None:
    """★ 这是"精度对不对"唯一能量化的真判据。

    传送点按定义都在陆地上。如果矩阵错了 / 轴转置了 / 平移量抄错了，
    落点会成片跑到海里。实测 935/936（99.9%）在陆地，唯一一个例外是
    贴图边缘的近海点，卡 99% 是留余量而不是放松要求。

    判据与诊断脚本一致：提瓦特底图的海明显偏蓝（b > r+20 且 b > g+10）。
    """
    Image = pytest.importorskip("PIL.Image", reason="Pillow 不在就跳过")
    import numpy as np
    Image.MAX_IMAGE_PIXELS = None

    arr = np.asarray(Image.open(_L4).convert("RGB"))
    h, w = arr.shape[:2]
    sx, sy = w / M.IMAGE_WIDTH, h / M.IMAGE_HEIGHT

    sea = 0
    for p in all_points:
        px, py = M.world_to_pixel(*p.xz)
        ix, iy = int(px * sx), int(py * sy)
        if not (0 <= ix < w and 0 <= iy < h):
            sea += 1
            continue
        r, g, b = (int(v) for v in arr[iy, ix])
        if b > r + 20 and b > g + 10:
            sea += 1
    ratio = sea / len(all_points)
    assert ratio <= 0.01, f"{sea}/{len(all_points)} ({ratio:.1%}) 的点落在海上"


# ==================================================================
# 3. 标定质量口径：把"20 米"这个验收线说清楚
# ==================================================================

def test_the_published_fit_quality_meets_the_twenty_meter_budget() -> None:
    """把验收线钉住，并说清"20 米"到底约束的是哪一层。

    ★ 口径（汇报时也要照这个说，别笼统喊"偏差 < 20 米"）
      用户要的验收线是 **20 像素**。在**原图**（1 米/像素）尺度上：

        * RMS 残差   12.65 米 < 20 ✔  达标
        * 最大残差   28.64 米 > 20 ✘  **超标**

      最大残差来自 23 个标定锚点里的一个离群点（当年标定时的测量
      误差），不是系统性偏差 —— RMS 才是代表性指标。这里用断言把
      这个离群点**钉住**：哪天有人"顺手把常量改小"让测试变绿，
      这里会立刻炸。

      另外，在**缩略图**层上：12.65 米 = 0.69 个缩略图像素 < 1，
      也就是说缩略图自身的量化（18.4 米/像素）比标定误差还大，
      "误差"在那一层根本被采样吃掉了 —— 想再降只能上 L2/L0。
    """
    assert M.FIT_ANCHOR_COUNT >= 20
    assert 0 < M.FIT_RMS_PX < M.FIT_MAX_RESIDUAL_PX
    assert M.FIT_RMS_PX <= 20.0, "标定 RMS 超过 20 米预算"
    # 离群点：留着，但把现状写死，改动必须是有意识的
    assert M.FIT_MAX_RESIDUAL_PX == pytest.approx(28.6371, abs=1e-4)
    # 缩略图层上，标定误差必须小于 1 个源像素
    assert M.FIT_RMS_PX / M.THUMB_TO_FULL < 1.0


# ==================================================================
# 4. 绘制单位 —— 用户看到的"对不上"就修在这里
# ==================================================================

@pytest.mark.parametrize("zoom", [1.0, 2.5, 8.0, 32.0])
def test_drawing_uses_screen_pixels_at_every_zoom(picker, all_points,
                                                   zoom) -> None:
    """★ 核心回归：金点/标记的**屏幕**大小不随缩放变化。

    老代码画的是 ``r = 3.0`` 场景单位。1 场景单位 = 18.4 米，所以那个点
    真实大小 55 米；32x 时它涨到 96 屏幕像素半径，绿环涨到 288 ——
    两三个点就糊成一坨，看着就是"标记和点对不上"。

    现在按 :data:`POINT_RADIUS_PX` / :data:`MARKER_RADIUS_PX` 走，
    这个断言会在有人改回场景单位时立刻炸。
    """
    picker.set_zoom_factor(zoom)
    picker._set_marker(*M.world_to_thumb(*all_points[0].xz))
    m11 = picker._view.transform().m11()

    for p in picker._visible:
        r_scene = picker._point_items[p.id].rect().width() / 2.0
        assert r_scene * m11 == pytest.approx(W.POINT_RADIUS_PX, abs=0.05)

    ring = picker._marker.rect().width() / 2.0
    assert ring * m11 == pytest.approx(W.MARKER_RADIUS_PX, abs=0.05)


@pytest.mark.parametrize("zoom", [1.0, 8.0, 32.0])
def test_outline_width_is_a_screen_constant(picker, zoom) -> None:
    """★ 轮廓线宽必须不吃 transform。

    ``QPen`` 默认按场景单位算线宽，32x 下 1 宽的边框会变成 32 屏幕像素，
    金点糊成一个实心色块，把底图和自己的圆心一起盖住 ——
    这是"标记跟金点对不上"的另一半原因（另一半是半径，見上一个测试）。
    """
    picker.set_zoom_factor(zoom)
    assert all(it.pen().isCosmetic() for it in picker._point_items.values())
    picker._set_marker(500.0, 500.0)
    assert all(it.pen().isCosmetic() for it in picker._marker_parts)


@pytest.mark.parametrize("zoom", [1.0, 8.0, 32.0])
def test_marker_center_stays_locked_on_its_point(picker, all_points,
                                                  zoom) -> None:
    """★ 绿标记的中心必须**精确**落在金点中心上，误差 < 1 场景单位。

    这是用户截图里那个现象的正面判据：放大到多少，标记都不许偏移。
    """
    picker.set_zoom_factor(zoom)
    for p in picker._visible[:60]:
        tx, ty = M.world_to_thumb(*p.xz)
        picker._set_marker(tx, ty)
        centre = picker._marker.rect().center()
        assert math.hypot(centre.x() - tx, centre.y() - ty) < 1.0

        # 命中的那个点也必须是它自己（半径按屏幕像素，两套换算必须同源）
        hit = picker._point_at(QPointF(tx, ty))
        assert hit is not None and hit.id == p.id


def test_dot_center_equals_the_projected_world_position(picker) -> None:
    """画的圆心 == ``world_to_thumb`` 的结果（绘制没偷偷加偏移）。"""
    for p in picker._visible:
        tx, ty = M.world_to_thumb(*p.xz)
        c = picker._point_items[p.id].rect().center()
        assert c.x() == pytest.approx(tx, abs=1e-6)
        assert c.y() == pytest.approx(ty, abs=1e-6)


def test_repeated_marker_placement_does_not_leak_items(picker) -> None:
    """点几十次不能往场景里堆几十个十字（老代码只回收了圆环）。"""
    before = len(picker._scene.items())
    for i in range(25):
        picker._set_marker(400.0 + i, 300.0 + i)
    assert len(picker._scene.items()) == before + 5   # 1 环 + 4 线


# ==================================================================
# 5. 分级加载：换层不许让点位漂移
# ==================================================================

@pytest.mark.parametrize("zoom", [1.0, 2.5, 8.0, 12.0, 32.0])
def test_switching_base_level_never_moves_the_points(picker, zoom) -> None:
    """★ 换底图层时，世界坐标到场景坐标的映射必须纹丝不动。

    底图层级是靠 ``QTransform`` 缩放进同一个 sceneRect（0..2000, 0..1000）
    实现的，点位计算一行都没改 —— 这条断言就是守住这个设计不变式的。
    如果哪天有人改成"每层一套场景坐标"，这里会立刻炸。
    """
    before = {p.id: picker._point_items[p.id].scenePos()
              for p in picker._visible}
    picker.set_zoom_factor(zoom)
    for p in picker._visible:
        after = picker._point_items[p.id].scenePos()
        assert after.x() == pytest.approx(before[p.id].x(), abs=1e-9)
        assert after.y() == pytest.approx(before[p.id].y(), abs=1e-9)


def test_plan_picks_the_level_by_zoom() -> None:
    """纯逻辑：倍率 -> 层级，不受视口位置影响。"""
    if not (Path(M.MAP_THUMB).parent / "P0_pyramid"
            / "chunks_index.json").is_file():
        pytest.skip('底图资产不可用 —— 米游社版权 / 体量过大，不随包分发；自备指引见 docs/ASSETS.md')
    pyr = W._LevelPyramid(Path(M.MAP_THUMB).parent)
    ladder = [(1.0, "thumb"), (1.99, "thumb"), (2.0, "l2"), (8.0, "l2"),
              (11.99, "l2"), (12.0, "tiles"), (32.0, "tiles")]
    for zoom, want in ladder:
        got = pyr.plan(zoom)
        assert got == want, f"{zoom}x 规划成 {got}"


def test_level_follows_the_zoom_ladder(picker) -> None:
    """1x -> thumb，2x~11x -> L2，>=12x -> 瓦片。

    ★ 每一步都先把视图挪回地图中心
      视图的 sceneRect 比地图大一圈（见 OVERSCAN），所以它**可以**停在
      地图外；那里本来就没有瓦片，``_apply_tiles`` 会退回 L2。
      不居中就断言层级，测的是"视口恰好在哪"而不是分层逻辑本身。
    """
    from PySide6.QtCore import QPointF
    ladder = [(1.0, "thumb"), (2.0, "l2"), (8.0, "l2"),
              (11.9, "l2"), (12.0, "tiles"), (32.0, "tiles")]
    for zoom, want in ladder:
        picker.set_zoom_factor(zoom)
        picker._view.centerOn(QPointF(1000.0, 500.0))
        picker._sync_level()
        assert picker._level == want, f"{zoom}x 用了 {picker._level}"


def test_zooming_outside_the_map_falls_back_to_a_single_image(picker) -> None:
    """★ 视野整个跑到地图外（overscan 余量区）时不该崩溃、不该空图。

    那里没有瓦片覆盖，退回 L2 整图（虽然那片区域本来就是黑的）。
    """
    from PySide6.QtCore import QPointF
    rect = picker._view.sceneRect()
    picker.set_zoom_factor(32.0)
    picker._view.centerOn(QPointF(rect.left() + 5.0, rect.top() + 5.0))
    picker._sync_level()
    assert picker._level in ("tiles", "l2", "thumb")
    assert picker._base_items, "视野跑到地图外时场景里一张底图都没有"


def test_tile_cache_is_capped(picker) -> None:
    """瓦片 LRU 不许无限增长。"""
    for zoom in (12.0, 16.0, 24.0, 32.0):
        picker.set_zoom_factor(zoom)
        # 来回扫一圈，制造远超容量的不同视野
        for tx in (100.0, 1200.0, 2400.0, 3000.0, 500.0):
            picker._view.centerOn(tx, 500.0)
            picker._sync_level()
        assert picker._pyramid.cache_size() <= picker._pyramid.TILE_CACHE


def test_tiles_land_on_their_own_grid_cell(picker) -> None:
    """★ 瓦片必须铺在**属于自己**的格子上。

    ``chunks_index.json`` 里的 bbox 是 36864x18432 那张全分辨率图的格子，
    而场景坐标是缩略图（2000x1000）。拿全分辨率像素直接 ``translate``
    会把每块瓦片甩到 **18.432 倍**远的地方 —— 底图和传送点彻底分家，
    正是"看着像位置偏了"的另一种成因。
    """
    picker.set_zoom_factor(16.0)
    if picker._level != "tiles":
        pytest.skip("瓦片资产不可用")
    index = picker._pyramid._tile_index
    assert len(picker._base_items) >= 1
    for tid, it in zip(picker._tile_ids, picker._base_items):
        bx, by, _bw, _bh = index[tid]
        rect = it.sceneBoundingRect()
        assert rect.left() == pytest.approx(bx / M.THUMB_TO_FULL, abs=1e-6)
        assert rect.top() == pytest.approx(by / M.THUMB_TO_FULL, abs=1e-6)
        # 瓦片宽度换算回缩略图坐标后，应正好是 THUMB_WIDTH/12（一格）
        assert rect.width() == pytest.approx(W.THUMB_WIDTH / 12.0, abs=1e-6)


def test_focusing_a_far_point_at_high_zoom_still_covers_it(picker,
                                                           all_points) -> None:
    """★ 回归：定位到一个**远处**的传送点并放大到 32x，画面不许空白。

    踩过的坑：``set_zoom_factor`` 内部按当时的视野换层，紧接着的
    ``centerOn`` 又把视野整个挪走，却没人再刷一次 —— 瓦片铺在原地，
    视图飞到 Mondstadt，屏幕上一片深色（实测就是这样）。
    所以 ``centerOn`` 一定要和换层成对，见 ``MapPicker._center_on``。
    """
    target = max(all_points, key=lambda p: p.xz[0])
    picker.focus_point(target, zoom=32.0)
    if picker._level != "tiles":
        pytest.skip("瓦片资产不可用")
    tx, ty = M.world_to_thumb(*target.xz)
    for it in picker._base_items:
        r = it.sceneBoundingRect()
        if r.left() - 1 <= tx <= r.right() + 1 and r.top() - 1 <= ty <= r.bottom() + 1:
            break
    else:
        laid = [(round(it.sceneBoundingRect().left()),
                 round(it.sceneBoundingRect().top()))
                for it in picker._base_items]
        pytest.fail(f"{target.name} 不在任何一块已铺瓦片上："
                    f"目标=({tx:.0f}, {ty:.0f}) 已铺={laid}")


def test_scene_rect_stays_in_thumbnail_coordinates_at_every_level(picker) -> None:
    """不管切到哪一层，sceneRect 永远是缩略图坐标（点位计算的地基）。"""
    for zoom in (1.0, 4.0, 16.0, 32.0):
        picker.set_zoom_factor(zoom)
        r = picker._scene.sceneRect()
        assert (r.width(), r.height()) == (W.THUMB_WIDTH, W.THUMB_HEIGHT)


# ==================================================================
# 6. 底图必须是彩色的（2026-10-16）
# ==================================================================

def _mean_saturation(qimage) -> float:
    """QImage 的平均饱和度（HSV 的 S 分量，0~1）。"""
    img = qimage.convertToFormat(QImage.Format_RGB32)
    n = img.width() * img.height()
    if n == 0:
        return 0.0
    step = max(1, n // 40000)          # 最多采 4 万点，够统计了
    pts = img.bits().tobytes()
    total = 0.0
    cnt = 0
    for i in range(0, n, step):
        o = i * 4
        b, g, r = pts[o], pts[o + 1], pts[o + 2]
        mx, mn = max(r, g, b), min(r, g, b)
        total += (mx - mn) / mx if mx else 0.0
        cnt += 1
    return total / cnt if cnt else 0.0


#: 饱和度下限 5%。实测彩色资产 ~30%，灰度资产 0.00%，阈值卡中间。
COLOR_SAT_MIN = 0.05


def _land_point(all_points):
    """挑一个必定在陆地上的神像点做靶子（陆地上才有颜色可测）。"""
    for p in all_points:
        if p.type == "Goddess":
            return p
    return all_points[0]


def _sat_of_current_base(picker) -> float:
    """当前底图上那块**非空**区域的平均饱和度。"""
    best = 0.0
    for it in reversed(picker._base_items):
        img = it.pixmap().toImage().scaled(
            96, 48, Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        s = _mean_saturation(img)
        best = max(best, s)
        if best > COLOR_SAT_MIN:
            break
    return best


def test_the_base_map_is_colour_not_greyscale(picker, all_points) -> None:
    """★ 回归：1x 的底图必须有颜色。

    缩略图 ``teyvat_P0_thumb.png`` 本来就是彩色的（饱和度 ~30%），
    所以这条钉的是"别哪天不小心换成了灰度资产"。
    """
    picker.focus_point(_land_point(all_points), zoom=1.0)
    sat = _sat_of_current_base(picker)
    assert sat > COLOR_SAT_MIN, f"底图饱和度只有 {sat:.1%}，是灰度的"


@pytest.mark.parametrize("zoom", [2.5, 8.0])
def test_mid_zoom_levels_are_colour(picker, all_points, zoom) -> None:
    """★ 回归：2x / 8x 用的 L2 层必须是彩色的。

    这正是本轮修的问题：``P0_pyramid/L2_full.png`` 是
    ``slice_p0_pyramid.py`` 里 ``im.convert("L")`` 的产物（mode="L"，
    饱和度 0.00%），地图一到 2x 就全灰。
    彩色那套在 ``P0_color/``，由 ``slice_p0_color_pyramid.py`` 生成。
    """
    picker.focus_point(_land_point(all_points), zoom=zoom)
    assert picker._level == "l2", f"{zoom}x 用了 {picker._level}"
    sat = _sat_of_current_base(picker)
    assert sat > COLOR_SAT_MIN, (
        f"{zoom}x 用的 L2 是灰度的（饱和度 {sat:.1%}）；"
        f"当前资产目录 = {picker._pyramid._sub.name}")


def test_tile_level_is_colour(picker, all_points) -> None:
    """★ 回归：12x 以上铺的瓦片必须是彩色的。"""
    picker.focus_point(_land_point(all_points), zoom=32.0)
    if picker._level != "tiles":
        pytest.skip("瓦片资产不可用")
    sat = _sat_of_current_base(picker)
    assert sat > COLOR_SAT_MIN, f"瓦片是灰度的（饱和度 {sat:.1%}）"


def test_pyramid_prefers_the_colour_assets_when_present() -> None:
    """有 ``P0_color/`` 就用它，否则退回 ``P0_pyramid/``（别写死）。"""
    if not (Path(M.MAP_THUMB).parent / "P0_pyramid"
            / "chunks_index.json").is_file():
        pytest.skip('底图资产不可用 —— 米游社版权 / 体量过大，不随包分发；自备指引见 docs/ASSETS.md')
    pyr = W._LevelPyramid(Path(M.MAP_THUMB).parent)
    color = Path(M.MAP_THUMB).parent / "P0_color"
    if (color / "chunks_index.json").is_file():
        assert pyr.is_color is True
        assert pyr._sub.name == "P0_color"
    else:
        assert pyr._sub.name == "P0_pyramid"
    # 无论走哪套，索引都必须是同一套格式（bbox 为 xyxy）
    pyr.load_index()
    assert len(pyr._tile_index) == 144
    xs = sorted({v[0] for v in pyr._tile_index.values()})
    assert xs[:3] == [0, 3072, 6144], f"瓦片起点对不上：{xs[:3]}"
    assert all(v[2] == 3072 and v[3] == 1536
               for v in pyr._tile_index.values()), "瓦片尺寸不是 3072x1536"


# ==================================================================
# 7. 数据集本身的形状也钉一下（避免换数据源时静默漂移）
# ==================================================================

def test_the_reference_dataset_still_has_the_shape_we_tested(all_points) -> None:
    assert len(all_points) == 936
    assert len({p.name for p in all_points}) == 106
    assert len({p.type for p in all_points}) == 10
