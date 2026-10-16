"""管理悬浮卡片（overlay）子进程。

★ overlay 没有 IPC 通道
----------------------
引擎（autopilot）有 ``commands.jsonl``，可以请它优雅停止；
overlay 是独立的 tkinter 进程，**什么通道都没有** —— 主控台只能
靠「进程存活」判断它。

所以这里的 ``stop()`` 用的是 ``terminate()``，**不假装优雅**：
tkinter 进程被 terminate 不会执行任何清理，但它本来也没有需要清理的
状态（窗口位置由它自己写文件，进程死了位置还在，下次启动直接读回来）。

★ 位置文件在哪
--------------
overlay 把窗口位置写在 ``<runtime_dir>/overlay_pos.json``。
runtime_dir 与引擎共用（``OverlayConfig.resolved_runtime_dir`` 委托给
``EngineConfig``）—— 两个进程在同一个目录交换状态，比各写各的可靠。

★ 刚启动时文件不存在是正常的
--------------------------
overlay 启动后要等它 ``resolve_pos()`` 落盘。头几百毫秒读不到是
预期行为，UI 显示「未定位」，**不要**当成错误。
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

from ..config.schema import EngineConfig, OverlayConfig
from ..paths import console_commands_path

logger = logging.getLogger(__name__)

#: 存活检查间隔。overlay 不写心跳，只能 poll 进程。
POLL_INTERVAL_MS = 500

POS_FILE_NAME = "overlay_pos.json"
#: 拖动缩放时的**临时**通道文件。松手后必须删掉 —— 留着会让下次启动的
#: overlay 读到上一次的中间值。与位置文件同目录，便于两边对齐。
LIVE_SCALE_FILE_NAME = "overlay_scale_live.json"

#: 传给 overlay 的环境变量名 —— 告诉它主控台的 runtime 目录在哪，
#: 它的 ↻ 按钮要往那儿写命令。与引擎侧 app.py::CONSOLE_CMD_DIR_ENV 同名。
CONSOLE_CMD_DIR_ENV = "NEKO_CONSOLE_CMD_DIR"


class OverlayManager(QObject):
    """悬浮卡片进程的生命周期。"""

    #: "stopped" | "running"
    state_changed = Signal(str)

    def __init__(self, overlay_cfg: OverlayConfig,
                 engine_cfg: EngineConfig,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cfg = overlay_cfg
        self.engine_cfg = engine_cfg
        self._proc: subprocess.Popen | None = None
        self._state = "stopped"
        self._exit_code: int | None = None

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

    # ==================================================================
    # 公开 API
    # ==================================================================
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def state(self) -> str:
        return self._state

    def exit_code(self) -> int | None:
        return self._exit_code

    def runtime_dir(self) -> Path:
        return self.cfg.resolved_runtime_dir(self.engine_cfg)

    def overlay_pos_file(self) -> Path:
        return self.runtime_dir() / POS_FILE_NAME

    def live_scale_file(self) -> Path:
        """拖动缩放的临时通道文件。**与 overlay_pos.json 同目录** ——
        两边都从各自的"位置文件所在目录"推导，天然对齐。"""
        return self.runtime_dir() / LIVE_SCALE_FILE_NAME

    # -- 实时缩放通道（拖动中）-----------------------------------------
    def write_live_scale(self, scale: float) -> bool:
        """把当前缩放写进临时通道，让正在跑的 overlay 立刻跟着变。

        ★ 拖动过程中会每秒调 20 次，所以这里的取舍和别处相反：
          **写失败必须静默**。磁盘抖一下就弹一次错误框，用户会以为程序崩了。
          真正的兜底是松手时的 :meth:`_commit_scale` —— 那时才必须可靠。

        :returns: 是否真的写成功（仅供日志/测试，UI 不据此弹窗）
        """
        try:
            f = self.live_scale_file()
            f.parent.mkdir(parents=True, exist_ok=True)
            # 先写 .tmp 再 replace —— overlay 每 80ms 读一次，
            # 撞上"正在写"的半截文件会解析失败并丢掉这一帧。
            tmp = f.with_name(f.name + ".tmp")
            tmp.write_text(
                json.dumps({"scale": float(scale),
                            "ts_ms": int(time.time() * 1000)}),
                encoding="utf-8")
            tmp.replace(f)
            return True
        except (OSError, ValueError, TypeError) as exc:
            logger.debug("写实时缩放文件失败（拖动中可忽略）：%s", exc)
            return False

    def clear_live_scale(self) -> None:
        """删掉临时通道文件。**松手后必须调用**。

        留着的话下次启动的 overlay 会读到上次的中间值，启动就闪一下。
        失败不抛 —— 这只是清理，失败的后果是下次启动可能多闪一帧。
        """
        try:
            self.live_scale_file().unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("删除实时缩放文件失败：%s", exc)

    def python_path(self) -> Path:
        return self.cfg.resolved_python(self.engine_cfg)

    def path_problems(self) -> list[str]:
        """路径问题列表（空 = 可启动）。**返回而不抛** ——
        UI 要把问题显示出来，不能让主控台起不来。"""
        problems: list[str] = []
        py = self.python_path()
        if not py.is_file():
            problems.append(f"Python 解释器无效（不存在）：{py}")
        root = self.engine_cfg.resolved_path()
        if not root.is_dir():
            problems.append(f"引擎目录无效（不存在）：{root}")
        elif not (root / "autopilot" / "tools" / "overlay.py").is_file():
            problems.append(f"引擎目录下找不到 autopilot\\tools\\overlay.py：{root}")
        return problems

    # -- 位置 -----------------------------------------------------------
    def read_position(self) -> dict | None:
        """读 ``overlay_pos.json``。**读不到返回 None**（不是 0,0）。

        ★ 文件不存在 / 半截 JSON / 字段缺失 —— 全部返回 None，
          UI 显示「未定位」。显示 ``0, 0`` 会让用户以为卡片跑到左上角了，
          而真相是「还没定位过」。
        """
        p = self.overlay_pos_file()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        if not isinstance(data.get("x"), int) or \
                not isinstance(data.get("y"), int):
            return None
        return data

    def reset_position(self) -> bool:
        """删掉位置文件。返回是否真的删了。

        ★ 卡片正在跑时**删了也没用** —— overlay 只在启动时读一次位置，
          内存里的窗口位置不会回写。要生效必须重启卡片，UI 会提示。
        """
        p = self.overlay_pos_file()
        try:
            if p.exists():
                p.unlink()
            return True
        except OSError as exc:
            logger.warning("删除卡片位置文件失败：%s", exc)
            return False

    # ==================================================================
    # 启停
    # ==================================================================
    def start(self) -> bool:
        if self.is_running():
            logger.info("卡片已在运行，忽略重复启动")
            return True

        problems = self.path_problems()
        if problems:
            for p in problems:
                logger.error("卡片路径检查失败：%s", p)
            self._emit_state("stopped")
            return False

        # overlay 读写位置文件需要目录存在
        try:
            self.runtime_dir().mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error("创建 runtime 目录失败：%s", exc)
            self._emit_state("stopped")
            return False

        cmd = [str(self.python_path()), "-m", "autopilot.tools.overlay",
               *self.cfg.resolved_args()]
        cwd = str(self.engine_cfg.resolved_path())

        # ★★ 目录来源：**主控台自己的** runtime，不是 self.runtime_dir()
        #
        #   曾经写成 self.runtime_dir()，那是**引擎**的
        #   ``<engine>/autopilot/_runtime``（它委托给 engine_cfg）。overlay
        #   于是把 RESTART_ENGINE 写进了**引擎读的**通道，而主控台读的是
        #   自己那本 —— 命令永远到不了，↻ 按钮点一百次也没反应。
        #
        #   ★ 这个坑很难自查，因为 self.runtime_dir() 对本模块的另外两个
        #     用途**恰好是对的**：
        #       · overlay_pos_file()  -> 引擎 _runtime  ✅（overlay 用
        #         __file__ 推导，落点一致）
        #       · live_scale_file()   -> 引擎 _runtime  ✅（同上）
        #     只有**命令通道**是主控台的。同一个方法两种语义，最容易混。
        #
        #   取 console_commands_path().parent 而不是自己拼 base_dir()/"runtime"：
        #   那个函数是**主控台命令文件的唯一定义处**，Poller 就按它读。
        #   从它取父目录，两个地方**在结构上不可能漂移** —— 而漂移正是
        #   本次事故的成因（两处各算各的，算岔了）。
        env = os.environ.copy()
        env[CONSOLE_CMD_DIR_ENV] = str(console_commands_path().parent)
        # ★ UTF-8：overlay 打印 "×" / "↻" / "⚠" 这些符号时，
        #   中文 Windows 的 GBK 编码不了，会直接 UnicodeEncodeError 崩掉。
        #   （引擎那条路径已经踩过这个坑了。）
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        logger.info("启动悬浮卡片：%s（cwd=%s）", " ".join(cmd), cwd)
        # ★ 单独一行打这个路径，格式固定成 "NEKO_CONSOLE_CMD_DIR=<...>"：
        #   出问题时只看这一行就能确认注入的是主控台还是引擎目录。
        logger.info("  %s=%s", CONSOLE_CMD_DIR_ENV, env[CONSOLE_CMD_DIR_ENV])

        try:
            # ★ stdout/stderr 必须 DEVNULL。
            #   建了 PIPE 却没人读 = 管道写满后 overlay 的 write() 阻塞
            #   -> 进程活着但窗口不动，而主控台显示「运行中」。
            #   overlay 的日志走它自己的文件，不靠标准输出。
            self._proc = subprocess.Popen(  # noqa: S603 - 参数是列表，无 shell
                cmd,
                cwd=cwd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_creation_flags(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("卡片启动失败：%s", exc)
            self._proc = None
            self._emit_state("stopped")
            return False

        self._exit_code = None
        self._emit_state("running")
        self._timer.start()
        return True

    def stop(self, timeout_ms: int = 3000) -> bool:
        """停掉卡片。

        overlay 没有 IPC 通道，**只能 terminate** —— 这里不假装优雅。
        它本来也没有需要清理的状态：窗口位置由它自己写进
        ``overlay_pos.json``，进程死了文件还在，下次启动直接读回来。

        :returns: True = 已退出
        """
        if not self.is_running():
            self._timer.stop()
            self._emit_state("stopped")
            return True

        logger.info("停止悬浮卡片（terminate，overlay 无 IPC 通道）")
        proc = self._proc
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=timeout_ms / 1000.0)
            except subprocess.TimeoutExpired:
                logger.warning("terminate %dms 无效，改用 kill", timeout_ms)
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:  # noqa: BLE001
                    logger.exception("强杀卡片失败")
            except Exception:  # noqa: BLE001
                logger.exception("停止卡片时异常")

        self._timer.stop()
        self._exit_code = proc.poll() if proc is not None else None
        self._proc = None
        self._emit_state("stopped")
        return True

    # ==================================================================
    # 内部
    # ==================================================================
    def _poll(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is not None:
            self._exit_code = proc.returncode
            logger.info("卡片进程已退出，exit=%s", self._exit_code)
            self._timer.stop()
            self._proc = None
            self._emit_state("stopped")

    def _emit_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        self.state_changed.emit(state)


def _creation_flags() -> int:
    """Windows 下不给卡片弹黑框。非 Windows 返回 0。"""
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


__all__ = [
    "CONSOLE_CMD_DIR_ENV",
    "LIVE_SCALE_FILE_NAME",
    "POLL_INTERVAL_MS",
    "POS_FILE_NAME",
    "OverlayManager",
]
