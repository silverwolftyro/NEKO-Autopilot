"""ORB 域差距判定 · 有真值的局部窗口搜索。

**为什么换实验**
全局 L2 库搜索 152 帧全零解。诊断出两个独立原因：
  (1) 架构：42 MPix 的底图最多只能挂 ~20 万描述子（OpenCV 这套
      knnMatch 再多就断言失败），而小地图本身就有 1700 个特征点。
      密度差 200 倍以上，**全局特征库这条路在原理上就不成立**。
  (2) 待定：游戏渲染小地图 和 米游社矢量底图 的**域差距**能不能跨。

本实验隔离掉 (1)，只测 (2)：把搜索窗口缩到真值附近，
窗口小到特征密度和小地图相当，**能不能找回来**。

**真值来源**
session_b 的传送 OCR 命中帧 -> 传送点 id -> tp.json 世界坐标
-> coord_transform -> 底图像素。

  f00127-f00139 -> 909  枫丹廷·灰河      L0 (18951, 7786)
  f00145-f00157 -> 906  枫丹廷·纳博内区  L0 (18815, 7779)
  f00163-f00211 -> 841  枫丹廷区          L0 (19141, 7655)
  f00235-f00367 -> 1010 伊黎耶林区        L0 (19864, 7992)

同时跑**灰度**窗口做对照 —— 上一轮发现金字塔把颜色丢了，
这里量化"丢颜色"到底值多少。
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
import json
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
DB = RECOG / "data" / "_ref_tp_sample.json"
DB_SESSION = RECOG / "raw" / "frames" / "session_20261001_b"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" / "orb_e"
FIG.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(RECOG / "exports" / "benchmark" / "scripts" / "misc"))
import coord_transform as ct          # noqa: E402


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


mm = _load("minimap_mask", PERC / "minimap_mask.py")
cfg = mm.load_circle_cfg()

# --------------------------------------------------------------- 真值
# 传送 OCR 命中帧本身是加载过渡帧（小地图被盖住，特征 0~6），
# **不能直接用**。真值锚点取"世界刚恢复的那一小段"——
# 传送契约保证玩家落点 = 传送点位置（tp.json 的 tranPosition）。
TP = {841: (19141, 7655, "枫丹廷区")}
# (起点, 终点, 真值传送点id, 标签)   —— 起点都是世界恢复的首帧
RANGES = [(211, 240, 841, "恢复后 3s 内（真值最硬）"),
          (211, 606, 841, "整段 39.5s（真值会随走动变松）")]

print("=" * 96)
print("0. 数据准备")
print("=" * 96)
t = time.perf_counter()
HALF = cv2.imread(str(P0), cv2.IMREAD_REDUCED_COLOR_2)      # 2 m/px 彩色
print(f"  彩色 1/2 层 {HALF.shape}  {time.perf_counter()-t:.1f}s")
MW = 2.0                                                     # m / px
BF = cv2.BFMatcher(cv2.NORM_HAMMING)
ORB_Q = cv2.ORB_create(nfeatures=2000, nlevels=8, scaleFactor=1.2,
                       fastThreshold=5)
ORB_W = cv2.ORB_create(nfeatures=8000, nlevels=8, scaleFactor=1.2,
                       fastThreshold=5)
ids = sorted(DB_SESSION.glob("*.jpg"))
print(f"  session_b {len(ids)} 帧")

# 圆心在小地图裁块内的位置（按**实际帧尺寸**算，不硬编码分辨率）
_f0 = cv2.imread(str(ids[0]))
H0, W0 = _f0.shape[:2]
xc0, yc0, x1_, y1_ = mm.minimap_rect(_f0, cfg)
CCX = float(cfg["cx"]) * W0 - xc0
CCY = float(cfg["cy"]) * H0 - yc0
print(f"  帧尺寸 {W0}x{H0}  小地图裁块 {x1_-xc0}x{y1_-yc0}"
      f"  圆心 ({CCX:.1f},{CCY:.1f})")


def window(center_l0, half_m, color=True):
    """以 L0 坐标为中心裁 half_m 半径的窗口。返回 (img, cx_h, cy_h)。"""
    cx, cy = center_l0[0] / 2.0, center_l0[1] / 2.0       # -> 2 m/px 层
    r = int(round(half_m / MW))
    H, W = HALF.shape[:2]
    x0, y0 = int(round(cx)) - r, int(round(cy)) - r
    x0c, y0c = max(0, x0), max(0, y0)
    x1c, y1c = min(W, x0 + 2 * r), min(H, y0 + 2 * r)
    sub = HALF[y0c:y1c, x0c:x1c]
    if not color:
        sub = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
    return sub, (x0c + r - x0), (y0c + r - y0)


def locate(mini, win, ratio=0.78, reproj=2.0):
    g = cv2.cvtColor(mini, cv2.COLOR_BGR2GRAY) if mini.ndim == 3 else mini
    kq, dq = ORB_Q.detectAndCompute(g, None)
    if dq is None or len(dq) < 8:
        return dict(qkp=0, good=0, inl=0, dx=np.nan, dy=np.nan, scale=np.nan)
    wk, wd = ORB_W.detectAndCompute(win, None)
    if wd is None or len(wd) < 8:
        return dict(qkp=len(kq), good=0, inl=0, dx=np.nan, dy=np.nan,
                    scale=np.nan)
    wp = np.array([k.pt for k in wk], np.float32)
    good = [m for m, n2 in BF.knnMatch(dq, wd, k=2)
            if m.distance < ratio * n2.distance]
    if len(good) < 4:
        return dict(qkp=len(kq), good=len(good), inl=0, dx=np.nan, dy=np.nan,
                    scale=np.nan)
    src = np.float32([kq[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([wp[m.trainIdx] for m in good]).reshape(-1, 1, 2)
    A, inl = cv2.estimateAffinePartial2D(
        src, dst, method=cv2.RANSAC, ransacReprojThreshold=reproj,
        maxIters=20000)

    if A is None or inl is None:
        return dict(qkp=len(kq), good=len(good), inl=0,
                    dx=np.nan, dy=np.nan, scale=np.nan)
    return dict(qkp=len(kq), good=len(good), inl=int(inl.sum()),
                dx=float(A[0, 0] * CCX + A[0, 1] * CCY + A[0, 2]),
                dy=float(A[1, 0] * CCX + A[1, 1] * CCY + A[1, 2]),
                scale=float(math.hypot(A[0, 0], A[1, 0])) * MW)


# --------------------------------------------------------------- 跑
HALF_M = 400          # 窗口半径（米）-> 800m x 800m
rows = []
print("\n" + "=" * 96)
print(f"1. 局部窗口搜索（半径 {HALF_M} m = {2*HALF_M} m 见方，彩色 2 m/px 底图）")
print("=" * 96)
for f0, f1, tid, label in RANGES:
    tl, ty, tname = TP[tid]
    step = max(1, (f1 - f0) // 11)
    picks = list(range(f0, f1 + 1, step))[:12]
    print(f"\n  --- 传送点 {tid} {tname}  真值 L0=({tl},{ty})  "
          f"| {label} | 取 {len(picks)} 帧 ---")
    win_c, ocx, ocy = window((tl, ty), HALF_M, color=True)
    win_g, _, _ = window((tl, ty), HALF_M, color=False)
    for f in picks:
        im = cv2.imread(str(ids[f - 1]))
        if im is None:
            continue
        mini, mask = mm.make_minimap_roi(im, cfg)
        gm = cv2.cvtColor(mini, cv2.COLOR_BGR2GRAY)
        nq = len(ORB_Q.detectAndCompute(gm, mask)[0])
        if nq < 400:
            print(f"    b:f{f:05d}  小地图特征 {nq:<5} 跳过(小地图不可见)")
            continue
        rc = locate(mini, win_c)
        rg = locate(mini, win_g)
        # 窗口局部坐标 -> 相对真值的偏移（米）
        ec = (math.hypot(rc["dx"] - ocx, rc["dy"] - ocy) * MW
              if rc["inl"] >= 6 else float("nan"))
        eg = (math.hypot(rg["dx"] - ocx, rg["dy"] - ocy) * MW
              if rg["inl"] >= 6 else float("nan"))
        rows.append(dict(tp=tid, frame=f, name=tname, qkp=nq,
                         c_inl=rc["inl"], c_err=ec, c_scale=rc["scale"],
                         c_dx=rc["dx"] - ocx, c_dy=rc["dy"] - ocy,
                         g_inl=rg["inl"], g_err=eg, tight=(f1 - f0 <= 40)))
        print(f"    b:f{f:05d}  qkp={nq:<5} "
              f"彩色 inl={rc['inl']:>4} 误差={ec:7.1f} m  |  "
              f"灰度 inl={rg['inl']:>4} 误差={eg:7.1f} m  "
              f"| 拟合尺度={rc['scale']:.2f} m/px")

# --------------------------------------------------------------- 汇总
print("\n" + "=" * 96)
print("2. 汇总")
print("=" * 96)
for key, lab in (("c", "彩色窗口"), ("g", "灰度窗口")):
    inls = np.array([r[f"{key}_inl"] for r in rows])
    errs = np.array([r[f"{key}_err"] for r in rows])
    ok = ~np.isnan(errs)
    print(f"\n  {lab}:  样本 {len(rows)}  有解 {(inls>=6).sum()}  "
          f"inl 中位 {np.median(inls):.0f}  max {inls.max()}")
    if ok.sum():
        e = errs[ok]
        print(f"    全部: 落点误差 中位 {np.median(e):.1f} m   "
              f"<50m {(e<50).sum()}/{ok.sum()}   <100m {(e<100).sum()}/{ok.sum()}")
        tk = np.array([r[f"{key}_err"] for r in rows
                       if r["tight"] and not math.isnan(r[f"{key}_err"])])
        if len(tk):
            print(f"    真值最硬的一段(恢复后3s内, n={len(tk)}): "
                  f"中位 {np.median(tk):.1f} m   "
                  f"<50m {(tk<50).sum()}/{len(tk)}")
sc = np.array([r["c_scale"] for r in rows if r["c_inl"] >= 6])
if len(sc):
    print(f"\n  拟合尺度 = {np.median(sc):.3f} m/小地图px  "
          f"(p25 {np.percentile(sc,25):.3f} p75 {np.percentile(sc,75):.3f})")
    r_norm = float(cfg["r_norm_by_width"]) * 2304
    print(f"  => 小地图半径 {r_norm:.0f}px 覆盖 {r_norm*np.median(sc):.0f} m "
          f"(视半径 {r_norm*np.median(sc)*2/3:.0f} m)")

with (FIG / "local_search_results.csv").open("w", encoding="utf-8-sig",
                                              newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["tp_id", "tp_name", "frame", "tight", "q_kp",
                "color_inliers", "color_err_m", "color_scale_m_per_px",
                "gray_inliers", "gray_err_m"])
    for r in rows:
        w.writerow([r["tp"], r["name"], f"b:f{r['frame']:05d}", int(r["tight"]),
                    r["qkp"], r["c_inl"],
                    None if math.isnan(r["c_err"]) else round(r["c_err"], 1),
                    round(r["c_scale"], 4), r["g_inl"],
                    None if math.isnan(r["g_err"]) else round(r["g_err"], 1)])
print(f"\n  -> {FIG/'local_search_results.csv'}")
print("=== DONE ===")
