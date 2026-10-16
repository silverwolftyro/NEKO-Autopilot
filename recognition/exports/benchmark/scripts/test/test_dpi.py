"""DPI 反算兜底路径的单测（§34 · 任务 D1）。

★ 为什么不依赖真实 DPI 环境
------------------------
本机的进程一旦 import :mod:`capturer` 就会真的变成 DPI-aware，
之后 ``GetSystemMetrics`` 返回物理像素 —— **没法在这个进程里复现
"unaware + 150%" 的状态**。所以这里用 mock 直接驱动
:meth:`Capturer._physical_rect` 的两条分支，验证纯算术。
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

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(PLUGIN_DIR)
sys.path.insert(0, str(ROOT))

from autopilot.perception import capturer as C  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f"   {detail}" if detail else ""))


print("=" * 92)
print("T1 · 模块 import 后 DPI 感知已生效")
print("=" * 92)
check("_DPI_AWARENESS_SET = True（只调一次的守卫已置位）",
      C._DPI_AWARENESS_SET is True)
check("_DPI_AWARE = True（本机 import 后实测生效）", C._DPI_AWARE is True)
import ctypes  # noqa: E402

w = ctypes.windll.user32.GetSystemMetrics(0)
h = ctypes.windll.user32.GetSystemMetrics(1)
print(f"      GetSystemMetrics = {w}x{h}")
check("GetSystemMetrics 返回的是物理尺寸（2560x1600）",
      (w, h) == (2560, 1600), f"{w}x{h}")

print("\n" + "=" * 92)
print("T2 · _proc_is_dpi_aware：INVALID 不能当成 aware")
print("=" * 92)
# 直接测判定表：只有 2/3 算 True
check("_proc_is_dpi_aware() 当前返回 True", C._proc_is_dpi_aware() is True)
_src = Path(C.__file__).read_text(encoding="utf-8")
check("判定只认 SYSTEM/PER_MONITOR（不是 != 1）",
      "return aware in (2, 3)" in _src,
      "写成 != 1 会把 INVALID 误判成 aware，导致根本不去调 SetProcessDpiAwareness")

print("\n" + "=" * 92)
print("T3 · _physical_rect 反算（mock DPI=144 即 150%）")
print("=" * 92)

cap = C.Capturer.__new__(C.Capturer)      # 不跑 __init__，只借方法
cap.window_dpi = lambda hwnd: 144        # mock：150% DPI


class FakeRect(tuple):
    pass


# 分支 1：进程 aware -> 不缩放
C._DPI_AWARE = True
r, s = C.Capturer._physical_rect(cap, (100, 200, 1706, 1066), 999)
check("aware=True -> 原样返回，不重复缩放", r == (100, 200, 1706, 1066) and s == 1.0,
      f"{r} scale={s}")

# 分支 2：进程 unaware + 150% -> 乘 1.5
C._DPI_AWARE = False
r, s = C.Capturer._physical_rect(cap, (100, 200, 1706, 1066), 999)
check("unaware + 150% -> scale=1.5", abs(s - 1.5) < 1e-9, f"scale={s}")
check("unaware + 150% -> 1706x1066 还原成 2559x1599",
      r == (150, 300, 2559, 1599), str(r))

# 分支 3：unaware 但 DPI=96（缩放 1.0）-> 不动
cap.window_dpi = lambda hwnd: 96
r, s = C.Capturer._physical_rect(cap, (10, 20, 800, 600), 999)
check("unaware + 96dpi(缩放1) -> 不缩放", r == (10, 20, 800, 600) and s == 1.0,
      f"{r} scale={s}")

# 分支 4：unaware + 100% DPI 边界
cap.window_dpi = lambda hwnd: 96
check("scale 恰为 1.0 时走短路（不四舍五入）",
      C.Capturer._physical_rect(cap, (0, 0, 100, 100), 1)[0] == (0, 0, 100, 100))

# 分支 5：unaware + 125%
cap.window_dpi = lambda hwnd: 120
r, s = C.Capturer._physical_rect(cap, (0, 0, 1000, 800), 1)
check("unaware + 125% -> scale=1.25 且尺寸正确",
      abs(s - 1.25) < 1e-9 and r == (0, 0, 1250, 1000), f"{r} scale={s}")

# 分支 6：window_dpi 是 @staticmethod，读不到时兜底返回 96（不炸）
_dpi = C.Capturer.window_dpi(0)          # 静态方法，不传 self
check("window_dpi 返回 > 0 的真实 DPI", _dpi > 0, str(_dpi))
check("window_dpi 对任意 hwnd 都不抛异常",
      isinstance(C.Capturer.window_dpi(0x7FFFFFFF), int))
C._DPI_AWARE = True                     # 还原默认（本进程 import 后确实是 aware）

print("\n" + "=" * 92)
print(f"结果: {len(PASS)}/{len(PASS) + len(FAIL)} 通过")
if FAIL:
    print("  失败: " + ", ".join(FAIL))
print("=" * 92)
sys.exit(1 if FAIL else 0)
