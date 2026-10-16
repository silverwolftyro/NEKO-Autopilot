"""引擎进程管理：spawn autopilot、优雅停、强杀、读快照与事件。

★ 为什么 stop() 走 commands.jsonl 而不是 terminate()
-----------------------------------------------
引擎的 ``MOVE_FORWARD`` 是 ``holds_keys`` 动作，最长 60 秒。直接
terminate 会让按键状态不明（角色可能一直往前走）。引擎自己实现了
``STOP`` 命令：它会先 ``release_all()`` 松键，再关截图器、关事件总线。
所以**优雅停必须让引擎自己走完流程**，terminate 只作为超时兜底。

★ 子进程为什么带 CREATE_NO_WINDOW
-------------------------------
不带的话会弹一个黑色控制台窗口，用户会以为程序出错了。
但这只在**真 exe 形态**下需要 —— Python 控制台跑引擎时用
``sys.executable -m ...`` 不加这个 flag（否则会复用当前控制台）。
"""

from __future__ import annotations

import json
import logging
# os：start() 里给子进程注入 PYTHONIOENCODING / PYTHONUTF8 时要
# os.environ.copy()。曾经漏掉它，点「启动引擎」直接 NameError 崩在 spawn 之前。
import os
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from ..config.schema import EngineConfig
from .events import EventReader
from .snapshot import SnapshotReader

logger = logging.getLogger(__name__)

#: 快照/事件轮询间隔（毫秒）。与引擎 200ms 写快照对齐。
POLL_INTERVAL_MS = 200

#: 引擎事件流超过这个行数就不再往 UI 里塞（防内存无限增长）
MAX_LINES = 500

#: 引擎 stderr 落盘的文件名（相对 runtime_dir）
STDERR_LOG_NAME = "engine_stderr.log"

#: stderr 日志超过这个大小就改名成 .1 重新开（覆盖，不留 .2）。
#: 只留一代：调试要的是"最近两次启动的报错"，留一堆反而不知道看哪个。
MAX_STDERR_LOG_BYTES = 1024 * 1024

#: stderr 里带这些词的行算"关键行"。**从底部往上找**第一个命中的。
#:
#: 为什么包含 Traceback/Exception：真正的异常类型在 traceback 的**最后**
#: 一行，而上面那些 File "...", line N 只是路径，匹配 Error 反而会抓到
#: 一个没用信息。
STDERR_KEY_MARKERS = ("Error", "Exception", "Traceback", "错误", "失败",
                      "✗", "Warning", "warning")

#: stderr 日志里每次启动的分隔标记。用来把"本次启动"从历史输出里切出来。
STDERR_BANNER = "===== engine start"

#: 拉起引擎后，等多久还没等到「新 run_id」就认为它没真正跑起来（秒）。
#:
#: ★ 为什么需要这个（2026-10-16 补，集成审计 F5）
#:   以前 ``Popen`` 一返回就 ``_emit_state("running")`` —— 那是**进程活着**
#:   就算数。但进程活着和引擎跑起来了是两件事：
#:
#:     1. Python 还在 import 一大堆东西（onnxruntime、PySide、cv2…）
#:     2. import 完了在等游戏窗口（``Capturer.WINDOW_WAIT_S = 30`` 秒）
#:
#:   这两种情况下 UI 都显示"运行中"，而首页卡片全是**上一次**留下的快照。
#:   用户点完启动，看着一个"在跑"的引擎，但游戏里什么也没发生，
#:   最要命的是**没有任何提示告诉他该去把游戏切到前台**。
#:
#:   真正的就绪信号是快照里 ``run_id`` 变了 —— 新 run 才有新快照。
#:   5 秒是个保守的门槛：import 再慢也该开始了；而真在等窗口时，
#:   我们宁可**继续说"启动中"**也不谎报"运行中"，因为等窗口本来就是
#:   正常路径，30 秒后才可能真的失败。
READY_TIMEOUT_S = 5.0


