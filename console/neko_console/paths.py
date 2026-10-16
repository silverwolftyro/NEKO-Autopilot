"""路径解析：开发模式与 Nuitka onefile 模式下的"用户目录"判定。

★ 这是本项目最容易踩的坑
----------------------
Nuitka ``--onefile`` 打包后，程序运行时会被解压到一个**临时目录**
（``%TEMP%\onefile_xxx_123456``），所有 ``__file__`` 都指向那个临时目录。

    若配置写到 ``Path(__file__).parent``，结果就是：
    用户双击 Console.exe 跑一次 → 临时目录里多了个 config.toml → 退出
    临时目录被清理 → 用户下次改的配置全部丢失。

所以必须区分两个目录：

    BASE   用户双击 exe 的那个目录   ← **配置写这里**
    BUNDLE 打包解压的临时目录       ← **只读内置 default.toml**

Nuitka 会把 ``__compiled__`` 注入 ``__main__`` 模块，不是注入到
``paths.py`` 自己。所以**不能**写 ``"__compiled__" in globals()`` ——
那永远是 False，必须去 ``sys.modules["__main__"]`` 里取。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

#: 日志文件名
LOG_FILE_NAME = "console.log"

#: 单文件上限，超过就改名 .1 重新开（简单轮转，不引入 logging.handlers）
MAX_LOG_BYTES = 2 * 1024 * 1024

#: 版本号，写进启动横幅。打包脚本与 pyproject 里也有，但日志不能为拿一个
#: 版本号去 import 应用包（那会触发一堆 import，诊断时反而添乱）。
APP_VERSION = "0.1.0"


def _compiled():
    """取 Nuitka 注入的 ``__compiled__`` 对象；非打包模式返回 None。"""
    main_mod = sys.modules.get("__main__")
    return getattr(main_mod, "__compiled__", None) if main_mod else None


def is_frozen() -> bool:
    """是否运行在 Nuitka onefile 里。"""
    return _compiled() is not None


def bundle_dir() -> Path:
    """只读资源根：打包后是解压临时目录，开发时是项目根。

    打包后用于读 ``neko_console/config/default.toml``（内置默认配置）。
    """
    c = _compiled()
    if c is not None:
        return Path(getattr(c, "containing_dir", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def base_dir() -> Path:
    """**用户目录**：配置文件、运行时通道文件都落这里。

    打包后 = exe 所在目录（用户双击的那个），不是临时解压目录。
    开发时 = 项目根（``neko_console/`` 的上一级）。
    """
    c = _compiled()
    if c is not None:
        # containing_dir 是 exe 自身所在目录，正是用户双击的位置
        return Path(getattr(c, "containing_dir", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def config_path() -> Path:
    """用户配置文件：``<BASE>/config.toml``。"""
    return base_dir() / "config.toml"


def bundled_default_config() -> Path:
    """内置默认配置：``<BUNDLE>/neko_console/config/default.toml``。"""
    return bundle_dir() / "neko_console" / "config" / "default.toml"


def runtime_dir() -> Path:
    """运行时通道目录（与引擎 / 卡片的 IPC 落点）。**本块空置，只建目录。**"""
    d = base_dir() / "runtime"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _resource_root() -> Path:
    """**打包资源**的根目录。**与 base_dir() 不是一回事，别混。**

        base_dir()      = exe 所在目录      → 用户数据（config.toml / runtime / log）
        _resource_root() = 程序文件所在目录  → 打包内置资源（assets/）

    开发模式下两者相同（都是项目根）；**打包成 onefile 之后两者分道扬镳**：

        base_dir()       = D:\\...\\build           （用户双击 exe 的地方）
        _resource_root() = %TEMP%\\onefile_xxx_... （Nuitka 解压出来的地方）

    Nuitka 的 ``--include-data-files=assets/icon.ico=assets/icon.ico`` 落在
    **解压目录**里。之前 :func:`asset` 按 ``base_dir()`` 去找，于是去
    ``D:\\...\\build\\assets\\icon.ico`` 找 —— 那儿什么都没有。

    而 ``QIcon`` 拿到 null 图标时，Windows 托盘**什么都不画**：进程活着、
    窗口正常，就是看不见图标。症状极具迷惑性，根因却只是取错了根目录。

    ★ 为什么用 ``__file__`` 上溯而不是 ``bundle_dir()``
      两者在开发模式下等价，打包模式下 :func:`bundle_dir` 走
      ``__compiled__.containing_dir``（带一个 ``sys.executable`` 的默认值兜底）。
      这里显式走 ``__file__``，是因为它**直接就是"Nuitka 把本模块放到哪"**
      的答案，不需要再猜 containing_dir 在 onefile 下到底指哪儿 ——
      那正是上一次踩坑的地方。
    """
    return Path(__file__).resolve().parent.parent


def _resource_roots() -> list[Path]:
    """资源根候选，按优先级排列。第一个**确实存在目标文件**的会被选中。

    ★ 为什么要多个候选
      `_resource_root()` 是首选，但"资源被解到哪"这件事受 build.ps1 的
      写法影响，而我们没法从代码侧断言它一定落在解压目录的根上。
      与其猜，不如依次试一遍 —— 反正检查 ``Path.exists()`` 只是一次 stat。

    候选里保留了 ``sys.executable`` 所在目录（也就是旧行为），这样
    "把 assets/ 直接摆在 exe 旁边"这种手工摆法也仍然能用。
    """
    roots: list[Path] = []
    try:
        roots.append(_resource_root())
    except Exception:  # noqa: BLE001 - 路径解析不该把资源定位带崩
        pass
    c = _compiled()
    if c is not None:
        cd = getattr(c, "containing_dir", None)
        if cd:
            roots.append(Path(cd))
    try:
        roots.append(Path(sys.executable).parent)
    except Exception:  # noqa: BLE001
        pass
    # 去重但保持顺序（Path 在 3.11 默认可哈希）
    seen: set[Path] = set()
    out: list[Path] = []
    for r in roots:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def asset(name: str) -> Path:
    """打包资源（图标等）。

    ★ 走 :func:`_resource_roots` 而不是 :func:`base_dir`
      打包后这两者指向不同的目录，资源在**前者**下面。细节见
      :func:`_resource_root` 的说明 —— 托盘图标消失就是这里取错根目录。

    :returns: **实际存在**的那个候选路径；若所有候选都没有该文件，返回
        首选候选拼出来的路径（由调用方据此判定"资源缺失"，
        例如 :meth:`neko_console.tray.Tray` 会打出 ``isNull=True``）。
    """
    roots = _resource_roots()
    for root in roots:
        p = root / name
        try:
            if p.exists():
                return p
        except OSError:
            continue
    return (roots[0] if roots else Path.cwd()) / name


def console_commands_path() -> Path:
    """插件 -> 主控台的命令文件：``<BASE>/runtime/console_commands.jsonl``。

    ★ 为什么在 paths 里再写一份
      插件侧（另一个仓库、另一个进程）也要找这个文件。它无法 import 本包，
      只能把路径"约定"下来 —— 所以这个文件名和它的位置必须是一个可被
      外部复述的**确定值**，而不是散落在某个类的 ``__init__`` 里。

    与 :func:`bridge` 状态文件同目录，两个方向的文件放一起，方便排查。
    """
    return base_dir() / "runtime" / "console_commands.jsonl"


def log_path() -> Path:
    """日志文件：优先 ``<BASE>/console.log``，BASE 不可写则回退到
    ``%LOCALAPPDATA%\\NEKO-Console\\console.log``，再不行退临时目录。

    ★ 为什么要回退
    --------------
    打包后 ``BASE`` = exe 所在目录。如果用户把 exe 放在
    ``C:\\Program Files\\...``，那个目录对普通用户是**只读**的 ——
    而 exe 用的是 ``--windows-console-mode=disable``，一旦日志写不进去，
    stdout/stderr 全丢，用户点 X 之后托盘图标消失这类问题就**彻底没有证据**。
    那时候唯一还能救命的只有"总得有个地方能写"。

    ★ 判定方式是**真开一次文件**，不是 ``os.access``
      ``os.access(W_OK)`` 在 Windows 上对提权的进程会误判，而且目录可写
      不等于文件可写。这里直接 append 打开一次，最诚实。

    ★ 失败也不抛 —— 日志没了不能反过来把程序搞崩。
    """
    for d in _log_dir_candidates():
        try:
            d.mkdir(parents=True, exist_ok=True)
            p = d / LOG_FILE_NAME
            with open(p, "a", encoding="utf-8"):
                pass                       # 只为验证可写，不写内容
            return p
        except OSError:
            continue
    return Path(tempfile.gettempdir()) / LOG_FILE_NAME


def _log_dir_candidates() -> list[Path]:
    """按优先级给出候选日志目录。"""
    out: list[Path] = []
    try:
        out.append(base_dir())
    except Exception:  # noqa: BLE001 - 路径解析都不该把日志功能带崩
        pass
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        out.append(Path(local) / "NEKO-Console")
    return out


__all__ = [
    "APP_VERSION",
    "LOG_FILE_NAME",
    "MAX_LOG_BYTES",
    "asset",
    "base_dir",
    "bundled_default_config",
    "bundle_dir",
    "config_path",
    "console_commands_path",
    "is_frozen",
    "log_path",
    "runtime_dir",
]
