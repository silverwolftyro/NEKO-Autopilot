"""阶段 C3 修正 + C4 判定树（手写 ID3，不用 sklearn）。

★ 修正 1 · 区分度指标
--------------------
上一版 ``|medA-medB| / (IQR_A+IQR_B+1e-9)`` 在某类 IQR=0 时爆到 4.6 亿。
改成分母加上两类的合并标准差，既稳又有界。

★ 修正 2 · 图表标签一律英文
--------------------------
matplotlib 缺 CJK 字形，中文标签会渲染成空白方块。
场景名映射到英文再画。

★ 关于训练目标
--------------
10 个场景类型里有 4 个只有 1 个样本（任务/活动/设置/邮箱），
**10 分类树不可训**。而产品真正需要的是 §4.2 的
"是不是大世界" —— 所以主树做**二分类**，多分类如实报告不可行。
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
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

RECOG = Path(RECOG_ROOT)
DAT = RECOG / "exports" / "benchmark" / "datasets"
LBL = DAT / "labeled_features.csv"
OUTD = DAT / "feature_discrimination.csv"
TREE_PY = RECOG / "exports" / "benchmark" / "scripts" / "misc" / "coord_scene_tree.py"

EN = {"大世界": "World", "背包": "Bag", "大地图": "BigMap",
      "派蒙界面": "PaimonMenu", "祈愿": "Wish", "加载": "Loading",
      "设置": "Settings", "任务": "Quest", "活动": "Event", "邮箱": "Mail"}
PAIRS = [("大世界", "背包"), ("大世界", "大地图"), ("大世界", "派蒙界面"),
         ("大世界", "祈愿"), ("大世界", "加载"), ("大世界", "设置"),
         ("背包", "大地图"), ("背包", "派蒙界面"), ("大地图", "派蒙界面")]


# ------------------------------------------------------------------ ID3
class Node:
    __slots__ = ("feat", "thr", "left", "right", "pred")

    def __init__(self):
        self.feat = None
        self.thr = None
        self.left = None
        self.right = None
        self.pred = None


def entropy(labels):
    n = len(labels)
    if n == 0:
        return 0.0
    c = Counter(labels)
    return -sum((v / n) * np.log2(v / n) for v in c.values())


def train(X, y, idx, depth, max_depth, min_purity, feat_names):
    node = Node()
    cnt = Counter(y[i] for i in idx)
    node.pred = max(cnt, key=cnt.get)
    n = len(idx)
    if depth >= max_depth or max(cnt.values()) / n >= min_purity \
            or len(cnt) == 1:
        return node
    base = entropy([y[i] for i in idx])
    best = (0.0, None, None)
    for f in range(X.shape[1]):
        vals = X[idx, f]
        # 只在分位点候选上试阈值，避免 O(n^2)
        qs = np.unique(np.percentile(vals, [10, 20, 30, 40, 50, 60, 70, 80, 90]))
        for t in qs[:-1] if len(qs) > 1 else []:
            m = vals <= t
            if m.all() or (~m).all():
                continue
            li = [i for i, k in zip(idx, m) if k]
            ri = [i for i, k in zip(idx, m) if not k]
            if not li or not ri:
                continue
            gain = (base
                    - len(li) / n * entropy([y[i] for i in li])
                    - len(ri) / n * entropy([y[i] for i in ri]))
            if gain > best[0]:
                best = (gain, f, float(t))
    if best[1] is None:
        return node
    f, t = best[1], best[2]
    m = X[idx, f] <= t
    node.feat = feat_names[f]
    node.thr = t
    node.left = train(X, y, [i for i, k in zip(idx, m) if k],
                      depth + 1, max_depth, min_purity, feat_names)
    node.right = train(X, y, [i for i, k in zip(idx, m) if not k],
                       depth + 1, max_depth, min_purity, feat_names)
    return node


def predict(node, row, feat_names):
    idx = {n: i for i, n in enumerate(feat_names)}
    while node.feat is not None:
        node = node.left if row[idx[node.feat]] <= node.thr else node.right
    return node.pred


def to_text(node, feat_names, prefix="", depth=0):
    if node.feat is None:
        return [f"{prefix}-> {node.pred}"]
    out = [f"{prefix}if {node.feat} <= {node.thr:.4f}:"]
    out += to_text(node.left, feat_names, prefix + "    ", depth + 1)
    out += [f"{prefix}else:"]
    out += to_text(node.right, feat_names, prefix + "    ", depth + 1)
    return out


def depth_of(n):
    return 0 if n.feat is None else 1 + max(depth_of(n.left),
                                             depth_of(n.right))


# ------------------------------------------------------------------ main
def main():
    rows, cols = [], []
    with LBL.open(encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh)
        cols = [c for c in rd.fieldnames
                if c not in ("frame_id", "scene_type", "scene_subtype")]
        for r in rd:
            rows.append(r)
    X = np.array([[float(r[c]) for c in cols] for r in rows], float)
    types = [r["scene_type"] for r in rows]
    print(f"标注帧 {len(rows)}   特征 {len(cols)} 列")
    for t, n in Counter(types).most_common():
        print(f"    {t:<10} {n:>4}" +
              ("   ⚠ 样本不足 (<4)" if n < 4 else ""))

    # ---------- C3 修正版区分度 ----------
    by = defaultdict(lambda: defaultdict(list))
    for r, t in zip(rows, types):
        for c in cols:
            by[t][c].append(float(r[c]))

    def iqr(v):
        return float(np.percentile(v, 75) - np.percentile(v, 25))

    res = []
    for c in cols:
        for a, b in PAIRS:
            if a not in by or b not in by or len(by[a][c]) < 3 \
                    or len(by[b][c]) < 3:
                continue
            va, vb = by[a][c], by[b][c]
            ma, mb = float(np.median(va)), float(np.median(vb))
            sd = np.sqrt((np.var(va) + np.var(vb)) / 2.0)
            den = iqr(va) + iqr(vb) + sd + 1e-6
            res.append((c, f"{a}|{b}", abs(ma - mb) / den, ma, mb))
    res.sort(key=lambda t: -t[2])
    with OUTD.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["feature", "scene_pair", "score", "median_A", "median_B"])
        for c, p, s, ma, mb in res:
            w.writerow([c, p, round(s, 3), round(ma, 3), round(mb, 3)])
    print(f"\nC3 修正版 -> {OUTD.name}")
    print("\n区分度 TOP 18:")
    for c, p, s, ma, mb in res[:18]:
        print(f"  {c:<34}{p:<20}{s:>8.2f}")

    # ---------- 箱须图（英文标签）----------
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    FIG = (RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe"
           / "feature_dist_by_scene")
    FIG.mkdir(parents=True, exist_ok=True)
    for roi in sorted({c.split("__")[0] for c in cols}):
        fcs = [c for c in cols if c.startswith(roi + "__")]
        ncol = 4
        nrow = (len(fcs) + ncol - 1) // ncol
        fig, axes = plt.subplots(nrow, ncol, figsize=(4.4 * ncol, 3.1 * nrow))
        axes = np.atleast_1d(axes).ravel()
        for i, c in enumerate(fcs):
            ax = axes[i]
            data, labs = [], []
            for t in sorted(by, key=lambda x: -len(by[x][c])):
                v = by[t][c]
                if v:
                    data.append(v)
                    labs.append(EN.get(t, t))
            if data:
                ax.boxplot(data, tick_labels=labs, showfliers=False)
            ax.set_title(c.split("__")[1], fontsize=8)
            ax.tick_params(axis="x", rotation=55, labelsize=6)
            ax.tick_params(axis="y", labelsize=6)
            ax.grid(alpha=.3)
        for j in range(len(fcs), len(axes)):
            axes[j].axis("off")
        fig.suptitle(f"feature distribution by scene type  -  {roi}",
                     fontsize=11)
        fig.tight_layout(rect=[0, 0, 1, 0.96])
        fig.savefig(FIG / f"dist_{roi}.png", dpi=110)
        plt.close(fig)
    print(f"\n箱须图 -> {FIG.name}/ (7 张，英文标签)")

    # ---------- C4 二分类判定树 ----------
    y = np.array([1 if t == "大世界" else 0 for t in types])
    print("\n" + "=" * 72)
    print("C4 · 二分类判定树（is_world = 1 / 0）")
    print("=" * 72)
    print(f"  正类(大世界) {int(y.sum())}   负类(其它) {int((1-y).sum())}")

    # 标准化
    # ★ 排除 ncc_world：**它的大世界中值就是用本训练集里的帧算的**，
    #   留着等于拿答案去对答案（实测 5-fold 会假性 100%）。用户也明确要求不进树。
    tree_cols = [c for c in cols if not c.endswith("__ncc_world")]
    dropped = [c for c in cols if c not in tree_cols]
    print(f"\n★ 排除泄漏列 {len(dropped)} 个: "
          f"{[c.split('__')[0] for c in dropped]}__ncc_world")

    mu, sd = X.mean(0), X.std(0)
    sel = [cols.index(c) for c in tree_cols]
    mu, sd = mu[sel], sd[sel]
    Xraw = X[:, sel]
    keep = sd > 1e-9
    Xz = (Xraw[:, keep] - mu[keep]) / sd[keep]
    fnames = [c for c, k in zip(tree_cols, keep) if k]
    print(f"  参与特征 {len(fnames)} 列（剔除 {int((~keep).sum())} 常量列）")

    MAXD, PURITY = 5, 0.95
    # 5-fold
    rs = np.random.default_rng(11)
    perm = rs.permutation(len(y))
    folds = np.array_split(perm, 5)
    accs = []
    for k in range(5):
        te = set(folds[k].tolist())
        tr = [i for i in range(len(y)) if i not in te]
        node = train(Xz, y, tr, 0, MAXD, PURITY, fnames)
        ok = sum(1 for i in folds[k] if predict(node, Xz[i], fnames) == y[i])
        accs.append(ok / len(folds[k]))
    print(f"  5-fold 交叉验证准确率: "
          f"{[f'{a:.3f}' for a in accs]}   平均 {np.mean(accs):.4f}")

    node = train(Xz, y, list(range(len(y))), 0, MAXD, PURITY, fnames)
    tr_acc = np.mean([predict(node, Xz[i], fnames) == y[i]
                      for i in range(len(y))])
    print(f"  全量训练集准确率: {tr_acc:.4f}   树深: {depth_of(node)}")

    print("\n  树结构:")
    for ln in to_text(node, fnames):
        print("    " + ln)

    # 混淆矩阵
    cm = Counter((int(y[i]), int(predict(node, Xz[i], fnames)))
                 for i in range(len(y)))
    print("\n  混淆矩阵 (行=真实 0=其它/1=大世界, 列=预测):")
    for t in (0, 1):
        print(f"    真实{t}: 预测0={cm[(t,0)]:>3}  预测1={cm[(t,1)]:>3}")

    # 阈值导出
    def thr_of(n, acc):
        if n.feat is None:
            return
        i = fnames.index(n.feat)
        acc.append((float(mu[i] + sd[i] * n.thr), n.feat, n.thr))
        thr_of(n.left, acc)
        thr_of(n.right, acc)

    TH = []
    thr_of(node, TH)
    print("\n  阈值（还原到原始量纲）:")
    for t, nm, z in TH:
        print(f"    {nm:<34} <= {t:.4f}")
    print("=== DONE ===")


if __name__ == "__main__":
    main()
