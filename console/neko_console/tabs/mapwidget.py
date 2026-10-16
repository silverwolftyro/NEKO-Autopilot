"""地图选择器：底图 + 传送点 + 点选。

★ 缩放模型（2026-10-16 重做）
--------------------------------
缩放倍率**相对于「刚好放下整张图」**，即 1x = 全图、8x = 放大 8 倍。
不是以前那种直接拿 :attr:`QGraphicsTransform.m11` 当倍率。

为什么必须改：``fitInView`` 算出来的绝对缩放**依赖控件当前尺寸**，
而 :meth:`_load_thumb` 是在 ``__init__`` 里调的 —— 那时控件还没布局，
viewport 只有几十像素，于是整张 2000px 的图被缩到 **48px 宽**，
且此后**再也不会重算**（实测 m11 恒为 0.024，1350x900 的窗口里
地图只有中间 30x20 像素的一个小点）。用户看到的"变形"就是这个。

现在的做法：
  * ``__init__`` **不**调 ``fitInView``；
  * 首次 ``showEvent`` / 之后每次 ``resizeEvent``，只要用户**没手动**
    缩放平移过，就重新 ``fit()``；
  * 用户一旦自己缩放/平移，``_user_adjusted`` 置位，窗口再变也不再打扰。

**长宽比**始终由 ``fitInView(..., Qt.KeepAspectRatio)`` 保证等比，
多余空间留成深色边框（背景刷无条件设置），不做任何拉伸。
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import (QEvent, QLineF, QPoint, QPointF, QRectF, Qt,
                            Signal)
from PySide6.QtGui import (QBrush, QColor, QPainter, QPen, QPixmap, QPolygon,
                           QTransform)
from PySide6.QtWidgets import (QGraphicsEllipseItem, QGraphicsScene, QGraphicsView,
                               QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QSlider, QVBoxLayout, QWidget)

from .mapdata import (MAP_THUMB, THUMB_HEIGHT, THUMB_TO_FULL, THUMB_WIDTH,
                     TeleportPoint, load_teleport_points, normalize_name,
                     search_points, thumb_to_world, world_to_thumb)

logger = logging.getLogger(__name__)

#: 缩放下限。1x = 整张图刚好放进视图，**不再允许缩小** ——
#: 再小只是把图缩成屏幕中央一个小点，四周全是空白，没有任何信息。
MIN_ZOOM = 1.0
#: 缩放上限。2026-10-16 从 8 提到 32：
#: 全图 36864 世界像素宽，2560 宽的屏在 8x 下每屏仍显示 4608 世界像素，
#: 传送到点周围完全看不清。32x 下每屏约 1150 世界像素，才够看一个传送点
#: 周边的地形细节。
MAX_ZOOM = 32.0
#: "定位到某点"时用的倍率。
FOCUS_ZOOM = 2.5

#: 默认显示的传送点类型。普通传送锚点有 777 个，全画出来是糊的。
DEFAULT_TYPES = ("Goddess", "BlessDomain", "OneTimeDomain")

#: 留白（等比缩放后多出来的部分）的底色。
LETTERBOX_BG = "#161a21"

#: 比例尺候选档位（米），取最接近的一档。
_SCALE_STEPS = (100, 250, 500, 1000, 2000, 5000, 10000, 20000)

#: 传送点圆点与选中标记的**屏幕**半径（像素）。
#:
#: ★ 为什么必须是屏幕单位而不是场景单位
#:   老代码写的是 ``r = 3.0`` 场景单位。场景 1 单位 = 18.4 米，
#:   所以那个点真实大小是 **55 米**；选中标记 r=9.0 更是 **166 米**。
#:   低倍时看不出问题，一旦放大，两者都跟着 transform 一起涨 ——
#:   32x 时金点半径 96 屏幕像素、绿环 288 像素，整张图被两个巨圈盖住，
#:   看上去就是「绿标记和金点对不上」。命中测试 ``_point_at`` 早就是按
#:   屏幕像素算的，只有绘制漏了，这里补齐。
POINT_RADIUS_PX = 4.0
MARKER_RADIUS_PX = 11.0

#: 地图资产根目录。缩略图、金字塔层、L0 瓦片都在这一层下面。
MAPS_ROOT = MAP_THUMB.parent


#: 缩略图上下左右各留多少余量给视图滚动。
#:
#: ★ 为什么需要余量
#:   QGraphicsView 的滚动条范围是按**视图自己的 sceneRect** 算的。
#:   若它正好等于地图，Qt 会强制视口不越界；鼠标贴边缩放时锚点要求的
#:   中心在地图外，于是被 clamp 拉回最近边界 —— 表现就是"缩放时突然
#:   往别处跳"。地图不是矩形、边缘外本来就是黑，所以放大 sceneRect
#:   让视图能滑到地图外是合理的。
#:
#: 取 50%：1x 时视口在场景坐标下约 2006x1228，比地图（2000x1000）略大，
#: 刚好覆盖"把地图中心挪到地图外"的需要，又不至于让用户把地图整个
#: 拖出视野（实测 25% 时视图自身就顶到余量边界，贴边缩放会差 1 px）。
OVERSCAN = 0.5


def _overscanned(rect: QRectF) -> QRectF:
    """把地图矩形按 :data:`OVERSCAN` 向外扩一圈。"""
    dx = rect.width() * OVERSCAN
    dy = rect.height() * OVERSCAN
    return QRectF(rect.left() - dx, rect.top() - dy,
                  rect.width() + dx * 2, rect.height() + dy * 2)


def _cosmetic_pen(color: str, width: float = 1.0) -> QPen:
    """不吃 transform 的画笔：**线宽永远是屏幕像素**。

    ★ 踩过的坑
      ``QPen`` 默认用场景单位算线宽。视图在 32x 时，1 宽的边框会变成
      32 屏幕像素 —— 金点直接糊成一个 40x40 的实心色块，把底图和
      自己的圆心全盖住，看上去就是"标记和点对不上"。
      ``setCosmetic(True)`` 之后线宽跟缩放无关。
    """
    pen = QPen(QColor(color), width)
    pen.setCosmetic(True)
    return pen


class _ScaleBar(QWidget):
    """左下角比例尺。

    ★ 为什么不能用 scene item 画
      scene item 跟着地图一起缩放，地图放大 8x 时比例尺本身也变成 8x 粗，
      那就不是比例尺而是装饰条了。必须是**屏幕空间**的覆盖层。
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._meters = 0
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedHeight(22)
        self.setMinimumWidth(90)

    def set_scale(self, meters_per_px: float, budget_px: int = 110) -> None:
        """按当前缩放选一个好看的档位。"""
        best_m, best_px = _SCALE_STEPS[0], 0
        for m in _SCALE_STEPS:
            px = m / meters_per_px if meters_per_px > 0 else 0.0
            if px <= budget_px:
                best_m, best_px = m, px
        self._meters = best_m
        self._px = int(best_px)
        self.setFixedWidth(max(90, self._px + 8))
        self.update()

    def paintEvent(self, _event) -> None:   # noqa: N802 - Qt 命名
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w = getattr(self, "_px", 0)
        y = self.height() - 7
        pen = QPen(QColor("#c8d0e0"))
        pen.setWidthF(1.6)
        p.setPen(pen)
        p.drawLine(0, y, w, y)
        p.drawLine(0, y - 4, 0, y + 3)
        p.drawLine(w, y - 4, w, y + 3)
        p.setPen(QColor("#c8d0e0"))
        p.drawText(0, y - 7, self.width(), 12, Qt.AlignHCenter,
                   f"{self._meters} m")
        p.end()


