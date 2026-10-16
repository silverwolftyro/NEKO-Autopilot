#!/usr/bin/env python
"""BetterGI 路线分类器（2026-10-09）

★ 它解决什么
-----------
BetterGI 的路线库是**采集路线为主**：去回同一个刷新点采一圈，路点绕、
间距不均、转角大。拿它们当导航路点用，脚本只能 A→B 直走，中间的障碍看不见，
真机表现为一路撞墙 + 连触发脱困 + 最后 panic。

分类器把「能不能当导航路点用」量化成两个几何指标：

======================  ==========================================
指标                     含义
======================  ==========================================
``detour``（绕行比）     实际路径长 / 起终点直线距离。≈1 = 几乎直线
``mean_turn``（均转角）  相邻段之间的平均转角绝对值。≈0 = 不折返
``cv``（间距变异系数）   路点间距的标准差/均值。≈0 = 间距均匀
======================  ==========================================

判据（默认）：``detour < 1.20`` 且 ``mean_turn < 15°`` -> 导航型。

★ 全池实测（蒙德 + 璃月，2026-10-09）
------------------------------------
    可走段（按 teleport 切分后）        1815
    含 dash 被我们滤掉                   506  (28%)
    **能走且是导航型**                    **7**

也就是说 —— 蒙德+璃月的 BetterGI 路线库里，**我们能执行的导航型路线只有 7 条**。
这不是筛得太严：放宽到 detour<1.3 / turn<20 / 跨度 800m 也只多出个位数。
瓶颈是数据本身（采集型为主）+ 我们做不到 dash。

★ 用法
------
::

    # 对已转好的路线目录做分类（我们自己的 user_waypoints schema）
    python classify_routes.py --input D:\\NEKO\\recognition\\data\\routes_nav

    # 对 BetterGI 原始路线目录做分类（自动切段 + 过滤做不到的移动）
    python classify_routes.py --input D:\\NEKO\\recognition\\...\\pathing --bgi

    # 只看统计不写文件
    python classify_routes.py --input <dir> --dry
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:                                        # pragma: no cover
    pass

#: 我们做不到的移动（硬门槛）。fly **不算** —— 滑翔是优化不是地形要求。
BAD_MODES = {"jump", "climb", "swim", "dash"}
DETOUR_MAX = 1.20
TURN_MAX = 15.0
CV_MAX = 1.20


def shape_of(pts: list[tuple[float, float]]) -> dict:
    """给一串点算形状指标。``pts`` 已含起点。"""
    seg = [(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
           for i in range(len(pts) - 1)]
    ds = [math.hypot(*s) for s in seg]
    if not ds:
        return {"detour": 99.0, "mean_turn": 99.0, "turn_sum": 99.0,
                "cv": 99.0, "path": 0.0, "span": 0.0,
                "dmin": 0.0, "dmax": 0.0}
    turns = []
    for i in range(1, len(seg)):
        a1 = math.atan2(seg[i - 1][1], seg[i - 1][0])
        a2 = math.atan2(seg[i][1], seg[i][0])
        turns.append(abs(math.degrees((a2 - a1 + math.pi) % (2 * math.pi)
                                      - math.pi)))
    span = math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1])
    path = sum(ds)
    mu = path / len(ds)
    sd = math.sqrt(sum((d - mu) ** 2 for d in ds) / len(ds))
    return {
        "detour": path / span if span > 0 else 99.0,
        "mean_turn": sum(turns) / len(turns) if turns else 0.0,
        "turn_sum": sum(turns),
        "cv": sd / mu if mu > 0 else 99.0,
        "path": path, "span": span,
        "dmin": min(ds), "dmax": max(ds),
    }


def is_nav(s: dict, detour_max: float, turn_max: float,
           cv_max: float = 99.0) -> bool:
    return (s["detour"] < detour_max and s["mean_turn"] < turn_max
            and s["cv"] < cv_max)


def classify_ours(path: Path, detour_max: float, turn_max: float,
                  cv_max: float) -> list[dict]:
    """分类**我们自己的** user_waypoints schema 路线。"""
    out = []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:                                # noqa: BLE001
        return [{"file": path.name, "error": str(exc)}]
    wps = doc.get("waypoints") or []
    st = doc.get("start") or ([wps[0]["x"], wps[0]["z"]] if wps else [0, 0])
    pts = [(float(st[0]), float(st[1]))] + [(float(w["x"]), float(w["z"]))
                                             for w in wps]
    s = shape_of(pts)
    out.append({"file": path.name, "points": len(wps), **s,
                "nav": is_nav(s, detour_max, turn_max, cv_max)})
    return out


def classify_bgi(path: Path, detour_max: float, turn_max: float,
                 cv_max: float) -> list[dict]:
    """分类 **BetterGI 原始**路线：按 teleport 切段 + 过滤做不到的移动。"""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:                                # noqa: BLE001
        return [{"file": path.name, "error": str(exc)}]
    pos = doc.get("positions") or []
    if not pos:
        return []
    idx = [i for i, p in enumerate(pos)
           if str(p.get("type", "")) == "teleport"]
    if not idx:
        return []
    out = []
    for si, (a, b) in enumerate(zip(idx, idx[1:] + [len(pos)]), 1):
        seg = pos[a:b]
        if len(seg) < 2:
            continue
        bad = {str(p.get("move_mode", "")) for p in seg} & BAD_MODES
        if bad:
            out.append({"file": path.name, "seg": f"{si}", "points": 0,
                        "skip": f"含做不到的移动 {sorted(bad)}"})
            continue
        try:
            sx, sz = float(seg[0]["y"]), float(seg[0]["x"])   # ★ 轴互换
        except (KeyError, TypeError, ValueError):
            continue
        pts = [(sx, sz)]
        for p in seg[1:]:
            if str(p.get("type", "")) in ("path", "target", ""):
                try:
                    pts.append((float(p["y"]), float(p["x"])))   # 轴互换
                except (KeyError, TypeError, ValueError):
                    continue
        if len(pts) < 2:
            continue
        s = shape_of(pts)
        out.append({"file": path.name, "seg": f"{si}", "points": len(pts) - 1,
                    **s, "nav": is_nav(s, detour_max, turn_max, cv_max)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="BetterGI 路线形状分类器")
    ap.add_argument("--input", required=True, help="路线目录")
    ap.add_argument("--output", default="", help="导航型子集的输出目录（空=只统计）")
    ap.add_argument("--bgi", action="store_true",
                    help="输入是 BetterGI 原始格式（自动切段+轴互换）")
    ap.add_argument("--detour-max", type=float, default=DETOUR_MAX)
    ap.add_argument("--turn-max", type=float, default=TURN_MAX)
    ap.add_argument("--cv-max", type=float, default=CV_MAX,
                    help="间距变异系数上限（默认 1.2=不额外限制）")
    ap.add_argument("--dry", action="store_true", help="只统计不写文件")
    args = ap.parse_args()

    d = Path(args.input)
    if not d.is_dir():
        print(f"  ✗ 不是目录：{d}")
        return 2
    fn = classify_bgi if args.bgi else classify_ours
    rows: list[dict] = []
    for p in sorted(d.glob("*.json")):
        if p.name == "_index.json":
            continue
        rows.extend(fn(p, args.detour_max, args.turn_max, args.cv_max))

    scored = [r for r in rows if "nav" in r]
    nav = [r for r in scored if r["nav"]]
    skipped = [r for r in rows if "skip" in r]
    print()
    print("=" * 92)
    print(f"  输入        : {d}   （{'BetterGI 原始' if args.bgi else '我们的 schema'}）")
    print(f"  判据        : 绕行比<{args.detour_max:g} 且 均转角<{args.turn_max:g}° "
          f"且 间距CV<{args.cv_max:g}")
    print(f"  可评估段    : {len(scored)}")
    print(f"  **导航型**  : {len(nav)}")
    if skipped:
        print(f"  跳过        : {len(skipped)}")
    print("=" * 92)
    if nav:
        print(f"  {'文件':<44}{'点数':>4}{'绕行比':>8}{'均转角':>8}{'CV':>7}{'跨度m':>9}")
        print("  " + "-" * 88)
        for r in sorted(nav, key=lambda x: x.get("span", 0)):
            tag = r["file"][:38] + (f" [{r['seg']}]" if "seg" in r else "")
            print(f"  {tag:<44}{r['points']:>4}{r['detour']:>8.2f}"
                  f"{r['mean_turn']:>7.1f}°{r['cv']:>7.2f}{r['span']:>9.1f}")
    if args.output and not args.dry and nav:
        o = Path(args.output)
        o.mkdir(parents=True, exist_ok=True)
        n = 0
        for r in nav:
            src = d / r["file"]
            if not src.is_file():
                continue
            n += 1
            (o / f"{n:03d}-{src.name}").write_text(
                src.read_text(encoding="utf-8"), encoding="utf-8")
        (o / "_nav_index.json").write_text(json.dumps(
            nav, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  写出 {n} 条导航型 -> {o}/")
    elif args.output:
        print("\n  没有导航型可写（或 --dry）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
