"""跨块切换的定向验证。

**为什么要单独测**
真实走路段路径只有 38~56 m，一块瓦片 3072 m，**跨块 0 次** ——
等于"跨块切换"这条逻辑压根没被触发过。
本测试从**紧贴块边缘**的起点出发，喂足够多的合法小步，逼它真的跨过去。

    起点 world(3600,4300) -> pixel(19160,7647) -> r04_c06
    r04_c06 bbox=(18432,6144,21504,7680)，in_tile y=1503，距下边缘 33 px
    2.9px/步 x 0.389 = 1.13 世界单位/步 -> ~29 步跨过下边缘 -> r05_c06
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

import importlib.util
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PERC = (Path(PLUGIN_DIR.parent)
        / "genshin_game_buddy" / "autopilot" / "perception")


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


tt = _load("tile_tracker", PERC / "tile_tracker.py")
TileTracker = tt.TileTracker

print("=" * 92)
print("跨块切换定向验证（向下走，逼过 r04_c06 的下边缘）")
print("=" * 92)
tr = TileTracker((3600.0, 4300.0))
st = tr.update(0.0, 0.0)
print(f"  起点: tile={st.tile_id}  in_tile=({st.in_tile_px[0]:.0f},"
      f"{st.in_tile_px[1]:.0f})  块高=1536  距下边缘 "
      f"{1536-st.in_tile_px[1]:.0f}px")

STEP = 2.9
crossed = 0
prev_tile = st.tile_id
for i in range(1, 60):
    try:
        st = tr.update(0.0, STEP)          # 沿 +y（小地图向下）走
    except tt.TeleportJump:
        print(f"  !! 第 {i} 步被跳变拦截（不该发生，step={STEP}"
              f"<{tt.MAX_STEP_PX}）")
        break
    if st.tile_changed:
        crossed += 1
        print(f"  第 {i:>2} 步 **跨块**: {prev_tile} -> {st.tile_id}  "
              f"in_tile=({st.in_tile_px[0]:.0f},{st.in_tile_px[1]:.0f})  "
              f"world=({st.world_pos[0]:.0f},{st.world_pos[1]:.0f})  "
              f"漂移<={st.drift_bound:.2f}m")
        prev_tile = st.tile_id
    if crossed >= 2:
        break

print(f"\n  总跨块 {crossed} 次  终态 tile={st.tile_id}  "
      f"steps={st.step_index}  路径长 {st.path_len:.1f}m  "
      f"漂移上界 {st.drift_bound:.2f}m")

print("\n" + "=" * 92)
print("反向验证：往回走，应该跨回 r04_c06")
print("=" * 92)
for i in range(1, 60):
    st = tr.update(0.0, -STEP)
    if st.tile_changed:
        print(f"  第 {i:>2} 步跨块 -> {st.tile_id}  "
              f"in_tile=({st.in_tile_px[0]:.0f},{st.in_tile_px[1]:.0f})")
        break
else:
    print("  未跨块（步数不够？）")

print("\n" + "=" * 92)
print("neighbors 在块边缘的返回")
print("=" * 92)
nb = tr.nearby_tiles()
print(f"  当前 {st.tile_id}  邻居 {len(nb)}: {nb}")
assert st.tile_id in nb, "neighbors 必须包含自身"
print("  ✓ neighbors 包含自身")

print("\n" + "=" * 92)
print("teleport_to 重置")
print("=" * 92)
before = (st.step_index, st.world_pos, st.path_len)
st2 = tr.teleport_to((150.0, 300.0))     # 换到完全不同的起点
print(f"  重置前 steps={before[0]} world={before[1]} path={before[2]:.1f}m")
print(f"  重置后 steps={st2.step_index} world={st2.world_pos} "
      f"tile={st2.tile_id} path={st2.path_len:.1f}m 漂移={st2.drift_bound:.2f}m")
assert st2.step_index == 0 and st2.path_len == 0.0 and st2.drift_bound == 0.0
print("  ✓ 重置后累积量全部清零")
print("=== DONE ===")
