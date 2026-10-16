"""锚点外观对比：f00081 vs session_a vs 用户模板。

★ 起因
------
用户模板在 f00081 上 ncc=0.78~0.92（命中），在 session_a 上只有 0.39~0.45
（找不到）。但 cross_dataset_compare.png 显示 **session_a 里明明也有锚点**
（蓝菱形，贴在神像旁边）。所以要查的是：**两个数据集的锚点长得不一样**。

对比三处的高饱和区（菱形）色相/饱和度/明度。
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
    / "landmark2"
FIG.mkdir(parents=True, exist_ok=True)


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


mm = _load("minimap_mask", PERC / "minimap_mask.py")
cfg = mm.load_circle_cfg()


def parts(p):
    im = cv2.imread(str(p))
    c, m = mm.make_minimap_roi(im, cfg)
    a0, b0, _, _ = mm.minimap_rect(im, cfg)
    return c, cfg["cx"] * im.shape[1] - a0, cfg["cy"] * im.shape[0] - b0


cA, _, _ = parts(RECOG / "raw" / "frames" / "batch_20260929b" / "f00081.jpg")
aA = cA[163 - 13:163 + 13, 110 - 12:110 + 12].copy()

ids = sorted((RECOG / "raw" / "frames"
              / "session_20261001_a").glob("*.jpg"))
cB, cxB, cyB = parts(ids[179])
aB = cB[122 - 13:122 + 13, 60 - 12:60 + 12].copy()

shot = cv2.imdecode(np.fromfile(
    str(RECOG / "exports" / "benchmark"
        / "屏幕截图 2026-10-02 142123.png"), dtype=np.uint8),
    cv2.IMREAD_COLOR)
aC = cv2.resize(shot, (int(shot.shape[1] * 0.12), int(shot.shape[0] * 0.12)),
                interpolation=cv2.INTER_AREA)


def stats(img, tag):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, w = img.shape[:2]
    cyy, cxx = h // 2, w // 2
    r = max(2, int(min(h, w) * 0.22))
    p = hsv[max(0, cyy - r):cyy + r, max(0, cxx - r):cxx + r]
    m = p[:, :, 1] > 90
    b = img[:, :, 0]
    g = img[:, :, 1]
    rd = img[:, :, 2]
    if m.sum() < 5:
        print(f"  {tag:<26} {w}x{h}  中心无高饱和像素")
        return
    print(f"  {tag:<26} {w:>2}x{h:<2}  菱形 HSV: H {np.median(p[:,:,0][m]):5.1f} "
          f" S {np.median(p[:,:,1][m]):5.1f}  V {np.median(p[:,:,2][m]):5.1f}"
          f"   像素 {m.sum():3d}")
    print(f"  {'':<26}          中心 BGR: "
          f"({np.median(b[cyy-r:cyy+r, cxx-r:cxx+r]):.0f},"
          f"{np.median(g[cyy-r:cyy+r, cxx-r:cxx+r]):.0f},"
          f"{np.median(rd[cyy-r:cyy+r, cxx-r:cxx+r]):.0f})")


print("锚点菱形区域颜色对比：")
stats(aA, "A f00081 (用户锚点)")
stats(aB, "B a:f00180 蓝菱形")
stats(aC, "C 用户模板 @0.12")

Z = 14
tiles = []
for img, lab in ((aA, "A f00081 anchor (ncc 0.78)"),
                 (aB, "B a:f00180 diamond"),
                 (aC, "C user template @0.12")):
    if img is None or img.size == 0:
        continue
    b = cv2.resize(img, (img.shape[1] * Z, img.shape[0] * Z),
                   interpolation=cv2.INTER_NEAREST)
    for k in range(0, b.shape[1], 5 * Z):
        cv2.line(b, (k, 0), (k, b.shape[0]), (0, 255, 255), 1)
    for k in range(0, b.shape[0], 5 * Z):
        cv2.line(b, (0, k), (b.shape[1], k), (0, 255, 255), 1)
    bar = np.zeros((26, b.shape[1], 3), np.uint8)
    cv2.putText(bar, lab, (4, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                (255, 255, 255), 1)
    tiles.append(np.vstack([bar, b]))
H = max(t.shape[0] for t in tiles)
tiles = [cv2.copyMakeBorder(t, 0, H - t.shape[0], 0, 0,
                            cv2.BORDER_CONSTANT) for t in tiles]
g = np.hstack(tiles)
hdr = np.zeros((32, g.shape[1], 3), np.uint8)
cv2.putText(hdr, "ANCHOR APPEARANCE  f00081 vs session_a vs user template  "
                 "(cyan grid = 5px, x14)", (8, 22), cv2.FONT_HERSHEY_SIMPLEX,
            0.55, (255, 255, 255), 1)
p = FIG / "anchor_appearance_compare.png"
cv2.imwrite(str(p), np.vstack([hdr, g]))
print(f"  -> {p.name}")
print("=== DONE ===")
