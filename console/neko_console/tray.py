"""系统托盘封装：右键菜单（打开/完全退出）+ 双击恢复 + 首次气泡提示。

★ 关窗口 ≠ 退出
----------------
用户点主窗口的 X 时**只隐藏窗口**，进程继续活着（托盘里还在）。
真正退出必须走托盘右键菜单的「完全退出」。

这是桌面软件的惯例，但很多程序做错 —— 用户点了 X 发现进程还在、
资源还占着，下次双击又起一个，最后一堆僵尸进程。所以这里做成
配置项可关，但默认开。

★ 为什么菜单和气泡都强调「完全退出」
------------------------------------
这是一个能截屏的高权限常驻程序。"进程还在"对用户不是小事。
菜单写「退出」太轻，气泡如果不点破，用户会一直以为点 X 就关掉了。
所以两处都用「完全退出」，并让气泡直接说"仍在运行"。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .paths import asset

logger = logging.getLogger(__name__)


class Tray:
    """QSystemTrayIcon 封装。"""

    def __init__(self, *,
                 on_open: Callable[[], None],
                 on_quit: Callable[[], None]) -> None:
        self._on_open = on_open
        self._on_quit = on_quit
        self._notice_shown = False

        # ★ QIcon 必须挂在实例上。
        #   局部变量会在 __init__ 返回后被 GC —— QSystemTrayIcon 只在
        #   C++ 侧留了一个拷贝，Python 侧对象没了，后续任何隐式引用
        #   都可能拿到悬空指针。
        icon_path = str(asset("assets/icon.ico"))
        self._pix = QIcon(icon_path)
        # ★★★ 打包后托盘图标消失，第一嫌疑就是这条路径 ★★★
        #   源码模式下 asset() 指向 <项目根>/assets/icon.ico；
        #   打包后它指向 **onefile 的临时解压目录**。如果 build.ps1 没把
        #   assets/ 一起打进去（或者层级不对），QIcon 就是 null，
        #   而 null 图标在 Windows 托盘里**什么都不画** —— 进程活着、
        #   窗口正常，就是看不见图标。日志里必须有这条完整路径才能定位。
        logger.info("托盘图标路径: %s", icon_path)
        logger.info("托盘图标文件存在: %s", os.path.isfile(icon_path))
        if self._pix.isNull():
            # 图标缺失不该让主控台起不来 —— 用系统默认图标继续
            logger.warning("QIcon 加载失败（isNull=True）：%s", icon_path)
        else:
            logger.info("QIcon 加载成功（isNull=False）")

        # ★ 托盘图标必须挂在 QApplication 下。
        #   无 parent 时由 Python 拥有生命周期，任何一次引用断裂
        #   （异常回溯、lambda 捕获失效等）都会把它析构掉 ——
        #   表现就是"托盘图标莫名消失，但进程还在"。
        app = QApplication.instance()
        self.icon = QSystemTrayIcon(self._pix, app)
        self.icon.setToolTip("NEKO-Console")

        # ★ 托盘子系统的两项能力。分开记，因为"图标不显示"至少有三种
        #   完全不同的病因，处置方式也不同：
        #     · isSystemTrayAvailable() = False -> 这台机器/这个会话压根
        #       没有托盘（服务会话、某些远程会话），改代码没用；
        #     · supportsMessages() = False       -> 图标能显示，但气泡
        #       弹不出来（"点 X 连提示都没有"就是它）；
        #     · QIcon isNull                       -> 资源没打进去。
        logger.info("QSystemTrayIcon.isSystemTrayAvailable() = %s",
                    QSystemTrayIcon.isSystemTrayAvailable())
        logger.info("QSystemTrayIcon.supportsMessages() = %s",
                    QSystemTrayIcon.supportsMessages())

        # ★★★ 最关键的一处：QMenu 曾经是局部变量 ★★★
        #   QAction("打开", menu) 只把 menu 设为 action 的 **C++ 父对象**，
        #   方向是 action → menu，**不会**反向延长 menu 的 Python 生命周期。
        #   于是 __init__ 一返回 menu 就被 GC、C++ 对象被 delete，
        #   而 QSystemTrayIcon 还握着 setContextMenu(menu) 的指针。
        #   Windows 上这会让 shell 通知区域的图标数据失效 ——
        #   症状是"关掉主窗口后，托盘图标也跟着没了，进程却还在跑"。
        self.menu = QMenu()
        act_open = QAction("打开", self.menu)
        act_open.triggered.connect(self._on_open)
        # ★ 写「完全退出」而不是「退出」
        #   "退出"太轻，用户会以为和点 X 是一回事 —— 而点 X 只是缩托盘、
        #   进程还在跑。差两个字，但决定了用户会不会在任务管理器里
        #   盯着一个"退出了却还在"的高权限进程发愁。
        act_quit = QAction("完全退出", self.menu)
        act_quit.triggered.connect(self._on_quit)
        self.menu.addAction(act_open)
        self.menu.addSeparator()
        self.menu.addAction(act_quit)
        # action 也存一份引用，双保险（它们的父是 menu，理论上够，
        # 但 PySide6 的父子所有权在混用时容易出意外）
        self._actions = (act_open, act_quit)

        self.icon.setContextMenu(self.menu)
        # 双击恢复
        self.icon.activated.connect(self._on_activated)
        self.icon.show()
        # ★ show() 之后立刻回读一次。isVisible()=False 而进程活着，
        #   说明图标**根本没挂上托盘**（资源为空、或托盘子系统不可用）——
        #   这正是打包后"点 X 之后托盘区看不到图标"要区分的第一现场。
        logger.info("托盘图标 show() 之后 isVisible() = %s",
                    self.icon.isVisible())
        logger.info("托盘图标已创建并显示（menu 已挂实例引用）")

    # ------------------------------------------------------------------
    def hide(self) -> None:
        """隐藏托盘图标。**只在真正退出时调**，缩到托盘绝不调。"""
        try:
            self.icon.hide()
            # 退出路径的留证：托盘摘掉了，进程也该马上消失
            logger.info("托盘图标 hide() 已调用，isVisible() = %s",
                        self.icon.isVisible())
        except Exception as exc:  # noqa: BLE001 - 退出路径不该抛
            logger.warning("隐藏托盘图标失败（忽略）：%s", exc)

    def is_visible(self) -> bool:
        try:
            return self.icon.isVisible()
        except Exception:  # noqa: BLE001
            return False

    def _on_activated(self, reason) -> None:
        """托盘点击。

        Windows 系统托盘惯例是单击激活，但**用户明确要求双击才打开**，
        单击只做「选中」—— 视觉上是图标被按下一下（这是系统行为，
        我们不需要额外做什么）。

            Trigger(1)      单击     -> 不做事（只选中）
            DoubleClick(2)  双击     -> 打开窗口
            Context(3)      右键     -> 弹菜单（不处理，交给 Qt 默认）

        右键菜单保持不变。
        """
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._on_open()

    def notify_minimized(self) -> None:
        """首次缩到托盘时弹一次气泡。之后不再打扰。

        ★ 文案里必须有「仍在运行」和「完全退出」
          --------------------------------------
        用户点 X 之后最常见的误解是"我关掉了"。可实际发生的是
        "窗口没了、进程还在、而且这东西能截屏"。所以这句话的职责
        不是介绍功能，而是**纠正一个错误认知**：

            主控台仍在运行。右键托盘图标可完全退出。

        「仍在运行」否掉"我关了"，「完全退出」给出真正的出路 ——
        而且和菜单项的字面完全一致，用户照着点就行。
        """
        if self._notice_shown:
            return
        self._notice_shown = True
        # ★ "缩到托盘"这一刻托盘到底还在不在 —— 这是打包问题里最关键的一行。
        #   任务原本要求写在 MainWindow.closeEvent 里，但那个文件本轮不允许改。
        #   这里用 notify_minimized() 代替：它是 closeEvent 的 hide() 分支
        #   唯一能经过的托盘侧钩子（closeEvent -> _on_close -> 本方法）。
        #   ⚠ 它只在**首次**缩托盘时被调用（_on_close 里有 tray_notice_shown
        #     提前 return），所以后续几次缩托盘不会有这行日志。
        logger.info("缩到托盘：tray.isVisible() = %s（isNull=%s）",
                    self.icon.isVisible(), self._pix.isNull())
        try:
            self.icon.showMessage(
                "主控台已缩到托盘（未退出）",
                "主控台仍在运行。右键托盘图标可完全退出。",
                QSystemTrayIcon.MessageIcon.Information,
                6000,
            )
        except Exception as exc:  # noqa: BLE001 - 气泡失败不该影响隐藏
            logger.warning("托盘提示失败（忽略）：%s", exc)

    def message(self, title: str, body: str) -> None:
        try:
            self.icon.showMessage(
                title, body,
                QSystemTrayIcon.MessageIcon.Information, 4000)
        except Exception as exc:  # noqa: BLE001
            logger.warning("托盘消息失败（忽略）：%s", exc)


__all__ = ["Tray"]
