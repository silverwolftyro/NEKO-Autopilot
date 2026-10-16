"""F12 物理可见性最小诊断 —— 只回答一件事：**本进程能否看见物理 F12？**

只用 ctypes + time，**不 import 任何项目模块**，不启动任何主循环。
``python diag_f12.py`` -> 切到游戏狂按 F12。

判读：命中 0 次 = 本进程看不见物理 F12（查权限 / 安全软件 / 窗口焦点）；
命中 N 次 = F12 可见，问题在寻路脚本那一侧。
"""
import sys

# ---- console encoding ----------------------------------------------
# chcp 65001 only changes the console code page; Python still writes bytes
# in its own encoding (cp936/GBK on Chinese Windows), so every Chinese
# string in this script comes out as mojibake. Force both streams UTF-8.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):   # redirected to StringIO -> no method
        _s.reconfigure(encoding="utf-8", errors="replace")
import ctypes
import time

POLL_S = 0.005          # 5ms 轮询
DEBOUNCE_S = 0.150      # 150ms 去抖：长按只记一次，不刷屏
DURATION_S = 30.0
F12 = 0x7B

u32 = ctypes.WinDLL("user32", use_last_error=True)
# ⚠️ 必须显式声明：默认 ctypes 把 HWND 当 32 位 int，64 位窗口句柄
#    会被截断，前台窗口标题就可能打错对象（诊断工具不能骗人）。
u32.GetForegroundWindow.restype = ctypes.c_void_p
u32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
shell32 = ctypes.WinDLL("shell32")

print(f"管理员权限     : {bool(shell32.IsUserAnAdmin())}")
hwnd = u32.GetForegroundWindow()
buf = ctypes.create_unicode_buffer(512)
u32.GetWindowTextW(hwnd, buf, 512)
print(f"前台窗口       : {buf.value!r}  hwnd=0x{hwnd:X}")

print(f"\n轮询 {DURATION_S:.0f}s，每 {POLL_S*1000:.0f}ms 一次。"
      f"现在去按 F12！\n")
t0 = time.monotonic()
last = -999.0
count = 0
while time.monotonic() - t0 < DURATION_S:
    if u32.GetAsyncKeyState(F12) & 0x8000:
        now = time.monotonic() - t0
        if now - last >= DEBOUNCE_S:
            count += 1
            print(f"  命中 #{count}  t={now:6.3f}s")
        last = now
    time.sleep(POLL_S)

print(f"\n总命中         : {count} 次")
if count == 0:
    print("→ 本进程**看不见**物理 F12。先查权限 / 安全软件 / 窗口焦点。")
else:
    print("→ F12 可见。问题在寻路脚本那一侧，不在系统。")
