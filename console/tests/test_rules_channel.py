"""``RulesRepository`` 单测（Console 侧，2026-10-12 任务 A）。

这个模块能**改用户的规则文件**，所以测试重点不是「能不能读」，
而是「**改错规则**」和「**悄悄改坏文件**」这两种失败模式：

1. 文本扫描出来的块与 tomllib 解析出的规则靠**下标**对齐。一旦文本块比
   解析结果多（多行字符串里恰好有 ``[[rules]]``），下标就会错位 ——
   结果是**改错了规则还一声不吭**。模块里加了交叉校验，本文件守它。
2. 注释掉的 ``# [[rules]]`` 占位规则不能被当成真规则列出去
   （真实教训：8 条会变成 11 条）。
3. 写回必须**原子**（先临时文件再 replace）—— 中途崩了留下半个 TOML，
   引擎下次启动直接解析失败。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from neko_console.channels.rules import (
    RuleEditError,
    RulesRepository,
    rules_dir,
)

GOOD = """\
[[rules]]
name = "world_attack_only_with_enemy"
action = "NORMAL_ATTACK"
when = "has_nearby_enemy()"
priority = 10
scenes = ["WORLD"]
enabled = true
note = "附近有敌人就打"

[[rules]]
name = "world_pickup_drops"
action = "USE_PICKUP"
when = "has_nearby_drops()"
priority = 40
scenes = ["WORLD"]
enabled = false
note = "捡掉落物"
"""

#: 注释掉的占位规则 —— 必须被跳过
WITH_COMMENTED = """\
# [[rules]]
# name = "commented_out"
# action = "WAIT"

