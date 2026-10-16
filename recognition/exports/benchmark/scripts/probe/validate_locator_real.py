"""任务 A/B · 真实帧验证 + 缩放档语义判定。"""

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
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

PERC = (Path(PLUGIN_DIR.parent)
        / "genshin_game_buddy" / "autopilot" / "perception")
RECOG = Path(RECOG_ROOT)
DA = RECOG / "raw" / "frames" / "session_20261001_a"
DAT = RECOG / "exports" / "benchmark" / "datasets"


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


ml = _load("minimap_locator", PERC / "minimap_locator.py")
cl = _load("chunk_loader", PERC / "chunk_loader.py")
pl = _load("pyramid_loader", PERC / "pyramid_loader.py")
ct = _load("neko_coord_transform",
           RECOG / "exports" / "benchmark" / "scripts" / "misc"
           / "coord_transform.py")

CH = cl.ChunkLoader(cache_size=24)
PY = pl.PyramidLoader(preload=True)
print(f"块缓存 {CH._cache_size}  金字塔 L1-L4 常驻  底图 {CH.width}x{CH.height}")


def run(fid, tag):
    im = cv2.imread(str(DA / f"{fid}.jpg"))
    if im is None:
        return None
    out = {}
    for nm, hs in (("field", "field"), ("city", "city")):
        t = time.perf_counter()
        r = ml.locate_minimap(im, hint_scale=hs, chunk_loader=CH,
                              pyramid=PY, coord_transform=ct)
        out[nm] = r
    return out


def main():
    # 5 帧大世界段（避开段 7 t=65.6~74.1 过渡态）
    picks = [("f00200", 20.0), ("f00400", 40.0), ("f00600", 60.0),
             ("f01000", 100.0), ("f01400", 140.0)]
    rows = []
    print("=" * 96)
    print("任务 A · 真实帧双档验证")
    print("=" * 96)
    for fid, ts in picks:
        r = run(fid, ts)
        if r is None:
            print(f"  {fid} 读不到")
            continue
        f, c = r["field"], r["city"]
        win = abs(f.confidence - c.confidence)
        print(f"\n  {fid}  (t={ts:.1f}s)")
        for nm, x in (("FIELD", f), ("CITY ", c)):
            w = (f"{x.world[0]:.0f},{x.world[1]:.0f}"
                 if x.world else "—")
            print(f"    {nm}  score={x.confidence:+.4f}  world=({w:>18})  "
                  f"{x.elapsed_ms:6.0f} ms  {x.note[:40]}")
        print(f"    两档差 {win:.4f}  ->  "
              f"{'FIELD 更像' if f.confidence > c.confidence else 'CITY 更像'}"
              f"   {'(都低于 0.98)' if max(f.confidence, c.confidence) < 0.98 else ''}")
        rows.append({"frame_id": fid, "timestamp_sec": ts,
                     "field_score": round(f.confidence, 4),
                     "field_world_x": round(f.world[0], 1) if f.world else "",
                     "field_world_z": round(f.world[1], 1) if f.world else "",
                     "field_ms": round(f.elapsed_ms, 1),
                     "city_score": round(c.confidence, 4),
                     "city_world_x": round(c.world[0], 1) if c.world else "",
                     "city_world_z": round(c.world[1], 1) if c.world else "",
                     "city_ms": round(c.elapsed_ms, 1),
                     "score_gap": round(win, 4)})

    with (DAT / "locator_session_a.csv").open(
            "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"\n  -> locator_session_a.csv  {len(rows)} 行")

    fs = [r["field_score"] for r in rows]
    cs = [r["city_score"] for r in rows]
    fm = [r["field_ms"] for r in rows]
    print("\n" + "=" * 96)
    print("任务 B · 缩放档语义")
    print("=" * 96)
    print(f"  FIELD 分数 {[round(v,3) for v in fs]}  均值 {np.mean(fs):.3f}")
    print(f"  CITY  分数 {[round(v,3) for v in cs]}  均值 {np.mean(cs):.3f}")
    print(f"  FIELD 胜 {sum(1 for a,b in zip(fs,cs) if a>b)}/{len(rows)} 帧")
    print(f"  >=0.98 的比例: FIELD {sum(1 for v in fs if v>=0.98)/len(fs):.0%}"
          f"   CITY {sum(1 for v in cs if v>=0.98)/len(cs):.0%}")
    print(f"  耗时 FIELD 均 {np.mean(fm):.0f} ms  最大 {max(fm):.0f} ms"
          f"  (L1 预算 100ms)")

    # ---- T4 一致性 ----
    print("\n" + "=" * 96)
    print("任务 E · 连续 5 帧一致性")
    print("=" * 96)
    seq = [f"f0020{i}" for i in range(0, 5)]
    ws = []
    for fid in seq:
        im = cv2.imread(str(DA / f"{fid}.jpg"))
        if im is None:
            continue
        r = ml.locate_minimap(im, hint_scale="field", chunk_loader=CH,
                              pyramid=PY, coord_transform=ct)
        if r.world:
            ws.append((fid, r.world[0], r.world[1], r.confidence))
    for fid, x, z, sc in ws:
        print(f"    {fid}  ({x:9.1f}, {z:9.1f})  score={sc:.4f}")
    if len(ws) >= 2:
        xs = [w[1] for w in ws]
        zs = [w[2] for w in ws]
        spread = float(np.hypot(max(xs) - min(xs), max(zs) - min(zs)))
        ok = spread < 10.0
        print(f"  5 帧坐标散布 {spread:.1f} 米  期望 <10  "
              f"{'✅' if ok else '❌'}")
    else:
        print("  样本不足")


if __name__ == "__main__":
    main()
