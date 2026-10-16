"""诊断：三角本体能否从视野锥/水域里分离出来？

若"圆心连通域"在所有阈值下都远大于三角本体的 400~600 px，
说明颜色+连通域方法**结构性不成立** —— 三角和视野锥同色且相连。
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
x0, y0, _, _ = mm.minimap_rect(cv2.imread(str(ids[199])), cfg)
CX = float(cfg["cx"]) * 2304 - x0
CY = float(cfg["cy"]) * 1440 - y0

print("=" * 92)
print("三角本体 vs 视野锥 vs 水域：阈值扫描")
print("=" * 92)
print("  三角本体实测约 400~600 px（含描边）。")
print("  如果圆心连通域远大于这个数 -> 三角和别的东西连成一片。\n")
for f in (200, 400, 900, 1300):
    im = cv2.imread(str(ids[f - 1]))
    crop, _ = mm.make_minimap_roi(im, cfg)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    print(f"  --- a:f{f:05d} ---")
    for lo, hi, slo, vlo in ((85, 105, 90, 120), (85, 100, 150, 150),
                             (80, 95, 120, 140), (90, 110, 120, 130),
                             (85, 100, 180, 160), (88, 100, 200, 170)):
        m = ((H >= lo) & (H <= hi) & (S >= slo) & (V >= vlo)).astype(np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        n, lab, st, ct = cv2.connectedComponentsWithStats(m, 8)
        area, size = 0, (0, 0)
        for j in range(1, n):
            x, y, w, h, a = st[j]
            if x <= CX < x + w and y <= CY < y + h:
                area, size = a, (w, h)
        flag = "OK" if 200 <= area <= 800 else "**过大/过小**"
        print(f"    H[{lo:>3},{hi:>3}] S>{slo:<3} V>{vlo:<3} -> "
              f"圆心连通域 {area:>5}px {size[0]}x{size[1]:<3} {flag}")

print("\n" + "=" * 92)
print("结论判据")
print("=" * 92)
print("  若绝大多数阈值下圆心连通域都 >800px，说明：")
print("    玩家三角与白色视野锥、青蓝水域在像素上**连通**，")
print("    颜色阈值无法把它们分开 —— 需要形状/模板方法，不是颜色方法。")
print("=== DONE ===")