[[rules]]
name = "real_rule"
action = "WAIT"
when = "true"
priority = 50
scenes = ["WORLD"]
enabled = true
"""


class _FakeEngineConfig:
    def __init__(self, root: Path, runtime: Path) -> None:
        self._root, self._runtime = root, runtime

    def resolved_path(self) -> Path:
        return self._root

    def resolved_runtime_dir(self) -> Path:
        return self._runtime

    def resolved_python(self) -> Path:
        import sys

        return Path(sys.executable)


@pytest.fixture
def env():
    tmp = Path(tempfile.mkdtemp(prefix="rulechan_"))
    root = tmp / "engine"
    rdir = root / "autopilot" / "rules"
    rdir.mkdir(parents=True)
    runtime = tmp / "runtime"
    runtime.mkdir()
    try:
        yield tmp, root, rdir, runtime
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _repo(rdir: Path, env) -> RulesRepository:
    _tmp, root, _rd, runtime = env
    return RulesRepository(_FakeEngineConfig(root, runtime))  # noqa: F841


# ==================================================================
# 目录推导
# ==================================================================

def test_rules_dir__appends_the_known_suffix() -> None:
    assert rules_dir(r"C:\engine") == Path(r"C:\engine\autopilot\rules")


def test_repo__accepts_the_engine_root(env) -> None:
    _tmp, root, rdir, _rt = env
    assert RulesRepository(root).rules_dir == rdir


def test_repo__accepts_the_rules_dir_directly(env) -> None:
    """两种都收。传目录进来再叠一层会得到假路径，而 ``Path.glob`` 对
    不存在的目录**不抛异常、只是静悄悄地返回空** —— 看起来像「规则没了」。"""
    _tmp, _root, rdir, _rt = env
    assert RulesRepository(rdir).rules_dir == rdir


def test_repo__engine_root_is_not_mistaken_for_a_rules_dir(env) -> None:
    """★ 判据顺序不能反：引擎根顶层同样躺着好几个 .toml。"""
    _tmp, root, rdir, _rt = env
    (root / "plugin.toml").write_text("x = 1", encoding="utf-8")
    assert RulesRepository(root).rules_dir == rdir
    assert RulesRepository(root).rules_dir != root


# ==================================================================
# scan()
# ==================================================================

def test_scan__returns_one_entry_per_toml(env) -> None:
    _tmp, _root, rdir, _rt = env
    (rdir / "a.toml").write_text(GOOD, encoding="utf-8")
    (rdir / "b.toml").write_text(GOOD, encoding="utf-8")
    got = RulesRepository(rdir).scan()
    assert len(got) == 2
    assert [f.name for f in got] == ["a.toml", "b.toml"]      # 已排序


def test_scan__ignores_non_toml_files(env) -> None:
    _tmp, _root, rdir, _rt = env
    (rdir / "a.toml").write_text(GOOD, encoding="utf-8")
    (rdir / "README.md").write_text("x", encoding="utf-8")
    (rdir / "old.toml.bak").write_text(GOOD, encoding="utf-8")
    assert len(RulesRepository(rdir).scan()) == 1


def test_scan__missing_directory_returns_empty_not_raises(env) -> None:
    """``Path.glob`` 对不存在的目录静悄悄返回空 —— 不抛。"""
    _tmp, root, _rd, _rt = env
    assert RulesRepository(root / "根本没有").scan() == []


def test_scan__empty_directory_returns_empty(env) -> None:
    _tmp, _root, rdir, _rt = env
    assert RulesRepository(rdir).scan() == []


def test_scan__one_broken_file_does_not_hide_the_good_ones(env) -> None:
    """★ 一个坏文件不该让另外两个好的一起消失。"""
    _tmp, _root, rdir, _rt = env
    (rdir / "a_good.toml").write_text(GOOD, encoding="utf-8")
    (rdir / "b_broken.toml").write_text("[[rules\nname = ", encoding="utf-8")
    (rdir / "c_good.toml").write_text(GOOD, encoding="utf-8")
    got = RulesRepository(rdir).scan()
    assert len(got) == 3
    assert got[0].ok and got[2].ok
    assert not got[1].ok and got[1].error


def test_scan__a_file_with_no_rules_table_is_ok_but_empty(env) -> None:
    """不是所有 toml 都装规则（比如配置片段）。"""
    _tmp, _root, rdir, _rt = env
    (rdir / "meta.toml").write_text("x = 1\n", encoding="utf-8")
    got = RulesRepository(rdir).scan()
    assert len(got) == 1 and got[0].ok and got[0].rules == []


def test_scan__utf8_bom_is_tolerated(env) -> None:
    """★ 带 BOM 的文件 tomllib 会报错，但引擎那边多半能读。"""
    _tmp, _root, rdir, _rt = env
    (rdir / "bom.toml").write_bytes("﻿".encode("utf-8") + GOOD.encode("utf-8"))
    got = RulesRepository(rdir).scan()
    assert got[0].ok, f"BOM 文件被判成坏了：{got[0].error}"
    assert len(got[0].rules) == 2


def test_scan__commented_out_rules_are_not_listed(env) -> None:
    """★ 真实教训：注释掉的 ``# [[rules]]`` 占位规则会把 8 条变成 11 条。"""
    _tmp, _root, rdir, _rt = env
    (rdir / "a.toml").write_text(WITH_COMMENTED, encoding="utf-8")
    got = RulesRepository(rdir).scan()
    assert len(got[0].rules) == 1
    assert got[0].rules[0].name == "real_rule"


def test_scan__non_dict_rule_entries_are_skipped(env) -> None:
    _tmp, _root, rdir, _rt = env
    (rdir / "a.toml").write_text('rules = [1, "x", {name="ok"}]',
                                 encoding="utf-8")
    got = RulesRepository(rdir).scan()
    assert [r.name for r in got[0].rules] == ["ok"]


# ==================================================================
# RuleInfo / RuleFile 的展示字段
# ==================================================================

def test_scan__parses_all_display_fields(env) -> None:
    _tmp, _root, rdir, _rt = env
    (rdir / "a.toml").write_text(GOOD, encoding="utf-8")
    r = RulesRepository(rdir).scan()[0].rules[0]
    assert r.name == "world_attack_only_with_enemy"
    assert r.priority == 10
    assert r.scenes == ["WORLD"]
    assert r.enabled is True
    assert r.has_enabled_key is True
    assert "附近有敌人" in r.note


