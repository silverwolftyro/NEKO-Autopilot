"""决定性诊断：定位失败到底是"被无效区骗了"还是"风格差异"。

不调参、不改 minimimap_locator，只量。
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

import csv
import importlib.util
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

PERC = (Path(PLUGIN_DIR.parent)
        / "genshin_game_buddy" / "autopilot" / "perception")
RECOG = Path(RECOG_ROOT)
DA = RECOG / "raw" / "frames" / "session_20261001_a"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" / "diag"
FIG.mkdir(parents=True, exist_ok=True)


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


ml = _load("minimap_locator", PERC / "minimap_locator.py")
mm = _load("minimap_mask", PERC / "minimap_mask.py")
pl = _load("pyramid_loader", PERC / "pyramid_loader.py")
cl = _load("chunk_loader", PERC / "chunk_loader.py")

PY = pl.PyramidLoader(preload=True)
CH = cl.ChunkLoader(cache_size=24)

FID = "f00200"
im = cv2.imread(str(DA / f"{FID}.jpg"))
mini, mask = mm.make_minimap_roi(im)
mini_g = cv2.cvtColor(mini, cv2.COLOR_BGR2GRAY)
SC = 1.0
TRUTH = {"枫丹廷": (18827, 7648), "星落湖": (24782, 8682)}


def ncc_at(search, templ, m, x, y):
    th, tw = templ.shape[:2]
    if y + th > search.shape[0] or x + tw > search.shape[1]:
        return -1.0
    nz = m > 0
    tv = templ[nz].astype(np.float64)
    mt, st = tv.mean(), tv.std()
    if st < 1e-9:
        return 0.0
    win = search[y:y + th, x:x + tw][nz].astype(np.float64)
    mw, sw = win.mean(), win.std()
    if sw < 1e-9:
        return 0.0
    return float(((tv - mt) * (win - mw)).mean() / (st * sw))


def tpl_for(lv):
    k = 1.0 / (2 ** lv)
    d = int(round(mini_g.shape[0] * SC * k))
    t = cv2.resize(mini_g, (d, d),
                   interpolation=cv2.INTER_AREA if d < mini_g.shape[0]
                   else cv2.INTER_CUBIC)
    m = cv2.resize(mask, (d, d), interpolation=cv2.INTER_NEAREST)
    return t, (m > 127).astype(np.uint8) * 255, d


def sqdiff_map(search, t, m):
    return cv2.matchTemplate(search, t, cv2.TM_SQDIFF, mask=m)


# ================= 实验 1
print("=" * 88)
print("实验 1 · 真值周围的分数分布（400x400 窗口）")
print("=" * 88)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

t4, m4, d4 = tpl_for(4)
L4 = PY.get_level(4)
k4 = 1.0 / 16.0
exp1 = []
fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
for ax, (tname, (tx, ty)) in zip(axes, TRUTH.items()):
    cx, cy = int(tx * k4), int(ty * k4)
    R = 400
    x0, y0 = max(0, cx - R // 2), max(0, cy - R // 2)
    win = L4[y0:y0 + R, x0:x0 + R]
    resp = sqdiff_map(win, t4, m4)
    order = np.argsort(resp.ravel())
    flat = resp.ravel()
    # 真值位置在 resp 里的坐标
    ty_r, tx_r = cy - y0, cx - x0
    best_pos = (order[0] % resp.shape[1], order[0] // resp.shape[1])
    rank = None
    for i, idx in enumerate(order):
        yy, xx = idx // resp.shape[1], idx % resp.shape[1]
        if abs(xx - tx_r) <= 2 and abs(yy - ty_r) <= 2:
            rank = i + 1
            break
    s_true = ncc_at(win, t4, m4, max(0, tx_r - t4.shape[1] // 2),
                    max(0, ty_r - t4.shape[0] // 2))
    top = [(int(i % resp.shape[1]), int(i // resp.shape[1])) for i in order[:5]]
    s_top = [round(ncc_at(win, t4, m4, x, y), 4) for x, y in top]
    exp1.append({"truth": tname, "rank": rank, "ncc_true": s_true,
                 "ncc_top": s_top, "best_at": best_pos,
                 "truth_at_resp": (tx_r, ty_r)})
    print(f"\n  【{tname}】 窗口 ({x0},{y0}) {R}x{R}  模板 {d4}px")
    print(f"    真值在窗口内 ({tx_r},{ty_r})  局部 NCC = {s_true:+.4f}")
    print(f"    真值排名 = {rank} / {flat.size}")
    print(f"    窗口 top-5 位置 {top}")
    print(f"    对应 NCC     {s_top}")
    ax.imshow(resp, cmap="magma")
    ax.plot(tx_r, ty_r, "c+", ms=16, mew=3)
    ax.plot(best_pos[0], best_pos[1], "r*", ms=16)
    ax.set_title(f"{tname}  SQDIFF resp  (+ = truth, * = best)")
    ax.set_xlabel("x")
    ax.set_ylabel("y")
fig.tight_layout()
p1 = FIG / "exp1_truth_neighbourhood.png"
fig.savefig(p1, dpi=100)
plt.close(fig)
print(f"\n  -> {p1.name}")

# ================= 实验 2
print("\n" + "=" * 88)
print("实验 2 · 全局 top-20（L4）")
print("=" * 88)
resp = sqdiff_map(L4, t4, m4)
flat = resp.ravel()
order = np.argsort(flat)[:20]
tops = []
for i in order:
    y, x = int(i // resp.shape[1]), int(i % resp.shape[1])
    sc = ncc_at(L4, t4, m4, x, y)
    tops.append((x, y, sc))
truth_scores = {}
for tname, (tx, ty) in TRUTH.items():
    cx, cy = int(tx * k4), int(ty * k4)
    sc = ncc_at(L4, t4, m4, max(0, cx - t4.shape[1] // 2),
                max(0, cy - t4.shape[0] // 2))
    truth_scores[tname] = sc
    rank = None
    for n, (x, y, _) in enumerate(tops, 1):
        if abs(x - cx) <= 8 and abs(y - cy) <= 8:
            rank = n
            break
    print(f"  真值 {tname}: NCC {sc:+.4f}  排名 {rank}/20")
print(f"  top-20 分数: {[round(t[2],3) for t in tops]}")
print(f"  top-20 最高 {max(t[2] for t in tops):+.4f}  "
      f"最低 {min(t[2] for t in tops):+.4f}")

CWp = 200
sheet = np.full((CWp * 5 + 40, CWp * 4 + 12, 3), 24, np.uint8)
for n, (x, y, sc) in enumerate(tops):
    px, py = (x + t4.shape[1] / 2) * 16, (y + t4.shape[0] / 2) * 16
    crop = CH.load_l0_window((px, py), 256)
    crop = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
    crop = cv2.resize(crop, (CWp, CWp), interpolation=cv2.INTER_AREA)
    r, c = divmod(n, 4)
    xo, yo = 6 + c * CWp, 40 + r * CWp
    sheet[yo:yo + CWp, xo:xo + CWp] = crop
    cv2.putText(sheet, f"#{n+1} {sc:+.3f}", (xo + 4, yo + 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
    cv2.putText(sheet, f"({int(px)},{int(py)})", (xo + 4, yo + CWp - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 230, 255), 1)
blank = int((L4 == 0).mean())
cv2.putText(sheet, f"{FID}  L4 global top-20   black={blank:.1%}   "
                   f"truth: " + "  ".join(f"{k}={v:+.3f}"
                                           for k, v in truth_scores.items()),
            (6, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
p2 = FIG / "exp2_global_top20.png"
cv2.imwrite(str(p2), sheet)
print(f"  -> {p2.name}   L4 纯黑像素占比 {blank:.1%}")

# ================= 实验 3
print("\n" + "=" * 88)
print("实验 3 · mask 掉底图无效区")
print("=" * 88)
valid = ((L4 > 10) & (L4 < 250)).astype(np.uint8) * 255
print(f"  有效区占比 {valid.mean()/255:.1%}   L4 纯黑 {(L4==0).mean():.1%}")
resp_v = sqdiff_map(L4, t4, m4)
# 响应点 (y,x) 对应窗口中心在 (y+th/2, x+tw/2)，按中心取有效区
_th, _tw = t4.shape[:2]
valid_r = valid[_th // 2:_th // 2 + resp_v.shape[0],
                 _tw // 2:_tw // 2 + resp_v.shape[1]]
assert valid_r.shape == resp_v.shape, (valid_r.shape, resp_v.shape)
resp_v[valid_r == 0] = resp_v.max() + 1e9      # 无效区排到最低
order_v = np.argsort(resp_v.ravel())[:20]
tops_v = []
for i in order_v:
    y, x = int(i // resp_v.shape[1]), int(i % resp_v.shape[1])
    tops_v.append((x, y, ncc_at(L4, t4, m4, x, y)))
tv = {}
for tname, (tx, ty) in TRUTH.items():
    cx, cy = int(tx * k4), int(ty * k4)
    tv[tname] = ncc_at(L4, t4, m4, max(0, cx - t4.shape[1] // 2),
                       max(0, cy - t4.shape[0] // 2))
tb = max(t[2] for t in tops)
print(f"  {'':<8}{'真值':<10}{'全局最高':<10}{'差距':<8}{'真值排名'}")
print(f"  {'无 mask':<8}{np.mean(list(truth_scores.values())):<10.4f}"
      f"{tb:<10.4f}{tb-np.mean(list(truth_scores.values())):<8.4f}-")
print(f"  {'有 mask':<8}{np.mean(list(tv.values())):<10.4f}"
      f"{max(t[2] for t in tops_v):<10.4f}"
      f"{max(t[2] for t in tops_v)-np.mean(list(tv.values())):<8.4f}-")
for tname in TRUTH:
    print(f"    {tname}: 无mask {truth_scores[tname]:+.4f} -> "
          f"有mask {tv[tname]:+.4f}")

# ================= 实验 4
print("\n" + "=" * 88)
print("实验 4 · 粗搜层对比")
print("=" * 88)
print(f"  {'层':<4}{'模板':>6}{'真值':>9}{'全局最高':>10}{'差距':>8}"
      f"{'真值排名':>10}{'耗时':>9}")
print("  " + "-" * 60)
rows4 = []
for lv in (4, 3, 2, 1):
    t, m, d = tpl_for(lv)
    img = PY.get_level(lv)
    k = 1.0 / (2 ** lv)
    t0 = time.perf_counter()
    r = sqdiff_map(img, t, m)
    dt = (time.perf_counter() - t0) * 1000
    o = np.argsort(r.ravel())
    bx, by = int(o[0] % r.shape[1]), int(o[0] // r.shape[1])
    s_best = ncc_at(img, t, m, bx, by)
    ss, rr_ = [], []
    for name, (tx, ty) in TRUTH.items():
        cx, cy = int(tx * k), int(ty * k)
        ss.append(ncc_at(img, t, m,
                         max(0, cx - t.shape[0] // 2),
                         max(0, cy - t.shape[0] // 2)))
        for n, i in enumerate(o[:20], 1):
            yy, xx = int(i // r.shape[1]), int(i % r.shape[1])
            if abs(xx - cx) <= 8 and abs(yy - cy) <= 8:
                rr_.append(n)
                break
        else:
            rr_.append(None)
    st = float(np.mean(ss))
    rk = min([v for v in rr_ if v] or [999])
    print(f"  L{lv:<3}{d:>6}{st:>9.4f}{s_best:>10.4f}{s_best-st:>8.4f}"
          f"{rk:>10}{dt:>8.0f}ms")
    rows4.append({"level": lv, "tpl": d, "truth": st, "best": s_best,
                  "rank": rk, "ms": round(dt)})
with (FIG / "exp4_levels.csv").open("w", encoding="utf-8-sig",
                                   newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows4[0]))
    w.writeheader()
    w.writerows(rows4)
print(f"\n  -> exp4_levels.csv")
print("\n=== DONE ===")
