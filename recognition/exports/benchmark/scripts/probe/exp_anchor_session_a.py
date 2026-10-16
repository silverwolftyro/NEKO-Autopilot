"""任务 B/C/D · 用户锚点模板 + 七天神像，在 session_a 全量搜。

★ 三个模板
----------
T1_raw   整幅截图 216x246 @ scale 0.12 -> 26x30   （f00081 上 ncc=0.923）
T2_tight 用更严的"中性白"判据收紧残余            （浅绿底色 G 显著高于 R/B）
T3_stat  七天神像青圆盘 22x22                    （任务 D 的对照组）

★ 收紧判据修正
--------------
上一版 `S<45 & V>205` 把浅绿底色(200,225,200)也判成白，覆盖 75%。
真正区分：白色针体 mx-mn≈0，浅绿底色 mx-mn≈25。改用 **mx-mn<12**。

★ 标签策略（不循环论证）
------------------------
组 A/B **不用模板匹配结果当标签**（那是循环论证）。
用**独立方法**：NCC 峰值 + 距圆心>25px + 峰值显著性(峰/次峰)。
同时人工看图裁决。
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
SHOT = RECOG / "exports" / "benchmark" / "屏幕截图 2026-10-02 142123.png"
DA = RECOG / "raw" / "frames" / "session_20261001_a"
SEG = RECOG / "exports" / "benchmark" / "datasets" / "session_a_segments.csv"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" \
    / "landmark2"
FIG.mkdir(parents=True, exist_ok=True)
S_ANCHOR = 0.12          # f00081 上实测最佳缩放
MIN_R = 25.0             # 距圆心最小值（排除玩家自己）


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


mm = _load("minimap_mask", PERC / "minimap_mask.py")
cfg = mm.load_circle_cfg()

shot = cv2.imdecode(np.fromfile(str(SHOT), dtype=np.uint8), cv2.IMREAD_COLOR)
SH, SW = shot.shape[:2]
T1 = cv2.resize(shot, (int(SW * S_ANCHOR), int(SH * S_ANCHOR)),
                interpolation=cv2.INTER_AREA)
print(f"T1_raw  = 整幅 {SW}x{SH} @ {S_ANCHOR} -> {T1.shape[1]}x{T1.shape[0]}")

# ---- T2_tight：更严的中性白
hsv = cv2.cvtColor(shot, cv2.COLOR_BGR2HSV)
Hh, Ss, Vv = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
mx = shot.max(2).astype(int)
mn = shot.min(2).astype(int)
neut = (mx > 190) & ((mx - mn) < 12)               # 真正中性白（排除浅绿）
darkm = Vv < 150
bluem = (Hh >= 95) & (Hh <= 135) & (Ss > 110)
m = (neut | darkm | bluem).astype(np.uint8)
m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
n, lab, st, ct = cv2.connectedComponentsWithStats(m, 8)
comps = [(st[j][0], st[j][1], st[j][2], st[j][3], st[j][4])
         for j in range(1, n) if st[j][4] >= 30]
comps.sort(key=lambda c: -c[4])
if comps:
    bx0 = max(0, min(c[0] for c in comps) - 3)
    by0 = max(0, min(c[1] for c in comps) - 3)
    bx1 = min(SW, max(c[0] + c[2] for c in comps) + 3)
    by1 = min(SH, max(c[1] + c[3] for c in comps) + 3)
    T2 = shot[by0:by1, bx0:bx1].copy()
    cov = float((m[by0:by1, bx0:bx1] > 0).mean())
    print(f"T2_tight= 收紧 {TPLW if False else T2.shape[1]}x{T2.shape[0]} "
          f"@({bx0},{by0})  覆盖率 {cov:.0%}   "
          f"(收紧前 {int(SW*S_ANCHOR)}x{int(SH*S_ANCHOR)})")
else:
    T2 = T1
    print("T2_tight 收紧失败")

T2s = cv2.resize(T2, (max(8, int(T2.shape[1] * S_ANCHOR)),
                     max(8, int(T2.shape[0] * S_ANCHOR))),
                 interpolation=cv2.INTER_AREA)

# ---- T3_stat：七天神像青圆盘（取自 a:f00180）
im0 = cv2.imread(str(sorted(DA.glob("*.jpg"))[179]))
c0, mk0 = mm.make_minimap_roi(im0, cfg)
_mx, _my, _, _ = mm.minimap_rect(im0, cfg)
CCX = float(cfg["cx"]) * 2304 - _mx
CCY = float(cfg["cy"]) * 1440 - _my
h0 = cv2.cvtColor(c0, cv2.COLOR_BGR2HSV)
sel = (((h0[:, :, 0] >= 85) & (h0[:, :, 0] <= 100) & (h0[:, :, 1] > 60)
        & (h0[:, :, 2] > 170) & (mk0 > 0)).astype(np.uint8))
sel = cv2.morphologyEx(sel, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
n, lab, st, ct = cv2.connectedComponentsWithStats(sel, 8)
best3 = None
for j in range(1, n):
    x, y, w, h, a = st[j]
    if 200 <= a <= 700 and 0.85 <= w / max(h, 1) <= 1.2 and \
            math.hypot(ct[j][0] - CCX, ct[j][1] - CCY) >= 26:
        if best3 is None or a > best3[4]:
            best3 = (ct[j][0], ct[j][1], w, h, a)
if best3:
    T3 = c0[int(best3[1] - 11):int(best3[1] + 11),
            int(best3[0] - 11):int(best3[0] + 11)].copy()
    print(f"T3_stat = 青圆盘 {T3.shape[1]}x{T3.shape[0]} @ "
          f"a:f00180 ({best3[0]:.0f},{best3[1]:.0f})")
else:
    T3 = None
    print("T3_stat 未检出")

TEMPLATES = [("T1_raw", T1), ("T2_tight", T2s)]
if T3 is not None:
    TEMPLATES.append(("T3_statue", T3))
TG = [(n_, cv2.cvtColor(t, cv2.COLOR_BGR2GRAY).astype(np.float32))
      for n_, t in TEMPLATES]

# ---- session_a 全量
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


ids = sorted(DA.glob("*.jpg"))
picks = [i for i in range(1, len(ids) + 1, 3) if is_world(i)]
print(f"\nsession_a 世界帧抽样 {len(picks)}（每 3 帧取 1）")

rows = []
t0 = time.perf_counter()
for k, i in enumerate(picks, 1):
    im = cv2.imread(str(ids[i - 1]))
    crop, mask = mm.make_minimap_roi(im, cfg)
    mx0, my0, _, _ = mm.minimap_rect(im, cfg)
    cx = float(cfg["cx"]) * im.shape[1] - mx0
    cy = float(cfg["cy"]) * im.shape[0] - my0
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32)
    rec = dict(frame=f"a:f{i:05d}", idx=i)
    for tn, tg in TG:
        if tg.shape[0] > crop.shape[0] or tg.shape[1] > crop.shape[1]:
            rec[tn] = np.nan
            rec[tn + "_r"] = np.nan
            continue
        res = cv2.matchTemplate(g, tg, cv2.TM_CCOEFF_NORMED)
        rh, rw = res.shape
        yy, xx = np.ogrid[:rh, :rw]
        rr = np.hypot(xx + tg.shape[1] // 2 - cx,
                      yy + tg.shape[0] // 2 - cy)
        vv = np.where(mask[:rh, :rw] > 0, res, -1.0)
        vv = np.where(rr >= MIN_R, vv, -1.0)      # 排除玩家自己
        j = int(np.argmax(vv))
        y2, x2 = divmod(j, rw)
        rec[tn] = float(vv[y2, x2])
        rec[tn + "_r"] = float(rr[y2, x2])
        rec[tn + "_x"] = x2 + tg.shape[1] // 2
        rec[tn + "_y"] = y2 + tg.shape[0] // 2
    rows.append(rec)
    if k % 100 == 0:
        print(f"    {k}/{len(picks)}  {time.perf_counter()-t0:.0f}s")
print(f"完成 {time.perf_counter()-t0:.0f}s")

print("\n" + "=" * 92)
print("各模板在 session_a 的分数分布（已排除距圆心<25px 的玩家位置）")
print("=" * 92)
for tn, _ in TG:
    v = np.array([r[tn] for r in rows if not np.isnan(r[tn])])
    rr = np.array([r[tn + "_r"] for r in rows if not np.isnan(r[tn])])
    if len(v) == 0:
        print(f"  {tn:<10} 无数据")
        continue
    hi = (v >= 0.8).sum()
    print(f"  {tn:<10} n={len(v):<4} 中位 {np.median(v):.3f}  "
          f"p25 {np.percentile(v,25):.3f}  p75 {np.percentile(v,75):.3f}  "
          f"p95 {np.percentile(v,95):.3f}  max {v.max():.3f}")
    print(f"             >=0.8: {hi} 帧 ({hi/len(v)*100:.1f}%)   "
          f">=0.7: {(v>=0.7).sum()} 帧 ({(v>=0.7).sum()/len(v)*100:.1f}%)   "
          f"命中距圆心中位 {np.median(rr):.0f}px")

with (FIG / "session_a_anchor_scores.csv").open("w", encoding="utf-8-sig",
                                                newline="") as fh:
    cols = ["frame", "idx"]
    for tn, _ in TG:
        cols += [tn, tn + "_r", tn + "_x", tn + "_y"]
    w = csv.writer(fh)
    w.writerow(cols)
    for r in rows:
        w.writerow([r.get(c, "") if isinstance(r.get(c, ""), str)
                    else (round(r[c], 4) if c in r and not np.isnan(r[c])
                          else "") for c in cols])
print(f"  -> session_a_anchor_scores.csv")

# ---- 出图：高分帧的匹配位置
best_rows = sorted([r for r in rows if not np.isnan(r["T1_raw"])],
                   key=lambda r: -r["T1_raw"])[:6]
tiles = []
for r in best_rows:
    i = r["idx"]
    im = cv2.imread(str(ids[i - 1]))
    crop, _mk = mm.make_minimap_roi(im, cfg)
    v = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    tn = "T1_raw"
    hw, hh = T1.shape[1] // 2, T1.shape[0] // 2
    cv2.rectangle(v, ((r[tn + "_x"] - hw) * 2, (r[tn + "_y"] - hh) * 2),
                  ((r[tn + "_x"] + hw) * 2, (r[tn + "_y"] + hh) * 2),
                  (0, 0, 255), 2)
    cv2.circle(v, (int(CCX * 2), int(CCY * 2)), 10, (255, 0, 255), 2)
    bar = np.zeros((26, v.shape[1], 3), np.uint8)
    cv2.putText(bar, f"{r['frame']}  ncc={r[tn]:.3f}  r={r[tn+'_r']:.0f}px",
                (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    tiles.append(np.vstack([bar, v]))
if tiles:
    grid = np.vstack([np.hstack(tiles[j:j + 3])
                      for j in range(0, len(tiles), 3)])
    hdr = np.zeros((30, grid.shape[1], 3), np.uint8)
    cv2.putText(hdr, "session_a top-6 hits of USER ANCHOR template (T1_raw)  "
                     "red = match, magenta = player", (8, 21),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
    cv2.imwrite(str(FIG / "session_a_anchor_hits.png"), np.vstack([hdr, grid]))
    print(f"  -> session_a_anchor_hits.png")
print("=== DONE ===")
