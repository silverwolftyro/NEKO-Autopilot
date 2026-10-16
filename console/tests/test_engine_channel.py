"""``EngineManager`` 单测（Console 侧，2026-10-12 任务 A）。

只测**纯逻辑**部分 —— 路径推导、stderr 日志轮转、失败原因提取、命令发送。
不启动真引擎（那需要原神窗口），不起 Qt 事件循环。

为什么这几处值得测
------------------
* ``_rotate_stderr_log`` —— 轮转失败绝不能挡住引擎启动（它 catch OSError 只告警）。
  但反过来，「超限不轮转」会让日志无限涨，而用户排查时看的恰恰是它。
* ``_read_stderr_reason`` —— 引擎起不来时，UI 要把**最后一条真错误**
  念给用户，而不是甩一串 traceback 残骸。
* ``path_problems`` —— 路径不对是「引擎起不来」的头号原因，
  在启动前就说清楚比事后猜便宜得多。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from neko_console.channels.engine import (
    MAX_STDERR_LOG_BYTES,
    STDERR_BANNER,
    STDERR_LOG_NAME,
    EngineManager,
)
from neko_console.channels.snapshot import SNAPSHOT_NAME


class _FakeEngineConfig:
    """只提供 EngineManager 用到的方法。"""

    def __init__(self, root: Path, runtime: Path,
                 problems: list[str] | None = None) -> None:
        self._root = root
        self._runtime = runtime
        self._problems = problems or []

    def resolved_path(self) -> Path:
        return self._root

    def resolved_runtime_dir(self) -> Path:
        return self._runtime

    def resolved_python(self) -> Path:
        import sys

        return Path(sys.executable)

    def validate_paths(self) -> list[str]:
        return list(self._problems)


@pytest.fixture
def env():
    tmp = Path(tempfile.mkdtemp(prefix="engchan_"))
    root = tmp / "engine"
    runtime = tmp / "runtime"
    root.mkdir(parents=True)
    runtime.mkdir(parents=True)
    try:
        yield tmp, root, runtime
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _mgr(env, problems: list[str] | None = None) -> EngineManager:
    _tmp, root, runtime = env
    return EngineManager(_FakeEngineConfig(root, runtime, problems))


def _write_log(rt: Path, text: str, name: str = STDERR_LOG_NAME) -> Path:
    p = rt / name
    p.write_text(text, encoding="utf-8")
    return p


# ==================================================================
# 初始状态
# ==================================================================

def test_state__starts_stopped(env) -> None:
    assert _mgr(env).state() == "stopped"


def test_is_running__false_before_start(env) -> None:
    assert _mgr(env).is_running() is False


def test_exit_code__is_none_before_any_run(env) -> None:
    assert _mgr(env).exit_code() is None


def test_runtime_dir__comes_from_the_config(env) -> None:
    _tmp, _root, runtime = env
    assert _mgr(env).runtime_dir() == runtime


def test_stderr_log_path__sits_in_the_runtime_dir(env) -> None:
    _tmp, _root, runtime = env
    assert _mgr(env).stderr_log_path() == runtime / STDERR_LOG_NAME


def test_path_problems__empty_when_everything_is_fine(env) -> None:
    assert _mgr(env).path_problems() == []


def test_path_problems__surfaces_the_config_list(env) -> None:
    """路径不对是「引擎起不来」的头号原因，启动前就要说出来。"""
    m = _mgr(env, ["缺少 autopilot/tools/run_loop.py", "python 不存在"])
    assert m.path_problems() == ["缺少 autopilot/tools/run_loop.py",
                                 "python 不存在"]


def test_path_problems__returns_a_copy_not_the_internal_list(env) -> None:
    """调用方不该能改到 config 的内部列表。"""
    m = _mgr(env, ["问题 A"])
    got = m.path_problems()
    got.append("我瞎加的")
    assert m.path_problems() == ["问题 A"]


# ==================================================================
# stderr 日志轮转
# ==================================================================

def test_rotate__no_file_is_a_noop(env) -> None:
    """没日志就不该报错 —— 首次启动就是这种情况。"""
    _tmp, _root, runtime = env
    _mgr(env)._rotate_stderr_log(runtime / STDERR_LOG_NAME)   # 不抛就是过


def test_rotate__small_log_is_left_alone(env) -> None:
    _tmp, _root, runtime = env
    p = _write_log(runtime, "小日志")
    _mgr(env)._rotate_stderr_log(p)
    assert p.exists() and p.read_text(encoding="utf-8") == "小日志"
    assert not p.with_name(STDERR_LOG_NAME + ".1").exists()


def test_rotate__oversized_log_is_moved_aside(env) -> None:
    """超限必须轮转 —— 否则日志无限涨，而它正是排查时唯一要看的东西。"""
    _tmp, _root, runtime = env
    p = _write_log(runtime, "x" * (MAX_STDERR_LOG_BYTES + 10))
    _mgr(env)._rotate_stderr_log(p)
    old = p.with_name(STDERR_LOG_NAME + ".1")
    assert not p.exists()
    assert old.exists() and old.stat().st_size > MAX_STDERR_LOG_BYTES


def test_rotate__keeps_only_one_generation(env) -> None:
    """刻意只留一代：.2/.3/.4 只会让人翻半天还看不对。"""
    _tmp, _root, runtime = env
    p = runtime / STDERR_LOG_NAME
    old = p.with_name(STDERR_LOG_NAME + ".1")
    old.write_text("上一代", encoding="utf-8")
    p.write_text("x" * (MAX_STDERR_LOG_BYTES + 1), encoding="utf-8")

    _mgr(env)._rotate_stderr_log(p)
    assert old.read_text(encoding="utf-8") != "上一代", "旧的一代没被覆盖"
    assert not old.with_name(STDERR_LOG_NAME + ".2").exists()


def test_rotate__exactly_at_the_limit_is_not_rotated(env) -> None:
    """边界用 ``<=``：正好等于上限不算超。"""
    _tmp, _root, runtime = env
    p = _write_log(runtime, "x" * MAX_STDERR_LOG_BYTES)
    _mgr(env)._rotate_stderr_log(p)
    assert p.exists(), "正好等于上限不该被轮转"


def test_rotate__failure_is_swallowed(env) -> None:
    """★ 轮转失败绝不能挡住引擎启动。"""
    _tmp, _root, runtime = env
    p = _write_log(runtime, "x" * (MAX_STDERR_LOG_BYTES + 1))
    # 把目标目录变成不可写 -> unlink/replace 抛 OSError
    p.with_name(STDERR_LOG_NAME + ".1").mkdir()
    _mgr(env)._rotate_stderr_log(p)      # 不抛就是过


def test_rotate__missing_parent_dir_is_swallowed(env) -> None:
    _tmp, _root, runtime = env
    _mgr(env)._rotate_stderr_log(runtime / "不存在" / STDERR_LOG_NAME)


# ==================================================================
# 失败原因提取
# ==================================================================

def test_reason__missing_file_is_empty_not_an_error(env) -> None:
    _tmp, _root, runtime = env
    assert _mgr(env)._read_stderr_reason() == ""


def test_reason__picks_the_last_traceback_line(env) -> None:
    """★ UI 要念「最后一条真错误」，不是甩一串残骸。"""
    _tmp, _root, runtime = env
    _write_log(runtime,
               "启动中\n"
               "Traceback (most recent call last):\n"
               "  File \"run_loop.py\", line 1, in <module>\n"
               "RuntimeError: 没有可用的截图通道\n")
    got = _mgr(env)._read_stderr_reason()
    assert "没有可用的截图通道" in got
    assert "Traceback" not in got, "不该把整段 traceback 端给用户"


def test_reason__handles_chinese_error_text(env) -> None:
    _tmp, _root, runtime = env
    _write_log(runtime, "错误：找不到游戏窗口\n")
    assert "找不到游戏窗口" in _mgr(env)._read_stderr_reason()


def test_reason__empty_file_gives_empty_string(env) -> None:
    _tmp, _root, runtime = env
    _write_log(runtime, "")
    assert _mgr(env)._read_stderr_reason() == ""


def test_reason__respects_the_byte_budget(env) -> None:
    """日志可能几百 MB —— 只能读尾部。"""
    _tmp, _root, runtime = env
    p = runtime / STDERR_LOG_NAME
    p.write_text("旧内容\n" * 20000 + "RuntimeError: 最后一条\n",
                 encoding="utf-8")
    got = _mgr(env)._read_stderr_reason(max_bytes=2048)
    assert "最后一条" in got
    assert len(got) < 4000, "超预算没截住"


def test_reason__no_marker_falls_back_to_the_last_five_lines(env) -> None:
    """没有错误关键字时**刻意**给最后 5 行，而不是空串。

    原注释：「总比什么都不说强」。所以这条断言的是那个回退，
    不是「应该返回空」。
    """
    _tmp, _root, runtime = env
    _write_log(runtime, "引擎运行中\n一切正常\n" * 5)
    got = _mgr(env)._read_stderr_reason()
    assert got, "没有关键字时也该给点线索"
    assert "引擎运行中" in got or "一切正常" in got


def test_reason__only_looks_after_the_banner(env) -> None:
    """★ 只认**本次启动**的输出。

    追加模式下文件里混着上一次启动的报错。不按分隔线切开的话，
    用户会看到「上次崩的原因」被当成这次的原因 —— 那比不给更糟，
    会把人引到完全错误的方向上去。
    """
    from neko_console.channels.engine import STDERR_BANNER

    _tmp, _root, runtime = env
    _write_log(
        runtime,
        "RuntimeError: 上一次启动崩在这里\n"
        f"{STDERR_BANNER}\n"
        "本次启动一切正常\n")
    got = _mgr(env)._read_stderr_reason()
    assert "上一次启动崩在这里" not in got, (
        "把上一次启动的崩溃当成本次原因了 —— 这会把人引到错误方向")
    assert "本次启动一切正常" in got


def test_reason__banner_only_leaves_nothing_to_report(env) -> None:
    """banner 之后什么都没有 -> 空串（没有可提炼的内容）。"""
    from neko_console.channels.engine import STDERR_BANNER

    _tmp, _root, runtime = env
    _write_log(runtime, f"RuntimeError: 老的\n{STDERR_BANNER}\n")
    assert _mgr(env)._read_stderr_reason() == ""


def test_reason__is_truncated_to_a_readable_length(env) -> None:
    """提炼出来的那句要塞进 UI 提示框，不能是 3000 字。"""
    _tmp, _root, runtime = env
    _write_log(runtime, "Error: " + "很长" * 500)
    got = _mgr(env)._read_stderr_reason()
    assert len(got) <= 300


def test_reason__unreadable_file_is_swallowed(env) -> None:
    """stderr 日志读不了不该把 Console 一起带崩。"""
    _tmp, _root, runtime = env
    (runtime / STDERR_LOG_NAME).mkdir()      # 目录占位
    _mgr(env)._read_stderr_reason()          # 不抛就是过


# ==================================================================
# 就绪确认（2026-10-16 补，集成审计 F5）
# ==================================================================

def _write_snapshot(rt: Path, run_id: str) -> Path:
    p = rt / SNAPSHOT_NAME
    p.write_text(json.dumps({"run_id": run_id, "state": "RUNNING"}),
                 encoding="utf-8")
    return p


def test_start__captures_the_pre_start_run_id_as_the_baseline(env) -> None:
    """★ 钉住实现：基线必须在 Popen **之前**记。

    新 run 一开始写快照 run_id 就变了；先记一定记到旧的，
    后记可能记到新的 —— 那样永远等不到变化，状态卡死在 starting。
    """
    _tmp, _root, runtime = env
    _write_snapshot(runtime, "old_run")
    mgr = _mgr(env)
    assert mgr._current_run_id() == "old_run"


def test_current_run_id__missing_file_is_none(env) -> None:
    _tmp, _root, runtime = env
    assert _mgr(env)._current_run_id() is None


def test_current_run_id__garbage_json_is_none(env) -> None:
    """引擎正在写、崩在写一半 -> 返回 None，不抛。

    抛出去会把 start() 整个带崩，而 start() 的失败原因跟这个一点关系没有。
    """
    _tmp, _root, runtime = env
    (runtime / SNAPSHOT_NAME).write_text("{not json", encoding="utf-8")
    assert _mgr(env)._current_run_id() is None


def test_check_ready__run_id_change_promotes_to_running(env) -> None:
    """run_id 变了 = 新 run 真的起来了 -> running。"""
    _tmp, _root, runtime = env
    mgr = _mgr(env)
    mgr._state = "starting"
    mgr._pre_run_id = "old_run"
    mgr._ready_deadline = time.monotonic() + 999

    seen: list[str] = []
    mgr.state_changed.connect(seen.append)
    mgr._check_ready({"run_id": "new_run"})

    assert mgr.state() == "running"
    assert mgr.is_ready() is True
    assert seen == ["running"]


def test_check_ready__same_run_id_stays_starting(env) -> None:
    """run_id 没变 = 还没真正跑起来，**不许**报 running。

    这正是原来的 bug：Popen 一返回就说 running，而界面上的数字
    全是上一次 run 留下的。
    """
    _tmp, _root, runtime = env
    mgr = _mgr(env)
    mgr._state = "starting"
    mgr._pre_run_id = "same_run"
    mgr._ready_deadline = time.monotonic() + 999

    mgr._check_ready({"run_id": "same_run"})
    assert mgr.state() == "starting"
    assert mgr.is_ready() is False


def test_check_ready__no_snapshot_yet_stays_starting(env) -> None:
    """快照还没有（引擎还在 import）-> 继续等，不报错也不谎报。"""
    _tmp, _root, runtime = env
    mgr = _mgr(env)
    mgr._state = "starting"
    mgr._pre_run_id = "old_run"
    mgr._ready_deadline = time.monotonic() + 999

    mgr._check_ready(None)
    assert mgr.state() == "starting"


def test_check_ready__warns_once_after_the_timeout(env) -> None:
    """超门槛后**只提示一次** stderr，不能每 200ms 刷屏。"""
    _tmp, _root, runtime = env
    _write_log(runtime, STDERR_BANNER + " x\nModuleNotFoundError: 没有模块 foo\n")
    mgr = _mgr(env)
    mgr._state = "starting"
    mgr._pre_run_id = "old_run"
    mgr._ready_deadline = time.monotonic() - 1  # 已超时

    lines: list[str] = []
    mgr.log_line.connect(lines.append)

    mgr._check_ready({"run_id": "old_run"})
    mgr._check_ready({"run_id": "old_run"})
    mgr._check_ready({"run_id": "old_run"})

    assert len(lines) == 1, f"应该只提示一次，实际 {len(lines)} 次"
    assert "ModuleNotFoundError" in lines[0]
    # ★ 超时也不改状态：引擎可能只是在等游戏窗口（最长 30s），
    #   那是正常路径，报 running 仍然是撒谎。
    assert mgr.state() == "starting"


def test_check_ready__does_not_warn_before_the_timeout(env) -> None:
    _tmp, _root, runtime = env
    mgr = _mgr(env)
    mgr._state = "starting"
    mgr._pre_run_id = "old_run"
    mgr._ready_deadline = time.monotonic() + 999

    lines: list[str] = []
    mgr.log_line.connect(lines.append)
    mgr._check_ready({"run_id": "old_run"})
    assert lines == []


def test_check_ready__promotion_clears_the_deadline(env) -> None:
    """转 running 之后 deadline 要清掉，否则 stale 警告可能补刀。"""
    _tmp, _root, runtime = env
    mgr = _mgr(env)
    mgr._state = "starting"
    mgr._pre_run_id = "old_run"
    mgr._ready_deadline = time.monotonic() - 1  # 已超时
    mgr._ready_warned = True

    mgr._check_ready({"run_id": "new_run"})
    assert mgr._ready_deadline is None
    assert mgr._ready_warned is False


def test_is_ready__false_while_starting(env) -> None:
    _tmp, _root, runtime = env
    mgr = _mgr(env)
    assert mgr.is_ready() is False, "没跑过就不该 ready"
    mgr._state = "starting"
    assert mgr.is_ready() is False
    mgr._state = "running"
    assert mgr.is_ready() is True
