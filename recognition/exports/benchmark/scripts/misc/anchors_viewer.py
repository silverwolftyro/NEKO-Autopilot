#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""anchors_viewer.py —— 巨图锚点坐标标定查看器（tkinter + Pillow）· v1

============================================================================
它解决什么问题
============================================================================
系统看图工具都不显示像素坐标，而坐标系标定需要在一张 36864x18432 的
巨大底图上点出几个锚点 (name, x, y)。这个脚本就是干这件事的最小工具：
鼠标悬停实时读原图坐标，左键点一下就记一个命名锚点，存成 JSON。

============================================================================
用法
============================================================================
    D:\\NEKO\\NEKO-Console\\.venv\\Scripts\\python.exe anchors_viewer.py ^
        D:\\NEKO\\recognition\\data\\mihoyo_maps\\teyvat_P0.png

    # 可选：指定存盘位置（默认 = <图片全路径>.anchors.json）
    python anchors_viewer.py teyvat_P0.png --out my_anchors.json

    # 可选：只读模式，禁止写入
    python anchors_viewer.py teyvat_P0.png --read-only

============================================================================
操作
============================================================================
    鼠标移动    底部状态栏显示原图坐标
    左键单击    标点（弹出输入框，输入锚点名 -> 记录 (name, x, y)）
    左键拖动    平移地图（超过 4 像素才算拖动；拖过之后松开不会标点）
    滚轮        缩放（最大 200%），以光标位置为中心
    中键拖动    平移地图（备用，行为同左键拖动）
    右上角 X    退出
    底部按钮    重置视图 / 撤销 / 保存 / 退出

    缩放上限 200%。**超过 100% 用 NEAREST 放大**，即整数像素复制，
    不会插值糊边，标点能精确对准单个像素。

    【没有键盘快捷键，这是故意的】
    以前 S/R/Z/Q/Esc 是用 root.bind_all 全局绑定的，后果是：在弹出来的
    锚点命名框里用中文输入法打拼音时，任何一次按键都会同时命中全局快捷键
    —— "jiazhai" 里的 z 会触发撤销、Esc 会直接退出程序。bind_all 不看焦点，
    对话框也拦不住。与其去做输入法事件过滤（复杂且不可靠），不如直接删掉
    全部键盘绑定，把功能移到按钮上。鼠标事件是绑在 canvas 上的，不受影响。

============================================================================
自动保存
============================================================================
每加一个锚点、每撤销一个锚点，都会立刻写盘，不用手动保存。

写盘是**原子**的：先写 <名字>.anchors.json.tmp，flush + fsync 之后
用 os.replace() 覆盖正式文件。os.replace 在同分区上是原子的，所以
磁盘上的 JSON 任何时刻都是"完整的旧内容"或"完整的新内容"，
不会因为崩在写一半而变成半截文件，也就不会丢掉之前标的点。

保存失败（磁盘满 / 只读 / 权限）只往 stderr 打一行日志，并在状态栏
把标记变成 "保存失败!"，**不弹窗、不阻塞** —— 用户正在标点，
不该被一个保存错误打断。点底部「保存」按钮也走同一条路径。

============================================================================
关键实现（为什么不能照搬"直接 crop"）
============================================================================
本机实测（Pillow 12.3.0 / 36864x18432 RGBA / 16GB 内存）：

    Image.open(path)                  0.04s    不读像素，只读文件头
    首次真正解码                       5.5s    常驻 2.35GB（RGBA 全图 core）
    全图 -> 1/8 BOX resize             7~11s   与上面合计 = 实测启动 16.6s
    解码后再 crop + resize             0.01~0.20s  几乎不涨内存

也就是说：
  * `Image.open()` 是惰性的，但 `crop()` / `resize()` / `reduce()` 都会先
    `self.load()` —— 把【整张 2.7GB RGBA 图】解进内存，而不是"只裁一块"。
    这是 PIL 的行为，不是用法问题。所以"用 crop 避免全图入内存"不成立；
    能避免的只是【重复解码】——解一次之后 crop 就很快了。
  * PNG 没有 `draft()`（那是 JPEG 的），没法让 PIL 只解低分辨率。
    实测 `im.draft("RGB", ...)` 对 PNG 是空操作，size 不变。
  * 因此本脚本的策略是：
      1) 首次解码【只做一次】，并顺手建一层内存金字塔（1/8、1/16 … 1/128，
         约 57MB），供缩小时的视图使用；
      2) 放大时直接从原图 `crop` 一小块 —— 此时解码已完成，crop 是快的；
      3) 全分辨率 crop 的像素量有硬上限（MAX_CROP_PX），超了就降一级，
         免得"缩到整图"时真去 copy 一张 2.7GB 的图。
  * 所有 PIL 操作都在【工作线程】里做，主线程只跑 Tk。
    这是本脚本最重要的一条：首次解码 5.5s、建金字塔 7.4s，如果放在主线程，
    窗口会"未响应"十几秒。放工作线程后界面全程可交互（有加载提示）。
    请求会合并（coalesce），拖动时只渲染最新那一帧。

============================================================================
已知限制 / 排查
============================================================================
[A] 首次打开实测约 16.6 秒（解码 5.5s + 建 1/8 金字塔 7~11s），之后所有
    缩放/平移都是 0.01~0.20s 的即时响应。内存常驻约 2.4GB —— 这是 PIL 解
    RGBA 大图的固有成本，不是本脚本的 bug；本工具没有更省内存的全保真办法。
    期间窗口不会卡死：解码在工作线程里做，界面照常响应（画布上有进度提示）。
[B] 报 MemoryError
    -> 内存真的不够。PIL 一次性要 宽x高x4 字节（本例 2.7GB）。
[C] 报"缺少 Pillow / tkinter"
    -> pip install Pillow；tkinter 随 Python 官方安装包自带，
       若用的是某些精简发行版需重装带 tcl/tk 的 Python。
[D] 锚点存哪了
    -> 默认 <图片全路径>.anchors.json，例如
       teyvat_P0.png.anchors.json，与图片同目录。
[E] 坐标是整数像素
    -> 显示与记录都取整（四舍五入到整像素），符合"精度到整数像素"的要求。
