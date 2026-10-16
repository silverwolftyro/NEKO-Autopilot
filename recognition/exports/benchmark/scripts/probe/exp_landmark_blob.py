"""验证 1（定稿）· 用颜色+形状自动定位传送锚点图标。

目测坐标不可靠（估计偏了 10+ px）。改用**颜色 + 连通域**：
原神小地图上的传送锚点是**高饱和度蓝紫色菱形**，
这个颜色在小地图的青绿色水域/褐色陆地上不会混淆。

流程：
  1. HSV 筛出 H∈[100,150] S>80 V>60 的像素（蓝~靛蓝）
  2. 连通域分析
  3. 过滤：面积 40~500 px、等效直径 8~26 px、长宽比 0.6~1.7
  4. 每个候选裁 40x40 @x6 拼图，人眼确认
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

import importlib.util
import math
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

PERC = (Path(PLUGIN_DIR.parent)
        / "genshin_game_buddy" / "autopilot" / "perception")
RECOG = Path(RECOG_ROOT)
DA = RECOG / "raw" / "frames" / "session_20261001_a"
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
H0, W0 = 1440, 2304
x0, y0, _, _ = mm.minimap_rect(cv2.imread(str(ids[199])), cfg)
CX, CY = float(cfg["cx"]) * W0 - x0, float(cfg["cy"]) * H0 - y0


def find_blobs(crop, mask):
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    sel = ((H >= 100) & (H <= 150) & (S > 80) & (V > 60) & (mask > 0))
    sel = sel.astype(np.uint8)
    sel = cv2.morphologyEx(sel, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(sel, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if not (40 <= a <= 500):
            continue
        d = math.sqrt(w * h)
        if not (8 <= d <= 26):
            continue
        ar = w / max(h, 1)
        if not (0.6 <= ar <= 1.7):
            continue
        if math.hypot(cent[i][0] - CX, cent[i][1] - CY) < 25:
            continue
        out.append(dict(cx=float(cent[i][0]), cy=float(cent[i][1]),
                        w=int(w), h=int(h), area=int(a),
                        r=float(math.hypot(cent[i][0] - CX, cent[i][1] - CY))))
    out.sort(key=lambda z: -z["area"])
    return out, sel


print("蓝紫色连通域检测（传送锚点候选）：")
allpos = {}
for f in (200, 350, 700, 800, 900, 1100, 1250, 1450):
    im = cv2.imread(str(ids[f - 1]))
    crop, mask = mm.make_minimap_roi(im, cfg)
    blobs, sel = find_blobs(crop, mask)
    allpos[f] = blobs
    print(f"  a:f{f:05d}  {len(blobs):>2} 个  "
          + "  ".join(f"({b['cx']:.0f},{b['cy']:.0f}){b['w']}x{b['h']}"
                      for b in blobs[:6]))

# 拼图：f00200 的所有候选 + 全图标注
f = 200
im = cv2.imread(str(ids[f - 1]))
crop, mask = mm.make_minimap_roi(im, cfg)
blobs = allpos[f]
R, Z = 20, 6
tiles = []
for i, b in enumerate(blobs[:8]):
    gx, gy = int(round(b["cx"])), int(round(b["cy"]))
    sub = crop[max(0, gy - R):gy + R, max(0, gx - R):gx + R]
    if sub.shape[0] < R * 2 or sub.shape[1] < R * 2:
        continue
    big = cv2.resize(sub, (R * 2 * Z, R * 2 * Z),
                     interpolation=cv2.INTER_NEAREST)
    for k in range(0, R * 2 * Z, 5 * Z):
        cv2.line(big, (k, 0), (k, big.shape[0]), (0, 255, 255), 1)
        cv2.line(big, (0, k), (big.shape[0], k), (0, 255, 255), 1)
    cv2.line(big, (R * Z, 0), (R * Z, big.shape[0]), (0, 0, 255), 2)
    cv2.line(big, (0, R * Z), (R * 2 * Z, R * Z), (0, 0, 255), 2)
    bar = np.zeros((24, big.shape[1], 3), np.uint8)
    cv2.putText(bar, f"#{i} ({gx},{gy}) {b['w']}x{b['h']} a={b['area']}",
                (4, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (255, 255, 255), 1)
    tiles.append(np.vstack([bar, big]))
while tiles and len(tiles) % 4:
    tiles.append(np.zeros_like(tiles[0]))
grid = np.vstack([np.hstack(tiles[i:i + 4]) for i in range(0, len(tiles), 4)])
hdr = np.zeros((30, grid.shape[1], 3), np.uint8)
cv2.putText(hdr, f"a:f{f:05d}  blue-violet blob candidates  40x40 @ x6  "
                f"grid=5px", (6, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
            (0, 255, 255), 1)
out = np.vstack([hdr, grid])
cv2.imwrite(str(FIG / "probe_anchor_templates.png"), out)
print(f"\n  -> probe_anchor_templates.png  {out.shape}")

# 整张小地图 + 标注 + HSV mask
vis = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
_, sel = find_blobs(crop, mask)
selv = cv2.resize(sel * 255, (vis.shape[1], vis.shape[0]),
                  interpolation=cv2.INTER_NEAREST)
selv = cv2.cvtColor(selv, cv2.COLOR_GRAY2BGR)
for i, b in enumerate(blobs):
    px, py = int(b["cx"] * 2), int(b["cy"] * 2)
    cv2.circle(vis, (px, py), 26, (0, 0, 255), 2)
    cv2.putText(vis, f"#{i}", (px - 9, py + 7), cv2.FONT_HERSHEY_SIMPLEX,
                0.55, (0, 0, 255), 2)
pair = np.hstack([vis, selv])
hdr2 = np.zeros((30, pair.shape[1], 3), np.uint8)
cv2.putText(hdr2, f"a:f{f:05d}  left = minimap with candidates   |   "
                 f"right = HSV mask (H100-150 S>80 V>60)", (6, 21),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
cv2.imwrite(str(FIG / "probe_anchor_onmap.png"), np.vstack([hdr2, pair]))
print(f"  -> probe_anchor_onmap.png  {pair.shape}")
print("=== DONE ===")
