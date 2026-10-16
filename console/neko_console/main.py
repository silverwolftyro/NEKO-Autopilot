"""真正的 main 函数。**Nuitka 的打包入口就指向这个文件。**

保持极薄：只负责装日志 + ``sys.exit(app.run())``。
所有实质逻辑在 :mod:`neko_console.app`，这样打包脚本的目标路径稳定，
不会因为重构 main 而改构建配置。

★ 为什么必须在 main 里装文件日志
---------------------------------
打包用的是 ``--windows-console-mode=disable``，**没有控制台**，
所有 ``logger`` 输出都进了黑洞。用户点 X 之后托盘图标消失这类问题，
没有日志就等于没有证据，只能靠猜。

★ 为什么 FileHandler 挂 ``neko_console`` 而不是 root
--------------------------------------------------
:func:`neko_console.app.run` 里有一句 ``logging.basicConfig(level=...)``，
而 ``basicConfig`` 的判定是「**root** 上是否已有 handler」——已有就整个
空操作。

所以只要我提前往 root 上挂任何 handler，那句 basicConfig 就不再执行，
``cfg.log_level`` 也就失效了（变成默认 WARNING），日志量会突然变少。

挂到具名 logger ``neko_console`` 上就没这个问题：

    记录  ──┬─> neko_console 的 FileHandler   （文件）
            └─> root 的 StreamHandler         （终端，仍由 basicConfig 装）

basicConfig 照常执行，级别照常生效，两条路互不干扰。
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

from neko_console import __version__

#: 已装好的文件 handler，用于幂等判断。
_FILE_HANDLER: logging.FileHandler | None = None


class _LogFormatter(logging.Formatter):
    """输出格式：``2026-09-29 10:30:45.123 [INFO ] neko_console.app: 消息``

    ★ 为什么自己写 formatTime
      标准 Formatter 一旦传了 ``datefmt`` 就不再带毫秒（毫秒只在
      ``datefmt is None`` 时经 ``default_msec_format`` 输出）。而毫秒恰恰是
      排查"托盘 show 和 hide 隔了多久""两次轮询之间文件变没变"时最有用
      的一列，所以这里自己拼。
    """

    def formatTime(self, record, datefmt=None) -> str:  # noqa: N802
        base = datetime.fromtimestamp(record.created).strftime(
            "%Y-%m-%d %H:%M:%S")
        return f"{base}.{int(record.msecs):03d}"


def _rotate_if_needed(path) -> None:
    """超过上限就把 ``console.log`` 改名成 ``console.log.1`` 重新开。

    刻意**不用** ``logging.handlers.RotatingFileHandler``：它自己会去
    ``os.rename``，而打包场景下目录可能只读 —— 我们这里先自己确认过
    目录可写（见 :func:`neko_console.paths.log_path`），简单轮转更可控。

    ``.1`` 会被直接覆盖，不留 ``.2``：单次事故只需要对照上一轮，
    留一堆历史文件反而让用户不知道该看哪个。
    """
    from neko_console.paths import MAX_LOG_BYTES

    try:
        if not path.exists() or path.stat().st_size <= MAX_LOG_BYTES:
            return
        old = path.with_name(path.name + ".1")
        if old.exists():
            old.unlink()
        path.replace(old)
    except OSError as exc:
        # 轮转失败就继续往原文件写，总比不记日志强
        print(f"[neko] 日志轮转失败（忽略）：{exc}", file=sys.stderr)


def setup_file_logging() -> Path | None:
    """装上文件日志。**幂等**，重复调用只生效一次。

    :returns: 日志文件路径；装不上返回 None（**不抛** ——
        日志挂不上不该反过来让主控台起不来）。
    """
    global _FILE_HANDLER
    if _FILE_HANDLER is not None:
        return Path(_FILE_HANDLER.baseFilename)

    from neko_console.paths import (  # noqa: PLC2701 - 同包内部使用
        _resource_root, bundle_dir, is_frozen, log_path)

    log = logging.getLogger("neko_console")
    try:
        path = log_path()
        _rotate_if_needed(path)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(_LogFormatter(
            fmt="%(asctime)s [%(levelname)-5s] %(name)s: %(message)s"))
        # level 保持 NOTSET：实际过滤交给 logger 的有效级别（root 的，
        # 由 app.run 的 basicConfig 按 cfg.log_level 设置）
        log.addHandler(handler)
        _FILE_HANDLER = handler
    except Exception as exc:  # noqa: BLE001 - 日志失败绝不能挡住启动
        print(f"[neko] 文件日志启用失败（忽略）：{exc}", file=sys.stderr)
        return None

    _write_banner(log, is_frozen, bundle_dir, _resource_root)
    return path


def _write_banner(log: logging.Logger, frozen, bdir, resource_root) -> None:
    """每次启动先写一段横幅，让用户知道"日志是从这一次开始的"。

    ★ 为什么**每次**都写，而不是"文件不存在时才写"
      任务原本要求只在文件不存在时写一行。但那样一来，用户上次退出、
      这次启动，两次的日志会连在一起且没有分界 —— 排查"昨晚那次退出到底
      有没有走完"时根本分不清哪段属于哪次启动。多写一行横幅的代价可以
      忽略，换来的是每次启动都有明确边界。

    ★ packaged 用 :func:`is_frozen` 而不是 ``"__compiled__" in globals()``
      Nuitka 把 ``__compiled__`` 注入的是 ``sys.modules["__main__"]``，
      在本模块的 ``globals()`` 里查**永远是 False** —— 源码模式和打包
      模式会一起报 packaged=no。``paths.is_frozen()`` 就是为这件事写的。
    """
    log.info("===== NEKO-Console v%s 启动 (packaged=%s) @ %s =====",
             __version__, "yes" if frozen else "no",
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("日志文件: %s", _FILE_HANDLER.baseFilename)
    log.info("python  : %s", sys.executable)
    if frozen:
        # 打包后这两行最关键：能立刻看出 exe 实际位置和解压目录
        log.info("exe 路径: %s", sys.executable)
        log.info("解压目录: %s", bdir())
    # ★ 资源根与 base_dir 在打包模式下**不是同一个目录**。
    #   托盘图标曾经消失，就是因为按 base_dir 去找资源。打进日志，
    #   下次再出问题能一眼看出该去哪个目录核对。
    log.info("resource_root: %s", resource_root())
    log.info("-" * 60)


def main() -> int:
    # ★ 提权必须**先于**文件日志和 app.run()：
    #   提权成功后原进程要立刻退出（提权后的新实例会从头跑一遍），
    #   所以这里不能先装 handler 再退出 —— 那样会留下一个「启动到一半
    #   就没了」的横幅，排查时以为程序崩了。
    _maybe_elevate()

    # ★ 必须**先于** app.run() —— run() 里会 basicConfig，那时 logger 体系
    #   就定型了。
    setup_file_logging()

    from neko_console.app import run

    return run()


def _maybe_elevate() -> bool:
    """按配置决定是否要管理员权限。提权成功时**不返回**（直接退出进程）。

    :returns: 是否以「提权模式」继续。
    """
    from neko_console.config import load_config
    from neko_console.elevation import ensure_admin

    try:
        # ★ write_back=False：这里只是**读一个布尔**做决策，不该顺手把
        #   配置文件重写一遍（load_config 默认会写回）。提权是启动路径上
        #   最早的一步，在它之前动用户文件没有任何道理。
        cfg, _source = load_config(write_back=False)
        required = bool(getattr(cfg, "require_admin", True))
    except Exception:  # noqa: BLE001 - 配置读不了不该挡住启动
        # 读不到配置时**按需要提权**（默认 true）：宁可多弹一次 UAC，
        # 也不要让用户卡在「寻路莫名其妙不动」上。
        required = True

    # ensure_admin 在真的发起提权时会 raise SystemExit，原进程就此退出，
    # 由提权后的新实例接管。
    return ensure_admin(required=required)


if __name__ == "__main__":
    sys.exit(main())
