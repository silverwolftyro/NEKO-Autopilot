"""检查两个批次的 F 编号是否冲突 + 内容是否重复。"""

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

import hashlib
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RECOG = Path(RECOG_ROOT)
A = RECOG / "raw" / "frames" / "batch_20260929a"
B = RECOG / "raw" / "frames" / "batch_20260929b"

fa = {p.stem: p for p in A.glob("*.jpg")}
fb = {p.stem: p for p in B.glob("*.jpg")}


def md5(p):
    h = hashlib.md5()
    with p.open("rb") as fh:
        h.update(fh.read(1 << 20))
    return h.hexdigest()


common = sorted(set(fa) & set(fb))
print(f"batch_a: {len(fa)}   batch_b: {len(fb)}")
print(f"编号重叠: {len(common)} 个")
if common:
    same = [k for k in common if md5(fa[k]) == md5(fb[k])]
    print(f"  内容完全相同: {len(same)}")
    print(f"  内容不同    : {len(common) - len(same)}")
    diff = [k for k in common if md5(fa[k]) != md5(fb[k])]
    print(f"  不同的编号（前 20）: {diff[:20]}")
print()
print(f"仅 a 独有: {sorted(set(fa) - set(fb))[:10]} ... 共 {len(set(fa)-set(fb))}")
print(f"仅 b 独有: {sorted(set(fb) - set(fa))[:5]} ... 共 {len(set(fb)-set(fa))}")
print()
print("a 范围:", min(fa), "~", max(fa))
print("b 范围:", min(fb), "~", max(fb))
print("=== DONE ===")
