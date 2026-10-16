"""ORB 定位零解 · 根因诊断。

上一轮 152 帧全部 inl<6。这里把两种可能拆开：

  H1「库太稀疏」  —— L2 整图 60k 特征点 = 每 71 万像素 1 个，
                     而小地图是每 4 万像素 1 个。密度差 17 倍，
                     小地图的特征在对面的底图上根本不存在。
  H2「域差距」    —— 游戏渲染的小地图 和 米游社扁平矢量底图
                     压根没有共同的角点，画风不同。

**判别实验（有真值）**
取 23 个锚点的 L0 裁块（已知它来自底图哪一块），
去 L2 整图库里定位：
  * 找到了 -> 管线本身没问题，零解是 H2（域差距）
  * 找不到 -> 管线在库这一层就断了，是 H1（密度/尺度）
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
DA = RECOG / "raw" / "frames" / "session_20261001_a"
ANCH = RECOG / "data" / "mihoyo_maps" / "teyvat_P0.png.anchors.json"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" / "orb_e"
FIG.mkdir(parents=True, exist_ok=True)


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


mm = _load("minimap_mask", PERC / "minimap_mask.py")
cl = _load("chunk_loader", PERC / "chunk_loader.py")
pl = _load("pyramid_loader", PERC / "pyramid_loader.py")

CH = cl.ChunkLoader(cache_size=24)
PY = pl.PyramidLoader(preload=())
anchors = json.loads(ANCH.read_text(encoding="utf-8"))["points"]
K2 = 0.25
BF = cv2.BFMatcher(cv2.NORM_HAMMING)
ORB_Q = cv2.ORB_create(nfeatures=2000, nlevels=8, scaleFactor=1.2,
                       fastThreshold=5)

# ================= 1. 画风对比图
print("=" * 92)
print("1. 画风对比：游戏小地图 vs 米游社底图（同一区域，两种缩放）")
print("=" * 92)
TARGET = "七天神像-枫丹廷区"          # x=18830 y=7651
tgt = next(a for a in anchors if a["name"] == TARGET)
print(f"  对照锚点: {TARGET}  L0=({tgt['x']},{tgt['y']})")

# 小地图
im = cv2.imread(str(sorted(DA.glob("*.jpg"))[1099]))     # a:f01100
mini, _ = mm.make_minimap_roi(im)
t_mini = cv2.resize(mini, (512, 512), interpolation=cv2.INTER_CUBIC)
cv2.putText(t_mini, "a:f01100 minimap (game render)", (8, 26),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 4)
cv2.putText(t_mini, "a:f01100 minimap (game render)", (8, 26),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)

# L0 原图裁块（1 m/px），取 512 宽
c0 = CH.load_l0_window((tgt["x"], tgt["y"]), 1024)
if c0.ndim == 2:
    c0 = cv2.cvtColor(c0, cv2.COLOR_GRAY2BGR)
t0 = cv2.resize(c0, (512, 512), interpolation=cv2.INTER_AREA)
cv2.putText(t0, "mihoyo P0 L0 crop 1024m wide", (8, 26),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 4)
cv2.putText(t0, "mihoyo P0 L0 crop 1024m wide", (8, 26),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)
# 放大 2x 看真实细节
t0b = cv2.resize(c0[:512, :512], (512, 512), interpolation=cv2.INTER_NEAREST)
cv2.putText(t0b, "L0 crop 512m wide @1m/px (2x zoom)", (8, 26),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 4)
cv2.putText(t0b, "L0 crop 512m wide @1m/px (2x zoom)", (8, 26),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

l2 = PY.get_level(2)
x2, y2 = int(tgt["x"] * K2), int(tgt["y"] * K2)
c2 = l2[max(0, y2 - 64):y2 + 64, max(0, x2 - 64):x2 + 64]
t2 = cv2.resize(c2, (512, 512), interpolation=cv2.INTER_CUBIC)
t2 = cv2.cvtColor(t2, cv2.COLOR_GRAY2BGR)
cv2.putText(t2, "mihoyo P0 L2 crop 512m wide @4m/px", (8, 26),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 4)
cv2.putText(t2, "mihoyo P0 L2 crop 512m wide @4m/px", (8, 26),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)

grid = np.vstack([np.hstack([t_mini, t0]), np.hstack([t0b, t2])])
cv2.imwrite(str(FIG / "diag_style_compare.png"), grid)
print(f"  -> {FIG/'diag_style_compare.png'}")

# ================= 2. 库密度
print("\n" + "=" * 92)
print("2. 底图特征点密度")
print("=" * 92)
for lv in (2, 3, 4):
    im_ = PY.get_level(lv)
    h, w = im_.shape
    mpix = w * h / 1e6
    print(f"  L{lv} {w}x{h} = {mpix:.1f} MPix   "
          f"60k 库 -> 每 {w*h/60000:.0f} px 一个特征点")
mm_g = cv2.cvtColor(mini, cv2.COLOR_BGR2GRAY)
_, mm_mask = mm.make_minimap_roi(im)
kq, _ = ORB_Q.detectAndCompute(mm_g, mm_mask)
print(f"  小地图 261x261 = {261*261/1e6:.3f} MPix   "
      f"{len(kq)} 特征点 -> 每 {261*261/max(len(kq),1):.0f} px 一个")

# ================= 3. 有真值的定位测试
print("\n" + "=" * 92)
print("3. 判别实验 · L0 裁块（有真值）在 L2 整图库里定位")
print("=" * 92)


def build_lib(level, nfeat, nlevels=10):
    im_ = PY.get_level(level)
    t = time.perf_counter()
    o = cv2.ORB_create(nfeatures=nfeat, nlevels=nlevels, scaleFactor=1.2,
                       fastThreshold=5)
    k, d = o.detectAndCompute(im_, None)
    pts = np.array([x.pt for x in k], dtype=np.float32)
    return pts, d, time.perf_counter() - t


def locate(qk, qd, lib_pts, lib_d, ratio=0.78, reproj=3.0):
    if qd is None or len(qd) < 4:
        return dict(good=0, inl=0, x=np.nan, y=np.nan, hit=False)
    good = [m for m, n2 in BF.knnMatch(qd, lib_d, k=2)
            if m.distance < ratio * n2.distance]
    if len(good) < 4:
        return dict(good=len(good), inl=0, x=np.nan, y=np.nan, hit=False)
    src = np.float32([qk[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([lib_pts[m.trainIdx] for m in good]).reshape(-1, 1, 2)
    A, inl = cv2.estimateAffinePartial2D(src, dst, cv2.RANSAC, reproj, 20000)
    if A is None or inl is None:
        return dict(good=len(good), inl=0, x=np.nan, y=np.nan, hit=False)
    n = int(inl.sum())
    cx, cy = 256.0, 256.0
    x = A[0, 0] * cx + A[0, 1] * cy + A[0, 2]
    y = A[1, 0] * cx + A[1, 1] * cy + A[1, 2]
    return dict(good=len(good), inl=n, x=x, y=y, hit=False)


cfg = mm.load_circle_cfg()

for lib_level, nfeat in ((2, 60000), (2, 300000), (3, 200000)):
    lp, ld, tb = build_lib(lib_level, nfeat)
    print(f"\n  --- 库: L{lib_level} nfeatures={nfeat} -> 实际 {len(lp)} "
          f"特征点, {tb:.1f}s ---")
    hits, inls, errs = 0, [], []
    for a in anchors[:10]:
        c = CH.load_l0_window((a["x"], a["y"]), 512)
        g = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY)
        k_, d_ = ORB_Q.detectAndCompute(g, None)
        res = locate(k_, d_, lp, ld)
        kk = 1.0 / (2 ** lib_level)
        if res["inl"] >= 6:
            dpx = math.hypot(res["x"] - a["x"] * kk, res["y"] - a["y"] * kk)
            errs.append(dpx)
            # 命中判据：落点距真值 < 该块半宽（512m -> 缩放到当前层）
            tol = 256 * kk
            res["hit"] = dpx < tol
            hits += int(res["hit"])
        inls.append(res["inl"])
        print(f"    {a['name'][:14]:<16} qkp={len(k_):>4} good={res['good']:>4} "
              f"inl={res['inl']:>3} 落点=({res['x']:7.0f},{res['y']:7.0f}) "
              f"真值=({a['x']*kk:7.0f},{a['y']*kk:7.0f}) "
              f"误差={dpx:7.1f}px {'HIT' if res['hit'] else ''}"
              if res["inl"] >= 6 else
              f"    {a['name'][:14]:<16} qkp={len(k_):>4} good={res['good']:>4} "
              f"inl={res['inl']:>3}  --")
    ai = np.array(inls)
    print(f"    >> inl 中位 {np.median(ai):.0f} max {ai.max()}  "
          f"命中 {hits}/10  误差中位 "
          f"{np.median(errs):.0f}px" if errs else
          f"    >> inl 中位 {np.median(ai):.0f} max {ai.max()}  命中 0/10")

print("\n=== DONE ===")
