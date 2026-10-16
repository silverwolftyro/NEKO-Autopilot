"""ORB 定位严格验证 · 实验 E 补完。

从 session_a 每隔 10 帧抽一帧，切小地图，ORB 匹配 L2 全图，记录
匹配点数 + 最高分位置 + 位置连续性，输出 Top-5 三格拼图。

★ 关键设计
----------
* **L2 ORB 库只建一次**（0.4s / 60000 特征点），152 帧共用。
* **位置不是"匹配点的质心"，而是仿射变换把小地图圆心映射过去**——
  质心会被"半张图匹配上"的偏置拉偏，圆心映射是物理上正确的位置。
* **同时记录尺度 / 旋转 / 内点空间散布**，这三个是识别假阳性的关键：
  - 小地图是正北朝上的，旋转应当恒为 0
  - 小地图缩放档固定，拟合尺度应当恒定
  - 一次匹配覆盖的底图范围有限，内点不该撒满整张图

只读帧，不改 minimap_locator.py，不引入新依赖。
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
SEG = RECOG / "exports" / "benchmark" / "datasets" / "session_a_segments.csv"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" / "orb_e"
FIG.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- 参数
SAMPLE_EVERY = 10          # 每隔 10 帧抽一帧
MAP_LEVEL = 2              # 底图层（2 = 9216x4608，4 m/px）
MAP_FEATURES = 60000       # L2 ORB 库特征点数
Q_FEATURES = 2000          # 小地图 ORB 特征点数
LOWE_RATIO = 0.78          # Lowe ratio test
RANSAC_REPROJ = 3.0        # RANSAC 重投影阈值（L2 像素）
MIN_INLIERS = 6            # 少于这个数直接判"无有效解"
JUMP_L2 = 500.0            # 连续帧位置跳变阈值（L2 像素）
CROP = 512                 # 拼图每格边长


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


mm = _load("minimap_mask", PERC / "minimap_mask.py")
pl = _load("pyramid_loader", PERC / "pyramid_loader.py")

# ---------------------------------------------------------------- 底图侧
anchors = json.loads(ANCH.read_text(encoding="utf-8"))["points"]
L0_W, L0_H = json.loads(ANCH.read_text(encoding="utf-8"))["image_size"]
K = 1.0 / (2 ** MAP_LEVEL)               # L0 -> 当前层
anc_l = [(a["name"], a["x"] * K, a["y"] * K) for a in anchors]

print(f"锚点 {len(anchors)}  L0={L0_W}x{L0_H}  "
      f"当前层 L{MAP_LEVEL} 缩放 {K}  (1 px = {1/K:.0f} m)")

t0 = time.perf_counter()
PY = pl.PyramidLoader(preload=())
MAP = PY.get_level(MAP_LEVEL)
MH, MW = MAP.shape
print(f"L{MAP_LEVEL} = {MW}x{MH}  读盘+常驻 {time.perf_counter()-t0:.1f}s")

t0 = time.perf_counter()
ORB_MAP = cv2.ORB_create(nfeatures=MAP_FEATURES, nlevels=10,
                         scaleFactor=1.2, fastThreshold=5)
kp_map, ds_map = ORB_MAP.detectAndCompute(MAP, None)
mp = np.array([k.pt for k in kp_map], dtype=np.float32)
print(f"L{MAP_LEVEL} ORB 库: {len(kp_map)} 特征点  {time.perf_counter()-t0:.1f}s")

ORB_Q = cv2.ORB_create(nfeatures=Q_FEATURES, nlevels=8,
                       scaleFactor=1.2, fastThreshold=5)
BF = cv2.BFMatcher(cv2.NORM_HAMMING)

# ---------------------------------------------------------------- 分段
segs = []
with SEG.open(encoding="utf-8-sig", newline="") as fh:
    for r in csv.DictReader(fh):
        segs.append((int(r["frame_first"][1:]), int(r["frame_last"][1:]),
                     r["raw_prediction"] == "world"))


def is_world(idx: int) -> bool:
    for a, b, w in segs:
        if a <= idx <= b:
            return w
    return False


# ---------------------------------------------------------------- 抽帧
ids = sorted(DA.glob("*.jpg"))
picks = [(i + 1, ids[i].stem) for i in range(0, len(ids), SAMPLE_EVERY)]
print(f"\n抽帧: 每 {SAMPLE_EVERY} 帧一帧 -> {len(picks)} 帧 "
      f"(a:{picks[0][1]} .. a:{picks[-1][1]})")
n_world = sum(1 for i, _ in picks if is_world(i))
print(f"  其中大世界段 {n_world} 帧 / 非世界 {len(picks)-n_world} 帧")

cfg = mm.load_circle_cfg()
rows = []
t0 = time.perf_counter()
for n, (idx, stem) in enumerate(picks, 1):
    im = cv2.imread(str(ids[idx - 1]))
    if im is None:
        print(f"  [{n}/{len(picks)}] {stem} 读帧失败")
        continue
    crop, mask = mm.make_minimap_roi(im, cfg)
    H, W = im.shape[:2]
    x0, y0, x1, y1 = mm.minimap_rect(im, cfg)
    # 圆心在小地图裁块里的位置（玩家所在点）
    ccx = float(cfg["cx"]) * W - x0
    ccy = float(cfg["cy"]) * H - y0

    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    kq, dq = ORB_Q.detectAndCompute(g, mask)
    r = dict(idx=idx, stem=stem, world=is_world(idx), qkp=0, good=0,
             inl=0, x=np.nan, y=np.nan, scale=np.nan, rot=np.nan,
             spread=np.nan, med=np.nan, skip="")
    if dq is None or len(dq) < 4:
        r["skip"] = "小地图无特征"
        rows.append(r)
        continue
    r["qkp"] = len(kq)
    good = [m for m, n2 in BF.knnMatch(dq, ds_map, k=2)
            if m.distance < LOWE_RATIO * n2.distance]
    r["good"] = len(good)
    if len(good) < 4:
        r["skip"] = "good<4"
        rows.append(r)
        continue

    src = np.float32([kq[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([mp[m.trainIdx] for m in good]).reshape(-1, 1, 2)
    # 相似变换（缩放+旋转+平移），小地图正北朝上 -> 旋转应恒为 0
    A, inl = cv2.estimateAffinePartial2D(
        src, dst, method=cv2.RANSAC,
        ransacReprojThreshold=RANSAC_REPROJ, maxIters=20000)
    if A is None:
        r["skip"] = "仿射无解"
        rows.append(r)
        continue
    r["inl"] = int(inl.sum()) if inl is not None else 0
    if r["inl"] < MIN_INLIERS:
        r["skip"] = f"inl<{MIN_INLIERS}"
        rows.append(r)
        continue

    a, b, tx = A[0, 0], A[0, 1], A[0, 2]
    c, d, ty = A[1, 0], A[1, 1], A[1, 2]
    r["scale"] = math.hypot(a, b)
    r["rot"] = math.degrees(math.atan2(b, a))
    px = a * ccx + b * ccy + tx
    py = c * ccx + d * ccy + ty
    r["x"], r["y"] = px, py

    # 内点在底图上的空间散布（假阳性会撒满整张图）
    keep = dst[inl.ravel().astype(bool)].reshape(-1, 2)
    if len(keep) >= 2:
        r["spread"] = float(np.mean(np.std(keep, axis=0)))

    # 落点是否越界
    if not (0 <= px < MW and 0 <= py < MH):
        r["skip"] = "落点越界"
    rows.append(r)
    if n % 20 == 0:
        print(f"  [{n}/{len(picks)}] {stem} world={r['world']} "
              f"good={r['good']} inl={r['inl']} "
              f"pos=({r['x']:.0f},{r['y']:.0f}) {time.perf_counter()-t0:.0f}s")

print(f"匹配阶段 {time.perf_counter()-t0:.1f}s")

# ---------------------------------------------------------------- 统计
def stat(name, v):
    v = np.asarray([x for x in v if x is not None and
                    not (isinstance(x, float) and math.isnan(x))],
                   dtype=float)
    if len(v) == 0:
        print(f"  {name:<24} (空)")
        return v
    print(f"  {name:<24} n={len(v):<4} min={v.min():8.2f} p25={np.percentile(v,25):8.2f} "
          f"中位={np.median(v):8.2f} p75={np.percentile(v,75):8.2f} max={v.max():8.2f}")
    return v


valid = [r for r in rows if r["skip"] == ""]
print("\n" + "=" * 92)
print("1. 匹配结果统计")
print("=" * 92)
print(f"  抽帧 {len(rows)}  有解 {len(valid)}  无解 "
      f"{len(rows)-len(valid)}")
sk = {}
for r in rows:
    if r["skip"]:
        sk[r["skip"]] = sk.get(r["skip"], 0) + 1
for k_, v_ in sorted(sk.items(), key=lambda z: -z[1]):
    print(f"    无解原因 {k_:<16} {v_}")

wv = [r for r in valid if r["world"]]
nv = [r for r in valid if not r["world"]]
print(f"\n  大世界帧 {len(wv)} 帧有解 / 非世界帧 {len(nv)} 帧有解")
print("\n  [全部有解帧]")
stat("小地图特征点", [r["qkp"] for r in valid])
stat("good matches", [r["good"] for r in valid])
stat("RANSAC 内点", [r["inl"] for r in valid])
stat("内点占比", [r["inl"] / max(r["good"], 1) for r in valid])
stat("拟合尺度", [r["scale"] for r in valid])
stat("拟合旋转(度)", [r["rot"] for r in valid])
stat("内点空间散布", [r["spread"] for r in valid])
if wv:
    print("\n  [仅大世界帧]")
    stat("good matches", [r["good"] for r in wv])
    stat("RANSAC 内点", [r["inl"] for r in wv])
    stat("内点占比", [r["inl"] / max(r["good"], 1) for r in wv])
    stat("拟合尺度", [r["scale"] for r in wv])
    stat("拟合旋转(度)", [r["rot"] for r in wv])
    stat("内点空间散布", [r["spread"] for r in wv])

# ---------------------------------------------------------------- 连续性
print("\n" + "=" * 92)
print(f"2. 位置连续性（阈值 {JUMP_L2:.0f} L2 px = {JUMP_L2/K:.0f} m）")
print("=" * 92)
jump_rows = []
prev = None
for r in rows:
    if r["skip"] != "":
        prev = None                      # 断档，不跨段比
        continue
    if prev is None:
        r["jump"] = np.nan
        r["susp"] = False
    else:
        dj = math.hypot(r["x"] - prev["x"], r["y"] - prev["y"])
        r["jump"] = dj
        r["susp"] = bool(dj > JUMP_L2)
    jump_rows.append(r)
    prev = r
jv = [r["jump"] for r in rows if r.get("jump") is not None
      and not (isinstance(r["jump"], float) and math.isnan(r["jump"]))]
if jv:
    stat("帧间位移(L2 px)", jv)
    ns = sum(1 for r in rows if r.get("susp"))
    print(f"  可疑(位移 > {JUMP_L2:.0f})  {ns} 帧")
# 只在世界段内部看
print("\n  [仅大世界帧之间的位移]")
jw = []
prev = None
for r in rows:
    if not r["world"]:
        prev = None
        continue
    if r["skip"] != "":
        prev = None
        continue
    if prev is not None:
        jw.append(math.hypot(r["x"] - prev["x"], r["y"] - prev["y"]))
    prev = r
stat("世界段内位移", jw)

# ---------------------------------------------------------------- 质量排序
# 先验约束：旋转必须接近 0（正北朝上），尺度必须与全局中位一致
sc_all = np.array([r["scale"] for r in wv], dtype=float)
sc_med = float(np.median(sc_all)) if len(sc_all) else float("nan")
print(f"\n  世界帧拟合尺度中位 = {sc_med:.4f} L2 px / 小地图 px "
      f"= {sc_med/K:.3f} m / 小地图 px"
      f"  (小地图半径 {cfg['r_norm_by_width']*2304:.0f}px -> 覆盖 "
      f"{cfg['r_norm_by_width']*2304*sc_med/K:.0f} m 半径)")

for r in rows:
    if r["skip"] != "":
        r["score"] = -1
        continue
    pen = 1.0
    if not math.isnan(r["rot"]):
        pen *= math.exp(-(r["rot"] / 5.0) ** 2)         # 旋转偏离 0 的惩罚
    if not math.isnan(sc_med) and sc_med > 0:
        pen *= math.exp(-((math.log(r["scale"] / sc_med)) / 0.25) ** 2)
    r["score"] = r["inl"] * pen

cand = sorted([r for r in rows if r["skip"] == "" and r["world"]],
              key=lambda r: -r["score"])
print(f"\n  候选(有解且大世界) {len(cand)} 帧，Top 分数: "
      f"{[ (c['stem'], int(c['inl']), round(c['score'],1)) for c in cand[:8] ]}")


def nearest_anchor(x, y):
    return min(anc_l, key=lambda a: (a[1] - x) ** 2 + (a[2] - y) ** 2)


# ---------------------------------------------------------------- 拼图
def tile_minimap(im, cfg, stem, extra):
    crop, _ = mm.make_minimap_roi(im, cfg)
    t = cv2.resize(crop, (CROP, CROP), interpolation=cv2.INTER_CUBIC)
    return annotate(t, [f"{stem}", "MINIMAP (game render)"] + extra,
                    (0, 200, 255))


def tile_map(img, cx, cy, lines, tag):
    x0 = int(round(cx - CROP / 2))
    y0 = int(round(cy - CROP / 2))
    H, W = img.shape[:2]
    out = np.zeros((CROP, CROP, 3), np.uint8)
    sx0, sy0 = max(0, x0), max(0, y0)
    sx1, sy1 = min(W, x0 + CROP), min(H, y0 + CROP)
    if sx1 > sx0 and sy1 > sy0:
        out[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = \
            cv2.cvtColor(img[sy0:sy1, sx0:sx1], cv2.COLOR_GRAY2BGR)
    # 画十字标出中心 + 边框坐标
    c = CROP // 2
    cv2.line(out, (c - 26, c), (c - 8, c), (0, 0, 255), 2)
    cv2.line(out, (c + 8, c), (c + 26, c), (0, 0, 255), 2)
    cv2.line(out, (c, c - 26), (c, c - 8), (0, 0, 255), 2)
    cv2.line(out, (c, c + 8), (c, c + 26), (0, 0, 255), 2)
    cv2.circle(out, (c, c), 34, (0, 0, 255), 2)
    for (px, py) in ((0, 0), (CROP - 1, 0), (0, CROP - 1), (CROP - 1, CROP - 1)):
        cv2.line(out, (px - 4, py), (px + 4, py), (0, 255, 0), 1)
        cv2.line(out, (px, py - 4), (px, py + 4), (0, 255, 0), 1)
    return annotate(out, lines, tag)


def annotate(img, lines, tag):
    bar = np.zeros((30 + 24 * len(lines), img.shape[1], 3), np.uint8)
    out = np.vstack([bar, img])
    for i, s in enumerate(lines):
        y = 22 + 24 * i
        cv2.putText(out, s, (8, y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.52, (0, 0, 0), 3)
        cv2.putText(out, s, (8, y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.52, (255, 255, 255), 1)
    cv2.rectangle(out, (0, 0), (out.shape[1] - 1, out.shape[0] - 1),
                  (90, 90, 90), 1)
    return out


rows_img = []
for i, r in enumerate(cand[:5], 1):
    im = cv2.imread(str(ids[r["idx"] - 1]))
    na = nearest_anchor(r["x"], r["y"])
    d_m = math.hypot(na[1] - r["x"], na[2] - r["y"]) / K
    p1 = tile_minimap(im, cfg, r["stem"],
                      [f"kp={r['qkp']} good={r['good']} inl={r['inl']}",
                       f"suspect={r.get('susp', False)}"])
    p2 = tile_map(MAP, r["x"], r["y"],
                  [f"ORB MATCH L{MAP_LEVEL}",
                   f"({r['x']:.0f}, {r['y']:.0f}) L2px",
                   f"L0=({r['x']*K:.0f}, {r['y']*K:.0f})"],
                  (0, 200, 255))
    p3 = tile_map(MAP, na[1], na[2],
                  [f"NEAREST ANCHOR  {d_m:.0f} m away",
                   f"{na[0]}",
                   f"({na[1]:.0f}, {na[2]:.0f}) L2px"],
                  (0, 255, 120))
    row = np.hstack([p1, p2, p3])
    cv2.rectangle(row, (0, 0), (15, row.shape[0] - 1), (0, 0, 255), -1)
    cv2.putText(row, f"#{i}", (1, row.shape[0] // 2), cv2.FONT_HERSHEY_SIMPLEX,
                0.7, (255, 255, 255), 2)
    rows_img.append(row)
    print(f"\n  #{i} {r['stem']}  inl={r['inl']} good={r['good']} "
          f"pos=({r['x']:.0f},{r['y']:.0f})  最近锚点 {na[0]} ({d_m:.0f} m)")

mont = np.vstack(rows_img)
cv2.imwrite(str(FIG / "top5_triplet.png"), mont)
print(f"\n  -> {FIG/'top5_triplet.png'}  {mont.shape}")

# ---------------------------------------------------------------- 总览图
ov = cv2.resize(MAP, (2048, 1024), interpolation=cv2.INTER_AREA)
ov = cv2.cvtColor(ov, cv2.COLOR_GRAY2BGR)
sc_ov = 2048.0 / MW
for name, ax, ay in anc_l:
    px, py = int(ax * sc_ov), int(ay * sc_ov)
    cv2.drawMarker(ov, (px, py), (0, 255, 0), cv2.MARKER_TRIANGLE_UP, 16, 2)
for r in rows:
    if r["skip"] != "" or not r["world"]:
        continue
    px, py = int(r["x"] * sc_ov), int(r["y"] * sc_ov)
    col = (0, 0, 255) if r.get("susp") else (255, 200, 0)
    cv2.circle(ov, (px, py), 5, col, -1)
top = cand[:5]
for r in top:
    px, py = int(r["x"] * sc_ov), int(r["y"] * sc_ov)
    cv2.circle(ov, (px, py), 22, (0, 0, 255), 3)
    cv2.putText(ov, r["stem"], (px + 26, py - 8), cv2.FONT_HERSHEY_SIMPLEX,
                0.6, (0, 0, 255), 2)
    cv2.putText(ov, r["stem"], (px + 26, py - 8), cv2.FONT_HERSHEY_SIMPLEX,
                0.6, (255, 255, 255), 1)
hdr = np.zeros((40, 2048, 3), np.uint8)
cv2.putText(hdr, f"L{MAP_LEVEL} overview  |  green tri = 23 anchors  |  "
                f"orange dot = valid match  |  red dot = suspect  |  "
                f"big red ring = top5 ({len(top)})",
            (8, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
cv2.imwrite(str(FIG / "overview_positions.png"), np.vstack([hdr, ov]))
print(f"  -> {FIG/'overview_positions.png'}")

# ---------------------------------------------------------------- CSV
out = FIG / "orb_l2_results.csv"
with out.open("w", encoding="utf-8-sig", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["frame_idx", "frame", "is_world", "q_kp", "good", "inliers",
                "inl_ratio", "x_l2", "y_l2", "x_l0", "y_l0",
                "scale", "rot_deg", "inl_spread", "jump_l2", "suspicious",
                "skip", "score"])
    for r in rows:
        w.writerow([
            r["idx"], f"a:{r['stem']}", int(r["world"]), r["qkp"], r["good"],
            r["inl"], round(r["inl"] / max(r["good"], 1), 4),
            None if math.isnan(r["x"]) else round(r["x"], 1),
            None if math.isnan(r["y"]) else round(r["y"], 1),
            None if math.isnan(r["x"]) else round(r["x"] * K, 1),
            None if math.isnan(r["y"]) else round(r["y"] * K, 1),
            None if math.isnan(r["scale"]) else round(r["scale"], 4),
            None if math.isnan(r["rot"]) else round(r["rot"], 2),
            None if math.isnan(r["spread"]) else round(r["spread"], 1),
            None if r.get("jump") is None or math.isnan(r.get("jump", float("nan")))
            else round(r["jump"], 1),
            int(r.get("susp", False)), r["skip"],
            round(r.get("score", -1), 2)])
print(f"  -> {out}")
print("=== DONE ===")
