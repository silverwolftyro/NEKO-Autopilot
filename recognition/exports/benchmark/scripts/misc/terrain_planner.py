#!/usr/bin/env python
"""地形规划器：用可通行概率图做 A*（2026-10-08）—— 阶段 D。

★ 定位：**先独立验证，不接 e2e**
--------------------------------
这一版**不碰** ``e2e_pathfind.py``。目的只有一个：在已知起终点上，
A* 能不能绕开山/水/湖，出一条**人能一眼看出合理**的路线。
做不到就回去改模型，别急着接进主循环。

★ 输入 / 输出
--------------
    输入  起点/终点世界坐标 + ``terrain_passable.png``（阶段 C 产出）
    输出  3~10 个路点（世界坐标）+ ``%TEMP%\\terrain_route.png``

★ 代价函数
----------
    cost = 1 + LAMBDA × (1 - passable_prob)

不是「只看能不能走」而是「越难走越贵」—— 直线穿过一条窄路可能比绕远一点
更划算。``--lambda`` 调大 = 更保守（更愿意绕）。

★ 与 K 常数的关系（重要）
--------------------------
世界坐标 -> 底图像素用的是 :func:`coord_transform.world_to_pixel`，
那是**锚点标定出来的仿射矩阵**，与 ``minimap_scale.toml`` 的 K **无关**。
所以本规划器的结论不受 §50 那场 0.389 / 0.42 裁决影响。

用法::

    # 先看看可通行图长什么样
    python terrain_planner.py --show

    # 规划一条路
    python terrain_planner.py --start 1928.4,-739.9 --end 2566.1,-1323.5

    # 调保守程度 + 画到指定文件
    python terrain_planner.py --start X,Z --end X,Z --lambda 12 --out route.png
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
import heapq
import math
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:                                     # pragma: no cover
    pass

PASSABLE_PNG = Path(RECOG_ROOT / 'data' / 'terrain_passable.png')
BASE_MAP = Path(RECOG_ROOT / 'data' / 'mihoyo_maps' / 'teyvat_P0.png')
INFER_SCALE = 8          # 必须与 train_terrain.py 的 INFER_SCALE 一致
DEFAULT_LAMBDA = 6.0
#: 代价 >= 这个值 = 墙，**直接跳过**（不是「很贵」，见 astar 里的说明）。
BLOCK_COST = 1e5


def hr(t: str) -> None:
    print("\n" + "=" * 74 + f"\n  {t}\n" + "=" * 74)


def load_passable() -> np.ndarray:
    if not PASSABLE_PNG.is_file():
        raise SystemExit(
            f"✗ 没有 {PASSABLE_PNG}\n"
            f"  先跑：python train_terrain.py --infer-only")
    a = np.array(Image.open(PASSABLE_PNG).convert("L"))
    print(f"  可通行图 {a.shape[1]}x{a.shape[0]}  "
          f"均值 {a.mean():.0f}  可通行(>127)占比 {(a > 127).mean() * 100:.1f}%")
    return a


def w2p(x: float, z: float) -> tuple[float, float]:
    from coord_transform import world_to_pixel
    return world_to_pixel(float(x), float(z))


# ---------------------------------------------------------------- A*
def astar(cost: np.ndarray, start: tuple[int, int], goal: tuple[int, int],
          stride: int = 4) -> list[tuple[int, int]] | None:
    """8 邻域 A*。返回**原始像素**坐标路径（含起终点）。

    ★ 为什么加 stride
      可通行图 4608x2304 = 1000 万格，纯 Python A* 太慢。按 stride 抽稀
      到约 1/16 再搜，最后把路径投回原分辨率。stride 越大越快、
      但窄通道可能被跳过 —— 卡墙了就把它调小。
    """
    H, W = cost.shape
    ch, cw = max(1, H // stride), max(1, W // stride)
    small = cost[::stride, ::stride]
    sh, sw = small.shape

    def to_s(px, py):
        return (min(sh - 1, max(0, py // stride)),
                min(sw - 1, max(0, px // stride)))

    s = to_s(*start)
    g = to_s(*goal)
    if not (0 <= s[0] < sh and 0 <= s[1] < sw):
        raise SystemExit(f"✗ 起点落在图外：{start} -> 抽稀 {s}（图 {sh}x{sw}）")
    if not (0 <= g[0] < sh and 0 <= g[1] < sw):
        raise SystemExit(f"✗ 终点落在图外：{goal} -> 抽稀 {g}（图 {sh}x{sw}）")

    NB = [(-1, -1, 1.414), (-1, 0, 1.0), (-1, 1, 1.414),
          (0, -1, 1.0), (0, 1, 1.0),
          (1, -1, 1.414), (1, 0, 1.0), (1, 1, 1.414)]

    def h(a, b):
        dy, dx = abs(a[0] - g[0]), abs(a[1] - g[1])
        return (dx + dy) + (1.414 - 2) * min(dx, dy)      # 八方向启发式

    openq = [(h(s, g), 0.0, s)]
    came: dict = {}
    gsc = {s: 0.0}
    closed = set()
    pop = 0
    while openq:
        _, cg, cur = heapq.heappop(openq)
        if cur in closed:
            continue
        closed.add(cur)
        pop += 1
        if cur == g:
            break
        if pop > 400000:
            print("  ⚠ 展开节点超 40 万，放弃（试试 --stride 更大）")
            return None
        cy, cx = cur
        for dy, dx, w in NB:
            ny, nx = cy + dy, cx + dx
            if not (0 <= ny < sh and 0 <= nx < sw):
                continue
            _c = float(small[ny, nx])
            # ★★★ 硬阻断必须是**跳过**，不能只是「代价很大」
            #   之前用 1e6 当墙，结果 1e6 × 430 格 = 4.3e8 的路径**照样能走**
            #   —— A* 只要存在有限代价的路径就会找到它。全黑图实测仍规划出
            #   一条 216 节点的直线，就是这么来的。有墙就得真的过不去。
            if _c >= BLOCK_COST:
                continue
            ng = cg + _c * w
            if ng < gsc.get((ny, nx), 1e18):
                gsc[(ny, nx)] = ng
                came[(ny, nx)] = cur
                heapq.heappush(openq, (ng + h((ny, nx), g), ng, (ny, nx)))

    if g not in came and g != s:
        return None
    path = [g]
    while path[-1] != s:
        path.append(came.get(path[-1]))
        if path[-1] is None:
            return None
    path.reverse()
    # 投回原分辨率
    return [(int(x * stride), int(y * stride)) for y, x in path]


def thin(path: list[tuple[int, int]], n: int) -> list[tuple[int, int]]:
    """把长路径抽成 <=n 个路点（首尾必留）。"""
    if len(path) <= n:
        return path
    idx = np.linspace(0, len(path) - 1, n).round().astype(int)
    return [path[i] for i in sorted(set(idx.tolist()))]


# ---------------------------------------------------------------- 出图
def draw(route_px, route_w, start_w, end_w) -> Path:
    tmp = Path(os.environ.get("TEMP", "."))
    out = Path(args_out) if args_out else tmp / "terrain_route.png"
    p = load_passable()
    pw, ph = p.shape[1], p.shape[0]

    panels = []
    if BASE_MAP.is_file():
        try:
            b = Image.open(BASE_MAP).convert("RGB").resize((pw, ph),
                                                          Image.BILINEAR)
            panels.append(("底图", b))
        except Exception as exc:                            # noqa: BLE001
            print(f"  ⚠ 底图加载失败（{exc}），只画概率图")
    panels.append(("可通行概率", Image.fromarray(p).convert("RGB")))

    H = max(im.height for _, im in panels)
    W = sum(im.width for _, im in panels) + 8 * (len(panels) - 1) + 260
    canvas = Image.new("RGB", (W, H + 22), (30, 30, 46))
    x = 0
    for name, im in panels:
        canvas.paste(im, (x, 22))
        x += im.width + 8
    d = ImageDraw.Draw(canvas)

    # 路线按各面板底图的缩放比映射
    for name, im in panels:
        kx = im.width / pw
        ky = im.height / ph
        ox = panels[0][1].width + 8 if name == "可通行概率" else 0
        pts = [(ox + rx * kx, 22 + ry * ky) for rx, ry in route_px]
        if len(pts) > 1:
            d.line(pts, fill=(255, 235, 120), width=3)
        for i, (rx, ry) in enumerate(route_px):
            px, py = ox + rx * kx, 22 + ry * ky
            d.ellipse([px - 4, py - 4, px + 4, py + 4], fill=(120, 255, 160))
            d.text((px + 6, py - 6), str(i + 1), fill=(255, 255, 255))
        sx, sy = ox + start_px[0] * kx, 22 + start_px[1] * ky
        gx, gy = ox + goal_px[0] * kx, 22 + goal_px[1] * ky
        d.ellipse([sx - 8, sy - 8, sx + 8, sy + 8], outline=(90, 220, 255), width=3)
        d.ellipse([gx - 8, gy - 8, gx + 8, gy + 8], outline=(255, 120, 120),
                  width=3)

    for name, im in panels:
        d.text((4, 5), name, fill=(205, 214, 244))

    iw = 260
    tx = W - iw + 6
    d.text((tx, 30), f"起点 {start_w[0]:.0f}, {start_w[1]:.0f}",
           fill=(160, 230, 255))
    d.text((tx, 48), f"终点 {end_w[0]:.0f}, {end_w[1]:.0f}",
           fill=(255, 170, 170))
    d.text((tx, 66), f"路点 {len(route_w)} 个", fill=(235, 235, 245))
    if route_px:
        seg = sum(math.hypot(route_px[i + 1][0] - route_px[i][0],
                             route_px[i + 1][1] - route_px[i][1])
                  for i in range(len(route_px) - 1))
        d.text((tx, 84), f"像素路径长 {seg:.0f}px", fill=(200, 200, 215))
        d.text((tx, 102), f"底图像素 (1:{INFER_SCALE} 缩放)",
               fill=(160, 160, 180))
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    print(f"  ✓ {out}")
    return out


args_out = None
start_px = goal_px = (0, 0)


def cmd_show() -> int:
    p = load_passable()
    tmp = Path(os.environ.get("TEMP", "."))
    o = tmp / "terrain_passable_preview.png"
    im = Image.fromarray(p).convert("RGB")
    im.thumbnail((1400, 1400), Image.BILINEAR)
    im.save(o)
    print(f"  ✓ {o}  {im.width}x{im.height}")
    print("  白色 = 模型认为可通行。请肉眼扫一遍：")
    print("    1) 山体内部应该是黑的")
    print("    2) 湖泊/海面应该是黑的")
    print("    3) 城镇/道路网应该是白的")
    print("  不满足就别急着跑规划器，先回去多标几张。")
    return 0


def main() -> int:
    global args_out, start_px, goal_px
    ap = argparse.ArgumentParser(description="地形 A* 规划器（阶段 D）")
    ap.add_argument("--start", metavar="X,Z")
    ap.add_argument("--end", metavar="X,Z")
    ap.add_argument("--lambda", type=float, default=DEFAULT_LAMBDA,
                    dest="lam",
                    help="越大越保守（越愿意绕）")
    ap.add_argument("--min-passable", type=float, default=0.15,
                    help="低于此可通行概率 = 硬阻断（不是「很贵」，是走不了）")
    ap.add_argument("--start-slack", type=int, default=6,
                    help="起终点豁免半径（缩放图上的像素）")
    ap.add_argument("--stride", type=int, default=4, help="A* 抽稀步长")
    ap.add_argument("--max-waypoints", type=int, default=10)
    ap.add_argument("--out", help="输出图路径（默认 %%TEMP%%\\terrain_route.png）")
    ap.add_argument("--show", action="store_true", help="只看可通行图")
    a = ap.parse_args()
    args_out = a.out

    if a.show:
        return cmd_show()
    if not a.start or not a.end:
        ap.print_help()
        return 2

    def parse(v, tag):
        try:
            x, z = v.split(",")
            return float(x), float(z)
        except Exception:                                   # noqa: BLE001
            raise SystemExit(f"✗ {tag} 格式应为 'X,Z'，收到 {v!r}")

    sw_, sz_ = parse(a.start, "--start")
    ew, ez = parse(a.end, "--end")

    p = load_passable()
    hr("规划")
    start_px = w2p(sw_, sz_)
    goal_px = w2p(ew, ez)
    print(f"  起点 底图像素 ({start_px[0]:.0f}, {start_px[1]:.0f})")
    print(f"  终点 底图像素 ({goal_px[0]:.0f}, {goal_px[1]:.0f})")

    prob = p.astype(np.float32) / 255.0
    cost = 1.0 + a.lam * (1.0 - prob)
    # ★★ 硬阻断：标注语义里「不可通行」= 0，不是「很贵」。
    #   只用代价的话，λ=6 时穿一道 40 格宽的山（6.7/格 = 268）比绕路
    #   （340 格 × 1.0 = 340）**更便宜**，A* 就会理直气壮地穿山而过 ——
    #   实测就是这样。规划器不能产出穿山的路线，所以低于阈值的直接封死。
    hard = prob < a.min_passable
    cost[hard] = 1e6
    # ★ 起终点豁免：人站在那儿可能就是块「判不出来」的像素
    #   （阴影/水面/图标遮挡）。封死它们会让规划直接失败，而用户的判断
    #   显然比他更可信。半径 --start-slack 像素内都放行。
    cost[0, :] = cost[-1, :] = 1e6                    # 图像外圈封死
    cost[:, 0] = cost[:, -1] = 1e6
    _slack = max(1, a.start_slack)
    for _cx, _cy in ((start_px[0] // INFER_SCALE, start_px[1] // INFER_SCALE),
                     (goal_px[0] // INFER_SCALE, goal_px[1] // INFER_SCALE)):
        _y0 = max(0, _cy - _slack)
        _x0 = max(0, _cx - _slack)
        cost[_y0:_cy + _slack + 1, _x0:_cx + _slack + 1] = 1.0
    n_hard = int(hard.sum())
    print(f"  硬阻断（可通行 < {a.min_passable:.2f}）: "
          f"{n_hard / hard.size * 100:.1f}% 的像素")
    if n_hard / hard.size > 0.85:
        print("  ⚠ 超过 85% 被判不可通行 —— 这不像一张真实的地形图。")
        print("    先跑 train_terrain.py --infer-only 看图，确认模型没学歪。")

    route_px = astar(cost, start_px, goal_px, a.stride)
    if not route_px:
        print("✗ A* 没找到路。可能是：")
        print("  · 起终点在不可通行区（模型判死了）")
        print("  · stride 太大把窄通道跳过去了（试试 --stride 2）")
        print("  · --lambda 太大 -> 改小试试")
        return 2

    route_px = thin(route_px, a.max_waypoints)
    from coord_transform import pixel_to_world
    route_w = [pixel_to_world(x, y) for x, y in route_px]

    print()
    print(f"  ★ 路线 {len(route_w)} 个路点（世界坐标）：")
    for i, (x, z) in enumerate(route_w, 1):
        print(f"     {i}. ({x:9.1f}, {z:9.1f})")

    print()
    draw(route_px, route_w, (sw_, sz_), (ew, ez))
    print()
    print("  ★ 现在去打开那张图，肉眼看：")
    print("    路线有没有绕开山/水？有没有贴着图边缘乱走？")
    print("    不合理就调 --lambda（大了更保守）再试。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
