"""管理寻路（e2e_pathfind）子进程。

★ 与 overlay 通道的根本差别
------------------------
overlay 没有 IPC 通道，Console 只能 ``terminate`` 它，且那没关系 ——
tkinter 进程没什么要清理的。

**寻路不一样**：它正在往游戏里注入按键（Shift+W）。直接 terminate 会让按键
状态不明 —— 角色可能松开鼠标之后还一直往前走，而「一直往前走」的根因
（有个进程被强杀）离症状非常远。

所以这里的 ``stop()`` 走三级：

1. 写 ``nav_stop.flag`` —— ``e2e_pathfind`` 每帧检查它，收到后**先
   ``sprint_off()`` 松键**再退出。这是唯一能让按键状态确定的路径。
2. 轮询等它自己退（默认 10 秒）。
3. 超时才 ``terminate()``，且**在返回值里如实说明**「按键状态可能不明」。

★ 绝不能与引擎并行
------------------
引擎（autopilot）和寻路都用 ``Win32Backend`` 注入同一套键鼠。一方
``release_all()`` 会松开对方按住的键，角色动作完全不可预测。所以
:meth:`PathfindChannel.start` 会**拒绝**在引擎运行时启动，而不是
「启动了再说」—— 那个错一旦发生，现象（角色乱走）和病因（两个注入源）
之间隔着十万八千里。

位置文件与 overlay 共用同一个 ``_runtime``：两个进程在同一个目录交换
状态，比各写各的可靠。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from ..config.schema import EngineConfig
from ..paths import console_commands_path

logger = logging.getLogger(__name__)

#: 存活检查间隔。寻路会写 pathfind_state.json，所以这里既能看进程也能看文件。
POLL_INTERVAL_MS = 500

STATE_FILE_NAME = "pathfind_state.json"
STOP_FILE_NAME = "nav_stop.flag"

#: 状态文件超过这么久没更新就算「陈旧」。
#:
#: ★ 为什么必须有它
#:   ``e2e_pathfind`` **每帧**重写这个文件，所以文件存在 ≠ 它还活着。
#:   寻路崩了 / 被 kill 了，文件里还留着最后一帧 —— UI 会一直显示
#:   「运行中 / MOVING」，而真实情况是五分钟前就停了。**陈旧比没有状态
#:   更危险**，因为它会让人以为寻路还在努力工作。
STALE_AFTER_S = 5.0

#: 写 flag 之后等多久才 terminate（兜底，正常路径不该走到）。
DEFAULT_GRACE_S = 10.0

#: 引擎 _runtime 下没有这个文件时，是否算「寻路没跑过」。
_ABSENT_OK = True


class PathfindChannel(QObject):
    """寻路子进程的生命周期 + 状态读取。"""

    #: "stopped" | "running"
    state_changed = Signal(str)

    def __init__(self, engine_cfg: EngineConfig,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.engine_cfg = engine_cfg
        self._proc: subprocess.Popen | None = None
        self._state = "stopped"
        self._exit_code: int | None = None
        self._last_error = ""

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

    # ==================================================================
    # 路径
    # ==================================================================
    def runtime_dir(self) -> Path:
        """与引擎 / overlay 共用同一个 _runtime。"""
        return self.engine_cfg.resolved_runtime_dir()

    def state_file(self) -> Path:
        return self.runtime_dir() / STATE_FILE_NAME

    def stop_file(self) -> Path:
        """停止信号文件。**必须与 overlay 的 NAV_STOP_FILE 同一个** ——
        浮窗点一下也要能停寻路，两个路径各算各的就会出现「卡片停了、
        寻路还在走」。"""
        return self.runtime_dir() / STOP_FILE_NAME

    def script_path(self) -> Path:
        """寻路脚本位置。解析规则见 ``EngineConfig.resolved_pathfind_script``。

        ★ 别再写死成 ``<engine.path>/autopilot/tools/`` —— 那个目录存在，
          但里面没有 ``e2e_pathfind.py``（脚本在 recognition 仓库），
          于是「启动」永远被 :meth:`path_problems` 拒掉。
        """
        return self.engine_cfg.resolved_pathfind_script()

    def python_path(self) -> Path:
        return self.engine_cfg.resolved_python()

    def path_problems(self) -> list[str]:
        """路径问题列表（空 = 可启动）。**返回而不抛** ——
        UI 要把问题显示出来，不能让主控台起不来。"""
        problems: list[str] = []
        py = self.python_path()
        if not py.is_file():
            problems.append(f"Python 解释器无效（不存在）：{py}")
        script = self.script_path()
        if not script.is_file():
            problems.append(f"找不到寻路脚本：{script}")
        return problems

    # ==================================================================
    # 状态
    # ==================================================================
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def state(self) -> str:
        return self._state

    def exit_code(self) -> int | None:
        return self._exit_code

    def last_error(self) -> str:
        return self._last_error

    def read_state(self) -> dict | None:
        """读 ``pathfind_state.json``。**读不到返回 None**（不是空 dict）。

        ★ 脚本每帧都在写，撞上「只写了一半」是常态 —— 所以
          ``json.JSONDecodeError`` 必须吞掉返回 None，下一轮再读。
          解析失败绝不能当成「寻路失败了」弹给用户。
        """
        try:
            data = json.loads(self.state_file().read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def state_age_s(self) -> float:
        """状态文件**最后修改**距今多少秒。

        ★ 用文件的 mtime，不信 JSON 里的时间戳
          ------------------------------------
          脚本每帧都重写整个文件，所以 mtime 就是「它还在动」的可靠证据；
          而 JSON 内部的时间字段格式各家不一，出错时恰好是最需要它准的时候。
          读不到文件返回 ``inf``（= 最陈旧），让调用方统一走「不可信」分支。
        """
        try:
            return max(0.0, time.time() - self.state_file().stat().st_mtime)
        except OSError:
            return float("inf")

    def is_state_stale(self) -> bool:
        """状态文件是否已陈旧（超过 :data:`STALE_AFTER_S` 没更新）。"""
        return self.state_age_s() > STALE_AFTER_S

    def read_recent_events(self, limit: int = 12) -> list[dict]:
        """从当前 run 的事件流尾部取几条，给 UI 显示。

        事件流是**引擎**写的；寻路跑的时候引擎是停的，所以这里读到的
        往往是上一次 run 的残留 —— UI 上要标出来，别让人以为寻路产生了
        这些事件。
        """
        d = self.runtime_dir()
        try:
            run_id = (d / "run_current.txt").read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return []
        if not run_id:
            return []
        stream = d / f"{run_id}.stream.jsonl"
        try:
            lines = stream.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        out: list[dict] = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                out.append(item)
        return out

    # ==================================================================
    # 启停
    # ==================================================================
    def start(self, *, start_world: str, target_world: str,
              live: bool = True, delay: float = 0.0,
              extra_args: list[str] | None = None,
              engine_running: bool = False) -> tuple[bool, str]:
        """启动寻路。返回 ``(是否成功, 原因)``。

        :param engine_running: 由调用方（tab）传入 ``EngineManager.is_running()``。
            刻意做成**参数**而不是本对象去查引擎 —— channel 之间不互相
            持有，依赖关系由 app.py 组装。

        ★ 三道护栏的顺序是有讲究的
          1) 已在跑 -> 拒（否则两个寻路抢键鼠）
          2) 引擎在跑 -> 拒（引擎和寻路抢键鼠）
          3) 路径问题 -> 拒（不然起来就崩）
        """
        if self.is_running():
            return False, f"寻路已在运行（pid={self._proc.pid}）"

        if engine_running:
            return False, (
                "引擎正在运行 —— **不能与寻路并行**。\n"
                "两者都用同一套键鼠注入，一方松键会打断另一方。\n"
                "请先在「引擎」页停掉引擎。")

        problems = self.path_problems()
        if problems:
            return False, "\n".join(problems)

        try:
            self.runtime_dir().mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return False, f"创建 runtime 目录失败：{exc}"

        # ★ 启动前删掉上一次的停止 flag。flag 的语义是「存在即停」，
        #   不清掉的话新实例一起来就秒停，而用户看到的只是「转一下就没了」。
        try:
            self.stop_file().unlink(missing_ok=True)
        except OSError as exc:
            return False, f"无法清理停止信号文件：{exc}"

        cmd = [str(self.python_path()), str(self.script_path()),
               "--start-world", start_world,
               "--target-world", target_world]
        if live:
            cmd.append("--live")
        if delay > 0:
            cmd += ["--delay", str(delay)]
        cmd += list(extra_args or [])

        env = os.environ.copy()
        env["NEKO_CONSOLE_CMD_DIR"] = str(console_commands_path().parent)
        # ★ UTF-8：e2e_pathfind 打印「→」「⚠」这类符号，GBK 编不了会
        #   直接 UnicodeEncodeError 崩掉（引擎那条路径已踩过）。
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        logger.info("启动寻路：%s", " ".join(cmd))
        try:
            # ★ stdout/stderr 必须 DEVNULL：建了 PIPE 却没人读 = 管道写满后
            #   子进程阻塞 -> 进程活着但角色不动，而 UI 显示「运行中」。
            self._proc = subprocess.Popen(  # noqa: S603 - 参数是列表，无 shell
                cmd,
                cwd=str(self.script_path().parent),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_creation_flags(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("寻路启动失败：%s", exc)
            self._proc = None
            self._last_error = str(exc)
            self._emit_state("stopped")
            return False, f"启动失败：{type(exc).__name__}: {exc}"

        self._exit_code = None
        self._last_error = ""
        self._emit_state("running")
        self._timer.start()
        return True, f"已启动 pid={self._proc.pid}"

    def stop(self, *, grace_s: float = DEFAULT_GRACE_S) -> tuple[bool, str]:
        """优雅停止：flag -> 轮询 -> terminate 兜底。

        :returns: ``(是否已停, 说明)``。说明里会如实写出是不是走了兜底 ——
            terminate 过的按键状态是未知的，UI 要能告诉用户。
        """
        if not self.is_running():
            self._timer.stop()
            self._emit_state("stopped")
            return True, "本来就没在跑"

        logger.info("停止寻路：先写 flag，等 %.1fs", grace_s)
        try:
            self.stop_file().unlink(missing_ok=True)
            self.stop_file().touch()
        except OSError as exc:
            logger.warning("写停止信号失败（将退化为 terminate）：%s", exc)

        proc = self._proc
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline:
            if proc is not None and proc.poll() is not None:
                return self._finish(proc, "已停止（flag 生效，脚本自己退的）")
            time.sleep(0.1)

        logger.warning("等待 %.1fs 后仍未退出，terminate 兜底", grace_s)
        try:
            if proc is not None:
                proc.terminate()
                proc.wait(timeout=3.0)
        except Exception as exc:  # noqa: BLE001
            logger.exception("terminate 寻路失败")
            self._last_error = str(exc)
            self._finish(proc, f"停止失败：{exc}")
            return False, f"停止失败：{exc}"
        self._finish(proc, "已 terminate 兜底（★按键状态可能不明，按一下空格/点一下游戏即可恢复）")
        return True, "已 terminate 兜底（★按键状态可能不明）"

    # ==================================================================
    # 内部
    # ==================================================================
    def _finish(self, proc: subprocess.Popen | None, msg: str) -> tuple[bool, str]:
        self._timer.stop()
        self._exit_code = proc.poll() if proc is not None else None
        self._proc = None
        self._emit_state("stopped")
        return True, msg

    def _poll(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is not None:
            self._exit_code = proc.returncode
            logger.info("寻路进程已退出，exit=%s", self._exit_code)
            self._timer.stop()
            self._proc = None
            self._emit_state("stopped")

    def _emit_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        self.state_changed.emit(state)


def _creation_flags() -> int:
    """Windows 下不给子进程弹黑框。非 Windows 返回 0。"""
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


__all__ = [
    "DEFAULT_GRACE_S",
    "POLL_INTERVAL_MS",
    "STATE_FILE_NAME",
    "STOP_FILE_NAME",
    "PathfindChannel",
]