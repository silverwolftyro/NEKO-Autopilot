"""仿射拟合 v4（最终）—— 神像唯一匹配 + 8 种轴向假设优选 + 生成坐标变换模块。

★ 为什么是"轴交换"
------------------
19 个锚点在 8 种轴向假设里，``px~wz, py~wx`` 拿到 19/19 内点。
其余假设残差几百到几千像素。说明拼出来的底图相对世界坐标是**转置**的 ——
很可能是 ``stitch_tiles.py`` 把瓦片 (x,y) 写进了 (row,col)。

★ 顺带的干净结果
----------------
两个方向的斜率都 ≈ -1.0，即 1 世界单位 = 1 像素、两个轴都反向。
BetterGI 公式里的 ``Scale/zoom`` 因此就是 **1.0**。
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

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

RECOG = Path(RECOG_ROOT)
ANCHORS = RECOG / "data" / "mihoyo_maps" / "teyvat_P0.png.anchors.json"
TP = RECOG / "data" / "_ref_tp_sample.json"
OUT_MODULE = RECOG / "exports" / "benchmark" / "scripts" / "misc" / "coord_transform.py"

TYPO = {"七天神仙": "七天神像", "安绕": "安饶"}
STATUE_PREFIX = re.compile(r"七天神像|风神像|神像")


def norm(s):
    s = str(s or "").strip()
    for a, b in TYPO.items():
        s = s.replace(a, b)
    for ch in "，、－— ":
        s = s.replace(ch, "-" if ch in "，、－—" else "")
    return re.sub(r"-{2,}", "-", s).strip("-")


def area_key(label):
    s = norm(label)
    s = re.sub(r"^(七天神像|风神像|神像)-?", "", s)
    s = re.sub(r"(传送锚点|传送点|锚点)$", "", s)
    return s.strip("-")


def load_tp():
    raw = json.loads(TP.read_text(encoding="utf-8-sig"))
    out = []
    for region in raw.get("data", []):
        for p in region.get("points", []):
            pos = p.get("position")
            if not (isinstance(pos, list) and len(pos) == 3):
                continue
            out.append({"name": norm(p.get("name", "")),
                        "areas": [norm(a) for a in (p.get("areas") or [])],
                        "wx": float(pos[0]), "wz": float(pos[2])})
    return out


def fit(u, v, px, py):
    A = np.column_stack([np.ones_like(u), u])
    B = np.column_stack([np.ones_like(v), v])
    cx, *_ = np.linalg.lstsq(A, px, rcond=None)
    cy, *_ = np.linalg.lstsq(B, py, rcond=None)
    ex = px - (cx[0] + cx[1] * u)
    ey = py - (cy[0] + cy[1] * v)
    return cx[0], cx[1], cy[0], cy[1], float(np.sqrt((ex**2 + ey**2).mean()))


def main():
    doc = json.loads(ANCHORS.read_text(encoding="utf-8"))
    W, H = doc["image_size"]
    anchors = doc["points"]
    pts = load_tp()

    rows, suspect = [], []
    for a in anchors:
        key = area_key(a["name"])
        want = bool(STATUE_PREFIX.search(norm(a["name"])))
        cands = [p for p in pts
                 if key and (want == ("神像" in p["name"]))
                 and (any(key == x or key in x for x in p["areas"])
                      or key in p["name"])]
        if not cands:
            suspect.append((a["name"], "无候选"))
            continue
        c = cands[0]
        rows.append((a["name"], a["x"], a["y"], c["wx"], c["wz"],
                     c["name"], len(cands), want))
        if not want or len(cands) > 1:
            suspect.append((a["name"], f"非神像/多候选({len(cands)})"))

    print("=" * 82)
    print("锚点 -> tp.json 匹配（神像强制过滤）")
    print("=" * 82)
    print(f"  {'锚点名':<22} {'世界 x':>10} {'世界 z':>10} {'px':>6} {'py':>6}"
          f"  {'候选':>4}  tp 主名")
    for nm, x, y, wx, wz, tn, n, _w in rows:
        print(f"  {nm:<22} {wx:>10.1f} {wz:>10.1f} {x:>6} {y:>6} {n:>4}  {tn}")
    if suspect:
        print(f"\n  ⚠ 可疑锚点 {suspect}")

    wx = np.array([r[3] for r in rows])
    wz = np.array([r[4] for r in rows])
    px = np.array([r[1] for r in rows], float)
    py = np.array([r[2] for r in rows], float)

    print("\n" + "=" * 82)
    print("轴向假设对比")
    print("=" * 82)
    results = []
    for label, u, v in [
        ("px~wx , py~wz", wx, wz), ("px~wx , py~-wz", wx, -wz),
        ("px~-wx, py~wz ", -wx, wz), ("px~-wx, py~-wz", -wx, -wz),
        ("px~wz , py~wx", wz, wx), ("px~wz , py~-wx", wz, -wx),
        ("px~-wz, py~wx ", -wz, wx), ("px~-wz, py~-wx", -wz, -wx),
    ]:
        a, b, c, d, rms = fit(u, v, px, py)
        results.append((rms, label, a, b, c, d))
        print(f"  {label}   RMS = {rms:10.2f} px")
    results.sort(key=lambda t: t[0])
    rms, label, a, b, c, d = results[0]

    print("\n" + "=" * 82)
    print(f"最优：{label}    RMS = {rms:.2f} px    阈值 50 -> "
          f"{'✅ 通过' if rms < 50 else '❌ 不通过'}")
    print("=" * 82)
    swapped = label.strip().startswith("px~w") and "wz" in label.split(",")[0]
    if swapped:
        print(f"  px = {a:.4f} + ({b:.6f}) * world_z")
        print(f"  py = {c:.4f} + ({d:.6f}) * world_x")
    else:
        print(f"  px = {a:.4f} + ({b:.6f}) * world_x")
        print(f"  py = {c:.4f} + ({d:.6f}) * world_z")
    print(f"  1 世界单位 = {abs(b):.4f} px (px轴) / {abs(d):.4f} px (py轴)")
    print(f"  BetterGI Scale/zoom ≈ {abs(b):.4f}")

    print(f"\n  {'锚点':<22} {'px实':>6} {'px预':>9} {'dx':>7} "
          f"{'py实':>6} {'py预':>9} {'dy':>7} {'r':>7}")
    if swapped:
        u, v = wz, wx
    else:
        u, v = wx, wz
    res = []
    for r, uu, vv in zip(rows, u, v):
        pu, pv = a + b * uu, c + d * vv
        rr = ((pu - r[1]) ** 2 + (pv - r[2]) ** 2) ** 0.5
        res.append((rr, r[0]))
        print(f"  {r[0]:<22} {r[1]:>6} {pu:>9.1f} {pu-r[1]:>7.1f} "
              f"{r[2]:>6} {pv:>9.1f} {pv-r[2]:>7.1f} {rr:>7.1f}")
    res.sort(reverse=True)
    print(f"\n  残差最大 3 个:")
    for rr, nm in res[:3]:
        print(f"    {nm:<22} {rr:.1f} px")
    print(f"  中位残差 {sorted(x for x, _ in res)[len(res)//2]:.1f} px")

    # ---------- 生成模块 ----------
    if rms >= 50:
        print("\n  ❌ RMS 超阈值，**不生成** coord_transform.py"
              "（避免写进一个不可信的公式）")
        return
    ax_u = "world_z" if swapped else "world_x"
    ax_v = "world_x" if swapped else "world_z"
    OUT_MODULE.parent.mkdir(parents=True, exist_ok=True)
    OUT_MODULE.write_text(f'''"""底图像素 <-> 世界坐标 互转（**由 19 个人工锚点拟合而来**）。

