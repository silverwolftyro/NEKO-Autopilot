"""彩色原图 vs 灰度金字塔：画风与信息量对照。

**背景**：`P0_pyramid` 切出来饱和度 0%（纯灰），
源图 `teyvat_P0.png` 饱和度 17.5%。颜色是在切片时丢的。

先确认两件事，再决定要不要重建彩色金字塔：
  1. 彩色原图长什么样？和游戏小地图是同一套画风吗？
  2. 彩色版能不能提供灰度版没有的判别信息（尤其"水 vs 地"）
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

import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

RECOG = Path(RECOG_ROOT)
P0 = RECOG / "data" / "mihoyo_maps" / "teyvat_P0.png"
L2 = RECOG / "data" / "mihoyo_maps" / "P0_pyramid" / "L2_full.png"
ANCH = RECOG / "data" / "mihoyo_maps" / "teyvat_P0.png.anchors.json"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" / "orb_e"
FIG.mkdir(parents=True, exist_ok=True)

anchors = json.loads(ANCH.read_text(encoding="utf-8"))["points"]
tgt = next(a for a in anchors if a["name"] == "七天神像-枫丹廷区")
print(f"对照锚点 七天神像-枫丹廷区  L0=({tgt['x']},{tgt['y']})")

# 1/2 降采样读入（18432x9216，约 500 MB BGR）
t = time.perf_counter()
half = cv2.imread(str(P0), cv2.IMREAD_REDUCED_COLOR_2)
print(f"原图 1/2 读入 {half.shape}  {time.perf_counter()-t:.1f}s")
assert half is not None, "读不到原图"

sat = half.max(2).astype(int) - half.min(2).astype(int)
print(f"  有彩像素 (>30) = {100*(sat>30).mean():.2f}%   "
      f"p95 饱和度 {np.percentile(sat,95):.0f}")

# 全图缩略
ov = cv2.resize(half, (1600, 800), interpolation=cv2.INTER_AREA)
cv2.putText(ov, "mihoyo teyvat_P0.png  (color, 1/32)", (10, 28),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4)
cv2.putText(ov, "mihoyo teyvat_P0.png  (color, 1/32)", (10, 28),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 1)
cv2.imwrite(str(FIG / "p0_color_overview.png"), ov)

# 锚点处裁块（半分辨率 = 2 m/px）
cx, cy = tgt["x"] // 2, tgt["y"] // 2
c512 = half[cy - 256:cy + 256, cx - 256:cx + 256].copy()
c512 = cv2.resize(c512, (512, 512), interpolation=cv2.INTER_CUBIC)
cv2.putText(c512, "P0 COLOR crop 1024m @2m/px", (8, 26),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 4)
cv2.putText(c512, "P0 COLOR crop 1024m @2m/px", (8, 26),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)

# 同区域灰度版对照
g512 = cv2.cvtColor(c512, cv2.COLOR_BGR2GRAY)
g512 = cv2.cvtColor(g512, cv2.COLOR_GRAY2BGR)
cv2.putText(g512, "same crop, GRAY (what we searched)", (8, 26),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 4)
cv2.putText(g512, "same crop, GRAY (what we searched)", (8, 26),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

grid = np.hstack([c512, g512])
cv2.imwrite(str(FIG / "p0_color_vs_gray.png"), grid)
print(f"  -> {FIG/'p0_color_vs_gray.png'}")
print(f"  -> {FIG/'p0_color_overview.png'}")

# 颜色判别力：水(蓝) / 地(绿褐) 在彩色与灰度下能不能分开
lab = half[cy - 256:cy + 256, cx - 256:cx + 256]
hsv = cv2.cvtColor(lab, cv2.COLOR_BGR2HSV)
gray = cv2.cvtColor(lab, cv2.COLOR_BGR2GRAY)
print(f"\n  裁块内 彩色像素 {100*(sat[cy-256:cy+256, cx-256:cx+256]>30).mean():.1f}%"
      f"   灰度标准差 {gray.std():.1f}")
print(f"  灰度直方图通道数(>1%) = "
      f"{int((np.histogram(gray,256,range=(0,256))[0] > gray.size*0.01).sum())}"
      f"   彩色 BGR 标准差 = {lab.reshape(-1,3).std(0).round(1)}")
print("\n=== DONE ===")
