"""悬浮卡片页签：启停卡片 / 随引擎联动 / 位置查看与重置。"""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QPushButton,
                               QSlider, QVBoxLayout, QWidget)

from ..channels.overlay import OverlayManager
from ..config import save_config
from ..config.schema import OVERLAY_SCALE_MAX, OVERLAY_SCALE_MIN

logger = logging.getLogger(__name__)

#: 状态 -> (文字, 颜色)
STATE_STYLE = {
    "stopped": ("未运行", "#6c7086"),
    "running": ("运行中", "#a6e3a1"),
}

#: 位置显示的刷新间隔。位置文件由 overlay 自己在拖动/微调时写，
#: 不需要快到 200ms —— 500ms 足够，且这是纯磁盘 stat，开销可忽略。
POS_REFRESH_MS = 500

#: 滑块量纲 = **百分之一倍**（50 = 0.5x，100 = 1.0x，300 = 3.0x）。
#: QSlider 原生就是整数刻度，硬塞浮点还得自己来回换算，只会更难写对。
#: 上下界从 schema 取，不在 UI 里另写一份 —— 范围变了只改 schema 一处。
SCALE_SLIDER_MIN = int(round(OVERLAY_SCALE_MIN * 100))
SCALE_SLIDER_MAX = int(round(OVERLAY_SCALE_MAX * 100))
#: 一格 5%：够细能调准，又不会一拖就跳出去。
SCALE_STEP = 5

#: 拖动时写实时通道的节流间隔（毫秒）。20Hz —— 再快 overlay 的 80ms
#: 轮询也接不住，只是白写文件。
LIVE_WRITE_INTERVAL_MS = 50

#: 松手后**延迟**删除临时文件的毫秒数。
#:
#: ★ 为什么不能写完立刻删
#:   overlay 每 80ms 才轮询一次。松手那一刻写入的最终值，如果立刻删掉，
#:   十有八九赶不上那一轮轮询 —— 用户就会看到"拖到位了，一松手又弹回去"。
#:   300ms > 一轮轮询 + 读取时间，保证最后一帧一定送得到。
LIVE_CLEAR_DELAY_MS = 300


