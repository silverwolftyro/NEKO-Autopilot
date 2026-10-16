"""帧间隔(dt)扫描 —— 证明"去抖失效"是数据问题，不是状态机问题。

★ 为什么做这个
--------------
418 帧**没有真实时间戳**。若假设 10fps(dt=0.1s)，N=3.0s 需要**连续 30 帧**
非世界才切换。但实际非世界游程最长 55 帧、其余多在 3~18 帧之间 ——
**大多数段达不到 30 帧**，状态机就一直卡在初始状态，准确率崩到 44.74%。

这说明**帧集不是 10fps 的连续录屏**，而是被抽稀/采样过的。
换句话说：dt 假设错了，不是去抖逻辑错了。

本脚本扫描不同 dt，看哪个值能让"帧数"换算回"秒数"后语义成立。
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
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2

RECOG = Path(RECOG_ROOT)
DET = (RECOG.parent / "N.E.K.O-main" / "N.E.K.O-main" / "plugin" / "plugins"
       / "genshin_game_buddy" / "autopilot" / "perception" / "scene_detector.py")
LAB = RECOG / "exports" / "benchmark" / "datasets" / "scene_labels.csv"
BATCH = {"a": RECOG / "raw" / "frames" / "batch_20260929a",
         "b": RECOG / "raw" / "frames" / "batch_20260929b"}
EX = {"中间态", "特殊-视频", "特殊-回归活动"}

_s = importlib.util.spec_from_file_location("scene_detector", DET)
_sd = importlib.util.module_from_spec(_s)
sys.modules["scene_detector"] = _sd
_s.loader.exec_module(_sd)
SceneDetector = _sd.SceneDetector

truth = {r["frame_id"]: r["scene_type"] for r in
         csv.DictReader(LAB.open(encoding="utf-8-sig", newline=""))}
ids = [f"{k}:{p.stem}" for k, d in BATCH.items() if d.is_dir()
       for p in sorted(d.glob("*.jpg"))]

# ---- 先算一遍单帧原始判定（贵的部分只做一次）----
print(f"算 {len(ids)} 帧的单帧判定 ...")
tree = _sd._tree()
raws = []
for i, fid in enumerate(ids):
    im = cv2.imread(str(BATCH[fid.split(":")[0]] / f"{fid.split(':')[1]}.jpg"))
    raws.append(tree.classify_frame(im, fid).is_world)
    if (i + 1) % 150 == 0:
        print(f"  {i+1}/{len(ids)}")

lab = [(i, truth[f]) for i, f in enumerate(ids)
       if f in truth and truth[f] not in EX]
raw_err = sum(1 for i, s in lab if raws[i] != (s == "大世界"))
print(f"\n单帧原始判定: {len(lab)-raw_err}/{len(lab)} = "
      f"{(len(lab)-raw_err)/len(lab):.4f}   误判 {raw_err}\n")

# 游程
runs, cur, st = [], raws[0], 0
for i, v in enumerate(raws):
    if v != cur:
        runs.append((cur, i - st))
        cur, st = v, i
runs.append((cur, len(raws) - st))
nn = sorted([L for v, L in runs if not v], reverse=True)
ww = sorted([L for v, L in runs if v], reverse=True)
print(f"非世界游程 {len(nn)} 段: {nn}")
print(f"大世界游程 {len(ww)} 段: {ww}\n")

print(f"{'dt(秒/帧)':>10}{'隐含fps':>9}{'N需帧数':>9}{'M需帧数':>9}"
      f"{'切换次数':>9}{'去抖后误判':>11}{'去抖后准确率':>13}")
print("-" * 72)
for dt in (0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0):
    box = {"i": 0}

    def clf(_f, box=box):
        v = raws[box["i"]]
        box["i"] += 1
        return v

    det = SceneDetector(n_seconds=3.0, m_seconds=1.0, classifier=clf)
    stab, chg, t = [], 0, 0.0
    for v in raws:
        t += dt
        st_ = det.update(None, t)
        stab.append(st_.is_world)
        chg += 1 if st_.changed else 0
    e = sum(1 for i, s in lab if stab[i] != (s == "大世界"))
    need_n = int(-(-3.0 // dt))
    need_m = int(-(-1.0 // dt))
    print(f"{dt:>10.2f}{1/dt:>9.2f}{need_n:>9}{need_m:>9}{chg:>9}"
          f"{e:>11}{(len(lab)-e)/len(lab):>13.4f}")
print("=== DONE ===")
