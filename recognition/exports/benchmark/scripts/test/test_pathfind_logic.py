"""e2e_pathfind.py 的逻辑单测（§46 五处真机问题 + §51 A~H）。

★ 为什么不直接跑 e2e_pathfind.py
--------------------------------
它要真窗口 + 真游戏。本文件用**假 Capturer + 脚本化位置**驱动
``main()`` 走完整循环，只验证决策逻辑与按键序列，不发任何真实输入。

★★ 两条踩过血的规矩
--------------------
1. ``main()`` 里是**函数内延迟 import**::

       from autopilot.perception.localization_service import LocalizationService
       from autopilot.actuator.simulator import InputSimulator

   所以必须 patch **源模块的属性**（``LS.LocalizationService = ...``），
   patch 脚本自己的名字（``mod.LocalizationService = ...``）**完全无效** ——
   那是本文件第一版全部断言失败的原因。

2. ★★ 绝不能 ``super().key_down(vk)``
   ----------------------------------
   上一版 harness 就是这么写的，而 ``Win32Backend.send`` 是**真的
   ``user32.SendInput``** —— 也就是说跑一次单测就会往当前前台窗口
   注入真实的 Shift+W。``InputSimulator`` 构造时用
   ``Win32Backend()``，所以 ``--live`` 分支下必然走真实注入。

   现在做两层防护：
     ① ``_Sim.__init__`` 构造完立刻把 backend 换成 ``DryRunBackend``
        （``probe_backend`` 对 DryRun 直接短路返回 ok，不碰 user32）
     ② 把 ``Win32Backend.send`` 换成会抛异常的哨兵 —— 万一路径哪天
        绕过了 ①，单测当场炸，而不是静默地往你机器里敲键盘

3. 所有 run() 一律传 ``--delay 0``。默认 8s，而本文件要跑十几次
   main()，不关就是几分钟。
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

import contextlib
import glob
import importlib.util
import io
import math
import sys
import time
from pathlib import Path

import cv2

ROOT = Path(PLUGIN_DIR)
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCRIPT = Path(RECOG_ROOT / 'exports' / 'benchmark' / 'scripts' / 'misc' / 'e2e_pathfind.py')
_spec = importlib.util.spec_from_file_location("_pathfind", SCRIPT)
pf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pf)          # 只改 sys.path，安全

import autopilot.actuator.simulator as SIM                            # noqa: E402
import autopilot.perception.capturer as CAP                           # noqa: E402
import autopilot.perception.localization_service as LS                # noqa: E402
from autopilot.actuator.simulator import DryRunBackend, InputSimulator  # noqa: E402
from autopilot.perception.capturer import (                          # noqa: E402
    CaptureChannel, CapturedFrame,
)
from autopilot.perception.localization_service import (                # noqa: E402
    LocalizationState, LocalizationStateMachine,
)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f"   {detail}" if detail else ""))


def section(t):
    print("\n" + "=" * 92 + f"\n  {t}\n" + "=" * 92)


# ---------------------------------------------------------------- 假环境
_frames = [cv2.imread(f) for f in sorted(glob.glob(
    RECOG_ROOT / 'exports' / 'benchmark' / 'diagnosis' / 'smoke_frame_*.png'))]
_frames = [f for f in _frames if f is not None]
if not _frames:
    print("✗ 找不到诊断帧，本文件无法运行。先跑 smoke_end_to_end.py 落盘。")
    raise SystemExit(2)


class _FakeCap:
    """假抓屏器。``grab()`` 每次返回一张诊断帧。

    ★ 它只提供 ``open/close/get_active_channel/grab/window_rect`` ——
      脚本若在别处用到别的属性就会 AttributeError。这是**故意的**：
      上一次重写把限幅变量误命名成 ``cap``（顶掉真 Capturer），
      正是靠这里的 AttributeError 才暴露出来。
    """

    window_rect = (0, 0, _frames[0].shape[1], _frames[0].shape[0])
    closed = 0

    def __init__(self, *a, **k):
        self.n = 0

    def open(self):
        pass

    def close(self):
        type(self).closed += 1

    def get_active_channel(self):
        return CaptureChannel.MSS

    def grab(self):
        img = _frames[self.n % len(_frames)]
        self.n += 1
        return CapturedFrame(image=img, rect=self.window_rect,
                             timestamp_ms=int(time.time() * 1000),
                             frame_seq=self.n, channel=CaptureChannel.MSS)


def make_svc(script, start=(0.0, 0.0)):
    """返回一个 LocalizationService 子类：按 script 逐帧喂位移。

    script 元素可以是标量（沿 +X）或 (dx, dz) 元组。
    """

    class _S(LS.LocalizationService):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.k = 0
            self.pos = start
            self.sc = list(script)

        def on_frame(self, img, ts):
            self.k += 1
            mv = self.sc.pop(0) if self.sc else 0.0
            if isinstance(mv, tuple):
                self.pos = (self.pos[0] + mv[0], self.pos[1] + mv[1])
            else:
                self.pos = (self.pos[0] + mv, self.pos[1])
            return LocalizationState(state=LocalizationStateMachine.TRACKING,
                                     world_pos=self.pos, is_world=True,
                                     scene_changed=False)

        def get_position(self):
            return self.pos

    return _S


class _Sim(InputSimulator):
    """只记账，不注入。★ 绝不调 ``super().key_*``。"""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.backend = DryRunBackend()      # ① 换掉 Win32Backend

    def key_down(s, vk):
        KEYS.append(("down", int(vk)))

    def key_up(s, vk):
        KEYS.append(("up", int(vk)))

    def key_combo(s, mods, key):
        COMBOS.append(([int(x) for x in mods], int(key)))

    def key_press(s, vk, hold_ms=0):
        KEYS.append(("press", int(vk)))
        time.sleep(min(hold_ms, 0) / 1000.0)   # 不真的等

    def mouse_move_relative(s, dx, dy):
        MOUSE.append(int(dx))


KEYS, MOUSE, COMBOS = [], [], []


def run(script, argv, start=(0.0, 0.0)):
    """跑一次 main()，返回 (stdout+stderr 文本, 按键序列, 鼠标, 组合键)。

    ★ stderr 也一并收：脚本把「目标太近」这类**错误**走 stderr、
      把横幅走 stdout。测试不该反过来 dictate 脚本用哪个流。
    """
    KEYS.clear(); MOUSE.clear(); COMBOS.clear()
    CAP.Capturer = _FakeCap
    SIM.InputSimulator = _Sim
    LS.LocalizationService = make_svc(script, start)
    old = sys.argv
    buf = io.StringIO()
    try:
        sys.argv = argv
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            pf.main()
    finally:
        sys.argv = old
    return buf.getvalue(), list(KEYS), list(MOUSE), list(COMBOS)


def argv_of(target="500,0", start="0,0", seconds="5", live=True, extra=()):
    """统一构造 argv：一律带 --delay 0（见模块 docstring 第 3 条）。"""
    a = ["x", "--target-world", target, "--start-world", start,
         "--seconds", seconds, "--delay", "0"]
    if live:
        a.append("--live")
    return a + list(extra)


SHIFT_VK, W_VK = 0x10, 0x57

# ★ 哨兵 ②：任何真实注入当场炸。
_real_send = SIM.Win32Backend.send
_real_user32 = SIM._user32


def _trap_send(self, inputs):
    raise AssertionError(
        f"单测里出现了真实 SendInput（{len(inputs)} 个事件）—— "
        f"harness 失效，这会往你机器的前台窗口敲键盘")


class _SafeU32:
    """让 F12 监听线程**正常轮询**、同时记录任何 SendInput 的假件。

    ★ 不能把它做成「一调用就炸」的陷阱 —— ``_PanicWatcher._poll`` 每
      轮都 import 并调用 ``_user32()``，炸掉的话监听线程会 ``_fail()``，
      ``panic.start()`` 返回错误，**每一次 run() 都会在「F12 急停不可用」
      处 return 2**，整个测试静默失真。
    """

    def __init__(self):
        self.calls = 0
        self.sent = []

    def GetAsyncKeyState(self, vk):
        self.calls += 1
        return 0                      # 永远「没按 F12」

    def SendInput(self, n, arr, sz):
        self.sent.append(n)           # 记下来，汇总处断言恒空
        return 0                      # 谎报 0 个成功，触发 raise_on_mismatch


_safe = _SafeU32()

try:
    SIM.Win32Backend.send = _trap_send     # 哨兵 ②：一网打尽
    SIM._user32 = lambda: _safe            # 哨兵 ①：只记账不注入

    # ★ §48 之后 ``main()`` 开头就有 ``require_admin()`` 门禁。本文件在
    #   普通（非管理员）shell 里跑，不 stub 的话每次都会在第一行
    #   ``return 2``，**所有 run() 断言静默全灭**。这里只 stub 掉
    #   环境相关的三样，决策逻辑照跑不误。
    _real_admin = pf._uipi_require_admin
    _admin_calls = []
    pf._uipi_require_admin = lambda live: (
        _admin_calls.append(live), 0)[1]
    pf._uipi_check = lambda live: None          # 纯诊断，打印即可
    pf._probe_channels = lambda rect: []        # 通道探针要真窗口

    # ============================================================ 改动 1
    section("改动 1 · 方向用滑动窗口，不用单帧")
    check("WINDOW_N = 10 帧", pf.WINDOW_N == 10, str(pf.WINDOW_N))
    check("窗口位移门槛 = 1.0 世界单位", pf.WINDOW_MOVE_MIN == 1.0,
          str(pf.WINDOW_MOVE_MIN))
    out, _, _, _ = run([(1.2, 0.0)] * 30,
                       argv_of(seconds="4"))
    # 前 10 帧窗口没攒满 -> 不该有 θ_m；攒满后才有
    # ⚠️ 过滤**不能**用 `"---" not in l`：turn 字段在 Δ 落在死区内时也是 `---`，
    #    会把「已算出方向但不需要转」的帧一起滤掉（第一版就栽在这）。
    pre = [l for l in out.splitlines() if "θ_m=  ---" in l]
    post = [l for l in out.splitlines() if "θ_m=" in l and "θ_m=  ---" not in l]
    check("窗口攒满前不给方向（θ_m = ---）", len(pre) >= 8, f"{len(pre)} 帧")
    check("窗口攒满后开始给方向", len(post) > 0, f"{len(post)} 帧")
    check("日志打印的是 |win|（窗口位移）而非 |d|（单帧）",
          "|win|=" in out and "|d|=" not in out)
    check("★ 抓屏器在正常结束时被 close（没被限幅变量顶掉）",
          _FakeCap.closed > 0, f"closed={_FakeCap.closed}")

    # ============================================================ 改动 2
    section("改动 2 · Shift + W 跑步（原子组合 + 修饰键优先）")
    out, keys, _, combos = run([(1.2, 0.0)] * 30, argv_of(seconds="3"))
    check("★ 先用 key_combo 原子建立 Shift+W 组合键",
          combos and combos[0] == ([SHIFT_VK], W_VK), str(combos))
    check("key_combo 只调用一次（不是每帧）", len(combos) == 1, str(combos))
    i_w = next((i for i, (a, v) in enumerate(keys)
                if a == "down" and v == W_VK), -1)
    i_s = next((i for i, (a, v) in enumerate(keys)
                if a == "down" and v == SHIFT_VK), -1)
    check("W 已按下 (0x57)", i_w >= 0, str(keys[:4]))
    check("★ 持续按住阶段 Shift 在 W **之前**", 0 <= i_s < i_w,
          f"shift@{i_s} w@{i_w}")
    check("退出时 W 被松开", ("up", W_VK) in keys)
    check("退出时 Shift 被松开", ("up", SHIFT_VK) in keys)
    check("W 只按一次（长按不重复 down）",
          sum(1 for a, v in keys if a == "down" and v == W_VK) == 1)
    check("Shift 只按一次",
          sum(1 for a, v in keys if a == "down" and v == SHIFT_VK) == 1)
    _, k2, _, c2 = run([(1.2, 0.0)] * 30,
                       argv_of(seconds="3", extra=["--walk"]))
    check("★ --walk 时完全不发 Shift（只按 W）",
          not c2 and not any(v == SHIFT_VK for _, v in k2), str(k2[:4]))

    # ============================================================ 转向律
    section("转向律 · 误差比例控制（取代分档绝对增益）")
    check("★ 比例系数 TURN_FRACTION = 0.45", pf.TURN_FRACTION == 0.45,
          str(pf.TURN_FRACTION))
    check("  限幅按**度数**：±45° / 大误差 ±60° / 90° 触发",
          (pf.TURN_MAX_DEG, pf.TURN_MAX_DEG_BIG, pf.TURN_BIG_ERR_DEG)
          == (45.0, 60.0, 90.0))
    check("  死区 = 8°（旧 5°）", pf.TURN_DEADZONE_DEG == 8.0)
    check("★ MOUSE_DEG_PER_PX = 0.07（2026-10-04 真机标定，非猜测值）",
          pf.MOUSE_DEG_PER_PX == 0.07, str(pf.MOUSE_DEG_PER_PX))
    # 死区 8°，但**像素**发多少？|Δ|=45° -> 0.45*45/0.07 = 289px
    w45, _c45, px45 = pf._turn_plan(45.0)
    check("  |Δ|=45° -> 计划转 20.25°（反号，向目标收敛）",
          abs(w45 + 20.25) < 1e-9 and px45 < 0, f"{w45:.2f}° / {px45:.1f}px")
    check("★ |Δ|=45° -> 下发 289px（旧假定 1°/px 时只有 20px，慢 15 倍）",
          abs(abs(px45) - 289.286) < 0.01, f"{px45:.1f}px")
    _w177, _c177, _px177 = pf._turn_plan(177.0)
    check("  |Δ|=177°（起步背对）-> 命中大误差限幅 60° = 857px",
          _c177 == 60.0 and abs(abs(_px177) - 857.14) < 0.1,
          f"{_px177:.0f}px")
    # 想转角度 = -0.45 * Δ，符号与 Δ 相反
    w30, _, _ = pf._turn_plan(30.0)
    check("  _turn_plan(+30°) 想转负角（收敛方向）", w30 < 0, f"{w30:.1f}°")
    check("  _turn_plan(+30°) 幅度 = 0.45*30 = 13.5°", abs(w30 + 13.5) < 1e-9,
          f"{w30:.2f}")
    check("★ 对称性：_turn_plan(-d) = -_turn_plan(d)（量级）",
          abs(pf._turn_plan(-30.0)[0] + w30) < 1e-9)
    # ★ 限幅确实生效，且按度数不是像素
    w200, cap200, px200 = pf._turn_plan(200.0)
    check("★ |Δ|=200° 被限到 ±60°（旧代码是 500px = 500°）",
          abs(w200) == 60.0 and cap200 == 60.0, f"{w200:.1f}° cap={cap200}")
    check("★ |Δ|=60° 不触发大误差限幅，限幅值 45°",
          pf._turn_plan(60.0)[1] == 45.0, str(pf._turn_plan(60.0)[1]))
    check("  死区内也会算出一个很小的值（由调用方跳过）",
          abs(pf._turn_plan(1.0)[0]) < 1.0)
    check("  不会下发 0 像素（取整会白按一次）",
          all(abs(pf._turn_plan(d)[2]) >= pf.MIN_TURN_PX
              for d in (0.3, 0.9, 1.5, -0.4)))
    check("★ 启动横幅用 _tier_text() 且不崩", isinstance(pf._tier_text(), str),
          pf._tier_text())

    # ---- 稳定性：这是本轮的核心断言 ----
    def _simulate(d0, G, n=30, use_nominal=True):
        """按真实控制律推演 Δ 演化。

        ``use_nominal=False`` 时把 ``MOUSE_DEG_PER_PX`` 也当作未知量
        一起扰动 —— 检验「假定值填错时环路是否仍稳定」。
        """
        d = float(d0)
        turns, seq = 0, [d]
        for _ in range(n):
            if abs(d) <= pf.TURN_DEADZONE_DEG:
                break
            want, _, px = pf._turn_plan(d)
            if not use_nominal:
                # 控制器以为 1px=G_nominal，实际只有 G
                d = d + G * (want * G / pf.MOUSE_DEG_PER_PX)
            else:
                d = d + G * want
            turns += 1
            seq.append(d)
        moving = [abs(x) for x in seq if x != 0.0]
        mono = all(moving[i + 1] < moving[i] for i in range(len(moving) - 1))
        return mono, turns, (seq[-1] if seq else d)

    for G in (0.5, 1.0, 2.0, 4.0):
        mono, turns, last = _simulate(177.0, G)
        check(f"★ G={G} 度/px：177° 单调收敛（{turns} 次转向，末值 {last:+.1f}°）",
              mono and abs(last) <= pf.TURN_DEADZONE_DEG)
    # ★ 标定后最关键的一条：假定值与真实值差很多时**仍然稳定**。
    #   注意断言的是「**单调不震荡**」，不是「30 帧内收敛」——
    #   G 极小时环路增益也极小（0.02*0.45=0.009），收敛本来就慢，
    #   但那依然是**稳定**的。稳定性 = 不反向放大，不是快。
    for G in (0.02, 0.05, 0.2, 0.5):
        mono, turns, last = _simulate(177.0, G, use_nominal=True)
        check(f"★ G={G}（远小于假定 {pf.MOUSE_DEG_PER_PX}）：单调不震荡"
              f"（{turns} 次，末值 {last:+.1f}°，"
              f"仍比起点 {abs(last) < abs(177.0)}）", mono)
    # 反过来：真实 G **远大于**假定值 -> 有效比例被放大，必须被死区/限幅兜住
    for G in (0.7, 1.4, 2.8):
        _mono, turns, last = _simulate(177.0, G, use_nominal=True)
        check(f"★ G={G}（远大于假定）：末值不超过限幅带"
              f"（{turns} 次，末值 {last:+.1f}°）",
              abs(last) <= pf.TURN_MAX_DEG_BIG * 1.05)

    # 旧律在同样条件下的极限环 —— 留作反例证据
    def _simulate_legacy(d0, G, n=12):
        d = float(d0)
        for _ in range(n):
            g, _t = pf._gain_for(d)
            c = pf._clamp_for(d)
            px = max(-c, min(c, -g * d))
            d = d + G * px
        return d
    legacy = _simulate_legacy(60.0, 1.0)
    check("（反例）旧分档绝对增益在 G=1.0 下 12 次转向后 |Δ| 仍 >100°"
          " —— 证明它就是震荡根因", abs(legacy) > 100.0, f"末值 {legacy:+.1f}°")

    # 真跑：转向上限必须换算回像素 —— 限幅是**按度数**定的
    _px_cap = pf.TURN_MAX_DEG_BIG / pf.MOUSE_DEG_PER_PX
    out, _, mouse, _ = run([(0.0, 1.2)] * 30, argv_of(seconds="5"))
    if mouse:
        check(f"实跑：所有转向都在限幅等效 {_px_cap:.0f}px 内"
              f"（{pf.TURN_MAX_DEG_BIG:g}° / {pf.MOUSE_DEG_PER_PX:g}°每px）",
              all(abs(v) <= _px_cap + 1 for v in mouse),
              f"max={max(map(abs, mouse))}px")
    else:
        check("实跑：本次无转向（死区内），跳过幅度断言", True, "mouse=[]")
    check("★ 打滑恢复那条路径也走比例律且有界（回归：曾一帧发 93px）",
          abs(pf.SLIP_PURE_MAX_DEG) <= 60.0
          and 0 < pf.SLIP_PURE_FRACTION <= 1.0,
          f"限幅 ±{pf.SLIP_PURE_MAX_DEG}° 比例 {pf.SLIP_PURE_FRACTION}")
    # ★★ 任务 A：打滑恢复的**发送**必须走 _turn_dispatch，受单次上限约束。
    #   真机 t=4.3s 打出「+40°/+571px」—— 那一发绕过了 40px 上限，
    #   与上一轮 SPLIT_MAX_SEGMENTS=1 绕过 60px 同一类 bug。
    #   旧断言只看限幅常量，看不到「发送路径」，所以一直没抓到。
    _slip_turn = min(pf.SLIP_PURE_MAX_DEG,
                     max(-pf.SLIP_PURE_MAX_DEG,
                         -pf.SLIP_PURE_FRACTION * -74.0))    # 真机 Δ=-74°
    _sg, _sr = pf._turn_dispatch(_slip_turn)
    check(f"★★ 打滑恢复 Δ=-74° -> 计划 {_slip_turn:+.0f}°，"
          f"**每段 <= {pf.MOUSE_SATURATION_PX}px**（回归：曾一发 571px）",
          all(abs(p) <= pf.MOUSE_SATURATION_PX for p in _sg),
          f"{_sg} 最大 {max((abs(p) for p in _sg), default=0)}px")
    check("  打滑恢复欠账不为 0（说明确实超上限、需要后续帧补完）",
          abs(_sr) > 0.5, f"仍欠 {_sr:+.1f}°")
    # 单次注入总量 = 段数 × 每段上限，仍不得超过 SPLIT 预算
    check("  打滑恢复本帧注入总量受预算约束",
          sum(abs(p) for p in _sg)
          <= pf.MOUSE_SATURATION_PX * max(1, pf.SPLIT_MAX_SEGMENTS),
          f"总 {sum(abs(p) for p in _sg)}px")
    # 常态与打滑共用同一个发送函数 —— 源码层面不允许再有
    #「把算出来的角度除以 MOUSE_DEG_PER_PX 直接发」的裸发。
    #   ★ 不能简单统计 mouse_move_relative 的行数：脱困的固定 25px 摆头
    #     和遍历 _segs 的循环体都是**合法**的发送点。要抓的是那种
    #     「自己算一遍 turn_deg，然后 turn_deg / MOUSE_DEG_PER_PX 一次发」——
    #     571px 就是这么漏出去的。
    _src = Path(pf.__file__).read_text(encoding="utf-8")
    _bare = [ln.strip() for ln in _src.splitlines()
             if ("mouse_move_relative" in ln
                 and "MOUSE_DEG_PER_PX" in ln          # 按角度换算像素的裸发
                 and not ln.strip().startswith("#"))]
    check("★ 没有「角度/MOUSE_DEG_PER_PX 直接裸发」的发送点"
          f"（当前 {len(_bare)} 处）", len(_bare) == 0, _bare)
    # 反向确认：脱困的固定摆头幅度必须仍在单次上限内
    check("  脱困摆头 25px 本身不超过单次上限",
          25 <= pf.MOUSE_SATURATION_PX, f"25px <= {pf.MOUSE_SATURATION_PX}px")
    check(f"  TURN_CAL_MIN_N = {pf.TURN_CAL_MIN_N}（样本量不足时不回填标定）",
          pf.TURN_CAL_MIN_N == 30, str(pf.TURN_CAL_MIN_N))
    check(f"  SLIP_STAND_MS = {pf.SLIP_STAND_MS:g}（站定等待，提成常量）",
          pf.SLIP_STAND_MS == 250.0, str(pf.SLIP_STAND_MS))
    check("启动横幅打印的是新转向律", "比例" in out,
          [l.strip() for l in out.splitlines() if "比例" in l][:1])
    check("日志行已无 g=/档= 列（分档已废弃）",
          "档=L" not in out and " g=" not in out)

    # ============================================================ 任务 A/F
    section("任务 A/F · 卡住检测 = 采样队列（旧的反向 180° 逻辑已删）")
    check("★ 采样周期 SAMPLE_MS = 1000", pf.SAMPLE_MS == 1000.0)
    check("★ 队列长度 SAMPLE_N = 8", pf.SAMPLE_N == 8)
    check("★ 阈值 STUCK_DIST = 3.0", pf.STUCK_DIST == 3.0)
    check("★ 快速成功窗口 ESCAPE_FAST_WIN_S = 3s", pf.ESCAPE_FAST_WIN_S == 3.0)
    # 源码级不变量：反向 180° 那套必须**不存在**了
    # ⚠️ 只查**日志串**「判定为**方向反了**」。裸查「方向反了」会误报 ——
    #    模块 docstring 里还留着这段历史说明（「旧的…会清空方向窗口」）。
    _src = SCRIPT.read_text(encoding="utf-8")
    for dead in ("REVERSE_FRAMES", "REVERSE_TURN_PX", "far_streak",
                 "判定为**方向反了**", "TURN_GAIN"):
        check(f"★ 旧符号 `{dead}` 已彻底删除", dead not in _src)
    # 走远了 -> 队列位移 >=3 -> 不该脱困
    out, _, _, _ = run([(1.2, 0.0)] * 40, argv_of(seconds="9"))
    check("★ 正常前进 9s 一次都不脱困（采样队列不会误报）",
          "卡住 ->" not in out and "卡墙/攀爬" not in out,
          [l.strip() for l in out.splitlines() if "脱困" in l][:1])
    # 站着不动 9s：每 1s 采一次，8 个样本后首尾位移 0 < 3 -> 判卡住
    out, _, _, _ = run([(1.2, 0.0)] * 5 + [(0.0, 0.0)] * 60, argv_of(seconds="14"))
    check("★ 原地不动 ~12s 触发「卡住 -> L1 脱困」", "卡住 ->" in out,
          [l.strip() for l in out.splitlines() if "卡住 ->" in l][:1])
    check("★ 日志里能看到队列位移（= 判据本身）", "队列=" in out)
    # 脱困级别递增。
    # ⚠️ 别断言「L1 一定来自卡住」：卡墙（20 帧 ≈ 4s）比卡住队列
    #    （8 样本 ≈ 7s）先到，第一枪通常是卡墙给的 L1。真不变量是
    #    **级别单调不降且最终升到 L3**，来源可以任意。
    _seq = [int(m) for m in __import__("re").findall(r"-> L(\d)", out)]
    check("★ 脱困级别单调不降并最终升到 L3",
          len(_seq) >= 2 and _seq == sorted(_seq) and _seq[-1] == 3, str(_seq))
    check("★ 两种检测器都参与（卡墙 + 卡住都出现过）",
          "卡墙/攀爬" in out and "卡住 ->" in out,
          [l.strip() for l in out.splitlines()
           if "-> L" in l][:2])
    # 快速成功：卡住一下后走远 -> 归零
    out, _, _, _ = run([(1.2, 0.0)] * 5 + [(0.0, 0.0)] * 12 + [(1.5, 0.0)] * 40,
                       argv_of(seconds="20"))
    check("★ 脱困后走远会打印「级别归零」",
          "级别归零" in out,
          [l.strip() for l in out.splitlines() if "归零" in l][:2])

    # ---- 任务 A：脱困非阻塞状态机 -----------------------------------
    section("任务 A · 脱困非阻塞（状态机）")
    # ★ 真机 60s 实测脱困**同步阻塞** 2813ms（L1×2 + L2 + L3），
    #   把主循环从 200ms 拖到 438ms；而卡住检测恰恰因为帧率慢而误触发 ——
    #   阻塞与误触发互为因果。现在必须证明：脱困期间**不再阻塞**。
    out, _, _, _ = run([(1.2, 0.0)] * 5 + [(0.0, 0.0)] * 60,
                       argv_of(seconds="14"))
    _blk = [l.strip() for l in out.splitlines() if "阻塞来源" in l]
    check("★ 脱困仍能在「原地不动」时触发（状态机没把功能改坏）",
          "-> L" in out, [l.strip() for l in out.splitlines() if "-> L" in l][:1])
    # 非阻塞的核心证据：L1 的两段（Shift 400ms + S 700ms = 1100ms）
    # 不再一次性占住主循环。检查是否存在**任何**同步 wait 残留。
    # ⚠️ **必须用 AST 取函数体**：docstring 是三引号字符串，里面正好
    #    引用了旧的 ``panic.stop.wait(0.4)`` 做对比说明。剥「# 注释」
    #    剥不掉它（会误报），纯文本搜又分不清代码和文档。
    #    AST 拿到的是**真正的代码**，docstring 单独剔除。
    import ast as _ast
    _src = Path(pf.__file__).read_text(encoding="utf-8")
    _fns = {n.name: n for n in _ast.walk(_ast.parse(_src))
            if isinstance(n, _ast.FunctionDef)}

    def _body_code(name: str) -> str:
        out: list[str] = []
        for st in _fns[name].body:
            if isinstance(st, _ast.Expr) and isinstance(
                    getattr(st, "value", None), _ast.Constant):
                continue                      # docstring / 裸字符串
            out.append(_ast.get_source_segment(_src, st) or "")
        return "\n".join(out)

    _esc_code = _body_code("do_escape")
    check("★★ do_escape 内已无任何 panic.stop.wait（真脱困即返回）",
          "panic.stop.wait" not in _esc_code,
          [ln.strip() for ln in _esc_code.splitlines()
           if "panic.stop.wait" in ln])
    check("★ do_escape 内已无 _esc_block 累加（不再计时阻塞）",
          "_esc_block" not in _esc_code)
    _tick_code = _body_code("escape_tick")
    # ⚠️ escape_tick 用的是 ``now_abs()`` 抽象而不是直接 time.monotonic()
    #   （脱困时间轴必须和 state 里的 due 用同一个时钟），所以查 now_abs。
    check("★ escape_tick 走绝对时钟 now_abs（非 sleep 轮询）",
          "now_abs()" in _tick_code and "sleep" not in _tick_code,
          [ln.strip() for ln in _tick_code.splitlines() if "now_abs" in ln])
    check("★ escape_tick 每帧查急停（F12 仍能立即收手）",
          "panic.stop.is_set()" in _tick_code)
    check("★ 状态机的 due 与 tick 用同一个时钟（now_abs）",
          hasattr(pf, "_EscapeState")
          and "now_abs()" in _body_code("do_escape"))
    check("★ 状态机类存在且记录了 hold/due/stage",
          hasattr(pf, "_EscapeState")
          and {"hold", "due", "stage"} <= set(pf._EscapeState.__slots__),
          getattr(pf, "_EscapeState", None).__slots__
          if hasattr(pf, "_EscapeState") else "缺失")
    # ★ 键泄漏防护：非阻塞化后 finally 必须负责松开状态机手里的键
    check("★★ 退出时释放状态机按着的键（否则角色一直后退）",
          "esc_state.hold" in _src and _src.count("esc_state.hold") >= 2)
    # 脱困进行中不能叠 Shift+W（非阻塞化后主循环每帧都会走到稳态分支）
    # ★ 2026-10-08：稳态分支改成三分支（脱困 / 站定 / 正常），
    #   这里断言的是**分支顺序**这个不变量，不再钉死 `if not _esc_busy:`
    #   这句字面 —— 2026-10-04 那次重写已经证明字面断言太脆。
    _i_esc = _src.find("if _esc_busy:")
    _i_std = _src.find("elif _stand_active:")
    check("★ 脱困帧不叠加 sprint_on（Shift+W 会让下坠失效）",
          0 <= _i_esc < _i_std
          and "else:\n                        sprint_on()" in _src,
          f"脱困分支 @ {_i_esc} < 站定分支 @ {_i_std} < else: sprint_on()")
    # ---- 任务 A：移动诊断（1.58 m/s 的归因）----
    check("★ 汇总里有「移动诊断」段（W 按住帧数 / 等效速度）",
          "★ 移动诊断" in out or "移动诊断" in out,
          [l.strip() for l in out.splitlines() if "移动诊断" in l][:1])
    check("★ 汇总里报了等效速度 m/s（1.58 m/s 的直接来源）",
          "m/s" in out,
          [l.strip() for l in out.splitlines() if "m/s" in l][:1])
    check("★ 汇总里区分了「脱困中故意没按 W」的帧",
          "其中脱困中" in out,
          [l.strip() for l in out.splitlines() if "脱困中" in l][:1])
    check("★ 汇总里报了「按着 W 却几乎不动」的帧占比",
          "m 的帧" in out or "几乎不动" in out,
          [l.strip() for l in out.splitlines()
           if "几乎不动" in l or "m 的帧" in l][:1])
    # ★ 回归：空转占比曾报出 **118%** —— 分子统计了「所有帧」，
    #   分母却是「W 按住的帧」，两个口径不一致。占比必须 <= 100%。
    _stall = [l for l in out.splitlines() if "m 的帧" in l]
    if _stall:
        import re as _re2
        _m = _re2.search(r"(\d+)（占按住帧 (\d+)%）", _stall[0])
        _ok = (not _m) or int(_m.group(1)) <= int(_m.group(2))
        check("★★ 空转占比 <= 100%（回归：曾报 118%，分子分母口径不一致）",
              _ok, _stall[0].strip())
    check("★ 汇总里分列 L1/L2/L3 计数",
          "卡住脱困    : L1" in out and "L2" in out and "L3" in out,
          [l.strip() for l in out.splitlines() if "卡住脱困" in l][:1])

    # ============================================================ 任务 B
    section("任务 B · 攀爬/卡墙阈值 3 -> 20 帧")
    check("★ CLIMB_FRAMES = 20", pf.CLIMB_FRAMES == 20, str(pf.CLIMB_FRAMES))
    check("  CLIMB_EPS = 0.02", pf.CLIMB_EPS == 0.02, str(pf.CLIMB_EPS))
    # 只归零 10 帧（2s）不该判卡墙
    out, _, _, _ = run([(1.2, 0.0)] * 5 + [(0.0, 0.0)] * 10, argv_of(seconds="4"))
    check("★ 归零 10 帧不误判卡墙（旧阈值 3 帧会误报）",
          "卡墙/攀爬" not in out,
          [l.strip() for l in out.splitlines() if "卡墙" in l][:1])
    # 归零 30 帧该判
    out, _, _, _ = run([(1.2, 0.0)] * 5 + [(0.0, 0.0)] * 40, argv_of(seconds="10"))
    check("★ 归零 30 帧判卡墙并升级脱困", "卡墙/攀爬" in out,
          [l.strip() for l in out.splitlines() if "卡墙" in l][:1])
    check("★ 卡墙日志打印的是**连续**帧数，不是被清零后的 0",
          "连续 2" in out or "连续 3" in out or "连续 1" in out,
          [l.strip() for l in out.splitlines() if "卡墙" in l][:1])
    check("汇总里有独立的「攀爬/卡墙」计数", "攀爬/卡墙   :" in out)

    # ============================================================ 任务 A/E
    section("任务 A/E · 打滑检测（收敛检查 + 次数上限）")
    check("  SLIP_MIN_ERR_DEG = 12 / SLIP_FRAMES = 3 / SLIP_SECONDS = 2",
          (pf.SLIP_MIN_ERR_DEG, pf.SLIP_FRAMES, pf.SLIP_SECONDS)
          == (12.0, 3, 2.0))
    # ★★ 任务 B 的核心不变量：打滑门限必须**大于**死区。
    #   真机 t=59.7s Δ=+7.1°（死区 8° 以内）触发了第 3 次打滑恢复 ——
    #   角色本已对准却被按停 250ms，纯属自找的打断。
    check("★★ 打滑门限 > 转向死区（否则「不转向却反复打滑」）",
          pf.SLIP_MIN_ERR_DEG > pf.TURN_DEADZONE_DEG,
          f"{pf.SLIP_MIN_ERR_DEG:g}° > {pf.TURN_DEADZONE_DEG:g}°")
    check("  旧名 SLIP_DEG 保留为别名（不断引用）",
          pf.SLIP_DEG == pf.SLIP_MIN_ERR_DEG, str(pf.SLIP_DEG))
    # ★★ 任务 B 的核心不变量。
    #   真正的矛盾是：**门限 < 死区** 时，「已对准不转」（|Δ| <= 死区）
    #   与「判定没收敛」（|Δ| > 门限）在区间 **(门限, 死区]** 上同时成立。
    #   旧门限 5° < 死区 8° -> 荒谬区间 (5°, 8°]，真机 Δ=+7.1° 正在其中。
    #   ⚠️ 注意区间方向是 (门限, 死区]，不是 (死区, 门限] —— 后者在
    #   门限 < 死区 时恒为空，我第一版就是这么写错的。
    #
    # ⚠️ 把门限**调大**并不是「消灭」荒谬区间，而是把它挪到 (死区, 门限]。
    #   12° 时新区间是 (8°, 12°] —— 依然存在，但真机那个 Δ=7.1° 已经出去了。
    #   所以正确的断言是「Δ=7.1 不再触发」，不是「区间为空」。
    def _in_band(d: float, thr: float, dead: float) -> bool:
        """Δ=d 是否落在荒谬区间里：``不转向`` 且 ``判打滑`` 同时成立。

        ⚠️ 别用「按步长采样后看 7.1 在不在列表里」来判断 —— 7.1 落在
        0.5° 步长的采样点**之间**，我第一版就是这么误判成「区间不含 7.1」。
        直接判不等式才对。
        """
        lo, hi = sorted((thr, dead))
        return lo < abs(d) <= hi
    _old_slip, _old_dead = pf.SLIP_MIN_ERR_DEG, pf.TURN_DEADZONE_DEG
    try:
        pf.SLIP_MIN_ERR_DEG = 5.0        # 复现旧配置
        pf.TURN_DEADZONE_DEG = 8.0
        check("★ 旧门限 5° < 死区 8° -> 荒谬区间 (5°,8°] **含 Δ=7.1°**",
              _in_band(7.1, 5.0, 8.0))
        check("  同区间外的 Δ 不受影响（如 3° 已小于门限）",
              not _in_band(3.0, 5.0, 8.0))
        check("  旧配置下 Δ=7.1 会被打滑判据命中（真机 #3 的成因）",
              7.1 > pf.SLIP_MIN_ERR_DEG and 7.1 <= pf.TURN_DEADZONE_DEG)
    finally:
        pf.SLIP_MIN_ERR_DEG = _old_slip
        pf.TURN_DEADZONE_DEG = _old_dead
    check("★ 新配置下 Δ=7.1 **不再**触发打滑（真机 #3 已修）",
          not (7.1 > pf.SLIP_MIN_ERR_DEG))
    check("  新门限仍需 > 死区（否则区间会落在已对准的那一段）",
          pf.SLIP_MIN_ERR_DEG > pf.TURN_DEADZONE_DEG,
          f"{pf.SLIP_MIN_ERR_DEG:g}° > {pf.TURN_DEADZONE_DEG:g}°")
    # ★★ 判据层才是真修法：死区内的 Δ 一律不计入 slip_streak。
    #   即使有人把门限改回 5°（荒谬区间重现），(5°,8°] 也不再触发。
    import ast as _ast3
    _s3 = Path(pf.__file__).read_text(encoding="utf-8")
    _main3 = [n for n in _ast3.walk(_ast3.parse(_s3))
              if isinstance(n, _ast3.FunctionDef) and n.name == "main"][0]
    _body3 = "\n".join(
        (_ast3.get_source_segment(_s3, st) or "")
        for st in _main3.body)
    check("★★ 判据层拦死区：打滑分支前先判 TURN_DEADZONE_DEG",
          "abs(d_deg) > TURN_DEADZONE_DEG" in _body3
          and "abs(d_deg) > SLIP_MIN_ERR_DEG" in _body3)
    check("  SLIP_PURE_GAIN = 1.0（站定用最小增益）", pf.SLIP_PURE_GAIN == 1.0)
    check(f"  SLIP_CONVERGE_N = {pf.SLIP_CONVERGE_N}（收敛检查窗口，"
          f"2026-10-08 由 6 改 10）",
          pf.SLIP_CONVERGE_N == 10, str(pf.SLIP_CONVERGE_N))
    check("★ SLIP_MAX_RECOVERIES = 3（次数上限）", pf.SLIP_MAX_RECOVERIES == 3)
    # 一直沿 +X 走、目标在正 +Z -> Δ 恒 ≈ +90°，**不收敛** -> 真打滑
    # ⚠️ 目标必须**偏离**移动方向：目标也放 +X 的话 Δ=0，永远不会打滑
    out, _, mouse, _ = run([(1.2, 0.0)] * 80,
                           argv_of(target="0,500", seconds="16"))
    check("★ Δ 不收敛时仍触发打滑恢复", "打滑恢复" in out,
          [l.strip() for l in out.splitlines() if "打滑" in l][:1])
    check("★ 打滑日志打印**真实持续时长**（回归：上一版写死 el - 0）",
          "持续 " in out and "松 W" in out,
          [l.strip() for l in out.splitlines() if "打滑恢复" in l][:1])
    check("★ 打滑日志点明「未在收敛」这个触发依据",
          "未在收敛" in out,
          [l.strip() for l in out.splitlines() if "打滑恢复" in l][:1])
    # ★ 次数上限：40 秒里绝不该再出现 14 次
    _n_slip = out.count("打滑恢复 #")
    check(f"★ 40s 内打滑恢复 {_n_slip} 次 <= 上限 {pf.SLIP_MAX_RECOVERIES}",
          _n_slip <= pf.SLIP_MAX_RECOVERIES, f"{_n_slip} 次")
    check("汇总里有「打滑恢复 N 次」", "打滑恢复    :" in out,
          [l.strip() for l in out.splitlines() if "打滑恢复" in l][-1:])

    # ---- 收敛检查（纯函数级，比整循环路径仿真可靠）----
    # ---- 饱和上限（任务 A/B/C：一次发多少，与拆不拆彻底解耦）----
    section("单次注入上限 · 无论拆不拆都生效")
    check("★ MOUSE_SATURATION_PX = 40（40~60px 达成率 74%，60~80 掉到 55%）",
          pf.MOUSE_SATURATION_PX == 40, str(pf.MOUSE_SATURATION_PX))
    check("  SPLIT_SLEEP_MS = 20（仅拆分启用时才有意义）",
          pf.SPLIT_SLEEP_MS == 20.0, str(pf.SPLIT_SLEEP_MS))
    check("★ SPLIT_MAX_SEGMENTS = 1（每帧一次注入）",
          pf.SPLIT_MAX_SEGMENTS == 1, str(pf.SPLIT_MAX_SEGMENTS))
    check("★ TURN_QUEUE_MAX_FRAMES = 300（2026-10-08 由 30 改：退居 panic 兜底）",
          pf.TURN_QUEUE_MAX_FRAMES == 300, str(pf.TURN_QUEUE_MAX_FRAMES))
    check("  TURN_EFF_NOISE_DEG = 2.0（噪声地板）",
          pf.TURN_EFF_NOISE_DEG == 2.0, str(pf.TURN_EFF_NOISE_DEG))
    check(f"  MOVE_STALL_EPS = {pf.MOVE_STALL_EPS:g}"
          f"（按着 W 却没动的判定阈值）",
          pf.MOVE_STALL_EPS == 0.05, str(pf.MOVE_STALL_EPS))
    # ---- 任务 A 的核心回归：不拆**不等于**不限幅 ----
    segs, rest = pf._split_turn(857)
    check("★★ 不拆时也必须限幅：857px -> 只发 40px，余 817px 退回队列",
          segs == [40] and rest == 817, f"{segs} rest={rest}")
    segs, rest = pf._split_turn(40)
    check("  正好 40px -> 单段全发，无余量", segs == [40] and rest == 0, str(segs))
    segs, rest = pf._split_turn(41)
    check("  41px -> 发 40 留 1（边界不越界）",
          segs == [40] and rest == 1, f"{segs} rest={rest}")
    segs, rest = pf._split_turn(0)
    check("  0px -> 空列表不炸", segs == [] and rest == 0, str(segs))
    segs, rest = pf._split_turn(-857)
    check("  负向保持符号：-857 -> [-40] 余 -817",
          segs == [-40] and rest == -817, f"{segs} rest={rest}")
    segs, rest = pf._split_turn(39)
    check("  39px 不足上限，原样发", segs == [39] and rest == 0, str(segs))
    # 拆分开关打开时，每段仍 <= 上限（上限与段数解耦的另一半）
    _old_n = pf.SPLIT_MAX_SEGMENTS
    try:
        pf.SPLIT_MAX_SEGMENTS = 8
        segs, rest = pf._split_turn(857)
        check("  [开关打开] 857px -> 8×40=320 + 余 537（每段 <=40）",
              len(segs) == 8 and all(abs(p) <= 40 for p in segs)
              and sum(segs) == 320 and rest == 537,
              f"{segs} rest={rest}")
        segs, rest = pf._split_turn(100)
        check("  [开关打开] 100px -> 3 段均分（33/33/34），无余量",
              segs == [33, 33, 34] and rest == 0, f"{segs}")
    finally:
        pf.SPLIT_MAX_SEGMENTS = _old_n
    # ---- 任务 B：队列按**角度**记账，且不跨帧过冲 ----
    segs, rest = pf._turn_dispatch(-60.0)
    check("★ _turn_dispatch：欠 60° -> 本帧只发 40px(2.8°)，仍欠 57.2°",
          segs == [-40] and abs(rest - (-57.2)) < 0.05,
          f"{segs} rest={rest:.2f}")
    segs, rest = pf._turn_dispatch(-1.0)
    check("  欠 1° -> 一次发 14px，残差 0.02° 被地板结清（不拖尾）",
          segs == [-14] and rest == 0.0, f"{segs} rest={rest}")
    segs, rest = pf._turn_dispatch(0.0)
    check("  欠 0° -> 不发不炸", segs == [] and abs(rest) < 1e-6, f"{segs}")
    # ★ 账本守恒：反复派发同一笔欠账，累计发出的角度必须等于欠账本身
    #   ⚠️ 循环变量**绝不能叫 _frames** —— 那是本模块全局的假帧列表，
    #   一旦覆盖，后面 _Cap.grab() 里的 len(_frames) 会炸
    #   「object of type 'int' has no len」（真踩过）。
    _owed, _tot, _nf = -60.0, 0.0, 0
    while abs(_owed) > 1e-6 and _nf < 100:
        _sg, _owed = pf._turn_dispatch(_owed)
        _tot += abs(sum(_sg)) * pf.MOUSE_DEG_PER_PX
        _nf += 1
    check(f"★ 账本守恒：欠 60° 分 {_nf} 帧发完，累计发出 == 60°",
          abs(_tot - 60.0) < 0.1, f"发出 {_tot:.3f}° 共 {_nf} 帧")
    check("★ 队列必定归零（不会无限拖尾）", _owed == 0.0, f"残留 {_owed}")
    check("  40px 上限下 60° 需要 20 帧以上（证明限幅真的在生效）",
          _nf >= 20, f"{_nf} 帧")
    # ★ 拖尾回归：欠账小到取整成 0 像素时必须**结清**，否则每帧空转
    _t = -0.02
    for _ in range(5):
        _sg2, _t = pf._turn_dispatch(_t)
    check("★ 拖尾回归：0.02° 欠账反复派发 5 次后归零（不会卡住）",
          _t == 0.0, f"残留 {_t}")
    # ★ 跨帧过冲回归：上帧欠 60°，下帧 Δ 已小 -> 欠账必须跟着缩
    _q = -57.2                                    # 上帧发完 40px 后的欠账
    _want_small = -4.5                            # 下帧 Δ 变小，只需 4.5°
    _owed = min(abs(_q), abs(_want_small))
    check("★★ 跨帧过冲回归：欠 57.2° 但当前只需 4.5° -> 取 4.5°，"
          "不会把旧的 57.2° 发出去",
          abs(_owed - 4.5) < 1e-9, f"{_owed}")
    # ---- 动态跳变阈值（任务 B）----
    section("动态跳变阈值 · 不写死帧率")
    check("  SPEED_RUN_MPS = 11.0 / STEP_LIMIT_SAFETY = 3.0",
          (pf.SPEED_RUN_MPS, pf.STEP_LIMIT_SAFETY) == (11.0, 3.0))
    check("  阈值夹在 [50, 500]（下限保走路裕量，上限远低于真传送 100~5000px）",
          (pf.STEP_LIMIT_MIN_PX, pf.STEP_LIMIT_MAX_PX) == (50.0, 500.0))
    _ptw = 0.389                       # minimap_scale.toml 的 city_px_to_world
    # 200ms 一帧跑步 11 m/s -> 5.7px，×3 = 17 -> 被下限抬到 50
    check("★ 200ms 帧间隔 -> 阈值取下限 50px（与旧写死值一致）",
          pf._dynamic_max_step_px(_ptw, 0.2) == 50.0,
          f"{pf._dynamic_max_step_px(_ptw, 0.2):.1f}")
    # 1.6fps（625ms）-> 17.7px ×3 = 53 -> 略高于下限，会自动放大
    _v625 = pf._dynamic_max_step_px(_ptw, 0.625)
    check("★ 1.6fps（625ms）-> 阈值自动放大到 ~53px",
          50.0 < _v625 < 60.0, f"{_v625:.1f}px")
    # 极端慢（5s 一帧）-> 141px×3 = 424，仍在上限内
    _v5 = pf._dynamic_max_step_px(_ptw, 5.0)
    check("  5s 巨慢帧 -> ~424px，未撞上限 500",
          400 < _v5 < 500, f"{_v5:.1f}px")
    # 上限封顶：极慢也不放行到传送量级
    check("  ★ 极慢帧也被封在 500px 以内（真传送 100~5000 仍在外面）",
          pf._dynamic_max_step_px(_ptw, 60.0) == 500.0)
    check("  单调性：帧间隔越长阈值越大（不认死数）",
          pf._dynamic_max_step_px(_ptw, 1.0)
          > pf._dynamic_max_step_px(_ptw, 0.2))
    check("  px_to_world 读坏时走**保守**分支（取下限，不放开到上限）",
          pf._dynamic_max_step_px(0.0, 0.2) == 50.0
          and pf._dynamic_max_step_px(-1.0, 5.0) == 50.0)

    # ============================================================ 任务 D
    # ★ 2026-10-08：窗口 SLIP_CONVERGE_N 6 -> 10，判据去掉 5° 幅度门限
    #   （只留「后半段均值 < 前半段均值」）。
    #   下面的序列一律喂 **10** 个样本 —— 喂 6 个会因「样本不足」返回 False，
    #   那样断言照样绿、但绿得毫无意义（这次改窗口时踩过）。
    check("★ SLIP_CONVERGE_N = 10（收敛窗口，2026-10-08 由 6 改 10）",
          pf.SLIP_CONVERGE_N == 10, str(pf.SLIP_CONVERGE_N))
    check("★ _is_converging：|Δ| 恒定 -> False（真打滑，触发）",
          pf._is_converging([90, 90, 91, 90, 89, 90, 90, 91, 90, 89]) is False,
          "10 样本，均值持平 -> 不豁免")
    check("★ _is_converging：|Δ| 递增 -> False（打滑恶化）",
          pf._is_converging([10, 20, 30, 40, 45, 55, 60, 70, 75, 80]) is False,
          "10 样本，后半段更大 -> 不豁免")
    check("★ _is_converging：样本不足 -> False（证据不够，不豁免）",
          pf._is_converging([40, 30, 20]) is False)
    check("  _is_converging：空序列不炸", pf._is_converging([]) is False)

    # ---- 本轮真机回归：179° 大角度慢收敛（本轮 bug 2 的现场）----
    #   t=4.2/10.6/18.8s 三次打滑时 Δ 分别 170.8 / 102.3 / 18.0，**都在降**，
    #   但每帧只降 1~2°。旧 5° 幅度门限在 6 帧窗口里刚好够不到 ->
    #   判「未在收敛」-> 反复打断正在好好转向的角色。
    for _lbl, _seq in (
        ("#1 Δ≈170.8", [179.0, 177.6, 176.3, 174.9, 173.6, 172.2,
                        170.8, 169.4, 168.0, 166.7]),
        ("#2 Δ≈102.3", [110.0, 108.4, 106.9, 105.3, 103.8, 102.3,
                        100.9, 99.4, 98.0, 96.5]),
        ("#3 Δ≈18.0",  [24.0, 23.2, 22.5, 21.7, 21.0, 20.2,
                        19.5, 18.7, 18.0, 17.4]),
    ):
        check(f"★ 大角度慢收敛 {_lbl} -> 判为收敛（不再误打滑）",
              pf._is_converging(_seq) is True,
              f"每帧降 1~1.3°，旧 5° 门限够不到")

    check("★ _is_converging：慢收敛只剩 ~0.2°/帧也判收敛（B3：只看方向）",
          pf._is_converging([20.0, 19.8, 19.6, 19.4, 19.2, 19.0,
                             18.8, 18.6, 18.4, 18.2]) is True,
          "旧判据会因降幅不足 5° 误判打滑")
    check("★ _is_converging：持平（new == old）-> False（严格小于）",
          pf._is_converging([20.0] * 5 + [20.0] * 5) is False)
    check("★ _is_converging：噪声抖动（均值持平）-> False",
          pf._is_converging([20.0, 21.0, 19.0, 20.5, 19.5,
                             20.2, 19.8, 20.1, 19.9, 20.0]) is False,
          "10 帧窗口把单帧噪声平均掉了")
    check("  SLIP_CONVERGE_MIN_DEG 仍保留 = 5.0（不再参与判据，仅作参考）",
          pf.SLIP_CONVERGE_MIN_DEG == 5.0)
    # ★ 判据里**不许**再出现幅度门限（否则以后有人手滑把它加回来）。
    #   用源码文本切出 _is_converging 函数体来查，本文件没有 import inspect。
    _src_uc = Path(pf.__file__).read_text(encoding="utf-8")
    _i0 = _src_uc.index("def _is_converging(")
    # ★ 切到**下一个 def** 为止，不能切到 _turn_plan —— 2026-10-08 在两者
    #   之间插了 _stand_turn_step，按名字切会把别的函数一起框进来。
    _i1 = min(x for x in (_src_uc.find("\ndef ", _i0 + 1),
                          _src_uc.find("\nclass ", _i0 + 1))
              if x > 0)
    _body_uc = _src_uc[_i0:_i1]
    # 切掉 docstring（里面**故意**提到过 SLIP_CONVERGE_MIN_DEG 的历史），
    # 只留真正会被执行的代码行。
    _d0 = _body_uc.index('"""', _body_uc.index('"""') + 3) + 3
    _code_uc = "\n".join(l for l in _body_uc[_d0:].splitlines()
                         if not l.strip().startswith("#"))
    check("★ _is_converging 的可执行代码里不再出现幅度门限",
          "SLIP_CONVERGE_MIN_DEG" not in _code_uc
          and _code_uc.strip().endswith("return new < old"))

    # ---- 限频（任务 C）----
    check("  TURN_MIN_INTERVAL_S = 0.08（限频，主循环 200ms/帧下不触发）",
          pf.TURN_MIN_INTERVAL_S == 0.08, str(pf.TURN_MIN_INTERVAL_S))
    check("  TURN_MEASURE_SAMPLES = 200（实测 G 的样本上限）",
          pf.TURN_MEASURE_SAMPLES == 200)

    # ============================================================ 任务 G
    section("任务 G · 到达判据（进入方向圆周均值 + 最终目标豁免朝向）")
    check("  HEADING_WINDOW_S = 2.5", pf.HEADING_WINDOW_S == 2.5)
    check("  ARRIVE_HEADING_TOL = 30", pf.ARRIVE_HEADING_TOL == 30.0)
    check("★ 单路点 -> WAYPOINT_MODE=False（最终目标豁免朝向）",
          pf.WAYPOINT_MODE is False)
    # _circ_mean_deg：+179 / -179 几乎同向，算术平均会得 0°
    cm = pf._circ_mean_deg([179.0, -179.0])
    check("★ 圆周均值：+179/-179 -> 180（算术平均会得 0°）",
          cm is not None and abs(abs(cm) - 180.0) < 1e-6, str(cm))
    check("  圆周均值：[0,90] -> 45", abs(pf._circ_mean_deg([0.0, 90.0]) - 45) < 1e-6)
    check("  圆周均值：样本 <2 -> None", pf._circ_mean_deg([10.0]) is None)
    check("  圆周均值：完全对消 -> None",
          pf._circ_mean_deg([0.0, 180.0]) is None)
    # _heading_deg
    h = pf._heading_deg([(0.0, 0, 0), (1.0, 1, 0), (2.0, 2, 0)], 2.0, 2.5)
    check("★ _heading_deg 沿 +X -> 0°", h is not None and abs(h) < 1e-6, str(h))
    h = pf._heading_deg([(0.0, 0, 0), (1.0, 0, 1), (2.0, 0, 2)], 2.0, 2.5)
    check("  _heading_deg 沿 +Z -> 90°", h is not None and abs(h - 90) < 1e-6, str(h))
    h = pf._heading_deg([(0.0, 0, 0), (1.0, 1, 0), (2.0, 2, 0)], 2.0, 0.5)
    check("  _heading_deg 窗口太窄 -> None（样本不足）", h is None, str(h))
    h = pf._heading_deg([(0.0, 0, 0), (1.0, 0, 0), (2.0, 0, 0)], 2.0, 2.5)
    check("  _heading_deg 全归零 -> None（不拿噪声当方向）", h is None, str(h))
    # 真跑到目标
    out, _, _, _ = run([(1.2, 0.0)] * 30, argv_of(target="20,0", seconds="6"))
    check("★ 走到目标附近会判「到达」并退出（回归：上一版这里 NameError）",
          "★ 到达" in out and "结果        : arrive" in out,
          [l.strip() for l in out.splitlines() if "到达" in l][:1])
    check("★ 到达判据只按距离（最终目标豁免朝向）",
          "只按距离" in out, [l.strip() for l in out.splitlines()
                           if "到达 :" in l][:1])

    # ============================================================ 任务 A
    section("任务 A · F12 急停（独立线程）+ 急停后绝不再按键")
    # ⚠️ 这里验证的是**机制**（独立线程 + Event 唤醒 + 松键回调），
    #    不是真实 F12 硬件路径 —— agent 环境按不了物理键。
    #    做法是把 simulator._user32 换成一个「报告 F12 已按下」的假实现。
    class _FakeU32:
        def __init__(self, fire_after=10 ** 9):
            self.f12_down = False
            self.calls = 0
            self.fire_after = fire_after
            self.sent = []          # 真被调用的 SendInput（应恒为空）

        def GetAsyncKeyState(self, vk):
            self.calls += 1
            if self.calls >= self.fire_after:
                self.f12_down = True
            if self.f12_down and int(vk) == 0x7B:      # VK_F12
                return 0x8000
            return 0

        def SendInput(self, n, arr, sz):
            self.sent.append(n)
            return n

    fake = _FakeU32()
    SIM._user32 = lambda: fake
    try:
        rel_calls = []
        ev = pf._PanicWatcher(release_cb=lambda: rel_calls.append(time.monotonic()),
                              on_panic=lambda: None)
        t_start = time.monotonic()
        err = ev.start()
        check("★ 监听线程启动无异常", err == "", err)
        fake.f12_down = True               # 模拟「用户按下 F12」
        t_press = time.monotonic()
        deadline = t_press + 1.0
        while not ev.detected_at and time.monotonic() < deadline:
            time.sleep(0.0005)
        detect_ms = (ev.detected_at - t_press) * 1000 if ev.detected_at else 9e9
        while not ev.stop.is_set() and time.monotonic() < deadline:
            time.sleep(0.0005)
        signal_ms = (time.monotonic() - t_press) * 1000
        check("★ F12 被检测到", ev.fired)
        check(f"★ 检测延迟 {detect_ms:.0f}ms < 100ms（安全关键）",
              detect_ms < 100, f"{detect_ms:.0f}ms")
        check(f"★ 主循环被通知 {signal_ms:.0f}ms < 300ms（含连松几下）",
              signal_ms < 300, f"{signal_ms:.0f}ms")
        check("★ 松键回调被调用（安全关键动作）",
              len(rel_calls) >= pf._PanicWatcher.REL_ROUNDS,
              f"{len(rel_calls)} 次")
        check("★ 轮询频率 ≥ 100Hz（POLL_S=5ms）",
              pf._PanicWatcher.POLL_S <= 0.01, f"{pf._PanicWatcher.POLL_S}s")
        ev.shutdown()
        check("shutdown 能干净收尾", not ev._thread.is_alive())
    finally:
        SIM._user32 = lambda: _safe

    ev2 = pf._PanicWatcher(release_cb=lambda: None, on_panic=lambda: None)
    ev2.start()
    time.sleep(0.2)
    check("未按 F12 时不误触发", not ev2.stop.is_set() and not ev2.fired)
    ev2.shutdown()

    # ★★ 急停**发生在跑动途中**时，绝不能「松开后又按下去」
    fake2 = _FakeU32(fire_after=100)      # 约 0.5s 后按下，够跑好几帧
    SIM._user32 = lambda: fake2
    try:
        out, keys, _, _ = run([(1.2, 0.0)] * 40, argv_of(seconds="20"))
        downs = [i for i, (a, v) in enumerate(keys)
                 if a == "down" and v == W_VK]
        ups = [i for i, (a, v) in enumerate(keys)
               if a == "up" and v == W_VK]
        check("★ 跑动途中 F12 触发 -> 结果标为 panic",
              "结果        : panic" in out,
              [l.strip() for l in out.splitlines() if "结果" in l][:1])
        check("★ 急停后 W 确实被松开", len(ups) >= 1, f"up@{ups}")
        check("★★ 松开之后**再没有任何 key_down(W)**",
              bool(ups) and (not downs or max(downs) < min(ups)),
              f"last down@{max(downs) if downs else None} "
              f"first up@{min(ups) if ups else None}")
    finally:
        SIM._user32 = lambda: _safe

    # ============================================================ 任务 H
    section("任务 H · --start-world 必填（拒绝自动猜起点）")
    sys.argv = ["x", "--target-world", "100,0"]
    try:
        pf._parse_args()
        check("★ 缺 --start-world 必须报错退出", False, "居然解析成功了")
    except SystemExit as e:
        check("★ 缺 --start-world 时 SystemExit(2)", e.code == 2, str(e.code))
    check("★ 报错文案含用户原话「不知道它会跑哪去」",
          "不知道它会跑哪去" in _src)
    check("★ 报错文案说明去哪拿坐标（e2e_teleport / pos=）",
          "e2e_teleport.py" in _src and "pos=" in _src)
    sys.argv = ["x", "--target-world", "100,0", "--start-world", "1,2"]
    check("给了 --start-world 就用它", pf._parse_args()["start"] == (1.0, 2.0))
    sys.argv = ["x", "--start-world", "1,2"]
    try:
        pf._parse_args()
        check("★ 缺 --target-world 必须报错退出", False, "居然解析成功了")
    except SystemExit as e:
        check("★ 缺 --target-world 时 SystemExit(2)", e.code == 2, str(e.code))
    sys.argv = ["x"]

    # ============================================================ 任务 C
    section("任务 C · 距离体检（A~H 重写曾误删，这里断言它还在）")
    check("★ 启动即崩的回归：main() 完整跑完不抛异常",
          isinstance(out, str) and len(out) > 0)
    out, _, _, _ = run([(1.2, 0.0)] * 10, argv_of(target="3,0", seconds="2"))
    check("★ 目标 < 5m 拒绝启动", "目标太近" in out,
          [l.strip() for l in out.splitlines() if "太近" in l][:1])
    out, _, _, _ = run([(1.2, 0.0)] * 30, argv_of(target="900,0", seconds="3"))
    check("★ 目标 > 300m 打印警告但不阻止",
          "> 300m" in out and "不阻止" in out,
          [l.strip() for l in out.splitlines() if "300m" in l][:1])
    check("★ 警告文案含「直线距离」", "直线距离" in out)
    check("路点信息含起点/目标",
          all(k in out for k in ("起点", "目标", "直线距离")))
    check("F12 急停线程在主循环前就已启动提示", "F12 急停" in out,
          [l.strip() for l in out.splitlines() if "F12" in l][:1])

    # ============================================================ 编码
    section("源码卫生")
    raw = SCRIPT.read_bytes()
    n_crlf = raw.count(b"\r\n")
    check("★ 文件是纯 LF（无 CRLF 混用）", n_crlf == 0, f"crlf={n_crlf}")
    txt = raw.decode("utf-8")
    n_bad = txt.count(chr(0xFFFD))
    check("★ 无 U+FFFD 损坏字符（上一次重写混进了 2 处）",
          n_bad == 0, f"{n_bad} 处")
    # ★ 这条以前是「源码里根本没有 importlib.util」（防死导入）。
    #   2026-10-05 加了 --use-llm 之后，_load_path_planner() **真的**要用它
    #   按文件加载同目录的 path_planner.py —— 但刻意**懒加载**：
    #   不用 --use-llm 的人永远不需要付这个 import 的代价，
    #   而本文件的惯例（_PanicWatcher 里的 import threading）也是懒加载。
    #   所以断言改成「只在函数体内出现、顶层没有」。
    #   ★ 判「顶层」必须看**列 0**，不能 lstrip —— 那样函数体里那行
    #     缩进的 import 也会被当成顶层，这条检查就永远失败了。
    _top_level_ilu = any(
        ln.startswith("import importlib.util") for ln in txt.splitlines())
    check("★ importlib.util 不在顶层（懒加载，只在 --use-llm 时付代价）",
          not _top_level_ilu,
          "顶层出现次数=0" if not _top_level_ilu else "顶层有 import")
    check("★ importlib.util 只在 _load_path_planner 里懒加载一次",
          txt.count("import importlib.util") == 1
          and "def _load_path_planner" in txt
          and txt.index("def _load_path_planner")
          < txt.index("import importlib.util"),
          f"出现 {txt.count('import importlib.util')} 次")
    for dead in ("GIVE_UP_WAIT_MS", "CLIMB_RELEASE_MS", "_next_waypoint",
                 '_arc'):
        check(f"★ 死符号 `{dead}` 已清理", dead not in txt)
    check("★ 全程零真实输入注入（harness 哨兵未被触发）",
          not _safe.sent and not fake.sent and not fake2.sent,
          f"_safe={_safe.sent} fake={fake.sent} fake2={fake2.sent}")

finally:
    SIM.Win32Backend.send = _real_send
    SIM._user32 = _real_user32

# ======================================================================
# 2026-10-08 · 179° 大角度转向回归（本轮两个真机 bug 的现场）
# ----------------------------------------------------------------------
# 现象：--start-world 1928.4,-739.9 --target-world 1968.4,-739.9 --live
#       θ_m=-179° θ_t=0° -> Δ=179°。
#       前 8 秒方向对（θ_m -179 -> -113，Δ +179 -> +121），
#       t=8.7s 起「转向余量挂了 31/32/33... 帧，作废」每帧刷屏，
#       同时每 ~6s 一次「打滑恢复」（Δ 170.8 / 102.3 / 18.0，全程在降）。
# ======================================================================
print("\n" + "=" * 92)
print("=== 179° 大角度转向 · bug 1：队列永不归零的数学证明 ===")
print("=" * 92)

_budget = pf.MOUSE_SATURATION_PX * max(1, pf.SPLIT_MAX_SEGMENTS) * pf.MOUSE_DEG_PER_PX
check("★ 每帧转向预算 = 40px × 0.07 = 2.8°/帧",
      abs(_budget - 2.8) < 1e-9, f"{_budget:.2f}")
_drain_deg = _budget / pf.TURN_FRACTION
check(f"★ 队列归零门槛 |Δ| <= {_drain_deg:.2f}°（want<=预算）",
      abs(_drain_deg - 2.8 / 0.45) < 1e-6, f"{_drain_deg:.3f}°")
check("★★ 而转向死区 8.0° > 归零门槛 -> 只要在转向，队列**永不归零**",
      pf.TURN_DEADZONE_DEG > _drain_deg,
      f"死区 {pf.TURN_DEADZONE_DEG}° > 门槛 {_drain_deg:.2f}°"
      "  ==> 旧的 30 帧判废对任何超过 30 帧的转向都必炸")
check("★★ 所以作废判据**不能**是帧数（已退居 300 帧 panic 兜底）",
      pf.TURN_QUEUE_MAX_FRAMES >= 300, str(pf.TURN_QUEUE_MAX_FRAMES))

print("\n" + "=" * 92)
print("=== 179° 大角度转向 · bug 1：同号不递减，不再作废 ===")
print("=" * 92)


def _sim_turn_queue(d_seq, reset_age_on_fire=True):
    """复刻主循环里那段队列逻辑（原样搬过来跑，返回作废/反号事件与账本轨迹）。

    ★ 刻意**不复用**主循环代码：这里就是一份「独立实现」，
      两边对不上才说明主循环写错了 —— 直接调主循环会掩盖问题。

    :param reset_age_on_fire: 作废时是否把账龄清零。
        ``True`` = 现有行为；``False`` = **修前**行为（旧代码漏了这一步，
        于是越过上限后每帧都打印 —— 真机日志 31/32/33… 连着刷的原因）。

    :returns: ``(作废事件, 反号事件, 峰值幅值, 末值, 逐帧账本)``
    """
    q = 0.0
    age = 0
    fires = []
    turns = []
    hist = []
    max_q = 0.0
    for k, d in enumerate(d_seq):
        want, _cap, _px = pf._turn_plan(float(d))
        turned = (q != 0 and (want >= 0) != (q >= 0))
        tgt = want
        if (not turned and q != 0 and (q >= 0) == (want >= 0)
                and abs(q) > abs(want)):
            tgt = q
        _segs, rest = pf._turn_dispatch(tgt)
        if turned:
            turns.append((k, q, want))
            q = 0.0
            age = 0
        else:
            q = rest
            max_q = max(max_q, abs(q))
            if q != 0:
                age += 1
                if age > pf.TURN_QUEUE_MAX_FRAMES:
                    fires.append((k, age, rest))
                    q = 0.0
                    if reset_age_on_fire:
                        age = 0
            else:
                age = 0
        hist.append(q)
    return fires, turns, max_q, q, hist


# 真机轨迹近似：Δ 从 179° 起每帧降 1.5°（θ_m -179 -> -113 用掉 8s ≈ 44 帧）
_seq179 = [179.0 - 1.5 * k for k in range(120)]
_fires, _turns, _maxq, _left, _hist = _sim_turn_queue(_seq179)
check("★★ 179°→0° 全程 120 帧：一次都不作废（修前是每帧刷屏）",
      len(_fires) == 0, f"作废 {len(_fires)} 次，前 3 次={_fires[:3]}")
check("★ 欠账全程有界（被 |want|-预算 自限，不会跑飞）",
      _maxq < 60.0, f"峰值 {_maxq:.1f}°（理论上限 |60|-2.8=57.2°）")
check("★ Δ 进死区后队列归零",
      _left == 0.0, f"末值 {_left}")

# ---- 修前对照：同一条轨迹，把上限按回 30，且**不**清账龄（= 旧代码）----
_saved = pf.TURN_QUEUE_MAX_FRAMES
try:
    pf.TURN_QUEUE_MAX_FRAMES = 30
    _old_fires, _t30, _q30, _l30, _h30 = _sim_turn_queue(
        _seq179, reset_age_on_fire=False)          # ★ 旧代码就是不清账龄
    _new_fires, _tn30, _qn30, _ln30, _hn30 = _sim_turn_queue(
        _seq179, reset_age_on_fire=True)           # 只把上限按回 30
finally:
    pf.TURN_QUEUE_MAX_FRAMES = _saved
check("★ 对照：上限 30 + 旧逻辑（不清账龄）-> 第 31 帧起**每帧都作废**",
      len(_old_fires) > 60,
      f"120 帧里作废 {len(_old_fires)} 次，首 5 次(帧,年龄,欠账)="
      f"{[(a, b, round(c, 1)) for a, b, c in _old_fires[:5]]}")
check("★ 对照：首个作废发生在第 31 帧（与真机日志「挂了 31 帧」一致）",
      _old_fires and _old_fires[0][1] == 31
      and abs(_old_fires[0][0] - 30) <= 1,
      f"帧 {_old_fires[0][0]} / 账龄 {_old_fires[0][1]}" if _old_fires else "-")
check("★ 对照：补上「作废时清账龄」后，刷屏变成每 ~31 帧一次",
      0 < len(_new_fires) < len(_old_fires) / 5,
      f"{len(_old_fires)} 次 -> {len(_new_fires)} 次")
check("★ 对照：作废瞬间的欠账量级与真机日志一致（-57° vs 实测 -51.9°）",
      _old_fires and -60.0 < _old_fires[0][2] < -40.0,
      f"{_old_fires[0][2]:+.1f}°" if _old_fires else "-")

print("\n" + "=" * 92)
print("=== 179° 大角度转向 · A2：方向反号立刻作废（这才是真的方向变了）===")
print("=" * 92)
# 前 60 帧 Δ>0 收敛到 89°，然后**翻到负方向**（-170 起），模拟目标跑到背后
_rev_seq = [179.0 - 1.5 * k for k in range(60)] + [-170.0 + 2.0 * k
                                                  for k in range(40)]
_f2, _t2, _m2, _l2, _h2 = _sim_turn_queue(_rev_seq)
check("★ 目标翻到反方向时**只作废一次**（不是等帧数）",
      len(_t2) == 1,
      f"反号事件 {len(_t2)} 次 "
      f"{[(a, round(b, 1), round(c, 1)) for a, b, c in _t2]}")
check("★ 反号那一帧的旧账确实与新 want 反号",
      _t2 and (_t2[0][1] >= 0) != (_t2[0][2] >= 0),
      f"旧账 {_t2[0][1]:+.1f}° / 新 want {_t2[0][2]:+.1f}°" if _t2 else "-")
if _t2:
    _k = _t2[0][0]
    check("★ 反号前一刻账本还在**旧方向**",
          _h2[_k - 1] < 0, f"帧 {_k - 1}: {_h2[_k - 1]:+.1f}°")
    check("★ 反号帧账本被**清零**（旧账 -37.9° 被丢弃，不是被继续还）",
          _h2[_k] == 0.0, f"帧 {_k}: {_h2[_k]:+.1f}°（丢弃了 {_t2[0][1]:+.1f}°）")
    check("★ 反号后一帧**在新方向**重建",
          _h2[_k + 1] > 0, f"帧 {_k + 1}: {_h2[_k + 1]:+.1f}°")
else:
    check("★ 反号前一刻账本还在旧方向", False, "没有反号事件")
    check("★ 反号帧账本被清零", False, "-")
    check("★ 反号后一帧在新方向重建", False, "-")
check("★ 反向段没有触发任何 panic 作废（判的是方向，不是年龄）",
      len(_f2) == 0, f"{len(_f2)} 次")

print("\n" + "=" * 92)
print("=== 179° 大角度转向 · bug 2：慢收敛不再被判打滑 ===")
print("=" * 92)
for _lbl, _d0, _step in (("#1 Δ≈170.8", 179.0, 1.3),
                         ("#2 Δ≈102.3", 110.0, 1.3),
                         ("#3 Δ≈18.0", 24.0, 0.6)):
    _w = [_d0 - _step * k for k in range(pf.SLIP_CONVERGE_N)]
    _hist: list[float] = []
    for _v in _w:
        _hist.append(abs(_v))
        while len(_hist) > pf.SLIP_CONVERGE_N:
            _hist.pop(0)
    check(f"★ 真机打滑 #{_lbl} 的 Δ 序列 -> 判为收敛（不再打断转向）",
          pf._is_converging(_hist, pf.SLIP_CONVERGE_N) is True,
          f"每帧降 {_step}°，窗口 {pf.SLIP_CONVERGE_N} 帧")
# 真打滑必须**仍然**被抓到，否则就是把安全阀拆了
_bad = [float(abs(v)) for v in (30.0, 31.0, 33.0, 36.0, 40.0, 45.0,
                                51.0, 58.0, 66.0, 75.0)]
check("★★ 真打滑（Δ 持续变大）-> 仍判未收敛（安全阀没被拆掉）",
      pf._is_converging(_bad, pf.SLIP_CONVERGE_N) is False)
_flat = [30.0] * pf.SLIP_CONVERGE_N
check("★★ Δ 持平（真卡死）-> 判未收敛",
      pf._is_converging(_flat, pf.SLIP_CONVERGE_N) is False)

# ======================================================================
# 2026-10-08 · 大角度站定转（拆段加速）
# ----------------------------------------------------------------------
# 物理式：边走边转 Δ=179° 的弧长 = π·v/ω。ω=2.8°/帧÷0.2s=14°/s，
#   v≈3m/s -> 弧长 38.6m，与真机反推的 32.8m 同量级。
# 新机制：|Δ|>60° 就**一直不按 W**，每帧把转向拆成 8 段、段间 40ms，
#   单帧转向量从 2.8° 抬到 22.4°，整次转向压到 ~8 帧。
# ⚠️ 站定第 8 帧起位移窗口全是静止样本 -> θ_m 断 -> 判据拿不到输入。
#   所以「一次站到底」结构上做不到，必须有安全阀强制走一帧。
# ======================================================================
print("\n" + "=" * 92)
print("=== 大角度站定转 · 状态机 + 拆段加速 ===")
print("=" * 92)


def _run_stand(d_seq):
    """按帧喂 Δ，返回 (事件序列, 每帧的 active 序列)。"""
    act = False
    evs, acts = [], []
    for k, d in enumerate(d_seq):
        act, ev = pf._stand_turn_step(act, float(d))
        evs.append((k, ev, d))
        acts.append(act)
    return evs, acts


# ---- 用例 1：|Δ|=179° 起步，站定转到 <=20° 才恢复 ----
_traj179 = [179.0 - 1.5 * k for k in range(120)]
_ev1, _a1 = _run_stand(_traj179)
_starts = [k for k, e, _ in _ev1 if e == "start"]
_dones = [k for k, e, _ in _ev1 if e == "done"]
check("★★ |Δ|=179° 第 0 帧立刻进入站定", _starts == [0], str(_starts))
check("★★ 一直站定到 Δ<=20° 才退出（中途**一次都不按 W**）",
      len(_dones) == 1 and _traj179[_dones[0]] <= pf.TURN_RESUME_DEG,
      f"退出于帧 {_dones}（Δ={_traj179[_dones[0]]:+.1f}°）" if _dones else "-")
check("★ 站定期间每一帧 active=True（不再有 3:1 断续占空比）",
      all(_a1[:_dones[0]]) and not any(_a1[_dones[0] + 1:]),
      f"前 {_dones[0]} 帧全站定，之后不再进")

# ---- 用例 2：|Δ|=45° 不触发（小角度仍是边走边转）----
_ev2, _a2 = _run_stand([45.0 - 0.5 * k for k in range(60)])
check("★★ |Δ|=45° 全程不触发站定（小角度仍边走边转）",
      not any(_a2) and not any(e for _, e, _ in _ev2),
      f"active 次数={sum(_a2)}")
check("★ |Δ|=59.9° 不触发 / 60.1° 触发（阈值是 60，不是约等于）",
      not any(_run_stand([59.9] * 10)[1])
      and any(_run_stand([60.1] * 10)[1]))

# ---- 用例 3：滞回 65° -> 59° 不退出，20° 才退出 ----
_hyst = [65.0, 64.0, 62.0, 61.0, 60.0, 59.0, 58.0, 40.0, 25.0, 21.0, 20.0, 19.0]
_ev3, _a3 = _run_stand(_hyst)
_I21, _I20 = _hyst.index(21.0), _hyst.index(20.0)
check("★ 从 65° 起进入站定", _ev3[0][1] == "start", str(_ev3[0]))
check("★★ 滞回：65°->59° 期间**不退出**（20°~60° 是死区）",
      not any(e == "done" for k, e, _ in _ev3[:_I21]),
      f"前 {_I21} 帧无 done 事件")
check("★★ 滞回：Δ=21.0 不退 / Δ=20.0 退（规格写的是 <= 20）",
      _ev3[_I21][1] != "done" and _ev3[_I20][1] == "done"
      and not any(e == "done" for k, e, _ in _ev3[:_I20]),
      f"帧 {_I21}:{_ev3[_I21][1]!r}  帧 {_I20}:{_ev3[_I20][1]!r}")
check("★ 滞回带宽 = 40°（(20,60] 内状态不变）",
      abs((pf.TURN_STAND_DEG - pf.TURN_RESUME_DEG) - 40.0) < 1e-9)

# ---- 用例 4：连续多帧都 >60° 不会反复触发 ----
_ev4, _a4 = _run_stand([179.0, 178.0, 177.0, 176.0, 175.0, 174.0])
check("★★ 连续 6 帧都 >60°：只 start 一次，不反复进出",
      [k for k, e, _ in _ev4 if e == "start"] == [0]
      and not any(e == "done" for _, e, _ in _ev4),
      f"start={[k for k, e, _ in _ev4 if e == 'start']} "
      f"done={[k for k, e, _ in _ev4 if e == 'done']}")

# ---- 镜像方向 ----
_evm, _am = _run_stand([-179.0 + 1.5 * k for k in range(120)])
check("★ 负方向同样工作（判据用 abs）",
      _evm[0][1] == "start"
      and len([1 for _, e, _ in _evm if e == "done"]) == 1,
      f"start={[k for k, e, _ in _evm if e == 'start']} "
      f"done={[k for k, e, _ in _evm if e == 'done']}")
check("★ 正负轨迹的 active 序列完全一致（纯对称）", _am == _a1)

# ---- 拆段加速：单帧转向量真的抬上去了吗 ----
_sg_norm, _rest_norm = pf._turn_dispatch(-60.0)
check("★ 常态：1 段 40px = 2.8°/帧",
      len(_sg_norm) == 1 and sum(abs(x) for x in _sg_norm) == 40
      and abs(40 * pf.MOUSE_DEG_PER_PX - 2.8) < 1e-9,
      f"{_sg_norm} = {sum(abs(x) for x in _sg_norm) * pf.MOUSE_DEG_PER_PX:.1f}°")
_sg_st, _rest_st = pf._turn_dispatch(-60.0, pf.TURN_STAND_SEGMENTS)
_px_st = sum(abs(x) for x in _sg_st)
check("★★ 站定：拆 8 段共 320px = 22.4°/帧（8 倍加速）",
      len(_sg_st) == 8 and _px_st == 320
      and abs(_px_st * pf.MOUSE_DEG_PER_PX - 22.4) < 1e-6,
      f"{len(_sg_st)} 段 { _px_st}px = {_px_st * pf.MOUSE_DEG_PER_PX:.1f}°")
check("★★ 每段仍 <= 饱和阈值（段数不放松「一次多少」）",
      all(abs(x) <= pf.MOUSE_SATURATION_PX for x in _sg_st),
      f"最大单段 {max((abs(x) for x in _sg_st), default=0)}px")
check("★ 179° 理论上 8 帧转完（100% 达成率）",
      179.0 <= 8 * _px_st * pf.MOUSE_DEG_PER_PX + 1e-9,
      f"8×22.4 = {8 * _px_st * pf.MOUSE_DEG_PER_PX:.0f}°")
check("★ 按实测达成率 74% 只有 ~133° —— 缺口靠安全阀兜",
      8 * _px_st * pf.MOUSE_DEG_PER_PX * 0.74 < 179.0,
      f"{8 * _px_st * pf.MOUSE_DEG_PER_PX * 0.74:.0f}° < 179°")
_sg_cap, _rest_cap = pf._turn_dispatch(-179.0, 3)
check("★ 段数上限真生效（传 3 就只拆 3 段，截断部分留成欠账）",
      len(_sg_cap) == 3 and _rest_cap < 0,
      f"{len(_sg_cap)} 段 {_sg_cap}，仍欠 {_rest_cap:+.1f}°")

# ---- 段间隔 ----
check("★★ 段间隔 40ms > 30fps 一帧（33.3ms），拆分不被并进同一帧",
      pf.TURN_STAND_SEG_SLEEP_MS > 1000.0 / 30.0,
      f"{pf.TURN_STAND_SEG_SLEEP_MS:g}ms > {1000 / 30:.1f}ms")
check("★ 阻塞代价可算：每帧 7 段间隔 = 280ms",
      abs(7 * pf.TURN_STAND_SEG_SLEEP_MS - 280.0) < 1e-9)

# ---- 安全阀：阈值必须高于朝向门禁（赶在死之前救）----
check("★★ 安全阀阈值 > 朝向门禁（否则等 θ_m 死了才救，来不及）",
      pf.TURN_STAND_BLIND_M > pf.WINDOW_MOVE_MIN,
      f"{pf.TURN_STAND_BLIND_M:g}m > {pf.WINDOW_MOVE_MIN:g}m")

# ---- 不动的东西 ----
check("★ 没动 _turn_plan 的比例律（仍是 0.45x）",
      pf.TURN_FRACTION == 0.45, str(pf.TURN_FRACTION))
check("★ 没动常态每帧预算（仍 40px = 2.8°）",
      pf.SPLIT_MAX_SEGMENTS == 1 and pf.MOUSE_SATURATION_PX == 40)
check("★ 没动打滑参数（10 / 12° / 3 次）",
      pf.SLIP_CONVERGE_N == 10 and pf.SLIP_MIN_ERR_DEG == 12.0
      and pf.SLIP_MAX_RECOVERIES == 3)
check("★ 没动脱困参数", pf.ESCAPE_MAX_LEVEL == 3)
check("★ 没动小角度死区（8°）", pf.TURN_DEADZONE_DEG == 8.0)
_src_st = Path(pf.__file__).read_text(encoding="utf-8")
check("★★ 断续站定的死符号已彻底删除",
      all(k not in _src_st for k in ("TURN_STAND_FRAMES", "TURN_WALK_FRAMES",
                                     "_stand_phase", "_stand_hold",
                                     "def w_only(")),
      "3:1 断续那套全部清干净")
check("★ Bug1 的反号作废逻辑仍在（站定不能把它一起删掉）",
      "_turned_around" in _src_st and "转向余量作废" in _src_st)
check("★ 段间隔夹在段与段之间（不是攒到最后一次性等）",
      "_gap_ms / 1000.0" in _src_st)
# ---------------------------------------------------------------- 汇总
print("\n" + "=" * 92)
print(f"结果: {len(PASS)}/{len(PASS) + len(FAIL)} 通过")
if FAIL:
    print("  失败: " + ", ".join(FAIL))
print("=" * 92)
sys.exit(1 if FAIL else 0)
