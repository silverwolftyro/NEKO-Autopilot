"""管线对照实验：ORB 匹配器本身是好的吗？

局部窗口 + 已知真值下，游戏小地图只拿到 3~4 个内点。
在下"域差距无法跨越"的结论前，**必须先证明管线本身没坏**。

四组对照，全部用同一套 matcher / 同一块底图窗口：

  C0  底图裁块 vs 同一块底图窗口（同源同尺度）      —— 应当几乎全中
  C1  L1 层(1 m/px)裁块 vs 窗口(2 m/px)             —— 测跨尺度
  C2  灰度金字塔裁块 vs 彩色窗口                     —— 测灰度版底图
  C3  **游戏小地图** vs 彩色窗口                     —— 真实目标

C0/C1/C2 成功而 C3 失败 = 域差距确凿。
C0 也失败 = 匹配器/参数问题，跟域差距无关。
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
L0G = RECOG / "data" / "mihoyo_maps" / "P0_pyramid" / "L0"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" / "orb_e"
FIG.mkdir(parents=True, exist_ok=True)


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


mm = _load("minimap_mask", PERC / "minimap_mask.py")
cfg = mm.load_circle_cfg()

TRUTH = (19141, 7655)          # 传送点 841 枫丹廷区，L0 像素
HALF_M = 400
MW = 2.0
BF = cv2.BFMatcher(cv2.NORM_HAMMING)
ORB_Q = cv2.ORB_create(nfeatures=2000, nlevels=8, scaleFactor=1.2,
                       fastThreshold=5)
ORB_W = cv2.ORB_create(nfeatures=12000, nlevels=8, scaleFactor=1.2,
                       fastThreshold=5)

print("=" * 92)
print("0. 准备")
print("=" * 92)
t = time.perf_counter()
HALF = cv2.imread(str(P0), cv2.IMREAD_REDUCED_COLOR_2)      # 2 m/px 彩色
print(f"  彩色 1/2 层 {HALF.shape}  {time.perf_counter()-t:.1f}s")
ids = sorted((RECOG / "raw" / "frames" / "session_20261001_b").glob("*.jpg"))
im0 = cv2.imread(str(ids[214]))
H0, W0 = im0.shape[:2]
x0, y0, x1, y1 = mm.minimap_rect(im0, cfg)
CCX, CCY = float(cfg["cx"]) * W0 - x0, float(cfg["cy"]) * H0 - y0
print(f"  帧 {W0}x{H0}  小地图 {x1-x0}x{y1-y0}  圆心 ({CCX:.1f},{CCY:.1f})")

# 窗口（彩色，2 m/px）
r = int(HALF_M / MW)
cx, cy = TRUTH[0] / 2.0, TRUTH[1] / 2.0
wx0, wy0 = int(round(cx)) - r, int(round(cy)) - r
WIN = HALF[wy0:wy0 + 2 * r, wx0:wx0 + 2 * r].copy()
OCX, OCY = r, r                       # 窗口内真值位置
print(f"  窗口 {WIN.shape}  ({2*r*MW:.0f} m 见方)  "
      f"角点 L0=({wx0*2},{wy0*2})")
t = time.perf_counter()
wk, wd = ORB_W.detectAndCompute(WIN, None)
print(f"  窗口 ORB {len(wk)} 特征点  {time.perf_counter()-t:.1f}s")
wp = np.array([k.pt for k in wk], np.float32)


def match_report(img, mask=None, tag="", ratio=0.78, reproj=2.0):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    kq, dq = ORB_Q.detectAndCompute(g, mask)
    if dq is None or len(dq) < 8:
        print(f"  {tag:<42} 查询特征 {0 if dq is None else len(dq)}  -> 无查询")
        return dict(tag=tag, qkp=0, good=0, inl=0, err=float("nan"),
                    scale=float("nan"))
    good = [m for m, n2 in BF.knnMatch(dq, wd, k=2)
            if m.distance < ratio * n2.distance]
    if len(good) < 4:
        print(f"  {tag:<42} 查询 {len(kq):>4} good {len(good):>3} "
              f"-> good<4")
        return dict(tag=tag, qkp=len(kq), good=len(good), inl=0,
                    err=float("nan"), scale=float("nan"))
    src = np.float32([kq[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([wp[m.trainIdx] for m in good]).reshape(-1, 1, 2)
    A, inl = cv2.estimateAffinePartial2D(
        src, dst, method=cv2.RANSAC, ransacReprojThreshold=reproj,
        maxIters=20000)
    n = 0 if (A is None or inl is None) else int(inl.sum())
    if A is None:
        print(f"  {tag:<42} 查询 {len(kq):>4} good {len(good):>4} "
              f"inl 0  -> 仿射无解")
        return dict(tag=tag, qkp=len(kq), good=len(good), inl=0,
                    err=float("nan"), scale=float("nan"))
    dx = A[0, 0] * CCX + A[0, 1] * CCY + A[0, 2] - OCX
    dy = A[1, 0] * CCX + A[1, 1] * CCY + A[1, 2] - OCY
    err = math.hypot(dx, dy) * MW
    sc = math.hypot(A[0, 0], A[1, 0]) * MW
    print(f"  {tag:<42} 查询 {len(kq):>4} good {len(good):>4} inl {n:>4}"
          f"  误差 {err:7.1f} m  尺度 {sc:.2f} m/px")
    return dict(tag=tag, qkp=len(kq), good=len(good), inl=n, err=err, scale=sc)


print("=" * 92)
print("1. 对照实验")
print("=" * 92)
rows = []

# C0 同源同尺度：从窗口正中取 QP 边长的裁块（边长受窗口限制，别越界）
QP = 384
assert QP <= WIN.shape[0] and QP <= WIN.shape[1], \
    f"查询裁块 {QP} 超过窗口 {WIN.shape[1]}，会切片出负下标"
q = WIN[OCY - QP // 2:OCY + QP // 2, OCX - QP // 2:OCX + QP // 2].copy()
rows.append(match_report(q, tag=f"C0 同源同尺度 {QP}裁块@2m/px"))

# C1 跨尺度：同一个地物，取 2 m/px 的 QP/2 边长再放大回 QP
#    （等价于"底图 4 m/px 放大 2 倍"）
half = QP // 2
q4 = HALF[int(cy) - half // 2:int(cy) + half // 2,
          int(cx) - half // 2:int(cx) + half // 2].copy()
q4 = cv2.resize(q4, (QP, QP), interpolation=cv2.INTER_CUBIC)
print(f"  [C1 源裁块 {q4.shape}]")
rows.append(match_report(q4, tag=f"C1 跨尺度 QP/2裁块@2m/px 2x放大"))

# C2 灰度金字塔 L0 分块 vs 彩色窗口（只测"灰度库"这一个变量）
cr, cc = TRUTH[1] // 1536, TRUTH[0] // 3072
blk = cv2.imread(str(L0G / f"r{cr:02d}_c{cc:02d}.png"), cv2.IMREAD_GRAYSCALE)
print(f"  [C2 分块 r{cr:02d}_c{cc:02d} 读到={blk is not None} "
      f"shape={None if blk is None else blk.shape}]")
if blk is not None:
    # 块内坐标 -> 截 QP 边长
    bxc = TRUTH[0] - cc * 3072
    byc = TRUTH[1] - cr * 1536
    h = min(QP, blk.shape[0])
    w = min(QP, blk.shape[1])
    qg = blk[byc - h // 2:byc + h // 2, bxc - w // 2:bxc + w // 2].copy()
    print(f"  [C2 裁块 {qg.shape} 非黑占比 "
          f"{100*(qg>12).mean():.1f}%]")
    rows.append(match_report(qg, tag=f"C2 灰度金字塔L0裁块@{h}x{w}@1m/px"))
else:
    print("  C2 分块读不到，跳过")

# C2b 彩色窗口裁块转灰度（隔离"颜色"这一个变量）
rows.append(match_report(cv2.cvtColor(q, cv2.COLOR_BGR2GRAY),
                         tag="C2b C0 裁块转灰度（隔离颜色变量）"))

# C3 真实游戏小地图
for f in (215, 217, 219, 221, 223):
    im = cv2.imread(str(ids[f - 1]))
    mini, mask = mm.make_minimap_roi(im, cfg)
    rows.append(match_report(mini, mask, tag=f"C3 游戏小地图 b:f{f:05d}"))

print("\n" + "=" * 92)
print("2. 汇总")
print("=" * 92)
print(f"  {'组':<40}{'查询特征':>9}{'good':>7}{'内点':>7}{'误差m':>10}")
for r_ in rows:
    print(f"  {r_['tag']:<40}{r_['qkp']:>9}{r_['good']:>7}"
          f"{r_['inl']:>7}{r_['err']:>10.1f}")

with (FIG / "control_results.csv").open("w", encoding="utf-8-sig",
                                        newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["tag", "q_kp", "good", "inliers", "err_m", "scale_m_per_px"])
    for r_ in rows:
        w.writerow([r_["tag"], r_["qkp"], r_["good"], r_["inl"],
                    None if math.isnan(r_["err"]) else round(r_["err"], 1),
                    None if math.isnan(r_["scale"]) else round(r_["scale"], 4)])
print(f"\n  -> {FIG/'control_results.csv'}")
print("=== DONE ===")
