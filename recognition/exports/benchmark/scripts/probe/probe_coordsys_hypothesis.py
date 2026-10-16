"""任务 1+2：Left/Top 与 tp.json 坐标系比对 + 其他 Map JSON 的 bounds 搜索。

★ 只读
----
BetterGI 侧只用本地已有的 _ref_tp_sample.json（与 BetterGI 的 tp.json 同源），
不写、不复制任何数据。
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
import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RECOG = Path(RECOG_ROOT)
TP = RECOG / "data" / "_ref_tp_sample.json"
BGI_MAP = Path(_pl.Path(_os.environ.get("NEKO_BETTERGI_DIR", "").strip() or "BetterGI") / 'I' / 'Assets' / 'Map' / 'Teyvat')

MAP4 = ["city_info.json", "city_6_0_info.json",
        "layer_map_info.json", "layer_map_6_0_info.json"]

MAP6 = ["path_map_info.json", "layer_map_info_liyue.json",
        "path_map_6_0_info.json", "mapback_info.json",
        "mapback_6_0_info.json", "template_match_studio_layers.json"]

#: 宽高 / 边界类字段名片段
BOUND_HINTS = ("bound", "rect", "width", "height", "size", "extent",
               "right", "bottom", "left", "top", "min", "max", "x", "y")


def rng(col):
    if not col:
        return "空"
    neg = sum(1 for v in col if v < 0)
    return (f"{min(col):>10.1f} .. {max(col):>9.1f}   "
            f"负值 {neg:>4}/{len(col):<4}")


# ============================================ 任务 1
def task1() -> None:
    print("=" * 84)
    print("任务 1 · Left/Top  vs  tp.json 坐标范围")
    print("=" * 84)

    print("\n[1a] tp.json 传送点坐标（世界坐标）")
    raw = json.loads(TP.read_text(encoding="utf-8-sig"))
    regions = raw.get("data", [])
    pos, tran = [], []
    npts = 0
    for r in regions:
        for p in r.get("points", []):
            npts += 1
            v = p.get("position")
            if isinstance(v, list) and len(v) == 3:
                pos.append(tuple(float(x) for x in v))
            t = p.get("tranPosition")
            if isinstance(t, list) and len(t) == 3:
                tran.append(tuple(float(x) for x in t))
    print(f"  区域 {len(regions)} 个 / 传送点 {npts} 个")
    print(f"  position     有效 {len(pos)} 个")
    for i, ax in enumerate("xyz"):
        print(f"    {ax} = {rng([t[i] for t in pos])}")
    print(f"  tranPosition 有效 {len(tran)} 个")
    for i, ax in enumerate("xyz"):
        print(f"    {ax} = {rng([t[i] for t in tran])}")

    xs = [t[0] for t in pos]
    ys = [t[1] for t in pos]
    zs = [t[2] for t in pos]
    print(f"\n  水平面分量 (x ∪ z) 合并: {rng(xs + zs)}")
    print(f"  y（高度）单独看: {rng(ys)}")
    print(f"  水平面符号分布: 负 {sum(1 for v in xs+zs if v<0):>4}"
          f" / 正 {sum(1 for v in xs+zs if v>=0):>4} / 共 {len(xs+zs)}")

    print("\n[1b] BetterGI 四个 Map JSON 的 Left/Top")
    L, T, rows = [], [], []
    for name in MAP4:
        p = BGI_MAP / name
        if not p.is_file():
            continue
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        for r in data:
            L.append(float(r["Left"]))
            T.append(float(r["Top"]))
            rows.append((name, r))
        print(f"  {name:<24} n={len(data):>4}  "
              f"Scale={sorted({r['Scale'] for r in data})}")
    print(f"\n  Left = {rng(L)}")
    print(f"  Top  = {rng(T)}")
    print(f"  Left ∪ Top 符号: 负 {sum(1 for v in L+T if v<0):>4}"
          f" / 非负 {sum(1 for v in L+T if v>=0):>4} / 共 {len(L+T)}")

    print("\n[1c] 并排比较")
    print(f"  {'轴':<16} {'Left/Top 范围':>26} {'tp.json 同类范围':>26}")
    print("  " + "-" * 70)
    print(f"  {'X':<16} {rng(L):>26} {rng(xs):>26}")
    print(f"  {'Y':<16} {rng(T):>26} {rng(zs):>26}   <- tp 的 z")
    print(f"  {'(对照) tp.y':<16} {'':>26} {rng(ys):>26}   <- 高度，非水平")

    print(f"\n  数量级:  Left/Top 最大 {max(L+T):.1f}   "
          f"tp 水平面最大 {max(xs+zs):.1f}   "
          f"-> 倍率约 {max(xs+zs)/max(L+T):.2f}")
    print(f"  tp 水平面最小 {min(xs+zs):.1f}   Left/Top 最小 {min(L+T):.1f}")
    print(f"  负值占比:  Left/Top {100*sum(1 for v in L+T if v<0)/len(L+T):.1f}%"
          f"   tp 水平面 {100*sum(1 for v in xs+zs if v<0)/len(xs+zs):.1f}%")

    # 若按 Scale 缩放后能否对齐
    print("\n[1d] 按 Scale 缩放后能否对齐（假设 Left/Top 是世界坐标被缩放过）")
    byscale = {}
    for name, r in rows:
        byscale.setdefault(r["Scale"], []).append(
            (float(r["Left"]), float(r["Top"])))
    for s in sorted(byscale):
        g = byscale[s]
        gl = [v[0] for v in g]
        gt = [v[1] for v in g]
        print(f"  Scale={s}  n={len(g):>4}  "
              f"Left {rng(gl)}")
        print(f"  {'':>13}       Top  {rng(gt)}")

    # 落点/本体差异
    print("\n[1e] tp.json 本体 vs 落点 距离")
    import math
    ds = []
    for (a, b) in zip(pos, tran):
        d = math.dist(a, b)
        ds.append(d)
    if ds:
        print(f"  n={len(ds)}  min={min(ds):.2f} m  max={max(ds):.2f} m  "
              f"均值={sum(ds)/len(ds):.2f} m")


# ============================================ 任务 2
def find_arrays(o, want, acc, budget=[80000], path="$"):
    if budget[0] <= 0:
        return
    budget[0] -= 1
    if isinstance(o, dict):
        for k, v in o.items():
            acc.append((path, str(k), k))
            find_arrays(v, want, acc, budget, f"{path}.{k}")
    elif isinstance(o, list):
        if len(o) == want and all(isinstance(x, (int, float))
                                  and not isinstance(x, bool) for x in o):
            acc.append((path, f"[{want}元数组]", o))
        if o:
            find_arrays(o[0], want, acc, budget, f"{path}[0]")


def task2() -> None:
    print()
    print("=" * 84)
    print("任务 2 · 其他 Map JSON 有没有完整 bounds")
    print("=" * 84)
    for name in MAP6:
        p = BGI_MAP / name
        print("-" * 84)
        if not p.is_file():
            print(f"[{name}] !! 不存在")
            continue
        print(f"[{name}]  {p.stat().st_size:,} B")
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception as e:                  # noqa: BLE001
            print(f"  !! 解析失败 {e}")
            continue
        print(f"  顶层: {type(data).__name__}")
        if isinstance(data, dict):
            print(f"  键数: {len(data)}")
            for i, (k, v) in enumerate(data.items()):
                if i >= 6:
                    print(f"    …还有 {len(data)-6} 个键: "
                          f"{list(data.keys())[6:][:10]}")
                    break
                s = json.dumps(v, ensure_ascii=False)
                print(f"    {k!r} = {s[:110]}")
        elif isinstance(data, list):
            print(f"  元素数: {len(data)}  类型 "
                  f"{type(data[0]).__name__ if data else '?'}")
            for j, sub in enumerate(data[:2]):
                s = json.dumps(sub, ensure_ascii=False)
                print(f"    [{j}] {s[:300]}")

        for want in (4, 3, 2):
            acc = []
            find_arrays(data, want, acc)
            if not acc:
                continue
            if want == 4:
                print(f"  ★ 找到 {len(acc)} 处 4 元数值数组:")
                for path, key, val in acc[:6]:
                    print(f"      {path}  {key} = {val}")
            else:
                print(f"  {want}元数值数组: {len(acc)} 处  "
                      f"首例 {acc[0][0]} = {acc[0][2]}")

        acc = []
        find_arrays(data, 0, acc)
        keys = sorted({k for _, k, _ in acc if not k.endswith("数组")})
        hits = [k for k in keys if any(h in k.lower() for h in
                                      ("bound", "rect", "width", "height",
                                       "size", "extent", "right", "bottom"))]
        print(f"  边界类字段名: {hits if hits else '无'}")
        print(f"  全部键名(前 25): {keys[:25]}")


def main() -> None:
    task1()
    task2()
    print()
    print("=== DONE ===")


if __name__ == "__main__":
    main()
