"""LLM 路径规划骨架 —— 给定起终点世界坐标，输出中间路点列表。

★ 本轮**不真调 API**
--------------------
只做：读底图 -> 裁剪 -> 标注 -> 编码 -> 组装 prompt -> 检查环境变量
-> 有则准备调用、无则跳过 -> 输出所有中间产物。
真实 HTTP 调用藏在 ``PATH_PLANNER_REAL_CALL=1`` 后面，默认走不到。

★ 为什么裁剪要自己写，而不复用 chunk_loader
--------------------------------------------
``perception/chunk_loader.py::load_l0_window()`` 返回的是**正方形灰度**
窗口（给 NCC 相位相关用的）。本任务要的是**任意矩形、彩色**的裁剪图
（要给人和多模态模型看），两者规格不兼容。它的价值在**跨块补拉**，
所以这里借它的块边界逻辑，不借它的返回值。

★★ 最省内存的读法：按需解码 + 先降采样
--------------------------------------
底图 36864x18432 RGBA **整张解码 = 2.6 GiB**，直接 ``PIL.open().crop()``
会把内存吃光。PIL 的 ``Image.draft()`` 能在**解码前**让 libjpeg
按 1/2、1/4、1/8 降采样，峰值内存降到几十分之一。本脚本据此：

  1. 先算出**目标输出尺寸**（长边 MAX_EDGE=1024）；
  2. ``draft()`` 选一个**不小于**该尺寸的降采样档 ``s``；
  3. 只在 L0 分块上取窗口 —— 分块是 3072x1536 的小 PNG，
     单块解码峰值约 18 MiB，而不是 2.6 GiB。

两条路取其一：分块（默认，有跨块补拉和 LRU）或直接开原图 draft。

★ 世界坐标 -> 像素：复用 coord_transform
-----------------------------------------
``misc/coord_transform.py`` 是 23 个人工锚点拟合的仿射矩阵
（RMS **12.65 px**）。**底图相对世界坐标是转置的**：像素 X 主要跟随
``world_z``，像素 Y 主要跟随 ``world_x``。该模块已把转置吸收进系数，
**直接用即可**，不要试图"纠正"轴向（那会得到 4762 像素残差）。

用法::

    python path_planner.py --start-world 0,0 --target-world 100,0
    python path_planner.py --start-world 0,0 --target-world 100,0 \\
        --out "D:\\tmp\\crop.png"
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
import base64
import importlib.util
import io
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

# chcp 65001 只改控制台代码页，Python 仍按自己的编码写字节 ——
# 中文 Windows 上是 cp936，不强制 UTF-8 就是满屏乱码。
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):     # 被重定向到 StringIO 时没这个方法
        _s.reconfigure(encoding="utf-8", errors="replace")

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

# ---------------------------------------------------------------- 路径
MISC_DIR = Path(__file__).resolve().parent
RECOG = Path(RECOG_ROOT)
MAP_PNG = RECOG / "data" / "mihoyo_maps" / "teyvat_P0.png"
PYRAMID = RECOG / "data" / "mihoyo_maps" / "P0_pyramid"

# ---------------------------------------------------------------- 常量
#: 输出图长边（像素）。多模态模型按 1024 量级看图，再大只是烧 token。
MAX_EDGE = 1024
#: 包围盒外扩比例。留白让模型看到起点/终点周边的地形，
#: 而不是把两个点顶在图像边缘上。
BUFFER = 0.20
#: 起终点标注半径（**输出图**上的像素，不是底图上的）。
MARK_RADIUS = 8
#: L0 分块尺寸（与 chunks_index.json 一致；不对就以文件为准）。
FALLBACK_CHUNK = (3072, 1536)
#: 真实调用的超时（秒）。
#: ★ 10 → 60（2026-10-06 真机）：一张 1024px 地图 + 推理模型，
#:   10s 会**经常**在模型还在想的时候被本地掐断 —— 表现为
#:   「[TIMEOUT] 10s 没响应」，而服务端其实好好的。60s 是这类请求的
#:   合理上限；真超时了你会看到明确报错而不是一个半吊子的结果。
API_TIMEOUT_S = 60
#: 请求的 max_tokens 默认值。
#:
#: ★ 为什么从 500 提到 2000（2026-10-06 真机打脸）
#:   --------------------------------------------
#:   真跑 DeepSeek 的 usage 是 ``completion=500`` —— **恰好等于**当时的
#:   max_tokens，而 content 整块是空的。这就是被 max_tokens 掐断的
#:   指纹：模型的**思考过程**先把预算吃光了，正文一个字没轮到。
#:
#: ★ 2000 → 4000（2026-10-06 对照实验）
#:
#:   配套 :data:`DEFAULT_REASONING_EFFORT` 改成 ``minimal`` 一起看：那一档
#:   下模型能正常产出正文（实测 completion=687 tokens、finish_reason=stop）。
#:   4000 是「够用且不浪费」的量 —— 正文本身几百 token 就够，剩下的是
#:   给推理的余量；真不够时 API 会用 finish_reason=length 明确告诉你，
#:   不用一开始就开到 8000（那是推理被吃光的量级）。
DEFAULT_MAX_TOKENS = 4000

#: ``reasoning_effort`` 的默认值。deepseek-flash 是推理模型，实测它的
#: 推理长度随 max_tokens 线性增长（4000 -> 6665 字；8000 -> 13370 字，
#: 都把预算吃光）。这个参数是唯一能从**请求侧**压住推理量的开关。
#:
#: ★ "low" → "minimal"（2026-10-06 对照实验）
#:
#:   ``diag_llm_vision2.py --only A`` 在**同一张复杂地图**上跑出
#:   ``effort=minimal / finish_reason=stop / completion=687 tokens``，
#:   content 有内容且地形判断正确 —— 而同一张图在 ``low`` 下无论
#:   max_tokens 给到 2000 还是 8000，推理都会把预算吃光。
#:
#:   ★ 所以根因不是「字段没送到」，而是「low 压不住」
#:     ----------------------------------------------------
#:   之前一直假设 DeepSeek 可能不认这个字段（开发机是占位符 key，探不了）。
#:   现在有反证了：effort=minimal 能让正文拿到配额，说明**端点认、而且
#:   真的按档位生效**。问题一直在「档位选错了」，不是「参数没传」。
#:
#:   对应的自愈逻辑（:data:`_REASONING_EFFORT_OK`）**继续保留** ——
#:   万一换一个 OpenAI 兼容层真的不认，也不至于整个功能死掉。
DEFAULT_REASONING_EFFORT = "minimal"

#: 进程内标记：这个端点到底认不认 ``reasoning_effort``。
#: ``None`` = 还没试过（默认带上）；``False`` = 试过且被拒，全进程不再发。
_REASONING_EFFORT_OK: bool | None = None

#: max_tokens 超过这个值就提醒「加预算可能没用」。
#:
#: ★ 为什么要有这条
#:   ------------
#: 踩过一次坑：content 为空，直觉是「预算不够」，于是 500 -> 4000 -> 8000
#: 一路加，推理从 6665 字涨到 13370 字，**照样吃光**。问题根本不在预算上。
#: 不提醒的话，下一个人还会接着往上加。
MAX_TOKENS_WARN_AT = 16000

# ---------------------------------------------------------------- DeepSeek
#: OpenAI 兼容端点。**写死**而不是从环境变量来：这条路只服务 DeepSeek，
#: 留一个 base 变量只会让人改错成 /v1/chat/completions 之外的路径。
DEEPSEEK_ENDPOINT = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_KEY_ENV = "DEEPSEEK_API_KEY"
DEEPSEEK_MODEL_ENV = "DEEPSEEK_MODEL"
DEFAULT_MODEL = "deepseek-flash"

#: 世界坐标绝对值上限（米）。提瓦特全图远小于此，
#: 超出就是模型编的（或轴向搞反了）——**宁可整份判废**，不拿脏路点去走。
WORLD_COORD_LIMIT = 30000.0

#: 真实调用开关（环境变量形式）。``--real`` 是主开关；这个留着是为了
#: 上一轮脚本化用法（``PATH_PLANNER_REAL_CALL=1``）继续有效。
REAL_CALL_ENV = "PATH_PLANNER_REAL_CALL"

#: 路点间距下限（米）。比这个更近的路点没有信息量，
#: 还会让直线寻路的「太近不启动」门限频繁触发。
MIN_WAYPOINT_GAP_M = 50.0
#: 允许的路点数量区间。
MIN_WAYPOINTS, MAX_WAYPOINTS = 3, 6


def _load_coord_transform():
    """按文件加载 ``coord_transform``（同目录脚本，无包上下文）。"""
    p = MISC_DIR / "coord_transform.py"
    spec = importlib.util.spec_from_file_location("_ct", p)
    m = importlib.util.module_from_spec(spec)
    sys.modules["_ct"] = m
    spec.loader.exec_module(m)
    return m


CT = _load_coord_transform()


# ---------------------------------------------------------------- 几何
def world_to_px(wx: float, wz: float) -> tuple[float, float]:
    """世界坐标 -> 底图像素（复用 23 锚点标定的仿射，含转置）。"""
    return CT.world_to_pixel(float(wx), float(wz))


def crop_box_px(start: tuple[float, float],
                target: tuple[float, float],
                buffer_ratio: float = BUFFER) -> tuple[int, int, int, int]:
    """起点/终点的像素包围盒 + 缓冲，夹到底图范围内。

    :returns: ``(x0, y0, x1, y1)``，``x1/y1`` 为**开区间**（PIL 语义）。
    """
    sx, sy = world_to_px(*start)
    tx, ty = world_to_px(*target)
    x0f, x1f = (sx, tx) if sx <= tx else (tx, sx)
    y0f, y1f = (sy, ty) if sy <= ty else (ty, sy)
    # 退化情况：两点重合或几乎重合 -> 给一个最小窗口，
    # 否则 crop 宽或高为 0，PIL 直接报错。
    w, h = x1f - x0f, y1f - y0f
    if w < 1.0:
        x0f, x1f = (x0f - 0.5), (x1f + 0.5)
    if h < 1.0:
        y0f, y1f = (y0f - 0.5), (y1f + 0.5)
    x0f -= w * buffer_ratio
    x1f += w * buffer_ratio
    y0f -= h * buffer_ratio
    y1f += h * buffer_ratio
    x0, y0 = max(0, int(np.floor(x0f))), max(0, int(np.floor(y0f)))
    x1 = min(CT.IMAGE_WIDTH, int(np.ceil(x1f)))
    y1 = min(CT.IMAGE_HEIGHT, int(np.ceil(y1f)))
    w_px, h_px = x1 - x0, y1 - y0
    # ★ 退化保护（新增）
    #   起终点很近、或两点正好沿同一世界轴（共线于裁剪框某一边）时，
    #   包围盒会被压成一条细缝（实测 100m 且 z=0 -> 2x141 像素），
    #   这种图对模型**毫无价值**。给每个方向一个最小窗口，
    #   宁可多给一点地形，也不要给一条 2 像素宽的缝。
    min_w = max(8, int(MAX_EDGE * 0.12))     # ≈123px ≈ 123 米
    min_h = max(8, int(MAX_EDGE * 0.12))
    if w_px < min_w:
        cx = (x0 + x1) * 0.5
        x0, x1 = int(cx - min_w / 2), int(cx + min_w / 2)
    if h_px < min_h:
        cy = (y0 + y1) * 0.5
        y0, y1 = int(cy - min_h / 2), int(cy + min_h / 2)
    # 扩张后可能越界，重新夹一次
    x0 = max(0, x0)
    y0 = max(0, y0)
    x1 = min(CT.IMAGE_WIDTH, x1)
    y1 = min(CT.IMAGE_HEIGHT, y1)
    if x1 - x0 < 1 or y1 - y0 < 1:
        raise ValueError(
            f"裁剪框退化：({x0},{y0})-({x1},{y1})。"
            f"起终点可能都在底图外（底图 {CT.IMAGE_WIDTH}x{CT.IMAGE_HEIGHT}）。")
    return x0, y0, x1, y1


# ---------------------------------------------------------------- 读底图
def _chunk_index() -> dict:
    p = PYRAMID / "chunks_index.json"
    if not p.is_file():
        raise FileNotFoundError(
            f"找不到分块索引: {p}\n"
            f"（先跑 build/slice_p0_pyramid.py 切片；"
            f"或设 NEKO_P0_PYRAMID 指向别的目录）")
    return json.loads(p.read_text(encoding="utf-8"))


def _read_via_chunks(box: tuple[int, int, int, int]) -> np.ndarray:
    """从 L0 分块拼出矩形窗口（RGB）。**逐块解码，峰值内存 ~几十 MiB。**"""
    meta = _chunk_index()
    cw, chh = meta["chunk_size"]
    gx, gy = meta["grid"]
    x0, y0, x1, y1 = box
    out = np.zeros((y1 - y0, x1 - x0, 3), dtype=np.uint8)
    r0, c0 = int(y0 // chh), int(x0 // cw)
    r1, c1 = int((y1 - 1) // chh), int((x1 - 1) // cw)
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            if not (0 <= r < gy and 0 <= c < gx):
                continue                       # 底图外，留黑
            p = PYRAMID / "L0" / f"r{r:02d}_c{c:02d}.png"
            im = Image.open(p).convert("RGB")
            # 该块在窗口坐标系里的位置（负数表示窗口左/上越过了块边）
            dx, dy = c * cw - x0, r * chh - y0
            sx0, sy0 = max(0, -dx), max(0, -dy)
            ax0, ay0 = max(0, dx), max(0, dy)
            w = min(im.width - sx0, (x1 - x0) - ax0)
            h = min(im.height - sy0, (y1 - y0) - ay0)
            if w > 0 and h > 0:
                out[ay0:ay0 + h, ax0:ax0 + w] = np.asarray(
                    im.crop((sx0, sy0, sx0 + w, sy0 + h)))
    return out


def _read_via_draft(box: tuple[int, int, int, int]) -> np.ndarray:
    """直接开原图，``draft()`` 先降采样再裁。**峰值内存远低于整图解码。**

    ``draft()`` 在**解码前**生效（libjpeg 按 1/2、1/4、1/8 出图），
    所以 36864x18432 不会真的解成 2.6 GiB。
    """
    im = Image.open(MAP_PNG)
    w_px, h_px = box[2] - box[0], box[3] - box[1]
    want = max(w_px, h_px) / float(MAX_EDGE)
    s = 1
    for cand in (8, 4, 2, 1):          # 由粗到细，取第一个够用的
        if want <= cand:
            s = cand
            break
    if s > 1:
        im.draft("RGB", (w_px // s, h_px // s))
    im = im.convert("RGB")
    sx, sy = box[0] // s, box[1] // s
    sw, sh = max(1, w_px // s), max(1, h_px // s)
    return np.asarray(im.crop((sx, sy, sx + sw, sy + sh)))


def read_crop(box: tuple[int, int, int, int],
              method: str = "chunks") -> tuple[Image.Image, str]:
    """读裁剪区并返回 ``(PIL 图, 实际用的方法)``。"""
    if method == "chunks":
        try:
            return Image.fromarray(_read_via_chunks(box)), "chunks"
        except FileNotFoundError as exc:
            print(f"  [警告] 分块不可用（{exc}），回退到原图 draft()")
    return Image.fromarray(_read_via_draft(box)), "draft"


# ---------------------------------------------------------------- 标注
def _world_to_img(img: Image.Image, box: tuple[int, int, int, int],
                  wx: float, wz: float) -> tuple[float, float]:
    """世界坐标 -> **已缩放输出图**上的像素坐标。**唯一真源。**

    ★ 为什么必须抽出来
      --------------
    :func:`annotate`（画起终点）和 :func:`draw_route`（画路点）都要做
    「世界 -> 底图像素 -> 输出图像素」两步换算。两处各写一遍的话，改了
    一处忘另一处 = 路点画歪了 —— 而这种错在图上**还挺像那么回事**，
    肉眼极难发现。

    ★ 为什么 x / y 的缩放因子分开算
      --------------------------
    等比缩放下两者应当相等，但 resize 用的是 ``int()`` 截断，末位会差
    一点点。所以老老实实各算各的，不图省事共用一个 factor。
    """
    x0, y0, x1, y1 = box
    px, py = world_to_px(wx, wz)
    cx = (px - x0) * (img.width / float(x1 - x0))
    cy = (py - y0) * (img.height / float(y1 - y0))
    return cx, cy


def annotate(img: Image.Image, box: tuple[int, int, int, int],
              start: tuple[float, float],
              target: tuple[float, float]) -> Image.Image:
    """在缩放后的图上画起点(红)/终点(绿)。**就地绘制**，返回同一对象。

    ★ 为什么是「就地」而不是「返回新图」（2026-10-06 更正声明）
      --------------------------------------------------
      以前这里写的是「**返回新图，不改入参**」—— 那是**错的**：
      ``ImageDraw.Draw(img)`` 就是往 ``img`` 上画。声明与实现不符，
      照着声明写代码的人会拿到意外结果（改一个副本却发现原图也变了）。

      保留就地语义（它本身是对的）：起终点本来就该出现在**发给模型的那
      张图**上，prompt 里也明写「红点=起点，绿点=终点」。
      要留一份干净底图，自己 ``img.copy()`` 再传进来。
    """
    d = ImageDraw.Draw(img)
    for (wx, wz), color in ((start, (255, 60, 60)), (target, (60, 220, 90))):
        cx, cy = _world_to_img(img, box, wx, wz)   # ★ 换算只在那一处
        r = MARK_RADIUS
        # 外圈黑边，保证在任何底色上都看得清
        d.ellipse([cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2],
                  fill=(0, 0, 0))
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    return img


#: 路点/连线配色。黄 = 「中间路点」，与红(起点)/绿(终点)区分开。
ROUTE_COLOR = (255, 214, 64)
ROUTE_OUTLINE = (24, 20, 12)


def apply_flip(img: Image.Image, flip: bool) -> Image.Image:
    """水平镜像整张图（可选）。**只翻左右，不翻上下。**

    ★★ 为什么不是「左右 + 上下 = 180° 旋转」
      -----------------------------------
      实测（crop 1000,1000→3000,3000，框 2801x2798）四个角的位置：

      ==================  =========  ==========  ============
      点                  不翻       只左右翻     180° 旋转
      ==================  =========  ==========  ============
      起点 (1000,1000)    右下       **左下**     左上
      终点 (3000,3000)    左上       **右上**     右下
      ==================  =========  ==========  ============

      只有「只左右翻」符合需求里的「左下 / 右上」；180° 旋转会得到
      左上 / 右下，与预期相反。

      而且从「让模型看懂」的角度，只有左右翻是**有收益**的：
        · 世界 Z ↑ -> 图上 ←（不翻）  /  → 右（翻了）   ← 修正了横向轴
        · 世界 X ↑ -> 图上 ↑（两种都一样）              ← 纵向轴本来就是对的
      180° 旋转会把纵向轴也弄反：X ↑ 变成向下，反而**制造**一个新矛盾。

    ★ 为什么放在绘制的**最后**
      ----------------------
      :func:`_world_to_img` / :func:`annotate` / :func:`draw_route`
      全部按**未翻转**的坐标系计算；这里在它们都跑完之后把成品镜像一次。
      于是「发给模型的图」和「路径图」用的是**同一套**坐标，
      绝不可能出现两图对不齐。
    """
    return img.transpose(Image.FLIP_LEFT_RIGHT) if flip else img


def _load_font(size: int):
    """拿一个能画数字的字体。**拿不到就返回 None**（调用方跳过编号）。

    依次试几个 Windows 自带字体；都没有就返回 None，让调用方决定怎么办
    —— 比在这里 raise 强：画不出编号的图仍然有连线，比整张图生成失败
    有用得多。
    """
    from PIL import ImageFont                    # noqa: PLC0415

    for name in ("segoeui.ttf", "arial.ttf", "tahoma.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:                         # noqa: BLE001
            continue
    try:
        return ImageFont.load_default()
    except Exception:                             # noqa: BLE001
        return None


def draw_route(img: Image.Image, box: tuple[int, int, int, int],
               start: tuple[float, float], target: tuple[float, float],
               waypoints: list[dict]) -> Image.Image:
    """把**完整路径**画到已标注的图上：起点→路点→…→终点。

    :returns: **同一对象**（就地绘制，理由见 :func:`annotate` 的说明）。
        调用点传的是 ``img.copy()`` —— 这一步**必须**：``img`` 是即将
        base64 编码发给模型的那张，就地画上去等于让模型看见「自己上一轮
        的答案已经画在图上」，形成自我强化。

    ★ 折线为什么**包括起点和终点**
      --------------------------
      只画中间路点的话，「这条路到底通不通」根本看不出来 —— 而这正是
      这张图存在的意义。起终点连线一画上，断头路、逆序路一眼就露馅。

    ★ 为什么每样东西都带一圈黑边
      ------------------------
      底图是深绿/深蓝的森林与水域，**纯黄线在上面会糊掉**。
      和 :func:`annotate` 里的起终点同一个办法：先画粗一号的黑色底，
      再画细一号的彩色面。
    """
    if not waypoints:
        return img
    d = ImageDraw.Draw(img)
    r = max(4, MARK_RADIUS - 2)
    # 编号字号：按点的直径给，而不是按正文字号。12px 的数字挂在 1024px 的
    # 图上等于没有 —— 试出来的：能渲染，但小到实际没用。
    font = _load_font(max(16, r * 2))

    pts = [_world_to_img(img, box, start[0], start[1])]
    pts += [_world_to_img(img, box, float(w["x"]), float(w["z"]))
            for w in waypoints]
    pts.append(_world_to_img(img, box, target[0], target[1]))

    d.line(pts, fill=ROUTE_OUTLINE, width=r + 3, joint="curve")
    d.line(pts, fill=ROUTE_COLOR, width=max(1, r - 1), joint="curve")

    for i, w in enumerate(waypoints, 1):
        cx, cy = _world_to_img(img, box, float(w["x"]), float(w["z"]))
        d.ellipse([cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2],
                  fill=(0, 0, 0))
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ROUTE_COLOR)
        if font is not None:
            # 放在点的**正上方**并居中：放右侧的话，编号会被后面的
            # 折线压住（折线正从点心上穿过）。
            # ★ 白字 + 黑描边，不是黑字
            #   底图明暗跨度极大（亮岩 vs 近黑的水域），任何**纯色**编号
            #   在其中一端都会糊掉。白字配黑描边两边都读得出 —— 这是
            #   描边存在的全部意义。
            d.text((cx, cy - r - 6), str(i), fill=(0, 0, 0), font=font,
                   anchor="ms", stroke_width=3, stroke_fill=(0, 0, 0))
            d.text((cx, cy - r - 6), str(i), fill=(255, 255, 255), font=font,
                   anchor="ms")
    return img


def coord_note(flip: bool) -> str:
    """生成 prompt 里的【坐标系说明】。**两版内容都来自实测**。

    ★ 为什么必须逐轴说，不能只说「X/Z 增大就往哪边」
      --------------------------------------------
      这两个世界轴落在图上是**互相垂直**的（X 走纵向、Z 走横向）。
      只说「一起增大」会把它说成一个对角方向，模型一对照就又矛盾了。

    ★ 数字怎么来的
      ------------
      实测 crop(1000,1000 -> 3000,3000)：框 2801x2798，
      起点落在图 (85.7%, 85.7%) = 右下，终点 (14.3%, 14.3%) = 左上。
      水平镜像后 x 变成 100-x：起点 (14.3%, 85.7%) = 左下，
      终点 (85.7%, 14.3%) = 右上。**不是凭想象写的**。
    """
    if flip:
        return """【坐标系说明】（重要，先看这段）
