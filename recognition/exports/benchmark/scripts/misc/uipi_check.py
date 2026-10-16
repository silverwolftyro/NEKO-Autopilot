"""UIPI 权限自检 —— 在发任何按键**之前**先确认注入不会被系统拦掉。

★ 为什么需要它
--------------
原神的反作弊是**内核级**的，游戏必须以管理员权限运行。UIPI
（User Interface Privilege Isolation）是**单向**的：**低权限进程向
高权限窗口注入输入会被系统直接丢弃**，反方向允许。

所以隔离是**永久的**，脚本也只能以管理员运行：Shift+W / 鼠标转向 /
普攻 / 闪避在普通权限下**全部静默失效** —— 不报错、不抛异常、
什么现象都没有。

⚠️ 它**不能**解释 ``GetAsyncKeyState`` 读不到：那是全局状态查询，
   不针对任何窗口，严格说不受 UIPI 管辖。两件事分开定位。

★ 两个入口
----------
:func:`require_admin`  --live 的**门禁**，非管理员直接 exit 2。
:func:`check`          纯诊断，按标题找到游戏窗口后报判定，**不拦**。

本模块是权限策略的**唯一实现** —— ``diag_env.py`` 一律 import 过来，
不要在别处再抄一份。

退出码：2 = 需要管理员权限，调用方应立即退出（**在发第一个键之前**）。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

# ★ 本模块会被两个 live 脚本 import，但**不能**假设调用方已经配好 stdout。
#   中文 Windows 控制台默认 GBK，而下面的提示里带了 ✗ / ★ / —— 这类字符
#   在 GBK 下会直接抛 UnicodeEncodeError，把「权限不足」这条关键信息
#   变成一个看不懂的崩溃。自己兜住。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 - 老 Python 没这功能，忽略
    pass

#: 提权 API 需要的原型。故意**不缓存** restype/argtypes 到模块级，
#: 全部在函数内部设 —— 这些是 DLL 句柄级别的属性，混进别的模块会互相污染。
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)

#: 0x2000 = TOKEN_QUERY
_TOKEN_QUERY = 0x0008
#: 25 = TokenElevation
_TOKEN_ELEVATION = 25
#: 0x1000 = PROCESS_QUERY_LIMITED_INFORMATION
_PROCESS_QUERY_LIMITED = 0x1000

#: 游戏窗口标题的候选子串（中英文都认）。匹配用**子串**，大小写无关。
GAME_TITLES: tuple[str, ...] = (
    "原神", "Genshin Impact", "GenshinImpact", "YuanShen",
)

#: ★ **宿主进程黑名单**。实测踩过：Edge 标签页标题里含「原神」
#:   （用户在查资料），被当成了游戏窗口 —— 诊断结论直接变成假的。
#:   判据是标题或窗口类名里出现这些标记。
HOST_MARKERS: tuple[str, ...] = (
    "edge", "chrome", "chromium", "firefox", "opera", "brave", "vivaldi",
    "powershell", "pwsh", "windowsterminal", "cmd.exe", "conhost",
    "devenv", "code.exe", "explorer", "applicationframehost", "widgets",
    "taskmgr", "terminal", "notepad", "wechat", "qq.exe", "discord",
)

#: Unity 游戏的窗口类名。用来给「标题不含游戏字样」的情况兜底。
GAME_CLASS_HINTS: tuple[str, ...] = ("unitywndclass", "unity", "unrealwindow")

#: 隔离判定结果
SAME_LEVEL = "same"            # 同级别，无隔离
ISOLATED = "isolated"          # 确认隔离
LIKELY_ISOLATED = "likely"     # 查不到目标提权状态，但本进程非管理员
MAYBE_HIGHER = "maybe_higher"  # 本进程已是管理员还查不到 -> 目标 >= 我
UNKNOWN = "unknown"            # 游戏没开


def _is_host(title: str, cls: str) -> bool:
    low = f"{title} {cls}".lower()
    return any(m in low for m in HOST_MARKERS)


def _score(title: str, cls: str) -> int:
    """给候选窗口打分。-1 = 排除。分数越高越像游戏。"""
    t = title.strip()
    if _is_host(title, cls):
        return -1
    low = t.lower()
    if t in GAME_TITLES:                      # 精确等于「原神」
        return 100
    if any(c in cls.lower() for c in GAME_CLASS_HINTS):
        return 90 + (20 if ("原神" in t or "genshin" in low) else 0)
    if ("genshin" in low or "yuanshen" in low) and len(t) <= 40:
        return 70
    if "原神" in t and len(t) <= 12:           # 短标题（游戏标题一般很短）
        return 60
    if "原神" in t:
        return 30
    return -1


def verdict(me_admin: bool, game_elev: bool | None,
            game_found: bool = True) -> str:
    """把「本进程权限 + 游戏提权状态（可能查不到）」合成一个结论。

    ★ 关键修正（曾踩的坑）
    -------------------
    旧逻辑：``OpenProcess`` 失败 -> 「判不了」。这是**错的**。
    ``OpenProcess(PROCESS_QUERY_LIMITED)`` 失败的常见原因**就是目标
    进程完整性级别高于本进程** —— 也就是说，「打不开」本身就是一条
    隔离证据，不该被当成「信息缺失」丢掉。

        打不开 + 我不是管理员 -> 大概率目标比我高 = **很可能隔离**
        打不开 + 我是管理员   -> 目标 >= 我（可能同级别也可能更高），
                                保守说「需进一步查」而不是「无隔离」
        打得开              -> 比 TokenElevation，才是确凿结论
    """
    if not game_found:
        return UNKNOWN
    if game_elev is None:
        return LIKELY_ISOLATED if not me_admin else MAYBE_HIGHER
    return SAME_LEVEL if me_admin == game_elev else ISOLATED


# --------------------------------------------------------------------------
# 进程 / 窗口查询
# --------------------------------------------------------------------------
def is_admin() -> bool:
    """当前进程是否以管理员运行。"""
    try:
        return bool(_shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001 - 查不到就当不是，保守
        return False


def process_elevated(pid: int) -> bool | None:
    """某进程是否以管理员运行。**打不开就返回 None**，不猜。

    ★ restype 必须显式声明：不声明时 ctypes 按 c_int 解释返回值，
      64 位下 GetCurrentProcess 的伪句柄（-1）会被截断成垃圾，
      OpenProcessToken 直接报 ERROR_INVALID_HANDLE。
    """
    try:
        _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                         wintypes.DWORD]
        _kernel32.OpenProcess.restype = wintypes.HANDLE
        h = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED, False, pid)
        if not h:
            return None
        tok = wintypes.HANDLE()
        _advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                              ctypes.POINTER(wintypes.HANDLE)]
        if not _advapi32.OpenProcessToken(h, _TOKEN_QUERY, ctypes.byref(tok)):
            return None
        n = wintypes.DWORD()
        _advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        if not _advapi32.GetTokenInformation(tok, _TOKEN_ELEVATION, None, 0,
                                            ctypes.byref(n)):
            return None
        buf = ctypes.create_string_buffer(n.value)
        if not _advapi32.GetTokenInformation(tok, _TOKEN_ELEVATION, buf, n,
                                            ctypes.byref(n)):
            return None
        return bool(ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0])
    except Exception:  # noqa: BLE001
        return None


def _window_pid(hwnd) -> int:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def find_game_window(titles: tuple[str, ...] = GAME_TITLES
                     ) -> tuple[int, str] | tuple[None, None]:
    """按标题**枚举所有窗口**找游戏。返回 ``(hwnd, 标题)`` 或 ``(None, None)``。

    ★ 为什么不能用「前台窗口」
    ------------------------
    脚本启动时前台是 PowerShell/终端，不是游戏。按前台查提权，
    查的是**终端的权限**，与游戏无关 —— 那正是曾经两次「空测试」
    的成因。要判断隔离，必须找到**游戏进程**自己。

    ★ 多候选择优
    ------------
    实测踩过：Edge 标签页标题含「原神」会被误判。所以先按
    :func:`_score` 打分（排除宿主 > 精确标题 > Unity 类名 > 短标题），
    取最高分；一个都没有才返回 ``(None, None)``。
    """
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p,
                                       ctypes.c_int]
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetClassNameW.argtypes = [wintypes.HWND, ctypes.c_wchar_p,
                                      ctypes.c_int]
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    cands: list[tuple[int, int, str]] = []      # (分数, hwnd, 标题)

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        n = _user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        _user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        cbuf = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, cbuf, 256)
        s = _score(title, cbuf.value)
        if s >= 0:
            cands.append((s, int(hwnd), title))
        return True

    try:
        _user32.EnumWindows(WNDENUMPROC(_cb), 0)
    except Exception:  # noqa: BLE001 - 枚举失败就当没找到
        return None, None
    if not cands:
        return None, None
    cands.sort(key=lambda x: -x[0])
    _s, hwnd, title = cands[0]
    return hwnd, title


def foreground_info() -> tuple[str, bool | None]:
    """返回 ``(窗口标题, 该进程是否提权)``。查不到时第二项为 None。"""
    _user32.GetForegroundWindow.restype = wintypes.HWND
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return "<无前台窗口>", None
    buf = ctypes.create_unicode_buffer(512)
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
    _user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value or f"<hwnd=0x{hwnd:X}>", process_elevated(_window_pid(hwnd))


# --------------------------------------------------------------------------
# 抓帧通道探针
# --------------------------------------------------------------------------
def probe_channels(rect=None) -> list[tuple[str, bool, str]]:
    """逐个探测 dxcam / mss，**把失败原因带出来**。

    为什么要自己探一遍：``Capturer._init_channel`` 里
    ``import dxcam`` 失败是 ``except Exception: return False`` ——
    原因被吞掉；``_demote_channel`` 的失败只打到 ``logger.debug``，
    默认看不见。于是用户只看到「降级：mss -> printwindow」，
    看不到**为什么**降级。这里每个通道都给出 (通道, 可用, 原因)。

    :param rect: 抓帧区域 ``(x, y, w, h)``；None 则用当前前台窗口。
    """
    out: list[tuple[str, bool, str]] = []
    if rect is None:
        _user32.GetForegroundWindow.restype = wintypes.HWND
        hwnd = _user32.GetForegroundWindow()
        _user32.GetWindowRect.argtypes = [wintypes.HWND,
                                          ctypes.c_void_p]
        r = wintypes.RECT()
        if hwnd and _user32.GetWindowRect(hwnd, ctypes.byref(r)):
            rect = (r.left, r.top, r.right - r.left, r.bottom - r.top)
        else:
            rect = (0, 0, 0, 0)

    # ---- dxcam ----
    try:
        import dxcam  # noqa: PLC0415
        cam = dxcam.create(output_color="BGR")
        if cam is None:
            out.append(("dxcam", False, "dxcam.create() 返回 None"
                        "（没有可用的输出设备 / 驱动不支持 DXGI 复制）"))
        else:
            try:
                img = cam.grab(region=rect if rect[2] > 0 else None)
                if img is None or getattr(img, "size", 0) == 0:
                    out.append(("dxcam", False, "create 成功但 grab() 返回空帧"))
                else:
                    out.append(("dxcam", True,
                                f"可用（抓到 {img.shape[1]}x{img.shape[0]}）"))
            finally:
                for m in ("stop", "release", "close"):
                    fn = getattr(cam, m, None)
                    if callable(fn):
                        try:
                            fn()
                        except Exception:  # noqa: BLE001
                            pass
                        break
    except Exception as exc:  # noqa: BLE001
        out.append(("dxcam", False, f"{type(exc).__name__}: {exc}"))

    # ---- mss ----
    try:
        import mss  # noqa: PLC0415
        inst = mss.MSS()
        try:
            if rect[2] > 0 and rect[3] > 0:
                s = inst.grab({"left": rect[0], "top": rect[1],
                               "width": rect[2], "height": rect[3]})
                out.append(("mss", True,
                            f"可用（抓到 {s.width}x{s.height}）"))
            else:
                out.append(("mss", False, "窗口矩形无效（w/h <= 0）"))
        finally:
            inst.close()
    except Exception as exc:  # noqa: BLE001
        out.append(("mss", False, f"{type(exc).__name__}: {exc}"))

    return out


# --------------------------------------------------------------------------
# live 入口门禁
# --------------------------------------------------------------------------
def require_admin(live: bool) -> int:
    """``--live`` 的**唯一**门禁：必须是管理员。返回 0 继续 / 2 退出。

    ★ 为什么是硬要求（不是建议）
    --------------------------
    原神反作弊是**内核级**的，游戏必须以管理员权限运行。UIPI 隔离是
    **单向且永久**的：低权限进程发往高权限窗口的输入（按键、鼠标移动）
    以及部分抓帧路径，都会被系统**静默丢弃** —— 不报错、不抛异常，
    什么现象都没有。降权游戏不可行，所以脚本只能跟着提权。

    ★ dry-run 不需要管理员
      它不发任何输入，也没有隔离问题。强制提权只会平白增加摩擦。

    ★ 只检查**本进程**是管理员，**不检查游戏是否更高**。
      那种情况由 :func:`check` 报警（显著标记），但**不拦** ——
      管理员脚本 + 更高游戏的场景罕见，不该因为可能的误判阻止用户。

    ★ **此函数是唯一的权限策略点。**
      将来若出现非管理员方案（低级键盘钩子 / IPC 委托 / 反作弊策略变化），
      **只改本函数 + :func:`check`**，任何调用方都不用动。
    """
    if not live:
        return 0
    if is_admin():
        return 0
    print()
    print("  " + "!" * 68)
    print("  !!  --live 需要**管理员权限**                                !!")
    print("  !!                                                          !!")
    print("  !!  原神的反作弊是内核级的，游戏必须以管理员权限运行。       !!")
    print("  !!  UIPI 隔离是单向且永久的：本脚本按普通权限运行时，        !!")
    print("  !!  发往游戏的按键 / 鼠标移动会被系统**静默丢弃**，          !!")
    print("  !!  而且**不报错**。降权游戏不可行，所以只能提权跑脚本。    !!")
    print("  " + "!" * 68)
    print()
    print("  请用**管理员** PowerShell 重跑：")
    print('      右键「Windows PowerShell」->「以管理员身份运行」')
    print("      然后再执行本命令")
    print()
    print("  只想看决策不发键？去掉 --live 即可，**不需要管理员**。")
    print()
    return 2


# --------------------------------------------------------------------------
# 诊断（非门禁）
# --------------------------------------------------------------------------
def check(live: bool) -> int:
    """**纯诊断**：找到游戏窗口后报隔离判定。**永远返回 0，从不拦。**

    门禁在 :func:`require_admin`（入口）。这里只负责把「游戏标题 +
    双方权限 + 判定」摆出来，且在判定为隔离时打**显著标记** —— 那行
    日志很容易被几百行帧输出淹没，而它恰恰是「按键全被静默吞掉」的
    唯一提示。

    ★ 脚本启动时前台是终端，按前台查权限查的是终端而不是游戏，
      曾经因此产生两次「空测试」—— 所以这里**只认按标题枚举出来的
      游戏窗口**。
    """
    if not live:
        return 0
    me = is_admin()
    hwnd, title = find_game_window()
    if hwnd is None:
        print("  [UIPI] 未检测到游戏窗口（按标题找：" +
              " / ".join(GAME_TITLES) + "）-> 隔离判定跳过")
        return 0
    fg_elev = process_elevated(_window_pid(hwnd))
    v = verdict(me, fg_elev, game_found=True)
    if v in (ISOLATED, LIKELY_ISOLATED):
        print()
        print("  " + "!" * 68)
        print("  !!!  权限级别仍不匹配 —— 按键可能被静默吞掉            !!!")
        print(f"  !!!  游戏={title!r} 管理员={fg_elev} | 本进程管理员={me}   !!!")
        print("  !!!                                                      !!!")
        print("  !!!  发往游戏的按键 / 鼠标移动会被系统丢弃且不报错，     !!!")
        print("  !!!  所以「机器人不动」很可能**不是寻路逻辑的问题**。   !!!")
        print("  !!!  若已确认双方同级，这行可忽略（可能是误判）。       !!!")
        print("  " + "!" * 68)
        return 0
    tag = {SAME_LEVEL: "同级别，无隔离",
           MAYBE_HIGHER: "游戏权限 >= 本进程（无法进一步区分）",
           UNKNOWN: "未知"}[v]
    print(f"  [UIPI] 游戏={title!r} 管理员={fg_elev} | "
          f"本进程管理员={me}  ->  {tag}")
    return 0


__all__ = ["GAME_TITLES", "HOST_MARKERS", "ISOLATED", "LIKELY_ISOLATED",
           "MAYBE_HIGHER", "SAME_LEVEL", "UNKNOWN", "check", "find_game_window",
           "foreground_info", "is_admin", "process_elevated", "probe_channels",
           "require_admin", "verdict"]
