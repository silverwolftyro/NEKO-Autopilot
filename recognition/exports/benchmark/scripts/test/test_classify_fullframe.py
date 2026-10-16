"""验证 classify_eb_main 的**整帧入口**。

上一轮 296 帧的分类结果是走 ``classify_features(特征)`` 算的 —— 特征直接
读 CSV，没有重新裁图。所以 ``classify_eb_main(整帧)`` 这条真正的入口
**从未被执行过**。本脚本专门补这个验证。

★ 为什么结果必然能对上
----------------------
``eb_main_classifier`` 内部是 ``from .eb_main import extract_features``。
它解析到 ``sys.modules["apkg.detectors.eb_main"]`` —— 而本脚本加载
``apkg.detectors`` 包时**已经把那个模块注册进去了**。

也就是说整帧路径和 CSV 路径用的是**同一个模块对象、同一份函数**，
特征提取是同一段代码、同一批像素。这不是"两次独立计算碰巧一致"，
而是"同一条代码路径跑了两遍"。所以 10/10 一致是**应该**的 ——
一旦不一致，说明装载顺序或导入有别的问题，那才值得查。
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
PERCEPTION = Path(
    PLUGIN_DIR / 'autopilot' / 'perception')

CSV_CLS = BENCH / "eb_main_classify.csv"
# ★ 帧目录要写成**一个**完整字面量。早先拆成两个相邻 raw 字符串
#   （r"...\frames" + r"batch_20260929b"），Python 直接把它们连成
#   "...\framesbatch_20260929b" —— 少个反斜杠，10 帧全部 imread 失败，
#   看起来像"整帧入口全线崩溃"，其实是路径拼错。
FRAMES = Path(RECOG_ROOT / 'raw' / 'frames' / 'batch_20260929b')
SEED = 7
#: 3 ready / 5 not_ready / 2 unknown = 10
QUOTA = {"ready": 3, "not_ready": 5, "unknown": 2}


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
    # 断言：包 __init__ 已经把 eb_main 注册好了
    assert "apkg.detectors.eb_main" in sys.modules, \
        "eb_main 未注册 —— 整帧与 CSV 路径会加载到不同实例，对比失去意义"
    return load("apkg.detectors.eb_main_classifier",
                PERCEPTION / "detectors" / "eb_main_classifier.py")


def main() -> int:
    import cv2
    cls = _load()

    with open(CSV_CLS, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    by_state: dict[str, list[dict]] = {}
    for r in rows:
        by_state.setdefault(r["state"], []).append(r)

    print("=" * 92)
    print("classify_eb_main(整帧) vs CSV  对照验证")
    print("=" * 92)
    print(f"  eb_main 模块实例 : {id(sys.modules['apkg.detectors.eb_main']):#x}")
    print(f"  池子规模         : "
          + ", ".join(f"{k}={len(v)}" for k, v in sorted(by_state.items())))
    print()

    rng = np.random.default_rng(SEED)
    picked: list[dict] = []
    for state, need in QUOTA.items():
        pool = by_state.get(state, [])
        if len(pool) < need:
            print(f"  [不足] {state} 只有 {len(pool)} 帧，少抽 "
                  f"{need - len(pool)}")
        k = min(need, len(pool))
        idx = rng.choice(len(pool), k, replace=False)
        picked += [pool[int(i)] for i in idx]
    picked.sort(key=lambda r: r["frame_id"])

    hdr = (f"  {'frame_id':<10}{'CSV state':<12}{'CSV conf':>9}"
           f"{'整帧 state':<12}{'整帧 conf':>10}{'CSV ring':>10}"
           f"{'整帧 ring':>10}  一致")
    print(hdr)
    print("  " + "-" * 88)

    mismatches = []
    for r in picked:
        fid = r["frame_id"]
        img = cv2.imread(str(FRAMES / f"{fid}.jpg"), cv2.IMREAD_COLOR)
        if img is None:
            print(f"  {fid:<10}[读图失败] {FRAMES / (fid + '.jpg')}")
            mismatches.append(fid)
            continue
        state, conf = cls.classify_eb_main(img)
        # 整帧路径拿到的 ring：再走一次特征提取只为对照
        feats = sys.modules["apkg.detectors.eb_main"].extract_features(img)
        ring_ff = round(float(feats.ring_sat_delta), 4)

        csv_state = r["state"]
        csv_conf = float(r["confidence"])
        csv_ring = float(r["ring_sat_delta"])
        same = (state.value == csv_state
                and abs(conf - csv_conf) < 5e-4
                and abs(ring_ff - csv_ring) < 5e-4)
        if not same:
            mismatches.append(fid)
        print(f"  {fid:<10}{csv_state:<12}{csv_conf:>9.4f}"
              f"{state.value:<12}{conf:>10.4f}{csv_ring:>10.2f}"
              f"{ring_ff:>10.2f}  {'OK' if same else 'MISMATCH'}")

    total = len(picked)
    print()
    print("=" * 92)
    print(f"  一致性: {total - len(mismatches)} / {total}")
    if mismatches:
        print(f"  不一致的帧: {', '.join(mismatches)}")
        print("  ↓ 逐项差异（需要人工核对）")
        for fid in mismatches:
            r = next(x for x in rows if x["frame_id"] == fid)
            print(f"    {fid}")
            print(f"      CSV   : state={r['state']} "
                  f"conf={float(r['confidence']):.4f} "
                  f"ring={float(r['ring_sat_delta']):+.4f}")
            img = cv2.imread(str(FRAMES / f"{fid}.jpg"), cv2.IMREAD_COLOR)
            if img is not None:
                st, cf = cls.classify_eb_main(img)
                f = sys.modules["apkg.detectors.eb_main"].extract_features(img)
                print(f"      整帧  : state={st.value} conf={cf:.4f} "
                      f"ring={float(f.ring_sat_delta):+.4f}")
    else:
        print("  ✓ 整帧入口与 CSV 结果完全一致，"
              "说明 classify_eb_main 可以安全接入流水线。")
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
