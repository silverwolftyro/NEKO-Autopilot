"""仿射拟合 v3 —— 神像过滤 + RANSAC 消歧。

★ v2 为什么也不行
-----------------
候选池里混进了同区域的**所有**传送点（层岩巨渊 23 个），而不是神像。
而锚点名字写的是「七天神像-XXX」，用户点的是**神像图标**。
v3 对神像锚点强制要求候选主名含「神像」，候选数骤降。

★ 仍有多候选时
--------------
RANSAC：随机取 2 个锚点各随机挑一个候选，解出轴对齐模型，
再用全部锚点统计内点（残差 < tol）。跑多轮取最优，然后贪心重指派 + 重拟合。
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
import random
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

RECOG = Path(RECOG_ROOT)
ANCHORS = RECOG / "data" / "mihoyo_maps" / "teyvat_P0.png.anchors.json"
TP = RECOG / "data" / "_ref_tp_sample.json"

TYPO = {"七天神仙": "七天神像", "安绕": "安饶"}
STATUE_PREFIX = re.compile(r"七天神像|风神像|神像")
INLIER_TOL = 60.0          # 内点阈值（像素）
RANSAC_ITERS = 6000
SEED = 20261001


def norm(s):
    s = str(s or "").strip()
    for a, b in TYPO.items():
        s = s.replace(a, b)
    for ch in "，、－— ":
        s = s.replace(ch, "-" if ch in "，、－—" else "")
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-")


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
            nm = norm(p.get("name", ""))
            out.append({"name": nm, "raw_name": str(p.get("name", "")),
                        "areas": [norm(a) for a in (p.get("areas") or [])],
                        "wx": float(pos[0]), "wz": float(pos[2])})
    return out


def candidates_for(label, key, pts):
    want_statue = bool(STATUE_PREFIX.search(norm(label)))
    out = []
    for p in pts:
        if want_statue and "神像" not in p["name"]:
            continue
        if not key:
            continue
        hit = any(key == a or key in a for a in p["areas"])
        if not hit and key not in p["name"]:
            continue
        out.append(p)
    return out


def axis_val(hyp, w):
    """hyp: 0=px~wx 1=px~wz ; 返回 (u, v) 的原始世界分量。"""
    neg_x, neg_y = hyp[2], hyp[3]
    if hyp[0] == 0:
        u, v = w["wx"], w["wz"]
    else:
        u, v = w["wz"], w["wx"]
    return (-u if neg_x else u), (-v if neg_y else v)


def solve2(u1, p1, u2, p2):
    d = u1 - u2
    if abs(d) < 1e-9:
        return None
    b = (p1 - p2) / d
    return (p1 - b * u1, b)


def score(model, items):
    """model=(a,b,c,d)；返回内点数与残差列表。"""
    a, b, c, d = model
    nin, errs = 0, []
    for anchor, u, v, pxx, pyy, cands in items:
        pu = a + b * u
        pv = c + d * v
        best = min(((pu - pxx) ** 2 + (pv - pyy) ** 2) ** 0.5
                   for _ in cands) if False else None
        errs.append((anchor, u, v, pxx, pyy, pu, pv))
    return errs


def main():
    random.seed(SEED)
    np.random.seed(SEED)
    doc = json.loads(ANCHORS.read_text(encoding="utf-8"))
    W, H = doc["image_size"]
    anchors = doc["points"]
    pts = load_tp()

    print("=" * 80)
    print("A · 仿射拟合 v3（神像过滤 + RANSAC）")
    print("=" * 80)
    print(f"底图 {W} x {H}   锚点 {len(anchors)}   tp 点 {len(pts)}\n")

    # ---------- 候选池 ----------
    items, skipped = [], []
    print("-" * 80)
    print(f"  {'锚点名':<22} {'地名':<12} {'候选':>4}  候选主名")
    print("-" * 80)
    for a in anchors:
        key = area_key(a["name"])
        cands = candidates_for(a["name"], key, pts)
        if not cands:
            skipped.append(a["name"])
            print(f"  {a['name']:<22} {key:<12} {0:>4}  ✗ 无候选")
            continue
        names = sorted({c["name"] for c in cands})
        items.append({"anchor": a, "key": key, "cands": cands})
        print(f"  {a['name']:<22} {key:<12} {len(cands):>4}  "
              f"{'、'.join(names)[:44]}")
    print(f"\n  可用锚点 {len(items)}，跳过 {len(skipped)}：{skipped}")

    if len(items) < 6:
        print("  !! 可用锚点不足")
        return

    HYPS = [(0, 1, False, False), (0, 1, True, False), (0, 1, False, True),
            (0, 1, True, True), (1, 0, False, False), (1, 0, True, False),
            (1, 0, False, True), (1, 0, True, True)]

    best_global = None
    for hyp in HYPS:
        # 预计算每个锚点每个候选的 (u,v)
        pre = []
        for it in items:
            arr = []
            for c in it["cands"]:
                u, v = axis_val(hyp, c)
                arr.append((u, v, c))
            pre.append((it["anchor"]["x"], it["anchor"]["y"], arr))
        idxs = [i for i in range(len(pre)) if len(pre[i][2]) > 0]
        if len(idxs) < 4:
            continue
        for _ in range(RANSAC_ITERS // 8):
            i, j = random.sample(idxs, 2)
            ui, vi, _ = random.choice(pre[i][2])
            uj, vj, _ = random.choice(pre[j][2])
            rx = solve2(ui, pre[i][0], uj, pre[j][0])
            ry = solve2(vi, pre[i][1], vj, pre[j][1])
            if rx is None or ry is None:
                continue
            model = (rx[0], rx[1], ry[0], ry[1])
            nin = 0
            for k in idxs:
                pxx, pyy, arr = pre[k]
                pu = model[0] + model[1] * arr[0][0]
                pv = model[2] + model[3] * arr[0][1]
                if min(((pu - pxx) ** 2 + (pv - pyy) ** 2) ** 0.5
                       for _ in arr) < INLIER_TOL:
                    nin += 1
            if best_global is None or nin > best_global[0]:
                best_global = (nin, hyp, model, pre, idxs)

    nin, hyp, model, pre, idxs = best_global
    tag = ("px~wx" if hyp[0] == 0 else "px~wz") + \
          (",py~wx" if hyp[1] == 0 else ",py~wz") + \
          (f" (符号 {int(hyp[2])},{int(hyp[3])})")
    print(f"\n  RANSAC 最佳内点 {nin}/{len(idxs)}   轴向 {tag}")

    # ---------- 迭代重指派 + 重拟合 ----------
    assign = [0] * len(idxs)
    for _ in range(40):
        changed = 0
        for k in idxs:
            pxx, pyy, arr = pre[k]
            pu = model[0] + model[1] * arr[0][0]
            pv = model[2] + model[3] * arr[0][1]
            d = [((pu - pxx) ** 2 + (pv - pyy) ** 2) ** 0.5, t[0]]
            bi = min(range(len(arr)), key=lambda q: d[0] - arr[q][0] * 0)
            bs = min(range(len(arr)),
                     key=lambda q: (pu - pxx) ** 2 + (pv - pyy) ** 2
                     - 0)  # 占位
            # 正确做法：逐候选各自算残差
            best_i, best_e = assign[k], 1e18
            for qi, (u, v, _c) in enumerate(arr):
                e = (model[0] + model[1] * u - pxx) ** 2 + \
                    (model[2] + model[3] * v - pyy) ** 2
                if e < best_e:
                    best_e, best_i = e, qi
            if best_i != assign[k]:
                assign[k] = best_i
                changed += 1
        us = np.array([pre[k][2][assign[k]][0] for k in idxs])
        vs = np.array([pre[k][2][assign[k]][1] for k in idxs])
        PXX = np.array([pre[k][0] for k in idxs], float)
        PYY = np.array([pre[k][1] for k in idxs], float)
        A = np.column_stack([np.ones_like(us), us])
        B = np.column_stack([np.ones_like(vs), vs])
        cx, *_ = np.linalg.lstsq(A, PXX, rcond=None)
        cy, *_ = np.linalg.lstsq(B, PYY, rcond=None)
        model = (cx[0], cx[1], cy[0], cy[1])
        if changed == 0:
            break

    a, b, c, d = model
    ex = PXX - (a + b * us)
    ey = PYY - (c + d * vs)
    rms = float(np.sqrt((ex ** 2 + ey ** 2).mean()))

    print("\n" + "=" * 80)
    print("最终模型")
    print("=" * 80)
    print(f"  轴向: {tag}")
    print(f"  px = {a:.4f} + {b:.6f} * u")
    print(f"  py = {c:.4f} + {d:.6f} * v")
    print(f"  RMS = {rms:.2f} px   阈值 50 -> "
          f"{'✅ 通过' if rms < 50 else '❌ 不通过'}")
    print(f"  尺度: 1 世界单位 = {abs(b):.4f} px (u) / {abs(d):.4f} px (v)")
    print(f"  BetterGI Scale/zoom ≈ {abs(b):.3f}")

    print(f"\n  {'锚点':<22} {'地区':<10} {'px实':>6} {'px预':>9} {'dx':>7}"
          f" {'py实':>6} {'py预':>9} {'dy':>7} {'r':>7} 命中")
    res = []
    for idx, k in enumerate(idxs):
        pxx, pyy, arr = pre[k]
        u, v, c = arr[assign[k]]
        pu, pv = a + b * u, c + d * v
        rr = ((pu - pxx) ** 2 + (pv - pyy) ** 2) ** 0.5
        res.append((rr, items[k]["anchor"]["name"]))
        print(f"  {items[k]['anchor']['name']:<22} {items[k]['key']:<10} "
              f"{pxx:>6} {pu:>9.1f} {pu-pxx:>7.1f} {pyy:>6} {pv:>9.1f} "
              f"{pv-pyy:>7.1f} {rr:>7.1f}  {c['name']}")
    res.sort(reverse=True)
    print(f"\n  残差最大 3 个:")
    for rr, nm in res[:3]:
        print(f"    {nm:<22} {rr:.1f} px")
    print(f"  中位残差 {sorted(x for x,_ in res)[len(res)//2]:.1f} px")
    print(f"  超过 {INLIER_TOL}px 的外点: "
          f"{sum(1 for x,_ in res if x>INLIER_TOL)} / {len(res)}")
    print("\n=== DONE ===")


if __name__ == "__main__":
    main()
