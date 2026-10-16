"""Console 寻路**全链路离线端到端**测试（2026-10-12）。

★ 这个文件和 ``test_pathfind_live.py`` 的区别
  ------------------------------------------
  那边起的是本文件里现写的最小假脚本，只验证「通道能不能起进程/读状态」。
  这边起的是**可复用的** ``fixtures/fake_e2e_pathfind.py`` —— 它复刻了真
  ``e2e_pathfind.py`` 的对外行为：同样的参数、同样的状态 schema（version 2）、
  同样的 ``nav_stop.flag`` 停止协议、同样的**原子写**、同样往事件流写事件。

  所以这里测的是**整条链路**：
  ``Console 点「启动」 -> 组命令行 -> Popen -> 脚本写状态 -> Console 读状态
   -> 状态变化 -> 点「停止」 -> 写 flag -> 脚本松键退出 -> Console 报「优雅停止」``

⚠️ 这些用例**真的起真子进程**（不是 mock）。所以：
  · 每个用例用独立临时目录，互不干扰
  · 超时都设了上限，卡住会失败而不是把 CI 挂死
  · ``NEKO_PLUGIN_ROOT`` 指向临时目录 —— 假脚本靠它定位 ``_runtime``，
    与真脚本 ``NEKO_PLUGIN_ROOT`` 是同一套约定
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

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from neko_console.channels.pathfind import PathfindChannel
from neko_console.config.schema import EngineConfig

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "fake_e2e_pathfind.py"

#: 插件侧正本（任务 D 的交付物）。用于镜像守卫；不存在就跳过。
PLUGIN_TOOL = (Path(PLUGIN_DIR / 'autopilot' / 'tools' / 'fake_pathfind.py'))
#: 镜像的 md5。**改假脚本时必须一起更新** —— 详见文件末尾的守卫。
FIXTURE_MD5 = "e43b0b0a110eca63a1060c7681fbb161"


# ==================================================================
# 夹具：搭一个假的插件目录树
# ==================================================================

class _Env:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.tools = root / "autopilot" / "tools"
        self.runtime = root / "autopilot" / "_runtime"
        self.script = self.tools / "fake_e2e_pathfind.py"


@pytest.fixture
def env(monkeypatch):
    """搭 ``<tmp>/autopilot/{tools,_runtime}``，并把 NEKO_PLUGIN_ROOT 指过去。"""
    if not FIXTURE.is_file():
        pytest.skip(f"假脚本缺失：{FIXTURE}")
    tmp = Path(tempfile.mkdtemp(prefix="pf_e2e_"))
    e = _Env(tmp)
    e.tools.mkdir(parents=True)
    e.runtime.mkdir(parents=True)
    shutil.copyfile(FIXTURE, e.script)
    # ★ 假脚本与真脚本用同一个环境变量名定位 _runtime
    monkeypatch.setenv("NEKO_PLUGIN_ROOT", str(e.root))
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    try:
        yield e
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _ch(env: _Env, *, python: Path | None = None) -> PathfindChannel:
    cfg = EngineConfig(
        path=str(env.root),
        python=str(python or Path(os.sys.executable)),
        pathfind_script=str(env.script),
    )
    return PathfindChannel(cfg)


def _state(env: _Env) -> dict | None:
    p = env.runtime / "pathfind_state.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _events(env: _Env) -> list[dict]:
    try:
        run_id = (env.runtime / "run_current.txt").read_text(
            encoding="utf-8").strip()
    except OSError:
        return []
    f = env.runtime / f"{run_id}.stream.jsonl"
    try:
        return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()
                if l.strip()]
    except (OSError, ValueError):
        return []


def _wait(pred, timeout=15.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return bool(pred())


# ==================================================================
# 任务 B · 启动 -> 状态 -> 到达
# ==================================================================

def test_e2e__process_really_starts_and_writes_state(env) -> None:
    """★ 进程真的起来了，状态文件真的出现了，Console 真的读到了。"""
    ch = _ch(env)
    assert ch.path_problems() == [], ch.path_problems()
    assert ch.read_state() is None, "起之前不该有状态"

    ok, msg = ch.start(start_world="0,0", target_world="30,40",
                       live=True, engine_running=False)
    assert ok is True, msg
    assert ch.is_running() is True
    try:
        assert _wait(lambda: _state(env) is not None), "假脚本没写状态文件"
        st = ch.read_state()
        assert st is not None
        assert st["schema_version"] == 2, "状态 schema 必须与真脚本一致"
        assert st["state"] == "navigating"
        assert st["seq"] >= 1
        assert st["fake"] is True, "应当能一眼看出这是假脚本写的"
        assert st["target_pos"] == [30.0, 40.0]
    finally:
        ch.stop(grace_s=10.0)


def test_e2e__state_progresses_navigating_to_arrived(env) -> None:
    """★★ 从 navigating 走到 arrived，Console 侧全程看得到中间态。"""
    ch = _ch(env)
    ok, msg = ch.start(start_world="0,0", target_world="10,10",
                       live=True, engine_running=False)
    assert ok is True, msg
    try:
        seen_navigating = _wait(lambda: (ch.read_state() or {}).get("state")
                                == "navigating")
        assert seen_navigating, "没看到 navigating"
        assert _wait(lambda: (ch.read_state() or {}).get("state") == "arrived",
                     timeout=25.0), "没走到 arrived"
        # arrived 时距离应当归零
        assert (ch.read_state() or {}).get("dist") == 0.0
    finally:
        if ch.is_running():
            ch.stop(grace_s=10.0)


def test_e2e__seq_keeps_increasing(env) -> None:
    """★ seq 每 tick +1 —— overlay 靠它判断「这确实是新状态」。"""
    ch = _ch(env)
    ch.start(start_world="0,0", target_world="100,100", live=True,
             engine_running=False)
    try:
        assert _wait(lambda: (ch.read_state() or {}).get("seq", 0) >= 2)
        s1 = (ch.read_state() or {})["seq"]
        assert _wait(lambda: (ch.read_state() or {}).get("seq", 0) > s1)
        assert (ch.read_state() or {})["seq"] > s1
    finally:
        ch.stop(grace_s=10.0)


def test_e2e__self_exit_is_detected_with_its_code(env) -> None:
    """脚本自己跑完退出，Console 要认出 exit=0 并切回 stopped。"""
    ch = _ch(env)
    ok, _ = ch.start(start_world="0,0", target_world="5,5", live=True,
                     delay=0, engine_running=False,
                     extra_args=["--seconds", "1", "--fake-tick-ms", "200"])
    assert ok is True
    assert _wait(lambda: not ch.is_running(), timeout=25.0), "脚本没自行退出"
    ch._poll()                      # 手动驱动（无 QApplication 事件循环）
    assert ch.is_running() is False
    assert ch.exit_code() == 0
    assert ch.state() == "stopped"


def test_e2e__events_reach_the_ui_side(env) -> None:
    """★ 事件流要能被 ``read_recent_events`` 读到（UI 就是读它）。"""
    ch = _ch(env)
    ch.start(start_world="0,0", target_world="10,10", live=True,
             engine_running=False,
             extra_args=["--seconds", "1", "--fake-tick-ms", "200"])
    try:
        assert _wait(lambda: {e["code"] for e in ch.read_recent_events()}
                     >= {"PATHFIND_START", "PATHFIND_ARRIVED"},
                     timeout=25.0), ch.read_recent_events()
        codes = [e["code"] for e in ch.read_recent_events()]
        assert codes[0] == "PATHFIND_START"
        assert "PATHFIND_ARRIVED" in codes
    finally:
        if ch.is_running():
            ch.stop(grace_s=10.0)


# ==================================================================
# 任务 C · 停止的两条路径
# ==================================================================

def test_stop__flag_path_is_graceful_and_script_reports_stopped(env) -> None:
    """★★ 优雅停止的完整闭环。

    ``stop()`` -> 写 ``nav_stop.flag`` -> 脚本看到 -> 先写 ``state=stopped``
    再退出 -> Console 报「flag 生效，脚本自己退的」。
    """
    ch = _ch(env)
    ok, msg = ch.start(start_world="0,0", target_world="100,100", live=True,
                       engine_running=False,
                       extra_args=["--seconds", "60", "--fake-tick-ms", "200"])
    assert ok is True, msg
    assert _wait(lambda: (ch.read_state() or {}).get("state") == "navigating")

    ok2, msg2 = ch.stop(grace_s=15.0)
    assert ok2 is True
    assert "terminate" not in msg2, msg2
    assert "flag" in msg2, msg2
    assert ch.state() == "stopped"

    # 脚本确实写下了 stopped，而不是直接消失
    st = _state(env)
    assert st is not None, "状态文件不该被删"
    assert st["state"] == "stopped", st
    assert "PATHFIND_STOPPED" in [e["code"] for e in _events(env)]


def test_stop__flag_file_is_actually_created(env) -> None:
    """停止的第一动作是写 flag，顺序反了就没意义了。"""
    ch = _ch(env)
    ch.start(start_world="0,0", target_world="100,100", live=True,
             engine_running=False,
             extra_args=["--seconds", "60", "--fake-tick-ms", "200"])
    assert _wait(lambda: (ch.read_state() or {}).get("state") == "navigating")
    ch.stop(grace_s=15.0)
    assert (env.runtime / "nav_stop.flag").is_file()


def test_stop__terminate_fallback_is_disclosed(env) -> None:
    """★★ 脚本无视 flag 时，Console 必须 terminate 并**如实说明**。

    假装干净 = 用户以为松开了，结果角色还一直往前走，而根因离症状极远。
    """
    ch = _ch(env)
    ok, msg = ch.start(start_world="0,0", target_world="100,100", live=True,
                       engine_running=False,
                       extra_args=["--fake-mode", "ignore-stop",
                                   "--seconds", "600",
                                   "--fake-tick-ms", "200"])
    assert ok is True, msg
    assert _wait(lambda: (ch.read_state() or {}).get("state") == "navigating")

    ok2, msg2 = ch.stop(grace_s=1.0)      # 给 1 秒就会走兜底
    assert ok2 is True
    assert "terminate" in msg2, msg2
    assert "按键状态可能不明" in msg2, msg2
    assert ch.state() == "stopped"


def test_stop__terminate_fallback_actually_kills_the_process(env) -> None:
    """兜底不能只是嘴上说 —— 进程得真的死。"""
    ch = _ch(env)
    ch.start(start_world="0,0", target_world="100,100", live=True,
             engine_running=False,
             extra_args=["--fake-mode", "ignore-stop", "--seconds", "600"])
    assert _wait(lambda: (ch.read_state() or {}).get("state") == "navigating")
    ch.stop(grace_s=1.0)
    assert ch.is_running() is False
    assert _wait(lambda: not ch.is_running(), timeout=5.0)


def test_stop__when_not_running_is_a_noop(env) -> None:
    ch = _ch(env)
    ok, msg = ch.stop()
    assert ok is True and "本来就没在跑" in msg


# ==================================================================
# 崩溃 / 卡住：状态「陈旧」这个最危险的场景
# ==================================================================

def test_crash__state_is_left_behind_as_stale_navigating(env) -> None:
    """★★ 崩溃后状态文件**还在、内容还是 navigating** —— 最危险的一格。

    只按「文件存在」判断的话，UI 会一直显示「运行中 / 正在前往」，
    而真实情况是它早就死了。**陈旧比没有状态更危险。"""
    ch = _ch(env)
    ok, msg = ch.start(start_world="0,0", target_world="100,100", live=True,
                       engine_running=False,
                       extra_args=["--fake-mode", "crash",
                                   "--fake-crash-after", "2",
                                   "--fake-tick-ms", "200"])
    assert ok is True, msg
    assert _wait(lambda: not ch.is_running(), timeout=20.0), "崩溃模式没退出"
    ch._poll()
    assert ch.exit_code() == 9, "崩溃应当是 exit 9"

    st = _state(env)
    assert st is not None, "崩溃不该删掉状态文件"
    assert st["state"] == "navigating", st["state"]
    assert ch.state() == "stopped"


def test_stuck__keeps_reporting_navigating_forever(env) -> None:
    """卡住时状态一直 navigating，直到用户点停。"""
    ch = _ch(env)
    ch.start(start_world="0,0", target_world="100,100", live=True,
             engine_running=False,
             extra_args=["--fake-mode", "stuck", "--fake-tick-ms", "200"])
    try:
        assert _wait(lambda: (ch.read_state() or {}).get("state") == "navigating")
        assert _wait(lambda: (ch.read_state() or {}).get("phase") == "stuck")
        assert ch.is_running() is True
    finally:
        ch.stop(grace_s=10.0)


# ==================================================================
# 与真脚本的契约一致性
# ==================================================================

def test_contract__script_accepts_the_args_console_actually_sends(env) -> None:
    """★ Console 会发的参数，假脚本必须全认得。

    参数名对不上，这条链路测的就不是「Console 拼装正确」了。
    """
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, str(env.script),
         "--start-world", "1,2", "--target-world", "3,4",
         "--delay", "0", "--seconds", "1", "--live"],
        capture_output=True, text=True, timeout=30,
        # ★ 必须显式 utf-8：``text=True`` 不给编码就用**系统默认**，
        #   Windows 上是 GBK，而假脚本打印 UTF-8（真脚本也这么干，
        #   Console 自己拉它时就是靠 PYTHONIOENCODING=utf-8）。
        #   不写这一行的话，测试会以 UnicodeDecodeError 失败 ——
        #   症状和「脚本崩了」长得一模一样。
        encoding="utf-8", errors="replace",
        env={**os.environ, "NEKO_PLUGIN_ROOT": str(env.root)})
    assert r.returncode == 0, r.stderr
    assert "arrived" in r.stdout.lower(), r.stdout


def test_contract__missing_required_args_exits_2(env) -> None:
    """★ 真脚本缺参数是 exit 2，不是崩。假脚本照做。"""
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, str(env.script), "--start-world", "1,2"],
        capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace",
        env={**os.environ, "NEKO_PLUGIN_ROOT": str(env.root)})
    assert r.returncode == 2, r.stdout + r.stderr


def test_contract__stale_stop_flag_is_cleared_at_startup(env) -> None:
    """★ 上次残留的 flag 不清掉，新实例一起来就秒停。"""
    flag = env.runtime / "nav_stop.flag"
    flag.write_text("上次残留", encoding="utf-8")

    ch = _ch(env)
    ok, msg = ch.start(start_world="0,0", target_world="100,100", live=True,
                       engine_running=False,
                       extra_args=["--fake-mode", "stuck",
                                   "--fake-tick-ms", "200"])
    assert ok is True, msg
    try:
        # 起来了且没被残留 flag 秒停
        assert _wait(lambda: (ch.read_state() or {}).get("state") == "navigating")
        assert ch.is_running() is True
    finally:
        ch.stop(grace_s=10.0)


# ==================================================================
# 任务 D · 镜像守卫
# ==================================================================

def test_mirror__fixture_matches_the_plugin_tool() -> None:
    """★ Console 侧夹具与插件侧正本必须**逐字节相同**。

    这是两个仓库里同一份「假寻路」逻辑。不加守卫的话，两边各自演进几个月后
    就不知���道哪份才是当前行为了 —— 而这类漂移极难察觉，因为它不会报错。
    做法照搬仓库既有约定（配置层镜像感知层常量 + 单测钉住）。
    """
    if not PLUGIN_TOOL.is_file():
        pytest.skip(f"本机没有插件侧正本 {PLUGIN_TOOL}")
    assert FIXTURE.read_bytes() == PLUGIN_TOOL.read_bytes(), (
        "tests/fixtures/fake_e2e_pathfind.py 与插件侧 fake_pathfind.py 已漂移。"
        "改了一边就要把另一边同步过来。")


def test_mirror__fixture_content_is_pinned() -> None:
    """即使插件侧不存在，也能靠这个 md5 发现夹具被单方面改过。"""
    import hashlib

    assert hashlib.md5(FIXTURE.read_bytes()).hexdigest() == FIXTURE_MD5, (
        "假脚本内容变了却没同步 FIXTURE_MD5。改脚本时把这个常量一起更新。")
