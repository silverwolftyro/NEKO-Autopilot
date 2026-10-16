"""规则页签：浏览 autopilot/rules/*.toml，切换每条规则的启用状态。

★ 范围（第一版刻意收窄）
------------------------
只做**只读浏览 + 启用/停用**。不新增规则、不编辑条件、不改优先级 ——
那些要一个真正的编辑器，不该塞进一个列表页里糊弄。

★ 引擎路径怎么来的
------------------
``app.py`` 里是 ``RulesTab()``，**零参数**构造。所以引擎路径不能从
构造参数拿，改成首次显示时自己 ``load_config(write_back=False)`` 读。
用 ``write_back=False`` 是刻意的：只是看了一眼规则页，不该顺手把用户的
config.toml 重写一遍。

★ 刷新策略
----------
    · 首次显示时加载一次
    · 「重新加载」按钮强制重读
    · 引擎启停**不**自动重载
    · **不做文件系统 watcher**

规则改动要重启引擎才生效，引擎自己也会读这些文件，所以"文件变了就偷偷
刷新"没有任何收益，只有在用户没预期的时候把选中项和滚动位置弄乱。

★ 改完 checkbox 之后**不**自动 reload
------------------------------------
新状态已经是勾对了的，再 reload 一次只会让选中项跳走、表格闪一下。
真对不上了（比如你手工改了 TOML），点「重新加载」就行。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QSignalBlocker, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView,
                               QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..channels.rules import RuleEditError, RuleFile, RulesRepository
from ..config import load_config

logger = logging.getLogger(__name__)

#: 左栏固定宽度（像素）。约 200 —— 装得下 "genshin_combat.toml（8 条）"。
LEFT_PANEL_WIDTH = 200

#: 「启用」列宽 + 行高。
#: 用 Qt 原生勾选后，指示器尺寸由 Qt 按当前 DPI 自己算，我们只管给足空间。
#: 行高给 28 是因为默认行高（~20）在 150% 缩放下会把指示器压扁。
CHECKBOX_COLUMN_WIDTH = 48
ROW_HEIGHT = 28

#: 表格列：标题 / 表头文案 / 相对宽度权重
COLUMNS = (
    ("启用", 0),
    ("名字", 320),
    ("优先级", 90),
    ("场景", 420),
)

#: 与全站配色一致的提示色
C_OK = "#a6e3a1"
C_ERR = "#f38ba8"
C_DIM = "#6c7086"

DEFAULT_HINT = ("勾选框控制规则是否启用。取消勾选 = 在 TOML 里写 "
                "enabled = false；重新勾选 = 删掉这一行回到默认。"
                "改完记得重启引擎。")


class RulesTab(QWidget):
    """规则列表页。"""

    TITLE = "规则"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._files: list[RuleFile] = []
        self._repo: RulesRepository | None = None
        #: 当前表格正在显示哪个文件。原生勾选不再携带上下文，
        #: 靠它把「第几行」还原成「哪个文件的哪条规则」。
        self._current: RuleFile | None = None
        self._loaded = False
        self._build()

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        split = QSplitter(Qt.Orientation.Horizontal)

        # ── 左：文件列表 ──────────────────────────────
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(6)
        title = QLabel("规则文件")
        title.setStyleSheet(f"color: {C_DIM};")
        left_lay.addWidget(title)
        self._list = QListWidget()
        left_lay.addWidget(self._list)
        left.setMaximumWidth(360)
        split.addWidget(left)

        # ── 右：规则表格 / 错误详情 ────────────────────
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(8)

        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels([c[0] for c in COLUMNS])
        self._table.verticalHeader().setVisible(False)
        # ★ 行高写死：默认行高（约 20px）在 150% 缩放下会把 Qt 原生勾选
        #   指示器**压扁**，用户看到的就是半个/残缺的方框。
        self._table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.itemChanged.connect(self._on_item_changed)
        header = self._table.horizontalHeader()
        for i, (_, weight) in enumerate(COLUMNS):
            if weight:
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
            else:
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
                self._table.setColumnWidth(i, CHECKBOX_COLUMN_WIDTH)
        right_lay.addWidget(self._table, 1)

        self._err = QLabel()
        self._err.setWordWrap(True)
        self._err.setTextFormat(Qt.TextFormat.PlainText)
        self._err.setStyleSheet(f"color: {C_ERR};")
        self._err.hide()
        right_lay.addWidget(self._err)

        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([LEFT_PANEL_WIDTH, 900])
        lay.addWidget(split, 1)

        # ── 底：状态 + 重新加载 ────────────────────────
        bottom = QHBoxLayout()
        self._status = QLabel()
        self._status.setStyleSheet(f"color: {C_DIM};")
        bottom.addWidget(self._status)
        bottom.addStretch(1)
        btn_reload = QPushButton("重新加载")
        btn_reload.clicked.connect(self._reload)
        bottom.addWidget(btn_reload)
        lay.addLayout(bottom)

        self._hint = QLabel(DEFAULT_HINT)
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(f"color: {C_DIM};")
        lay.addWidget(self._hint)

        self._list.currentItemChanged.connect(self._on_file_selected)

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:
        """**首次显示**才加载 —— 别的 tab 都用不到规则，白读一遍 IO
        只会拖慢主控台启动。"""
        super().showEvent(event)
        if not self._loaded:
            self._reload()

    def _reload(self) -> None:
        self._loaded = True

        cfg, _source = load_config(write_back=False)
        root = cfg.engine.resolved_path()
        self._repo = RulesRepository(root)

        # ★ 路径问题优先于文件问题：根目录都错了，报"某个文件解析失败"
        #   是误导 —— 真相是"引擎根本不在那儿"。
        if not root.is_dir():
            self._show_fatal(f"引擎路径未配置或无效：{root}")
            return
        if not self._repo.rules_dir.is_dir():
            self._show_fatal(f"未找到规则目录 {self._repo.rules_dir}")
            return

        try:
            self._files = self._repo.scan()
        except Exception as exc:  # noqa: BLE001 - 兜底不让 UI 崩
            logger.exception("扫描规则目录失败")
            self._show_fatal(f"读取规则目录失败：{exc}")
            return

        self._err.hide()
        self._table.show()

        good = [f for f in self._files if f.ok]
        total = sum(len(f.rules) for f in good)
        broken = len(self._files) - len(good)
        text = f"加载了 {len(good)} 个文件，{total} 条规则"
        if broken:
            text += f"，{broken} 个读取失败（左侧标红，点开看详情）"
        self._status.setText(text)

        self._list.clear()
        for f in self._files:
            if f.ok:
                item = QListWidgetItem(f"{f.name}（{len(f.rules)} 条）")
            else:
                item = QListWidgetItem(f"{f.name}（读取失败）")
                # 显式 QColor：setForeground 收的是 QBrush/QColor，
                # 传 Qt.GlobalColor 依赖 PySide6 的隐式转换，跨版本不稳。
                item.setForeground(QColor(C_ERR))
            item.setData(Qt.ItemDataRole.UserRole, f.path.name)
            self._list.addItem(item)

        if self._list.count():
            self._list.setCurrentRow(0)
        else:
            self._status.setText("规则目录是空的 —— 没有找到任何 .toml")

        self._set_hint(DEFAULT_HINT, C_DIM)

    def _show_fatal(self, message: str) -> None:
        """整页级错误（路径不对 / 目录不存在）。表格直接藏起来。"""
        self._current = None
        self._list.clear()
        self._clear_table()
        self._table.hide()
        self._err.setText(message)
        self._err.show()
        self._status.setText("没有加载到任何规则")

    # ------------------------------------------------------------------
    # 选中文件
    # ------------------------------------------------------------------
    def _current_file(self) -> RuleFile | None:
        item = self._list.currentItem()
        if item is None:
            return None
        name = item.data(Qt.ItemDataRole.UserRole)
        for f in self._files:
            if f.path.name == name:
                return f
        return None

    def _on_file_selected(self, _cur, _prev) -> None:
        f = self._current_file()
        if f is None:
            self._current = None
            return
        if not f.ok:
            # ★ 坏文件**绝不能让 _current 指着一个没有规则的文件**，
            #   否则用户在上一个文件的表格里点勾选，会写到错误的路径上。
            self._current = None
            self._clear_table()
            self._table.hide()
            self._err.setText(f"{f.name} 读取失败：\n{f.error}")
            self._err.show()
            return

        self._err.hide()
        self._table.show()
        self._fill_table(f)

    def _clear_table(self) -> None:
        # ★ clearContents() 会触发 itemChanged。必须静音，否则清表过程
        #   会被 _on_item_changed 当成"用户取消勾选"而误写 TOML。
        blocker = QSignalBlocker(self._table)
        try:
            self._table.clearContents()
            self._table.setRowCount(0)
        finally:
            del blocker

    def _fill_table(self, f: RuleFile) -> None:
        """填表。**任何异常都转成红字显示**，绝不留一张空表。

        ★ 为什么要 try/except 包住
        --------------------------
        这个函数是槽函数（由 ``currentItemChanged`` 触发）。Qt 的槽里抛异常，
        PySide6 只会往 **stderr** 打一段 traceback 然后**静默放弃整个槽** ——
        UI 上看不出任何异常，只剩下一张 `setRowCount()` 造出来的**空表**。

        上一版就踩了这个坑：漏 import ``QCheckBox``，于是
        ``setRowCount(7)`` 造出 7 个空行，紧接着第一行
        ``cb = QCheckBox()`` 抛 NameError，后面所有 ``setItem`` /
        ``setCellWidget`` 全没执行 —— 用户看到的就是"行数对但每格空白、
        checkbox 也没有"，而日志里那条 NameError 淹没在 Qt 的字体警告里。

        所以：**宁可整张表挂掉并大声报错，也不要"看起来像加载成功"。**

        ★ 为什么用 Qt 原生勾选而不是塞 QCheckBox
        ---------------------------------------
        上一版把真 ``QCheckBox`` 塞进 ``QWidget`` 再 ``setCellWidget``，
        结果指示器被固定尺寸和高 DPI 双重夹击，显示成半个/残缺。
        改成 ``QTableWidgetItem.setCheckState()`` 之后，**尺寸、样式、
        高 DPI 缩放全部交给 Qt 自己算** —— 它比我们更清楚该画多大，
        也不用手工给 ``::indicator`` 写死像素。
        """
        self._current = f
        self._clear_table()
        self._table.setRowCount(len(f.rules))
        # ★ 整表静音：填 19 个勾选状态时每个都会发 itemChanged。
        #   ★★ 必须用 try/finally 释放 —— 漏了的话表格会被**永久静音**，
        #   表现是"勾选框点不动"，比报错更难查。
        blocker = QSignalBlocker(self._table)
        try:
            for row, rule in enumerate(f.rules):
                check = QTableWidgetItem()
                check.setFlags(Qt.ItemFlag.ItemIsUserCheckable
                               | Qt.ItemFlag.ItemIsEnabled
                               | Qt.ItemFlag.ItemIsSelectable)
                check.setCheckState(Qt.CheckState.Checked if rule.enabled
                                    else Qt.CheckState.Unchecked)
                check.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row, 0, check)

                for col, value in ((1, rule.name), (2, rule.priority_text),
                                   (3, rule.scenes_text)):
                    item = QTableWidgetItem(value)
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled
                                  | Qt.ItemFlag.ItemIsSelectable)
                    if col == 3:
                        item.setToolTip(rule.note or "")
                    self._table.setItem(row, col, item)
        except Exception as exc:  # noqa: BLE001 - 这里就是要兜住一切
            logger.exception("填充规则表格失败（%s）", f.name)
            failed = exc
        else:
            failed = None
        finally:
            del blocker
        if failed is not None:
            self._current = None
            self._clear_table()
            self._table.setRowCount(0)
            self._table.hide()
            self._err.setText(
                f"规则表格渲染失败：{type(failed).__name__}: {failed}\n"
                f"请把这条发给开发者 —— 规则数据本身是好的（"
                f"{len(f.rules)} 条已读取），坏的是界面绘制。")
            self._err.show()
            self._set_hint(f"⚠ 渲染 {f.name} 失败：{failed}", C_ERR)
            return

        # 收尾自检：行数对得上、每行真的有字。**当场发现比让用户盯着空表猜好**
        filled = sum(
            1 for r in range(self._table.rowCount())
            if self._table.item(r, 1) is not None
            and self._table.item(r, 1).text()
        )
        if filled != len(f.rules):
            logger.error("表格自检失败：应有 %d 行有名字，实际 %d 行",
                         len(f.rules), filled)
            self._set_hint(
                f"⚠ 表格自检失败（{filled}/{len(f.rules)} 行有内容），见日志。",
                C_ERR)

    # ------------------------------------------------------------------
    # 启用/停用
    # ------------------------------------------------------------------
    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        """原生勾选的变化入口。**只有第 0 列**算勾选，其余列是只读文本。"""
        if item.column() != 0:
            return
        f = self._current
        if f is None:
            return
        row = item.row()
        if not (0 <= row < len(f.rules)):
            return
        rule = f.rules[row]
        on = item.checkState() == Qt.CheckState.Checked
        if on == rule.enabled:
            return                      # 状态没真变（比如程序自己刷的），不写盘
        self._on_toggled(on, f.path, rule.name)

    def _on_toggled(self, on: bool, path: Path, name: str) -> None:
        if self._repo is None:
            return
        try:
            action = self._repo.set_enabled(path, name, on)
        except RuleEditError as exc:
            self._set_hint(f"⚠ {exc}", C_ERR)
            # ★ 写失败了就把界面**回滚**到磁盘的真实状态 ——
            #   留着勾却没落盘，用户重启引擎才发现规则没变，
            #   会以为是引擎的 bug。
            #
            #   ★ 必须**延迟一轮**再重载：现在正处在 itemChanged 回调里，
            #     而 _reload() 会 setRowCount(0) 把正在被 Qt 遍历的
            #     这些 item 销毁掉 —— 在信号回调里销毁 sender 会崩。
            #     QTimer.singleShot 让它回到事件循环再执行。
            QTimer.singleShot(0, self._reload)
            return
        except Exception as exc:  # noqa: BLE001 - 兜底
            logger.exception("修改规则启用状态时异常")
            self._set_hint(f"⚠ 修改失败：{exc}", C_ERR)
            QTimer.singleShot(0, self._reload)
            return

        # 写成功了：同步内存里的 RuleInfo，否则界面状态和磁盘会各说各话
        #（下一次重载前用户再点同一个框，on == rule.enabled 会误判成"没变"）
        for rule in (self._current.rules if self._current else []):
            if rule.name == name:
                rule.enabled = on
                break
        self._set_hint(f"{action}。规则改动需重启引擎生效。", C_OK)

    # ------------------------------------------------------------------
    def _set_hint(self, text: str, color: str) -> None:
        self._hint.setText(text)
        self._hint.setStyleSheet(f"color: {color};")

    def shutdown(self) -> None:
        """与其它 tab 保持一致的空实现。这里没有定时器要停。"""
        self._current = None


__all__ = [
    "CHECKBOX_COLUMN_WIDTH",
    "C_DIM",
    "C_ERR",
    "C_OK",
    "COLUMNS",
    "DEFAULT_HINT",
    "LEFT_PANEL_WIDTH",
    "ROW_HEIGHT",
    "RulesTab",
]
