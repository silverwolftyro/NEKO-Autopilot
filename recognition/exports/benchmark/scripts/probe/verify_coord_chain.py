"""坐标系统串联验证：coord_transform 公式 × chunks_index 块划分。

★ 链路
----
anchors.json(手工像素) -> tp.json(世界坐标) -> coord_transform(反算像素)
                                              -> 对比手工值
                                              -> 在 L4 / L0块 / 原图上裁
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
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image, ImageDraw

Image.MAX_IMAGE_PIXELS = None

RECOG = Path(RECOG_ROOT)
MISC = RECOG / "exports" / "benchmark" / "scripts" / "misc"
MAPS = RECOG / "data" / "mihoyo_maps"
ANCH = MAPS / "teyvat_P0.png.anchors.json"
TP = RECOG / "data" / "_ref_tp_sample.json"
OUT = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe"


def _load(name, p):
    s = importlib.util.spec_from_file_location(name, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[name] = m
    s.loader.exec_module(m)
    return m


ct = _load("coord_transform", MISC / "coord_transform.py")


def norm(s):
    s = str(s or "").strip()
    for a, b in (("七天神仙", "七天神像"), ("安绕", "安饶")):
        s = s.replace(a, b)
    for ch in "，、－—· ":
        s = s.replace(ch, "-")
    return s.strip("-")


def area_key(label):
    import re
    return re.sub(r"(传送锚点|传送点|锚点)$", "",
                  re.sub(r"^.{0,4}神像[-·]?", "", norm(label))).strip("-")


def find_world(label, pts):
    key = area_key(label)
    best = None
    for p in pts:
        nm, ar = norm(p["name"]), [norm(a) for a in p["areas"]]
        hit = any(key == a or key in a for a in ar)
        nmhit = key in nm
        if not (hit or nmhit):
            continue
        sc = (2 if hit else 0) + (1 if nmhit else 0)
        if best is None or sc > best[0]:
            best = (sc, p)
    return best[1] if best else None


def main():
    doc = json.loads(ANCH.read_text(encoding="utf-8"))
    W, H = doc["image_size"]
    idx = json.loads((MAPS / "P0_pyramid" / "chunks_index.json")
                     .read_text(encoding="utf-8"))
    tp_raw = json.loads(TP.read_text(encoding="utf-8-sig"))
    pts = [{"name": p["name"], "areas": p.get("areas") or [],
            "wx": p["position"][0], "wz": p["position"][2]}
           for r in tp_raw["data"] for p in r.get("points", [])]

    # 挑 3 个地理分散的锚点
    want = ["七天神像-安绕之野", "七天神像-枫丹廷区", "风神像-星落湖"]
    amap = {a["name"]: a for a in doc["points"]}
    picks = [w for w in want if w in amap]

    print("=" * 84)
    print(f"底图 {W} x {H}   块 {idx['chunk_size']}   网格 {idx['grid']}")
    print("=" * 84)
    rows = []
    for name in picks:
        a = amap[name]
        p = find_world(name, pts)
        if p is None:
            print(f"  {name:<20}  ✗ tp.json 里找不到")
            continue
        px, py = ct.world_to_pixel(p["wx"], p["wz"])
        back = ct.pixel_to_world(px, py)
        ex, ey = px - a["x"], py - a["y"]
        rows.append((name, a["x"], a["y"], px, py, ex, ey, p))
        print(f"  {name}")
        print(f"    tp 点      {p['name']}  areas={p['areas']}  id={p.get('id')}")
        print(f"    世界坐标   ({p['wx']:.2f}, {p['wz']:.2f})")
        print(f"    手工像素   ({a['x']}, {a['y']})")
        print(f"    反算像素   ({px:.1f}, {py:.1f})")
        print(f"    误差       dx={ex:+.1f}  dy={ey:+.1f}  "
              f"|r|={np.hypot(ex, ey):.1f} px")
        print(f"    round-trip ({back[0]:.4f}, {back[1]:.4f})  "
              f"（应回到 {p['wx']:.2f}, {p['wz']:.2f}）")

    if not rows:
        print("没有可用锚点")
        return
    worst = max(np.hypot(r[5], r[6]) for r in rows)
    print(f"\n  最大误差 {worst:.1f} px   期望 < 30  -> "
          f"{'✅ 通过' if worst < 30 else '❌ 超标'}")

    # ---------- B / C / D ----------
    base = None
    l4 = None
    print("\n" + "=" * 84)
    print("裁图")
    print("=" * 84)
    CW, CH = 340, 340
    sheet = Image.new("RGB", (CW * 3 + 20, 60 + len(rows) * (CH + 54)),
                      (18, 18, 24))
    sd = ImageDraw.Draw(sheet)
    sd.text((8, 10), "coord_transform x chunks_index cross-check   "
                     "(left: L0 chunk 256px | mid: L4 64px x4 | "
                     "right: original 512px + red dot)", fill=(255, 255, 255))

    import cv2
    for i, (name, ax, ay, px, py, ex, ey, p) in enumerate(rows):
        if base is None:
            with Image.open(MAPS / "teyvat_P0.png") as im:
                base = im.convert("L")
            with Image.open(MAPS / "P0_pyramid" / "L4_full.png") as im:
                l4 = im.convert("L")
        y0 = 60 + i * (CH + 54)

        # C: L0 块
        ci = next((c for c in idx["chunks"]
                   if c["bbox"][0] <= px < c["bbox"][2]
                   and c["bbox"][1] <= py < c["bbox"][3]), None)
        with Image.open(MAPS / "P0_pyramid" / ci["file"]) as im:
            ch = im.convert("L")
        lx = int(px - ci["bbox"][0]) - 128
        ly = int(py - ci["bbox"][1]) - 128
        c0 = ch.crop((max(0, lx), max(0, ly), max(0, lx) + 256,
                      max(0, ly) + 256))
        c0 = c0.resize((CW, CH), Image.Resampling.NEAREST)

        # B: L4
        s4 = 1.0 / 16.0
        c1 = l4.crop((int(px * s4) - 32, int(py * s4) - 32,
                      int(px * s4) + 32, int(py * s4) + 32))
        c1 = c1.resize((CW, CH), Image.Resampling.NEAREST)

        # D: 原图 512 + 红点
        c2 = base.crop((int(px) - 256, int(py) - 256,
                        int(px) + 256, int(py) + 256)).convert("RGB")
        c2 = c2.resize((CW, CH), Image.Resampling.LANCZOS)
        d2 = ImageDraw.Draw(c2)
        d2.ellipse([CW//2-9, CH//2-9, CW//2+9, CH//2+9], outline=(255, 0, 0),
                   width=3)
        d2.line([CW//2-22, CH//2, CW//2+22, CH//2], fill=(255, 0, 0), width=2)
        d2.line([CW//2, CH//2-22, CW//2, CH//2+22], fill=(255, 0, 0), width=2)

        for j, (t, tag) in enumerate(((c0, f"L0 {ci['id']} 256px"),
                                      (c1, "L4 64px x4"),
                                      (c2, "original 512px + dot"))):
            sheet.paste(t, (6 + j * CW, y0))
            d = ImageDraw.Draw(sheet)
            d.text((10 + j * CW, y0 + CH + 4), tag, fill=(180, 180, 195))
            d.text((10 + j * CW, y0 + CH + 18),
                   f"err dx={ex:+.1f} dy={ey:+.1f}", fill=(255, 210, 0))
        d = ImageDraw.Draw(sheet)
        d.text((6, y0 + CH + 34),
               f"{name}  |  world=({p['wx']:.0f}, {p['wz']:.0f})  "
               f"pixel=({px:.0f}, {py:.0f})  hand=({ax}, {ay})",
               fill=(0, 255, 200))

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "coord_chain_check.png"
    sheet.save(p)
    print(f"  -> {p.name}  {sheet.size}   {len(rows)} 个锚点 x 3 格")


if __name__ == "__main__":
    main()