class _NorthArrow(QWidget):
    """右上角北向箭头。

    方位由 :func:`mapdata.world_to_pixel` 的仿射系数实测得出：

        世界 +X -> 屏幕 (0, -1)   即**向上**
        世界 -Z -> 屏幕 (+1, 0)   即**向右**

    也就是屏幕上**正上方就是北**，箭头因此固定朝上，不需要旋转。
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedSize(34, 40)

    def paintEvent(self, _event) -> None:   # noqa: N802 - Qt 命名
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        cx, cy = self.width() // 2, self.height() - 12
        p.setPen(QPen(QColor("#c8d0e0"), 1.4))
        p.setBrush(QBrush(QColor("#7CFFB2")))
        # QPolygon 从序列构造时只吃 QPoint（不是 QPointF），别传错类型。
        p.drawPolygon(QPolygon([QPoint(cx, cy - 14), QPoint(cx - 6, cy + 2),
                                QPoint(cx + 6, cy + 2)]))
        p.setPen(QColor("#c8d0e0"))
        p.drawText(0, 0, self.width(), 14, Qt.AlignCenter, "N")
        p.end()


class _LevelPyramid:
    """按缩放倍率换底图层级（2026-10-16）。

    ★ 为什么需要它
      缩略图 2000x1000 覆盖 36864x18432 的地图 —— **1 缩略图像素 = 18.4 米**。
      倍率再怎么放大，放大的只是这 18.4 米的格子，不是新信息：
      32x 时每个源像素被拉到 32x32 的马赛克。这正是"放大后细节看不清、
      看着像位置偏了"的根源 —— **不是坐标不准，是根本没有那么多细节**。

      ``recognition/data/mihoyo_maps/P0_pyramid/`` 里已经有现成的分层：

      =======================  ============  ============  ==========
      资产                       尺寸          米/像素       内存(QPixmap)
      =======================  ============  ============  ==========
      ``teyvat_P0_thumb.png``    2000x1000     18.43        8 MB
      ``P0_pyramid/L2_full.png``  9216x4608     4.00        17 MB
      ``P0_pyramid/L1_full.png``  18432x9216    2.00        68 MB
      ``P0_pyramid/L0/*.png``    3072x1536     1.00        18 MB/块
      =======================  ============  ============  ==========

      本类只用三层：**thumb -> L2 -> L0 瓦片**。
      L1 单张就要 68 MB 常驻，而它覆盖的倍率区间 L2 也能满足，
      为省 68 MB 常驻内存不值得。

    瓦片按需加载：32x 时视野只有约 1150x575 全分辨率像素，
    比一块瓦片（3072x1536）还小，所以最多命中 1~4 块，
    LRU 兜住来回平移，不预加载 144 块。
    """

    #: (倍率下限, 优先级) -> 资产选择。倍率 >= TILE_ZOOM 走瓦片。
    TILE_ZOOM = 12.0
    #: 瓦片 LRU 容量。32x 下一屏最多 4 块，给 8 块留平移余量。
    TILE_CACHE = 8
    #: 单次最多往场景里铺几块瓦片。
    #:
    #: ★ 为什么还要再卡一道：``tile_ids_for`` 是按视野**相交**选的，
    #:   控件还没布局时（离屏 / 构造中）viewport 可能小到畸形，
    #:   或者反过来大到覆盖全图，于是 144 块全命中 —— 每块 18MB 的
    #:   QPixmap 现场解码再全部 ``addPixmap``，光这一下就是 2.6GB 的
    #:   分配抖动，实测能把一次缩放拖到几十秒。这里按"离视野中心近"
    #:   排序取前 N 块，多出来的下一轮补。
    TILE_DRAW = 16

    def __init__(self, maps_root: Path, thumb_path: Path | None = None) -> None:
        self.root = Path(maps_root)
        self._thumb_path = Path(thumb_path) if thumb_path else \
            (self.root / "teyvat_P0_thumb.png")
        # ★ 优先用彩色金字塔 ``P0_color/``，没有就退回灰度的 ``P0_pyramid/``。
        #   灰度那套是 ``build/slice_p0_pyramid.py`` 第 87 行
        #   ``im.convert("L")`` 的产物（饱和度 0.00%），>=2x 时整张图是黑的。
        #   彩色那套由 ``build/slice_p0_color_pyramid.py`` 生成，
        #   索引格式完全一致，所以下面所有代码都不用分支。
        color = self.root / "P0_color"
        self._sub = color if (color / "chunks_index.json").is_file() \
            else self.root / "P0_pyramid"
        self.is_color = self._sub.name == "P0_color"
        self._l2_path = self._sub / "L2_full.png"
        self._tile_dir = self._sub / "L0"
        self._tile_index: dict[str, tuple[int, int, int, int]] = {}
        self._index_loaded = False
        self._tiles: "OrderedDict[str, object]" = OrderedDict()
        self._memo: dict[str, object] = {}

    # ------------------------------------------------------------------
    def load_index(self) -> None:
        """读 ``chunks_index.json``（瓦片 id -> 全分辨率 xywh）。

        ⚠️ 索引里的 ``bbox`` 是 **[x0, y0, x1, y1]**（左上和**右下角**），
           不是常见的 [x, y, w, h]。
           实测第一块 ``[0, 0, 3072, 1536]`` 两种读法都成立，很有迷惑性；
           但第二块是 ``[3072, 0, 6144, 1536]`` —— 按 xywh 读会得到
           "宽 6144、高 1536"，直接把它当 xywh 的话每块瓦片从第二块起
           尺寸就全是错的（顺带一提，同文件里的 ``size`` 才是 [w, h]）。
        """
        if self._index_loaded:
            return
        self._index_loaded = True
        p = self._sub / "chunks_index.json"
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:               # noqa: BLE001
            logger.warning("瓦片索引读不到（%s）：%s", p, exc)
            return
        for c in doc.get("chunks", ()):
            try:
                x0, y0, x1, y1 = c["bbox"]
            except (KeyError, TypeError, ValueError):
                logger.warning("瓦片 %s 的 bbox 读不了，跳过", c.get("id"))
                continue
            self._tile_index[c["id"]] = (x0, y0, x1 - x0, y1 - y0)

    def available(self) -> list[str]:
        """现有层级资产清单（供诊断/测试用）。"""
        return [str(p) for p in (self._thumb_path, self._l2_path)
                if p.is_file()]

    def path_for(self, level: str) -> Path | None:
        """某一级对应的文件；``tiles`` 不是单文件，返回 None。"""
        if level == "thumb":
            return self._thumb_path
        if level == "l2":
            return self._l2_path
        return None

    def bbox(self, tid: str):
        """瓦片在全分辨率像素下的 ``(x, y, w, h)``。"""
        return self._tile_index.get(tid)

    # ------------------------------------------------------------------
    def plan(self, zoom: float, scene_rect=None) -> str:
        """当前倍率该用哪一层。

        :param scene_rect: 预留参数（未来按视野挑瓦片），现在没用上。

        阈值依据（2560 宽屏、整图 36864 世界像素）：

        ==========  ==========================  ====================
        倍率        每屏世界像素                用哪层
        ==========  ==========================  ====================
        1x          36864                       thumb（18.4 m/px）
        2x~11x      18432 ~ 3350               L2（4 m/px，仍在缩）
        >= 12x      <= 3072                    L0 瓦片（1 m/px 原生）
        ==========  ==========================  ====================
        """
        if zoom >= self.TILE_ZOOM and (self._tile_index
                                      or self._tile_dir.is_dir()):
            return "tiles"
        if zoom >= 2.0 and self._l2_path.is_file():
            return "l2"
        return "thumb"

    def tile_ids_for(self, scene_rect, limit: int | None = None) -> list[str]:
        """视野（全分辨率像素矩形）覆盖的瓦片 id。

        :param scene_rect: ``(x0, y0, x1, y1)``，全分辨率像素坐标。
        :param limit: 最多返回几块；超了按"格子中心离视野中心近"优先。
            见 :attr:`TILE_DRAW` 的说明。
        """
        self.load_index()
        if not self._tile_index:
            return []
        x0, y0, x1, y1 = scene_rect
        hits = []
        for tid, (tx, ty, tw, th) in self._tile_index.items():
            if tx < x1 and tx + tw > x0 and ty < y1 and ty + th > y0:
                hits.append(tid)
        if limit is not None and len(hits) > limit:
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0

            def _dist(tid: str) -> tuple[float, str]:
                tx, ty, tw, th = self._tile_index[tid]
                return (((tx + tw / 2.0 - cx) ** 2 +
                         (ty + th / 2.0 - cy) ** 2) ** 0.5, tid)

            hits.sort(key=_dist)
            hits = hits[:limit]
        hits.sort()
        return hits

    def get_tile(self, tid: str):
        """取一块瓦片（带 LRU）。没有就返回 None。"""
        if tid in self._tiles:
            self._tiles.move_to_end(tid)
            return self._tiles[tid]
        p = self._tile_dir / f"{tid}.png"
        try:
            px = QPixmap(str(p))
        except Exception as exc:               # noqa: BLE001
            logger.warning("瓦片 %s 加载失败：%s", p, exc)
            return None
        if px.isNull():
            return None
        self._tiles[tid] = px
        while len(self._tiles) > self.TILE_CACHE:
            self._tiles.popitem(last=False)
        return px

    def cache_size(self) -> int:
        return len(self._tiles)


class MapPicker(QWidget):
    """底图 + 传送点 + 点选。

    信号：
        picked(float, float)   —— 用户点了一下，(x, z) 世界坐标
        anchor_picked(object)  —— 点中的是某个传送点，传 ``TeleportPoint``
        anchors_filtered(list) —— 过滤后可见的传送点
        zoom_changed(float)    —— 缩放倍率变了（1x=全图），供外部同步 UI
    """

    picked = Signal(float, float)
    anchor_picked = Signal(object)
    anchors_filtered = Signal(list)
    zoom_changed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._scene = QGraphicsScene(self)
        self._view = QGraphicsView(self._scene, self)
        self._view.setDragMode(QGraphicsView.ScrollHandDrag)
        # ★ 缩放锚点**自己算**（见 :meth:`MapPicker._zoom_at`），
        #   所以这里必须关掉 Qt 的自动锚定。
        #   设成 AnchorUnderMouse 是错的，实测：
        #   ``set_zoom_factor`` 先 ``resetTransform()``，此时变换刚被清零，
        #   Qt 内部的 ``centerOn(mapToScene(lastKnownMousePos))`` 就把
        #   **屏幕坐标当成场景坐标**去定位，视图瞬间跑到天边
        #   （实测滚动条从 -2 窜到 993），随后 ``scale()`` 再错一次。
        #   而且那两步里的 centerOn 都会被滚动范围钳制，我们自己算的
        #   锚点修正量随后就被吃掉（实测 _pan_by 是空操作）。
        #   AnchorViewCenter 则完全不插手，让 set_zoom_factor 自己定位。
        self._view.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
        self._view.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self._view.setMouseTracking(True)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 120 是"看得见地图"的下限（以前是 340，整个 tab 的高度地板大头）。
        self._view.setMinimumHeight(120)
        self._view.setCursor(Qt.CrossCursor)
        self._view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # ★ 把滚轮事件劫到 MapPicker 上。QGraphicsView 自己也会处理滚轮，
        #   光靠 QWidget 的 wheelEvent 收不到（子控件先拿）。
        self._view.viewport().installEventFilter(self)
        # ★ 遮蔽 Qt 的鼠标事件前，**必须先把原方法存下来**。
        #   直接在 _on_press 里写 self._view.mousePressEvent(event) 会调到
        #   自己 —— 因为实例属性已经遮蔽了类方法，那是无限递归
        #   （实测 RecursionError: maximum recursion depth exceeded）。
        self._base_mouse_move = self._view.mouseMoveEvent
        self._base_mouse_press = self._view.mousePressEvent
        self._view.mouseMoveEvent = self._on_move
        self._view.mousePressEvent = self._on_press

        self._hint = QLabel("把鼠标移到地图上看坐标，点一下选定"
                            "（滚轮缩放，拖动平移）")
        self._hint.setStyleSheet("color: #a6adc8;")
        self._readout = QLabel("—")
        self._readout.setStyleSheet("color: #89b4fa; font-family: Consolas;")

        # ── 缩放控件（B 任务）──────────────────────────────────────
        self._zoom_out_btn = QPushButton("−")
        self._zoom_out_btn.setFixedWidth(28)
        self._zoom_out_btn.setToolTip("缩小")
        self._zoom_in_btn = QPushButton("+")
        self._zoom_in_btn.setFixedWidth(28)
        self._zoom_in_btn.setToolTip("放大")
        self._zoom_slider = QSlider(Qt.Horizontal)
        self._zoom_slider.setRange(100, int(MAX_ZOOM * 100))
        self._zoom_slider.setValue(100)
        self._zoom_slider.setToolTip("1x = 全图")
        self._zoom_lbl = QLabel("1.0x")
        self._zoom_lbl.setMinimumWidth(44)
        self._zoom_lbl.setStyleSheet("color: #a6adc8; font-family: Consolas;")
        self._btn_fit = QPushButton("全图")
        self._btn_fit.setToolTip("回到全图（1x）")
        self._btn_focus = QPushButton("定位")
        self._btn_focus.setToolTip("回到最近点选的/选中的传送点")

        zrow = QHBoxLayout()
        zrow.setContentsMargins(0, 0, 0, 0)
        zrow.addWidget(self._zoom_out_btn)
        zrow.addWidget(self._zoom_slider, 1)
        zrow.addWidget(self._zoom_in_btn)
        zrow.addWidget(self._zoom_lbl)
        zrow.addWidget(self._btn_fit)
        zrow.addWidget(self._btn_focus)
        self._zoom_out_btn.clicked.connect(lambda: self.zoom_by(1 / 1.3))
        self._zoom_in_btn.clicked.connect(lambda: self.zoom_by(1.3))
        self._zoom_slider.valueChanged.connect(
            lambda v: self.set_zoom_factor(v / 100.0, from_slider=True))
        self._btn_fit.clicked.connect(self.fit)
        self._btn_focus.clicked.connect(self.focus_last)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(self._view, 1)
        lay.addLayout(zrow)
        lay.addWidget(self._hint)
        lay.addWidget(self._readout)

        # ── 覆盖层：比例尺 + 北向（C 任务）────────────────────────
        vp = self._view.viewport()
        self._scalebar = _ScaleBar(vp)
        self._north = _NorthArrow(vp)

        self._points: tuple[TeleportPoint, ...] = ()
        self._visible: list[TeleportPoint] = []
        self._point_items: dict[int, QGraphicsEllipseItem] = {}
        self._thumb_ok = False
        #: 当前底图用的是哪一层（thumb / l2 / tiles）。
        self._level = ""
        #: 当前已经铺进场景的瓦片 id。换层时对比用，避免重复建 item。
        self._tile_ids: tuple[str, ...] = ()
        self._base_items: list = []
        self._pyramid: _LevelPyramid | None = None
        #: 用户手动缩放/平移过没有。为真就不再自动 fit。
        self._user_adjusted = False
        #: 缩放倍率（相对全图）。只是缓存，真值问 :meth:`zoom_factor`。
        self._zoom = 1.0
        self._marker: QGraphicsEllipseItem | None = None
        #: 标记的所有零件（圆环 + 4 根十字线）。只存圆环的话，
        #: 十字线会一次次往场景里堆，点几十次就多几十个 item。
        self._marker_parts: list = []
        #: 标记所在场景坐标，缩放后据此重画。
        self._marker_at: tuple[float, float] | None = None
        self._last_focus: tuple[float, float] | None = None

        # ★ 背景刷**无条件**设置：等比缩放后多出来的留白必须是深色，
        #   否则白底 + 深色地图会让人以为图没加载全。
        self._scene.setBackgroundBrush(QBrush(QColor(LETTERBOX_BG)))

        self._scene.setSceneRect(0, 0, THUMB_WIDTH, THUMB_HEIGHT)
        # ★ 视图的 sceneRect 比地图**大**一圈。
        #   滚动条的取值范围是由"视图的 sceneRect"算出来的；若它等于地图
        #   本身，Qt 会强制把视口整个按在地图内 —— 于是"鼠标贴边缩放"时，
        #   锚点要求的中心落在地图外，就会被 clamp 拉回来，表现为视图
        #   突然往另一边跳（实测 hScroll 的 minimum 恒为 0，value 被
        #   死死钉在 0 上，一漂就是几百个场景单位）。
        #   地图不是矩形，边缘外本来就有大片黑，所以允许视图中心跑到
        #   地图外是合理的。**场景**的 sceneRect 不动 —— 它是点位计算的
        #   地基，test_scene_rect_matches_the_thumbnail 就守着它。
        self._view.setSceneRect(_overscanned(QRectF(0, 0, THUMB_WIDTH,
                                                   THUMB_HEIGHT)))
        self._load_thumb()
        self.set_points(load_teleport_points())

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------
    def _load_thumb(self) -> None:
        """把底图塞进场景。

        ★ 这里**不**调 ``fitInView``
          本函数在 ``__init__`` 里跑，此时控件还没布局，viewport 只有几十像素，
          fitInView 会算出一个极小的缩放并且**永不再算** ——
          实测整张图被缩到 48px 宽、m11 恒为 0.024。
          首次缩放交给 :meth:`showEvent`。

        ★ 底图图层由 :class:`_LevelPyramid` 按倍率切换，这里只放最基础那张。
          分级策略见 :meth:`_LevelPyramid.plan`。
        """
        # ★ 每次现取 :data:`MAP_THUMB`，不要在 import 时抄一份 ——
        #   测试会 monkeypatch 这个模块属性来模拟"底图缺失"。
        self._thumb_path = Path(MAP_THUMB)
        self._base_items = []
        self._level = ""
        self._tile_ids = ()
        # 资产根跟着缩略图走，这样换一份底图目录时金字塔层也一起换。
        self._pyramid = _LevelPyramid(self._thumb_path.parent, self._thumb_path)
        self._pyramid.load_index()
        if not self._thumb_path.is_file():
            logger.warning("底图缩略图不在：%s", self._thumb_path)
            self._hint.setText("底图缺失（%s），只能用手动输入" % self._thumb_path)
            return
        if self._apply_level(self._pyramid.plan(1.0)):
            self._thumb_ok = True
        else:
            self._hint.setText("底图缺失（%s），只能用手动输入" % self._thumb_path)

    # ------------------------------------------------------------------
    # 底图分层（C 任务）
    # ------------------------------------------------------------------
    def _swap_base(self, items: list) -> None:
        """换底图：**先挂新的再删旧的**。

        ★ 为什么不能先删后加
          "删完旧图 -> addPixmap 新图"之间场景里一张图都没有，
          Qt 只要在这个空档重绘一次，用户看到的就是一帧闪白 ——
          正好是任务 D 要验收的"缩放切换无闪烁"。
          反过来做的话，任意时刻场景里都有底图，切换过程无空档。
        """
        old = list(self._base_items)
        self._base_items = items
        for it in old:
            self._scene.removeItem(it)

    def _apply_level(self, level: str) -> bool:
        """把某一层底图画进场景。返回是否成功。"""
        if level == "tiles":
            return self._apply_tiles()
        path = self._pyramid.path_for(level)
        if path is None:
            return False
        # ★ 只加载一次。L2 单张 17MB，重复解码会卡住主线程。
        px = self._pyramid._memo.get(str(path))
        if px is None:
            px = QPixmap(str(path))
            if px.isNull():
                return False
            self._pyramid._memo[str(path)] = px
        item = self._scene.addPixmap(px)
        item.setZValue(0)
        item.setTransformationMode(Qt.SmoothTransformation)
        # 场景坐标始终是缩略图坐标（0..2000, 0..1000），
        # 底图层级只通过 QTransform 缩放进 sceneRect —— 点位换算一行都不用改。
        item.setTransform(QTransform().scale(
            THUMB_WIDTH / float(px.width()),
            THUMB_HEIGHT / float(px.height())))
        self._swap_base([item])
        self._level = level
        self._tile_ids = ()
        return True

    def _apply_tiles(self) -> bool:
        """按当前视野加载瓦片（只在 >= TILE_ZOOM 时走）。"""
        rect = self._visible_full_rect()
        ids = self._pyramid.tile_ids_for(rect, limit=self._pyramid.TILE_DRAW)
        if not ids:
            # 视野跑到瓦片覆盖范围外，退回单张大图而不是留白。
            return self._apply_level("l2" if self._pyramid._l2_path.is_file()
                                     else "thumb")
        items = []
        for tid in ids:
            px = self._pyramid.get_tile(tid)
            bbox = self._pyramid.bbox(tid)
            if px is None or bbox is None:
                continue
            bx, by, bw, bh = bbox
            # ★ 一块瓦片只覆盖全图 12x12 网格里的**一格**（1/144）。
            #   缩放比要按"自己那格换算成缩略图坐标有多宽"来算，
            #   直接套 THUMB_WIDTH/px.width() 会把每块瓦片拉成整张图那么宽
            #   （12 倍），144 块全叠在一起，底图就是一坨糊。
            cw = bw / THUMB_TO_FULL      # 这一格在缩略图坐标里的宽
            ch = bh / THUMB_TO_FULL
            # 平移量同理要除以 THUMB_TO_FULL —— bbox 记的是全分辨率
            # 像素，直接 translate 会把瓦片甩到 18.432 倍远的地方。
            # 显式搭 6 参矩阵，避免 QTransform 链式的顺序歧义
            t = QTransform(cw / float(px.width()), 0.0, 0.0,
                           ch / float(px.height()),
                           bx / THUMB_TO_FULL, by / THUMB_TO_FULL)
            it = self._scene.addPixmap(px)
            it.setZValue(0)
            it.setTransformationMode(Qt.SmoothTransformation)
            it.setTransform(t)
            items.append(it)
        if not items:
            return self._apply_level("l2" if self._pyramid._l2_path.is_file()
                                     else "thumb")
        self._swap_base(items)
        self._level = "tiles"
        self._tile_ids = tuple(ids)
        return True

    def _visible_full_rect(self) -> tuple[float, float, float, float]:
        """当前视野在全分辨率像素下的矩形。"""
        r = self._view.mapToScene(
            self._view.viewport().rect()).boundingRect()
        s = THUMB_TO_FULL
        return (r.left() * s, r.top() * s, r.right() * s, r.bottom() * s)

    def _center_on(self, tx: float, ty: float) -> None:
        """把视图中心挪到某点，**然后立刻**同步底图层。

        ★ 这两件事必须成对
          :meth:`set_zoom_factor` 内部的换层是按**当时**的视野挑的瓦片；
          紧接着 ``centerOn`` 又把视野整个挪走了。如果不再刷一次，
          场景里留的还是老位置的瓦片 —— 实测表现是：搜索"定位"到一个
          传送点后画面整块空白（瓦片铺在地图左上角，视图飞到了 Mondstadt）。
          32x 时尤其明显，因为只有 >= 12x 才会铺瓦片。
        """
        self._view.centerOn(tx, ty)
        self._sync_level()

    def _sync_level(self) -> None:
        """缩放/平移后同步底图层。

        ★ 平移时**不重复建 item**：先比瓦片 id 集合，一样就直接返回。
          拖动地图时 mousemove 每秒能来几十次，每次全量拆了再挂新的
          QPixmap item 会明显卡手。
        """
        if self._pyramid is None:
            return
        zoom = self.zoom_factor()
        rect = self._visible_full_rect()
        want = self._pyramid.plan(zoom, rect)
        if want == self._level:
            if want != "tiles":
                return
            if tuple(self._pyramid.tile_ids_for(rect)) == self._tile_ids:
                return
        self._apply_level(want)

    def _scene_r(self, screen_px: float) -> float:
        """屏幕像素 -> 场景单位。

        绘制半径一律走这里：形状大小得跟缩放无关，否则 32x 时两个
        传送点画出来是直径 700 屏幕像素的圆饼（见 POINT_RADIUS_PX 注释）。
        """
        return screen_px / max(1e-6, self._view.transform().m11())

    def _refresh_screen_geometry(self) -> None:
        """缩放变了就重算点与标记的矩形。

        ★ 这是"标记跟金点对不上"的直接修法
          半径按屏幕像素给，就必须**跟着 m11 一起重算**：只在
          :meth:`set_filter` 里算一次的话，场景里的矩形是按当时的
          m11 烘死的，之后缩放再变，它就跟着一起涨 ——
          8x 时金点半径 32 屏幕像素，32x 时 128，整片糊成一坨。
          这次用户报的现象就是这么来的。
        """
        r = self._scene_r(POINT_RADIUS_PX)
        d = r * 2.0
        for it in self._point_items.values():
            c = it.rect().center()          # 中心不变，只改大小
            it.setRect(QRectF(c.x() - r, c.y() - r, d, d))
        if self._marker_at is not None:
            self._set_marker(*self._marker_at)

    def set_points(self, points) -> None:
        """设置可点的传送点全集。"""
        self._points = tuple(points)
        self.set_filter("")

    def set_filter(self, text: str) -> None:
        """按地名/地区/国家过滤。空字符串 = 只画有辨识度的那几类。"""
        for it in self._point_items.values():
            self._scene.removeItem(it)
        self._point_items.clear()

        if normalize_name(text):
            hits = search_points(text)
            self._visible = hits[:400]
        else:
            self._visible = [p for p in self._points
                             if p.type in DEFAULT_TYPES][:400]

        # ★ 半径按屏幕像素给（老代码写死 3.0 场景单位 = 55 米真实大小，
        #   放大后点会涨成一坨糊住底图 —— 见 POINT_RADIUS_PX 注释）。
        r = self._scene_r(POINT_RADIUS_PX)
        pen = _cosmetic_pen("#ffd479")
        brush = QBrush(QColor("#ffb347"))
        for p in self._visible:
            tx, ty = world_to_thumb(*p.xz)
            it = self._scene.addEllipse(
                QRectF(tx - r, ty - r, r * 2, r * 2), pen, brush)
            it.setZValue(2)
            it.setToolTip(
                f"{p.name}（{p.type_cn}）\n"
                f"{p.country} {'/'.join(p.areas)}\n"
                f"X={p.xz[0]:.1f}  Z={p.xz[1]:.1f}")
            self._point_items[p.id] = it
        self.anchors_filtered.emit(list(self._visible))

    def selected_points(self) -> list[TeleportPoint]:
        return list(self._visible)

    def select_point(self, point: TeleportPoint) -> None:
        """把某个传送点选中并把视图移过去。"""
        tx, ty = world_to_thumb(*point.xz)
        for pid, it in self._point_items.items():
            it.setPen(_cosmetic_pen("#ffd479"))
            it.setBrush(QBrush(QColor("#ffb347" if pid != point.id else "#7CFFB2")))
        self._center_on(tx, ty)
        self._last_focus = (tx, ty)
        self._set_marker(tx, ty)
        self._readout.setText(f"已选：{point.name}　"
                              f"X={point.xz[0]:.1f}  Z={point.xz[1]:.1f}")

    # ------------------------------------------------------------------
    # 缩放（B 任务）
    # ------------------------------------------------------------------
    def fit_scale(self) -> float:
        """「整张图刚好放进视图」时的绝对缩放（场景单位 -> 屏幕像素）。"""
        vw, vh = self._view.viewport().width(), self._view.viewport().height()
        if vw <= 0 or vh <= 0:
            return 0.0
        return min(vw / THUMB_WIDTH, vh / THUMB_HEIGHT)

    def zoom_factor(self) -> float:
        """当前倍率，1.0 = 全图。"""
        base = self.fit_scale()
        if base <= 0:
            return 1.0
        return max(MIN_ZOOM, min(MAX_ZOOM,
                                 self._view.transform().m11() / base))

    def set_zoom_factor(self, factor: float, *, from_slider: bool = False,
                        anchor: QPoint | None = None) -> None:
        """把倍率**绝对**设成 ``factor``（1.0 = 全图）。

        :param anchor: 视口坐标下的锚点。给了就"钉住这个场景点"缩放，
            滚轮走这条路径；不给就保持**视图中心**不动（滑块、+/− 按钮走这条）。
        """
        f = max(MIN_ZOOM, min(MAX_ZOOM, float(factor)))
        base = self.fit_scale()
        if base <= 0:
            self._zoom = f
            self._emit_zoom()
            return
        # ★ 缩放**前**先记下要钉住的场景点：变换一改就没法反推了。
        vcenter = self._view.viewport().rect().center()
        hold_center = self._view.mapToScene(vcenter)
        hold_anchor = self._view.mapToScene(anchor) if anchor else None

        # ★ 以 fit 为基准重设，而不是在当前 transform 上再 scale ——
        #   累乘会累积浮点误差，而且一旦被谁改过一次非等比变换就再也纠不回来。
        self._view.resetTransform()
        self._view.scale(base * f, base * f)

        # ★ 缩放**后**把场景点挪回它该在的屏幕位置。
        #   这一步**必须**用滚动条增量，不能用 centerOn()：
        #   centerOn 内部对滚动范围做钳制，鼠标贴边时想要的中心落在
        #   sceneRect 之外就被夹回最近边界 —— 视图"啪"地跳一下。
        if hold_anchor is not None:
            self._pin_to(hold_anchor, anchor)
        else:
            self._pin_to(hold_center, vcenter)

        self._zoom = f
        if not from_slider:
            self._user_adjusted = True
        self._emit_zoom()

    def _pan_by(self, dx: float, dy: float) -> None:
        """把视图内容朝右下方向推 ``dx``/``dy`` **屏幕像素**（可为小数）。

        ⚠️ 滚动条的符号和直觉相反
          值**变大** = 往右滚 = 内容往**左**移。
          所以调用方必须传 ``当前屏幕位置 - 目标屏幕位置``，
          **不是**反过来 —— 传反了锚点会被越推越远
          （实测传反时：左上角缩放漂 316 px，连滚 10 次漂到 -9364 px 外）。
        """
        hs = self._view.horizontalScrollBar()
        vs = self._view.verticalScrollBar()
        if dx:
            hs.setValue(hs.value() + int(round(dx)))
        if dy:
            vs.setValue(vs.value() + int(round(dy)))

    def _screen_of(self, scene_pt: QPointF) -> QPointF:
        """场景点 -> 视口坐标，**亚像素精度**。

        ⚠️ 别用 ``QGraphicsView.mapFromScene``
           它返回整数 ``QPoint``，内部是 ``QPointF::toPoint()`` —— **截断**
           而不是四舍五入，误差是**单向偏置**的。缩放一次一次地攒起来，
           实测连滚 10 次锚点能飘出 7.6 像素：每一步都把"当下那个点"
           钉得死死的，回头看最初那个点却已经跑远了。

        改成用 ``mapToScene`` 的**浮点**返回值反解仿射系数：
        场景→视口是无旋转的线性映射 ``screen = (scene - o) / k + c``，
        取相邻两个视口点做两次 ``mapToScene`` 就能定出 o 和 k
        （实测反推出的 1/k 与真实 ``m11`` 一致到小数点后 6 位）。
        参考点取视口中心附近，避开 ``mapToScene`` 内部的越界钳制。
        """
        vp = self._view.viewport()
        cx, cy = vp.width() // 2, vp.height() // 2
        o = self._view.mapToScene(QPoint(cx, cy))
        kx = self._view.mapToScene(QPoint(cx + 1, cy)).x() - o.x()
        ky = self._view.mapToScene(QPoint(cx, cy + 1)).y() - o.y()
        if abs(kx) < 1e-9 or abs(ky) < 1e-9:
            p = self._view.mapFromScene(scene_pt)
            return QPointF(p)
        return QPointF((scene_pt.x() - o.x()) / kx + cx,
                       (scene_pt.y() - o.y()) / ky + cy)

    def _pin_to(self, scene_pt: QPointF, screen_pt: QPoint) -> None:
        """平移视图，让场景点 ``scene_pt`` 落在屏幕坐标 ``screen_pt`` 上。

        ★ 为什么要迭代精修
          滚动条的取值是**整数**，一次 ``setValue`` 之后必然还剩零点几像素。
          如果只是"估一下、挪一下"就收工，这点残差会**逐次累积**。
          这里反复量、反复挪，直到对齐或顶到边界，于是每一步都从
          "上一个点已经钉死在光标上"出发，漂移无从累积。
          顶到场景边界时也只会多试几次就停 —— 该停就停，不会跳。
        """
        hs = self._view.horizontalScrollBar()
        vs = self._view.verticalScrollBar()
        for _ in range(4):
            at = self._screen_of(scene_pt)
            dx = at.x() - screen_pt.x()
            dy = at.y() - screen_pt.y()
            if abs(dx) < 0.01 and abs(dy) < 0.01:
                return
            h0, v0 = hs.value(), vs.value()
            self._pan_by(dx, dy)
            if hs.value() == h0 and vs.value() == v0:
                return          # 顶到场景边界了，再试也是白试

    def _zoom_at(self, screen_pos: QPoint, factor: float) -> None:
        """以视口坐标 ``screen_pos`` 下的场景点为锚点缩放（滚轮入口）。

        ★ 为什么不用 Qt 自带的 ``AnchorUnderMouse``
          1. ``set_zoom_factor`` 先 ``resetTransform()`` 再 ``scale()``，
             这两步之间 Qt 会拿 ``lastKnownMousePos`` 经过**已被清零**的
             变换去 ``centerOn``，算出来的是另一个场景点（实测中心点缩放
             都能漂 200 场景单位）；
          2. 滚轮事件的 ``position()`` 我们拿得到，却一直被丢掉；
          3. 即便算对了，``centerOn`` 也会被钳制。
          自己算一遍最省心，也不依赖 Qt 的内部状态。
        """
        f = self.zoom_factor()
        target = max(MIN_ZOOM, min(MAX_ZOOM, f * factor))
        if abs(target - f) < 1e-9:
            return                        # 已在上限/下限，什么都不做
        self.set_zoom_factor(target, anchor=screen_pos)

    def zoom_by(self, mult: float) -> None:
        """相对当前倍率再乘一个系数（+/− 按钮：保持视图中心）。"""
        self.set_zoom_factor(self.zoom_factor() * mult)

    def _emit_zoom(self) -> None:
        f = self.zoom_factor()
        self._zoom_lbl.setText(f"{f:.1f}x")
        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(int(round(f * 100)))
        self._zoom_slider.blockSignals(False)
        # ★ 先换底图层，再刷覆盖层：覆盖层的比例尺要按新层的实际
        #   分辨率算，不然跨层那一瞬间刻度是错的。
        self._sync_level()
        self._update_overlays()
        self.zoom_changed.emit(f)

    def fit(self) -> None:
        """回到全图。"""
        self._user_adjusted = False
        self._view.resetTransform()
        self._view.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)
        self._zoom = 1.0
        self._emit_zoom()

    # ------------------------------------------------------------------
    # 定位（D 任务）
    # ------------------------------------------------------------------
    def focus_point(self, point: TeleportPoint, zoom: float = FOCUS_ZOOM) -> None:
        """平移 + 放大到某个传送点（搜索命中后调用）。"""
        tx, ty = world_to_thumb(*point.xz)
        self.set_zoom_factor(zoom)
        self._center_on(tx, ty)
        self._last_focus = (tx, ty)
        self._set_marker(tx, ty)

    def focus_last(self) -> None:
        """"定位"按钮：回到最近点选/选中的位置。

        ⚠️ 当前**没有**"当前位置"数据源 ——
           ``console_bridge.json`` 只有 console/engine/overlay/pathfind 的
           state 与 pid，没有玩家坐标。引擎的 ``StatePacket.position`` 目前
           没进 bridge。等那条链路通了，这里换成读实时坐标即可。
        """
        if self._last_focus is None:
            self.fit()
            return
        tx, ty = self._last_focus
        if self.zoom_factor() <= MIN_ZOOM + 1e-6:
            self.set_zoom_factor(FOCUS_ZOOM)
        self._center_on(tx, ty)

    def _set_marker(self, tx: float, ty: float) -> None:
        """在场景里放一个十字标记（D 任务：点选要有可见反馈）。

        ★ 半径同样按屏幕像素给。老代码 ``r = 9.0`` 是场景单位（= 166 米），
          32x 时绿环半径 288 屏幕像素，正好把一堆金点全吞进去 ——
          用户看到的"绿标记跟金点对不上"就是这个。
        """
        for it in self._marker_parts:
            self._scene.removeItem(it)
        self._marker_parts.clear()
        r = self._scene_r(MARKER_RADIUS_PX)
        pen = _cosmetic_pen("#7CFFB2", 1.6)
        m = self._scene.addEllipse(QRectF(tx - r, ty - r, r * 2, r * 2), pen,
                                   QBrush(Qt.NoBrush))
        m.setZValue(3)
        self._marker_parts.append(m)
        for dx, dy in ((-r * 1.7, 0.0), (r * 1.7, 0.0),
                       (0.0, -r * 1.7), (0.0, r * 1.7)):
            # addLine 要 QLineF/QLine，不收 (QPointF, QPointF, QPen)
            ln = self._scene.addLine(
                QLineF(QPointF(tx + dx * 0.55, ty + dy * 0.55),
                       QPointF(tx + dx, ty + dy)), pen)
            ln.setZValue(3)
            self._marker_parts.append(ln)
        self._marker = m
        self._marker_at = (tx, ty)

    # ------------------------------------------------------------------
    # 覆盖层（C 任务）
    # ------------------------------------------------------------------
    def _update_overlays(self) -> None:
        """比例尺随缩放重算，位置随视图尺寸重排。"""
        m11 = max(1e-9, self._view.transform().m11())
        # 1 场景单位 = THUMB_TO_FULL 世界米；1 屏幕像素 = 1/m11 场景单位
        meters_per_px = THUMB_TO_FULL / m11
        self._scalebar.set_scale(meters_per_px)
        vp = self._view.viewport()
        self._scalebar.move(8, vp.height() - self._scalebar.height() - 6)
        self._north.move(vp.width() - self._north.width() - 8, 8)
        self._scalebar.raise_()
        self._north.raise_()
        # 点和标记是屏幕像素尺寸，跟着 m11 重算（见方法注释）
        self._refresh_screen_geometry()

    def resizeEvent(self, event) -> None:     # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._update_overlays()
        if not self._user_adjusted:
            self.fit()

    def showEvent(self, event) -> None:       # noqa: N802 - Qt 命名
        super().showEvent(event)
        # 首次拿到真实尺寸后再 fit —— 这正是旧代码在 __init__ 里做、
        # 结果算出一个 0.024 缩放的根因。
        if not self._user_adjusted:
            self.fit()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt 命名
        if obj is self._view.viewport() and event.type() == QEvent.Wheel:
            self.wheelEvent(event)
            return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def _scene_pos(self, event) -> QPointF:
        vp = self._view.viewport()
        return self._view.mapToScene(vp.mapFromGlobal(event.globalPosition()).toPoint())

    def _world_at(self, pos: QPointF) -> tuple[float, float] | None:
        if not (0 <= pos.x() < THUMB_WIDTH and 0 <= pos.y() < THUMB_HEIGHT):
            return None
        try:
            return thumb_to_world(pos.x(), pos.y())
        except ValueError as exc:          # 标定被改坏
            logger.warning("坐标反算失败：%s", exc)
            return None

    def _point_at(self, pos: QPointF, screen_radius: float = 7.0) -> TeleportPoint | None:
        """命中测试。**半径按屏幕像素给**，再换算成场景单位。

        ⚠️ 直接拿场景像素当半径会踩这个坑（实测）：
           视图 fitInView 到 900px 宽（2000px 的图 -> 缩放 0.45）时，
           3 场景像素的点只剩 1.3 屏幕像素，鼠标事件还原回场景坐标还有
           ±8px 抖动，**根本点不中**。
           按屏幕像素算半径，缩放多少手感都一样。
        """
        s = max(1e-6, self._view.transform().m11())
        radius = screen_radius / s
        best, best_d = None, radius
        for p in self._visible:
            tx, ty = world_to_thumb(*p.xz)
            d = ((tx - pos.x()) ** 2 + (ty - pos.y()) ** 2) ** 0.5
            if d <= best_d:
                best, best_d = p, d
        return best

    def _on_move(self, event) -> None:
        pos = self._scene_pos(event)
        wx = self._world_at(pos)
        if wx:
            self._readout.setText(
                f"X = {wx[0]:9.1f}    Z = {wx[1]:9.1f}     "
                f"精度 ≈ ±{THUMB_TO_FULL * 0.5:.0f} m")
        self._base_mouse_move(event)   # 交回 Qt 原实现（ScrollHandDrag 靠它）
        self._sync_level()            # 拖完位置变了，瓦片集合要跟着换

    def _on_press(self, event) -> None:
        self._base_mouse_press(event)  # 先让 Qt 的拖动逻辑走
        pos = self._scene_pos(event)
        wx = self._world_at(pos)
        if wx is None:
            return
        # 任何一次点击都留下标记 —— 以前只更新坐标文字，地图上什么都没有。
        self._last_focus = (pos.x(), pos.y())
        self._set_marker(pos.x(), pos.y())
        hit = self._point_at(pos)
        if hit is not None:
            self.select_point(hit)
            self.anchor_picked.emit(hit)
        self.picked.emit(wx[0], wx[1])

    def wheelEvent(self, event) -> None:      # noqa: N802 - Qt 命名
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        # 滚轮事件是投给 viewport 的，position() 就是视口坐标
        self._zoom_at(event.position().toPoint(), factor)
        event.accept()
