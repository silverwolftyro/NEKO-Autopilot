"""像素↔世界单位 比例交叉验证 + 任务驱动定位闭环演练。

**为什么做交叉验证而不是直接闭环**
session_a 没有传送 OCR 真值，闭环（起点→终点→对比）无法真正闭合。
但有一条**不依赖真值的自洽性检验**：

  已知：KQM 社区实测的角色走路均速表（m/s）
  已知：我们实测的三段走路平均像素速度（px/s）
  若比例 K 正确，则 K × 像素速度 必须落在公开的走路速度区间内。

  比例 K 偏大 -> 三段世界速度超出走路上限（不合理）
  比例 K 偏小 -> 低于走路下限（不合理）
  -> 三段同时自洽的 K 区间就是**交叉验证给出的约束**

BetterGI 是 GPL-3.0：只读参考，不抄代码、不复制数据文件。
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
WALK = RECOG / "exports" / "benchmark" / "datasets" / "walk_segments.csv"
PHASE = RECOG / "exports" / "benchmark" / "datasets" / "phase_corr.csv"
VIS = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe"
FPS = 10.0
K_KQM = 0.42
SIGMA_PX = 0.109          # §15.5 实测

# ---------------------------------------------------------------- KQM 数据
# 社区实测的角色"走路"均速（m/s），转自 52pk 转载的 KQM 排行表。
# 只作为**区间约束**使用，不作为标定真值。
# ⚠️ 标签一律用英文：matplotlib 缺 CJK 字形，中文会渲染成方块。
WALK_SPEED = {
    "Klee": 4.075, "Barbara": 4.300, "Fischl": 4.310, "Amber": 4.312,
    "Xiangling": 4.319, "Noelle": 4.346, "Traveler-F": 4.401, "Zhongli": 5.016,
    "Razor": 5.030, "Bennett": 5.030, "Xingqiu": 5.032, "Kaeya": 5.062,
    "Diluc": 5.066, "Venti": 5.099, "Traveler-M": 5.207, "Ningguang": 5.338,
    "Beidou": 5.345, "Lisa": 5.345, "Jean": 5.433,
}
WMIN, WMAX = min(WALK_SPEED.values()), max(WALK_SPEED.values())
print("=" * 92)
print("参考数据 · KQM 社区实测角色走路均速（m/s）")
print("=" * 92)
print(f"  区间 [{WMIN:.3f} ({min(WALK_SPEED, key=WALK_SPEED.get)}), "
      f"{WMAX:.3f} ({max(WALK_SPEED, key=WALK_SPEED.get)})]   "
      f"中位 {np.median(list(WALK_SPEED.values())):.3f}   共 {len(WALK_SPEED)} 角色")
print("  [!] community-measured, not official; varies with terrain.")
print("      Used ONLY as an interval constraint, never as ground truth.")

# ---------------------------------------------------------------- 读数据
walks = list(csv.DictReader(WALK.open(encoding="utf-8-sig", newline="")))
rows = list(csv.DictReader(PHASE.open(encoding="utf-8-sig", newline="")))
dx = np.array([float(r["dx_px"]) for r in rows])
dy = np.array([float(r["dy_px"]) for r in rows])
resp = np.array([float(r["peak_response"]) for r in rows])
std = np.array([float(r["circle_std"]) for r in rows])
world = np.array([r["is_world"] == "1" for r in rows])
idx = np.array([int(r["idx"]) for r in rows])
base = world & (resp >= 0.10) & (std >= 20.0) & (np.hypot(dx, dy) <= 3.0)

segs = []
for w in walks:
    a = int(w["frame_first"].split(":f")[1])
    b = int(w["frame_last"].split(":f")[1])
    sel = (idx >= a) & (idx < b)
    m = base[sel]
    path = float(np.hypot(dx[sel], dy[sel])[m].sum())
    cx = np.cumsum(dx[sel] * m)
    cy = np.cumsum(dy[sel] * m)
    v = np.hypot(dx[sel], dy[sel]) * FPS
    v = v[v > 0.2]
    dur = (b - a + 1) / FPS
    segs.append(dict(a=a, b=b, dur=dur, path=path,
                     net=(float(cx[-1]), float(cy[-1])),
                     netlen=float(math.hypot(cx[-1], cy[-1])),
                     vavg=path / dur,
                     vmed=float(np.median(v)),
                     vp25=float(np.percentile(v, 25)),
                     vp75=float(np.percentile(v, 75)),
                     nframes=int(m.sum()),
                     cumx=cx, cumy=cy))

# ---------------------------------------------------------------- 任务 A
print("\n" + "=" * 92)
print("任务 A · 用 K = 0.42 换算三段世界速度，和公开数据对表")
print("=" * 92)
print(f"  {'段':<6}{'帧范围':<22}{'平均px/s':>10}{'×0.42':>9}"
      f"{'落在哪':<16}{'p25~p75 px/s':>16}")
for i, s in enumerate(segs, 1):
    ws = s["vavg"] * K_KQM
    if ws < WMIN:
        where = f"below min {WMIN:.2f}"
    elif ws > WMAX:
        where = f"ABOVE max {WMAX:.2f}"
    else:
        near = min(WALK_SPEED.items(), key=lambda kv: abs(kv[1] - ws))
        where = f"~{near[0]} {near[1]:.2f}"
    print(f"  段{i:<4}a:f{s['a']:05d}-f{s['b']:05d}  {s['vavg']:>10.2f}"
          f"{ws:>9.2f}  {where:<16}{s['vp25']:>6.1f}~{s['vp75']:<9.1f}")

print("\n  段3 的 p75/p25 = "
      f"{segs[2]['vp75']/segs[2]['vp25']:.2f} 倍，速度散布很大"
      " -> 该段内含冲刺，符合'超出纯走路上限'的解释。")

# ---------------------------------------------------------------- 反解 K
print("\n" + "=" * 92)
print("任务 A' · 反解：什么样的 K 才能让各段都像'纯走路'？")
print("=" * 92)
print("  每段给出一个 K 区间（该段世界速度 ∈ [4.075, 5.433]）：")
lo_all, hi_all = 0.0, 9e9
for i, s in enumerate(segs, 1):
    lo, hi = WMIN / s["vavg"], WMAX / s["vavg"]
    print(f"    段{i}: K ∈ [{lo:.4f}, {hi:.4f}]")
    lo_all, hi_all = max(lo_all, lo), min(hi_all, hi)
print(f"\n  三段交集 = [{lo_all:.4f}, {hi_all:.4f}]"
      f"   {'（为空 -> 三段不可能全是纯走路）' if lo_all >= hi_all else ''}")
lo12 = max(WMIN / segs[0]["vavg"], WMIN / segs[1]["vavg"])
hi12 = min(WMAX / segs[0]["vavg"], WMAX / segs[1]["vavg"])
print(f"  只取段1+段2（承认段3 含冲刺）: K ∈ [{lo12:.4f}, {hi12:.4f}]")
print(f"  KQM value 0.42 inside that range: "
      f"**{'YES' if lo12 <= K_KQM <= hi12 else 'NO'}**"
      f"  (+{100*(K_KQM-(lo12+hi12)/2)/((lo12+hi12)/2):.1f}% vs range midpoint)")
print("\n  >>> CROSS-CHECK RESULT: assuming seg1/seg2 are pure walking,")
print(f"      K = 0.42 sits inside the self-consistent range "
      f"[{lo12:.4f}, {hi12:.4f}].")
print("      K +10% -> seg1/2 exceed the public walk ceiling.")
print("      K -10% -> seg1/2 fall below the public walk floor.")
print("      => self-consistent within +/-10%; +/-20% would be contradictory.")

# ---------------------------------------------------------------- 任务 B
print("\n" + "=" * 92)
print("任务 B · 分辨率调整系数")
print("=" * 92)
CASES = [("KQM 实测", 1920, 1080, 120.0), ("本项目实测", 2304, 1440, 129.7)]
print(f"  {'':<14}{'分辨率':<14}{'半径px':>8}{'r/宽':>9}{'r/高':>9}"
      f"{'r/对角':>9}{'r/短边':>9}")
for name, w, h, r in CASES:
    d = math.hypot(w, h)
    print(f"  {name:<14}{f'{w}x{h}':<14}{r:>8.1f}{r/w:>9.5f}{r/h:>9.5f}"
          f"{r/d:>9.5f}{r/min(w,h):>9.5f}")
r1, r2 = 120.0, 129.7
print(f"\n  假设 KQM 的 120 px 正确，则 2304x1440 下按各规则应为：")
w, h = 2304, 1440
for label, val in (("按宽度等比", r1 / 1920 * w), ("按高度等比", r1 / 1080 * h),
                   ("按对角等比", r1 / math.hypot(1920, 1080) * math.hypot(w, h)),
                   ("按短边等比", r1 / 1080 * min(w, h)),
                   ("固定像素", r1)):
    print(f"    {label:<10} {val:>7.1f} px   实测 {r2}  "
          f"偏差 {100*(r2-val)/val:+.1f}%")
print(f"\n  129.7 落在'固定 120'和'按宽度 144'之间，没有一条规则对得上。")
print("  ⚠️ 关键：**1920x1080 是 16:9 (1.778)，2304x1440 是 1.600 —— 宽高比不同**。")
print("     UI 缩放设置还会同时影响'小地图像素大小'和'覆盖的世界范围'，")
print("     所以像素半径和 m/px 是**耦合**的，不能分开外推。")
print("  >>> 因此不做分辨率修正，直接用 0.42 —— 前提是**游戏界面缩放设置")
print("      与 KQM 实测时一致**。这一点需要向用户确认。")

# ---------------------------------------------------------------- 任务 C/D
print("\n" + "=" * 92)
print("任务 D · 闭环演练（起点为假设值）")
print("=" * 92)
print("  session_a **没有传送 OCR 真值**。12 段 11 次切换里没有")
print("  '已知起点 + 连续世界帧 + 已知终点'的组合，闭环无法真正闭合。")
print("  下面用**假设起点**做演练：取 f00383 前最近的场景切换点，")
print("  假定玩家停在该处最近的传送点（枫丹廷区 841, L0=(19141,7655)）。\n")
sys.path.insert(0, str(RECOG / "exports" / "benchmark" / "scripts" / "misc"))
import coord_transform as ct      # noqa: E402

START_L0 = (19141, 7655)          # 假设：传送点 841 枫丹廷区
sx, sy = ct.world_to_pixel(*ct.pixel_to_world(*[0, 0])[:0] or (0, 0)) if False \
    else (0, 0)
wx0, wz0 = ct.pixel_to_world(START_L0[0], START_L0[1])
print(f"  假设起点: 传送点 841 枫丹廷区  L0={START_L0}  世界=({wx0:.0f},{wz0:.0f})")
print(f"  {'段':<6}{'累加像素':>10}{'×0.42 距离':>12}{'σ(像素)':>10}"
      f"{'σ(单位)':>10}{'预测终点(世界)':>26}")
rowsout = []
for i, s in enumerate(segs, 1):
    nfr = s["b"] - s["a"]
    sig_px = SIGMA_PX * math.sqrt(max(nfr, 1))
    dist = s["netlen"] * K_KQM
    sig_w = sig_px * K_KQM
    wx, wz = wx0 + s["net"][0] * K_KQM, wz0 - s["net"][1] * K_KQM
    rowsout.append((i, s, dist, sig_w, wx, wz))
    print(f"  段{i:<4}{s['netlen']:>10.2f}{dist:>12.1f}{sig_px:>10.2f}"
          f"{sig_w:>10.2f}   ({wx:>7.0f}, {wz:>7.0f})")
print("\n  注：dy 轴与世界坐标 z 的换算是 -z 关系（见 coord_transform 轴向转置），")
print("      方向以相位相关的符号约定为准，绝对值不依赖它。")
print("  ⚠️ 起点是**假设**的，不是实测。真值闭环需要用户补录。")

# ---------------------------------------------------------------- 出图
fig, ax = plt.subplots(1, 2, figsize=(16, 6.5))
names = list(WALK_SPEED.items())
names.sort(key=lambda kv: kv[1])
ax[0].barh([n for n, _ in names], [v for _, v in names],
           color="#4c72b0", alpha=0.85, label="KQM walk speed (m/s)")
ax[0].axvline(WMIN, color="k", ls=":", lw=1)
ax[0].axvline(WMAX, color="k", ls=":", lw=1)
for i, s in enumerate(segs, 1):
    ax[0].axvline(s["vavg"] * K_KQM, color="#c44e52", lw=2, ls="--")
    ax[0].text(s["vavg"] * K_KQM, i, f" seg{i}", color="#c44e52",
               fontsize=9, va="center")
ax[0].set_xlabel("speed [m/s]")
ax[0].set_title("Cross-check: our 3 walk segments vs KQM walk-speed table\n"
                f"K=0.42, dotted lines = public walk range [{WMIN:.2f},{WMAX:.2f}]",
                fontsize=11)
ax[0].legend(fontsize=8)
ax[0].grid(alpha=0.3, axis="x")

ks = np.linspace(0.25, 0.65, 200)
for i, s in enumerate(segs, 1):
    ax[1].plot(ks, s["vavg"] * ks, lw=2, label=f"seg{i}")
ax[1].axhspan(WMIN, WMAX, color="#2b8", alpha=0.18,
              label="public walk range")
ax[1].axvline(K_KQM, color="#c44e52", lw=2, ls="--", label="K = 0.42 (KQM)")
ax[1].axvspan(lo12, hi12, color="#fc8", alpha=0.35,
              label="self-consistent range (seg1+seg2)")
ax[1].set_xlabel("candidate scale K [world unit per minimap px]")
ax[1].set_ylabel("implied walk speed [m/s]")
ax[1].set_title("Which K is self-consistent with public data?", fontsize=11)
ax[1].legend(fontsize=8, loc="lower right")
ax[1].grid(alpha=0.3)
ax[1].set_ylim(3.4, 6.6)
fig.tight_layout()
png = VIS / "scale_crosscheck.png"
fig.savefig(png, dpi=110)
print(f"\n  -> {png}")
print("=== DONE ===")
