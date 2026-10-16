"""仿射拟合 v2 —— 修正匹配 + 两轮消歧。

★ v1 为什么失败
--------------
1. **匹配串台**：「七天神像-风息山」「七天神仙-风龙废墟」「七天神仙-苍风高地」
   三个不同神像全被匹配到同一个通用条目「七天神像-风」，世界坐标完全相同。
   tp.json 里神像主名只有元素没有地点，**光靠主名无法区分是哪一座**。
2. **匹配到区域名**：其余锚点匹配到裸区域名（「龙脊雪山」…），
   那是该区域里**某个传送点**的位置，不是神像的位置。

★ v2 做法
--------
- 按 ``areas[]`` 匹配神像：锚点名去掉「七天神像-」前缀后的地名，
  去找 tp.json 中 **名字是神像** 且 **areas 含该地名** 的点。
- 唯一命中 = 高置信，直接用。
- 多个命中 = 歧义，留到第二轮：用第一轮模型挑残差最小的那个。
- 无法归属的锚点**不进拟合**，单独报告。
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

STATUE = re.compile(r"^(?:七天神仙|七天神像|风神像|神像|传送锚点|传送点|锚点)")
TYPO = {"七天神仙": "七天神像", "安绕": "安饶"}


def norm(s: str) -> str:
    s = str(s or "").strip()
    for a, b in TYPO.items():
        s = s.replace(a, b)
    s = s.replace("，", "-").replace("、", "-").replace("－", "-")
    s = s.replace("—", "-").replace(" ", "")
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-")


def area_key(label: str) -> str:
    """从锚点名里抠出地名。"""
    s = norm(label)
    s = STATUE.sub("", s)
    return s.strip("-")


def load_tp():
    raw = json.loads(TP.read_text(encoding="utf-8-sig"))
    pts = []
    for region in raw.get("data", []):
        rname = region.get("description") or region.get("mapName")
        for p in region.get("points", []):
            pos = p.get("position")
            if not (isinstance(pos, list) and len(pos) == 3):
                continue
            pts.append({
                "name": str(p.get("name", "")),
                "areas": [norm(a) for a in (p.get("areas") or [])],
                "type": str(p.get("type", "")),
                "country": str(p.get("country", "")),
                "region": rname,
                "wx": float(pos[0]), "wz": float(pos[2]),
                "id": p.get("id"),
            })
    return pts


def find_candidates(key: str, pts) -> list[dict]:
    """返回该地名的所有候选传送点（神像优先，其次 areas 命中）。"""
    if not key:
        return []
    out = []
    for p in pts:
        hit_area = any(key == a or key in a or a in key for a in p["areas"])
        hit_name = key in norm(p["name"])
        if not (hit_area or hit_name):
            continue
        # 神像：areas 命中 + 主名像神像
        is_statue = "神像" in p["name"] or " Statue" in p["name"]
        p2 = dict(p)
        p2["_statue"] = is_statue
        p2["_strong"] = hit_area or (is_statue and hit_name)
        out.append(p2)
    # 神像且 areas 精确命中排最前
    out.sort(key=lambda p: (not p["_strong"], not p["_statue"],
                            -difflib.SequenceMatcher(
                                None, key, norm(p["name"])).ratio()))
    return out


def fit_two(M, px, py):
    cx, *_ = np.linalg.lstsq(M, px, rcond=None)
    cy, *_ = np.linalg.lstsq(M, py, rcond=None)
    ex = px - M @ cx
    ey = py - M @ cy
    return cx, cy, float(np.sqrt((ex ** 2 + ey ** 2).mean()))


def main() -> None:
    print("=" * 80)
    print("A · 仿射拟合 v2（区域感知匹配 + 消歧）")
    print("=" * 80)

    doc = json.loads(ANCHORS.read_text(encoding="utf-8"))
    W, H = doc["image_size"]
    anchors = doc["points"]
    pts = load_tp()
    print(f"底图 {W} x {H}   锚点 {len(anchors)}   tp 点 {len(pts)}\n")

    confident, ambiguous, dropped = [], [], []
    print("-" * 80)
    print(f"  {'锚点名':<22} {'地名':<12} {'候选':>4} {'处理':<10} 命中")
    print("-" * 80)
    for a in anchors:
        key = area_key(a["name"])
        cands = find_candidates(key, pts)
        # 只保留「神像 or areas 精确命中」的强候选
        strong = [c for c in cands if c["_strong"]]
        if len(strong) == 1:
            confident.append((a, strong[0], key))
            print(f"  {a['name']:<22} {key:<12} {len(strong):>4} "
                  f"{'高置信':<10} {strong[0]['name']}  "
                  f"[{','.join(strong[0]['areas'][:2])}]")
        elif len(strong) > 1:
            ambiguous.append((a, strong, key))
            print(f"  {a['name']:<22} {key:<12} {len(strong):>4} "
                  f"{'歧义':<10} {len(strong)} 个同区神像")
        else:
            dropped.append((a, key, len(cands)))
            print(f"  {a['name']:<22} {key:<12} {len(cands):>4} "
                  f"{'丢弃':<10} 无强候选")

    def design(ws, zps):
        return np.column_stack([np.ones_like(ws), ws, zps])

    # ---------- 第一轮：高置信点，四种轴向假设 ----------
    print("\n" + "=" * 80)
    print("第一轮：只用高置信点，试四种轴向假设")
    print("=" * 80)
    wx = np.array([c["wx"] for _, c, _ in confident])
    wz = np.array([c["wz"] for _, c, _ in confident])
    px = np.array([a["x"] for a, _, _ in confident], dtype=float)
    py = np.array([a["y"] for a, _, _ in confident], dtype=float)

    hypotheses = {
        "px~wx , py~wz": (wx, wz),
        "px~wz , py~wx": (wz, wx),
        "px~-wx, py~-wz": (-wx, -wz),
        "px~-wz, py~-wx": (-wz, -wx),
    }
    best = None
    for label, (u, v) in hypotheses.items():
        cx, cy, rms = fit_two(design(u, v), px, py)
        print(f"  {label}   RMS = {rms:9.2f} px")
        if best is None or rms < best[2]:
            best = (label, (u, v), rms)
    print(f"\n  最优假设: {best[0]}   RMS = {best[2]:.2f} px")
    if len(confident) < 6:
        print("  !! 高置信点不足 6 个，结论不可靠")
        return

    # ---------- 第二轮：用第一轮模型消歧 ----------
    _, (u, v), _ = best
    cu, cv = best[1]
    M = design(u, v)
    cx, *_ = np.linalg.lstsq(M, px, rcond=None)
    cy, *_ = np.linalg.lstsq(M, py, rcond=None)

    print("\n" + "=" * 80)
    print(f"第二轮：用 [{best[0]}] 模型给歧义点消歧")
    print("=" * 80)
    resolved = []
    for a, cands, key in ambiguous:
        scored = []
        for c in cands:
            pu = c["wx"] if best[0].startswith("px~wx") else c["wz"]
            pv = c["wz"] if best[0].startswith("px~wx") else c["wx"]
            if "-" in best[0].split(",")[0]:
                pu = -pu
            if "-" in best[0].split(",")[1]:
                pv = -pv
            ppx = cx[0] + cx[1] * pu + cx[2] * pv
            ppy = cy[0] + cy[1] * pu + cy[2] * pv
            d = ((ppx - a["x"]) ** 2 + (ppy - a["y"]) ** 2) ** 0.5
            scored.append((d, c))
        scored.sort(key=lambda t: t[0])
        d, c = scored[0]
        resolved.append((a, c, key, d))
        print(f"  {a['name']:<22} -> {c['name']:<16} "
              f"[{','.join(c['areas'][:2])}]  残差 {d:8.1f} px"
              f"   (次优 {scored[1][0]:.1f})")

    # ---------- 合并，全量重拟合 ----------
    final = [(a, c, k) for a, c, k in confident] + \
            [(a, c, k) for a, c, k, _ in resolved]
    fx = np.array([c["wx"] for _, c, _ in final])
    fz = np.array([c["wz"] for _, c, _ in final])
    fpx = np.array([a["x"] for a, _, _ in final], dtype=float)
    fpy = np.array([a["y"] for a, _, _ in final], dtype=float)

    fu = fx if best[0].startswith("px~wx") else fz
    fv = fz if best[0].startswith("px~wx") else fx
    if "-" in best[0].split(",")[0]:
        fu = -fu
    if "-" in best[0].split(",")[1]:
        fv = -fv
    FM = design(fu, fv)
    cx, cy, rms = fit_two(FM, fpx, fpy)

    print("\n" + "=" * 80)
    print(f"最终模型（{len(final)} 点，全部 {len(anchors)} 中占比 "
          f"{len(final)/len(anchors):.0%}）")
    print("=" * 80)
    print(f"  轴向: {best[0]}")
    print(f"  px = {cx[0]:.4f} + {cx[1]:.6f}*u + {cx[2]:.6f}*v")
    print(f"  py = {cy[0]:.4f} + {cy[1]:.6f}*u + {cy[2]:.6f}*v")
    print(f"  RMS = {rms:.2f} px   阈值 50 px -> "
          f"{'✅ 通过' if rms < 50 else '❌ 不通过'}")

    print(f"\n  {'锚点':<22} {'px实际':>7} {'px预测':>9} {'dx':>8} "
          f"{'py实际':>7} {'py预测':>9} {'dy':>8} {'r':>8}")
    res = []
    for (a, c, k) in final:
        i = list(zip(fu, fv)).index((fu[len(res)], fv[len(res)])) \
            if False else None
        ppx = cx[0] + cx[1] * (fu[len(res)]) + cx[2] * (fv[len(res)])
        ppy = cy[0] + cy[1] * (fu[len(res)]) + cy[2] * (fv[len(res)])
        dx, dy = ppx - a["x"], ppy - a["y"]
        rr = (dx * dx + dy * dy) ** 0.5
        res.append((rr, a["name"]))
        print(f"  {a['name']:<22} {a['x']:>7} {ppx:>9.1f} {dx:>8.1f} "
              f"{a['y']:>7} {ppy:>9.1f} {dy:>8.1f} {rr:>8.1f}")
    res.sort(reverse=True)
    print(f"\n  残差最大 3 个:")
    for rr, nm in res[:3]:
        print(f"    {nm:<22} {rr:.1f} px")
    med = sorted(x for x, _ in res)[len(res) // 2]
    print(f"  中位残差 {med:.1f} px")

    if dropped:
        print(f"\n  ⚠ 未进拟合 {len(dropped)} 个:")
        for a, k, n in dropped:
            print(f"    {a['name']:<22} 地名={k!r} 弱候选 {n}")

    print(f"\n  尺度: 1 世界单位 = {abs(cx[1]):.4f} 像素 (u) / "
          f"{abs(cy[2]):.4f} 像素 (v)")
    print(f"  BetterGI 的 Scale/zoom 约等于 {abs(cx[1]):.3f}")
    print("\n=== DONE ===")


if __name__ == "__main__":
    main()
