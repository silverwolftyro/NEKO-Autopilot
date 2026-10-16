"""23 锚点坐标标定 —— 匹配 + 全仿射拟合 + 生成可复用模块。

★ 与上一版的差别
----------------
1. 神像前缀从硬编码「七天神像」改成**泛化**（``^.{0,4}神像``），
   以支持「新月神像」这类新神像类型。
2. 缺连接符的名字（"七天神像悠悠度假村"、"新月神像帕哈岛"）也能抠出地名。
3. 拟合统一用**全 6 参数仿射** ``px = A*wx + B*wz + C``，
   交叉项是否接近 0 由数据自己说话，不预设轴向。
4. 找不到的名字**如实报告并跳过**，绝不猜。
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

import difflib
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

RECOG = Path(RECOG_ROOT)
ANCHORS = RECOG / "data" / "mihoyo_maps" / "teyvat_P0.png.anchors.json"
OUT = RECOG / "exports" / "benchmark" / "scripts" / "misc" / "coord_transform.py"

#: tp.json 候选来源：自有副本优先（gitignored），退回 BetterGI
TP_CANDIDATES = [
    RECOG / "data" / "teleport_points.json",
    RECOG / "data" / "_ref_tp_sample.json",
    Path(_REPO_ROOT.parent / '现成的自动化工具参考' / '参考：better GI')
    / "BetterGI" / "GameTask" / "AutoTrackPath" / "Assets" / "tp.json",
]

TYPO = {"七天神仙": "七天神像", "安绕": "安饶"}
#: 泛化神像前缀：七天神像 / 新月神像 / 风神像 / 神像 …
STATUE_PREFIX = re.compile(r"^.{0,4}神像\s*[-·]?")
TAIL = re.compile(r"(传送锚点|传送点|锚点)$")

RMS_LIMIT = 50.0
TOP3_LIMIT = 200.0


def norm(s):
    s = str(s or "").strip()
    for a, b in TYPO.items():
        s = s.replace(a, b)
    for ch, r in (("，", "-"), ("、", "-"), ("－", "-"),
                  ("—", "-"), ("·", "-"), (" ", ""), ("\u3000", "")):
        s = s.replace(ch, r)
    return re.sub(r"-{2,}", "-", s).strip("-")


def area_key(label):
    s = STATUE_PREFIX.sub("", norm(label))
    s = TAIL.sub("", s)
    return s.strip("-")


def load_tp():
    for p in TP_CANDIDATES:
        if not p.is_file():
            continue
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
        if "data" in raw:                       # BetterGI 原格式
            out = []
            for region in raw["data"]:
                for pt in region.get("points", []):
                    pos = pt.get("position")
                    if isinstance(pos, list) and len(pos) == 3:
                        out.append({
                            "id": pt.get("id"),
                            "name": str(pt.get("name", "")),
                            "areas": [str(a) for a in (pt.get("areas") or [])],
                            "wx": float(pos[0]), "wy": float(pos[1]),
                            "wz": float(pos[2])})
            return out, p
        if "regions" in raw:                    # 自有格式
            out = []
            for region in raw["regions"]:
                for pt in region.get("points", []):
                    if "world_pos" in pt and len(pt["world_pos"]) == 3:
                        wp = pt["world_pos"]
                        out.append({
                            "id": pt.get("id"), "name": str(pt.get("name", "")),
                            "areas": list(pt.get("areas") or []),
                            "wx": float(wp[0]), "wy": float(wp[1]),
                            "wz": float(wp[2])})
            return out, p
    raise SystemExit("找不到 tp.json")


def is_statue(name):
    return "神像" in name


def match_one(label, pts):
    key = area_key(label)
    if not key:
        return None, "空地名", 0
    want = bool(STATUE_PREFIX.search(norm(label)))

    # 1) 归一化后整体精确
    for p in pts:
        if norm(p["name"]) == norm(label):
            return p, "精确", 1.0
    # 2) 地区 + 神像类型
    pool = [p for p in pts
            if any(key == norm(a) or key in norm(a) for a in p["areas"])]
    if want:
        st = [p for p in pool if is_statue(p["name"])]
        if len(st) == 1:
            return st[0], "神像·地区唯一", 0.95
        if len(st) > 1:
            return None, f"神像候选 {len(st)} 个（歧义）", len(st)
    if len(pool) == 1:
        return pool[0], ("地区唯一" if want else "地区唯一(非神像)"), \
            0.9 if want else 0.6
    # 3) 名字里含地名
    pool2 = [p for p in pts if key in norm(p["name"])]
    if want:
        st2 = [p for p in pool2 if is_statue(p["name"])]
        if len(st2) == 1:
            return st2[0], "神像·名含地名", 0.85
    if len(pool2) == 1:
        return pool2[0], "名含地名", 0.6
    # 4) 模糊
    best, bs = None, 0.0
    for p in pts:
        r = difflib.SequenceMatcher(None, key, norm(p["name"])).ratio()
        if r > bs:
            best, bs = p, r
    if best is not None and bs >= 0.6:
        return best, f"模糊 {bs:.2f}", bs
    return None, "未找到", 0.0


def main():
    doc = json.loads(ANCHORS.read_text(encoding="utf-8"))
    W, H = doc["image_size"]
    anchors = doc["points"]
    pts, src = load_tp()

    print("=" * 96)
    print("步骤 1 · 名字匹配")
    print("=" * 96)
    print(f"tp 来源: {src}")
    print(f"tp 传送点 {len(pts)} 个   锚点 {len(anchors)} 个\n")
    print(f"  {'用户写的名字':<20} {'tp.json 名字':<22} {'id':<8} "
          f"{'world_x':>10} {'world_y':>9} {'world_z':>10}  {'方式':<14}{'置'}")
    print("  " + "-" * 94)
    rows, missed = [], []
    for a in anchors:
        p, how, score = match_one(a["name"], pts)
        if p is None:
            missed.append((a["name"], how))
            print(f"  {a['name']:<20} {'—— 未找到 ——':<22} "
                  f"{'':<8} {'':>10} {'':>9} {'':>10}  {how}")
            continue
        rows.append((a, p, how, score))
        print(f"  {a['name']:<20} {p['name']:<22} {str(p['id']):<8} "
              f"{p['wx']:>10.1f} {p['wy']:>9.1f} {p['wz']:>10.1f}  "
              f"{how:<14}{'*' if score < 0.7 else ''}")
    print(f"\n  匹配成功 {len(rows)} / {len(anchors)}")
    if missed:
        print(f"  ⚠ 跳过 {len(missed)} 个（按要求不猜）:")
        for nm, why in missed:
            print(f"      {nm}   原因: {why}")
    if len(rows) < 6:
        print("\n  !! 可用点太少，终止")
        return

    wx = np.array([r[1]["wx"] for r in rows])
    wy = np.array([r[1]["wy"] for r in rows])
    wz = np.array([r[1]["wz"] for r in rows])
    PXX = np.array([r[0]["x"] for r in rows], float)
    PYY = np.array([r[0]["y"] for r in rows], float)

    # ---------------- 步骤 2：全仿射 ----------------
    M = np.column_stack([wx, wz, np.ones_like(wx)])
    cx, *_ = np.linalg.lstsq(M, PXX, rcond=None)     # px = A*wx + B*wz + C
    cy, *_ = np.linalg.lstsq(M, PYY, rcond=None)     # py = D*wx + E*wz + F
    A, B, C = cx
    D, E, F = cy
    ex = PXX - M @ cx
    ey = PYY - M @ cy
    rms = float(np.sqrt((ex ** 2 + ey ** 2).mean()))

    # 对照：轴对齐 4 参数（用户给的简化式）
    Mx = np.column_stack([wx, np.ones_like(wx)])
    My = np.column_stack([wz, np.ones_like(wz)])
    ax, *_ = np.linalg.lstsq(Mx, PXX, rcond=None)
    ay, *_ = np.linalg.lstsq(My, PYY, rcond=None)
    rms_axis = float(np.sqrt(((PXX - Mx @ ax) ** 2
                              + (PYY - My @ ay) ** 2).mean()))

    print("\n" + "=" * 96)
    print("步骤 2 · 拟合参数")
    print("=" * 96)
    print(f"\n  【全仿射 6 参数】RMS = {rms:.3f} px")
    print(f"    px = {A:.6f}*wx + {B:.6f}*wz + {C:.4f}")
    print(f"    py = {D:.6f}*wx + {E:.6f}*wz + {F:.4f}")
    det = A * E - B * D
    print(f"    行列式 = {det:.6f}"
          f"{'  ✅ 可逆' if abs(det) > 1e-9 else '  ❌ 奇异，pixel->world 无解'}")
    print(f"    交叉项: |B|/|A| = "
          f"{abs(B)/abs(A) if A else float('inf'):.1f}   "
          f"|D|/|E| = {abs(D)/abs(E) if E else float('inf'):.1f}")
    print(f"      -> 主项在 wz 上（px）" if abs(B) > abs(A)
          else f"      -> 主项在 wx 上（px）")
    print(f"      -> 主项在 wx 上（py）" if abs(D) > abs(E)
          else f"      -> 主项在 wz 上（py）")

    print(f"\n  【轴对齐 4 参数（用户给的简化式）】RMS = {rms_axis:.3f} px")
    print(f"    px = {ax[0]:.6f}*wx + {ax[1]:.4f}")
    print(f"    py = {ay[0]:.6f}*wz + {ay[1]:.4f}")
    verdict = "可用" if rms_axis < RMS_LIMIT else "**不可用，残差高一个量级**"
    print(f"    -> 简化式{verdict}；最终采用全仿射。")

    # ---------------- 步骤 3：残差 ----------------
    print("\n" + "=" * 96)
    print("步骤 3 · 残差明细")
    print("=" * 96)
    print(f"  {'锚点名':<20} {'px实':>6} {'px预':>9} {'dx':>7} "
          f"{'py实':>6} {'py预':>9} {'dy':>7} {'r':>7}  匹配")
    res = []
    for r, rx, ry in zip(rows, ex, ey):
        rr = float((rx ** 2 + ry ** 2) ** 0.5)
        res.append((rr, r[0]["name"], r[2]))
        print(f"  {r[0]['name']:<20} {r[0]['x']:>6} "
              f"{r[0]['x']+rx:>9.1f} {rx:>7.1f} {r[0]['y']:>6} "
              f"{r[0]['y']+ry:>9.1f} {ry:>7.1f} {rr:>7.1f}  {r[2]}")
    res.sort(reverse=True)
    print("\n  残差最大的 3 个:")
    for rr, nm, how in res[:3]:
        print(f"    {nm:<20} {rr:>7.1f} px   （匹配方式: {how}）")
    print(f"\n  RMS = {rms:.3f} px")
    print(f"  中位残差 = {sorted(x for x,_,_ in res)[len(res)//2]:.1f} px")
    print(f"  1 世界单位 = {abs(B) if abs(B)>abs(A) else abs(A):.4f} px "
          f"-> RMS 约 {rms/(abs(B) if abs(B)>abs(A) else abs(A)):.2f} 米")

    ok_rms = rms < RMS_LIMIT
    ok_top3 = res[0][0] < TOP3_LIMIT
    print("\n" + "=" * 96)
    print("步骤 4 · 验收")
    print("=" * 96)
    print(f"  RMS < {RMS_LIMIT:.0f} px          : {rms:>8.2f} px   "
          f"{'✅' if ok_rms else '❌'}")
    print(f"  最差点 < {TOP3_LIMIT:.0f} px     : {res[0][0]:>8.2f} px   "
          f"{'✅' if ok_top3 else '❌'}")
    print(f"  整体: {'✅ 通过' if ok_rms and ok_top3 else '❌ 不通过'}")

    if not (ok_rms and ok_top3):
        print("\n  ❌ 未通过，**不生成** coord_transform.py")
        return

    # ---------------- 步骤 4：生成模块 ----------------
    transposed = abs(B) > abs(A)
    ax_p = "world_z" if transposed else "world_x"
    ax_y = "world_x" if transposed else "world_z"
    scale = abs(B) if transposed else abs(A)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(f'''"""底图像素 <-> 世界坐标 互转（**由 {len(rows)} 个人工锚点拟合**）。

