"""``tabs/rules.py`` 单测（Console 侧，2026-10-12）。

离屏跑，用假的 ``RulesRepository`` / 假的 ``load_config``，**不读用户真实的
config.toml、不碰真实规则目录**。

★ 重点守三件事

1. **坏文件绝不能让 ``_current`` 指着它** —— 否则用户在上一个文件的表格里
   点勾选，会把状态写到**错误的路径**上。
2. **清表必须静音**（``QSignalBlocker``）—— 否则清表过程会被
   ``_on_item_changed`` 当成「用户取消勾选」而误写 TOML。
   更阴的是 ``_fill_table`` 里那个 blocker 漏释放的话，表格会**永久静音**，
   表现是「勾选框点不动」，比报错更难查。
3. **整表挂掉要大声报错**，不能留一张看起来像「加载成功」的空表。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt                      # noqa: E402
from PySide6.QtWidgets import QApplication         # noqa: E402

from neko_console.channels.rules import RuleFile, RuleInfo   # noqa: E402
from neko_console.tabs import rules as R            # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _rule(name="r1", enabled=True, priority=3, scenes=("WORLD",),
          note="") -> RuleInfo:
    return RuleInfo(name=name, priority=priority, scenes=list(scenes),
                    note=note, enabled=enabled, has_enabled_key=True)


def _file(path: Path, rules=None, error=None) -> RuleFile:
    return RuleFile(path=path, rules=list(rules or []), error=error)


class _FakeCfgEngine:
    def __init__(self, root) -> None:
        self._root = root

    def resolved_path(self):
        return self._root


class _FakeCfg:
    def __init__(self, root) -> None:
        self.engine = _FakeCfgEngine(root)


class _FakeRepo:
    def __init__(self, root, files=None, raises=None, rules_dir=None) -> None:
        self.root = root
        self._files = files if files is not None else []
        self._raises = raises
        self.rules_dir = rules_dir or (Path(root) / "autopilot" / "rules")

    def scan(self):
        if self._raises is not None:
            raise self._raises
        return list(self._files)


def _patch(monkeypatch, tmp_path, repo, *, rules_dir_ok=True):
    """把 load_config / RulesRepository 都换掉。"""
    monkeypatch.setattr(R, "load_config",
                        lambda write_back=True: (_FakeCfg(tmp_path), "fake"))
    monkeypatch.setattr(R, "RulesRepository", lambda root: repo)


@pytest.fixture
def tab(qapp):
    t = R.RulesTab()
    yield t
    t.shutdown()
    t.deleteLater()


# ==================================================================
# 加载与致命错误
# ==================================================================

def test_reload__bad_engine_path_is_reported_as_the_root_problem(tab, monkeypatch,
                                                                tmp_path) -> None:
    """★ 根目录都错了，报「某个文件解析失败」是误导。

    真相是「引擎根本不在那儿」—— 用户要去改配置，不是去查规则。
    """
    ghost = tmp_path / "没有这个引擎目录"
    _patch(monkeypatch, ghost, _FakeRepo(ghost))
    tab._reload()
    assert not tab._err.isHidden()
    assert "引擎路径" in tab._err.text()
    assert tab._status.text() == "没有加载到任何规则"


def test_reload__missing_rules_dir_is_reported(tab, monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, tmp_path, _FakeRepo(tmp_path))
    tab._reload()
    assert "未找到规则目录" in tab._err.text()


def test_reload__scan_exception_does_not_crash_the_tab(tab, monkeypatch,
                                                      tmp_path) -> None:
    """★ 兜底不让 UI 崩。"""
    (tmp_path / "autopilot" / "rules").mkdir(parents=True)
    _patch(monkeypatch, tmp_path,
           _FakeRepo(tmp_path, raises=RuntimeError("磁盘炸了")))
    tab._reload()
    assert "读取规则目录失败" in tab._err.text()
    assert "磁盘炸了" in tab._err.text()


def test_reload__empty_rules_dir_says_so(tab, monkeypatch, tmp_path) -> None:
    """★ 「没找到」和「加载失败」必须能区分开。"""
    (tmp_path / "autopilot" / "rules").mkdir(parents=True)
    _patch(monkeypatch, tmp_path, _FakeRepo(tmp_path, files=[]))
    tab._reload()
    assert "空的" in tab._status.text()


def test_reload__status_counts_files_and_rules(tab, monkeypatch, tmp_path) -> None:
    (tmp_path / "autopilot" / "rules").mkdir(parents=True)
    files = [
        _file(tmp_path / "a.toml", [_rule("r1"), _rule("r2")]),
        _file(tmp_path / "b.toml", [_rule("r3")]),
    ]
    _patch(monkeypatch, tmp_path, _FakeRepo(tmp_path, files=files))
    tab._reload()
    assert "2 个文件，3 条规则" in tab._status.text()


def test_reload__broken_files_are_counted_separately(tab, monkeypatch,
                                                     tmp_path) -> None:
    """★ 坏文件单独计数，别混进「加载了 N 个」里。"""
    (tmp_path / "autopilot" / "rules").mkdir(parents=True)
    files = [
        _file(tmp_path / "ok.toml", [_rule("r1")]),
        _file(tmp_path / "bad.toml", error="第 3 条语法错"),
    ]
    _patch(monkeypatch, tmp_path, _FakeRepo(tmp_path, files=files))
    tab._reload()
    text = tab._status.text()
    assert "1 个文件" in text and "1 条规则" in text
    assert "1 个读取失败" in text


def test_reload__bad_file_is_marked_in_red_in_the_list(tab, monkeypatch,
                                                       tmp_path) -> None:
    (tmp_path / "autopilot" / "rules").mkdir(parents=True)
    files = [_file(tmp_path / "bad.toml", error="炸了")]
    _patch(monkeypatch, tmp_path, _FakeRepo(tmp_path, files=files))
    tab._reload()
    item = tab._list.item(0)
    assert "读取失败" in item.text()
    assert item.foreground().color().name() == R.C_ERR


def test_reload__first_file_is_selected_automatically(tab, monkeypatch,
                                                      tmp_path) -> None:
    (tmp_path / "autopilot" / "rules").mkdir(parents=True)
    files = [_file(tmp_path / "a.toml", [_rule("r1")])]
    _patch(monkeypatch, tmp_path, _FakeRepo(tmp_path, files=files))
    tab._reload()
    assert tab._list.currentRow() == 0
    assert tab._current is not None


# ==================================================================
# 选中文件
# ==================================================================

def test_broken_file_never_becomes_current(tab, monkeypatch, tmp_path) -> None:
    """★★ 最要命的一条。

    坏文件若让 ``_current`` 指过去，用户在上一个文件的表格里点勾选，
    就会把 enabled 写到**错误的文件**上 —— 而且不报错。
    """
    (tmp_path / "autopilot" / "rules").mkdir(parents=True)
    good = _file(tmp_path / "ok.toml", [_rule("r1")])
    bad = _file(tmp_path / "bad.toml", error="第 3 条语法错")
    _patch(monkeypatch, tmp_path, _FakeRepo(tmp_path, files=[good, bad]))
    tab._reload()

    tab._list.setCurrentRow(1)              # 选中坏文件
    assert tab._current is None
    assert not tab._err.isHidden()
    assert "bad.toml" in tab._err.text()

    tab._list.setCurrentRow(0)              # 切回好文件
    assert tab._current is good
    assert tab._err.isHidden()


def test_current_file_is_none_without_selection(tab, monkeypatch, tmp_path) -> None:
    (tmp_path / "autopilot" / "rules").mkdir(parents=True)
    _patch(monkeypatch, tmp_path,
           _FakeRepo(tmp_path, files=[_file(tmp_path / "a.toml", [_rule()])]))
    tab._reload()
    # ★ 要 setCurrentRow(-1)：clearSelection() 只清高亮，currentItem() 还在，
    #   于是「没选中」这个状态根本造不出来。
    tab._list.setCurrentRow(-1)
    assert tab._current_file() is None


# ==================================================================
# 表格填充
# ==================================================================

def test_fill__rows_and_columns_are_populated(tab, monkeypatch, tmp_path) -> None:
    (tmp_path / "autopilot" / "rules").mkdir(parents=True)
    f = _file(tmp_path / "a.toml", [_rule("战斗", enabled=True),
                                    _rule("采集", enabled=False)])
    _patch(monkeypatch, tmp_path, _FakeRepo(tmp_path, files=[f]))
    tab._reload()

    assert tab._table.rowCount() == 2
    assert tab._table.item(0, 1).text() == "战斗"
    assert tab._table.item(0, 2).text() == "3"


def test_fill__checked_state_reflects_enabled(tab, monkeypatch, tmp_path) -> None:
    (tmp_path / "autopilot" / "rules").mkdir(parents=True)
    f = _file(tmp_path / "a.toml", [_rule("开", enabled=True),
                                    _rule("关", enabled=False)])
    _patch(monkeypatch, tmp_path, _FakeRepo(tmp_path, files=[f]))
    tab._reload()

    assert tab._table.item(0, 0).checkState() == Qt.CheckState.Checked
    assert tab._table.item(1, 0).checkState() == Qt.CheckState.Unchecked


def test_fill__checkbox_is_user_checkable(tab, monkeypatch, tmp_path) -> None:
    """★ 用 QTableWidgetItem 的原生勾选，尺寸/高 DPI 交给 Qt 自己算。"""
    (tmp_path / "autopilot" / "rules").mkdir(parents=True)
    f = _file(tmp_path / "a.toml", [_rule()])
    _patch(monkeypatch, tmp_path, _FakeRepo(tmp_path, files=[f]))
    tab._reload()
    flags = tab._table.item(0, 0).flags()
    assert flags & Qt.ItemFlag.ItemIsUserCheckable


def test_fill__empty_scenes_show_a_dash_not_blank(tab, monkeypatch,
                                                   tmp_path) -> None:
    """★ 留白会让人以为是加载失败。"""
    (tmp_path / "autopilot" / "rules").mkdir(parents=True)
    f = _file(tmp_path / "a.toml", [_rule(scenes=())])
    _patch(monkeypatch, tmp_path, _FakeRepo(tmp_path, files=[f]))
    tab._reload()
    assert tab._table.item(0, 3).text() == "—"


def test_fill__rule_becomes_current(tab, monkeypatch, tmp_path) -> None:
    (tmp_path / "autopilot" / "rules").mkdir(parents=True)
    f = _file(tmp_path / "a.toml", [_rule()])
    _patch(monkeypatch, tmp_path, _FakeRepo(tmp_path, files=[f]))
    tab._reload()
    assert tab._current is f


def test_fill__table_is_not_left_silenced_afterwards(tab, monkeypatch,
                                                     tmp_path) -> None:
    """★★ blocker 必须用 try/finally 释放。

    漏释放的话表格被**永久静音** —— 勾选框点不动，没有任何报错。
    """
    (tmp_path / "autopilot" / "rules").mkdir(parents=True)
    f = _file(tmp_path / "a.toml", [_rule("r1"), _rule("r2")])
    _patch(monkeypatch, tmp_path, _FakeRepo(tmp_path, files=[f]))
    tab._reload()
    # ★ 只用 signalsBlocked() 读状态 —— blockSignals(bool) 是 setter，
    #   无参调用会直接 TypeError。
    assert tab._table.signalsBlocked() is False


def test_clear_table__does_not_trip_item_changed(tab, monkeypatch, tmp_path) -> None:
    """★★ clearContents() 会发 itemChanged，不静音就会被当成用户操作去写 TOML。"""
    (tmp_path / "autopilot" / "rules").mkdir(parents=True)
    f = _file(tmp_path / "a.toml", [_rule("r1", enabled=True)])
    _patch(monkeypatch, tmp_path, _FakeRepo(tmp_path, files=[f]))
    tab._reload()

    writes: list = []
    monkeypatch.setattr(tab, "_on_item_changed", lambda item: writes.append(item))
    tab._clear_table()
    assert writes == []
    assert tab._table.rowCount() == 0


def test_fill__render_failure_is_shown_loudly_not_as_an_empty_table(
        tab, monkeypatch, tmp_path) -> None:
    """★★ Qt 的槽里抛异常只会往 stderr 打 traceback 然后**静默放弃整个槽**。

    UI 上看不出任何异常，只剩一张 ``setRowCount()`` 造出来的**空表** ——
    用户以为「这文件没规则」。所以宁可整张表挂掉并大声报错。
    """
    (tmp_path / "autopilot" / "rules").mkdir(parents=True)
    f = _file(tmp_path / "a.toml", [_rule("r1"), _rule("r2")])
    _patch(monkeypatch, tmp_path, _FakeRepo(tmp_path, files=[f]))
    tab._reload()
    assert tab._table.rowCount() == 2

    # 让填充过程在设 item 时炸掉，模拟「漏 import QCheckBox」那类历史 bug
    real_set_item = tab._table.setItem
    calls = {"n": 0}

    def _boom(row, col, item):
        calls["n"] += 1
        if calls["n"] > 1:
            raise NameError("name 'QCheckBox' is not defined")
        return real_set_item(row, col, item)

    monkeypatch.setattr(tab._table, "setItem", _boom)
    tab._fill_table(f)

    assert "渲染失败" in tab._err.text()
    assert "NameError" in tab._err.text()
    assert "QCheckBox" in tab._err.text()      # 报错要点名真凶
    assert tab._current is None               # 坏状态下不留 _current
    assert tab._table.rowCount() == 0
    assert tab._table.isHidden()


def test_fill__table_is_unsilenced_after_a_render_failure(tab, monkeypatch,
                                                          tmp_path) -> None:
    """失败路径也必须释放 blocker —— 否则用户重试也点不动。"""
    (tmp_path / "autopilot" / "rules").mkdir(parents=True)
    f = _file(tmp_path / "a.toml", [_rule()])
    _patch(monkeypatch, tmp_path, _FakeRepo(tmp_path, files=[f]))
    tab._reload()

    def _boom(row, col, item):
        raise NameError("boom")

    monkeypatch.setattr(tab._table, "setItem", _boom)
    tab._fill_table(f)
    assert tab._table.signalsBlocked() is False


# ==================================================================
# 常量与布局
# ==================================================================

def test_columns_are_declared(tab) -> None:
    assert len(R.COLUMNS) == 4
    assert tab._table.columnCount() == 4


def test_table_is_read_only(tab) -> None:
    """只做「浏览 + 启停」，不该让用户直接编辑单元格。"""
    assert tab._table.editTriggers() == tab._table.editTriggers().NoEditTriggers


def test_status_is_empty_before_the_first_load(tab) -> None:
    """★ 首次显示才加载 —— 别的 tab 用不到规则，白读一遍 IO 只拖慢启动。

    所以构造完状态栏是空的，这是**设计**不是 bug。
    """
    assert tab._status.text() == ""
    assert tab._loaded is False


def test_reload__flips_the_loaded_flag_so_showevent_wont_reload_twice(
        tab, monkeypatch, tmp_path) -> None:
    (tmp_path / "autopilot" / "rules").mkdir(parents=True)
    _patch(monkeypatch, tmp_path,
           _FakeRepo(tmp_path, files=[_file(tmp_path / "a.toml", [_rule()])]))
    tab._reload()
    assert tab._loaded is True


def test_shutdown_does_not_raise(tab) -> None:
    tab.shutdown()      # 不抛即可