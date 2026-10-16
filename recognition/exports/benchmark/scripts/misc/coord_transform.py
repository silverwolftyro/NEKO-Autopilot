"""底图像素 <-> 世界坐标 互转（**由 23 个人工锚点拟合**）。

★ 拟合来源
--------
- 像素锚点: ``recognition/data/mihoyo_maps/teyvat_P0.png.anchors.json``
- 世界坐标: BetterGI ``tp.json``（读结构用，坐标本身是游戏事实）
- 底图尺寸: 36864 x 18432 像素
- 参与锚点: 23 / 23（0 个未匹配已跳过）
- 残差 RMS: **12.648 像素**，最大 28.6 像素，中位 9.1 像素
- 验收: RMS < 50 ✅ / 最差 < 200 ✅

★ 坐标轴（重要）
--------------------
**底图相对世界坐标是转置的**：像素 X 主要跟随 ``world_z``，像素 Y 主要跟随 ``world_x``。
本模块的仿射矩阵**已经把转置吸收进去了** —— 直接用即可，
不要试图"纠正"轴向，那会得到 4762 像素的残差（按 ``px ~ world_x`` 拟合实测值）。

★ 与 stitch_tiles.py 的关系
---------------------------
stitch_tiles.py 把瓦片 (x, y) 写入了 (row, col)，
导致底图相对世界坐标是转置的——这就是 px~world_z、py~world_x 的原因。
修复方式见规划文档 §4.6。

**⚠️ 重拼底图后必须重跑 :func:`verify_anchors`。**
若 RMS 突然变大、或直接跳到几百上千像素，说明转置关系变了（拼接顺序改了），
本模块的系数**全部作废**，必须重新标定。

★ 精度
------
1 世界单位 ≈ 0.9995 像素 -> RMS 12.65 px ≈ **12.65 米**。
BetterGI 公式里的 ``Scale/zoom`` 因此为 1.0。
残差主要来自**人工点击精度**，不是变换本身。

★ 适用范围
----------
只对这张 P0 白天底图有效。换地图 / 换图层 / 重拼图都要重新标定。
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
import os
import sys

# ---- console encoding ----------------------------------------------
# chcp 65001 only changes the console code page; Python still writes bytes
# in its own encoding (cp936/GBK on Chinese Windows), so every Chinese
# string in this script comes out as mojibake. Force both streams UTF-8.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):   # redirected to StringIO -> no method
        _s.reconfigure(encoding="utf-8", errors="replace")

import json
from pathlib import Path

#: 底图尺寸（像素）
IMAGE_WIDTH = 36864
IMAGE_HEIGHT = 18432

#: px = _A*world_x + _B*world_z + _C
_A = -0.0005251239
_B = -0.9995176200
_C = 23460.212244
#: py = _D*world_x + _E*world_z + _F
_D = -0.9982704816
_E = -0.0004493965
_F = 11242.579297

#: 行列式，pixel->world 可逆性判据
_DETERMINANT = -0.9977886999
#: 拟合残差
FIT_RMS_PX = 12.6483
FIT_MAX_RESIDUAL_PX = 28.6371
FIT_ANCHOR_COUNT = 23
#: 每世界单位对应像素数（主轴）
PIXELS_PER_UNIT = 0.999518

#: verify_anchors() 找不到数据时的搜索顺序
_TP_SEARCH = (
    Path(RECOG_ROOT / 'data' / 'teleport_points.json'),
    Path(RECOG_ROOT / 'data' / '_ref_tp_sample.json'),
)


def world_to_pixel(world_x: float, world_z: float) -> tuple[float, float]:
    """世界坐标 -> 底图像素坐标。

    :param world_x: 世界 X（米）
    :param world_z: 世界 Z（米）
    :returns: ``(pixel_x, pixel_y)``
    """
    return (_A * world_x + _B * world_z + _C,
            _D * world_x + _E * world_z + _F)


def pixel_to_world(pixel_x: float, pixel_y: float) -> tuple[float, float]:
    """底图像素坐标 -> 世界坐标。:func:`world_to_pixel` 的精确逆。

    :param pixel_x: 底图像素 X
    :param pixel_y: 底图像素 Y
    :returns: ``(world_x, world_z)``
    :raises ValueError: 矩阵奇异（标定数据不足），无法求逆
    """
    if abs(_DETERMINANT) < 1e-9:
        raise ValueError(
            f"仿射矩阵奇异（行列式 {_DETERMINANT:.3e}），无法 pixel->world；"
            "请重新标定")
    dx = pixel_x - _C
    dy = pixel_y - _F
    return ((_E * dx - _B * dy) / _DETERMINANT,
            (-_D * dx + _A * dy) / _DETERMINANT)


def is_inside(pixel_x: float, pixel_y: float) -> bool:
    """像素是否落在底图范围内。"""
    return 0 <= pixel_x < IMAGE_WIDTH and 0 <= pixel_y < IMAGE_HEIGHT


def _norm(s: str) -> str:
    s = str(s or "").strip()
    for a, b in (("七天神仙", "七天神像"), ("安绕", "安饶")):
        s = s.replace(a, b)
    for ch, r in (("，", "-"), ("、", "-"), ("－", "-"),
                  ("—", "-"), ("·", "-"), (" ", ""), ("\u3000", "")):
        s = s.replace(ch, r)
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")


def _area_key(label: str) -> str:
    import re
    s = re.sub(r"^.{0,4}神像\s*[-·]?", "", _norm(label))
    s = re.sub(r"(传送锚点|传送点|锚点)$", "", s)
    return s.strip("-")


def _load_teleport_points() -> list[dict]:
    """读传送点世界坐标。按 ``_TP_SEARCH`` 顺序找，找到第一个就用。"""
    import re
    for path in _TP_SEARCH:
        if not path.is_file():
            continue
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        out = []
        if isinstance(raw, dict) and "data" in raw:
            for region in raw["data"]:
                for pt in region.get("points", []):
                    pos = pt.get("position")
                    if isinstance(pos, list) and len(pos) == 3:
                        out.append({"name": str(pt.get("name", "")),
                                    "areas": [str(a) for a in (pt.get("areas") or [])],
                                    "wx": float(pos[0]), "wz": float(pos[2])})
        elif isinstance(raw, dict) and "regions" in raw:
            for region in raw["regions"]:
                for pt in region.get("points", []):
                    wp = pt.get("world_pos")
                    if isinstance(wp, list) and len(wp) == 3:
                        out.append({"name": str(pt.get("name", "")),
                                    "areas": [str(a) for a in (pt.get("areas") or [])],
                                    "wx": float(wp[0]), "wz": float(wp[2])})
        if out:
            return out
    return []


def verify_anchors(anchors_json_path: str) -> dict:
    """重跑标定验证，返回误差报告。

    用**当前**仿射参数去预测每个锚点的像素坐标，和人工标注值比对。

    :param anchors_json_path: ``*.anchors.json`` 路径
    :returns: 报告 dict，字段如下::

        {
          "ok": bool,                 # 是否通过阈值
          "rms_px": float,            # 残差均方根（像素）
          "max_residual_px": float,
          "matched": int,             # 成功匹配并验证的锚点数
          "skipped": [str, ...],      # 匹配不上跳过的名字
          "points": [                 # 逐点明细
              {"name", "pixel_x", "pixel_y", "world_x", "world_z",
                "dx", "dy", "residual_px"}, ...
          ]
        }

    :raises FileNotFoundError: anchors_json_path 不存在
    :raises RuntimeError: 找不到传送点数据源（无法验证）
    """
    path = Path(anchors_json_path)
    if not path.is_file():
        raise FileNotFoundError(f"锚点文件不存在: {path}")

    doc = json.loads(path.read_text(encoding="utf-8"))
    anchors = doc.get("points") or doc.get("anchors") or []

    pts = _load_teleport_points()
    if not pts:
        raise RuntimeError(
            "找不到传送点数据源，无法验证。已找: "
            + ", ".join(str(p) for p in _TP_SEARCH))

    results, skipped = [], []
    for a in anchors:
        label = a.get("name")
        key = _area_key(label)
        if not key:
            skipped.append(str(label))
            continue
        pool = [p for p in pts
                if any(key == _norm(x) or key in _norm(x) for x in p["areas"])]
        st = [p for p in pool if "神像" in p["name"]]
        hit = st[0] if len(st) == 1 else (pool[0] if len(pool) == 1 else None)
        if hit is None:
            skipped.append(str(label))
            continue
        wx, wz = hit["wx"], hit["wz"]
        px, py = world_to_pixel(wx, wz)
        dx = px - float(a["x"])
        dy = py - float(a["y"])
        results.append({
            "name": str(label), "matched_name": hit["name"],
            "world_x": wx, "world_z": wz,
            "pixel_x": float(a["x"]), "pixel_y": float(a["y"]),
            "dx": dx, "dy": dy,
            "residual_px": (dx * dx + dy * dy) ** 0.5,
        })

    if not results:
        raise RuntimeError("没有任何锚点匹配得上，无法验证")
    rms = (sum(r["residual_px"] ** 2 for r in results) / len(results)) ** 0.5
    worst = max(results, key=lambda r: r["residual_px"])
    return {
        "ok": rms < 50 and worst["residual_px"] < 200,
        "rms_px": rms,
        "max_residual_px": worst["residual_px"],
        "worst_name": worst["name"],
        "matched": len(results),
        "skipped": skipped,
        "pixels_per_unit": PIXELS_PER_UNIT,
        "rms_meters": rms / PIXELS_PER_UNIT,
        "points": results,
    }


__all__ = [
    "FIT_ANCHOR_COUNT",
    "FIT_MAX_RESIDUAL_PX",
    "FIT_RMS_PX",
    "IMAGE_HEIGHT",
    "IMAGE_WIDTH",
    "PIXELS_PER_UNIT",
    "is_inside",
    "pixel_to_world",
    "verify_anchors",
    "world_to_pixel",
]
