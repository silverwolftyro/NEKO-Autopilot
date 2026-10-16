"""决定性一步：游戏内大地图 ↔ 米游社 P0 底图 能不能对齐？

**为什么先做这个**
上一轮"大地图 vs 小地图"只拿到 2 个内点，但那个测试有两个缺陷：
1. 裁的 768x768 中心块**未必是城区**（大地图中心常落在水面/山地）
2. 不知道大地图的**精确 m/px**，尺度对不对都不知道

如果大地图能和 P0 对齐，就能一次性拿到：
* 大地图的精确 m/px 和像素->世界坐标 的仿射
* 城区在大地图画面上的确切位置
* 拼接多帧成底图的标定参数

而这一步能成，**本身就有价值**：说明"游戏渲染地图"和"米游社静态图"
是同域的（和"游戏渲染小地图 vs 米游社底图"完全不同）。

**验证链**
  步骤 1  大地图画面(去 UI) -> P0 枫丹廷子图     [同域对齐，决定 m/px]
  步骤 2  用步骤 1 得到的仿射裁出城区             [拿到真·城区块]
  步骤 3  城区块 vs 游戏小地图                    [最终判据]

对照始终是 米游社底图自匹配 = 1187 内点。
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
P0 = RECOG / "data" / "mihoyo_maps" / "teyvat_P0.png"
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
ct = _load("coord_transform",
           RECOG / "exports" / "benchmark" / "scripts" / "misc"
           / "coord_transform.py")

ids = sorted(DB.glob("*.jpg"))
BIG_FRAMES = [120, 167, 224, 339, 439, 481, 570, 967]
MINI_FRAMES = [20, 30, 40, 50]

# 大地图画面里真正的"地图区"（去掉右侧传送锚点面板和顶部资源栏）
# 归一化 x0,y0,x1,y1，按实际帧尺寸缩放，不硬编码分辨率
MAP_ROI = (0.000, 0.055, 0.760, 0.985)

BF = cv2.BFMatcher(cv2.NORM_HAMMING)
OQ = cv2.ORB_create(nfeatures=6000, nlevels=10, scaleFactor=1.2,
                    fastThreshold=5)
OMINI = cv2.ORB_create(nfeatures=3000, nlevels=8, scaleFactor=1.2,
                       fastThreshold=5)

t0 = time.perf_counter()
# P0 彩色 1/2 层 = 2 m/px
L1 = cv2.imread(str(P0), cv2.IMREAD_REDUCED_COLOR_2)
print(f"P0 彩色 1/2 层 {L1.shape} @2 m/px   读入 {time.perf_counter()-t0:.1f}s")

# 枫丹廷区域（底图像素 L0 18830,7651 附近），1/2 层坐标
CX1, CY1 = int(18830 / 2) - 1150, int(7651 / 2) - 950     # 2300x1900
SUB = L1[CY1:CY1 + 1900, CX1:CX1 + 2300].copy()
print(f"P0 枫丹廷子图 {SUB.shape}  L0 角点 ({CX1*2},{CY1*2})  "
      f"覆盖 {2300*2}m x {1900*2}m")
t = time.perf_counter()
sk, sd_ = OQ.detectAndCompute(SUB, None)
sp = np.array([k.pt for k in sk], np.float32)
print(f"  ORB 库 {len(sk)} 点  {time.perf_counter()-t:.1f}s")


def map_region(im):
    H, W = im.shape[:2]
    x0, y0, x1, y1 = MAP_ROI
    return im[int(y0 * H):int(y1 * H), int(x0 * W):int(x1 * W)].copy()


def fit(query, libk, libp, libd, tag, ratio=0.78, reproj=2.5):
    g = cv2.cvtColor(query, cv2.COLOR_BGR2GRAY) if query.ndim == 3 else query
    kq, dq = OQ.detectAndCompute(g, None)
    if dq is None or len(dq) < 8 or libd is None or len(libd) < 2:
        print(f"  {tag:<40} 查询 {0 if dq is None else len(dq)}  -> 无查询")
        return None
    good = [m for m, n2 in BF.knnMatch(dq, libd, k=2)
            if m.distance < ratio * n2.distance]
    if len(good) < 4:
        print(f"  {tag:<40} 查询 {len(kq):>5} good {len(good):>4} -> good<4")
        return None
    src = np.float32([kq[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([libp[m.trainIdx] for m in good]).reshape(-1, 1, 2)
    A, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC,
                                         ransacReprojThreshold=reproj,
                                         maxIters=30000)
    n = 0 if (A is None or inl is None) else int(inl.sum())
    if A is None:
        print(f"  {tag:<40} good {len(good):>4} inl 0  -> 无解")
        return None
    sc = math.hypot(A[0, 0], A[1, 0]) * 2.0        # P0 子图是 2 m/px
    rot = math.degrees(math.atan2(A[1, 0], A[0, 0]))
    print(f"  {tag:<40} 查询 {len(kq):>5} good {len(good):>4} inl {n:>4}"
          f"  尺度={sc:.3f} m/px  旋转={rot:+.2f}deg")
    return dict(A=A, inl=n, good=len(good), scale=sc, rot=rot, qk=kq, qd=dq)


print("\n" + "=" * 96)
print("步骤 1 · 大地图画面(去 UI) -> P0 枫丹廷子图（同域对齐）")
print("=" * 96)
aligns = {}
for f in BIG_FRAMES:
    im = cv2.imread(str(ids[f - 1]))
    reg = map_region(im)
    r = fit(reg, sk, sp, sd_, f"大地图 f{f:05d} -> P0 子图")
    if r:
        aligns[f] = r
        # 画面中心在大地图子图坐标 -> 世界坐标
        cx, cy = reg.shape[1] / 2, reg.shape[0] / 2
        px = r["A"][0, 0] * cx + r["A"][0, 1] * cy + r["A"][0, 2] + CX1
        py = r["A"][1, 0] * cx + r["A"][1, 1] * cy + r["A"][1, 2] + CY1
        wx, wz = ct.pixel_to_world(px * 2, py * 2)
        print(f"      画面中心 -> L0=({px*2:.0f},{py*2:.0f})  "
              f"世界=({wx:.0f},{wz:.0f})   视野宽度 "
              f"{reg.shape[1]*r['scale']:.0f} m")

if not aligns:
    raise SystemExit("步骤 1 零对齐，后面的步骤没有意义")

best = max(aligns.items(), key=lambda kv: kv[1]["inl"])
bf_, br = best
print(f"\n  最强对齐: f{bf_:05d}  inl={br['inl']}  "
      f"尺度={br['scale']:.3f} m/px  视野 "
      f"{cv2.imread(str(ids[bf_-1])).shape[1]*MAP_ROI[2]*br['scale']:.0f} m 宽")

print("\n" + "=" * 96)
print("步骤 2+3 · 用对齐结果裁出城区，再 vs 游戏小地图")
print("=" * 96)
# 枫丹廷区在 L0 约 (19141, 7655)（传送点 841），换算到 P0 子图坐标
TX, TY = 19141 / 2 - CX1, 7655 / 2 - CY1
print(f"  目标城区在子图坐标 ({TX:.0f},{TY:.0f})")
mini, msk = mm.make_minimap_roi(cv2.imread(str(ids[MINI_FRAMES[0] - 1])), cfg)
mimg = cv2.cvtColor(cv2.resize(mini, (512, 512), interpolation=cv2.INTER_CUBIC),
                    cv2.COLOR_BGR2GRAY)
mk, md = OMINI.detectAndCompute(mimg, msk)
print(f"  小地图查询 {len(mk)} 特征点")

for f, r in list(aligns.items())[:4]:
    im = cv2.imread(str(ids[f - 1]))
    reg = map_region(im)
    A = r["A"]
    # 城区中心 -> 该帧大地图画面坐标 -> 大地图区坐标
    bx = A[0, 0] * TX + A[0, 1] * TY + A[0, 2]
    by = A[1, 0] * TX + A[1, 1] * TY + A[1, 2]
    print(f"\n  f{f:05d}: 城区中心在画面区坐标 ({bx:.0f},{by:.0f})")
    for half in (256, 512):
        if not (half <= bx < reg.shape[1] - half
                and half <= by < reg.shape[0] - half):
            print(f"      半宽 {half}: 出画面，跳过")
            continue
        crop = reg[int(by - half):int(by + half), int(bx - half):int(bx + half)]
        ck, cd = OQ.detectAndCompute(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), None)
        if cd is None or len(cd) < 8:
            print(f"      半宽 {half}: 裁块无特征")
            continue
        good = [m for m, n2 in BF.knnMatch(md, cd, k=2)
                if m.distance < 0.78 * n2.distance]
        if len(good) < 4:
            print(f"      半宽 {half} ({half*2*r['scale']:.0f} m): "
                  f"good {len(good)} -> <4")
            continue
        src = np.float32([mk[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([[k.pt for k in ck][m.trainIdx] for m in good]
                          ).reshape(-1, 1, 2)
        A2, inl = cv2.estimateAffinePartial2D(
            src, dst, method=cv2.RANSAC, ransacReprojThreshold=2.5,
            maxIters=20000)
        n = 0 if (A2 is None or inl is None) else int(inl.sum())
        sc2 = (float("nan") if A2 is None
               else math.hypot(A2[0, 0], A2[1, 0]) * 2.0)
        rot2 = (float("nan") if A2 is None
                else math.degrees(math.atan2(A2[1, 0], A2[0, 0])))
        print(f"      半宽 {half} ({half*2*r['scale']:.0f} m 见方): "
              f"块特征 {len(ck):>5} good {len(good):>4} inl {n:>4} "
              f"尺度={sc2:.3f} m/px 旋转={rot2:+.1f}deg")
        cv2.imwrite(str(FIG / f"citycrop_f{f:05d}_h{half}.png"),
                    np.hstack([
                        cv2.resize(mimg, (384, 384),
                                   interpolation=cv2.INTER_AREA),
                        cv2.resize(crop, (384, 384),
                                   interpolation=cv2.INTER_AREA)]))

print(f"\n  参照：米游社底图 vs 小地图 = 2 内点；米游社底图自匹配 = 1187")
print(f"  总耗时 {time.perf_counter()-t0:.0f}s")
print("=== DONE ===")
