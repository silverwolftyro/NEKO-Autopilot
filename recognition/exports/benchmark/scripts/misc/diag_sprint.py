"""Shift+W 跑步诊断（真机）—— 回答「W 按住了，为什么角色在走路？」

★ 真机症状
-----------
60s / 4.7fps：``W 按住 281/282 帧（100%）``，但**等效速度 1.40 m/s**
= 原神的**走路**速度，跑步应该是 5~6 m/s。也就是说 Shift 那一半
大概率没生效。

★ 为什么不走「打开记事本打几个 w」那种办法
--------------------------------------------
那个办法验证不了真正的问题：``Shift+W`` 没生效时，游戏收到的**仍然
是有效的 W**（走路）。所以记事本里照样会打出 ``wwww``，你看到
「有反应」就会误判成「按键没问题」。

真正要回答的是三个**可客观测量**的问题：

  Q1  系统层面，Shift 到底有没有被按住？
     ``GetAsyncKeyState`` 读的是**物理/输入层**状态，不受 UIPI 管辖
     （UIPI 只挡「注入到更高权限窗口」，不挡「查询全局键态」）。
  Q2  SendInput 报告注入成功吗？发了几条？
     ``InputSimulator._send`` 本身会在 ``sent != want`` 时抛错，
     这里把 history 打出来核对。
  Q3  **游戏把这次输入当成了什么？** ← 只有人能回答
     这是本脚本存在的**根本原因**。系统层读数全是绿的，角色还是
     只走路 —— 说明游戏在**输入到达之后**自己做的判断有问题。
     ★ 但这问**不能**在脚本里问：问你要脚本在前台，看角色要游戏在前台，
       Windows 前台窗口只有一个，两个条件**物理上互斥**（详见
       「为什么是分次模式」）。所以观察由你**事后**用 ``--test-note``
       补录进日志。
  Q4  换几种注入时序、换几种**事件类型**，游戏行为有没有差别？

★ 6 种策略分两类，别混为一谈
------------------------------
A~D 换的是**时序**，但全部是 **VK 事件**（``wScan=0``、不带
``KEYEVENTF_SCANCODE``）—— 游戏收到的始终是「程序合成的虚拟键码
事件」。若游戏按「真实硬件 / 合成输入」区分，调时序是**调不出来**的。

E/F 换的是**事件类型本身**：

  E. 纯扫描码  ``wVk=0`` + ``KEYEVENTF_SCANCODE``，扫描码由
     ``MapVirtualKeyEx(VK, MAPVK_VK_TO_EXSCANCODE)`` 取
     （W=0x11、LSHIFT=0x2A）—— 走真实硬件那条路。
  F. 双发      同一次按键先发 VK 再发扫描码，两条路径都让它收到。

★ 为什么是「分次模式」而不是「交互模式」（2026-10-04 改）
----------------------------------------------------------
第一版加过 ``--interactive``：每条策略跑完停下，提示你按 r/w/d/n。
**这个设计不可行** —— 它要求同时满足两个物理上互斥的条件：

  · 要看见 PowerShell 的提示  ->  PowerShell 必须在前台
  · 要让按键进游戏 / 看见角色 ->  游戏必须在前台

Windows 的前台窗口**只有一个**。切到 PowerShell 打字 = 游戏失焦 =
按键全进 PowerShell；留在游戏里 = 根本看不见提示。所以 ``--interactive``
永远只会得到「没反应」，而那恰恰是本脚本要诊断的现象本身 ——
**它制造了一个和 bug 无法区分的现象**，比没有还糟。

改成：**一次跑一条，跑完立刻退出**，你切回来在命令行里用
``--test-note`` 把观察补录进去。6 次跑完 grep 一下日志就齐了。

★ 安全
------
本脚本**会真的按住键**。跑之前把游戏切到前台。它只按 Shift 和 W，
不点任何按钮、不用鼠标，结束时无条件释放全部键。

用法（管理员 PowerShell，UIPI 隔离要求）::

    chcp 65001

    # 第一步：全跑一遍，看系统层读数（可选）
    python diag_sprint.py --live --delay 8 --log-file "$env:TEMP\\sprint.log"

    # 第二步：分 6 次跑，一次一条，每次把观察补录进去
    #   ★ 跑之前先删一次旧日志，之后 6 次都带 --log-append
    Remove-Item "$env:TEMP\\sprint.log" -ErrorAction SilentlyContinue
    python diag_sprint.py --live --only A --test-note "走路" --log-file "$env:TEMP\\sprint.log" --log-append
    python diag_sprint.py --live --only B --test-note "走路" --log-file "$env:TEMP\\sprint.log" --log-append
    ...
    python diag_sprint.py --live --only F --test-note "跑步" --log-file "$env:TEMP\\sprint.log" --log-append

    # 第三步：汇总（6 条 TEST-NOTE 一眼看完）
    Select-String -Path "$env:TEMP\\sprint.log" -Pattern '\\[TEST-NOTE\\]'

    python diag_sprint.py                     # 不加 --live 只做静态检查

★ ``--log-append`` 干什么（2026-10-04 补）
----------------------------------------
``--log-file`` 默认是 ``mode="w"`` **覆盖**。分 6 次跑同一个文件时，
跑完 F 的时候 A~E 已经没了 —— 而那 6 条恰恰是这次诊断的全部价值。
加 ``--log-append`` 走 ``mode="a"``，文件不存在时正常新建。

代价是**你自己负责清空**：脚本不知道你是不是想接着上一轮攒，
所以第一次跑之前手动 ``Remove-Item`` 一次。
脚本在「单跑 + 用了 ``--log-file`` 但没加 ``--log-append``」时会提醒你。

★ ``--only`` 强制 ``--delay >= 5``
----------------------------------
分次模式下你得「跑一次 -> 切回 PowerShell 报告 -> 再跑下一次」，
留不够时间就有一半按键进了 PowerShell，结论直接作废。脚本在**任何
注入之前**就拦下（return 2），不靠人记得。

★ ``--test-note`` 强烈建议加上（2026-10-04 改）
----------------------------------------------
不加的话你只会看到「系统层 Shift 按住了」—— 那是**已经知道**的结论
（A/B/D 早就读到了），看不到真正要答的问题。``--test-note`` 把你的
观察（跑/走/闪避）补录成 ``[TEST-NOTE] A: 走路`` 这样的行，
一次 grep 就能把 6 次结果汇总起来。

★ ``--log-file`` 强烈建议加上（2026-10-04 补）
------------------------------------------------
``| Tee-Object`` 写文件用的编码取决于 PowerShell 版本（5.1 = ANSI/GBK，
7+ = utf8NoBOM），读取时又可能按另一套编码解 —— 结果就是
「控制台看着正常、文件里全是乱码」。和 e2e_pathfind.py 一样，
编码在**写的那一端**就定死，shell 不参与：文件固定 UTF-8 / LF / 行缓冲。

★ ``--delay`` 是**必须的**（2026-10-04 补）
----------------------------------------
第一版漏了这个，于是脚本**立刻**开始按 Shift+W —— 而人还在
PowerShell 里，**所有按键全进了 PowerShell**，诊断结论直接作废。
``e2e_pathfind.py`` / ``e2e_dryrun.py`` 都有 ``[DELAY]``，这里对齐。
默认 8 秒与它们一致；倒计时结束后还有一道**前台看门狗**：
前台不是游戏窗口就直接退出（exit 2），不拿无效数据糊弄。
"""
from __future__ import annotations

