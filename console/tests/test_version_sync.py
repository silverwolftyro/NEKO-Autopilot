"""版本号跨仓一致性（2026-10-16 加）。

★ 为什么要跨仓断言
  `plugin.toml:43-48` 与 `neko_console/__init__.py:14-18` 都写着「两边
  版本号必须一致 —— 用户一眼就能判断插件和主控台是配套的」。

  但这条约束此前**没有任何测试守着**，实测漂过：
      plugin.toml          3.1.18
      plugin pyproject     3.1.18
      autopilot_bridge     3.1.16     ← 复制的那份
      neko_console         3.1.16
  插件侧 `test_manifest__identity_is_consistent` **照样绿**，因为它只比
  manifest 与 pyproject。这条把主控台拉进来。

★ 找不到插件仓就 skip，不假装通过
  主控台仓库本身不包含插件源码（插件在 `..\\N.E.K.O-main\\...`）。
  找不到时 `skip`，而不是静默 pass —— 「没验过」和「验过了」必须能区分。
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

CONSOLE_DIR = Path(__file__).resolve().parents[1]


def _find_plugin_dir(console_dir: Path) -> Path | None:
    """沿目录树找 ``<某层>/plugin/plugins/genshin_game_buddy``。

    发布布局里插件就在仓库根的 ``plugin/`` 下，源仓布局里则隔着
    ``N.E.K.O-main/N.E.K.O-main/``。写死后者的话，发布包里这条
    "找不到 manifest 就 skip" 会**静默地永远跳过** —— 版本漂移守卫就废了。
    """
    for parent in console_dir.parents:
        cand = parent / "plugin" / "plugins" / "genshin_game_buddy"
        if (cand / "plugin.toml").is_file():
            return cand
    return None


def _find_plugin_dir(console_dir: Path) -> Path | None:
    """沿目录树找 ``<某层>/plugin/plugins/genshin_game_buddy``。

    发布布局里插件就在仓库根的 ``plugin/`` 下，源仓布局里则隔着
    ``N.E.K.O-main/N.E.K.O-main/``。写死后者的话，发布包里这条
    "找不到 manifest 就 skip" 会**静默地永远跳过** —— 版本漂移守卫就废了。
    """
    for parent in console_dir.parents:
        cand = parent / "plugin" / "plugins" / "genshin_game_buddy"
        if (cand / "plugin.toml").is_file():
            return cand
    return None
PLUGIN_DIR = _find_plugin_dir(CONSOLE_DIR) or (
    CONSOLE_DIR.parent
    / "N.E.K.O-main" / "N.E.K.O-main"
    / "plugin" / "plugins" / "genshin_game_buddy"
)
MANIFEST = PLUGIN_DIR / "plugin.toml"


def _plugin_version() -> str | None:
    try:
        return tomllib.loads(MANIFEST.read_text(encoding="utf-8"))["plugin"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return None


def test_console_version_matches_plugin_manifest() -> None:
    """`neko_console.__version__` 必须等于插件 manifest 的 version。"""
    from neko_console import __version__

    declared = _plugin_version()
    if declared is None:
        pytest.skip(f"找不到插件 manifest：{MANIFEST}")
    assert __version__ == declared, (
        f"neko_console.__version__={__version__} != plugin.toml {declared}"
    )


def test_console_version_comment_matches_declared() -> None:
    """`__init__.py` 顶部三段式注释的小迭代号必须等于实际版本。

    同源问题：注释里的数字不会自动跟着 version 变，实测出现过
    「注释写 16、下面写 18」。注释比代码漂移更难查。
    """
    from neko_console import __version__

    parts = __version__.split(".")
    assert len(parts) >= 3, f"版本号 {__version__} 不是三段式"

    text = (CONSOLE_DIR / "neko_console" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(
        r"^#\s+" + re.escape(parts[0]) + r"\s+= .*?\n"
        r"^#\s+" + re.escape(parts[1]) + r"\s+= .*?\n"
        r"^#\s+(\d+)\s+= 小迭代号",
        text,
        re.M,
    )
    assert m, "neko_console/__init__.py 里找不到版本号三段式注释"
    assert m.group(1) == parts[2], (
        f"注释里的小迭代号是 {m.group(1)}，实际版本 {__version__}"
    )
