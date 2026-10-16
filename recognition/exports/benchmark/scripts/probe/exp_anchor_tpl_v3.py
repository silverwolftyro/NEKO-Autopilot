"""任务 A（定稿）· 自匹配精确定位 + 干净模板 + f00081 验证。

★ 为什么不用颜色分割
--------------------
截图背景是**浅绿**（低饱和、高亮），白针体是**中性白**。
"S<45 且 V>205"把两者都判成白色 -> 掩码覆盖 75%，等于没分。

★ 自匹配定位法（不猜）
----------------------
1. 用整张截图作模板，在 f00081 上多尺度搜 -> 得到最佳缩放比 s
2. 把截图自己缩到 s，在缩小版里用同样的模板滑窗匹配**自己**
   -> 峰值位置就是图标在缩小版里的位置
3. 乘 1/s 换算回原图坐标 -> 图标的精确中心和尺寸
这样图标边界是**数据说了算**，不是我目测。
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

PERC = (Path(_REPO_ROOT.parent / 'N.E.K-O-main' / 'N.E.K.O-main' / 'plugin' / 'plugins')
        if False else
        Path(PLUGIN_DIR.parent)
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
SH, SW = shot.shape[:2]
sg = cv2.cvtColor(shot, cv2.COLOR_BGR2GRAY).astype(np.float32)
print(f"截图 {SW}x{SH}")

fb = cv2.imread(str(FRAME_B))
mini, mask = mm.make_minimap_roi(fb, cfg)
mx0, my0, _, _ = mm.minimap_rect(fb, cfg)
CCX = float(cfg["cx"]) * fb.shape[1] - mx0
CCY = float(cfg["cy"]) * fb.shape[0] - my0
mg = cv2.cvtColor(mini, cv2.COLOR_BGR2GRAY).astype(np.float32)

# ---- 步骤 1：整图模板在 f00081 上的最佳缩放
print("\n[1] 整图模板 -> f00081 多尺度")
best = None
for s in (0.30, 0.24, 0.20, 0.16, 0.14, 0.12, 0.10, 0.09, 0.08, 0.07, 0.06):
    t = cv2.resize(sg, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    if t.shape[0] < 6 or t.shape[1] < 6:
        continue
    res = cv2.matchTemplate(mg, t, cv2.TM_CCOEFF_NORMED)
    rh, rw = res.shape
    v = np.where(mask[:rh, :rw] > 0, res, -1.0)
    k = int(np.argmax(v))
    yy, xx = divmod(k, rw)
    print(f"   s={s:.2f}  {t.shape[1]:>3}x{t.shape[0]:<3}  "
          f"ncc={v[yy,xx]:6.3f}  距圆心 "
          f"{math.hypot(xx+t.shape[1]//2-CCX, yy+t.shape[0]//2-CCY):5.1f}px")
    if best is None or v[yy, xx] > best[0]:
        best = (float(v[yy, xx]), s)
S = best[1]
print(f"   -> 最佳 s={S:.2f}  ncc={best[0]:.3f}")

# ---- 步骤 2：截图自匹配，定位图标中心
small = cv2.resize(shot, None, fx=S, fy=S, interpolation=cv2.INTER_AREA)
smg = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
self_res = cv2.matchTemplate(smg, smg, cv2.TM_CCOEFF_NORMED)
self_res[:] = -1.0                      # 排除"完全重合"的平凡解（0,0）
self_res[-1, -1] = -1.0
self_res[0, 0] = -1.0
# 真正的自匹配峰值会落在四角（全等），也排除
self_res[:4, :4] = -1.0
self_res[:4, -4:] = -1.0
self_res[-4:, :4] = -1.0
self_res[-4:, -4:] = -1.0
k = int(np.argmax(self_res))
sy, sx = divmod(k, self_res.shape[1])
print(f"\n[2] 截图自匹配 -> 峰值 ({sx},{sy}) ncc={self_res[sy,sx]:.3f}")
print(f"    缩小版 {small.shape[1]}x{small.shape[0]}  "
      f"-> 原图坐标 ({sx/S:.1f},{sy/S:.1f})")

# 图标在原图里的框：以自匹配中心为基准，按缩小版大小回推
iw, ih = small.shape[1], small.shape[0]
cx0, cy0 = sx / S, sy / S                 # 原图坐标
# 模板左上角 = 峰值位置（matchTemplate 返回左上角）
tx, ty = cx0, cy0
pad = 6
bx0, by0 = max(0, int(tx) - pad), max(0, int(ty) - pad)
bx1 = min(SW, int(round(tx + iw / S)) + pad)
by1 = min(SH, int(round(ty + ih / S)) + pad)
TPL = shot[by0:by1, bx0:bx1].copy()
print(f"    干净模板框 x[{bx0},{bx1}) y[{by0},{by1}) = "
      f"{TPL.shape[1]}x{TPL.shape[0]}")
print(f"    （自匹配只保证对齐，框的大小来自缩放比，可能还需收紧）")

# 步骤 3：按"中性白/深色/高饱和蓝"再收紧一次（这次排除绿背景）
hsv = cv2.cvtColor(TPL, cv2.COLOR_BGR2HSV)
Hh, Ss, Vv = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
B_, G_, R_ = TPL[:, :, 0], TPL[:, :, 1], TPL[:, :, 2]
mx = TPL.max(2).astype(int)
mn = TPL.min(2).astype(int)
neutral_white = (mx > 200) & ((mx - mn) < 26)          # 真的中性白
icon_px = (neutral_white | (Vv < 150) |
           ((Hh >= 95) & (Hh <= 135) & (Ss > 110)))
m = icon_px.astype(np.uint8)
m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
n, lab, st, ct = cv2.connectedComponentsWithStats(m, 8)
comps = [st[j] for j in range(1, n) if st[j][4] >= 40]
if comps:
    x0c = min(c[0] for c in comps)
    y0c = min(c[1] for c in comps)
    x1c = max(c[0] + c[2] for c in comps)
    y1c = max(c[1] + c[3] for c in comps)
    CLEAN = TPL[y0c:y1c, x0c:x1c].copy()
    cov = float((m[y0c:y1c, x0c:x1c] > 0).mean())
    print(f"    收紧后 {CLEAN.shape[1]}x{CLEAN.shape[0]}  "
          f"图标覆盖率 {cov:.0%}")
else:
    CLEAN = TPL
    cov = 0.0
    print("    收紧失败，用自匹配框")

# ---- 出图
Z = 12
tiles = []
for name, img in (("raw box", TPL), ("tight", CLEAN)):
    if img is None or img.size == 0:
        continue
    b = cv2.resize(img, (img.shape[1] * Z, img.shape[0] * Z),
                   interpolation=cv2.INTER_NEAREST)
    for k2 in range(0, b.shape[1], 5 * Z):
        cv2.line(b, (k2, 0), (k2, b.shape[0]), (0, 255, 255), 1)
    for k2 in range(0, b.shape[0], 5 * Z):
        cv2.line(b, (0, k2), (b.shape[1], k2), (0, 255, 255), 1)
    bar = np.zeros((24, b.shape[1], 3), np.uint8)
    cv2.putText(bar, f"{name}: {img.shape[1]}x{img.shape[0]}",
                (4, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    tiles.append(np.vstack([bar, b]))
_H = max(t.shape[0] for t in tiles)
tiles = [cv2.copyMakeBorder(t, 0, _H - t.shape[0], 0, 0,
                            cv2.BORDER_CONSTANT) for t in tiles]
row = np.hstack(tiles)
hdr = np.zeros((30, row.shape[1], 3), np.uint8)
cv2.putText(hdr, f"USER ANCHOR TEMPLATE  cyan grid=5px x12  "
                 f"scale-to-minimap = {S:.2f}", (8, 21),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)
cv2.imwrite(str(FIG / "template_user_anchor_v2.png"), np.vstack([hdr, row]))
print(f"  -> template_user_anchor_v2.png")

# ---- 在 f00081 验证干净模板
tg = cv2.cvtColor(CLEAN, cv2.COLOR_BGR2GRAY).astype(np.float32)
tg = cv2.resize(tg, (max(6, int(tg.shape[1] * S)), max(6, int(tg.shape[0] * S))),
                 interpolation=cv2.INTER_AREA)
res = cv2.matchTemplate(mg, tg, cv2.TM_CCOEFF_NORMED)
rh, rw = res.shape
v = np.where(mask[:rh, :rw] > 0, res, -1.0)
k = int(np.argmax(v))
yy, xx = divmod(k, rw)
px, py = xx + tg.shape[1] // 2, yy + tg.shape[0] // 2
rr = math.hypot(px - CCX, py - CCY)
print(f"\n[3] 干净模板({tg.shape[1]}x{tg.shape[0]}) -> f00081: "
      f"ncc={v[yy,xx]:.3f} @({px},{py}) 距圆心 {rr:.1f}px")
print(f"    {'>> 距圆心>25px，不是玩家自己' if rr > 25 else '!! 距圆心<25px，可能是玩家'}")

vis = cv2.resize(mini, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
cv2.rectangle(vis, ((px - tg.shape[1] // 2) * 3, (py - tg.shape[0] // 2) * 3),
              ((px + tg.shape[1] // 2) * 3, (py + tg.shape[0] // 2) * 3),
              (0, 0, 255), 2)
cv2.putText(vis, f"{v[yy,xx]:.3f}", ((px - 20) * 3, (py - 26) * 3),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2)
cv2.circle(vis, (int(CCX * 3), int(CCY * 3)), 14, (255, 0, 255), 3)
cv2.putText(vis, "player", (int(CCX * 3) + 18, int(CCY * 3) + 6),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 2)
hdr2 = np.zeros((30, vis.shape[1], 3), np.uint8)
cv2.putText(hdr2, "batch_20260929b/f00081.jpg | red = clean user-anchor "
                  "template | magenta = player", (8, 21),
            cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1)
cv2.imwrite(str(FIG / "verify_anchor_clean_f00081.png"), np.vstack([hdr2, vis]))
print(f"  -> verify_anchor_clean_f00081.png")

# 保存模板供后续脚本用
np.save(FIG / "user_anchor_template.npy", CLEAN)
(FIG / "user_anchor_scale.txt").write_text(
    f"{S}\n", encoding="utf-8")
print(f"  -> user_anchor_template.npy {CLEAN.shape}")
print("=== DONE ===")
