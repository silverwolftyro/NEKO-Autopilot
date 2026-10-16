"""关键判定：BetterGI 的 Left/Top 与米游社瓦片索引是否同一坐标系。

只读、只统计数值范围，不复制任何分区数据。
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

import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BGI = Path(_pl.Path(_os.environ.get("NEKO_BETTERGI_DIR", "").strip() or "BetterGI") / 'I' / 'Assets' / 'Map' / 'Teyvat')
FILES = ["city_info.json", "city_6_0_info.json",
         "layer_map_info.json", "layer_map_6_0_info.json"]

#: 已知瓦片网格（来自 mihoyo_meta.json + 实际文件数）
TILE = 256
PX_W, PX_H = 144 * TILE, 72 * TILE        # 36864 x 18432
TILE_X, TILE_Y = 144, 72

allrows = []
for name in FILES:
    p = BGI / name
    if not p.is_file():
        continue
    rows = json.loads(p.read_text(encoding="utf-8-sig"))
    for r in rows:
        r["__src"] = name
        allrows.append(r)

print("=" * 78)
print(f"合并 {len(allrows)} 个图层，来自 {len(FILES)} 个文件")
print()
print(f"{'文件':<24} {'n':>4} {'Left 范围':>26} {'Top 范围':>26} {'Scale':>8}")
print("-" * 92)
for name in FILES:
    rs = [r for r in allrows if r["__src"] == name]
    if not rs:
        continue
    L = [float(r["Left"]) for r in rs]
    T = [float(r["Top"]) for r in rs]
    sc = sorted({r["Scale"] for r in rs})
    print(f"{name:<24} {len(rs):>4} "
          f"{min(L):>11.1f} .. {max(L):>10.1f} "
          f"{min(T):>11.1f} .. {max(T):>10.1f} {str(sc):>8}")

L = [float(r["Left"]) for r in allrows]
T = [float(r["Top"]) for r in allrows]
print()
print("=" * 78)
print("全量范围")
print(f"  Left : {min(L):>12.1f} .. {max(L):>10.1f}")
print(f"  Top  : {min(T):>12.1f} .. {max(T):>10.1f}")
print(f"  Scale: {dict(Counter(r['Scale'] for r in allrows))}")
print(f"  Floor: {dict(Counter(r['Floor'] for r in allrows))}")

print()
print("=" * 78)
print("与瓦片网格比对")
print(f"  瓦片网格      : {TILE_X} x {TILE_Y} 片 = "
      f"{PX_W} x {PX_H} 像素 (每片 {TILE})")
print(f"  Left 落在瓦片像素域 [0,{PX_W}] ?  "
      f"{'是' if min(L) >= 0 and max(L) <= PX_W else '否（有负值或越界）'}")
print(f"  Top  落在瓦片像素域 [0,{PX_H}] ?  "
      f"{'是' if min(T) >= 0 and max(T) <= PX_H else '否（有负值或越界）'}")
for s in sorted({r["Scale"] for r in allrows}):
    sub = [r for r in allrows if r["Scale"] == s]
    sl = [float(r["Left"]) for r in sub]
    st = [float(r["Top"]) for r in sub]
    print(f"  Scale={s}: n={len(sub):>4}  "
          f"Left {min(sl):>10.1f}..{max(sl):>9.1f}  "
          f"Top {min(st):>10.1f}..{max(st):>9.1f}")

print()
print("  负值图层数: "
      f"{sum(1 for r in allrows if float(r['Left']) < 0 or float(r['Top']) < 0)}"
      f" / {len(allrows)}")

print()
print("=" * 78)
print("分组统计（按 LayerId 前缀）")
pref = Counter()
for r in allrows:
    lid = str(r["LayerId"])
    p = "".join(ch for ch in lid.split("_")[0] if ch.isalpha())
    pref[p] += 1
print("  " + "  ".join(f"{k}={v}" for k, v in pref.most_common(12)))

print()
print("  LayerGroupId 出现情况:",
      f"{len({r.get('LayerGroupId') for r in allrows if 'LayerGroupId' in r})} 个唯一")
grp = Counter(r.get("LayerGroupId") for r in allrows
              if r.get("LayerGroupId"))
print("  同一 LayerGroupId 最多含", max(grp.values()), "个楼层")

print()
print("=== DONE ===")
