"""session_b 全量小地图目视筛查：哪些帧才是真正的大世界。

**为什么必须目视**
小地图 ROI 在加载界面上会被角色立绘盖住，而立绘本身纹理极丰富
（b:f00215 有 1658 个 ORB 特征，比真实小地图的 937~1720 毫不逊色）。
"ORB 特征数 > 400"这个过滤器**骗人**，必须看图。

输出 11x14 网格，每格带帧号。
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
ids = sorted((RECOG / "raw" / "frames" / "session_20261001_b").glob("*.jpg"))

CELL = 128
picks = list(range(10, len(ids) + 1, 10))
tiles = []
for f in picks:
    im = cv2.imread(str(ids[f - 1]))
    c, _ = mm.make_minimap_roi(im, cfg)
    t = cv2.resize(c, (CELL, CELL), interpolation=cv2.INTER_AREA)
    cv2.rectangle(t, (0, 0), (CELL - 1, 18), (0, 0, 0), -1)
    cv2.putText(t, f"{f:05d}", (3, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                (0, 255, 255), 1)
    tiles.append(t)

COLS = 14
while len(tiles) % COLS:
    tiles.append(np.zeros((CELL, CELL, 3), np.uint8))
grid = np.vstack([np.hstack(tiles[i:i + COLS])
                  for i in range(0, len(tiles), COLS)])
hdr = np.zeros((30, grid.shape[1], 3), np.uint8)
cv2.putText(hdr, f"session_b minimap, every 10th frame, {len(picks)} tiles "
                f"(black bar = frame no.)", (8, 21),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
out = np.vstack([hdr, grid])
cv2.imwrite(str(FIG / "session_b_minimap_grid.png"), out)
print(f"  -> {FIG/'session_b_minimap_grid.png'}  {out.shape}")
print("=== DONE ===")
