"""环境诊断 —— 一眼看清「这台机器上现在能不能干活」。

不开游戏也能跑；开了游戏则连游戏进程一起查。

用法::

    python diag_env.py                 # 全查（默认先倒数 8 秒）
    python diag_env.py --delay 15      # 切游戏需要更久就加大
    python diag_env.py --delay 0       # 已经切好了，不用等
    python diag_env.py --quiet         # 只出结论，不出细节

★ 为什么默认要倒数
------------------
在 PowerShell 里敲命令时，**原神会失焦甚至被最小化**。这时
``GetWindowRect`` 返回的是哨兵矩形（实测 ``158x26 @ (-21333,-21333)``），
dxcam 会直接报 ``Invalid Region``。倒数的意义就是**给你切回前台的时间**。

查五件事
--------
1. 本进程权限（普通 / 管理员）
2. 游戏窗口在不在（按标题枚举**所有**窗口，不看前台）
3. 游戏进程权限
4. dxcam / mss 两个抓帧通道各自能不能用，**失败原因**
5. UIPI 隔离判定：同级别 / 隔离 / 游戏没开

★ 为什么值得单独有一个脚本
--------------------------
前面几轮的教训：UIPI 自检查的是**前台窗口**，而脚本启动时前台是终端，
于是「检查通过」其实什么都没查 —— 两次空测试就是这么来的。
而 ``Capturer`` 的通道降级只报结果不报原因（``import dxcam`` 失败被
``except Exception: return False`` 吞掉），用户只看到「降级了」，
看不到「为什么」。

这个脚本把三件事一次说清：权限、游戏在不在、每个通道为什么不行。

⚠️ 只用 ctypes + 标准库，**不 import 主工程**。
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

# ★ 检测逻辑**全部**来自同目录的 uipi_check —— 那边是唯一事实源。
#   这里曾经有一份独立拷贝，于是上一轮修「Edge 标签页标题含『原神』被
#   误判成游戏」和「OpenProcess 打不开 -> unknown 判定不准」时，
#   **本文件没跟着改**，跑出来还是旧行为。两份拷贝必然漂移，删掉。
#   （uipi_check 是同目录的纯 ctypes 脚本，不是主工程，import 它
#     不破坏「独立诊断、不依赖主工程」的前提。）
sys.path.insert(0, str(Path(__file__).resolve().parent))
import uipi_check as U  # noqa: E402

_user32 = ctypes.WinDLL("user32", use_last_error=True)

SEP = "=" * 72


def hr(t: str = "") -> None:
    print(f"\n{SEP}\n  {t}\n{SEP}" if t else SEP)


def window_rect(hwnd) -> tuple[int, int, int, int]:
    """取窗口矩形。诊断脚本要拿它去探抓帧通道，属于展示层胶水。"""
    if not hwnd:
        return (0, 0, 0, 0)
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
    r = wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return (0, 0, 0, 0)
    return (r.left, r.top, r.right - r.left, r.bottom - r.top)


def countdown(seconds: float) -> None:
    """开跑前倒数，让用户切回原神。**自己实现**，不 import e2e_* 的。"""
    if seconds <= 0:
        return
    print(f"\n  [DELAY] {seconds:g} 秒后开始 —— ★ 现在切到原神，"
          f"**保持它在前台**！")
    print("         （切窗口会让游戏失焦甚至最小化，窗口矩形会退化成"
          "哨兵值，诊断结果就不准了）")
    end = time.monotonic() + seconds
    while True:
        left = end - time.monotonic()
        if left <= 0:
            break
        print(f"    剩余 {left:5.1f} s\r", end="", flush=True)
        time.sleep(0.1)
    print("\n")


DEFAULT_DELAY = 8.0
#: 最小化窗口的哨兵坐标。Win32 对最小化窗口返回 -32000 一族的大负值，
#: 实测见过 (-21333, -21333)。用它识别「窗口被最小化」。
_MINIMIZED_SENTINEL = -20000


def main() -> int:
    argv = sys.argv[1:]
    quiet = "--quiet" in argv
    delay = DEFAULT_DELAY
    if "--delay" in argv:
        i = argv.index("--delay")
        try:
            delay = max(0.0, float(argv[i + 1]))
        except (IndexError, ValueError):
            print(f"✗ --delay 取值不合法: {argv[i+1] if i+1 < len(argv) else '(缺)'}",
                  file=sys.stderr)
            return 2

    hr("0 · 延迟")
    print(f"  延时            : {delay:g} 秒（--delay 0 可关闭）")
    countdown(delay)

    hr("1 · 本进程")
    me = U.is_admin()
    print(f"  权限            : {'管理员' if me else '普通'}")
    print(f"  解释            : "
          f"{'能向普通窗口注入' if me else '无法向管理员窗口注入（UIPI）'}")

    hr("2 · 游戏窗口")
    hwnd, title = U.find_game_window()
    rect = window_rect(hwnd)
    # 必须在分支外初始化：hwnd 为 None 时下面的汇总也会读它。
    _degenerate = False
    if hwnd is None:
        print(f"  ✗ 未找到。按标题找：{' / '.join(U.GAME_TITLES)}")
        print("    请先开原神再跑本脚本。")
    else:
        print(f"  ✓ {title!r}  hwnd=0x{hwnd:X}")
        print(f"    窗口矩形       : {rect[2]}x{rect[3]} @ ({rect[0]},{rect[1]})")
        # ★ 最小化的两种表现都要认：
        #   (a) 宽高 0
        #   (b) 宽高是 158x26 那种「只剩一条边」，且坐标是 -21333 一族的
        #       大负值（Win32 对最小化窗口返回的哨兵矩形）
        #   只判 (a) 会漏掉 (b) —— 而 (b) 正是实测里 dxcam 报
        #   Invalid Region 的那种。只警告，不阻止。
        _degenerate = (rect[2] <= 0 or rect[3] <= 0
                       or rect[0] <= _MINIMIZED_SENTINEL
                       or rect[1] <= _MINIMIZED_SENTINEL)
        if _degenerate:
            print("    ⚠ 游戏窗口似乎最小化了 —— 诊断结果可能不准。")
            print(f"      （宽高 {rect[2]}x{rect[3]}、坐标 ({rect[0]},{rect[1]}) "
                  f"是 Win32 对最小化窗口的哨兵值）")
            print("      请把游戏恢复成**正常显示**（不是最小化到托盘）"
                  "再重跑。")
            print("      接下来的通道结果多半会报 Invalid Region / 空帧。")

    hr("3 · 游戏进程权限")
    game_elev = None
    if hwnd is None:
        print("  -  游戏没开，跳过")
    else:
        game_elev = U.process_elevated(U._window_pid(hwnd))
        if game_elev is None:
            print("  ?  提权状态查不到（OpenProcess 被拒）")
        else:
            print(f"  权限            : {'管理员' if game_elev else '普通'}")

    hr("4 · 抓帧通道")
    chans = U.probe_channels(rect)
    for ch, ok, why in chans:
        print(f"  {ch:12} {'✓' if ok else '✗'}  {why}")
    if not chans:
        print("  （连探针都没跑起来 —— 见上面的异常）")

    # ★ 判定**统一**走 uipi_check.verdict —— 旧的本文件版本把
    #   「OpenProcess 打不开」一律当 unknown，漏掉了「打不开本身就是
    #   隔离证据」这一条。
    hr("5 · UIPI 隔离判定")
    verdict = U.verdict(me, game_elev, game_found=(hwnd is not None))
    if verdict == U.SAME_LEVEL:
        print(f"  ✓ 同级别（本进程{'管理员' if me else '普通'} = "
              f"游戏{'管理员' if game_elev else '普通'}）-> **无隔离**")
    elif verdict == U.UNKNOWN:
        print("  ?  游戏没开 -> **判不了**")
    elif verdict == U.MAYBE_HIGHER:
        print("  ?  游戏提权状态**打不开**，而本进程已是管理员")
        print("     -> 游戏权限 >= 本进程（无法进一步区分）")
    elif verdict == U.LIKELY_ISOLATED:
        print("  ✗ **很可能隔离** —— 游戏提权状态打不开，而本进程不是管理员")
        print("     OpenProcess 打不开通常**本身就是隔离的证据**"
              "（目标完整性级别更高）。")
    else:
        print("  ✗ **隔离** —— 双方权限级别不同：")
    if verdict in (U.ISOLATED, U.LIKELY_ISOLATED):
        print("       · 按键注入（Shift+W / 普攻 / 闪避）不生效")
        print("       · 鼠标移动不生效")
        print("       · 部分抓帧路径也可能拿不到内容")
        print("       · 而且**不报错**")
        print()
        print("    两条路，选哪条都行：")
        print("      a) 让游戏以**普通权限**运行（若反作弊允许）")
        print("      b) 用**管理员** PowerShell 跑脚本")

    if not quiet:
        hr("小结")
        ok_ch = [c for c, o, _ in chans if o]
        print(f"  可用抓帧通道 : {ok_ch or '（一个都没有）'}")
        print(f"  隔离判定     : {verdict}")
        # ⚠️ 这里必须比 U.SAME_LEVEL（值是 "same"），不能比字面量 "ok" ——
        #    之前写的是 "ok"，永远不匹配，于是即使一切正常也显示
        #    「先把上面标 ✗ 的问题解决」，把好消息变成噪音。
        if _degenerate:
            print("  → ⚠ 窗口当时是**最小化**状态，上面的通道结果不可信。")
            print("    恢复游戏为正常显示后重跑本脚本。")
        elif verdict != U.SAME_LEVEL or not ok_ch:
            print("  → 先把上面标 ✗ 的问题解决，再跑 e2e_* 系列。")
        else:
            print("  → 环境就绪，可以跑 e2e_dryrun.py --live 验证。")

    hr()
    return 0 if verdict == "ok" else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
