"""用人工标注的像素锚点 + tp.json 世界坐标拟合底图仿射变换。

★ 输入
----
- ``teyvat_P0.png.anchors.json``  人工标注的像素坐标
- ``_ref_tp_sample.json``          BetterGI tp.json（936 个传送点世界坐标）

★ 方法
----
先做全仿射（6 参数，含交叉项），看交叉项是否接近 0。
若接近 0，退化成轴对齐的 4 参数模型，**这个才是可解释的**。

★ 名字匹配
--------
tp.json 里一个传送点常常只有"七天神像-风"这种主名，
区域名放在 ``areas[]``。所以候选池同时收：
    主名 / 主名+区域 / 纯区域名
匹配顺序：精确 -> 规范化 -> 纠错替换 -> difflib 模糊。
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

import difflib
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

RECOG = Path(RECOG_ROOT)
ANCHORS = RECOG / "data" / "mihoyo_maps" / "teyvat_P0.png.anchors.json"
TP = RECOG / "data" / "_ref_tp_sample.json"

#: 常见错别字 -> 正确写法
ALIAS = {
    "七天神仙": "七天神像",
    "传送锚点": "",
    "传送点": "",
    "锚点": "",
}
#: 归一化时剥掉的前缀/后缀
STRIP = ("七天神像", "神像", "传送锚点", "传送点", "锚点", " Statue")


def norm(s: str) -> str:
    s = str(s or "").strip()
    s = s.replace("，", "-").replace("、", "-").replace(" ", "")
    s = s.replace("－", "-").replace("—", "-")
    for a, b in ALIAS.items():
        s = s.replace(a, b)
    s = re.sub(r"^-+|-+$", "", s)
    s = re.sub(r"-{2,}", "-", s)
    return s


def build_candidates() -> list[tuple[str, str, str, float, float]]:
    """(主名, 展示名, 区域串, world_x, world_z)"""
    raw = json.loads(TP.read_text(encoding="utf-8-sig"))
    out = []
    for region in raw.get("data", []):
        for p in region.get("points", []):
            pos = p.get("position")
            if not (isinstance(pos, list) and len(pos) == 3):
                continue
            name = str(p.get("name", ""))
            areas = [str(a) for a in (p.get("areas") or [])]
            wx, wz = float(pos[0]), float(pos[2])
            out.append((name, name, "/".join(areas), wx, wz))
            for a in areas:
                out.append((name, a, "/".join(areas), wx, wz))
                out.append((name, f"{name}-{a}", "/".join(areas), wx, wz))
    return out


def match(label: str, cands) -> tuple | None:
    n = norm(label)
    if not n:
        return None
    # 1) 规范化后精确
    for c in cands:
        if norm(c[1]) == n:
            return (c, 1.0, "精确")
    # 2) 子串包含（双向）
    best = None
    for c in cands:
        cn = norm(c[1])
        if not cn:
            continue
        if n in cn or cn in n:
            r = difflib.SequenceMatcher(None, n, cn).ratio()
            if best is None or r > best[1]:
                best = (c, r, "包含")
    if best and best[1] >= 0.55:
        return best
    # 3) 模糊
    best = None
    for c in cands:
        cn = norm(c[1])
        if not cn:
            continue
        r = difflib.SequenceMatcher(None, n, cn).ratio()
        if best is None or r > best[1]:
            best = (c, r, "模糊")
    if best and best[1] >= 0.45:
        return best
    return None


def fit(M: np.ndarray, t: np.ndarray) -> np.ndarray:
    """最小二乘：解 t ≈ M @ w"""
    coef, *_ = np.linalg.lstsq(M, t, rcond=None)
    return coef


def main() -> None:
    print("=" * 80)
    print("A · 仿射矩阵拟合")
    print("=" * 80)

    anchors = json.loads(ANCHORS.read_text(encoding="utf-8"))["points"]
    W, H = json.loads(ANCHORS.read_text(encoding="utf-8"))["image_size"]
    print(f"底图尺寸 {W} x {H}   锚点 {len(anchors)} 个")

    cands = build_candidates()
    print(f"tp.json 候选条目 {len(cands)} 条（含主名/区域/组合）\n")

    rows, miss = [], []
    print("-" * 80)
    print("  锚点名 -> tp.json 匹配")
    print("-" * 80)
    for a in anchors:
        r = match(a["name"], cands)
        if r is None:
            miss.append(a["name"])
            print(f"  [未匹配] {a['name']}")
            continue
        (nm, disp, area, wx, wz), score, how = r
        rows.append((a["name"], a["x"], a["y"], wx, wz, score, how, disp, area))
        flag = " " if score >= 0.9 else ("~" if score >= 0.6 else "!")
        print(f"  {flag} {a['name']:<22} -> {disp:<20} "
              f"[{how} {score:.2f}]  w=({wx:>9.1f}, {wz:>9.1f})  "
              f"px=({a['x']:>6}, {a['y']:>6})")

    if miss:
        print(f"\n  ⚠ 未匹配 {len(miss)} 个: {miss}")
    print(f"\n  参与拟合 {len(rows)} 个点")
    if len(rows) < 4:
        print("  !! 点太少，无法拟合")
        return

    wx = np.array([r[3] for r in rows])
    wz = np.array([r[4] for r in rows])
    px = np.array([r[1] for r in rows], dtype=float)
    py = np.array([r[2] for r in rows], dtype=float)

    # ---- 模型 1：全仿射 6 参数 ----
    ones = np.ones_like(wx)
    Mfull = np.column_stack([ones, wx, wz])
    cx = fit(Mfull, px)
    cy = fit(Mfull, py)
    ex = px - Mfull @ cx
    ey = py - Mfull @ cy
    rms_full = float(np.sqrt((ex ** 2 + ey ** 2).mean()))

    # ---- 模型 2：轴对齐 4 参数 ----
    Mx = np.column_stack([ones, wx])
    My = np.column_stack([ones, wz])
    ax, bx = fit(Mx, px)
    ay, by = fit(My, py)
    ex2 = px - Mx @ [ax, bx]
    ey2 = py - My @ [ay, by]
    rms_axis = float(np.sqrt((ex2 ** 2 + ey2).mean()))
    rms_axis = float(np.sqrt(((ex2 ** 2) + (ey2 ** 2)).mean()))

    # ---- 模型 3：轴交换（验证 y 轴到底对应 wx 还是 wz）----
    ax2, bx2 = fit(np.column_stack([ones, wz]), px)
    ay2, by2 = fit(np.column_stack([ones, wx]), py)
    ex3 = px - np.column_stack([ones, wz]) @ [ax2, bx2]
    ey3 = py - np.column_stack([ones, wx]) @ [ay2, by2]
    rms_swap = float(np.sqrt(((ex3 ** 2) + (ey3 ** 2)).mean()))

    print("\n" + "=" * 80)
    print("拟合结果")
    print("=" * 80)
    print(f"\n  模型 A  全仿射 6 参数 (含交叉项)")
    print(f"    px = {cx[0]:>12.4f} + {cx[1]:>10.6f}*wx + {cx[2]:>10.6f}*wz")
    print(f"    py = {cy[0]:>12.4f} + {cy[1]:>10.6f}*wx + {cy[2]:>10.6f}*wz")
    print(f"    RMS = {rms_full:.2f} px")
    print(f"    交叉项占主项比例: x {abs(cx[2])/abs(cx[1]):.2%}  "
          f"y {abs(cy[1])/abs(cy[1] or 1):.2%}")

    print(f"\n  模型 B  轴对齐 4 参数  px = a*wx + b   py = c*wz + d")
    print(f"    px = {ax:>12.4f} + {bx:>10.6f} * wx")
    print(f"    py = {ay:>12.4f} + {by:>10.6f} * wz")
    print(f"    RMS = {rms_axis:.2f} px")

    print(f"\n  模型 C  轴交换（px~wz, py~wx）  RMS = {rms_swap:.2f} px")
    print(f"    -> {'轴交换更差，B 模型正确' if rms_axis < rms_swap else '轴交换更好！需重新检查'}")

    best = min(rms_full, rms_axis, rms_swap)
    print(f"\n  最优 RMS = {best:.2f} px"
          f"   阈值 50 px  ->  {'✅ 通过' if best < 50 else '❌ 不通过'}")

    # ---- 残差表（用最优模型 B）----
    print("\n" + "=" * 80)
    print("残差明细（按模型 B 轴对齐）")
    print("=" * 80)
    print(f"  {'锚点':<22} {'px实际':>7} {'px预测':>9} {'dx':>7} "
          f"{'py实际':>7} {'py预测':>9} {'dy':>7} {'r':>7}")
    res = []
    for r in rows:
        ppx = ax + bx * r[3]
        ppy = ay + by * r[4]
        dx, dy = ppx - r[1], ppy - r[2]
        rr = (dx * dx + dy * dy) ** 0.5
        res.append((rr, r[0]))
        print(f"  {r[0]:<22} {r[1]:>7} {ppx:>9.1f} {dx:>7.1f} "
              f"{r[2]:>7} {ppy:>9.1f} {dy:>7.1f} {rr:>7.1f}")
    res.sort(reverse=True)
    print(f"\n  残差最大的 3 个:")
    for rr, nm in res[:3]:
        print(f"    {nm:<22} {rr:.1f} px")
    print(f"  残差中位数 {sorted(x for x, _ in res)[len(res)//2]:.1f} px")

    # ---- 尺度换算 ----
    print("\n" + "=" * 80)
    print("尺度换算")
    print("=" * 80)
    print(f"  1 世界单位 = {abs(bx):.4f} 像素 (x)   {abs(by):.4f} 像素 (y)")
    print(f"  1 像素 = {1/abs(bx):.4f} 世界单位")
    print(f"  36864 px 宽 -> 覆盖 {36864/abs(bx):.0f} 世界单位")
    print(f"  与 BetterGI 公式比对: Scale/zoom = {-abs(bx):.4f}"
          f"（负号来自 Y 轴/方向翻转，见报告）")

    print("\n=== DONE ===")


if __name__ == "__main__":
    main()
