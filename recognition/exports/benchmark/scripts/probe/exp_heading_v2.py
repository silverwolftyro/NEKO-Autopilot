"""任务 A/B/C 修正版 · 玩家朝向提取。

★ 修正的两个 bug
----------------
1. **质心报告双重偏移**（我的代码 bug，非提取 bug）
   patch 是以圆心为原点的局部裁块，质心本就是相对量，
   我却减了全局圆心 CX/CY -> 报告显示 -110，实际提取正确。
2. **水域与三角同色**（真问题）
   排除圆心 14px 后仍有面积 2161 的青蓝连通域 —— 那是水域，
   H 同样落在 85~105。**颜色单独分不开**。

   -> 改用"多连通域 + 选质心离圆心最近的那个"。
      玩家标记永远在小地图圆心（水域会随玩家移动而进出视野），
      所以"最近圆心"是可靠判据。
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
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

PERC = (Path(PLUGIN_DIR.parent)
        / "genshin_game_buddy" / "autopilot" / "perception")
RECOG = Path(RECOG_ROOT)
DA = RECOG / "raw" / "frames" / "session_20261001_a"
PC = RECOG / "exports" / "benchmark" / "datasets" / "phase_corr.csv"
WALK = RECOG / "exports" / "benchmark" / "datasets" / "walk_segments.csv"
SEG = RECOG / "exports" / "benchmark" / "datasets" / "session_a_segments.csv"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" \
    / "heading"
FIG.mkdir(parents=True, exist_ok=True)

R = 22                                   # 窗口半宽（够容纳三角+视野锥根部）
# ★ 阈值经 diag_heading_separability.py 扫描确定：
#   低饱和（S>90）会把玩家三角 + 白色视野锥 + 相邻水域连成一片
#   （圆心连通域 3540px / 131x53），形状信息全丢。
#   高饱和（S>=150,V>=150）时圆心连通域稳定在 280~400px、24~29px，
#   正是三角本体。四帧实测都落在 280~400 区间。
HSV_LO = (85, 150, 150)
HSV_HI = (100, 255, 255)
MIN_PIX, MAX_PIX = 150, 700
MAX_OFF = 12.0                          # 质心离圆心最大距离（px）


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


mm = _load("minimap_mask", PERC / "minimap_mask.py")
cfg = mm.load_circle_cfg()
ids = sorted(DA.glob("*.jpg"))
x0, y0, _, _ = mm.minimap_rect(cv2.imread(str(ids[199])), cfg)
CX = float(cfg["cx"]) * 2304 - x0
CY = float(cfg["cy"]) * 1440 - y0

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


def _tip_angle(cnt):
    """从三角轮廓算"尖端方向"（图像坐标系，x 右 y 下，0 度=+x）。

    做法：最小外接矩形 -> 旋转矫正 -> 沿长轴分两半比宽度，
    窄的一半是尖端。把 local 方向转回图像方向。
    """
    rect = cv2.minAreaRect(cnt)
    (cxr, cyr), (_, _), ang = rect
    box = cv2.boxPoints(rect).astype(np.float32)
    ca, sa = math.cos(math.radians(ang)), math.sin(math.radians(ang))
    Rm = np.array([[ca, sa], [-sa, ca]], np.float32)
    local = (box - np.array([cxr, cyr], np.float32)) @ Rm.T
    ys, xs = local[:, 1], local[:, 0]
    if len(ys) < 3:
        return None
    span = ys.max() - ys.min()
    if span < 4:
        return None
    half = (ys.min() + ys.max()) / 2
    up = xs[ys <= half]
    dn = xs[ys >= half]
    w_up = (up.max() - up.min()) if len(up) > 1 else 0.0
    w_dn = (dn.max() - dn.min()) if len(dn) > 1 else 0.0
    if w_up == 0 and w_dn == 0:
        return None
    tip_y = ys.min() if w_up <= w_dn else ys.max()
    d = np.array([0.0, -1.0 if tip_y == ys.min() else 1.0])
    d = Rm.T @ d
    return math.degrees(math.atan2(float(d[1]), float(d[0])))


def extract_heading(frame):
    """返回 ``((off_x, off_y), angle_deg)``，偏移相对**小地图圆心**。

    :returns: ``None`` 表示提取不到（黑场 / 菜单 / 圆心附近无青蓝）。
    """
    im = cv2.imread(str(ids[frame - 1]))
    if im is None:
        return None
    crop, _ = mm.make_minimap_roi(im, cfg)
    cyi, cxi = int(round(CY)), int(round(CX))
    patch = crop[cyi - R:cyi + R, cxi - R:cxi + R]
    if patch.shape[0] < 8 or patch.shape[1] < 8:
        return None
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, np.array(HSV_LO), np.array(HSV_HI))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    # 多个候选里选"质心离圆心最近"的：玩家标记恒在圆心，水域会飘
    best = None
    for c in cnts:
        a = cv2.contourArea(c)
        if not (MIN_PIX <= a <= MAX_PIX):
            continue
        M = cv2.moments(c)
        if abs(M["m00"]) < 1e-6:
            continue
        mx = M["m10"] / M["m00"] - R          # 相对圆心
        my = M["m01"] / M["m00"] - R
        off = math.hypot(mx, my)
        if off > MAX_OFF:
            continue
        ang = _tip_angle(c)
        if ang is None:
            continue
        cand = (off, (mx, my), ang, int(a))
        if best is None or cand[0] < best[0]:
            best = cand
    if best is None:
        return None
    _, (mx, my), ang, area = best
    return (mx, my), ang, area


# ---------------------------------------------------------------- A
print("=" * 96)
print("任务 A · 玩家三角提取（修正版：多连通域选最靠圆心）")
print("=" * 96)
sample = [200, 400, 600, 900, 1100, 1300, 1450]
print(f"  {'帧':<12}{'偏移(相对圆心)':>18}{'朝向角':>10}{'面积':>8}")
tilesA = []
for f in sample:
    r = extract_heading(f)
    if r is None:
        print(f"  a:f{f:05d}       未检出")
        continue
    (mx, my), ang, area = r
    print(f"  a:f{f:05d}   ({mx:+6.1f},{my:+6.1f}){ang:>10.1f}°{area:>8d}")
    im = cv2.imread(str(ids[f - 1]))
    crop, _ = mm.make_minimap_roi(im, cfg)
    cyi, cxi = int(round(CY)), int(round(CX))
    patch = crop[cyi - R:cyi + R, cxi - R:cxi + R].copy()
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, np.array(HSV_LO), np.array(HSV_HI))
    Z = 9
    big = cv2.resize(patch, (2 * R * Z, 2 * R * Z),
                     interpolation=cv2.INTER_NEAREST)
    for k in range(0, big.shape[1], 10 * Z):
        cv2.line(big, (k, 0), (k, big.shape[0]), (0, 255, 255), 1)
    for k in range(0, big.shape[0], 10 * Z):
        cv2.line(big, (0, k), (big.shape[1], k), (0, 255, 255), 1)
    bz, bzy = (mx + R) * Z, (my + R) * Z
    a = math.radians(ang)
    cv2.circle(big, (int(bz), int(bzy)), 8, (255, 0, 255), -1)
    cv2.arrowedLine(big, (int(bz), int(bzy)),
                    (int(bz + 60 * math.cos(a)), int(bzy + 60 * math.sin(a))),
                    (255, 0, 255), 5, cv2.LINE_AA)
    cv2.drawMarker(big, (R * Z, R * Z), (255, 255, 0),
                   cv2.MARKER_CROSS, 26, 2)
    bar = np.zeros((26, big.shape[1], 3), np.uint8)
    cv2.putText(bar, f"a:f{f:05d}  heading {ang:+.0f}deg  "
                     f"offset({mx:+.1f},{my:+.1f})  area {area}",
                (4, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1)
    tilesA.append(np.vstack([bar, big]))
if tilesA:
    while len(tilesA) % 4:
        tilesA.append(np.zeros_like(tilesA[0]))
    gridA = np.vstack([np.hstack(tilesA[i:i + 4])
                      for i in range(0, len(tilesA), 4)])
    hdr = np.zeros((34, gridA.shape[1], 3), np.uint8)
    cv2.putText(hdr, "Task A: player triangle heading (FIXED)  |  magenta dot "
                     "+ arrow = centroid & heading, yellow cross = minimap "
                     "centre", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                (255, 255, 255), 1)
    cv2.imwrite(str(FIG / "taskA_heading_fixed.png"),
                np.vstack([hdr, gridA]))
    print(f"\n  -> taskA_heading_fixed.png")

# ---------------------------------------------------------------- C
print("\n" + "=" * 92)
print("任务 C · 朝向稳定性（静止段圆统计）")
print("=" * 92)
prow = {int(r["idx"]): (float(r["dx_px"]), float(r["dy_px"]))
        for r in csv.DictReader(PC.open(encoding="utf-8-sig", newline=""))}
static_runs, cur = [], []
for i in range(1, len(ids)):
    ok = is_world(i) and i in prow
    if ok and math.hypot(*prow[i]) < 0.12:
        cur.append(i)
    else:
        if len(cur) >= 10:
            static_runs.append(cur)
        cur = []
if len(cur) >= 10:
    static_runs.append(cur)
print(f"  连续静止段（|d|<0.12px，每段>=10帧）: {len(static_runs)} 段")
sds = []
for run in static_runs[:12]:
    angs = [extract_heading(f)[1] for f in run if extract_heading(f)]
    if len(angs) < 8:
        continue
    rad = np.radians(np.array(angs))
    mu = np.arctan2(np.sin(rad).mean(), np.cos(rad).mean())
    Rl = math.hypot(np.cos(rad).mean(), np.sin(rad).mean())
    sd = math.degrees(math.sqrt(-2 * math.log(max(Rl, 1e-9))))
    sds.append(sd)
    print(f"    f{run[0]:05d}-f{run[-1]:05d}  {len(angs):>3}帧  "
          f"σ={sd:6.2f}°  均角={math.degrees(mu):+7.1f}°  R={Rl:.3f}")
if sds:
    print(f"\n  >> 静止段 σ: 中位 {np.median(sds):.2f}°  最大 {max(sds):.2f}°")
    verdict = ("✅ 可用（σ<10°）" if np.median(sds) < 10 else
               "🟡 勉强（10~30°）" if np.median(sds) < 30 else
               "❌ 不可用（>30°）")
    print(f"     判定: {verdict}")

# ---------------------------------------------------------------- B
print("\n" + "=" * 92)
print("任务 B · 三角朝向 vs 累计位移方向（相关性，非真值）")
print("=" * 96)
walks = list(csv.DictReader(WALK.open(encoding="utf-8-sig", newline="")))
deltas = []
for w in walks:
    a = int(w["frame_first"].split(":f")[1])
    b = int(w["frame_last"].split(":f")[1])
    vx = vy = 0.0
    for i in range(a, b):
        if i in prow:
            vx += prow[i][0]
            vy += prow[i][1]
    mv = math.degrees(math.atan2(vy, vx))
    mid = (a + b) // 2
    r = extract_heading(mid)
    if r is None:
        print(f"  a:f{a:05d}-f{b:05d}: 中点未检出")
        continue
    hd = r[1]
    d = (hd - mv + 180) % 360 - 180
    deltas.append(abs(d))
    print(f"  a:f{a:05d}-f{b:05d}: 行走 {mv:+7.1f}°  三角 {hd:+7.1f}°  "
          f"夹角 {d:+7.1f}°  {'✓' if abs(d)<45 else '✗'}")
if deltas:
    print(f"\n  >> 夹角绝对值 中位 {np.median(deltas):.1f}°  "
          f"最大 {max(deltas):.1f}°")

# ---------------------------------------------------------------- 序列
picks = [i for i in range(1, len(ids) + 1, 5) if is_world(i)]
rows = []
for f in picks:
    r = extract_heading(f)
    if r:
        (mx, my), ang, area = r
        rows.append((f, ang, mx, my, area))
with (FIG / "heading_series.csv").open("w", encoding="utf-8-sig",
                                      newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["frame", "t_sec", "heading_deg", "off_x", "off_y", "area"])
    for f, ang, mx, my, area in rows:
        w.writerow([f"a:f{f:05d}", round((f - 1) / 10.0, 1), round(ang, 2),
                    round(mx, 2), round(my, 2), area])
print(f"\n  世界帧抽样 {len(picks)}，提取成功 {len(rows)} "
      f"({100*len(rows)/max(len(picks),1):.1f}%)")
print(f"  -> heading_series.csv")
print("=== DONE ===")
