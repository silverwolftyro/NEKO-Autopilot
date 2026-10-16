"""修正版：把大地图帧的城区块裁对，再 vs 游戏小地图。

**上一轮为什么失败**
用"画面中心 768x768"当大地图查询块 —— 但那一块落在水面/山地上，
不在城区。城区在画面区坐标约 (1295, 1104)。

**已经确定的事实（本轮）**
* 大地图 ↔ P0 底图：**同域**，f00167 对齐 235 内点，
  尺度 **1.121 m/px**，旋转 −0.03°（几乎正交，无畸变）
* 大地图视野 1964 m 宽，城区在画面上约 893 px 直径
* 小地图 261 px —— 只要小地图也是 ~1.12 m/px，尺度就几乎完全一致

**本轮要测的**
用对齐结果精确定位城区，裁出各种大小的块，逐个 vs 小地图，
看能不能在内点上分离出信号。同时补 SIFT 对照和 P0 城区块对照。
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
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

PERC = (Path(PLUGIN_DIR.parent)
        / "genshin_game_buddy" / "autopilot" / "perception")
RECOG = Path(RECOG_ROOT)
P0 = RECOG / "data" / "mihoyo_maps" / "teyvat_P0.png"
DB = RECOG / "raw" / "frames" / "session_20261001_b"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" \
    / "bigmap"
FIG.mkdir(parents=True, exist_ok=True)


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


mm = _load("minimap_mask", PERC / "minimap_mask.py")
cfg = mm.load_circle_cfg()
ct = _load("coord_transform",
           RECOG / "exports" / "benchmark" / "scripts" / "misc"
           / "coord_transform.py")
ids = sorted(DB.glob("*.jpg"))

MAP_ROI = (0.000, 0.055, 0.760, 0.985)
BIG_FRAMES = [120, 167]
MINI_FRAMES = [20, 30, 40, 50, 60]
BF = cv2.BFMatcher(cv2.NORM_HAMMING)
BF2 = cv2.BFMatcher(cv2.NORM_L2)
OQ = cv2.ORB_create(nfeatures=6000, nlevels=10, scaleFactor=1.2, fastThreshold=5)
OMINI = cv2.ORB_create(nfeatures=3000, nlevels=8, scaleFactor=1.2,
                       fastThreshold=5)
SIFT = cv2.SIFT_create(nfeatures=8000)

L1 = cv2.imread(str(P0), cv2.IMREAD_REDUCED_COLOR_2)          # 2 m/px
CX1, CY1 = int(18830 / 2) - 1150, int(7651 / 2) - 950
SUB = L1[CY1:CY1 + 1900, CX1:CX1 + 2300].copy()
sk, sd_ = OQ.detectAndCompute(SUB, None)
sp = np.array([k.pt for k in sk], np.float32)
TX, TY = 19141 / 2 - CX1, 7655 / 2 - CY1                     # 城区在子图坐标
print(f"P0 枫丹廷子图 {SUB.shape}  城区@({TX:.0f},{TY:.0f})")


def map_region(im):
    H, W = im.shape[:2]
    x0, y0, x1, y1 = MAP_ROI
    return im[int(y0 * H):int(y1 * H), int(x0 * W):int(x1 * W)].copy()


def align(reg):
    g = cv2.cvtColor(reg, cv2.COLOR_BGR2GRAY)
    kq, dq = OQ.detectAndCompute(g, None)
    good = [m for m, n2 in BF.knnMatch(dq, sd_, k=2)
            if m.distance < 0.78 * n2.distance]
    src = np.float32([kq[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([sp[m.trainIdx] for m in good]).reshape(-1, 1, 2)
    A, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC,
                                         ransacReprojThreshold=2.5,
                                         maxIters=30000)
    return A, int(inl.sum()) if inl is not None else 0


# ---------------- 定位城区在大地图画面上的位置
print("\n" + "=" * 96)
print("1. 对齐 + 城区定位")
print("=" * 96)
info = {}
for f in BIG_FRAMES:
    im = cv2.imread(str(ids[f - 1]))
    reg = map_region(im)
    A, n = align(reg)
    bx = A[0, 0] * TX + A[0, 1] * TY + A[0, 2]
    by = A[1, 0] * TX + A[1, 1] * TY + A[1, 2]
    sc = math.hypot(A[0, 0], A[1, 0]) * 2.0
    maxh = int(min(bx - 6, reg.shape[1] - bx - 6, by - 6, reg.shape[0] - by - 6))
    info[f] = dict(reg=reg, A=A, inl=n, bx=bx, by=by, sc=sc, maxh=maxh)
    print(f"  f{f:05d}  对齐 inl={n:>4}  尺度={sc:.3f} m/px  "
          f"城区@({bx:.0f},{by:.0f})  画面区 {reg.shape[1]}x{reg.shape[0]}  "
          f"最大可用半宽 {maxh} ({2*maxh*sc:.0f} m 见方)")

# ---------------- 小地图查询
print("\n" + "=" * 96)
print("2. 大地图城区块 vs 游戏小地图（ORB / SIFT）")
print("=" * 96)
for mf in MINI_FRAMES:
    mini, msk = mm.make_minimap_roi(cv2.imread(str(ids[mf - 1])), cfg)
    g = cv2.cvtColor(mini, cv2.COLOR_BGR2GRAY)
    mk, md = OMINI.detectAndCompute(g, msk)
    smk, smd = SIFT.detectAndCompute(cv2.resize(
        mini, (512, 512), interpolation=cv2.INTER_CUBIC), None)
    for f, d in info.items():
        reg, bx, by, sc, mh = d["reg"], d["bx"], d["by"], d["sc"], d["maxh"]
        for half in sorted({128, 192, mh}, reverse=True):
            if half < 64 or half > mh:
                continue
            crop = reg[int(by - half):int(by + half),
                       int(bx - half):int(bx + half)]
            if crop.shape[0] < 128:
                continue
            # --- ORB
            ck, cd = OQ.detectAndCompute(cv2.cvtColor(crop,
                                                      cv2.COLOR_BGR2GRAY), None)
            g_orb, i_orb = 0, 0
            if cd is not None and len(cd) >= 8:
                good = [m for m, n2 in BF.knnMatch(md, cd, k=2)
                        if m.distance < 0.78 * n2.distance]
                g_orb = len(good)
                if g_orb >= 4:
                    src = np.float32([mk[m.queryIdx].pt
                                      for m in good]).reshape(-1, 1, 2)
                    dsp = np.array([k.pt for k in ck], np.float32)
                    dst = np.float32([dsp[m.trainIdx]
                                      for m in good]).reshape(-1, 1, 2)
                    A2, inl = cv2.estimateAffinePartial2D(
                        src, dst, method=cv2.RANSAC,
                        ransacReprojThreshold=2.5, maxIters=20000)
                    i_orb = 0 if (A2 is None or inl is None) else int(inl.sum())
            # --- SIFT
            big = cv2.resize(crop, (512, 512), interpolation=cv2.INTER_CUBIC)
            bk, bd = SIFT.detectAndCompute(cv2.cvtColor(big, cv2.COLOR_BGR2GRAY),
                                           None)
            i_sift = 0
            if bd is not None and smd is not None:
                good2 = [m for m, n2 in BF2.knnMatch(smd, bd, k=2)
                         if m.distance < 0.75 * n2.distance]
                if len(good2) >= 4:
                    src = np.float32([smk[m.queryIdx].pt
                                      for m in good2]).reshape(-1, 1, 2)
                    dsp = np.array([k.pt for k in bk], np.float32)
                    dst = np.float32([dsp[m.trainIdx]
                                      for m in good2]).reshape(-1, 1, 2)
                    A3, inl = cv2.estimateAffinePartial2D(
                        src, dst, method=cv2.RANSAC,
                        ransacReprojThreshold=3.0, maxIters=20000)
                    i_sift = (0 if (A3 is None or inl is None)
                              else int(inl.sum()))
            print(f"  小地图f{mf:05d} vs 大地图f{f:05d} 半宽{half:>4}"
                  f"({2*half*sc:>5.0f}m) 块特征{len(ck) if cd is not None else 0:>5}"
                  f"  ORB good={g_orb:>4} inl={i_orb:>4}"
                  f"   SIFT inl={i_sift:>4}")
            if half == mh:
                cv2.imwrite(str(FIG / f"cmp_mini{mf:05d}_big{f:05d}.png"),
                            np.hstack([
                                cv2.resize(mini, (420, 420),
                                           interpolation=cv2.INTER_CUBIC),
                                cv2.resize(crop, (420, 420),
                                           interpolation=cv2.INTER_AREA)]))

# ---------------- 对照：P0 底图同区域 vs 小地图
print("\n" + "=" * 96)
print("3. 对照 · P0 底图同区域块 vs 同一批小地图")
print("=" * 96)
for mf in MINI_FRAMES[:2]:
    mini, msk = mm.make_minimap_roi(cv2.imread(str(ids[mf - 1])), cfg)
    mk, md = OMINI.detectAndCompute(cv2.cvtColor(mini, cv2.COLOR_BGR2GRAY), msk)
    for half_px in (256, 512, 1024):          # P0 子图是 2 m/px
        h = int(half_px * 1.121 / 2.0)        # 按大地图同视野取
        if not (h <= TX < SUB.shape[1] - h and h <= TY < SUB.shape[0] - h):
            continue
        crop = SUB[int(TY - h):int(TY + h), int(TX - h):int(TX + h)]
        ck, cd = OQ.detectAndCompute(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY),
                                     None)
        g_o = i_o = 0
        if cd is not None and len(cd) >= 8:
            good = [m for m, n2 in BF.knnMatch(md, cd, k=2)
                    if m.distance < 0.78 * n2.distance]
            g_o = len(good)
            if g_o >= 4:
                src = np.float32([mk[m.queryIdx].pt
                                  for m in good]).reshape(-1, 1, 2)
                dsp = np.array([k.pt for k in ck], np.float32)
                dst = np.float32([dsp[m.trainIdx]
                                  for m in good]).reshape(-1, 1, 2)
                A2, inl = cv2.estimateAffinePartial2D(
                    src, dst, method=cv2.RANSAC, ransacReprojThreshold=2.5,
                    maxIters=20000)
                i_o = 0 if (A2 is None or inl is None) else int(inl.sum())
        print(f"  小地图f{mf:05d} vs P0城区块 {2*half_px}m  块特征{len(ck) if cd is not None else 0}"
              f"  good={g_o:>4} inl={i_o:>4}")
print("\n  参照：小地图 vs 小地图 = 2270 内点；"
      "米游社底图 vs 小地图 = 2 内点；大地图 vs P0 底图 = 235 内点")
print("=== DONE ===")
