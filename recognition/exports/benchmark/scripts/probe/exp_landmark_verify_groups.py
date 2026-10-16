"""验证 3 失败复盘 · 组 B 的标签到底对不对？

AUC=0.668，组 B 中位 0.932（判据要求 <=0.5）。
两种可能，**必须分开**，否则又要犯"没有负对照的判据"的错：

  假阴性 1：组 B 帧**其实有锚点**，只是 blob 的 HSV 阈值没覆盖到
             -> 标签错，AUC 0.668 不能说明 NCC 无效
  假阴性 2：组 B 帧**确实没有锚点**，NCC 在背景上乱高分
             -> 标签对，NCC 确实无效

本脚本从两组各抽帧，画「小地图 + NCC 匹配框 + blob 框」，供人眼裁决。
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

import csv
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
DA = RECOG / "raw" / "frames" / "session_20261001_a"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" \
    / "landmark"
FIG.mkdir(parents=True, exist_ok=True)


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


mm = _load("minimap_mask", PERC / "minimap_mask.py")
cfg = mm.load_circle_cfg()
ids = sorted(DA.glob("*.jpg"))
rows = list(csv.DictReader(
    (FIG / "landmark_scores.csv").open(encoding="utf-8-sig", newline="")))

im0 = cv2.imread(str(ids[199]))
_x, _y, _, _ = mm.minimap_rect(im0, cfg)
CCX = float(cfg["cx"]) * 2304 - _x
CCY = float(cfg["cy"]) * 1440 - _y
TX, TY, TW, TH = 60, 117, 26, 30
TPL = im0
crop0, _m0 = mm.make_minimap_roi(im0, cfg)
TPL_G = cv2.cvtColor(crop0[TY - TH // 2:TY + TH // 2,
                          TX - TW // 2:TX + TW // 2],
                     cv2.COLOR_BGR2GRAY).astype(np.float32)


def analyse(i):
    im = cv2.imread(str(ids[i - 1]))
    crop, mask = mm.make_minimap_roi(im, cfg)
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32)
    res = cv2.matchTemplate(g, TPL_G, cv2.TM_CCOEFF_NORMED)
    rh, rw = res.shape
    valid = np.where(mask[:rh, :rw] > 0, res, -1.0).astype(np.float32)
    yy, xx = np.ogrid[:rh, :rw]
    valid[(xx + TW // 2 - CCX) ** 2 + (yy + TH // 2 - CCY) ** 2 < 32 ** 2] = -1
    k = int(np.argmax(valid))
    yy0, xx0 = divmod(k, rw)
    px, py = xx0 + TW // 2, yy0 + TH // 2
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    Hh, Ss, Vv = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    sel = (((Hh >= 100) & (Hh <= 150) & (Ss > 80) & (Vv > 60)
            & (mask > 0)).astype(np.uint8))
    sel = cv2.morphologyEx(sel, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, st, ct = cv2.connectedComponentsWithStats(sel, 8)
    bl = []
    for j in range(1, n):
        x, y, w, h, a = st[j]
        if 40 <= a <= 500 and 8 <= math.sqrt(w * h) <= 26 and \
                0.6 <= w / max(h, 1) <= 1.7 and \
                math.hypot(ct[j][0] - CCX, ct[j][1] - CCY) >= 25:
            bl.append((float(ct[j][0]), float(ct[j][1]), int(w), int(h)))
    return crop, float(valid[yy0, xx0]), (px, py), bl


# 抽组 A / 组 B 各 4 帧
A = [r for r in rows if r["group"] == "A"]
B = [r for r in rows if r["group"] == "B"]
A.sort(key=lambda r: -float(r["ncc_score"]))
B.sort(key=lambda r: float(r["ncc_score"]))
pickA = A[::max(1, len(A) // 4)][:4]
pickB = B[::max(1, len(B) // 4)][:4]

for gname, pick in (("A", pickA), ("B", pickB)):
    tiles = []
    for r in pick:
        i = int(r["frame"].split(":f")[1])
        crop, sc, (px, py), bl = analyse(i)
        v = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        # NCC 匹配框（红）
        cv2.rectangle(v, ((px - TW // 2) * 2, (py - TH // 2) * 2),
                      ((px + TW // 2) * 2, (py + TH // 2) * 2),
                      (0, 0, 255), 2)
        # blob（绿）
        for (bx, by, bw, bh) in bl:
            cv2.rectangle(v, (int((bx - bw / 2) * 2), int((by - bh / 2) * 2)),
                          (int((bx + bw / 2) * 2), int((by + bh / 2) * 2)),
                          (0, 255, 0), 2)
        cv2.circle(v, (int(CCX * 2), int(CCY * 2)), 8, (255, 0, 255), 2)
        bar = np.zeros((46, v.shape[1], 3), np.uint8)
        cv2.putText(bar, f"{r['frame']}  ncc={sc:.3f}", (6, 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        cv2.putText(bar, f"blobs={len(bl)}  red=NCC green=blob", (6, 38),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1)
        tiles.append(np.vstack([bar, v]))
    while len(tiles) % 2:
        tiles.append(np.zeros_like(tiles[0]))
    grid = np.vstack([np.hstack(tiles[i:i + 2]) for i in range(0, len(tiles), 2)])
    hdr = np.zeros((30, grid.shape[1], 3), np.uint8)
    cv2.putText(hdr, f"GROUP {gname}  ({'blob detected' if gname=='A' else 'no blob'})"
                     f"  -  look with your own eyes whether an anchor icon exists",
                (6, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                (0, 255, 255) if gname == "A" else (255, 160, 160), 1)
    p = FIG / f"verify_group_{gname}.png"
    cv2.imwrite(str(p), np.vstack([hdr, grid]))
    print(f"  -> {p.name}  {grid.shape}")

print("\n  组 B 各帧明细（分数升序前 12）:")
for r in B[:12]:
    print(f"    {r['frame']}  ncc={r['ncc_score']}  blobs={r['n_blobs']}  "
          f"match=({r['match_x']},{r['match_y']})  r={r['r_from_center']}")
print("  === DONE ===")
