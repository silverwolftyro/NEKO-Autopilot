"""Autopilot B 轨桥接：主控台 ↔ N.E.K.O. 插件的双向文件通道。

★ 为什么要文件而不是管道 / socket
-------------------------------
主控台（NEKO-Console，PySide6）和插件（genshin_game_buddy，宿主进程内）
是两个**互不引用**的进程，甚至可能在不同的 Python 环境里。文件是唯一
不需要任何握手、进程死一个另一个照常工作的通道 —— 这也符合 A 轨
（autopilot/run/）已经在用的模式。

方向：
    主控台 -> 插件   ``console_bridge.json``   :class:`BridgeWriter`
    插件 -> 主控台   ``console_commands.jsonl`` :class:`CommandPoller`

★ 命令格式：两边必须一致
-------------------------
本模块是**唯一定义处**。插件侧的 ``autopilot_bridge.send_command()`` 按
同样格式追加一行 JSONL；格式若要改，**两边同时改**。

    {"schema_version": 1, "cmd_id": "...", "ts_ms": 1727500000000,
     "cmd": "START_ENGINE", "args": {}}

用 JSONL 而不是单个 JSON：多个命令天然排队，不会后写覆盖先写。
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: 状态文件（主控台 -> 插件）
BRIDGE_FILE_NAME = "console_bridge.json"
#: 状态文件写刷新间隔。200ms：人眼察觉不到延迟，又不至于把 SSD 写满。
BRIDGE_WRITE_INTERVAL_MS = 200
#: 状态文件结构版本。插件侧遇到不认识的版本应视为"读不了"，而不是猜。
BRIDGE_SCHEMA_VERSION = 1

#: 命令文件（插件 -> 主控台）
COMMANDS_FILE_NAME = "console_commands.jsonl"
COMMANDS_SCHEMA_VERSION = 1

#: 主控台认为"插件还连着"的最大间隔。插件侧用同一个值判定主控台死活。
CONSOLE_ALIVE_TIMEOUT_MS = 3000

#: 命令全集。插件侧应只允许这几个 —— 收到不认识的命令必须拒绝执行，
#: 不能"尽力猜一个意思"。命令名一旦发出去就是对外契约。
VALID_COMMANDS = (
    "START_ENGINE",
    "STOP_ENGINE",
    # 悬浮卡片上的 ↻ 按钮写这条。**必须加进来**，
    # 否则 CommandPoller 会以"未注册命令"拒掉它 —— 那是有意的保护
    # （不能猜命令的意思），所以加命令必须同步改这一处。
    "RESTART_ENGINE",
    "START_OVERLAY",
    "STOP_OVERLAY",
    "QUIT_CONSOLE",
)

#: 命令有效期（毫秒）。
#:
#: ★ 为什么必须有过期这回事
#:   ``console_commands.jsonl`` 同样是"追加写、消费者读"且**没人清空**。
#:   插件发了一条 ``START_ENGINE``，而主控台当时不在运行 —— 那条命令就一直
#:   躺在文件里。等用户下次开主控台，它会被执行：用户明明什么都没点，
#:   引擎却自己起来了。
#:   引擎侧 ``run_loop.py`` 用的是同一个思路（``COMMAND_TTL_S = 30``）。
#:   两边阈值**应该一致**，否则会出现"一边认为过期、另一边认为有效"的窗口。
COMMAND_TTL_MS = 30_000


def purge_expired_commands(path: Path, ttl_ms: int = COMMAND_TTL_MS,
                           now_ms: int | None = None) -> int:
    """把命令文件里过期和坏掉的行清掉，**保留新鲜的**。

    :returns: 保留了多少行。

    ★ 为什么要保留新鲜的，而不是整个清空
      主控台启动的那一瞬间，插件可能刚发了一条 ``START_ENGINE`` ——
      那是**给这次会话的**，整个清空等于把用户的操作丢掉。

    ★ 用**重写**而不是 truncate
      truncate 会把"还没消费的新命令"一起抹掉。而这条命令通道是
      追加式的文件协议，重写是唯一能"只删一部分"的做法。
      先写 .tmp 再 os.replace，避免并发写把文件截成半行。

    ★ 顺带清掉解析不了的行
      半截 JSON（写一半被杀进程）会永远卡在那里，每轮 poll 都报一次警告。
      这里一并清掉。
    """
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    try:
        if not path.exists():
            return 0
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.debug("清理命令文件失败（读）：%s", exc)
        return 0

    kept: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue                        # 坏行：清掉
        if not isinstance(data, dict):
            continue
        ts = data.get("ts_ms")
        if isinstance(ts, (int, float)) and ts < now - ttl_ms:
            continue                        # 过期：清掉
        kept.append(line)

    try:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("".join(x + "\n" for x in kept), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.debug("清理命令文件失败（写）：%s", exc)
        return len(kept)
    if kept:
        logger.info("命令文件清理：保留 %d 行（其余为过期或损坏）", len(kept))
    return len(kept)


def _pid_of(mgr: Any) -> int | None:
    """取子进程 manager 的 pid；没跑着返回 None。

    ★ 为什么读私有 ``_proc``
      ``EngineManager`` / ``OverlayManager`` 都没有公开 pid 取值方法，而
      本轮任务不允许改那两个文件。与其在 app.py 里散落私有访问，
      把这一处集中在这里 —— 将来给 manager 加 ``pid()`` 时，
      **只需要改这一个函数**。
    """
    proc = getattr(mgr, "_proc", None)
    if proc is None:
        return None
    pid = getattr(proc, "pid", None)
    return pid if isinstance(pid, int) else None


class BridgeWriter:
    """把主控台状态写成插件可读的文件。"""

    def __init__(self, base_dir: Path) -> None:
        self._dir = Path(base_dir) / "runtime"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file = self._dir / BRIDGE_FILE_NAME

    def path(self) -> Path:
        return self._file

    def write(self, payload: dict[str, Any]) -> None:
        """原子写。**失败静默** —— 这是 5Hz 的后台心跳，绝不能因为
        磁盘抖一下就把主控台搞崩；真正需要可靠的是命令通道，不是状态。"""
        try:
            tmp = self._file.with_name(self._file.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False),
                           encoding="utf-8")
            os.replace(tmp, self._file)
        except (OSError, TypeError, ValueError) as exc:
            logger.debug("写桥接状态失败（忽略）：%s", exc)


class CommandPoller:
    """从 JSONL 命令文件里增量读命令。

    ★ 为什么要自己处理"半行"
      插件那边是"追加一行"，主控台这边可能在追加动作的**中途**去读 ——
      拿到的是半截 JSON。``json.loads`` 会直接炸，而炸了如果把 offset 往前
      推，这一条命令就**永久丢失**；不推的话下一轮又会读到同一段死循环。

      所以这里维护一个残留缓冲：只有读到换行才认为一条命令完整。
    """

    def __init__(self, base_dir: Path,
                 handler: Callable[[str, dict[str, Any]], None]) -> None:
        self._file = Path(base_dir) / "runtime" / COMMANDS_FILE_NAME
        self._handler = handler
        self._offset = 0
        self._partial = ""
        self._seen_ids: set[str] = set()
        # ★ 主控台启动时清理一次。
        #   只在构造时做（不是每轮 poll）—— 每轮重写文件既没必要也会
        #   和插件的追加写打架。构造即"启动"，语义正好对上。
        purge_expired_commands(self._file)

    def path(self) -> Path:
        return self._file

    def reset(self) -> None:
        """忘掉已读位置（退出时用，免得下次启动重放旧命令）。"""
        self._offset = 0
        self._partial = ""

    def poll(self) -> list[dict[str, Any]]:
        """读一轮，返回本次真正派发出去的命令（调试/测试用）。"""
        fired: list[dict[str, Any]] = []
        try:
            if not self._file.exists():
                return fired
            size = self._file.stat().st_size
        except OSError:
            return fired

        # 文件被截断/重建（插件侧清了目录）-> 从头读，但重置残留缓冲，
        # 否则上一条命令的尾巴会粘到新文件的第一行前面。
        if size < self._offset:
            self._offset = 0
            self._partial = ""

        try:
            with open(self._file, "r", encoding="utf-8", newline="") as f:
                f.seek(self._offset)
                chunk = f.read()
                self._offset = f.tell()
        except (OSError, UnicodeDecodeError) as exc:
            logger.debug("读命令文件失败（忽略）：%s", exc)
            return fired

        self._partial += chunk
        *lines, self._partial = self._partial.split("\n")
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            cmd = self._parse(line)
            if cmd is not None:
                fired.append(cmd)
                self._dispatch(cmd)
        return fired

    def _parse(self, line: str) -> dict[str, Any] | None:
        try:
            data = json.loads(line)
        except ValueError:
            logger.warning("命令行不是合法 JSON（已跳过）：%.120s", line)
            return None
        if not isinstance(data, dict):
            return None
        cmd = data.get("cmd")
        if not isinstance(cmd, str):
            return None
        # ★ 不认识就不执行。命令是对外契约，宁可漏一条也不能猜错意思 ——
        #   猜错的后果是"用户点停止、主控台把引擎杀了"这类灾难。
        if cmd not in VALID_COMMANDS:
            logger.warning("收到未注册命令 %r，已拒绝", cmd)
            return None
        # ★ 过期过滤**内建**在这里，而不是靠调用方先清理。
        #   调用方（app.py）新建一个 CommandPoller 就自动获得这个保护，
        #   不用记得在启动钩子里手动调一次 —— 那种"记得"迟早会忘。
        ts = data.get("ts_ms")
        if isinstance(ts, (int, float)) and \
                ts < time.time() * 1000 - COMMAND_TTL_MS:
            logger.info("命令 %s 已过期（%.1fs 前），忽略",
                        cmd, (time.time() * 1000 - ts) / 1000.0)
            return None
        # cmd_id 去重：插件重发 / 文件被重放时不会执行两遍
        cid = data.get("cmd_id")
        if isinstance(cid, str) and cid:
            if cid in self._seen_ids:
                logger.debug("命令 %s 重复，已忽略", cid)
                return None
            if len(self._seen_ids) > 512:
                self._seen_ids.clear()      # 防无限增长，丢了就当没发过
            self._seen_ids.add(cid)
        args = data.get("args")
        return {"cmd": cmd, "args": args if isinstance(args, dict) else {}}

    def _dispatch(self, cmd: dict[str, Any]) -> None:
        try:
            self._handler(str(cmd["cmd"]), dict(cmd.get("args") or {}))
        except Exception:  # noqa: BLE001 - 一条命令炸了不能带倒整个轮询
            logger.exception("执行命令 %s 时异常", cmd.get("cmd"))


def build_command_line(cmd: str, args: dict[str, Any] | None = None) -> str:
    """拼一行命令 JSON。**插件侧必须产出完全一致的形状。**"""
    return json.dumps({
        "schema_version": COMMANDS_SCHEMA_VERSION,
        "cmd_id": uuid.uuid4().hex,
        "ts_ms": int(time.time() * 1000),
        "cmd": cmd,
        "args": args or {},
    }, ensure_ascii=False)


__all__ = [
    "BRIDGE_FILE_NAME",
    "BRIDGE_SCHEMA_VERSION",
    "BRIDGE_WRITE_INTERVAL_MS",
    "COMMANDS_FILE_NAME",
    "COMMANDS_SCHEMA_VERSION",
    "COMMAND_TTL_MS",
    "CONSOLE_ALIVE_TIMEOUT_MS",
    "VALID_COMMANDS",
    "BridgeWriter",
    "CommandPoller",
    "build_command_line",
    "purge_expired_commands",
]