★ 拟合来源
--------
- 像素锚点: ``recognition/data/mihoyo_maps/teyvat_P0.png.anchors.json``
- 世界坐标: BetterGI ``tp.json``（经 ``_tp_converter`` 同源副本读取）
- 底图尺寸: {W} x {H} 像素
- 参与锚点: {len(rows)} 个
- 残差 RMS: **{rms:.2f} 像素**（阈值 50，已通过）
- 最大残差: {res[0][0]:.1f} 像素（{res[0][1]}）

★ 轴向：{label.strip()}
------------------------------------------------
底图像素与世界坐标是**转置**关系：像素 X 跟随世界 Z，像素 Y 跟随世界 X，
两个方向都反向。拼接脚本 ``stitch_tiles.py`` 很可能把瓦片 (x, y) 写进了
(row, col)。**如果以后重拼底图，先验证这个转置是否还在。**

★ 精度
------
1 世界单位 ≈ {abs(b):.4f} 像素，因此 RMS {rms:.2f} px ≈ **{rms/abs(b):.2f} 米**。
BetterGI 公式里的 ``Scale/zoom`` 因此为 1.0。

★ 适用范围
----------
只对**这张 P0 白天底图**有效。换地图/换图层/重拼图都要重新标定。
"""

from __future__ import annotations

#: 底图尺寸（像素）
IMAGE_WIDTH = {W}
IMAGE_HEIGHT = {H}

#: px = _OFFSET_PX + _SLOPE_PX * {ax_u}
_OFFSET_PX = {a:.6f}
_SLOPE_PX = {b:.8f}
#: py = _OFFSET_PY + _SLOPE_PY * {ax_v}
_OFFSET_PY = {c:.6f}
_SLOPE_PY = {d:.8f}

#: 拟合残差（像素）
FIT_RMS_PX = {rms:.3f}


def world_to_pixel(world_x: float, world_z: float) -> tuple[float, float]:
    """世界坐标 -> 底图像素坐标。

    :param world_x: 世界 X（米）
    :param world_z: 世界 Z（米）
    :returns: ``(px, py)``
    """
    return (_OFFSET_PX + _SLOPE_PX * world_z,
            _OFFSET_PY + _SLOPE_PY * world_x)


def pixel_to_world(px: float, py: float) -> tuple[float, float]:
    """底图像素坐标 -> 世界坐标。``world_to_pixel`` 的精确逆。"""
    return ((py - _OFFSET_PY) / _SLOPE_PY,
            (px - _OFFSET_PX) / _SLOPE_PX)


def is_inside(px: float, py: float) -> bool:
    """像素是否落在底图范围内。"""
    return 0 <= px < IMAGE_WIDTH and 0 <= py < IMAGE_HEIGHT


__all__ = [
    "FIT_RMS_PX",
    "IMAGE_HEIGHT",
    "IMAGE_WIDTH",
    "is_inside",
    "pixel_to_world",
    "world_to_pixel",
]
''', encoding="utf-8")
    print(f"\n  ✅ 已生成 {OUT_MODULE}")
    print(f"     round-trip 校验: ", end="")
    import importlib.util
    spec = importlib.util.spec_from_file_location("ct", OUT_MODULE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    w = m.world_to_pixel(1234.5, -678.9)
    back = m.pixel_to_world(*w)
    print(f"w->p={tuple(round(v,3) for v in w)} p->w="
          f"{tuple(round(v,6) for v in back)}")
    print("\n=== DONE ===")


if __name__ == "__main__":
    main()
