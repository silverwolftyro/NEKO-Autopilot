"""冻结版判定树最终自检。"""

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

RECOG = Path(RECOG_ROOT)
TREE = RECOG / "exports" / "benchmark" / "scripts" / "misc" / "coord_scene_tree.py"
LAB = RECOG / "exports" / "benchmark" / "datasets" / "scene_labels.csv"
EX = {"中间态", "特殊-视频", "特殊-回归活动"}

spec = importlib.util.spec_from_file_location("cst", TREE)
m = importlib.util.module_from_spec(spec)
sys.modules["cst"] = m
spec.loader.exec_module(m)

print(f"阈值: edge_density <= {m.EDGE_DENSITY_MAX}   "
      f"mean_s <= {m.MEAN_S_MAX}")
print(f"ROI : {m.HP_BAR_ROI}")

rows = list(csv.DictReader(LAB.open(encoding="utf-8-sig", newline="")))
tr = [r for r in rows if r["scene_type"] not in EX]
ok, errs = 0, []
for r in tr:
    res = m.classify_frame_id(r["frame_id"])
    if res.is_world == (r["scene_type"] == "大世界"):
        ok += 1
    else:
        errs.append((r["frame_id"], r["scene_type"],
                     "非大世界" if res.is_world else "大世界"))
print(f"\n准确率 {ok} / {len(tr)} = {ok/len(tr):.4f}")
print(f"误判 {len(errs)} 帧:")
for f, t, p in errs:
    print(f"  {f:<14} 真实={t:<8} 预测={p}")

d = m.__doc__ or ""
print(f"\ndocstring 记账段: "
      f"HUD淡出={'HUD 淡出态' in d}  时序去抖={'时序去抖' in d}  "
      f"冻结={'冻结' in d}")
rep = RECOG / "notes" / "scene_detector_report.md"
print(f"完成报告存在: {rep.is_file()}  ({rep.stat().st_size if rep.is_file() else 0} B)")
cfg = RECOG / "configs" / "rois.toml"
txt = cfg.read_text(encoding="utf-8")
for k in ("paimon_button", "avatar", "paimon_panel"):
    print(f"rois.toml 含 {k}: {k in txt}")
