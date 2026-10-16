"""快测：游戏内大地图 ↔ 游戏小地图，域差距还在吗？

**这一步决定要不要投入全流程（拼底图 + OCR + 全量验证）。**

已知：
* 米游社底图 ↔ 小地图：ORB 2 内点，SIFT 2 内点，彩色 NCC 变号
* 全图 4095 窗口穷举也只到 5~7 内点

现在换成**游戏内大地图画面**当参考 —— 如果同一套渲染管线，
同域差距应该消失，ORB 内点应该回到两位数甚至三位数。

三组对照：
  A 大地图裁块 @全分辨率   vs  小地图
  B 大地图裁块 @1/2 分辨率 vs  小地图      （模拟尺度差）
  C **小地图 vs 小地图自身**（尺度/流程自洽性检查，必须存在）

C 组是下限基准：如果 C 组也拿不到内点，说明小地图本身的特征
就不稳定（图标遮挡 / 缩放模糊），换个底图也救不了。
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
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

PERC = (Path(PLUGIN_DIR.parent)
        / "genshin_game_buddy" / "autopilot" / "perception")
RECOG = Path(RECOG_ROOT)
DB = RECOG / "raw" / "frames" / "session_20261001_b"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" \
    / "bigmap"
FIG.mkdir(parents=True, exist_ok=True)


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


mm = _load("minimap_mask", PERC / "minimap_mask.py")
cfg = mm.load_circle_cfg()
ids = sorted(DB.glob("*.jpg"))

BIGMAPS = [120, 150, 170, 190, 210, 230, 240]     # 目视确认的大地图帧
MINIMAPS = [20, 30, 40, 50, 60, 70]              # 目视确认的世界帧
BF = cv2.BFMatcher(cv2.NORM_HAMMING)
OQ = cv2.ORB_create(nfeatures=3000, nlevels=8, scaleFactor=1.2, fastThreshold=5)
OW = cv2.ORB_create(nfeatures=15000, nlevels=8, scaleFactor=1.2, fastThreshold=5)

_f = cv2.imread(str(ids[MINIMAPS[0] - 1]))
FH, FW = _f.shape[:2]
x0, y0, x1, y1 = mm.minimap_rect(_f, cfg)
CCX, CCY = float(cfg["cx"]) * FW - x0, float(cfg["cy"]) * FH - y0
print(f"帧 {FW}x{FH}  小地图 {x1-x0}x{y1-y0}  圆心 ({CCX:.1f},{CCY:.1f})")


def feats(img, n, mask=None):
    o = OQ if n <= 3000 else OW
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    k, d = o.detectAndCompute(g, mask)
    return k, d


def match_db(qk, qd, dbk, dbd, label, ratio=0.78):
    good = [m for m, n2 in BF.knnMatch(qd, dbd, k=2)
            if m.distance < ratio * n2.distance]
    if len(good) < 4:
        print(f"  {label:<46} good={len(good):>4}  -> good<4")
        return len(good), 0
    dp = np.array([k.pt for k in dbk], np.float32)
    src = np.float32([qk[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([dp[m.trainIdx] for m in good]).reshape(-1, 1, 2)
    A, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC,
                                         ransacReprojThreshold=2.5,
                                         maxIters=20000)
    n = 0 if (A is None or inl is None) else int(inl.sum())
    ex = sc = rot = float("nan")
    if A is not None:
        qx, qy = qk[0].pt if False else 0, 0
        h, w = (qk[0].pt[1], qk[0].pt[0])
        ex = float("nan")
        sc = math.hypot(A[0, 0], A[1, 0])
        rot = math.degrees(math.atan2(A[1, 0], A[0, 0]))
    print(f"  {label:<46} good={len(good):>4} inl={n:>4} "
          f"尺度={sc:.3f} 旋转={rot:+.1f}deg")
    return len(good), n


print("\n" + "=" * 96)
print("C 组 · 小地图 vs 小地图（自洽性基准，必须有信号）")
print("=" * 96)
mini_ref, _ = feats(cv2.imread(str(ids[MINIMAPS[0] - 1]))[y0:y1, x0:x1], 3000)
mrk, mrd = feats(cv2.imread(str(ids[MINIMAPS[0] - 1]))[y0:y1, x0:x1], 3000)
for f in MINIMAPS[1:]:
    c, _ = mm.make_minimap_roi(cv2.imread(str(ids[f - 1])), cfg)
    qk, qd = feats(c, 3000)
    match_db(qk, qd, mrk, mrd, f"C 小地图 f{f:05d} vs f{MINIMAPS[0]:05d}")

print("\n" + "=" * 96)
print("A 组 · 大地图裁块(全分辨率) vs 小地图")
print("=" * 96)
resA = []
for bf_ in BIGMAPS:
    im = cv2.imread(str(ids[bf_ - 1]))
    H, W = im.shape[:2]
    # 取画面正中一块 768x768（必定在地图内，避开右侧传送锚点面板）
    cx, cy = W // 2, H // 2
    crop = im[cy - 384:cy + 384, cx - 384:cx + 384].copy()
    bk, bd = feats(crop, 15000)
    for f in (MINIMAPS[0], MINIMAPS[2]):
        c, msk = mm.make_minimap_roi(cv2.imread(str(ids[f - 1])), cfg)
        qk, qd = feats(c, 3000, msk)
        g, n = match_db(qk, qd, bk, bd, f"A 大地图 f{bf_:05d} vs 小地图 f{f:05d}")
        resA.append(n)
        cv2.imwrite(str(FIG / f"A_bigmap{bf_:05d}_vs_mini{f:05d}.png"),
                    np.hstack([
                        cv2.resize(c, (384, 384), interpolation=cv2.INTER_CUBIC),
                        cv2.resize(crop, (384, 384), interpolation=cv2.INTER_AREA)]))

print("\n" + "=" * 96)
print("B 组 · 大地图裁块(1/2 分辨率) vs 小地图（模拟尺度差）")
print("=" * 96)
resB = []
for bf_ in BIGMAPS[:4]:
    im = cv2.imread(str(ids[bf_ - 1]))
    H, W = im.shape[:2]
    cx, cy = W // 2, H // 2
    crop = im[cy - 384:cy + 384, cx - 384:cx + 384]
    small = cv2.resize(crop, (192, 192), interpolation=cv2.INTER_AREA)
    bk, bd = feats(cv2.resize(small, (384, 384), interpolation=cv2.INTER_CUBIC), 15000)
    for f in (MINIMAPS[0], MINIMAPS[2]):
        c, msk = mm.make_minimap_roi(cv2.imread(str(ids[f - 1])), cfg)
        qk, qd = feats(c, 3000, msk)
        g, n = match_db(qk, qd, bk, bd, f"B 大地图1/2 f{bf_:05d} vs 小地图 f{f:05d}")
        resB.append(n)

print("\n" + "=" * 96)
print("汇总")
print("=" * 96)
print(f"  A 组(大地图全分辨率) 内点: {resA}  中位 "
      f"{np.median(resA) if resA else 0:.0f}")
print(f"  B 组(大地图 1/2)     内点: {resB}  中位 "
      f"{np.median(resB) if resB else 0:.0f}")
print(f"\n  参照：米游社底图 vs 小地图 = 2 内点；"
      f"米游社底图自匹配 = 1187 内点")
print("=== DONE ===")