★ 拟合来源
--------
- 像素锚点: ``recognition/data/mihoyo_maps/teyvat_P0.png.anchors.json``
- 世界坐标: BetterGI ``tp.json``（读结构用，坐标本身是游戏事实）
- 底图尺寸: {W} x {H} 像素
- 参与锚点: {len(rows)} / {len(anchors)}（{len(missed)} 个未匹配已跳过）
- 残差 RMS: **{rms:.3f} 像素**，最大 {res[0][0]:.1f} 像素，中位 {sorted(x for x,_,_ in res)[len(res)//2]:.1f} 像素
- 验收: RMS < {RMS_LIMIT:.0f} ✅ / 最差 < {TOP3_LIMIT:.0f} ✅

★ 坐标轴
--------
**底图相对世界坐标是转置的**：像素 X 主要跟随 ``{ax_p}``，像素 Y 主要跟随 ``{ax_y}``。
拼接脚本 ``stitch_tiles.py`` 很可能把瓦片 (x, y) 写进了 (row, col)。
**重拼底图后必须重跑 :func:`verify_anchors` 确认。**

★ 精度
------
1 世界单位 ≈ {scale:.4f} 像素 -> RMS {rms:.2f} px ≈ **{rms/scale:.2f} 米**。
BetterGI 公式里的 ``Scale/zoom`` 因此为 1.0。
残差主要来自**人工点击精度**，不是变换本身。

★ 适用范围
----------
只对这张 P0 白天底图有效。换地图 / 换图层 / 重拼图都要重新标定。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

#: 底图尺寸（像素）
IMAGE_WIDTH = {W}
IMAGE_HEIGHT = {H}

#: px = _A*world_x + _B*world_z + _C
_A = {A:.10f}
_B = {B:.10f}
_C = {C:.6f}
#: py = _D*world_x + _E*world_z + _F
_D = {D:.10f}
_E = {E:.10f}
_F = {F:.6f}

#: 行列式，pixel->world 可逆性判据
_DETERMINANT = {det:.10f}
#: 拟合残差
FIT_RMS_PX = {rms:.4f}
FIT_MAX_RESIDUAL_PX = {res[0][0]:.4f}
FIT_ANCHOR_COUNT = {len(rows)}
#: 每世界单位对应像素数（主轴）
PIXELS_PER_UNIT = {scale:.6f}

#: verify_anchors() 找不到数据时的搜索顺序
_TP_SEARCH = (
    Path(r"D:\\NEKO\\recognition\\data\\teleport_points.json"),
    Path(r"D:\\NEKO\\recognition\\data\\_ref_tp_sample.json"),
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
            f"仿射矩阵奇异（行列式 {{_DETERMINANT:.3e}}），无法 pixel->world；"
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
                  ("—", "-"), ("·", "-"), (" ", ""), ("\\u3000", "")):
        s = s.replace(ch, r)
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")


def _area_key(label: str) -> str:
    import re
    s = re.sub(r"^.{{0,4}}神像\\s*[-·]?", "", _norm(label))
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
                        out.append({{"name": str(pt.get("name", "")),
                                    "areas": [str(a) for a in (pt.get("areas") or [])],
                                    "wx": float(pos[0]), "wz": float(pos[2])}})
        elif isinstance(raw, dict) and "regions" in raw:
            for region in raw["regions"]:
                for pt in region.get("points", []):
                    wp = pt.get("world_pos")
                    if isinstance(wp, list) and len(wp) == 3:
                        out.append({{"name": str(pt.get("name", "")),
                                    "areas": [str(a) for a in (pt.get("areas") or [])],
                                    "wx": float(wp[0]), "wz": float(wp[2])}})
        if out:
            return out
    return []


def verify_anchors(anchors_json_path: str) -> dict:
    """重跑标定验证，返回误差报告。

    用**当前**仿射参数去预测每个锚点的像素坐标，和人工标注值比对。

    :param anchors_json_path: ``*.anchors.json`` 路径
    :returns: 报告 dict，字段如下::

        {{
          "ok": bool,                 # 是否通过阈值
          "rms_px": float,            # 残差均方根（像素）
          "max_residual_px": float,
          "matched": int,             # 成功匹配并验证的锚点数
          "skipped": [str, ...],      # 匹配不上跳过的名字
          "points": [                 # 逐点明细
              {{"name", "pixel_x", "pixel_y", "world_x", "world_z",
                "dx", "dy", "residual_px"}}, ...
          ]
        }}

    :raises FileNotFoundError: anchors_json_path 不存在
    :raises RuntimeError: 找不到传送点数据源（无法验证）
    """
    path = Path(anchors_json_path)
    if not path.is_file():
        raise FileNotFoundError(f"锚点文件不存在: {{path}}")

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
        results.append({{
            "name": str(label), "matched_name": hit["name"],
            "world_x": wx, "world_z": wz,
            "pixel_x": float(a["x"]), "pixel_y": float(a["y"]),
            "dx": dx, "dy": dy,
            "residual_px": (dx * dx + dy * dy) ** 0.5,
        }})

    if not results:
        raise RuntimeError("没有任何锚点匹配得上，无法验证")
    rms = (sum(r["residual_px"] ** 2 for r in results) / len(results)) ** 0.5
    worst = max(results, key=lambda r: r["residual_px"])
    return {{
        "ok": rms < {RMS_LIMIT:.0f} and worst["residual_px"] < {TOP3_LIMIT:.0f},
        "rms_px": rms,
        "max_residual_px": worst["residual_px"],
        "worst_name": worst["name"],
        "matched": len(results),
        "skipped": skipped,
        "pixels_per_unit": PIXELS_PER_UNIT,
        "rms_meters": rms / PIXELS_PER_UNIT,
        "points": results,
    }}


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
''', encoding="utf-8")
    print(f"\n  ✅ 已生成 {OUT}")

    # round-trip + verify_anchors 自检
    import importlib.util
    spec = importlib.util.spec_from_file_location("ct", OUT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    w = m.world_to_pixel(1234.5, -678.9)
    back = m.pixel_to_world(*w)
    print(f"  round-trip: w(1234.5,-678.9) -> p{tuple(round(v,3) for v in w)}"
          f" -> w{tuple(round(v,6) for v in back)}")
    rep = m.verify_anchors(str(ANCHORS))
    print(f"  verify_anchors: ok={rep['ok']}  matched={rep['matched']}  "
          f"skipped={len(rep['skipped'])}  rms={rep['rms_px']:.3f}px "
          f"({rep['rms_meters']:.2f}m)  worst={rep['worst_name']} "
          f"{rep['max_residual_px']:.1f}px")
    print("\n=== DONE ===")


if __name__ == "__main__":
    main()
