"""验证 C/D 收尾 · 误差量化 + 标定可行性判定。

**已确立的事实**
* A：相位相关有效，峰值中位 0.98 —— 小地图帧间是**纯平移，无旋转**
* 附加：log-polar 尺度比中位 **1.00267** —— 小地图**缩放档全程不变**，
  所以 "px -> m" 是一个常数，累加方案在原理上成立
* B：筛出 3 段走路

**C（标定）需要什么**
"1 px = 多少米"。session_a 没有传送 OCR 真值；
session_b 的传送命中之间隔着加载屏，位移链断裂，累加不可用。
-> **C 阻塞**。

**但误差量级可以先测（不依赖真值）**
把一段连续走路按时间对半切开，两半各自累加。
单向直走时两半方向一致、量级可比，**两者之差就是不可加性/漂移的上界**。
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
import math
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt      # noqa: E402

RECOG = Path(RECOG_ROOT)
CSV = RECOG / "exports" / "benchmark" / "datasets" / "phase_corr.csv"
WALK = RECOG / "exports" / "benchmark" / "datasets" / "walk_segments.csv"
VIS = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe"
DATASETS = RECOG / "exports" / "benchmark" / "datasets"
FPS = 10.0
JUMP_MAX = 3.0

rows = list(csv.DictReader(CSV.open(encoding="utf-8-sig", newline="")))
dx = np.array([float(r["dx_px"]) for r in rows])
dy = np.array([float(r["dy_px"]) for r in rows])
resp = np.array([float(r["peak_response"]) for r in rows])
std = np.array([float(r["circle_std"]) for r in rows])
world = np.array([r["is_world"] == "1" for r in rows])
idx = np.array([int(r["idx"]) for r in rows])
mag = np.hypot(dx, dy)
base = world & (resp >= 0.10) & (std >= 20.0) & (mag <= JUMP_MAX)
walks = list(csv.DictReader(WALK.open(encoding="utf-8-sig", newline="")))

print("=" * 92)
print("1. 误差上界：对半切分一致性")
print("=" * 92)
print("  单向直走时，把一段按时间对半切开，两半累加应当接近。")
print("  差值 / 半程 = 相对不可加性。\n")
half_err = []
for w in walks:
    a = int(w["frame_first"].split(":f")[1])
    b = int(w["frame_last"].split(":f")[1])
    ii = idx[(idx >= a) & (idx < b)]
    m = base[(idx >= a) & (idx < b)]
    cx, cy = np.cumsum(dx[(idx >= a) & (idx < b)] * m), \
        np.cumsum(dy[(idx >= a) & (idx < b)] * m)
    half = len(cx) // 2
    h1 = math.hypot(cx[half - 1], cy[half - 1])
    h2 = math.hypot(cx[-1] - cx[half - 1], cy[-1] - cy[half - 1])
    # 方向偏差
    a1 = math.degrees(math.atan2(cy[half - 1], cx[half - 1]))
    a2 = math.degrees(math.atan2(cy[-1] - cy[half - 1], cx[-1] - cx[half - 1]))
    dd = abs((a2 - a1 + 180) % 360 - 180)
    rel = abs(h1 - h2) / max((h1 + h2) / 2, 1e-9)
    path = float(mag[(idx >= a) & (idx < b)][m].sum())
    half_err.append(rel)
    print(f"  a:f{a:05d}-f{b:05d}  前半 {h1:6.2f} px / 后半 {h2:6.2f} px  "
          f"差 {abs(h1-h2):5.2f} px ({rel*100:4.1f}%)  "
          f"方向差 {dd:4.1f}deg  整段路径 {path:6.2f} px")
print(f"\n  >> 对半相对误差 中位 {np.median(half_err)*100:.1f}%  "
      f"最大 {max(half_err)*100:.1f}%")
print("  >> 注意：这含真实的速度变化（玩家非匀速），是**上界**。")
print("  >> 纯测量噪声应该比它小。")

print("\n" + "=" * 92)
print("2. 标定可行性（C）")
print("=" * 92)
print("  session_a: 无传送 OCR 真值，12 段 11 次切换里没有可用的")
print("             '已知起点 + 连续世界帧 + 已知终点' 组合。")
print("  session_b: 有 4 个传送点真值(909/906/841/1010)，但两两之间的")
print("             OCR 命中帧之间**全是非世界段**（加载屏），位移链断裂。")
print("  -> C 阻塞。\n")
print("  需要的新录制（最小要求）：")
print("    1) 在 A 传送点，执行一次传送，等世界完全恢复")
print("    2) 连续 20~40 秒**只走路不传送**（可以走直线）")
print("    3) 停在 B 传送点，执行一次传送并等世界恢复")
print("    4) 全程录屏不切场景\n")
print("  有了这段，比例 = |AB 的世界距离| / |累加位移|，")
print("  且 A/B 都在 tp.json 里，误差也可以直接算（= 验证 D）。")

print("\n" + "=" * 92)
print("3. 比例尺换算表（速度假设 -> m/px -> 覆盖范围）")
print("=" * 92)
print("  1 px = X 米 的换算，取决于小地图缩放档。用角色移速反推仅供参考：")
print("  已知小地图半径 130 px（pad=3 时 132.7）。\n")
R_NORM = 0.05630
RAD = R_NORM * 2304 + 3
print(f"  {'假设速度':<12}{'m/px':>9}{'半径覆盖m':>11}{'直径覆盖m':>11}"
      f"{'段1 90.14px':>14}{'段3 137.21px':>15}")
for label, v in (("走路 5.5 m/s", 5.5), ("走路 6.0 m/s", 6.0),
                 ("慢走 4.0 m/s", 4.0), ("冲刺 20 m/s", 20.0)):
    mpp = v / 11.28                      # 用段1 速度中位 11.28 px/s 反推
    print(f"  {label:<12}{mpp:>9.3f}{RAD*mpp:>11.0f}{2*RAD*mpp:>11.0f}"
          f"{90.14*mpp:>13.0f}m{137.21*mpp:>14.0f}m")
print("\n  ⚠️ 上表是**假设**驱动的，不是标定结果。")
print("     缩放档是玩家设置，移速受角色/地形/buff 影响。")
print("     真正的常数必须靠上面说的新录制实测。")

# ---------------------------------------------------------------- 出图
fig, ax = plt.subplots(1, 2, figsize=(16, 6.5))
# 左：三段累加轨迹
for k, w in enumerate(walks):
    a = int(w["frame_first"].split(":f")[1])
    b = int(w["frame_last"].split(":f")[1])
    m = (idx >= a) & (idx < b)
    cx, cy = np.cumsum(dx[m] * base[m]), np.cumsum(dy[m] * base[m])
    ax[0].plot(cx, cy, lw=1.8, label=f"a:f{a:05d}-f{b:05d}  "
                                     f"({float(w['path_px']):.0f} px)")
    ax[0].plot([0], [0], "o", ms=6)
    ax[0].plot([cx[-1]], [cy[-1]], "s", ms=6)
    half = len(cx) // 2
    ax[0].plot([cx[half-1]], [cy[half-1]], "^", ms=8, mfc="none", mew=2)
ax[0].set_xlabel("accumulated dx [px]")
ax[0].set_ylabel("accumulated dy [px]")
ax[0].set_title("Accumulated minimap trajectory per walk segment\n"
                "o=start  s=end  ^=half-way split", fontsize=11)
ax[0].legend(fontsize=8)
ax[0].grid(alpha=0.3)
ax[0].axis("equal")

# 右：段内速度分布
for w in walks:
    a = int(w["frame_first"].split(":f")[1])
    b = int(w["frame_last"].split(":f")[1])
    m = (idx >= a) & (idx < b)
    v = mag[m] * FPS
    v = v[v > 0.2]
    ax[1].hist(v, bins=28, alpha=0.55, histtype="step",
               label=f"f{a:05d}-f{b:05d}")
ax[1].set_xlabel("|v| [px/s]  (10 fps)")
ax[1].set_ylabel("frame count")
ax[1].set_title("Frame-to-frame speed inside walk segments\n"
                "spread = the player accelerates / dashes", fontsize=11)
ax[1].legend(fontsize=8)
ax[1].grid(alpha=0.3)
fig.tight_layout()
png = VIS / "walk_traj_speed.png"
fig.savefig(png, dpi=110)
print(f"\n  -> {png}")

# 补充 CSV
with (DATASETS / "walk_half_split.csv").open("w", encoding="utf-8-sig",
                                             newline="") as fh:
    w_ = csv.writer(fh)
    w_.writerow(["frame_first", "frame_last", "half1_px", "half2_px",
                 "abs_diff_px", "rel_diff_pct", "heading_diff_deg",
                 "full_path_px"])
    for w in walks:
        a = int(w["frame_first"].split(":f")[1])
        b = int(w["frame_last"].split(":f")[1])
        sel = (idx >= a) & (idx < b)
        m = base[sel]
        cx, cy = np.cumsum(dx[sel] * m), np.cumsum(dy[sel] * m)
        half = len(cx) // 2
        h1 = math.hypot(cx[half - 1], cy[half - 1])
        h2 = math.hypot(cx[-1] - cx[half - 1], cy[-1] - cy[half - 1])
        a1 = math.degrees(math.atan2(cy[half - 1], cx[half - 1]))
        a2 = math.degrees(math.atan2(cy[-1] - cy[half - 1],
                                      cx[-1] - cx[half - 1]))
        dd = abs((a2 - a1 + 180) % 360 - 180)
        w_.writerow([w["frame_first"], w["frame_last"],
                     round(h1, 2), round(h2, 2),
                     round(abs(h1 - h2), 2),
                     round(100 * abs(h1 - h2) / max((h1 + h2) / 2, 1e-9), 1),
                     round(dd, 2), w["path_px"]])
print(f"  -> {DATASETS/'walk_half_split.csv'}")
print("=== DONE ===")
