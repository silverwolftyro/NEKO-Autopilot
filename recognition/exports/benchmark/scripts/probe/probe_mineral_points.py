"""Sam5440 传送点仓库：矿物坐标专项分析（修正版）。

★ 修正说明
----------
上一版脚本用 `ore` 做正则子串匹配，把 `before` / `explore` 全命中了，
导致 10,911 这个数字被严重虚高。本版改用：
  - 目录名**精确**等于 Item 分类
  - 中文矿物关键词（矿/水晶/晶体/晶石/矿石/原矿）
  - 英文用词边界（\\bOre\\b 等），不再裸匹配子串
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
SORTED = ROOT / "AutoGeneratePoint" / "Points(SortByItemKind)[ver3.4][cn-en][2023-01-18]"

#: 中文矿物关键词
CN_MIN = re.compile(r"矿|水晶|晶体|晶石|矿石|原矿")
#: 英文矿物关键词 —— **带词边界**，不再裸匹配子串
EN_MIN = re.compile(r"\b(Ore|ores|Crystal|Crystals|Mineral|Minerals)\b")


def is_mineral_dir(name: str) -> bool:
    return bool(CN_MIN.search(name) or EN_MIN.search(name))


# ---------------------------------------------------- 1. Item 分类下的矿物
def probe_cn_item() -> None:
    print("=" * 78)
    print("[1] Points(Raw)/cn/Item 下的矿物物件")
    base = RAW / "cn" / "Item"
    if not base.is_dir():
        print(f"  !! 不存在: {base}")
        return
    maps = sorted(p for p in base.iterdir() if p.is_dir())
    print(f"  地图子目录 {len(maps)} 个: "
          f"{[m.name for m in maps][:8]}")

    rows = []
    for mp in maps:
        if not mp.is_dir():
            continue
        for item in sorted(mp.iterdir()):
            if not item.is_dir():
                continue
            if not is_mineral_dir(item.name):
                continue
            nfiles = 0
            try:
                nfiles = sum(1 for _ in item.iterdir())
            except OSError:
                pass
            rows.append((item.name, mp.name, nfiles))
    print(f"  矿物物件条目: {len(rows)}")
    for nm, mp, nf in rows[:40]:
        print(f"    {nf:>4} 个文件  [{mp}] {nm}")
    if len(rows) > 40:
        print(f"    ... 还有 {len(rows) - 40} 条")
    return rows


# ---------------------------------------------------- 2. 单文件结构
def deep_one(path: Path) -> None:
    print()
    print("=" * 78)
    print(f"[2] 单文件结构: {path.name}")
    print(f"    相对路径: {path.relative_to(ROOT)}")
    print(f"    字节: {path.stat().st_size}")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    print(f"  顶层: {type(data).__name__}")
    if isinstance(data, list):
        print(f"  元素数: {len(data)}")
        for i, e in enumerate(data[:3]):
            if isinstance(e, dict):
                print(f"    [{i}] 字段 {list(e.keys())}")
                for k, v in e.items():
                    print(f"         {k!r} = {v!r}")
            else:
                print(f"    [{i}] = {e!r}")
    elif isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, list):
                print(f"  {k!r}: list x{len(v)}  样本={v[:2]}")
            else:
                print(f"  {k!r} = {v!r}")


# ---------------------------------------------------- 3. 坐标分布
def coord_stats(paths: list[Path], cap: int = 60) -> None:
    print()
    print("=" * 78)
    print(f"[3] 坐标分布（抽样 {min(cap, len(paths))} 个矿物文件）")
    xs, ys, zs, pts = [], [], [], 0
    fields = Counter()
    for p in paths[:cap]:
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception:                        # noqa: BLE001
            continue
        items = data if isinstance(data, list) else [data]
        for e in items:
            if not isinstance(e, dict):
                continue
            fields.update(e.keys())
            for k, v in e.items():
                kl = k.lower()
                if not isinstance(v, list) or len(v) != 3:
                    continue
                if not all(isinstance(x, (int, float)) for x in v):
                    continue
                pts += 1
                if "x" in kl:
                    xs.append(float(v[0]))
                if "y" in kl:
                    ys.append(float(v[1]))
                if "z" in kl:
                    zs.append(float(v[2]))
    print(f"  坐标点总数: {pts:,}")
    print(f"  出现过的字段: {dict(fields)}")
    for nm, col in (("x", xs), ("y", ys), ("z", zs)):
        if col:
            print(f"    {nm}: n={len(col):>6}  "
                  f"min={min(col):>10.2f}  max={max(col):>10.2f}")
    if ys:
        print(f"  y(=高度) 样本区间 {min(ys):.1f} ~ {max(ys):.1f}")
    allc = xs + ys + zs
    if allc:
        neg = sum(1 for c in allc if c < 0)
        print(f"  含负值分量: {neg:,}/{len(allc):,} "
              f"({100.0*neg/len(allc):.1f}%)  "
              f"绝对值最大 {max(abs(c) for c in allc):.2f}")


# ---------------------------------------------------- 4. SortByItemKind 树
def probe_sorted() -> None:
    print()
    print("=" * 78)
    print("[4] Points(SortByItemKind)[ver3.4] —— 中文+英文对照的整理树")
    if not SORTED.is_dir():
        print(f"  !! 不存在: {SORTED}")
        return
    for p in sorted(SORTED.iterdir()):
        if p.is_dir():
            try:
                n = sum(1 for _ in p.rglob("*.json"))
            except OSError:
                n = -1
            print(f"  DIR  {p.name}   (json {n} 个)")
        else:
            print(f"  FILE {p.name}   ({p.stat().st_size} B)")


def main() -> None:
    rows = probe_cn_item() or []
    files = []
    for item_dir in (RAW / "cn" / "Item").glob("*/*"):
        if item_dir.is_dir() and is_mineral_dir(item_dir.name):
            try:
                files += [f for f in sorted(item_dir.iterdir())
                          if f.suffix == ".json"]
            except OSError:
                pass
    print()
    print(f"  矿物 JSON 文件总数: {len(files)}")
    if files:
        deep_one(files[0])
        coord_stats(files)
    probe_sorted()
    print()
    print("=== DONE ===")


if __name__ == "__main__":
    main()
