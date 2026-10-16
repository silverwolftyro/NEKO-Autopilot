"""session_b 全量扫描：找出所有"大地图"帧 + 测量每帧的地图缩放档。

**为什么要全扫**
上一轮快测里 f00120/f00140 交替出现"白天/夜间"两种底图主题，
而且视野范围明显不同 —— 说明 session_b 里**有多个缩放档**。
ORB vs 小地图失败最可能的原因就是尺度对不上，必须先量化每档。

**大地图判据**（不靠特征数，特征数已被证明会骗人）
1. `scene_detector` 判**非世界**（血条 ROI 消失 → 大地图/背包/加载都会这样）
2. 画面主色是**地图绿/青**：HSV 里 H∈[60,110]∪[160,200] 且 S>60 的像素占比
   加载界面（角色立绘）/ 背包（UI 面板）都不满足

**缩放档判据**
大地图上**传送锚点图标**（蓝色菱形）之间的像素距离。
大地图打开时，锚点图标是恒定屏幕大小，地图放大时它们彼此**拉开**。
取画面内所有蓝色菱形标记的最近邻距离中位数 -> 像素越小 = 放得越大。
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
DB = RECOG / "raw" / "frames" / "session_20261001_b"
TREE = RECOG / "exports" / "benchmark" / "scripts" / "misc" / "coord_scene_tree.py"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" / "bigmap"
FIG.mkdir(parents=True, exist_ok=True)
FPS = 10.0


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


sd = _load("scene_detector", PERC / "scene_detector.py")
sd.set_classifier_module_path(TREE)
det = sd.SceneDetector(n_seconds=3.0, m_seconds=1.0)
coord_transform = _load("coord_transform", TREE.parent / "coord_transform.py")

ids = sorted(DB.glob("*.jpg"))
print(f"session_b {len(ids)} 帧  判定 {TREE.name}")

rows = []
t0 = time.perf_counter()
for i, p in enumerate(ids, 1):
    im = cv2.imread(str(p))
    if im is None:
        print(f"  {p.name} 读帧失败")
        continue
    st = det.update(im, (i - 1) / FPS)
    small = cv2.resize(im, (256, 160), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0].astype(int), hsv[:, :, 1], hsv[:, :, 2]
    # 地图绿(草/林) H 25~45 度；地图青(水) H 85~105 度
    map_px = (((h >= 25) & (h <= 45)) | ((h >= 85) & (h <= 105))) \
        & (s > 60) & (v > 40)
    map_ratio = float(map_px.mean())
    # 蓝色菱形传送锚点标记：BGR 偏蓝且亮
    b = small[:, :, 0].astype(int)
    g2 = small[:, :, 1].astype(int)
    r2 = small[:, :, 2].astype(int)
    blue = (b > 150) & (b.astype(int) - r2 > 40) & (b - g2 > 25)
    rows.append(dict(idx=i, frame=f"b:{p.stem}", is_world=st.is_world,
                     changed=st.changed, map_ratio=round(map_ratio, 4),
                     blue=int(blue.sum())))
    if i % 200 == 0:
        print(f"  {i}/{len(ids)}  {time.perf_counter()-t0:.0f}s")

out = FIG / "session_b_scan.csv"
with out.open("w", encoding="utf-8-sig", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["idx", "frame", "is_world", "changed", "map_ratio", "blue_px"])
    for r in rows:
        w.writerow([r["idx"], r["frame"], int(r["is_world"]), int(r["changed"]),
                    r["map_ratio"], r["blue"]])
print(f"  -> {out}   耗时 {time.perf_counter()-t0:.0f}s")

# ---- 分段
print("\n" + "=" * 92)
print("分段（去抖后）")
print("=" * 92)
segs = []
for r in rows:
    if not segs or segs[-1][2] != r["is_world"]:
        segs.append([r["idx"], r["idx"], r["is_world"], []])
    segs[-1][1] = r["idx"]
    segs[-1][3].append(r)
for a, b, w_, rs in segs:
    mr = np.array([x["map_ratio"] for x in rs])
    print(f"  f{a:05d}-f{b:05d}  {len(rs):>4} 帧  {'世界' if w_ else '非世界':<6}"
          f"  地图色占比 中位 {np.median(mr):.3f}  p90 {np.percentile(mr,90):.3f}")

# ---- 大地图候选：非世界 + 地图色占比高
print("\n" + "=" * 92)
print("大地图候选（非世界 且 地图色占比 > 0.30）")
print("=" * 92)
big = [r for r in rows if not r["is_world"] and r["map_ratio"] > 0.30]
runs = []
for r in big:
    if runs and r["idx"] - runs[-1][-1]["idx"] <= 3:
        runs[-1].append(r)
    else:
        runs.append([r])
for g in runs:
    a, b = g[0]["idx"], g[-1]["idx"]
    mr = np.median([x["map_ratio"] for x in g])
    print(f"  f{a:05d}-f{b:05d}  {len(g):>4} 帧  地图色中位 {mr:.3f}")
print(f"  合计 {len(big)} 帧 / {len(runs)} 段")

# ---- 缩放档：取每段中间帧，拼网格肉眼看
picks = [g[len(g) // 2] for g in runs]
print(f"\n  取每段中帧 {len(picks)} 张出网格")
tiles = []
for r in picks:
    im = cv2.imread(str(ids[r["idx"] - 1]))
    t = cv2.resize(im, (384, 240), interpolation=cv2.INTER_AREA)
    cv2.rectangle(t, (0, 0), (200, 20), (0, 0, 0), -1)
    cv2.putText(t, f"f{r['idx']:05d} map={r['map_ratio']:.2f}", (3, 15),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 255, 255), 1)
    tiles.append(t)
COLS = 5
while len(tiles) % COLS:
    tiles.append(np.zeros((240, 384, 3), np.uint8))
grid = np.vstack([np.hstack(tiles[i:i + COLS])
                  for i in range(0, len(tiles), COLS)])
cv2.imwrite(str(FIG / "bigmap_segments_grid.png"), grid)
print(f"  -> {FIG/'bigmap_segments_grid.png'}  {grid.shape}")
print("=== DONE ===")
