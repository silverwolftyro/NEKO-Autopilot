"""session_b 信息框帧验证（每帧只 OCR 一次）。"""

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

import importlib.util
import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2

RECOG = Path(RECOG_ROOT)
PERC = (RECOG.parent / "N.E.K.O-main" / "N.E.K.O-main" / "plugin" / "plugins"
        / "genshin_game_buddy" / "autopilot" / "perception")
DB = RECOG / "raw" / "frames" / "session_20261001_b"
CJK = re.compile(r"[\u4e00-\u9fff]")

s = importlib.util.spec_from_file_location("teleport_ocr",
                                          PERC / "teleport_ocr.py")
to = importlib.util.module_from_spec(s)
sys.modules["teleport_ocr"] = to
s.loader.exec_module(to)


def main(limit=38, sample=160):
    ids = sorted(DB.glob("*.jpg"))
    step = max(1, len(ids) // sample)
    print("=" * 78)
    print(f"session_b 端到端验证（后端={to.get_backend()}，"
          f"共 {len(ids)} 帧，抽样 {len(ids)//step} 帧）")
    print("=" * 78)
    hit, probed, rows = 0, 0, []
    for i in range(0, len(ids), step):
        if probed >= limit:
            break
        p = ids[i]
        im = cv2.imread(str(p))
        if im is None:
            continue
        try:
            region = to.crop_info_box(im)
        except ValueError:
            continue
        # ★ 每帧只 OCR 一次，结果直接喂给 recognize_teleport
        lines = to.ocr_region(region)
        cjk = [t for t in lines if CJK.search(t)]
        if len(cjk) < 2:
            continue
        probed += 1
        clean = to._clean_lines(lines)
        r = to.recognize_teleport(im, lines=lines)
        if r is not None:
            hit += 1
            rows.append((p.stem, "命中",
                         f"id={r.matched_id} {r.name!r} "
                         f"main={r.main_title[:14]!r} "
                         f"sub={r.sub_title[:22]!r} c={r.confidence}"))
        else:
            rows.append((p.stem, "匹配失败", f"{clean}"))
    print(f"  信息框已显示的帧: {probed}")
    print(f"  ★ 命中率 {hit}/{probed} = {hit/probed:.1%}" if probed
          else "  无可用帧")
    print(f"  分布: {dict(Counter(r[1] for r in rows))}\n")
    print(f"  {'帧号':<11}{'结果':<9}明细")
    print("  " + "-" * 70)
    for fid, res, d in rows:
        print(f"  {fid:<11}{res:<9}{d[:64]}")
    if hit:
        got = sorted({r[2].split('id=')[1].split()[0] for r in rows
                      if r[1] == '命中'})
        print(f"\n  命中 ID: {got}")


if __name__ == "__main__":
    main()
