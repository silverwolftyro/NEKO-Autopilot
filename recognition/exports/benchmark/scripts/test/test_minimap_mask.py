"""minimap_mask 单元测试 + 5 帧验证拼图。"""

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
OUT = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe"

s = importlib.util.spec_from_file_location("minimap_mask",
                                          PERC / "minimap_mask.py")
mm = importlib.util.module_from_spec(s)
sys.modules["minimap_mask"] = mm
s.loader.exec_module(mm)

CFG = mm.load_circle_cfg()
print(f"配置: cx={CFG['cx']} cy={CFG['cy']} "
      f"r={CFG['r_norm_by_width']}  ref={CFG['ref_width']}x{CFG['ref_height']}")


# ------------------------------------------------ 单测
def t1_shape():
    f = np.zeros((720, 1280, 3), np.uint8)
    m = mm.make_minimap_mask(f)
    ok = m.shape == f.shape[:2] and m.dtype == np.uint8
    print(f"\n[T1] 形状一致  mask{m.shape} vs frame{f.shape[:2]}  "
          f"dtype={m.dtype}  -> {'PASS' if ok else 'FAIL'}")
    return ok


def t2_center():
    f = np.zeros((720, 1280, 3), np.uint8)
    m = mm.make_minimap_mask(f)
    h, w = m.shape
    cx, cy = int(CFG["cx"] * w), int(CFG["cy"] * h)
    ok = m[cy, cx] == 255
    print(f"\n[T2] 圆心为 255  中心({cx},{cy})={m[cy, cx]}  "
          f"-> {'PASS' if ok else 'FAIL'}")
    return ok


def t3_corners():
    f = np.zeros((720, 1280, 3), np.uint8)
    m = mm.make_minimap_mask(f)
    h, w = m.shape
    corners = [m[0, 0], m[0, -1], m[-1, 0], m[-1, -1]]
    ok = all(c == 0 for c in corners)
    print(f"\n[T3] 四角为 0  {corners}  -> {'PASS' if ok else 'FAIL'}")
    return ok


def t4_scale():
    """不同分辨率下，圆心/半径应等比例缩放。"""
    res = [(1280, 720), (1152, 720), (2560, 1600), (2304, 1440)]
    print(f"\n[T4] 分辨率缩放")
    base = None
    ok = True
    for w, h in res:
        f = np.zeros((h, w, 3), np.uint8)
        m = mm.make_minimap_mask(f)
        ys, xs = np.nonzero(m)
        cx, cy = xs.mean(), ys.mean()
        # ★ mask 是 0/255，sum() 带了 255 倍，必须除回来
        area_px = m.sum() / 255.0
        r_eff = float(np.sqrt(area_px / np.pi))
        print(f"    {w}x{h}: 中心=({cx:.1f},{cy:.1f}) "
              f"理论=({CFG['cx']*w:.1f},{CFG['cy']*h:.1f})   "
              f"有效半径={r_eff:.1f}  理论={CFG['r_norm_by_width']*w:.1f}")
        if abs(cx - CFG["cx"] * w) > 1.5 or abs(cy - CFG["cy"] * h) > 1.5:
            ok = False
        if abs(r_eff - CFG["r_norm_by_width"] * w) > 1.5:
            ok = False
        frac = area_px / (h * w)
        theo = np.pi * (CFG["r_norm_by_width"] * w) ** 2 / (h * w)
        print(f"              面积占比 {frac:.4%}  理论 {theo:.4%}")
        if abs(frac - theo) / theo > 0.02:
            ok = False
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


# ------------------------------------------------ 验证拼图
def montage(n=5):
    da = RECOG / "raw" / "frames" / "session_20261001_a"
    b = RECOG / "raw" / "frames" / "batch_20260929b"
    picks = []
    if da.is_dir():
        ids = sorted(da.glob("*.jpg"))
        cand = [p for p in ids if 176 <= int(p.stem[1:]) <= 292]
        step = max(1, len(cand) // n)
        picks = cand[::step][:n]
    if not picks:
        ids = sorted(b.glob("*.jpg"))
        picks = [ids[i] for i in range(0, len(ids), max(1, len(ids) // n))][:n]
    print(f"\n验证帧 {len(picks)}: {[p.stem for p in picks]}")
    PADS = [0, 3, 5]
    CW, CH = 300, 300
    sheet = np.full((40 + len(picks) * (CH + 30), CW * len(PADS) + 20, 3),
                    24, np.uint8)
    for i, p in enumerate(picks):
        im = cv2.imread(str(p))
        y0r = 40 + i * (CH + 30)
        for j, pad in enumerate(PADS):
            crop, m = mm.make_minimap_roi(im, CFG, pad=pad)
            applied = cv2.bitwise_and(crop, crop, mask=m)
            shown = crop.copy()
            cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(shown, cnts, -1, (0, 255, 255), 2)
            tiles = [shown, applied] if len(PADS) == 2 else [shown, applied]
            tile = tiles[0] if len(PADS) == 3 else tiles[1]
            t = cv2.resize(tile, (CW, CH), interpolation=cv2.INTER_NEAREST)
            x = 6 + j * CW
            sheet[y0r:y0r + CH, x:x + CW] = t
            cv2.putText(sheet, f"pad={pad}", (x + 4, y0r - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
            cv2.putText(sheet, p.stem, (x + CW - 86, y0r + CH - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 230, 255), 1,
                        lineType=cv2.LINE_AA)
    cv2.putText(sheet, "minimap circle  cx=%.5f cy=%.5f r(w)=%.5f  "
                "yellow contour = mask boundary  (default pad=%d)"
                % (CFG["cx"], CFG["cy"], CFG["r_norm_by_width"],
                   mm.DEFAULT_ROI_PAD),
                (6, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "minimap_circle_check.png"
    cv2.imwrite(str(p), sheet)
    print(f"  -> {p.name}  {sheet.shape}   对比 pad = {PADS}")


if __name__ == "__main__":
    print("=" * 70)
    print("minimap_mask 单元测试")
    print("=" * 70)
    res = [("T1 形状一致", t1_shape()),
           ("T2 圆心255", t2_center()),
           ("T3 四角0", t3_corners()),
           ("T4 分辨率缩放", t4_scale())]
    print("\n" + "=" * 70)
    allok = True
    for nm, ok in res:
        print(f"{nm:<18}{'PASS' if ok else 'FAIL':>8}")
        allok &= ok
    print("=" * 70)
    montage()
    print("=== DONE ===")
