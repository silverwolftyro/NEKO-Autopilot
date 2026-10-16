"""bgi_world.onnx 推理耗时基准（§23）。

用途：为「世界实体检测能不能进 200 ms tick」这个决定提供**实测**数据，
而不是靠估。

跑法::

    python bench_world_entities.py            # 全量 batch_a + batch_b
    python bench_world_entities.py --limit 30 # 快速抽样

⚠️ **CUDA 需要额外装 CUDA/cuDNN 运行时**（onnxruntime-gpu 本身**不含**
   这些 DLL）。没有它们时 ORT 会**静默回退 CPU 且不抛异常** ——
   所以本脚本用 ``session.get_providers()``（实际加载了什么）而不是
   ``ort.get_available_providers()``（编译期清单，会撒谎）来判定。
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

import argparse
import statistics
import sys

# ---- console encoding ----------------------------------------------
# chcp 65001 only changes the console code page; Python still writes bytes
# in its own encoding (cp936/GBK on Chinese Windows), so every Chinese
# string in this script comes out as mojibake. Force both streams UTF-8.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):   # redirected to StringIO -> no method
        _s.reconfigure(encoding="utf-8", errors="replace")
import time
from pathlib import Path

sys.path.insert(0, PLUGIN_DIR)

import cv2
import numpy as np

from autopilot.perception.world_entities import (
    BGI_WORLD_CLASSES, BGI_WORLD_ONNX, WorldEntityDetector,
)

BATCHES = {
    "batch_a": Path(RECOG_ROOT / 'raw' / 'frames' / 'batch_20260929a'),
    "batch_b": Path(RECOG_ROOT / 'raw' / 'frames' / 'batch_20260929b'),
}
#: tick 预算。core/runtime.py:138 的 tick_rate_hz 默认 5.0 -> 200 ms
TICK_BUDGET_MS = 200.0


def imread(p: Path):
    # 中文路径 cv2.imread 会失败，必须走 fromfile + imdecode
    return cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)


def pct(sorted_vals, q):
    if not sorted_vals:
        return 0.0
    return sorted_vals[min(len(sorted_vals) - 1, int(q * len(sorted_vals)))]


def bench(det: WorldEntityDetector, paths, label: str) -> dict:
    ts, counts = [], []
    for p in paths:
        img = imread(p)
        if img is None:
            continue
        det.detect(img)                      # 预热该分辨率
        t = time.perf_counter()
        ents = det.detect(img)
        ts.append((time.perf_counter() - t) * 1000)
        counts.append(len(ents))
    s = sorted(ts)
    r = {
        "label": label, "n": len(s),
        "min": s[0] if s else 0.0,
        "p50": statistics.median(s) if s else 0.0,
        "p90": pct(s, 0.90), "max": s[-1] if s else 0.0,
        "mean": statistics.mean(s) if s else 0.0,
        "hits": sum(counts),
    }
    print(f"\n[{label}]  n={r['n']}  检出实体 {r['hits']} 个")
    print(f"  min {r['min']:.0f} | p50 {r['p50']:.0f} | p90 {r['p90']:.0f} "
          f"| max {r['max']:.0f} | mean {r['mean']:.0f} ms")
    print(f"  p50 占 {TICK_BUDGET_MS:.0f}ms tick 预算的 "
          f"{r['p50'] / TICK_BUDGET_MS * 100:.0f}%")
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="每个批次最多测多少帧（0 = 全部）")
    args = ap.parse_args()

    if not BGI_WORLD_ONNX.is_file():
        print(f"缺模型: {BGI_WORLD_ONNX}")
        return 1

    det = WorldEntityDetector(timeout_ms=1e9)     # 关超时，纯测耗时
    det.load()
    print(f"模型      : {BGI_WORLD_ONNX.name}")
    print(f"类名      : {BGI_WORLD_CLASSES.name}")
    print(f"请求的 EP : {det.load()._requested_providers}")
    print(f"★实际 EP  : {det.load().current_provider()}  "
          f"(GPU={det.load().is_gpu})")
    if det.load().fallback_reason():
        print(f"★回退原因 : {det.load().fallback_reason()}")
    det.warmup()

    results = []
    for tag, base in BATCHES.items():
        if not base.is_dir():
            print(f"\n跳过 {tag}: {base} 不存在")
            continue
        paths = sorted(base.glob("*.jpg"))
        if args.limit:
            paths = paths[:args.limit]
        if paths:
            results.append(bench(det, paths, tag))

    if len(results) >= 1:
        allt = []
        for tag, base in BATCHES.items():
            ps = sorted(base.glob("*.jpg"))
            if args.limit:
                ps = ps[:args.limit]
            for p in ps:
                img = imread(p)
                if img is not None:
                    det.detect(img)
                    allt.append(det.last_ms)
        s = sorted(allt)
        print("\n" + "=" * 78)
        print(f"合计 n={len(s)}  p50 {statistics.median(s):.0f}  "
              f"p90 {pct(s, 0.9):.0f}  max {s[-1]:.0f} ms")
        print(f"p50 / 200ms tick = {statistics.median(s) / TICK_BUDGET_MS * 100:.0f}%")
        print("结论：见 perception/world_entities.py 的 WORLD_ENTITY_TIMEOUT_MS 注释")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
