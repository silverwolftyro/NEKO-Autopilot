"""``EngineManager.start(blocked_reason=...)`` 互斥护栏单测。

背景（2026-10-16 审计 F1）
------------------------
引擎与寻路都用 ``Win32Backend`` 注入**同一套键鼠**，一方 ``release_all()``
会松开对方按住的键 -> 角色乱走。

``PathfindChannel.start()`` 早就有 ``engine_running`` 参数拒绝「引擎在跑时启动寻路」，
但**反向那条一直缺着**：``EngineManager.start()`` 过去只看自己有没有 Popen。
本轮补上 ``blocked_reason`` 形参，由 app.py / 引擎页在三个入口传入。

本文件只断言「**该被拒绝时有没有被拒绝**」——
它拦下 ``Popen``，不真的拉起子进程，避免在 CI 里跑引擎。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from neko_console.channels.engine import EngineManager


class _FakeEngineConfig:
    """只提供 ``start()`` 路径检查 + 命令拼装用得到的几项。

    ``args`` 刻意给 ``["--dry-run"]``：start() 会把它拼进命令行，
    缺了就 AttributeError（那不是本文件要测的东西）。
    """

    def __init__(self, root: Path, runtime: Path) -> None:
        self._root, self._runtime = root, runtime
        self.args: list[str] = ["--dry-run"]

    def resolved_runtime_dir(self) -> Path:
        return self._runtime

    def resolved_path(self) -> Path:
        return self._root

    def resolved_python(self) -> Path:
        return Path(sys.executable)

    def validate_paths(self) -> list[str]:
        return []


@pytest.fixture()
def mgr(tmp_path: Path) -> EngineManager:
    # ★ 刻意**不**在这里建 QCoreApplication / QApplication。
    #   本仓 Qt 测试跑在 offscreen 平台下；本文件自己造一个 app 实例会
    #   与后续 widget 测试的 app 实例冲突，实测直接把整个 pytest 进程
    #   打成 STATUS_STACK_BUFFER_OVERRUN（本文件单跑 10 passed，
    #   混在全量里就崩）。QTimer 没事件循环也只是不触发，不影响断言。
    return EngineManager(_FakeEngineConfig(tmp_path, tmp_path / "_runtime"))


# ----------------------------------------------------------------------
# 护栏本体
# ----------------------------------------------------------------------
def test_start_refuses_when_blocked_reason_given(mgr: EngineManager) -> None:
    """传入阻塞理由 -> 必须拒绝，且**不能**真的拉起进程。"""
    lines: list[str] = []
    mgr.log_line.connect(lines.append)

    ok = mgr.start(blocked_reason="寻路正在运行 —— 不能并行")

    assert ok is False
    assert mgr.state() == "stopped"
    assert lines, "拒绝时必须让用户看到原因，不能只写日志"
    assert "寻路" in lines[-1]


def test_start_refusal_emits_nothing_to_process(mgr: EngineManager) -> None:
    """被拒绝后 ``is_running()`` 必须是 False —— 没起进程，也没留下半死的 Popen。"""
    mgr.start(blocked_reason="blocked")
    assert mgr.is_running() is False
    assert mgr._proc is None


def test_start_without_blocked_reason_proceeds_to_spawn(
        mgr: EngineManager, monkeypatch: pytest.MonkeyPatch) -> None:
    """没有阻塞理由时**必须放行到 Popen**。

    用 Popen 抛异常的方式断言「走到了 spawn 这一步」——
    这样既能证明护栏没有误伤，又不会真的拉起子进程。
    抛异常时 start() 走的是 ``_emit_state("crashed")`` 分支。
    """
    import subprocess

    from neko_console.channels import engine as engine_mod

    def _boom(*a, **k):
        raise OSError("spawn 被测试拦下")

    monkeypatch.setattr(engine_mod.subprocess, "Popen", _boom)

    ok = mgr.start()

    assert ok is False
    # 关键断言：状态是 crashed（= 走到了 Popen 并失败），
    # 而不是 stopped（= 在入口就被 blocked_reason 拦掉了）
    assert mgr.state() == "crashed", (
        f"状态是 {mgr.state()!r}，说明没走到 Popen —— "
        "护栏误伤了正常启动")


def test_start_with_empty_blocked_reason_proceeds(
        mgr: EngineManager, monkeypatch: pytest.MonkeyPatch) -> None:
    """``blocked_reason=""``（空串）视为**没有阻塞** —— 空串不是理由。"""
    from neko_console.channels import engine as engine_mod

    monkeypatch.setattr(
        engine_mod.subprocess, "Popen",
        lambda *a, **k: (_ for _ in ()).throw(OSError("stop here")))

    mgr.start(blocked_reason="")
    assert mgr.state() == "crashed"


# ----------------------------------------------------------------------
# 与 PathfindChannel 的对称性
# ----------------------------------------------------------------------
def test_guards_are_symmetric(mgr: EngineManager) -> None:
    """两侧护栏必须**方向相反但都存在**。

    这是本轮唯一有资格抓回归的断言：将来有人把 ``blocked_reason``
    形参悄悄删掉、或把 PathfindChannel 的 engine_running 删掉，这里会红。
    """
    import inspect

    from neko_console.channels.pathfind import PathfindChannel

    eng_sig = inspect.signature(EngineManager.start)
    pf_sig = inspect.signature(PathfindChannel.start)

    assert "blocked_reason" in eng_sig.parameters, (
        "EngineManager.start 的 blocked_reason 形参被删了 —— "
        "「寻路在跑时启动引擎」的护栏就没了")
    assert "engine_running" in pf_sig.parameters, (
        "PathfindChannel.start 的 engine_running 形参被删了 —— "
        "「引擎在跑时启动寻路」的护栏就没了")


# ----------------------------------------------------------------------
# app.py 的三个启动入口必须都传阻塞理由
# ----------------------------------------------------------------------
def test_app_start_engine_entry_passes_block(mgr: EngineManager) -> None:
    """``START_ENGINE`` / ``RESTART_ENGINE`` 两个入口必须查寻路。"""
    import inspect

    from neko_console import app as app_mod

    src = inspect.getsource(app_mod.MainWindow._on_bridge_command)
    assert "blocked_reason=self._pathfind_block()" in src, (
        "START_ENGINE 没有传 blocked_reason —— 插件面板仍能绕过护栏")
    assert "_pathfind_block()" in src.split("RESTART_ENGINE")[1][:800], (
        "RESTART_ENGINE 没有查寻路")


def test_engine_tab_passes_block() -> None:
    """引擎页「启动」按钮必须查寻路。"""
    import inspect

    from neko_console.tabs.engine import EngineTab

    src = inspect.getsource(EngineTab._on_start)
    assert "blocked_reason=" in src, (
        "引擎页的 _on_start 没有传 blocked_reason —— 第三个绕过入口")


# ----------------------------------------------------------------------
# _pathfind_block 本身
# ----------------------------------------------------------------------
def test_pathfind_block_returns_reason_when_running() -> None:
    class _PF:
        def is_running(self) -> bool:
            return True

    from neko_console.app import MainWindow

    w = MainWindow.__new__(MainWindow)          # 不跑 __init__（不起 Qt 窗口）
    w.pathfind = _PF()
    reason = MainWindow._pathfind_block(w)
    assert reason and "寻路" in reason


def test_pathfind_block_returns_none_when_not_running() -> None:
    class _PF:
        def is_running(self) -> bool:
            return False

    from neko_console.app import MainWindow

    w = MainWindow.__new__(MainWindow)
    w.pathfind = _PF()
    assert MainWindow._pathfind_block(w) is None


def test_pathfind_block_swallows_exception() -> None:
    """护栏读不到时必须放行 —— 不能因为寻路状态异常就让引擎永远起不来。"""
    class _PF:
        def is_running(self) -> bool:
            raise RuntimeError("boom")

    from neko_console.app import MainWindow

    w = MainWindow.__new__(MainWindow)
    w.pathfind = _PF()
    assert MainWindow._pathfind_block(w) is None
