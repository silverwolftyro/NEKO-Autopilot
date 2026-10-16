"""最终拼图：ORB 声称的位置 vs 玩家大概率所在的位置。

列 1  真实小地图
列 2  ORB 全图搜索的**最强命中**（内点只有 5~7，纯噪声地板）
列 3  传送点 841 枫丹廷区（玩家大概率在这，未经独立验证）

所有图带帧号和坐标。matplotlib 风格标签一律英文（无 CJK 字形）。
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

SIZE = 512
# (帧, ORB 全图最强命中 L0, 内点数, 传送点 841 L0)
CASES = [(20, (14963, 5732), 7, (19141, 7655)),
         (40, (14692, 6334), 6, (19141, 7655)),
         (60, (14692, 6334), 5, (19141, 7655))]

HALF = cv2.imread(str(P0), cv2.IMREAD_REDUCED_COLOR_2)      # 2 m/px
ids = sorted((RECOG / "raw" / "frames" / "session_20261001_b").glob("*.jpg"))


def cap(img, lines, color):
    bar = np.zeros((26 * len(lines) + 8, SIZE, 3), np.uint8)
    out = np.vstack([bar, img])
    for i, s in enumerate(lines):
        y = 22 + 26 * i
        cv2.putText(out, s, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3)
        cv2.putText(out, s, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    cv2.rectangle(out, (0, 0), (SIZE - 1, out.shape[0] - 1), (80, 80, 80), 1)
    return out


def cross(img, r=40):
    c = SIZE // 2
    cv2.line(img, (c - r, c), (c - r // 3, c), (0, 0, 255), 2)
    cv2.line(img, (c + r // 3, c), (c + r, c), (0, 0, 255), 2)
    cv2.line(img, (c, c - r), (c, c - r // 3), (0, 0, 255), 2)
    cv2.line(img, (c, c + r // 3), (c, c + r), (0, 0, 255), 2)
    cv2.circle(img, (c, c), r, (0, 0, 255), 2)
    return img


def color_crop(l0, meters=1024):
    r = int(meters / 2 / 2.0)
    cx, cy = l0[0] / 2.0, l0[1] / 2.0
    H, W = HALF.shape[:2]
    x0, y0 = max(0, int(cx) - r), max(0, int(cy) - r)
    sub = HALF[y0:min(H, y0 + 2 * r), x0:min(W, x0 + 2 * r)]
    out = np.zeros((SIZE, SIZE, 3), np.uint8)
    h, w = sub.shape[:2]
    out[:h, :w] = cv2.resize(sub, (w, h), interpolation=cv2.INTER_CUBIC)
    out = cv2.resize(out, (SIZE, SIZE), interpolation=cv2.INTER_CUBIC)
    return cross(out)


rows = []
for f, hit, inl, tp in CASES:
    im = cv2.imread(str(ids[f - 1]))
    mini, _ = mm.make_minimap_roi(im, cfg)
    tag = f"b:f{f:05d}"
    p1 = cap(cv2.resize(mini, (SIZE, SIZE), interpolation=cv2.INTER_CUBIC),
             [tag, "1. GAME MINIMAP (rendered)", "2443 ORB features",
              "query image"], (0, 255, 255))
    p2 = cap(color_crop(hit),
             [tag, f"2. ORB BEST HIT  inl={inl}",
              f"L0=({hit[0]},{hit[1]})", "out of 4095 windows scanned"],
             (0, 140, 255))
    p3 = cap(color_crop(tp),
             [tag, "3. TELEPORT 841  Fontaine Court",
              f"L0=({tp[0]},{tp[1]})", "where player most likely is"],
             (0, 255, 120))
    rows.append(np.hstack([p1, p2, p3]))

mont = np.vstack(rows)
hdr = np.zeros((64, mont.shape[1], 3), np.uint8)
cv2.putText(hdr, "ORB full-map search verdict: real minimap max 5-7 RANSAC inliers "
                 "anywhere on 4095 windows", (10, 26),
            cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 1)
cv2.putText(hdr, "same-domain control (map crop vs map) = 1187 inliers  ->  "
                 "pipeline is sound, domain gap is the blocker",
            (10, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 1)
out = np.vstack([hdr, mont])
cv2.imwrite(str(FIG / "verdict_triplet.png"), out)
print(f"  -> {FIG/'verdict_triplet.png'}  {out.shape}")
print("=== DONE ===")
