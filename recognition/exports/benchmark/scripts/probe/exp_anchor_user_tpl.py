"""任务 A/D · 用户截图提模板 + 在原始帧里交叉验证。

★ 两个必须先记住的用户更正
-----------------------------
  蓝菱形 + 光柱  = **玩家自己**（强制居中）→ ❌ 排除，不是地标
  青圆盘        = **七天神像**（62 个固定点）→ ✅ 可用

所以上一轮我用"蓝菱形"当锚点模板做的验证 3（AUC 0.915）**整条作废**。

★ 读图注意
----------
截图路径含中文，Windows 上 cv2.imread 会失败，必须 np.fromfile + imdecode。
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
SHOT = RECOG / "exports" / "benchmark" / "屏幕截图 2026-10-02 142123.png"
FRAME_B = RECOG / "raw" / "frames" / "batch_20260929b" / "f00081.jpg"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" \
    / "landmark2"
FIG.mkdir(parents=True, exist_ok=True)


def imread_u(p):
    """支持中文路径的 imread。"""
    return cv2.imdecode(np.fromfile(str(p), dtype=np.uint8),
                        cv2.IMREAD_UNCHANGED)


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


mm = _load("minimap_mask", PERC / "minimap_mask.py")
cfg = mm.load_circle_cfg()

shot = imread_u(SHOT)
print(f"截图 {SHOT.name}  shape={shot.shape}  dtype={shot.dtype}")
if shot.ndim == 3 and shot.shape[2] == 4:
    alpha = shot[:, :, 3]
    ys, xs = np.where(alpha > 128)
    print(f"  自带 alpha: 有效像素 {alpha.size and (alpha>128).sum()}  "
          f"bbox x[{xs.min()},{xs.max()}] y[{ys.min()},{ys.max()}]")
    shot = shot[:, :, :3]
shot = shot[:, :, :3] if shot.ndim == 3 else cv2.cvtColor(shot, cv2.COLOR_GRAY2BGR)
H, W = shot.shape[:2]
print(f"  RGB {W}x{H}")

# ---- 手工定位：截图是 200x200 左右的特写，图标居中
# 用"最暗的轮廓"找图标边界：图标有很粗的深色描边
g = cv2.cvtColor(shot, cv2.COLOR_BGR2GRAY)
dark = (g < 120).astype(np.uint8)
dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
n, lab, st, ct = cv2.connectedComponentsWithStats(dark, 8)
best = None
for j in range(1, n):
    x, y, w, h, a = st[j]
    if a < 200:
        continue
    if best is None or a > best[4]:
        best = (x, y, w, h, a)
if best is None:
    print("  !! 没找到深色轮廓，退回中心裁剪")
    bx0, by0, bw, bh = W // 2 - 45, H // 2 - 55, 90, 110
else:
    bx0, by0, bw, bh, ba = best
    print(f"  最暗轮廓 bbox x[{bx0},{bx0+bw}) y[{by0},{by0+bh})  面积 {ba}")

# 手动精确定位：先看整图 + 网格，再按目视框定。
# 自动"最暗轮廓"容易把图标 + 残余背景一起框进来（上一版 109x71 就是）。
# 策略：用带 alpha 的原图 + 多阈值轮廓 + 面积排序，人工复核后写死。
Z = 8
full = cv2.resize(shot, (shot.shape[1] * Z, shot.shape[0] * Z),
                  interpolation=cv2.INTER_NEAREST)
for k in range(0, full.shape[1], 10 * Z):
    cv2.line(full, (k, 0), (k, full.shape[0]),
             (0, 255, 255) if k % (50 * Z) == 0 else (90, 90, 90), 1)
for k in range(0, full.shape[0], 10 * Z):
    cv2.line(full, (0, k), (full.shape[1], k),
             (0, 255, 255) if k % (50 * Z) == 0 else (90, 90, 90), 1)
for k in range(0, full.shape[1], 50 * Z):
    cv2.putText(full, str(k // Z), (k + 2, 16), cv2.FONT_HERSHEY_SIMPLEX,
                0.4, (0, 255, 255), 1)
for k in range(50 * Z, full.shape[0], 50 * Z):
    cv2.putText(full, str(k // Z), (2, k - 4), cv2.FONT_HERSHEY_SIMPLEX,
                0.4, (0, 255, 255), 1)
hdr = np.zeros((30, full.shape[1], 3), np.uint8)
cv2.putText(hdr, "USER SCREENSHOT 216x246  (cyan grid=10px, labeled=50px)",
            (6, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
cv2.imwrite(str(FIG / "screenshot_grid.png"), np.vstack([hdr, full]))
print(f"  -> screenshot_grid.png  (原图 216x246，用于人工框定)")

Z = 10
big = cv2.resize(TPL, (TPL.shape[1] * Z, TPL.shape[0] * Z),
                 interpolation=cv2.INTER_NEAREST)
for k in range(0, big.shape[1], 5 * Z):
    cv2.line(big, (k, 0), (k, big.shape[0]), (0, 255, 255), 1)
for k in range(0, big.shape[0], 5 * Z):
    cv2.line(big, (0, k), (big.shape[1], k), (0, 255, 255), 1)
_SIDE = 260
pad = np.zeros((big.shape[0], _SIDE, 3), np.uint8)
cv2.putText(pad, "template: %dx%d" % (TPL.shape[1], TPL.shape[0]),
            (8, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
cv2.putText(pad, "cyan grid = 5px", (8, 58), cv2.FONT_HERSHEY_SIMPLEX,
            0.45, (200, 200, 200), 1)
cv2.putText(pad, "x10 zoom", (8, 82), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
            (200, 200, 200), 1)
row1 = np.hstack([big, np.zeros((big.shape[0], 10, 3), np.uint8), pad])
bar = np.zeros((46, row1.shape[1], 3), np.uint8)
cv2.putText(bar, "user screenshot anchor template", (6, 20),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
cv2.putText(bar, "dark-outline bbox, +2px pad", (6, 40),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)
cv2.imwrite(str(FIG / "template_from_screenshot.png"),
            np.vstack([bar, row1]))
print(f"  -> template_from_screenshot.png  {np.vstack([bar,row1]).shape}")


# ---- 在 f00081.jpg 的小地图里找它
fb = cv2.imread(str(FRAME_B))
mini, mask = mm.make_minimap_roi(fb, cfg)
mg = cv2.cvtColor(mini, cv2.COLOR_BGR2GRAY).astype(np.float32)
tg = cv2.cvtColor(TPL, cv2.COLOR_BGR2GRAY).astype(np.float32)
res = cv2.matchTemplate(mg, tg, cv2.TM_CCOEFF_NORMED)
rh, rw = res.shape
valid = np.where(mask[:rh, :rw] > 0, res, -1.0)
yy, xx = np.ogrid[:rh, :rw]
CCX = float(cfg["cx"]) * fb.shape[1] - mm.minimap_rect(fb, cfg)[0]
CCY = float(cfg["cy"]) * fb.shape[0] - mm.minimap_rect(fb, cfg)[1]
r_from_c = np.hypot(xx + TPL.shape[1] // 2 - CCX, yy + TPL.shape[0] // 2 - CCY)
print(f"\n  f00081 小地图 {mini.shape[1]}x{mini.shape[0]}  圆心({CCX:.1f},{CCY:.1f})")
print(f"  NCC 分数: max {valid.max():.3f} @ 距圆心 "
      f"{r_from_c.ravel()[int(np.argmax(valid))]:.0f}px")
print("  分数 >=0.6 的位置（按分数排序，前 10）:")
k = int(np.argmax(valid))
hits = []
for _ in range(4000):
    if valid[k] < 0.6:
        break
    hits.append((float(valid[k]), int(k % rw) + TPL.shape[1] // 2,
                 int(k // rw) + TPL.shape[0] // 2,
                 float(r_from_c.ravel()[k])))
    ny, nx = np.unravel_index(int(np.argmax(np.where(
        np.arange(valid.size).reshape(valid.shape) != k, valid, -1))), valid.shape)
    valid[k] = -1
    k = int(np.ravel_multi_index((ny, nx), valid.shape))
for s_, x_, y_, r_ in sorted(hits, reverse=True)[:10]:
    tag = "  <== 玩家自己?" if r_ < 25 else ""
    print(f"    ncc={s_:.3f}  ({x_:>3},{y_:>3})  距圆心 {r_:5.1f}px{tag}")

vis = cv2.resize(mini, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
for s_, x_, y_, r_ in sorted(hits, reverse=True)[:3]:
    cv2.rectangle(vis, ((x_ - TPL.shape[1] // 2) * 3,
                        (y_ - TPL.shape[0] // 2) * 3),
                  ((x_ + TPL.shape[1] // 2) * 3,
                   (y_ + TPL.shape[0] // 2) * 3), (0, 0, 255), 2)
    cv2.putText(vis, f"{s_:.2f}", ((x_ - 12) * 3, (y_ - 30) * 3),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
cv2.circle(vis, (int(CCX * 3), int(CCY * 3)), 14, (255, 0, 255), 3)
cv2.putText(vis, "player", (int(CCX * 3) + 18, int(CCY * 3) + 6),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 2)
hdr = np.zeros((30, vis.shape[1], 3), np.uint8)
cv2.putText(hdr, "batch_20260929b/f00081.jpg  minimap ROI  |  red = "
                 "user-anchor template hits, magenta = player",
            (6, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
cv2.imwrite(str(FIG / "verify_in_f00081.png"), np.vstack([hdr, vis]))
print(f"  -> verify_in_f00081.png")
print("=== DONE ===")
