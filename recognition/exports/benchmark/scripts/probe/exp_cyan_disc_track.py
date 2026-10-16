"""任务 B · 青色圆盘（D 类）的连续帧位置稳定性 —— 区分"固定地标"与"任务标记"。

★ 判据（不需要外部知识，纯物理）
------------------------------------
小地图跟随玩家。若某图标代表**固定的世界地标**（传送锚点/神像/商店），
玩家站着不动时，它在小地图上的**像素位置恒定**。
若代表**任务标记**（会随任务进度跳到别处）或**UI 元素**（会呼吸/闪烁/位移），
位置会变。

**位置恒定 = 固定世界地标**（可做长期定位）
位置漂移 = 任务标记或 UI 元素（不可用）

同时把上一轮的"菱形模板"一并放进对比，让用户确认哪个是锚点。
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
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" \
    / "landmark"
FIG.mkdir(parents=True, exist_ok=True)


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


mm = _load("minimap_mask", PERC / "minimap_mask.py")
cfg = mm.load_circle_cfg()
ids = sorted(DA.glob("*.jpg"))
im0 = cv2.imread(str(ids[199]))
_mx, _my, _, _ = mm.minimap_rect(im0, cfg)
CCX = float(cfg["cx"]) * 2304 - _mx
CCY = float(cfg["cy"]) * 1440 - _my


def d_cyan(crop, mask):
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    sel = (((H >= 85) & (H <= 100) & (S > 60) & (V > 170)
            & (mask > 0)).astype(np.uint8))
    sel = cv2.morphologyEx(sel, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, st, ct = cv2.connectedComponentsWithStats(sel, 8)
    out = []
    for j in range(1, n):
        x, y, w, h, a = st[j]
        if 60 <= a <= 900 and 0.75 <= w / max(h, 1) <= 1.35 and \
                10 <= math.sqrt(w * h) <= 32:
            if math.hypot(ct[j][0] - CCX, ct[j][1] - CCY) < 26:
                continue
            out.append((float(ct[j][0]), float(ct[j][1]), int(w), int(h), int(a)))
    return out


RANGES = [("seg2  f00176-f00292", 176, 292),
          ("seg4  f00380-f00470", 380, 470),
          ("seg6  f00584-f00655", 584, 655),
          ("seg10 f00855-f01000", 855, 1000)]
print("=" * 92)
print("青色圆盘位置稳定性（连续帧，固定世界地标应当位置恒定）")
print("=" * 92)
allrows = []
for name, a, b in RANGES:
    print(f"\n  --- {name} ---")
    prev = None
    stats_l, stats_w, jumps = [], [], []
    for i in range(a, b + 1, 2):
        im = cv2.imread(str(ids[i - 1]))
        crop, mask = mm.make_minimap_roi(im, cfg)
        bl = d_cyan(crop, mask)
        if not bl:
            continue
        bl.sort(key=lambda z: -z[4])
        cx, cy, w, h, a_ = bl[0]
        r = math.hypot(cx - CCX, cy - CCY)
        stats_l.append(r)
        stats_w.append(w)
        allrows.append((i, cx, cy, w, h, r, a_))
        if prev is not None:
            jumps.append(math.hypot(cx - prev[0], cy - prev[1]))
        prev = (cx, cy)
    if not stats_l:
        print("    无检出")
        continue
    L = np.array(stats_l)
    W = np.array(stats_w, float)
    J = np.array(jumps) if jumps else np.array([0.0])
    print(f"    检出 {len(L)} 帧  距圆心 中位 {np.median(L):.1f} "
          f"p10 {np.percentile(L,10):.1f} p90 {np.percentile(L,90):.1f}")
    print(f"    尺寸 中位 {np.median(W):.0f}  p10 {np.percentile(W,10):.0f} "
          f"p90 {np.percentile(W,90):.0f}")
    print(f"    帧间位移 中位 {np.median(J):.2f} px  "
          f"p90 {np.percentile(J,90):.2f}  max {J.max():.2f}")
    print(f"    位置极差 {L.max()-L.min():.2f} px（距离圆心）")

# ---- 出图：青色圆盘在连续帧里的高倍裁剪序列
print("\n" + "=" * 92)
print("出图")
print("=" * 92)
TILES = []
for (i, cx, cy, w, h, r, a_) in allrows[:10]:
    im = cv2.imread(str(ids[i - 1]))
    crop, _m = mm.make_minimap_roi(im, cfg)
    pad = 20
    pc = cv2.copyMakeBorder(crop, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
    sub = pc[int(cy) + pad - 20:int(cy) + pad + 20,
             int(cx) + pad - 20:int(cx) + pad + 20]
    big = cv2.resize(sub, (200, 200), interpolation=cv2.INTER_NEAREST)
    for k in range(0, 200, 25):
        cv2.line(big, (k, 0), (k, 200), (0, 255, 255), 1)
        cv2.line(big, (0, k), (200, k), (0, 255, 255), 1)
    bar = np.zeros((44, 200, 3), np.uint8)
    cv2.putText(bar, f"a:f{i:05d}", (4, 17), cv2.FONT_HERSHEY_SIMPLEX,
                0.45, (0, 255, 255), 1)
    cv2.putText(bar, f"{w}x{h} r={r:.0f}", (4, 36),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    TILES.append(np.vstack([bar, big]))
while TILES and len(TILES) % 5:
    TILES.append(np.zeros_like(TILES[0]))
grid = np.vstack([np.hstack(TILES[i:i + 5]) for i in range(0, len(TILES), 5)])
hdr = np.zeros((32, grid.shape[1], 3), np.uint8)
cv2.putText(hdr, "Cyan disc across CONSECUTIVE frames  (cyan grid = 5px, x5)  "
                 "-  identical shape + fixed size => fixed world landmark",
            (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
cv2.imwrite(str(FIG / "cyan_disc_sequence.png"), np.vstack([hdr, grid]))
print(f"  -> cyan_disc_sequence.png  {np.vstack([hdr,grid]).shape}")

# ---- 上一轮"菱形模板"并排放，供对比指认
crop0, _ = mm.make_minimap_roi(im0, cfg)
prev_tpl = crop0[117 - 15:117 + 15, 60 - 13:60 + 13]
big = cv2.resize(prev_tpl, (300, 300), interpolation=cv2.INTER_NEAREST)
for k in range(0, 300, 30):
    cv2.line(big, (k, 0), (k, 300), (0, 255, 255), 1)
    cv2.line(big, (0, k), (300, k), (0, 255, 255), 1)
im, _m = mm.make_minimap_roi(cv2.imread(str(ids[180])), cfg)
_c, mask = mm.make_minimap_roi(cv2.imread(str(ids[180])), cfg)
bl = d_cyan(_c, mask)
cand = []
if bl:
    bl.sort(key=lambda z: -z[4])
    cx, cy, w, h, a_ = bl[0]
    pad = 20
    pc = cv2.copyMakeBorder(_c, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
    sub = pc[int(cy) + pad - 20:int(cy) + pad + 20,
             int(cx) + pad - 20:int(cx) + pad + 20]
    big2 = cv2.resize(sub, (300, 300), interpolation=cv2.INTER_NEAREST)
    for k in range(0, 300, 30):
        cv2.line(big2, (k, 0), (k, 300), (0, 255, 255), 1)
        cv2.line(big2, (0, k), (300, k), (0, 255, 255), 1)
    cand.append(big2)
left = np.vstack([np.zeros((40, 300, 3), np.uint8), big])
for row in left:
    pass
cv2.putText(left, "PREVIOUS 'diamond' template  a:f00200 (60,117) 26x30",
            (4, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 180, 180), 1)
if cand:
    right = np.vstack([np.zeros((40, 300, 3), np.uint8), cand[0]])
    cv2.putText(right, "Cyan disc  a:f00181  22x22  (D class)",
                (4, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (180, 255, 180), 1)
    pair = np.hstack([left, np.zeros((left.shape[0], 16, 3), np.uint8), right])
else:
    pair = left
hdr2 = np.zeros((32, pair.shape[1], 3), np.uint8)
cv2.putText(hdr2, "SIDE BY SIDE: previous template  vs  cyan disc  -  "
                  "which one is the teleport waypoint?", (8, 22),
            cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 1)
cv2.imwrite(str(FIG / "template_vs_cyandisc.png"), np.vstack([hdr2, pair]))
print(f"  -> template_vs_cyandisc.png  {np.vstack([hdr2,pair]).shape}")

with (FIG / "cyan_disc_track.csv").open("w", encoding="utf-8-sig",
                                        newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["frame", "cx", "cy", "w", "h", "r_from_center", "area"])
    for row in allrows:
        w.writerow([f"a:f{row[0]:05d}", round(row[1], 1), round(row[2], 1),
                    row[3], row[4], round(row[5], 1), row[6]])
print(f"  -> cyan_disc_track.csv  ({len(allrows)} 行)")
print("=== DONE ===")
