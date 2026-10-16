"""排查：哪些大世界帧的技能栏会消失 / 变淡？"""

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

import cv2
import numpy as np
from PIL import Image, ImageDraw

RECOG = Path(RECOG_ROOT)
FB = RECOG / "raw" / "frames" / "batch_20260929b"
FA = RECOG / "raw" / "frames" / "batch_20260929a"
OUT = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe"
W, H = 2304, 1440

#: 技能栏 ROI
SB = (0.8620, 0.8880, 0.9680, 0.9780)
HB = (0.4201, 0.9368, 0.5807, 0.9472)
MM = (0.0299, 0.0116, 0.1464, 0.1911)

ALL = ["f00167", "f00168", "f00172", "f00174", "f00203", "f00205",
       "f00209", "f00245", "f00263", "f00267", "f00278", "f00279"]


def load(fid):
    for d in (FB, FA):
        p = d / f"{fid}.jpg"
        if p.is_file():
            im = cv2.imread(str(p))
            if im is not None:
                return im
    return None


def stats(fid):
    im = load(fid)
    if im is None:
        return None
    out = {}
    for nm, r in (("skill", SB), ("hp", HB), ("minimap", MM)):
        c = im[int(r[1] * H):int(r[3] * H), int(r[0] * W):int(r[2] * W)]
        hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
        out[nm] = {
            "V_mean": float(hsv[:, :, 2].mean()),
            "V_p95": float(np.percentile(hsv[:, :, 2], 95)),
            "S_mean": float(hsv[:, :, 1].mean()),
            "bright_frac": float((hsv[:, :, 2] > 170).mean()),
        }
    return out


def main():
    print(f"{'帧':<9}{'技能栏 V均值':>12}{'V95':>8}{'亮占比':>9}"
          f"   {'血条 V均值':>11}{'亮占比':>9}   {'小地图 V95':>10}{'亮占比':>9}")
    print("-" * 88)
    rows = []
    for fid in ALL:
        s = stats(fid)
        if s is None:
            continue
        rows.append((fid, s))
        sk, hp, mm = s["skill"], s["hp"], s["minimap"]
        flag = "  <<< 技能栏异常" if sk["bright_frac"] < 0.05 else ""
        print(f"{fid:<9}{sk['V_mean']:>12.1f}{sk['V_p95']:>8.0f}"
              f"{sk['bright_frac']:>9.3f}   {hp['V_mean']:>11.1f}"
              f"{hp['bright_frac']:>9.3f}   {mm['V_p95']:>10.0f}"
              f"{mm['bright_frac']:>9.3f}{flag}")

    # 出图：4 个可疑帧的整帧缩略
    sus = ["f00168", "f00245", "f00263", "f00279"]
    tiles = []
    for fid in sus:
        im = load(fid)
        if im is None:
            continue
        im = cv2.resize(im, (768, 480), interpolation=cv2.INTER_AREA)
        pil = Image.fromarray(cv2.cvtColor(im, cv2.COLOR_BGR2RGB))
        d = ImageDraw.Draw(pil)
        d.text((6, 4), fid, fill=(255, 220, 0))
        d.rectangle([SB[0] * 768, SB[1] * 480, SB[2] * 768, SB[3] * 480],
                    outline=(0, 255, 0), width=2)
        tiles.append(pil)
    if tiles:
        sheet = Image.new("RGB", (768 * 2, 480 * 2), (24, 24, 30))
        for i, t in enumerate(tiles[:4]):
            sheet.paste(t, ((i % 2) * 768, (i // 2) * 480))
        p = OUT / "suspect_frames.png"
        sheet.save(p)
        print(f"\n可疑帧拼图 -> {p.name}  {sheet.size}")
    print("=== DONE ===")


if __name__ == "__main__":
    main()
