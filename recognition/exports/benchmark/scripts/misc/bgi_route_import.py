#!/usr/bin/env python
"""BetterGI 路线库 -> 我们的 user_waypoints.json schema（2026-10-08）

★ 数据源：**本地 git 缓存**，不走 GitHub API
------------------------------------------------
上游是 ``babalae/bettergi-scripts-list`` 的 ``repo/pathing/``。
先在本地克隆一份（**匿名、公开仓库、无限流**）::

    git clone --depth 1 https://github.com/babalae/bettergi-scripts-list.git ^
        D:\\NEKO\\recognition\\exports\\benchmark\\_bgi_cache\\repo

规模（实测）：19,273 文件 / 114.9 MB，``repo/pathing/**/*.json`` **5,303 条**，
分 6 类：其他 / 地方特产 / 敌人与魔物 / 矿物 / 锄地专区 / 食材与炼金。

★ 为什么要本地而不是 API
----------------------
GitHub **未认证 API 只有 60 次/小时**，而逐层翻目录要几百次调用 —— 上一版
就是这么撞死的（403 rate limit exceeded）。git 协议不走这个配额，
``raw.githubusercontent.com`` 那个 CDN 也不走。

★ 坐标系（已从数据本身验证，不是猜的）
--------------------------------------
BetterGI 路径追踪 JSON 的 ``positions[].x/y`` 与我们的世界坐标是
**同一套数的两个轴互换**::

    我们的 x = BGI 的 y
    我们的 z = BGI 的 x

证据（4 个跨区域样本，每个都拿 BGI 的第一个 teleport 点去比
``recognition/data/_ref_tp_sample.json`` 里最近的传送锚点）：

======================================  =========================  ========
BGI (x, y)                              我们最近传送锚点 (x, z)      距离
======================================  =========================  ========
(-914.85, 1795.52)  蒙德·清泉镇         (1790.31, -912.98)          1.87 m
( 490.07, 1435.40)  璃月·无妄坡         (1446.85,   481.78)        14.13 m
(-164.26, 1182.79)  璃月·地中之盐       (1182.93,  -160.05)         4.21 m
(-188.26,  972.73)  璃月·明蕴镇上方     ( 967.44,  -186.95)         5.45 m
======================================  =========================  ========

残差方向各不相同 -> **不是常量平移，是纯粹的轴互换**；
残差本身来自「这条路线传送到的是哪几个锚点里的哪一个」。

★ 我们**不能**做的两件事（过滤时必须挡住）
----------------------------------------
1. **不能传送**。e2e_pathfind 只按 W/Shift。所以路线按 teleport **切段**，
   每段只含 1 个 teleport（且在段首）—— 正好符合我们的能力。
2. **不能跳/爬/游/冲**。``jump`` ``climb`` ``swim`` ``dash`` 是硬门槛，
   遇到整段作废。

   ⚠️ ``fly`` **不算**（2026-10-08 修正）。它是**滑翔优化**（迪希雅/夜兰那种），
   不是地形硬要求 —— 同一个点走过去通常也通。把它当硬门槛会把几乎所有
   采集路线毙掉（实测第一条嘟嘟莲路线的两段各含 1 个 fly）。现在 fly 的点
   照收，只在 ``note`` 里标出来：**绕不过去是实跑发现的，不该在导入阶段
   就替他决定**。

★ 用法
------
::

    python bgi_route_import.py --list                        # 看看有多少
    python bgi_route_import.py --sample 10                   # 抽 10 条写盘
    python bgi_route_import.py --sample 10 --dry             # 只统计不写
    python bgi_route_import.py --sample 10 --cache D:\\other\\repo
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

import argparse
import json
import math
import os
import sys
import time
from collections import Counter
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:                                        # pragma: no cover
    pass

#: 本地克隆目录（**默认**）。可用 --cache 覆盖。
DEFAULT_CACHE = Path(RECOG_ROOT / 'exports' / 'benchmark' / '_bgi_cache' / 'repo')
#: 相对缓存根的路线目录。
PATHING = "repo/pathing"

OUT_DIR = Path(RECOG_ROOT / 'data' / 'routes')
SCHEMA_VERSION = 1

#: 抽样窗口
SPAN_MIN, SPAN_MAX = 50.0, 300.0
WP_MIN, WP_MAX = 5, 30
PREFERRED_AREAS = ("蒙德", "璃月")
CATEGORIES = ("地方特产", "矿物", "敌人与魔物", "锄地专区", "食材与炼金", "其他")

#: 真正做不到的（硬门槛）
BAD_MODES = {"jump", "climb", "swim", "dash"}
#: 能做到但方式不同（照收，标注）
SOFT_MODES = {"fly"}


# ---------------------------------------------------------------- 本地遍历
def cache_root(cache: str | Path) -> Path:
    p = Path(cache)
    if (p / "repo" / "pathing").is_dir():
        return p                      # 传的是仓库根
    if (p / "pathing").is_dir():
        return p / ".."                # 传的是仓库根的上一级
    return p


def walk_routes(cache: Path, area: str = "", category: str = "",
                mat_filter: str = "") -> list[dict]:
    """遍历本地缓存里的路线 JSON。**不碰网络**。"""
    root = cache_root(cache) / "repo" / "pathing"
    if not root.is_dir():
        print(f"  ✗ 找不到路线目录：{root}")
        print(f"    先克隆：git clone --depth 1 "
              f"https://github.com/babalae/bettergi-scripts-list.git "
              f"{DEFAULT_CACHE}")
        return []
    cats = [category] if category else list(CATEGORIES)
    out: list[dict] = []
    for c in cats:
        cdir = root / c
        if not cdir.is_dir():
            continue
        for p in cdir.rglob("*.json"):
            rel = p.relative_to(root).as_posix()
            if area and area not in rel:
                continue
            parts = rel.split("/")
            if len(parts) < 3:
                continue
            material = parts[1]
            if mat_filter and mat_filter not in material:
                continue
            out.append({"path": p, "rel": rel,
                        "name": p.stem, "category": c,
                        "area": area or _area_of(rel),
                        "material": material,
                        "size": p.stat().st_size})
    return out


def _area_of(rel: str) -> str:
    """从路径/文件名里认地区。BetterGI 命名规范里地区是文件名的一段。"""
    for a in ("蒙德", "璃月", "稻妻", "须弥", "枫丹", "纳塔", "至冬",
              "挪德卡莱"):
        if a in rel:
            return a
    return ""


# ---------------------------------------------------------------- 转换
def bgi_to_ours(x: float, y: float) -> tuple[float, float]:
    """BGI (x, y) -> 我们 (x, z)。**轴互换**，见文件头验证表。"""
    return y, x


def parse_route(meta: dict) -> tuple[list[dict], list[tuple[str, str]]]:
    """读一条 BGI 路线，**按 teleport 切段**，每段转成我们的 schema。

    :returns: ``(段列表, 跳过原因列表)`` —— 跳过的**都带原因**，
        不静默丢（静默丢会让抽样悄悄少几条而统计表上看不出来）。
    """
    skipped: list[tuple[str, str]] = []
    try:
        doc = json.loads(Path(meta["path"]).read_text(encoding="utf-8"))
    except Exception as exc:                                # noqa: BLE001
        return [], [(meta["name"], f"读取失败 {exc}")]
    pos = doc.get("positions") or []
    info = doc.get("info") or {}
    name = str(info.get("name") or meta["name"])
    if not pos:
        return [], [(name, "positions 为空")]

    idx = [i for i, p in enumerate(pos)
           if str(p.get("type", "")) == "teleport"]
    if not idx:
        return [], [(name, "没有 teleport（我们无法确定起点）")]
    bounds = list(zip(idx, idx[1:] + [len(pos)]))
    segs: list[dict] = []
    for si, (a, b) in enumerate(bounds, 1):
        seg = pos[a:b]
        if len(seg) < 2:
            continue
        bad = {str(p.get("move_mode", "")) for p in seg} & BAD_MODES
        if bad:
            skipped.append((f"{name}#{si}", f"含做不到的移动 {sorted(bad)}"))
            continue
        try:
            sx, sz = bgi_to_ours(float(seg[0]["x"]), float(seg[0]["y"]))
        except (KeyError, TypeError, ValueError):
            skipped.append((f"{name}#{si}", "teleport 点坐标不可读"))
            continue
        wps: list[dict] = []
        for p in seg[1:]:
            if str(p.get("type", "")) in ("path", "target", ""):
                try:
                    x, z = bgi_to_ours(float(p["x"]), float(p["y"]))
                except (KeyError, TypeError, ValueError):
                    continue
                mode = str(p.get("move_mode", ""))
                note = p.get("type", "")
                if mode in SOFT_MODES:
                    note = (note + " " + mode).strip()
                    note += "（原滑翔，我们走）"
                wps.append({"x": round(x, 1), "z": round(z, 1), "note": note})
        if not wps:
            skipped.append((f"{name}#{si}", "段内没有可用路点"))
            continue
        span = math.hypot(wps[-1]["x"] - sx, wps[-1]["z"] - sz)
        segs.append({
            "name": (name if len(bounds) == 1
                     else f"{name} [第{si}/{len(bounds)}段]"),
            "base_name": name, "seg_i": si, "seg_n": len(bounds),
            "area": meta.get("area", ""), "material": meta.get("material", ""),
            "category": meta.get("category", ""),
            "start": [round(sx, 1), round(sz, 1)],
            "waypoints": wps,
            "target": [wps[-1]["x"], wps[-1]["z"]],
            "span": span, "src": meta["rel"],
        })
    if not segs:
        skipped.append((name, "没有任何一段可用"))
    return segs, skipped


def in_window(r: dict, span_min: float = SPAN_MIN,
              span_max: float = SPAN_MAX) -> bool:
    return (WP_MIN <= len(r["waypoints"]) <= WP_MAX
            and span_min <= r["span"] <= span_max)


def route_shape(r: dict) -> dict:
    """量一条路线的形状：绕行比 / 均转角 / 间距变异系数。

    ★ 为什么必须有这个（2026-10-09 真机打脸）
    ---------------------------------------
    第一版抽样只按**跨度**挑（50~300m），抽出来的 10 条**一条导航型都没有** ——
    全是绕圈采集型。真机跑第一条就 panic，用户才看出问题。

    根因：BGI 的**采集路线天生是绕圈的**（去回同一个刷新点采一堆），
    路点间距小、转角大、绕行比高；而**导航型路线长、直、间距均匀**。
    只按跨度筛会系统性地把采集型抽出来、把导航型漏掉。

    全池实测（870 段）：绕行比中位 1.29、均转角中位 52.7°；
    符合「绕行比<1.15 且 均转角<10°」的只有 **49 段（5.6%）**。
    """
    sx, sz = r["start"]
    pts = [(sx, sz)] + [(w["x"], w["z"]) for w in r["waypoints"]]
    seg = [(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
           for i in range(len(pts) - 1)]
    ds = [math.hypot(*s) for s in seg]
    turns = []
    for i in range(1, len(seg)):
        a1 = math.atan2(seg[i - 1][1], seg[i - 1][0])
        a2 = math.atan2(seg[i][1], seg[i][0])
        turns.append(abs(math.degrees((a2 - a1 + math.pi) % (2 * math.pi)
                                      - math.pi)))
    path = sum(ds)
    detour = path / r["span"] if r["span"] > 0 else 99.0
    mu = path / len(ds) if ds else 0.0
    sd = math.sqrt(sum((d - mu) ** 2 for d in ds) / len(ds)) if ds else 0.0
    return {"detour": detour, "path": path,
            "mean_turn": sum(turns) / len(turns) if turns else 0.0,
            "turn_sum": sum(turns), "cv": (sd / mu) if mu > 0 else 99.0,
            "dmin": min(ds) if ds else 0.0, "dmax": max(ds) if ds else 0.0}


def is_nav_shape(s: dict, detour_max: float = 1.15,
                 turn_max: float = 10.0) -> bool:
    """导航型判据：绕行比小（几乎直线）+ 均转角小（走向不折返）。"""
    return s["detour"] < detour_max and s["mean_turn"] < turn_max


# ---------------------------------------------------------------- 命令
def cmd_list(args: argparse.Namespace) -> int:
    root = cache_root(Path(args.cache))
    print(f"  缓存目录：{root}")
    if not (root / "repo" / "pathing").is_dir():
        print(f"  ✗ 里面没有 {PATHING}/。")
        print(f"    先跑：git clone --depth 1 "
              f"https://github.com/babalae/bettergi-scripts-list.git "
              f"{DEFAULT_CACHE}")
        return 2
    total = 0
    for c in CATEGORIES:
        d = root / "repo" / "pathing" / c
        if not d.is_dir():
            continue
        n = len(list(d.rglob("*.json")))
        total += n
        print(f"    {c:<12} {n:>5} 条")
    print(f"    {'合计':<12} {total:>5} 条")
    for a in PREFERRED_AREAS:
        rs = walk_routes(Path(args.cache), area=a, category=args.category)
        print(f"    其中「{a}」: {len(rs)} 条")
    return 0


def cmd_sample(args: argparse.Namespace) -> int:
    areas = [args.area] if args.area else list(PREFERRED_AREAS)
    routes: list[dict] = []
    for a in areas:
        got = walk_routes(Path(args.cache), area=a,
                          category=args.category, mat_filter=args.material)
        print(f"  扫 {a}：{len(got)} 条")
        if args.verbose:
            for m in got:
                segs, sk = parse_route(m)
                args.skipped.extend(sk)
                for r in segs:
                    routes.append(r)
                    print(f"    · {r['name'][:42]:<42} "
                          f"{len(r['waypoints']):>2}点 {r['span']:6.1f}m")
        else:
            for m in got:
                segs, sk = parse_route(m)
                args.skipped.extend(sk)
                routes.extend(segs)

    ok = [r for r in routes if in_window(r, args.span_min, args.span_max)]
    for r in ok:
        r["shape"] = route_shape(r)
    # ★ 默认只要**导航型**（2026-10-09）。按跨度抽样会系统性抽到采集型 ——
    #   见 route_shape 的说明。加 --all-shapes 才把采集型也放进来。
    shaped = ok if args.all_shapes else [r for r in ok
                                         if is_nav_shape(r["shape"])]
    print()
    print(f"  切出可走段   : {len(routes)}")
    print(f"  落在跨度窗口 : {len(ok)}")
    print(f"  其中**导航型**: {len(shaped)}"
          f"（绕行比<{args.detour_max:g} 且 均转角<{args.turn_max:g}°）"
          f"{'   --all-shapes：不过滤形状' if args.all_shapes else ''}")
    if shaped:
        print(f"    导航型绕行比 中位 "
              f"{sorted(r['shape']['detour'] for r in shaped)[len(shaped) // 2]:.2f}"
              f" / 均转角 中位 "
              f"{sorted(r['shape']['mean_turn'] for r in shaped)[len(shaped) // 2]:.1f}°")
    print(f"    采集型（被滤掉）: {len(ok) - len(shaped)}")
    for reason, c in Counter(s for _, s in args.skipped).most_common(4):
        print(f"    跳过 {c:>4} 段 —— {reason}")

    # ★ 分层抽样：按跨度铺开取，而不是「按跨度排序取前 N」。
    #   上一版按 span 升序取前 10，抽出来的全是 50~54m —— 等于只测了
    #   窗口下沿，完全测不到 150m / 300m 时的转向与到达行为。
    pool = sorted(shaped or ok, key=lambda r: r["span"])
    picked: list[dict] = []
    if pool:
        k = min(args.sample, len(pool))
        for i in range(k):
            idx = (i * (len(pool) - 1)) // max(1, k - 1) if k > 1 else 0
            if pool[idx] not in picked:
                picked.append(pool[idx])
    # ★ 导航型不够数时**不静默拿采集型补齐** —— 那正是上一版翻车的根因。
    #   要补必须显式 --all-shapes，否则就少给几条，如实报数。
    if (len(picked) < args.sample and not args.all_shapes
            and len(shaped) < len(ok)):
        print(f"  ⚠ 只有 {len(shaped)} 段导航型，少于要的 {args.sample} 条。")
        print(f"    要凑数就加 --all-shapes（会混进采集型，就是这次的坑）。")
    if args.all_shapes:
        for r in sorted(ok, key=lambda r: r["span"]):   # 不够就用其他补齐
            if len(picked) >= args.sample:
                break
            if r not in picked:
                picked.append(r)
    print()
    print(f"  === 抽 {len(picked)} 条（按跨度 {SPAN_MIN:g}~{SPAN_MAX:g}m 分层铺开）===")
    for i, r in enumerate(picked, 1):
        print(f"  {i:>2}. {r['name'][:44]:<44} "
              f"{len(r['waypoints']):>2}点 跨度{r['span']:6.1f}m "
              f"绕行{r['shape']['detour']:.2f} 均转角{r['shape']['mean_turn']:5.1f}°  "
              f"起({r['start'][0]:8.1f},{r['start'][1]:8.1f}) "
              f"终({r['target'][0]:8.1f},{r['target'][1]:8.1f})")

    if args.dry:
        print("\n  (--dry：不写文件)")
        return 0
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n  写入 {out_dir}/")
    index = []
    for i, r in enumerate(picked, 1):
        fn = f"{i:02d}-{r['base_name'].replace('/', '_')}.json"
        p = out_dir / fn
        p.write_text(json.dumps({
            "schema_version": SCHEMA_VERSION,
            "created_at_ms": int(time.time() * 1000),
            "start": r["start"], "waypoints": r["waypoints"],
            "target": r["target"],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        index.append({"file": fn, "name": r["name"],
                      "base_name": r["base_name"],
                      "seg": f"{r['seg_i']}/{r['seg_n']}",
                      "area": r["area"], "material": r["material"],
                      "category": r["category"], "points": len(r["waypoints"]),
                      "span_m": round(r["span"], 1),
                      "detour": round(r["shape"]["detour"], 2),
                      "mean_turn_deg": round(r["shape"]["mean_turn"], 1),
                      "start": r["start"], "target": r["target"],
                      "src": r["src"]})
        print(f"    {fn}  ({len(r['waypoints'])} 点, {r['span']:.1f}m)")
    (out_dir / "_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    _index.json（{len(picked)} 条，含 BetterGI 原始路径与段号）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="BetterGI 路线 -> 我们的 user_waypoints schema（本地缓存）")
    ap.add_argument("--cache", default=str(DEFAULT_CACHE),
                    help=f"本地克隆目录（默认 {DEFAULT_CACHE}）")
    ap.add_argument("--list", action="store_true", help="只统计不解析")
    ap.add_argument("--category", default="",
                    help="限定分类（默认全扫：%s）" % "/".join(CATEGORIES))
    ap.add_argument("--area", default="", help="限定地区（默认蒙德+璃月）")
    ap.add_argument("--material", default="", help="限定材料名")
    ap.add_argument("--sample", type=int, default=10, help="抽几条")
    ap.add_argument("--span-max", type=float, default=SPAN_MAX,
                    help=f"跨度上限（米，默认 {SPAN_MAX:g}）")
    ap.add_argument("--span-min", type=float, default=SPAN_MIN,
                    help=f"跨度下限（米，默认 {SPAN_MIN:g}）")
    ap.add_argument("--out-dir", default=str(OUT_DIR),
                    help=f"输出目录（默认 {OUT_DIR}）")
    ap.add_argument("--all-shapes", action="store_true",
                    help="不过滤形状（采集型也放进来。默认只要导航型）")
    ap.add_argument("--detour-max", type=float, default=1.15,
                    help="导航型的绕行比上限（默认 1.15）")
    ap.add_argument("--turn-max", type=float, default=10.0,
                    help="导航型的均转角上限（度，默认 10）")
    ap.add_argument("--dry", action="store_true", help="只统计不写文件")
    ap.add_argument("--verbose", action="store_true", help="打印每段解析结果")
    args = ap.parse_args()
    args.skipped: list[tuple[str, str]] = []
    if args.list:
        return cmd_list(args)
    return cmd_sample(args)


if __name__ == "__main__":
    raise SystemExit(main())
