"""验证 3 修正 · 标签器漏检导致的假阴性。

★ 复盘结论：方法没错，标签错了
----------------------------------
组 B 目视复核（verify_group_B.png）：
  a:f00883  ncc=0.932  红框**正好框住蓝色菱形锚点** -> 应是组 A
  a:f00649  ncc=0.934  红框**正好框住锚点**           -> 应是组 A
  a:f01261  ncc=0.330  图上有锚点，NCC 框到了空白       -> blob 漏检
  a:f01111  ncc=0.291  小地图被橙黄渐变覆盖，真无图标   -> 真组 B

根因：锚点图标是**半透明**的，叠在地图上后 S 通道被背景稀释，
旧阈值 S>80 漏检严重。AUC=0.668 反映的是**标签噪声**，不是方法缺陷。

修正：放宽 HSV 阈值 + 加形态学核 + 面积/形状门槛重做标签，
并和"严格阈值"标签做交叉表，看修正效果。
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
import math
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
SEG = RECOG / "exports" / "benchmark" / "datasets" / "session_a_segments.csv"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" \
    / "landmark"
FIG.mkdir(parents=True, exist_ok=True)


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


mm = _load("minimap_mask", PERC / "minimap_mask.py")
cfg = mm.load_circle_cfg()
ids = sorted(DA.glob("*.jpg"))
segs = []
with SEG.open(encoding="utf-8-sig", newline="") as fh:
    for r in csv.DictReader(fh):
        segs.append((int(r["frame_first"][1:]), int(r["frame_last"][1:]),
                     r["raw_prediction"] == "world"))


def is_world(i):
    for a, b, w in segs:
        if a <= i <= b:
            return w
    return False


im0 = cv2.imread(str(ids[199]))
_x, _y, _, _ = mm.minimap_rect(im0, cfg)
CCX = float(cfg["cx"]) * 2304 - _x
CCY = float(cfg["cy"]) * 1440 - _y
TX, TY, TW, TH = 60, 117, 26, 30
crop0, _m = mm.make_minimap_roi(im0, cfg)
TPL_G = cv2.cvtColor(crop0[TY - TH // 2:TY + TH // 2,
                          TX - TW // 2:TX + TW // 2],
                     cv2.COLOR_BGR2GRAY).astype(np.float32)


def ncc_scan(crop, mask):
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32)
    res = cv2.matchTemplate(g, TPL_G, cv2.TM_CCOEFF_NORMED)
    rh, rw = res.shape
    valid = np.where(mask[:rh, :rw] > 0, res, -1.0).astype(np.float32)
    yy, xx = np.ogrid[:rh, :rw]
    valid[(xx + TW // 2 - CCX) ** 2 + (yy + TH // 2 - CCY) ** 2 < 32 ** 2] = -1
    k = int(np.argmax(valid))
    y0, x0 = divmod(k, rw)
    return float(valid[y0, x0]), x0 + TW // 2, y0 + TH // 2


def blobs_relaxed(crop, mask):
    """放宽的蓝紫菱形检测：S>45 V>35，闭运算核放大，允许更小连通域。"""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    Hh, Ss, Vv = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    sel = (((Hh >= 95) & (Hh <= 155) & (Ss > 45) & (Vv > 35)
            & (mask > 0)).astype(np.uint8))
    sel = cv2.morphologyEx(sel, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    sel = cv2.morphologyEx(sel, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, lab, st, ct = cv2.connectedComponentsWithStats(sel, 8)
    out = []
    for j in range(1, n):
        x, y, w, h, a = st[j]
        if not (30 <= a <= 600):
            continue
        d = math.sqrt(w * h)
        if not (6 <= d <= 28):
            continue
        if not (0.55 <= w / max(h, 1) <= 1.8):
            continue
        if math.hypot(ct[j][0] - CCX, ct[j][1] - CCY) < 25:
            continue
        out.append((float(ct[j][0]), float(ct[j][1]), int(w), int(h), int(a)))
    return out


picks = [i for i in range(1, len(ids) + 1, 6) if is_world(i)]
print(f"世界帧抽样 {len(picks)}")
rows = []
t0 = time.perf_counter()
for k, i in enumerate(picks, 1):
    im = cv2.imread(str(ids[i - 1]))
    crop, mask = mm.make_minimap_roi(im, cfg)
    sc, px, py = ncc_scan(crop, mask)
    bl = blobs_relaxed(crop, mask)
    rows.append(dict(idx=i, frame=f"a:f{i:05d}", score=sc, px=px, py=py,
                     nblob=len(bl), has=len(bl) > 0,
                     r=math.hypot(px - CCX, py - CCY)))
    if k % 50 == 0:
        print(f"    {k}/{len(picks)}  {time.perf_counter()-t0:.0f}s")

A = np.array([r["score"] for r in rows if r["has"]])
B = np.array([r["score"] for r in rows if not r["has"]])
print(f"\n  修正后标签（放宽 blob）：")
print(f"    组 A（有, n={len(A)}）: 中位 {np.median(A):.3f}  "
      f"p25 {np.percentile(A,25):.3f}  p75 {np.percentile(A,75):.3f}  "
      f"min {A.min():.3f}")
print(f"    组 B（无, n={len(B)}）: 中位 {np.median(B):.3f}  "
      f"p25 {np.percentile(B,25):.3f}  p75 {np.percentile(B,75):.3f}  "
      f"max {B.max():.3f}")
print(f"\n  判据：A 中位 >= 0.8 ? {np.median(A) >= 0.8 if len(A) else 'NA'}")
print(f"        B 中位 <= 0.5 ? {np.median(B) <= 0.5 if len(B) else 'NA'}")
if len(A) and len(B):
    ov = min(np.percentile(A, 25), np.percentile(B, 75)) - \
         max(np.percentile(A, 75), np.percentile(B, 25))
    allv = np.concatenate([A, B])
    ranks = allv.argsort().argsort() + 1
    auc = (ranks[:len(A)].sum() - len(A) * (len(A) + 1) / 2) / (len(A) * len(B))
    print(f"        p25/p75 不重叠 ? {ov < 0}  (重叠量 {ov:+.3f})")
    print(f"        AUC = {auc:.3f}")
    thr = 0.6
    tp = int((A >= thr).sum())
    fp = int((B >= thr).sum())
    print(f"\n    阈值 {thr}: 组A 命中 {tp}/{len(A)} = {tp/len(A)*100:.1f}%   "
          f"组B 误报 {fp}/{len(B)} = {fp/len(B)*100:.1f}%")

# 与旧标签的交叉表（量化标签修正的效果）
old = {r["frame"]: r["group"] for r in csv.DictReader(
    (FIG / "landmark_scores.csv").open(encoding="utf-8-sig", newline=""))}
flip_ab = sum(1 for r in rows if old.get(r["frame"]) == "B" and r["has"])
flip_ba = sum(1 for r in rows if old.get(r["frame"]) == "A" and not r["has"])
print(f"\n  标签修正量: 旧B->新A {flip_ab} 帧, 旧A->新B {flip_ba} 帧")

with (FIG / "landmark_scores_v2.csv").open("w", encoding="utf-8-sig",
                                           newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["frame", "ncc_score", "match_x", "match_y",
                "r_from_center", "n_blobs_relaxed", "group_v2", "group_v1"])
    for r in rows:
        w.writerow([r["frame"], round(r["score"], 4), r["px"], r["py"],
                    round(r["r"], 1), r["nblob"],
                    "A" if r["has"] else "B", old.get(r["frame"], "")])
print(f"  -> landmark_scores_v2.csv")

# 分布图
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt      # noqa: E402
fig, ax = plt.subplots(1, 2, figsize=(15, 5.5))
bins = np.linspace(-0.1, 1.05, 34)
ax[0].hist(A, bins=bins, alpha=0.65, label=f"group A: anchor icon (n={len(A)})",
           color="#4c72b0", density=True)
ax[0].hist(B, bins=bins, alpha=0.65, label=f"group B: no icon (n={len(B)})",
           color="#c44e52", density=True)
ax[0].axvline(0.6, color="k", ls="--", lw=1, label="threshold 0.6")
ax[0].set_xlabel("NCC template score")
ax[0].set_ylabel("density")
ax[0].set_title("Landmark anchor-icon detection: score distribution\n"
                "(label = relaxed blue-violet blob detector)", fontsize=11)
ax[0].legend(fontsize=8)
ax[0].grid(alpha=0.3)
srt = np.sort(np.concatenate([A, B]))
ax[1].plot(srt, np.arange(1, len(srt) + 1) / len(srt), lw=2,
           label=f"ROC  AUC={auc:.3f}" if len(A) and len(B) else "ROC")
ax[1].plot([0, 1], [0, 1], "k--", lw=0.9, label="chance 0.5")
ax[1].axvline(0.6, color="grey", ls=":", lw=1)
ax[1].set_xlabel("threshold")
ax[1].set_ylabel("TPR")
ax[1].set_title("Separability of 'has icon' vs 'no icon'", fontsize=11)
ax[1].legend(fontsize=8)
ax[1].grid(alpha=0.3)
ax[1].set_xlim(-0.1, 1.05)
ax[1].set_ylim(0, 1.02)
fig.tight_layout()
p = FIG / "landmark_score_distribution.png"
fig.savefig(p, dpi=110)
print(f"  -> {p.name}")
print("=== DONE ===")
