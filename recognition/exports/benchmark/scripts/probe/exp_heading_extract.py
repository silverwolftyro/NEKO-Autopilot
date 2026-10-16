"""任务 A/B/C · 玩家朝向提取与验证。

★ 提取方法（已由 centre_probe.png 目视确认可行）
----------------------------------------------------
在圆心 ±R 窗口内取"青蓝"像素（HSV H∈[85,105], S>90, V>120）。
掩膜图精确勾出三角，尖端方向即朝���。
- 质心 = 三角位置（略偏离圆心是正常的，视野锥有偏移）
- 朝向 = 用掩膜的**凸包最小外接三角形**，或 PCA 主轴
  （PCA 对"上粗下尖"的三角会偏向长边，凸包更准）

★ 验证（B）为什么这样验
----------------------
任务书要"朝向已知"的配对帧。session_a 里没有 —— 我不编。
但有一个**不需要真值**的自洽判据：
    玩家三角形朝向 ≈ 相位相关测出的**行走方向**
    （玩家朝哪走，镜头就朝哪，三角就指哪）
拿三段真实走路段的累计位移方向 vs 三角朝向比对。
**这是相关性不是真值**，所以只作"方向一致性"证据，不宣称绝对朝向正确。

★ 一个诚实的前提
----------------
"固定地图方向"是用户确认的设置。若设置被改（地图跟随角色旋转），
三角指向的物理含义会变，位移-朝向一致性会失效。
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
DA = RECOG / "raw" / "frames" / "session_20261001_a"
PC = RECOG / "exports" / "benchmark" / "datasets" / "phase_corr.csv"
WALK = RECOG / "exports" / "benchmark" / "datasets" / "walk_segments.csv"
SEG = RECOG / "exports" / "benchmark" / "datasets" / "session_a_segments.csv"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" / "heading"
FIG.mkdir(parents=True, exist_ok=True)

# 提取参数（HSV 范围由 centre_probe.png 目视标定）
R = 30
HSV_LO = (85, 90, 120)
HSV_HI = (105, 255, 255)
MIN_PIX = 40
MAX_PIX = 1600


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


def extract_heading(frame):
    """从小地图提取玩家三角：返回 (centroid, angle_deg) 或 None。

    :returns: ``((cx, cy), angle)``。angle 是"三角尖端在图像坐标系
        （x 向右、y 向下）里的方向角，``atan2(dy, dx)``。
        尖端朝上 = -90°。``None`` 表示这一帧提取不到（黑场/菜单）。
    """
    im = cv2.imread(str(ids[frame - 1]))
    if im is None:
        return None
    crop, _ = mm.make_minimap_roi(im, cfg)
    cyi, cxi = int(round(CY)), int(round(CX))
    y1, x1 = cyi + R, cxi + R
    patch = crop[max(0, cyi - R):y1, max(0, cxi - R):x1]
    if patch.shape[0] < 8 or patch.shape[1] < 8:
        return None
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, np.array(HSV_LO), np.array(HSV_HI))
    n = int((m > 0).sum())
    if n < MIN_PIX or n > MAX_PIX:
        return None
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    if cv2.contourArea(c) < MIN_PIX / 2:
        return None
    # 质心（相对裁块坐标）
    M = cv2.moments(c)
    if abs(M["m00"]) < 1e-6:
        return None
    mx = M["m10"] / M["m00"]
    my = M["m01"] / M["m00"]
    # 朝向：拟合最小外接三角形的"尖端"。三角是上粗下尖或反之，
    # 用旋转矫正后的形状判断哪端是尖：尖端的二阶矩更小。
    rect = cv2.minAreaRect(c)
    (cxr, cyr), (w, h), ang = rect
    box = cv2.boxPoints(rect)
    box = box.astype(np.float32)
    ca, sa = math.cos(math.radians(ang)), math.sin(math.radians(ang))
    Rm = np.array([[ca, sa], [-sa, ca]], np.float32)
    local = (box - np.array([cxr, cyr], np.float32)) @ Rm.T
    ys, xs = local[:, 1], local[:, 0]
    # 沿长轴（y）分成两半，比较两半的宽度：窄的一半是尖端
    half = (ys.min() + ys.max()) / 2
    top_w = xs[ys <= half].ptp() if (ys <= half).sum() > 1 else 0
    bot_w = xs[ys >= half].ptp() if (ys >= half).sum() > 1 else 0
    # 尖端 = 宽度更小的一半
    tip_is_top = top_w <= bot_w
    # 尖端方向（世界图像坐标）: 长轴方向 ±
    axis = math.radians(ang)          # box 长轴方向
    # 沿 y 为长轴 -> 方向是 ±(90° - ang)... 直接用几何算
    # 尖端在 local y 的 +侧 或 -侧
    tip_local_y = ys.min() if tip_is_top else ys.max()
    # 把 local 方向转回图像方向
    dir_local = np.array([0.0, np.sign(tip_local_y) if tip_local_y else 1.0])
    dir_img = Rm.T @ dir_local
    angle = math.degrees(math.atan2(float(dir_img[1]), float(dir_img[0])))
    return (mx, my), angle


# ---------------------------------------------------------------- 任务 A
print("=" * 92)
print("任务 A · 提取玩家三角（圆心 ±%d 窗口，HSV H%s）" % (R, HSV_LO[:1]))
print("=" * 92)
sample_frames = [200, 400, 600, 900, 1100, 1300, 1450]
print(f"  {'帧':<12}{'质心(相对圆心)':>20}{'朝向角':>10}")
tilesA = []
for f in sample_frames:
    r = extract_heading(f)
    if r is None:
        print(f"  a:f{f:05d}      —— 未检出")
        continue
    (mx, my), ang = r
    print(f"  a:f{f:05d}   ({mx-CX:+6.1f},{my-CY:+6.1f}){ang:>10.1f}°")
    # 画：掩膜 + 质心 + 方向箭头
    im = cv2.imread(str(ids[f - 1]))
    crop, _ = mm.make_minimap_roi(im, cfg)
    cyi, cxi = int(round(CY)), int(round(CX))
    patch = crop[cyi - R:cyi + R, cxi - R:cxi + R].copy()
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, np.array(HSV_LO), np.array(HSV_HI))
    Z = 8
    big = cv2.resize(patch, (2 * R * Z, 2 * R * Z),
                     interpolation=cv2.INTER_NEAREST)
    for k in range(0, big.shape[1], 10 * Z):
        cv2.line(big, (k, 0), (k, big.shape[0]), (0, 255, 255), 1)
    for k in range(0, big.shape[0], 10 * Z):
        cv2.line(big, (0, k), (big.shape[1], k), (0, 255, 255), 1)
    bz = (mx + R) * Z
    bzy = (my + R) * Z
    cv2.circle(big, (int(bz), int(bzy)), 7, (255, 0, 255), -1)
    a = math.radians(ang)
    cv2.arrowedLine(big, (int(bz), int(bzy)),
                    (int(bz + 40 * math.cos(a)), int(bzy + 40 * math.sin(a))),
                    (255, 0, 255), 4, cv2.LINE_AA)
    cv2.drawMarker(big, (R * Z, R * Z), (255, 255, 0),
                   cv2.MARKER_CROSS, 24, 2)
    bar = np.zeros((24, big.shape[1], 3), np.uint8)
    cv2.putText(bar, f"a:f{f:05d}  heading {ang:+.0f}deg  "
                     f"centroid({mx-CX:+.0f},{my-CY:+.0f})", (4, 17),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    tilesA.append(np.vstack([bar, big]))
if tilesA:
    while len(tilesA) % 4:
        tilesA.append(np.zeros_like(tilesA[0]))
    gridA = np.vstack([np.hstack(tilesA[i:i + 4])
                      for i in range(0, len(tilesA), 4)])
    hdr = np.zeros((32, gridA.shape[1], 3), np.uint8)
    cv2.putText(hdr, "Task A: player-triangle heading extraction  |  "
                     "magenta = centroid + heading arrow, yellow = minimap "
                     "centre, grid 10px x8", (8, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
    cv2.imwrite(str(FIG / "taskA_heading_extract.png"),
                np.vstack([hdr, gridA]))
    print(f"\n  -> taskA_heading_extract.png")

# ---------------------------------------------------------------- 任务 C
print("\n" + "=" * 92)
print("任务 C · 朝向稳定性（连续帧噪声）")
print("=" * 92)
# 找一段玩家不动的连续段（位移≈0），量朝向抖动
prow = {int(r["idx"]): (float(r["dx_px"]), float(r["dy_px"]))
        for r in csv.DictReader(PC.open(encoding="utf-8-sig", newline=""))}
static_runs = []
cur = []
for i in range(1, len(ids)):
    if not is_world(i) or i not in prow:
        if len(cur) >= 8:
            static_runs.append(cur)
        cur = []
        continue
    dx, dy = prow[i]
    if math.hypot(dx, dy) < 0.12:        # 静止阈值
        cur.append(i)
    else:
        if len(cur) >= 8:
            static_runs.append(cur)
        cur = []
if len(cur) >= 8:
    static_runs.append(cur)
print(f"  找到 {len(static_runs)} 段连续静止（|d|<0.12px，每段>=8帧）")
stds = []
samples = []
for run in static_runs[:12]:
    angs = []
    for f in run:
        r = extract_heading(f)
        if r:
            angs.append(r[1])
    if len(angs) < 6:
        continue
    A = np.array(angs)
    # 角度环绕 -> 用复数均值算圆统计标准差
    rad = np.radians(A)
    mu = math.atan2(np.sin(rad).mean(), np.cos(rad).mean())
    R_len = math.hypot(np.cos(rad).mean(), np.sin(rad).mean())
    circ_sd = math.degrees(math.sqrt(-2 * math.log(max(R_len, 1e-9))))
    stds.append(circ_sd)
    samples.append((run[0], run[-1], circ_sd, len(angs)))
    print(f"    f{run[0]:05d}-f{run[-1]:05d}  {len(angs):>3} 帧  "
          f"圆统计σ={circ_sd:5.2f}°  平均角={math.degrees(mu):+7.1f}°  "
          f"集中度R={R_len:.3f}")
if stds:
    print(f"\n  >> 静止段朝向圆统计σ: 中位 {np.median(stds):.2f}°  "
          f"最大 {max(stds):.2f}°")
    print("     （σ 小 = 三角稳，方向提取可用；σ 大 = 提取噪声高）")

# ---------------------------------------------------------------- 任务 B
print("\n" + "=" * 92)
print("任务 B · 方向一致性（三角朝向 vs 累计位移方向）")
print("=" * 92)
walks = list(csv.DictReader(WALK.open(encoding="utf-8-sig", newline="")))
print("  用三段真实走路：把整段 dx/dy 累加成'行走方向'，")
print("  与段中点那一帧的三角朝向比夹角。")
deltas = []
for w in walks:
    a = int(w["frame_first"].split(":f")[1])
    b = int(w["frame_last"].split(":f")[1])
    vx = vy = 0.0
    for i in range(a, b):
        if i in prow:
            vx += prow[i][0]
            vy += prow[i][1]
    move_ang = math.degrees(math.atan2(vy, vx))
    mid = (a + b) // 2
    r = extract_heading(mid)
    if r is None:
        print(f"  a:f{a:05d}-f{b:05d}: 中点 f{mid:05d} 未检出三角")
        continue
    head_ang = r[1]
    d = (head_ang - move_ang + 180) % 360 - 180
    deltas.append(abs(d))
    print(f"  a:f{a:05d}-f{b:05d}: 行走方向 {move_ang:+7.1f}°  "
          f"三角朝向 {head_ang:+7.1f}°  夹角 {d:+7.1f}°  "
          f"{'✓一致' if abs(d)<45 else '✗不一致'}")
if deltas:
    print(f"\n  >> 夹角绝对值: 中位 {np.median(deltas):.1f}°  "
          f"最大 {max(deltas):.1f}°  "
          f"（<45° 视为一致）")
    print("     ★ 这是**相关性**不是真值：玩家可能侧身/倒退走，")
    print("       所以只证明'三角大致跟着行走方向转'，不证明绝对朝向对。")

# ---------------------------------------------------------------- 出图
print("\n" + "=" * 92)
print("保存朝向时间序列")
print("=" * 92)
picks = [i for i in range(1, len(ids) + 1, 5) if is_world(i)]
rows = []
for f in picks:
    r = extract_heading(f)
    if r:
        (mx, my), ang = r
        rows.append((f, ang, mx - CX, my - CY))
with (FIG / "heading_series.csv").open("w", encoding="utf-8-sig",
                                      newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["frame", "t_sec", "heading_deg", "centroid_dx", "centroid_dy"])
    for f, ang, dx_, dy_ in rows:
        w.writerow([f"a:f{f:05d}", round((f - 1) / 10.0, 1), round(ang, 2),
                    round(dx_, 2), round(dy_, 2)])
n_ok = len(rows)
print(f"  世界帧抽样 {len(picks)}，成功提取 {n_ok} "
      f"({100*n_ok/max(len(picks),1):.1f}%)")
print(f"  -> heading_series.csv")
print("=== DONE ===")
