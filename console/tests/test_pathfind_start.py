"""``PathfindChannel.start()`` 单测（Console 侧，2026-10-12 任务 A）。

上一轮测了「停止」和「状态读取」，**唯独没测「启动」** ——
而启动恰恰是三道护栏 + 原子清理 + 环境变量拼装最集中的地方。
更关键的是：**上轮用的假脚本真的被 Popen 起来了**，那已经算半个集成测试；
本文件改成**拦下 Popen**，只断言「该发出去的命令长什么样」，
这样既能覆盖参数拼装，又不会在 CI 里真的拉起子进程。

三道护栏的**顺序**是有讲究的（``pathfind.py:214-218``）：
    1) 已在跑  -> 拒（两个寻路抢键鼠）
    2) 引擎在跑 -> 拒（引擎和寻路抢键鼠）
    3) 路径有问题 -> 拒（不然起来就崩）
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from neko_console.channels.pathfind import PathfindChannel


SCRIPT = """\
import time
print("started", flush=True)
time.sleep(30)
"""


class _FakeEngineConfig:
    def __init__(self, root: Path, runtime: Path, py: Path | None = None) -> None:
        self._root, self._runtime = root, runtime
        self._py = py or Path(sys.executable)

    def resolved_path(self) -> Path:
        return self._root

    def resolved_runtime_dir(self) -> Path:
        return self._runtime

    def resolved_pathfind_script(self) -> Path:
        """本文件约定寻路脚本就在插件内（真实解析规则见
        ``EngineConfig.resolved_pathfind_script``）。"""
        return self._root / "autopilot" / "tools" / "e2e_pathfind.py"

    def resolved_python(self) -> Path:
        return self._py


@pytest.fixture
def env():
    tmp = Path(tempfile.mkdtemp(prefix="pfstart_"))
    root = tmp / "engine"
    runtime = tmp / "runtime"
    sdir = root / "autopilot" / "tools"
    sdir.mkdir(parents=True)
    runtime.mkdir(parents=True)
    (sdir / "e2e_pathfind.py").write_text(SCRIPT, encoding="utf-8")
    try:
        yield tmp, root, runtime, sdir
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class _PopenSpy:
    """拦下 Popen，记录命令与 env，并返回一个假的「活着」的进程。"""

    def __init__(self, ret=None, boom=None):
        self.calls: list[dict] = []
        self._ret = ret
        self._boom = boom

    def __call__(self, cmd, **kw):
        self.calls.append({"cmd": cmd, **kw})
        if self._boom:
            raise self._boom
        return self._ret


class _FakeProc:
    pid = 4242

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        pass

    def kill(self):
        pass


@pytest.fixture
def spy(monkeypatch):
    s = _PopenSpy(ret=_FakeProc())
    monkeypatch.setattr(subprocess, "Popen", s)
    return s


def _ch(env, **kw) -> PathfindChannel:
    _tmp, root, runtime, _sd = env
    return PathfindChannel(_FakeEngineConfig(root, runtime), **kw)


def _start(ch, **kw):
    args = {"start_world": "1,2", "target_world": "3,4"}
    args.update(kw)
    return ch.start(**args)


# ==================================================================
# 护栏 1：已在跑
# ==================================================================

def test_start__refused_when_already_running(env, spy) -> None:
    """两个寻路会抢同一套键鼠 —— 一方松键会打断另一方。"""
    ch = _ch(env)
    ch._proc = _FakeProc()
    ok, why = _start(ch)
    assert ok is False and "已在运行" in why
    assert spy.calls == []


# ==================================================================
# 护栏 2：引擎在跑
# ==================================================================

def test_start__refused_when_the_engine_is_running(env, spy) -> None:
    """★ 这条护栏是整个「引擎 / 寻路二选一」设计的理由。"""
    ch = _ch(env)
    ok, why = _start(ch, engine_running=True)
    assert ok is False
    assert "引擎" in why and "不能与寻路并行" in why
    assert spy.calls == []


def test_start__engine_check_precedes_path_problems(env, spy) -> None:
    """顺序：引擎护栏比路径检查先跑 —— 用户先要看到「停掉引擎」。"""
    _tmp, root, runtime, _sd = env
    (root / "autopilot" / "tools" / "e2e_pathfind.py").unlink()   # 路径也有问题
    ch = PathfindChannel(_FakeEngineConfig(root, runtime))
    ok, why = _start(ch, engine_running=True)
    assert ok is False
    assert "引擎" in why


# ==================================================================
# 护栏 3：路径问题
# ==================================================================

def test_start__refused_when_the_script_is_missing(env, spy) -> None:
    """脚本不在 -> 起来就崩，宁可不起。"""
    _tmp, root, runtime, sdir = env
    (sdir / "e2e_pathfind.py").unlink()
    ch = PathfindChannel(_FakeEngineConfig(root, runtime))
    ok, why = _start(ch)
    assert ok is False and why.strip()
    assert spy.calls == []


def test_start__refused_when_python_is_missing(env, spy) -> None:
    _tmp, root, runtime, _sd = env
    ch = PathfindChannel(_FakeEngineConfig(root, runtime,
                                           py=root / "没有python.exe"))
    ok, why = _start(ch)
    assert ok is False and "Python" in why


def test_start__all_path_problems_are_listed_at_once(env, spy) -> None:
    """一次列全，用户不该改一个试一次。"""
    _tmp, root, runtime, sdir = env
    (sdir / "e2e_pathfind.py").unlink()
    ch = PathfindChannel(_FakeEngineConfig(root, runtime,
                                           py=root / "没有python.exe"))
    _ok, why = _start(ch)
    assert "Python" in why and "e2e_pathfind" in why


# ==================================================================
# 正常启动：命令拼装
# ==================================================================

def test_start__succeeds_and_reports_the_pid(env, spy) -> None:
    ch = _ch(env)
    ok, why = _start(ch)
    assert ok is True and "4242" in why
    assert ch.is_running() is True


def test_start__passes_both_world_coordinates(env, spy) -> None:
    _start(_ch(env), start_world="10,20", target_world="30,40")
    cmd = spy.calls[0]["cmd"]
    assert "--start-world" in cmd and "10,20" in cmd
    assert "--target-world" in cmd and "30,40" in cmd


def test_start__adds_live_by_default(env, spy) -> None:
    """默认 live —— 不带 --live 的脚本只规划不注入按键，演示会没有效果。"""
    _start(_ch(env))
    assert "--live" in spy.calls[0]["cmd"]


def test_start__omits_live_when_asked(env, spy) -> None:
    _start(_ch(env), live=False)
    assert "--live" not in spy.calls[0]["cmd"]


def test_start__passes_delay_only_when_positive(env, spy) -> None:
    _start(_ch(env), delay=0)
    assert "--delay" not in spy.calls[0]["cmd"]


def test_start__passes_delay_when_positive(env, spy) -> None:
    _start(_ch(env), delay=2.5)
    cmd = spy.calls[0]["cmd"]
    assert "--delay" in cmd and "2.5" in cmd


def test_start__appends_extra_args_last(env, spy) -> None:
    _start(_ch(env), extra_args=["--verbose", "--speed=2"])
    cmd = spy.calls[0]["cmd"]
    assert cmd[-2:] == ["--verbose", "--speed=2"]


def test_start__runs_with_the_scripts_own_directory_as_cwd(env, spy) -> None:
    """脚本靠相对路径找配置 —— cwd 错了就找不到数据。"""
    _tmp, root, runtime, sdir = env
    _start(_ch(env))
    assert Path(spy.calls[0]["cwd"]) == sdir


# ==================================================================
# 环境变量
# ==================================================================

def test_start__forces_utf8_because_the_script_prints_symbols(env, spy) -> None:
    """★ e2e_pathfind 打印「→」「⚠」，GBK 编不了会直接崩掉。

    引擎那条路径已经踩过一次这个坑。
    """
    _start(_ch(env))
    envd = spy.calls[0]["env"]
    assert envd["PYTHONIOENCODING"] == "utf-8"
    assert envd["PYTHONUTF8"] == "1"


def test_start__tells_the_script_where_console_commands_live(env, spy) -> None:
    """脚本要把控制指令写回 Console，路径靠环境变量传。"""
    _start(_ch(env))
    envd = spy.calls[0]["env"]
    assert "NEKO_CONSOLE_CMD_DIR" in envd
    assert Path(envd["NEKO_CONSOLE_CMD_DIR"]).is_absolute()


def test_start__inherits_the_parent_environment(env, spy) -> None:
    """子进程要能读到 PATH 之类的东西。"""
    _start(_ch(env))
    envd = spy.calls[0]["env"]
    assert envd.get("PATH") == __import__("os").environ.get("PATH")


# ==================================================================
# 启动前清理
# ==================================================================

def test_start__clears_a_stale_stop_flag(env, spy) -> None:
    """★ flag 的语义是「存在即停」。不清掉的话新实例一起来就秒停，
    而用户看到的只是「转一下就没了」。"""
    ch = _ch(env)
    flag = ch.stop_file()
    flag.write_text("上次残留", encoding="utf-8")
    assert flag.exists()
    _start(ch)
    assert not flag.exists()


def test_start__stop_flag_missing_is_fine(env, spy) -> None:
    ch = _ch(env)
    assert not ch.stop_file().exists()
    ok, _ = _start(ch)
    assert ok is True


# ==================================================================
# 启动失败
# ==================================================================

def test_start__popen_failure_is_reported_not_raised(env, monkeypatch) -> None:
    """起不来要告诉用户为什么，而不是让主控台跟着崩。"""
    boom = _PopenSpy(boom=OSError("拒绝访问"))
    monkeypatch.setattr(subprocess, "Popen", boom)
    ch = _ch(env)
    ok, why = _start(ch)
    assert ok is False
    assert "启动失败" in why and "拒绝访问" in why
    assert ch.is_running() is False


def test_start__failed_start_leaves_no_stale_process(env, monkeypatch) -> None:
    """失败后 ``_proc`` 必须是 None —— 否则 is_running() 会一直说「在跑」。"""
    monkeypatch.setattr(subprocess, "Popen", _PopenSpy(boom=OSError("x")))
    ch = _ch(env)
    _start(ch)
    assert ch._proc is None


def test_start__successful_start_clears_the_last_error(env, spy) -> None:
    ch = _ch(env)
    ch._last_error = "上一轮的错"
    _start(ch)
    assert ch._last_error == ""


def test_start__successful_start_clears_the_exit_code(env, spy) -> None:
    ch = _ch(env)
    ch._exit_code = 3
    _start(ch)
    assert ch.exit_code() is None


# ==================================================================
# stdout / stderr
# ==================================================================

def test_start__does_not_create_a_pipe(env, spy) -> None:
    """★ 建了 PIPE 却没人读 = 管道写满后子进程阻塞 ->
    进程活着但角色不动，而 UI 显示「运行中」。

    这是「看起来在跑、其实卡死」最难查的一类，必须钉死 DEVNULL。
    """
    _start(_ch(env))
    kw = spy.calls[0]
    assert kw["stdout"] is subprocess.DEVNULL
    assert kw["stderr"] is subprocess.DEVNULL
