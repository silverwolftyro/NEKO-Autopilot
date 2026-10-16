"""ORB 定位根因判定 · 有真值的多变体对比。

**问题**：上一轮 152 帧全零解。到底是表示法不对，还是域差距无法跨越？

**已确认的事实**
* 源图 `teyvat_P0.png` 是彩色的（17.5% 像素有饱和度，锚点裁块内 87%）
* 我建的 `P0_pyramid` 是**纯灰度**的（饱和度 0%）—— 切片时把颜色丢了
* 灰度化把"水(青绿) / 植被(深绿) / 路(米色)"这个最强的语义维度抹平了：
  彩色版 BGR 标准差 [34.6, 24.8, 48.0]，灰度版只有 28.5
* 游戏小地图是彩色的，与彩色底图**语义一致、风格不同**

**判别设计（全部有真值）**
查询 = 23 个锚点的底图裁块（已知它来自哪），在整图库里找回来。
* 找得到 -> 管线没问题，零解是"游戏渲染 vs 底图"的域差距
* 找不到 -> 表示法/库密度本身就不行，跟域差距无关

变体：
  V0 灰度 ORB（= 上一轮的做法）
  V1 分通道 ORB（B/G/R 各自建库，描述子拼接过半）
  V2 语义类别边界 ORB（k-means 量化 -> 类别边界图 -> ORB）
  V3 SIFT（尺度不变，作为 ORB 的对照）
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

import json
import math
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

RECOG = Path(RECOG_ROOT)
P0 = RECOG / "data" / "mihoyo_maps" / "teyvat_P0.png"
L2G = RECOG / "data" / "mihoyo_maps" / "P0_pyramid" / "L2_full.png"
ANCH = RECOG / "data" / "mihoyo_maps" / "teyvat_P0.png.anchors.json"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" / "orb_e"
FIG.mkdir(parents=True, exist_ok=True)

anchors = json.loads(ANCH.read_text(encoding="utf-8"))["points"]
BF = cv2.BFMatcher(cv2.NORM_HAMMING)
BF_L2 = cv2.BFMatcher(cv2.NORM_L2)
OK, REPROJ, RATIO = 6, 3.0, 0.78

print("=" * 96)
print("0. 数据准备")
print("=" * 96)
t = time.perf_counter()
HALF = cv2.imread(str(P0), cv2.IMREAD_REDUCED_COLOR_2)     # 2 m/px
print(f"  彩色 1/2 层 {HALF.shape}  {time.perf_counter()-t:.1f}s   (2 m/px)")
t = time.perf_counter()
L2C = cv2.imread(str(P0), cv2.IMREAD_REDUCED_COLOR_4)      # 4 m/px
print(f"  彩色 1/4 层 {L2C.shape}  {time.perf_counter()-t:.1f}s   (4 m/px)")
L2GRAY = cv2.imread(str(L2G), cv2.IMREAD_GRAYSCALE)
print(f"  已有灰度 L2 {L2GRAY.shape}")
assert L2C.shape[:2] == L2GRAY.shape, "彩色/灰度 L2 尺寸不一致"
KW = 0.25                                           # L0 -> 彩色 L2
MH, MW = L2GRAY.shape


# ---------------------------------------------------------------- 工具
def quantize(img, k=6, sample=200_000, seed=0):
    """k-means 量化成 k 类，返回 label map（uint8）。

    只在随机采样上训练，再逐块 assign 全图 —— 42 MPix 全量训练不现实。
    """
    rng = np.random.default_rng(seed)
    H, W = img.shape[:2]
    sy, sx = rng.integers(0, H, sample), rng.integers(0, W, sample)
    pts = img[sy, sx].astype(np.float32).reshape(-1, 1, 3)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _, _, cen = cv2.kmeans(pts, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    cen = cen.astype(np.float32)
    lab = np.empty((H, W), np.uint8)
    step = max(1, 2_000_000 // W)
    for y0 in range(0, H, step):
        y1 = min(H, y0 + step)
        blk = img[y0:y1].astype(np.float32)
        d = ((blk[:, :, None, :] - cen[None, None, :, :]) ** 2).sum(-1)
        lab[y0:y1] = d.argmin(-1).astype(np.uint8)
    return lab, cen


def boundaries(lab):
    """类别边界图：相邻像素类别不同 -> 255。ORB 吃的是这张图。"""
    e = np.zeros_like(lab)
    e[:, 1:] |= (lab[:, 1:] != lab[:, :-1])
    e[1:, :] |= (lab[1:, :] != lab[:-1, :])
    return e * 255


def build_orb(img, nfeat, nlevels=10, gray=True, tag=""):
    if gray and img.ndim == 3:
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        g = img
    o = cv2.ORB_create(nfeatures=nfeat, nlevels=nlevels, scaleFactor=1.2,
                       fastThreshold=5)
    k, d = o.detectAndCompute(g, None)
    print(f"      [build_orb{tag}] nfeat={nfeat} -> 实际 {0 if d is None else len(d)}")
    return k, d


def find_here(qk, qd, lp, ld, ratio=RATIO, ratio_l2=False):
    if (qd is None or len(qd) < 4 or ld is None or len(ld) < 2
            or lp is None or len(lp) < 2):
        return dict(good=0, inl=0, x=np.nan, y=np.nan, spread=np.nan)
    bf = BF_L2 if ratio_l2 else BF
    knn = bf.knnMatch(qd, ld, k=2)
    good = [m for m, p in knn if m.distance < ratio * p.distance]
    if len(good) < 4:
        return dict(good=len(good), inl=0, x=np.nan, y=np.nan, spread=np.nan)
    src = np.float32([qk[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([lp[m.trainIdx] for m in good]).reshape(-1, 1, 2)
    A, inl = cv2.estimateAffinePartial2D(src, dst, cv2.RANSAC, REPROJ, 20000)
    if A is None or inl is None:
        return dict(good=len(good), inl=0, x=np.nan, y=np.nan, spread=np.nan)
    keep = dst[inl.ravel().astype(bool)].reshape(-1, 2)
    return dict(good=len(good), inl=int(inl.sum()),
                x=A[0, 0] * 256 + A[0, 1] * 256 + A[0, 2],
                y=A[1, 0] * 256 + A[1, 1] * 256 + A[1, 2],
                spread=float(np.mean(np.std(keep, axis=0))) if len(keep) >= 2
                else np.nan)


RESULTS = {}


def run_variant(tag, desc, make_lib, make_query, ratio=RATIO, ratio_l2=False,
                tol_px=64.0):
    t = time.perf_counter()
    lp, ld = make_lib()
    tl = time.perf_counter() - t
    hits, inls, errs, goods = 0, [], [], []
    detail = []
    for a in anchors:
        qk, qd = make_query(a)
        r = find_here(qk, qd, lp, ld, ratio, ratio_l2)
        inls.append(r["inl"])
        goods.append(r["good"])
        tx, ty = a["x"] * KW, a["y"] * KW
        if r["inl"] >= OK:
            e = math.hypot(r["x"] - tx, r["y"] - ty)
            errs.append(e)
            hit = e < tol_px
            hits += int(hit)
        else:
            e, hit = float("nan"), False
        detail.append((a["name"], r["good"], r["inl"], r["x"], r["y"], e, hit))
    ai, ag = np.array(inls), np.array(goods)
    ae = np.array(errs) if errs else np.array([np.nan])
    print(f"\n  [{tag}] {desc}")
    print(f"    库构建 {tl:.1f}s   good 中位 {np.median(ag):.0f}   "
          f"inl 中位 {np.median(ai):.0f}  max {ai.max()}")
    print(f"    >>> 命中 {hits}/{len(anchors)}   "
          f"落点误差 中位 {np.nanmedian(ae):.0f}px  "
          f"p90 {np.nanpercentile(ae,90):.0f}px  (判据 <{tol_px:.0f}px = {tol_px/KW:.0f}m)")
    print(f"        总耗时 {time.perf_counter()-t:.1f}s")
    RESULTS[tag] = dict(desc=desc, hits=hits, n=len(anchors), detail=detail,
                        inl_med=float(np.median(ai)),
                        err_med=float(np.nanmedian(ae)))
    return detail


# ---------------------------------------------------------------- V0
print("\n" + "=" * 96)
print("1. V0 · 灰度 ORB（= 上一轮实际用的东西）")
print("=" * 96)


def v0_lib():
    k, d = build_orb(L2GRAY, 300000)
    return np.array([x.pt for x in k], np.float32), d


def q_gray512(a):
    c = HALF[a["y"] // 2 - 256:a["y"] // 2 + 256,
             a["x"] // 2 - 256:a["x"] // 2 + 256]
    g = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY)
    return build_orb(g, 2000, nlevels=8)


run_variant("V0", "灰度 ORB / 灰度 L2 库", v0_lib, q_gray512)

# ---------------------------------------------------------------- V1
print("\n" + "=" * 96)
print("2. V1 · 分通道 ORB（B/G/R 各自建库，命中过半才采纳）")
print("=" * 96)


def v1_lib():
    libs = []
    for ci, cn in enumerate("BGR"):
        g = L2C[:, :, ci].copy()
        k, d = build_orb(g, 200000, gray=False)
        libs.append((cn, np.array([x.pt for x in k], np.float32), d))
    return libs


def q_color512(a):
    c = HALF[a["y"] // 2 - 256:a["y"] // 2 + 256,
             a["x"] // 2 - 256:a["x"] // 2 + 256]
    return [build_orb(c[:, :, i].copy(), 2000, nlevels=8, gray=False)
            for i in range(3)]


_t = time.perf_counter()
V1_LIBS = v1_lib()
print(f"  库构建 {time.perf_counter()-_t:.1f}s  "
      f"{[ (c, len(d)) for c,_,d in V1_LIBS ]}")
hits = inls = 0
v1_detail = []
for a in anchors:
    qs = q_color512(a)
    per = [find_here(qs[i][0], qs[i][1], V1_LIBS[i][1], V1_LIBS[i][2])
           for i in range(3)]
    best = max(per, key=lambda r: r["inl"])
    tx, ty = a["x"] * KW, a["y"] * KW
    e = (math.hypot(best["x"] - tx, best["y"] - ty)
         if best["inl"] >= OK else float("nan"))
    hit = bool(best["inl"] >= OK and e < 64.0)
    hits += int(hit)
    v1_detail.append((a["name"], best["good"], best["inl"], best["x"],
                      best["y"], e, hit))
    if a["name"].endswith("枫丹廷区") or a["name"].endswith("星落湖"):
        print(f"    {a['name'][:14]:<16} 逐通道 inl="
              f"{[p['inl'] for p in per]}  落点=({best['x']:.0f},{best['y']:.0f}) "
              f"真值=({tx:.0f},{ty:.0f}) {'HIT' if hit else ''}")
es = [d[5] for d in v1_detail if not math.isnan(d[5])]
print(f"    >>> 命中 {hits}/{len(anchors)}   "
      f"落点误差 中位 {np.median(es) if es else float('nan'):.0f}px")
RESULTS["V1"] = dict(desc="分通道 ORB", hits=hits, n=len(anchors),
                     detail=v1_detail, inl_med=0,
                     err_med=float(np.median(es)) if es else float("nan"))

# ---------------------------------------------------------------- V2
print("\n" + "=" * 96)
print("3. V2 · 语义类别边界 ORB（k-means 量化 -> 边界图 -> ORB）")
print("=" * 96)
t = time.perf_counter()
lab2, cen = quantize(L2C, k=6, sample=200_000, seed=42)
print(f"  L2 量化 {time.perf_counter()-t:.1f}s  类别占比 "
      f"{np.round(np.bincount(lab2.ravel(), minlength=6)/lab2.size,3)}")
ed2 = boundaries(lab2)
print(f"  边界像素占比 {100*(ed2>0).mean():.2f}%")
t = time.perf_counter()
k, d = build_orb(ed2, 300000, gray=False)
lp2 = np.array([x.pt for x in k], np.float32)
print(f"  边界图 ORB {len(k)} 特征点  {time.perf_counter()-t:.1f}s")

hits, v2_detail = 0, []
for a in anchors:
    c = HALF[a["y"] // 2 - 256:a["y"] // 2 + 256,
             a["x"] // 2 - 256:a["x"] // 2 + 256]
    lab_q, _ = quantize(c, k=6, sample=40_000, seed=42)
    ed_q = boundaries(lab_q)
    qk, qd = build_orb(ed_q, 2000, nlevels=8, gray=False)
    r = find_here(qk, qd, lp2, d)
    tx, ty = a["x"] * KW, a["y"] * KW
    e = (math.hypot(r["x"] - tx, r["y"] - ty)
         if r["inl"] >= OK else float("nan"))
    hit = bool(r["inl"] >= OK and e < 64.0)
    hits += int(hit)
    v2_detail.append((a["name"], r["good"], r["inl"], r["x"], r["y"], e, hit))
es2 = [x[5] for x in v2_detail if not math.isnan(x[5])]
print(f"    >>> 命中 {hits}/{len(anchors)}   "
      f"落点误差 中位 {np.median(es2) if es2 else float('nan'):.0f}px   "
      f"inl 中位 {np.median([x[2] for x in v2_detail]):.0f}")
RESULTS["V2"] = dict(desc="语义类别边界 ORB", hits=hits, n=len(anchors),
                     detail=v2_detail, inl_med=float(np.median([x[2] for x in v2_detail])),
                     err_med=float(np.median(es2)) if es2 else float("nan"))

# ---------------------------------------------------------------- V3
print("\n" + "=" * 96)
print("4. V3 · SIFT 对照（尺度不变，彩色转灰度）")
print("=" * 96)
t = time.perf_counter()
SIFT = cv2.SIFT_create(nfeatures=200000)
k, d = SIFT.detectAndCompute(L2GRAY, None)
lp3 = np.array([x.pt for x in k], np.float32)
print(f"  SIFT 库 {len(k)} 特征点  {time.perf_counter()-t:.1f}s")
hits, v3_detail = 0, []
for a in anchors:
    c = HALF[a["y"] // 2 - 256:a["y"] // 2 + 256,
             a["x"] // 2 - 256:a["x"] // 2 + 256]
    g = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY)
    qk, qd = SIFT.detectAndCompute(g, None)
    r = find_here(qk, qd, lp3, d, ratio=0.75, ratio_l2=True, tol_px=64.0)
    tx, ty = a["x"] * KW, a["y"] * KW
    e = (math.hypot(r["x"] - tx, r["y"] - ty)
         if r["inl"] >= OK else float("nan"))
    hit = bool(r["inl"] >= OK and e < 64.0)
    hits += int(hit)
    v3_detail.append((a["name"], r["good"], r["inl"], r["x"], r["y"], e, hit))
es3 = [x[5] for x in v3_detail if not math.isnan(x[5])]
print(f"    >>> 命中 {hits}/{len(anchors)}   "
      f"落点误差 中位 {np.median(es3) if es3 else float('nan'):.0f}px   "
      f"inl 中位 {np.median([x[2] for x in v3_detail]):.0f}")
RESULTS["V3"] = dict(desc="SIFT", hits=hits, n=len(anchors), detail=v3_detail,
                     inl_med=float(np.median([x[2] for x in v3_detail])),
                     err_med=float(np.median(es3)) if es3 else float("nan"))

# ---------------------------------------------------------------- 汇总
print("\n" + "=" * 96)
print("汇总（有真值，查询=底图自身裁块，库=同图 L2）")
print("=" * 96)
print(f"  {'变体':<6}{'说明':<22}{'命中':>8}{'inl中位':>10}{'误差中位(px)':>14}")
for k_, v_ in RESULTS.items():
    print(f"  {k_:<6}{v_['desc']:<22}{v_['hits']:>4}/{v_['n']:<3}"
          f"{v_['inl_med']:>10.0f}{v_['err_med']:>14.0f}")
print(f"\n  -> {FIG/'variant_summary.json'}")
(FIG / "variant_summary.json").write_text(
    json.dumps({k_: {kk: vv for kk, vv in v_.items() if kk != 'detail'}
                for k_, v_ in RESULTS.items()},
               ensure_ascii=False, indent=2), encoding="utf-8")
print("=== DONE ===")