# ── 发布包路径解析 ─────────────────────────────────────────────────
# ★ 这段由发布构建注入，替代原先散落各处的本机绝对路径。
#   优先级：环境变量 > 从本文件向上找仓库根 > 相对当前目录（响亮失败）。
#   刻意用 ``_pl`` / ``_os`` 私有别名：这些脚本五花八门，
#   用 ``Path`` / ``os`` 直接定义会和它们各自的 import 打架。
import os as _os
import pathlib as _pl


def _neko_repo_root() -> "_pl.Path":
    for _p in _pl.Path(__file__).resolve().parents:
        if (_p / "recognition" / "configs" / "rois.toml").is_file():
            return _p
    return _pl.Path(__file__).resolve().parents[0]


_REPO_ROOT = _neko_repo_root()
RECOG_ROOT = _pl.Path(_os.environ.get("NEKO_RECOGNITION_ROOT", "").strip()
                      or (_REPO_ROOT / "recognition"))
PLUGIN_DIR = _pl.Path(_os.environ.get("NEKO_PLUGIN_DIR", "").strip()
                      or (_REPO_ROOT / "plugin" / "plugins" / "genshin_game_buddy"))
CONSOLE_DIR = _pl.Path(_os.environ.get("NEKO_CONSOLE_DIR", "").strip()
                       or (_REPO_ROOT / "console"))
