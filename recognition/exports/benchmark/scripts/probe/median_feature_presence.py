"""时域中值 + 逐特征 NCC —— 只跑目视确认过的分组。

★ 存在性判据
------------
对每个 ROI、每帧，算它与"该组中值图同位置"的归一化互相关（NCC）。
  NCC 高 -> 该 ROI 是**静止 UI**（每帧都一样）
  NCC 低 -> 该 ROI 内容在变（世界背景 / 动画）
这就是"这个 UI 元素在该界面下是否存在"的量化判据。

★ 分组来源
----------
全部来自我**逐格目视**过的 contact_01 / contact_02，
不是自动分类（自动分类已证明不可用）。
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

import cv2
import numpy as np
from PIL import Image, ImageDraw

RECOG = Path(RECOG_ROOT)
BATCH = {"a": RECOG / "raw" / "frames" / "batch_20260929a",
         "b": RECOG / "raw" / "frames" / "batch_20260929b"}
OUT = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe" / "median_by_scene"
W, H = 2304, 1440

#: 候选特征（归一化）—— 两个"待测"位置也一并纳入
FEATS = {
    "minimap":   [0.0299, 0.0116, 0.1464, 0.1911],
    "hp_bar":    [0.4201, 0.9368, 0.5807, 0.9472],
    "skill_bar": [0.8620, 0.8880, 0.9680, 0.9780],
    "party_1":   [0.9258, 0.2972, 0.9670, 0.3576],
    "party_2":   [0.9253, 0.3694, 0.9670, 0.4313],
    "party_3":   [0.9253, 0.4500, 0.9666, 0.5111],
    "party_4":   [0.9201, 0.5167, 0.9583, 0.5799],
    "avatar_guess": [0.0000, 0.0100, 0.0600, 0.0600],
    "paimon_guess": [0.0000, 0.2000, 0.0600, 0.9000],
}

#: 界面分组（全部目视确认，来自 contact_01/02）
GROUPS = {
    "world":    ["a:f00001", "a:f00005", "a:f00009", "a:f00011",
                 "a:f00030", "a:f00045", "a:f00049", "b:f00167", "b:f00209"],
    "paimon":   ["a:f00051", "a:f00055", "a:f00060", "a:f00065",
                 "a:f00079", "a:f00085"],
    "bag":      ["a:f00014", "a:f00020", "a:f00026"],
    "map":      ["a:f00031", "a:f00036", "a:f00041"],
    "artifact": ["a:f00057"],
    "loading":  ["a:f00067", "a:f00069", "a:f00070"],
    "menu_paimon_other": ["b:f00279"],
}


def load(fid):
    tag, stem = fid.split(":")
    p = BATCH[tag] / f"{stem}.jpg"
    if not p.is_file():
        return None
    return cv2.imread(str(p))


def gray(fid):
    im = load(fid)
    return None if im is None else cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)


def ncc(a, b):
    a = a.astype(np.float32).ravel()
    b = b.astype(np.float32).ravel()
    a -= a.mean()
    b -= b.mean()
    d = (np.linalg.norm(a) * np.linalg.norm(b))
    return 0.0 if d < 1e-6 else float(a.dot(b) / d)


def crop(g, r):
    y0, y1 = int(r[1] * H), max(int(r[3] * H), int(r[1] * H) + 2)
    x0, x1 = int(r[0] * W), max(int(r[2] * W), int(r[0] * W) + 2)
    return g[y0:y1, x0:x1]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    names = list(FEATS)
    print("特征 × 界面 存在性表   （数值 = 与组中值的 NCC，>0.6 判为【静止UI存在】）")
    print("=" * 108)
    print(f"  {'界面':<18}{'n':>3}  " + "".join(f"{n[:10]:>11}" for n in names))
    print("  " + "-" * 104)
    table = {}
    for gname, fids in GROUPS.items():
        gs = [gray(f) for f in fids]
        gs = [g for g in gs if g is not None]
        if not gs:
            continue
        med = np.median(np.stack(gs), axis=0).astype(np.uint8)
        row = {}
        for n in names:
            r = FEATS[n]
            m = crop(med, r)
            vals = [ncc(crop(g, r), m) for g in gs]
            row[n] = float(np.mean(vals))
        table[gname] = row
        print(f"  {gname:<18}{len(gs):>3}  " +
              "".join(f"{row[n]:>11.2f}" for n in names))
        # 存中值图（缩略 + 画框）
        small = cv2.resize(med, (1152, 720), interpolation=cv2.INTER_AREA)
        pil = Image.fromarray(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
        d = ImageDraw.Draw(pil)
        for n, r in FEATS.items():
            x0, y0 = r[0] * 1152, r[1] * 720
            x1, y1 = r[2] * 1152, r[3] * 720
            hit = row[n] > 0.6
            d.rectangle([x0, y0, x1, y1],
                        outline=(0, 255, 0) if hit else (255, 80, 80), width=2)
        ids = ",".join(fids[:4]) + ("..." if len(fids) > 4 else "")
        d.text((6, 6), f"{gname}  n={len(gs)}  [{ids}]",
               fill=(255, 255, 0))
        d.text((6, 24), "green=静止UI存在  red=内容在变", fill=(200, 200, 200))
        p = OUT / f"median_{gname}.png"
        pil.save(p)
    print("\n中值图目录:", OUT)

    # 大世界独有性
    print("\n" + "=" * 108)
    print("大世界独有性分析：world 通过 且 所有非 world 组至少一个不通过")
    print("-" * 108)
    for n in names:
        w = table.get("world", {}).get(n, 0)
        others = {g: table[g][n] for g in table if g != "world"}
        fail = [g for g, v in others.items() if v <= 0.6]
        exclusive = w > 0.6 and len(fail) == len(others)
        print(f"  {n:<14} world={w:>6.2f}  "
              f"非world未通过 {len(fail)}/{len(others)}  "
              f"{'✅ 独有' if exclusive else ''}")
        if fail:
            print(f"       未通过: {fail}")
    # ---- 跨界面判别力：拿 world 的中值 ROI 当模板，看别组像不像 ----
    print("\n" + "=" * 108)
    print("跨界面判别力（这才是要找的东西）")
    print("-" * 108)
    print("  模板 = world 组该 ROI 的中值。数值 = 其它组同位置的 NCC。")
    print("  NCC 低 = 该 ROI 在别的界面长得不一样 -> 有判别力")
    print()
    med_all = {}
    for gname, fids in GROUPS.items():
        gs = [gray(f) for f in fids]
        gs = [g for g in gs if g is not None]
        if gs:
            med_all[gname] = np.median(np.stack(gs), axis=0).astype(np.uint8)
    if "world" in med_all:
        wm = med_all["world"]
        others = [g for g in med_all if g != "world" and len(GROUPS[g]) >= 3]
        print(f"  {'特征':<14}" + "".join(f"{g[:12]:>14}" for g in others)
              + f"{'最低':>8}{'判别':>8}")
        print("  " + "-" * 96)
        disc = []
        for n in names:
            r = FEATS[n]
            t = crop(wm, r)
            vals = {g: ncc(crop(med_all[g], r), t) for g in others}
            lo = min(vals.values()) if vals else 1.0
            avg = sum(vals.values()) / len(vals) if vals else 1.0
            disc.append((n, vals, lo, avg))
            print(f"  {n:<14}" + "".join(f"{vals[g]:>14.2f}" for g in others)
                  + f"{lo:>8.2f}{'★' if lo < 0.5 else '':>8}")
        print("\n  判别力排序（最低 NCC 越小越有判别力）:")
        for n, vals, lo, avg in sorted(disc, key=lambda t: t[2]):
            print(f"    {n:<14} 最低={lo:>6.2f}  平均={avg:>6.2f}"
                  f"  {'✅ 强判别' if lo < 0.4 else ('🟡 中等' if lo < 0.6 else '❌ 无判别力')}")
    print("\n=== DONE ===")


if __name__ == "__main__":
    main()
