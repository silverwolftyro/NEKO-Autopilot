"""``tabs/about.py`` 单测（Console 侧，2026-10-12）。

离屏跑，不开窗口。

★ 这一页看着像静态页，但 :func:`_build_time` 有**三条**分支
  （开发模式 / 读 exe 的 mtime / 读不到），其中「读不到」那条的取值是
  ``未知`` 而不是编一个时间 —— 这正是本仓库反复强调的取舍，值得钉住。
"""

from __future__ import annotations

import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel      # noqa: E402

from neko_console.tabs import about as A                 # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _Boom:
    """``stat()`` 一定抛 OSError 的假 Path —— 模拟 exe 被挪走 / 权限不足。"""

    def __init__(self, *a, **kw) -> None:
        pass

    def stat(self):
        raise OSError("权限不足")


# ==================================================================
# _build_time
# ==================================================================

def test_build_time__dev_mode_says_dev(monkeypatch) -> None:
    monkeypatch.setattr(A, "is_frozen", lambda: False)
    assert A._build_time() == "dev"


def test_build_time__frozen_reads_exe_mtime(monkeypatch) -> None:
    """★ 打包后展示的是 **exe 文件的修改时间**，不是源码时间。

    排查时「我到底有没有用上刚打的包」只能靠这个。
    """
    monkeypatch.setattr(A, "is_frozen", lambda: True)
    got = A._build_time()
    assert got != "dev" and got != "未知"
    # 形如 2026-10-12 19:30:00
    assert len(got) == 19 and got[4] == "-" and got[10] == " "


def test_build_time__unreadable_exe_says_unknown_not_a_guess(monkeypatch) -> None:
    """★ 读不到就说读不到。

    显示一个编造的时间比不显示更糟 —— 用户会拿它判断「我用的是不是新包」，
    编一个时间等于给了一个假的判据。
    """
    monkeypatch.setattr(A, "is_frozen", lambda: True)
    monkeypatch.setattr(A, "Path", _Boom)
    assert A._build_time() == "未知"


def test_build_time__dev_mode_does_not_touch_the_filesystem(monkeypatch) -> None:
    """开发模式不该去 stat 一个根本不存在的 exe。"""
    monkeypatch.setattr(A, "is_frozen", lambda: False)

    def _boom(*a, **kw):        # 一旦被调用就让测试失败
        raise AssertionError("开发模式不该去 stat 文件")

    monkeypatch.setattr(A, "Path", _boom)
    assert A._build_time() == "dev"


def test_build_time__frozen_uses_sys_executable(monkeypatch) -> None:
    """它读的是「当前运行的那个 exe」。"""
    seen: list[str] = []

    class _FakePath:
        def __init__(self, p):
            seen.append(str(p))

        def stat(self):
            return type("S", (), {"st_mtime": time.time()})()

    monkeypatch.setattr(A, "is_frozen", lambda: True)
    monkeypatch.setattr(A, "Path", _FakePath)
    A._build_time()
    assert seen and seen[0] == str(sys.executable)


# ==================================================================
# 页面内容
# ==================================================================

def test_about_tab__shows_version(qapp) -> None:
    from neko_console import __version__

    t = A.AboutTab()
    try:
        assert __version__ in _texts(t)
    finally:
        t.deleteLater()


def test_about_tab__shows_license(qapp) -> None:
    t = A.AboutTab()
    try:
        assert "Apache-2.0" in _texts(t)
    finally:
        t.deleteLater()


def test_about_tab__shows_user_directory(qapp) -> None:
    from neko_console.paths import base_dir

    t = A.AboutTab()
    try:
        assert str(base_dir()) in _texts(t)
    finally:
        t.deleteLater()


def test_about_tab__dev_mode_says_so(qapp, monkeypatch) -> None:
    monkeypatch.setattr(A, "is_frozen", lambda: False)
    t = A.AboutTab()
    try:
        assert "开发模式" in _texts(t)
        assert "Nuitka" not in _texts(t)
    finally:
        t.deleteLater()


def test_about_tab__frozen_mode_says_so(qapp, monkeypatch) -> None:
    monkeypatch.setattr(A, "is_frozen", lambda: True)
    t = A.AboutTab()
    try:
        assert "Nuitka" in _texts(t)
    finally:
        t.deleteLater()


def test_about_tab__text_is_selectable(qapp) -> None:
    """路径要能选中复制 —— 用户经常要贴给别的地方问问题。"""
    from PySide6.QtCore import Qt

    t = A.AboutTab()
    try:
        flags = _labels(t)[-1].textInteractionFlags()
        assert flags & Qt.TextSelectableByMouse
    finally:
        t.deleteLater()


def test_about_tab__title(qapp) -> None:
    assert A.AboutTab.TITLE == "关于"


def test_about_tab__labels_wrap_so_long_paths_stay_readable(qapp) -> None:
    """用户目录是很长的路径；不换行的话窗口窄一点就全被截断。"""
    t = A.AboutTab()
    try:
        assert any(lbl.wordWrap() for lbl in _labels(t))
    finally:
        t.deleteLater()


def _labels(w) -> list[QLabel]:
    return w.findChildren(QLabel)


def _texts(w) -> str:
    return "\n".join(lbl.text() for lbl in _labels(w))