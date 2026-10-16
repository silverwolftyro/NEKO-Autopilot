"""任务 A 第一步 · 近距离看清玩家标记（青蓝三角 + 白视野锥）。

不知道确切的 HSV 范围和形状之前不写提取代码 ——
先把圆心区域的原始像素和 HSV 分布打出来。
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
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

PERC = (Path(PLUGIN_DIR.parent)
        / "genshin_game_buddy" / "autopilot" / "perception")
RECOG = Path(RECOG_ROOT)
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" \
    / "heading"
FIG.mkdir(parents=True, exist_ok=True)


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


mm = _load("minimap_mask", PERC / "minimap_mask.py")
cfg = mm.load_circle_cfg()
ids = sorted((RECOG / "raw" / "frames"
              / "session_20261001_a").glob("*.jpg"))
FRAMES = [200, 400, 700, 1000, 1300]

# 圆心在裁块里的位置
x0, y0, x1, y1 = mm.minimap_rect(cv2.imread(str(ids[199])), cfg)
CX = float(cfg["cx"]) * 2304 - x0
CY = float(cfg["cy"]) * 1440 - y0
R = 34                                    # 取圆心 68x68 窗口
print(f"小地图裁块 {x1-x0}x{y1-y0}  圆心 ({CX:.1f},{CY:.1f})  取 {2*R}x{2*R} 窗口")

tiles = []
print("\n" + "=" * 92)
print("圆心区域 HSV 分布（找青蓝三角）")
print("=" * 92)
for f in FRAMES:
    im = cv2.imread(str(ids[f - 1]))
    crop, _ = mm.make_minimap_roi(im, cfg)
    cyi, cxi = int(round(CY)), int(round(CX))
    patch = crop[cyi - R:cyi + R, cxi - R:cxi + R]
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    # 青蓝候选：H 85-105, S>90, V>120
    cand = ((H >= 85) & (H <= 105) & (S > 90) & (V > 120))
    n_c = int(cand.sum())
    b, g, r = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
    # 蓝绿倾向：G 和 B 都明显高于 R
    teal = ((b.astype(int) + g.astype(int)) / 2 - r.astype(int)) > 40
    n_t = int(teal.sum())
    if n_c:
        ys, xs = np.where(cand)
        hmed = int(np.median(H[cand]))
        print(f"  a:f{f:05d}: 青蓝候选 {n_c:>3}px  H中位 {hmed}  "
              f"蓝绿倾向 {n_t:>3}px  "
              f"质心 ({xs.mean()-R:+.1f},{ys.mean()-R:+.1f})  "
              f"(相对圆心)")
    else:
        print(f"  a:f{f:05d}: 青蓝候选 0px   蓝绿倾向 {n_t:>3}px")
    # 拼图：原图 + HSV 掩膜
    Z = 7
    big = cv2.resize(patch, (2 * R * Z, 2 * R * Z),
                     interpolation=cv2.INTER_NEAREST)
    for k in range(0, big.shape[1], 10 * Z):
        cv2.line(big, (k, 0), (k, big.shape[0]), (0, 255, 255), 1)
    for k in range(0, big.shape[0], 10 * Z):
        cv2.line(big, (0, k), (big.shape[1], k), (0, 255, 255), 1)
    zy, zx = R * Z, R * Z
    cv2.drawMarker(big, (zx, zy), (255, 0, 255), cv2.MARKER_CROSS, 30, 2)
    m2 = cv2.resize(cv2.cvtColor(cand.astype(np.uint8) * 255,
                                 cv2.COLOR_GRAY2BGR),
                    (2 * R * Z, 2 * R * Z), interpolation=cv2.INTER_NEAREST)
    bar = np.zeros((24, big.shape[1] * 2 + 6, 3), np.uint8)
    cv2.putText(bar, f"a:f{f:05d}", (4, 17), cv2.FONT_HERSHEY_SIMPLEX,
                0.45, (255, 255, 255), 1)
    row = np.hstack([big, np.zeros((big.shape[0], 6, 3), np.uint8), m2])
    tiles.append(np.vstack([bar, row]))

H2 = max(t.shape[0] for t in tiles)
tiles = [cv2.copyMakeBorder(t, 0, H2 - t.shape[0], 0, 0,
                            cv2.BORDER_CONSTANT) for t in tiles]
COLS = 2
while len(tiles) % COLS:
    tiles.append(np.zeros_like(tiles[0]))
grid = np.vstack([np.hstack(tiles[i:i + COLS])
                  for i in range(0, len(tiles), COLS)])
hdr = np.zeros((32, grid.shape[1], 3), np.uint8)
cv2.putText(hdr, f"MINIMAP CENTRE {2*R}x{2*R} px, x7 zoom  |  left: BGR  "
                 f"right: cyan-blue mask  |  magenta cross = minimap centre",
            (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
p = FIG / "centre_probe.png"
cv2.imwrite(str(p), np.vstack([hdr, grid]))
print(f"\n  -> {p.name}  {np.vstack([hdr,grid]).shape}")
print("=== DONE ===")