这张图是**水平镜像**过的。请按下面的对应关系理解，不要用「坐标大就在右边」
的常识去套：
  · 世界 X 增大 -> 图上**向上**
  · 世界 Z 增大 -> 图上**向右**
所以 起点(%s,%s) 在图片的**左下角**，终点(%s,%s) 在图片的**右上角**。
这是正确的，不是矛盾；照着它读图就不会绕错方向。""" % (
            "{:.0f}", "{:.0f}", "{:.0f}", "{:.0f}")
    return """【坐标系说明】（重要，先看这段）
这张图的地图是**上下颠倒**的（南方朝上）。请按下面的对应关系理解，
不要用「坐标大就在右边」的常识去套：
  · 世界 X 增大 -> 图上**向上**
  · 世界 Z 增大 -> 图上**向左**
所以 起点(%s,%s) 在图片的**右下角**，终点(%s,%s) 在图片的**左上角**。
这是正确的，不是矛盾；照着它读图就不会绕错方向。""" % (
        "{:.0f}", "{:.0f}", "{:.0f}", "{:.0f}")


# ---------------------------------------------------------------- prompt
def build_prompt(box: tuple[int, int, int, int],
                 start: tuple[float, float],
                 target: tuple[float, float],
                 flip: bool = False) -> str:
    """组装 prompt（含裁剪区的**世界坐标范围**，模型据此反算路点）。"""
    x0, y0, x1, y1 = box
    # 像素 -> 世界：注意 px 主轴是 world_z、py 主轴是 world_x
    w_tl = CT.pixel_to_world(x0, y0)
    w_br = CT.pixel_to_world(x1, y1)
    wx_min, wx_max = sorted((w_tl[0], w_br[0]))
    wz_min, wz_max = sorted((w_tl[1], w_br[1]))
    # 坐标系说明里的四个数字用 start/target 的真值填，不写死
    _cs = coord_note(flip).format(start[0], start[1], target[0], target[1])
    return f"""你是原神地图向导。给你一张提瓦特地图的局部截图。