"""

from __future__ import annotations

# ── 发布包路径解析 ─────────────────────────────────────────────────
# ★ 这段由发布构建注入，替代原先散落各处的本机绝对路径。
#   优先级：环境变量 > 从本文件向上找仓库根 > 相对当前目录（响亮失败）。
#   刻意用 ``_pl`` / ``_os`` 私有别名：这些脚本五花八门，
#   用 ``Path`` / ``os`` 直接定义会和它们各自的 import 打架。
import os as _os
import pathlib as _pl


def _neko_repo_root() -> "_pl.Path":
    for _p in _pl.Path(__file__).resolve().parents:
        if (_p / "recognition" / "configs" / "rois.toml").is_file():
            return _p
    return _pl.Path(__file__).resolve().parents[0]


_REPO_ROOT = _neko_repo_root()
RECOG_ROOT = _pl.Path(_os.environ.get("NEKO_RECOGNITION_ROOT", "").strip()
                      or (_REPO_ROOT / "recognition"))
PLUGIN_DIR = _pl.Path(_os.environ.get("NEKO_PLUGIN_DIR", "").strip()
                      or (_REPO_ROOT / "plugin" / "plugins" / "genshin_game_buddy"))
CONSOLE_DIR = _pl.Path(_os.environ.get("NEKO_CONSOLE_DIR", "").strip()
                       or (_REPO_ROOT / "console"))

import argparse
import ctypes
import json
import math
import os
import queue
import sys

# ---- console encoding ----------------------------------------------
# chcp 65001 only changes the console code page; Python still writes bytes
# in its own encoding (cp936/GBK on Chinese Windows), so every Chinese
# string in this script comes out as mojibake. Force both streams UTF-8.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):   # redirected to StringIO -> no method
        _s.reconfigure(encoding="utf-8", errors="replace")
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# ---- 世界坐标换算（可选增强）------------------------------------------
#: ``coord_transform`` 与本脚本同目录。它把底图像素换算成世界坐标，
#: 系数是拟合出来的仿射常量。**缺失/损坏都不影响看图与标点** ——
#: 状态栏只是少显示一段坐标，不是工具不能用了。
#: （``_WORLD_FN`` 用哨兵 None 区分「还没试」和「试过但没有」。）
_WORLD_FN = None
_WORLD_TRIED = False


def _pixel_to_world():
    """返回 ``pixel_to_world(px, py) -> (wx, wz)``，不可用则 None。"""
    global _WORLD_FN, _WORLD_TRIED
    if not _WORLD_TRIED:
        _WORLD_TRIED = True
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from coord_transform import pixel_to_world as _f
            _WORLD_FN = _f
        except Exception as exc:  # noqa: BLE001 - 换算是增强项，不是依赖
            sys.stderr.write(f"[WARN] 世界坐标换算不可用（{exc}）；"
                             f"状态栏只显示像素坐标。\n")
            _WORLD_FN = None
    return _WORLD_FN


#: :func:`_world_to_pixel` 的懒加载哨兵，与上面同构（None = 不可用）。
_W2P_FN = None
_W2P_TRIED = False


def _world_to_pixel():
    """返回 ``world_to_pixel(wx, wz) -> (px, py)``，不可用则 None。

    与 :func:`_pixel_to_world` 严格互逆，用来把路点**画回**画布。
    没有它就只能显示像素坐标、画不出标记 —— 但那时路点点选本来就
    不该可用（世界坐标是它的前提），所以返回 None 是对的。
    """
    global _W2P_FN, _W2P_TRIED
    if not _W2P_TRIED:
        _W2P_TRIED = True
        try:
            if _pixel_to_world() is None:
                raise RuntimeError("coord_transform 整体不可用")
            from coord_transform import world_to_pixel as _f
            _W2P_FN = _f
        except Exception:                             # noqa: BLE001
            _W2P_FN = None
    return _W2P_FN


# ---------------------------------------------------------------- 依赖自检
try:
    from PIL import Image, ImageTk
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "[FATAL] 缺少 Pillow。\n"
        "        安装：D:\\NEKO\\NEKO-Console\\.venv\\Scripts\\python.exe -m pip install Pillow\n"
    )
    raise SystemExit(2)

try:
    import tkinter as tk
    from tkinter import messagebox, simpledialog
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "[FATAL] 缺少 tkinter。\n"
        "        官方 Python 安装包自带；若是最小化发行版，请换成带 tcl/tk 的 Python。\n"
    )
    raise SystemExit(2)

# 36864x18432 = 6.8 亿像素，远超 PIL 默认的"解压炸弹"阈值（约 1.8 亿），
# 不关掉会直接抛 DecompressionBombError。stitch_tiles.py 也是这么做的。
Image.MAX_IMAGE_PIXELS = None

# 画布底色：底图有透明区域（实测 alpha 范围 0~255），需要一层底色衬托
CANVAS_BG = (32, 32, 36)

# 全分辨率 crop 的像素上限。PIL 的 crop 会真的复制一块出来（4 字节/像素），
# 8 倍窗口宽（约 1.1 万像素见方）就上亿像素了，必须设闸。
# 32M 像素 = 128MB 的临时缓冲，实测量级完全可接受（crop 完即释放）。
MAX_CROP_PX = 32_000_000

# 金字塔各级的分母（1/8 是最细的一级）。最细一级刻意不做 1/2、1/4：
# 那两级单张就是 679MB / 170MB，而它们的收益只是少一点放大模糊，
# 直接给 1/8 最多也就放大 1.4 倍，不划算。
PYRAMID_DIVISORS = (8, 16, 32, 64, 128)

# 缩放上下限（相对"适应窗口"的倍数）
ZOOM_MIN_FACTOR = 0.35
# 上限 2.0 = 200%（zoom 就是"屏幕像素/原图像素"，1.0 即 100%）。
# 超过 100% 会走 NEAREST 像素级放大，见 ImageSource._filter_for。
ZOOM_MAX = 2.0

# 左键拖动超过这个像素数才算"拖地图"，否则松开时按"单击"处理 -> 标点
DRAG_THRESHOLD = 4

RENDER_POLL_MS = 16      # 主线程轮询渲染结果的间隔
RESIZE_DEBOUNCE_MS = 160  # 窗口拖动时，等停稳再重渲染


# ============================================================ 数据
# ================================================================== 路点点选
#: 用户点选的路点存到哪。``e2e_pathfind.py --waypoints-file`` 读的就是它。
WAYPOINTS_PATH = Path(RECOG_ROOT / 'data' / 'user_waypoints.json')

#: 路点文件格式版本。**对不上就拒绝加载** —— 宁可让用户重选，
#: 也别拿旧字段名渲染出一串错的坐标让角色往水里走。
WAYPOINTS_SCHEMA = 1

#: 标记画在图上的半径（屏幕像素）。与锚点的 4px 区分开，一眼能认出是路点。
WP_R = 7


def load_waypoints(path: Path) -> list[dict]:
    """读路点文件。**读不了就返回空列表，绝不抛。**

    看图工具因为附属文件出问题就整个打不开，是本末倒置。
    """
    try:
        # utf-8-sig：有 BOM（记事本 / PowerShell 存的）和没 BOM 的都能读
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    if not isinstance(doc, dict):
        return []
    if int(doc.get("schema_version", 0) or 0) != WAYPOINTS_SCHEMA:
        return []
    out = []
    for w in (doc.get("waypoints") or []):
        try:
            out.append({"x": float(w["x"]), "z": float(w["z"]),
                        "note": str(w.get("note", "") or "")})
        except (KeyError, TypeError, ValueError):
            return []          # ★ 半份即判废，不做"尽力修正"
    return out


def dump_waypoints(path: Path, waypoints: list[dict],
                   start: tuple[float, float] | None = None,
                   target: tuple[float, float] | None = None) -> bool:
    """写路点文件。**失败只告警不抛**（同 save 的口径）。

    ★ start / target 由路点序列**首尾自动补齐**（2026-10-08）
      --------------------------------------------------------
    以前这两个字段只认调用方显式传进来的值，而 :meth:`_save_waypoints`
    从来不传 —— 于是存出去的文件永远是 ``"start": null, "target": null``，
    ``e2e_pathfind.py`` 一读就按「target 必须是 [x, z] 两元素」整份拒绝。

    「序列的第一个点是起点、最后一个点是终点」本来就是**结构性事实**，
    所以在**写文件这一层**补齐，而不是指望每个调用方都记得传。
    调用方仍然可以显式传（传了就以它为准），只是不传也不再产出 null。

    ★ waypoints 列表**原样保留全部**（不做去尾）
      ------------------------------------------
    e2e 会逐点走完队列，而「最终目标」取自 target。两者重复一个点无妨，
    反而去掉会让用户数着屏幕上的点对不上，容易以为自己漏选。
    """
    if not waypoints:
        # ★ 空序列没有首也没有尾，硬写 null 等于把问题推给下游。
        #   在这一层就说清楚「先点选」，别让用户对着 e2e 的报错猜。
        sys.stderr.write("[WARN] 没有路点，拒绝保存 —— 先点选路点再 Ctrl+S。\n")
        return False

    wps = [{"x": round(w["x"], 1), "z": round(w["z"], 1),
            "note": w.get("note", "")} for w in waypoints]
    # ★ 只有 None 才降级；显式传了 (0.0, 0.0) 之类也要照写。
    if start is None:
        start = (wps[0]["x"], wps[0]["z"])          # 单点时 == 那个点
    if target is None:
        target = (wps[-1]["x"], wps[-1]["z"])        # 单点时 == 那个点
    doc = {
        "schema_version": WAYPOINTS_SCHEMA,
        "created_at_ms": int(time.time() * 1000),
        "start": [round(start[0], 1), round(start[1], 1)],
        "waypoints": wps,
        "target": [round(target[0], 1), round(target[1], 1)],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except OSError as exc:
        # 本文件没有 logger，一律 stderr（与 _pixel_to_world 的告警同口径）
        sys.stderr.write(f"[WARN] 路点写入失败（{exc}）；"
                         f"文件未更新，本次选点仍在内存里。\n")
        return False
    return True


class Anchor:
    name: str
    x: int
    y: int


@dataclass
class View:
    """一帧视图请求：画布左上角对应的原图坐标 + 缩放 + 画布尺寸。"""
    token: int
    x0: float          # 画布 (0,0) 处的原图坐标
    y0: float
    zoom: float        # 屏幕像素 / 原图像素
    cw: int            # 画布宽（像素）
    ch: int


# ============================================================ 图像源（工作线程独占）
class ImageSource:
    """持有 PIL 对象。**只在工作线程里使用**，绝不跨线程访问。

    PIL 的 Image 不是线程安全的，而且一个 Image 对象被两个线程同时 load()
    会互相踩。这里干脆把"全部图像操作"收进一个线程，主线程只拿渲染好的成品。
    """

    def __init__(self, path: Path):
        self.path = path
        self.img: Image.Image | None = None
        self.width = 0
        self.height = 0
        self.levels: list[tuple[float, Image.Image]] = []  # (scale, image)，细 -> 粗
        self.decode_seconds = 0.0
        self.pyramid_seconds = 0.0

    # ---------------------------------------------------------- 加载
    def load(self, status) -> None:
        """解码一次 + 建金字塔。这是唯一一次全图解码。"""
        t0 = time.perf_counter()
        status(f"正在解码底图（{self.path.name}）…\n"
               f"首次打开需要 10~20 秒，请稍候")
        self.img = Image.open(self.path)
        self.width, self.height = self.img.size

        # 关键：这一步会触发全图解码（PIL 没有 PNG 版的 draft）。
        # 之后 self.img 的 core 就在内存里常驻着，后续 crop 才快。
        lv = self.img.resize(
            (max(1, self.width // PYRAMID_DIVISORS[0]),
             max(1, self.height // PYRAMID_DIVISORS[0])),
            Image.Resampling.BOX,
        )
        self.levels.append((1.0, self.img))                     # 全分辨率
        self.levels.append((1.0 / PYRAMID_DIVISORS[0], lv))
        self.decode_seconds = time.perf_counter() - t0

        t1 = time.perf_counter()
        status("正在建立缩略金字塔…")
        prev = lv
        for div in PYRAMID_DIVISORS[1:]:
            nxt = prev.resize(
                (max(1, self.width // div), max(1, self.height // div)),
                Image.Resampling.BOX,
            )
            self.levels.append((1.0 / div, nxt))
            prev = nxt
        self.pyramid_seconds = time.perf_counter() - t1

    # ---------------------------------------------------------- 渲染
    def _pick_level(self, need_w: float, need_h: float, target_w: float):
        """选一级：在"缩放后仍不低于画布分辨率"的前提下，挑最省的那一级。

        条件即 level.scale >= 视图缩放比；levels 是从细到粗排的，取满足条件的
        最小 scale，就正好是"刚好够清晰"的那一级（标准 mipmap 选级）。
        """
        best = None
        best_w = None
        for scale, im in self.levels:
            sw = need_w * scale
            if sw >= target_w - 0.5 and (best_w is None or sw < best_w):
                best, best_w = im, sw
        if best is None:
            # 没有任何一级"够清晰"。典型触发条件：zoom > 1（放大超过 100%），
            # 此时需要的原图区域比画布还小，条件 scale >= zoom 恒不成立。
            # 这种情况要的是【最细】的一级凑合放大 —— 绝不能退回最粗一级，
            # 那会糊成一团。这正是">100% 看着像被卡在 100%"的根因。
            best = self.levels[0][1]

        # 闸门：选中的是全分辨率、而需要的区域又太大 -> 退一级，
        # 宁可稍微放大一点也不去 copy 一张上亿像素的图。
        if best is self.img and need_w * need_h > MAX_CROP_PX:
            for scale, im in self.levels:
                if im is not self.img:
                    best = im
                    break
        return best

    @staticmethod
    def _filter_for(src_w: float, dst_w: float, nearest: bool = False):
        """按缩放倍率挑重采样。

        nearest=True 用于"放大超过 100%"：NEAREST 就是整数像素复制，
        等价于"把原图那几个像素直接铺开"，不会有 LANCZOS/BILINEAR 的
        插值糊边，标点能精确对准某一个像素。
        """
        if nearest:
            return Image.Resampling.NEAREST
        if dst_w <= 0:
            return Image.Resampling.BILINEAR
        factor = src_w / dst_w
        if factor >= 2.0:
            return Image.Resampling.BOX
        if factor >= 1.0:
            return Image.Resampling.LANCZOS
        return Image.Resampling.BILINEAR

    def render(self, view: View) -> Image.Image:
        """把 view 描述的那一屏渲染成画布大小的 RGB 图。"""
        if self.img is None:
            raise RuntimeError("图像尚未加载")

        out = Image.new("RGB", (view.cw, view.ch), CANVAS_BG)

        # 视图覆盖的原图矩形（可能超出图片边界）
        sw = view.cw / view.zoom
        sh = view.ch / view.zoom
        img_x0, img_y0 = float(self.width), float(self.height)

        # 与图片求交，得到真正要画的区域
        ix0 = max(0.0, view.x0)
        iy0 = max(0.0, view.y0)
        ix1 = min(img_x0, view.x0 + sw)
        iy1 = min(img_y0, view.y0 + sh)
        if ix1 - ix0 < 1 or iy1 - iy0 < 1:
            return out  # 完全在图片外（缩得太小又拖远了），纯底色

        need_w, need_h = ix1 - ix0, iy1 - iy0
        level = self._pick_level(need_w, need_h, need_w * view.zoom)
        scale = 1.0 if level is self.img else (level.width / self.width)

        # 在该级上取整到像素，并夹进边界
        bx0 = max(0, int(ix0 * scale))
        by0 = max(0, int(iy0 * scale))
        bx1 = min(level.width, int(ix1 * scale + 0.999))
        by1 = min(level.height, int(iy1 * scale + 0.999))
        if bx1 <= bx0 or by1 <= by0:
            return out

        tile = level.crop((bx0, by0, bx1, by1))

        # 目标尺寸 / 位置（把"被裁掉的部分"换算成画布上的偏移）
        dw = max(1, int(round(need_w * view.zoom)))
        dh = max(1, int(round(need_h * view.zoom)))
        dx = int(round((ix0 - view.x0) * view.zoom))
        dy = int(round((iy0 - view.y0) * view.zoom))

        tile = tile.resize((dw, dh), self._filter_for(
            tile.width, dw,
            # >100% 且拿的是原图 -> 像素级放大，不插值
            nearest=(view.zoom > 1.0 and level is self.img)))
        if tile.mode == "RGBA":
            out.paste(tile, (dx, dy), tile)   # 用自身 alpha 合成到画布底色
        else:
            out.paste(tile, (dx, dy))
        return out


# ============================================================ 主应用
class AnchorsViewer:
    def __init__(self, root: tk.Tk, image_path: Path, out_path: Path,
                 read_only: bool):
        self.root = root
        self.image_path = image_path
        self.out_path = out_path
        self.read_only = read_only

        self.anchors: list[Anchor] = []
        self.anchor_seq = 0
        #: 用户点选的路点（世界坐标 + 备注）。**与 anchors 是两回事**：
        #: anchors 是给图像配准用的命名点，这里是给 e2e 寻路用的顺序路点。
        self.waypoints: list[dict] = []
        self.waypoints_path: Path = WAYPOINTS_PATH

        self.ready = False
        self.zoom = 1.0
        self.cx = 0.0          # 画布中心对应的原图坐标
        self.cy = 0.0
        self.mouse = (0, 0)    # 最近一次鼠标画布坐标
        self.token = 0
        self.photo = None      # 必须持引用，否则 PhotoImage 会被回收 -> 图消失

        self._resize_job = None
        self._press = None

        # ---- 拖动（左键 / 中键共用一套状态）----
        self._dragging = False
        self._pan_cx = 0.0     # 按下瞬间的视图中心，拖动按"位移"算而不是累加，
        self._pan_cy = 0.0     # 避免浮点误差累积导致地图慢慢漂走

        # ---- 窗口存活 / after 回调登记 ----
        # 关窗后残留的 after 回调会调 self.root.title(...) 直接抛 TclError
        # （"application has been destroyed"）。所以：所有 after 都登记 id，
        # 关窗时统一取消；且每个入口再查一次 _alive()。
        self._closed = False
        self._after_ids: set[str] = set()

        # ---- 保存状态 ----
        self._dirty = False
        self._save_note = "未保存"
        self._flash_text = ""

        # ---- 线程通信：主线程给请求，工作线程给成品 ----
        self.inbox: queue.Queue = queue.Queue()
        self.outbox: queue.Queue = queue.Queue()
        self.source = ImageSource(image_path)

        self._build_ui()
        self._load_existing_anchors()
        self._start_worker()
        self._after(RENDER_POLL_MS, self._pump)

    # ---------------------------------------------------------- 窗口存活
    def _alive(self) -> bool:
        """窗口还在吗？关窗后任何改 UI 的调用都要先过这一关。"""
        if self._closed:
            return False
        try:
            return bool(self.root.winfo_exists())
        except tk.TclError:
            return False

    def _after(self, ms: int, fn) -> str | None:
        """登记式 after：关窗时能一次性取消，回调里再兜一层存活检查。"""
        if not self._alive():
            return None
        aid: str | None = None

        def wrapper() -> None:
            if aid is not None:
                self._after_ids.discard(aid)
            if not self._alive():
                return
            fn()

        aid = self.root.after(ms, wrapper)
        self._after_ids.add(aid)
        return aid

    def _cancel_after(self, aid: str | None) -> None:
        if aid is None:
            return
        self._after_ids.discard(aid)
        try:
            self.root.after_cancel(aid)
        except (tk.TclError, ValueError):
            pass

    def _cancel_all_after(self) -> None:
        for aid in list(self._after_ids):
            self._cancel_after(aid)

    def _button_cmd(self, fn):
        """底部按钮的统一入口：窗口已销毁就直接吞掉。

        真正的护栏其实是 simpledialog —— 它 grab_set() 之后输入被对话框独占，
        主窗口的按钮根本点不到，所以标点命名框开着时不会误触。
        这层 _alive() 是兜底：防止关窗过程中残留的按钮回调踩到已死的 root。
        """
        def handler() -> None:
            if not self._alive():
                return
            fn()
            # 按钮会把焦点抢走，而滚轮在 Windows 上是按焦点投递的 ——
            # 点完按钮把焦点还给画布，免得滚轮"点了按钮之后就失灵"。
            if self._alive():
                self.canvas.focus_set()
        return handler

    # ---------------------------------------------------------- UI
    def _build_ui(self) -> None:
        self.root.title(f"{self.image_path.name} — 锚点坐标查看器")
        self.root.geometry("1280x800")
        self.root.configure(bg="#101014")

        self.canvas = tk.Canvas(self.root, bg="#202024", highlightthickness=0,
                                cursor="crosshair")
        self.canvas.pack(side="top", fill="both", expand=True)

        bar = tk.Frame(self.root, bg="#16161a")
        bar.pack(side="bottom", fill="x")
        self.lbl_pos = tk.Label(bar, text="原图坐标: —", bg="#16161a",
                                fg="#d8d8e0", anchor="w",
                                font=("Consolas", 10))
        self.lbl_pos.pack(side="left", padx=10, pady=4)
        # ★ 路点序列**独立一个 label**：``lbl_pos`` 是鼠标每动一下就刷的
        #   悬停坐标，两者共用一个会互相覆盖 —— 鼠标一动就把路点列表冲掉。
        self.lbl_wp = tk.Label(bar, text="", bg="#16161a", fg="#ffd640",
                               anchor="w", font=("Consolas", 10))
        self.lbl_wp.pack(side="left", padx=(0, 10), pady=4)
        self.lbl_info = tk.Label(bar, text="", bg="#16161a", fg="#8fa8c8",
                                 anchor="e", font=("Consolas", 10))
        self.lbl_info.pack(side="right", padx=10, pady=4)

        # 底部按钮栏 —— 取代删掉的键盘快捷键。
        # pack 顺序：canvas(top) -> 状态栏(bottom，贴最底) -> 按钮栏(bottom，在状态栏上方)
        btns = tk.Frame(self.root, bg="#1c1c22")
        btns.pack(side="bottom", fill="x")
        for text, fn in (("重置视图", self.reset_view),
                         ("撤销", self.undo),
                         ("保存", self.save),
                         ("退出", self.quit)):
            tk.Button(btns, text=text, command=self._button_cmd(fn),
                      bg="#2a2a32", fg="#e0e0e8", activebackground="#3a3a46",
                      activeforeground="#ffffff", relief="flat", bd=0,
                      padx=16, pady=5, cursor="hand2",
                      font=("Microsoft YaHei", 9)).pack(side="left",
                                                        padx=(6, 0), pady=5)

        # 加载提示直接画在画布中间
        self.hint = self.canvas.create_text(
            640, 400, text="正在打开图片…", fill="#9ad0ff",
            font=("Microsoft YaHei", 14), tags="hint")

        # ---- 事件（只用鼠标：键盘快捷键已全部删除，原因见文件头"操作"）----
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        # 左键：单击标点 / 拖动拖地图（用 B1-Motion，只在按住左键时触发）
        self.canvas.bind("<Button-1>", self._on_lpress)
        self.canvas.bind("<B1-Motion>", self._on_ldrag)
        self.canvas.bind("<ButtonRelease-1>", self._on_lrelease)
        self.canvas.bind("<Button-3>", self._on_rpress)      # 右键=删最近路点
        self.canvas.bind("<Key>", self._on_key)             # Ctrl+C/S/O
        # 中键拖动（备用，行为同左键拖动）
        self.canvas.bind("<Button-2>", self._on_mpress)     # Windows 中键
        self.canvas.bind("<B2-Motion>", self._on_mdrag)
        self.canvas.bind("<ButtonRelease-2>", self._on_mrelease)
        self.canvas.bind("<MouseWheel>", self._on_wheel)     # Windows / macOS
        self.canvas.bind("<Button-4>", self._on_wheel_x11)   # X11 上滚
        self.canvas.bind("<Button-5>", self._on_wheel_x11)   # X11 下滚
        self.canvas.bind("<Configure>", self._on_configure)
        # 画布保留焦点：Windows 上 Tk 的滚轮事件是按焦点投递的（丢了焦点
        # 滚轮可能没反应）。画布不绑任何键，拿焦点不会误触发别的动作。
        self.canvas.focus_set()

    # ---------------------------------------------------------- 锚点持久化
    def _load_existing_anchors(self) -> None:
        if not self.out_path.exists():
            self._save_note = "未保存"
            return
        try:
            with self.out_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            pts = data.get("points") or data.get("anchors") or []
            for p in pts:
                self.anchors.append(Anchor(str(p["name"]), int(p["x"]), int(p["y"])))
            self.anchor_seq = len(self.anchors)
            self._save_note = f"已载入 {len(self.anchors)} 个"
        except (OSError, ValueError, KeyError, TypeError) as exc:
            sys.stderr.write(f"[WARN] 读不了已有锚点 {self.out_path}：{exc}（按空处理）\n")
            self._save_note = "未保存"

    def build_payload(self) -> dict:
        """保存用的数据结构（单独拆出来，便于无窗口自测）。

        格式与旧版完全一致（image / image_size / saved_at / points），
        读取端同时认 "points" 和旧的 "anchors" 键，所以老文件照样能读。
        """
        return {
            "image": str(self.image_path),
            "image_size": [self.source.width, self.source.height],
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "points": [{"name": a.name, "x": a.x, "y": a.y} for a in self.anchors],
        }

    def save(self, auto: bool = False) -> bool:
        """写盘。**原子**：先写 .tmp，fsync，再 os.replace 覆盖。

        os.replace 在同分区上是原子操作，所以任何时刻磁盘上的
        .anchors.json 要么是旧的完整内容、要么是新的完整内容，
        不会出现"崩在写一半"导致的半截 JSON。

        auto=True（加/撤销锚点后的自动保存）失败时只打日志，
        既不弹窗也不阻塞 —— 用户正在标点，不该被一个保存错误打断。
        """
        if self.read_only:
            self._save_note = "只读模式"
            if not auto:
                self._flash("只读模式：不保存")
            return False

        payload = self.build_payload()
        tmp = self.out_path.with_name(self.out_path.name + ".tmp")
        try:
            tmp.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())      # 先把字节压到盘上，再换名字
            os.replace(tmp, self.out_path)  # 原子替换
        except OSError as exc:
            sys.stderr.write(f"[WARN] 保存失败 {self.out_path}：{exc}\n")
            try:
                if tmp.exists():
                    tmp.unlink()            # 别留半截 .tmp 在目录里
            except OSError:
                pass
            self._dirty = True
            self._save_note = "保存失败!"
            self._update_info()
            if not auto:
                self._flash(f"保存失败：{exc}")
            return False

        self._dirty = False
        self._save_note = datetime.now().strftime("已保存 %H:%M:%S")
        self._update_info()
        print(f"[SAVE] {self.out_path}  ({len(self.anchors)} 个锚点)"
              + ("  (自动)" if auto else ""), flush=True)
        if not auto:
            self._flash(f"已保存 {len(self.anchors)} 个锚点 -> {self.out_path.name}")
        return True

    # ---------------------------------------------------------- 坐标换算
    def _canvas_size(self) -> tuple[int, int]:
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        return (cw if cw > 1 else 1280), (ch if ch > 1 else 800)

    def _view_x0(self, cw: int) -> float:
        return self.cx - (cw / 2.0) / self.zoom

    def _view_y0(self, ch: int) -> float:
        return self.cy - (ch / 2.0) / self.zoom

    def canvas_to_source(self, mx: float, my: float) -> tuple[float, float]:
        cw, ch = self._canvas_size()
        return (self._view_x0(cw) + mx / self.zoom,
                self._view_y0(ch) + my / self.zoom)

    def _fit_zoom(self) -> float:
        cw, ch = self._canvas_size()
        if not self.source.width:
            return 1.0
        return min(cw / self.source.width, ch / self.source.height)

    # ---------------------------------------------------------- 事件
    def _on_motion(self, ev) -> None:
        if not self._alive():
            return
        self.mouse = (ev.x, ev.y)
        if not self.ready:
            return
        sx, sy = self.canvas_to_source(ev.x, ev.y)
        inside = 0 <= sx < self.source.width and 0 <= sy < self.source.height
        mark = "" if inside else "  (图外)"
        self.lbl_pos.configure(
            text=f"原图坐标: x={int(round(sx))}  y={int(round(sy))}"
                 f"{mark}{self._world_suffix(ev.x, ev.y)}")
        self._update_info(full=False)

    def _mouse_to_world(self, canvas_x: float, canvas_y: float
                        ) -> tuple[tuple[float, float] | None, str]:
        """画布坐标 -> 世界坐标。**悬停与点击唯一的换算入口。**

        :returns: ``((wx, wz), "")`` 或 ``(None, 失败原因)``。

        ★★★ 为什么必须有这个函数（2026-10-06 真机踩出来的）
          --------------------------------------------------
          之前悬停走 ``wx, wz = fn(sx, sy)``，点击走
          ``wx, wz = float(fn(sx, sy))`` —— **只差一个 ``float()``**。
          而 ``fn`` 返回的是 **2 元组**��``float(2元组)`` 直接抛
          ``TypeError``，又正好被 ``except Exception`` 吞掉，
          表现就是「悬停有 world 坐标、点击永远说换算失败」。

          一行之差、且被 except 掩盖，排查成本极高。两条路径现在共用
          这一个函数，**结构上不可能再分叉**。

        ★ 诊断一律走 stderr，并带完整 traceback
          --------------------------------------
          状态栏只有一行字，装不下 traceback。以前那种「catch 住就
          ``_flash('换算失败')``」等于把根因扔了 —— 而失败恰恰是
          最需要根因的时候。
        """
        sys.stderr.write(f"[WP] mouse_to_world canvas=({canvas_x}, {canvas_y})\n")
        fn = _pixel_to_world()
        if fn is None:
            why = "coord_transform.pixel_to_world 不可用（import 期自检失败）"
            sys.stderr.write(f"[WP] 失败在 <载入 pixel_to_world>: {why}\n")
            return None, why
        try:
            sx, sy = self.canvas_to_source(canvas_x, canvas_y)
        except Exception as exc:                        # noqa: BLE001
            sys.stderr.write(f"[WP] 失败在 <canvas_to_source>: "
                             f"{type(exc).__name__}: {exc}\n")
            traceback.print_exc()
            return None, f"画布→底图像素失败（{type(exc).__name__}: {exc}）"
        sys.stderr.write(f"[WP] 底图像素=({sx:.1f}, {sy:.1f})\n")
        try:
            # ★ 这里**只能**解包，不能再套 float()：fn 返回的是 2 元组。
            wx, wz = fn(float(sx), float(sy))
            wx, wz = float(wx), float(wz)   # 归一成 float，不改变结构
        except Exception as exc:                        # noqa: BLE001
            sys.stderr.write(f"[WP] 失败在 <pixel_to_world>: "
                             f"{type(exc).__name__}: {exc}\n")
            traceback.print_exc()
            return None, (f"底图像素→世界坐标失败（{type(exc).__name__}: "
                          f"{exc}）")
        sys.stderr.write(f"[WP] 世界坐标=({wx:.2f}, {wz:.2f})\n")
        return (wx, wz), ""

    def _world_suffix(self, canvas_x: float, canvas_y: float) -> str:
        """悬停用的 world 后缀。**走 :meth:`_mouse_to_world`**。

        ★ 换算不可用时**不打断看图**（本函数在鼠标每动一下时都会调），
          但把**具体原因**显示出来，并指向 stderr 看 traceback。
        """
        w, why = self._mouse_to_world(canvas_x, canvas_y)
        if w is None:
            return f"   [world: {why}；详情见 stderr]"
        return f"   world: x={w[0]:9.2f}  z={w[1]:9.2f}"

    def _on_leave(self, _ev) -> None:
        if not self._alive():
            return
        self.lbl_pos.configure(text="原图坐标: —")

    # ---------------------------------------------------- 平移（左键 / 中键）
    def _pan_begin(self, ev) -> None:
        self._press = (ev.x, ev.y)
        self._pan_cx, self._pan_cy = self.cx, self.cy
        self._dragging = False

    def _pan_move(self, ev, need_threshold: bool) -> None:
        if self._press is None or not self.ready:
            return
        dx = ev.x - self._press[0]
        dy = ev.y - self._press[1]
        if need_threshold and not self._dragging:
            if abs(dx) <= DRAG_THRESHOLD and abs(dy) <= DRAG_THRESHOLD:
                return          # 还在"单击"的容差内，先不动
            self._dragging = True
            if self._alive():
                self.canvas.configure(cursor="fleur")
        # 从"按下瞬间的中心"直接算，而不是逐帧累加 —— 地图严格跟手、不漂
        self.cx = self._pan_cx - dx / self.zoom
        self.cy = self._pan_cy - dy / self.zoom
        self._request_render()

    def _pan_end(self) -> bool:
        """收尾，返回"这次是否真的拖动过"。"""
        was = self._dragging
        self._press = None
        self._dragging = False
        if self._alive():
            self.canvas.configure(cursor="crosshair")
        return was

    def _on_lpress(self, ev) -> None:
        self._pan_begin(ev)

    def _on_ldrag(self, ev) -> None:
        self._pan_move(ev, need_threshold=True)

    def _on_lrelease(self, ev) -> None:
        """左键：拖动过 -> 平移；没拖动（≤4px）-> 放一个点。

        ★ 裸左键放**路点**（即时编号，无对话框）
          --------------------------------------------
          选一条 5 段的路要点 5 次；每次弹一次「锚点名称」对话框，
          就是把这个功能做得没人愿意用。所以裸左键 = 路点，
          命名锚点挪到 **Shift+左键**（原有功能保留，一行没删）。

        判「有没有拖过」用的是 :meth:`_pan_end` 的返回值 —— 这套
        click/drag 区分本来就存在（平移要用），不是新加的机制。
        """
        if self._press is None or not self.ready:
            self._pan_end()
            return
        dragged = self._pan_end()
        if dragged:
            return              # 刚才是拖地图，不当成标点

        sx, sy = self.canvas_to_source(ev.x, ev.y)
        if not (0 <= sx < self.source.width and 0 <= sy < self.source.height):
            self._flash("点在图片外面了，不记录")
            return

        if ev.state & 0x0001:            # Shift 按着 -> 走命名锚点那条老路
            self._add_named_anchor(sx, sy)
            return
        self._add_waypoint(ev.x, ev.y)     # ← 传画布坐标，让它自己换算

    def _add_named_anchor(self, sx: float, sy: float) -> None:
        """Shift+左键：原有���命名锚点。**逻辑一字未改**，只是换了触发键。"""
        self.anchor_seq += 1
        default = f"anchor{self.anchor_seq}"
        px, py = int(round(sx)), int(round(sy))
        name = simpledialog.askstring(
            "记录锚点",
            f"原图坐标  x={px}  y={py}\n\n锚点名称：",
            initialvalue=default, parent=self.root)
        # 对话框关掉后焦点不一定回得来，手动还给画布（滚轮要它）
        if self._alive():
            self.canvas.focus_set()
        if not name:
            self.anchor_seq -= 1
            self._flash("已取消")
            return
        self.anchors.append(Anchor(name.strip(), px, py))
        self._redraw_anchors()
        # 关键：加完立刻落盘，别等用户手动保存（崩溃就全丢了）
        self.save(auto=True)
        self._flash(f"已记录 {name.strip()} = ({px}, {py})")

    def _add_waypoint(self, canvas_x: float, canvas_y: float) -> None:
        """裸左键：放一个编号路点。**没有对话框** —— 这是它能好用的原因。

        ★ 走 :meth:`_mouse_to_world`，与悬停**同一个**换算。
          之前这里多套了一层 ``float(fn(...))``，而 ``fn`` 返回 2 元组，
          于是必然 TypeError —— 又被 ``except Exception`` 吞掉，表现为
          「悬停正常、点击永远失败」。现在结构上不可能再分叉。
        """
        w, why = self._mouse_to_world(canvas_x, canvas_y)
        if w is None:
            self._flash(f"world 坐标换算失败：{why}（详情见 stderr）")
            return
        wx, wz = w
        self.waypoints.append({"x": wx, "z": wz, "note": ""})
        self._redraw_waypoints()
        self._update_info(full=True)
        self._flash(f"已选路点 {len(self.waypoints)}：({wx:.0f}, {wz:.0f})")

    def _on_rpress(self, ev) -> None:
        """右键：删掉**离光标最近**的那个路点。

        为什么是「最近」而不是「最后一个」：改主意时用户点的是刚看过的
        那个点，而那个点未必是最后加的。
        """
        if not self._alive():
            return
        self.canvas.focus_set()
        if not self.waypoints:
            self._flash("还没有路点可删")
            return
        best, best_d = None, None
        for i, w in enumerate(self.waypoints):
            pos = self._wp_canvas_xy(w)
            if pos is None:
                continue
            d = math.hypot(pos[0] - ev.x, pos[1] - ev.y)
            if best_d is None or d < best_d:
                best, best_d = i, d
        if best is None:
            self._flash("world 坐标换算不可用，删不了")
            return
        if best_d > WP_R * 6:            # 离得太远，不猜
            self._flash(f"离光标太远，没删（最近的在 {best_d:.0f}px 外）")
            return
        gone = self.waypoints.pop(best)
        self._redraw_waypoints()
        self._update_info(full=True)
        self._flash(f"已删除路点 {best + 1}：({gone['x']:.0f}, {gone['z']:.0f})")

    def _redraw_waypoints(self) -> None:
        """路点画成独立的 canvas item —— 加/删一个点**不触发重渲染**。

        跟 :meth:`_redraw_anchors` 同思路：图像是重渲染出来的（贵），
        标记是叠在上面的（便宜）。点选是高频操作，走后一条路才不卡。
        """
        if not self._alive():
            return
        self.canvas.delete("wp")
        if not self.ready:
            return
        cw, ch = self._canvas_size()
        for i, w in enumerate(self.waypoints, 1):
            pos = self._wp_canvas_xy(w)
            if pos is None:
                continue
            mx, my = pos
            if not (-40 <= mx <= cw + 40 and -40 <= my <= ch + 40):
                continue        # 视野外，不画（也省得画一堆看不见的点）
            self.canvas.create_oval(mx - WP_R, my - WP_R, mx + WP_R, my + WP_R,
                                    outline="#0b0b0f", width=2, fill="#ffd640",
                                    tags="wp")
            self.canvas.create_text(mx, my, text=str(i), fill="#1a1a1f",
                                    font=("Consolas", 9, "bold"), tags="wp")

    def _waypoints_json(self) -> str:
        """当前路点序列（给剪贴板 / 状态栏用）。"""
        return json.dumps({"schema_version": WAYPOINTS_SCHEMA,
                           "waypoints": [{"x": round(w["x"], 1),
                                          "z": round(w["z"], 1),
                                          "note": w.get("note", "")}
                                         for w in self.waypoints]},
                          ensure_ascii=False, indent=2)

    def _copy_waypoints(self) -> None:
        """Ctrl+C：复制到剪贴板。**tk 自带剪贴板，不引任何依赖。**"""
        if not self._alive():
            return
        if not self.waypoints:
            self._flash("还没有路点可复制")
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self._waypoints_json())
            self._flash(f"已复制 {len(self.waypoints)} 个路点到剪贴板")
        except tk.TclError as exc:
            self._flash(f"复制失败：{exc}")

    def _save_waypoints(self, auto: bool = False) -> None:
        if not self._alive():
            return
        if not self.waypoints:
            if not auto:
                self._flash("还没有路点，没什么可保存")
            return
        if dump_waypoints(self.waypoints_path, self.waypoints):
            if not auto:
                self._flash(f"已保存 {len(self.waypoints)} 个路点 → "
                            f"{self.waypoints_path}")
        else:
            self._flash(f"保存失败：{self.waypoints_path}")

    def _load_waypoints(self) -> None:
        """Ctrl+O：从文件加载。**读不出来就明说，绝不清空已有路点。**"""
        if not self._alive():
            return
        try:
            from tkinter import filedialog            # noqa: PLC0415
            p = filedialog.askopenfilename(
                title="加载路点", initialdir=str(self.waypoints_path.parent),
                filetypes=[("路点 JSON", "*.json"), ("全部", "*.*")])
        except tk.TclError as exc:
            self._flash(f"打开对话框失败：{exc}")
            return
        if not p:
            return
        got = load_waypoints(Path(p))
        if not got:
            self._flash(f"{Path(p).name} 里没有可用的路点（版本不符或格式错）")
            return
        self.waypoints = got
        self.waypoints_path = Path(p)
        self._redraw_waypoints()
        self._update_info(full=True)
        self._flash(f"已加载 {len(got)} 个路点 ← {self.waypoints_path.name}")

    def _on_key(self, ev) -> None:
        """Ctrl+C / Ctrl+S / Ctrl+O。

        ★ 要判 Ctrl 位而**不是**用 ``ev.state & 0x4``
          ------------------------------------------
          0x4 在 X11 上是 Super/Command，在 Windows 上才是 Control。
          只在 Windows 跑的话两者一致，但这里跟 :meth:`_on_lrelease`
          里判 Shift 用的是同一种写法，保持一致。
        """
        if not self._alive():
            return
        k = (ev.keysym or "").lower()
        ctrl = bool(ev.state & 0x0004)
        if ctrl and k == "c":
            self._copy_waypoints()
        elif ctrl and k == "s":
            self._save_waypoints()
        elif ctrl and k == "o":
            self._load_waypoints()
        else:
            return
        return "break"          # 别让 Ctrl+C / Ctrl+S 触发 tk 默认行为

    def _wp_canvas_xy(self, w: dict) -> tuple[float, float] | None:
        """路点（世界坐标）-> 画布坐标。**换算不可用就返回 None。**

        world -> 原图像素 -> 画布，两步都跟 :meth:`canvas_to_source`
        严格互逆 —— 所以画出来的标记正好落在当初点下去的那个像素上。
        """
        fn = _world_to_pixel()
        if fn is None:
            sys.stderr.write("[WP] 失败在 <载入 world_to_pixel>: "
                             "coord_transform.world_to_pixel 不可用\n")
            traceback.print_exc()
            return None
        try:
            sx, sy = fn(float(w["x"]), float(w["z"]))
        except Exception:                              # noqa: BLE001
            # 同样不能静默：这正是「点得下、画不出来 / 删不掉」的根因所在。
            sys.stderr.write(f"[WP] 失败在 <world_to_pixel> "
                             f"world=({w.get('x')}, {w.get('z')})\n")
            traceback.print_exc()
            return None
        cw, ch = self._canvas_size()
        return ((sx - self._view_x0(cw)) * self.zoom,
                (sy - self._view_y0(ch)) * self.zoom)

    def _on_mpress(self, ev) -> None:
        self._pan_begin(ev)

    def _on_mdrag(self, ev) -> None:
        self._pan_move(ev, need_threshold=False)   # 中键按下即拖，不要阈值

    def _on_mrelease(self, _ev) -> None:
        self._pan_end()

    def _zoom_at(self, mx: float, my: float, factor: float) -> None:
        if not self.ready or not self._alive():
            return
        sx, sy = self.canvas_to_source(mx, my)   # 缩放前，光标下的原图坐标
        fit = self._fit_zoom()
        self.zoom = max(fit * ZOOM_MIN_FACTOR, min(ZOOM_MAX, self.zoom * factor))
        cw, ch = self._canvas_size()
        # 让 (sx, sy) 缩放后仍停在光标处
        self.cx = sx + (cw / 2.0 - mx) / self.zoom
        self.cy = sy + (ch / 2.0 - my) / self.zoom
        self._request_render()
        self._update_info()

    def _on_wheel(self, ev) -> None:
        self._zoom_at(ev.x, ev.y, 1.18 ** (ev.delta / 120.0))

    def _on_wheel_x11(self, ev) -> None:
        self._zoom_at(ev.x, ev.y, 1.18 if ev.num == 4 else 1 / 1.18)

    def _on_configure(self, _ev) -> None:
        """窗口尺寸变化：debounce 一下再重渲染，别每个事件都算。"""
        self._cancel_after(self._resize_job)
        self._resize_job = self._after(RESIZE_DEBOUNCE_MS, self._on_resize_done)

    def _on_resize_done(self) -> None:
        self._resize_job = None
        if not self.ready:
            return
        self._request_render()

    # ---------------------------------------------------------- 视图操作
    def reset_view(self) -> None:
        if not self.ready or not self._alive():
            return
        self.zoom = self._fit_zoom()
        self.cx = self.source.width / 2.0
        self.cy = self.source.height / 2.0
        self._request_render()
        self._update_info()

    def undo(self) -> None:
        if not self.anchors:
            self._flash("没有可撤销的锚点")
            return
        a = self.anchors.pop()
        self._redraw_anchors()
        self.save(auto=True)      # 撤销同样立刻落盘，盘上始终等于屏幕上的
        self._flash(f"已撤销 {a.name}")

    def quit(self) -> None:
        """收尾：先掐掉所有 after 回调，再销毁窗口。

        顺序很重要 —— 先把 _closed 立起来，之后任何残留回调都会自己退出，
        否则会撞上 "can't invoke wm command: application has been destroyed"。
        """
        if self._closed:
            return
        self._closed = True
        self._cancel_all_after()
        self.inbox.put(None)       # 让工作线程退出
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    # ---------------------------------------------------------- 渲染调度
    def _request_render(self) -> None:
        if not self.ready or not self._alive():
            return
        cw, ch = self._canvas_size()
        self.token += 1
        self.inbox.put(View(token=self.token,
                            x0=self._view_x0(cw), y0=self._view_y0(ch),
                            zoom=self.zoom, cw=cw, ch=ch))

    def _pump(self) -> None:
        """主线程定时取工作线程的成品。after() 驱动，没有死循环。"""
        if not self._alive():
            return
        try:
            while True:
                msg = self.outbox.get_nowait()
                kind = msg[0]
                if kind == "status":
                    self._set_hint(msg[1])
                elif kind == "image":
                    _, token, pil = msg
                    if token == self.token:          # 丢弃过期帧
                        self._show_image(pil)
                elif kind == "ready":
                    _, cw, ch = msg
                    self.ready = True
                    self._set_hint(None)
                    print(f"[LOAD] {self.source.width}x{self.source.height}  "
                          f"解码 {self.source.decode_seconds:.1f}s + "
                          f"金字塔 {self.source.pyramid_seconds:.1f}s", flush=True)
                    self._flash(f"已加载（解码 {self.source.decode_seconds:.1f}s）")
                    self.reset_view()
                elif kind == "error":
                    self._set_hint("加载失败：\n" + msg[1])
                    if self._alive():
                        messagebox.showerror("加载失败", msg[1])
        except queue.Empty:
            pass
        # 关窗后不再续期，否则回调会撞上已销毁的 root
        self._after(RENDER_POLL_MS, self._pump)

    def _show_image(self, pil: Image.Image) -> None:
        if not self._alive():
            return
        self.photo = ImageTk.PhotoImage(pil)
        if self.canvas.find_withtag("img"):
            self.canvas.itemconfigure("img", image=self.photo)
        else:
            self.canvas.create_image(0, 0, anchor="nw", image=self.photo,
                                     tags="img")
            self.canvas.tag_lower("img")
        self._redraw_anchors()
        self._redraw_waypoints()

    def _redraw_anchors(self) -> None:
        """锚点画成独立的 canvas item，不参与图像渲染，加一个点无需重渲染。"""
        if not self._alive():
            return
        self.canvas.delete("anchor")
        if not self.ready:
            return
        cw, ch = self._canvas_size()
        x0, y0 = self._view_x0(cw), self._view_y0(ch)
        for a in self.anchors:
            mx = (a.x - x0) * self.zoom
            my = (a.y - y0) * self.zoom
            if not (-30 <= mx <= cw + 30 and -30 <= my <= ch + 30):
                continue
            self.canvas.create_oval(mx - 4, my - 4, mx + 4, my + 4,
                                    outline="#ffffff", width=1, fill="#ff3b30",
                                    tags="anchor")
            self.canvas.create_text(mx + 7, my - 7, text=a.name, anchor="sw",
                                    fill="#ffe14d", font=("Consolas", 10, "bold"),
                                    tags="anchor")
        self.canvas.tag_raise("anchor")
        self.canvas.tag_raise("hint")

    def _set_hint(self, text: str | None) -> None:
        if not self._alive():
            return
        if text:
            cw, ch = self._canvas_size()
            self.canvas.coords("hint", cw / 2, ch / 2)
            self.canvas.itemconfigure("hint", text=text, state="normal")
        else:
            self.canvas.itemconfigure("hint", state="hidden")

    def _flash(self, text: str) -> None:
        self._flash_text = text
        self._update_info()

    def _update_info(self, full: bool = True) -> None:
        """刷新标题栏 + 状态栏。

        `full=False` 时不碰窗口标题 —— 鼠标每动一下都会调到这里，
        反复 SetWindowText 在 Windows 上会让标题栏闪。
        关窗后一律直接返回，避免 TclError。
        """
        if not self._alive():
            return
        zoom_pct = self.zoom * 100.0
        if full:
            self.root.title(f"{self.image_path.name} — {zoom_pct:.2f}%  "
                            f"[{len(self.anchors)} 锚点] — 锚点坐标查看器")
        bits = [f"缩放 {zoom_pct:.2f}%", f"锚点 {len(self.anchors)}"]
        if self.read_only:
            bits.append("只读")
        else:
            bits.append("● 未保存" if self._dirty else self._save_note)
        extra = self._flash_text
        if extra:
            bits.append(extra)
        self.lbl_info.configure(text="   ".join(bits))
        self._update_wp_status()

    def _update_wp_status(self) -> None:
        """状态栏左侧那条：把已选路点序列**完整**打出来。

        ★ 为什么要完整列，而不是「已选 5 个」
          ----------------------------
          用户点选的顺序本身就是信息（路点按序走）。只给个计数，
          他没法确认第 3 个点是不是想要的 —— 而这份 JSON 就是要拿给
          e2e 用的，序列错了角色就走错路。
        """
        if not self._alive():
            return
        if not self.waypoints:
            self.lbl_wp.configure(text="")
            return
        seq = " → ".join(
            f"{i}({w['x']:.0f},{w['z']:.0f})"
            for i, w in enumerate(self.waypoints, 1))
        self.lbl_wp.configure(
            text=f"已选 {len(self.waypoints)} 个路点：{seq}")

    # ---------------------------------------------------------- 工作线程
    def _start_worker(self) -> None:
        self.worker = threading.Thread(target=self._worker, daemon=True,
                                       name="anchors-render")
        self.worker.start()

    def _worker(self) -> None:
        """独占 PIL。先加载，再不停处理最新的渲染请求。"""
        def status(text: str) -> None:
            self.outbox.put(("status", text))

        try:
            self.source.load(status)
        except MemoryError:
            self.outbox.put(("error",
                             "内存不足，无法解码这张图。\n"
                             f"需要约 {self.source.width}x{self.source.height}x4 字节。\n"
                             "请关掉其他程序后重试。"))
            return
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.outbox.put(("error", f"{type(exc).__name__}: {exc}"))
            return

        self.outbox.put(("ready", self.source.width, self.source.height))

        while True:
            view = self.inbox.get()
            if view is None:
                return
            # 合并请求：只渲染最新的那一帧，拖动时不排队堆积
            while True:
                try:
                    newer = self.inbox.get_nowait()
                except queue.Empty:
                    break
                if newer is None:
                    return
                view = newer
            try:
                pil = self.source.render(view)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                continue
            # 只保留最新成品，主线程来不及取也不积压
            while True:
                try:
                    self.outbox.get_nowait()
                except queue.Empty:
                    break
            self.outbox.put(("image", view.token, pil))


# ============================================================ 入口
def default_out_path(image_path: Path) -> Path:
    """同名文件 + .anchors.json，例如 teyvat_P0.png.anchors.json"""
    return image_path.with_name(image_path.name + ".anchors.json")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="巨图锚点坐标标定查看器（tkinter + Pillow）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="操作：左键单击标点 / 左键拖动平移 / 滚轮缩放(最大200%) / "
               "中键拖动平移 / 右上角 X 退出 / 底部按钮 重置视图·撤销·保存·退出",
    )
    ap.add_argument("image", help="要查看的图片路径（可以很大，例如 36864x18432）")
    ap.add_argument("--out", default=None,
                    help="锚点 JSON 输出路径（默认 <图片全路径>.anchors.json）")
    ap.add_argument("--read-only", action="store_true", help="只读：禁止写盘")
    args = ap.parse_args(argv)

    image_path = Path(args.image).expanduser()
    if not image_path.is_file():
        sys.stderr.write(f"[FATAL] 找不到图片：{image_path}\n")
        return 2

    out_path = (Path(args.out).expanduser() if args.out
                else default_out_path(image_path))

    print(f"[INFO] 图片   : {image_path}")
    print(f"[INFO] 锚点   : {out_path}"
          + ("  (只读)" if args.read_only else ""), flush=True)

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        sys.stderr.write(f"[FATAL] 起不了 Tk 窗口：{exc}\n"
                         "        没有图形界面 / 无 DISPLAY 时无法运行。\n")
        return 2

    app = AnchorsViewer(root, image_path, out_path, args.read_only)
    root.protocol("WM_DELETE_WINDOW", app.quit)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
