"""瓦片追踪可视化：把累加轨迹画到 144 块瓦片网格上。

三条轨迹（三段真实走路）用不同颜色，起终点标出，中途跨块处打标记。
同时画块边界网格和当前块高亮。
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
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

PERC = (Path(PLUGIN_DIR.parent)
        / "genshin_game_buddy" / "autopilot" / "perception")
RECOG = Path(RECOG_ROOT)
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" / "tile"
FIG.mkdir(parents=True, exist_ok=True)


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


tt = _load("tile_tracker", PERC / "tile_tracker.py")
TileTracker = tt.TileTracker
TileGrid = tt.TileGrid
TeleportJump = tt.TeleportJump

PC = RECOG / "exports" / "benchmark" / "datasets" / "phase_corr.csv"
WALK = RECOG / "exports" / "benchmark" / "datasets" / "walk_segments.csv"
prow = {int(r["idx"]): (float(r["dx_px"]), float(r["dy_px"]))
        for r in csv.DictReader(PC.open(encoding="utf-8-sig", newline=""))}
walks = list(csv.DictReader(WALK.open(encoding="utf-8-sig", newline="")))

grid = TileGrid()
# 局部视野：只画枫丹廷附近几块，缩放看得清
CX, CY = 19100, 7600                # 关注的底图像素中心
SPAN_X, SPAN_Y = 3200, 3200
X0, Y0 = CX - SPAN_X // 2, CY - SPAN_Y // 2
SCALE = 1.0                          # 1:1

W = SPAN_X
H = SPAN_Y
canvas = np.full((H, W, 3), 28, np.uint8)
# 画瓦片边界 + id
for tid in [f"r{r:02d}_c{c:02d}" for r in range(grid.grid[1])
            for c in range(grid.grid[0])]:
    try:
        t = grid.get(tid)
    except KeyError:
        continue
    x0, y0 = t.x0 - X0, t.y0 - Y0
    x1, y1 = t.x1 - X0, t.y1 - Y0
    if x1 < 0 or y1 < 0 or x0 > W or y0 > H:
        continue
    cv2.rectangle(canvas, (x0, y0), (x1, y1), (70, 70, 70), 1)
    cv2.putText(canvas, tid, (x0 + 6, y0 + 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (110, 110, 110), 1)

# 起点
START = (3600.0, 4300.0)
COLORS = [(60, 200, 255), (80, 255, 120), (255, 120, 200)]
tr0 = TileTracker(START, grid=grid)
s0 = tr0.update(0.0, 0.0)
sx, sy = int(s0.pixel[0] - X0), int(s0.pixel[1] - Y0)
cv2.drawMarker(canvas, (sx, sy), (0, 0, 255), cv2.MARKER_TILTED_CROSS, 28, 3)
cv2.putText(canvas, "START", (sx + 12, sy - 10), cv2.FONT_HERSHEY_SIMPLEX,
            0.6, (0, 0, 255), 2)

print("逐段累加：")
for w, col in zip(walks, COLORS):
    a = int(w["frame_first"].split(":f")[1])
    b = int(w["frame_last"].split(":f")[1])
    tr = TileTracker(START, grid=grid)
    pts = []
    crosses = 0
    jumps = 0
    st = tr.update(0.0, 0.0)
    pts.append((int(st.pixel[0] - X0), int(st.pixel[1] - Y0)))
    for i in range(a, b):
        if i not in prow:
            continue
        dx, dy = prow[i]
        try:
            st = tr.update(dx, dy)
        except TeleportJump:
            jumps += 1
            continue
        pts.append((int(st.pixel[0] - X0), int(st.pixel[1] - Y0)))
        if st.tile_changed:
            crosses += 1
            cv2.circle(canvas, pts[-1], 9, (0, 255, 255), 2)
    arr = np.array(pts, np.int32)
    cv2.polylines(canvas, [arr], False, col, 3, cv2.LINE_AA)
    for p in pts[::max(1, len(pts) // 6)]:
        cv2.circle(canvas, tuple(p), 4, col, -1)
    end = pts[-1]
    cv2.drawMarker(canvas, end, col, cv2.MARKER_SQUARE, 14, 3)
    print(f"  a:f{a:05d}-f{b:05d}: {len(pts)} 点  跨块 {crosses}  "
          f"跳变 {jumps}  终点({end[0]+X0},{end[1]+Y0})  "
          f"路径长 {st.path_len:.1f}m  漂移<={st.drift_bound:.2f}m")

hdr = np.zeros((70, W, 3), np.uint8)
cv2.putText(hdr, "TileTracker accumulated trajectories  |  "
                 "144-tile grid, r=rows c=cols, 3072x1536 px each",
            (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 1)
cv2.putText(hdr, "red X = start (world 3600,4300)   yellow rings = tile "
                 "crossings   3 colors = 3 walk segments",
            (10, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (200, 200, 200), 1)
out = np.vstack([hdr, canvas])
p = FIG / "trajectories.png"
cv2.imwrite(str(p), out)
print(f"  -> {p}  {out.shape}")
print("=== DONE ===")
