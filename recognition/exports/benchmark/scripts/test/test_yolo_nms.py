"""``perception/yolo.py`` 的 NMS 回归测试（§22 修的 blocker bug）。

★ 为什么这些测试存在
--------------------
``_nms`` 曾经用 ``boxes[rest, 0]`` 这种 numpy 风格索引去取一个 **Python
list** 的列 -> ``TypeError: list indices must be integers or slices, not tuple``。

因为 0/1 个框时 :func:`_nms` 会提前 return，**单框和空框永远测不出来**。
而 :meth:`YoloDetector.detect` 又把异常吞掉返回空列表，所以线上表现为
「检出 ≥2 个物体的那一帧，结果莫名其妙是空的」。

所以测试必须**显式构造 ≥2 个框**，否则修没修都过。
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

import numpy as np

ROOT = Path(PLUGIN_DIR)
sys.path.insert(0, str(ROOT))

from autopilot.perception import yolo as Y      # noqa: E402

D = Y.Detection
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f"   {detail}" if detail else ""))


def mk(specs):
    """specs = [(label, (x,y,w,h), conf), ...]"""
    dets = [D(l, b, c) for l, b, c in specs]
    boxes = [b for _, b, _ in specs]
    scores = [c for _, _, c in specs]
    return dets, boxes, scores


print("=" * 92)
print("T1 · _nms 边界")
print("=" * 92)

r = Y._nms([], [], [], 0.5)
check("空列表 -> 空", r == [], str(r))

r = Y._nms(*mk([("a", (0, 0, 10, 10), 0.9)]), 0.5)
check("单框 -> 原样返回（提前 return）", len(r) == 1 and r[0].box == (0, 0, 10, 10))

print("\n" + "=" * 92)
print("T2 · 重叠抑制（曾经必崩的路径）")
print("=" * 92)

r = Y._nms(*mk([
    ("a", (0, 0, 100, 100), 0.9),
    ("a", (5, 5, 100, 100), 0.8),
    ("a", (7, 7, 100, 100), 0.7),
]), 0.5)
check("3 个高度重叠 -> 合成 1 个", len(r) == 1, str([d.box for d in r]))
check("保留的是分数最高的那个", r[0].conf == 0.9, str(r[0].conf))

r = Y._nms(*mk([
    ("ore", (100, 100, 40, 40), 0.9),
    ("ore", (105, 102, 40, 40), 0.85),
    ("chest", (108, 101, 40, 40), 0.8),
]), 0.5)
check("同类抑制 + 异类保留 -> 2 个", len(r) == 2, str([(d.label, d.conf) for d in r]))

r = Y._nms(*mk([
    ("a", (0, 0, 10, 10), 0.9),
    ("b", (100, 100, 10, 10), 0.8),
    ("c", (300, 300, 10, 10), 0.7),
]), 0.5)
check("3 个不重叠 -> 全保留", len(r) == 3, str(len(r)))
check("按置信度降序", [d.conf for d in r] == [0.9, 0.8, 0.7], str([d.conf for d in r]))

# 覆盖 while 的多次迭代：3 个连续被最高分那个抑制 + 1 个远处的幸存
r = Y._nms(*mk([
    ("a", (0, 0, 100, 100), 0.9),
    ("a", (5, 5, 100, 100), 0.85),
    ("a", (10, 10, 100, 100), 0.8),
    ("a", (500, 500, 100, 100), 0.7),
]), 0.5)
check("级联抑制 -> 2 个（远处那个幸存）", len(r) == 2,
      str([(d.box, d.conf) for d in r]))
check("幸存的是分数最高与最低那两个", [d.conf for d in r] == [0.9, 0.7],
      str([d.conf for d in r]))

r = Y._nms(*mk([
    ("a", (0, 0, 100, 100), 0.9),
    ("a", (1, 1, 100, 100), 0.8),
    ("a", (2, 2, 100, 100), 0.85),
]), 0.5)
check("抑制后剩下的分数更高的那个接手", len(r) == 1 and r[0].conf == 0.9,
      str([d.conf for d in r]))

print("\n" + "=" * 92)
print("T3 · _postprocess 端到端（synthetic ONNX 输出）")
print("=" * 92)

NAMES = ["health_bar", "enemy_identify", "drops", "ore",
         "unmined_ore", "chest", "locked_chest"]


def synth(rows, n_attrs=None):
    """rows = [(cx, cy, w, h, {cls_idx: score}), ...] -> [1, 4+nc, N]"""
    n = n_attrs or (4 + len(NAMES))
    out = np.zeros((1, n, len(rows)), dtype=np.float32)
    for i, (cx, cy, w, h, sc) in enumerate(rows):
        out[0, :4, i] = [cx, cy, w, h]
        for k, v in sc.items():
            out[0, 4 + k, i] = v
    return out


# 同一物体被 3 个锚点各命中一次（bgi_world 实测就是这个形态）
out = synth([(320, 320, 100, 100, {1: 0.9}),
             (323, 321, 100, 100, {1: 0.8}),
             (325, 322, 100, 100, {1: 0.7})])
r = Y._postprocess(out, 1.0, (0, 0), (720, 1280), NAMES, 0.35, 0.5)
check("3 个重合框 -> _postprocess 合成 1 个", len(r) == 1, str(len(r)))
check("保留分数最高的", r and r[0].label == "enemy_identify", str(r[0].label if r else None))

# 不同类别、不同位置
out = synth([(320, 320, 60, 60, {5: 0.9}),
             (700, 400, 80, 80, {3: 0.8})])
r = Y._postprocess(out, 1.0, (0, 0), (720, 1280), NAMES, 0.35, 0.5)
check("2 个不同实体 -> 都保留", len(r) == 2, str(len(r)))
check("类别正确", sorted(d.label for d in r) == ["chest", "ore"],
      str(sorted(d.label for d in r)))

# 低置信度全过滤
out = synth([(320, 320, 60, 60, {5: 0.10})])
r = Y._postprocess(out, 1.0, (0, 0), (720, 1280), NAMES, 0.35, 0.5)
check("低于阈值 -> 空", r == [], str(r))

# 空输出
r = Y._postprocess(np.zeros((1, 11, 0), np.float32), 1.0, (0, 0),
                   (720, 1280), NAMES, 0.35, 0.5)
check("N=0 -> 空", r == [], str(r))

print("\n" + "=" * 92)
print("T4 · end2end 分支（[1, N, 6]）")
print("=" * 92)
e2e = np.array([[10, 20, 110, 120, 0.9, 5],
                [200, 300, 240, 340, 0.8, 3]], dtype=np.float32)
r = Y._postprocess(e2e, 1.0, (0, 0), (720, 1280), NAMES, 0.35, 0.5)
check("end2end 解析出 2 个", len(r) == 2, str(len(r)))
check("end2end box = (x, y, w, h)",
      r[0].box == (10, 20, 100, 100), str(r[0].box))

print("\n" + "=" * 92)
print("T5 · provider 探测（§23）")
print("=" * 92)

import onnxruntime as ort  # noqa: E402

from autopilot.perception.yolo import (                    # noqa: E402
    PROVIDER_PREFERENCE, YoloDetector, available_providers,
)

check("PROVIDER_PREFERENCE 按性能降序",
      PROVIDER_PREFERENCE.index("TensorrtExecutionProvider")
      < PROVIDER_PREFERENCE.index("CUDAExecutionProvider")
      < PROVIDER_PREFERENCE.index("CPUExecutionProvider"),
      " -> ".join(p.replace("ExecutionProvider", "") for p in PROVIDER_PREFERENCE))

cands = available_providers()
check("available_providers() 非空且以 CPU 收尾（永远有兜底）",
      bool(cands) and cands[-1] == "CPUExecutionProvider", str(cands))
check("available_providers() 的每个项都在 ORT 声明清单里",
      all(p in ort.get_available_providers() for p in cands),
      str(ort.get_available_providers()))

# ★ 核心：声明可用 ≠ 真能用。装了 onnxruntime-gpu 但缺 CUDA 运行时时，
#   ort.get_available_providers() 会列 CUDA，而 session 只加载到 CPU。
declared = ort.get_available_providers()
actual_loaded: list[str] = []
try:
    so = ort.SessionOptions()
    _s = ort.InferenceSession(RECOG_ROOT / 'models' / 'pretrained' / 'bgi_world.onnx',
                              sess_options=so,
                              providers=[p for p in PROVIDER_PREFERENCE
                                         if p in declared])
    actual_loaded = list(_s.get_providers())
except Exception as exc:  # noqa: BLE001
    actual_loaded = [f"<session failed: {type(exc).__name__}>"]

print(f"      ORT 声明: {declared}")
print(f"      实际加载: {actual_loaded}")
if "CUDAExecutionProvider" in declared and \
        "CUDAExecutionProvider" not in actual_loaded:
    print("      → 本机就是这个陷阱：声明有 CUDA，实际静默回退 CPU")
    check("get_available_providers() 会撒谎（已复现，测试必须用 current_provider）",
          True, "声明有 CUDA 但实际只有 " + ",".join(actual_loaded))
else:
    check("当前机器 CUDA 真的可用或完全没声明", True, str(actual_loaded))

# YoloDetector 的真值判据
names = open(RECOG_ROOT / 'models' / 'pretrained' / 'classes_bgi_world.txt',
             encoding="utf-8").read().split()
det = YoloDetector(RECOG_ROOT / 'models' / 'pretrained' / 'bgi_world.onnx', names)
check("current_provider() 与 session 一致",
      det.current_provider() == det.providers[0], det.current_provider())
check("current_provider() 属于声明清单（不会凭空造）",
      det.current_provider() in ort.get_available_providers(), det.current_provider())
check("is_gpu 与 current_provider 自洽",
      det.is_gpu == (det.current_provider() not in
                     ("CPUExecutionProvider", "AzureExecutionProvider")),
      f"is_gpu={det.is_gpu} provider={det.current_provider()}")
check("fallback_reason() 非空 <=> 没走上 GPU",
      bool(det.fallback_reason()) != det.is_gpu,
      f"is_gpu={det.is_gpu} reason={det.fallback_reason()[:50]}")
check("providers 属性非空", bool(det.providers), str(det.providers))
det.close()

print("\n" + "=" * 92)
print(f"结果: {len(PASS)}/{len(PASS) + len(FAIL)} 通过")
if FAIL:
    print("  失败: " + ", ".join(FAIL))
print("=" * 92)
sys.exit(1 if FAIL else 0)
