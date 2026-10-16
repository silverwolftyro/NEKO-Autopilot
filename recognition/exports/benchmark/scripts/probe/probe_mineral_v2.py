"""Sam5440：锁定真正的矿物坐标（枚举真实结构）。

上两版的教训：
  - `ore` 裸子串会命中 before/explore —— 一律用词边界
  - 猜目录结构会落空 —— 先枚举，再分析
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

ROOT = Path(_REPO_ROOT.parent / 'Genshin_Impact_Teleport')
RAW = ROOT / "AutoGeneratePoint" / "Points(Raw)[cn-en-ru]"
SORT34 = ROOT / "AutoGeneratePoint" / "Points(SortByItemKind)[ver3.4][cn-en][2023-01-18]"
ALL15 = SORT34 / "Teleport ALL[v3.4][AL-15M][2023-01-18]"

CN_MIN = re.compile(r"矿|水晶|晶体|晶石|矿石|原矿")
EN_MIN = re.compile(r"\b(Ore|Ores|Crystal|Crystals|Mineral|Minerals)\b")


def is_mineral(s: str) -> bool:
    return bool(CN_MIN.search(s) or EN_MIN.search(s))


def tree(base: Path, depth: int, label: str) -> None:
    print("=" * 78)
    print(f"[{label}] {base}")
    if not base.is_dir():
        print(f"  !! 不存在")
        return
    cur = [base]
    for d in range(depth):
        nxt = []
        for c in cur:
            try:
                kids = sorted(p for p in c.iterdir() if p.is_dir())
            except OSError:
                continue
            if not kids:
                continue
            pad = "  " * (d + 1)
            names = [k.name for k in kids]
            show = names[:12]
            mark = ""
            if any(is_mineral(n) for n in names):
                mark = "   <<< 含矿物"
            print(f"{pad}{c.name}  [{len(kids)}] "
                  f"{show}{'…' if len(names) > 12 else ''}{mark}")
            nxt += kids
        cur = nxt
        if len(cur) > 40:
            print(f"  (子目录过多，停止展开: {len(cur)})")
            break


def main() -> None:
    tree(RAW, 1, "A. Points(Raw) 第一层")
    tree(ALL15, 1, "B. Teleport ALL v3.4 分类层")

    # ---- 找出矿物类目 ----
    print()
    print("=" * 78)
    print("[C] 矿物类目（分类层）")
    mineral_cats = []
    if ALL15.is_dir():
        for cat in sorted(ALL15.iterdir()):
            if cat.is_dir() and is_mineral(cat.name):
                n = sum(1 for _ in cat.rglob("*.json"))
                mineral_cats.append((cat.name, n))
                print(f"  {n:>6} json   {cat.name}")
    if not mineral_cats:
        print("  分类层没有矿物关键字 —— 往下看子类目")
        if ALL15.is_dir():
            for cat in sorted(ALL15.iterdir()):
                if not cat.is_dir():
                    continue
                subs = [p for p in sorted(cat.iterdir()) if p.is_dir()]
                hit = [p for p in subs if is_mineral(p.name)]
                if hit:
                    print(f"  分类 {cat.name} 下:")
                    for h in hit:
                        n = sum(1 for _ in h.rglob("*.json"))
                        print(f"      {n:>6} json   {h.name}")

    # ---- 找具体矿物物件目录 ----
    print()
    print("=" * 78)
    print("[D] 矿物物件目录（含点数统计）")
    item_dirs = []
    for base in [ALL15, RAW]:
        if not base.is_dir():
            continue
        for d in base.rglob("*"):
            if not d.is_dir() or not is_mineral(d.name):
                continue
            try:
                js = [f for f in d.iterdir() if f.suffix == ".json"]
            except OSError:
                continue
            if js:
                item_dirs.append((d, js))
    item_dirs.sort(key=lambda x: -len(x[1]))
    print(f"  找到 {len(item_dirs)} 个矿物物件目录")
    for d, js in item_dirs[:25]:
        print(f"    {len(js):>5} 个文件   {d.relative_to(ROOT)}")

    # ---- 单文件结构 + 坐标分布 ----
    if item_dirs:
        d, js = item_dirs[0]
        print()
        print("=" * 78)
        print(f"[E] 单文件结构: {js[0].name}")
        print(f"    父目录: {d.relative_to(ROOT)}")
        data = json.loads(js[0].read_text(encoding="utf-8-sig"))
        print(f"  顶层: {type(data).__name__}")
        items = data if isinstance(data, list) else [data]
        print(f"  元素数: {len(items)}")
        for e in items[:3]:
            if isinstance(e, dict):
                print(f"    字段 {list(e.keys())}")
                for k, v in e.items():
                    print(f"      {k!r} = {v!r}")
            else:
                print(f"    {e!r}")

        print()
        print("=" * 78)
        print("[F] 坐标分布（抽样 80 个矿物文件）")
        axes = {"x": [], "y": [], "z": []}
        fields = Counter()
        npts = 0
        pool = [f for _, jsx in item_dirs[:40] for f in jsx][:80]
        for p in pool:
            try:
                dd = json.loads(p.read_text(encoding="utf-8-sig"))
            except Exception:                    # noqa: BLE001
                continue
            for e in (dd if isinstance(dd, list) else [dd]):
                if not isinstance(e, dict):
                    continue
                fields.update(e.keys())
                for k, v in e.items():
                    if (isinstance(v, list) and len(v) == 3
                            and all(isinstance(x, (int, float))
                                    and not isinstance(x, bool) for x in v)):
                        npts += 1
                        for ax, i in (("x", 0), ("y", 1), ("z", 2)):
                            if ax in k.lower():
                                axes[ax].append(float(v[i]))
        print(f"  坐标点总数: {npts:,}")
        print(f"  字段集合: {dict(fields)}")
        for ax in ("x", "y", "z"):
            c = axes[ax]
            if c:
                print(f"    {ax}: n={len(c):>6}  "
                      f"min={min(c):>10.2f}  max={max(c):>10.2f}")
        allc = axes["x"] + axes["y"] + axes["z"]
        if allc:
            neg = sum(1 for c in allc if c < 0)
            print(f"  含负值: {neg:,}/{len(allc):,} "
                  f"({100.0*neg/len(allc):.1f}%)  "
                  f"绝对值最大 {max(abs(c) for c in allc):.2f}")
    print()
    print("=== DONE ===")


if __name__ == "__main__":
    main()
