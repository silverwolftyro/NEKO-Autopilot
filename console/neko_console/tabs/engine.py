"""引擎页签：启动 / 停止 / 强杀 + 实时事件流。"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWidget)

from ..channels.engine import MAX_LINES, EngineManager

logger = logging.getLogger(__name__)

#: 状态 -> (文字, 颜色)
#:
#: ★ 为什么多一个 "starting"（2026-10-16，集成审计 F5）
#:   引擎拉起来之后要 import 一大堆东西，还要等游戏窗口（最长 30s）。
#:   这段时间以前显示"运行中"，用户对着静止的界面等，还以为坏了。
#:   现在明说"启动中…"，颜色用黄 —— 它没失败，只是在等。
STATE_STYLE = {
    "stopped": ("未启动", "#6c7086"),
    "starting": ("启动中…", "#f9e2af"),
    "running": ("运行中", "#a6e3a1"),
    "crashed": ("已崩溃", "#f38ba8"),
}

#: 「进程活着」的状态集合。
#:
#: ★ 与"真的跑起来了"分开
#:   决定「停止 / 强杀」按钮该不该亮的是**进程还活着**，不是它跑完没有。
#:   启动中照样要能停 —— 用户等不及想关掉是常态。
ALIVE_STATES = ("starting", "running")

#: 事件流行首 -> 颜色。**只给"需要用户行动"的行上色**，
#: 引擎自己吐的正常事件流保持默认色，否则满屏彩色反而看不清重点。
LINE_COLORS = (
    ("引擎退出", "#f38ba8"),      # 引擎退了 —— 唯一需要注意的一类
    ("✗", "#f38ba8"),
    ("⚠", "#f9e2af"),
    ("✓", "#a6e3a1"),
)


def line_color(line: str) -> str | None:
    """这一行要不要上色。``code=0`` 的退出是正常收尾，不该吓人。"""
    if line.startswith("引擎退出"):
        return None if "code=0" in line else "#f38ba8"
    for prefix, color in LINE_COLORS:
        if line.startswith(prefix):
            return color
    return None


class EngineTab(QWidget):
    """引擎控制台。"""

    def __init__(self, manager: EngineManager, parent: QWidget | None = None,
                 *, pathfind: object | None = None) -> None:
        super().__init__(parent)
        self.mgr = manager
        #: 寻路 channel（可选）。**只为互斥护栏存在** —— 不持有它做任何别的事，
        #: 也不用它判断状态健康度；见 :meth:`_pathfind_block`。
        self._pathfind = pathfind
        self._build()
        self._connect()
        self._refresh_paths()
        self._apply_state(self.mgr.state())

    # ------------------------------------------------------------------
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        # 状态灯 + 文字
        top = QHBoxLayout()
        self._lamp = QLabel()
        self._lamp.setFixedSize(14, 14)
        self._state_lbl = QLabel()
        self._exit_lbl = QLabel()
        self._exit_lbl.setStyleSheet("color: #6c7086;")
        top.addWidget(self._lamp)
        top.addWidget(self._state_lbl)
        top.addStretch(1)
        top.addWidget(self._exit_lbl)
        lay.addLayout(top)

        # 路径两行
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        self._path_lbl = QLabel()
        self._runtime_lbl = QLabel()
        for i, (name, lbl) in enumerate((("引擎目录", self._path_lbl),
                                        ("运行时目录", self._runtime_lbl))):
            lbl.setTextInteractionFlags(
                lbl.textInteractionFlags().TextSelectableByMouse)
            grid.addWidget(QLabel(name + "："), 0, i)
            grid.addWidget(lbl, 1, i)
            btn = QPushButton("打开目录")
            btn.clicked.connect(
                lambda _=False, p=("path", "runtime"): self._open_dir(p[1]))
            grid.addWidget(btn, 2, i)
        for col in (0, 1):
            grid.setColumnStretch(col, 1)
        lay.addLayout(grid)

        self._warn = QLabel()
        self._warn.setWordWrap(True)
        self._warn.setStyleSheet("color: #fab387;")
        self._warn.hide()
        lay.addWidget(self._warn)

        # 按钮组
        row = QHBoxLayout()
        self._btn_start = QPushButton("启动")
        self._btn_stop = QPushButton("停止")
        self._btn_kill = QPushButton("强制终止")
        self._btn_start.clicked.connect(self._on_start)
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_kill.clicked.connect(self._on_kill)
        for b in (self._btn_start, self._btn_stop, self._btn_kill):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        lay.addWidget(QLabel("事件流："))
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(MAX_LINES)   # 防内存无限增长
        lay.addWidget(self._log, 1)

    def _connect(self) -> None:
        self.mgr.state_changed.connect(self._apply_state)
        self.mgr.log_line.connect(self._append)

    # ------------------------------------------------------------------
    def _refresh_paths(self) -> None:
        self._path_lbl.setText(str(self.mgr.cfg.resolved_path()))
        self._runtime_lbl.setText(str(self.mgr.runtime_dir()))
        problems = self.mgr.path_problems()
        if problems:
            self._warn.setText("⚠ " + "\n⚠ ".join(problems))
            self._warn.show()
            self._btn_start.setEnabled(False)
        else:
            self._warn.hide()
            self._btn_start.setEnabled(not self.mgr.is_running())
        # runtime 目录不存在不算错（引擎启动时才建），只是提示
        if not self.mgr.runtime_dir().is_dir():
            self._runtime_lbl.setText(
                str(self.mgr.runtime_dir()) + "（尚不存在，引擎启动时创建）")

    def _apply_state(self, state: str) -> None:
        text, color = STATE_STYLE.get(state, (state, "#cdd6f4"))
        self._lamp.setStyleSheet(
            f"background:{color}; border-radius:7px;")
        self._state_lbl.setText(text)
        self._state_lbl.setStyleSheet(f"color:{color};")

        # ★ 「进程活着」而不是「状态是 running」（2026-10-16）
        #   启动中也要能点停止 —— 用户等不及想关掉是常态，而以前
        #   那个窗口里停止按钮是灰的。
        alive = state in ALIVE_STATES
        self._btn_start.setEnabled(not alive)
        self._btn_stop.setEnabled(alive)
        self._btn_kill.setEnabled(alive)
        if not alive:
            problems = self.mgr.path_problems()
            self._btn_start.setEnabled(not problems)
            self._refresh_paths()

        code = self.mgr.exit_code()
        self._exit_lbl.setText(f"exit={code}" if code is not None else "")

    def _append(self, line: str) -> None:
        """追加一行事件。

        ★ 为什么不用 appendPlainText
          它只能写纯文本，没法给单行上色。而「引擎退出（code=2）：找不到
          标题包含 '原神' 的窗口」这一行，是整页里**唯一**告诉用户下一步
          该做什么的东西 —— 它必须在满屏日志里一眼能被看见，而不是混在
          几十行灰色事件流中间靠用户自己找。

        QPlainTextEdit 是可以逐块设色的：用 QTextCursor 移到末尾，
        带 QTextCharFormat 插入即可，不影响只读和滚动。
        """
        color = line_color(line)
        if color is None:
            self._log.appendPlainText(line)
        else:
            cursor = self._log.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            cursor.insertText(line + "\n", fmt)
        # 滚到底
        bar = self._log.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ------------------------------------------------------------------
    def _on_start(self) -> None:
        self._append("— 请求启动引擎 —")
        # ★ 寻路互斥（2026-10-16 补）
        #   PathfindChannel 会拒绝「引擎在跑时启动寻路」，但反向这条一直缺着。
        #   这里把寻路 channel 由 app.py 注入进来（构造参数可选，缺省不启用），
        #   三个启动入口（引擎页 / START_ENGINE / RESTART_ENGINE）共用同一句话。
        blocked = self._pathfind_block()
        if self.mgr.start(blocked_reason=blocked):
            self._append(f"✓ 已启动（cwd={self.mgr.cfg.resolved_path()}）")
        else:
            if blocked:
                self._append(f"✗ 拒绝启动：{blocked}")
            else:
                self._append("✗ 启动失败，见日志")

    def _pathfind_block(self) -> str | None:
        """寻路在跑时给出拒绝理由，否则 ``None``。**不抛**——
        读不到寻路状态就当没跑，绝不能因此让引擎起不来。"""
        pf = self._pathfind
        if pf is None:
            return None
        try:
            if not pf.is_running():
                return None
            return ("寻路正在运行 —— 引擎与寻路抢同一套键鼠，不能并行。"
                    "请先在「寻路」页停掉寻路。")
        except Exception:  # noqa: BLE001 - 护栏读不到 ≠ 阻塞启动
            logger.exception("检查寻路状态失败，按「未运行」处理")
            return None

    def _on_stop(self) -> None:
        self._append("— 请求优雅停止 —")
        ok = self.mgr.stop()
        self._append("✓ 已停止" if ok else "⚠ 超时后被强制终止")

    def _on_kill(self) -> None:
        self._append("— 强制终止 —")
        self.mgr.kill()
        self._append("✓ 已终止（按键可能已卡住，建议重启游戏）")

    def _open_dir(self, which: str) -> None:
        path = (self.mgr.cfg.resolved_path() if which == "path"
                else self.mgr.runtime_dir())
        if not Path(path).is_dir():
            self._append(f"⚠ 目录不存在：{path}")
            return
        try:
            if sys.platform == "win32":
                os.startfile(str(path))  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", str(path)])  # noqa: S603,S607
        except Exception as exc:  # noqa: BLE001
            logger.warning("打开目录失败：%s", exc)
            self._append(f"⚠ 打开目录失败：{exc}")


__all__ = ["LINE_COLORS", "STATE_STYLE", "EngineTab", "line_color"]
