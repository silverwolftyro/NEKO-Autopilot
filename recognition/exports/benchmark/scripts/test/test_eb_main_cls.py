"""批量验证 eb_main 三态分类器 + 抽查拼图。

用法::

    D:\\NEKO\\N.E.K.O-main\\N.E.K.O-main\\.venv\\Scripts\\python.exe ^
        D:\\NEKO\\recognition\\exports\\benchmark\\scripts\\test_eb_main_cls.py

★ 验证的核心不是"跑通"，是**和 KMeans 对表**
------------------------------------------
分类器用的三个簇心就是 KMeans 的输出，所以理论上应当复现同一批结论。
对不上就说明标准化 / 距离 / 特征顺序里有东西错了 —— 这是最有价值的检查，
比看任何单帧结果都强。
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
import sys
import types
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent
FRAMES = Path(RECOG_ROOT / 'raw' / 'frames' / 'batch_20260929b')
PERCEPTION = Path(
    PLUGIN_DIR / 'autopilot' / 'perception')

CSV_FEATURES = BENCH / "eb_main_features.csv"
CSV_CLUSTER = BENCH / "eb_main_features_clustered.csv"
OUT_CSV = BENCH / "eb_main_classify.csv"
SEED = 42


def _load():
    pkg = types.ModuleType("apkg")
    pkg.__path__ = [str(PERCEPTION)]
    sys.modules["apkg"] = pkg

    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

    load("apkg.paths", PERCEPTION / "paths.py")
    load("apkg.roi_loader", PERCEPTION / "roi_loader.py")
    load("apkg.detectors", PERCEPTION / "detectors" / "__init__.py")
    load("apkg.detectors.eb_main",
         PERCEPTION / "detectors" / "eb_main.py")
    cls = load("apkg.detectors.eb_main_classifier",
               PERCEPTION / "detectors" / "eb_main_classifier.py")
    return load("apkg.roi_loader", PERCEPTION / "roi_loader.py"), cls


def _montage(fids, confs, out: Path, title: str) -> None:
    import cv2
    from_apkg = sys.modules["apkg.roi_loader"]
    thumb, pad, strip = 128, 8, 26
    cols, rows = 5, 4
    cw = thumb + pad
    ch = thumb + pad + strip
    canvas = np.full((46 + rows * ch, cols * cw + pad, 3), 48, np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(canvas, title, (pad + 6, 32), font, 0.7,
                (235, 235, 235), 2, cv2.LINE_AA)
    for n, fid in enumerate(fids[:20]):
        img = cv2.imread(str(FRAMES / f"{fid}.jpg"), cv2.IMREAD_COLOR)
        if img is None:
            continue
        crop = from_apkg.load_rois()["eb_main"].crop(img[:, :, :3])
        if crop is None or getattr(crop, "size", 0) == 0:
            continue
        th = cv2.resize(crop, (thumb, thumb), interpolation=cv2.INTER_AREA)
        gx, gy = n % cols, n // cols
        x0, y0 = pad + gx * cw, 46 + gy * ch
        canvas[y0:y0 + thumb, x0:x0 + thumb] = th
        label = f"{fid}  {confs[fid]:.2f}"
        (tw, tht), _ = cv2.getTextSize(label, font, 0.44, 1)
        cv2.putText(canvas, label, (x0 + max(1, (thumb - tw) // 2),
                                    y0 + thumb + tht + 9),
                    font, 0.44, (215, 215, 215), 1, cv2.LINE_AA)
    cv2.imwrite(str(out), canvas)


def main() -> int:
    import cv2
    _, cls = _load()

    frames = sorted(FRAMES.glob("*.jpg"))
    print("=" * 74)
    print("eb_main 三态分类器批量验证")
    print("=" * 74)
    print(f"  遍历 {len(frames)} 帧 -> {OUT_CSV.name}")
    print()

    # 特征表是上一轮算好的，直接复用（不重跑特征提取）
    feat_by_id: dict[str, dict] = {}
    with open(CSV_FEATURES, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            feat_by_id[r["frame_id"]] = r

    rows: list[dict] = []
    for p in frames:
        fid = p.stem
        f = feat_by_id.get(fid)
        if f is None:
            continue
        # 用一个极简 shim 把 CSV 字典喂给 classify_features
        shim = type("F", (), {
            k: float(f[k]) for k in cls.FEATURES
        })()
        shim.to_dict = lambda: {k: float(f[k]) for k in cls.FEATURES}
        state, conf, ring = cls.classify_features(shim)
        rows.append({"frame_id": fid, "state": state.value,
                     "confidence": round(conf, 4),
                     "ring_sat_delta": round(ring, 4)})

    cols = ["frame_id", "state", "confidence", "ring_sat_delta"]
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    # ── 状态分布 ──────────────────────────────────────────────────
    from collections import Counter
    cnt = Counter(r["state"] for r in rows)
    print("  三态分布:")
    for s in (cls.EbMainState.READY, cls.EbMainState.NOT_READY,
              cls.EbMainState.UNKNOWN):
        print(f"    {s.value:<10}{cnt.get(s.value, 0):>6} 帧")
    print(f"    {'合计':<10}{len(rows):>6} 帧")
    print()

    by_state: dict[str, list[str]] = {}
    for r in rows:
        by_state.setdefault(r["state"], []).append(r["frame_id"])
    for k in by_state:
        by_state[k].sort()

    for name in ("ready", "unknown"):
        ids = by_state.get(name, [])
        print(f"  {name.upper()} 帧号（{len(ids)} 个）:")
        for i in range(0, len(ids), 12):
            print("   ", ", ".join(ids[i:i + 12]))
        print()

    low = sorted((r for r in rows if r["confidence"] < cls.LOW_CONFIDENCE),
                 key=lambda r: r["confidence"])
    print(f"  confidence < {cls.LOW_CONFIDENCE} 的帧（边界样本，{len(low)} 个）:")
    for r in low:
        print(f"    {r['frame_id']}  {r['state']:<10} "
              f"conf={r['confidence']:.4f}  ring={r['ring_sat_delta']:+.1f}")
    print()

    # ── 与 KMeans 对表 ────────────────────────────────────────────
    with open(CSV_CLUSTER, encoding="utf-8") as fh:
        kmeans = {r["frame_id"]: int(r["cluster_id"])
                  for r in csv.DictReader(fh)}
    print("=" * 74)
    print("与 KMeans cluster_id 对表")
    print("=" * 74)
    ref = {2: "ready", 0: "not_ready", 1: "unknown"}
    print(f"  {'KMeans簇':<12}{'参考状态':<12}{'该簇帧数':>10}"
          f"{'分类器一致':>12}")
    print("  " + "-" * 50)
    agree_all = 0
    total_all = 0
    for c in (0, 1, 2):
        ids = [r["frame_id"] for r in rows
               if kmeans.get(r["frame_id"]) == c]
        want = ref[c]
        ok = sum(1 for r in rows
                 if kmeans.get(r["frame_id"]) == c
                 and r["state"] == want)
        agree_all += ok
        total_all += len(ids)
        print(f"  cluster{c:<6}{want:<12}{len(ids):>10}{ok:>12}")
    print(f"  {'合计':<12}{'':<12}{total_all:>10}{agree_all:>12}"
          f"   ({agree_all / max(1, total_all):.1%})")

    cm = {r["frame_id"]: r for r in rows}
    r2 = [f for f, c in kmeans.items() if c == 2 and f in cm]
    ok2 = [f for f in r2 if cm[f]["state"] == "ready"]
    bad2 = [(f, cm[f]["state"], cm[f]["confidence"]) for f in r2
            if cm[f]["state"] != "ready"]
    print()
    print(f"  ★ cluster2 的 15 帧中，被判为 READY 的有 {len(ok2)} 个")
    if bad2:
        print("    未判为 READY 的：")
        for f, s, cf in sorted(bad2, key=lambda t: t[2]):
            print(f"      {f}  -> {s}  conf={cf:.4f}")
    print()

    # ── 抽查拼图 ──────────────────────────────────────────────────
    confs = {r["frame_id"]: r["confidence"] for r in rows}
    rng = np.random.default_rng(SEED)
    specs = [("ready", BENCH / "cls_ready_20.png", "READY  -  sample 20"),
             ("not_ready", BENCH / "cls_notready_20.png", "NOT_READY  -  sample 20"),
             ("unknown", BENCH / "cls_unknown_20.png", "UNKNOWN  -  sample 20")]
    for name, out, title in specs:
        pool = by_state.get(name, [])
        if len(pool) >= 20:
            pick = [pool[i] for i in
                    rng.choice(len(pool), 20, replace=False)]
        else:
            pick = pool
        _montage(sorted(pick), confs, out, f"{title}   (n={len(pool)})")
        print(f"  拼图 -> {out.name}  ({len(pick)} 张)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
