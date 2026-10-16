"""阶段 1 · 场景检测器数据准备 —— 大世界 UI 位置目视测量。

★ 做法
------
先渲染带**归一化网格**的整帧图，肉眼确认 UI 元素落在哪，
再切候选 ROI。**不盲猜** —— 猜出来的位置用户一眼就能看出不对。

★ 只读
----
帧文件只读不改。产物只写 visualizations/ui_probe/ 和 datasets/。
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
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image, ImageDraw

RECOG = Path(RECOG_ROOT)
FRAMES_B = RECOG / "raw" / "frames" / "batch_20260929b"
FRAMES_A = RECOG / "raw" / "frames" / "batch_20260929a"
OUT_DIR = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe"
OUT_DIR.mkdir(parents=True, exist_ok=True)

REF = (2304, 1440)

#: 已确认在大世界的帧（world_frames.csv, is_world=1, confidence=1.0）
WORLD = ["f00167", "f00172", "f00209", "f00245", "f00279", "f00267"]


def find_frame(fid: str) -> Path | None:
    for d in (FRAMES_B, FRAMES_A):
        p = d / f"{fid}.jpg"
        if p.is_file():
            return p
    return None


def grid_overlay(im: Image.Image, step: float = 0.1,
                 major: float = 0.5) -> Image.Image:
    """在图上叠加归一化坐标网格。"""
    out = im.copy()
    d = ImageDraw.Draw(out, "RGBA")
    W, H = out.size
    n = int(round(1.0 / step))
    for i in range(n + 1):
        f = i * step
        x = int(f * W)
        y = int(f * H)
        is_major = abs(f - round(f / major) * major) < 1e-6
        col = (255, 90, 90, 200) if is_major else (120, 200, 255, 110)
        w = 3 if is_major else 1
        d.line([(x, 0), (x, H)], fill=col, width=w)
        d.line([(0, y), (W, y)], fill=col, width=w)
        if i % 2 == 0:
            d.text((x + 3, 3), f"{f:.1f}", fill=(255, 255, 0, 255))
            d.text((3, y + 3), f"{f:.1f}", fill=(0, 255, 255, 255))
    return out


def save_overview(fid: str) -> None:
    p = find_frame(fid)
    if p is None:
        print(f"  !! 找不到 {fid}")
        return
    im = Image.open(p).convert("RGB")
    if im.size != REF:
        print(f"  !! {fid} 尺寸 {im.size} != {REF}")
    g = grid_overlay(im)
    g = g.resize((1152, 720), Image.Resampling.LANCZOS)
    out = OUT_DIR / f"overview_{fid}.png"
    g.save(out)
    print(f"  {out.name}   {im.size} -> {g.size}")


def save_bottom(fid: str, y0: float, y1: float, scale: float = 0.75) -> None:
    """底部横条：左=全宽，右=右侧局部。"""
    p = find_frame(fid)
    if p is None:
        return
    im = Image.open(p).convert("RGB")
    W, H = im.size
    strip = im.crop((0, int(y0 * H), W, int(y1 * H)))
    nw = int(strip.width * scale)
    nh = max(1, int(strip.height * scale))
    strip = strip.resize((nw, nh), Image.Resampling.LANCZOS)
    g = grid_overlay(strip, step=0.02)
    out = OUT_DIR / f"bottom_{fid}.png"
    g.save(out)
    print(f"  {out.name}   y {y0}~{y1}  {im.size} -> {g.size}")


def main() -> None:
    print(f"输出目录: {OUT_DIR}\n")
    print("[1] 整帧网格图（看 UI 元素落点）")
    for fid in ["f00167", "f00209"]:
        save_overview(fid)

    print("\n[2] 底部横条（细看血条 / 技能栏）")
    for fid in ["f00167", "f00209", "f00279"]:
        save_bottom(fid, 0.80, 1.00, 0.60)

    print("\n[3] 右下角局部（技能栏候选）")
    for fid in ["f00167", "f00209"]:
        p = find_frame(fid)
        if p is None:
            continue
        im = Image.open(p).convert("RGB")
        W, H = im.size
        crop = im.crop((int(0.60 * W), int(0.82 * H), W, H))
        crop = crop.resize((int(crop.width * 0.8),
                            int(crop.height * 0.8)), Image.Resampling.LANCZOS)
        # 叠一层相对全局的细网格
        g = ImageDraw.Draw(crop, "RGBA")
        step_x = 0.02 * W * 0.8
        step_y = 0.02 * H * 0.8
        for i in range(1, 21):
            gx = 0.60 + i * 0.02
            if gx >= 1.0:
                break
            x = int((gx - 0.60) * W * 0.8)
            col = (255, 90, 90, 200) if abs(gx * 10 - round(gx * 10)) < 1e-6 \
                and abs(gx * 10 % 5) < 1e-6 else (120, 200, 255, 110)
            g.line([(x, 0), (x, crop.height)], fill=col, width=1)
            if i % 2 == 0:
                g.text((x + 2, 2), f"{gx:.2f}", fill=(255, 255, 0, 255))
        for i in range(1, 10):
            gy = 0.82 + i * 0.02
            if gy >= 1.0:
                break
            y = int((gy - 0.82) * H * 0.8)
            g.line([(0, y), (crop.width, y)], fill=(120, 200, 255, 110), width=1)
            g.text((2, y + 2), f"{gy:.2f}", fill=(0, 255, 255, 255))
        out = OUT_DIR / f"br_{fid}.png"
        g_img = ImageDraw.Draw(crop)
        crop.save(out)
        print(f"  {out.name}   x 0.60~1.0 / y 0.82~1.0  -> {crop.size}")
        del g_img
    print("\n=== DONE ===")


if __name__ == "__main__":
    main()
