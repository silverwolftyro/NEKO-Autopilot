"""DirectML 版 bgi_world 全量扫描（§35）。

★ 为什么不用 ultralytics
-----------------------
ultralytics（AGPL-3.0，运行时禁 import）**不认 DirectML** ——
早先那批扫描（batch_a 122 帧 / batch_b 296 帧）全部跑在纯 CPU 上。
本脚本走 :mod:`perception.yolo` 的 **ONNX Runtime** 路径，直接指定 provider，
拿到 DirectML 的真实数字。

★ 报两遍
--------
``--provider both``（默认）会用**同一个模型**分别建 DirectML 和 CPU 两个
session，各扫一遍 580 帧，输出可比的 p50/p90/max。两者都跑才有对比意义。

跑法::

    python scan_bgi_world_dml.py                       # DML + CPU 各扫 580 帧
    python scan_bgi_world_dml.py --provider dml        # 只跑 DML
    python scan_bgi_world_dml.py --limit 30 --preview 5
    python scan_bgi_world_dml.py --conf 0.25

⚠️ **只读帧文件，不改游戏状态**（不按键、不动鼠标）。
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
import csv
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

# ---- 绝对路径（不依赖 cwd）-----------------------------------------------
PLUGIN_ROOT = Path(
    PLUGIN_DIR)
sys.path.insert(0, str(PLUGIN_ROOT))

MODEL = Path(RECOG_ROOT / 'models' / 'pretrained' / 'bgi_world.onnx')
CLASSES = Path(RECOG_ROOT / 'models' / 'pretrained' / 'classes_bgi_world.txt')
FRAMES = Path(RECOG_ROOT / 'raw' / 'frames' / 'combat_20261003')
OUT_DIR = Path(RECOG_ROOT / 'exports' / 'benchmark' / 'datasets')
PREVIEW_DIR = Path(RECOG_ROOT / 'exports' / 'benchmark' / 'diagnosis' / 'bgi_world_scan')

import cv2  # noqa: E402

from autopilot.perception.yolo import YoloDetector  # noqa: E402

PROVIDER_MAP = {
    "dml": ["DmlExecutionProvider", "CPUExecutionProvider"],
    "cpu": ["CPUExecutionProvider"],
}


def load_frames(limit: int = 0) -> list[Path]:
    if not FRAMES.is_dir():
        raise SystemExit(f"帧目录不存在: {FRAMES}")
    ps = sorted(FRAMES.glob("*.jpg"))
    return ps[:limit] if limit else ps


def imread(p: Path):
    # 中文路径 cv2.imread 会失败，必须走 fromfile + imdecode
    return cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)


def pct(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    return sorted_vals[min(len(sorted_vals) - 1, int(q * len(sorted_vals)))]


def scan(det: YoloDetector, frames: list[Path], conf: float,
         preview: int, tag: str) -> dict:
    """扫一遍。返回统计 + 每帧明细（供 CSV）。"""
    rows, ms_list = [], []
    class_tot = Counter()
    n_hit_frames = 0
    shown = 0

    for i, p in enumerate(frames, 1):
        img = imread(p)
        if img is None:
            rows.append({"frame": p.stem, "ms": 0.0, "n_det": 0, "classes": ""})
            continue
        hits = det.detect(img, conf=conf, timeout_ms=1e9)   # 关超时，纯测速
        ms = det.last_inference_ms
        ms_list.append(ms)
        kinds = [h.label for h in hits]
        class_tot.update(kinds)
        if hits:
            n_hit_frames += 1
        rows.append({"frame": p.stem, "ms": round(ms, 1), "n_det": len(hits),
                     "classes": "|".join(kinds)})

        # 预览：只存**有检出**的前 N 张，方便肉眼确认检出的对不对
        if preview and hits and shown < preview:
            shown += 1
            _save_preview(img, hits, PREVIEW_DIR / f"{tag}_{p.stem}.png")
            print(f"    [预览] {p.stem} -> {len(hits)} 个")

        if i % 50 == 0:
            print(f"  {tag}: {i}/{len(frames)}", flush=True)

    s = sorted(ms_list)
    return {
        "provider": det.current_provider(),
        "n_frames": len(rows),
        "n_frames_with_det": n_hit_frames,
        "total_det": sum(class_tot.values()),
        "class_dist": dict(class_tot),
        "ms": {"min": round(s[0], 1) if s else 0,
               "p50": round(statistics.median(s), 1) if s else 0,
               "p90": round(pct(s, 0.90), 1),
               "max": round(s[-1], 1) if s else 0,
               "mean": round(statistics.mean(s), 1) if s else 0},
        "rows": rows,
    }


def _save_preview(img, hits, out: Path) -> None:
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        canvas = img.copy()
        for h in hits:
            x, y, w, hh = h.box
            cv2.rectangle(canvas, (x, y), (x + w, y + hh), (0, 220, 255), 2)
            cv2.putText(canvas, f"{h.label} {h.conf:.2f}", (x, max(14, y - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 255), 1,
                        cv2.LINE_AA)
        ok, buf = cv2.imencode(".png", canvas)
        if ok:
            buf.tofile(str(out))
    except Exception as exc:  # noqa: BLE001 - 预览失败不该中断扫描
        print(f"    ⚠ 预览保存失败 {out.name}: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["dml", "cpu", "both"], default="both")
    ap.add_argument("--limit", type=int, default=0, help="只扫前 N 帧（0=全部）")
    ap.add_argument("--conf", type=float, default=0.35)
    ap.add_argument("--preview", type=int, default=3, help="存 N 张有检出的预览图")
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    if not MODEL.is_file():
        print(f"✗ 模型不存在: {MODEL}")
        return 2
    if not CLASSES.is_file():
        print(f"✗ 类名表不存在: {CLASSES}")
        return 2
    names = [ln.strip() for ln in CLASSES.read_text(encoding="utf-8").splitlines()
             if ln.strip()]

    frames = load_frames(a.limit)
    print("=" * 92)
    print("bgi_world 全量扫描（DirectML vs CPU）")
    print("=" * 92)
    import onnxruntime as ort
    print(f"  onnxruntime   : {ort.__version__}")
    print(f"  声明的 provider: {ort.get_available_providers()}")
    print(f"  模型          : {MODEL.name}")
    print(f"  类数          : {len(names)}")
    print(f"  帧目录        : {FRAMES}")
    print(f"  帧数          : {len(frames)}")
    print(f"  conf 阈值     : {a.conf}")

    todo = ["dml", "cpu"] if a.provider == "both" else [a.provider]
    results = {}
    for tag in todo:
        print(f"\n>>> 扫描 {tag.upper()} …")
        try:
            det = YoloDetector(MODEL, names, providers=PROVIDER_MAP[tag])
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {tag} session 创建失败: {type(exc).__name__}: {exc}")
            continue
        actual = det.current_provider()
        # ⚠ tag 是 "dml"/"cpu"，provider 是 "DmlExecutionProvider"/... ——
        #   直接比字符串永远不等，会误报 ⚠。按包含关系比。
        expect_ep = "Dml" if tag == "dml" else "CPU"
        got_dml = "Dml" in actual
        ok = (got_dml if tag == "dml" else not got_dml)
        print(f"  实际 provider: {actual}" + ("" if ok else f"  ⚠ 请求的是 {tag}"))
        if det.fallback_reason():
            print(f"  回退原因     : {det.fallback_reason()}")
        det.warmup()
        t0 = time.perf_counter()
        r = scan(det, frames, a.conf, a.preview, tag)
        r["wall_s"] = round(time.perf_counter() - t0, 1)
        r["requested"] = tag
        results[tag] = r
        m = r["ms"]
        print(f"  实际 provider : {r['provider']}")
        print(f"  有检出的帧    : {r['n_frames_with_det']}/{r['n_frames']}")
        print(f"  检出实体总数  : {r['total_det']}")
        print(f"  类别分布      : {r['class_dist']}")
        print(f"  单帧耗时      : min={m['min']} p50={m['p50']} "
              f"p90={m['p90']} max={m['max']} mean={m['mean']} ms")
        print(f"  总墙钟        : {r['wall_s']} s")

    # ---- 落盘 ----
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = {k: {kk: vv for kk, vv in v.items() if kk != "rows"}
               for k, v in results.items()}
    (OUT_DIR / "bgi_world_scan_dml.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if results:
        first = next(iter(results.values()))
        with (OUT_DIR / "bgi_world_scan_frames.csv").open(
                "w", newline="", encoding="utf-8-sig") as fh:
            wtr = csv.DictWriter(fh, fieldnames=["frame", "ms", "n_det", "classes"])
            wtr.writeheader()
            wtr.writerows(first["rows"])
        print(f"\n明细 CSV -> {OUT_DIR / 'bgi_world_scan_frames.csv'}")

    # ---- 对比 ----
    print("\n" + "=" * 92)
    print("耗时对比")
    print("=" * 92)
    if "dml" in results and "cpu" in results:
        d, c = results["dml"]["ms"]["p50"], results["cpu"]["ms"]["p50"]
        print(f"  CPU     p50 = {c:7.1f} ms   ({results['cpu']['provider']})")
        print(f"  DirectML p50 = {d:7.1f} ms   ({results['dml']['provider']})")
        if c > 0:
            print(f"  提速     = {c / d:.2f}x" if d > 0 else "  提速 = n/a")
        same = (results["dml"]["total_det"] == results["cpu"]["total_det"])
        print(f"  检出数一致 : {same} "
              f"(DML={results['dml']['total_det']}, CPU={results['cpu']['total_det']})"
              + ("" if same else "  ← DirectML 与 CPU 结果不一致，"
                                      "大概率是某些算子回落了"))
    else:
        for k, v in results.items():
            print(f"  {k.upper():9} p50 = {v['ms']['p50']} ms ({v['provider']})")

    print(f"\n预览图 -> {PREVIEW_DIR}")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
