"""界面自动分类器 —— 用大块平坦区域（UI 面板）做主信号。

★ 依据
------
菜单类界面有**大块低纹理面板**；大世界画面几乎全是高纹理地形/建筑。
所以"平坦面积占比"能把两类分开。

★ 不是最终结论
--------------
本脚本给出的是**自动初分**，必须人工确认。
验证集来自我目视确认过的帧（见 KNOWN）。

★ 只读
----
帧只读不改。
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

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

RECOG = Path(RECOG_ROOT)
BATCH = {"a": RECOG / "raw" / "frames" / "batch_20260929a",
         "b": RECOG / "raw" / "frames" / "batch_20260929b"}
W, H = 2304, 1440

#: 我目视确认过的帧（用于验证分类器）
KNOWN = {
    # a 批次
    "a:f00001": "world", "a:f00005": "world", "a:f00009": "world",
    "a:f00011": "world", "a:f00030": "world", "a:f00045": "world",
    "a:f00049": "world",
    "a:f00012": "panel", "a:f00014": "bag", "a:f00020": "bag",
    "a:f00026": "bag",
    "a:f00031": "map", "a:f00036": "map", "a:f00041": "map",
    "a:f00051": "paimon", "a:f00055": "paimon", "a:f00060": "paimon",
    "a:f00065": "paimon", "a:f00079": "paimon", "a:f00085": "paimon",
    "a:f00057": "artifact",
    "a:f00067": "loading", "a:f00069": "loading", "a:f00070": "loading",
    # b 批次
    "b:f00167": "world", "b:f00209": "world", "b:f00245": "world",
    "b:f00279": "menu_other",
}


def load(fid):
    tag, stem = fid.split(":")
    p = BATCH[tag] / f"{stem}.jpg"
    if not p.is_file():
        return None
    im = cv2.imread(str(p))
    return im


def features(fid):
    im = load(fid)
    if im is None:
        return None
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    # 局部标准差：面板区低，纹理区高
    k = 31
    mu = cv2.blur(g.astype(np.float32), (k, k))
    mu2 = cv2.blur(g.astype(np.float32) ** 2, (k, k))
    sd = np.sqrt(np.maximum(mu2 - mu * mu, 0))
    flat = float((sd < 6).mean())              # 平坦面积占比
    very_flat = float((sd < 3).mean())
    # 大连通平坦区（真正的"面板"，不是天空）
    mask = (sd < 5).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            np.ones((15, 15), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    big = sum(st for *_, st in stats[1:] if st >= 0.01 * g.size)
    big_frac = big / g.size
    # 小地图区纹理（大世界小地图有内容，菜单里可能是纯色）
    mm = g[int(0.0116 * H):int(0.1911 * H), int(0.0299 * W):int(0.1464 * W)]
    mm_sd = float(mm.std())
    # 整体亮度
    return {"flat": flat, "very_flat": very_flat, "big_frac": big_frac,
            "mm_sd": mm_sd, "mean": float(g.mean())}


def main():
    ids = []
    for tag, d in BATCH.items():
        if d.is_dir():
            ids += [f"{tag}:{p.stem}" for p in sorted(d.glob("*.jpg"))]
    print(f"总帧数 {len(ids)}   正在提取特征...\n")

    feats = {}
    for i, fid in enumerate(ids):
        f = features(fid)
        if f:
            feats[fid] = f
        if (i + 1) % 100 == 0:
            print(f"  {i+1}/{len(ids)}")

    print("\n[验证] 目视已知帧的特征值")
    print(f"  {'帧':<14}{'我的判定':<12}{'flat':>7}{'vflat':>7}"
          f"{'big%':>7}{'mm_sd':>8}{'mean':>7}")
    print("  " + "-" * 72)
    groups = defaultdict(list)
    for fid, lab in sorted(KNOWN.items()):
        f = feats.get(fid)
        if not f:
            continue
        print(f"  {fid:<14}{lab:<12}{f['flat']:>7.3f}{f['very_flat']:>7.3f}"
              f"{f['big_frac']*100:>7.1f}{f['mm_sd']:>8.1f}{f['mean']:>7.1f}")
        groups[lab].append(f["big_frac"])

    print("\n  按类别的 big_frac（大面板占比）中位数:")
    for lab in sorted(groups):
        v = sorted(groups[lab])
        print(f"    {lab:<12} n={len(v):>3}  "
              f"min={v[0]:.3f}  med={v[len(v)//2]:.3f}  max={v[-1]:.3f}")

    import json
    out = RECOG / "exports" / "benchmark" / "datasets" / "ui_frame_features.json"
    out.write_text(json.dumps(feats, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"\n特征已存: {out.name}  ({len(feats)} 帧)")
    print("=== DONE ===")


if __name__ == "__main__":
    main()
