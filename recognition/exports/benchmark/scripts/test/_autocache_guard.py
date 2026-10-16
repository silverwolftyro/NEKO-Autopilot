"""auto cache 守卫 —— **`conftest.py` 在本仓库的 runner 等价物**。

★ 为什么不直接用 conftest.py
--------------------------
本目录的测试**不是 pytest 测试**：全部是「模块级代码 + ``check()`` 累加
PASS/FAIL + ``sys.exit(1 if FAIL else 0)`` 的**自定义 runner**，
一个 ``def test_`` 都没有。pytest 即使被调用也**收集不到任何用例**，
``conftest.py`` 里的 autouse fixture 永远不会执行。

所以真正起作用的是本模块 —— 每个测试文件在动手前显式调
:func:`reset_auto_cache`。

（``conftest.py`` 也留了一份 autouse fixture，以防将来把整套测试迁到
pytest；届时删掉本文件即可。pytest 本机已安装，不算新增依赖。）
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

ROOT = Path(PLUGIN_DIR)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def reset_auto_cache() -> bool:
    """清空 builder 的 ``"auto"`` 进程级缓存。返回是否原本有缓存。

    **什么时候必须调**：

    * 任何断言依赖「auto 当前是启用还是降级」之前
    * 改过 :data:`world_entities.BGI_WORLD_ONNX` 指向之后
    * 断言「两次构造拿到同一实例」之后（否则后面全在用缓存）

    :func:`builder.StateBuilder.__init__` 里**不会**自动调它
    （那等于每次构造付 1.2 s，生产上不可接受）。
    """
    from autopilot.perception import builder
    return builder.clear_auto_cache()


def restore_entity_paths(we_mod) -> None:
    """把 ``world_entities`` 的路径常量恢复成默认值。

    测试里为了模拟「缺模型」会临时改 ``BGI_WORLD_ONNX``。**忘了还原
    的话，后面所有用例都在用一个假路径跑** —— 这比 cache 串味更隐蔽。
    """
    from autopilot.perception import paths as P
    we_mod.BGI_WORLD_ONNX = P.BGI_WORLD_ONNX
    we_mod.BGI_WORLD_CLASSES = P.BGI_WORLD_CLASSES


__all__ = ["reset_auto_cache", "restore_entity_paths", "ROOT"]
