"""时域中值法剥离静止 UI。

★ 为什么
-------
UI 元素在所有大世界帧里**位置不变**，而世界背景一直在动。
逐像素取中值 -> 静止的 UI 保留，运动的世界被抹平。
底图世界反而是**静止**的（转视角），所以中值图会留下世界；
但底图世界逐帧**平移**，中值后仍是平��噪点，不会形成锐利边缘。

★ 输出
------
右下角区域的 2 倍放大中值图，带 0.01 细网格 + 0.05 标签，用于目视读坐标。
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
FRAMES_B = RECOG / "raw" / "frames" / "batch_20260929b"
FRAMES_A = RECOG / "raw" / "frames" / "batch_20260929a"
OUT = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe"

WORLD = ["f00167", "f00168", "f00172", "f00174", "f00203", "f00205",
         "f00209", "f00245", "f00263", "f00267", "f00278", "f00279"]
W, H = 2304, 1440
ZOOM = 2


def load(fid):
    for d in (FRAMES_B, FRAMES_A):
        p = d / f"{fid}.jpg"
        if p.is_file():
            im = cv2.imread(str(p))
            if im is not None:
                return im
    return None


def dump(x0f, y0f, x1f, y1f, name, label_every=0.05, step=0.01):
    xs, xe = int(x0f * W), int(x1f * W)
    ys, ye = int(y0f * H), int(y1f * H)
    acc = []
    for fid in WORLD:
        im = load(fid)
        if im is not None:
            acc.append(im[ys:ye, xs:xe])
    if not acc:
        print("  !! 无帧")
        return
    med = np.median(np.stack(acc), axis=0).astype(np.uint8)
    med = cv2.resize(med, None, fx=ZOOM, fy=ZOOM,
                     interpolation=cv2.INTER_NEAREST)
    h, w = med.shape[:2]
    d = cv2.cvtColor(med, cv2.COLOR_BGR2RGB)
    from PIL import Image, ImageDraw
    pil = Image.fromarray(d)
    dr = ImageDraw.Draw(pil, "RGBA")
    sx = (xe - xs) * ZOOM
    sy = (ye - ys) * ZOOM

    n = int(round((x1f - x0f) / step))
    for i in range(n + 1):
        gx = x0f + i * step
        x = int(i * step * (xe - xs) * ZOOM)
        major = abs(gx - round(gx / label_every) * label_every) < 1e-6
        col = (255, 70, 70, 220) if major else (90, 190, 255, 120)
        dr.line([(x, 0), (x, sy)], fill=col, width=2 if major else 1)
        if major:
            dr.text((x + 2, 2), f"{gx:.2f}", fill=(255, 255, 0, 255))
    m = int(round((y1f - y0f) / step))
    for i in range(m + 1):
        gy = y0f + i * step
        y = int(i * step * (ye - ys) * ZOOM)
        major = abs(gy - round(gy / label_every) * label_every) < 1e-6
        col = (255, 70, 70, 220) if major else (90, 190, 255, 120)
        dr.line([(0, y), (sx, y)], fill=col, width=2 if major else 1)
        if major:
            dr.text((2, y + 2), f"{gy:.2f}", fill=(0, 255, 255, 255))
    p = OUT / name
    pil.save(p)
    print(f"  {name}   x {x0f}~{x1f}  y {y0f}~{y1f}   "
          f"px {xe-xs}x{ye-ys} -> {w}x{h}   {len(acc)} 帧中值")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print("时域中值图：")
    dump(0.78, 0.85, 1.00, 1.00, "median_bottomright.png")
    dump(0.35, 0.90, 0.75, 1.00, "median_hpbar.png")
    dump(0.80, 0.70, 1.00, 0.92, "median_br_wide.png")
    print("=== DONE ===")


if __name__ == "__main__":
    main()
