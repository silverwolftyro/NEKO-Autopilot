"""QApplication 封装 + 单例保护（QLocalServer IPC）+ 主窗口（左侧竖排 tab）。

★ 单例机制：QLocalServer + QLocalSocket（命名管道）
----------------------------------------------
第二个实例启动时的三个选项（打开 / 关闭旧实例并重开 / 取消）**必须有
一条通道才能实现** —— 纯 QSharedMemory 只能回答"有没有别人在跑"，
回答不了"把那个窗口叫到前台"。

崩溃恢复为什么天然成立：Windows 上 QLocalServer 底层是**命名管道**，
它是内核对象，进程一死（含崩溃 / 强杀）句柄立刻关闭、名字随即消失。
所以"连不上"天然等价于"没有活着的实例"，不需要额外的存活探测，
也不会出现"上次崩了，用户永远起不来"。

早先用过 QSharedMemory，现在**完全移除** —— 两套机制回答同一个问题
（有没有别人在跑），必然出现"一个说有、一个说没有"的分裂状态。

★ 为什么不用「检查端口 / 找进程」
------------------------------
* 端口方案：要挑空闲端口、要处理 TIME_WAIT、还要防 PID 复用
* 进程名方案：上一个实例崩了之后名字还在列表里，用户就永远起不来
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QListWidget, QMainWindow,
                               QMessageBox, QScrollArea, QSplitter,
                               QStackedWidget, QVBoxLayout, QWidget)

from . import __version__
from .bridge import (BRIDGE_SCHEMA_VERSION, BRIDGE_WRITE_INTERVAL_MS,
                      BridgeWriter, CommandPoller, _pid_of)
from .channels.engine import EngineManager
from .channels.overlay import OverlayManager
from .channels.pathfind import PathfindChannel
from .channels.snapshot import SnapshotReader
from .config import ConsoleConfig, load_config, save_config
from .paths import asset, base_dir, is_frozen
from .tabs import (AboutTab, EngineTab, HomeTab, OverlayTab, PathfindTab,
                   RulesTab)
from .tray import Tray

logger = logging.getLogger(__name__)

#: IPC 服务名。改名会让正在运行的旧实例"失联"（它还监听老名字），
#: 所以只在做大版本变更时才改。
IPC_SERVER_NAME = "NekoConsole.IPC"

#: 命令字。两边必须一致。
CMD_SHOW = "SHOW"
CMD_SHUTDOWN = "SHUTDOWN"

#: 硬退出兜底的延迟（秒）。
#:
#: 给正常路径留的窗口：停引擎(≤3s) + 停卡片(≤2s) 都在**同步**做完，
#: 真正跑到这一步时其实已经只剩收尾，所以 0.5s 很宽裕。
#:
#: ★ 关键不是数值，是这个定时器**必须用 threading 而不是 QTimer**
#:   —— 见 :meth:`MainWindow._arm_hard_exit` 的说明。
HARD_EXIT_DELAY_S = 0.5

#: IPC 超时（毫秒）。刻意很短 —— 命名管道在 Windows 上是内核对象，
#: 连不上通常就是"没人监听"，等久了只会让启动变卡。
IPC_CONNECT_TIMEOUT_MS = 300
IPC_WRITE_TIMEOUT_MS = 300
#: 等旧实例真正退出的上限。超过就放弃"接管"，不硬来。
IPC_EXIT_WAIT_MS = 3000


def _ipc_connect(timeout_ms: int = IPC_CONNECT_TIMEOUT_MS) \
        -> QLocalSocket | None:
    """尝试连上已有实例。连不上返回 ``None``。

    ★ 崩溃恢复为什么天然成立
    ----------------------
    Windows 上 QLocalServer 用**命名管道**，它是内核对象，进程一死
    （包括崩溃 / 强杀）句柄立刻关闭，名字随即消失。所以"连不上"就
    等价于"没有活着的实例"，不需要额外的存活探测。

    Unix 上是 socket 文件，崩溃会留下残骸 —— 那种情况下靠
    :func:`_become_primary` 里的 ``removeServer`` 清理。
    """
    sock = QLocalSocket()
    sock.connectToServer(IPC_SERVER_NAME)
    if not sock.waitForConnected(timeout_ms):
        sock.abort()
        return None
    return sock


def _ipc_send(sock: QLocalSocket, command: str) -> bool:
    """往已有实例发一条命令。"""
    try:
        sock.write(command.encode("utf-8"))
        sock.waitForBytesWritten(IPC_WRITE_TIMEOUT_MS)
        sock.disconnectFromServer()
        return True
    except Exception as exc:  # noqa: BLE001 - IPC 失败不该崩掉启动流程
        logger.warning("发送 IPC 命令失败（%s）：%s", command, exc)
        return False


def _wait_primary_gone(timeout_ms: int = IPC_EXIT_WAIT_MS) -> bool:
    """轮询直到连不上（= 旧实例真的退出了），或超时返回 False。"""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        sock = _ipc_connect(150)
        if sock is None:
            return True
        sock.abort()
        time.sleep(0.05)
    return False


def _become_primary(app: QApplication) -> QLocalServer | None:
    """把自己变成服务方。失败返回 ``None``。

    ``removeServer`` 是为了清掉崩溃残留（Unix 的 socket 文件）。
    Windows 上对命名管道是 no-op，无害。
    """
    QLocalServer.removeServer(IPC_SERVER_NAME)
    server = QLocalServer(app)
    if not server.listen(IPC_SERVER_NAME):
        logger.error("无法监听 IPC 名称 %s：%s",
                     IPC_SERVER_NAME, server.errorString())
        return None
    logger.debug("IPC 服务已监听：%s", IPC_SERVER_NAME)
    return server


def _ask_existing_instance(sock: QLocalSocket) -> str:
    """弹三选一对话框。返回 ``"open"`` / ``"reopen"`` / ``"cancel"``。"""
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle("主控台已在运行")
    box.setText("已有一个主控台在运行。请选择操作。")

    b_open = box.addButton("打开已有实例",
                          QMessageBox.ButtonRole.AcceptRole)
    b_reopen = box.addButton("关闭旧实例并重开",
                             QMessageBox.ButtonRole.DestructiveRole)
    b_cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(b_open)
    box.exec()

    clicked = box.clickedButton()
    if clicked is b_open:
        return "open"
    if clicked is b_reopen:
        return "reopen"
    return "cancel"


class MainWindow(QMainWindow):
    """主窗口：左侧竖排 tab 列表，右侧内容区。"""

    def __init__(self, cfg: ConsoleConfig) -> None:
        super().__init__()
        self.cfg = cfg
        self._quitting = False

        # ★ 标题带版本号：托盘最小化之后，用户从任务栏/alt-tab 唯一能
        #   确认"我跑的是哪个版本"的地方就是这里。
        self.setWindowTitle(f"NEKO-Console v{__version__}")
        icon = QIcon(str(asset("assets/icon.ico")))
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.resize(cfg.window_width, cfg.window_height)
        # ★ 自由缩放下限（2026-10-16）
        #   Qt 在**没显式设 minimumSize** 时会把 minimumSizeHint 当硬地板。
        #   实测不设的话是 2020x961 —— 引擎 tab 的表格宽度 1876 顶宽，
        #   寻路 tab 的 MapPicker(340) + 事件流(120) 逐项相加顶高 961，
        #   于是窗口根本缩不下去。显式设一个值就**接管**了地板。
        self.setMinimumSize(320, 240)

        self._tray = Tray(on_open=self.restore_window, on_quit=self.quit_app)

        # 引擎管理器：主控台**持有**它（不做 QObject parent 绑定，
        # 因为 QTimer 在 EngineManager 内部，parent 语义容易绕）
        self.engine = EngineManager(cfg.engine, parent=self)
        self.cfg = cfg
        # 悬浮卡片管理器
        self.overlay = OverlayManager(cfg.overlay, cfg.engine, parent=self)
        # 寻路管理器。⚠️ 与 overlay 的关键差别：它有 ``nav_stop.flag`` 这条
        #   IPC 通道，所以 stop() 能走「先 flag 让脚本自己松键退出」的路，
        #   而不是像 overlay 那样只能 terminate。
        self.pathfind = PathfindChannel(cfg.engine, parent=self)
        # ★ 当前是不是管理员。寻路的 UIPI 门禁要求提权，所以 UI 必须能
        #   据此把「启动寻路」置灰，而不是让用户点了之后看进程闪退。
        from neko_console.elevation import is_admin

        self.is_admin = is_admin()
        if not self.is_admin:
            logger.warning("当前非管理员 —— 寻路（--live）不可用："
                           "UIPI 门禁会 3 秒内 exit 2")

        # ── Autopilot B 轨：与 N.E.K.O. 插件的双向文件桥 ──────────
        # 状态文件（主控台 -> 插件）：每 BRIDGE_WRITE_INTERVAL_MS 写一次
        self._bridge = BridgeWriter(base_dir())
        self._snapshot_reader = SnapshotReader(cfg.engine.resolved_runtime_dir())
        # 命令文件（插件 -> 主控台）：与状态同一个定时器里轮询，省一个 QTimer
        self._command_poller = CommandPoller(base_dir(), self._on_bridge_command)
        self._bridge_timer = QTimer(self)
        self._bridge_timer.setInterval(BRIDGE_WRITE_INTERVAL_MS)
        self._bridge_timer.timeout.connect(self._on_bridge_tick)
        self._bridge_timer.start()
        logger.info("B 轨桥接已就绪：状态文件=%s", self._bridge.path())

        self._list = QListWidget()
        self._list.setFixedWidth(140)
        self._stack = QStackedWidget()
        tabs = [
            ("首页", HomeTab(self.engine, pathfind=self.pathfind)),
            ("引擎", EngineTab(self.engine, pathfind=self.pathfind)),
            ("规则", RulesTab()),
            ("悬浮卡片", OverlayTab(self.overlay, cfg, engine=self.engine)),
            ("寻路", PathfindTab(self.pathfind, self.engine,
                                  is_admin=self.is_admin)),
            ("关于", AboutTab()),
        ]
        for name, widget in tabs:
            self._list.addItem(name)
            self._stack.addWidget(self._wrap_scrollable(widget))
        self._list.currentRowChanged.connect(self._stack.setCurrentIndex)
        self._list.setCurrentRow(0)

        # 引擎启停 -> 联动卡片（仅当 follow_engine 打开）
        self.engine.state_changed.connect(self._follow_engine)

        split = QSplitter()
        split.addWidget(self._list)
        split.addWidget(self._stack)
        split.setStretchFactor(1, 1)

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)
        self.setCentralWidget(central)
        # 注意：**不要**在这里写 self.closeEvent.connect(...)。
        # closeEvent 是 Qt 的虚方法（重写实现），不是信号 ——
        # self.closeEvent 拿到的是绑定方法对象，没有 .connect()。
        # 正确做法见下方 closeEvent()。

    @staticmethod
    def _wrap_scrollable(widget: QWidget) -> QScrollArea:
        """把一个 tab 包进 QScrollArea，让它在**小于自身自然尺寸**时改为滚动。

        ★ 为什么必须有这层（2026-10-16）
          ``setMinimumSize(320, 240)`` 只把**地板**降下来了，但 tab 内部
          各控件的 minimumSizeHint 是逐项相加的（寻路 tab 光 MapPicker 340
          + 事件流 120 就 460）。地板比这些小，Qt 不会报错，只会把控件
          压扁/裁掉 —— 表现为控件重叠、按钮点不到。
          包一层滚动区，内容不够高就出滚动条，而不是互相压扁。

        ★ 为什么设 ``setMinimumSize(0, 0)``
          QScrollArea 的 sizeHint 来自被包 widget；不清掉显式最小尺寸的
          话，widget 自己声明的 minimumSize 仍会透过滚动区顶住地板。

        ``widgetResizable(True)`` —— 窗口变大时内容跟着铺满，而不是
        挤在左上角一小块。
        """
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.NoFrame)
        widget.setMinimumSize(0, 0)
        area.setWidget(widget)
        return area

    # -- IPC（第二个实例发来的命令）---------------------------------
    def handle_ipc(self) -> None:
        """处理所有待处理的 IPC 连接。由 ``server.newConnection`` 驱动。"""
        server = getattr(self, "_ipc_server", None)
        if server is None:
            return
        while server.hasPendingConnections():
            sock = server.nextPendingConnection()
            if sock is None:
                continue
            # 挂成子对象：否则局部变量一退，socket 被 GC，连接中途断掉
            sock.setParent(self)
            sock.readyRead.connect(
                lambda s=sock: self._on_ipc_ready(s))

    def _on_ipc_ready(self, sock: QLocalSocket) -> None:
        payload = bytes(sock.readAll()).decode("utf-8", errors="replace")
        command = payload.strip().upper()
        logger.info("收到 IPC 命令：%s", command or "<空>")

        if command == CMD_SHOW:
            # 从托盘弹出 / 切到前台
            self.restore_window()
        elif command == CMD_SHUTDOWN:
            # ★ 必须**真的退出**，不能只是隐藏 —— 否则第二个实例等不到
            #   "旧实例已消失"，双方僵持。
            #
            #   这里走的就是 :meth:`quit_app`，它末尾已经武装了
            #   ``threading.Timer`` 硬退出兜底 —— 无论事件循环死没死、
            #   解释器关闭卡没卡住，500ms 后进程一定消失。所以第二个
            #   实例的 ``_wait_primary_gone()`` 现在能可靠地等到。
            #
            #   singleShot(0) 只是让当前这轮事件处理先跑完
            #   （socket 得以正常收尾），不影响后面的硬退出。
            QTimer.singleShot(0, self.quit_app)
        else:
            logger.debug("忽略未知 IPC 命令：%r", command)

        sock.disconnectFromServer()

    # -- 关窗 ------------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名，必须保持
        """重写虚方法，而不是连信号。

        ★ 为什么必须重写
        --------------
        ``QWidget.closeEvent`` 是 protected 虚方法。PySide6 把它暴露成
        Python 的绑定方法（``builtin_function_or_method``），**不是 Signal**，
        所以 ``self.closeEvent.connect(...)`` 会抛
        ``AttributeError: 'builtin_function_or_method' object has no
        attribute 'connect'``。

        ★ 为什么必须 ignore
        -------------------
        不调 ``event.ignore()`` 的话，Qt 会**销毁窗口** —— 托盘图标还在
        但界面已经没了，"托盘双击恢复"就会恢复出一个空壳（子控件已销毁）。
        ignore 掉才是"只隐藏不销毁"。
        """
        if self._quitting:
            # 退出流程中来的 close：放行，让 Qt 正常销毁窗口。
            # ★ 这里**绝不能 hide()** —— 否则窗口只是隐藏、子控件没销毁，
            #   托盘也退出了，用户就彻底失去所有入口。
            event.accept()
            logger.info("closeEvent：退出流程中，接受关闭（窗口将被销毁）")
            return

        event.ignore()
        self.hide()
        # ★ 只隐藏窗口。托盘图标**保持显示** —— 它是用户唯一还能
        #   交互的入口（双击恢复 / 右键退出）。缩到托盘期间托盘必须活着。
        logger.info("closeEvent：已隐藏窗口，托盘图标保持显示")
        self._on_close()

    def _on_close(self) -> None:
        """缩到托盘的**附带动作**（不含 Qt 契约本身）。

        首次隐藏时弹一次气泡提示，并把"提示过了"写回配置 —— 用户不该
        被同一个提示反复打扰。
        """
        if self.cfg.tray_notice_shown:
            return
        self._tray.notify_minimized()
        self.cfg.tray_notice_shown = True
        save_config(self.cfg)

    def restore_window(self) -> None:
        """托盘双击 / 菜单「打开」：恢复窗口并置顶。"""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _follow_engine(self, state: str) -> None:
        """引擎启停时联动卡片（仅当用户勾了 follow_engine）。

        ★ 只在"引擎启停"时联动，**缩到托盘时绝不联动** ——
          用户只是收起窗口，不是在关服务。
        """
        mgr = getattr(self, "overlay", None)
        if mgr is None or not self.cfg.overlay.follow_engine:
            return
        # ★ 含 "starting"（2026-10-16，集成审计 F5）
        #   卡片要跟着引擎**进程**起来，不是等它跑完 import。
        #   引擎等游戏窗口最长 30 秒，让用户对着空桌面等 30 秒才看到卡片，
        #   比"卡片先出来、里面数字是空的"糟糕得多 —— 后者至少知道在动。
        if state in ("starting", "running"):
            logger.info("follow_engine：引擎启动 -> 启动卡片（state=%s）", state)
            mgr.start()
        else:
            logger.info("follow_engine：引擎停止 -> 停止卡片")
            mgr.stop(timeout_ms=2000)

    # -- Autopilot B 轨：与 N.E.K.O. 插件的双向桥接 --------------------
    def _on_bridge_tick(self) -> None:
        """B 轨一个心跳周期：**先收命令，再写状态**。

        顺序有讲究：命令先收，才不会在"插件刚点了启动引擎"的那一拍就把
        「引擎未启动」写回去，面板上会看到按钮点了但状态不变。
        """
        try:
            self._command_poller.poll()
        except Exception:  # noqa: BLE001 - 命令轮询炸了不能停掉心跳
            logger.exception("B 轨命令轮询异常")
        self._write_bridge(running=True)

    def _write_bridge(self, *, running: bool) -> None:
        """把主控台/引擎/卡片状态写进桥接文件。

        :param running: 写 ``false`` 只在退出流程里用一次，给插件一个
            "主控台正在退"的信号。插件据此把面板置灰，而不是继续显示
            一份已经没人更新的状态。
        """
        try:
            snap = self._snapshot_reader.read()
        except Exception:  # noqa: BLE001 - 快照读不到不该拖垮心跳
            snap = None

        stats: dict[str, Any] = {
            "tick_hz": None, "tick_p95_ms": None, "scene": None,
            "intent_count": None, "idle_streak": None,
        }
        if isinstance(snap, dict):
            intent = snap.get("intent")
            intent = intent if isinstance(intent, dict) else {}
            stats = {
                "tick_hz": snap.get("tick_hz"),
                "tick_p95_ms": snap.get("tick_p95_ms"),
                "scene": snap.get("scene"),
                "intent_count": intent.get("count"),
                "idle_streak": intent.get("idle_streak"),
            }

        payload: dict[str, Any] = {
            "schema_version": BRIDGE_SCHEMA_VERSION,
            "written_at_ms": int(time.time() * 1000),
            "console": {
                "running": running,
                "version": __version__,
                "pid": os.getpid(),
            },
            "engine": {
                "state": self.engine.state(),
                "pid": _pid_of(self.engine),
            },
            "overlay": {
                "state": self.overlay.state(),
                "pid": _pid_of(self.overlay),
            },
            # ★ pathfind 字段（2026-10-16 补）
            #   之前桥接里只有 console/engine/overlay，**插件面板因此不知道
            #   寻路在跑** —— 面板显示「引擎已停止」时，寻路可能正在注入按键。
            #   加了这个字段，面板才能在 START_ENGINE 时给出互斥提示。
            #   ⚠️ 两侧契约必须同改（neko_console/bridge.py ↔ autopilot_bridge.py
            #      ↔ __init__.py 的 context），否则面板会静默失联。
            "pathfind": {
                "state": self.pathfind.state(),
                "pid": _pid_of(self.pathfind),
            },
            "stats": stats,
        }
        self._bridge.write(payload)

    def _pathfind_block(self) -> str | None:
        """寻路在跑时，返回一句「不能启动引擎」的理由；否则 ``None``。

        ★ 这条护栏的**另一半**
          :meth:`PathfindChannel.start` 里已经有反向检查
          （``engine_running`` 参数）—— 寻路拒绝在引擎运行时启动。
          本方法补上缺失的那一半：引擎也拒绝在寻路运行时启动。
          缺了它，「先跑寻路 → 再点启动引擎」会让两个注入源抢同一套键鼠。

        :returns: 人类可读的**句子片段**（不是完整句子），调用方负责接成
            完整提示，避免同一句话说三遍措辞不一致。
        """
        pf = getattr(self, "pathfind", None)
        if pf is None:
            return None
        try:
            if not pf.is_running():
                return None
            return "寻路正在运行 —— 引擎与寻路抢同一套键鼠，不能并行"
        except Exception:  # noqa: BLE001 - 读不到就当没跑，绝不能因此卡住启动
            logger.exception("检查寻路状态失败，按「未运行」处理")
            return None

    def _on_bridge_command(self, cmd: str, args: dict[str, Any]) -> None:
        """插件发来的命令。**命令名已在 :class:`CommandPoller` 里校验过**，
        这里只做分发。

        ★ QUIT_CONSOLE 要等一拍再执行
          主控台此刻正在处理的是"读到一个命令"这个回调。直接同步退出会
          在自己的事件处理中途拆掉窗口。丢给事件循环下一轮，干净。
        """
        logger.info("B 轨收到命令：%s args=%s", cmd, args)
        if cmd == "START_ENGINE":
            self.engine.start(blocked_reason=self._pathfind_block())
        elif cmd == "STOP_ENGINE":
            self.engine.stop()
        elif cmd == "RESTART_ENGINE":
            # ★ 只做「停 + 启」，不插手别的。引擎是本进程持有的 Popen，
            #   重启的每一步都必须发生在这里 —— 任何别的"重启"实现都会
            #   绕过优雅停止（release_all），让角色一直往前走。
            #   引擎没在跑时跳过 stop：对已退出的进程调 stop 没有意义。
            #
            # ★ 寻路在跑时**直接拒绝，不重启**：引擎没跑的情况下
            #   「重启」无意义，而跑着的情况下重启 = 两个注入源并行。
            blocked = self._pathfind_block()
            if blocked:
                self.engine.log_line.emit(f"✗ 收到重启引擎命令，但{blocked}")
                return
            if self.engine.is_running():
                stopped = self.engine.stop(timeout_ms=3000)
                stop_txt = "已停止" if stopped else "停止超时"
            else:
                stop_txt = "原本未运行"
            started = self.engine.start()
            start_txt = "已启动" if started else "启动失败"
            self.engine.log_line.emit(
                f"收到重启引擎命令：{stop_txt} -> {start_txt}")
        elif cmd == "START_OVERLAY":
            self.overlay.start()
        elif cmd == "STOP_OVERLAY":
            self.overlay.stop()
        elif cmd == "QUIT_CONSOLE":
            QTimer.singleShot(0, self.quit_app)
        else:
            # 理论上到不了这里（poller 已挡掉）。真到了说明有人绕过 poller
            # 直接调了本函数 —— 那更不能猜着执行。
            logger.warning("未处理的桥接命令：%r", cmd)

    def _shutdown_overlay(self) -> None:
        """退出前停卡片。overlay 无 IPC，terminate 即可。"""
        mgr = getattr(self, "overlay", None)
        if mgr is None:
            return
        try:
            if not mgr.is_running():
                logger.info("卡片未在运行，跳过")
                return
            logger.info("退出前：停止悬浮卡片…")
            mgr.stop(timeout_ms=2000)
            logger.info("卡片已停止")
        except Exception:  # noqa: BLE001 - 卡片关不掉也不能挡住主控台退出
            logger.exception("关闭卡片时异常（继续退出主控台）")

    def _shutdown_engine(self) -> None:
        """退出前停引擎：先优雅（发 STOP 命令），超时再强杀。

        顺序不能反：引擎的 MOVE_FORWARD 最长 60 秒，直接 terminate
        会让按键状态不明（角色可能一直往前走）。必须让它自己走完
        ``release_all()`` 流程。
        """
        mgr = getattr(self, "engine", None)
        if mgr is None:
            return
        try:
            if not mgr.is_running():
                logger.info("引擎未在运行，跳过引擎关闭")
                return
            logger.info("退出前：优雅停止引擎…")
            ok = mgr.stop(timeout_ms=3000)
            if not ok:
                logger.warning("引擎未在 3000ms 内退出，强制终止")
                mgr.kill()
            else:
                logger.info("引擎已优雅停止")
        except Exception:  # noqa: BLE001 - 引擎关不掉也不能挡住主控台退出
            logger.exception("关闭引擎时异常（继续退出主控台）")

    def quit_app(self) -> None:
        """**彻底退出**（托盘右键「完全退出」走这里，IPC SHUTDOWN 也走这里）。

        ★ 用户要的不是"优雅"，是"死透"
          ---------------------------
        这是一个**能截屏、有高权限**的常驻程序。用户点了退出却在任务
        管理器里看到它还在 —— 那是信任问题，不是技术问题。

        ★ 三段硬退
          --------
            1. 停引擎（≤3s，先优雅后强杀）
            2. 停卡片（≤2s，terminate）
            3. 隐藏托盘 —— 视觉上立刻"消失了"
            4. 武装硬退出兜底 —— 无论如何 500ms 后进程一定消失

        ★ 为什么兜底不能用 QTimer
          ----------------------
        ``QTimer.singleShot`` 挂在**事件循环**上。事件循环一退出，
        定时器跟着消失 —— 而事件循环退出之后，Python 还要走**解释器
        关闭**（join 所有非 daemon 线程、跑 atexit、flush 缓冲），
        **恰恰就是这一段最容易挂住**。

        于是原来的 ``QTimer.singleShot(500, self._hard_exit)`` 处在
        一个自相矛盾的位置：事件循环好好的时候它多半轮不到；
        事件循环卡死 / 解释器关闭卡住的时候它已经死了。
        这正是"点了退出，任务管理器里进程还在"的原因。

        改用**非 daemon 的 threading.Timer**：它是一个真实的 OS 线程，
        既不受事件循环死活影响，也不会被解释器在关闭时杀掉 ——
        解释器反而要**等**它。它醒来直接 ``os._exit(0)``，砍掉一切。
        """
        logger.info("退出流程开始")

        # 幂等：托盘菜单可能连点，或 IPC 和菜单同时触发
        if self._quitting:
            logger.debug("已在退出流程中，忽略重复请求")
            return
        self._quitting = True

        # ★ 先发"我要退了"，再停子进程。
        #   顺序反了的话，插件看到的是"主控台突然不更新了"，只能靠
        #   written_at_ms 超时去猜是不是退了；写这一行之后，面板可以
        #   **立刻**置灰并提示"主控台正在退出"。
        try:
            self._write_bridge(running=False)
        except Exception:  # noqa: BLE001 - 退出流程不该被状态写入拖住
            logger.exception("退出前写桥接状态失败（忽略）")
        self._bridge_timer.stop()
        # 别在退出这一轮里再去执行插件的命令
        self._command_poller.reset()

        # ★ 顺序：先引擎后卡片。
        #   引擎是唯一会**发按键**的子进程 —— 它没停干净，卡片停了也没用，
        #   角色可能继续自己走。所以先确保它死透。
        #   （卡片只是显示，terminate 即可，没有优雅退出可言。）
        self._shutdown_engine()
        self._shutdown_overlay()

        server = getattr(self, "_ipc_server", None)
        if server is not None:
            try:
                server.close()
                logger.info("QLocalServer 已关闭")
            except Exception:  # noqa: BLE001
                logger.warning("QLocalServer 关闭异常", exc_info=True)

        # 视觉上先"消失"，用户立刻觉得它死了
        self._tray.hide()
        logger.info("托盘已隐藏")

        try:
            # ★ 真 close：触发 closeEvent，_quitting=True 时走 accept 分支，
            #   窗口被真正销毁，而不是 hide。
            self.close()
            logger.info("窗口已销毁")
        except Exception:  # noqa: BLE001
            logger.warning("窗口关闭异常", exc_info=True)

        # 正常路径：让 Qt 干净地收尾（能走到这里就别浪费）
        QApplication.quit()
        logger.info("QApplication.quit 已调用")

        # ★ 兜底：无条件武装，不看事件循环死活
        self._arm_hard_exit()

    def _arm_hard_exit(self) -> None:
        """武装"无论如何 500ms 后进程一定消失"的兜底。

        ★ 为什么是 threading.Timer 且 daemon=False
          -----------------------------------------
            · **threading.Timer** 而非 QTimer —— 见 :meth:`quit_app`
              的说明：QTimer 挂在事件循环上，事件循环一死它就死，
              而它要救的恰恰是"事件循环已死/解释器关闭卡住"这种情况。
            · **daemon=False** —— 这是整个机制的支点。
              解释器关闭时会 join 每一个非 daemon 线程，所以这个线程
              一定会被等到、一定会醒、一定会执行 ``os._exit(0)``。
              若设成 daemon=True，它会在解释器关闭的第一时间被直接干掉，
              机制等于不存在。
        """
        timer = threading.Timer(HARD_EXIT_DELAY_S, self._hard_exit)
        timer.daemon = False       # 显式写出来：这是承重设计，不是默认值碰巧
        timer.start()
        logger.info("已武装硬退出兜底：%.1fs 后无条件 os._exit(0)",
                    HARD_EXIT_DELAY_S)

    @staticmethod
    def _hard_exit() -> None:
        """兜底硬退出。正常路径下轮不到这里，真走到说明前面卡住了。"""
        logger.warning("兜底 os._exit 触发：正常退出未在 %.1fs 内完成，强制结束",
                       HARD_EXIT_DELAY_S)
        # ★ os._exit 不跑 atexit、不 flush。日志文件是 buffered 的，
        #   不显式刷一下，最后这几行关键信息就丢了 —— 而这恰恰是
        #   最需要留证的一次退出。
        for handler in logging.getLogger().handlers:
            try:
                handler.flush()
            except Exception:  # noqa: BLE001 - 刷不出去也不能挡住退出
                pass
        try:
            sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass
        os._exit(0)


def run() -> int:
    """主控台入口。返回进程退出码。"""
    cfg, source = load_config()

    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # ★ 规格要求的那一行
    logging.getLogger("neko_console").info("配置已加载：来源=%s", source)

    app = QApplication(sys.argv)
    app.setApplicationName("NEKO-Console")
    app.setOrganizationName("N.E.K.O.")
    # 关键：**不设 quitOnLastWindowClosed**。Qt 默认在最后一个窗口关闭时
    # 退出整个 app，而我们的「关窗口」语义是「缩到托盘」。
    app.setQuitOnLastWindowClosed(False)

    server = None
    if cfg.single_instance:
        # ① 先问：有没有活着的旧实例？
        sock = _ipc_connect()
        if sock is not None:
            choice = _ask_existing_instance(sock)
            logger.info("已有实例在运行，用户选择：%s", choice)

            if choice == "open":
                _ipc_send(sock, CMD_SHOW)
                return 0

            if choice == "cancel":
                return 0

            # choice == "reopen"：请旧实例退出，等它真的消失
            _ipc_send(sock, CMD_SHUTDOWN)
            if not _wait_primary_gone():
                logger.warning("旧实例未在 %dms 内退出，仍继续接管",
                               IPC_EXIT_WAIT_MS)
            sock.abort()
            # 继续往下走，自己成为新实例

        # ② 成为服务方（连不上 = 没人监听，或旧实例已死）
        server = _become_primary(app)
        if server is None:
            QMessageBox.critical(
                None, "NEKO-Console",
                "无法获取主控台唯一实例：\n"
                "IPC 名称已被占用且无法接管。\n\n"
                f"可在 config.toml 里设置 single_instance = false 后重试。")
            logger.error("获取 IPC 服务失败，退出")
            return 2

    win = MainWindow(cfg)
    if server is not None:
        win._ipc_server = server          # noqa: SLF001 - 同类协作
        server.newConnection.connect(win.handle_ipc)
    if not cfg.start_minimized:
        win.show()

    code = app.exec()

    if server is not None:
        try:
            server.close()
        except Exception:  # noqa: BLE001 - 退出路径不该再抛
            logger.debug("IPC 服务关闭失败（忽略）", exc_info=True)
    logger.info("主控台已退出（code=%s, 打包=%s）", code, is_frozen())
    return int(code)


__all__ = ["MainWindow", "run"]
