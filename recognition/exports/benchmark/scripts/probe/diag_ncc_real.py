"""诊断：真实帧上 NCC 到底能到多少分？和缩放档/表示方式的关系。"""

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

import cv2
import numpy as np

PERC = (Path(PLUGIN_DIR.parent)
        / "genshin_game_buddy" / "autopilot" / "perception")
RECOG = Path(RECOG_ROOT)
DA = RECOG / "raw" / "frames" / "session_20261001_a"


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


ml = _load("minimap_locator", PERC / "minimap_locator.py")
mm = _load("minimap_mask", PERC / "minimap_mask.py")
pl = _load("pyramid_loader", PERC / "pyramid_loader.py")
ct = _load("neko_coord_transform",
           RECOG / "exports" / "benchmark" / "scripts" / "misc"
           / "coord_transform.py")

PY = pl.PyramidLoader(preload=(4, 3, 2, 1))
L4 = PY.get_level(4)
k4 = 1.0 / 16.0

FID = "f00200"
im = cv2.imread(str(DA / f"{FID}.jpg"))
mini, mask = mm.make_minimap_roi(im)
mini_g = cv2.cvtColor(mini, cv2.COLOR_BGR2GRAY)
print(f"{FID}  小地图裁块 {mini_g.shape}  "
      f"圆内像素 {int((mask>127).sum())}")
print(f"L4 {L4.shape}   底图灰度 mean={L4.mean():.1f} std={L4.std():.1f}")
print(f"小地图灰度 mean={mini_g.mean():.1f} std={mini_g.std():.1f}")


def top5(search, templ, m, tag):
    res = cv2.matchTemplate(search, templ, cv2.TM_SQDIFF, mask=m)
    flat = res.ravel()
    idx = np.argpartition(flat, 5)[:5]
    idx = idx[np.argsort(flat[idx])]
    out = []
    for i in idx:
        y, x = divmod(int(i), res.shape[1])
        sc, _ = ml.match_ncc(search, templ, m, )
        out.append((x, y))
    print(f"  {tag:<26} 模板 {templ.shape[0]:>3}px  "
          f"SQDIFF 最优 @({out[0][0]},{out[0][1]})")
    sc, loc = ml.match_ncc(search, templ, m)
    print(f"     NCC 最高分 {sc:+.4f} @ {loc}")
    return sc


print("\n" + "=" * 76)
print("A · 灰度 NCC（L4 1/16 全局搜）")
print("=" * 76)
for sc_name, sc in (("FIELD 1.0", 1.0), ("CITY 2.0", 2.0),
                    ("0.75", 0.75), ("1.5", 1.5)):
    d = int(round(mini_g.shape[0] * sc * k4))
    if d < 4 or d >= min(L4.shape):
        print(f"  {sc_name:<26} 模板 {d}px 跳过（越界）")
        continue
    t = cv2.resize(mini_g, (d, d), interpolation=cv2.INTER_AREA)
    m = cv2.resize(mask, (d, d), interpolation=cv2.INTER_NEAREST)
    m = (m > 127).astype(np.uint8) * 255
    s, _ = ml.match_ncc(L4, t, m)
    print(f"  {sc_name:<26} 模板 {d:>3}px  最高 NCC {s:+.4f}")

print("\n" + "=" * 76)
print("B · 换成 Canny 边缘图后再匹配")
print("=" * 76)
mini_e = cv2.Canny(mini_g, 60, 160)
l4_e = cv2.Canny(L4, 60, 160)
print(f"  边缘密度: 小地图 {mini_e.mean()/255:.3%}  L4 {l4_e.mean()/255:.3%}")
for sc_name, sc in (("FIELD 1.0", 1.0), ("CITY 2.0", 2.0)):
    d = int(round(mini_e.shape[0] * sc * k4))
    if d < 4 or d >= min(L4.shape):
        continue
    t = cv2.resize(mini_e, (d, d), interpolation=cv2.INTER_AREA)
    m = cv2.resize(mask, (d, d), interpolation=cv2.INTER_NEAREST)
    m = (m > 127).astype(np.uint8) * 255
    t = (t > 0).astype(np.uint8) * 255
    s, _ = ml.match_ncc(l4_e, t, m)
    print(f"  {sc_name:<26} 模板 {d:>3}px  最高 NCC {s:+.4f}")

print("\n" + "=" * 76)
print("C · 直方图均衡化后再灰度匹配")
print("=" * 76)
l4_eq = cv2.equalizeHist(L4)
for sc_name, sc in (("FIELD 1.0", 1.0), ("CITY 2.0", 2.0)):
    d = int(round(mini_g.shape[0] * sc * k4))
    if d < 4 or d >= min(L4.shape):
        continue
    t = cv2.resize(cv2.equalizeHist(mini_g), (d, d),
                   interpolation=cv2.INTER_AREA)
    m = cv2.resize(mask, (d, d), interpolation=cv2.INTER_NEAREST)
    m = (m > 127).astype(np.uint8) * 255
    s, _ = ml.match_ncc(l4_eq, t, m)
    print(f"  {sc_name:<26} 模板 {d:>3}px  最高 NCC {s:+.4f}")

print("\n" + "=" * 76)
print("D · 无掩码 / 有掩码 / 已知真值处的分数")
print("=" * 76)
# 用 coord_transform 反算一个真值位置：假设帧 200 在枫丹附近（随便取一个点）
for wx, wz, tag in ((3598.52, 4633.18, "枫丹廷(真值)"),
                    (2566.11, -1323.47, "星落湖(真值)")):
    px, py = ct.world_to_pixel(wx, wz)
    print(f"  {tag}: 像素 ({px:.0f},{py:.0f})  L4 坐标 ({px*k4:.1f},{py*k4:.1f})")
    d = int(round(mini_g.shape[0] * 1.0 * k4))
    t = cv2.resize(mini_g, (d, d), interpolation=cv2.INTER_AREA)
    m = cv2.resize(mask, (d, d), interpolation=cv2.INTER_NEAREST)
    m = (m > 127).astype(np.uint8) * 255
    # 在真值附近取 64x64 小窗算 NCC
    cx, cy = int(px * k4), int(py * k4)
    x0, y0 = max(0, cx - 32), max(0, cy - 32)
    win = L4[y0:y0 + 64, x0:x0 + 64]
    if win.shape[0] >= d and win.shape[1] >= d:
        s, _ = ml.match_ncc(win, t, m)
        print(f"     真值邻域 NCC {s:+.4f}")
    else:
        print(f"     邻域太小({win.shape}) 跳过")
