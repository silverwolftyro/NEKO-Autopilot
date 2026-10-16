"""验证 B/C/D · 走路段筛选 + 无真值自洽性检验。

**B**：从 phase_corr.csv 筛"纯走路段"
**C**：像素位移 -> 世界位移 的标定（能不能标？）
**D**：累积误差（条件式，依赖 C）

**关键：C 的标定不需要外部真值 —— 用"往返自洽性"。**
如果玩家走出去再走回来，相位相关累加的净位移应该回到 0。
残差就是纯漂移，直接给出精度上界。这比"和某个真值比"更严格，
因为它**同时**检验了比例尺和随机误差。
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
VIS = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe"
FPS = 10.0

# 判据阈值（全部来自 A 的实测分布，不是我拍脑袋填的）
JUMP_MAX = 3.0        # A 的 p95=2.01, max=124.79 -> 3 px 以上是事件跳变
RESP_MIN = 0.10       # 任务书指定
STD_MIN = 20.0        # A 的 p25=39.1, min=0.69 -> 20 以下圆内太脏
WIN = 20              # 2 秒窗口
COS_MIN = math.cos(math.radians(30))

rows = []
with CSV.open(encoding="utf-8-sig", newline="") as fh:
    for r in csv.DictReader(fh):
        rows.append(r)
N = len(rows)
idx = np.array([int(r["idx"]) for r in rows])
dx = np.array([float(r["dx_px"]) for r in rows])
dy = np.array([float(r["dy_px"]) for r in rows])
resp = np.array([float(r["peak_response"]) for r in rows])
std = np.array([float(r["circle_std"]) for r in rows])
world = np.array([r["is_world"] == "1" for r in rows])
t = np.array([float(r["t_sec"]) for r in rows])
mag = np.hypot(dx, dy)
ang = np.degrees(np.arctan2(dy, dx))

print("=" * 92)
print("B · 走路段筛选")
print("=" * 92)
base = world & (resp >= RESP_MIN) & (std >= STD_MIN) & (mag <= JUMP_MAX)
print(f"  基础过滤: 世界 & resp>={RESP_MIN} & std>={STD_MIN} & |d|<={JUMP_MAX}")
print(f"    -> {base.sum()} / {N} 帧 ({100*base.sum()/N:.1f}%)")
print(f"    被 |d| 过滤掉(事件跳变): {int((world & (mag > JUMP_MAX)).sum())} 帧")
print(f"    被 std  过滤掉(圆内太脏): {int((world & (std < STD_MIN)).sum())} 帧")

# 滑动窗口方向一致性
ok = np.zeros(N, bool)
coswin = np.full(N, -1.0)
for i in range(N - WIN + 1):
    m = base[i:i + WIN]
    if m.sum() < WIN * 0.6:          # 窗口内至少 60% 帧是有效位移
        continue
    vx = dx[i:i + WIN][m].sum()
    vy = dy[i:i + WIN][m].sum()
    norm = math.hypot(vx, vy)
    if norm < 0.5:                   # 净位移太小 = 只是站着不动
        continue
    # 窗口内每个有效向量与净方向的夹角
    c = (dx[i:i + WIN][m] * vx + dy[i:i + WIN][m] * vy) / (
        np.maximum(mag[i:i + WIN][m], 1e-9) * norm)
    cmean = float(np.mean(c))
    coswin[i] = cmean
    if cmean >= COS_MIN:
        ok[i:i + WIN] = True

# 合并成段
runs = []
i = 0
while i < N:
    if ok[i]:
        j = i
        while j + 1 < N and ok[j + 1]:
            j += 1
        runs.append([i, j])
        i = j + 1
    else:
        i += 1
print(f"\n  连续 {WIN} 帧方向一致(夹角<30deg) -> 种子 {ok.sum()} 帧")
print(f"  合并后走路段: {len(runs)} 段\n")

segs_out = []
for a, b in runs:
    m = base[a:b + 1]
    n = int(m.sum())
    path = float(mag[a:b + 1][m].sum())
    netx = float(dx[a:b + 1][m].sum())
    nety = float(dy[a:b + 1][m].sum())
    net = math.hypot(netx, nety)
    dur = (b - a + 1) / FPS
    segs_out.append(dict(a=int(idx[a]), b=int(idx[b + 1]), n=n, dur=dur,
                         path=path, net=net, netx=netx, nety=nety,
                         mean_cos=float(np.mean(coswin[a:b + 1][
                             coswin[a:b + 1] > 0])) if (
                             coswin[a:b + 1] > 0).any() else float("nan"),
                         resp=float(np.median(resp[a:b + 1][m]))))
    print(f"  a:f{idx[a]:05d}-f{idx[b+1]:05d}  t={t[a]:6.1f}-{t[b+1]:6.1f}s  "
          f"{dur:5.1f}s  有效帧 {n:>3}/{b-a+1:<3}  "
          f"路径长 {path:6.2f} px  净位移 {net:6.2f} px  "
          f"({net/dur if dur else 0:5.2f} px/s)  "
          f"方向一致性 {segs_out[-1]['mean_cos']:.3f}  "
          f"resp中位 {segs_out[-1]['resp']:.3f}")

with (RECOG / "exports" / "benchmark" / "datasets"
     / "walk_segments.csv").open("w", encoding="utf-8-sig",
                                 newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["frame_first", "frame_last", "t_start_s", "t_end_s",
                "duration_s", "valid_frames", "path_px", "net_px",
                "net_dx_px", "net_dy_px", "dir_cosine", "resp_median"])
    for s in segs_out:
        w.writerow([f"a:f{s['a']:05d}", f"a:f{s['b']:05d}",
                    round((s['a'] - 1) / FPS, 1), round((s['b'] - 1) / FPS, 1),
                    round(s["dur"], 1), s["n"], round(s["path"], 2),
                    round(s["net"], 2), round(s["netx"], 2),
                    round(s["nety"], 2), round(s["mean_cos"], 3),
                    round(s["resp"], 3)])
print("\n  -> datasets/walk_segments.csv")

# ---------------------------------------------------------------- C/D
print("\n" + "=" * 92)
print("C/D · 无真值标定：往返自洽性")
print("=" * 92)
if not segs_out:
    print("  没有走路段，C/D 阻塞")
else:
    print("  思路: 对每段，找到使'前半段净位移'与'后半段净位移'最接近反向的时刻。")
    print("        两者之差 = 该段的闭合误差，纯漂移，不依赖任何外部真值。\n")
    clos = []
    for s in segs_out:
        a, b = s["a"], s["b"]
        ii = np.where((idx >= a) & (idx < b))[0]
        m = base[ii]
        cumx = np.cumsum(dx[ii] * m)
        cumy = np.cumsum(dy[ii] * m)
        totx, toty = cumx[-1], cumy[-1]
        # 闭合误差最小的切分点
        errs = np.hypot(cumx - totx / 2, cumy - toty / 2)
        k = int(np.argmin(errs))
        c1 = np.hypot(cumx[k], cumy[k])
        c2 = math.hypot(totx - cumx[k], toty - cumy[k])
        close = float(math.hypot(totx, toty))   # 整段净位移（应该≈0）
        path = s["path"]
        clos.append((s, close, path, errs[k]))
        print(f"  a:f{a:05d}-f{b:05d}  路径 {path:7.2f} px   "
              f"净位移 {close:7.2f} px   "
              f"回环率 {close/max(path,1e-9)*100:6.2f}%   "
              f"最佳切分处两半之差 {errs[k]:6.2f} px")
    if clos:
        worst = max(clos, key=lambda z: z[1] / max(z[2], 1e-9))
        print(f"\n  >> 最差回环率 {worst[1]/max(worst[2],1e-9)*100:.2f}% "
              f"(a:f{worst[0]['a']:05d}-f{worst[0]['b']:05d})")
        print("  >> 若要靠累加走 100 m 精度的路，这段就已经漂 "
              f"{worst[1]/max(worst[2],1e-9)*100:.0f}% —— "
              "换算成米见下一步")

# ---------------------------------------------------------------- 出图
fig, ax = plt.subplots(3, 1, figsize=(17, 10), sharex=True)
for a, b in [(lo, hi) for lo, hi, _, _ in
             [(s["a"], s["b"], 0, 0) for s in segs_out]]:
    for k in range(3):
        ax[k].axvspan((a - 1) / FPS, (b - 1) / FPS,
                      color="#fa0", alpha=0.30, zorder=0)
ax[0].plot(t, dx, lw=0.6, color="#1f77b4")
ax[0].set_ylabel("dx [px]")
ax[0].set_title("Walk-segment detection on minimap phase correlation\n"
                "yellow = detected walk segments", fontsize=12)
ax[1].plot(t, dy, lw=0.6, color="#d62728")
ax[1].set_ylabel("dy [px]")
ax[2].plot(t, mag, lw=0.6, color="#2ca02c")
ax[2].axhline(JUMP_MAX, color="k", ls="--", lw=0.9,
              label=f"jump cutoff {JUMP_MAX} px")
ax[2].set_ylabel("|d| [px]")
ax[2].set_xlabel("time [s]   (frame index / 10 fps)")
ax[2].legend(loc="upper right", fontsize=8)
fig.tight_layout()
png = VIS / "walk_segments.png"
fig.savefig(png, dpi=110)
print(f"\n  -> {png}")
print("=== DONE ===")
