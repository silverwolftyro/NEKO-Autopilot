"""``EngineConfig.resolved_pathfind_script()`` 单测（Console 侧，2026-10-12）。

**为什么这组测试存在**：早先 ``PathfindChannel.script_path()`` 把寻路脚本写死成
``<engine.path>/autopilot/tools/e2e_pathfind.py``。那个目录**存在**，但里面没有
``e2e_pathfind.py`` —— 脚本住在 ``recognition`` 仓库。于是点「启动」永远被
``path_problems()`` 拒掉，而错误信息指向一个看起来完全正常的目录。

★ 这里不碰真游戏、不起子进程：只测「路径解析」这一个纯函数。
  真的起进程的测试在 ``test_pathfind_live.py``。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from neko_console.channels.pathfind import PathfindChannel
from neko_console.config import schema
from neko_console.config.schema import DEFAULT_CONFIG, ConsoleConfig, EngineConfig


@pytest.fixture
def tmpdir():
    d = Path(tempfile.mkdtemp(prefix="pfpath_"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _cfg(root: Path, pathfind_script: str = "") -> EngineConfig:
    return EngineConfig(path=str(root), pathfind_script=pathfind_script)


def _plugin_script(root: Path) -> Path:
    return Path(root) / "autopilot" / "tools" / "e2e_pathfind.py"


# ==================================================================
# 三段优先级
# ==================================================================

def test_explicit_config_wins_over_everything(tmpdir) -> None:
    """用户填了就只认用户的 —— 哪怕那个文件根本不存在。

    ★ 刻意**不**校验存在性就返回：配置错了应该由 ``path_problems()``
      报「找不到寻路脚本：<用户填的那个>」，而不是悄悄换成别的脚本
      去跑。悄悄换 = 用户以为在跑 A，实际跑的是 B。
    """
    mine = tmpdir / "我的脚本.py"
    cfg = _cfg(tmpdir, pathfind_script=str(mine))
    assert cfg.resolved_pathfind_script() == mine
    assert mine.exists() is False


def test_blank_or_whitespace_config_means_auto(tmpdir, monkeypatch) -> None:
    """只填了空格 = 没填。"""
    fallback = tmpdir / "fallback.py"
    fallback.write_text("#", encoding="utf-8")
    monkeypatch.setattr(schema, "DEFAULT_PATHFIND_SCRIPT", str(fallback))
    cfg = _cfg(tmpdir, pathfind_script="   ")
    assert cfg.resolved_pathfind_script() == fallback


def test_plugin_copy_wins_over_factory_default(tmpdir, monkeypatch) -> None:
    """脚本一旦迁进插件，插件里那份自动接管，不需要用户改配置。

    这是选方案 A 而不是写死路径的主要理由：迁移动作不应该让 Console 失效。
    """
    fallback = tmpdir / "fallback.py"
    fallback.write_text("#", encoding="utf-8")
    monkeypatch.setattr(schema, "DEFAULT_PATHFIND_SCRIPT", str(fallback))
    in_plugin = _plugin_script(tmpdir)
    in_plugin.parent.mkdir(parents=True, exist_ok=True)
    in_plugin.write_text("#", encoding="utf-8")

    assert _cfg(tmpdir).resolved_pathfind_script() == in_plugin


def test_falls_back_to_factory_default_when_plugin_has_none(tmpdir, monkeypatch) -> None:
    """插件里没有（当前实况）-> 用 recognition 那份。"""
    fallback = tmpdir / "fallback.py"
    fallback.write_text("#", encoding="utf-8")
    monkeypatch.setattr(schema, "DEFAULT_PATHFIND_SCRIPT", str(fallback))
    assert _cfg(tmpdir).resolved_pathfind_script() == fallback


def test_missing_everywhere_points_at_the_plugin_location(tmpdir, monkeypatch) -> None:
    """两边都没有时，返回「本来该在哪」，让报错信息指向可操作的位置。"""
    ghost = tmpdir / "根本不存在.py"
    monkeypatch.setattr(schema, "DEFAULT_PATHFIND_SCRIPT", str(ghost))
    resolved = _cfg(tmpdir).resolved_pathfind_script()
    assert resolved == _plugin_script(tmpdir)
    assert resolved.is_file() is False


def test_explicit_config_is_pinned_even_when_a_better_file_appears(tmpdir, monkeypatch) -> None:
    """★ 用户显式指定的那份**永远**优先，不会被自动发现顶掉。

    解析会每次重新看磁盘，所以「自动」档确实可能中途变（插件里刚好
    多了一份）。但用户手填的配置必须是钉死的 —— 否则用户明明填了 A，
    系统却因为 B 恰好出现了而改跑 B，且界面上毫无提示。
    """
    fallback = tmpdir / "fallback.py"
    fallback.write_text("#", encoding="utf-8")
    monkeypatch.setattr(schema, "DEFAULT_PATHFIND_SCRIPT", str(fallback))

    mine = tmpdir / "我的脚本.py"
    mine.write_text("#", encoding="utf-8")
    cfg = _cfg(tmpdir, pathfind_script=str(mine))

    # 中途在插件目录里塞一份，看会不会被顶掉
    in_plugin = _plugin_script(tmpdir)
    in_plugin.parent.mkdir(parents=True, exist_ok=True)
    in_plugin.write_text("#", encoding="utf-8")

    assert cfg.resolved_pathfind_script() == mine
    assert cfg.resolved_pathfind_script() == cfg.resolved_pathfind_script()


# ==================================================================
# 与 PathfindChannel 的接线
# ==================================================================

def test_channel_uses_the_configured_script(tmpdir, monkeypatch) -> None:
    """通道必须走配置，不能自己再拼一遍。"""
    fallback = tmpdir / "fallback.py"
    fallback.write_text("#", encoding="utf-8")
    monkeypatch.setattr(schema, "DEFAULT_PATHFIND_SCRIPT", str(fallback))
    cfg = _cfg(tmpdir)
    assert PathfindChannel(cfg).script_path() == fallback


def test_channel_reports_the_real_script_when_it_exists(tmpdir) -> None:
    """★ 本机实况：脚本在 recognition 仓库，path_problems 必须为空。

    这条锁的是「别再退回写死路径」—— 它一旦回退，这里立刻红。
    """
    real = Path(schema.DEFAULT_PATHFIND_SCRIPT)
    if not real.is_file():
        pytest.skip(f"本机没有 {real}，跳过实况断言")
    cfg = EngineConfig(path=str(tmpdir))          # 插件里必然没有
    assert cfg.resolved_pathfind_script() == real
    assert PathfindChannel(cfg).path_problems() == []


# ==================================================================
# 配置往返：用户填的值不能在中途被丢掉
# ==================================================================

def test_default_config_declares_the_key() -> None:
    """★ 漏了这条，用户在 config.toml 里填的值会被静默丢弃。

    ``DEFAULT_CONFIG["engine"]`` 是配置收缩时的字段来源（见
    ``_flatten_nested``），键不在里面就等于这个字段不存在。
    """
    assert "pathfind_script" in DEFAULT_CONFIG["engine"]
    assert DEFAULT_CONFIG["engine"]["pathfind_script"] == ""


def test_bundled_default_toml_declares_the_key() -> None:
    """内置 default.toml 也要有 —— 那是用户换机器时看的说明。"""
    from neko_console.paths import bundled_default_config

    text = bundled_default_config().read_text(encoding="utf-8")
    assert "pathfind_script" in text


def test_value_survives_the_shrink_round_trip() -> None:
    """填了绝对路径 -> 收缩 -> 构回配置，值必须原样还在。

    ``clamped()`` 里有一次「BaseModel -> model_dump -> 收缩 ->
    model_validate」往返；字段名拼错或没进 DEFAULT_CONFIG，这里就丢。
    """
    mine = r"D:\somewhere\e2e_pathfind.py"
    cfg, notes = ConsoleConfig(
        engine={"path": r"D:\x", "pathfind_script": mine}).clamped()
    assert cfg.engine.pathfind_script == mine
    assert notes == []


def test_toml_round_trip_preserves_the_value() -> None:
    """写 TOML 再读回来，值还在（用 tomli_w 直接往返，不碰用户的 config.toml）。"""
    import tomllib

    import tomli_w

    mine = r"D:\somewhere\e2e_pathfind.py"
    cfg = ConsoleConfig(engine={"path": r"D:\x", "pathfind_script": mine})
    text = tomli_w.dumps(cfg.model_dump())
    back = tomllib.loads(text)
    assert back["engine"]["pathfind_script"] == mine
    assert EngineConfig(**back["engine"]).pathfind_script == mine


def test_absent_key_falls_back_to_auto(tmpdir, monkeypatch) -> None:
    """老 config.toml 里没这个键 -> 照常自动查找，不报错。

    配置向前兼容比向后兼容更重要。
    """
    fallback = tmpdir / "fallback.py"
    fallback.write_text("#", encoding="utf-8")
    monkeypatch.setattr(schema, "DEFAULT_PATHFIND_SCRIPT", str(fallback))
    cfg = EngineConfig(**{"path": str(tmpdir)})
    assert cfg.pathfind_script == ""
    assert cfg.resolved_pathfind_script() == fallback