"""验证 1 · 定位小地图上的锚点图标。

先把真实世界帧的小地图放大 + 打坐标网格，人眼/算法都能定位图标。
输出：
  probe_minimap_zoom_*.png   单帧放大（带 10px 网格和像素坐标）
  probe_icon_candidates.png   自动候选点标记

**自动候选的判据**（先粗筛，再人工确认）：
* 在圆形掩码内
* 距圆心 > 40 px（排除玩家箭头）
* 局部高对比（小图标 = 中心亮/暗 surrounded by 不同的地图底色）
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
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" / "landmark"
FIG.mkdir(parents=True, exist_ok=True)

FRAMES = [200, 350, 700, 800, 900, 1100, 1250, 1450]


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
x0, y0, x1, y1 = mm.minimap_rect(cv2.imread(str(ids[199])), cfg)
CX = float(cfg["cx"]) * W0 - x0
CY = float(cfg["cy"]) * H0 - y0
print(f"小地图裁块 {x1-x0}x{y1-y0}  圆心 ({CX:.1f},{CY:.1f})")


def grid_zoom(crop, z=2, step=10):
    t = cv2.resize(crop, None, fx=z, fy=z, interpolation=cv2.INTER_NEAREST)
    H, W = t.shape[:2]
    for x in range(0, W, step * z):
        cv2.line(t, (x, 0), (x, H), (0, 255, 255) if x % (50 * z) == 0
                 else (90, 90, 90), 1)
    for y in range(0, H, step * z):
        cv2.line(t, (0, y), (W, y), (0, 255, 255) if y % (50 * z) == 0
                 else (90, 90, 90), 1)
    for x in range(0, W, 50 * z):
        cv2.putText(t, str(x // z), (x + 2, 14), cv2.FONT_HERSHEY_SIMPLEX,
                    0.35, (0, 255, 255), 1)
    for y in range(50 * z, H, 50 * z):
        cv2.putText(t, str(y // z), (2, y - 3), cv2.FONT_HERSHEY_SIMPLEX,
                    0.35, (0, 255, 255), 1)
    return t


for f in FRAMES:
    im = cv2.imread(str(ids[f - 1]))
    crop, mask = mm.make_minimap_roi(im, cfg)
    t = grid_zoom(crop, 2, 10)
    hdr = np.zeros((30, t.shape[1], 3), np.uint8)
    cv2.putText(hdr, f"a:f{f:05d}  minimap {crop.shape[1]}x{crop.shape[0]}  "
                    f"cyan grid every 10px, labeled every 50px",
                (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    cv2.imwrite(str(FIG / f"probe_minimap_zoom_{f:05d}.png"),
                np.vstack([hdr, t]))
    print(f"  -> probe_minimap_zoom_{f:05d}.png")

# ---- 自动候选：高对比小斑点
print("\n自动候选点（LocalBinaryPatterns 响应的局部极大）：")
im = cv2.imread(str(ids[199]))
crop, mask = mm.make_minimap_roi(im, cfg)
g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
b = crop[:, :, 0].astype(int)
r = crop[:, :, 2].astype(int)
m = mask > 0
h, w = g.shape
resp = cv2.SimpleBlobDetector_create().detect(g) if False else None
# 用 Laplacian 绝对值的局部极大当"小图标"候选
lap = np.abs(cv2.Laplacian(g, cv2.CV_32F, ksize=3))
lm = np.where(m, lap, 0)
cands = []
for y in range(12, h - 12):
    for x in range(12, w - 12):
        if math.hypot(x - CX, y - CY) < 40:
            continue
        v = lm[y, x]
        if v < 30:
            continue
        if v == lm[y - 6:y + 7, x - 6:x + 7].max():
            cands.append((x, y, v, int(b[y, x] - r[y, x])))
cands.sort(key=lambda z: -z[2])
print(f"  共 {len(cands)} 个候选，取响应最强的 24 个")
t = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
for i, (x, y, v, bl) in enumerate(cands[:24]):
    cv2.circle(t, (x * 2, y * 2), 22, (0, 0, 255), 2)
    cv2.putText(t, str(i), (x * 2 - 8, y * 2 + 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
hdr = np.zeros((30, t.shape[1], 3), np.uint8)
cv2.putText(hdr, f"a:f00200  top-24 Laplacian maxima (b-r = bluemag), "
                f"2x zoom", (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
            (255, 255, 255), 1)
cv2.imwrite(str(FIG / "probe_icon_candidates.png"), np.vstack([hdr, t]))
print(f"  -> probe_icon_candidates.png")
for i, (x, y, v, bl) in enumerate(cands[:24]):
    print(f"    #{i:<3} ({x:>3},{y:>3})  lap={v:6.1f}  b-r={bl:>4}  "
          f"r_from_center={math.hypot(x-CX, y-CY):5.1f}")
print("=== DONE ===")
