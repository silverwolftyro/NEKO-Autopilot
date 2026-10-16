"""执行层按键语义单测（§37 松键 / §39 普攻点按）。

★ 背景：两个被真机实测抓出来的 bug
----------------------------------
1. **§37 松键**：``NORMAL_ATTACK`` 走 ``mouse_down``（长按语义），
   而 ``finally`` 只在 ``outcome != OK`` 时 ``release_all()``。
   「普攻成功 -> 切闪避」这条最常见的路径上，左键一直按���。
2. **§39 语义**：长按左键在原神里是**蓄力重击**，不是「攻击得更久」。
   --live 首测 6 帧意图只打出 1 次重击。于是普攻改成**点按（tap）**。

★ 为什么长按相关的测试用 MOVE_FORWARD 不用 NORMAL_ATTACK
---------------------------------------------------------
§39 之后 NORMAL_ATTACK **不再是长按动作**，它是「按一下就完事」。
拿它测松键逻辑会测到不存在的东西。这里统一用 ``MOVE_FORWARD``
（``holds_keys=True``、``min_gap_ms=0``、场景不限）当长按替身 ——
而且按住 W 本来就是「走路」的正确实现，不是错误语义。

★ 为什么先修 DryRunBackend 与 release_all
------------------------------------------
修之前 ``DryRunBackend`` 只跟踪键盘按下，鼠标键从不进 ``_down``；
而 ``release_all()`` 一律发 ``_mk_key(up=True)`` —— 对 ``VK_LBUTTON``
来说那是「键盘 A 键松开」，左键**根本没被松开**，只是返回值里有个 1。
两个问题都会让下面的断言无法成立，且干跑与真机不同口径。

⚠️ 本文件**不 import** Capturer / YOLO / 任何模型 —— 纯逻辑，可秒级跑完。
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
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(PLUGIN_DIR)
sys.path.insert(0, str(ROOT))

from autopilot.actuator.executor import IntentExecutor, Outcome   # noqa: E402
from autopilot.actuator.safety import SafetyConfig, SafetyGuard   # noqa: E402
from autopilot.actuator.simulator import (                        # noqa: E402
    DryRunBackend, InputSimulator, VK,
)
from autopilot.actuator.traits import (                           # noqa: E402
    DODGE_TAP_HOLD_MS, NORMAL_ATTACK_TAP_HOLD_MS, traits_of,
)
from autopilot.contracts.intent_packet import IntentPacket         # noqa: E402
from autopilot.contracts.state_packet import (                    # noqa: E402
    ResourceInfo, SceneInfo, StatePacket,
)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f"   {detail}" if detail else ""))


def section(title):
    print("\n" + "=" * 92)
    print(f"  {title}")
    print("=" * 92)


LB, SHIFT, W, E = int(VK.LBUTTON), int(VK.SHIFT), int(VK.W), int(VK.E)

#: 长按替身（§39 后 NORMAL_ATTACK 不再长按）
HOLD = "MOVE_FORWARD"

# ★ 帧间隔 300ms：> SafetyConfig.rate_limit_ms(50)，否则连续同动作会被
#   RATE_LIMITED 挡掉 —— 那样测到的是拒绝路径，不是按键语义。
TICK_MS = 300
_T0 = int(time.time() * 1000)      # 用真实墙钟：validator 的 TTL 检查拿
                                   # intent.issued_at_ms 与 now_ms 比，
                                   # 假时间戳会撞 CLOCK_SKEW。


def mk_state(sv: int = 1, scene: str = "WORLD") -> StatePacket:
    return StatePacket(
        scene=SceneInfo(id=scene, confidence=0.9),
        resource=ResourceInfo(perception_ok=True),
        state_version=sv, timestamp_ms=_T0,
    )


def mk_intent(action: str, sv: int = 1, scene: str = "WORLD", **params):
    return IntentPacket.make(
        action, state_version=sv, ttl_seconds=10,
        scene_required=(scene,), source="LOCAL", params=params or None,
    )


def mk_executor(rate_limit_ms: int = 50) -> tuple[IntentExecutor, DryRunBackend]:
    be = DryRunBackend()
    ex = IntentExecutor(
        simulator=InputSimulator(backend=be),
        # 未设前景标题 -> is_foreground_ok() 恒 True，不依赖 win32gui
        guard=SafetyGuard(SafetyConfig(rate_limit_ms=rate_limit_ms)),
    )
    return ex, be


def mouse_vk(be: DryRunBackend, vk: int, down: bool) -> list:
    return [e for e in be.events
            if e.kind == "mouse" and e.vk == vk and e.down is down]


def key_vk(be: DryRunBackend, vk: int, down: bool) -> list:
    return [e for e in be.events
            if e.kind == "key" and e.vk == vk and e.down is down]


# ================================================================ R0
section("R0 · DryRunBackend 看得见鼠标键 + release_all 发的是鼠标抬起")

be0 = DryRunBackend()
sim0 = InputSimulator(backend=be0)
sim0.mouse_down(VK.LBUTTON)
check("mouse_down 后 is_key_down(LBUTTON) 为 True",
      sim0.is_key_down(VK.LBUTTON), str(be0.pressed_keys))
sim0.mouse_up(VK.LBUTTON)
check("mouse_up 后 is_key_down(LBUTTON) 为 False",
      not sim0.is_key_down(VK.LBUTTON), str(be0.pressed_keys))
# ★ 必须**重新按住**再 release_all —— 上面已经 mouse_up 过，
#   此时什么都没按着，release_all 返回空是正确行为。
sim0.mouse_down(VK.LBUTTON)
released = sim0.release_all()
check("★ 按住时 release_all 真的发出鼠标抬起（旧实现发的是键盘 up）",
      LB in released, f"released={released}")
check("★ release_all 之后左键确实松开",
      not sim0.is_key_down(VK.LBUTTON), str(be0.pressed_keys))
up_events = [e for e in be0.events
             if e.kind == "mouse" and e.vk == LB and not e.down]
check("★ 发出去的是 mouse 事件而不是 key 事件",
      len(up_events) == 2, f"mouse_up={len(up_events)}")
sim0.key_down(VK.E)
check("键盘键仍然照常跟踪（没被鼠标改动带坏）", sim0.is_key_down(VK.E))

be1 = DryRunBackend()
sim1 = InputSimulator(backend=be1)
sim1.mouse_move_relative(10, 10)
check("鼠标移动不进 _down（vk=0 被跳过）",
      not be1.pressed_keys, str(be1.pressed_keys))

# ================================================================ R1
section("R1 · §39 普攻 = 点按 tap（每次都是完整的一对 down/up）")

t_na = traits_of("NORMAL_ATTACK")
check("★ NORMAL_ATTACK 不再是长按动作（holds_keys=False）",
      t_na.holds_keys is False, str(t_na.holds_keys))
check("★ tap_hold_ms 有值且为正",
      t_na.tap_hold_ms > 0, str(t_na.tap_hold_ms))
check("★ 模块常量与 traits 一致",
      t_na.tap_hold_ms == NORMAL_ATTACK_TAP_HOLD_MS,
      f"{t_na.tap_hold_ms} vs {NORMAL_ATTACK_TAP_HOLD_MS}")
check("★ max_duration_ms 已从 30000 降到 2000",
      t_na.max_duration_ms == 2_000, str(t_na.max_duration_ms))

ex1, be = mk_executor()
st = mk_state()
t0 = time.perf_counter()
r1 = ex1.execute(mk_intent("NORMAL_ATTACK"), st, now_ms=_T0)
elapsed = (time.perf_counter() - t0) * 1000

check("一次普攻执行 OK", r1.outcome is Outcome.OK, r1.outcome.value)
check("★ 一次普攻 = 恰好一对 down/up",
      len(mouse_vk(be, LB, True)) == 1 and len(mouse_vk(be, LB, False)) == 1,
      f"down={len(mouse_vk(be, LB, True))} up={len(mouse_vk(be, LB, False))}")
check("★ 点按不进 held_keys（没有「还按着」的状态）",
      ex1.held_keys == (), str(ex1.held_keys))
check(f"★ 真实保持时长 ≈ {NORMAL_ATTACK_TAP_HOLD_MS}ms（耗 {elapsed:.0f}ms）",
      elapsed >= NORMAL_ATTACK_TAP_HOLD_MS * 0.8,
      f"elapsed={elapsed:.0f}ms")
check("★ steps 如实写了 tap 时长",
      any("tap" in s for s in r1.steps), str(r1.steps))

# 三次普攻 = 三对 down/up（不再是「一次长按」）
ex1b, be1b = mk_executor()
for i in range(3):
    ex1b.execute(mk_intent("NORMAL_ATTACK"), st, now_ms=_T0 + i * TICK_MS)
check("★ 3 次普攻 = 3 对 down/up（不是一次长按）",
      len(mouse_vk(be1b, LB, True)) == 3 and len(mouse_vk(be1b, LB, False)) == 3,
      f"down={len(mouse_vk(be1b, LB, True))} up={len(mouse_vk(be1b, LB, False))}")
check("★ 3 次普攻后仍然没有键按着", ex1b.held_keys == (), str(ex1b.held_keys))

# 时长可由 params 覆盖（标定后不用改代码）
ex1c, be1c = mk_executor()
ex1c.execute(mk_intent("NORMAL_ATTACK", hold_ms=40), st, now_ms=_T0)
check("★ params.hold_ms 能覆盖 tap 时长（便于标定）",
      any("40ms" in s for s in ex1c.history[-1].steps),
      str(ex1c.history[-1].steps))

# ================================================================ R2
section("R2 · 长按动作切换 -> 先松后按（§37，用 MOVE_FORWARD→SPRINT）")

# ★ 切换的**两端都必须是长按动作**才有意义（§40 之后 DODGE 改点按了）。
#   SPRINT 是剩下少数几个真正该长按的动作之一（按住才是冲刺）。
ex2, be2 = mk_executor()
st2 = mk_state()
r_a = ex2.execute(mk_intent(HOLD), st2, now_ms=_T0)
r_b = ex2.execute(mk_intent("SPRINT"), st2, now_ms=_T0 + TICK_MS)

check("两次执行都 OK",
      r_a.outcome is Outcome.OK and r_b.outcome is Outcome.OK,
      f"{r_a.outcome.value} / {r_b.outcome.value}")
check("★ 切换时 W 被松开", len(key_vk(be2, W, False)) == 1,
      f"up={len(key_vk(be2, W, False))}")
check("★ Shift 已按下", len(key_vk(be2, SHIFT, True)) == 1)
check("★ held_keys 变成 (SHIFT,)",
      ex2.held_keys == (SHIFT,), str(ex2.held_keys))

order = [("Wup" if (e.kind == "key" and e.vk == W and not e.down)
          else "Sdown" if (e.kind == "key" and e.vk == SHIFT and e.down)
          else None) for e in be2.events]
order = [x for x in order if x]
check("★ 顺序是 Wup -> Sdown（先松后按）", order == ["Wup", "Sdown"], str(order))
check("steps 里记录了这次切换释放",
      any("release" in s for s in r_b.steps), str(r_b.steps))
check("★ 刚按下的 Shift 没有被误松",
      len(key_vk(be2, SHIFT, False)) == 0)

# ================================================================ R3
section("R3 · 长按 -> 点按类动作 -> 也要松")

ex3, be3 = mk_executor()
st3 = mk_state()
r3a = ex3.execute(mk_intent(HOLD), st3, now_ms=_T0)
r3b = ex3.execute(mk_intent("USE_SKILL"), st3, now_ms=_T0 + TICK_MS)

check("★ 切到 USE_SKILL 后 W 被松开",
      len(key_vk(be3, W, False)) == 1, f"up={len(key_vk(be3, W, False))}")
check("USE_SKILL 是点按，不进入 held", ex3.held_keys == (), str(ex3.held_keys))
check("★ E 键完整点按（down+up 各一次）",
      len(key_vk(be3, E, True)) == 1 and len(key_vk(be3, E, False)) == 1)

# ================================================================ R4
section("R4 · 异常/拒绝路径 -> 无条件释放（原有安全底线没被改坏）")

ex4, be4 = mk_executor()
st4 = mk_state()
ex4.execute(mk_intent(HOLD), st4, now_ms=_T0)
check("前置：W 已按住", ex4.held_keys == (W,), str(ex4.held_keys))

# 场景不匹配 -> 校验第 ① 步就 REJECTED，走不到「先松后按」，
# 只能靠 finally 的非 OK 分支兜底。这正是要验的那条路。
r4b = ex4.execute(mk_intent(HOLD, scene="COMBAT"), st4, now_ms=_T0 + TICK_MS)
check("★ 意图被拒绝", r4b.outcome is Outcome.REJECTED,
      f"{r4b.outcome.value}: {r4b.reason}")
check("★ 拒绝后 W 仍被释放（finally 兜底生效）",
      len(key_vk(be4, W, False)) == 1, f"up={len(key_vk(be4, W, False))}")
check("★ held_keys 被清空", ex4.held_keys == (), str(ex4.held_keys))
check("released_keys 记录了这次释放", W in r4b.released_keys,
      str(r4b.released_keys))

# ================================================================ R5
section("R5 · release_held() —— 「本轮无意图」时的出口")

ex5, be5 = mk_executor()
st5 = mk_state()
ex5.execute(mk_intent(HOLD), st5, now_ms=_T0)
check("前置：W 已按住", ex5.held_keys == (W,))
rel5 = ex5.release_held()
check("★ release_held() 释放了 W", W in rel5, str(rel5))
check("★ held_keys 清空", ex5.held_keys == (), str(ex5.held_keys))
check("再调一次是空操作（幂等）", ex5.release_held() == [])

# ================================================================ R6
section("R6 · 拒绝后下一条正常意图不会「双重按下」")

ex6, be6 = mk_executor()
st6 = mk_state()
ex6.execute(mk_intent(HOLD), st6, now_ms=_T0)
ex6.execute(mk_intent(HOLD, scene="COMBAT"), st6,
            now_ms=_T0 + TICK_MS)          # REJECTED，已清空 held
ex6.execute(mk_intent(HOLD), st6, now_ms=_T0 + 2 * TICK_MS)
check("★ 恢复后 W down 恰好 2 次（首次 + 恢复）",
      len(key_vk(be6, W, True)) == 2, f"down={len(key_vk(be6, W, True))}")
check("★ 最终只有 W 按住", ex6.held_keys == (W,), str(ex6.held_keys))

# ================================================================ R7
section("R7 · e2e_dryrun 的 --live 默认关闭")

E2E = Path(RECOG_ROOT / 'exports' / 'benchmark' / 'scripts' / 'misc' / 'e2e_dryrun.py')
if not E2E.is_file():
    check("e2e_dryrun.py 存在", False, str(E2E))
else:
    import importlib.util

    spec = importlib.util.spec_from_file_location("_e2e_probe", E2E)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # 模块级只改 sys.path，安全
    check("★ DEFAULT_LIVE 是 False（默认干跑）",
          getattr(mod, "DEFAULT_LIVE", None) is False,
          str(getattr(mod, "DEFAULT_LIVE", "缺失")))
    old = sys.argv
    try:
        sys.argv = ["e2e_dryrun.py"]
        a = mod._parse_args()
        check("★ 不给 --live -> live=False", a[-1] is False, str(a))
        sys.argv = ["e2e_dryrun.py", "--live"]
        check("★ 给 --live -> live=True", mod._parse_args()[-1] is True)
        sys.argv = ["e2e_dryrun.py", "--frames", "3", "--channel", "mss"]
        a3 = mod._parse_args()
        check("★ 其它参数不受影响",
              a3[1] == 3 and a3[2] == "mss" and a3[-1] is False, str(a3))
    finally:
        sys.argv = old

# ================================================================ R8
section("R8 · core/loop.py 空转分支会调 release_held（§38）")

from autopilot.core import CoreLoop                                  # noqa: E402
from autopilot.core.runtime import (                                 # noqa: E402
    RuntimeContext, RuntimeState,
)


class _FakeCapturer:
    def grab(self):
        return object()          # builder 是假的，帧内容无所谓

    def close(self):
        pass


class _FakeBuilder:
    """每 tick 递增 state_version，模拟真实的状态推进。"""

    def __init__(self):
        self.n = 0
        self.last_world_entities = None

    def build(self, _frame):
        self.n += 1
        return StatePacket(
            scene=SceneInfo(id="WORLD", confidence=0.9),
            resource=ResourceInfo(perception_ok=True),
            state_version=self.n, timestamp_ms=_T0,
        )

    def drain_events(self):
        return []


class _ScriptedPolicy:
    """按脚本返回意图：先给一条 MOVE_FORWARD，之后一直给 None。"""

    def __init__(self, script):
        self.script = list(script)
        self.polls = 0
        self.last_world_entities = None

    def poll(self, _state):
        self.polls += 1
        return self.script.pop(0) if self.script else None


cap8 = _FakeCapturer()
bld8 = _FakeBuilder()
be8 = DryRunBackend()
# ⚠️ guard 用 rate_limit_ms=0：这里是 6 个 tick 在**几毫秒内**跑完的，
#   默认 50 会把第 2 条 MOVE_FORWARD 以 RATE_LIMITED 拒掉。真机 tick 间隔
#   200ms 不会触发 —— 放开它，免得测到频率限制而不是松键逻辑。
ex8 = IntentExecutor(simulator=InputSimulator(backend=be8),
                     guard=SafetyGuard(SafetyConfig(rate_limit_ms=0)))
ctx8 = RuntimeContext(capturer=cap8, builder=bld8, policy=None, executor=ex8)
ctx8.policy = _ScriptedPolicy([mk_intent(HOLD, sv=1)])
loop8 = CoreLoop(ctx8)
ctx8.bus.start()
# 直接转状态而**不 start()**：start() 会起后台线程跑无限循环，
# 单测要的是「精确跑几�� tick」，线程既不确定也不必要。
loop8._transition(RuntimeState.RUNNING)

loop8._tick()      # tick 1：有意图 -> MOVE_FORWARD（按住 W）
check("tick1 产了意图", ctx8.policy.polls == 1, str(ctx8.policy.polls))
check("★ tick1 后 W 按着", ex8.held_keys == (W,), str(ex8.held_keys))
check("★ tick1 后确实 down 过 W", len(key_vk(be8, W, True)) == 1)
check("★ tick1 时还没松开（up=0）", len(key_vk(be8, W, False)) == 0)

loop8._tick()      # tick 2：无意图 —— §38 补的那一行
check("★ tick2（无意图）后 W 被松开",
      len(key_vk(be8, W, False)) == 1, f"up={len(key_vk(be8, W, False))}")
check("★ tick2 后 held_keys 清空", ex8.held_keys == (), str(ex8.held_keys))

for _ in range(3):
    loop8._tick()
check("★ 连续 3 次空转不抛（release_held 幂等）", True)
check("★ 连续空转后 W up 仍只有 1 次（无重复抬键）",
      len(key_vk(be8, W, False)) == 1, f"up={len(key_vk(be8, W, False))}")

ctx8.policy.script.append(mk_intent(HOLD, sv=6))
loop8._tick()
check("★ 空转后恢复意图 -> W 能重新 down（第 2 次）",
      len(key_vk(be8, W, True)) == 2, f"down={len(key_vk(be8, W, True))}")
check("★ 重新按住后 held_keys 正确", ex8.held_keys == (W,), str(ex8.held_keys))

ctx8.bus.drain_now()
loop8._state = RuntimeState.STOPPED      # 绕过状态机，只为收尾干净

# ================================================================ R9
section("R9 · §40 DODGE = 点按 Shift（点按闪避，长按=冲刺）")

t_dg = traits_of("DODGE")
check("★ DODGE 声明为非长按（holds_keys=False）",
      t_dg.holds_keys is False, str(t_dg.holds_keys))
check("★ DODGE tap_hold_ms 有值且为正", t_dg.tap_hold_ms > 0,
      str(t_dg.tap_hold_ms))
check("★ DODGE tap 时长落在 100~150ms（社区实测带内）",
      100 <= t_dg.tap_hold_ms <= 150, str(t_dg.tap_hold_ms))
check("★ max_duration_ms 已收紧到 tap 时长量级（<=1000）",
      t_dg.max_duration_ms <= 1000, str(t_dg.max_duration_ms))
check("★ max_duration_ms 容得下自己的 tap 时长",
      t_dg.max_duration_ms > t_dg.tap_hold_ms,
      f"max={t_dg.max_duration_ms} tap={t_dg.tap_hold_ms}")
check("★ min_gap_ms=1500 保留（闪避与冲刺共享 CD，不因改点按而失效）",
      t_dg.min_gap_ms == 1500, str(t_dg.min_gap_ms))

ex9, be9 = mk_executor()
st9 = mk_state()
t9 = time.perf_counter()
r9 = ex9.execute(mk_intent("DODGE"), st9, now_ms=_T0)
el9 = (time.perf_counter() - t9) * 1000

check("一次闪避执行 OK", r9.outcome is Outcome.OK, r9.outcome.value)
check("★ 一次闪避 = 恰好一对 Shift down/up",
      len(key_vk(be9, SHIFT, True)) == 1
      and len(key_vk(be9, SHIFT, False)) == 1,
      f"down={len(key_vk(be9, SHIFT, True))} "
      f"up={len(key_vk(be9, SHIFT, False))}")
check("★ 点按不进 held_keys（没有「还按着」的 Shift）",
      ex9.held_keys == (), str(ex9.held_keys))
check(f"★ 真实保持 ≈ {t_dg.tap_hold_ms}ms（耗 {el9:.0f}ms）",
      el9 >= t_dg.tap_hold_ms * 0.8, f"elapsed={el9:.0f}ms")
check("★ steps 如实写了 tap 时长",
      any("tap" in s for s in r9.steps), str(r9.steps))

# ★ now_ms 必须拉开 1600ms：DODGE 的 min_gap_ms=1500 + 守卫的
#   rate_limit_ms=50。用 300ms 会被 MIN_GAP 拒掉，测到的是拒绝路径。
ex9b, be9b = mk_executor()
for i in range(3):
    ex9b.execute(mk_intent("DODGE"), st9, now_ms=_T0 + i * 1600)
check("★ 3 次闪避 = 3 对 down/up（不是一直按住）",
      len(key_vk(be9b, SHIFT, True)) == 3
      and len(key_vk(be9b, SHIFT, False)) == 3,
      f"down={len(key_vk(be9b, SHIFT, True))} "
      f"up={len(key_vk(be9b, SHIFT, False))}")
check("★ 3 次闪避后仍然没有键按着", ex9b.held_keys == (), str(ex9b.held_keys))
check("★ 3 次都成功（没被 MIN_GAP 拒）",
      all(r.outcome is Outcome.OK for r in ex9b.history),
      str([r.outcome.value for r in ex9b.history]))

# ★ 与普攻对称：两个 tap 动作的时长都应落在同一档，便于算预算上界
check("★ DODGE 与 NORMAL_ATTACK 的 tap 时长同档（预算上界可统一算）",
      t_dg.tap_hold_ms == traits_of("NORMAL_ATTACK").tap_hold_ms,
      f"{t_dg.tap_hold_ms} vs {traits_of('NORMAL_ATTACK').tap_hold_ms}")

# ================================================================
print("\n" + "=" * 92)
print(f"结果: {len(PASS)}/{len(PASS) + len(FAIL)} 通过")
if FAIL:
    print("  失败: " + ", ".join(FAIL))
print("=" * 92)
sys.exit(1 if FAIL else 0)
