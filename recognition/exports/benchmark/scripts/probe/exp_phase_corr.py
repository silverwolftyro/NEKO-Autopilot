"""验证 A/B · 相位相关测小地图帧间位移。

**核心假设**
用户设置"固定地图方向"后，小地图帧间只有**平移**，
可以用 cv2.phaseCorrelate() 直接测出 (dx, dy)。

**A**：全量 1511 对连续帧，输出 CSV + 曲线图。
**B**：从中筛"纯走路段"（世界场景 + 峰值够 + 方向一致 + 圆内不脏）。

**符号约定自检**
相位相关的符号方向极易搞反，累加方向错了整条路就废了。
脚本开头用人工平移做一次自检，把实际约定打印出来并写进 CSV 说明。

只读 session_a，不动 minimap_locator.py，不引入新依赖。
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
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt      # noqa: E402

PERC = (Path(PLUGIN_DIR.parent)
        / "genshin_game_buddy" / "autopilot" / "perception")
RECOG = Path(RECOG_ROOT)
DA = RECOG / "raw" / "frames" / "session_20261001_a"
SEG = RECOG / "exports" / "benchmark" / "datasets" / "session_a_segments.csv"
VIS = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe"
VIS.mkdir(parents=True, exist_ok=True)
DATASETS = RECOG / "exports" / "benchmark" / "datasets"
FPS = 10.0


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


mm = _load("minimap_mask", PERC / "minimap_mask.py")
cfg = mm.load_circle_cfg()
ids = sorted(DA.glob("*.jpg"))
N = len(ids)
print(f"session_a {N} 帧  {ids[0].name} .. {ids[-1].name}")

# ---------------------------------------------------------------- 场景分段
segs = []
with SEG.open(encoding="utf-8-sig", newline="") as fh:
    for r in csv.DictReader(fh):
        segs.append((int(r["frame_first"][1:]), int(r["frame_last"][1:]),
                     r["raw_prediction"] == "world"))


def is_world(i):
    for a, b, w in segs:
        if a <= i <= b:
            return w
    return False


# ---------------------------------------------------------------- 预处理
_win_cache = {}


def prep(crop, mask):
    """裁块 + 圆掩码置零 + 减均值 + Hanning 窗 -> float64。"""
    key = crop.shape[:2]                       # (h, w)，不是 (h, w, ch)
    if key not in _win_cache:
        h, w = key
        _win_cache[key] = cv2.createHanningWindow((w, h), cv2.CV_64F)
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    m = (mask > 0).astype(np.float64)
    v = g.astype(np.float64) * m
    inside = m > 0
    if inside.sum() < 100:
        return None, 0.0
    v -= v[inside].mean()              # 去直流，相位相关必须先做
    v *= _win_cache[key]               # Hanning 抑制边缘环
    return v, float(g[inside].std())   # 圆内标准差 = "脏度"


def read(i):
    im = cv2.imread(str(ids[i - 1]))
    if im is None:
        raise FileNotFoundError(f"读帧失败: {ids[i-1]}")
    crop, mask = mm.make_minimap_roi(im, cfg)
    return prep(crop, mask)


# ---------------------------------------------------------------- 符号自检
print("\n" + "=" * 92)
print("0. 符号约定自检（人工平移后看 phaseCorrelate 返回什么）")
print("=" * 92)
a_img, _ = read(900)
b_img, _ = read(901)
M = np.float32([[1, 0, 7], [0, 1, -4]])          # 人工右移 7、下移 4
shifted = cv2.warpAffine(b_img, M, b_img.shape[:2][::-1])
(dx, dy), resp = cv2.phaseCorrelate(a_img, shifted)
print(f"  把 f000901 人工平移 (+7, -4) 后，与 f000900 做相关：")
print(f"    返回 (dx, dy) = ({dx:+.3f}, {dy:+.3f})  response={resp:.4f}")
print(f"    -> 约定：{'返回 (负的平移量)，即 src1 对齐到 src2 需加 (-dx,-dy)' if dx < 0 else '返回 (正的平移量)'}")

(dx0, dy0), r0 = cv2.phaseCorrelate(a_img, b_img)
print(f"  f000900 vs f000901 实测: ({dx0:+.3f}, {dy0:+.3f}) "
      f"response={r0:.4f}")

# ---------------------------------------------------------------- 全量
print("\n" + "=" * 92)
print("1. 全量相位相关（1511 对）")
print("=" * 92)
rows = []
t0 = time.perf_counter()
for i in range(1, N):
    pa, std_a = read(i)
    pb, std_b = read(i + 1)
    if pa is None or pb is None:
        rows.append(dict(idx=i, dx=np.nan, dy=np.nan, resp=0.0,
                         std=0.0, world=is_world(i)))
        continue
    (dx, dy), resp = cv2.phaseCorrelate(pa, pb)
    rows.append(dict(idx=i, dx=dx, dy=dy, resp=resp,
                     std=(std_a + std_b) / 2, world=is_world(i)))
    if i % 300 == 0:
        print(f"  {i}/{N}  {time.perf_counter()-t0:.0f}s")
print(f"  完成 {time.perf_counter()-t0:.0f}s")

# ---------------------------------------------------------------- 统计
dx = np.array([r["dx"] for r in rows])
dy = np.array([r["dy"] for r in rows])
resp = np.array([r["resp"] for r in rows])
mag = np.hypot(dx, dy)
wmask = np.array([r["world"] for r in rows])
smask = np.array([r["std"] for r in rows])
print("\n" + "=" * 92)
print("2. 统计")
print("=" * 92)


def st(name, v, mask=None):
    x = v[mask] if mask is not None else v
    x = x[~np.isnan(x)]
    if len(x) == 0:
        print(f"  {name:<26} (空)")
        return
    print(f"  {name:<26} n={len(x):<5} min={x.min():7.2f} p25={np.percentile(x,25):7.2f} "
          f"中位={np.median(x):7.2f} p75={np.percentile(x,75):7.2f} "
          f"p95={np.percentile(x,95):7.2f} max={x.max():7.2f}")


st("峰值响应 (全部)", resp)
st("峰值响应 (大世界段)", resp, wmask)
st("位移模长 px (大世界)", mag, wmask)
st("圆内标准差 (大世界)", smask, wmask)

print("\n  按峰值响应分档（只用大世界段）:")
for lo, hi in ((0.0, 0.05), (0.05, 0.10), (0.10, 0.20),
               (0.20, 0.40), (0.40, 10.0)):
    m = wmask & (resp >= lo) & (resp < hi)
    print(f"    resp [{lo:.2f},{hi:.2f})  {m.sum():>5} 帧  "
          f"位移中位 {np.median(mag[m]) if m.sum() else float('nan'):6.2f} px")

# ---------------------------------------------------------------- CSV
out = DATASETS / "phase_corr.csv"
with out.open("w", encoding="utf-8-sig", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["idx", "t_sec", "frame_from", "frame_to", "is_world",
                "dx_px", "dy_px", "mag_px", "peak_response", "circle_std"])
    for r in rows:
        w.writerow([r["idx"], round((r["idx"] - 1) / FPS, 1),
                    f"a:f{r['idx']:05d}", f"a:f{r['idx']+1:05d}",
                    int(r["world"]), round(r["dx"], 4), round(r["dy"], 4),
                    round(math.hypot(r["dx"], r["dy"]), 4),
                    round(r["resp"], 6), round(r["std"], 3)])
print(f"\n  -> {out}")

# ---------------------------------------------------------------- 曲线图
fig, ax = plt.subplots(4, 1, figsize=(17, 13), sharex=True,
                       gridspec_kw={"height_ratios": [1, 1, 1, 0.5]})
t = [(r["idx"] - 1) / FPS for r in rows]
# 世界段底色
for a, b, w_ in segs:
    for k in range(3):
        ax[k].axvspan((a - 1) / FPS, (b - 1) / FPS,
                      color="#2b6" if w_ else "#b44", alpha=0.18)
ax[0].plot(t, dx, lw=0.5, color="#1f77b4", label="dx (px)")
ax[0].set_ylabel("dx [px]")
ax[0].legend(loc="upper right", fontsize=8)
ax[0].set_title("Phase correlation on minimap, session_a (1511 pairs)\n"
                "green bands = world segments, red bands = non-world", fontsize=11)
ax[1].plot(t, dy, lw=0.5, color="#d62728", label="dy (px)")
ax[1].set_ylabel("dy [px]")
ax[1].legend(loc="upper right", fontsize=8)
ax[2].plot(t, mag, lw=0.5, color="#2ca02c", label="|d| (px)")
ax[2].axhline(1.0, color="k", ls="--", lw=0.8, label="1 px")
ax[2].axhline(3.0, color="k", ls=":", lw=0.8, label="3 px")
ax[2].set_ylabel("magnitude [px]")
ax[2].legend(loc="upper right", fontsize=8)
ax[3].plot(t, resp, lw=0.5, color="#9467bd", label="peak response")
ax[3].axhline(0.1, color="k", ls="--", lw=0.8, label="0.1")
ax[3].set_ylabel("response")
ax[3].set_xlabel("time [s]   (frame index / 10 fps)")
ax[3].legend(loc="upper right", fontsize=8)
fig.tight_layout()
png = VIS / "phase_corr_curve.png"
fig.savefig(png, dpi=110)
print(f"  -> {png}")
print("=== DONE ===")
