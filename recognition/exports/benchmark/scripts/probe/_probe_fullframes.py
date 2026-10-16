"""看整帧 —— 设计 scene 检测器前的必做一步。

簇 1 的拼图只显示 eb_main（骰子位置）那一小块，看不出整屏是什么。
判断「大世界 vs 非大世界」必须看整帧：minimap 在哪、被什么挡住了。
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
from pathlib import Path

import cv2
import numpy as np

BENCH = Path(RECOG_ROOT / 'exports' / 'benchmark')
FRAMES = Path(RECOG_ROOT / 'raw' / 'frames' / 'batch_20260929b')
CSV_IN = BENCH / "eb_main_features_clustered.csv"
OUT = BENCH / "_probe_fullframes.png"

#: 每簇各挑 2 帧，再加上几张簇 1 里形态各异的
PICKS = [
    ("c0", "f00280"), ("c0", "f00100"),
    ("c1-map", "f00250"), ("c1-map", "f00160"),
    ("c1-load", "f00064"), ("c1-dark", "f00155"),
    ("c1-blue", "f00268"), ("c2", "f00005"),
]

W, H = 460, 288
cols, rows = 2, 4
canvas = np.full((rows * (H + 26) + 10, cols * (W + 10) + 10, 3), 48, np.uint8)
font = cv2.FONT_HERSHEY_SIMPLEX

# ROI 框（画上去，便于确认 minimap 在哪）
ROIS = {
    "minimap": (0.0299, 0.0116, 0.1464, 0.1911),
    "eb_main": (0.9171, 0.8604, 0.9787, 0.9542),
}

for n, (tag, fid) in enumerate(PICKS):
    img = cv2.imread(str(FRAMES / f"{fid}.jpg"), cv2.IMREAD_COLOR)
    if img is None:
        continue
    ih, iw = img.shape[:2]
    vis = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)
    for name, (l, t, r, b) in ROIS.items():
        cv2.rectangle(vis, (int(l * W), int(t * H)),
                      (int(r * W), int(b * H)),
                      (0, 255, 255) if name == "minimap" else (0, 0, 255), 2)
        cv2.putText(vis, name, (int(l * W) + 3, int(t * H) + 16), font,
                    0.42, (0, 255, 255) if name == "minimap" else (0, 0, 255),
                    1, cv2.LINE_AA)
    gx, gy = n % cols, n // cols
    x = 10 + gx * (W + 10)
    y = 10 + gy * (H + 26)
    canvas[y:y + H, x:x + W] = vis
    cv2.putText(canvas, f"{tag}  {fid}  ({iw}x{ih})", (x, y + H + 18),
                font, 0.5, (225, 225, 225), 1, cv2.LINE_AA)

cv2.imwrite(str(OUT), canvas)
print(f"已写 {OUT}  shape={canvas.shape}")
print("黄框 = minimap ROI   红框 = eb_main ROI")
