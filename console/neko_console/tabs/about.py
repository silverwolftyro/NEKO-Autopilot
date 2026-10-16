"""关于页签：版本 / 许可 / 路径信息（本块唯一有真实内容的一页）。"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .. import __version__
from ..paths import base_dir, is_frozen


def _build_time() -> str:
    """这次构建的时间。**读 exe 文件的修改时间**；开发模式返回 ``dev``。

    ★ 为什么读 mtime 而不是编译期写入常量
      写死一个 ``__build_time__`` 需要在打包时注入，等于给构建脚本加
      一道新的耦合；而 exe 的 mtime 在 build.ps1 每次重新打包后自动刷新，
      **零维护成本**，而且它反映的正是"用户手上这份二进制是什么时候产出的"
      —— 比"源码什么时候写的"对排查更有用（比如"我到底有没有用上刚打的包"）。

    ★ 读不到就说读不到，不猜
      文件被挪走、权限不足、exe 被杀毒软件处理过都可能拿不到 mtime。
      那就显示 ``未知`` —— 显示一个编造的时间比不显示更糟。
    """
    if not is_frozen():
        return "dev"
    try:
        ts = Path(sys.executable).stat().st_mtime
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        return "未知"


class AboutTab(QWidget):
    """关于。展示版本、许可、运行模式与关键路径。"""

    TITLE = "关于"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(12)
        lay.addWidget(QLabel(self.TITLE))

        mode = "打包（Nuitka onefile）" if is_frozen() else "开发模式"
        text = (
            f"NEKO-Console {__version__}\n"
            f"构建时间：{_build_time()}\n"
            f"运行模式：{mode}\n"
            f"用户目录：{base_dir()}\n"
            f"许可证：Apache-2.0\n"
            f"图形栈：PySide6（LGPL）\n\n"
            "本块为主控台空壳：引擎、规则、悬浮卡片三个页签尚未接通。"
        )
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(lbl.textInteractionFlags().TextSelectableByMouse)
        lay.addWidget(lbl)
        lay.addStretch(1)
