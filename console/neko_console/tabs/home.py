"""首页：2x2 概览卡片，每 500ms 从快照刷新。"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QFrame, QGridLayout, QLabel, QVBoxLayout,
                               QWidget)

from ..channels.snapshot import STALE_AFTER_S

logger = logging.getLogger(__name__)

#: 空状态文案。用户想改措辞，直接改这个 dict 即可。
#:
#: ★ 为什么是「文字」而不是「—」
#: ------------------------------
#: 横杠是**占位符**，不是状态。引擎没跑是一个有确定含义的状态
#: （用户自己停的 / 还没启动），直接说清楚比画一根杠有用。
#:
#:   engine  是**状态**卡片 —— "已停止"是准确描述
#:   其它三张是**数据**卡片 —— 引擎没跑就是没有数据来源，
#:           "等待引擎"比"无数据"更准，因为它暗示"等一下就有了"，
#:           而"无数据"听起来像出了故障。
#:
#: ★ 刻意**不做**「文案写进 config.toml」
#: 交付用户是小白，不会去配置文件里改措辞。多一个配置项只是让
#: config.toml 更臃肿，"想改时改一处"的诉求这个模块级 dict 已经满足。
#:
#: ★ 刻意**不做**「按运行时长区分空状态」（刚启动中 / 从未启动）
#: 区分太细反而看不懂。语义简单的状态词更清楚。
_EMPTY_TEXT = {
    "engine": "已停止",
    "metrics": "等待引擎",
    "scene": "等待引擎",
    "intent": "等待引擎",
}

#: 空状态文字颜色。偏灰 —— 不抢注意力，但也不是"没有内容"的灰。
_EMPTY_COLOR = "#6c7086"


class _Card(QFrame):
    """一张卡片：标题 + 大字值。"""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        self._title = QLabel(title)
        self._title.setStyleSheet("color: #6c7086;")
        self._value = QLabel("—")     # 引擎没跑时显示破折号
        self._value.setStyleSheet(
            "font-size: 20px; color: #cdd6f4; font-weight: 600;")
        lay.addWidget(self._title)
        lay.addWidget(self._value)

    def set_value(self, text: str, color: str = "#cdd6f4") -> None:
        self._value.setText(text)
        self._value.setStyleSheet(
            f"font-size: 20px; color: {color}; font-weight: 600;")


#: 状态 -> 卡片配色
STATE_COLOR = {
    "RUNNING": "#a6e3a1",
    "PAUSED": "#f9e2af",
    "STOPPING": "#fab387",
    "STOPPED": "#6c7086",
    "IDLE": "#89b4fa",
}


class HomeTab(QWidget):
    """总览。数据全部来自引擎快照，读不到就显示 "—"（不崩）。"""

    REFRESH_MS = 500

    def __init__(self, manager, parent: QWidget | None = None,
                 *, pathfind: object | None = None) -> None:
        super().__init__(parent)
        self.mgr = manager
        #: 寻路 channel（可选）。**只为这一行存在** —— 寻路状态不写进
        #: console_bridge.json，也不进 state_snapshot.json，首页是唯一一个
        #: 「一眼看到寻路在不在」的位置（2026-10-16 补）。
        self._pathfind = pathfind

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        grid = QGridLayout()
        grid.setSpacing(12)
        self._c_state = _Card("引擎状态")
        self._c_rate = _Card("帧率 / P95")
        self._c_scene = _Card("场景")
        self._c_intent = _Card("意图数")
        self._c_pathfind = _Card("寻路")
        grid.addWidget(self._c_state, 0, 0)
        grid.addWidget(self._c_rate, 0, 1)
        grid.addWidget(self._c_scene, 1, 0)
        grid.addWidget(self._c_intent, 1, 1)
        # ★ 第 3 行独占整行：寻路与引擎互斥，这一行是唯一能一眼看出
        #   「现在是谁在占用键鼠」的地方，不该被挤成半宽。
        grid.addWidget(self._c_pathfind, 2, 0, 1, 2)
        for col in (0, 1):
            grid.setColumnStretch(col, 1)
        for row in (0, 1, 2):
            grid.setRowStretch(row, 1)
        lay.addLayout(grid)

        self._empty = QLabel("引擎未运行。切到「引擎」页签点「启动」。")
        self._empty.setStyleSheet("color: #6c7086;")
        lay.addWidget(self._empty)

        # ★ 500ms 刷新是**读缓存**，不是读文件 ——
        #   SnapshotReader 内部有 stat 短路，没变就零 IO。
        self._timer = QTimer(self)
        self._timer.setInterval(self.REFRESH_MS)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

        manager.snapshot_updated.connect(self._on_snapshot)
        # ★ 引擎一停就立刻把卡片清成 "——"。否则首页会**一直显示引擎
        #   最后一次运行留下的快照**（5.0 Hz / 30ms / 空转 25…），
        #   用户会以为引擎还活着。
        manager.state_changed.connect(self._on_state_changed)

        # 先摆成空状态，别让 _Card 的默认 "—" 在启动瞬间闪一下
        self._clear()

    # ------------------------------------------------------------------
    def _engine_alive(self) -> bool:
        try:
            return bool(self.mgr.is_running())
        except Exception:  # noqa: BLE001 - 读不到就当没跑
            return False

    def _clear(self, hint: str = "") -> None:
        """四张卡片进入空状态。

        文案与颜色都从模块级常量取 —— 想改措辞改 :data:`_EMPTY_TEXT`
        一处即可，不用翻到这里。
        """
        self._c_state.set_value(_EMPTY_TEXT["engine"], _EMPTY_COLOR)
        self._c_rate.set_value(_EMPTY_TEXT["metrics"], _EMPTY_COLOR)
        self._c_scene.set_value(_EMPTY_TEXT["scene"], _EMPTY_COLOR)
        self._c_intent.set_value(_EMPTY_TEXT["intent"], _EMPTY_COLOR)
        if hint:
            self._empty.setText(hint)
        self._empty.show()

    def _on_state_changed(self, state: str) -> None:
        """引擎状态变了。停了就立刻清空，不必等下一个 tick。"""
        if state == "starting":
            # ★ 2026-10-16（集成审计 F5）：拉起来了但还没产出新 run_id。
            #   别急着显示旧快照，也别显示"已停止"—— 说清楚在等什么。
            self._c_state.set_value("启动中…", "#f9e2af")
            self._c_rate.set_value("等待引擎", _EMPTY_COLOR)
            self._c_scene.set_value("等待引擎", _EMPTY_COLOR)
            self._c_intent.set_value("等待引擎", _EMPTY_COLOR)
            self._empty.setText(
                "引擎已启动，正在等待游戏窗口并初始化（最长 30 秒）。"
                "请确认原神已在前台运行。")
            self._empty.setStyleSheet("color: #f9e2af;")
            self._empty.show()
            return
        if state != "running":
            self._clear()

    def _refresh(self) -> None:
        """兜底轮询：即使 manager 的 timer 没跑（比如引擎未启动），
        也能把已缓存的快照显示出来。"""
        # ★ 寻路那一行**独立刷新**：引擎没跑时下面会 early return，
        #   但寻路照样可能在跑，不能跟着一起被清掉。
        self._refresh_pathfind()
        if not self._engine_alive():
            self._clear()
            return
        reader = getattr(self.mgr, "_snapshot", None)
        if reader is None:
            return
        snap = reader.read()
        if snap is not None:
            # 陈旧判定在 _apply 内部统一做（它拿得到 reader），这里不重复传。
            self._apply(snap)

    def _refresh_pathfind(self) -> None:
        """刷新「寻路」卡片。

        ★ 为什么读 ``read_state()`` 而不是 ``is_running()``
          ``is_running()`` 只看 Console 手里的 Popen；而
          ``pathfind_state.json`` 由脚本**每帧**重写，两者可能不一致
          （脚本崩了 Popen 还没回收 → 文件已陈旧）。
          这里以「进程在 + 文件没陈旧」为准，与 PathfindChannel 自己的
          判定口径一致（STALE_AFTER_S）。
        """
        pf = self._pathfind
        if pf is None:
            self._c_pathfind.set_value("—", "#6c7086")
            return
        try:
            if not pf.is_running():
                self._c_pathfind.set_value("未运行", "#6c7086")
                return
            st = pf.read_state() or {}
            if not st:
                self._c_pathfind.set_value("运行中（还没写状态）", "#f9e2af")
                return
            if pf.is_state_stale():
                self._c_pathfind.set_value("运行中（状态已陈旧）", "#f9e2af")
                return
            phase = st.get("state") or st.get("phase") or "?"
            self._c_pathfind.set_value(f"运行中 · {phase}", "#fab387")
        except Exception:  # noqa: BLE001 - 护栏读不到就当没跑
            self._c_pathfind.set_value("—", "#6c7086")

    def _on_snapshot(self, snap: dict[str, Any]) -> None:
        # ★ 双保险：即使 state_changed 信号漏了，单看 is_running()
        #   也能保证引擎没跑时绝不显示旧数据。
        if not self._engine_alive():
            self._clear()
            return
        self._apply(snap)

    def _apply(self, snap: dict[str, Any]) -> None:
        state = str(snap.get("state", "") or "")
        color = STATE_COLOR.get(state, "#6c7086")

        # ★ 读得到 ≠ 还能信（2026-10-16，集成审计 F3）
        #   SnapshotReader 带 stat 短路：引擎死掉之后 read() 照样返回最后
        #   一次那份快照，而「引擎死了」和「这一轮恰好没变化」在 read()
        #   眼里完全一样。必须单独问 is_stale()，不能靠 read() 是不是 None。
        #
        #   判不出（异常）时当不陈旧：宁可少提示一次，也不要把一个还在跑
        #   的引擎误报成死了 —— 那个误报会把用户引向错误的操作。
        stale = False
        reader = getattr(self.mgr, "_snapshot", None)
        if reader is not None:
            try:
                stale = bool(reader.is_stale())
            except Exception:  # noqa: BLE001
                logger.debug("快照陈旧判定失败，按不陈旧处理", exc_info=True)

        if stale:
            # 引擎进程可能还在，但快照已经几秒没动了 —— 数字全是旧的。
            # 与其让用户以为引擎还在跑，不如明说「这份数据过期了」。
            self._c_state.set_value("已停止 · 数据已过期", "#f9e2af")
            self._c_scene.set_value("—", "#6c7086")
            self._c_rate.set_value("—", "#6c7086")
            self._c_intent.set_value("—", "#6c7086")
            self._empty.setText(
                f"引擎已超过 {STALE_AFTER_S:g}s 没有更新状态快照，"
                "卡片数字已作废。停止并重启引擎可恢复。")
            self._empty.setStyleSheet("color: #f9e2af;")
            self._empty.show()
            return

        self._c_state.set_value(
            _zh(state) if state else "—",
            color if state else "#6c7086")

        hz = snap.get("tick_hz")
        p95 = snap.get("tick_p95_ms")
        if isinstance(hz, (int, float)) and hz > 0:
            self._c_rate.set_value(f"{float(hz):.1f} Hz / {float(p95 or 0):.0f} ms")
        else:
            self._c_rate.set_value("—", "#6c7086")

        scene = str(snap.get("scene", "") or "")
        self._c_scene.set_value(scene or "—",
                                "#cdd6f4" if scene else "#6c7086")

        intent = snap.get("intent") or {}
        if isinstance(intent, dict) and intent:
            cnt = intent.get("count", 0)
            idle = intent.get("idle_streak", 0)
            txt = f"{cnt}"
            col = "#cdd6f4"
            if idle:
                txt += f"（空转 {idle}）"
                col = "#f9e2af"
            self._c_intent.set_value(txt, col)
        else:
            self._c_intent.set_value("—", "#6c7086")

        # 引擎确实在跑（前面已判过 is_running），提示文字一律收起
        self._empty.hide()

    def shutdown(self) -> None:
        """关窗时停掉定时器（tab 被隐藏也不停，500ms 一次 stat 开销可忽略，
        但真退出时必须停）。"""
        self._timer.stop()


def _zh(state: str) -> str:
    return {"RUNNING": "运行中", "PAUSED": "已暂停", "STOPPING": "停止中",
            "STOPPED": "已停止", "IDLE": "空闲"}.get(state, state)


__all__ = ["STATE_COLOR", "HomeTab"]