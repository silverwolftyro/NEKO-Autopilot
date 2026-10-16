"""阶段 0 探针：BetterGI 分区 JSON 结构 + 米游社瓦片本地盘点。

★ 只读
----
不修改任何文件，不复制内容进仓库，不重新爬取。
只报告结构、字段名、样本（截断）与索引范围。
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
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BGI = Path(_pl.Path(_os.environ.get("NEKO_BETTERGI_DIR", "").strip() or "BetterGI") / 'I' / 'Assets' / 'Map' / 'Teyvat')
TILES = Path(RECOG_ROOT / 'data' / 'mihoyo_tiles')

TARGETS = ["city_info.json", "city_6_0_info.json",
           "layer_map_info.json", "layer_map_6_0_info.json"]

_CLIP = 70


def clip(v, n=_CLIP):
    if isinstance(v, str):
        s = v.replace("\n", "\\n")
        return f'"{s[:n]}…"' if len(s) > n else f'"{s}"'
    if isinstance(v, list):
        if not v:
            return "[]"
        if len(v) <= 4 and all(not isinstance(x, (list, dict)) for x in v):
            return "[" + ", ".join(clip(x, 24) for x in v) + "]"
        return f"[list x{len(v)}] 首项={clip(v[0], 46)}"
    if isinstance(v, dict):
        return "{obj:" + ",".join(list(v.keys())[:6]) + "}"
    return repr(v)


def show_dict_sample(d: dict, label: str) -> None:
    print(f"    样本 {label} —— 字段 {len(d)} 个:")
    for i, (k, v) in enumerate(d.items()):
        if i >= 14:
            print(f"        …（还有 {len(d) - 14} 个字段: "
                  f"{list(d.keys())[14:][:8]} …）")
            break
        print(f"        {k!r:26} = {clip(v)}")


def describe(p: Path) -> None:
    print("=" * 78)
    print(f"[{p.name}]  {p.stat().st_size:,} B")
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception as e:                       # noqa: BLE001
        print(f"  !! 解析失败 {e}")
        return

    print(f"  顶层: {type(data).__name__}")
    if isinstance(data, dict):
        print(f"  键数: {len(data)}")
        for i, (k, v) in enumerate(data.items()):
            if i >= 5:
                print(f"    …（还有 {len(data) - 5} 个键: "
                      f"{list(data.keys())[5:][:10]} …）")
                break
            print(f"    {k!r} = {clip(v)}")
            if isinstance(v, list) and v:
                print(f"        -> list x{len(v)}，元素类型 "
                      f"{type(v[0]).__name__}")
                for j, sub in enumerate(v[:3]):
                    if isinstance(sub, dict):
                        show_dict_sample(sub, f"[{j}]")
                    else:
                        print(f"        [{j}] {clip(sub)}")
    elif isinstance(data, list):
        print(f"  元素数: {len(data)}  元素类型 "
              f"{type(data[0]).__name__ if data else '?'}")
        for j, sub in enumerate(data[:3]):
            if isinstance(sub, dict):
                show_dict_sample(sub, f"[{j}]")
            else:
                print(f"    [{j}] {clip(sub)}")

    # 数值三元组（bounds 之类）
    tris = []
    def walk(o, budget=[60000]):
        if budget[0] <= 0:
            return
        budget[0] -= 1
        if isinstance(o, dict):
            for v in o.values():
                walk(v, budget)
        elif isinstance(o, list):
            if len(o) == 4 and all(isinstance(x, (int, float))
                                   and not isinstance(x, bool) for x in o):
                tris.append(("4", [float(x) for x in o]))
            elif len(o) == 3 and all(isinstance(x, (int, float))
                                     and not isinstance(x, bool) for x in o):
                tris.append(("3", [float(x) for x in o]))
            else:
                for v in o:
                    walk(v, budget)
    walk(data)
    c4 = [t for k, t in tris if k == "4"]
    c3 = [t for k, t in tris if k == "3"]
    print(f"  长度 4 的数值数组: {len(c4)}   长度 3 的数值数组: {len(c3)}")
    if c4:
        for ax in range(4):
            col = [t[ax] for t in c4]
            print(f"     [4元] 分量{ax}: min={min(col):>12.2f} "
                  f"max={max(col):>12.2f}")
    if c3:
        for ax in range(3):
            col = [t[ax] for t in c3]
            print(f"     [3元] 分量{ax}: min={min(col):>12.2f} "
                  f"max={max(col):>12.2f}")


def probe_tiles() -> None:
    print()
    print("=" * 78)
    print("[瓦片] 本地米游社底图盘点")
    if not TILES.is_dir():
        print(f"  !! 不存在: {TILES}")
        return
    print(f"  根: {TILES}")
    for d in sorted(TILES.iterdir()):
        if not d.is_dir():
            print(f"  FILE {d.name}  ({d.stat().st_size} B)")
            continue
        subs = sorted(p for p in d.iterdir())
        print(f"  DIR  {d.name}/   ({len(subs)} 项)")
        for s in subs:
            if s.is_dir():
                files = [f for f in s.iterdir() if f.is_file()]
                other = [f for f in s.iterdir() if not f.is_file()]
                print(f"        {s.name}/   文件 {len(files)}"
                      f"{f'  +{len(other)} 非文件' if other else ''}")
                for o in other[:5]:
                    print(f"           非文件: {o.name}")
                if files:
                    names = [f.name for f in files]
                    print(f"        样本: {names[:4]} … {names[-2:]}")
                    # 命名解析
                    pats = Counter()
                    a_vals, b_vals = set(), set()
                    for n in names:
                        m = re.match(r"^(\d+)_(\d+)_(\d+)_P(\d+)\.webp$", n)
                        if m:
                            pats["{m}_{x}_{y}_P{q}"] += 1
                            a_vals.add(int(m.group(2)))
                            b_vals.add(int(m.group(3)))
                    if pats:
                        print(f"        命名模式: {dict(pats)}")
                        if a_vals:
                            print(f"        第2段: {len(a_vals)} 个, "
                                  f"min={min(a_vals)} max={max(a_vals)} "
                                  f"样例={sorted(a_vals)[:10]}")
                        if b_vals:
                            print(f"        第3段: {len(b_vals)} 个, "
                                  f"min={min(b_vals)} max={max(b_vals)} "
                                  f"样例={sorted(b_vals)[:10]}")
            else:
                print(f"        FILE {s.name}  ({s.stat().st_size} B)")

    # 全量索引统计
    print()
    allf = list(TILES.rglob("*.webp"))
    print(f"  .webp 总数: {len(allf):,}")
    dirs = Counter(str(f.parent.relative_to(TILES)) for f in allf)
    print(f"  所在目录: {dict(dirs)}")
    a, b = set(), set()
    for f in allf:
        m = re.match(r"^(\d+)_(\d+)_(\d+)_P(\d+)\.webp$", f.name)
        if m:
            a.add(int(m.group(2)))
            b.add(int(m.group(3)))
    if a:
        print(f"  第2段全量: {len(a)} 个唯一, {min(a)}..{max(a)}")
        print(f"  第3段全量: {len(b)} 个唯一, {min(b)}..{max(b)}")
        expect = len(a) * len(b)
        print(f"  笛卡尔积应为 {expect:,}，实际 {len(allf):,} "
              f"-> {'完整' if expect == len(allf) else '不完整'}")
    other = [f for f in TILES.rglob("*")
             if f.is_file() and f.suffix.lower() not in (".webp", ".png")]
    print(f"  非图片文件: {len(other)}")
    for f in other[:20]:
        print(f"     {f.relative_to(TILES)}  ({f.stat().st_size} B)")


def find_metadata() -> None:
    print()
    print("=" * 78)
    print("[元数据] 本地是否存过米游社地图元数据")
    root = Path(RECOG_ROOT)
    hits = []
    for pat in ("**/*mihoyo*", "**/*map*meta*", "**/*mapData*",
                "**/*slice*", "**/*region*"):
        for f in root.glob(pat):
            if f.is_file() and f.suffix.lower() not in (".webp", ".png"):
                hits.append(f)
    seen, uniq = set(), []
    for f in hits:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    print(f"  命中 {len(uniq)} 个:")
    for f in uniq[:40]:
        print(f"     {f.relative_to(root)}  ({f.stat().st_size} B)")


def main() -> None:
    for name in TARGETS:
        p = BGI / name
        if p.is_file():
            describe(p)
        else:
            print("=" * 78)
            print(f"!! 缺失 {name}")
    probe_tiles()
    find_metadata()
    print()
    print("=== DONE ===")


if __name__ == "__main__":
    main()
