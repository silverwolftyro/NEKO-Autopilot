"""探测 BetterGI 采集/矿产相关 JSON 的**结构**。

★ 只读，不写，不复制内容
------------------------
本脚本只报告：顶层类型、元素数量、前 3 条的字段名、坐标/物品名字段是否存在。
字符串值一律截断到 60 字符，不落任何数据副本。

★ 合规
------
BetterGI 是 GPL-3.0。这里只做"读结构记字段名"，
不把内容搬进我们的仓库。口径见 recognition/notes/LICENSING.md。
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
import sys
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(_pl.Path(_os.environ.get("NEKO_BETTERGI_DIR", "").strip() or "BetterGI") / 'I' / 'Assets')

TARGETS = [
    BASE / "Model" / "Yap" / "all_list.json",
    BASE / "Model" / "Yap" / "index_2_word.json",
    BASE / "Config" / "Pick" / "default_pick_white_lists.json",
    BASE / "Config" / "Pick" / "default_pick_black_lists.json",
]

#: 坐标类字段名的判定片段
COORD_HINTS = ("pos", "coord", "location", "world", "xyz", "xy")
#: 物品名 / 标签类字段名的判定片段
NAME_HINTS = ("name", "item", "label", "title", "word", "text")

#: 打印时字符串截断长度
_CLIP = 60
#: 递归扫描键名的节点预算
_BUDGET = 20000


def clip(v: Any) -> str:
    """把任意值压成一行可读文本，超长截断。"""
    if isinstance(v, str):
        s = v.replace("\n", "\\n")
        if len(s) > _CLIP:
            return f'"{s[:_CLIP]}…" (len={len(v)})'
        return f'"{s}"'
    if isinstance(v, (int, float, bool)) or v is None:
        return repr(v)
    if isinstance(v, list):
        return f"[list x{len(v)}]"
    if isinstance(v, dict):
        return "{object:" + ",".join(list(v.keys())[:6]) + "}"
    return type(v).__name__


def scan_keys(obj: Any, acc: set[str], budget: list[int]) -> None:
    """递归收集所有出现过的键名（只看键，不看值）。"""
    if budget[0] <= 0:
        return
    budget[0] -= 1
    if isinstance(obj, dict):
        for k, v in obj.items():
            acc.add(str(k))
            scan_keys(v, acc, budget)
    elif isinstance(obj, list):
        for v in obj[:300]:
            scan_keys(v, acc, budget)


def sample_dict(d: dict, idx: int) -> None:
    print(f"    [{idx}] 字段 {len(d)} 个:")
    for i, (k, v) in enumerate(d.items()):
        if i >= 12:
            print(f"        …（还有 {len(d) - 12} 个字段）")
            break
        print(f"        {k!r:28} = {clip(v)}")


def report(path: Path) -> dict:
    print("=" * 78)
    print(f"文件: {path.name}")
    print(f"字节: {path.stat().st_size:,}")

    raw = path.read_text(encoding="utf-8-sig")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        print(f"!! 不是合法 JSON: {exc}")
        return {"top": "INVALID", "n": 0, "coord": [], "name": []}

    top = type(data).__name__
    keys: set[str] = set()
    scan_keys(data, keys, [_BUDGET])

    coord = sorted(k for k in keys
                   if any(h in k.lower() for h in COORD_HINTS))
    namef = sorted(k for k in keys
                   if any(h in k.lower() for h in NAME_HINTS))

    # ---- 顶层结构 + 元素数 ----
    if isinstance(data, list):
        n = len(data)
        print(f"顶层: array   元素数: {n:,}")
        if data and isinstance(data[0], dict):
            print("  元素类型: object")
        elif data:
            print(f"  元素类型: {type(data[0]).__name__}（标量）")
        for i, item in enumerate(data[:3]):
            print(f"  样本 {i}: {clip(item)}")
            if isinstance(item, dict):
                sample_dict(item, i)
    elif isinstance(data, dict):
        n = len(data)
        print(f"顶层: object   键数: {n:,}")
        for i, (k, v) in enumerate(data.items()):
            if i >= 3:
                print(f"  …（还有 {n - 3} 个键）")
                break
            print(f"  [{i}] {k!r} = {clip(v)}")
            if isinstance(v, list):
                print(f"      -> list x{len(v)}，前 3:")
                for j, sub in enumerate(v[:3]):
                    print(f"         ({j}) {clip(sub)}")
                    if isinstance(sub, dict):
                        sample_dict(sub, j)
            elif isinstance(v, dict):
                sample_dict(v, i)
    else:
        n = 1
        print(f"顶层: {top}（标量，非集合）")
        print(f"  值: {clip(data)}")

    print(f"坐标类字段: {coord if coord else '无'}")
    print(f"物品名类字段: {namef if namef else '无'}")
    print(f"全部出现过的键（前 20）: {sorted(keys)[:20]}")

    return {"top": top, "n": n, "coord": coord, "name": namef}


def main() -> None:
    rows = []
    for p in TARGETS:
        if not p.is_file():
            print("=" * 78)
            print(f"!! 缺失: {p}")
            rows.append((p.name, "MISSING", 0, "-", "-"))
            continue
        r = report(p)
        rows.append((p.name, r["top"], r["n"],
                     "有" if r["name"] else "无",
                     "有" if r["coord"] else "无"))

    print()
    print("=" * 78)
    print("汇总表")
    print("=" * 78)
    print(f"{'文件':<34} {'顶层':<8} {'元素数':>9}  {'物品名字段':<10} {'坐标字段'}")
    print("-" * 78)
    for name, top, n, nf, cf in rows:
        print(f"{name:<34} {top:<8} {n:>9,}  {nf:<10} {cf}")


if __name__ == "__main__":
    main()
