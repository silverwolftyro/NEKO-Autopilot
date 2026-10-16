"""修正：① 找 session_b 里真正"信息框已显示"的帧；② 段合并阈值对齐 M。"""

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
import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
from PIL import Image, ImageDraw

RECOG = Path(RECOG_ROOT)
PERC = (RECOG.parent / "N.E.K.O-main" / "N.E.K.O-main" / "plugin" / "plugins"
        / "genshin_game_buddy" / "autopilot" / "perception")
DA = RECOG / "raw" / "frames" / "session_20261001_a"
DB = RECOG / "raw" / "frames" / "session_20261001_b"
DAT = RECOG / "exports" / "benchmark" / "datasets"
FIG = RECOG / "exports" / "benchmark" / "visualizations" / "ui_probe"
CW, CH = 480, 300
M_SECONDS = 1.0        # 与 scene_detector 的 M 对齐
CJK = re.compile(r"[\u4e00-\u9fff]")


def _load(name, path):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s)
    sys.modules[name] = m
    s.loader.exec_module(m)
    return m


to = _load("teleport_ocr", PERC / "teleport_ocr.py")


def find_info_frames(sample=80):
    """在 session_b 里抽样，找信息框已显示的帧（含 >=2 行中文）。"""
    ids = sorted(DB.glob("*.jpg"))
    print(f"session_b {len(ids)} 帧，均匀抽 {sample} 帧探测信息框")
    hits = []
    for i in range(0, len(ids), max(1, len(ids) // sample)):
        p = ids[i]
        im = cv2.imread(str(p))
        if im is None:
            continue
        try:
            region = to.crop_info_box(im)
        except ValueError:
            continue
        lines = to.ocr_region(region)
        cjk = [t for t in lines if CJK.search(t)]
        if len(cjk) >= 2:
            hits.append((p, cjk))
    print(f"  命中 {len(hits)} 帧")
    for p, cjk in hits[:12]:
        print(f"    {p.stem}  {cjk}")
    return hits


def task_a(hits, limit=20):
    print()
    print("=" * 74)
    print("任务 A · 信息框帧上的端到端验证")
    print("=" * 74)
    if not hits:
        print("  !! 没找到信息框已显示的帧")
        return 0, []
    use = hits[:limit]
    hit, rows = 0, []
    for p, _cjk in use:
        im = cv2.imread(str(p))
        region = to.crop_info_box(im)
        lines = to.ocr_region(region)
        texts = [t for t in (to._norm(x) for x in lines) if t]
        if len(texts) < 2:
            rows.append((p.stem, "OCR 行数不足", f"{len(texts)} 行"))
            continue
        texts.sort(key=len)
        r = to.match_teleport(texts[0], texts[-1])
        if r is None:
            rows.append((p.stem, "匹配失败",
                         f"main={texts[0][:20]!r} sub={texts[-1][:24]!r}"))
        else:
            hit += 1
            rows.append((p.stem, "命中",
                         f"id={r.matched_id} {r.name!r} conf={r.confidence}"))
    print(f"  命中率 {hit}/{len(use)}")
    print(f"  分布: {dict(Counter(r[1] for r in rows))}\n")
    print(f"  {'帧号':<12}{'结果':<12}明细")
    print("  " + "-" * 70)
    for fid, res, d in rows:
        print(f"  {fid:<12}{res:<12}{d[:56]}")
    return hit, rows


def _dur(rows, s, e):
    return float(rows[e]["timestamp_sec"]) - float(rows[s]["timestamp_sec"]) + 0.1


def segments():
    """从 session_a_predictions.csv 提取段。

    ★ 抖动的正确处理
    ----------------
    原始 run 里可能夹着**时长 < M** 的短段（raw 判定抖了一下）。
    反复执行：删掉任何时长 < M 的段，把它的左右两段（同类）并起来。
    迭代到收敛。

    ⚠️ 只看"上一个已合并段"是**错的** —— 中间插进一个短段之后，
    后面的同类段会去和那个短段比，永远合不上。
    """
    rows = list(csv.DictReader(
        (DAT / "session_a_predictions.csv").open(encoding="utf-8-sig")))
    runs, cur, st = [], rows[0]["raw_prediction"], 0
    for i, r in enumerate(rows):
        if r["raw_prediction"] != cur:
            runs.append([cur, st, i - 1])
            cur, st = r["raw_prediction"], i
    runs.append([cur, st, len(rows) - 1])

    changed = True
    while changed and len(runs) > 1:
        changed = False
        for k in range(len(runs) - 1):
            v, s, e = runs[k]
            if _dur(rows, s, e) >= M_SECONDS:
                continue                       # 这段够长，是真的
            left = runs[k - 1][0] if k > 0 else None
            right = runs[k + 1][0] if k + 1 <= len(runs) - 1 else None
            if left is not None and right is not None and left == right:
                runs[k - 1][2] = runs[k + 1][2]      # 跳过短段，两侧合并
                del runs[k:k + 2]
            elif left is not None and right is None:
                runs[k - 1][2] = e                    # 尾部短段并进左边
                del runs[k]
            elif left is None and right is not None:
                runs[k + 1][1] = s                    # 头部短段并进右边
                del runs[k]
            else:
                continue                             # 孤立短段：保留
            changed = True
            break
    return rows, runs


def task_b():
    rows, segs = segments()
    print()
    print("=" * 74)
    print(f"任务 B · session_a 分段（合并阈值 M={M_SECONDS}s）")
    print("=" * 74)
    print(f"  段数: {len(segs)}   切换次数: {len(segs)-1}\n")
    with (DAT / "session_a_segments.csv").open(
            "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["seg_id", "raw_prediction", "t_start", "t_end",
                    "duration_s", "frame_first", "frame_last", "n_frames"])
        for k, (v, s, e) in enumerate(segs, 1):
            w.writerow([k, v, rows[s]["timestamp_sec"], rows[e]["timestamp_sec"],
                        round(float(rows[e]["timestamp_sec"])
                              - float(rows[s]["timestamp_sec"]) + 0.1, 1),
                        rows[s]["frame_id"], rows[e]["frame_id"], e - s + 1])
    print(f"  -> session_a_segments.csv  {len(segs)} 段")
    picks_all = []
    for k, (v, s, e) in enumerate(segs, 1):
        n = e - s + 1
        k_n = 6 if n > 200 else (5 if n > 60 else 3)
        idx = sorted({s + round(i * (n - 1) / (k_n - 1))
                      for i in range(k_n)}) if k_n > 1 else [s]
        pr = [rows[i] for i in idx]
        picks_all.append((k, v, s, e, pr))
        dur = float(rows[e]["timestamp_sec"]) - float(rows[s]["timestamp_sec"]) + 0.1
        print(f"  段{k:>2} {v:<10} t={rows[s]['timestamp_sec']:>6}~"
              f"{rows[e]['timestamp_sec']:<6} {dur:>5.1f}s {n:>4}帧  "
              f"{[r['frame_id'] for r in pr]}")

    LBL_W = 300
    PADT = 22
    roww = max(len(p[4]) for p in picks_all)
    sheet = Image.new("RGB", (LBL_W + roww * (CW + 6) + 12,
                             len(picks_all) * (CH + PADT + 8) + 40),
                      (18, 18, 24))
    d0 = ImageDraw.Draw(sheet)
    d0.text((8, 12), f"session_a segments   {len(segs)} segments / "
                     f"{len(segs)-1} transitions   "
                     f"green border = world, red = non-world", fill=(255, 255, 255))
    for k, (seg, v, s, e, picks) in enumerate(picks_all):
        y = 40 + k * (CH + PADT + 8)
        d = ImageDraw.Draw(sheet)
        d.rectangle([4, y, LBL_W - 4, y + CH], fill=(26, 26, 32))
        dur = float(rows[e]["timestamp_sec"]) - float(rows[s]["timestamp_sec"]) + 0.1
        d.text((14, y + 10), f"SEG {seg}", fill=(255, 220, 0))
        d.text((14, y + 34), f"t = {rows[s]['timestamp_sec']}~"
                            f"{rows[e]['timestamp_sec']}s", fill=(210, 210, 220))
        d.text((14, y + 58), f"duration {dur:.1f}s", fill=(150, 150, 165))
        d.text((14, y + 82), f"raw: {'WORLD' if v=='world' else 'NON-WORLD'}",
               fill=(0, 220, 120) if v == "world" else (255, 110, 110))
        d.text((14, y + 106), f"{e-s+1} frames", fill=(150, 150, 165))
        d.text((14, y + 130), rows[s]["frame_id"], fill=(130, 130, 145))
        d.text((14, y + 150), f"  .. {rows[e]['frame_id']}", fill=(130, 130, 145))
        for j, r in enumerate(picks):
            p = DA / f"{r['frame_id']}.jpg"
            im = cv2.imread(str(p))
            if im is None:
                continue
            h, w = im.shape[:2]
            if w / h > CW / CH:
                nw = int(h * CW / CH)
                im = im[:, (w - nw) // 2:(w - nw) // 2 + nw]
            else:
                nh = int(w * CH / CW)
                im = im[(h - nh) // 2:(h - nh) // 2 + nh, :]
            im = cv2.resize(im, (CW, CH), interpolation=cv2.INTER_AREA)
            pil = Image.fromarray(cv2.cvtColor(im, cv2.COLOR_BGR2RGB))
            dd = ImageDraw.Draw(pil)
            dd.rectangle([0, 0, CW - 1, CH - 1], outline=(
                (0, 220, 0) if v == "world" else (235, 70, 70)), width=4)
            fid, ts = r["frame_id"], r["timestamp_sec"]
            bb = dd.textbbox((0, 0), fid)
            dd.rectangle([CW - (bb[2]-bb[0]) - 12, CH - 26, CW - 2, CH - 2],
                         fill=(0, 0, 0))
            dd.text((CW - (bb[2]-bb[0]) - 8, CH - 24), fid, fill=(255, 230, 0))
            dd.text((CW - 80, CH - 24), f"t={ts}s", fill=(0, 255, 255))
            sheet.paste(pil, (LBL_W + j * (CW + 6), y))
    p = FIG / "session_a_segments.png"
    sheet.save(p)
    print(f"\n  -> {p.name}  {sheet.size}")


if __name__ == "__main__":
    hits = find_info_frames()
    task_a(hits)
    task_b()
    print("\n=== DONE ===")
