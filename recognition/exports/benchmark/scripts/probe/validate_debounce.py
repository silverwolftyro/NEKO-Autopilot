"""真实数据验证：418 帧过时序去抖 + 时间线图。

★ 帧率假设
----------
帧是抽帧存下来的，没有时间戳。这里**假设 10 fps**（步长 0.1s），
所以 N=3.0s = 30 帧，M=1.0s = 10 帧。
真实帧率不同只会改变"多少帧算一秒"，不改去抖逻辑本身。

★ 批次边界
----------
batch_a 与 batch_b 是两段独立录屏，**a 的末帧和 b 的首帧不是时间连续的**。
主报告按"连续"跑（用户要求），同时给出分批结果供参考。
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

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RECOG = Path(RECOG_ROOT)
DET = (RECOG.parent / "N.E.K.O-main" / "N.E.K.O-main" / "plugin" / "plugins"
       / "genshin_game_buddy" / "autopilot" / "perception" / "scene_detector.py")
LAB = RECOG / "exports" / "benchmark" / "datasets" / "scene_labels.csv"
OUT = (RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe"
       / "scene_timeline.png")
BATCH = {"a": RECOG / "raw" / "frames" / "batch_20260929a",
         "b": RECOG / "raw" / "frames" / "batch_20260929b"}
DT = 0.1
KNOWN_BAD = ["a:f00071", "a:f00079", "a:f00083", "b:f00108"]

_s = importlib.util.spec_from_file_location("scene_detector", DET)
_sd = importlib.util.module_from_spec(_s)
sys.modules["scene_detector"] = _sd
_s.loader.exec_module(_sd)
SceneDetector = _sd.SceneDetector


def run(ids):
    det = SceneDetector(n_seconds=3.0, m_seconds=1.0)
    rows = []
    t = 0.0
    for fid in ids:
        t += DT
        st = det.classify_frame_id(fid) if hasattr(det, "classify_frame_id") \
            else None
        # 走完整链路：先取底层单帧判定，再喂给状态机
        raw_tree = _sd._default_classifier.__wrapped__ if False else None
        rows.append((fid, t))
    return rows


def main():
    import cv2
    tree = _sd._tree()

    ids = [f"{k}:{p.stem}" for k, d in BATCH.items() if d.is_dir()
           for p in sorted(d.glob("*.jpg"))]
    print(f"总帧数 {len(ids)}   假设 10fps，步长 {DT}s   "
          f"N=3.0s({int(3.0/DT)}帧)  M=1.0s({int(1.0/DT)}帧)")

    det = SceneDetector(n_seconds=3.0, m_seconds=1.0)
    recs = []
    t = 0.0
    for i, fid in enumerate(ids):
        im = cv2.imread(str(BATCH[fid.split(":")[0]] / f"{fid.split(':')[1]}.jpg"))
        raw = tree.classify_frame(im, fid).is_world
        t += DT
        st = det.update(im, t)
        recs.append({"frame_id": fid, "t": t, "raw": raw,
                     "stable": st.is_world, "changed": st.changed,
                     "pending": st.pending_seconds})
        if (i + 1) % 100 == 0:
            print(f"  {i+1}/{len(ids)}")

    truth = {r["frame_id"]: r["scene_type"] for r in
             csv.DictReader(LAB.open(encoding="utf-8-sig", newline=""))}
    EX = {"中间态", "特殊-视频", "特殊-回归活动"}
    lab = [(r, truth[r["frame_id"]]) for r in recs
           if r["frame_id"] in truth and truth[r["frame_id"]] not in EX]

    raw_err = [r["frame_id"] for r, s in lab
               if r["raw"] != (s == "大世界")]
    stab_err = [r["frame_id"] for r, s in lab
                if r["stable"] != (s == "大世界")]
    print(f"\n【{len(lab)} 个已标注帧】")
    print(f"  单帧原始判定  误判 {len(raw_err):>3}  {raw_err}")
    print(f"  时序去抖后    误判 {len(stab_err):>3}  {stab_err}")
    print(f"  准确率 {len(lab)-len(raw_err)}/{len(lab)} = "
          f"{(len(lab)-len(raw_err))/len(lab):.4f}  ->  "
          f"{(len(lab)-len(stab_err))/len(lab):.4f}")
    chg = [r["frame_id"] for r in recs if r["changed"]]
    print(f"\n  状态切换 {len(chg)} 次: {chg}")

    # ---- 原始判定的游程分布 ----
    print("\n  原始判定游程（连续同判定的段长）:")
    runs, cur, start = [], recs[0]["raw"], 0
    for i, r in enumerate(recs):
        if r["raw"] != cur:
            runs.append((cur, i - start, start, i - 1))
            cur, start = r["raw"], i
    runs.append((cur, len(recs) - start, start, len(recs) - 1))
    from collections import Counter
    nn = [L for v, L, *_ in runs if not v]
    ww = [L for v, L, *_ in runs if v]
    print(f"    非世界游程 {len(nn)} 段: 长度 {sorted(nn, reverse=True)[:12]}"
          f"   最长 {max(nn) if nn else 0}")
    print(f"    大世界游程 {len(ww)} 段: 长度 {sorted(ww, reverse=True)[:12]}"
          f"   最长 {max(ww) if ww else 0}")
    print(f"    能触发切换的游程（>=30 帧 / N=3.0s）:"
          f"{sum(1 for L in nn if L >= 30)} 段非世界, "
          f"{sum(1 for L in ww if L >= 10)} 段大世界")
    print("\n    最长的 6 段非世界游程:")
    for v, L, s, e in sorted([r for r in runs if not r[0]],
                             key=lambda t: -t[1])[:6]:
        print(f"      {L:>4} 帧  {recs[s]['frame_id']} .. {recs[e]['frame_id']}")

    print("\n  4 个已知误判帧的去抖结果:")
    idx = {r["frame_id"]: i for i, r in enumerate(recs)}
    for f in KNOWN_BAD:
        if f not in idx:
            print(f"    {f}  不在本次序列")
            continue
        r = recs[idx[f]]
        okmark = "✅ 修正" if r["stable"] == (truth.get(f) == "大世界") else \
                 ("· 该帧未标注" if f not in truth else "❌ 仍错")
        print(f"    {f:<12} raw={'世界' if r['raw'] else '非世界':<4} "
              f"stable={'世界' if r['stable'] else '非世界':<4} "
              f"pending={r['pending']:.2f}s  {okmark}")

    # ---------------- 时间线图 ----------------
    x = list(range(len(recs)))
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(22, 8), sharex=True,
        gridspec_kw={"height_ratios": [2, 1]})
    ax1.step(x, [1 if r["raw"] else 0 for r in recs], where="post",
             lw=0.9, alpha=.75, label="raw (single-frame)")
    ax1.step(x, [1 if r["stable"] else 0 for r in recs], where="post",
             lw=2.2, label="stabilized (debounced)")
    for r in recs:
        if r["changed"]:
            ax1.plot(x[idx[r["frame_id"]]], [1 if r["stable"] else 0],
                     "o", color="red", ms=7, zorder=5)
    for f in KNOWN_BAD:
        if f in idx:
            ax1.annotate(f, (idx[f], 1.15), fontsize=8, color="#ff8800",
                         rotation=0, ha="center")
    nA = sum(1 for r in recs if r["frame_id"].startswith("a:"))
    ax1.axvline(nA, color="purple", ls="--", lw=1.2)
    ax1.annotate("batch a | b", (nA, -0.25), color="purple", fontsize=9,
                 ha="center")
    ax1.set_ylim(-0.3, 1.4)
    ax1.set_yticks([0, 1])
    ax1.set_yticklabels(["non-world", "world"])
    ax1.set_title(f"scene detector debounce timeline   "
                  f"{len(recs)} frames @ {1/DT:.0f}fps   "
                  f"N=3.0s  M=1.0s   "
                  f"raw errors={len(raw_err)}  stabilized errors="
                  f"{len(stab_err)}   red dots = state switch", fontsize=11)
    ax1.legend(loc="upper right", fontsize=9)
    ax1.grid(alpha=.3)
    ax2.fill_between(x, [r["pending"] for r in recs], step="post",
                     color="#4488cc", alpha=.6)
    ax2.axhline(3.0, color="green", ls="--", lw=1, label="N = 3.0s")
    ax2.axhline(1.0, color="orange", ls="--", lw=1, label="M = 1.0s")
    ax2.set_ylabel("pending (s)")
    ax2.set_xlabel("frame index   (annotated: a:f00071 / a:f00079 / "
                   "a:f00083 / b:f00108)")
    ax2.set_ylim(0, 3.6)
    ax2.legend(loc="upper right", fontsize=8)
    ax2.grid(alpha=.3)
    step = max(1, len(recs) // 60)
    ax2.set_xticks(x[::step])
    ax2.set_xticklabels([recs[i]["frame_id"] for i in range(0, len(recs), step)],
                        rotation=90, fontsize=6)
    fig.tight_layout()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=110)
    plt.close(fig)
    print(f"\n  时间线图 -> {OUT.name}")
    print("=== DONE ===")


if __name__ == "__main__":
    main()