红点=起点 ({start[0]:.1f}, {start[1]:.1f})，绿点=终点 ({target[0]:.1f}, {target[1]:.1f})。

{_cs}

裁剪区域的**世界坐标范围**：
  X 方向：{wx_min:.1f} ~ {wx_max:.1f}
  Z 方向：{wz_min:.1f} ~ {wz_max:.1f}

【重要】不要尝试从图片里推算像素坐标或做坐标换算。
红点和绿点的世界坐标已在上方给出，就是起点和终点。
图片只是让你看**地形**（山、水、道路的走向），
不需要你读出任何像素值或做几何计算。

【如何区分地图上的线】
这张图是**美术地图**，上面有大量线条，但**不是所有线都能走**：
  · **等高线**：最细、最密集、呈封闭环状或密排的浅色细线，
    表示地形起伏，**不可通行**，不要把它当成路。
  · **水陆边界**：颜色深浅交界处的细线，**不可通行**。
  · **可通行的道路**：更粗、更连续、通常连接城镇或重要地点，
    颜色可能偏亮或偏黄（在灰度图上表现为明显的亮线）。
  只有**明显更粗、更连续**的线才算道路。

你如果分不清，倾向保守：**宁可不走也不要把等高线当路**。

【执行顺序】
第一步：观察图片，用**一句话**描述从红点到绿点沿途的地形 ——
  中间有什么障碍、应该往哪绕。写完这句就够了，不要展开分析过程。