class EngineManager(QObject):
    """引擎进程的生命周期 + 状态通道。"""

    #: "stopped" | "starting" | "running" | "crashed"
    #:
    #: ★ 为什么多了 "starting"（2026-10-16，集成审计 F5）
    #:   "Popen 返回了" 不等于 "引擎跑起来了"。中间那段（import、等游戏
    #:   窗口）以前被谎报成 running，用户会对着一个静止的界面等。
    #:   详见 :data:`READY_TIMEOUT_S`。
    state_changed = Signal(str)
    #: 每读到一条事件就发一条（UI 拿去刷文本框）
    log_line = Signal(str)
    #: 快照更新（UI 拿去刷首页卡片）
    snapshot_updated = Signal(dict)

    def __init__(self, cfg: EngineConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self._proc: subprocess.Popen | None = None
        self._state = "stopped"
        rt = cfg.resolved_runtime_dir()
        self._snapshot = SnapshotReader(rt)
        self._events = EventReader(rt)
        self._exit_code: int | None = None

        #: 就绪确认用（2026-10-16）。启动**之前**快照里的 run_id ——
        #: 只有它变了才说明"新 run 真的起来了"。空串表示启动前没有快照。
        self._pre_run_id: str | None = None
        #: 超过这个 monotonic 时刻还没等到新 run_id，就提示一次 stderr。
        self._ready_deadline: float | None = None
        #: stderr 提示只发一次，别每 200ms 刷屏。
        self._ready_warned = False

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

    # ==================================================================
    # 公开 API
    # ==================================================================
    def runtime_dir(self) -> Path:
        return self.cfg.resolved_runtime_dir()

    def stderr_log_path(self) -> Path:
        """引擎 stderr 落盘位置：``<runtime_dir>/engine_stderr.log``。

        刻意和引擎自己的日志目录**分开**：那边是引擎主动写的结构化日志，
        这边是它"想说但没人听"的那些话（traceback / ImportError / 警告）。
        混在一起的话，读引擎日志的人会被大量无关的崩溃残骸干扰。
        """
        return self.runtime_dir() / STDERR_LOG_NAME

    def _rotate_stderr_log(self, path: Path) -> None:
        """超过上限就改名成 ``.log.1`` 覆盖。

        **只留一代**：调试真正需要的是"上一次启动也崩了吗、崩在哪"，
        留一串 ``.2 .3 .4`` 只会让人翻半天还看不对。
        任何失败都只告警 —— 轮转失败绝不能挡住引擎启动。
        """
        try:
            if not path.exists() or path.stat().st_size <= MAX_STDERR_LOG_BYTES:
                return
            old = path.with_name(path.name + ".1")
            if old.exists():
                old.unlink()
            path.replace(old)
            logger.info("引擎 stderr 日志已轮转：%s -> %s", path.name, old.name)
        except OSError as exc:
            logger.warning("引擎 stderr 日志轮转失败（忽略）：%s", exc)

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def state(self) -> str:
        return self._state

    def exit_code(self) -> int | None:
        return self._exit_code

    def path_problems(self) -> list[str]:
        """引擎路径问题列表（空 = 一切正常）。"""
        return self.cfg.validate_paths()

    # -- 启动 -----------------------------------------------------------
    def start(self, *, blocked_reason: str | None = None) -> bool:
        """拉起引擎。

        :param blocked_reason: 调用方传入的**外部阻塞原因**（非空即拒绝启动）。
            刻意做成参数而不是让本对象去查寻路 —— 与
            :meth:`PathfindChannel.start` 的 ``engine_running`` 参数对称：
            **channel 之间不互相持有，依赖关系由 app.py 组装**
            （见 ``app.py`` 里三个启动入口的检查）。

            ★ 为什么必须有这个参数
              ``PathfindChannel`` 拒绝在引擎运行时启动，但**反过来没人管**：
              引擎和寻路都用 ``Win32Backend`` 注入同一套键鼠，一方
              ``release_all()`` 会松开对方按住的键。护栏只做单向时，
              「先跑寻路 → 再点启动引擎」就会让角色乱走，而现象与病因
              （两个注入源）之间隔着十万八千里。
        """
        if blocked_reason:
            logger.warning("引擎启动被拒绝：%s", blocked_reason)
            self.log_line.emit(f"✗ 引擎启动被拒绝：{blocked_reason}")
            self._emit_state("stopped")
            return False

        if self.is_running():
            logger.info("引擎已在运行，忽略重复启动")
            return True

        problems = self.cfg.validate_paths()
        if problems:
            for p in problems:
                logger.error("引擎路径检查失败：%s", p)
            self._emit_state("stopped")
            return False

        try:
            rt = self.runtime_dir()
            rt.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error("创建 runtime 目录失败：%s", exc)
            self._emit_state("stopped")
            return False

        cmd = [str(self.cfg.resolved_python()), "-m",
               "autopilot.tools.run_loop", *self.cfg.args]
        cwd = str(self.cfg.resolved_path())
        logger.info("启动引擎：%s（cwd=%s）", " ".join(cmd), cwd)

        # ★★ stdout 保持 DEVNULL，stderr 改成落盘 ★★
        # ------------------------------------------------
        # stdout 不用管：引擎自己的 logging 会写进 run/ 目录，主控台要显示
        # 就去读那些文件，不该抢它的标准输出。
        #
        # 但 **stderr 之前也是 DEVNULL，那是错的**：Python 的
        # ImportError / ModuleNotFoundError / 语法错误 / 崩溃 traceback
        # 全都走 stderr。之前扔进 DEVNULL 的直接后果就是 ——
        # 引擎起不来时，主控台只显示"已崩溃"，用户**看不到任何原因**。
        # 而引擎秒退恰恰是这类错误。
        #
        # 也不能用 PIPE：建了管道没人读 = 引擎 write() 阻塞在 64KB 缓冲，
        # 进程活着却不 tick，状态灯还是绿的（症状更迷惑）。落盘没有这个问题。
        stderr_path = self.stderr_log_path()
        self._rotate_stderr_log(stderr_path)
        stderr_fh = None
        try:
            # ★ 必须用 'ab'（追加**二进制**），不能用 'wb'。
            #   'wb' 会**截断**—— 上一次崩在哪儿就永远看不见了，而"连续
            #   试几次看哪次崩"正是这个文件存在的意义。
            #
            #   顺带澄清：UnicodeEncodeError **不是这里造成的**。
            #   二进制模式下 Python 不做任何编码转换，写进去的就是字节；
            #   崩的是**子进程** —— 它自己的 stderr 用 GBK 编码，打不出
            #   '⚠' / '✗'。修法在下面的 env，不在 open()。
            stderr_fh = open(stderr_path, "ab")
            # 每次启动写一条分隔 + 命令行，秒退时才知道这段 traceback
            # 属于哪一次启动（连续试好几次时尤其重要）
            banner = (f"\n===== engine start {time.strftime('%Y-%m-%d %H:%M:%S')} "
                      f"=====\ncmd: {' '.join(cmd)}\ncwd: {cwd}\n")
            stderr_fh.write(banner.encode("utf-8"))
            stderr_fh.flush()
        except OSError as exc:
            # 落不了盘也不能挡住启动 —— 退回 DEVNULL，行为与改动前一致
            logger.error("打开引擎 stderr 日志失败（退回 DEVNULL）：%s", exc)
            if stderr_fh is not None:
                stderr_fh.close()
                stderr_fh = None

        # ★★ 强制子进程用 UTF-8 输出 ★★
        # ------------------------------------------------
        # 中文 Windows 的 locale 编码是 GBK，而引擎大量使用
        # '⚠' / '✗' / '·' 这类符号 —— **GBK 编码不了**，
        # 于是 print() 直接抛
        #   UnicodeEncodeError: 'gbk' codec can't encode character '\u26a0'
        # 引擎当场崩掉，而崩溃信息本身还是编码不了，于是什么都不留。
        #
        # 两个变量都要给，作用不同：
        #   PYTHONIOENCODING  改 stdout/stderr 的编码
        #   PYTHONUTF8=1      打开 UTF-8 模式，连默认的文件编码、
        #                       locale.getpreferredencoding() 一起换掉
        # 只给前者，引擎里那些 open() 不带 encoding 的代码仍会按 GBK 写文件。
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        logger.info("引擎 stderr 落盘：%s", stderr_path)
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - 参数是列表，无 shell
                cmd,
                cwd=cwd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=stderr_fh if stderr_fh is not None
                else subprocess.DEVNULL,
                creationflags=_creation_flags(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("引擎启动失败：%s", exc)
            self._proc = None
            self._emit_state("crashed")
            return False
        finally:
            # ★ 父进程这份句柄必须马上关掉。
            #   Popen 已经把句柄复制给了子进程，父进程留着既白占一个 fd，
            #   也会让下次 start() 的改名轮转在 Windows 上失败（文件被占用）。
            if stderr_fh is not None:
                try:
                    stderr_fh.close()
                except OSError:
                    pass

        # 换 run 了，旧游标作废
        self._events.reset()
        self._snapshot.invalidate()
        self._exit_code = None

        # ★ 就绪确认的基线（2026-10-16，集成审计 F5）
        #   必须**在 Popen 之前**记下当前 run_id：新 run 一旦开始写快照，
        #   run_id 就会变；先记就一定记到「旧的那个」，后记可能记到新的，
        #   那样永远等不到变化。
        self._pre_run_id = self._current_run_id()
        self._ready_deadline = time.monotonic() + READY_TIMEOUT_S
        self._ready_warned = False
        logger.info("引擎进程已拉起，等待新 run_id（基线=%r）",
                    self._pre_run_id)

        # 先报"启动中"而不是"运行中" —— 真就绪由 _poll 确认。
        self._emit_state("starting")
        self._timer.start()
        return True

    def _current_run_id(self) -> str | None:
        """当前快照里的 run_id；读不到返回 None。

        ★ 刻意走**独立的一次读**，不复用 :meth:`_snapshot.read`
          ``read()`` 带 stat 短路，返回的可能是一份几秒前的缓存。
          这里要的是"此刻磁盘上是什么"，宁多做一次 IO 也不能记错基线。
        """
        try:
            data = json.loads(self._snapshot.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        rid = data.get("run_id")
        return str(rid) if rid else None

    # -- 优雅停 ---------------------------------------------------------
    def stop(self, timeout_ms: int = 5000) -> bool:
        """优雅停：发 STOP 命令 -> 轮询等退出 -> 超时 terminate。

        :returns: True = 引擎已退出；False = 终止后仍在（基本不可能）
        """
        if not self.is_running():
            self._timer.stop()
            self._emit_state("stopped")
            return True

        logger.info("请求引擎优雅停止（timeout=%dms）", timeout_ms)
        ok = self._send_command("STOP")

        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            if not self.is_running():
                self._timer.stop()
                self._reap()
                self._emit_state("stopped")
                return ok
            time.sleep(0.1)

        logger.warning("引擎 %dms 内未退出，改用 terminate", timeout_ms)
        self._terminate()
        self._timer.stop()
        self._reap()
        self._emit_state("stopped")
        return False

    # -- 强杀 -----------------------------------------------------------
    def kill(self) -> bool:
        """直接杀。**按键可能卡住**，只在引擎无响应时用。"""
        if not self.is_running():
            self._timer.stop()
            self._emit_state("stopped")
            return True
        logger.warning("强制终止引擎进程")
        self._terminate()
        self._timer.stop()
        self._reap()
        self._emit_state("crashed")
        return True

    # ==================================================================
    # 内部
    # ==================================================================
    def _send_command(self, command: str) -> bool:
        """往 ``commands.jsonl`` 追写一条命令。

        ★ 必须用 ``"a"`` 模式（O_APPEND）：引擎也在同一个目录里读，
          追写保证不覆盖引擎的读取位置。
        """
        try:
            rt = self.runtime_dir()
            rt.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_version": "1.0",
                "cmd_id": f"cmd_{int(time.time() * 1000):06d}",
                "ts_ms": int(time.time() * 1000),
                "cmd": command,
                "args": {},
            }
            with (rt / "commands.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
            logger.info("已发送引擎命令：%s", command)
            return True
        except OSError as exc:
            logger.error("发送引擎命令失败：%s", exc)
            return False

    def _terminate(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            logger.error("terminate 无效，改用 kill")
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                logger.exception("强杀引擎失败")
        except Exception:  # noqa: BLE001
            logger.exception("终止引擎时异常")

    def _reap(self) -> None:
        """收尸：取退出码；若有 stdout 管道则关掉。

        用 DEVNULL 起进程后 ``proc.stdout`` 恒为 ``None``，这里的判断
        只是**防御性**保留 —— 万一将来有人改回 PIPE 而又忘了读，
        至少不会在这里因 ``None.close()`` 抛 AttributeError。
        """
        proc = self._proc
        if proc is None:
            return
        self._exit_code = proc.poll()
        stream = getattr(proc, "stdout", None)
        if stream is not None:
            try:
                stream.close()
            except Exception:  # noqa: BLE001 - 退出路径不该抛
                logger.debug("关闭引擎 stdout 失败（忽略）", exc_info=True)

    def _read_stderr_reason(self, max_bytes: int = 4096) -> str:
        """从 stderr 日志尾部提炼一句人话原因。提炼不出返回空串。

        ★ 为什么只看尾部
          文件是 ``'ab'`` 追加的，可能已经很大；而崩溃现场**一定在最后**。
          全量读进来只是白白占内存。

        ★ 为什么只认**本次启动**的输出
          追加模式下文件里混着上一次启动的报错。不按分隔线切开的话，
          用户会看到"上次崩的原因"被当成这次的原因 —— 那比不给更糟，
          会把人引到完全错误的方向上去。

        ★ 为什么从**底部往上**找关键行
          Python tracebox 的最后一行才是真正的异常
          （``WindowNotFoundError: 找不到标题包含 '原神' 的窗口``），
          从上往下找第一个带 ``Error`` 的行，很可能只抓到 import 那一行。
        """
        path = self.stderr_log_path()
        try:
            size = path.stat().st_size
            with open(path, "rb") as f:
                if size > max_bytes:
                    f.seek(size - max_bytes)
                raw = f.read()
        except OSError as exc:
            logger.debug("读引擎 stderr 失败（忽略）：%s", exc)
            return ""

        text = raw.decode("utf-8", errors="replace")
        lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]

        # 只保留最后一次启动之后的输出
        cut = -1
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].startswith(STDERR_BANNER):
                cut = i
                break
        lines = lines[cut + 1:] if cut >= 0 else lines
        if not lines:
            return ""

        for ln in reversed(lines):
            if any(m in ln for m in STDERR_KEY_MARKERS):
                return ln.strip()[:300]

        # 没匹配到关键字：给最后 5 行，总比什么都不说强
        return " / ".join(x.strip() for x in lines[-5:])[:300]

    def _poll(self) -> None:
        """定时器：检查进程 + 读快照 + 追事件。"""
        if self._proc is not None and self._proc.poll() is not None:
            # 引擎自己退了（可能 --duration 到点，也可能秒退）
            code = self._proc.returncode
            logger.info("引擎进程已退出，exit=%s", code)
            self._timer.stop()
            self._reap()
            # ★ 退出原因**必须让用户看到**。
            #   以前这里只 logger.info 一句，UI 上只剩一个"exit=2"，
            #   用户既不知道为什么，也没有下一步可做。现在把 stderr 里的
            #   关键行经 log_line 推到事件流里 —— 那是用户真正会去看的地方。
            reason = self._read_stderr_reason()
            if reason:
                self.log_line.emit(f"引擎退出（code={code}）：{reason}")
            elif code not in (0, None):
                self.log_line.emit(
                    f"引擎退出（code={code}）：stderr 里没有关键行，"
                    f"完整输出见 {self.stderr_log_path()}")
            self._emit_state("stopped")
            return

        snap = self._snapshot.read()
        if snap is not None:
            self.snapshot_updated.emit(snap)

        # ★ 就绪确认（2026-10-16，集成审计 F5）：run_id 变了才算真跑起来
        if self._state == "starting":
            self._check_ready(snap)

        for ev in self._events.poll():
            self.log_line.emit(format_event(ev))

    def is_ready(self) -> bool:
        """引擎是否已经**真正**跑起来（快照里出现过新 run_id）。

        ``state() == "running"`` 不再等价于它 —— 那是给 UI 的粗粒度状态，
        真正回答"引擎有没有产出新数据"的是这里。
        """
        return self._state == "running"

    def _check_ready(self, snap: dict | None) -> None:
        """``starting`` -> ``running`` 的判定（见 :data:`READY_TIMEOUT_S`）。"""
        now_run = str((snap or {}).get("run_id", "") or "") or None
        changed = now_run is not None and now_run != self._pre_run_id

        if changed:
            logger.info("引擎就绪：新 run_id=%s（基线=%r）",
                        now_run, self._pre_run_id)
            self._ready_deadline = None
            self._ready_warned = False
            self._emit_state("running")
            return

        # 没等到新 run_id。看是不是已经超了门槛 —— 超了就提示一次，
        # 说明"进程活着但没产出"，并把 stderr 里的原因摆出来。
        dl = self._ready_deadline
        if dl is None or time.monotonic() < dl or self._ready_warned:
            return
        self._ready_warned = True
        reason = self._read_stderr_reason()
        if reason:
            self.log_line.emit(f"引擎仍在启动（{READY_TIMEOUT_S:g}s 未产出新 run）：{reason}")
        else:
            self.log_line.emit(
                f"引擎仍在启动（{READY_TIMEOUT_S:g}s 未产出新 run）。"
                "若游戏未在前台，引擎会一直等窗口出现（最长 30 秒）；"
                f"完整输出见 {self.stderr_log_path()}")

    def _emit_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        self.state_changed.emit(state)


def _creation_flags() -> int:
    """Windows 下不给引擎弹黑框。非 Windows 返回 0。"""
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def format_event(ev: dict) -> str:
    """把一条事件 dict 排成一行可读文本。"""
    ts = ev.get("ts_ms", 0)
    clock = ""
    if isinstance(ts, (int, float)) and ts:
        clock = time.strftime("%H:%M:%S", time.localtime(ts / 1000))
    code = str(ev.get("code", "") or "?")
    sev = str(ev.get("severity", "INFO") or "INFO")
    msg = str(ev.get("message", "") or "")
    return f"[{clock}] {sev:<5} {code:<22} {msg}"[:300]


__all__ = [
    "MAX_LINES",
    "MAX_STDERR_LOG_BYTES",
    "POLL_INTERVAL_MS",
    "READY_TIMEOUT_S",
    "STDERR_BANNER",
    "STDERR_KEY_MARKERS",
    "STDERR_LOG_NAME",
    "EngineManager",
    "format_event",
]
