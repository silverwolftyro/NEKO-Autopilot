"""确定小地图 +x/+y 到世界 x/z 的映射（解决转置符号）。

★ 核心问题
----------
底图相对世界坐标是**转置**的（coord_transform 文档明写：
px ~ world_z, py ~ world_x）。而 TileTracker 一开始假设
"小地图 +x -> world_x, 小地图 +y -> world_z"（非转置），
所以走 +y 60 步却在同一块 r04_c06 里不动 —— 走的是块内另一个轴。

本脚本用两点差分算 world->pixel 的雅可比，确定转置关系。
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

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MISC = Path(RECOG_ROOT / 'exports' / 'benchmark' / 'scripts' / 'misc')
sys.path.insert(0, str(MISC))
import coord_transform as ct   # noqa: E402

print("=" * 92)
print("1. 底图雅可比：world->pixel 一阶偏导（两点差分，不手抄系数）")
print("=" * 92)
w0 = (3600.0, 4300.0)
p0 = ct.world_to_pixel(*w0)
p1 = ct.world_to_pixel(w0[0] + 10.0, w0[1])      # +world_x
p2 = ct.world_to_pixel(w0[0], w0[1] + 10.0)      # +world_z
dpx_dwx = (p1[0] - p0[0]) / 10.0
dpy_dwx = (p1[1] - p0[1]) / 10.0
dpx_dwz = (p2[0] - p0[0]) / 10.0
dpy_dwz = (p2[1] - p0[1]) / 10.0
print(f"  +world_x(10) -> dpx={dpx_dwx:+.4f}  dpy={dpy_dwx:+.4f}")
print(f"  +world_z(10) -> dpx={dpx_dwz:+.4f}  dpy={dpy_dwz:+.4f}")
print()
if abs(dpx_dwz) > abs(dpx_dwx):
    print(f"  => world_z 主要改 **px**（{dpx_dwz:+.4f}）—— 底图 x 轴 = 世界 z 轴")
    print(f"     底图 +px 方向 = {'+' if dpx_dwz>0 else '-'}world_z")
    cross = True
else:
    print("  => world_x 主要改 px（非转置？）")
    cross = False
if abs(dpy_dwx) > abs(dpy_dwz):
    print(f"  => world_x 主要改 **py**（{dpy_dwx:+.4f}）—— 底图 y 轴 = 世界 x 轴")
else:
    print(f"  => world_z 主要改 py")

print("\n" + "=" * 92)
print("2. 转置关系确定")
print("=" * 92)
print(f"  底图 +px = {'+' if dpx_dwz>0 else '-'}world_z  (系数 {dpx_dwz:+.4f})")
print(f"  底图 +py = {'+' if dpy_dwx>0 else '-'}world_x  (系数 {dpy_dwx:+.4f})")
print()
if cross:
    print("  底图的 x/y 两轴 = 世界 z/x 两轴，**交叉**（转置）。")
    print()
    print("  小地图正北朝上 => 小地图 +x（屏幕右）= 底图 +px，"
          "小地图 +y（屏幕下）= 底图 +py")
    print("  于是：")
    print(f"     小地图 +x -> {'' if dpx_dwz>0 else '-'}world_z")
    print(f"     小地图 +y -> {'' if dpy_dwx>0 else '-'}world_x")
    print()
    print("  >>> TileTracker 的 dwx=dx, dwz=dy **忽略了转置，是错的**。")
    print("  >>> 正确：dwx = -dy*scale, dwz = -dx*scale"
          f"   (本例中 dpx_dwz<0, dpy_dwx<0)")
else:
    print("  非转置，dwx=dx, dwz=dy 成立。")

print("\n" + "=" * 92)
print("3. 方向符号最终表")
print("=" * 92)
if cross:
    dwx_sign = -1 if dpy_dwx < 0 else 1
    dwz_sign = -1 if dpx_dwz < 0 else 1
    print(f"  小地图 dx_px=+1 (右1px) -> world ({dwx_sign*0:+d}, {dwz_sign*0:+d})"
          f"  即 world_z {dwx_sign:+d}")
    print(f"  小地图 dy_px=+1 (下1px) -> world_x {dwx_sign:+d}")
    print()
    print("  update() 应为：")
    print(f"     dwx = {dwx_sign:+d} * dy_px * scale")
    print(f"     dwz = {dwz_sign:+d} * dx_px * scale")
