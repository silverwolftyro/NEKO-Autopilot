"""F12 急停**纯监听**验证 —— 不截图、不感知、不定位、不发任何按键。

``e2e_pathfind.py`` 主循环第一行就是 ``cap.grab()``，窗口没抓到就立刻
抛异常退出，**F12 监听器根本没机会被测到**。本脚本把 F12 从截图依赖里
剥离：主循环只做两件事 —— 打印状态、``panic.stop.wait(0.2)``。

★ 用的是**真实的类**（从 e2e_pathfind.py import，含那 7 处
  ``[WATCHER]`` 插桩）。实测 import ~90ms，**不拖入任何感知模块**。
  ⚠️ 改动那边的 ``_PanicWatcher`` 后**必须重跑本脚本**。

``python diag_f12_watcher.py`` -> 3 秒后切到原神，数 5 秒按一次 F12。
"""

import importlib.util
import sys

# ---- console encoding ----------------------------------------------
# chcp 65001 only changes the console code page; Python still writes bytes
# in its own encoding (cp936/GBK on Chinese Windows), so every Chinese
# string in this script comes out as mojibake. Force both streams UTF-8.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):   # redirected to StringIO -> no method
        _s.reconfigure(encoding="utf-8", errors="replace")
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("e2e_pathfind",
                                              HERE / "e2e_pathfind.py")
pf = importlib.util.module_from_spec(_spec)
sys.modules["e2e_pathfind"] = pf          # dataclass / 相对引用需要
_spec.loader.exec_module(pf)

DURATION_S = 30.0
TICK_S = 0.2
COUNTDOWN_S = 3.0


def main() -> int:
    # 不建 InputSimulator：_rel 只用来看「松键回调被调了几次」，
    # 而真机上松键有没有真的生效，由 [WATCHER] 那几行日志 + 角色
    # 是否停下一起判断。这里验证的是**通知链路**，不是注入。
    rel_calls: list[float] = []

    def release():
        rel_calls.append(time.monotonic())
        print(f"[TEST] release_cb called (#{len(rel_calls)})", flush=True)

    panic = pf._PanicWatcher(release_cb=release)
    err = panic.start()
    if err:
        print(f"[FATAL] 监听线程没起来：{err}", file=sys.stderr)
        return 2

    print(f"\n[TEST] 切到原神，{COUNTDOWN_S:g}s 后开始。数 5 秒按一次 F12。\n")
    time.sleep(COUNTDOWN_S)

    t0 = time.monotonic()
    try:
        while True:
            el = time.monotonic() - t0
            print(f"[MAIN] tick t={el:5.1f} fired={panic.fired} "
                  f"stop={panic.stop.is_set()}", flush=True)
            if panic.fired:
                print(f"[MAIN] exit: F12 fired  "
                      f"(检测耗时 {panic.detected_at - t0:.3f}s, "
                      f"松键回调 {len(rel_calls)} 次)")
                return 0
            if el >= DURATION_S:
                print(f"[MAIN] exit: timeout  "
                      f"(松键回调 {len(rel_calls)} 次 —— 一次都没被调用)")
                return 1
            # ★ Event.wait 而不是 time.sleep：置位后立即返回，不干等满 200ms
            panic.stop.wait(TICK_S)
            print(f"[MAIN] wait ret fired={panic.fired} "
                  f"stop={panic.stop.is_set()}", flush=True)
    except KeyboardInterrupt:
        print("\n[MAIN] exit: Ctrl+C")
        return 130
    finally:
        panic.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
