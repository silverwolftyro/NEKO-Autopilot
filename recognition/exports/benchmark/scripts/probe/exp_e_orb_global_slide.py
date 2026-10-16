"""判决性实验：ORB 能不能在**整张米游社底图**上定位真实小地图。

之前所有局部窗口都锚在我猜的真值上，猜错就全盘失效（已发生一次：
b:f00215 其实是加载界面立绘）。这个实验不依赖任何真值假设：

* 拿 session_b 里**目视确认**的大世界帧的小地图
* 在整张彩色底图上按 800m 窗口 / 400m 步长滑窗，穷举全图
* 每个窗口算 RANSAC 内点数，记录排名

**判据不是"有没有解"，而是"多帧是否指向同一个窗口"**
—— 同一地点的连续帧必然落在同一片区域；假阳性是随机的。
随机取 3~5 帧，噪声峰不会重合。

    多帧峰值重合  -> ORB 定位可用，精度就是窗口尺度
    峰值各说各话  -> 域差距无法跨越，外观法到此为止
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

# 目视确认的大世界帧（见 session_b_minimap_grid.png）
FRAMES = [20, 40, 60]              # 枫丹廷城区，连续 4 秒内
WF, WIN, STEP = 800.0, 400, 200    # 米 / 窗口边(px@2m/px) / 步长(px)
MW = 2.0

HALF = cv2.imread(str(P0), cv2.IMREAD_REDUCED_COLOR_2)      # 2 m/px
H, W = HALF.shape[:2]
ids = sorted((RECOG / "raw" / "frames" / "session_20261001_b").glob("*.jpg"))
BF = cv2.BFMatcher(cv2.NORM_HAMMING)
OQ = cv2.ORB_create(nfeatures=3000, nlevels=8, scaleFactor=1.2, fastThreshold=5)
OW = cv2.ORB_create(nfeatures=6000, nlevels=8, scaleFactor=1.2, fastThreshold=5)
print(f"彩色底图 {W}x{H} @2 m/px   窗口 {WIN*2} m   步长 {STEP*2} m")

# 窗口内 ORB 特征点总数（把全图切成不重叠块一次建库，比滑窗重建快得多）
# 关键：块之间必须有重叠，否则小地图会跨块。用 STEP 步长建块，块宽 WIN。
xs = list(range(0, max(1, W - WIN + 1), STEP))
ys = list(range(0, max(1, H - WIN + 1), STEP))
print(f"滑窗位置 {len(xs)} x {len(ys)} = {len(xs)*len(ys)} 个")

im0 = cv2.imread(str(ids[FRAMES[0] - 1]))
Fh, Fw = im0.shape[:2]
rx0, ry0, _, _ = mm.minimap_rect(im0, cfg)
CCX = float(cfg["cx"]) * Fw - rx0
CCY = float(cfg["cy"]) * Fh - ry0

results = {}
t0 = time.perf_counter()
for fi, f in enumerate(FRAMES, 1):
    im = cv2.imread(str(ids[f - 1]))
    mini, mask = mm.make_minimap_roi(im, cfg)
    kq, dq = OQ.detectAndCompute(cv2.cvtColor(mini, cv2.COLOR_BGR2GRAY), None)
    print(f"\n  [{fi}/{len(FRAMES)}] b:f{f:05d}  查询特征 {len(kq)}  "
          f"(扫描中 {time.perf_counter()-t0:.0f}s)")
    hits = []
    for yi, y0 in enumerate(ys):
        row = HALF[y0:y0 + WIN, :]                       # 整条带一次转灰
        g = cv2.cvtColor(row, cv2.COLOR_BGR2GRAY)
        for x0 in xs:
            sub = g[:, x0:x0 + WIN]
            wk, wd = OW.detectAndCompute(sub, None)
            if wd is None or len(wd) < 8:
                continue
            good = [m for m, n2 in BF.knnMatch(dq, wd, k=2)
                    if m.distance < 0.78 * n2.distance]
            if len(good) < 5:
                continue
            wp = np.array([k.pt for k in wk], np.float32)
            src = np.float32([kq[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
            dst = np.float32([wp[m.trainIdx] for m in good]).reshape(-1, 1, 2)
            A, inl = cv2.estimateAffinePartial2D(
                src, dst, method=cv2.RANSAC, ransacReprojThreshold=2.0,
                maxIters=8000)
            n = 0 if (A is None or inl is None) else int(inl.sum())
            if n < 4 or A is None:
                continue
            sc = math.hypot(A[0, 0], A[1, 0]) * MW
            rot = abs(math.degrees(math.atan2(A[1, 0], A[0, 0])))
            # ★ 退化过滤：小地图正北朝上、缩放档固定，拟合出的相似变换
            #   尺度必须 > 0、旋转必须接近 0。尺度塌成 0 意味着所有内点
            #   重合成一个点（典型是水面/空地这种重复纹理的假聚类），
            #   这种"解"内点数再高也不是定位结果。
            if not (0.30 <= sc <= 10.0) or rot > 12.0:
                continue
            lx = x0 + A[0, 0] * CCX + A[0, 1] * CCY + A[0, 2]
            ly = y0 + A[1, 0] * CCX + A[1, 1] * CCY + A[1, 2]
            hits.append(dict(x=x0, y=y0, inl=n, good=len(good),
                             lx=lx, ly=ly, scale=sc, rot=rot))
        if yi % 10 == 0:
            print(f"      带 {yi+1}/{len(ys)}  已有命中窗口 {len(hits)}"
                  f"  {time.perf_counter()-t0:.0f}s")
    hits.sort(key=lambda h: -h["inl"])
    results[f] = hits
    print(f"    命中窗口 {len(hits)}   Top10:")
    for h in hits[:10]:
        print(f"      inl={h['inl']:>4} good={h['good']:>4}  "
              f"落点 L0=({h['lx']*2:6.0f},{h['ly']*2:6.0f})  "
              f"窗口=({h['x']},{h['y']})  尺度={h['scale']:.2f} m/px "
              f"旋转={h['rot']:.1f}deg")

# ---- 重合度：假阳性是随机的，真解必然重合
print("\n" + "=" * 92)
print("判决 · 多帧峰值是否重合")
print("=" * 92)
tops = {f: (results[f][0] if results[f] else None) for f in FRAMES}
for f, h in tops.items():
    if h is None:
        print(f"  b:f{f:05d}  最强命中 = 无")
    else:
        print(f"  b:f{f:05d}  最强命中 inl={h['inl']}  "
              f"落点 L0=({h['lx']*2:.0f},{h['ly']*2:.0f})")
if all(tops[f] for f in FRAMES):
    ds = [math.hypot(tops[a]["lx"] - tops[b]["lx"],
                     tops[a]["ly"] - tops[b]["ly"]) * MW
          for i, a in enumerate(FRAMES) for b in FRAMES[i + 1:]]
    print("  三个最强命中两两距离: "
          + "  ".join(f"{d:.0f} m" for d in ds))
    print("  -> " + ("★ 重合，判为真解" if max(ds) < 400 else "各说各话，判为噪声"))
else:
    print("  -> 至少一帧零命中，无法判决")

with (FIG / "global_slide_hits.csv").open("w", encoding="utf-8-sig",
                                          newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["frame", "rank", "window_x_2mpx", "window_y_2mpx",
                "inliers", "good", "hit_l0_x", "hit_l0_y", "scale_m_per_px",
                "rot_deg"])
    for f in FRAMES:
        for i, h in enumerate(results[f][:50], 1):
            w.writerow([f"b:f{f:05d}", i, h["x"], h["y"], h["inl"], h["good"],
                        round(h["lx"] * 2, 1), round(h["ly"] * 2, 1),
                        round(h["scale"], 4), round(h["rot"], 2)])
print(f"\n  总耗时 {time.perf_counter()-t0:.0f}s")
print(f"  -> {FIG/'global_slide_hits.csv'}")
print("=== DONE ===")
