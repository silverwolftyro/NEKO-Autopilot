"""任务 A 第一步 · 把用户截图打网格，人工/程序框定锚点图标边界。

自动检测不可靠（上一版"最暗轮廓"把图标+残余背景一起框成 109x71）。
先出网格图定坐标，再写死模板框。
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

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

RECOG = Path(RECOG_ROOT)
SHOT = RECOG / "exports" / "benchmark" / "屏幕截图 2026-10-02 142123.png"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" \
    / "landmark2"
FIG.mkdir(parents=True, exist_ok=True)

shot = cv2.imdecode(np.fromfile(str(SHOT), dtype=np.uint8),
                    cv2.IMREAD_UNCHANGED)
print(f"截图 shape={shot.shape} dtype={shot.dtype}")
alpha = shot[:, :, 3] if shot.ndim == 3 and shot.shape[2] == 4 else None
bgr = shot[:, :, :3].copy()
H, W = bgr.shape[:2]
print(f"  RGB {W}x{H}   alpha 存在: {alpha is not None}")
if alpha is not None:
    print(f"  alpha: min {alpha.min()} max {alpha.max()} "
          f"不透明像素 {(alpha>128).sum()} / {alpha.size}")
    ys, xs = np.where(alpha > 128)
    if len(xs):
        print(f"  alpha bbox: x[{xs.min()},{xs.max()}] y[{ys.min()},{ys.max()}]")

Z = 8
full = cv2.resize(bgr, (W * Z, H * Z), interpolation=cv2.INTER_NEAREST)
for k in range(0, full.shape[1], 10 * Z):
    c = (0, 255, 255) if k % (50 * Z) == 0 else (80, 80, 80)
    cv2.line(full, (k, 0), (k, full.shape[0]), c, 1)
for k in range(0, full.shape[0], 10 * Z):
    c = (0, 255, 255) if k % (50 * Z) == 0 else (80, 80, 80)
    cv2.line(full, (0, k), (full.shape[1], k), c, 1)
for k in range(0, full.shape[1], 50 * Z):
    cv2.putText(full, str(k // Z), (k + 3, 18), cv2.FONT_HERSHEY_SIMPLEX,
                0.45, (0, 0, 0), 3)
    cv2.putText(full, str(k // Z), (k + 3, 18), cv2.FONT_HERSHEY_SIMPLEX,
                0.45, (0, 255, 255), 1)
for k in range(50 * Z, full.shape[0], 50 * Z):
    cv2.putText(full, str(k // Z), (3, k - 5), cv2.FONT_HERSHEY_SIMPLEX,
                0.45, (0, 0, 0), 3)
    cv2.putText(full, str(k // Z), (3, k - 5), cv2.FONT_HERSHEY_SIMPLEX,
                0.45, (0, 255, 255), 1)
hdr = np.zeros((32, full.shape[1], 3), np.uint8)
cv2.putText(hdr, f"USER SCREENSHOT {W}x{H}   cyan grid=10px, labeled=50px",
            (8, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
out = np.vstack([hdr, full])
cv2.imwrite(str(FIG / "screenshot_grid.png"), out)
print(f"  -> screenshot_grid.png  {out.shape}")
print("=== DONE ===")
