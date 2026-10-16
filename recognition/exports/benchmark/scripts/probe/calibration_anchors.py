"""阶段 B · 坐标标定锚点（方案 D：零 GPL 图，只用数值 + 人工标点）。

★ B.1  BetterGI 城市图层的 Left/Top/Scale      —— 只打印字段值
★ B.2  tp.json 里地理相距最远的两个传送点世界坐标 —— 只打印数值

★ 合规：GPL 零争议路径
--------------------
本脚本**不读任何 BetterGI 的图片**，只读 JSON 里的数值字段。
像素坐标由用户在米游社拼图上人工标注，不涉及任何外部素材。
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
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RECOG = Path(RECOG_ROOT)
TP = RECOG / "data" / "_ref_tp_sample.json"
CITY = (Path(_REPO_ROOT.parent / '现成的自动化工具参考' / '参考：better GI')
        / "BetterGI" / "Assets" / "Map" / "Teyvat" / "city_info.json")

#: 西北侧国家
NW = {"蒙德"}
#: 东南侧国家
SE = {"璃月", "须弥", "稻妻", "纳塔", "枫丹"}


def b1() -> None:
    print("=" * 78)
    print("B.1 · BetterGI 城市图层 Left/Top/Scale（仅数值）")
    print("=" * 78)
    if not CITY.is_file():
        print(f"  !! 不存在: {CITY}")
        return
    data = json.loads(CITY.read_text(encoding="utf-8-sig"))
    print(f"  文件: {CITY.name}   图层 {len(data)} 个\n")
    print(f"  {'LayerId':<14} {'Name':<16} {'Floor':>6} "
          f"{'Left':>11} {'Top':>11} {'Scale':>6}")
    print("  " + "-" * 66)
    for r in sorted(data, key=lambda x: float(x["Left"])):
        print(f"  {r['LayerId']:<14} {r['Name']:<16} {r['Floor']:>6} "
              f"{float(r['Left']):>11.1f} {float(r['Top']):>11.1f} "
              f"{r['Scale']:>6}")
    L = [float(r["Left"]) for r in data]
    T = [float(r["Top"]) for r in data]
    S = sorted({r["Scale"] for r in data})
    print("  " + "-" * 66)
    print(f"  Left 范围 {min(L):.1f} .. {max(L):.1f}")
    print(f"  Top  范围 {min(T):.1f} .. {max(T):.1f}")
    print(f"  Scale 取值: {S}")
    print(f"\n  公式（来自 BetterGI BaseMapLayerByTemplateMatch.cs:107-114）:")
    print(f"    pixel_x = (Left - world_x) * Scale / zoom")
    print(f"    pixel_y = (Top  - world_y) * Scale / zoom")
    print(f"  → Left/Top 是**图层左上角的世界坐标**，Y 轴翻转，符号别搞反")


def b2() -> None:
    print()
    print("=" * 78)
    print("B.2 · tp.json 锚点候选（西北 / 东南 两侧各取最远的）")
    print("=" * 78)
    raw = json.loads(TP.read_text(encoding="utf-8-sig"))
    buckets = defaultdict(list)
    for region in raw.get("data", []):
        rname = region.get("description") or region.get("mapName")
        for p in region.get("points", []):
            pos = p.get("position")
            if not (isinstance(pos, list) and len(pos) == 3):
                continue
            c = str(p.get("country", ""))
            buckets[c].append((float(pos[0]), float(pos[1]), float(pos[2]),
                               p.get("name", ""), p.get("type", ""),
                               rname, p.get("id")))

    print("\n  各国家传送点数:")
    for c, pts in sorted(buckets.items(), key=lambda x: -len(x[1])):
        xs = [q[0] for q in pts]
        zs = [q[2] for q in pts]
        print(f"    {c or '(空)':<8} n={len(pts):>4}   "
              f"x {min(xs):>9.1f}..{max(xs):>9.1f}   "
              f"z {min(zs):>9.1f}..{max(zs):>9.1f}")

    def pick(countries, axis, want_min, k=3):
        out = []
        for c in countries:
            out += [q for q in buckets.get(c, [])]
        if not out:
            return []
        key = (lambda q: q[0]) if axis == 0 else (lambda q: q[2])
        return sorted(out, key=key)[:k] if want_min else \
            sorted(out, key=key, reverse=True)[:k]

    print("\n" + "-" * 78)
    print("  候选锚点 —— 请在米游社拼图上找**容易辨认**的：")
    print("  神像最好（每个区域都有，形状固定、位置好找）")
    print("-" * 78)

    groups = [
        ("西北侧 · x 最小（蒙德）", NW, 0, True),
        ("东南侧 · x 最大（璃月/须弥/稻妻/纳塔/枫丹）", SE, 0, False),
        ("东南侧 · z 最小（璃月/须弥/稻妻/纳塔/枫丹）", SE, 2, True),
        ("东南侧 · z 最大（璃月/须弥/稻妻/纳塔/枫丹）", SE, 2, False),
    ]
    for title, cs, axis, wmin in groups:
        got = pick(cs, axis, wmin, k=3)
        if not got:
            print(f"\n  [{title}]  —— 无数据")
            continue
        print(f"\n  [{title}]")
        for x, y, z, name, typ, region, pid in got:
            print(f"    id={str(pid):<8} {name:<18} {typ:<16}")
            print(f"      区域={region}  country={None}")
            print(f"      world = (x={x:>10.2f}, y={y:>8.2f}, z={z:>10.2f})"
                  f"   [y 为高度]")

    # 打印真实 country，便于人工挑
    print("\n" + "-" * 78)
    print("  逐个列出（带 country），供人工挑选")
    print("-" * 78)
    for c in ["蒙德", "璃月", "须弥"]:
        pts = buckets.get(c, [])
        if not pts:
            continue
        for tag, keyf, rev in (("x 最小", lambda q: q[0], False),
                               ("x 最大", lambda q: q[0], True),
                               ("z 最小", lambda q: q[2], False),
                               ("z 最大", lambda q: q[2], True)):
            best = sorted(pts, key=keyf, reverse=rev)[0]
            print(f"  {c:<4} {tag}: id={str(best[6]):<8} {best[3]:<18} "
                  f"({best[0]:>9.1f}, {best[1]:>7.1f}, {best[2]:>9.1f})  "
                  f"type={best[4]}")


def main() -> None:
    b1()
    b2()
    print()
    print("=== 下一步：等你在 mihoyo 拼图上标像素坐标 ===")
    print("=== 拼图路径: recognition/data/mihoyo_maps/  （若还没拼，先跑 build/ 下的拼图脚本）")
    print("=== DONE ===")


if __name__ == "__main__":
    main()
