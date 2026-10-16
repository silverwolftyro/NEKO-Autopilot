"""任务 B · session_a 时序去抖标定 + 任务 A.6 · session_b OCR 命中率。

★ 时间戳
--------
用户规则：第 N 张 jpg（f00001 = 第 1 张）对应 t = N/10 秒。
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

import csv
import importlib.util
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RECOG = Path(RECOG_ROOT)
PERC = (RECOG.parent / "N.E.K.O-main" / "N.E.K.O-main" / "plugin" / "plugins"
        / "genshin_game_buddy" / "autopilot" / "perception")
DA = RECOG / "raw" / "frames" / "session_20261001_a"
DB = RECOG / "raw" / "frames" / "session_20261001_b"
DAT = RECOG / "exports" / "benchmark" / "datasets"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe"
FPS = 10.0


def _load(name, path):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s)
    sys.modules[name] = m
    s.loader.exec_module(m)
    return m


sd = _load("scene_detector", PERC / "scene_detector.py")
to = _load("teleport_ocr", PERC / "teleport_ocr.py")
tree = sd._tree()


def frame_ts(idx):
    """第 idx+1 张 -> (idx+1)/10 秒。"""
    return (idx + 1) / FPS


# ============================================ 任务 B
def task_b():
    ids = sorted(p for p in DA.glob("*.jpg"))
    print(f"[B1] session_a 共 {len(ids)} 帧  "
          f"时长 {len(ids)/FPS:.1f}s @ {FPS}fps")
    rows = []
    for i, p in enumerate(ids):
        im = cv2.imread(str(p))
        if im is None:
            continue
        edge, ms = tree.hp_bar_features(im)
        raw = bool(edge > tree.EDGE_DENSITY_MAX and ms > tree.MEAN_S_MAX)
        rows.append({"frame_id": p.stem, "timestamp_sec": round(frame_ts(i), 2),
                     "raw_prediction": "world" if raw else "non-world",
                     "edge_density": round(edge, 5), "mean_s": round(ms, 2)})
        if (i + 1) % 250 == 0:
            print(f"    {i+1}/{len(ids)}")
    out = DAT / "session_a_predictions.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["frame_id", "timestamp_sec",
                                           "raw_prediction", "edge_density",
                                           "mean_s"])
        w.writeheader()
        w.writerows(rows)
    nw = sum(1 for r in rows if r["raw_prediction"] == "world")
    print(f"  -> {out.name}  {len(rows)} 行")
    print(f"  raw 分布: 大世界 {nw} ({nw/len(rows):.1%})  "
          f"非大世界 {len(rows)-nw} ({(len(rows)-nw)/len(rows):.1%})")

    # 游程
    raws = [r["raw_prediction"] == "world" for r in rows]
    runs, cur, st = [], raws[0], 0
    for i, v in enumerate(raws):
        if v != cur:
            runs.append((cur, st, i - 1))
            cur, st = v, i
    runs.append((cur, st, len(raws) - 1))
    print("\n  raw 游程（秒）:")
    for v, s, e in runs:
        d = (e - s + 1) / FPS
        if d >= 0.3:
            print(f"    {'世界' if v else '非世界':<5} {d:>6.1f}s  "
                  f"t={frame_ts(s):>6.1f}~{frame_ts(e):>6.1f}  "
                  f"{rows[s]['frame_id']}..{rows[e]['frame_id']}")

    # ---- N/M 扫描 ----
    print("\n[B2] N/M 参数扫描")
    print(f"  {'N(s)':>5}{'M(s)':>6}{'切换数':>7}   切换时刻(秒)")
    print("  " + "-" * 74)
    best = None
    for N in (1.0, 1.5, 2.0, 2.5, 3.0):
        for M in (0.3, 0.5, 1.0):
            box = {"i": 0}

            def clf(_f, box=box):
                v = raws[box["i"]]
                box["i"] += 1
                return v
            det = sd.SceneDetector(n_seconds=N, m_seconds=M, classifier=clf)
            chg, stab, t = [], [], 0.0
            for v in raws:
                t = frame_ts(len(stab))
                st_ = det.update(None, t)
                stab.append(st_.is_world)
                if st_.changed:
                    chg.append(t)
            # 与 raw 的一致率（raw 是"真相"的最佳代理）
            agree = sum(1 for a, b in zip(raws, stab) if a == b) / len(raws)
            print(f"  {N:>5.1f}{M:>6.1f}{len(chg):>7}   "
                  f"{[round(c,1) for c in chg][:10]}"
                  f"{'  一致率 %.3f' % agree if best is None else ''}")
            if best is None or agree > best[0]:
                best = (agree, N, M, len(chg), list(chg))
    print(f"\n  与 raw 一致率最高: N={best[1]}s M={best[2]}s "
          f"切换 {best[3]} 次  一致率 {best[0]:.4f}")

    # ---- 时间线图 ----
    recs = []
    for N, M in ((1.0, 0.5), (2.0, 0.5), (3.0, 1.0)):
        box = {"i": 0}

        def clf(_f, box=box):
            v = raws[box["i"]]
            box["i"] += 1
            return v
        det = sd.SceneDetector(n_seconds=N, m_seconds=M, classifier=clf)
        st_list, chg = [], []
        for i in range(len(raws)):
            s = det.update(None, frame_ts(i))
            st_list.append(s.is_world)
            if s.changed:
                chg.append((i, frame_ts(i), s.is_world))
        recs.append((N, M, st_list, chg))

    t = [r["timestamp_sec"] for r in rows]
    fig, ax = plt.subplots(figsize=(22, 7))
    ax.step(t, [1 if v else 0 for v in raws], where="post", lw=1.0,
            alpha=.7, label="raw (single-frame)", color="#888888")
    for k, (N, M, stl, chg) in enumerate(recs):
        col = ["#cc3333", "#3388cc", "#33aa55"][k]
        ax.step(t, [1 if v else 0 for v in stl], where="post", lw=1.8,
                color=col, label=f"stabilized N={N}s M={M}s")
        for i, ts, isw in chg:
            ax.plot(ts, 1 if isw else 0, "o", color=col, ms=6, zorder=5)
    ax.set_ylim(-.25, 1.45)
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["non-world", "world"])
    ax.set_xlabel("time (s)   [frame fNNNNN = t = NNNN.N s]")
    ax.set_title(f"session_a scene debounce   {len(rows)} frames @ {FPS:.0f}fps   "
                 f"raw: world {sum(raws)} / non-world {len(raws)-sum(raws)}"
                 f"   red/blue/green dots = state switch", fontsize=11)
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(alpha=.3)
    step = max(1, len(t) // 50)
    ax.set_xticks(t[::step])
    ax.set_xticklabels([rows[i]["frame_id"] for i in range(0, len(rows), step)],
                       rotation=90, fontsize=6)
    fig.tight_layout()
    FIG.mkdir(parents=True, exist_ok=True)
    p = FIG / "session_a_timeline.png"
    fig.savefig(p, dpi=110)
    plt.close(fig)
    print(f"\n[B3] 时间线图 -> {p.name}")
    return rows


# ============================================ 任务 A.6
def task_a6():
    ids = sorted(DB.glob("*.jpg"))[:20]
    print(f"\n[A.6] session_b 抽 {len(ids)} 帧跑 OCR")
    if not ids:
        print("  !! session_b 没帧")
        return
    hit = 0
    tried = 0
    for p in ids:
        im = cv2.imread(str(p))
        if im is None:
            continue
        tried += 1
        try:
            region = to.crop_info_box(im)
        except ValueError as exc:
            print(f"  {p.stem}  裁剪失败 {exc}")
            continue
        lines = to.ocr_region(region)
        r = None
        if len([x for x in lines if x.strip()]) >= 2:
            texts = sorted((to._norm(x) for x in lines if x.strip()),
                           key=len)
            r = to.match_teleport(texts[0], texts[-1])
        if r is not None:
            hit += 1
        print(f"  {p.stem}  OCR行数={len(lines):>2}  "
              f"-> {r.matched_id if r else 'None'}")
    print(f"  命中率 {hit}/{tried}")


if __name__ == "__main__":
    task_b()
    task_a6()
    print("=== DONE ===")
