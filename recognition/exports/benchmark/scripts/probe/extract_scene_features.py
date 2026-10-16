"""场景特征提取 + KMeans 聚类 + 典型样本挑选。

★ 单帧特征，不依赖帧集合
----------------------
每个特征都能从**一帧**算出来。所以一帧错分类不会传染到别的帧 ——
这是本轮不用中值图路线的原因。

★ 不用 sklearn
-------------
手写 KMeans（k-means++ 初始化 + Lloyd 迭代），只用 numpy。
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
import json
import math
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

RECOG = Path(RECOG_ROOT)
BATCH = {"a": RECOG / "raw" / "frames" / "batch_20260929a",
         "b": RECOG / "raw" / "frames" / "batch_20260929b"}
CSV_OUT = RECOG / "exports" / "benchmark" / "datasets" / "scene_features.csv"
MANI_OUT = RECOG / "exports" / "benchmark" / "datasets" / "cluster_manifest.csv"
CLU_DIR = (RECOG / "exports" / "benchmark" / "visualizations"
           / "ui_probe" / "cluster_samples")
W, H = 2304, 1440

#: 已知 ROI（归一化）
ROIS = {
    "minimap":   [0.0299, 0.0116, 0.1464, 0.1911],
    "hp_bar":    [0.4201, 0.9368, 0.5807, 0.9472],
    "skill_bar": [0.8620, 0.8880, 0.9680, 0.9780],
    "party_1":   [0.9258, 0.2972, 0.9670, 0.3576],
    "party_2":   [0.9253, 0.3694, 0.9670, 0.4313],
    "party_3":   [0.9253, 0.4500, 0.9666, 0.5111],
    "party_4":   [0.9201, 0.5167, 0.9583, 0.5799],
}

#: 用于算 ncc_vs_world_median 的"我目视核对过的大世界帧"
#: （contact_01/02 逐格看过 + suspect_frames 里看过）
WORLD_REF = ["a:f00001", "a:f00005", "a:f00009", "a:f00011", "a:f00030",
             "a:f00045", "a:f00049", "b:f00167", "b:f00209"]

FEAT_NAMES = ["mean_r", "mean_g", "mean_b", "mean_h", "mean_s", "mean_v",
              "std_v", "edge_density", "laplacian_var", "sat_ratio",
              "bright_ratio"]
K = 7
N_TYP, N_BOUND = 10, 5
PER_PAGE = 20
CELL_W, CELL_H = 480, 300


def load(fid):
    tag, stem = fid.split(":")
    p = BATCH[tag] / f"{stem}.jpg"
    if not p.is_file():
        return None
    return cv2.imread(str(p))


def crop(im, r):
    y0, y1 = int(r[1] * H), max(int(r[3] * H), int(r[1] * H) + 2)
    x0, x1 = int(r[0] * W), max(int(r[2] * W), int(r[0] * W) + 2)
    return im[y0:y1, x0:x1]


def roi_feats(c):
    if c is None or c.size == 0:
        return None
    rgb = cv2.cvtColor(c, cv2.COLOR_BGR2RGB)
    hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
    g = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY)
    return [
        float(rgb[:, :, 0].mean()), float(rgb[:, :, 1].mean()),
        float(rgb[:, :, 2].mean()),
        float(hsv[:, :, 0].mean() * 2.0),          # OpenCV H 是 0-179 -> 0-359
        float(hsv[:, :, 1].mean()), float(hsv[:, :, 2].mean()),
        float(hsv[:, :, 2].std()),
        float((cv2.Canny(g, 60, 160) > 0).mean()),
        float(cv2.Laplacian(g, cv2.CV_64F).var()),
        float((hsv[:, :, 1] > 60).mean()),
        float((hsv[:, :, 2] > 200).mean()),
    ]


def ncc(a, b):
    a = a.astype(np.float32).ravel() - a.mean()
    b = b.astype(np.float32).ravel() - b.mean()
    d = np.linalg.norm(a) * np.linalg.norm(b)
    return 0.0 if d < 1e-6 else float(a.dot(b) / d)


# ---------------------------------------------------------------- KMeans
def kmeans(X, k, iters=120, seed=7):
    rng = np.random.default_rng(seed)
    n = X.shape[0]
    # k-means++ 初始化
    centers = [X[rng.integers(n)]]
    for _ in range(k - 1):
        d2 = np.min(((X[:, None, :] - np.array(centers)[None]) ** 2)
                    .sum(-1), axis=1)
        s = d2.sum()
        if s <= 0:
            centers.append(X[rng.integers(n)])
        else:
            centers.append(X[rng.choice(n, p=d2 / s)])
    C = np.array(centers, float)
    lab = None
    for it in range(iters):
        d = ((X[:, None, :] - C[None]) ** 2).sum(-1)
        new = d.argmin(1)
        if lab is not None and (new == lab).all():
            break
        lab = new
        for j in range(k):
            m = lab == j
            if m.any():
                C[j] = X[m].mean(0)
    d = ((X[:, None, :] - C[None]) ** 2).sum(-1)
    lab = d.argmin(1)
    inertia = float(d.min(1).sum())
    return lab, C, inertia, it


def zscore(X):
    mu = X.mean(0)
    sd = X.std(0)
    keep = sd > 1e-9
    return (X[:, keep] - mu[keep]) / sd[keep], keep


def main():
    ids = []
    for tag, d in BATCH.items():
        if d.is_dir():
            ids += [f"{tag}:{p.stem}" for p in sorted(d.glob("*.jpg"))]
    print(f"帧总数 {len(ids)}")

    # ---- 大世界中值（仅用于 ncc 参考特征）----
    wm = {}
    for rn, r in ROIS.items():
        stack = [crop(load(f), r) for f in WORLD_REF]
        stack = [s for s in stack if s is not None]
        if stack:
            wm[rn] = np.median(np.stack(stack), axis=0).astype(np.uint8)
    print(f"大世界参考帧 {len(WORLD_REF)}（仅用于 ncc_vs_* 参考列）")

    # ---- A.2 特征提取 ----
    print("\n[A.2] 特征提取 ...")
    rows, names = [], []
    for i, fid in enumerate(ids):
        im = load(fid)
        rec = {"frame_id": fid}
        for rn, r in ROIS.items():
            c = crop(im, r)
            fv = roi_feats(c)
            if fv is None:
                for n in FEAT_NAMES:
                    rec[f"{rn}__{n}"] = ""
                rec[f"{rn}__ncc_world"] = ""
                continue
            for n, v in zip(FEAT_NAMES, fv):
                rec[f"{rn}__{n}"] = round(v, 4)
            t = wm.get(rn)
            rec[f"{rn}__ncc_world"] = (round(ncc(c, t), 4)
                                       if t is not None else "")
        rows.append(rec)
        names = [k for k in rec if k != "frame_id"]
        if (i + 1) % 100 == 0:
            print(f"  {i+1}/{len(ids)}")

    feat_cols = [c for c in rows[0] if c != "frame_id"]
    print(f"  特征列数 {len(feat_cols)} = {len(ROIS)} ROI x "
          f"({len(FEAT_NAMES)} 基础 + 1 ncc)")

    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
    with CSV_OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        wtr = csv.DictWriter(fh, fieldnames=["frame_id"] + feat_cols)
        wtr.writeheader()
        wtr.writerows(rows)
    print(f"  -> {CSV_OUT.name}  ({len(rows)} 行 x {len(feat_cols)+1} 列)")

    # ---- B.1 预处理 ----
    X = np.array([[float(r[c]) if r[c] != "" else np.nan
                   for c in feat_cols] for r in rows], float)
    col_mean = np.nanmean(X, axis=0)
    bad = np.isnan(X).any(axis=0)
    X = np.where(np.isnan(X), col_mean, X)
    Xz, keep = zscore(X)
    print(f"\n[B.1] z-score 后保留 {int(keep.sum())}/{len(feat_cols)} 列"
          f"（剔除 {int((~keep).sum())} 个常量列）")

    # ---- B.2 聚类 ----
    lab, C, inertia, it = kmeans(Xz, K)
    sizes = np.bincount(lab, minlength=K)
    print(f"\n[B.2] KMeans k={K}   迭代 {it} 次   inertia={inertia:.1f}")
    for j in range(K):
        print(f"    cluster {j}: {sizes[j]:>4} 帧")

    # ---- B.3 典型 / 边界样本 ----
    d = np.linalg.norm(Xz - C[lab], axis=1)
    picks = []
    for j in range(K):
        idx = np.where(lab == j)[0]
        order = idx[np.argsort(d[idx])]
        typ = order[:N_TYP]
        bnd = order[::-1][:N_BOUND]
        picks.append((j, list(typ), list(bnd)))
        print(f"    cluster {j}: 典型 {len(typ)} + 边界 {len(bnd)}")

    # ---- B.5 manifest ----
    with MANI_OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        wtr = csv.writer(fh)
        head = ["frame_id", "cluster_id", "is_typical", "dist_to_center"]
        head += [f"feat_{c}" for c in feat_cols[:5]]
        wtr.writerow(head)
        typ_set = set()
        for j, t, b in picks:
            for i in t:
                typ_set.add(i)
        for i, r in enumerate(rows):
            wtr.writerow([r["frame_id"], int(lab[i]),
                          1 if i in typ_set else 0,
                          round(float(d[i]), 3)]
                         + [r[c] for c in feat_cols[:5]])
    print(f"  -> {MANI_OUT.name}")

    # ---- B.4 拼图 ----
    from PIL import Image, ImageDraw
    CLU_DIR.mkdir(parents=True, exist_ok=True)
    for j, t, b in picks:
        items = [(i, 1) for i in t] + [(i, 0) for i in b]
        pages = math.ceil(len(items) / PER_PAGE)
        for pg in range(pages):
            chunk = items[pg * PER_PAGE:(pg + 1) * PER_PAGE]
            cols = 4
            rows_n = math.ceil(len(chunk) / cols)
            PADT = 22
            cw, ch = CELL_W, CELL_H + PADT
            sheet = Image.new("RGB", (cols * (cw + 6) + 6,
                                      rows_n * (ch + 6) + 40), (18, 18, 24))
            d2 = ImageDraw.Draw(sheet)
            d2.text((8, 10), f"cluster {j}  page {pg+1}/{pages}  "
                            f"n={sizes[j]}   green border=typical, "
                            f"red border=boundary", fill=(255, 255, 255))
            for k, (i, typ) in enumerate(chunk):
                im = load(rows[i]["frame_id"])
                if im is None:
                    continue
                h, w = im.shape[:2]
                tgt = CELL_W / CELL_H
                if w / h > tgt:
                    nw = int(h * tgt)
                    im = im[:, (w - nw) // 2:(w - nw) // 2 + nw]
                else:
                    nh = int(w / tgt)
                    im = im[(h - nh) // 2:(h - nh) // 2 + nh, :]
                im = cv2.resize(im, (CELL_W, CELL_H),
                                interpolation=cv2.INTER_AREA)
                pil = Image.fromarray(cv2.cvtColor(im, cv2.COLOR_BGR2RGB))
                r, c = divmod(k, cols)
                x = 6 + c * (cw + 6)
                y = 40 + r * (ch + 6)
                dd = ImageDraw.Draw(pil)
                dd.rectangle([0, 0, CELL_W - 1, CELL_H - 1],
                             outline=(0, 220, 0) if typ else (230, 60, 60),
                             width=5)
                dd.rectangle([0, 0, 190, 22], fill=(0, 0, 0))
                dd.text((4, 4), f"C{j}  "
                                 f"{'typical' if typ else 'boundary'}"
                                 f"  d={d[i]:.2f}", fill=(0, 255, 255))
                # ★ 帧号放右下角
                bb = dd.textbbox((0, 0), rows[i]["frame_id"])
                dd.rectangle([CELL_W - (bb[2]-bb[0]) - 10,
                              CELL_H - (bb[3]-bb[1]) - 10,
                              CELL_W - 2, CELL_H - 2], fill=(0, 0, 0))
                dd.text((CELL_W - (bb[2]-bb[0]) - 6,
                         CELL_H - (bb[3]-bb[1]) - 8),
                        rows[i]["frame_id"], fill=(255, 230, 0))
                sheet.paste(pil, (x, y))
            p = CLU_DIR / f"cluster_{j:02d}_page_{pg:02d}.png"
            sheet.save(p)
            print(f"    {p.name}  {sheet.size}  {len(chunk)} 格")

    print(f"\n拼图目录: {CLU_DIR}")
    print("=== DONE ===")


if __name__ == "__main__":
    main()
