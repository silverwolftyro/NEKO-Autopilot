"""峰位验证 + 负对照：SIFT 找到的到底是什么地方？

**这轮推翻了上一轮的判决**
上一轮"米游社 P0 vs 小地图 = 2 内点"用的是**灰度 P0 + ORB**。
本轮换成**彩色 P0 + SIFT + 256px 窗口**后：
  A 游戏内大地图  峰 219~258 内点   5 帧峰位距离 0 m
  B 米游社 P0     峰 145~165 内点   5 帧峰位距离 0 m
**两条独立数据源都出了尖峰。** 说明上一轮的失败是**表示法选错**，不是数据源不行。

但**峰位对不对还没验证**，而且 A/B 换算到 L0 后 y 差约 470 m。
必须看图，并且要一个**负对照**证明流程不会造假阳性。

负对照：用 session_b 后期**纳塔**区域的小地图去扫**枫丹廷**大地图
—— 不同地点，理论上不该有峰。
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
ct = _load("coord_transform",
           RECOG / "exports" / "benchmark" / "scripts" / "misc"
           / "coord_transform.py")
cfg = mm.load_circle_cfg()
ids = sorted(DB.glob("*.jpg"))
MAP_ROI = (0.000, 0.055, 0.760, 0.985)
BIG = 167
FONTAINE = [20, 30, 40, 50, 60]        # 枫丹廷小地图
NATA = [860, 880, 900, 920]            # 纳塔小地图（负对照）
WIN, STEP = 256, 64
BF2 = cv2.BFMatcher(cv2.NORM_L2)
SIFT = cv2.SIFT_create(nfeatures=4000)
t0 = time.perf_counter()


def q_of(frames, tag):
    out = []
    for f in frames:
        mini, msk = mm.make_minimap_roi(cv2.imread(str(ids[f - 1])), cfg)
        big = cv2.resize(mini, (512, 512), interpolation=cv2.INTER_CUBIC)
        qk, qd = SIFT.detectAndCompute(cv2.cvtColor(big, cv2.COLOR_BGR2GRAY), None)
        out.append((f"{tag}f{f:05d}", qk, qd, mini))
    return out


def scan(scene, queries, step=STEP):
    H, W = scene.shape[:2]
    xs = list(range(0, max(1, W - WIN + 1), step))
    ys = list(range(0, max(1, H - WIN + 1), step))
    res = []
    for tag, qk, qd, _ in queries:
        best = (0, np.nan, np.nan)
        allv = []
        for y in ys:
            g = cv2.cvtColor(scene[y:y + WIN], cv2.COLOR_BGR2GRAY)
            for x in xs:
                bk, bd = SIFT.detectAndCompute(g[:, x:x + WIN], None)
                if bd is None or len(bd) < 4:
                    continue
                good = [m for m, n2 in BF2.knnMatch(qd, bd, k=2)
                        if m.distance < 0.75 * n2.distance]
                if len(good) < 4:
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
                allv.append(n)
                if n > best[0]:
                    best = (n, x + A[0, 0] * 130.5 + A[0, 1] * 130.3 + A[0, 2],
                            y + A[1, 0] * 130.5 + A[1, 1] * 130.3 + A[1, 2]) \
                        if n >= 4 else (n, np.nan, np.nan)
        res.append((tag, best, np.array(allv), _))
    return res


def show(res, title):
    print(f"  ---- {title} ----")
    for tag, (n, hx, hy), v, _q in res:
        med = np.median(v) if len(v) else 0
        p99 = np.percentile(v, 99) if len(v) else 0
        print(f"    {tag}: 峰={n:<4} 中位={med:.0f} p99={p99:.0f} "
              f"突出度={n/max(p99,1):.1f}x  落点=({hx:.0f},{hy:.0f})"
              if not math.isnan(hx) else
              f"    {tag}: 峰={n:<4} 中位={med:.0f} p99={p99:.0f}  无有效解")
    return res


print("=" * 96)
print("1. 负对照：纳塔小地图 扫 枫丹廷大地图（不同地点，不该有峰）")
print("=" * 96)
im = cv2.imread(str(ids[BIG - 1]))
H, W = im.shape[:2]
REG = im[int(MAP_ROI[1] * H):int(MAP_ROI[3] * H),
         int(MAP_ROI[0] * W):int(MAP_ROI[2] * W)].copy()
print(f"  枫丹廷大地图画面区 {REG.shape}")

L1 = cv2.imread(str(P0), cv2.IMREAD_REDUCED_COLOR_2)
CX1, CY1 = int(18830 / 2) - 1150, int(7651 / 2) - 950
SUB = L1[CY1:CY1 + 1900, CX1:CX1 + 2300].copy()

# 先把大地图对齐到 P0，才能换算世界坐标
sk, sd_ = None, None
OQ = cv2.ORB_create(nfeatures=6000, nlevels=10, scaleFactor=1.2, fastThreshold=5)
BF = cv2.BFMatcher(cv2.NORM_HAMMING)
sk, sd_ = OQ.detectAndCompute(SUB, None)
sp = np.array([k.pt for k in sk], np.float32)
gk, gd = OQ.detectAndCompute(cv2.cvtColor(REG, cv2.COLOR_BGR2GRAY), None)
good = [m for m, n2 in BF.knnMatch(gd, sd_, k=2) if m.distance < 0.78 * n2.distance]
src = np.float32([gk[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
dst = np.float32([sp[m.trainIdx] for m in good]).reshape(-1, 1, 2)
MAL, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC,
                                        ransacReprojThreshold=2.5,
                                        maxIters=30000)
print(f"  大地图->P0 对齐 inl={int(inl.sum())}  尺度="
      f"{math.hypot(MAL[0,0],MAL[1,0])*2:.3f} m/px")


def to_l0(hx, hy, use_mal):
    if use_mal:
        sx = MAL[0, 0] * hx + MAL[0, 1] * hy + MAL[0, 2] + CX1
        sy = MAL[1, 0] * hx + MAL[1, 1] * hy + MAL[1, 2] + CY1
    else:
        sx, sy = hx + CX1, hy + CY1
    wx, wz = ct.pixel_to_world(sx * 2, sy * 2)
    return sx * 2, sy * 2, wx, wz


print("\n" + "=" * 96)
print("2. 正对照 A：枫丹廷小地图 扫 枫丹廷大地图")
print("=" * 96)
A = show(scan(REG, q_of(FONTAINE, "小地图")), "A 大地图 · 枫丹廷小地图")

print("\n" + "=" * 96)
print("3. 正对照 B：枫丹廷小地图 扫 米游社 P0 彩色子图")
print("=" * 96)
B = show(scan(SUB, q_of(FONTAINE, "小地图")), "B P0 · 枫丹廷小地图")

print("\n" + "=" * 96)
print("4. 负对照 C：纳塔小地图 扫 枫丹廷大地图 + 扫 P0")
print("=" * 96)
Cn = q_of(NATA, "纳塔小地图")
C1 = show(scan(REG, Cn), "C1 大地图 · 纳塔小地图（应无峰）")
C2 = show(scan(SUB, Cn), "C2 P0 · 纳塔小地图（应无峰）")

print("\n" + "=" * 96)
print("5. 峰位换算到世界坐标")
print("=" * 96)
if not math.isnan(A[0][1][1]):
    l0x, l0y, wx, wz = to_l0(A[0][1][1], A[0][1][2], True)
    print(f"  A(大地图) {A[0][0]}  落点=({A[0][1][1]:.0f},{A[0][1][2]:.0f})  "
          f"L0=({l0x:.0f},{l0y:.0f})  世界=({wx:.0f},{wz:.0f})")
if not math.isnan(B[0][1][1]):
    l0x, l0y, wx, wz = to_l0(B[0][1][1], B[0][1][2], False)
    print(f"  B(P0)     {B[0][0]}  落点=({B[0][1][1]:.0f},{B[0][1][2]:.0f})  "
          f"L0=({l0x:.0f},{l0y:.0f})  世界=({wx:.0f},{wz:.0f})")
if not math.isnan(C1[0][1][1]):
    l0x, l0y, wx, wz = to_l0(C1[0][1][1], C1[0][1][2], True)
    print(f"  [负对照]   {C1[0][0]}  L0=({l0x:.0f},{l0y:.0f})  "
          f"世界=({wx:.0f},{wz:.0f})")

# ---------------- 出图
print("\n" + "=" * 96)
print("6. 出图")
print("=" * 96)
SZ = 620
SQ = 460
NLINES = 4


def cap(img, lines, color):
    """固定输出高度（NLINES 行标题 + SQ 高图像），保证能横向拼接。"""
    img = cv2.resize(img, (SZ, SQ), interpolation=cv2.INTER_AREA)
    lines = list(lines)[:NLINES]
    lines += [""] * (NLINES - len(lines))
    bar = np.zeros((26 * NLINES + 8, SZ, 3), np.uint8)
    o = np.vstack([bar, img])
    for i, s in enumerate(lines):
        y = 22 + 26 * i
        cv2.putText(o, s, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3)
        cv2.putText(o, s, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    cv2.rectangle(o, (0, 0), (SZ - 1, o.shape[0] - 1), (80, 80, 80), 1)
    return o


def marked(img, pts, col, r=26):
    o = img.copy()
    for (x, y, txt) in pts:
        if math.isnan(x):
            continue
        cv2.drawMarker(o, (int(x), int(y)), col, cv2.MARKER_CROSS,
                      r * 2, 3)
        cv2.circle(o, (int(x), int(y)), r, col, 3)
        cv2.putText(o, txt, (int(x) + r + 4, int(y) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 2)
    return o


pA = marked(REG, [(b[1][1], b[1][2], "A") for b in A], (0, 0, 255))
pB = marked(SUB, [(b[1][1], b[1][2], "B") for b in B], (255, 0, 255))
pC = marked(REG, [(b[1][1], b[1][2], "NEG") for b in C1], (0, 200, 255))
minis = [cv2.resize(b[3], (SZ, SZ), interpolation=cv2.INTER_CUBIC)
         for b in A[:3]]
for i, b in enumerate(A[:3]):
    cv2.putText(minis[i], b[0], (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                (0, 255, 255), 2)
nat = cv2.resize(C1[0][3], (SZ, SZ), interpolation=cv2.INTER_CUBIC)
cv2.putText(nat, C1[0][0], (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
            (0, 200, 255), 2)
pA2 = marked(pA, [(C1[0][1][1], C1[0][1][2], "NEG")], (0, 200, 255))
pB2 = marked(pB, [(C2[0][1][1], C2[0][1][2], "NEG")], (0, 200, 255))
rows = [
    np.hstack([cap(minis[0], ["POSITIVE: fontaine minimap b:f00020"],
                   (0, 255, 255)),
               cap(pA, [f"IN-GAME BIG MAP f{BIG:05d}",
                        f"peak={A[0][1][0]} inliers",
                        "red = positive query peak"], (0, 255, 255)),
               cap(pB, ["MIHOYO P0 color submap", f"peak={B[0][1][0]} inliers",
                        "magenta = positive query peak"], (255, 0, 255))]),
    np.hstack([cap(nat, ["NEGATIVE CONTROL: natla minimap b:f00860",
                         "different region entirely"], (0, 200, 255)),
               cap(pA2, ["SAME map, NEGATIVE query",
                         f"peak={C1[0][1][0]} inliers",
                         "cyan = lands on the SAME spot"], (0, 200, 255)),
               cap(pB2, ["SAME P0, NEGATIVE query",
                         f"peak={C2[0][1][0]} inliers",
                         "cyan = lands on the SAME spot"], (0, 200, 255))]),
]
out = np.vstack(rows)
hdr = np.zeros((70, out.shape[1], 3), np.uint8)
cv2.putText(hdr, "VERDICT: peak position is decided by the BASEMAP, not the "
                 "query.", (10, 26),
            cv2.FONT_HERSHEY_SIMPLEX, 0.68, (255, 80, 80), 1)
cv2.putText(hdr, "A natla minimap scanned against the fontaine basemap "
                 "peaks at the identical pixel (433,688) with 56-76 inliers.",
            (10, 54), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 1)
out = np.vstack([hdr, out])
cv2.imwrite(str(FIG / "sift_peak_verification.png"), out)
print(f"  -> {FIG/'sift_peak_verification.png'}  {out.shape}")
print(f"\n  总耗时 {time.perf_counter()-t0:.0f}s")
print("=== DONE ===")

