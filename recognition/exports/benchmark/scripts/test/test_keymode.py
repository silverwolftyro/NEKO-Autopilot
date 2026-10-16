"""SCANCODE 注入模式的单测（2026-10-04 · Shift+W 只走不跑）。

★ 背景
--------
真机症状：``手动 Shift+W 跑步、脚本注入 Shift+W 只走路``。
代码排查确认 ``actuator/simulator.py`` 的 ``_mk_key`` 是**纯 VK 注入**：

    wVk=vk & 0xFFFF, wScan=0, dwFlags=KEYEVENTF_KEYUP|EXTENDEDKEY

``dwFlags`` 里**没有** ``KEYEVENTF_SCANCODE``（常量在第 43 行定义了却从没用过），
全文件搜不到 ``MapVirtualKey``。也就是说系统收到的是「程序合成的虚拟键码事件」，
而真实硬件走的是扫描码路径（物理键 set-1 扫描码 -> i8042/PS2 -> 系统翻译成 VK）。
带反作弊/自定义输入层的游戏可以区分两者。

本文件验证新增的三种模式（``vk`` / ``scancode`` / ``both``）构造是否正确。

★ 特别盯住的一个坑（本轮真踩过）
--------------------------------
``EXTENDED_KEYS`` 那个集合是 **VK 侧**启发式（BetterGI 留下的），
描述「哪些 VK 要配 KEYEVENTF_EXTENDEDKEY 才能用」。**不能**在扫描码模式下复用它：
本机实测 ``MapVirtualKeyEx(VK_SHIFT, MAPVK_VK_TO_EXSCANCODE) = 0x0000002A``，
高字节是 ``0x00`` 而**不是** ``0xE0`` —— LSHIFT 没有 E0 前缀。
误加 EXTENDEDKEY 等于告诉系统「这个键带 E0 前缀」，Shift 会直接按不动。

★ 断言风格
----------
零 ``def test_``（本仓库 conftest.py 不生效，自带 runner 直接跑）。
区间/包含关系一律**直接判不等式**，不做「按步长采样后查列表」——
用户明确反对后者（采样点之间的值必然查不到）。
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

import ctypes
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:                                   # pragma: no cover
    pass

ROOT = Path(PLUGIN_DIR)
sys.path.insert(0, str(ROOT))

from autopilot.actuator import simulator as S  # noqa: E402
from autopilot.actuator.simulator import (  # noqa: E402
    KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE,
    KEY_MODE_BOTH, KEY_MODE_SCANCODE, KEY_MODE_VK,
    INPUT, DryRunBackend, InputInjectionError, InputSimulator, InvalidStateError,
    VK, _mk_key, _mk_keys, _mk_scan_key, decode_events,
)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f"   {detail}" if detail else ""))


def ki(ev: INPUT):
    return ev.union.ki


# ======================================================================
# 1. VK 模式必须**字节级不变**（回归保护）
# ======================================================================
print("\n=== 1. VK 模式字节级不变（回归保护）===")
e = ki(_mk_key(int(VK.W), up=False))
check("vk 模式 wVk=0x57", e.wVk == 0x57, f"wVk=0x{e.wVk:02X}")
check("vk 模式 wScan=0", e.wScan == 0, f"wScan=0x{e.wScan:02X}")
check("vk 模式无 SCANCODE 标志", not (e.dwFlags & KEYEVENTF_SCANCODE),
      f"dwFlags=0x{e.dwFlags:04X}")

e = ki(_mk_key(int(VK.W), up=True))
check("vk 模式 keyup 纯 KEYUP", e.dwFlags == KEYEVENTF_KEYUP,
      f"dwFlags=0x{e.dwFlags:04X}")

# Shift 在 EXTENDED_KEYS 里（0x10），历史行为就是加 EXTENDEDKEY
e = ki(_mk_key(int(VK.SHIFT), up=False))
check("vk 模式 Shift 保留 EXTENDEDKEY（历史行为）",
      bool(e.dwFlags & KEYEVENTF_EXTENDEDKEY), f"dwFlags=0x{e.dwFlags:04X}")

# ======================================================================
# 2. SCANCODE 模式的字段
# ======================================================================
print("\n=== 2. SCANCODE 模式字段 ===")
scan, ext = S._vk_to_scancode(int(VK.W))
check("VK_W 扫描码 = 0x11", scan == 0x11, f"scan=0x{scan:02X}")
check("VK_W **非**扩展键", ext is False, f"ext={ext}")

e = ki(_mk_scan_key(int(VK.W), up=False))
check("scancode wVk=0（规范要求）", e.wVk == 0, f"wVk={e.wVk}")
check("scancode wScan=0x11", e.wScan == 0x11, f"wScan=0x{e.wScan:02X}")
check("scancode 带 SCANCODE 标志", bool(e.dwFlags & KEYEVENTF_SCANCODE),
      f"dwFlags=0x{e.dwFlags:04X}")
check("scancode 无 EXTENDEDKEY（W 非扩展）",
      not (e.dwFlags & KEYEVENTF_EXTENDEDKEY), f"dwFlags=0x{e.dwFlags:04X}")

e = ki(_mk_scan_key(int(VK.W), up=True))
check("scancode keyup = SCANCODE|KEYUP",
      e.dwFlags == (KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP),
      f"dwFlags=0x{e.dwFlags:04X}")

# ★ 本轮最关键的实测：Shift 走扫描码时**没有** E0 前缀
scan_sh, ext_sh = S._vk_to_scancode(int(VK.SHIFT))
check("VK_SHIFT 扫描码 = 0x2A", scan_sh == 0x2A, f"scan=0x{scan_sh:02X}")
check("VK_SHIFT 走扫描码时 ext=False（不能误加 EXTENDEDKEY）",
      ext_sh is False, f"ext={ext_sh}")
e = ki(_mk_scan_key(int(VK.SHIFT), up=False))
check("scancode Shift 无 EXTENDEDKEY",
      not (e.dwFlags & KEYEVENTF_EXTENDEDKEY), f"dwFlags=0x{e.dwFlags:04X}")
check("scancode Shift wScan=0x2A", e.wScan == 0x2A, f"wScan=0x{e.wScan:02X}")

# ======================================================================
# 3. 三种模式的事件条数
# ======================================================================
print("\n=== 3. 三种模式事件条数 ===")
check("_mk_keys(vk) -> 1 条", len(_mk_keys(int(VK.W), up=False, mode=KEY_MODE_VK)) == 1)
check("_mk_keys(scancode) -> 1 条",
      len(_mk_keys(int(VK.W), up=False, mode=KEY_MODE_SCANCODE)) == 1)
check("_mk_keys(both) -> 2 条", len(_mk_keys(int(VK.W), up=False, mode=KEY_MODE_BOTH)) == 2)

both = _mk_keys(int(VK.W), up=False, mode=KEY_MODE_BOTH)
check("both 第一条是 VK 模式（wVk != 0）", ki(both[0]).wVk == 0x57)
check("both 第二条是扫描码模式（wVk == 0）", ki(both[1]).wVk == 0)
check("both 第二条带 SCANCODE", bool(ki(both[1]).dwFlags & KEYEVENTF_SCANCODE))

try:
    _mk_keys(int(VK.W), up=False, mode="不存在的模式")
    check("未知模式响亮失败", False, "居然没抛")
except InputInjectionError:
    check("未知模式响亮失败", True, "抛 InputInjectionError")

# ======================================================================
# 4. decode_events 必须把 wVk=0 的扫描码事件**还原**成 VK
#    （不还原 -> DryRunBackend 跳过 -> 干跑完全看不到按键）
# ======================================================================
print("\n=== 4. decode_events 还原 VK（否则干跑瞎）===")
evs = decode_events(_mk_keys(int(VK.W), up=False, mode=KEY_MODE_SCANCODE))
check("scancode 事件被解码成 1 条", len(evs) == 1, f"n={len(evs)}")
check("scancode 事件 vk 还原成 0x57", evs[0].vk == 0x57, f"vk=0x{evs[0].vk:02X}")
check("scancode 事件 down=True", evs[0].down is True)

evs = decode_events(_mk_keys(int(VK.SHIFT), up=True, mode=KEY_MODE_SCANCODE))
check("scancode keyup 解码 down=False", evs[0].down is False)
check("scancode keyup vk 还原成 VK_SHIFT", evs[0].vk == int(VK.SHIFT),
      f"vk=0x{evs[0].vk:02X}")

# ★ 变异测试：把还原逻辑拆掉，本断言必须 FAIL
_orig = S._scancode_to_vk
S._scancode_to_vk = lambda scan: 0
try:
    evs = decode_events(_mk_keys(int(VK.W), up=False, mode=KEY_MODE_SCANCODE))
    check("★变异测试：拆掉还原后 vk 变 0（证明断言非形同虚设）",
          evs[0].vk == 0, f"vk=0x{evs[0].vk:02X}")
finally:
    S._scancode_to_vk = _orig

# ======================================================================
# 5. DryRunBackend 在 scancode 模式下能跟踪按键 + release_all 兜底
# ======================================================================
print("\n=== 5. DryRun 在 scancode 模式下的按键记账 ===")
for mode in (KEY_MODE_VK, KEY_MODE_SCANCODE, KEY_MODE_BOTH):
    bk = DryRunBackend()
    sim = InputSimulator(backend=bk, key_mode=mode)
    sim.key_down(VK.SHIFT)
    sim.key_down(VK.W)
    d1 = sim.is_key_down(VK.SHIFT)
    d2 = sim.is_key_down(VK.W)
    sim.release_all()
    check(f"[{mode}] 按下后 is_key_down=True",
          d1 and d2, f"shift={d1} w={d2}")
    check(f"[{mode}] release_all 后全部松开",
          not sim.is_key_down(VK.SHIFT) and not sim.is_key_down(VK.W))

# ======================================================================
# 6. key_combo 原子性在三种模式下都保持
# ======================================================================
print("\n=== 6. key_combo 原子性 ===")
# 一个 modifier:  SHIFT↓ W↓ W↑ SHIFT↑  -> 4 条逻辑事件。
# 注意 `both` 模式下每条**逻辑**事件展开成 2 条物理事件（VK + 扫描码），
# 所以「双发」时序看起来是 SHIFT↓ SHIFT↓ W↓ W↓ ... —— 这正是设计意图，
# 不是 bug。断言要看**去重后的逻辑时序**，不是原始物理序列。
_WANT_SEQ = [("SHIFT", True), ("W", True), ("W", False), ("SHIFT", False)]
for mode, mult in ((KEY_MODE_VK, 1), (KEY_MODE_SCANCODE, 1), (KEY_MODE_BOTH, 2)):
    bk = DryRunBackend()
    sim = InputSimulator(backend=bk, key_mode=mode)
    sim.key_combo([VK.SHIFT], VK.W)
    raw = [(ev.name, ev.down) for ev in bk.events]
    # 折叠连续重复（双发模式每条逻辑事件发两次）
    logical = [r for i, r in enumerate(raw) if i == 0 or r != raw[i - 1]]
    check(f"[{mode}] combo 逻辑时序 SHIFT↓W↓W↑SHIFT↑",
          logical == _WANT_SEQ, f"{logical}")
    check(f"[{mode}] combo 事件数 = 4 x {mult}", len(bk.events) == 4 * mult,
          f"n={len(bk.events)}")

# ======================================================================
# 7. key_mode 非法值必须在**构造期**就炸
# ======================================================================
print("\n=== 7. key_mode 校验 ===")
try:
    InputSimulator(backend=DryRunBackend(), key_mode="乱写")
    check("非法 key_mode 构造期就炸", False, "居然构造成功")
except InvalidStateError:
    check("非法 key_mode 构造期就炸", True, "抛 InvalidStateError")

sim = InputSimulator(backend=DryRunBackend(), key_mode=KEY_MODE_SCANCODE)
check("默认仍是 VK 模式（不改历史行为）",
      InputSimulator(backend=DryRunBackend()).key_mode == KEY_MODE_VK)

# ======================================================================
# 8. 兜底表对着**真实** MapVirtualKey 逐键核对
#    （防手抄的表悄悄过期）
# ======================================================================
print("\n=== 8. 兜底表 vs 真实 MapVirtualKey ===")
if S.IS_WINDOWS:
    u = ctypes.windll.user32
    u.MapVirtualKeyW.restype = ctypes.c_uint
    bad = []
    for sc, vk_fb in S._SCAN_TO_VK_FALLBACK.items():
        real = int(u.MapVirtualKeyW(sc, S.MAPVK_SCANCODE_TO_VK))
        if real != vk_fb:
            bad.append(f"sc=0x{sc:02X}: 表里 VK=0x{vk_fb:02X} 实际 0x{real:02X}")
    check(f"兜底表 {len(S._SCAN_TO_VK_FALLBACK)} 项全部与真实 API 一致",
          not bad, "; ".join(bad) if bad else "")

    # 反向：表里每个 **VK（键）** 都能由 MapVirtualKeyEx 拿到同样的扫描码（值）
    # ★ 键是 VK、值是扫描码 —— 传反了会把扫描码当 VK 喂给 API，
    #   报出一堆莫名其妙的「不一致」，那是**测试自己**的 bug 不是表的 bug。
    bad2 = []
    for vk, sc_fb in S._VK_TO_SCAN_FALLBACK.items():
        raw = int(u.MapVirtualKeyExW(vk, S.MAPVK_VK_TO_EXSCANCODE, 0))
        if (raw & 0xFF) != sc_fb:
            bad2.append(f"VK=0x{vk:02X}: 表里 0x{sc_fb:02X} 实际 0x{raw & 0xFF:02X}")
    check("反向表也全部与真实 API 一致", not bad2, "; ".join(bad2) if bad2 else "")

    # ★ 再验一次：Shift 走扫描码**没有** E0 前缀（扩展键判据的事实基础）
    raw = int(u.MapVirtualKeyExW(int(VK.SHIFT), S.MAPVK_VK_TO_EXSCANCODE, 0))
    check("★事实复核：VK_SHIFT 的 EXSCANCODE 高字节 != 0xE0",
          ((raw >> 24) & 0xFF) != 0xE0, f"0x{raw:08X}")
else:
    print("  [SKIP] 非 Windows，跳过真实 API 核对")

# ======================================================================
print()
print("=" * 70)
print(f"  PASS={len(PASS)}  FAIL={len(FAIL)}")
if FAIL:
    for n in FAIL:
        print(f"    !! {n}")
print("=" * 70)
raise SystemExit(1 if FAIL else 0)
