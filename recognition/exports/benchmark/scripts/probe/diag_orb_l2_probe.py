"""ORB 定位 · 实验 E 预探（L2 全图规模 + 真实小地图形态）。

只做三件测量，不下结论：
  1. L2 全图 ORB 特征点数 / 耗时 / 尺度设置
  2. session_a 世界段的真实小地图长什么样（直接看图）
  3. 小地图 ORB 特征点数 + 相对 L2 的粗略尺度差

输出图一律带帧号和坐标标注。
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
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

PERC = (Path(PLUGIN_DIR.parent)
        / "genshin_game_buddy" / "autopilot" / "perception")
RECOG = Path(RECOG_ROOT)
DA = RECOG / "raw" / "frames" / "session_20261001_a"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" / "orb_e"
FIG.mkdir(parents=True, exist_ok=True)


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


mm = _load("minimap_mask", PERC / "minimap_mask.py")
pl = _load("pyramid_loader", PERC / "pyramid_loader.py")

print(f"OpenCV {cv2.__version__}")

# ---------------- 1. L2 全图 ORB
print("\n" + "=" * 80)
print("1. L2 全图 ORB 规模")
print("=" * 80)
t0 = time.perf_counter()
l2 = pl.PyramidLoader(preload=()).get_level(2)
print(f"  L2 shape={l2.shape}  dtype={l2.dtype}  "
      f"灰度 min={l2.min()} max={l2.max()} mean={l2.mean():.1f}  "
      f"读盘 {time.perf_counter()-t0:.1f}s")

# 底图是否有内容：非纯黑像素占比
nz = float((l2 > 12).mean())
print(f"  非纯黑像素占比 = {nz*100:.1f}%")

for tag, kw in (
    ("A nfeat=20k  nl=8 sf=1.2", dict(nfeatures=20000, nlevels=8, scaleFactor=1.2)),
    ("B nfeat=60k  nl=10 sf=1.2", dict(nfeatures=60000, nlevels=10, scaleFactor=1.2)),
):
    t = time.perf_counter()
    o = cv2.ORB_create(fastThreshold=5, **kw)
    k, d = o.detectAndCompute(l2, None)
    print(f"  {tag:<26} kp={len(k):>7}  {time.perf_counter()-t:6.1f}s  "
          f"desc={None if d is None else d.shape}")

# 存一份 L2 缩略图给眼睛看
thumb = cv2.resize(l2, (1536, 768), interpolation=cv2.INTER_AREA)
cv2.imwrite(str(FIG / "L2_full_thumb_1536x768.png"), thumb)
print(f"  -> {FIG/'L2_full_thumb_1536x768.png'}")

# ---------------- 2. 真实小地图形态
print("\n" + "=" * 80)
print("2. session_a 世界段真实小地图")
print("=" * 80)
ids = sorted(DA.glob("*.jpg"))
print(f"  帧文件 {len(ids)} 个: {ids[0].name} .. {ids[-1].name}")

# session_a 12 段（已知世界段）→ 每段取一帧
WORLD_FRAMES = [30, 120, 200, 300, 350, 500, 700, 800, 1100, 1400, 1450, 1500]
oq = cv2.ORB_create(nfeatures=2000, fastThreshold=5)
rows = []
for fi in WORLD_FRAMES:
    if fi > len(ids):
        continue
    p = ids[fi - 1]
    im = cv2.imread(str(p))
    if im is None:
        print(f"  f{fi:05d} 读帧失败")
        continue
    crop, mask = mm.make_minimap_roi(im)
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    k, d = oq.detectAndCompute(g, mask)
    rows.append((fi, p.stem, crop.shape, len(k)))
    # 单张小地图大图（放大 2x 看得清）
    big = cv2.resize(crop, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_NEAREST)
    cv2.putText(big, f"{p.stem}  {crop.shape[1]}x{crop.shape[0]}  ORB kp={len(k)}",
                (8, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4)
    cv2.putText(big, f"{p.stem}  {crop.shape[1]}x{crop.shape[0]}  ORB kp={len(k)}",
                (8, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1)
    cv2.imwrite(str(FIG / f"minimap_{p.stem}_kp{len(k)}.png"), big)

for fi, stem, shp, n in rows:
    print(f"  {stem}  裁块 {shp[1]}x{shp[0]}  ORB kp={n}")

# 拼一张 12 联图
tiles = []
for fi in WORLD_FRAMES:
    p = ids[fi - 1]
    im = cv2.imread(str(p))
    crop, _ = mm.make_minimap_roi(im)
    t = cv2.resize(crop, (256, 256), interpolation=cv2.INTER_NEAREST)
    cv2.putText(t, p.stem, (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3)
    cv2.putText(t, p.stem, (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    tiles.append(t)
while len(tiles) % 4:
    tiles.append(np.zeros((256, 256, 3), np.uint8))
grid = np.vstack([np.hstack(tiles[i:i + 4]) for i in range(0, len(tiles), 4)])
cv2.imwrite(str(FIG / "minimap_grid_session_a.png"), grid)
print(f"  -> {FIG/'minimap_grid_session_a.png'}")

print("\n=== DONE ===")
