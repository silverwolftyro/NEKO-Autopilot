"""验证 C/D 续 · 比例尺是不是常数？缩放档变不变？

**上一轮留下的疑问**
三段走路的帧间速度分别是 9.49 / 10.98 / 12.94 px/s。
如果角色走路速度是常数、缩放档也不变，那"px -> m"应该是同一个比例。
三个值差 35% -> **要么玩家速度不同（这段在冲刺），要么小地图缩放档变了**。

**如果是缩放档变了，比例不是常数，累加方案从根本上就不可靠。**

**判据（不需要真值）**
用 log-polar 相位相关直接测**帧间尺度比**：
  尺度比 = 1.0  -> 缩放档没变
  尺度比 > 1.0  -> 缩放档变了
这与玩家动作无关 —— 站着不动时尺度比也必须是 1。

自检：人工把一帧缩小 0.8 倍，看能不能测出 0.8。
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
CSV = RECOG / "exports" / "benchmark" / "datasets" / "phase_corr.csv"
WALK = RECOG / "exports" / "benchmark" / "datasets" / "walk_segments.csv"
VIS = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe"
DATASETS = RECOG / "exports" / "benchmark" / "datasets"
FPS = 10.0
M_LP = 64


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


mm = _load("minimap_mask", PERC / "minimap_mask.py")
cfg = mm.load_circle_cfg()
ids = sorted(DA.glob("*.jpg"))
_win = {}


def prep(i):
    im = cv2.imread(str(ids[i - 1]))
    crop, mask = mm.make_minimap_roi(im, cfg)
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    h, w = g.shape
    if (h, w) not in _win:
        _win[(h, w)] = cv2.createHanningWindow((w, h), cv2.CV_64F)
    m = (mask > 0).astype(np.float64)
    v = g.astype(np.float64) * m
    ins = m > 0
    if ins.sum() < 100:
        return None
    v -= v[ins].mean()
    v *= _win[(h, w)]
    return v, g


def lp_shift(a, b, gshape):
    """log-polar 空间相位相关，**只返回原始 (dx, dy) 和响应**。

    故意不猜"dx -> log(scale)"的换算公式（猜错过一次）：
    换算系数由下面的人工缩放标定**实测拟合**得到。
    """
    h, w = gshape
    center = (w / 2.0, h / 2.0)
    ap = cv2.logPolar(a, center, M_LP, 32, cv2.CV_64F)
    bp = cv2.logPolar(b, center, M_LP, 32, cv2.CV_64F)
    ap -= ap.mean()
    bp -= bp.mean()
    try:
        (dx, dy), resp = cv2.phaseCorrelate(ap, bp)
    except cv2.error:
        return 0.0, 0.0, 0.0
    return dx, dy, resp


print("=" * 92)
print("0. log-polar 尺度换算系数：人工缩放实测标定")
print("=" * 92)
raw = {}
im900 = cv2.imread(str(ids[899]))
crop900, mask900 = mm.make_minimap_roi(im900, cfg)
g900 = cv2.cvtColor(crop900, cv2.COLOR_BGR2GRAY)
mf = (mask900 > 0).astype(np.float64)
a_lp = g900.astype(np.float64) * mf
a_lp -= a_lp[mf > 0].mean()
h0, w0 = g900.shape

print("  人工缩放 -> log-polar dx:")
dpts = []
for f in (1.35, 1.20, 1.10, 1.00, 0.90, 0.80, 0.70, 0.60, 0.50):
    if f < 1.0:
        sm = cv2.resize(g900, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        out = np.zeros_like(g900)
        oy = (h0 - sm.shape[0]) // 2
        ox = (w0 - sm.shape[1]) // 2
        out[oy:oy + sm.shape[0], ox:ox + sm.shape[1]] = sm
    else:
        big = cv2.resize(g900, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
        oy = (big.shape[0] - h0) // 2
        ox = (big.shape[1] - w0) // 2
        out = big[oy:oy + h0, ox:ox + w0]
    v = out.astype(np.float64) * mf
    v -= v[mf > 0].mean()
    dxp, dyp, rp = lp_shift(a_lp, v, g900.shape)
    dpts.append((f, dxp, rp))
    print(f"    f={f:.2f}  dx={dxp:+8.3f}  response={rp:.3f}")

fx = np.array([d[0] for d in dpts])
lf = np.log(fx)
ddx = np.array([d[1] for d in dpts])
K, C = np.polyfit(ddx, lf, 1)
print(f"\n  拟合 log(f) = {K:.6f} * dx + {C:.6f}")
for f, dxp, rp in dpts:
    est = math.exp(K * dxp + C)
    print(f"    f={f:.2f} -> 反算 {est:.4f}  误差 {100*(est-f)/f:+.1f}%")
print(f"  标定残差 RMS = "
      f"{np.sqrt(np.mean((np.exp(K*ddx+C)-fx)**2)):.4f}（以比例计）")

print("\n" + "=" * 92)
print("1. 全量帧间尺度比（1511 对）")
print("=" * 92)
sr_list, rs_list, dxy_list = [], [], []
t0 = time.perf_counter()
prev = prep(1)
for i in range(2, len(ids) + 1):
    cur = prep(i)
    if prev is None or cur is None:
        sr_list.append(np.nan)
        rs_list.append(0.0)
        dxy_list.append(0.0)
    else:
        dxp, dyp, rp = lp_shift(prev[0], cur[0], prev[1].shape)
        sr_list.append(math.exp(K * dxp + C))     # 用标定好的系数换算
        rs_list.append(rp)
        dxy_list.append(math.hypot(*cv2.phaseCorrelate(prev[0], cur[0])[0]))
    prev = cur
    if i % 400 == 0:
        print(f"  {i}/{len(ids)}  {time.perf_counter()-t0:.0f}s")
print(f"  完成 {time.perf_counter()-t0:.0f}s")

sr = np.array(sr_list)
fin = np.isfinite(sr)
print(f"\n  尺度比 中位 {np.median(sr[fin]):.5f}  "
      f"p1 {np.percentile(sr[fin],1):.4f}  "
      f"p99 {np.percentile(sr[fin],99):.4f}  "
      f"min {sr[fin].min():.4f}  max {sr[fin].max():.4f}")
moved = np.array(dxy_list) > 0.3
if moved.sum() and fin.sum():
    print(f"  只看有位移的帧对({int((moved&fin).sum())} 个): "
          f"中位 {np.median(sr[moved&fin]):.5f}  "
          f"p1 {np.percentile(sr[moved&fin],1):.4f}  "
          f"p99 {np.percentile(sr[moved&fin],99):.4f}")

out = DATASETS / "phase_scale.csv"
with out.open("w", encoding="utf-8-sig", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["idx", "t_sec", "frame_from", "frame_to", "scale_ratio",
                "lp_response", "mag_px"])
    for k, v in enumerate(sr_list):
        w.writerow([k + 2, round((k + 1) / FPS, 1), f"a:f{k+1:05d}",
                    f"a:f{k+2:05d}",
                    None if math.isnan(v) else round(v, 6),
                    round(rs_list[k], 5), round(dxy_list[k], 4)])
print(f"  -> {out}")

# ---------------------------------------------------------------- 走路段内速度
print("\n" + "=" * 92)
print("2. 三段走路的逐帧速度 + 该处尺度比")
print("=" * 92)
prow = {int(r["idx"]): (float(r["dx_px"]), float(r["dy_px"]))
        for r in csv.DictReader(CSV.open(encoding="utf-8-sig", newline=""))}
walks = list(csv.DictReader(
    WALK.open(encoding="utf-8-sig", newline="")))
fig, ax = plt.subplots(3, 1, figsize=(17, 10), sharex=True)
tscale = [(k + 1) / FPS for k in range(len(sr_list))]
ax[0].plot(tscale, sr, lw=0.5, color="#9467bd", label="scale ratio (log-polar)")
ax[0].axhline(1.0, color="k", ls="--", lw=0.9, label="1.0 (no zoom change)")
ax[0].set_ylabel("scale ratio")
ax[0].legend(loc="upper right", fontsize=8)
ax[0].set_title("Minimap frame-to-frame SCALE ratio (log-polar phase correlation)\n"
                "if this is flat at 1.0, pixels-per-meter is a constant",
                fontsize=12)

for w_ in walks:
    a = int(w_["frame_first"].split(":f")[1])
    b = int(w_["frame_last"].split(":f")[1])
    for k in range(3):
        ax[k].axvspan((a - 1) / FPS, (b - 1) / FPS, color="#fa0",
                      alpha=0.28, zorder=0)
    sp = []
    for i in range(a, b):
        d = prow.get(i)
        if d:
            sp.append((i - 1) / FPS)
            ax[1].plot((i - 1) / FPS, math.hypot(*d) * FPS, ".",
                       color="#d62728", ms=2.5)
    seg_sr = sr[[int(x * FPS) - 1 for x in sp if 0 <= int(x * FPS) - 1 < len(sr)]] \
        if sp else np.array([])
    if len(seg_sr):
        ax[0].plot(sp, seg_sr, ".", color="#ff0", ms=2.5)
    v = [math.hypot(*prow[i]) * FPS for i in range(a, b) if i in prow]
    if v:
        v = np.array(v)
        print(f"  a:f{a:05d}-f{b:05d}  帧间速度 中位 {np.median(v):6.2f} px/s  "
              f"p25 {np.percentile(v,25):6.2f}  p75 {np.percentile(v,75):6.2f}"
              f"   段内尺度比 中位 "
              f"{np.median(seg_sr) if len(seg_sr) else float('nan'):.5f}")
ax[1].set_ylabel("|v| [px/s]")
ax[2].plot(tscale, rs_list, lw=0.5, color="#2ca02c", label="log-polar response")
ax[2].set_ylabel("lp response")
ax[2].set_xlabel("time [s]")
ax[2].legend(loc="upper right", fontsize=8)
fig.tight_layout()
png = VIS / "scale_ratio_curve.png"
fig.savefig(png, dpi=110)
print(f"\n  -> {png}")
print("=== DONE ===")
