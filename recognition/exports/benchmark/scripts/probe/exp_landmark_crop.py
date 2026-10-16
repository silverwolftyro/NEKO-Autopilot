"""验证 1（续）· 精确裁出候选图标并测量。

a:f00200 目视看到 4 类图标（网格坐标，单位=裁块像素）：
  A 蓝紫色菱形   约 (118, 128)
  B 浅青色圆形   约 ( 40, 105)
  C 黑色 T 形    约 (152,  88)
  D 青色玩家箭头 圆心 (130, 130)

裁 48x48 区域放大 8 倍，人眼确认哪类是"传送锚点"，
并量出**图标本体的实际尺寸**（不含半透明光晕）。
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
DA = RECOG / "raw" / "frames" / "session_20261001_a"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" / "landmark"
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

GUESSES = [("A_diamond_blue", 118, 128), ("B_circle_cyan", 40, 105),
           ("C_letter_T", 152, 88), ("D_player_arrow", 130, 130)]
R = 24            # 裁 48x48
Z = 8

for f in (200, 900, 1450):
    im = cv2.imread(str(ids[f - 1]))
    crop, _ = mm.make_minimap_roi(im, cfg)
    tiles = []
    for name, gx, gy in GUESSES:
        sub = crop[max(0, gy - R):gy + R, max(0, gx - R):gx + R]
        big = cv2.resize(sub, (R * 2 * Z, R * 2 * Z),
                         interpolation=cv2.INTER_NEAREST)
        # 10px 原像素网格
        for k in range(0, R * 2 * Z, 10 * Z):
            cv2.line(big, (k, 0), (k, big.shape[0]), (0, 255, 255), 1)
            cv2.line(big, (0, k), (big.shape[0], k), (0, 255, 255), 1)
        cv2.line(big, (R * Z, 0), (R * Z, big.shape[0]), (0, 0, 255), 2)
        cv2.line(big, (0, R * Z), (big.shape[0], R * Z), (0, 0, 255), 2)
        bar = np.zeros((26, big.shape[1], 3), np.uint8)
        cv2.putText(bar, f"{name} ({gx},{gy})  grid=10px  z={Z}",
                    (4, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
        tiles.append(np.vstack([bar, big]))
    row = np.hstack(tiles)
    hdr = np.zeros((28, row.shape[1], 3), np.uint8)
    cv2.putText(hdr, f"a:f{f:05d}  icon candidates 48x48 @ x8", (6, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    out = np.vstack([hdr, row])
    p = FIG / f"probe_icons_f{f:05d}.png"
    cv2.imwrite(str(p), out)
    print(f"  -> {p.name}  {out.shape}")

# 顺带出一张小地图全览（多帧拼排），给用户确认"哪些是锚点图标"
sel = [200, 800, 1100, 1450]
tiles = []
for f in sel:
    im = cv2.imread(str(ids[f - 1]))
    crop, _ = mm.make_minimap_roi(im, cfg)
    t = cv2.resize(crop, (400, 400), interpolation=cv2.INTER_CUBIC)
    bar = np.zeros((26, 400, 3), np.uint8)
    cv2.putText(bar, f"a:f{f:05d}", (6, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                (0, 255, 255), 1)
    tiles.append(np.vstack([bar, t]))
out = np.vstack([np.hstack(tiles[:2]), np.hstack(tiles[2:])])
p = FIG / "probe_minimap_overview.png"
cv2.imwrite(str(p), out)
print(f"  -> {p.name}  {out.shape}")
print("=== DONE ===")
