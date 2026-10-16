"""任务 A · 从用户截图抠出锚点模板，并在 f00081.jpg 小地图里交叉验证。

★ 截图内容（目视）
--------------------
地图图钉：深藏青马蹄拱 + 中心亮蓝菱形 + 白色针体，尖端朝下。
四周是浅绿地图底色残余（用户说"边缘有一点残余"）。

★ 抠图策略（颜色分割，不用"最暗轮廓"）
--------------------------------------
上一版自动检测框出 109x71 = 图标 + 大片残余背景，NCC 只有 0.28。
改成三通道并集：
  dark   : V<150            -> 马蹄拱 + 深色描边
  blue   : H 95-130, S>110  -> 中心菱形
  white  : S<45, V>205      -> 白色针体
背景是低饱和浅绿，三者都不满足。

★ 尺度问题
----------
截图里的图标约 85x115 px，**远大于小地图上任何图标**（青圆盘 22x22），
说明用户是**放大截的**。所以要在 f00081 里做**多尺度匹配**，
不能直接用原尺寸。
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
import math
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

PERC = (Path(PLUGIN_DIR.parent)
        / "genshin_game_buddy" / "autopilot" / "perception")
RECOG = Path(RECOG_ROOT)
SHOT = RECOG / "exports" / "benchmark" / "屏幕截图 2026-10-02 142123.png"
FRAME_B = RECOG / "raw" / "frames" / "batch_20260929b" / "f00081.jpg"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" \
    / "landmark2"
FIG.mkdir(parents=True, exist_ok=True)


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


mm = _load("minimap_mask", PERC / "minimap_mask.py")
cfg = mm.load_circle_cfg()

shot = cv2.imdecode(np.fromfile(str(SHOT), dtype=np.uint8), cv2.IMREAD_COLOR)
H, W = shot.shape[:2]
hsv = cv2.cvtColor(shot, cv2.COLOR_BGR2HSV)
Hh, Ss, Vv = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
dark = Vv < 150
blue = (Hh >= 95) & (Hh <= 130) & (Ss > 110)
white = (Ss < 45) & (Vv > 205)
icon = (dark | blue | white).astype(np.uint8)
icon = cv2.morphologyEx(icon, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
icon = cv2.morphologyEx(icon, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

n, lab, st, ct = cv2.connectedComponentsWithStats(icon, 8)
cand = [(st[j][0], st[j][1], st[j][2], st[j][3], st[j][4])
        for j in range(1, n) if st[j][4] >= 200]
cand.sort(key=lambda z: -z[4])
print(f"截图 {W}x{H}   图标连通域 {len(cand)} 个 (面积>=200)")
for c in cand[:6]:
    print(f"   x[{c[0]},{c[0]+c[2]}) y[{c[1]},{c[1]+c[3]}) 面积 {c[4]}")
if not cand:
    raise SystemExit("没找到图标连通域")
bx, by, bw, bh, ba = cand[0]
# 把同高处的横向连通域并进来（图钉可能被马蹄/菱形/针体切成几块）
near = [c for c in cand
        if c[1] < by + bh and c[1] + c[3] > by
        and c[0] < bx + bw and c[0] + c[2] > bx]
x0 = min(c[0] for c in near)
y0 = min(c[1] for c in near)
x1 = max(c[0] + c[2] for c in near)
y1 = max(c[1] + c[3] for c in near)
PAD = 2
tx0, ty0 = max(0, x0 - PAD), max(0, y0 - PAD)
tx1, ty1 = min(W, x1 + PAD), min(H, y1 + PAD)
TPL = shot[ty0:ty1, tx0:tx1].copy()
TPL_M = icon[ty0:ty1, tx0:tx1] * 255
print(f"\n合并 {len(near)} 块 -> 模板框 x[{tx0},{tx1}) y[{ty0},{ty1})"
      f"  = {TPL.shape[1]}x{TPL.shape[0]}")
print(f"  模板内图标像素占比 {TPL_M.mean()/255:.1%}  "
      f"（越接近 100% 说明残余越少）")

# ---- 出图：模板 + 掩码叠加
Z = 8
big = cv2.resize(TPL, (TPL.shape[1] * Z, TPL.shape[0] * Z),
                 interpolation=cv2.INTER_NEAREST)
for k in range(0, big.shape[1], 10 * Z):
    cv2.line(big, (k, 0), (k, big.shape[0]), (0, 255, 255), 1)
for k in range(0, big.shape[0], 10 * Z):
    cv2.line(big, (0, k), (big.shape[1], k), (0, 255, 255), 1)
ov = big.copy()
sel = cv2.resize(TPL_M, (big.shape[1], big.shape[0]),
                 interpolation=cv2.INTER_NEAREST) > 127
ov[sel] = (0, 0, 255)
cut = (ov * 0.35 + big * 0.65).astype(np.uint8)
_SIDE = 300
pad = np.zeros((cut.shape[0], _SIDE, 3), np.uint8)
txt = [f"template {TPL.shape[1]}x{TPL.shape[0]}",
       f"at ({tx0},{ty0}) in 216x246 shot",
       f"icon coverage {TPL_M.mean()/255:.0%}",
       "red = segmented icon mask",
       "cyan grid = 10px, x8 zoom"]
for i, s_ in enumerate(txt):
    cv2.putText(pad, s_, (8, 28 + i * 26), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                (0, 255, 255) if i == 0 else (200, 200, 200), 1)
row = np.hstack([big, np.zeros((cut.shape[0], 8, 3), np.uint8),
                 cut, np.zeros((cut.shape[0], 8, 3), np.uint8), pad])
bar = np.zeros((30, row.shape[1], 3), np.uint8)
cv2.putText(bar, "USER ANCHOR TEMPLATE (color-segmented, tight)", (8, 21),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
cv2.imwrite(str(FIG / "template_user_anchor.png"), np.vstack([bar, row]))
print(f"  -> template_user_anchor.png  {np.vstack([bar,row]).shape}")

# ---- 在 f00081.jpg 小地图里多尺度交叉验证
fb = cv2.imread(str(FRAME_B))
mini, mask = mm.make_minimap_roi(fb, cfg)
mx0, my0, _, _ = mm.minimap_rect(fb, cfg)
CCX = float(cfg["cx"]) * fb.shape[1] - mx0
CCY = float(cfg["cy"]) * fb.shape[0] - my0
mg = cv2.cvtColor(mini, cv2.COLOR_BGR2GRAY).astype(np.float32)
tplg = cv2.cvtColor(TPL, cv2.COLOR_BGR2GRAY).astype(np.float32)
print(f"\nf00081 小地图 {mini.shape[1]}x{mini.shape[0]}  圆心 ({CCX:.1f},{CCY:.1f})")
print("多尺度 NCC（scale 指模板被缩到小地图上的比例）:")
best = None
for s in (1.0, 0.8, 0.6, 0.5, 0.4, 0.32, 0.26, 0.2, 0.16, 0.12, 0.09):
    ts = cv2.resize(tplg, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    if ts.shape[0] < 8 or ts.shape[1] < 8 or \
            ts.shape[0] > mini.shape[0] or ts.shape[1] > mini.shape[1]:
        continue
    res = cv2.matchTemplate(mg, ts, cv2.TM_CCOEFF_NORMED)
    rh, rw = res.shape
    v = np.where(mask[:rh, :rw] > 0, res, -1.0)
    k = int(np.argmax(v))
    yy, xx = divmod(k, rw)
    px, py = xx + ts.shape[1] // 2, yy + ts.shape[0] // 2
    rr = math.hypot(px - CCX, py - CCY)
    print(f"   scale {s:4.2f}  模板 {ts.shape[1]:>3}x{ts.shape[0]:<3} "
          f"best ncc={v[yy,xx]:6.3f}  @({px:>3},{py:>3}) 距圆心 {rr:5.1f}px")
    if best is None or v[yy, xx] > best[0]:
        best = (float(v[yy, xx]), s, px, py, rr, ts.shape[1], ts.shape[0])
print(f"\n  最佳: ncc={best[0]:.3f} @ scale {best[1]:.2f} "
      f"({best[2]},{best[3]}) 距圆心 {best[4]:.1f}px  "
      f"模板 {best[5]}x{best[6]}")
if best[4] < 25:
    print("  !! 最佳命中距圆心 <25px —— **那可能就是玩家自己，不是锚点**")

# ---- 出图：f00081 小地图 + 命中框
vis = cv2.resize(mini, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
if best[0] > 0.3:
    hw, hh = best[5], best[6]
    cv2.rectangle(vis, ((best[2] - hw // 2) * 3, (best[3] - hh // 2) * 3),
                  ((best[2] + hw // 2) * 3, (best[3] + hh // 2) * 3),
                  (0, 0, 255), 2)
    cv2.putText(vis, f"{best[0]:.2f} s={best[1]:.2f}",
                ((best[2] - hw // 2) * 3, (best[3] - hh // 2) * 3 - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
cv2.circle(vis, (int(CCX * 3), int(CCY * 3)), 14, (255, 0, 255), 3)
cv2.putText(vis, "player", (int(CCX * 3) + 18, int(CCY * 3) + 6),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 2)
hdr = np.zeros((30, vis.shape[1], 3), np.uint8)
cv2.putText(hdr, "batch_20260929b/f00081.jpg minimap | red = user anchor "
                 "template best hit | magenta = player",
            (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1)
cv2.imwrite(str(FIG / "verify_anchor_in_f00081.png"), np.vstack([hdr, vis]))
print(f"  -> verify_anchor_in_f00081.png")
print("=== DONE ===")
