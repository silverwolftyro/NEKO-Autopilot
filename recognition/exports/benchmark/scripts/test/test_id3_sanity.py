"""ID3 决策树单元测试 —— 用来定位实现 bug。

★ 为什么要这个
--------------
上一轮重训时出现"交叉验证 1.0000 > 训练集 0.9714"这种数学上不可能的结果，
说明 ``train()`` / ``predict()`` 至少有一个是错的。
在 bug 定位前，任何"新特征能不能提升分类"的结论都不可信。

★ 三个测试
----------
T1 线性可分      x>0.5 且 y>0.5 -> A     期望训练 100%
T2 两层规则      x>0.7 -> A elif y>0.5 -> A else B   期望能找到等价树
T3 20% 标签噪声  期望训练 ~80%，CV ~75%
"""

from __future__ import annotations

import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

MAX_DEPTH = 5
MIN_PURITY = 0.95


# =====================================================  被测实现
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
    if (depth >= max_depth or len(cnt) == 1
            or max(cnt.values()) / n >= min_purity):
        return node
    base = entropy([y[i] for i in idx])
    best = (0.0, None, None)
    for f in range(X.shape[1]):
        vals = X[idx, f]
        qs = np.unique(np.percentile(vals, [10, 20, 30, 40, 50, 60, 70, 80, 90]))
        for t in (qs[:-1] if len(qs) > 1 else []):
            m = vals <= t
            if m.all() or (~m).all():
                continue
            li = [i for i, k in zip(idx, m) if k]
            ri = [i for i, k in zip(idx, m) if not k]
            if not li or not ri:
                continue
            g = (base - len(li) / n * entropy([y[i] for i in li])
                 - len(ri) / n * entropy([y[i] for i in ri]))
            if g > best[0]:
                best = (g, f, float(t))
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


def predict(node, row, colmap):
    while node.feat is not None:
        node = node.left if row[colmap[node.feat]] <= node.thr else node.right
    return node.pred


def depth_of(nd):
    return 0 if nd.feat is None else 1 + max(depth_of(nd.left),
                                             depth_of(nd.right))


def show(nd, cm, pre="  "):
    if nd.feat is None:
        print(f"{pre}-> {nd.pred}")
        return
    print(f"{pre}if {nd.feat} <= {nd.thr:.4f}:")
    show(nd.left, cm, pre + "    ")
    print(f"{pre}else:")
    show(nd.right, cm, pre + "    ")


def acc(nd, X, y, cm):
    return float(np.mean([predict(nd, X[i], cm) == y[i]
                          for i in range(len(y))]))


# =====================================================  测试
def t1():
    rs = np.random.default_rng(1)
    X = rs.random((100, 2))
    y = ((X[:, 0] > 0.5) & (X[:, 1] > 0.5)).astype(int)
    cm = {n: i for i, n in enumerate(["x", "y"])}
    nd = train(X, y, list(range(100)), 0, MAX_DEPTH, MIN_PURITY, ["x", "y"])
    a = acc(nd, X, y, cm)
    print(f"\n[T1] 线性可分  训练准确率 = {a:.4f}   深度 {depth_of(nd)}")
    show(nd, cm)
    return a >= 0.99, a


def t2():
    rs = np.random.default_rng(2)
    X = rs.random((200, 2))
    y = np.where(X[:, 0] > 0.7, 1, np.where(X[:, 1] > 0.5, 1, 0))
    cm = {n: i for i, n in enumerate(["x", "y"])}
    nd = train(X, y, list(range(200)), 0, MAX_DEPTH, MIN_PURITY, ["x", "y"])
    a = acc(nd, X, y, cm)
    print(f"\n[T2] 两层规则  训练准确率 = {a:.4f}   深度 {depth_of(nd)}")
    show(nd, cm)
    return a >= 0.95, a


def t3():
    rs = np.random.default_rng(3)
    X = rs.random((400, 2))
    y0 = ((X[:, 0] > 0.5) & (X[:, 1] > 0.5)).astype(int)
    flip = rs.random(400) < 0.20
    y = np.where(flip, 1 - y0, y0)
    cm = {n: i for i, n in enumerate(["x", "y"])}
    nd = train(X, y, list(range(400)), 0, MAX_DEPTH, MIN_PURITY, ["x", "y"])
    a = acc(nd, X, y, cm)
    # 诚实 CV：阈值只由训练子集定，测试子集独立评
    rs2 = np.random.default_rng(11)
    perm = rs2.permutation(400)
    accs = []
    for k in range(5):
        te = set(perm[k * 80:(k + 1) * 80].tolist())
        tr = [i for i in range(400) if i not in te]
        n2 = train(X, y, tr, 0, MAX_DEPTH, MIN_PURITY, ["x", "y"])
        accs.append(float(np.mean([predict(n2, X[i], cm) == y[i]
                                   for i in perm[k * 80:(k + 1) * 80]])))
    print(f"\n[T3] 20% 噪声  训练 {a:.4f}   CV 各折 "
          f"{[f'{v:.3f}' for v in accs]}  平均 {np.mean(accs):.4f}")
    ok = 0.70 <= np.mean(accs) <= 0.92 and a >= 0.70
    return ok, np.mean(accs)


def t4():
    """★ 关键：训练集准确率不可能低于交叉验证平均太多。
    如果出现 CV > 训练集，说明 train/predict 状态被污染。"""
    rs = np.random.default_rng(4)
    X = rs.random((150, 3))
    y = ((X[:, 0] + X[:, 1] - X[:, 2]) > 0.5).astype(int)
    cm = {n: i for i, n in enumerate(["a", "b", "c"])}
    nd = train(X, y, list(range(150)), 0, MAX_DEPTH, MIN_PURITY,
               ["a", "b", "c"])
    a = acc(nd, X, y, cm)
    rs2 = np.random.default_rng(11)
    perm = rs2.permutation(150)
    accs = []
    for k in range(5):
        te = set(perm[k * 30:(k + 1) * 30].tolist())
        tr = [i for i in range(150) if i not in te]
        n2 = train(X, y, tr, 0, MAX_DEPTH, MIN_PURITY, ["a", "b", "c"])
        accs.append(float(np.mean([predict(n2, X[i], cm) == y[i]
                                   for i in perm[k * 30:(k + 1) * 30]])))
    print(f"\n[T4] 可分三元  训练 {a:.4f}   CV 平均 {np.mean(accs):.4f}   "
          f"CV 最低 {min(accs):.4f}")
    print(f"     训练 >= CV最低 ?  {a >= min(accs) - 1e-9}   "
          f"(False 即为状态污染/实现 bug)")
    show(nd, cm)
    return a >= min(accs) - 1e-9, a


def main():
    print("=" * 66)
    print("ID3 单元测试")
    print("=" * 66)
    r = []
    r.append(("T1 线性可分", *t1()))
    r.append(("T2 两层规则", *t2()))
    r.append(("T3 20%噪声", *t3()))
    r.append(("T4 CV一致性", *t4()))
    print("\n" + "=" * 66)
    print(f"{'测试':<14}{'实测':>10}{'判定':>8}")
    print("-" * 66)
    allok = True
    for nm, ok, v in r:
        print(f"{nm:<14}{v:>10.4f}{'✅ PASS' if ok else '❌ FAIL':>8}")
        allok &= bool(ok)
    print("=" * 66)
    print("全部通过" if allok else "有测试失败 —— 实现有 bug")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
