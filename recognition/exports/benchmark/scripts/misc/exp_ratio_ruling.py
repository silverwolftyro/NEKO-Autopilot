#!/usr/bin/env python
"""比例常数裁决实验（2026-10-08）—— 一次 A→B 直线跑，测出实测 K。

★ 要解决的问题
---------------
主文档 §16.4/§16.6 说「不做分辨率修正，用 K=0.42」，
而 ``recognition/configs/minimap_scale.toml`` 的主值是 **0.389**
（``resolution_corrected``，``has_conflict = true``）。
两者不能同时成立，必须有一次**绝对标定**来裁决。

★ 为什么这次测量**不是**循环论证
--------------------------------
:func:`cv2.phaseCorrelate` 返回的是**小地图像素**的帧间位移 ——
它只依赖图像，**完全不含 K**。K 是「世界单位 ↔ 小地图像素」的换算，
是另一层的量。所以：

    实测 K = |世界距离(A,B)| / |累积像素位移|

分子来自传送点真值（``_ref_tp_sample.json``，与 K 无关），
分母来自相位相关（也与 K 无关）。两边都不依赖待测的 K。

对比之下，§16.3 的「反解自洽区间」是把 K 当未知量塞进速度区间里解，
得到的 [0.3999, 0.4299] 只能证明**不矛盾**，不能证明**正确**。

★ 前置条件
----------
1. 站在锚点 A，按 ``--from`` 的名字（或直接给坐标）
2. 角色**朝向 B**，全程尽量走直线，别拐弯、别爬坡
3. 相位相关的核心假设是「小地图帧间只有平移」—— 缩放档全程别动

★ 用法
------
::

    # 1) 看看有哪些可用锚点（附近的）
    python exp_ratio_ruling.py --list 风起地

    # 2) 跑实验（站在 A，面朝 B）
    python exp_ratio_ruling.py --from "七天神像-风起地" --to "七天神像-蒙德城" --seconds 60

    # 3) 不想输名字，直接给坐标也行（名字只用来显示）
    python exp_ratio_ruling.py --from 1928.4,-739.9 --to 2566,-1323 --seconds 60
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
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:                                        # pragma: no cover
    pass

PLUGIN_ROOT = Path(PLUGIN_DIR)
sys.path.insert(0, str(PLUGIN_ROOT))

TP_JSON = Path(RECOG_ROOT / 'data' / '_ref_tp_sample.json')
SCALE_TOML = Path(RECOG_ROOT / 'configs' / 'minimap_scale.toml')
ROI_TOML = Path(RECOG_ROOT / 'configs' / 'rois.toml')

#: 待裁决的两个候选值。**不要在这里改** —— 它们是「被测对象」。
CAND_A, CAND_B = 0.389, 0.42


# ---------------------------------------------------------------- 传送点
def load_tp() -> list[dict]:
    """读传送点真值。返回可用锚点列表。"""
    doc = json.loads(TP_JSON.read_text(encoding="utf-8"))
    out = []
    for entry in doc.get("data", []):
        for p in entry.get("points", []):
            pos = p.get("position")
            if not (isinstance(pos, (list, tuple)) and len(pos) == 3):
                continue
            # 小地图世界坐标用 (x, z)，y 是高度 —— 方向判据只用 x/z
            out.append({
                "name": str(p.get("name", "")),
                "map": str(entry.get("mapName", "")),
                "country": str(p.get("country", "")),
                "areas": p.get("areas") or [],
                "x": float(pos[0]),
                "z": float(pos[2]),
                "y": float(pos[1]),
            })
    return out


def resolve(ref: str, anchors: list[dict]) -> tuple[float, float, str]:
    """把 ``--from/--to`` 解析成 (x, z, 显示名)。坐标直接透传。"""
    txt = ref.strip()
    if "," in txt:
        a, b = txt.split(",", 1)
        return float(a), float(b), f"({float(a):.1f}, {float(b):.1f})"
    hits = [a for a in anchors if txt in a["name"] or txt in a["country"]
            or any(txt in str(x) for x in a["areas"])]
    if not hits:
        raise SystemExit(
            f"✗ 锚点「{ref}」在 {TP_JSON.name} 里没找到。"
            f"先跑 --list {ref} 看看有哪些候选。")
    if len(hits) > 1:
        print(f"⚠ 「{ref}」匹配到 {len(hits)} 个，取第一个：")
        for h in hits[:8]:
            print(f"    {h['name']}  ({h['x']:.1f}, {h['z']:.1f})  "
                  f"[{h['map']}/{h['country']}]")
    h = hits[0]
    return h["x"], h["z"], h["name"]


def cmd_list(args: argparse.Namespace) -> int:
    anchors = load_tp()
    key = (args.list or "").strip()
    print(f"传送点总数：{len(anchors)}（来自 {TP_JSON}）\n")
    if key:
        hits = [a for a in anchors
                if key in a["name"] or key in a["country"]
                or any(key in str(x) for x in a["areas"])]
    else:
        hits = anchors
    for a in hits[:args.limit]:
        print(f"  {a['name']:<28} ({a['x']:9.1f}, {a['z']:9.1f})  "
              f"[{a['map']}/{a['country']}]")
    if len(hits) > args.limit:
        print(f"  …… 还有 {len(hits) - args.limit} 条（--limit 调大）")
    return 0


# ---------------------------------------------------------------- 比例标定
def read_current_k() -> tuple[float, str]:
    """读 ``minimap_scale.toml`` 现在的 K。**只读，不改**。"""
    try:
        import tomllib
        d = tomllib.loads(SCALE_TOML.read_text(encoding="utf-8"))
    except Exception as exc:                                # noqa: BLE001
        return (float("nan"), f"读不到 {SCALE_TOML.name}（{exc}）")
    scale = d.get("scale", {})
    k = scale.get("city_px_to_world")
    src = d.get("validation", {}).get("city_px_to_world_source", "?")
    return (float(k) if k is not None else float("nan"),
            f"{SCALE_TOML.name} 当前 city_px_to_world = {k}（来源 {src}）")


# ---------------------------------------------------------------- 主实验
def cmd_run(args: argparse.Namespace) -> int:
    import cv2
    import numpy as np
    from autopilot.perception.capturer import Capturer, CaptureChannel
    from autopilot.perception.roi_loader import load_rois

    anchors = load_tp()
    ax, az, aname = resolve(args.__dict__["from"], anchors)
    bx, bz, bname = resolve(args.to, anchors)
    world_d = math.hypot(bx - ax, bz - az)
    print("=" * 74)
    print("比例常数裁决实验")
    print("=" * 74)
    print(f"  起点 A: ({ax:.1f}, {az:.1f})  [{aname}]")
    print(f"  终点 B: ({bx:.1f}, {bz:.1f})  [{bname}]")
    print(f"  世界直线距离: {world_d:.1f} m")
    k_cur, k_desc = read_current_k()
    print(f"  {k_desc}")
    print()
    if world_d < 30.0:
        print(f"✗ A 到 B 只有 {world_d:.1f}m —— 短到测不出东西"
              f"（相位相关的 1px 级误差在这么短的基线上是致命的）。")
        print("  请挑两个相距 >= 100m、最好 300m 以上的锚点。")
        return 2
    if not args.live:
        print("⚠ 这是 dry-run 模式（没加 --live）：**不发任何按键**，")
        print("  你得自己手动走完 A→B。脚本只负责量。")
    print(f"  ★ 现在角色**面朝 B**，开始跑 {args.seconds:.0f}s。"
          f"中途别拐弯、别爬坡、别切缩放档。")
    if args.delay > 0:
        for t in range(int(args.delay), 0, -1):
            print(f"    剩余 {t}s")
            time.sleep(1.0)

    # ---- ROI：小地图圆形区域 ----------------------------------------
    rois = load_rois(ROI_TOML) if ROI_TOML.is_file() else {}
    rect = getattr(rois, "minimap_circle", None)
    print(f"\n  ROI: {rect}")

    cap = Capturer("原神", prefer=[CaptureChannel.DXCAM, CaptureChannel.MSS,
                                   CaptureChannel.PRINTWINDOW])
    cap.open()
    acc_px = 0.0
    prev = None
    t0 = time.monotonic()
    frames = 0
    drops = 0
    try:
        while time.monotonic() - t0 < args.seconds:
            fr = cap.grab()
            img = fr.image
            if rect is not None:
                x, y, w, h = (rect if isinstance(rect, (tuple, list))
                              else (rect.x, rect.y, rect.w, rect.h))
                img = img[y:y + h, x:x + w]
            g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
            if prev is not None:
                # ★ 返回的是**像素**位移，与 K 无关 —— 这是本实验不循环的关键
                (dx, dy), resp = cv2.phaseCorrelate(prev, g)
                mag = math.hypot(dx, dy)
                if resp > args.min_response:
                    acc_px += mag
                    frames += 1
                else:
                    drops += 1
            prev = g
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n  ⚠ Ctrl+C，提前结束。")
    finally:
        cap.close()

    el = time.monotonic() - t0
    print()
    print("=" * 74)
    print(f"  累积像素位移: {acc_px:.1f} px"
          f"（{frames} 帧有效 / {drops} 帧响应太低被剔除，用时 {el:.0f}s）")
    if acc_px < 10.0:
        print(f"✗ 累积位移只有 {acc_px:.1f}px —— 几乎没有有效样本。")
        print("  常见原因：① 没加 --live 且没真走动 ② 小地图没在屏幕上")
        print("  ③ 缩放档变过 ④ 窗口被遮挡")
        return 2
    k_meas = world_d / acc_px
    print(f"  实测 K = {world_d:.1f} / {acc_px:.1f} = **{k_meas:.4f}**")
    print()
    print("-" * 74)
    for name, k in (("0.389（minimap_scale.toml 主值）", CAND_A),
                    ("0.42 （主文档 §16.4/§16.6）", CAND_B)):
        diff = (k_meas - k) / k * 100.0
        mark = "支持" if abs(diff) <= 3.0 else "不支持"
        print(f"  与 {name:<32} 差 {abs(diff):5.1f}%  -> {mark}")
    print("-" * 74)
    da, db = abs(k_meas - CAND_A) / CAND_A, abs(k_meas - CAND_B) / CAND_B
    win = CAND_A if da < db else CAND_B
    print(f"  建议：以实测 K = {k_meas:.4f} 为准；")
    print(f"       与它更近的是 {win}"
          f"（{min(da, db) * 100:.1f}% vs {max(da, db) * 100:.1f}%）。")
    if min(da, db) > 3.0:
        print(f"  ⚠ 两个候选都不接近（最近的差 {min(da, db) * 100:.1f}%）——")
        print("    说明直线假设被破坏了（爬坡/拐弯/缩放档动过），")
        print("    **这次数据作废，别拿来裁决**。换个更平的路线重跑。")
    print()
    print("  ★ 本脚本**不修改** minimap_scale.toml，也不改主文档。")
    print("    要落地请人工把两个地方改成同一个值，并把另一个标为已废弃。")
    return 0


# ---------------------------------------------------------------- 入口
def main() -> int:
    ap = argparse.ArgumentParser(
        description="比例常数裁决实验：A→B 直线跑，实测 K",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", metavar="关键词",
                    help="列出匹配的传送点（不跑实验）")
    ap.add_argument("--limit", type=int, default=40, help="--list 最多显示几条")
    ap.add_argument("--from", dest="from_", metavar="A",
                    help="起点：锚点名或 'x,z'")
    ap.add_argument("--to", metavar="B", help="终点：锚点名或 'x,z'")
    ap.add_argument("--seconds", type=float, default=60.0, help="测量时长（秒）")
    ap.add_argument("--delay", type=float, default=5.0, help="启动前倒数秒")
    ap.add_argument("--interval", type=float, default=0.2, help="采样间隔（秒）")
    ap.add_argument("--min-response", type=float, default=0.05,
                    help="相位相关响应低于它就丢弃该样本")
    ap.add_argument("--live", action="store_true",
                    help="★ 脚本**不发按键**（本实验靠人走），"
                         "加它只是为了关掉脚本自己的提醒")
    args = ap.parse_args()
    args.__dict__["from"] = args.__dict__.pop("from_")

    if args.list:
        return cmd_list(args)
    if not args.__dict__.get("from") or not args.to:
        ap.print_help()
        print("\n提示：先用 --list <关键词> 找锚点，再跑 --from/--to。")
        return 2
    if not TP_JSON.is_file():
        print(f"✗ 找不到 {TP_JSON}")
        return 2
    return cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
