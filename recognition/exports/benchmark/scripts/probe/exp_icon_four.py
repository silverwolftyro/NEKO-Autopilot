"""任务 A/B · 四个候选对象摆在一起，供用户指认。

★ 自我修正（重要）
--------------------
`exp_icon_types.py` 的"近白描边 + 高频边缘"判据抓到的是**建筑图标**
（白描边 + 黑屋顶），**不是传送锚点**。传送锚点的蓝紫菱形**没有白描边**，
被这个判据排除了。

也就是说：**上一轮提取的蓝紫菱形，反而比这一轮检测器抓到的更可能是锚点。**

★ 本脚本只做一件事
--------------------
把 session_a 小地图上真实存在的 4 类对象裁出来并排，
附上各自的定量特征，让**用户指认哪个是传送锚点**。
不做类型假设，不下结论。

同时给出菱形的两项辅助证据：
  - 出现频次 / 相对位置分布（锚点应均匀分布，任务标记应集中某方向）
  - 像素尺寸稳定性（锚点固定像素，任务标记可能带动画/呼吸效果）
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
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

PERC = (Path(_REPO_ROOT.parent / 'N.E.K-O-main' / 'N.E.K.O-main' / 'plugin' / 'plugins')
        if False else
        Path(PLUGIN_DIR.parent)
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
_mx, _my, _, _ = mm.minimap_rect(im0, cfg)
CCX = float(cfg["cx"]) * 2304 - _mx
CCY = float(cfg["cy"]) * 1440 - _my

# ---- 四类对象的检测器（各自独立判据，互不干扰）
def d_diamond(crop, mask):
    """蓝紫菱形（上一轮模板候选）。半透明 -> 阈值放宽。"""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    sel = (((H >= 95) & (H <= 155) & (S > 45) & (V > 35) & (mask > 0))
           ).astype(np.uint8)
    sel = cv2.morphologyEx(sel, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    sel = cv2.morphologyEx(sel, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, lab, st, ct = cv2.connectedComponentsWithStats(sel, 8)
    out = []
    for j in range(1, n):
        x, y, w, h, a = st[j]
        if 30 <= a <= 600 and 6 <= math.sqrt(w * h) <= 28 and \
                0.55 <= w / max(h, 1) <= 1.8 and \
                math.hypot(ct[j][0] - CCX, ct[j][1] - CCY) >= 26:
            out.append((float(ct[j][0]), float(ct[j][1]), int(w), int(h), int(a)))
    return out


def d_building(crop, mask):
    """白描边 + 暗填充（建筑图标）。"""
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    S = hsv[:, :, 1]
    white = ((g > 200) & (S < 70)).astype(np.uint8)
    dark = (g < 70).astype(np.uint8)
    both = cv2.bitwise_and(white, dark)
    both = cv2.morphologyEx(both, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    both = np.where(mask > 0, both, 0)
    n, lab, st, ct = cv2.connectedComponentsWithStats(both, 8)
    out = []
    for j in range(1, n):
        x, y, w, h, a = st[j]
        if 40 <= a <= 400 and 0.8 <= w / max(h, 1) <= 3.5 and h >= 4:
            if math.hypot(ct[j][0] - CCX, ct[j][1] - CCY) < 26:
                continue
            out.append((float(ct[j][0]), float(ct[j][1]), int(w), int(h), int(a)))
    return out


def d_darkblob(crop, mask):
    """纯黑 + 白描边（T 形任务标记）。"""
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    dark = ((g < 60) & (mask > 0)).astype(np.uint8)
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, st, ct = cv2.connectedComponentsWithStats(dark, 8)
    out = []
    for j in range(1, n):
        x, y, w, h, a = st[j]
        if 40 <= a <= 500 and 0.8 <= w / max(h, 1) <= 3.0 and \
                math.hypot(ct[j][0] - CCX, ct[j][1] - CCY) >= 26:
            out.append((float(ct[j][0]), float(ct[j][1]), int(w), int(h), int(a)))
    return out


def d_cyan(crop, mask):
    """浅青圆形标记。"""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    sel = (((H >= 85) & (H <= 100) & (S > 60) & (V > 170)
            & (mask > 0)).astype(np.uint8))
    sel = cv2.morphologyEx(sel, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, st, ct = cv2.connectedComponentsWithStats(sel, 8)
    out = []
    for j in range(1, n):
        x, y, w, h, a = st[j]
        if 60 <= a <= 900 and 0.75 <= w / max(h, 1) <= 1.35 and \
                10 <= math.sqrt(w * h) <= 32:
            if math.hypot(ct[j][0] - CCX, ct[j][1] - CCY) < 26:
                continue
            out.append((float(ct[j][0]), float(ct[j][1]), int(w), int(h), int(a)))
    return out


DET = [("A blue-violet diamond", d_diamond),
       ("B white-outlined building", d_building),
       ("C black glyph (T)", d_darkblob),
       ("D cyan disc", d_cyan)]

picks = [i for i in range(1, len(ids) + 1, 10) if is_world(i)]
print(f"世界帧抽样 {len(picks)}")
stats = {k: {"hits": 0, "frames": set(), "wh": [], "wh2": [], "rel": []}
         for k, _ in DET}
samples = {k: [] for k, _ in DET}
for i in picks:
    im = cv2.imread(str(ids[i - 1]))
    crop, mask = mm.make_minimap_roi(im, cfg)
    for name, fn in DET:
        for (cx, cy, w, h, a) in fn(crop, mask):
            s = stats[name]
            s["hits"] += 1
            s["frames"].add(i)
            s["wh"].append(w)
            s["wh2"].append(h)
            s["rel"].append(math.hypot(cx - CCX, cy - CCY))
            if len(samples[name]) < 5:
                samples[name].append((i, int(cx), int(cy), w, h))

print(f"\n{'对象':<28}{'出现':>6}{'帧数':>6}{'宽中位':>9}{'高中位':>9}"
      f"{'距圆心中位':>12}")
for name, _ in DET:
    s = stats[name]
    if not s["hits"]:
        print(f"{name:<28}{0:>6}{0:>6}{'-':>9}{'-':>9}{'-':>12}")
        continue
    print(f"{name:<28}{s['hits']:>6}{len(s['frames']):>6}"
          f"{np.median(s['wh']):>9.0f}{np.median(s['wh2']):>9.0f}"
          f"{np.median(s['rel']):>12.0f}")

# ---- 拼图：四类各挑样本
R = 18
TILE = 200
rowsimg = []
for name, _ in DET:
    tiles = []
    for (i, cx, cy, w, h) in samples[name][:5]:
        im = cv2.imread(str(ids[i - 1]))
        crop, _m = mm.make_minimap_roi(im, cfg)
        pad = R
        pc = cv2.copyMakeBorder(crop, pad, pad, pad, pad,
                                cv2.BORDER_REPLICATE)
        sub = pc[cy + pad - R:cy + pad + R, cx + pad - R:cx + pad + R]
        big = cv2.resize(sub, (TILE, TILE), interpolation=cv2.INTER_NEAREST)
        for k in range(0, TILE, 30):
            cv2.line(big, (k, 0), (k, TILE), (0, 255, 255), 1)
            cv2.line(big, (0, k), (TILE, k), (0, 255, 255), 1)
        bar = np.zeros((24, TILE, 3), np.uint8)
        cv2.putText(bar, f"a:f{i:05d} {w}x{h}", (4, 17),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
        tiles.append(np.vstack([bar, big]))
    if not tiles:
        tiles = [np.zeros((24 + TILE, TILE, 3), np.uint8)]
    while len(tiles) < 5:
        tiles.append(np.zeros_like(tiles[0]))
    lbl = np.zeros((24 + TILE, 150, 3), np.uint8)
    for li, part in enumerate(name.split(" ", 1)):
        cv2.putText(lbl, part, (4, 24 + li * 20), cv2.FONT_HERSHEY_SIMPLEX,
                    0.42, (0, 255, 255), 1)
    rowsimg.append(np.hstack([lbl] + tiles[:5]))

grid = np.vstack(rowsimg)
hdr = np.zeros((34, grid.shape[1], 3), np.uint8)
cv2.putText(hdr, "FOUR distinct overlay objects on session_a minimaps "
                 "(cyan grid = 5px, x5.56)  -  WHICH ONE IS THE TELEPORT "
                 "WAYPOINT?", (8, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
            (255, 255, 255), 1)
out = np.vstack([hdr, grid])
cv2.imwrite(str(FIG / "icon_four_candidates.png"), out)
print(f"\n  -> icon_four_candidates.png  {out.shape}")

with (FIG / "icon_four_stats.csv").open("w", encoding="utf-8-sig",
                                        newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["object", "n_hits", "n_frames", "w_median", "h_median",
                "r_median", "w_p25", "w_p75"])
    for name, _ in DET:
        s = stats[name]
        if not s["hits"]:
            w.writerow([name, 0, 0, None, None, None, None, None])
            continue
        w.writerow([name, s["hits"], len(s["frames"]),
                    round(float(np.median(s["wh"])), 1),
                    round(float(np.median(s["wh2"])), 1),
                    round(float(np.median(s["rel"])), 1),
                    round(float(np.percentile(s["wh"], 25)), 1),
                    round(float(np.percentile(s["wh"], 75)), 1)])
print(f"  -> icon_four_stats.csv")

# 菱形的尺寸稳定性（关键判据）
s = stats["A blue-violet diamond"]
if s["hits"] >= 20:
    a = np.array(s["wh"], float)
    b = np.array(s["wh2"], float)
    print(f"\n  菱形尺寸稳定性: 宽 中位 {np.median(a):.1f} "
          f"p10 {np.percentile(a,10):.1f} p90 {np.percentile(a,90):.1f}  "
          f"| 高 中位 {np.median(b):.1f} "
          f"p10 {np.percentile(b,10):.1f} p90 {np.percentile(b,90):.1f}")
print("=== DONE ===")
