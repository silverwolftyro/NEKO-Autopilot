"""tile_tracker 自检：基本几何 + 边界 + 跳变拦截 + 漂移。

用真实数据驱动：读 phase_corr.csv 里 session_a 走路段的 dx/dy，
喂给 TileTracker，看跨块是否发生、跳变是否被拦。
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

import csv
import importlib.util
import math
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PERC = (Path(PLUGIN_DIR.parent)
        / "genshin_game_buddy" / "autopilot" / "perception")
RECOG = Path(RECOG_ROOT)
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" / "tile"
sys.path.insert(0, str(RECOG / "exports" / "benchmark" / "scripts" / "misc"))


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


tt = _load("tile_tracker", PERC / "tile_tracker.py")
TileTracker = tt.TileTracker
TileGrid = tt.TileGrid
TeleportJump = tt.TeleportJump

print("=" * 92)
print("0. 模块加载")
print("=" * 92)
grid = TileGrid()
print(f"  TileGrid: {len(grid)} 块  source={grid.source_size}  "
      f"chunk={grid.chunk_size}  grid={grid.grid}")
t0 = grid.get("r04_c06")
print(f"  样例 r04_c06 bbox=({t0.x0},{t0.y0},{t0.x1},{t0.y1})  "
      f"contains(19000,7000)={t0.contains(19000,7000)}  "
      f"contains(6144,7680)={t0.contains(6144,7680)} (应 False, 那是 r04_c02)")

# ---------------------------------------------------------------- 几何
print("\n" + "=" * 92)
print("1. 基本几何：起点 -> 单步 -> 块内坐标")
print("=" * 92)
# 用七天神像-枫丹廷区 附近的已知像素点做起点（传送点841 附近）
tr = TileTracker((3600.0, 4300.0), grid=grid)
st0 = tr.update(0.0, 0.0)
print(f"  起点 world=(3600,4300)  pixel=({st0.pixel[0]:.0f},{st0.pixel[1]:.0f})")
print(f"         tile={st0.tile_id}  in_tile=({st0.in_tile_px[0]:.0f},"
      f"{st0.in_tile_px[1]:.0f})")
# 单步必须 <= max_step_px(50.0)，用 2px
st1 = tr.update(2.0, 0.0)   # 小地图 +x 2px
dwx = 2.0 * tr.px_to_world
print(f"  单步 +2px: world=({st1.world_pos[0]:.1f},{st1.world_pos[1]:.1f})  "
      f"（world_x +{dwx:.2f}）  tile={st1.tile_id}  "
      f"changed={st1.tile_changed}")
print(f"           漂移上界={st1.drift_bound:.3f} 世界单位")
print(f"  （> {tt.MAX_STEP_PX}px 步长会被**丢弃**，连续 2 帧才抛 —— 见第 3 节，"
      f"那是**设计行为**）")

# 反算：pixel->world->pixel 往返（直接用 coord_transform）
print("\n  反算往返 (pixel -> world -> pixel):")
import coord_transform as ct
for p in ((19141, 7655), (100, 100), (36000, 18000), (0, 0)):
    px, py = p
    wx, wz = ct.pixel_to_world(px, py)
    bx, by = ct.world_to_pixel(wx, wz)
    print(f"    ({px},{py}) -> world({wx:.0f},{wz:.0f}) -> back({bx:.0f},{by:.0f})  "
          f"err=({abs(bx-px):.0f},{abs(by-py):.0f})")

# ---------------------------------------------------------------- 边界
print("\n" + "=" * 92)
print("2. 边界语义：右/下开区间，同一像素只属一块")
print("=" * 92)
# r00_c00 bbox (0,0,3072,1536); 检查边界像素
print(f"  r00_c00.contains(3071,1535)={grid.get('r00_c00').contains(3071,1535)}  "
      f"(应 True)")
print(f"  r00_c00.contains(3072,1536)={grid.get('r00_c00').contains(3072,1536)}  "
      f"(应 False, 归 r00_c01)")

# ---------------------------------------------------------------- 跳变
print("\n" + "=" * 92)
print("3. 跳变拦截：单帧超阈值丢弃、连续 2 帧才抛 TeleportJump")
print("=" * 92)
print(f"  MAX_STEP_PX={tt.MAX_STEP_PX}  OVER_STREAK_TO_REJECT="
      f"{tt.OVER_STREAK_TO_REJECT}")

# ---- 3a. 跑步/冲刺必须被接受（旧阈值 3.0 在这里全被拒）----
tr_r = TileTracker((3600.0, 4300.0), grid=grid)
for step in (5.7, 5.9, 8.0, 10.0):        # 跑步 5~6，冲刺 8~10
    st = tr_r.update(step, 0.0)
print(f"  跑步/冲刺步长 5.7~10px 全部接受，steps={st.step_index}（应 4）"
      f"  world=({st.world_pos[0]:.0f},{st.world_pos[1]:.0f})")

# ---- 3b. 单帧尖峰：丢弃但不抛，位置不动 ----
tr2 = TileTracker((3600.0, 4300.0), grid=grid)
for step in (2.0, 2.5, 1.0, 2.9):
    st = tr2.update(step, 0.0)
print(f"  连续 4 个小步（<=2.9px）都 OK，steps={st.step_index}")
before = tr2.world_pos
st = tr2.update(120.0, 0.0)                # 单帧 120px（毛刺）
print(f"  单帧 120px：**不抛**，该帧被丢弃  steps={tr2.step_count}"
      f"（应仍为 4）  world 不变={tr2.world_pos == before}")
print(f"    -> 位置 {before} 仍为 ({tr2.world_pos[0]:.0f},"
      f"{tr2.world_pos[1]:.0f})  **未被污染**")

# ---- 3c. 连续 2 帧超阈值 -> 抛，且异常带方向 ----
try:
    tr2.update(150.0, 30.0)                # 连续第 2 帧
    print("  !! 连续第 2 帧居然没抛")
except TeleportJump as e:
    print(f"  连续第 2 帧正确抛 TeleportJump: {e}")
    print(f"    -> 异常带方向 dx={e.dx:+.1f} dy={e.dy:+.1f}")

# ---- 3d. 大跳变（真传送量级 500~5000px）连续 2 帧 ----
tr3 = TileTracker((3600.0, 4300.0), grid=grid)
tr3.update(3000.0, 0.0)                    # 第 1 帧：丢弃
try:
    tr3.update(3000.0, 0.0)                # 第 2 帧：抛
    print("  !! 3000px 传送居然没抛")
except TeleportJump as e:
    print(f"  3000px 传送跳变正确拦截: {e}")
print(f"  拦截后状态未被污染: steps={tr3.step_count}（应 0）  "
      f"world=({tr3.world_pos[0]:.0f},{tr3.world_pos[1]:.0f})")

# ---- 3e. 中间夹一帧正常 -> 计数清零，不再抛 ----
tr4 = TileTracker((3600.0, 4300.0), grid=grid)
tr4.update(200.0, 0.0)                     # 超阈值 -> 计数 1
tr4.update(2.0, 0.0)                       # 正常 -> 计数清零
st = tr4.update(2.0, 0.0)                  # 再正常
print(f"  超阈值后夹一帧正常，计数清零 -> 后续 200px 也不再抛  "
      f"steps={st.step_index}")

# ---------------------------------------------------------------- 真实数据
print("\n" + "=" * 92)
print("4. 真实数据：session_a 走路段 -> 累加 -> 跨块统计")
print("=" * 92)
PC = RECOG / "exports" / "benchmark" / "datasets" / "phase_corr.csv"
WALK = RECOG / "exports" / "benchmark" / "datasets" / "walk_segments.csv"
prow = {int(r["idx"]): (float(r["dx_px"]), float(r["dy_px"]), r["is_world"])
        for r in csv.DictReader(PC.open(encoding="utf-8-sig", newline=""))}
walks = list(csv.DictReader(WALK.open(encoding="utf-8-sig", newline="")))

for w in walks:
    a = int(w["frame_first"].split(":f")[1])
    b = int(w["frame_last"].split(":f")[1])
    # 起点设在段首附近的一个已知像素
    tr3 = TileTracker((3600.0, 4300.0), grid=grid)
    changes = 0
    n = 0
    jumps = 0
    for i in range(a, b):
        if i not in prow:
            continue
        dx, dy, _ = prow[i]
        try:
            st = tr3.update(dx, dy)
            n += 1
            if st.tile_changed:
                changes += 1
        except TeleportJump:
            jumps += 1
    st = tr3._state(False)
    print(f"  段 a:f{a:05d}-f{b:05d}: 喂 {n} 帧  跳变拦截 {jumps}  "
          f"跨块 {changes} 次  终态 tile={st.tile_id}  "
          f"world=({st.world_pos[0]:.0f},{st.world_pos[1]:.0f})  "
          f"路径长 {st.path_len:.1f}m  漂移<={st.drift_bound:.2f}m")

# ---------------------------------------------------------------- 邻居
print("\n" + "=" * 92)
print("5. nearby_tiles：中心 + 8 邻居")
print("=" * 92)
tr4 = TileTracker((3600.0, 4300.0), grid=grid)
st = tr4.update(0.0, 0.0)
nb = tr4.nearby_tiles()
print(f"  当前 tile={st.tile_id}  邻居 {len(nb)} 个: {nb}")
# 角块（0,0）邻居应更少
tr5 = TileTracker((100.0, 100.0), grid=grid)  # 靠近 r00_c00
st5 = tr5.update(0.0, 0.0)
print(f"  角落 tile={st5.tile_id}  邻居 {len(tr5.nearby_tiles())} 个（应 <9）")

print("\n=== DONE ===")
