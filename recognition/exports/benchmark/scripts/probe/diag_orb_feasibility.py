"""ORB/SIFT 可行性验证（实验 A-D + E 粗测）。

一次性判定："特征点匹配"在这组数据源上能不能用。
不改 minimap_locator，只量。
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
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

PERC = (Path(PLUGIN_DIR.parent)
        / "genshin_game_buddy" / "autopilot" / "perception")
RECOG = Path(RECOG_ROOT)
ANCH = RECOG / "data" / "mihoyo_maps" / "teyvat_P0.png.anchors.json"
DA = RECOG / "raw" / "frames" / "session_20261001_a"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" / "orb"
FIG.mkdir(parents=True, exist_ok=True)


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


cl = _load("chunk_loader", PERC / "chunk_loader.py")
pl = _load("pyramid_loader", PERC / "pyramid_loader.py")
mm = _load("minimap_mask", PERC / "minimap_mask.py")

CH = cl.ChunkLoader(cache_size=24)
PY = pl.PyramidLoader(preload=(1, 2))

HAS_SIFT = hasattr(cv2, "SIFT_create")
print(f"OpenCV {cv2.__version__}  ORB={hasattr(cv2,'ORB_create')} "
      f"SIFT={HAS_SIFT}")

ORB = cv2.ORB_create(nfeatures=2000, fastThreshold=5)
SIFT = cv2.SIFT_create(nfeatures=2000) if HAS_SIFT else None
BF = cv2.BFMatcher()


def detect(img, use_sift=False):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    if use_sift and SIFT is not None:
        return SIFT.detectAndCompute(g, None)
    return ORB.detectAndCompute(g, None)


def match(a_desc, b_desc, k=2):
    if a_desc is None or b_desc is None or len(a_desc) < 2 or len(b_desc) < 2:
        return []
    raw = BF.knnMatch(a_desc, b_desc, k=k)
    good = []
    for pair in raw:
        if len(pair) == 2:
            m, n = pair
            if m.distance < 0.78 * n.distance:
                good.append(m)
    return good


doc = json.loads(ANCH.read_text(encoding="utf-8"))
anchors = doc["points"]
print(f"锚点 {len(anchors)} 个\n")

# ============ 实验 A + B
print("=" * 90)
print("实验 A/B · 底图侧特征点密度 + 底图自匹配")
print("=" * 90)
crops, kps, dsc, dens, selfm = {}, {}, {}, [], []
t0 = time.perf_counter()
for a in anchors:
    c = CH.load_l0_window((a["x"], a["y"]), 512)
    crops[a["name"]] = c
    for tag, dtor in (("ORB", False), ("SIFT", True)):
        k, d = detect(c, use_sift=dtor)
        kps[(a["name"], tag)] = k
        dsc[(a["name"], tag)] = d
        if tag == "ORB":
            dens.append(len(k))
    # B: 从 512 中央裁 260 做模板，自匹配
    t = c[126:386, 126:386]
    kt, dt = detect(t)
    good = match(dsc[(a["name"], "ORB")], dt)
    inl = 0
    if len(good) >= 4:
        src = np.float32([kps[(a["name"], "ORB")][m.queryIdx].pt
                           for m in good]).reshape(-1, 1, 2)
        dst = np.float32([kt[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        H, _ = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
        if H is not None:
            inl = int(sum(1 for m in good if True))
    selfm.append((a["name"], len(kps[(a["name"], "ORB")]), len(good), inl))
print(f"  耗时 {time.perf_counter()-t0:.1f}s")
d = np.array(dens)
print(f"\n  A) ORB 特征点数 / 512x512 底图块:")
print(f"     min={d.min()} p25={np.percentile(d,25):.0f} "
      f"中位={np.median(d):.0f} p75={np.percentile(d,75):.0f} max={d.max()}")
print(f"     <50 个的块: {(d<50).sum()}/{len(d)}")
sd_ = np.array([k for _, k, _, _ in selfm])
sm = np.array([g for _, _, g, _ in selfm])
print(f"\n  B) 底图自匹配（512 找 260 模板）:")
print(f"     ORB 特征点 中位 {np.median(sd_):.0f}")
print(f"     good matches 中位 {np.median(sm):.0f}  "
      f">30 的块: {(sm>30).sum()}/{len(sm)}")
print(f"     前 8 个: {[(n.split('-')[-1], g) for n, _, g, _ in selfm[:8]]}")

# ============ 实验 C
print("\n" + "=" * 90)
print("实验 C · 跨层匹配（L0 512 vs L2 同区域）")
print("=" * 90)
l2 = PY.get_level(2)
k4 = 1.0 / 4.0
cm = []
for a in anchors:
    x2, y2 = int(a["x"] * k4), int(a["y"] * k4)
    if not (0 <= x2 - 64 < l2.shape[1] - 128 and 0 <= y2 - 64 < l2.shape[0] - 128):
        continue
    c2 = l2[y2 - 64:y2 + 64, x2 - 64:x2 + 64]
    c2 = cv2.resize(c2, (512, 512), interpolation=cv2.INTER_CUBIC)
    k2, d2 = detect(c2)
    good = match(dsc[(a["name"], "ORB")], d2)
    cm.append((a["name"], len(good)))
cmv = np.array([g for _, g in cm])
print(f"  跨层 good matches 中位 {np.median(cmv):.0f}  "
      f">30: {(cmv>30).sum()}/{len(cmv)}")
print(f"  前 8 个: {[(n.split('-')[-1], g) for n, g in cm[:8]]}")

# ============ 实验 D
print("\n" + "=" * 90)
print("实验 D · 同区域 vs 不同区域（23 个锚点两两匹配）")
print("=" * 90)
N = len(anchors)
same, diff = [], []
t0 = time.perf_counter()
for i in range(N):
    for j in range(i + 1, N):
        g = match(dsc[(anchors[i]["name"], "ORB")],
                  dsc[(anchors[j]["name"], "ORB")])
        (same if i == j else diff).append(len(g))
sd2 = np.array(same)
df2 = np.array(diff)
print(f"  耗时 {time.perf_counter()-t0:.1f}s   配对 {len(same)} 组")
print(f"  自匹配(同块)   中位 {np.median(sd2):.0f}  "
      f">30: {(sd2>30).sum()}/{len(sd2)}")
print(f"  跨块(不同区域) 中位 {np.median(df2):.0f}  "
      f"p95 {np.percentile(df2,95):.0f}  max {df2.max()}")
if np.median(sd2) > 0:
    print(f"  ★ 同/中位 ÷ 异/中位 = {np.median(sd2)/max(np.median(df2),1e-9):.2f}x")

# ============ 实验 E 粗测
print("\n" + "=" * 90)
print("实验 E 粗测 · 真实小地图 vs 底图（无真值，只看有没有峰）")
print("=" * 90)
ids = sorted(DA.glob("*.jpg"))
picks = [ids[i] for i in range(200, len(ids), max(1, len(ids) // 6))][:6]
rows = []
for p in picks:
    im = cv2.imread(str(p))
    mini, mask = mm.make_minimap_roi(im)
    km, dm = detect(mini)
    best = 0
    for a in anchors:
        g = match(dm, dsc[(a["name"], "ORB")])
        if len(g) > best:
            best = len(g)
    rows.append((p.stem, len(km), best))
    print(f"  {p.stem}  小地图 ORB {len(km):>4}   "
          f"对 23 个锚点最大匹配 {best:>3}")
bs = [b for _, _, b in rows]
print(f"\n  小地图特征点 中位 {np.median([k for _, k, _ in rows]):.0f}")
print(f"  最大匹配数 中位 {np.median(bs):.0f}  max {max(bs)}")
print(f"  → {'有信号' if max(bs) > 15 else '几乎无信号'}")

with (FIG / "summary.csv").open("w", encoding="utf-8-sig", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["exp", "metric", "value"])
    w.writerow(["A", "orb_kp_median_512", np.median(d)])
    w.writerow(["A", "blocks_under_50_kp", int((d < 50).sum())])
    w.writerow(["B", "selfmatch_good_median", np.median(sm)])
    w.writerow(["B", "selfmatch_blocks_over_30", int((sm > 30).sum())])
    w.writerow(["C", "crosslevel_good_median", np.median(cmv)])
    w.writerow(["D", "same_block_median", np.median(sd2)])
    w.writerow(["D", "diff_block_median", np.median(df2)])
    w.writerow(["D", "diff_block_p95", np.percentile(df2, 95)])
    w.writerow(["E", "minimap_kp_median", np.median([k for _, k, _ in rows])])
    w.writerow(["E", "max_match_median", np.median(bs)])
print(f"\n  -> {FIG/'summary.csv'}")
print("=== DONE ===")