class OverlayTab(QWidget):
    """悬浮卡片控制台。"""

    def __init__(self, manager: OverlayManager, cfg, parent=None,
                 *, engine: object | None = None) -> None:
        super().__init__(parent)
        self.mgr = manager
        self.cfg = cfg
        #: 引擎 channel（可选）。**只为启动前的数据新鲜度提示存在**，
        #: 见 :meth:`_on_start`。
        self._engine = engine
        #: 上次写实时缩放通道的时刻（time.monotonic）。节流用。
        self._last_live_write = 0.0
        self._build()
        self._connect()
        self._apply_state(self.mgr.state())
        self._refresh_pos()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

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

        # 随引擎联动
        self._follow = QCheckBox("卡片随引擎启停")
        self._follow.setChecked(bool(self.cfg.overlay.follow_engine))
        self._follow.toggled.connect(self._on_follow_toggled)
        lay.addWidget(self._follow)

        # 卡片大小
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("卡片大小："))
        self._scale_slider = QSlider(Qt.Orientation.Horizontal)
        self._scale_slider.setRange(SCALE_SLIDER_MIN, SCALE_SLIDER_MAX)
        self._scale_slider.setSingleStep(SCALE_STEP)
        self._scale_slider.setPageStep(SCALE_STEP * 5)
        # ★ 必须开 tracking：关掉的话拖动时 valueChanged 不发，
        #   文案就停在原地不动，用户会以为滑块坏了。
        self._scale_slider.setTracking(True)
        self._scale_slider.setValue(
            int(round(float(self.cfg.overlay.scale) * 100)))
        self._scale_lbl = QLabel()
        self._scale_lbl.setMinimumWidth(64)
        self._scale_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scale_slider.valueChanged.connect(self._on_scale_dragged)
        self._scale_slider.sliderReleased.connect(self._on_scale_released)
        size_row.addWidget(self._scale_slider, 1)
        size_row.addWidget(self._scale_lbl)
        btn_scale_reset = QPushButton("恢复默认")
        btn_scale_reset.clicked.connect(self._on_scale_reset)
        size_row.addWidget(btn_scale_reset)
        lay.addLayout(size_row)
        self._update_scale_label(self._scale_slider.value())

        # 位置
        pos_row = QHBoxLayout()
        pos_row.addWidget(QLabel("当前位置："))
        self._pos_lbl = QLabel()
        self._pos_lbl.setTextInteractionFlags(
            self._pos_lbl.textInteractionFlags().TextSelectableByMouse)
        pos_row.addWidget(self._pos_lbl, 1)
        btn_reset = QPushButton("重置位置")
        btn_reset.clicked.connect(self._on_reset_pos)
        pos_row.addWidget(btn_reset)
        lay.addLayout(pos_row)

        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color: #6c7086;")
        lay.addWidget(self._hint)

        # 按钮组
        row = QHBoxLayout()
        self._btn_start = QPushButton("启动卡片")
        self._btn_stop = QPushButton("关闭卡片")
        self._btn_start.clicked.connect(self._on_start)
        self._btn_stop.clicked.connect(self._on_stop)
        for b in (self._btn_start, self._btn_stop):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        # Python 路径（只读，方便排查）
        py_row = QHBoxLayout()
        py_row.addWidget(QLabel("Python："))
        py_lbl = QLabel(str(self.mgr.python_path()))
        py_lbl.setStyleSheet("color: #6c7086;")
        py_lbl.setTextInteractionFlags(
            py_lbl.textInteractionFlags().TextSelectableByMouse)
        py_row.addWidget(py_lbl, 1)
        lay.addLayout(py_row)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_pos)

    # ------------------------------------------------------------------
    def _connect(self) -> None:
        self.mgr.state_changed.connect(self._apply_state)

    def _start_pos_timer(self) -> None:
        self._timer.setInterval(POS_REFRESH_MS)
        self._timer.start()

    def _apply_state(self, state: str) -> None:
        text, color = STATE_STYLE.get(state, (state, "#cdd6f4"))
        self._lamp.setStyleSheet(
            f"background:{color}; border-radius:7px;")
        self._state_lbl.setText(text)
        self._state_lbl.setStyleSheet(f"color:{color};")

        running = state == "running"
        problems = self.mgr.path_problems()
        self._btn_start.setEnabled(not running and not problems)
        self._btn_stop.setEnabled(running)
        if problems:
            self._hint.setText("⚠ " + "\n⚠ ".join(problems))
            self._hint.show()
        else:
            self._hint.setText(
                "卡片是独立的 tkinter 进程，没有 IPC 通道 —— "
                "「关闭」是直接结束进程，窗口位置会保留在位置文件里。")
            self._hint.show()

        code = self.mgr.exit_code()
        self._exit_lbl.setText(f"exit={code}" if code is not None else "")

        self._start_pos_timer()

    def _refresh_pos(self) -> None:
        data = self.mgr.read_position()
        if not data:
            # ★ 显示「未定位」而不是 0, 0。
            #   overlay 刚启动时还没写文件，显示 (0,0) 会让用户以为
            #   卡片跑到屏幕左上角了 —— 真相是"还没定位过"。
            self._pos_lbl.setText("未定位")
            self._pos_lbl.setStyleSheet("color: #6c7086;")
            return
        alpha = data.get("alpha")
        tail = f"，不透明度 {float(alpha):.2f}" if isinstance(
            alpha, (int, float)) else ""
        self._pos_lbl.setText(f"X={data['x']}  Y={data['y']}{tail}")
        self._pos_lbl.setStyleSheet("color: #cdd6f4;")

    # ------------------------------------------------------------------
    def _on_follow_toggled(self, on: bool) -> None:
        self.cfg.overlay.follow_engine = bool(on)
        save_config(self.cfg)
        logger.info("卡片随引擎启停 = %s（已写回配置）", on)

    # -- 卡片大小 -------------------------------------------------------
    def _update_scale_label(self, slider_value: int) -> None:
        """滑块右侧的数字。**拖动过程中就要变** ——
        只在松手后才更新的话，用户拖了半天屏幕没反应，会以为控件坏了。"""
        self._scale_lbl.setText(f"{slider_value}%")
        if slider_value == 100:
            note = "系统默认"
        elif slider_value > 100:
            note = "更大更清楚，但更挡游戏画面"
        else:
            note = "更小更不挡，但字更小"
        self._scale_lbl.setToolTip(f"{slider_value / 100:.2f} 倍 · {note}")

    def _on_scale_dragged(self, value: int) -> None:
        """拖动中：**数字标签立刻更新** + 节流写实时缩放通道。

        ★ 为什么标签不节流、只有写文件节流
          标签是纯内存操作，每秒几十次毫无压力；写文件每秒 20 次就是
          20 次 open+write+replace。分开节流之后，数字还是跟手的。
        """
        self._update_scale_label(value)
        # 卡片没跑就没有"实时"可言 —— 什么都不写，松手走普通路径
        if not self.mgr.is_running():
            return
        now = time.monotonic()
        if (now - self._last_live_write) * 1000.0 < LIVE_WRITE_INTERVAL_MS:
            return
        self._last_live_write = now
        self.mgr.write_live_scale(value / 100.0)

    def _on_scale_released(self) -> None:
        """松手。

        ★ 关键：**卡片在跑时不再重启**
          拖动过程中 overlay 已经实时跟着变了，重启只会白闪一下。
          这里改成"写完最终值 -> 等 overlay 收下 -> 删临时文件 -> 写配置"。

        ★ 最后一帧必须再写一次
          节流可能刚好把松手那一刻的值滤掉了（比如 10ms 内从 80 拖到 100）。
          不补这一写，overlay 会停在中间尺寸，而配置里记的是最终值 ——
          两边对不上，下次启动才会暴露。
        """
        value = self._scale_slider.value() / 100.0
        live = self.mgr.is_running()
        if live:
            self.mgr.write_live_scale(value)
            # ★ 延迟删：给 overlay 留出至少一轮轮询（80ms）
            QTimer.singleShot(LIVE_CLEAR_DELAY_MS, self.mgr.clear_live_scale)
        else:
            self.mgr.clear_live_scale()
        self._commit_scale(value, live=live)

    def _on_scale_reset(self) -> None:
        self._scale_slider.setValue(100)   # 会顺带触发 valueChanged 更新文案
        # 恢复默认走"松手"同一条路径：写实时值 + 清临时文件 + 提交
        self._on_scale_released()

    def _flash_hint(self, text: str, color: str = "#6c7086") -> None:
        """写一条**看得见**的提示。

        ★ 为什么要单独一个方法
        ----------------------
        ``_build()`` 给 ``_hint`` 设的是暗灰 ``#6c7086`` —— 那是"闲置说明"
        的样式。之前 ``_commit_scale`` 只 ``setText()`` 不改颜色，于是
        "卡片已重启" 这条**成功反馈**也是暗灰，混在一堆灰色说明里，
        用户扫一眼根本注意不到（实测反馈就是"没看到任何提示"）。
        所以每条反馈都必须自带颜色。
        """
        self._hint.setText(text)
        self._hint.setStyleSheet(f"color: {color};")
        self._hint.show()

    def _commit_scale(self, value: float, *, live: bool = False) -> None:
        """把缩放写进正式配置。

        :param live: ``True`` 表示**已经通过实时通道应用到正在跑的卡片**了，
            此时**绝不重启它** —— 重启只会白闪一下，还会打断用户刚拖好的尺寸。
        """
        value = round(max(OVERLAY_SCALE_MIN,
                          min(OVERLAY_SCALE_MAX, float(value))), 2)
        if abs(float(self.cfg.overlay.scale) - value) < 1e-6:
            self._flash_hint(
                f"缩放没有变化（当前 {value:.0%}），卡片保持原样。", "#f9e2af")
            return                      # 没变就别重启
        self.cfg.overlay.scale = value
        save_config(self.cfg)
        logger.info("卡片大小倍率 = %.2f（已写回配置）", value)

        if live:
            # 已经实时改过了。这里只落配置，**不重启**。
            self._flash_hint(f"缩放已应用（当前 {value:.0%}）。", "#a6e3a1")
            return

        # ★ 下面才是"卡片没在跑 / 需要重启"的路径：overlay 没有 IPC 通道，
        #   缩放只在**启动时**读一次配置，运行中改配置对它毫无影响。
        #   这里**不**二次确认 —— 它跟 KILL 那种不可逆操作不是一回事。
        if self.mgr.is_running():
            self.mgr.stop(timeout_ms=2000)
            ok = self.mgr.start()
            # ★ _apply_state 会覆写 _hint 的文案，所以必须**先**它、
            #   **后**写我们的提示，否则刚写的说明当场就被冲掉。
            self._apply_state(self.mgr.state())
            if ok:
                self._flash_hint(
                    f"缩放已保存，卡片已重启应用新缩放。（当前 {value:.0%}）",
                    "#a6e3a1")
            else:
                self._flash_hint(
                    f"缩放已保存，但卡片重启失败。请手动检查。"
                    f"（当前 {value:.0%}，见日志）", "#f38ba8")
        else:
            self._flash_hint(
                f"缩放已保存。下次启动卡片时生效。（当前 {value:.0%}）",
                "#a6e3a1")

    def _on_start(self) -> None:
        if self.mgr.start():
            self._refresh_pos()     # 刚起来位置文件多半还没写，会显示未定位
            # ★ 数据新鲜度提示（2026-10-16 补）
            #   卡片和引擎**共用同一个 _runtime 目录**，卡片自己读
            #   state_snapshot.json 显示引擎状态 —— 引擎没跑时它读到的是
            #   **上一次运行遗留的快照**，于是卡片照常显示「运行中 / 4.9Hz」。
            #   这里在起卡片的同一拍把这件事说清楚，否则用户只会觉得
            #   「卡片一直在动，但引擎早就没了」。
            self._flash_hint(*self._freshness_hint())
        else:
            self._apply_state(self.mgr.state())

    def _freshness_hint(self) -> tuple[str, str]:
        """(提示文案, 颜色)。引擎在跑就说一切正常，否则提醒数据是上次的。"""
        eng = self._engine
        if eng is None:
            return ("卡片已启动。", "#a6e3a1")
        try:
            alive = bool(eng.is_running())
        except Exception:  # noqa: BLE001 - 读不到引擎 ≠ 引擎没跑，保守不打扰
            return ("卡片已启动。", "#a6e3a1")
        if alive:
            return ("卡片已启动，正在显示引擎实时状态。", "#a6e3a1")
        return ("⚠ 卡片已启动，但**引擎没有在运行**。\n"
                "   卡片读的是 state_snapshot.json，此刻显示的是"
                "**上一次运行留下的数据**（场景 / Hz 都不会再变）。\n"
                "   要看实时状态请先到「引擎」页启动引擎。", "#f9e2af")

    def _on_stop(self) -> None:
        self.mgr.stop()
        self._apply_state(self.mgr.state())

    def _on_reset_pos(self) -> None:
        """重置位置 —— **热生效**，不用手动关再开。

        ★ overlay 只在**启动时**读一次位置，之后位置存在它自己的内存里。
          单纯删文件对正在跑的卡片完全无效 —— 用户点了发现没反应，
          会以为按钮坏了。overlay 没有 IPC 通道，唯一能"让它重新读"
          的办法就是重启它，所以这里走 stop -> reset -> start。
        """
        was_running = self.mgr.is_running()

        if was_running:
            self.mgr.stop(timeout_ms=2000)
            self.mgr.reset_position()
            ok = self.mgr.start()
            self._apply_state(self.mgr.state())
            if ok:
                self._hint.setText(
                    "位置已重置，浮窗已搬回默认位置"
                    "（屏幕右侧、纵向对齐小地图中心）。")
            else:
                self._hint.setText(
                    "⚠ 位置已重置，但浮窗重启失败 —— 见日志。")
            self._hint.show()
        else:
            self.mgr.reset_position()
            self._hint.setText(
                "位置已重置。下次启动卡片会回到默认位置"
                "（屏幕右侧、纵向对齐小地图中心）。")
            self._hint.show()

        self._refresh_pos()

    def shutdown(self) -> None:
        self._timer.stop()
        # ★ 兜底清临时通道文件。
        #   正常松手时会有 singleShot 去删，但主控台可能在拖动途中就被关掉，
        #   那个定时器来不及跑。文件留着的话，下次启动 overlay 会读到
        #   上一次的中间值，开局白闪一下。
        self.mgr.clear_live_scale()


__all__ = [
    "LIVE_CLEAR_DELAY_MS",
    "LIVE_WRITE_INTERVAL_MS",
    "POS_REFRESH_MS",
    "SCALE_SLIDER_MAX",
    "SCALE_SLIDER_MIN",
    "SCALE_STEP",
    "STATE_STYLE",
    "OverlayTab",
]