import os

import argparse
import atexit
import ctypes
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:                                   # pragma: no cover
    pass

PLUGIN_ROOT = Path(
    PLUGIN_DIR)
sys.path.insert(0, str(PLUGIN_ROOT))

from autopilot.actuator.simulator import (  # noqa: E402
    KEY_MODE_BOTH, KEY_MODE_SCANCODE, KEY_MODE_VK,
    VK, InputSimulator,
)

u = ctypes.windll.user32


# ------------------------------------------------------------ 日志接管
# ★ 源自 e2e_pathfind.py 的 --log-file 实现（2026-10-04 搬过来）。
#   ★ 2026-10-04 起**有意分叉**：本脚本多了 ``append`` 参数（分次模式
#     要往同一个文件攒 6 条 TEST-NOTE），e2e_pathfind.py 那边**没有**。
#     编码/换行/缓冲/atexit 恢复这些**实质行为两边仍然一致**，
#     分叉的只有这一个参数 —— 对照两份日志仍然可信。
class _Tee:
    """把输出同时写到原 stream 和一个**显式指定编码**的文件。

    ★ 为什么不让 PowerShell 去做这件事
    ----------------------------------
    ``| Tee-Object -FilePath run.log`` 写文件用的编码**取决于 PowerShell
    版本**：5.1 默认 ANSI（中文系统 = GBK），7+ 默认 utf8NoBOM。而读取时
    又可能按另一套编码解 —— 于是「控制台看着正常、文件里全是乱码」。
    编码必须在**写的那一端**就定死，shell 不参与。

    ★ 用法
    ----
    ``--log-file run.log``。文件固定 UTF-8 / LF（``newline="\\n"``），
    不管宿主是什么 Windows 区域设置。
    """

    def __init__(self, stream, fh) -> None:
        self._s = stream
        self._f = fh

    def write(self, data):
        self._s.write(data)
        self._f.write(data)
        return len(data)

    def flush(self):
        self._s.flush()
        self._f.flush()

    def isatty(self):
        return getattr(self._s, "isatty", lambda: False)()

    def fileno(self):
        return self._s.fileno()

    def reconfigure(self, **kw):
        if hasattr(self._s, "reconfigure"):
            self._s.reconfigure(**kw)

    @property
    def encoding(self):
        return getattr(self._s, "encoding", "utf-8")


def _open_log(path: str, append: bool = False) -> str:
    """开日志文件并接管 stdout/stderr。返回**绝对路径**。

    ``append=False``（默认，历史行为）= ``mode="w"`` 覆盖；
    ``append=True`` = ``mode="a"`` 追加，文件不存在时**正常新建**。

    ★ 追加模式解决什么（2026-10-04 补）
    -------------------------------
    分次模式下要跑 6 次（``--only A`` … ``--only F``）并把 6 条
    ``[TEST-NOTE]`` 汇总到**同一个**文件。全用默认的覆盖模式的话，
    跑完 F 时 A~E 已经被抹掉了 —— 而那恰恰是这次诊断的全部价值。
    """
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    # ★ encoding 显式 utf-8、newline 显式 \n —— 这是绕开 shell 编码的全部意义
    # ★ buffering=1 = **行缓冲**：每 print 一行立刻落盘。诊断日志最怕的就是
    #   脚本崩了 / 被 Ctrl+C / 被任务管理器杀掉，缓冲里的日志全丢。
    # ★ mode="a" 时 Python 不会在文件末尾补换行，上一轮最后一行若
    #   没以 \n 收尾（被强杀），本轮第一行会**粘在它后面**。
    #   所以追加模式先补一个换行再接管 —— 否则日志里会出现
    #   「...已释放全部按键。[TEST-NOTE] A: 走路」这种没法 grep 的行。
    mode = "a" if append else "w"
    existed = p.exists()
    fh = p.open(mode, encoding="utf-8", newline="\n", buffering=1)
    if append and existed:
        try:
            with p.open("rb") as probe:
                if probe.seek(0, 2) > 0:      # 非空
                    probe.seek(-1, 2)
                    if probe.read(1) != b"\n":
                        fh.write("\n")
        except OSError:                      # 补不上就补不上，不该因此崩掉
            pass
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = _Tee(old_out, fh)
    sys.stderr = _Tee(old_err, fh)

    def _restore() -> None:
        sys.stdout, sys.stderr = old_out, old_err
        try:
            fh.flush()
            fh.close()
        except Exception:  # noqa: BLE001
            pass

    atexit.register(_restore)      # 覆盖所有退出路径（含 SystemExit）
    return str(p)


