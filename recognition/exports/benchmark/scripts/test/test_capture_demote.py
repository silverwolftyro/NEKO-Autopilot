"""截图通道降级路径的单测（2026-10-04 · dxcam 瞬时空帧 bug）。

★ 修的是什么 bug
-----------------
``_grab_dxcam`` 里写明「dxcam 会**间歇性**返回 None，**单次空帧是正常的**，
只有连续 ``EMPTY_FRAME_THRESHOLD`` 次才判定该通道不可用」。但外层
``grab()`` 收到 ``None`` 就 ``raise CaptureError("截图为空")``，
立刻走 ``_demote_channel()`` —— 而 ``Capturer`` **没有任何回退路径**。

结果：一次瞬时空帧 = 永久降级。真机 60s 跑到 t=38.2s 降级，此后 22s
全程 mss，而 ``dxcam 空帧峰值 0``（那一帧压根没被统计到）。

修复：空帧**留在本通道重试**，不降级；连续空到阈值才降级。

★ 为什么不依赖真实 dxcam
-----------------------
本机没有原神窗口，dxcam/mss 都拿不到真画面。这里直接 mock
``_grab_once`` 喂预设的空/非空序列，只验证**控制流**：
空几次 -> 还剩什么通道。这正是 bug 所在。
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

import numpy as np  # noqa: E402

from autopilot.perception.capturer import (  # noqa: E402
    Capturer, CaptureChannel, CaptureError, WindowNotFoundError)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f"   {detail}" if detail else ""))


def make_capturer(empty_then_ok: int):
    """造一个只吃 mock 的 Capturer：前 N 次返回 None，之后返回有效图。

    不调 ``open()``（那会去找真窗口），直接塞 ``_active``。
    """
    cap = Capturer.__new__(Capturer)          # 绕过 __init__ 的窗口查找
    cap._title = "mock"
    cap._order = [CaptureChannel.DXCAM, CaptureChannel.MSS]
    cap._fail_threshold = 3                   # 默认值，故意小
    cap._allow_fallback_fullscreen = False
    cap._force_channel = None
    cap._capture_lock = __import__("threading").RLock()
    cap._hwnd = 1
    cap._win_rect = (0, 0, 64, 64)
    cap._active = CaptureChannel.DXCAM
    cap._dxcam_out = object()
    cap._mss_inst = object()
    cap._frame_seq = 0
    cap._consecutive_fail = 0
    cap._empty_streak = 0
    cap._closed = False

    state = {"n": 0, "alive": True}

    def _fake_grab_once():
        # ★ 必须尊重窗口存活状态：真环境下窗口没了，_grab_once 会失败。
        #   mock 若无视它，「窗口消失」用例会假通过（抓到帧了却以为该抛错）。
        if not state["alive"]:
            raise WindowNotFoundError("mock: 窗口没了")
        state["n"] += 1
        if state["n"] <= empty_then_ok:
            cap._empty_streak += 1      # 复刻 _grab_dxcam 的记账
            return None
        cap._empty_streak = 0
        return np.zeros((64, 64, 3), dtype=np.uint8)

    cap._grab_once = _fake_grab_once

    def _refresh(force=False):
        return state["alive"]
    cap._refresh_window = _refresh
    cap._demote_channel = lambda: False               # 不许降级，好观察重试
    return cap, state


print("=" * 92)
print("T1 · 单次/少量瞬时空帧：必须留在原通道，不得降级")
print("=" * 92)
for n_empty in (1, 2, 3, 4, 5, 6, 7):
    cap, st = make_capturer(n_empty)
    try:
        fr = cap.grab()
        ok = fr is not None and fr.channel is CaptureChannel.DXCAM
        check(f"  {n_empty} 次空帧后仍拿到 dxcam 帧（不降级）", ok,
              f"channel={fr.channel.value if fr else None} "
              f"抓取尝试 {st['n']} 次")
    except Exception as exc:  # noqa: BLE001
        check(f"  {n_empty} 次空帧后仍拿到 dxcam 帧（不降级）", False,
              f"{type(exc).__name__}: {exc}")

print()
print("=" * 92)
print("T2 · 空帧重试**不消耗** fail_threshold 预算")
print("=" * 92)
cap, st = make_capturer(7)
try:
    cap.grab()
    check("  fail_threshold=3，但 7 次空帧仍成功（空帧有独立预算）",
          True, f"总共尝试 {st['n']} 次")
except Exception as exc:  # noqa: BLE001
    check("  fail_threshold=3，但 7 次空帧仍成功（空帧有独立预算）", False,
          f"{type(exc).__name__}: {exc}")

print()
print("=" * 92)
print("T3 · 连续空到阈值：这时才该失败（通道真的坏了）")
print("=" * 92)
cap, st = make_capturer(999)
try:
    cap.grab()
    check("  一直空 -> 抛 CaptureError 而不是死循环", False, "居然成功了")
except WindowNotFoundError as exc:
    check("  一直空 -> 抛 CaptureError（窗口一直在，不该报窗口消失）", False,
          str(exc))
except CaptureError as exc:
    # ⚠️ 必须检查错误**来源**：如果报的是 AttributeError 之类，
    #   说明是 mock 缺属性炸的，不是「重试到上限」这条正常路径 ——
    #   那种情况下这条断言会假通过。
    _msg = str(exc)
    _real = ("重试" in _msg) or ("返回空" in _msg)
    check("  一直空 -> 抛 CaptureError 而不是死循环", _real,
          f"尝试 {st['n']} 次后抛出：{_msg[:70]}")

print()
print("=" * 92)
print("T4 · 空帧不误伤 _consecutive_fail（窗口判定独立）")
print("=" * 92)
cap, st = make_capturer(5)
try:
    cap.grab()
    check("  5 次空帧后 _consecutive_fail 仍为 0（那是窗口的问题，不是空帧的）",
          cap._consecutive_fail == 0, f"={cap._consecutive_fail}")
except Exception as exc:  # noqa: BLE001
    check("  5 次空帧后 _consecutive_fail 仍为 0", False,
          f"{type(exc).__name__}: {exc}")

print()
print("=" * 92)
print("T5 · 回归：窗口真的没了 -> 仍走 WindowNotFoundError")
print("=" * 92)
# ⚠️ 捕获顺序说明：``WindowNotFoundError`` 是 ``CaptureError`` 的子类，
#    而 ``grab()`` 里 ``except WindowNotFoundError: raise`` 排在
#    ``except CaptureError`` **之前** —— 所以窗口消失会原样透出。
#    这里必须先接子类再接父类，否则断言永远落在父类分支上。
cap, st = make_capturer(2)
st["alive"] = False                        # 窗口消失
try:
    cap.grab()
    check("  窗口消失 -> WindowNotFoundError", False, "居然成功了")
except WindowNotFoundError:
    check("  窗口消失 -> WindowNotFoundError（原样透出，不被降级吞掉）", True)
except CaptureError as exc:
    check("  窗口消失 -> WindowNotFoundError（原样透出，不被降级吞掉）", False,
          f"被降级成了父类: {type(exc).__name__}: {exc}")

# 中途消失：先空 3 帧（走空帧重试分支），再窗口没了。
# 这条专盯「空帧重试循环里有没有正确传播窗口死亡」——
# 空帧分支自己也会调 _refresh_window，很容易漏掉这条路径。
cap2, st2 = make_capturer(3)
_orig = cap2._grab_once


def _die_after_3(st=st2, f=_orig):
    if st["n"] >= 3:
        st["alive"] = False
    return f()


cap2._grab_once = lambda: _die_after_3()
try:
    cap2.grab()
    check("  空帧重试途中窗口消失 -> 仍抛 WindowNotFoundError", False,
          "居然成功了")
except WindowNotFoundError:
    check("  空帧重试途中窗口消失 -> 仍抛 WindowNotFoundError", True)
except CaptureError as exc:
    check("  空帧重试途中窗口消失 -> 仍抛 WindowNotFoundError", False,
          f"被降级成了父类: {type(exc).__name__}: {exc}")

print()
print("=" * 92)
print(f"结果: {len(PASS)}/{len(PASS) + len(FAIL)} 通过")
print("=" * 92)
if FAIL:
    for f in FAIL:
        print(f"  [FAIL] {f}")
sys.exit(1 if FAIL else 0)
