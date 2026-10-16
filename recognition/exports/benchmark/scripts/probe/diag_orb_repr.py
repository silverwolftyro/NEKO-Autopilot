"""最后一道尽调：域差距是否"表示法"问题？

对照实验已证明管线本身是好的（C0 内点 1187 / C1 跨尺度 348），
真实小地图只有 2~3 个内点。差距在**描述子层面**就已经出现
（C1 good 率 20% vs C3 good 率 1%），所以要问：
换一种表示法能不能跨过这个域差距？

五种，全部在同一个 800m 真值窗口上、同样 5 帧真实小地图：

  E1 显式多尺度 ORB   —— 排除"尺度没对齐"这个可能
  E2 SIFT             —— 梯度型、尺度不变，检验是不是 ORB/BRIEF 太脆
  E3 彩色模板匹配     —— 之前 NCC 失败是在**灰度**上做的；
                        水(青绿)/地(绿)/路(米色) 在彩色里是可分的
  E4 颜色直方图相关   —— 完全不看结构，只看颜色分布
  E5 语义类别图匹配   —— k-means 量化后比类别图，不看纹理

对照组 C1（同域跨尺度）每项都跑一遍，**没有对照就没有结论**。
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

TRUTH = (19141, 7655)
HALF_M, MW = 400, 2.0
FRAMES = [215, 217, 219, 221, 223]

HALF = cv2.imread(str(P0), cv2.IMREAD_REDUCED_COLOR_2)
ids = sorted((RECOG / "raw" / "frames" / "session_20261001_b").glob("*.jpg"))
im0 = cv2.imread(str(ids[FRAMES[0] - 1]))
H0, W0 = im0.shape[:2]
x0, y0, _, _ = mm.minimap_rect(im0, cfg)
CCX, CCY = float(cfg["cx"]) * W0 - x0, float(cfg["cy"]) * H0 - y0

r = int(HALF_M / MW)
cx, cy = TRUTH[0] / 2.0, TRUTH[1] / 2.0
WIN = HALF[int(round(cy)) - r:int(round(cy)) + r,
           int(round(cx)) - r:int(round(cx)) + r].copy()
OCX, OCY = r, r
WIN_L = WIN.shape[0]
print(f"窗口 {WIN.shape}  ({2*r*MW:.0f} m 见方)  圆心参考 ({CCX:.1f},{CCY:.1f})")

# 对照查询：同域跨尺度的底图裁块
QP = 384
CTRL = HALF[int(cy) - QP // 4:int(cy) + QP // 4,
            int(cx) - QP // 4:int(cx) + QP // 4].copy()
CTRL = cv2.resize(CTRL, (QP, QP), interpolation=cv2.INTER_CUBIC)
MINIS = []
for f in FRAMES:
    im = cv2.imread(str(ids[f - 1]))
    c, m = mm.make_minimap_roi(im, cfg)
    MINIS.append((f, c, m))
print(f"对照查询 {CTRL.shape}   真实小地图 {MINIS[0][1].shape} x{len(MINIS)}")

rows = []


def rec(tag, name, metric, val, err=np.nan, extra=""):
    rows.append(dict(tag=tag, name=name, metric=metric, val=val,
                     err=err, extra=extra))


# ---------------------------------------------------------------- E1 多尺度 ORB
print("\n" + "=" * 92)
print("E1 · 显式多尺度 ORB（排除尺度没对齐）")
print("=" * 92)
BF = cv2.BFMatcher(cv2.NORM_HAMMING)
OQ = cv2.ORB_create(nfeatures=3000, nlevels=8, scaleFactor=1.2, fastThreshold=5)
OW = cv2.ORB_create(nfeatures=12000, nlevels=8, scaleFactor=1.2, fastThreshold=5)
wk, wd = OW.detectAndCompute(WIN, None)
wp = np.array([k.pt for k in wk], np.float32)


def orb_try(img, tag, name):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    kq, dq = OQ.detectAndCompute(g, None)
    good = [m for m, n2 in BF.knnMatch(dq, wd, k=2) if m.distance < 0.78 * n2.distance]
    if len(good) < 4:
        rec(tag, name, "good", len(good))
        return len(good), 0
    src = np.float32([kq[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([wp[m.trainIdx] for m in good]).reshape(-1, 1, 2)
    A, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC,
                                         ransacReprojThreshold=2.0, maxIters=20000)
    n = 0 if (A is None or inl is None) else int(inl.sum())
    err = np.nan
    if A is not None:
        # 用"查询图中心"当参考点，和窗口真值中心比
        qx, qy = img.shape[1] / 2.0, img.shape[0] / 2.0
        err = (math.hypot(A[0, 0] * qx + A[0, 1] * qy + A[0, 2] - OCX,
                          A[1, 0] * qx + A[1, 1] * qy + A[1, 2] - OCY) * MW)
    rec(tag, name, "inl", n, err)
    return len(good), n


print(f"  对照 C1: ", end="")
g, n = orb_try(CTRL, "E1", "CTRL")
print(f"good={g} inl={n}")
SCALES = [1.0, 0.7, 0.5, 0.35, 0.25]
for s in SCALES:
    tot_in = tot_g = 0
    for f, c, _ in MINIS:
        img = c if s == 1.0 else cv2.resize(
            c, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        g, n = orb_try(img, "E1", f"b:f{f:05d}@x{s}")
        tot_g += g
        tot_in += n
    print(f"  小地图 x{s:<5} good 合计 {tot_g:<5} 内点 合计 {tot_in}")

# ---------------------------------------------------------------- E2 SIFT
print("\n" + "=" * 92)
print("E2 · SIFT")
print("=" * 92)
BF2 = cv2.BFMatcher(cv2.NORM_L2)
S = cv2.SIFT_create(nfeatures=8000)
swk, swd = S.detectAndCompute(cv2.cvtColor(WIN, cv2.COLOR_BGR2GRAY), None)
sp = np.array([k.pt for k in swk], np.float32)
print(f"  窗口 SIFT {len(swk)} 点")


def sift_try(img, tag, name):
    kq, dq = S.detectAndCompute(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), None)
    if dq is None or len(dq) < 8:
        rec(tag, name, "inl", 0)
        return 0, 0
    good = [m for m, n2 in BF2.knnMatch(dq, swd, k=2)
            if m.distance < 0.75 * n2.distance]
    if len(good) < 4:
        rec(tag, name, "inl", 0)
        return len(good), 0
    src = np.float32([kq[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([sp[m.trainIdx] for m in good]).reshape(-1, 1, 2)
    A, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC,
                                         ransacReprojThreshold=2.0, maxIters=20000)
    n = 0 if (A is None or inl is None) else int(inl.sum())
    err = np.nan
    if A is not None:
        qx, qy = img.shape[1] / 2.0, img.shape[0] / 2.0
        err = (math.hypot(A[0, 0] * qx + A[0, 1] * qy + A[0, 2] - OCX,
                          A[1, 0] * qx + A[1, 1] * qy + A[1, 2] - OCY) * MW)
    rec(tag, name, "inl", n, err)
    return len(good), n


g, n = sift_try(CTRL, "E2", "CTRL")
print(f"  对照 C1: good={g} inl={n}")
for f, c, _ in MINIS:
    g, n = sift_try(c, "E2", f"b:f{f:05d}")
    print(f"  小地图 b:f{f:05d}: good={g} inl={n}")


# ---------------------------------------------------------------- E3 彩色模板匹配
print("\n" + "=" * 92)
print("E3 · 彩色模板匹配（Lab 空间 TM_CCOEFF_NORMED）")
print("=" * 92)


def tpl(img, tag, name, same_size_win=True):
    t = cv2.resize(img, (WIN_L, WIN_L), interpolation=cv2.INTER_AREA)
    a = cv2.cvtColor(t, cv2.COLOR_BGR2LAB).astype(np.float32)
    b = cv2.cvtColor(WIN, cv2.COLOR_BGR2LAB).astype(np.float32)
    res = cv2.matchTemplate(b, a, cv2.TM_CCOEFF_NORMED)
    _, mx, _, loc = cv2.minMaxLoc(res)
    d = math.hypot(loc[0] - (OCX - (WIN_L - img.shape[1] / 2) / 2),
                   loc[1] - (OCY - (WIN_L - img.shape[0] / 2) / 2))
    err = d * MW
    rec(tag, name, "ncc", round(mx, 4), err, f"peak@({loc[0]},{loc[1]})")
    return mx, err


mx, err = tpl(CTRL, "E3", "CTRL")
print(f"  对照 C1: 彩色NCC={mx:.4f} 误差={err:.1f} m")
for f, c, _ in MINIS:
    mx, err = tpl(c, "E3", f"b:f{f:05d}")
    print(f"  小地图 b:f{f:05d}: 彩色NCC={mx:.4f} 误差={err:.1f} m")

# ---------------------------------------------------------------- E4 颜色直方图
print("\n" + "=" * 92)
print("E4 · 颜色直方图相关（HSV 2D 直方图，相关系数）")
print("=" * 92)


def hist2d(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h = cv2.calcHist([hsv], [0, 1], None, [36, 8], [0, 180, 0, 256])
    return cv2.normalize(h, h).flatten()


hw = hist2d(WIN)


def corr(img, tag, name):
    q = hist2d(img)
    c = float(np.corrcoef(hw, q)[0, 1])
    rec(tag, name, "corr", round(c, 4), np.nan)
    return c


c = corr(CTRL, "E4", "CTRL")
print(f"  对照 C1: 直方图相关={c:.4f}")
for f, c2, _ in MINIS:
    print(f"  小地图 b:f{f:05d}: 直方图相关={corr(c2, 'E4', f'b:f{f:05d}'):.4f}")

# ---------------------------------------------------------------- E5 语义类别
print("\n" + "=" * 92)
print("E5 · 语义类别图匹配（k-means 量化 -> 类别图互相关）")
print("=" * 92)
centers = None


def quant(img, k=6, sample=60000, seed=42):
    rng = np.random.default_rng(seed)
    H, W = img.shape[:2]
    ys, xs = rng.integers(0, H, sample), rng.integers(0, W, sample)
    pts = img[ys, xs].astype(np.float32).reshape(-1, 1, 3)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 15, 1.0)
    _, _, cen = cv2.kmeans(pts, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    cen = cen.astype(np.float32)
    lab = np.empty((H, W), np.uint8)
    for y0 in range(0, H, 256):
        y1 = min(H, y0 + 256)
        d = ((img[y0:y1].astype(np.float32)[:, :, None, :] - cen) ** 2).sum(-1)
        lab[y0:y1] = d.argmin(-1).astype(np.uint8)
    return lab


WLAB = quant(WIN)
wlab_f = WLAB.astype(np.float32)


def sem(img, tag, name):
    t = cv2.resize(img, (WIN_L, WIN_L), interpolation=cv2.INTER_NEAREST)
    tlab = quant(t).astype(np.float32)
    res = cv2.matchTemplate(wlab_f, tlab, cv2.TM_CCOEFF_NORMED)
    _, mx, _, loc = cv2.minMaxLoc(res)
    rec(tag, name, "sem_ncc", round(mx, 4), np.nan, f"peak@({loc[0]},{loc[1]})")
    return mx


mx = sem(CTRL, "E5", "CTRL")
print(f"  对照 C1: 类别图NCC={mx:.4f}")
for f, c2, _ in MINIS:
    print(f"  小地图 b:f{f:05d}: 类别图NCC={sem(c2, 'E5', f'b:f{f:05d}'):.4f}")

# ---------------------------------------------------------------- 汇总
print("\n" + "=" * 92)
print("汇总  （对照 = 同域跨尺度底图裁块；小地图 = 真实游戏渲染）")
print("=" * 92)
with (FIG / "representation_results.csv").open("w", encoding="utf-8-sig",
                                               newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["exp", "name", "metric", "value", "err_m", "extra"])
    for x in rows:
        w.writerow([x["tag"], x["name"], x["metric"], x["val"],
                    None if math.isnan(x["err"]) else round(x["err"], 1),
                    x["extra"]])
for tag, unit in (("E1", "内点"), ("E2", "内点"), ("E3", "彩色NCC"),
                  ("E4", "直方图相关"), ("E5", "类别图NCC")):
    ctrl = [x for x in rows if x["tag"] == tag and x["name"] == "CTRL"]
    mini = [x for x in rows if x["tag"] == tag and x["name"] != "CTRL"]
    if not ctrl:
        continue
    cv_ = [x["val"] for x in ctrl]
    mv = [x["val"] for x in mini if x["val"] is not None]
    print(f"  {tag} {unit:<10} 对照 {cv_[0]:<10} "
          f"小地图 中位 {np.median(mv) if mv else float('nan'):<10} "
          f"范围 {min(mv) if mv else 0}~{max(mv) if mv else 0}")
print(f"\n  -> {FIG/'representation_results.csv'}")
print("=== DONE ===")