第二步：基于你第一步的描述，输出 {MIN_WAYPOINTS}-{MAX_WAYPOINTS} 个中间路点。

输出 JSON（**先 terrain_analysis，后 waypoints**，两个键都要有）：
{{
  "terrain_analysis": "一句话：从起点向东南，中途有连绵山脉阻挡，沿南部河谷绕行可避开主山脊",
  "waypoints": [{{"x": 世界X, "z": 世界Z, "reason": "简短说明"}}]
}}

★ 路点必须反映你在 terrain_analysis 里描述的绕行策略：
  你说「沿南部河谷绕行」，路点的 z 就应该比直线路径小；
  你说「中间无障碍」，路点才可以接近直线。
  说一套、画另一套，比不写 terrain_analysis 更糟。

你的回答必须以 {{ 开头，}} 结束。

请规划一条从起点到终点的路径。
要求：
- 路点稀疏（相邻路点至少 {MIN_WAYPOINT_GAP_M:.0f} 米）
- 沿道路/河谷/山谷走，避开明显的高山和深水
- 只要 JSON，不要额外解释、不要 markdown 代码块
- 坐标必须落在上面给的世界范围内
- 第一个路点应当离起点不太远，最后一个应当离终点不太远
  （本项目是**直线寻路**，不会自己绕路；终点必须可达）

输出示例（这是格式示例，坐标和地形都是假的）：
{{"terrain_analysis": "从起点向东南，中途有中央山脉阻挡，沿南部河谷绕行可避开主山脊",
 "waypoints": [
  {{"x": 1200, "z": 1300, "reason": "沿河谷向东南"}},
  {{"x": 1800, "z": 1700, "reason": "绕过中央山脉南麓"}},
  {{"x": 2600, "z": 2500, "reason": "沿道路接近目标"}}
 ]}}

（你的真实输出必须用图中真实的地形特征，坐标必须是你
判断的合理位置，但格式和示例一致。）

