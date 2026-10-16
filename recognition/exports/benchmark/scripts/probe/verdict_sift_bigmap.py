"""判决验证：SIFT + 游戏内大地图底图，是不是真信号？

**目前观测**
大地图 f00167 上裁 287 m 见方的块，5 帧连续小地图的 SIFT 内点
**54 / 60 / 61 / 54 / 54**（ORB 同样条件只有 3~4，米游社 P0 块只有 2~3）。
噪声地板 2~5。看起来是 ~20 倍信噪比 —— 但**必须排除巧合**。

**判决设计**
把整个大地图画面区滑窗扫一遍，看三件事：
1. **峰突出度**：最佳位置的内点，比全图次高位置高多少？
   真定位 -> 单一尖峰；假阳性 -> 全图平坦噪声
2. **跨帧一致性**：5 帧的峰值位置是否重合在同一片区域
3. **同域对照**：同一套流程在米游社 P0 同区域块上跑，
   如果那边也是平的就说明流程本身在造假阳性

窗口 256x256 @1.12 m/px = 287 m 见方（与观测最优一致）。
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
ids = sorted(DB.glob("*.jpg"))
MAP_ROI = (0.000, 0.055, 0.760, 0.985)
BIG = 167
MINIS = [20, 30, 40, 50, 60]
WIN, STEP, MPP = 256, 64, 1.121          # 287 m 见方

BF2 = cv2.BFMatcher(cv2.NORM_L2)
SIFT = cv2.SIFT_create(nfeatures=4000)
t0 = time.perf_counter()


def map_region(im):
    H, W = im.shape[:2]
    x0, y0, x1, y1 = MAP_ROI
    return im[int(y0 * H):int(y1 * H), int(x0 * W):int(x1 * W)].copy()


def scan(scene, name, queries):
    """在 scene 上滑窗，返回每帧的全部 (内点数, 落点)。"""
    H, W = scene.shape[:2]
    xs = list(range(0, max(1, W - WIN + 1), STEP))
    ys = list(range(0, max(1, H - WIN + 1), STEP))
    print(f"  {name}: {W}x{H}  窗口{WIN} 步长{STEP} -> {len(xs)}x{len(ys)}"
          f" = {len(xs)*len(ys)} 个")
    per_frame = []
    for qtag, qk, qd in queries:
        hits = []
        for y in ys:
            strip = scene[y:y + WIN]
            g = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
            for x in xs:
                sub = g[:, x:x + WIN]
                bk, bd = SIFT.detectAndCompute(sub, None)
                if bd is None or len(bd) < 4:
                    continue
                good = [m for m, n2 in BF2.knnMatch(qd, bd, k=2)
                        if m.distance < 0.75 * n2.distance]
                if len(good) < 4:
                    hits.append((0, np.nan, np.nan))
                    continue
                src = np.float32([qk[m.queryIdx].pt
                                  for m in good]).reshape(-1, 1, 2)
                bp = np.array([k.pt for k in bk], np.float32)
                dst = np.float32([bp[m.trainIdx]
                                  for m in good]).reshape(-1, 1, 2)
                A, inl = cv2.estimateAffinePartial2D(
                    src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.0,
                    maxIters=8000)
                n = 0 if (A is None or inl is None) else int(inl.sum())
                if n >= 4:
                    hx = x + A[0, 0] * 130.5 + A[0, 1] * 130.3 + A[0, 2]
                    hy = y + A[1, 0] * 130.5 + A[1, 1] * 130.3 + A[1, 2]
                    hits.append((n, hx, hy))
                else:
                    hits.append((n, np.nan, np.nan))
        hits.sort(key=lambda z: -z[0])
        per_frame.append((qtag, hits))
        top = hits[:3]
        print(f"    {qtag}: Top3 " + "  ".join(
            f"inl={n}@({h:.0f},{v:.0f})" if not math.isnan(h)
            else f"inl={n}" for n, h, v in top))
    return per_frame


def report(per_frame, title):
    print(f"\n  ---- {title} ----")
    arrs, bests = [], []
    for qtag, hits in per_frame:
        v = np.array([h[0] for h in hits])
        arrs.append(v)
        bests.append(hits[0])
        top1 = hits[0][0]
        rest = np.array([h[0] for h in hits[1:]])
        p99 = np.percentile(rest, 99) if len(rest) else 0
        print(f"    {qtag}: 峰={top1:<4} 其余 p99={p99:.0f} "
              f"中位={np.median(v):.0f} 突出度={top1/max(p99,1):.1f}x")
    a = np.concatenate(arrs)
    print(f"    全图内点分布: 中位 {np.median(a):.0f}  p99 "
          f"{np.percentile(a,99):.0f}  max {a.max()}")
    ds = []
    for i in range(len(bests)):
        for j in range(i + 1, len(bests)):
            n1, h1, v1 = bests[i]
            n2, h2, v2 = bests[j]
            if not (math.isnan(h1) and math.isnan(h2)):
                ds.append(math.hypot(h1 - h2, v1 - v2) * MPP)
    if ds:
        print(f"    峰位两两距离: " + "  ".join(f"{d:.0f}m" for d in ds)
              + f"   最大 {max(ds):.0f} m")
        return max(ds)
    return None


# ---------------- 查询：小地图
queries = []
for f in MINIS:
    mini, msk = mm.make_minimap_roi(cv2.imread(str(ids[f - 1])), cfg)
    big = cv2.resize(mini, (512, 512), interpolation=cv2.INTER_CUBIC)
    qk, qd = SIFT.detectAndCompute(cv2.cvtColor(big, cv2.COLOR_BGR2GRAY), None)
    queries.append((f"小地图f{f:05d}", qk, qd))
print(f"小地图 SIFT 查询特征: "
      f"{[len(q[1]) for q in queries]}")

print("\n" + "=" * 96)
print("A · 扫描 游戏内大地图画面 f00167（真底图候选）")
print("=" * 96)
im = cv2.imread(str(ids[BIG - 1]))
reg = map_region(im)
pa = scan(reg, f"大地图 f{BIG:05d}", queries)
da = report(pa, "A · 游戏内大地图")

print("\n" + "=" * 96)
print("B · 对照 · 扫描 米游社 P0 同区域块（已知会失败）")
print("=" * 96)
L1 = cv2.imread(str(P0), cv2_IMREAD := cv2.IMREAD_REDUCED_COLOR_2)
CX1, CY1 = int(18830 / 2) - 1150, int(7651 / 2) - 950
TX, TY = 19141 / 2 - CX1, 7655 / 2 - CY1
sub = L1[CY1:CY1 + 1900, CX1:CX1 + 2300].copy()
# P0 是 2 m/px，换算成同视野：287m/2m = 144 px 窗口
PB = scan(sub, "P0 枫丹廷子图", queries)
report(PB, "B · 米游社 P0（对照）")

print("\n" + "=" * 96)
print("判决")
print("=" * 96)
print(f"  A 游戏内大地图 峰位最大间距 = {da:.0f} m" if da is not None else "  A 无峰")
print(f"  判据：峰位间距 < 300 m 且 峰突出度 > 3x  ->  SIFT + 大地图底图 可行")
print("=== DONE ===")
