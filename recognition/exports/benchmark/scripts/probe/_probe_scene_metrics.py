"""scene 判据选型 —— 写 game_scene.py 之前的必做验证。

用户设想的判据是"minimap ROI 有圆形边界 = 大世界"。
本脚本先量化验证它**行不行**，再比较几个候选，最后才决定检测器怎么写。

已知标签（人眼看整帧得出）：
    大世界     : f00280 f00100 f00005 f00001
    全屏地图   : f00250 f00160 f00064 f00155
    属性面板   : f00268
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
import sys
import types
from pathlib import Path

import cv2
import numpy as np

PERCEPTION = Path(
    PLUGIN_DIR / 'autopilot' / 'perception')
FRAMES = Path(RECOG_ROOT / 'raw' / 'frames' / 'batch_20260929b')

WORLD = ["f00280", "f00100", "f00005", "f00001"]
MAP = ["f00250", "f00160", "f00064", "f00155"]
PANEL = ["f00268"]


def _load_rois():
    pkg = types.ModuleType("apkg")
    pkg.__path__ = [str(PERCEPTION)]
    sys.modules["apkg"] = pkg
    for name in ("paths", "roi_loader"):
        spec = importlib.util.spec_from_file_location(
            f"apkg.{name}", PERCEPTION / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"apkg.{name}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["apkg.roi_loader"].load_rois()


def metrics(img: np.ndarray, minimap_rect) -> dict[str, float]:
    h, w = img.shape[:2]
    out: dict[str, float] = {}

    # ── 候选 A：minimap 圆度（用户设想的判据）─────────────────────
    mm = minimap_rect.crop(img[:, :, :3])
    gray = cv2.cvtColor(mm, cv2.COLOR_BGR2GRAY)
    gray = cv2.medianBlur(gray, 5)
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=30,
                               param1=80, param2=28,
                               minRadius=int(min(mm.shape[:2]) * 0.25),
                               maxRadius=int(min(mm.shape[:2]) * 0.49))
    out["A_hough_n"] = 0.0 if circles is None else float(len(circles[0]))

    # ── 候选 B：顶部 20% 是不是"天空"（高亮 + 偏蓝）─────────────
    top = img[: int(h * 0.20), :, :3]
    hsv = cv2.cvtColor(top, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    # 天空：偏蓝(H 90~130)、中等饱和、高明度
    blue = ((hue >= 90) & (hue <= 130) & (sat > 30) & (val > 120))
    out["B_sky_frac"] = float(blue.mean())
    out["B_top_bright"] = float((val > 150).mean())
    out["B_top_sat"] = float(sat.mean())

    # ── 候选 C：整帧绿色主导（地图的标志性颜色）──────────────────
    full_hsv = cv2.cvtColor(img, cv3.COLOR_BGR2HSV) if False else \
        cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    g = full_hsv[:, :, 1] > 60
    fh = full_hsv[:, :, 0]
    green = (fh >= 35) & (fh <= 85)
    out["C_green_frac"] = float((g & green).mean())

    # ── 候选 D：右上角角色血条区是否在（最稳的 UI 锚点）──────────
    # 原神角色血条在屏幕右侧中部。这里用"是否存在一条窄的红色横条"当粗判据。
    strip = img[int(h * 0.42):int(h * 0.47), int(w * 0.90):int(w * 0.99), :3]
    b, g2, r = strip[:, :, 0], strip[:, :, 1], strip[:, :, 2]
    out["D_redbar"] = float(((r > 150) & (r > g2 + 40) & (r > b + 40)).mean())

    # ── 候选 E：全帧梯度能量（地图线条多，世界画面纹理少/多）────────
    gr = cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)
    out["E_edge"] = float(cv2.Laplacian(gr, cv2.CV_64F).var())
    out["F_sat_mean"] = float(full_hsv[:, :, 1].mean())
    out["G_val_mean"] = float(full_hsv[:, :, 2].mean())
    return out


def main() -> None:
    rois = _load_rois()
    mm = rois["minimap"]
    groups = [("世界", WORLD), ("地图", MAP), ("面板", PANEL)]
    keys: list[str] = []

    print(f"{'帧':<10}{'标签':<6}" + "".join(f"{k:>14}" for k in (
        "A_hough_n", "B_sky_frac", "B_top_bright", "C_green_frac",
        "D_redbar", "E_edge", "F_sat_mean")))
    for label, ids in groups:
        for fid in ids:
            img = cv2.imread(str(FRAMES / f"{fid}.jpg"), cv2.IMREAD_COLOR)
            if img is None:
                continue
            m = metrics(img, mm)
            keys = list(m)
            print(f"{fid:<10}{label:<6}" + "".join(
                f"{m[k]:>14.4f}" for k in (
                    "A_hough_n", "B_sky_frac", "B_top_bright",
                    "C_green_frac", "D_redbar", "E_edge", "F_sat_mean")))

    print()
    print("判别方向解读：")
    print("  A_hough_n  高=找到圆（用户设想）  -> 看看世界帧和地图帧是否分得开")
    print("  B_sky_frac 高=有天空           -> 世界帧应当显著更高")
    print("  C_green_frac 高=大片绿色       -> 地图帧应当显著更高")
    print("  D_redbar   高=有角色血条       -> 世界帧应当显著更高")


if __name__ == "__main__":
    main()
