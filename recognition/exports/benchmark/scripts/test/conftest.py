"""pytest 入口守卫（**当前不会被触发**，见文件说明）。

本目录的测试是**自定义 runner**，不是 pytest 用例 —— 全部由
「模块级代码 + ``check()`` + ``sys.exit(1 if FAIL else 0)」组成，
没有一个 ``def test_``。pytest 即使被调用也收集不到用例，
所以下面的 autouse fixture 现在是**空转**的。

真正生效的守卫在 :mod:`_autocache_guard`（每个测试文件显式调用）。

保留本文件的唯一理由：pytest 本机已安装，若将来把整套测试迁到
pytest，这个 fixture 会自动生效，不必再补一遍。

⚠️ 若你把测试改写成真正的 pytest 用例，**记得删掉
``_autocache_guard.py``** —— 那时它就是重复劳动了。
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

import pytest

ROOT = Path(PLUGIN_DIR)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _clear_auto_cache_before_each():
    """每个测试**前后**各清一次 auto cache，防止跨用例串味。

    用 ``yield`` 而不是 try/finally：fixture 主体在 setup 阶段跑到 yield，
    teardown 阶段从 yield 继续 —— 即使测试体抛异常也一定会跑到清理。
    """
    from autopilot.perception import builder
    builder.clear_auto_cache()
    yield
    builder.clear_auto_cache()
