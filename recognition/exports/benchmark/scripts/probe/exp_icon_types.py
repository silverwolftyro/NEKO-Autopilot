"""任务 A · 枚举小地图上所有图标类型（不做类型假设）。

★ 为什么要枚举而不是直接认
----------------------------
上一轮用"HSV 蓝紫 + 连通域"抓到一个蓝紫菱形就当成了传送锚点。
**这是假设，不是证据。** 必须先把小地图上**所有**图标类型列出来，
再逐一归类，最后让用户确认。

★ 图标 vs 地图内容，怎么分
--------------------------
小地图的地图内容是从米游社矢量图渲染的：颜色集中在
青绿/沙褐/深绿几个色系，边缘柔和。
图标是**叠加层**：有**近白描边** + **高饱和填充** + **小尺度高频边缘**。
所以用两个正交特征打分：
  1. 近白像素占比（描边）
  2. 局部 Laplacian 强度（高频小结构）

两者的乘积就是"图标似然"。阈值不拍脑袋 —— 从直方图取峰。

流程：
  1. 每帧算 iconness 图，找局部极大
  2. 裁 32x32 归一化裁块
  3. 跨帧聚类（NCC 互相匹配阈值 0.75）
  4. 每簇输出：代表裁块 + 出现频次 + 颜色统计 + 典型尺寸
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
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

PERC = (Path(PLUGIN_DIR.parent)
        / "genshin_game_buddy" / "autopilot" / "perception")
RECOG = Path(RECOG_ROOT)
DA = RECOG / "raw" / "frames" / "session_20261001_a"
SEG = RECOG / "exports" / "benchmark" / "datasets" / "session_a_segments.csv"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" \
    / "landmark"
FIG.mkdir(parents=True, exist_ok=True)

ICON = 32          # 归一化裁块边长
R = 16             # 原始裁块半径
NFRAMES = 90       # 抽帧数


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


mm = _load("minimap_mask", PERC / "minimap_mask.py")
cfg = mm.load_circle_cfg()
ids = sorted(DA.glob("*.jpg"))
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


im0 = cv2.imread(str(ids[199]))
_mx, _my, _, _ = mm.minimap_rect(im0, cfg)
CCX = float(cfg["cx"]) * 2304 - _mx
CCY = float(cfg["cy"]) * 1440 - _my
# 玩家箭头中心区，半径 26 px 全部排除
PLAYER_GUARD = 26


def iconness_map(crop, mask):
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    S = hsv[:, :, 1].astype(np.float32)
    white = ((g > 195) & (S < 60)).astype(np.float32)      # 近白描边
    lap = np.abs(cv2.Laplacian(g.astype(np.float32), cv2.CV_32F, ksize=3))
    ic = cv2.boxFilter(white * np.clip(lap / 300.0, 0, 2), -1, (R, R))
    return np.where(mask > 0, ic, 0.0), g, S


def icon_candidates(crop, mask, q=99.90):
    """阈值取**该帧 iconness 分布的高分位**，不拍脑袋。"""
    iconness, g, S = iconness_map(crop, mask)
    m = mask > 0
    if m.sum() == 0:
        return []
    thr = max(float(np.percentile(iconness[m], q)), 1e-6)
    h, w = g.shape
    out = []
    for y in range(R + 2, h - R - 2):
        for x in range(R + 2, w - R - 2):
            if math.hypot(x - CCX, y - CCY) < PLAYER_GUARD:
                continue
            v = iconness[y, x]
            if v < thr:
                continue
            if v < iconness[y - 3:y + 4, x - 3:x + 4].max():
                continue
            gs = g[y - R:y + R, x - R:x + R]
            ss = S[y - R:y + R, x - R:x + R]
            out.append((x, y, float(v),
                        float(((gs > 195) & (ss < 60)).mean()),
                        float(ss.mean())))
    return out



def norm_crop(crop, x, y):
    sub = crop[y - R:y + R, x - R:x + R]
    if sub.shape[0] != 2 * R or sub.shape[1] != 2 * R:
        return None
    g = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (ICON, ICON), interpolation=cv2.INTER_AREA)
    g = cv2.GaussianBlur(g, (3, 3), 0)
    g = g - g.mean()
    n = np.linalg.norm(g)
    return g / n if n > 1e-6 else None


picks = [i for i in range(1, len(ids) + 1, 8) if is_world(i)][:NFRAMES]
print(f"世界帧抽样 {len(picks)} 帧（每 8 帧取 1）")

# 先看一帧的 iconness 分布，确认分位数阈值合理
_im = cv2.imread(str(ids[199]))
_c, _mk = mm.make_minimap_roi(_im, cfg)
_ic, _, _ = iconness_map(_c, _mk)
_v = _ic[_mk > 0]
print(f"  a:f00200 iconness: 中位 {np.median(_v):.3f}  "
      f"p99 {np.percentile(_v,99):.3f}  p99.5 {np.percentile(_v,99.5):.3f}  "
      f"p99.9 {np.percentile(_v,99.9):.3f}  max {_v.max():.3f}")
print(f"  -> 用 p99.5 作阈值（该帧约 {len(_v)*0.005:.0f} 个候选）")

clusters = []            # [{reps:[vec], members:[...]}]
raw_by_frame = {}
t0 = time.perf_counter()
for k, i in enumerate(picks, 1):
    im = cv2.imread(str(ids[i - 1]))
    crop, mask = mm.make_minimap_roi(im, cfg)
    cands = icon_candidates(crop, mask, q=99.5)
    cands.sort(key=lambda z: -z[2])
    cands = cands[:6]                 # 每帧最多 6 个，避免地图纹理泛滥
    raw_by_frame[i] = cands
    for (x, y, sc, wh, sat) in cands:
        v = norm_crop(crop, x, y)
        if v is None:
            continue
        best, bi = 0.0, -1
        for ci, cl in enumerate(clusters):
            for rv in cl["reps"]:
                s = float((v * rv).sum())
                if s > best:
                    best, bi = s, ci
        if best >= 0.75 and bi >= 0:
            clusters[bi]["reps"].append(v)
            clusters[bi]["members"].append((i, x, y, sc, wh, sat))
        else:
            clusters.append(dict(reps=[v], members=[(i, x, y, sc, wh, sat)]))
    if k % 20 == 0:
        print(f"    {k}/{len(picks)}  clusters={len(clusters)}  "
              f"{time.perf_counter()-t0:.0f}s")

clusters.sort(key=lambda c: -len(c["members"]))
print(f"\n聚类完成：{len(clusters)} 个候选类型，"
      f"耗时 {time.perf_counter()-t0:.0f}s")
print(f"\n{'簇':>4}{'出现':>6}{'帧数':>6}{'白边占比':>10}{'饱和度':>9}"
      f"  代表帧(最常见)")
rows = []
for ci, cl in enumerate(clusters[:12]):
    ms = cl["members"]
    wh = np.mean([m[4] for m in ms])
    sa = np.mean([m[5] for m in ms])
    fc = defaultdict(int)
    for m in ms:
        fc[m[0]] += 1
    top = max(fc.items(), key=lambda kv: kv[1])
    print(f"{ci:>4}{len(ms):>6}{len(fc):>6}{wh:>10.3f}{sa:>9.1f}"
          f"  a:f{top[0]:05d} (x{top[1]})")
    rows.append(dict(cluster=ci, count=len(ms), nframes=len(fc),
                      whiteness=round(float(wh), 3),
                      saturation=round(float(sa), 1),
                      top_frame=f"a:f{top[0]:05d}",
                      top_x=top[1], top_y=ms[0][3] if False else None))

# 出图：每个簇的代表裁块（从其出现最频繁的帧裁）
TILES = []
for ci, cl in enumerate(clusters[:12]):
    fc = defaultdict(list)
    for m in cl["members"]:
        fc[m[0]].append(m)
    best_i, best_n = max(fc.items(), key=lambda kv: len(kv[1]))
    x, y = best_n[0][1], best_n[0][2]
    im = cv2.imread(str(ids[best_i - 1]))
    crop, _ = mm.make_minimap_roi(im, cfg)
    sub = crop[max(0, y - R):y + R, max(0, x - R):x + R]
    if sub.shape[0] < 2 * R or sub.shape[1] < 2 * R:
        continue
    big = cv2.resize(sub, (200, 200), interpolation=cv2.INTER_NEAREST)
    for kk in range(0, 200, 25):
        cv2.line(big, (kk, 0), (kk, 200), (0, 255, 255), 1)
        cv2.line(big, (0, kk), (200, kk), (0, 255, 255), 1)
    bar = np.zeros((46, 200, 3), np.uint8)
    cv2.putText(bar, f"#{ci}  n={len(cl['members'])}", (4, 17),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    cv2.putText(bar, f"a:f{best_i:05d}", (4, 36),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
    TILES.append(np.vstack([bar, big]))
while TILES and len(TILES) % 6:
    TILES.append(np.zeros_like(TILES[0]))
grid = np.vstack([np.hstack(TILES[i:i + 6]) for i in range(0, len(TILES), 6)])
hdr = np.zeros((32, grid.shape[1], 3), np.uint8)
cv2.putText(hdr, "All icon types found on session_a minimaps  (cyan grid = 5px, "
                 "x6.25)  -  NO type assumed, please label them",
            (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
cv2.imwrite(str(FIG / "icon_types_all.png"), np.vstack([hdr, grid]))
print(f"\n  -> icon_types_all.png  {np.vstack([hdr,grid]).shape}")

with (FIG / "icon_types.csv").open("w", encoding="utf-8-sig",
                                   newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["cluster", "n_hits", "n_frames", "whiteness", "saturation",
                "top_frame", "top_x", "top_y"])
    for ci, cl in enumerate(clusters[:12]):
        fc = defaultdict(list)
        for m in cl["members"]:
            fc[m[0]].append(m)
        best_i, best_n = max(fc.items(), key=lambda kv: len(kv[1]))
        w.writerow([ci, len(cl["members"]), len(fc),
                    round(float(np.mean([m[4] for m in cl["members"]])), 3),
                    round(float(np.mean([m[5] for m in cl["members"]])), 1),
                    f"a:f{best_i:05d}", best_n[0][1], best_n[0][2]])
print(f"  -> icon_types.csv")
print("=== DONE ===")