如果判断不可达，输出 {{"terrain_analysis": "为什么不可达", "waypoints": [], "error": "原因"}}"""


# ---------------------------------------------------------------- 响应诊断
def _dbg(msg: str) -> None:
    """诊断输出。**一律走 stderr**。

    ★ 为什么不能打 stdout
      ------------------
    这个脚本的 stdout 是给人读的**日志流**（裁剪图路径、prompt、路点）。
    万一将来有人 ``> out.json`` 重定向，诊断混进去就会污染文件。
    stderr 是「给排障看」的正确去处。

    ★ 脱敏到什么程度
      ------------
    打印顶层**键名**而不是值 —— 键名里不会有密钥。而 content /
    reasoning_content 的前 200 字是**模型自己的输出**，里面不含
    任何凭据（请求头的 Authorization 从来不进响应体），所以可以打。
    """
    print(msg, file=sys.stderr)


def _dump_response_shape(doc: dict) -> None:
    """把响应结构打出来，让「为什么空」一眼可见。

    真机上 content 为空时，只有 usage 那行是不够的 —— ``completion=500``
    恰好等于 max_tokens 是个强暗示，但**暗示不等于证据**。这里把
    finish_reason / 键名 / reasoning_content 全部摆出来，下次再撞上
    就不用靠猜。
    """
    _dbg("  [DEBUG] ── 响应结构 ──")
    try:
        _dbg(f"  [DEBUG] 顶层 keys: {sorted(doc.keys())}")
    except Exception:                                  # noqa: BLE001
        _dbg("  [DEBUG] 顶层不是 dict（?）")
        return

    try:
        choice = doc["choices"][0]
    except (KeyError, IndexError, TypeError):
        _dbg("  [DEBUG] choices[0] 取不到")
        return

    _dbg(f"  [DEBUG] choices[0] keys: {sorted(choice.keys())}")
    _dbg(f"  [DEBUG] finish_reason: "
         f"{choice.get('finish_reason')!r}")

    msg = choice.get("message")
    if not isinstance(msg, dict):
        _dbg("  [DEBUG] message 不是对象")
        return
    _dbg(f"  [DEBUG] message keys: {sorted(msg.keys())}")

    content = msg.get("content")
    n = len(content) if isinstance(content, str) else 0
    _dbg(f"  [DEBUG] content 长度: {n}")
    _dbg("  [DEBUG] content 前 200 字: "
         + (repr(content[:200]) if isinstance(content, str) and content.strip()
            else "(空)"))

    reasoning = msg.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning.strip():
        _dbg(f"  [DEBUG] reasoning_content: 存在（{len(reasoning)} 字）"
             f" 前 200 字: {reasoning[:200]!r}")
    else:
        _dbg("  [DEBUG] reasoning_content: 不存在")

    tc = msg.get("tool_calls")
    if tc:
        _dbg(f"  [DEBUG] tool_calls: 存在（{len(tc)} 个）类型: "
             + ", ".join(str(t.get("type")) for t in tc
                         if isinstance(t, dict)))
    else:
        _dbg("  [DEBUG] tool_calls: 不存在")


def _extract_json_from_reasoning(text: str) -> str | None:
    """从思考过程里把 JSON 抠出来。

    模型在 ``reasoning_content`` 里写推理时，经常**顺带把最终 JSON 也
    写了一遍**（"所以我应该输出 {...} 这样"）。这时候 content 是空的，
    但答案其实已经在思考里躺着。

    ★★ 为什么必须**从左往右**扫，而不是找最后一个 ``{``
      ------------------------------------------------
      第一版用 ``s.rfind("{")``，在 ``{"waypoints": [{"x":1,"z":2}]}``
      上取到的是**最内层**那个 ``{"x":1,...}``，切出来是
      ``{"x":1.0,"z":2.0}]}`` —— 多了 ``]}``，必然解析失败。
      嵌套结构下「最后一个左括号」根本不是对象的起点。

      正确做法：用 ``JSONDecoder.raw_decode(s, i)`` 逐个 ``{`` 试解，
      它只吃一个**完整**的 JSON 值并返回结束位置，天然处理嵌套。
      **从左往右**的第一个成功解就是**最外层**那个 —— 正是我们要的。

    ★ 为什么要 ``waypoints`` 才算
      -------------------------
      思考里可能有 ``{"方向": "北"}`` 这类无关 JSON。认不出 waypoints
      的就继续往后扫，绝不把不相干的东西当答案返回。

    :returns: 抠出来的 JSON 文本（含且仅含那一个对象）；没有则 None。
    """
    if not isinstance(text, str) or not text.strip():
        return None
    dec = json.JSONDecoder()
    pos = 0
    while True:
        i = text.find("{", pos)
        if i < 0:
            return None
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            pos = i + 1                  # 这个 ``{`` 不是对象起点，往后挪
            continue
        if isinstance(obj, dict) and "waypoints" in obj:
            return text[i:end]
        pos = i + 1


# ---------------------------------------------------------------- API 骨架
def encode_png(img: Image.Image) -> str:
    """PNG -> base64（**内存里**编码，不落盘第二次）。"""
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def build_request(image_b64: str, prompt: str, model: str,
                  max_tokens: int = DEFAULT_MAX_TOKENS,
                  reasoning_effort: str = DEFAULT_REASONING_EFFORT,
                  ) -> dict[str, Any]:
    """OpenAI 兼容的 chat/completions 请求体（含 image_url）。

    ``temperature`` 固定 0.3：路径规划要的是**可复现**，同一张图跑两次
    不该给出两条不同的路。

    ★ ``reasoning_effort`` 默认**带上**
      --------------------------------
      deepseek-flash 是推理模型，而它的推理长度会随 max_tokens 线性增长
      （实测 4000 -> 6665 字推理吃光；8000 -> 13370 字还是吃光）。
      这个参数是唯一能从**请求侧**压住推理量的开关。

      ★★ 但我不能确定 DeepSeek 是否支持它
        ------------------------------
        OpenAI 兼容层对**未知字段**的行为不统一：有的原样忽略，有的直接
        400。既然探不了（开发机上的 key 是占位符），就不能把「大概率没事」
        当成「确定没事」塞进默认路径 —— 那样一旦不支持，**每一次**请求都
        400，功能直接死掉。

        所以做成：默认带上 + **被拒就自愈**（见 :func:`_call_llm` 里的
        400 重试）。第一次可能浪费一个来回，之后整个进程不再发它。
    """
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": int(max_tokens),
        "temperature": 0.3,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/png;base64,{image_b64}"}},
            ],
        }],
    }
    effort = (reasoning_effort or "").strip().lower()
    if effort and effort != "off":
        body["reasoning_effort"] = effort
    return body


def _call_llm(image_b64: str, prompt: str, *,
              real: bool = False,
              timeout: float = API_TIMEOUT_S,
              max_tokens: int = DEFAULT_MAX_TOKENS,
              model: str | None = None,
              reasoning_effort: str = DEFAULT_REASONING_EFFORT,
              dump_prompt: str | None = None) -> str | None:
    """调 LLM 取文本响应。**永不抛异常** —— 调用方可以无脑 await 结果。

    ★ 为什么不抛
      ----------
      调用方是寻路主流程，而寻路已经有「降级到直接朝目标走」这条退路。
      一个网络抖动不该把整个脚本炸掉 —— 炸掉的话，用户既没规划到路点，
      也没走到终点，什么都没发生。

    ★ 异常为什么要分级打印
      ------------------
      ``except Exception`` 一把抓只能打出 ``HTTPError: 401``，
      用户看不出是 key 错、限流、还是 endpoint 写错了。每一种的处理
      完全不同（改 key / 等 / 改配置），所以分开打。

    :param real: 是否真发请求。为假时只打印「会发什么」。
    :param reasoning_effort: 传给 API 的推理强度。不支持时自动去掉并重试。
    :param dump_prompt: 给定路径则把完整 prompt 写进去（--dump-prompt）。
    """
    global _REASONING_EFFORT_OK

    key = os.environ.get(DEEPSEEK_KEY_ENV, "").strip()
    mdl = (model or os.environ.get(DEEPSEEK_MODEL_ENV, "").strip()
           or DEFAULT_MODEL)
    real = bool(real) or os.environ.get(REAL_CALL_ENV, "").strip() == "1"

    # ★ 任务 E：长 prompt 在终端里不好读（滚动、换行、中文宽度全不对）。
    if dump_prompt:
        try:
            dp = Path(dump_prompt)
            dp.parent.mkdir(parents=True, exist_ok=True)
            dp.write_text(prompt, encoding="utf-8")
            print(f"  ★ prompt 已写入：{dp}（{len(prompt)} 字符）")
        except OSError as exc:
            _dbg(f"  [LLM] ⚠ prompt 写文件失败（不影响调用）：{exc}")

    print()
    print("─── 请求 ───")
    print(f"  POST  {DEEPSEEK_ENDPOINT}")
    print(f"  model = {mdl}")
    print(f"  auth  = {'Bearer ***' + key[-4:] if key else f'({DEEPSEEK_KEY_ENV} 未设)'}")
    print(f"  image = data:image/png;base64,<{len(image_b64)} chars>")
    print(f"  文本  = {len(prompt)} 字符；timeout={timeout:g}s "
          f"max_tokens={max_tokens}")
    # 任务 D：把「加预算没用」这件事说在**发请求之前**，而不是等
    # content 空了才在事后猜。
    if max_tokens > MAX_TOKENS_WARN_AT:
        _dbg(f"  [LLM] ⚠ max_tokens={max_tokens} 很大。"
             f"deepseek-flash 是推理模型，推理长度随 max_tokens 增长。"
             f"问题如果不在预算上，加预算可能没用。")
        _dbg("       （实测：4000 -> 6665 字推理吃光；8000 -> 13370 字"
             "照样吃光。真正该改的是 prompt，或用 --reasoning-effort low。）")
    # 打印本次**实际会发什么**的 effort：已经被确认不支持时是 off，
    # 否则是用户给的值。打印「用户以为发了什么」等于骗人。
    _send_now = ("off" if _REASONING_EFFORT_OK is False
                 else (reasoning_effort or "").strip().lower())
    _eff_note = "（本进程已确认该端点不支持，不再发送）" \
        if _REASONING_EFFORT_OK is False else ""
    print(f"  effort= {_send_now or '(不发送)'}{_eff_note}")
    print("─────────────")

    if not real:
        print(f"  [SKIP] 没给 --real（且 {REAL_CALL_ENV} != 1）—— 跳过真实调用")
        return None
    if not key:
        print(f"  [SKIP] {DEEPSEEK_KEY_ENV} 未设置，跳过真实调用")
        print("         提示：设置后重跑，例：")
        print(f"           $env:{DEEPSEEK_KEY_ENV} = 'sk-...'")
        return None

    import urllib.error  # noqa: PLC0415 - 只在真调用时导入
    import urllib.request  # noqa: PLC0415

    def _send(effort: str) -> str:
        payload = json.dumps(build_request(image_b64, prompt, mdl,
                                           max_tokens, effort))
        req = urllib.request.Request(
            DEEPSEEK_ENDPOINT,
            data=payload.encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"},
            method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")

    # ★ 自愈重试：只有「本进程还没确认不支持」且「确实带了 effort」时试。
    #   _REASONING_EFFORT_OK=False 之后就直接不带，少一次白费的对��。
    _send_effort = (reasoning_effort or "").strip().lower()
    if _REASONING_EFFORT_OK is False:
        _send_effort = ""
    try:
        raw = _send(_send_effort)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:                                # noqa: BLE001
            detail = "(读不到响应体)"
        # ★ 只有 400/422 且响应体点名了 reasoning_effort 才自愈。
        #   401（key 错）、429（限流）重试一万次也一样，白白烧一个来回。
        _looks_like_effort_rejection = (
            exc.code in (400, 422) and "reasoning_effort" in detail)
        if _looks_like_effort_rejection and _send_effort:
            _REASONING_EFFORT_OK = False
            _dbg(f"  [LLM] 该端点不认 reasoning_effort（HTTP {exc.code}）"
                 f" —— 去掉后重试一次。")
            _dbg("       本进程后续请求都不再发送该字段。")
            try:
                raw = _send("")
            except urllib.error.HTTPError as exc2:
                try:
                    detail2 = exc2.read().decode("utf-8", errors="replace")
                except Exception:                        # noqa: BLE001
                    detail2 = "(读不到响应体)"
                _dbg(f"  [LLM] [HTTP {exc2.code}] {exc2.reason}")
                _dbg(f"  [LLM] 响应体：{detail2[:500]}")
                return None
            except Exception as exc2:                     # noqa: BLE001
                _dbg(f"  [LLM] [ERR] {type(exc2).__name__}: {exc2}")
                return None
        else:
            # ★ 必须读 body：401/402/429/500 的区别全在里面，
            #   而 HTTPError 自己的 str() 只有状态码和一句 reason。
            _dbg(f"  [LLM] [HTTP {exc.code}] {exc.reason}")
            _dbg(f"  [LLM] 响应体：{detail[:500]}")
            if exc.code in (401, 402):
                _dbg(f"  [LLM] → 多半是 {DEEPSEEK_KEY_ENV} 不对或余额不足。")
            elif exc.code == 429:
                _dbg("  [LLM] → 限流。等一会儿再试，或调低频率。")
            return None
    except urllib.error.URLError as exc:
        _dbg(f"  [LLM] [NET] {exc.reason}")
        _dbg("  [LLM] → 连不上。检查网络/代理/防火墙。")
        return None
    except TimeoutError:
        _dbg(f"  [LLM] [TIMEOUT] {timeout:g}s 没响应。")
        _dbg("  [LLM] → 底图太大或网络慢；调大 --timeout，或缩短起终点距离。")
        return None
    except Exception as exc:                             # noqa: BLE001
        _dbg(f"  [LLM] [ERR] {type(exc).__name__}: {exc}")
        return None

    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        _dbg(f"  [LLM] [BADJSON] 响应不是合法 JSON：{exc}")
        _dbg(f"  [LLM] 原始前 500 字：{raw[:500]}")
        return None

    # usage：记一下，好知道一次规划花了多少钱。
    # ★ ``completion_tokens`` 恰好等于 max_tokens = 被截断的指纹
    #   （2026-10-06 真机：completion=500 而 max_tokens 也是 500）。
    u = doc.get("usage")
    if isinstance(u, dict):
        _dbg(f"  [LLM] usage: prompt={u.get('prompt_tokens')} "
             f"completion={u.get('completion_tokens')} "
             f"total={u.get('total_tokens')}")

    _dump_response_shape(doc)

    try:
        choice = doc["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        _dbg(f"  [LLM] [SHAPE] 响应结构不符预期（缺 choices/message）：{exc}")
        _dbg(f"  [LLM] 原始前 500 字：{raw[:500]}")
        return None

    # ★★ finish_reason 是判断「为什么空」的第一现场（2026-10-06）
    #   length = 被 max_tokens 截断，模型正写到一半被硬生生掐断 ——
    #   这时 content 半截 JSON、或者干脆只有思考过程。真机就是这么
    #   撞上的：usage 里 completion 恰好等于 max_tokens=500。
    _finish = str(choice.get("finish_reason") or "")
    if _finish == "length":
        # ★ 印**这次请求实际用的** max_tokens，不是默认值 —— 上线后才发现
        #   这里写的是 DEFAULT_MAX_TOKENS，于是用户明明传了 --max-tokens
        #   30000，提示还在让他「提高 --max-tokens」，能急死人。
        _dbg("  [LLM] ⚠ 被 max_tokens 截断（finish_reason=length）。"
             "请提高 --max-tokens。")
        _dbg(f"       本次实际用的 --max-tokens={max_tokens}"
             + (f"（默认值 {DEFAULT_MAX_TOKENS}）"
                if max_tokens == DEFAULT_MAX_TOKENS
                else "（命令行指定）")
             + f"；JSON + 思考过程很容易就吃超。")
    elif _finish and _finish != "stop":
        _dbg(f"  [LLM] ⚠ finish_reason={_finish!r}（不是正常的 'stop'）。")
        if _finish == "content_filter":
            _dbg("       内容被模型侧的安全策略拦下了，换个措辞或换个图试试。")

    content = message.get("content")
    if not isinstance(content, str):
        content = "" if content is None else str(content)

    if content.strip():
        return content

    # ★ 到这里 content 是空的。真机现象：content 空、但**根本没输出
    #   任何 reasoning**（usage 显示 completion=500 = max_tokens，
    #   而 reasoning_content 字段压根不存在）—— 所以那次是**纯截断**，
    #   模型的思考直接吃满了预算，一个字正文都没轮到。
    _dbg("  [LLM] [EMPTY] content 为空。")

    reasoning = message.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning.strip():
        # C：某些模型把「思考过程」放 reasoning_content、「最终答案」
        #    放 content。如果 content 空而 reasoning 有，答案可能就在
        #    思考里（模型忘了把结论单独输出一遍）。
        print("  [LLM] ⚠ content 空但 reasoning_content 有内容 —— "
              "尝试从思考过程里提取 JSON。", file=sys.stderr)
        rescued = _extract_json_from_reasoning(reasoning)
        if rescued is not None:
            print(f"  [LLM] ✓ 已从 reasoning_content 里提取到 JSON"
                  f"（{len(rescued)} 字）。", file=sys.stderr)
            return rescued
        print("  [LLM] ✗ reasoning_content 里也没有可用的 JSON。",
              file=sys.stderr)
        return None

    if message.get("tool_calls"):
        print("  [LLM] ⚠ 模型返回的是 tool_calls 而不是正文 —— "
              "说明它把这次请求当成了工具调用场景。", file=sys.stderr)
    return None


# ---------------------------------------------------------------- 解析
def _strip_fence(text: str) -> str:
    """剥掉 ```` ```json ... ``` ```` 围栏。**只剥一次**。

    模型很爱包 markdown 围栏。不剥的话 JSON 一定解析失败，而失败原因
    （"Expecting value"）完全指不出真正的问题是多了三个反引号。
    """
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _parse_waypoints(content: str) -> list[dict] | None:
    """结构 + 绝对范围校验。**任何不合规返回 None，不"尽力修正"。**

    :returns:
        * ``None`` —— 结构坏（不是 JSON / 没有 waypoints / 点缺字段 /
          坐标越界 / 坐标不是有限数）。**整份判废**：半份路点比没有更危险，
          因为调用方会拿它当「已规划成功」来用。
        * ``[]``   —— 结构对但**判定不可达**（模型给了空列表，通常带
          ``error`` 说明）。调用方据此走另一条话术。
        * ``[...]`` —— 正常路点。

    ★ 为什么返回 ``None`` 而不是抛异常
      ------------------------------
      模型输出是**不可信输入**。半截 JSON、坐标写成字符串、跑到地图外 ——
      这些都是模型偶尔会犯的错，不是程序错误。抛异常会让调用方不得不
      try/except，而它多半会顺手 catch 掉然后假装没发生过。
    """
    if not content or not str(content).strip():
        print("  ✗ 模型返回空内容")
        return None
    s = _strip_fence(str(content))
    try:
        doc = json.loads(s)
    except ValueError as exc:
        print(f"  ✗ JSON 解析失败：{exc}")
        print(f"    原始前 300 字：{s[:300]}")
        return None
    if not isinstance(doc, dict):
        print("  ✗ 顶层不是对象")
        return None
    wps = doc.get("waypoints")
    if not isinstance(wps, list):
        if doc.get("error"):
            print(f"  · 模型判定不可达：{doc['error']}")
        else:
            print("  ✗ 没有 waypoints 字段（且没有 error 说明）")
        return None
    if not wps:
        if doc.get("error"):
            print(f"  · 模型判定不可达：{doc['error']}")
        else:
            print("  · 路点列表为空（模型认为无需中间点 / 不可达）")
        return []

    out: list[dict] = []
    for i, w in enumerate(wps):
        if not isinstance(w, dict):
            print(f"  ✗ 第 {i + 1} 个路点不是对象（是 {type(w).__name__}）")
            return None
        try:
            x, z = float(w["x"]), float(w["z"])
        except (KeyError, TypeError, ValueError):
            print(f"  ✗ 第 {i + 1} 个路点缺 x/z 或不是数字：{w}")
            return None
        # ★ NaN / inf 会**静默穿过**所有大小比较（NaN 的任何比较都是
        #   False），所以必须显式挡掉。一个 NaN 坐标会让 atan2 给出
        #   任意角度，角色原地乱转。
        if not (math.isfinite(x) and math.isfinite(z)):
            print(f"  ✗ 第 {i + 1} 个路点坐标不是有限数：({x}, {z})")
            return None
        if abs(x) > WORLD_COORD_LIMIT or abs(z) > WORLD_COORD_LIMIT:
            print(f"  ✗ 第 {i + 1} 个路点 ({x:.1f},{z:.1f}) 越界"
                  f"（±{WORLD_COORD_LIMIT:.0f}）")
            return None
        out.append({"x": x, "z": z, "reason": str(w.get("reason", ""))})
    return out


def _extract_terrain(text: str) -> str:
    """把 ``terrain_analysis`` 单独抠出来打给人看。

    ★ 为什么不复用 :func:`_parse_waypoints` 的结果
      ------------------------------------------
      它只返回路点列表，terrain 会被丢掉；而本轮约束是不改它。
      这里独立解一次 JSON —— 一秒钟的事，换来 prompt 改动面最小。

    ★ 缺失时返回空串，**不报错**
      ----------------------
      模型有可能只吐 ``waypoints`` 不吐 ``terrain_analysis``（尤其是被
      few-shot 带偏、或 max_tokens 又被推理吃光时）。那种情况按原有逻辑
      照常走路点，只是不打印这行 —— 不能因为「少了个可选字段」就让整个
      规划失败。
    """
    if not isinstance(text, str) or "{" not in text:
        return ""
    s = _strip_fence(text)
    i, j = s.find("{"), s.rfind("}")
    try:
        d = json.loads(s[i:j + 1])
    except ValueError:
        return ""
    if not isinstance(d, dict):
        return ""
    return str(d.get("terrain_analysis") or "").strip()


def parse_waypoints(text: str, box: tuple[int, int, int, int],
                    start: tuple[float, float],
                    target: tuple[float, float]) -> list[dict] | None:
    """在 :func:`_parse_waypoints` 之上**再叠**一层：必须落在裁剪区内。

    绝对范围（±30000）只能挡住「离谱」，挡不住「在这张图里但在山那边」。
    裁剪区是**模型自己看到的范围**，它给不出框外的坐标 —— 给出来了
    说明它在编。
    """
    out = _parse_waypoints(text)
    if out is None:
        return None
    if not out:
        return out

    x0, y0, x1, y1 = box
    w_tl, w_br = CT.pixel_to_world(x0, y0), CT.pixel_to_world(x1, y1)
    wx_min, wx_max = sorted((w_tl[0], w_br[0]))
    wz_min, wz_max = sorted((w_tl[1], w_br[1]))
    # 允许一点点外溢：模型给的是"路点"，不是裁剪框边缘，
    # 卡在边界上的合理路点不该被判废。
    slack = 0.05 * max(wx_max - wx_min, wz_max - wz_min)

    for i, w in enumerate(out):
        if not (wx_min - slack <= w["x"] <= wx_max + slack
                and wz_min - slack <= w["z"] <= wz_max + slack):
            print(f"  ✗ 第 {i + 1} 个路点 ({w['x']:.1f},{w['z']:.1f}) "
                  f"超出裁剪区 X[{wx_min:.0f},{wx_max:.0f}] "
                  f"Z[{wz_min:.0f},{wz_max:.0f}]")
            return None
    if not (MIN_WAYPOINTS - 1 <= len(out) <= MAX_WAYPOINTS + 1):
        # 只提示不拒绝：数量偏离多半是模型对"中间路点"的理解不同，
        # 坐标合法就还有用。
        print(f"  · 路点数 {len(out)} 与期望 {MIN_WAYPOINTS}-"
              f"{MAX_WAYPOINTS} 略有出入，继续采用")
    return out


# ---------------------------------------------------------------- 对外入口
def get_waypoints(start: tuple[float, float],
                  target: tuple[float, float], *,
                  real: bool = False,
                  timeout: float = API_TIMEOUT_S,
                  max_tokens: int = DEFAULT_MAX_TOKENS,
                  model: str | None = None,
                  reasoning_effort: str = DEFAULT_REASONING_EFFORT,
                  method: str = "chunks",
                  out: str | None = None,
                  dump_prompt: str | None = None,
                  viz: bool = False,
                  flip: bool = False,
                  verbose: bool = True) -> list[dict] | None:
    """给定起终点，返回中间路点列表。**给 ``e2e_pathfind.py`` 用的入口。**

    流程：裁剪框 -> 读底图 -> 标注 -> 缩放 -> 编码 -> prompt -> 调用 -> 解析。

    ★ 返回值的三个含义，调用方必须区分
      ------------------------------------
      * ``[wp, wp, ...]`` —— 规划成功，按顺序走
      * ``[]``           —— 模型判定不可达 / 无需中间点
      * ``None``         —— 没规划成（无 key / 网络失败 / 输出不合规）。
        **两种都要降级**，区别只在给用户看的说明。

    ★ 永不抛异常
      ----------
      规划是**锦上添花**：失败了就退回「直接朝终点走」，那本来就是
      可用的退路。这里抛异常会把一个可选功能变成致命错误。

    ★ 关于「不真调 API」
      ------------------
    ``real=False`` 时只打印裁剪图路径、prompt 和 [SKIP] 提示，不发请求。
    这是默认行为 —— 免得脚本因为环境里恰好有个 key 就默默把图发出去、
    把钱花了。
    """
    def say(*a):
        if verbose:
            print(*a)

    say("=" * 70)
    say("[LLM-PLAN] 1 · 坐标与裁剪框")
    say("=" * 70)
    say(f"  起点 world=({start[0]:.1f}, {start[1]:.1f})  "
        f"px={tuple(round(v, 1) for v in world_to_px(*start))}")
    say(f"  终点 world=({target[0]:.1f}, {target[1]:.1f})  "
        f"px={tuple(round(v, 1) for v in world_to_px(*target))}")
    try:
        box = crop_box_px(start, target)
    except ValueError as exc:
        print(f"  ✗ {exc}", file=sys.stderr)
        return None
    say(f"  裁剪框 px {box} = {box[2] - box[0]}x{box[3] - box[1]} 像素")
    say(f"  ★ 仿射残差 RMS {CT.FIT_RMS_PX:.2f}px（≈{CT.FIT_RMS_PX:.0f} 米）")

    say()
    say("=" * 70)
    say("[LLM-PLAN] 2 · 读底图 + 裁剪 + 标注")
    say("=" * 70)
    try:
        img, used = read_crop(box, method)
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"  ✗ 读底图失败：{exc}", file=sys.stderr)
        return None
    scale = MAX_EDGE / float(max(img.width, img.height))
    if scale < 1.0:
        img = img.resize((max(1, int(img.width * scale)),
                          max(1, int(img.height * scale))), Image.LANCZOS)
    img = annotate(img, box, start, target)
    # ★★ 翻转放在**标注之后、所有坐标计算之前**。
    #   顺序反过来（先翻再标注）的话，_world_to_img 算出的仍是**未翻转**
    #   坐标系的像素，画上去就错位了。而把翻转放到最后，就等于
    #   「所有绘制都在原始坐标系里做完，最后整张图转个方向」——
    #   _world_to_img / annotate / draw_route **一行都不用改**，
    #   也就不会出现「发给模型的图」和「路径图」用两套坐标系这种事。
    #
    #   ⚠ 这里只用 FLIP_LEFT_RIGHT，**不做上下翻转**（详见 apply_flip）。
    shot = apply_flip(img, flip)
    say(f"  读法: {used}  -> 长边 <= {MAX_EDGE}（当前 {shot.width}x{shot.height}）")
    say(f"  图像翻转: {'开（世界 Z 增大 -> 图上向右）' if flip else '关（世界 Z 增大 -> 图上向左）'}")

    out_path = Path(out) if out else (
        Path(os.environ.get("TEMP", ".")) / "path_planner_input.png")
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shot.save(out_path, format="PNG")
    except OSError as exc:
        print(f"  ⚠ 裁剪图保存失败（不影响规划）：{exc}", file=sys.stderr)
    else:
        say(f"  ★ 裁剪图已保存：{out_path}")
        say(f"    红点=起点 绿点=终点；**这张就是发给模型的图**")

    say()
    say("=" * 70)
    say("[LLM-PLAN] 3 · 编码 + prompt")
    say("=" * 70)
    b64 = encode_png(shot)          # ★ 发给模型的是**翻转后**那张
    prompt = build_prompt(box, start, target, flip=flip)
    say(f"  base64 {len(b64)} 字符（约 {len(b64) * 3 // 4} 字节 PNG）")
    say(f"  prompt {len(prompt)} 字符"
        + (f"（已落盘：{dump_prompt}）" if dump_prompt else ""))
    if verbose:
        for ln in prompt.splitlines():
            print(f"  │ {ln}")

    say()
    say("=" * 70)
    say("[LLM-PLAN] 4 · 调用模型")
    say("=" * 70)
    text = _call_llm(b64, prompt, real=real, timeout=timeout,
                     max_tokens=max_tokens, model=model,
                     reasoning_effort=reasoning_effort,
                     dump_prompt=dump_prompt)
    if text is None:
        say()
        # ★ 「dry-run 下这是预期」这句话**只在没加 --real 时才成立**。
        #   真跑失败还说「预期」，等于把失败报成成功 —— 2026-10-06 真机上
        #   就是这么把人带偏的：明明 --real 了，却看到一句
        #   「未拿到响应 —— dry-run 下这是预期」。
        if real:
            say(f"  [LLM] 未拿到响应 —— **调用失败了**。")
            say(f"  [LLM] 完整诊断见 stderr（DEBUG 块 / HTTP 码 / finish_reason）。")
        else:
            say("  未拿到响应 —— dry-run 下这是**预期**。")
            say(f"  真跑：加 --real，并设 $env:{DEEPSEEK_KEY_ENV}")
        return None

    say()
    say("=" * 70)
    say("[LLM-PLAN] 5 · 解析")
    say("=" * 70)
    wps = parse_waypoints(text, box, start, target)
    if wps is None:
        say("  ✗ 解析失败（输出不合规）。")
        return None
    if not wps:
        say("  · 空路点列表 —— 不可达或无需绕路。")
        return []
    say(f"  解析出 {len(wps)} 个路点：")
    # ★ 把模型对地形的说法原样打出来。上一轮的真机结论就是从 reasoning
    #   里读到的「中间有山需要绕行」而路点仍是直线 —— **模型自己说得对、
    #   坐标却没跟上**。这两行并排打印，那个矛盾一眼就能看见。
    _terr = _extract_terrain(text)
    if _terr:
        say(f"  ★ 模型的地形判断：{_terr}")
    else:
        say("  · 模型没给 terrain_analysis（不影响路点，仅少一条线索）")
    say(f"    起点 ({start[0]:.1f}, {start[1]:.1f})")
    for i, w in enumerate(wps, 1):
        say(f"    {i}. ({w['x']:9.1f}, {w['z']:9.1f})  {w['reason']}")
    say(f"    终点 ({target[0]:.1f}, {target[1]:.1f})")

    # ── --viz：把完整路径画回同一张图 ──────────────────────────────
    #   画在**已经标注过起终点、已经缩放过**的那张 img 上，而不是重新
    #   读一遍底图 —— 重新读一遍的话，两张图的缩放因子可能差一两个像素，
    #   路点就会和起终点对不齐（而这种错在图上看着还挺像那么回事）。
    if viz:
        try:
            # ★ 用**未翻转**的 img 画（draw_route 走 _world_to_img，算的是
            #   原始坐标系），画完再整体镜像 —— 与发模型那张同一套坐标。
            routed = apply_flip(draw_route(img.copy(), box, start, target, wps),
                                flip)
            viz_path = Path(os.environ.get("TEMP", ".")) / "path_planner_route.png"
            routed.save(viz_path, format="PNG")
            say()
            say(f"  ★ 路径图已保存：{viz_path}")
            say(f"    {routed.width}x{routed.height}；黄线=完整路径，"
                f"黄点+编号=路点，红=起点，绿=终点")
        except OSError as exc:
            _dbg(f"  [LLM] ⚠ 路径图保存失败（不影响规划结果）：{exc}")
    return wps


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(
        description="LLM 路径规划（默认 dry-run，不发请求）")
    ap.add_argument("--start-world", required=True, metavar="X,Z")
    ap.add_argument("--target-world", required=True, metavar="X,Z")
    ap.add_argument("--real", action="store_true",
                    help="真发请求。不加就只打印裁剪图与 prompt")
    ap.add_argument("--timeout", type=float, default=API_TIMEOUT_S,
                    help=f"请求超时秒数（默认 {API_TIMEOUT_S:g}）")
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS,
                    help=f"max_tokens（默认 {DEFAULT_MAX_TOKENS}）")
    ap.add_argument("--model", default=None,
                    help=f"模型名（默认读 ${DEEPSEEK_MODEL_ENV}，"
                         f"再默认 {DEFAULT_MODEL}）")
    ap.add_argument("--reasoning-effort", default=DEFAULT_REASONING_EFFORT,
                    help=f"推理强度（默认 {DEFAULT_REASONING_EFFORT}；"
                         f"off=不发该字段）。端点不支持时会自动去掉并重试")
    ap.add_argument("--dump-prompt", action="store_true",
                    help="把完整 prompt 写到 %TEMP%\\llm_prompt.txt"
                         "（终端里看长中文 prompt 很难受）")
    ap.add_argument("--viz", action="store_true",
                    help="把 LLM 返回的路点画到裁剪图上，输出"
                         " %TEMP%\\path_planner_route.png（需配合 --real）")
    ap.add_argument("--flip", action="store_true",
                    help="把裁剪图**水平镜像**，让世界 Z 增大对应图上向右。"
                         "默认关（保持现状可复现）")
    ap.add_argument("--out", default=None, metavar="路径",
                    help="裁剪图输出路径（默认 %TEMP%\\path_planner_input.png）")
    ap.add_argument("--method", choices=("chunks", "draft"), default="chunks",
                    help="读底图方式：chunks=分块拼（默认）/ draft=原图降采样")
    args = ap.parse_args()

    try:
        start = tuple(float(v) for v in args.start_world.split(","))
        target = tuple(float(v) for v in args.target_world.split(","))
    except (ValueError, TypeError):
        print("✗ 坐标格式应为 X,Z", file=sys.stderr)
        return 2
    if len(start) != 2 or len(target) != 2:
        print("✗ 坐标必须各有两个分量（X,Z）", file=sys.stderr)
        return 2
    # ★ 参数门禁放在**任何 I/O 之前**。不校验直接往下走的话，用户会先
    #   等它读底图（几秒到几十秒）再被告知 --timeout 写错了。
    if args.timeout <= 0:
        print(f"✗ --timeout 必须 > 0（收到 {args.timeout}）", file=sys.stderr)
        return 2
    if args.max_tokens <= 0:
        print(f"✗ --max-tokens 必须 > 0（收到 {args.max_tokens}）",
              file=sys.stderr)
        return 2
    _eff = (args.reasoning_effort or "").strip().lower()
    if _eff and _eff not in ("low", "medium", "high", "minimal", "off"):
        print(f"✗ --reasoning-effort 取值应为 low/medium/high/minimal/off"
              f"（收到 {args.reasoning_effort!r}）", file=sys.stderr)
        return 2

    _dump = (str(Path(os.environ.get("TEMP", ".")) / "llm_prompt.txt")
             if args.dump_prompt else None)

    # ★ 整个流程交给 :func:`get_waypoints` —— 它和 e2e_pathfind.py 走的是
    #   **同一个函数**。上一轮 main() 自己内联了全套流程，于是「手动跑」
    #   和「e2e 调用」很容易长成两套不一样的东西；这里从根上消掉。
    wps = get_waypoints(start, target, real=args.real,
                        timeout=args.timeout, max_tokens=args.max_tokens,
                        model=args.model, reasoning_effort=args.reasoning_effort,
                        method=args.method, out=args.out,
                        dump_prompt=_dump, viz=bool(args.viz),
                        flip=bool(args.flip))

    if wps is None:
        return 0                          # 没规划成：dry-run 下的预期结果
    if not wps:
        return 0                          # 判定不可达

    print()
    print("  ↓ 交给 e2e_pathfind.py：加 --use-llm 会自动按顺序走这些路点。")
    for i, w in enumerate(wps, 1):
        print(f"     --target-world {w['x']:.1f},{w['z']:.1f}   # 第 {i} 段")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
