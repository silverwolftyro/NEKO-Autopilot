"""真值三格拼图：让眼睛直接判断"游戏小地图"和"米游社底图"像不像。

原始需求是 Top-5 ORB 匹配拼图，但 152 帧全部零解，没有可展示的匹配位置。
改成真值版：每一格都取**已知正确位置**，四列并排：

  [小地图]  [彩色底图 1024m]  [彩色底图 512m]  [灰度金字塔 512m]

第 4 列尤其关键 —— **ORB 实际吃到的那张图**就是它。
所有图带帧号和坐标。
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
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

PERC = (Path(PLUGIN_DIR.parent)
        / "genshin_game_buddy" / "autopilot" / "perception")
RECOG = Path(RECOG_ROOT)
P0 = RECOG / "data" / "mihoyo_maps" / "teyvat_P0.png"
L0G = RECOG / "data" / "mihoyo_maps" / "P0_pyramid" / "L0"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" / "orb_e"
FIG.mkdir(parents=True, exist_ok=True)


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


mm = _load("minimap_mask", PERC / "minimap_mask.py")
cfg = mm.load_circle_cfg()

TRUTH = (19141, 7655)                       # 传送点 841 枫丹廷区 (L0 px)
FRAMES = [215, 217, 219, 221, 223]
SIZE = 512

HALF = cv2.imread(str(P0), cv2.IMREAD_REDUCED_COLOR_2)      # 2 m/px
cr, cc = TRUTH[1] // 1536, TRUTH[0] // 3072
BLKG = cv2.imread(str(L0G / f"r{cr:02d}_c{cc:02d}.png"),
                  cv2.IMREAD_GRAYSCALE)                     # 1 m/px
ids = sorted((RECOG / "raw" / "frames" / "session_20261001_b").glob("*.jpg"))
print(f"真值 L0=({TRUTH[0]},{TRUTH[1]})  彩色 2m/px {HALF.shape}  "
      f"灰度块 r{cr:02d}_c{cc:02d} {BLKG.shape}")


def cap(img, lines, color):
    bar = np.zeros((26 * len(lines) + 8, SIZE, 3), np.uint8)
    out = np.vstack([bar, img])
    for i, s in enumerate(lines):
        y = 22 + 26 * i
        cv2.putText(out, s, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3)
        cv2.putText(out, s, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    cv2.rectangle(out, (0, 0), (SIZE - 1, out.shape[0] - 1), (80, 80, 80), 1)
    return out


def cross(img, c=SIZE // 2, r=40):
    cv2.line(img, (c - r, c), (c - r // 3, c), (0, 0, 255), 2)
    cv2.line(img, (c + r // 3, c), (c + r, c), (0, 0, 255), 2)
    cv2.line(img, (c, c - r), (c, c - r // 3), (0, 0, 255), 2)
    cv2.line(img, (c, c + r // 3), (c, c + r), (0, 0, 255), 2)
    cv2.circle(img, (c, c), r, (0, 0, 255), 2)
    return img


def color_crop(l0, meters):
    """彩色底图裁块，meters 宽，返回 SIZE x SIZE。"""
    r = int(meters / 2 / 2.0)                  # 2 m/px
    cx, cy = l0[0] / 2.0, l0[1] / 2.0
    x0, y0 = int(round(cx)) - r, int(round(cy)) - r
    sub = HALF[max(0, y0):y0 + 2 * r, max(0, x0):x0 + 2 * r]
    return cv2.resize(sub, (SIZE, SIZE), interpolation=cv2.INTER_CUBIC)


def gray_crop(l0, meters):
    """灰度金字塔 L0 裁块（1 m/px），meters 宽。"""
    r = int(meters / 2)
    bxc, byc = l0[0] - cc * 3072, l0[1] - cr * 1536
    sub = BLKG[max(0, byc - r):byc + r, max(0, bxc - r):bxc + r]
    if sub.ndim == 2:
        sub = cv2.cvtColor(sub, cv2.COLOR_GRAY2BGR)
    return cv2.resize(sub, (SIZE, SIZE), interpolation=cv2.INTER_CUBIC)


rows = []
for f in FRAMES:
    im = cv2.imread(str(ids[f - 1]))
    mini, _ = mm.make_minimap_roi(im, cfg)
    tag = f"b:f{f:05d}"

    p1 = cap(cv2.resize(mini, (SIZE, SIZE), interpolation=cv2.INTER_CUBIC),
             [tag, "GAME MINIMAP (rendered)", f"circle r=130 px"],
             (0, 255, 255))
    p2 = cap(cross(color_crop(TRUTH, 1024)),
             [tag, "MIHOYO MAP - COLOR  1024 m wide", "ground truth here"],
             (0, 255, 120))
    p3 = cap(cross(color_crop(TRUTH, 512)),
             [tag, "MIHOYO MAP - COLOR  512 m wide", "same area, 2x zoom"],
             (0, 255, 120))
    p4 = cap(cross(gray_crop(TRUTH, 512)),
             [tag, "P0_pyramid L0 - GRAY 512 m", "what ORB actually saw"],
             (200, 200, 255))
    rows.append(np.hstack([p1, p2, p3, p4]))

mont = np.vstack(rows)
hdr = np.zeros((46, mont.shape[1], 3), np.uint8)
cv2.putText(hdr, "ground truth: teleport waypoint 841  Fontaine Court  L0=(19141,7655)   |   "
                "ORB inliers: control(cross-scale map crop)=401   real minimap=2",
            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 1)
out = np.vstack([hdr, mont])
cv2.imwrite(str(FIG / "truth_triplet.png"), out)
print(f"  -> {FIG/'truth_triplet.png'}  {out.shape}")

# 单独一张放大的：同一地点 小地图 vs 彩色底图 并排，便于细看
one = np.hstack([
    cap(cv2.resize(mini, (SIZE, SIZE), interpolation=cv2.INTER_CUBIC),
        ["b:f00215 minimap", "game render"], (0, 255, 255)),
    cap(cross(color_crop(TRUTH, 512)),
        ["mihoyo color 512m", "ground truth"], (0, 255, 120))])
cv2.imwrite(str(FIG / "side_by_side_1frame.png"), one)
print(f"  -> {FIG/'side_by_side_1frame.png'}")
print("=== DONE ===")
