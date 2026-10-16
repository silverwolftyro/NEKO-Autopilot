"""阶段 1 · 大世界 UI 元素自动定位（血条 / 技能栏）。

★ 为什么不用肉眼估
-----------------
在 2304x1440 的图上靠肉眼读归一化坐标，误差轻松到 ±0.01，
而技能栏图标本身才 0.03 宽 —— 读不回来。改用像素统计。

★ 思路
------
UI 元素在**每一帧大世界画面里位置固定**，而世界背景一直在变。
所以：
  1. 在候选区域找"亮/高饱和"的连通块（技能图标、HP 条）
  2. 跨多帧统计每个块的出现位置
  3. **位置几乎不变且面积稳定**的 = UI 元素
只读帧，不改帧。
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

import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

RECOG = Path(RECOG_ROOT)
FRAMES_B = RECOG / "raw" / "frames" / "batch_20260929b"
FRAMES_A = RECOG / "raw" / "frames" / "batch_20260929a"
OUT = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe"

#: 已确认大世界的帧
WORLD = ["f00167", "f00168", "f00172", "f00174", "f00203", "f00205",
         "f00209", "f00245", "f00263", "f00267", "f00278", "f00279"]

W, H = 2304, 1440


def load(fid):
    for d in (FRAMES_B, FRAMES_A):
        p = d / f"{fid}.jpg"
        if p.is_file():
            im = cv2.imread(str(p))
            if im is not None:
                return cv2.cvtColor(im, cv2.COLOR_BGR2RGB)
    return None


def np_box(x0, y0, x1, y1):
    return [round(x0 / W, 4), round(y0 / H, 4),
            round(x1 / W, 4), round(y1 / H, 4)]


# ------------------------------------------------ 血条：找亮绿色横条
def probe_hp(frames):
    print("=" * 78)
    print("[A] 血条定位（找高亮度绿色横条）")
    print("=" * 78)
    found = []
    for fid in frames:
        im = load(fid)
        if im is None:
            continue
        R = im[:, :, 0].astype(np.int16)
        G = im[:, :, 1].astype(np.int16)
        B = im[:, :, 2].astype(np.int16)
        # 亮绿：G 明显高于 R/B，且整体够亮
        mask = ((G - np.maximum(R, B) > 35) & (G > 110)).astype(np.uint8)
        # 只在底部中央 30~70% x, 90~98% y 找
        x0, x1 = int(0.30 * W), int(0.70 * W)
        y0, y1 = int(0.90 * H), int(0.98 * H)
        sub = np.zeros_like(mask)
        sub[y0:y1, x0:x1] = mask[y0:y1, x0:x1]
        n, lab, stats, _ = cv2.connectedComponentsWithStats(sub, 8)
        best = None
        for i in range(1, n):
            bx, by, bw, bh, area = stats[i]
            if bw < 80 or bh < 3 or bh > 40:      # 细长横条
                continue
            if area < 400:
                continue
            if best is None or area > best[4]:
                best = (bx, by, bw, bh, area)
        if best:
            bx, by, bw, bh, area = best
            found.append((fid, bx, by, bw, bh, area))
            print(f"  {fid}  x{bx:>5}~{bx+bw:<5} y{by:>4}~{by+bh:<4} "
                  f"  ({bw}x{bh}, area={area})  "
                  f"norm={np_box(bx, by, bx+bw, by+bh)}")
    if not found:
        print("  !! 一帧都没找到")
        return
    a = np.array([[f[1], f[2], f[3], f[4]] for f in found], float)
    print(f"\n  {len(found)}/{len(frames)} 帧命中")
    for k, name in ((0, "x0"), (1, "y0"), (2, "x1"), (3, "y1")):
        col = a[:, k]
        print(f"    {name}: min={col.min():>7.1f} med={np.median(col):>7.1f} "
              f"max={col.max():>7.1f}  std={col.std():>6.1f}")
    x0, y0 = np.median(a[:, 0]), np.median(a[:, 1])
    x1, y1 = np.median(a[:, 0] + a[:, 2]), np.median(a[:, 1] + a[:, 3])
    # 向外扩 3px 保险
    x0, y0 = int(x0) - 3, int(y0) - 3
    x1, y1 = int(x1) + 3, int(y1) + 3
    print(f"\n  ★ 血条 ROI（中位数 + 3px 保险）= {np_box(x0, y0, x1, y1)}")


# ------------------------------------------------ 技能栏：找右下角亮块
def probe_skill(frames):
    print("\n" + "=" * 78)
    print("[B] 技能栏定位（右下角亮/高饱和连通块）")
    print("=" * 78)
    tally = Counter()
    per_frame = []
    for fid in frames:
        im = load(fid)
        if im is None:
            continue
        hsv = cv2.cvtColor(im, cv2.COLOR_RGB2HSV)
        x0, x1 = int(0.78 * W), W
        y0, y1 = int(0.84 * H), H
        sub = hsv[y0:y1, x0:x1]
        S = sub[:, :, 1].astype(np.int16)
        V = sub[:, :, 2].astype(np.int16)
        # 图标 = 亮（V 高）且低饱和（白色按键框）或高饱和（彩色图标）
        mask = ((V > 150) & ((S < 70) | (S > 150))).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                                np.ones((2, 2), np.uint8))
        n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        boxes = []
        for i in range(1, n):
            bx, by, bw, bh, area = stats[i]
            if bw < 18 or bh < 18 or area < 320:
                continue
            if bw > 300 or bh > 300:
                continue
            gx0, gy0 = bx + x0, by + y0
            boxes.append((gx0, gy0, bw, bh, area))
            # 量化到 0.005 网格做聚类投票
            key = (round(gx0 / W / 0.005), round(gy0 / H / 0.005),
                   round((gx0 + bw) / W / 0.005),
                   round((gy0 + bh) / H / 0.005))
            tally[key] += 1
        if fid == "f00167":
            dbg = cv2.cvtColor(mask * 255, cv2.COLOR_GRAY2BGR)
            cv2.imwrite(str(OUT / "debug_skill_mask_f00167.png"), dbg)
        per_frame.append((fid, boxes))
        desc = "  ".join(f"[{a/W:.3f},{b/H:.3f},{(a+c)/W:.3f},{(b+d)/H:.3f}]"
                         for a, b, c, d, _ in boxes[:6])
        print(f"  {fid}  块数={len(boxes)}  {desc}")

    print(f"\n  跨 {len(per_frame)} 帧的稳定块（出现 >= 60% 帧）:")
    thr = max(3, int(len(per_frame) * 0.6))
    for key, cnt in tally.most_common(12):
        if cnt < thr:
            break
        gx0, gy0, gx1, gy1 = [k * 0.005 for k in key]
        print(f"    出现 {cnt:>2}/{len(per_frame)}  "
              f"norm=[{gx0:.3f}, {gy0:.3f}, {gx1:.3f}, {gy1:.3f}]  "
              f"像素={int(gx0*W)}x{int(gy0*H)}")
    if not tally:
        print("    （无稳定块）")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    frames = [f for f in WORLD if load(f) is not None]
    print(f"可用大世界帧 {len(frames)}: {frames}\n")
    probe_hp(frames)
    probe_skill(frames)
    print("\n=== DONE ===")


if __name__ == "__main__":
    main()
