"""寻路页签：起停 / 坐标输入 / 状态与事件流。

★ 这个页签存在的**唯一理由**是把「不能与引擎并行」这条护栏摆在用户面前。

护栏做在 channel 里（``PathfindChannel.start(engine_running=...)``），
这里的职责是**把拒绝的原因说清楚**，以及在引擎运行时**主动禁用按钮** ——
让人在点之前就知道点不了，比点了之后弹窗友好。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QTextEdit,
                               QVBoxLayout, QWidget)

from ..channels.engine import EngineManager
from ..channels.pathfind import PathfindChannel
from .mapdata import (NavMode, StartRequest, StartSource, TargetSpec,
                      fmt_xz, get_start_position,
                      resolve_target_from_description,
                      verify_against_reference)
from .mapwidget import MapPicker
from .tpsearch import (build_disambiguation_question, needs_disambiguation,
                       search_teleport)

logger = logging.getLogger(__name__)


def text_hint(spec: TargetSpec) -> str:
    """解析失败时给用户的下一步建议。"""
    if "同名" in spec.entity_type or "候选" in spec.entity_type:
        return "用下方地图搜索同名地点，自己挑一个"
    return "只支持传送点地名或 'X,Z' 坐标"

#: 状态 -> (文字, 颜色)
STATE_STYLE = {
    "stopped": ("未运行", "#6c7086"),
    "running": ("运行中", "#a6e3a1"),
}

#: 状态文件超过这么久没更新就算「陈旧」。**与 channel 的
#: ``STALE_AFTER_S`` 同源**（channel 才是判定方，这里只是文案里的阈值）。
STATUS_REFRESH_MS = 600
STALE_AFTER_S = 5.0

#: 提示用户「游戏要前台」的那句话
FOREGROUND_HINT = "游戏必须在前台，否则抓不到画面（寻路会自己退出）"


class PathfindTab(QWidget):
    """寻路控制台。"""

    def __init__(self, channel: PathfindChannel, engine: EngineManager,
                 *, is_admin: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.ch = channel
        self.engine = engine
        #: 是否以管理员运行。**False 时「启动寻路」不可用** ——
        #: e2e_pathfind --live 的 UIPI 门禁会 3 秒内 exit 2，而症状
        #: 只是「进程闪退」，不提示的话根本联想不到权限。
        self._admin = bool(is_admin)
        self._build()
        self._connect()
        self._apply_state(self.ch.state())
        self._refresh()

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        # 顶部状态灯
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

        self._nav_state_lbl = QLabel()
        self._nav_state_lbl.setWordWrap(True)
        lay.addWidget(self._nav_state_lbl)

        # ★ 护栏提示条。引擎在跑时它变红 —— 这是整个页签的核心信息。
        self._guard_lbl = QLabel()
        self._guard_lbl.setWordWrap(True)
        lay.addWidget(self._guard_lbl)

        # 坐标
        box = QGroupBox("坐标（提瓦特坐标系 X,Z）")
        grid = QVBoxLayout(box)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("起点来源："))
        self._src_combo = QComboBox()
        for src in StartSource:
            self._src_combo.addItem(f"{src.value}（{src.name}）", src)
            idx = self._src_combo.count() - 1
            if not src.available:
                # 不可用的来源保留在列表里（说明存在）但不让选 ——
                # 直接删掉的话，用户根本不知道「以后会有这个功能」
                item = self._src_combo.model().item(idx)
                item.setEnabled(False)
                item.setToolTip(src.unavailable_reason)
        self._src_combo.setCurrentIndex(0)
        self._src_combo.currentIndexChanged.connect(self._on_source_changed)
        row1.addWidget(self._src_combo, 1)
        grid.addLayout(row1)

        self._start_row = QHBoxLayout()
        self._start_row.addWidget(QLabel("起点："))
        self._start_edit = QLineEdit()
        self._start_edit.setPlaceholderText("例如 4634.52, 4687.13")
        self._start_row.addWidget(self._start_edit, 1)
        grid.addLayout(self._start_row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("终点："))
        self._target_edit = QLineEdit()
        self._target_edit.setPlaceholderText("例如 4634.52, 4800.00")
        row2.addWidget(self._target_edit, 1)
        grid.addLayout(row2)

        # 目标描述（任务 C）：只支持精确地名，先给个入口
        row_desc = QHBoxLayout()
        row_desc.addWidget(QLabel("描述去哪："))
        self._desc_edit = QLineEdit()
        self._desc_edit.setPlaceholderText("输入传送点地名，如「七天神像-风」；回车解析")
        self._desc_edit.returnPressed.connect(self._on_desc_submit)
        row_desc.addWidget(self._desc_edit, 1)
        self._btn_desc = QPushButton("解析")
        self._btn_desc.clicked.connect(self._on_desc_submit)
        row_desc.addWidget(self._btn_desc)
        grid.addLayout(row_desc)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("启动延迟(秒)："))
        self._delay_spin = QLineEdit("10")
        self._delay_spin.setFixedWidth(60)
        row3.addWidget(self._delay_spin)
        self._live = QCheckBox("真发按键(--live)")
        self._live.setChecked(True)
        row3.addWidget(self._live)
        self._nav_mode = QComboBox()
        for mode in NavMode:
            self._nav_mode.addItem(mode.value, mode)
            idx = self._nav_mode.count() - 1
            if not mode.available:
                item = self._nav_mode.model().item(idx)
                item.setEnabled(False)
                item.setToolTip(mode.unavailable_reason)
        row3.addWidget(self._nav_mode)
        row3.addStretch(1)
        grid.addLayout(row3)

        self._hint = QLabel(FOREGROUND_HINT)
        self._hint.setStyleSheet("color: #fab387;")
        grid.addWidget(self._hint)
        lay.addWidget(box)

        # ── 地图（任务 A）────────────────────────────────────────
        lay.addWidget(QLabel("在地图上点一下选点（金色圆点是可点的传送点）："))
        self._map = MapPicker()
        self._map.picked.connect(self._on_map_pick)
        self._map.anchor_picked.connect(self._on_map_anchor)
        lay.addWidget(self._map, 1)

        map_row = QHBoxLayout()
        map_row.addWidget(QLabel("搜索传送点："))
        # ★ 2026-10-16：QLineEdit -> 可编辑 QComboBox + 实时候选下拉。
        #   原来只有「回车 -> 高亮地图上的同名点」，用户打错一个字就什么都没有，
        #   而 936 个点里 777 个都叫「传送锚点」，光靠回车+高亮没法用。
        #   现在边打边出候选，点一条直接填终点坐标。
        self._search_combo = QComboBox()
        self._search_combo.setEditable(True)
        self._search_combo.setInsertPolicy(QComboBox.NoInsert)
        self._search_combo.setPlaceholderText(
            "地名 / 地区 / 国家，或拼音（qtsx），回车取第一条")
        self._search_combo.setMinimumWidth(220)
        self._search_combo.lineEdit().textEdited.connect(self._on_search_typed)
        self._search_combo.activated.connect(self._on_pick_candidate)
        # 回车 = 取第一条。原来只有 returnPressed 接 _on_search，现在下拉里
        # 已经有排好序的候选，直接用第一条比重新在地图里 find 一遍更准。
        self._search_combo.lineEdit().returnPressed.connect(self._on_search)
        # 250ms 防抖：一次搜索 ~28ms（936 条），不防抖的话连打 "qitianshenxiang"
        # 会在每个键上各跑一遍，打字过程明显发涩。
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._refresh_candidates)
        self._candidates: list = []
        map_row.addWidget(self._search_combo, 1)
        # 保留原来的搜索框语义：编辑框内容 = 地图过滤词。
        self._search_edit = self._search_combo.lineEdit()
        # 「全图」按钮搬进了 MapPicker 自己的缩放控件行（B 任务 2026-10-16）。
        # 这里保留同名属性指向同一个按钮，不破坏既有引用/测试，
        # 也不在 tab 层再摆一个重复按钮。
        self._btn_fit = self._map._btn_fit
        lay.addLayout(map_row)

        # ── 坐标系数漂移告警（补回缺失的「参照校验」入口）──────────
        #   mapdata 里抄了一份 recognition 的仿射系数。两边不同步的话，
        #   地图上点出来的坐标会**静默地全错** —— 而用户看不出任何异常，
        #   只会觉得「这地图不准」。所以构造时对一次，不一致就红字告知。
        self._drift_lbl = QLabel()
        self._drift_lbl.setWordWrap(True)
        self._drift_lbl.setStyleSheet("color: #fab387;")
        self._drift_lbl.hide()
        lay.addWidget(self._drift_lbl)
        self._check_coord_drift()

        # 按钮
        btn_row = QHBoxLayout()
        self._btn_start = QPushButton("启动寻路")
        self._btn_start.clicked.connect(self._on_start)
        self._btn_stop = QPushButton("停止寻路")
        self._btn_stop.clicked.connect(self._on_stop)
        btn_row.addWidget(self._btn_start)
        btn_row.addWidget(self._btn_stop)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        self._msg = QLabel()
        self._msg.setWordWrap(True)
        lay.addWidget(self._msg)

        # 事件流
        lay.addWidget(QLabel("引擎事件流（注意：寻路跑的时候引擎是停的，"
                             "这里多半是上一次 run 的残留）："))
        self._events = QTextEdit()
        self._events.setReadOnly(True)
        self._events.setMinimumHeight(120)
        lay.addWidget(self._events)

    def _connect(self) -> None:
        self.ch.state_changed.connect(self._apply_state)
        self._timer = QTimer(self)
        self._timer.setInterval(STATUS_REFRESH_MS)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

    # ------------------------------------------------------------------
    # 地图 / 来源 / 描述（任务 A/B/C）
    # ------------------------------------------------------------------

    def _check_coord_drift(self) -> None:
        """构造时对一次坐标系数，不一致就红字提示。

        ★ 为什么必须**在 UI 上说**
          系数漂移的症状是「地图上点哪儿都偏一点」—— 没有报错、没有事件、
          日志里什么都没有，用户只会以为地图画得不准，然后失去信任。
          这正是 :func:`mapdata.verify_against_reference` 当初被写出来的理由。

        ★ 为什么不一致就禁用「启动」
          偏了还能用（用户自己看着办），禁掉则是把一个「可以错但可见」的
          状态变成「不能用」，代价更大。这里选**告知而不阻断**。

        源模块不在本机时它返回 ``checked=False`` —— 跨项目可选检查不该
        变成 Console 的启动依赖。
        """
        try:
            rpt = verify_against_reference()
        except Exception as exc:      # noqa: BLE001
            logger.warning("坐标系数校验失败：%s", exc)
            return
        if not rpt.get("checked"):
            return                  # 源模块不在，静默跳过
        diffs = rpt.get("diffs") or []
        if not diffs:
            return
        names = ", ".join(str(d[0]) for d in diffs[:4])
        rms = rpt.get("rms_px")
        self._drift_lbl.setText(
            f"⚠ 坐标换算系数与 recognition 源模块不一致（{names}"
            f"{' …' if len(diffs) > 4 else ''}）。"
            f"地图选点会偏，标定 RMS={rms}px。改了一边记得同步另一边。")
        self._drift_lbl.show()

    def _current_source(self) -> StartSource:
        return self._src_combo.currentData()

    def _on_source_changed(self) -> None:
        src = self._current_source()
        manual = src is StartSource.MANUAL
        self._start_edit.setEnabled(manual)
        # ⚠️ 提示挂在 QLineEdit 上而不是 QHBoxLayout —— 布局没有 setToolTip
        if manual:
            self._start_edit.setToolTip("")
        else:
            self._start_edit.setToolTip(
                "起点由地图选点决定，手动输入已禁用")

    def _on_map_pick(self, x: float, z: float) -> None:
        """地图上点了一下。写进起点框（起点来源为手动时）或直接当起点。"""
        txt = fmt_xz(x, z)
        if self._current_source() is StartSource.MANUAL:
            self._start_edit.setText(txt)
        else:
            self._pending_pick = (x, z)
        self._readout(f"地图点选：{txt}")

    def _on_map_anchor(self, point) -> None:
        """点中的是某个传送点。"""
        self._map.select_point(point)
        self._target_edit.setText(fmt_xz(*point.tran_xz))
        self._pending_target_anchor = point
        self._readout(f"已选传送点：{point.name}（{point.type_cn}）"
                      f" → {self._target_edit.text()}")

    # ------------------------------------------------------------------
    # 搜索：实时候选 + NEKO 消歧
    # ------------------------------------------------------------------
    def _on_search_typed(self, text: str) -> None:
        """用户敲了字。重启防抖，由 :meth:`_refresh_candidates` 真正去搜。"""
        self._search_timer.start()

    def _refresh_candidates(self) -> None:
        """跑一次模糊搜索，把候选灌进下拉框。

        ★ 为什么 ``clear()`` 之后必须把 ``setEditText`` 放回来
          清空 QComboBox 会连带把编辑框里的文字也清掉，用户刚打的东西就没了。
          这里先把原文存下来，灌完再塞回去，并让 currentIndex = -1
          （否则 QComboBox 会把第一项自动选进编辑框，等于替用户做了决定）。
        """
        text = self._search_edit.text()
        combo = self._search_combo
        if not text.strip():
            self._candidates = []
            combo.clear()
            combo.setEditText("")
            self._map.set_filter("")
            self._readout("")
            return

        try:
            cands = search_teleport(text, limit=10)
        except Exception as exc:  # noqa: BLE001 - 搜索失败不该拖垮整个 tab
            logger.warning("传送点搜索失败：%s", exc, exc_info=True)
            cands = []

        self._candidates = list(cands)
        combo.clear()
        for i, c in enumerate(self._candidates):
            combo.addItem(c.label, i)
            combo.setItemData(
                i, f"{c.subtitle}\n匹配 {c.score:.2f}（{', '.join(c.reasons)}）",
                Qt.ToolTipRole)
        combo.setCurrentIndex(-1)
        combo.setEditText(text)

        # 地图上同步高亮：老行为保留，只是变成了「边打边亮」。
        self._map.set_filter(text)

        if not self._candidates:
            self._readout(f"「{text}」没匹配到传送点"
                          "（可以试试拼音，或换个更常见的说法）")
            return
        if needs_disambiguation(self._candidates):
            # ★ NEKO 交互的接缝在这里。
            #   现在没有 Console->NEKO 的反向通道（bridge.py 只有
            #   console_bridge.json 主控台->插件、console_commands.jsonl
            #   插件->主控台），所以本轮只把问题**显示出来**。
            #   等反向通道就位，把这行塞进通道即可，调用方不用改。
            self._readout(build_disambiguation_question(self._candidates))
        else:
            self._readout(f"「{text}」匹配 {len(self._candidates)} 个，最像："
                          f"{self._candidates[0].label}")

    def _on_pick_candidate(self, index: int) -> None:
        """用户在下拉里点了一条（或回车取第一条）-> 当成终点。"""
        if not (0 <= index < len(self._candidates)):
            return
        self._apply_candidate(self._candidates[index])

    def _on_search(self) -> None:
        """回车：取下拉里的第一条。"""
        if self._candidates:
            self._apply_candidate(self._candidates[0])
            return
        q = self._search_edit.text()
        self._map.set_filter(q)
        hits = self._map.selected_points()
        if not q:
            self._readout("显示默认传送点（神像 / 秘境）")
        elif hits:
            self._readout(f"「{q}」匹配 {len(hits)} 个："
                          + "、".join(p.name for p in hits[:6])
                          + ("…" if len(hits) > 6 else ""))
        else:
            self._readout(f"「{q}」没匹配到传送点")

    def _apply_candidate(self, cand) -> None:
        """把一条候选落成终点。

        用 ``tran_xz``（传送后落点）而不是 ``xz``（图标所在格子）——
        这是 ``tests/test_tabs_pathfind.py`` 钉着的既有约定。
        """
        point = cand.point
        self._search_combo.setEditText(cand.label)
        self._target_edit.setText(fmt_xz(*point.tran_xz))
        self._pending_target_anchor = point
        # ★ 地图自动平移 + 放大到候选点。以前只 `select_point`（居中），
        #   在全图视图下点一个传送点，缩放不变、用户根本看不出移到哪了。
        self._map.focus_point(point)
        self._map.set_filter("")
        self._readout(f"✓ 终点：{cand.label}（{cand.subtitle}）"
                      f" → {self._target_edit.text()}")

    def _on_desc_submit(self) -> None:
        """任务 C：描述 -> 目标。"""
        text = self._desc_edit.text()
        spec = resolve_target_from_description(text)
        self._apply_target(spec)

    def _apply_target(self, spec: TargetSpec) -> None:
        if spec.ok and spec.kind == "point":
            self._target_edit.setText(fmt_xz(spec.x, spec.z))
            self._readout(f"✓ 目标：坐标 {spec.label}")
        elif spec.ok and spec.kind == "anchor" and spec.anchor:
            self._target_edit.setText(fmt_xz(*spec.anchor.tran_xz))
            self._map.set_filter(spec.anchor.name)
            self._map.select_point(spec.anchor)
            self._readout(f"✓ 目标：{spec.anchor.name}（{spec.anchor.type_cn}）")
        else:
            self._readout(f"✗ 没解析出来：{spec.label}"
                          + (f"　—— {text_hint(spec)}" if text_hint(spec) else ""))

    def _readout(self, msg: str) -> None:
        self._map._readout.setText(msg)

    # ------------------------------------------------------------------
    # 动作
    # ------------------------------------------------------------------

    def _parse_xy(self, edit: QLineEdit, what: str) -> tuple[str | None, str]:
        """校验 'X,Z' 文本。起点不再走这里（改由 ``_resolve_start`` 按来源解析）。"""
        raw = edit.text().strip()
        if not raw:
            return None, f"请填写{what}"
        parts = raw.replace("，", ",").split(",")
        if len(parts) != 2:
            return None, f"{what}格式应为 'X,Z'，收到 {raw!r}"
        try:
            float(parts[0].strip())
            float(parts[1].strip())
        except ValueError:
            return None, f"{what}必须是两个数字，收到 {raw!r}"
        # ★ 返回**归一化后**的串，不是原文
        #   ------------------------------------
        #   这里原来 return raw.replace(" ", "")，全角逗号「，」会原样带出去。
        #   但 e2e_pathfind 的 _pair 是 ``float(s.split(",")[0]), float(s.split(",")[1])`` ——
        #   全角逗号 split 出来只有一个元素 -> IndexError -> 脚本 exit 2。
        #   症状是「本机校验通过，点启动后进程 1 秒就没了」，报错还来自
        #   子进程，跟真正原因（一个全角逗号）隔着十万八千里。
        #
        #   归一化只动分隔符、不重排数字（不用 float 回写），所以
        #   「4634.52，4687.13」->「4634.52,4687.13」，精度无损。
        return ",".join(p.strip() for p in parts), ""

    def _resolve_start(self) -> tuple[str | None, str]:
        """按来源解析起点。拿不到就返回错误说明，**绝不猜坐标**。"""
        src = self._current_source()
        anchor = getattr(self, "_pending_target_anchor", None)
        req = StartRequest(
            source=src,
            manual=self._start_edit.text(),
            anchor=anchor if src is StartSource.ANCHOR else None,
        )
        pos = get_start_position(req)
        if pos is None:
            if src.unavailable_reason:
                return None, src.unavailable_reason
            if src is StartSource.ANCHOR:
                return None, "请先在下方地图上点一个起点"
            return None, "请填写起点坐标，或在地图上点一下"
        return fmt_xz(*pos), ""

    def _on_start(self) -> None:
        start, err1 = self._resolve_start()
        if start is None:
            self._msg.setText(f"✗ {err1}")
            return
        target, err2 = self._parse_xy(self._target_edit, "终点")
        if target is None:
            self._msg.setText(f"✗ {err2}")
            return

        try:
            delay = float(self._delay_spin.text() or 0)
        except ValueError:
            self._msg.setText("✗ 启动延迟必须是数字")
            return
        if delay < 0:
            self._msg.setText("✗ 启动延迟不能为负")
            return

        ok, msg = self.ch.start(
            start_world=start, target_world=target,
            live=self._live.isChecked(), delay=delay,
            engine_running=self.engine.is_running(),
        )
        self._msg.setText(("✓ " if ok else "✗ ") + msg)
        self._apply_state(self.ch.state())

    def _on_stop(self) -> None:
        ok, msg = self.ch.stop()
        self._msg.setText(("✓ " if ok else "✗ ") + msg)
        self._apply_state(self.ch.state())

    # ------------------------------------------------------------------
    # 状态刷新
    # ------------------------------------------------------------------
    def _apply_state(self, state: str) -> None:
        text, color = STATE_STYLE.get(state, (state, "#89b4fa"))
        self._state_lbl.setText(text)
        self._state_lbl.setStyleSheet(f"color: {color};")
        self._lamp.setStyleSheet(f"background: {color}; border-radius: 7px;")
        code = self.ch.exit_code()
        self._exit_lbl.setText(f"exit={code}" if code is not None else "")
        self._btn_start.setEnabled(state != "running")

    def _refresh(self) -> None:
        engine_on = self.engine.is_running()
        running = self.ch.is_running()

        # ★ 两条护栏**都要**能让按钮置灰，而且非提权优先 ——
        #   非提权时就算引擎没跑也起不来，先说最根本的那条。
        if not self._admin and not running:
            self._guard_lbl.setStyleSheet("color: #f38ba8;")
            self._guard_lbl.setText(
                "⚠ 当前不是管理员 —— **寻路不可用**。\n"
                "   e2e_pathfind --live 有 UIPI 门禁（游戏内核级反作弊禁止"
                "低权限进程注入按键），不��权会 3 秒内自行退出。\n"
                "   解法：以管理员身份重启主控台，或把 config.toml 里的 "
                "console.require_admin 改成 false（那样只能查看，不能寻路）。")
            self._btn_start.setEnabled(False)
        elif engine_on and not running:
            self._guard_lbl.setStyleSheet("color: #f38ba8;")
            self._guard_lbl.setText(
                "⚠ 引擎正在运行 —— 寻路与引擎抢同一套键鼠，**不能并行**。"
                "请先停掉引擎。")
            self._btn_start.setEnabled(False)
        else:
            self._guard_lbl.setStyleSheet("color: #6c7086;")
            self._guard_lbl.setText(
                "✓ 无冲突（引擎未运行）" if not engine_on else "")
            self._btn_start.setEnabled(not running)

        st = self.ch.read_state()
        if not st:
            self._nav_state_lbl.setText("状态：—" if not running
                                        else "状态：脚本还没写状态文件")
        elif self.ch.is_state_stale():
            # ★ 陈旧比没有状态更危险 —— 文件里还留着最后一帧，不说的话
            #   用户会以为寻路还在努力工作。
            age = self.ch.state_age_s()
            phase = st.get("state") or "unknown"
            self._nav_state_lbl.setStyleSheet("color: #fab387;")
            self._nav_state_lbl.setText(
                f"⚠ 状态已陈旧 {age:.0f}s（{phase}）—— "
                f"寻路可能已经停了，点「停止寻路」确认一下")
        else:
            self._nav_state_lbl.setStyleSheet("")
            phase = st.get("state") or "unknown"
            dist = st.get("distance_m")
            bits = [f"阶段={phase}"]
            if dist is not None:
                bits.append(f"剩余={float(dist):.1f}m")
            for k in ("target_x", "target_z"):
                if k in st:
                    bits.append(f"{k}={st[k]}")
            self._nav_state_lbl.setText("状态：" + "  ".join(bits))

        evs = self.ch.read_recent_events(limit=10)
        lines = [f"{e.get('ts_ms', 0)}  {e.get('code', '?')}  "
                 f"{str(e.get('message', ''))[:60]}" for e in evs]
        self._events.setPlainText("\n".join(lines))


__all__ = ["FOREGROUND_HINT", "STALE_AFTER_S", "STATUS_REFRESH_MS", "PathfindTab"]