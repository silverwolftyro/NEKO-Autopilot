"""任务 3 · 补测 avatar / paimon_panel / paimon_button。

★ 帧来源
--------
派蒙中值：scene_labels.csv 里 scene_type=派蒙界面 的**全部** 9 帧
          （含 2 帧"换头像"变体，用于确认头像位置）
大世界中值：scene_type=大世界 的 45 帧 —— 派蒙按钮在菜单打开时是隐藏的，
          只能从大世界帧里找。

★ 出图带读数网格
---------------
0.01 细网格 + 0.05 主网格带标签，2 倍放大，可直接读归一化坐标。
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
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np
from PIL import Image, ImageDraw

RECOG = Path(RECOG_ROOT)
DAT = RECOG / "exports" / "benchmark" / "datasets"
LAB = DAT / "scene_labels.csv"
OUT = (RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe"
       / "roi_measure")
BATCH = {"a": RECOG / "raw" / "frames" / "batch_20260929a",
         "b": RECOG / "raw" / "frames" / "batch_20260929b"}
W, H = 2304, 1440
ZOOM = 2


def load(fid):
    tag, stem = fid.split(":")
    p = BATCH[tag] / f"{stem}.jpg"
    if not p.is_file():
        return None
    return cv2.imread(str(p))


def dump(med, x0f, y0f, x1f, y1f, name, title):
    xs, xe = int(x0f * W), int(x1f * W)
    ys, ye = int(y0f * H), int(y1f * H)
    crop = med[ys:ye, xs:xe]
    crop = cv2.resize(crop, None, fx=ZOOM, fy=ZOOM,
                      interpolation=cv2.INTER_NEAREST)
    pil = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil, "RGBA")
    cw = (xe - xs) * ZOOM
    ch = (ye - ys) * ZOOM
    n = int(round((x1f - x0f) / 0.01))
    for i in range(n + 1):
        gx = x0f + i * 0.01
        x = int(i * 0.01 * (xe - xs) * ZOOM)
        major = abs(gx - round(gx / 0.05) * 0.05) < 1e-6
        d.line([(x, 0), (x, ch)], fill=(255, 60, 60, 230) if major
               else (90, 190, 255, 120), width=2 if major else 1)
        if major:
            d.text((x + 2, 2), f"{gx:.2f}", fill=(255, 255, 0, 255))
    m = int(round((y1f - y0f) / 0.01))
    for i in range(m + 1):
        gy = y0f + i * 0.01
        y = int(i * 0.01 * (ye - ys) * ZOOM)
        major = abs(gy - round(gy / 0.05) * 0.05) < 1e-6
        d.line([(0, y), (cw, y)], fill=(255, 60, 60, 230) if major
               else (90, 190, 255, 120), width=2 if major else 1)
        if major:
            d.text((2, y + 2), f"{gy:.2f}", fill=(0, 255, 255, 255))
    d.text((6, ch - 22), title, fill=(255, 255, 255))
    p = OUT / name
    pil.save(p)
    print(f"  {name}   x {x0f}~{x1f} y {y0f}~{y1f}   "
          f"px {xe-xs}x{ye-ys} -> {pil.size}")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    groups = {}
    with LAB.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            groups.setdefault(r["scene_type"], []).append(r["frame_id"])
    paimon = groups.get("派蒙界面", [])
    world = groups.get("大世界", [])
    print(f"派蒙界面 {len(paimon)} 帧: {paimon}")
    print(f"大世界   {len(world)} 帧")

    mp = np.median(np.stack([load(f) for f in paimon
                             if load(f) is not None]), axis=0).astype(np.uint8)
    mw = np.median(np.stack([load(f) for f in world
                             if load(f) is not None]), axis=0).astype(np.uint8)
    Image.fromarray(cv2.cvtColor(cv2.resize(mp, (1152, 720)),
                                  cv2.COLOR_BGR2RGB)).save(OUT / "median_paimon_full.png")
    Image.fromarray(cv2.cvtColor(cv2.resize(mw, (1152, 720)),
                                  cv2.COLOR_BGR2RGB)).save(OUT / "median_world_full.png")
    print("\n整帧中值:")
    dump(mp, 0.00, 0.00, 0.55, 1.00, "paimon_left.png",
         f"paimon median n={len(paimon)}  x0~0.55")
    dump(mp, 0.00, 0.00, 0.25, 0.22, "paimon_topleft.png",
         "avatar zone")
    dump(mw, 0.60, 0.00, 1.00, 0.18, "world_topright.png",
         f"world median n={len(world)}  top-right (paimon button?)")
    print(f"\n输出目录: {OUT}")
    print("=== DONE ===")


if __name__ == "__main__":
    main()
