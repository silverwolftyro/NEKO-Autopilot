"""管理员提权。

★ 为什么需要它
--------------
``e2e_pathfind.py --live`` 有一道 UIPI 门禁：游戏的内核级反作弊让 Windows
禁止低完整性级别进程向它注入按键，所以脚本**必须**以管理员身份运行，
否则 3 秒内 ``return 2`` 退出。

症状非常难查：Console 界面显示「已启动 pid=xxxx」，两秒后进程自己没了，
状态区一片空白，日志里只有一行退出码 2。所以干脆在 Console 启动时
就提权 —— 一次 UAC 换掉后面所有的「为什么又不动了」。

★ 提权失败**不阻断** Console
--------------------------
用户点了 UAC 的「否」是合法的选择，不是错误。所以提权失败时
Console 照常启动，只是标记为「非提权模式」，寻路按钮置灰并说清原因。
只用来查看状态 / 改规则的用户根本不需要管理员。

★ 重新拉起自己时怎么重建命令行
----------------------------
打包形态（``Console.exe``）和源码形态（``python -m neko_console``）
的重新启动参数完全不同。判断依据用 :func:`neko_console.paths.is_frozen`
而不是 ``"__compiled__" in globals()`` —— Nuitka 把 ``__compiled__``
注入的是 ``sys.modules["__main__"]``，在本模块的 ``globals__`` 里查
**永远是 False**，两种形态会被一起误判（main.py:124 记着同一个坑）。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

#: 提权成功后，原进程用这个退出码退出。
#: 选 0 而不是 1：用户看到的「主控台关了」是**预期**行为，不是失败。
RELAUNCH_EXIT_CODE = 0

# ★ 这里**没有**「已提权」的哨兵变量，而且是有意不写的。
#   最自然的写法是设一个环境变量给提权后的进程，让它别再弹第二次 UAC ——
#   但 ``ShellExecuteW(..., "runas", ...)`` 是经由系统的提权宿主启动的，
#   **不继承调用方的环境变量**，那个哨兵永远传不过去，是个假实现。
#
#   真正防重复提权的是 :func:`is_admin`：提权成功的新进程
#   ``IsUserAnAdmin()`` 必然为 True，直接就进 True 分支了。
#   「ShellExecuteW 报成功但实际没提权」在 Windows 上不会发生
#   （rc > 32 意味着提权宿主已经接受并创建了进程），所以不需要额外哨兵。


def is_admin() -> bool:
    """当前进程是不是管理员。非 Windows / 查询失败一律返回 False。

    用 ``shell32.IsUserAnAdmin`` 而不是 token 检查 —— 它不需要关掉
    UAC 生效时的「模拟」行为，也不会在某些组策略下抛异常。
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception as exc:  # noqa: BLE001 - 查不到就当没提权，不能崩
        logger.debug("IsUserAnAdmin 失败（按非提权处理）：%s", exc)
        return False


def _relaunch_argv() -> list[str] | None:
    """提权后要执行的完整命令行。构造不出来返回 None。"""
    from neko_console.paths import is_frozen

    if is_frozen():
        exe = Path(sys.executable)
        if not exe.is_file():
            logger.error("打包形态下 sys.executable 不存在：%s", exe)
            return None
        return [str(exe)]

    # 源码形态：用 ``-m neko_console`` 重新拉起。
    # 刻意不用 ``sys.argv[0]``：那样在 ``python -m`` 下拿到的是 __main__.py
    # 的路径，直接跑会丢掉 -m 的包上下文。
    exe = Path(sys.executable)
    if not exe.is_file():
        logger.error("源码形态下 sys.executable 不存在：%s", exe)
        return None
    return [str(exe), "-m", "neko_console"]


def relaunch_elevated() -> bool:
    """用 UAC 重新拉起自己。**成功发起返回 True**（不代表用户同意了）。

    :returns: True 只说明 ShellExecuteW 调用成功发出了请求；用户点「否」
        时提权后的进程压根不会启动，**这里察觉不到**。所以调用方必须
        按「仍是非提权模式」处理，并且 :func:`ensure_admin` 会在
        收到哨兵变量时直接认定自己已提权。
    """
    if sys.platform != "win32":
        return False
    argv = _relaunch_argv()
    if not argv:
        return False

    params = subprocess.list2cmdline(argv[1:])
    # ★ 用 "runas" 动词弹 UAC。
    #   lpDirectory 传当前工作目录 —— 不传的话新进程的 cwd 会变成
    #   C:\Windows\System32，而 Console 有几处按相对路径找资源。
    cwd = os.getcwd()
    try:
        import ctypes

        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", argv[0], params, cwd, 1)
        # ShellExecuteW 返回 > 32 才是成功；它**不是** HRESULT。
        logger.info("ShellExecuteW(runas) rc=%s  参数=%s", rc, params)
        return int(rc) > 32
    except Exception as exc:  # noqa: BLE001
        logger.error("提权失败：%s", exc)
        return False


def ensure_admin(*, required: bool) -> bool:
    """保证管理员权限。返回**当前是否可继续按提权模式对待**。

    :param required: 配置项 ``console.require_admin``。为 False 时
        直接返回当前状态，不弹 UAC（纯查看模式的用户不该被 UAC 打扰）。

    流程::

        已是管理员 ──────────────► True（直接跑）
        非管理员 + required=False ─► False（明确不想要）
        非管理员 + required=True ─► 弹 UAC → 原进程 exit

    防重复提权靠 ``is_admin()``：提权成功的新进程它必然为 True。
    """  # noqa: E501
    if is_admin():
        return True
    if not required:
        logger.info("未提权，且配置关闭了 require_admin —— 按非提权模式启动")
        return False

    logger.warning("当前不是管理员，而 console.require_admin=true —— 尝试 UAC 提权")
    ok = relaunch_elevated()
    if ok:
        # 提权后的实例会带着哨兵变量启动，这里直接退出，别留着两个实例
        raise SystemExit(RELAUNCH_EXIT_CODE)
    logger.warning("提权未成功（用户可能点了「否」）—— 继续以非提权模式启动，"
                   "寻路会不可用")
    return False


__all__ = [
    "RELAUNCH_EXIT_CODE",
    "ensure_admin",
    "is_admin",
    "relaunch_elevated",
]