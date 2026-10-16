"""阶段 C2+C3 · 特征对照表 + 特征区分度分析。

★ 区分度判据
------------
对特征 f 和场景对 (A,B)：
    score = |median_A - median_B| / (IQR_A + IQR_B + eps)
score 越大越能分开。score > 1 表示两个 IQR 几乎不重叠。
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
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

RECOG = Path(RECOG_ROOT)
DAT = RECOG / "exports" / "benchmark" / "datasets"
LAB = DAT / "scene_labels.csv"
FEAT = DAT / "scene_features.csv"
OUTL = DAT / "labeled_features.csv"
OUTD = DAT / "feature_discrimination.csv"
FIG = (RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe"
       / "feature_dist_by_scene")

PAIRS = [("大世界", "背包"), ("大世界", "大地图"), ("大世界", "派蒙界面"),
         ("大世界", "祈愿"), ("大世界", "加载"),
         ("背包", "大地图"), ("背包", "派蒙界面"),
         ("大地图", "派蒙界面")]


def main():
    labels = {}
    with LAB.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            labels[r["frame_id"]] = (r["scene_type"], r["scene_subtype"],
                                     r["note"])

    rows, cols = [], []
    with FEAT.open(encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh)
        cols = [c for c in rd.fieldnames if c != "frame_id"]
        for r in rd:
            if r["frame_id"] in labels:
                st, sub, note = labels[r["frame_id"]]
                rec = {"frame_id": r["frame_id"], "scene_type": st,
                       "scene_subtype": sub}
                for c in cols:
                    v = r[c]
                    rec[c] = float(v) if v not in ("", None) else float("nan")
                rows.append(rec)
    with OUTL.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["frame_id", "scene_type",
                                           "scene_subtype"] + cols)
        w.writeheader()
        w.writerows(rows)
    print(f"C2 -> {OUTL.name}   {len(rows)} 行 x {len(cols)+3} 列")

    by = defaultdict(lambda: defaultdict(list))
    for r in rows:
        for c in cols:
            if not np.isnan(r[c]):
                by[r["scene_type"]][c].append(r[c])
    types = sorted(by)
    print(f"场景类型 {len(types)}: {types}")
    for t in types:
        n = len(by[t][cols[0]])
        print(f"    {t:<10} n={n}")

    # ---- 区分度 ----
    def iqr(v):
        return float(np.percentile(v, 75) - np.percentile(v, 25))

    results = []
    for c in cols:
        for a, b in PAIRS:
            if a not in by or b not in by:
                continue
            va, vb = by[a][c], by[b][c]
            if len(va) < 3 or len(vb) < 3:
                continue
            ma, mb = float(np.median(va)), float(np.median(vb))
            s = abs(ma - mb) / (iqr(va) + iqr(vb) + 1e-9)
            results.append((c, f"{a}|{b}", s, ma, mb))
    results.sort(key=lambda t: -t[2])

    with OUTD.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["feature", "scene_pair", "score", "median_A", "median_B"])
        for c, p, s, ma, mb in results:
            w.writerow([c, p, round(s, 3), round(ma, 3), round(mb, 3)])
    print(f"\nC3 -> {OUTD.name}   {len(results)} 行")

    print("\n" + "=" * 84)
    print("区分度 TOP 30")
    print("=" * 84)
    print(f"  {'特征':<38}{'场景对':<22}{'score':>8}")
    print("  " + "-" * 68)
    for c, p, s, ma, mb in results[:30]:
        print(f"  {c:<38}{p:<22}{s:>8.2f}")

    # 单特征对所有场景对的平均分（找"全能特征"）
    per_feat = defaultdict(list)
    for c, p, s, _, _ in results:
        per_feat[c].append(s)
    print("\n按特征聚合（对全部 8 个场景对的平均 score）:")
    rank = sorted(((c, sum(v) / len(v), max(v)) for c, v in per_feat.items()),
                  key=lambda t: -t[1])
    print(f"  {'特征':<38}{'平均':>8}{'最高':>8}")
    print("  " + "-" * 54)
    for c, avg, mx in rank[:20]:
        print(f"  {c:<38}{avg:>8.2f}{mx:>8.2f}")

    # ---- 箱须图：每个 ROI 一张，子图排布 ----
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    FIG.mkdir(parents=True, exist_ok=True)
    rois = sorted({c.split("__")[0] for c in cols})
    for roi in rois:
        fcs = [c for c in cols if c.startswith(roi + "__")]
        n = len(fcs)
        ncol = 4
        nrow = (n + ncol - 1) // ncol
        fig, axes = plt.subplots(nrow, ncol,
                                 figsize=(4.2 * ncol, 3.0 * nrow))
        axes = np.atleast_1d(axes).ravel()
        for i, c in enumerate(fcs):
            ax = axes[i]
            data, labs = [], []
            for t in types:
                v = by[t][c]
                if v:
                    data.append(v)
                    labs.append(t)
            if data:
                ax.boxplot(data, labels=labs, showfliers=False)
            ax.set_title(c.split("__")[1], fontsize=8)
            ax.tick_params(axis="x", rotation=60, labelsize=6)
            ax.tick_params(axis="y", labelsize=6)
            ax.grid(alpha=.3)
        for j in range(n, len(axes)):
            axes[j].axis("off")
        fig.suptitle(f"feature distribution by scene  —  {roi}", fontsize=11)
        fig.tight_layout(rect=[0, 0, 1, 0.97])
        p = FIG / f"dist_{roi}.png"
        fig.savefig(p, dpi=110)
        plt.close(fig)
        print(f"  图: {p.name}")
    print("\n=== DONE ===")


if __name__ == "__main__":
    main()