def test_rule_info__missing_enabled_means_enabled(env) -> None:
    """缺字段 = 默认启用。这条约定写在模块 docstring 里。"""
    _tmp, _root, rdir, _rt = env
    (rdir / "a.toml").write_text(
        '[[rules]]\nname = "r"\naction = "WAIT"\n', encoding="utf-8")
    r = RulesRepository(rdir).scan()[0].rules[0]
    assert r.enabled is True and r.has_enabled_key is False


def test_rule_info__empty_scenes_show_a_dash(env) -> None:
    """空列表显示 ``—`` 而不是留白 —— 留白会让人以为是加载失败。"""
    _tmp, _root, rdir, _rt = env
    (rdir / "a.toml").write_text('[[rules]]\nname = "r"', encoding="utf-8")
    r = RulesRepository(rdir).scan()[0].rules[0]
    assert r.scenes_text == "—"


def test_rule_info__multiple_scenes_are_joined(env) -> None:
    _tmp, _root, rdir, _rt = env
    (rdir / "a.toml").write_text(
        '[[rules]]\nname = "r"\nscenes = ["WORLD", "UNKNOWN"]', encoding="utf-8")
    assert RulesRepository(rdir).scan()[0].rules[0].scenes_text == "WORLD, UNKNOWN"


def test_rule_info__missing_priority_shows_a_dash(env) -> None:
    _tmp, _root, rdir, _rt = env
    (rdir / "a.toml").write_text('[[rules]]\nname = "r"', encoding="utf-8")
    assert RulesRepository(rdir).scan()[0].rules[0].priority_text == "—"


def test_rule_info__non_int_priority_becomes_none(env) -> None:
    """写成字符串的 priority 不能当数字用。"""
    _tmp, _root, rdir, _rt = env
    (rdir / "a.toml").write_text('[[rules]]\nname = "r"\npriority = "高"',
                                 encoding="utf-8")
    assert RulesRepository(rdir).scan()[0].rules[0].priority is None


def test_rule_file__enabled_count_counts_only_enabled(env) -> None:
    _tmp, _root, rdir, _rt = env
    (rdir / "a.toml").write_text(GOOD, encoding="utf-8")
    assert RulesRepository(rdir).scan()[0].enabled_count == 1


# ==================================================================
# set_enabled() —— 改写用户文件，最危险的路径
# ==================================================================

def test_set_enabled__turns_a_rule_off(env) -> None:
    _tmp, _root, rdir, _rt = env
    p = rdir / "a.toml"
    p.write_text(GOOD, encoding="utf-8")
    RulesRepository(rdir).set_enabled(p, "world_attack_only_with_enemy", False)
    assert "enabled = false" in p.read_text(encoding="utf-8")


def test_set_enabled__turns_a_rule_back_on_by_deleting_the_key(env) -> None:
    """★ 关键语义：**True = 回到默认，不是写 ``enabled = true``**。

    缺 ``enabled`` 字段本来就等于启用（见 ``test_rule_info__missing_enabled_
    means_enabled``），所以「打开」的正确做法是**把这个键删掉**，
    让文件回到最干净的默认状态 —— 而不是塞一行 ``enabled = true``。

    这一点必须钉死：界面上勾选/取消勾选反复切换时，文件不会
    堆出一堆 ``enabled = true`` 的噪声行。
    """
    _tmp, _root, rdir, _rt = env
    p = rdir / "a.toml"
    p.write_text(GOOD, encoding="utf-8")
    RulesRepository(rdir).set_enabled(p, "world_pickup_drops", True)
    body = p.read_text(encoding="utf-8")
    # 第二条原本是 enabled = false，现在这一行**被删掉了**
    tail = body.split("world_pickup_drops")[1]
    assert "enabled" not in tail, "回到默认应当删键，不是写 true"
    # 第一条的 enabled = true 还在 —— 没被误伤
    assert body.count("enabled = true") == 1