def hr(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def foreground_title() -> tuple[str, int]:
    """当前前台窗口的标题和 hwnd。"""
    fg = u.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(256)
    u.GetWindowTextW(fg, buf, 256)
    return buf.value, fg


#: 认得出「是游戏」的前台窗口标题关键字（小地图诊断只关心焦点对不对，
#: 不关心具体是哪个游戏进程）。
GAME_HINTS = ("原神", "Genshin", "YuanShen")

#: ★ ``--only`` 单跑时的 --delay 下限（2026-10-04）。
#: 分次模式下你得「跑一次 -> 切回 PowerShell 报告 -> 再跑下一次」，
#: 留不够时间就有一半按键进了 PowerShell，结论直接作废。
ONLY_MIN_DELAY_S = 5.0


def looks_like_game(title: str) -> bool:
    return any(h.lower() in title.lower() for h in GAME_HINTS)


def wait_foreground(delay: float) -> None:
    """★ 延迟启动 + 前台看门狗（2026-10-04 补，对齐 e2e_* 的约定）。

    没有这一步的话，脚本会**立刻**开始按 Shift+W —— 而用户还在
    PowerShell 里，**所有按键全进了 PowerShell**，诊断结论直接作废。
    ``e2e_pathfind.py`` / ``e2e_dryrun.py`` 都有 ``[DELAY]``，
    这个新脚本一开始漏了。
    """
    if delay <= 0:
        return
    hr("S0.5 · 延迟启动")
    _t, _h = foreground_title()
    print(f"\n  [DELAY] {delay:g}s 后开始 —— ★ 现在切到原神，"
          f"**保持它在前台**！")
    if not looks_like_game(_t):
        print(f"    （当前前台是 {_t!r}，不是游戏 —— 正是要等你切过去的）")
    end = time.monotonic() + delay
    while time.monotonic() < end:
        print(f"    剩余 {end - time.monotonic():5.1f} s\r",
              end="", flush=True)
        time.sleep(0.1)
    print()


# ------------------------------------------------------------ 客观读键态
def key_state(vk: int) -> tuple[bool, bool]:
    """返回 ``(down, toggled)`` —— GetAsyncKeyState 的两个有效位。

    高位 = 当前是否按下；低位 = 自上次调用以来是否按过（用于验证
    「按下沿」真的被系统看到了，而不只是我们自称按了）。
    """
    s = u.GetAsyncKeyState(vk)
    return bool(s & 0x8000), bool(s & 0x0001)


def snapshot(tag: str) -> dict:
    sd, st_ = key_state(int(VK.SHIFT))
    wd, wt = key_state(int(VK.W))
    print(f"    {tag:<26} Shift={'↓按下' if sd else '↑抬起'}"
          f"{'(有按下沿)' if st_ else '          '}"
          f"   W={'↓按下' if wd else '↑抬起'}{'(有按下沿)' if wt else ''}")
    return {"shift_down": sd, "shift_edge": st_,
            "w_down": wd, "w_edge": wt}


# ------------------------------------------------------------ 注入策略
def strat_combo_then_hold(sim) -> None:
    """**当前 e2e_pathfind.py 的做法**（先复现问题）。"""
    sim.key_combo([VK.SHIFT], VK.W)
    time.sleep(0.02)
    sim.key_down(VK.SHIFT)
    sim.key_down(VK.W)


def strat_shift_first(sim) -> None:
    """先按住 Shift，等它被游戏消化，再按 W（用户方案 a）。"""
    sim.key_down(VK.SHIFT)
    time.sleep(0.10)
    sim.key_down(VK.W)


def strat_shift_combo_hold(sim) -> None:
    """先 Shift+W 原子敲一次建立组合，再补上持续按住。"""
    sim.key_down(VK.SHIFT)
    time.sleep(0.05)
    sim.key_combo([VK.SHIFT], VK.W)
    sim.key_down(VK.SHIFT)
    sim.key_down(VK.W)


def strat_w_then_shift(sim) -> None:
    """反序：先 W 再 Shift（验证游戏是否只认某一种到达顺序）。"""
    sim.key_down(VK.W)
    time.sleep(0.05)
    sim.key_down(VK.SHIFT)


#: 每个策略 = (显示名, 函数, key_mode, 这条在**验什么**)
#:
#: ★ 为什么 A~D 换的是「时序」，E/F 换的是「**事件类型**」
#: ------------------------------------------------------
#: A~D 全都是 **VK 事件**（wScan=0、不带 KEYEVENTF_SCANCODE），
#: 无论怎么调时序，游戏收到的都还是「程序合成的虚拟键码事件」。
#: 若游戏按「真实硬件 / 合成输入」区分输入，调时序是**调不出来的**。
#: E/F 换的是事件类型本身：E 只发扫描码，F 两条都发。
STRATS = [
    ("A. combo+hold（当前做法）", strat_combo_then_hold, KEY_MODE_VK,
     "复现问题：现状就是只走不跑"),
    ("B. Shift 先落 100ms 再 W", strat_shift_first, KEY_MODE_VK,
     "换时序：Shift 早到 100ms"),
    ("C. Shift 落 + combo + 持续按住", strat_shift_combo_hold, KEY_MODE_VK,
     "换时序：先敲一次建立组合再按住"),
    ("D. W 先落再 Shift（反序）", strat_w_then_shift, KEY_MODE_VK,
     "换时序：反序到达"),
    ("E. 纯扫描码（SCANCODE）", strat_combo_then_hold, KEY_MODE_SCANCODE,
     "★换事件类型：wVk=0 + KEYEVENTF_SCANCODE，走硬件那条路"),
    ("F. VK + 扫描码 双发", strat_combo_then_hold, KEY_MODE_BOTH,
     "★换事件类型：两条都发，看游戏吃哪条"),
]

# ------------------------------------------------------------ 分次报告
def _emit_test_note(note: str | None, letter: str) -> str | None:
    """把用户给的观察写成一条 ``[TEST-NOTE]`` 记录。返回该行（无则 None）。

    ★ 为什么不设计成「脚本停下来问」
    ------------------------------
    问用户必须脚本在前台且游戏也看得见，而 Windows 的**前台窗口只能
    有一个**：切到 PowerShell 打字 = 游戏失焦 = 按键进不去游戏；
    留在游戏里 = 看不见 PowerShell 的提示。两个条件**物理上互斥**。
    所以观察只能由用户**事后**补录，这条记录就是补录的载体。

    行首固定 ``[TEST-NOTE]``，是为了让汇总能一条 grep 捞出全部 6 次：
    ``Select-String -Path sprint.log -Pattern '\\[TEST-NOTE\\]'``
    """
    if note is None:
        return None
    clean = " ".join(str(note).split())      # 折行/多余空白压成单行
    if not clean:
        return None
    line = f"[TEST-NOTE] {letter}: {clean}"
    print(line)
    return line


def _next_letter(letter: str) -> str:
    """下一条策略的字母，A->B->...->F->A。F 之后回到 A。"""
    order = "ABCDEF"
    try:
        i = order.index(letter.upper())
    except ValueError:
        return "A"
    return order[(i + 1) % len(order)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="真的按住键（不加则只做静态检查）")
    ap.add_argument("--delay", type=float, default=8.0,
                    help="开始注入前等多少秒让你切到游戏（默认 8，与 e2e_* 一致）")
    ap.add_argument("--hold", type=float, default=1.5,
                    help="每种策略按住多少秒（默认 1.5）")
    ap.add_argument("--log-file", default=None,
                    help="把 stdout+stderr 另存为 UTF-8/LF 文件（绕开 shell 编码）")
    ap.add_argument("--log-append", action="store_true",
                    help="往 --log-file **追加**而不是覆盖（分 6 次跑同一个文件时必加）")
    ap.add_argument("--only", default=None,
                    help="只跑指定策略，如 --only E（默认全跑 A~F）")
    ap.add_argument("--test-note", default=None, metavar="备注",
                    help="★ 把你的观察写进日志，如 --test-note 走路。"
                         "跑完会多一行 [TEST-NOTE] A: 走路")
    args = ap.parse_args()

    # ★ --only 在**任何**环境检查之前就解析并校验（2026-10-04）。
    #   放在前台看门狗后面的话，非法值会先被看门狗拦下（前台不是游戏
    #   就 return 2），用户看到的仍是「前台不是游戏窗口」而不是
    #   「--only 写错了」—— 报错信息指错方向，排查成本白付。
    strats = STRATS
    if args.only:
        want = args.only.strip().upper()
        strats = [s for s in STRATS if s[0][:1].upper() == want]
        if not strats:
            print(f"  !! --only {args.only!r} 没匹配到任何策略"
                  f"（可选 A~F）", file=sys.stderr)
            return 2

    # ★ --only 单跑时**强制**留够切窗口的时间（2026-10-04）。
    #   分次模式下你必须「跑一次 -> 切回 PowerShell 报告 -> 再跑下一次」，
    #   --delay 留小了会有一半按键进 PowerShell，测出来的结论直接作废。
    #   这里在**任何注入之前**就拦下，不靠人记得。
    if args.live and args.only and args.delay < ONLY_MIN_DELAY_S:
        print(f"  !! --only 模式下 --delay 必须 >= {ONLY_MIN_DELAY_S:g}s"
              f"（当前 {args.delay:g}s）。", file=sys.stderr)
        print(f"     你要切到原神、跑完再切回 PowerShell 报告，留够时间。",
              file=sys.stderr)
        print(f"     例：--live --only A --delay {ONLY_MIN_DELAY_S:g}",
              file=sys.stderr)
        return 2

    # ★ 必须在**任何输出之前**接管，否则开头的横幅已经乱码了。
    if args.log_file:
        _p = _open_log(args.log_file, append=args.log_append)
        print(f"  日志        : {_p}  （UTF-8/LF，"
              f"{'追加' if args.log_append else '覆盖'}，已接管 stdout+stderr）")

    # ★ 延迟必须在**任何注入之前**，否则按键全进了 PowerShell。
    #   --delay 0 才跳过（方便已经手动切好窗口时用）。
    if args.live:
        wait_foreground(args.delay)

    hr("S0 · 环境与前置检查")
    fg_title, fg_hwnd = foreground_title()
    print(f"  前台窗口      : {fg_title!r}  (hwnd={fg_hwnd})")
    if looks_like_game(fg_title):
        print(f"  [OK] 前台是游戏窗口")
    else:
        # ★ 看门狗：非游戏在前台时**响亮警告**并停下。
        #   继续跑只会把按键发给 PowerShell，测出来的结论毫无意义。
        # ⚠️ 这里刻意用 ASCII 标记（``!!``）而不是 ``✗``：诊断脚本的报错
        #   必须在**任何**终端编码下都清晰可读，而 Unicode 符号经过
        #   某些重定向/管道（GBK 控制台 + Set-Content）会被转义成
        #   字面 ``\u2717`` 反而更难懂。
        print()
        print(f"  !! 前台不是游戏窗口（当前 {fg_title!r}）。", file=sys.stderr)
        print("     现在按的键会**发给 PowerShell**，诊断结果无效。",
              file=sys.stderr)
        print("     请切到原神窗口后重跑，或用 --delay 给自己留时间。",
              file=sys.stderr)
        return 2
    print(f"  目标窗口标题  : 原神")
    print()
    print("  键盘布局（VK_W=0x57 恒定，但产生的字符随布局变）：")
    print("    - QWERTY/AZERTY 下 VK_W 产生 'w'")
    print("    - 若游戏内布局非 QWERTY，可能出现「按了 W 但走不了」")

    if not args.live:
        print()
        print("=" * 78)
        print("静态检查完成。加 --live 才会真的按住键。")
        print("=" * 78)
        return 0

    print()
    print("  按 Shift 或 W 取消一下，确认游戏里没卡住的按键…")
    time.sleep(1.0)

    # ★ 每种 key_mode 一个独立的 sim —— key_mode 是 InputSimulator 的
    #   构造参数，构造后就不再改。这样 release_all / diagnose 的口径
    #   和按下时严格一致（中途改字段会让「按下的路」和「抬起的路」
    #   不一致，键会卡在游戏里）。
    sims: dict[str, object] = {}
    for mode in {s[2] for s in strats}:
        sim = InputSimulator(key_mode=mode)
        try:
            rep = sim.diagnose()
            print(f"  backend[{mode}] : {rep}")
        except Exception as exc:                              # noqa: BLE001
            print(f"  backend[{mode}] 诊断失败: {exc}")
        sims[mode] = sim

    results = []
    try:
        for name, fn, mode, why in strats:
            sim = sims[mode]
            letter = name[0].upper()
            print()
            print("=" * 78)
            print(f"{name}    按住 {args.hold:.1f}s")
            print(f"  key_mode = {mode}    验的是：{why}")
            print("=" * 78)
            # 先确保干净
            sim.release_all()
            time.sleep(0.15)
            snapshot("注入前")

            fn(sim)
            # 立刻读 —— 这一刻最能说明系统到底认不认
            time.sleep(0.05)
            a = snapshot("注入后 50ms")
            print(f"    ★ {args.hold:.1f}s 内**盯着游戏看角色在做什么**")
            time.sleep(max(0.0, args.hold - 0.3))
            b = snapshot(f"按住中 {args.hold-0.3:.1f}s")

            sim.release_all()
            time.sleep(0.1)
            c = snapshot("释放后")

            ok = a["shift_down"] and b["shift_down"]
            verdict = ("系统层 Shift 确实按住"
                       if ok else
                       "!! Shift 没被按住 <- 就是走路的原因")
            print(f"  => {verdict}")
            results.append((name, mode, ok, a, b, c))

            # ★ --only 单跑：立刻退出，**不等待**（2026-10-04）。
            #   你切回 PowerShell 时脚本已经结束、日志已经写完。
            #   任何 input()/等待都会让流程卡在一个你看不见的提示上。
            if args.only:
                _emit_test_note(args.test_note, letter)
                print()
                print("  单跑模式：脚本到此结束（日志已写完，可切回 PowerShell）。")
                _nxt = _next_letter(letter)
                print(f"  下一条建议：--live --only {_nxt} --test-note \"...\"")
                if args.log_file and not args.log_append:
                    # ★ 不加这句，用户 6 次跑完只会留下最后一条 TEST-NOTE
                    print("  !! 记得加 --log-append，否则下一轮会把这份日志**覆盖**掉。")
                return 0
    finally:
        for sim in sims.values():
            sim.release_all()
        print()
        print("  已释放全部按键。")

    print()
    print("=" * 78)
    print("汇总")
    print("=" * 78)
    print(f"  {'策略':<32} {'模式':<10} {'系统层':<10}")
    print("  " + "-" * 74)
    for name, mode, ok, a, b, c in results:
        print(f"  {name:<32} {mode:<10} {'按住' if ok else '!!没按住':<10}")

    print()
    if all(r[2] for r in results):
        print("  所有策略在**系统层**都按住了 Shift。")
        print("  -> 注入本身没问题，问题在**游戏是否接受**。")
        print()
        print("  ★ 接下来分次跑，一次一条，把观察写进日志：")
        for name, mode, ok, a, b, c in results:
            letter = name[0].upper()
            print(f"       --live --only {letter} --test-note \"跑步/走路/闪避\"")
        print("  然后 grep TEST-NOTE 日志文件汇总。")
    else:
        bad = [r[0] for r in results if not r[2]]
        print(f"  !! 以下策略 Shift 没被按住: {bad}")
        print("  -> 注入本身有问题，先看上面每轮的「注入后 50ms」读数。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
