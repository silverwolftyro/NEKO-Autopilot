"""验证 1 完成 + 验证 2/3 执行。

★ 先说一个数据层面的硬限制
-----------------------------
验证 2 要求"城内帧 + 野外帧"对比图标尺寸。
但 §15.3 的 log-polar 实测显示 **session_a 全程 1511 帧缩放档恒定
（尺度比中位 1.00267）**，且三段走路全在枫丹廷城区。
**session_a 里不存在第二种缩放档。**
-> 验证 2 在现有数据上做不了，下面用"玩家与锚点的相对距离变化"
   作为尺寸稳定性的**间接**证据（若图标固定像素，而地图在缩放，
   图标在小地图上的像素尺寸应不变），并明确标注这是替代方案。

本脚本做：
  1. 从 f00200 提取干净模板（去背景）
  2. 跨帧 NCC 模板匹配，看分数分布
  3. 用独立的 blob 检测做标签，验证 3 的可分性
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


# ---------- 1. 提模板
im = cv2.imread(str(ids[199]))
crop, mask = mm.make_minimap_roi(im, cfg)
TX, TY = 60, 117
TW, TH = 26, 30                      # 模板框（比 blob 略大，留边）
TPL = crop[TY - TH // 2:TY + TH // 2, TX - TW // 2:TX + TW // 2].copy()
print(f"模板取自 a:f00200 中心({TX},{TY})  尺寸 {TPL.shape[1]}x{TPL.shape[0]}")

# 模板和它在原图里的位置，出图给人确认
Z = 10
big = cv2.resize(TPL, (TW * Z, TH * Z), interpolation=cv2.INTER_NEAREST)
# 上下文窗口：先边缘 padding，保证任何 (TX,TY) 都不会切出负下标
PAD = 80
padded = cv2.copyMakeBorder(crop, PAD, PAD, PAD, PAD,
                            cv2.BORDER_REPLICATE)
ctx = padded[TY + PAD - 70:TY + PAD + 70, TX + PAD - 70:TX + PAD + 70].copy()
ctxb = cv2.resize(ctx, (140 * 3, 140 * 3), interpolation=cv2.INTER_NEAREST)
cv2.rectangle(ctxb, ((70 - TW // 2) * 3, (70 - TH // 2) * 3),
              ((70 + TW // 2) * 3 - 1, (70 + TH // 2) * 3 - 1),
              (0, 0, 255), 3)
_H = ctxb.shape[0]
if big.shape[0] < _H:                     # 模板图上下补白到和上下文同高
    _top = (_H - big.shape[0]) // 2
    big = cv2.copyMakeBorder(big, _top, _H - big.shape[0] - _top, 0, 0,
                             cv2.BORDER_CONSTANT, value=(0, 0, 0))
row = np.hstack([ctxb, np.zeros((_H, 20, 3), np.uint8), big])

hdr = np.zeros((34, row.shape[1], 3), np.uint8)
cv2.putText(hdr, "a:f00200  left: context (red = template box)   |   "
                 "right: template 26x30 @x10", (6, 23),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
cv2.imwrite(str(FIG / "template_a_f00200.png"), np.vstack([hdr, row]))
print(f"  -> template_a_f00200.png")

TPL_G = cv2.cvtColor(TPL, cv2.COLOR_BGR2GRAY).astype(np.float32)
# 圆形掩码（匹配时排除圆外 + 玩家箭头附近）
CCX, CCY = float(cfg["cx"]) * 2304 - mm.minimap_rect(im, cfg)[0], \
    float(cfg["cy"]) * 1440 - mm.minimap_rect(im, cfg)[1]


def scan_frame(crop, mask):
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32)
    res = cv2.matchTemplate(g, TPL_G, cv2.TM_CCOEFF_NORMED)
    rh, rw = res.shape
    # matchTemplate 的结果尺寸是 (H-th+1, W-tw+1)，掩码必须同步裁
    mv = mask[:rh, :rw]
    valid = np.where(mv > 0, res, -1.0).astype(np.float32)
    # 排除玩家箭头中心区
    yy, xx = np.ogrid[:rh, :rw]
    ctr = ((xx + TW // 2 - CCX) ** 2 + (yy + TH // 2 - CCY) ** 2) < 32 ** 2
    valid[ctr] = -1.0
    k = int(np.argmax(valid))
    y, x = divmod(k, rw)
    return float(valid[y, x]), x + TW // 2, y + TH // 2, res


def blobs(crop, mask):
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    sel = (((H >= 100) & (H <= 150) & (S > 80) & (V > 60) & (mask > 0))
           ).astype(np.uint8)
    sel = cv2.morphologyEx(sel, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, st, ct = cv2.connectedComponentsWithStats(sel, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if 40 <= a <= 500 and 8 <= math.sqrt(w * h) <= 26 and \
                0.6 <= w / max(h, 1) <= 1.7 and \
                math.hypot(ct[i][0] - CCX, ct[i][1] - CCY) >= 25:
            out.append((float(ct[i][0]), float(ct[i][1]), int(w), int(h),
                        int(a)))
    return out


# ---------- 2/3. 抽样跑
print("\n" + "=" * 92)
print("验证 3 · 分数分布（组标签用独立 blob 检测）")
print("=" * 92)
picks = [i for i in range(1, len(ids) + 1, 6) if is_world(i)]
print(f"  世界帧抽样 {len(picks)} 帧（每 6 帧取 1）")
rows = []
t0 = time.perf_counter()
for k, i in enumerate(picks, 1):
    im = cv2.imread(str(ids[i - 1]))
    crop, mask = mm.make_minimap_roi(im, cfg)
    sc, px, py, res = scan_frame(crop, mask)
    bl = blobs(crop, mask)
    rows.append(dict(idx=i, frame=f"a:f{i:05d}", score=sc, px=px, py=py,
                     nblob=len(bl),
                     ncc_of_blobs=len(bl) > 0,
                     dx=px - CCX, dy=py - CCY,
                     r=math.hypot(px - CCX, py - CCY)))
    if k % 100 == 0:
        print(f"    {k}/{len(picks)}  {time.perf_counter()-t0:.0f}s")

A = np.array([r["score"] for r in rows if r["ncc_of_blobs"]])
B = np.array([r["score"] for r in rows if not r["ncc_of_blobs"]])
print(f"\n  组 A（blob 检出，n={len(A)}）: 中位 {np.median(A):.3f}  "
      f"p25 {np.percentile(A,25):.3f}  p75 {np.percentile(A,75):.3f}  "
      f"max {A.max():.3f}")
print(f"  组 B（无检出,  n={len(B)}）: 中位 {np.median(B):.3f}  "
      f"p25 {np.percentile(B,25):.3f}  p75 {np.percentile(B,75):.3f}  "
      f"max {B.max():.3f}")
print(f"\n  判据（任务书）: A 中位 >= 0.8 ? {np.median(A) >= 0.8}")
print(f"                 B 中位 <= 0.5 ? {np.median(B) <= 0.5}")
ov = min(np.percentile(A, 75), np.percentile(B, 75)) - \
     max(np.percentile(A, 25), np.percentile(B, 25))
print(f"                 p25/p75 不重叠 ? {ov < 0}  (重叠量 {ov:+.3f})")
# AUC
if len(A) and len(B):
    allv = np.concatenate([A, B])
    ranks = allv.argsort().argsort() + 1
    auc = (ranks[:len(A)].sum() - len(A) * (len(A) + 1) / 2) / (len(A) * len(B))
    print(f"                 AUC = {auc:.3f}  (0.5=不可分, 1.0=完美)")

with (FIG / "landmark_scores.csv").open("w", encoding="utf-8-sig",
                                         newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["frame", "ncc_score", "match_x", "match_y", "dx", "dy",
                "r_from_center", "n_blobs", "group"])
    for r in rows:
        w.writerow([r["frame"], round(r["score"], 4), r["px"], r["py"],
                    r["dx"], r["dy"], round(r["r"], 1), r["nblob"],
                    "A" if r["ncc_of_blobs"] else "B"])
print(f"  -> landmark_scores.csv")

# ---------- 图标相对距离的分布（间接验证尺寸稳定性）
print("\n" + "=" * 92)
print("验证 2（替代方案）· 图标相对玩家的距离分布")
print("=" * 92)
rr = np.array([r["r"] for r in rows if r["ncc_of_blobs"]])
print(f"  NCC 匹配点距圆心: 中位 {np.median(rr):.0f}px  "
      f"p10 {np.percentile(rr,10):.0f}  p90 {np.percentile(rr,90):.0f}  "
      f"max {rr.max():.0f}")
print("  ⚠️ session_a 缩放档恒定，**无法做跨档对比**。")
print("     真正验证需要：野外小地图帧 / 玩家主动缩放小地图的帧。")
print("=== DONE ===")