def test_set_enabled__only_touches_the_named_rule(env) -> None:
    """★ 改 A 不能动到 B —— 用户以为停了 A、实际停了 B 是最坏的失败。"""
    _tmp, _root, rdir, _rt = env
    p = rdir / "a.toml"
    p.write_text(GOOD, encoding="utf-8")
    RulesRepository(rdir).set_enabled(p, "world_pickup_drops", False)
    body = p.read_text(encoding="utf-8")
    assert "enabled = true" in body                    # 第一条没被动
    assert body.count("enabled = false") == 1          # 只多出这一条
    assert "world_attack_only_with_enemy" in body      # 名字没被改坏
    assert RulesRepository(rdir).scan()[0].enabled_count == 1


def test_set_enabled__missing_enabled_key_defaults_to_adding_it(env) -> None:
    """反过来：显式关闭是「写键」，因为默认值是开。"""
    _tmp, _root, rdir, _rt = env
    p = rdir / "a.toml"
    p.write_text('[[rules]]\nname = "r"\naction = "WAIT"\n', encoding="utf-8")
    RulesRepository(rdir).set_enabled(p, "r", False)
    assert "enabled = false" in p.read_text(encoding="utf-8")


def test_set_enabled__unknown_rule_name_is_rejected(env) -> None:
    """宁可报错让用户点「重新加载」，也不能改错。"""
    _tmp, _root, rdir, _rt = env
    p = rdir / "a.toml"
    p.write_text(GOOD, encoding="utf-8")
    with pytest.raises(RuleEditError) as ei:
        RulesRepository(rdir).set_enabled(p, "查无此规则", False)
    assert "查无此规则" in str(ei.value)
    assert p.read_text(encoding="utf-8") == GOOD   # 文件没被动过


def test_set_enabled__broken_toml_is_refused_not_guessed(env) -> None:
    _tmp, _root, rdir, _rt = env
    p = rdir / "a.toml"
    p.write_text("[[rules\nname = ", encoding="utf-8")
    with pytest.raises(RuleEditError):
        RulesRepository(rdir).set_enabled(p, "x", False)


def test_set_enabled__missing_file_is_refused(env) -> None:
    _tmp, _root, rdir, _rt = env
    with pytest.raises(RuleEditError):
        RulesRepository(rdir).set_enabled(rdir / "不存在.toml", "x", False)


def test_set_enabled__no_temp_file_is_left_behind(env) -> None:
    """原子写：临时文件必须被 replace 掉，不能留在目录里。"""
    _tmp, _root, rdir, _rt = env
    p = rdir / "a.toml"
    p.write_text(GOOD, encoding="utf-8")
    RulesRepository(rdir).set_enabled(p, "world_pickup_drops", True)
    assert not list(rdir.glob("*.neko-tmp"))


def test_set_enabled__preserves_comments_and_layout(env) -> None:
    """只动 enabled 那一行 —— 注释、顺序、空行都要原样保留。"""
    _tmp, _root, rdir, _rt = env
    p = rdir / "a.toml"
    src = ('# 顶部注释\n'
           '\n'
           '[[rules]]\n'
           '# 这条注释必须活下来\n'
           'name = "r"\n'
           'enabled = true\n'
           '\n')
    p.write_text(src, encoding="utf-8")
    RulesRepository(rdir).set_enabled(p, "r", False)
    out = p.read_text(encoding="utf-8")
    assert "# 顶部注释" in out
    assert "# 这条注释必须活下来" in out
    assert "enabled = false" in out
    assert out.count("\n") == src.count("\n")      # 行数没变


def test_set_enabled__round_trip_keeps_the_file_readable(env) -> None:
    """开→关→开 之后文件必须还能被 scan 读回来。

    半截 TOML 会让引擎下次启动直接挂掉，这是最坏的结果。
    """
    _tmp, _root, rdir, _rt = env
    p = rdir / "a.toml"
    p.write_text(GOOD, encoding="utf-8")
    repo = RulesRepository(rdir)
    repo.set_enabled(p, "world_attack_only_with_enemy", False)
    repo.set_enabled(p, "world_attack_only_with_enemy", True)
    got = repo.scan()
    assert got[0].ok, f"改完读不回来：{got[0].error}"
    assert got[0].enabled_count == 1               # 只有一条开着
