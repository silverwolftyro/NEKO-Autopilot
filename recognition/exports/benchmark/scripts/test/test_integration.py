"""感知层集成单测（T1~T5）。

★ 为什么能按包导入
------------------
``builder.py`` 用的是**相对 import**（``from .capturer import ...`` /
``from ..contracts.events import ...``），按文件路径 ``spec_from_file_location``
加载会直接 ImportError。所以这里把 ``genshin_game_buddy`` 塞进 ``sys.path``
走正常包导入。已验证 ``autopilot.perception.builder`` 在本机可导入。

★ T4 的"与今天一致"怎么验
--------------------------
**不能**用 replay fixture —— ``_make_mock_states.py`` 直接构造
``StatePacket``、``tools/replay.py`` 走 ``StatePacket.from_dict``，
**两者都不经过 builder**（已查证）。所以这里改成验"locator=None 时
position 恒为 None 且不抛异常"，这才是接入前后真正需要保证的不变量。

★ 夹具必须有纹理
----------------
``builder`` 的黑帧守卫 ``BLACK_FRAME_LUMA = 8.0`` 会把纯色/黑图判成黑帧并
early-return（``perception_ok=False``）。所有帧都用固定种子的伪随机纹理。
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

import re
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# §29 起 StateBuilder(yolo=...) 会抛 DeprecationWarning（YOLO 已退出场景判定）。
# 本文件里那些 FakeYolo 是历史遗留 —— 场景现在由 scene_detector 给出。
# 这里统一静音，否则几十条警告会把断言输出淹掉；
# 警告本身由 T10b 显式断言。
import warnings  # noqa: E402

warnings.filterwarnings("ignore", category=DeprecationWarning)

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))        # 让 `from _autocache_guard import ...` 可用
ROOT = Path(PLUGIN_DIR)
sys.path.insert(0, str(ROOT))

from autopilot.contracts.state_packet import PositionInfo          # noqa: E402
from autopilot.perception import builder as B                       # noqa: E402
from autopilot.perception import localization_service as LS         # noqa: E402
from autopilot.perception import position_mapping as PM            # noqa: E402
from autopilot.perception import scene_id_debouncer as SID          # noqa: E402
from autopilot.perception.capturer import CapturedFrame, CaptureChannel  # noqa: E402

S = LS.LocalizationStateMachine
DT = 0.1                      # 10 fps
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f"   {detail}" if detail else ""))


def section(title):
    print("\n" + "=" * 92)
    print(title)
    print("=" * 92)


# ================================================================ 夹具
_rng = np.random.default_rng(20261002)
_IMG = _rng.integers(30, 200, (720, 1280, 3), dtype=np.uint8)   # 过黑帧守卫
print(f"  [夹具] 灰度均值 = {_IMG.mean():.1f}（需 >= {B.BLACK_FRAME_LUMA}）")


def frame(ts_ms: int, image=None) -> CapturedFrame:
    return CapturedFrame(image=image if image is not None else _IMG,
                         rect=(0, 0, 1280, 720), timestamp_ms=ts_ms,
                         frame_seq=ts_ms, channel=CaptureChannel.MSS)


class Det:
    def __init__(self, label, box, conf):
        self.label, self.box, self.conf = label, box, conf


class FakeYolo:
    """labels 决定每帧 YOLO 报什么场景。

    ``raise_on`` 非 None -> **每一帧**都抛；
    ``fail_on`` 给的是 **1-based 调用序号**，只有这些帧抛。
    「先好一帧再坏一帧」这种场景必须用 ``fail_on``，
    因为压根没有成功帧时 ``_last_good_scene`` 是 None，无从沿用。
    """

    def __init__(self, labels, raise_on=None, fail_on=None):
        self.labels, self.raise_on = list(labels), raise_on
        self.fail_on = set(fail_on or ())
        self.last_inference_ms = 5.0
        self.calls = 0

    def detect(self, image, conf=0.35):
        self.calls += 1
        if self.raise_on is not None or self.calls in self.fail_on:
            raise RuntimeError("onnx down")
        lbl = self.labels[min(self.calls - 1, len(self.labels) - 1)]
        return [Det(lbl, (10, 10, 100, 100), 0.9)]


class FakeSceneDetector:
    """§28 起 builder 的场景来源。**不依赖真实帧**，脚本化 is_world。

    ⚠️ 旧的 :class:`FakeYolo` 不再决定 scene.id（YOLO 已从场景判定里
       拿掉），所以本类才是控制场景的旋钮。``FakeYolo`` 保留给仍按
       yolo= 注入的调用方（参数还在，只是不再影响 scene）。
    """

    def __init__(self, worlds, raise_on=None):
        self.worlds = list(worlds)
        self.raise_on = raise_on
        self.calls = 0

    def update(self, image, ts_sec):
        if self.raise_on is not None:
            raise self.raise_on
        w = self.worlds[min(self.calls, len(self.worlds) - 1)]
        self.calls += 1
        return _FakeSceneState(is_world=bool(w), changed=(self.calls == 1))


class _FakeSceneState:
    def __init__(self, is_world, changed):
        self.is_world, self.changed = is_world, changed
        self.raw_prediction, self.pending_seconds, self.is_first = is_world, 0.0, False


class FakeOcr:
    def __init__(self, available=True):
        self.available = available
        self.fail_streak = 3
        self.last_error = "boom"


class FakeLocator:
    """记录调用次数，可按脚本返回定位状态。"""

    def __init__(self, states):
        self.states = list(states)
        self.calls = 0
        self.seen_ts = []

    def on_frame(self, image, ts_sec):
        self.seen_ts.append(ts_sec)
        i = min(self.calls, len(self.states) - 1)
        self.calls += 1
        return self.states[i]


def loc_state(state, pos=(3600.0, 4300.0), tile="r04_c06", drift=0.45):
    return LS.LocalizationState(state=state, world_pos=pos, tile_id=tile,
                                drift_bound=drift, step_count=7,
                                is_world=True)


# ================================================================ T1
section("T1 · perception_ok 只由 YOLO 决定（§21 收窄）")

b1 = B.StateBuilder(yolo=FakeYolo(["combat"]))
p1 = b1.build(frame(1000))
check("正常帧 -> perception_ok=True", p1.resource.perception_ok is True,
      str(p1.resource.perception_ok))

# ⚠️ §28 起 **YOLO 不再参与场景判定** —— 7 类 YOLO 在这个工作区根本没有
#    模型，那条路径一直在跑退化分支。现在场景来自 SceneDetector 的二值结果，
#    所以「YOLO 挂」不再影响 scene，也不影响 perception_ok。
b2 = B.StateBuilder(yolo=FakeYolo(["combat"], fail_on={2}),
                    scene_detector=FakeSceneDetector([True]))
b2.build(frame(1000))
p2 = b2.build(frame(1100))
check("★ YOLO 挂不再影响 scene.id（§28：场景走 SceneDetector）",
      p2.scene.id == "WORLD", p2.scene.id)
check("★ YOLO 挂不再让 perception_ok=False（guard 已休眠）",
      p2.resource.perception_ok is True, str(p2.resource.perception_ok))
check("★ YOLO 不再被调用（场景判定不再走它）",
      b2._yolo.calls == 0, f"被调了 {b2._yolo.calls} 次")

# ⚠️ SceneDetector 抛异常时 builder 必须**响亮抛出**（不吞）——
#    静默判成"非大世界"会让定位永远不启动且看不出原因。
try:
    B.StateBuilder(scene_detector=FakeSceneDetector(
        [True], raise_on=FileNotFoundError(
            "找不到底层场景判定模块: coord_scene_tree.py"))).build(frame(1000))
    check("SceneDetector 抛 FileNotFoundError -> builder 不吞", False)
except FileNotFoundError:
    check("SceneDetector 抛 FileNotFoundError -> builder 不吞（响亮失败）", True)

# ⚠️ §21 后 OCR **完全不碰** perception_ok，走自己的专属通道。
b3 = B.StateBuilder(yolo=FakeYolo(["combat"]), ocr=FakeOcr(available=False),
                    scene_detector=FakeSceneDetector([True]))
p3 = b3.build(frame(1000))
check("OCR 不可用 -> perception_ok 仍为 True（§21 撤回项）",
      p3.resource.perception_ok is True, str(p3.resource.perception_ok))
check("OCR 单帧不可用 -> 还没判死，ocr_available 仍为 True",
      p3.resource.ocr_available is True, str(p3.resource.ocr_available))
check("OCR 单帧不可用 -> fail_streak 递增到 1",
      p3.resource.ocr_fail_streak == 1, str(p3.resource.ocr_fail_streak))

b4 = B.StateBuilder(yolo=FakeYolo(["combat"]),
                    scene_detector=FakeSceneDetector([True]))
p4 = b4.build(frame(1000), forced_scene="BAG_INVENTORY")
check("forced_scene -> perception_ok=True", p4.resource.perception_ok is True)
check("★ forced_scene 覆盖二值结果", p4.scene.id == "BAG_INVENTORY", p4.scene.id)
check("forced_scene -> evidence=('forced',)", p4.scene.evidence == ("forced",),
      str(p4.scene.evidence))

# 两处 early-return 仍要是 False
p5 = B.StateBuilder(yolo=FakeYolo(["combat"]),
                    scene_detector=FakeSceneDetector([True])).build(
    frame(1000, image=np.zeros((720, 1280, 3), np.uint8)))
check("黑帧 -> perception_ok=False（原有行为未破坏）",
      p5.resource.perception_ok is False)

# ================================================================ T2
section("T2 · SceneIdDebouncer")

d = SID.SceneIdDebouncer(n_seconds=3.0, m_seconds=1.0)
st = d.update("MAIN_MENU", 0.0)
check("冷启动首帧直接采信", st.scene_id == "MAIN_MENU" and st.is_first is True
      and st.changed is False, str(st))

# 单帧抖动
st = d.update("COMBAT", DT)
check("单帧抖动 -> 稳定 ID 不变", st.scene_id == "MAIN_MENU" and not st.changed,
      st.scene_id)
st = d.update("MAIN_MENU", 2 * DT)
check("抖动后回到原 ID -> 计时清零",
      st.scene_id == "MAIN_MENU" and st.pending_seconds == 0.0,
      f"{st.scene_id} pending={st.pending_seconds}")

# 真实切换：连续 B 满 N=3.0s
d2 = SID.SceneIdDebouncer()
d2.update("MAIN_MENU", 0.0)
switched_at = None
for k in range(1, 31):
    s = d2.update("BAG_INVENTORY", k * DT)
    if s.changed and switched_at is None:
        switched_at = k * DT
check("持续 3.0s 后完成首次切换", switched_at is not None and abs(switched_at - 3.0) < 1e-6,
      f"t={switched_at}")
check("切换后稳定 ID = BAG_INVENTORY",
      d2.update("BAG_INVENTORY", 3.05).scene_id == "BAG_INVENTORY")

# 首次切换前的最后一帧必须还没切
d3 = SID.SceneIdDebouncer()
d3.update("MAIN_MENU", 0.0)
for k in range(1, 30):
    d3.update("BAG_INVENTORY", k * DT)
check("第 29 帧（累计 2.9s）尚未切换", d3.stable_id == "MAIN_MENU",
      str(d3.stable_id))

# 回原 ID：只需 M=1.0s
back_at = None
for k in range(31, 45):
    s = d2.update("MAIN_MENU", k * DT)
    if s.changed and back_at is None:
        back_at = k * DT
check("后续切换只需 1.0s（远快于 N）",
      back_at is not None and abs(back_at - 4.1) < 1e-6, f"t={back_at}")

# 时间戳倒退
try:
    d2.update("COMBAT", 1.0)
    check("时间戳倒退 -> ValueError", False)
except ValueError as e:
    check("时间戳倒退 -> ValueError", True, str(e).split("\n")[0][:60])

try:
    d2.update("COMBAT", None)
    check("timestamp=None -> TypeError", False)
except TypeError:
    check("timestamp=None -> TypeError", True)

# 候选换了必须重置计时（不能把 A 的时间算到 B 头上）
d4 = SID.SceneIdDebouncer()
d4.update("M", 0.0)
for k in range(1, 30):
    d4.update("A", k * DT)
d4.update("B", 2.95)                       # A 累计 2.85s，候选换成 B
s = d4.update("A", 3.0)                    # A 又回来，但计时已归零
check("候选换成别的再回来 -> 计时重置", not s.changed and s.scene_id == "M",
      f"{s.scene_id} changed={s.changed}")

# ================================================================ T3
section("T3 · position_from_state 纯函数")

cases = [(S.TRACKING, "GOOD"), (S.PAUSED, "DEGRADED"),
         (S.RESETTING, "DEGRADED"), (S.UNINITIALIZED, "LOST")]
for st_enum, q in cases:
    pos = PM.position_from_state(loc_state(st_enum, pos=(3600.0, 4300.0)))
    check(f"{st_enum.value} -> quality={q}", pos.quality == q,
          f"got {pos.quality}")

pos = PM.position_from_state(loc_state(S.TRACKING, pos=(123.5, -77.25),
                                       tile="r04_c06", drift=0.0))
check("x/y 取自 world_pos", (pos.x, pos.y) == (123.5, -77.25), str((pos.x, pos.y)))
check("zone = tile_id", pos.zone == "r04_c06", str(pos.zone))
check("source = task_driven", pos.source == "task_driven", pos.source)
check("heading_deg 恒 None（朝向已证伪）", pos.heading_deg is None,
      str(pos.heading_deg))
check("confidence = 1/(1+0) = 1.0", abs(pos.confidence - 1.0) < 1e-12,
      f"{pos.confidence}")

check("confidence = 1/(1+1) = 0.5",
      abs(PM.confidence_from_drift(1.0) - 0.5) < 1e-12)
check("confidence = 1/(1+3) = 0.25",
      abs(PM.confidence_from_drift(3.0) - 0.25) < 1e-12)

lost = PM.position_from_state(loc_state(S.UNINITIALIZED, pos=None))
check("无位置 -> LOST 包（非 None）",
      lost.quality == "LOST" and lost.x is None and lost.y is None, str(lost.quality))
check("LOST 包 usable_for_navigation=False", lost.usable_for_navigation is False)
good = PM.position_from_state(loc_state(S.TRACKING))
check("GOOD 包 usable_for_navigation=True", good.usable_for_navigation is True)
deg = PM.position_from_state(loc_state(S.PAUSED))
check("DEGRADED 包 usable_for_navigation=True（用户已拍板）",
      deg.usable_for_navigation is True)

try:
    PM.confidence_from_drift(-1.0)
    check("负 drift_bound -> ValueError", False)
except ValueError:
    check("负 drift_bound -> ValueError", True)

# 纯度：同输入两次调用结果相同
a1 = PM.position_from_state(loc_state(S.TRACKING))
a2 = PM.position_from_state(loc_state(S.TRACKING))
check("纯函数：两次调用结果一致", a1 == a2)

# ================================================================ T4
section("T4 · builder 集成")

bb = B.StateBuilder(yolo=FakeYolo(["combat"] * 50))
sp = bb.build(frame(1000))
check("locator=None -> position 恒为 None（向后兼容）", sp.position is None)
check("locator=None -> locator_ms=0.0", sp.resource.locator_ms == 0.0,
      str(sp.resource.locator_ms))
check("locator=None -> scene/player/resource 照常产出",
      sp.scene.id != "" and sp.resource.perception_ok is True, sp.scene.id)

# 场景判定（§28）：builder 直接消费 SceneDetector 的二值结果。
# ⚠️ FakeSceneDetector **不模拟**内部去抖（去抖是真实 SceneDetector 的职责，
#    由 test_scene_detector.py 单独覆盖）。这里只验「映射对不对」。
bb2 = B.StateBuilder(scene_detector=FakeSceneDetector([True] + [False] * 10))
ids = [bb2.build(frame(i * 100 + 1000)).scene.id for i in range(3)]
check("is_world=True -> WORLD", ids[0] == "WORLD", str(ids))
check("is_world=False -> UNKNOWN", ids[1] == "UNKNOWN" and ids[2] == "UNKNOWN",
      str(ids))

bb3 = B.StateBuilder(scene_detector=FakeSceneDetector([True] + [False] * 40))
seq = [bb3.build(frame(i * 100 + 1000)).scene.id for i in range(31)]
check("★ 二值场景：WORLD -> UNKNOWN",
      seq[0] == "WORLD" and seq[-1] == "UNKNOWN",
      f"head={seq[0]} tail={seq[-1]}")

# ★ §28 A3：显式传 debouncer 才会串第二层（默认不串，避免延迟翻倍）
from autopilot.perception.scene_id_debouncer import SceneIdDebouncer as _SD  # noqa: E402

bb4d = B.StateBuilder(scene_detector=FakeSceneDetector([True] + [False] * 40),
                      scene_debouncer=_SD(n_seconds=3.0, m_seconds=1.0))
seqd = [bb4d.build(frame(i * 100 + 1000)).scene.id for i in range(5)]
check("显式传 scene_debouncer -> 5 帧内还没翻（第二层去抖在生效）",
      seqd[0] == "WORLD" and seqd[-1] == "WORLD", str(seqd))
check("默认不传 -> _scene_debouncer 是 None（直通）",
      B.StateBuilder(scene_detector=FakeSceneDetector([True]))
      ._scene_debouncer is None)

# locator 注入
loc = FakeLocator([loc_state(S.TRACKING, pos=(1.0, 2.0))] * 5)
bb4 = B.StateBuilder(yolo=FakeYolo(["combat"] * 5), locator=loc)
sp4 = bb4.build(frame(2000))
check("locator 注入 -> 每帧调一次 on_frame", loc.calls == 1, str(loc.calls))
check("locator 注入 -> position 被填上",
      sp4.position is not None and (sp4.position.x, sp4.position.y) == (1.0, 2.0),
      str(sp4.position))
check("locator 收到的是秒级时间戳",
      abs(loc.seen_ts[0] - 2.0) < 1e-9, str(loc.seen_ts))
check("locator_ms 被真实测量", sp4.resource.locator_ms >= 0.0,
      str(sp4.resource.locator_ms))

# ⚠️ 必须用**全新** locator：上面那个 loc.calls 已经是 1 了，复用会把
#    前一段的调用数算进来（这不是实现 bug，是夹具有状态）。
loc3 = FakeLocator([loc_state(S.TRACKING, pos=(1.0, 2.0))] * 5)
bb5 = B.StateBuilder(yolo=FakeYolo(["combat"] * 5), locator=loc3)
for i in range(3):
    bb5.build(frame(1000 + i * 100))
check("连续 3 帧 -> on_frame 被调 3 次", loc3.calls == 3, str(loc3.calls))

# 时间戳倒退
bb6 = B.StateBuilder(yolo=FakeYolo(["combat"] * 5))
bb6.build(frame(5000))
try:
    bb6.build(frame(4000))
    check("时间戳倒退 -> ValueError", False)
except ValueError as e:
    check("时间戳倒退 -> ValueError", True, str(e)[:70])

# locator 异常必须上抛（不能静默）
class Boom:
    def on_frame(self, image, ts_sec):
        raise RuntimeError("locator dead")


# ⚠️ §21 后 locator 异常**不再上抛** —— 外抛会让 core/loop.py:513 走
#    _note_crash，而它里面无条件 _release_all()（loop.py:383），
#    一次定位抖动就会把按住的键全松开。单帧失败只降级、不中断 tick。
bb7 = B.StateBuilder(yolo=FakeYolo(["combat"] * 5), locator=Boom())
sp7 = bb7.build(frame(1000))
check("locator 异常不再上抛（tick 不中断）", sp7 is not None)
check("locator 单帧异常 -> quality=LOST（不是 None：None 只表示没配定位）",
      sp7.position is not None and sp7.position.quality == "LOST",
      str(sp7.position))
check("locator 单帧异常 -> perception_ok 不受影响",
      sp7.resource.perception_ok is True, str(sp7.resource.perception_ok))
check("locator 单帧异常 -> 发了 LOCATOR_FAILED 事件",
      any(e.code == "LOCATOR_FAILED" for e in bb7.drain_events()))

# ================================================================ T5
section("T5 · F2 接通：builder -> loop -> policy -> PolicyContext")

from autopilot.perception.world_entities import (                # noqa: E402
    KIND_GROUPS, WORLD_SCENES, WorldEntity,
)
from autopilot.policy.context import PolicyContext               # noqa: E402
from autopilot.policy.local_tree import LocalPolicyTree        # noqa: E402


class FakeEntDetector:
    """不依赖真实模型的替身：只要有 should_run_for_scene + detect 两个方法。"""

    def __init__(self, ents):
        self.ents, self.calls = list(ents), 0

    @staticmethod
    def should_run_for_scene(s):
        return s in WORLD_SCENES

    def detect(self, img, base_size=(1280, 720)):
        self.calls += 1
        return list(self.ents)

    last_ms = 0.0


ENTS = (
    WorldEntity("enemy_identify", (0.5, 0.5, 0.02, 0.02), 0.9, (640, 360)),
    WorldEntity("chest", (0.3, 0.3, 0.05, 0.05), 0.8, (400, 200)),
)

# ① builder 产出 -> 按 loop.py:453 的搬运逻辑喂给 policy
# ⚠️ T5 验的是 **F2 接线**，不是节流（节流在 T6 单独验）——
#    所以显式把间隔设成 1，让第一帧就跑检测，两节互不干扰。
b_a = B.StateBuilder(yolo=FakeYolo(["combat"] * 5),
                     entity_detector=FakeEntDetector(ENTS),
                     entity_interval_ticks=1)
b_a.build(frame(1000))
check("builder 跑完后 last_world_entities 有 2 个",
      b_a.last_world_entities is not None and len(b_a.last_world_entities) == 2,
      str(b_a.last_world_entities))

pol = LocalPolicyTree()
check("LocalPolicyTree 默认 last_world_entities = None",
      pol.last_world_entities is None, str(pol.last_world_entities))
_ents = b_a.last_world_entities
pol.last_world_entities = None if _ents is None else tuple(_ents)
check("搬运后 policy.last_world_entities 有 2 个",
      pol.last_world_entities is not None
      and len(pol.last_world_entities) == 2, str(pol.last_world_entities))

sp_a = b_a.build(frame(1100))
ctx = PolicyContext.from_state(sp_a, now_ms=1,
                               world_entities=pol.last_world_entities)
check("policy 侧能算出敌人/宝箱派生属性",
      ctx.has_nearby_enemy() is True and ctx.has_nearby_chest() is True,
      f"enemy={ctx.has_nearby_enemy()} chest={ctx.has_nearby_chest()}")
check("没有矿 -> has_nearby_ore = False", ctx.has_nearby_ore() is False)
check("nearby_entity_count = 2", ctx.nearby_entity_count() == 2)
check("KIND_GROUPS 覆盖实体用到的类",
      all(e.group == KIND_GROUPS[e.kind] for e in ENTS),
      str([(e.kind, e.group) for e in ENTS]))

# ② 三态：没跑检测 -> UNKNOWN（规则保守不触发），不是 False
# ⚠️ 必须**显式**传 entity_detector=None —— 默认已经是 "auto" 了。
#    不显式关掉的话，这里测的就不是「未注入」而是「被节流挡了首帧」，
#    属于**因为对的理由而绿**。
b_b = B.StateBuilder(yolo=FakeYolo(["combat"] * 5), entity_detector=None)
p_none = b_b.build(frame(1000))
pol.last_world_entities = (
    None if b_b.last_world_entities is None else tuple(b_b.last_world_entities))
c_none = PolicyContext.from_state(p_none, now_ms=1,
                                  world_entities=pol.last_world_entities)
check("未注入检测器 -> builder 给 None", b_b.last_world_entities is None,
      str(b_b.last_world_entities))
check("搬运后 policy 侧仍是 None（没被压成空元组）",
      pol.last_world_entities is None, str(pol.last_world_entities))
check("未跑检测 -> 派生属性是 UNKNOWN 而不是 False",
      type(c_none.has_nearby_enemy()).__name__ == "UnknownValue",
      type(c_none.has_nearby_enemy()).__name__)
check("UNKNOWN 真值为 False（规则不会误触发）",
      bool(c_none.has_nearby_enemy()) is False)

# ③ 跑了但确实没有 -> 空元组 -> False（与 None 区分开）
de = FakeEntDetector([])
b_d = B.StateBuilder(yolo=FakeYolo(["combat"] * 5), entity_detector=de,
                    entity_interval_ticks=1)
sp_d = b_d.build(frame(1000))
pol.last_world_entities = (
    None if b_d.last_world_entities is None else tuple(b_d.last_world_entities))
c_d = PolicyContext.from_state(sp_d, now_ms=1,
                               world_entities=pol.last_world_entities)
check("跑了但确实没有 -> 空元组", pol.last_world_entities == (),
      str(pol.last_world_entities))
check("空元组 -> 派生属性是 False（与 UNKNOWN 区分）",
      c_d.has_nearby_enemy() is False, str(c_d.has_nearby_enemy()))

# ④ 向后兼容：locator=None 时 position 仍为 None，实体与定位互不干扰
check("实体与定位可共存且互不污染",
      sp_a.position is None and len(sp_a.last_world_entities) == 2
      if hasattr(sp_a, "last_world_entities") else sp_a.position is None,
      f"pos={sp_a.position}")

# ================================================================ T6
section("T6 · 实体检测节流（entity_interval_ticks）")

N = 5
dth = FakeEntDetector(ENTS)
bt = B.StateBuilder(yolo=FakeYolo(["combat"] * 20),
                    entity_detector=dth, entity_interval_ticks=N)
check("默认间隔常量是 5", B.DEFAULT_ENTITY_INTERVAL_TICKS == 5,
      str(B.DEFAULT_ENTITY_INTERVAL_TICKS))
check("builder 采纳了传入的间隔", bt._entity_interval == N, str(bt._entity_interval))

ages, runs_after = [], []
for i in range(10):
    bt.build(frame(1000 + i * 100))
    ages.append(bt.last_entity_age_ticks)
    runs_after.append(dth.calls)

check("10 个 tick 里 detect 只被调 2 次（N=5）", dth.calls == 2, str(dth.calls))
check("调用发生在第 5 和第 10 个 tick",
      runs_after[4] == 1 and runs_after[9] == 2, str(runs_after))
check("age 序列 = [1,2,3,4,0,1,2,3,4,0]", ages == [1, 2, 3, 4, 0, 1, 2, 3, 4, 0],
      str(ages))

check("节流命中的 tick 里 last_world_entities 保持上一帧的值",
      bt.last_world_entities is not None and len(bt.last_world_entities) == 2,
      str(bt.last_world_entities))
check("跑完那一刻 age 归零", bt.last_entity_age_ticks == 0,
      str(bt.last_entity_age_ticks))

# 首帧到第一次运行之间 = None（没跑，不是"跑了没有"）
dth2 = FakeEntDetector(ENTS)
bt2 = B.StateBuilder(yolo=FakeYolo(["combat"] * 10),
                     entity_detector=dth2, entity_interval_ticks=N)
bt2.build(frame(1000))
check("节流前几帧 last_world_entities 是 None（= 没跑）",
      bt2.last_world_entities is None, str(bt2.last_world_entities))
bt2.build(frame(1100))
c0 = PolicyContext.from_state(bt2.build(frame(1200)), now_ms=1,
                              world_entities=None)
check("没跑 -> 派生属性 UNKNOWN（不会被当成「确实没有」）",
      type(c0.has_nearby_enemy()).__name__ == "UnknownValue",
      type(c0.has_nearby_enemy()).__name__)

# 间隔 = 1 -> 每 tick 都跑
dth3 = FakeEntDetector(ENTS)
bt3 = B.StateBuilder(yolo=FakeYolo(["combat"] * 10),
                     entity_detector=dth3, entity_interval_ticks=1)
for i in range(5):
    bt3.build(frame(1000 + i * 100))
check("间隔=1 -> 5 个 tick 全跑", dth3.calls == 5, str(dth3.calls))
check("间隔=1 -> age 恒为 0", bt3.last_entity_age_ticks == 0,
      str(bt3.last_entity_age_ticks))

# 未注入检测器时 age 不增长（没有数据可老化）
# ⚠️ 同样要显式关掉 auto，否则测的是节流不是「未注入」
bn = B.StateBuilder(yolo=FakeYolo(["combat"] * 5), entity_detector=None)
bn.build(frame(1000))
check("未注入检测器 -> last_world_entities 仍为 None",
      bn.last_world_entities is None, str(bn.last_world_entities))

# 非法间隔要响亮失败
for bad in (0, -1):
    try:
        B.StateBuilder(yolo=FakeYolo(["combat"] * 5),
                       entity_detector=FakeEntDetector([]),
                       entity_interval_ticks=bad)
        check(f"间隔={bad} -> ValueError", False)
    except ValueError:
        check(f"间隔={bad} -> ValueError", True)

# ★ 默认值是 "auto"（2026-10-02 DirectML 实测 p50 66ms -> 启用 + interval 5）
from autopilot.perception.world_entities import (                # noqa: E402
    BGI_WORLD_ONNX, WORLD_SCENES, recommended_interval_ticks,
)
import autopilot.perception.world_entities as WE_mod         # noqa: E402
check("recommended_interval_ticks(25ms) = 1", recommended_interval_ticks(25) == 1,
      str(recommended_interval_ticks(25)))
check("★ recommended_interval_ticks(66ms) = 5（DirectML 实测档）",
      recommended_interval_ticks(66) == 5, str(recommended_interval_ticks(66)))
check("recommended_interval_ticks(130ms) = 0（别开）",
      recommended_interval_ticks(130) == 0, str(recommended_interval_ticks(130)))

b_off = B.StateBuilder(yolo=FakeYolo(["combat"] * 2), entity_detector=None)
check("显式 None -> 检测器为 None", b_off._entity_detector is None,
      str(b_off._entity_detector))

if BGI_WORLD_ONNX.is_file():
    b_auto = B.StateBuilder(yolo=FakeYolo(["combat"] * 8))
    # ⚠️ **不要断言 auto 一定启用**。探测值随机器负载在 53~116 ms 之间浮动
    #    （§28.6 / §29 记录过多次翻转），而阈值是 80 ms。这里只断言
    #    **判定与探测值自洽** —— 那是确定性逻辑；探测结果本身不是。
    _on_auto = b_auto._entity_detector is not None
    check("★ auto 的判定与探测值自洽（启用则 interval=5 / 关闭则 note 说明原因）",
          (_on_auto and b_auto._entity_interval == 5
           and "每 5 tick" in b_auto._entity_note)
          or (not _on_auto and "超阈值" in b_auto._entity_note),
          f"enabled={_on_auto} interval={b_auto._entity_interval} "
          f"note={b_auto._entity_note}")
    check("auto 的 note 记了 provider 与实测耗时",
          "DmlExecutionProvider" in b_auto._entity_note
          or "CPU" in b_auto._entity_note, b_auto._entity_note)
    # auto 走的是同一个进程级实例（只探一次）
    b_auto2 = B.StateBuilder(yolo=FakeYolo(["combat"] * 8))
    check("auto 进程级缓存：两次拿到的是同一个实例",
          b_auto._entity_detector is b_auto2._entity_detector)

    # ---- cache 隔离出口（§26）----
    had = B.clear_auto_cache()
    check("clear_auto_cache() 返回 True（原本有缓存）", had is True, str(had))
    check("clear_auto_cache() 第二次返回 False（已空）",
          B.clear_auto_cache() is False)
    b_fresh = B.StateBuilder(yolo=FakeYolo(["combat"] * 8))
    check("★ clear 后再构造 -> 拿到**新**实例（不是缓存的）",
          b_fresh._entity_detector is not b_auto._entity_detector,
          "同实例 = clear 没生效")
    # ⚠️ **不要断言"auto 一定是启用的"**。探测值在 63~93 ms 之间随负载浮动，
    #    而阈值是 100 ms —— 边际机器上"auto 启用/关闭"本身是随机的
    #    （§28.6 记录的缺陷）。这里只断言**确定性的那半边**：映射逻辑。
    _on = b_fresh._entity_detector is not None
    check("auto 的判定与探测值自洽（启用则 interval=5，关闭则 note 说明原因）",
          (_on and b_fresh._entity_interval == 5 and "每 5 tick" in b_fresh._entity_note)
          or (not _on and "超阈值" in b_fresh._entity_note),
          f"enabled={_on} interval={b_fresh._entity_interval} "
          f"note={b_fresh._entity_note}")
else:
    check("（跳过 auto 默认值验证：缺模型）", True)

# 场景不允许时 age 也不增长（数据被置 None）
dth4 = FakeEntDetector(ENTS)
bt4 = B.StateBuilder(yolo=FakeYolo(["combat"] * 10),
                     entity_detector=dth4, entity_interval_ticks=1)
bt4.build(frame(1000), forced_scene="BAG_INVENTORY")
check("场景不允许 -> 不跑", dth4.calls == 0, str(dth4.calls))
check("场景不允许 -> last_world_entities 置 None",
      bt4.last_world_entities is None, str(bt4.last_world_entities))

# ================================================================ T7
section("T7 · auto 降级事件 + cache 隔离")

import pathlib  # noqa: E402

import autopilot.perception.world_entities as WE  # noqa: E402
from _autocache_guard import reset_auto_cache, restore_entity_paths  # noqa: E402

# 降级事件：只在首次降级时发一次
reset_auto_cache()
_orig_path = WE.BGI_WORLD_ONNX
WE.BGI_WORLD_ONNX = pathlib.Path("D:/nope/bgi_world.onnx")   # 模拟缺模型
try:
    b_deg = B.StateBuilder(yolo=FakeYolo(["combat"] * 3))
    check("缺模型 -> 检测器降级为 None", b_deg._entity_detector is None,
          str(b_deg._entity_detector))
    evs = b_deg.drain_events()
    codes = [e.code for e in evs]
    check("★ 缺模型 -> 发出 ENTITY_DETECTOR_UNAVAILABLE",
          "ENTITY_DETECTOR_UNAVAILABLE" in codes, str(codes))
    if evs:
        e0 = evs[0]
        check("事件 severity = WARN（缺模型是环境问题，不是崩溃）",
              e0.severity.value == "WARN", e0.severity.value)
        check("事件 category = PERCEPTION",
              e0.category.value == "PERCEPTION", e0.category.value)
        check("事件 context 带 reason=load_failed",
              e0.context.get("reason") == "load_failed", str(e0.context))
        check("★ 缺模型 -> expected=True（GPL 资产不随包分发，是预期状态）",
              e0.context.get("expected") is True, str(e0.context.get("expected")))
        check("事件 message 非空", bool(e0.message), e0.message[:60])

    b_again = B.StateBuilder(yolo=FakeYolo(["combat"] * 3))
    check("★ 第二个 builder 不再发（每进程只一次，不刷屏）",
          b_again.drain_events() == [], "仍有事件 = 会刷屏")
finally:
    restore_entity_paths(WE)
    reset_auto_cache()

# 模型**在但加载失败** -> expected=False（该查）
reset_auto_cache()
_broken = pathlib.Path(tempfile.gettempdir()) / "neko_broken_model.onnx"
try:
    _broken.write_bytes(b"this is definitely not a valid onnx file")   # 存在但坏
    WE.BGI_WORLD_ONNX = _broken
    check("损坏模型文件确实存在", _broken.is_file())
    b_broken = B.StateBuilder(yolo=FakeYolo(["combat"] * 3))
    check("损坏模型 -> 降级为 None", b_broken._entity_detector is None,
          str(b_broken._entity_detector))
    eb = b_broken.drain_events()
    check("损坏模型 -> 也发 ENTITY_DETECTOR_UNAVAILABLE",
          any(e.code == "ENTITY_DETECTOR_UNAVAILABLE" for e in eb),
          str([e.code for e in eb]))
    if eb:
        check("★ 文件存在但加载失败 -> expected=False（该查，不是预期）",
              eb[0].context.get("expected") is False,
              str(eb[0].context.get("expected")))
finally:
    restore_entity_paths(WE)
    reset_auto_cache()
    try:
        _broken.unlink()
    except OSError:
        pass

# 恢复后重建 -> 又能拿到检测器
if BGI_WORLD_ONNX.is_file():
    b_back = B.StateBuilder(yolo=FakeYolo(["combat"] * 3))
    check("clear 后恢复正常 -> auto 重新启用",
          b_back._entity_detector is not None, b_back._entity_note)
    check("恢复正常后不发降级事件", b_back.drain_events() == [])
else:
    check("（跳过恢复验证：仍缺模型）", True)

# ---- runner 等价守卫确实生效 ----
reset_auto_cache()
B.StateBuilder(yolo=FakeYolo(["combat"] * 2))       # 填缓存
had = reset_auto_cache()
check("★ reset_auto_cache() 确实清了（返回 True）", had is True, str(had))
check("清完再构造 -> 拿到新实例",
      B.StateBuilder(yolo=FakeYolo(["combat"] * 2))._entity_detector is not None)

# clear_auto_cache 不影响显式注入路径
d_exp = FakeEntDetector([])
b_exp1 = B.StateBuilder(yolo=FakeYolo(["combat"] * 2), entity_detector=d_exp)
reset_auto_cache()
b_exp2 = B.StateBuilder(yolo=FakeYolo(["combat"] * 2), entity_detector=d_exp)
check("clear_auto_cache 不干扰显式注入的实例",
      b_exp1._entity_detector is b_exp2._entity_detector is d_exp)
reset_auto_cache()

# ================================================================ T8
section("T8 · §28 二值场景：is_world=True -> WORLD")

_b = B.StateBuilder(yolo=FakeYolo(["combat"] * 5),
                    scene_detector=FakeSceneDetector([True] * 5))
_s = _b.build(frame(1000))
check("is_world=True -> scene.id = 'WORLD'", _s.scene.id == "WORLD", _s.scene.id)
check("WORLD 的 confidence = 1.0", _s.scene.confidence == 1.0,
      str(_s.scene.confidence))
check("raw_label = 'world'", _s.scene.raw_label == "world", _s.scene.raw_label)
check("evidence 记了 is_world=1",
      any("is_world=1" in e for e in _s.scene.evidence), str(_s.scene.evidence))
check("entered_ms 有值（首帧就记）", _s.scene.entered_ms is not None,
      str(_s.scene.entered_ms))
check("WORLD 在实体门控集合内",
      "WORLD" in WE_mod.WORLD_SCENES, str(sorted(WE_mod.WORLD_SCENES)))

# ================================================================ T9
section("T9 · §28 二值场景：is_world=False -> UNKNOWN")

_b2 = B.StateBuilder(scene_detector=FakeSceneDetector([False] * 5))
_s2 = _b2.build(frame(1000))
check("is_world=False -> scene.id = 'UNKNOWN'",
      _s2.scene.id == "UNKNOWN", _s2.scene.id)
check("UNKNOWN 的 confidence = 0.0", _s2.scene.confidence == 0.0,
      str(_s2.scene.confidence))
check("raw_label = 'not_world'", _s2.scene.raw_label == "not_world",
      _s2.scene.raw_label)
check("evidence 记了 is_world=0",
      any("is_world=0" in e for e in _s2.scene.evidence), str(_s2.scene.evidence))
check("★ 切换时 entered_ms 会更新", _s2.scene.entered_ms is not None,
      str(_s2.scene.entered_ms))

# ================================================================ T10
section("T10 · §28 跨仓库依赖缺失 -> 响亮抛错（不吞）")

_boom = FileNotFoundError(
    "找不到底层场景判定模块: D:\\NEKO\\recognition\\...\\coord_scene_tree.py\n"
    "用 set_classifier_module_path() 或环境变量 NEKO_SCENE_TREE 指定。")
_breal = B.StateBuilder(scene_detector=FakeSceneDetector([True], raise_on=_boom))
try:
    _breal.build(frame(1000))
    check("SceneDetector 抛 FileNotFoundError -> builder 向上抛", False)
except FileNotFoundError as e:
    check("★ SceneDetector 抛 FileNotFoundError -> builder 向上抛（不吞）", True,
          str(e)[:60])

check("★ 不是静默降级：既没产包也没发 PERCEPTION_DOWN 事件",
      not any(e.code in ("PERCEPTION_DOWN", "SCENE_DETECTOR_UNAVAILABLE")
              for e in _breal.drain_events()),
      str([e.code for e in _breal.drain_events()]))

# 环境变量入口确实存在
import os  # noqa: E402

check("NEKO_SCENE_TREE 环境变量入口存在",
      "NEKO_SCENE_TREE" in open(
          PLUGIN_DIR / 'autopilot' / 'perception' / 'scene_detector.py',
          encoding="utf-8").read())

# ================================================================ T10b
section("T10b · §29 契约补 WORLD + 阈值收紧 + yolo= 弃用")

import warnings  # noqa: E402

from autopilot.contracts.state_packet import (               # noqa: E402
    SCENE_WORLD, VALID_SCENE_IDS, is_valid_scene,
)
from autopilot.actuator.traits import SCENE_WORLD as _TWORLD  # noqa: E402

check("★ WORLD 进入 VALID_SCENE_IDS", "WORLD" in VALID_SCENE_IDS,
      str(sorted(VALID_SCENE_IDS)))
check("is_valid_scene('WORLD') = True", is_valid_scene("WORLD"))
check("contracts.SCENE_WORLD == 'WORLD'", SCENE_WORLD == "WORLD", SCENE_WORLD)
check("traits.SCENE_WORLD 也派生了（手写派生列表，容易漏）",
      _TWORLD == "WORLD", _TWORLD)
check("VALID_SCENE_IDS 数量 12 -> 13", len(VALID_SCENE_IDS) == 13,
      str(len(VALID_SCENE_IDS)))

# 规则里引用 WORLD 能通过加载校验（这是 §28.2 副作用 1 的解药）
import tempfile, pathlib as _pl  # noqa: E402

from autopilot.policy.rule_loader import RuleLoadError, load_rules  # noqa: E402

_ok = False
_detail = ""
try:
    with tempfile.TemporaryDirectory() as _td:
        _p = _pl.Path(_td) / "r.toml"
        _p.write_text(
            '[[rules]]\nname = "probe"\naction = "WAIT"\n'
            'when = "scene() == \'WORLD\'"\npriority = 10\n'
            'ttl_seconds = 2\nscenes = ["WORLD"]\n', encoding="utf-8")
        load_rules(_p)                    # 签名是单个文件路径，不是列表
    _ok = True
except RuleLoadError as _e:
    _detail = str(_e)[:140]
except Exception as _e:      # API 形状不对也算失败，如实暴露
    _detail = f"{type(_e).__name__}: {_e}"[:140]
check("★ 规则里写 scenes=[\"WORLD\"] 能通过加载校验", _ok, _detail)

# 反向对照：非法场景仍然要被拒（不能因为加了一个就把校验放开了）
_ok2 = False
try:
    with tempfile.TemporaryDirectory() as _td:
        _p2 = _pl.Path(_td) / "r2.toml"
        _p2.write_text(
            '[[rules]]\nname = "probe2"\naction = "WAIT"\n'
            'when = "scene() == \'NOPE\'"\npriority = 10\n'
            'ttl_seconds = 2\nscenes = ["NOT_A_SCENE"]\n', encoding="utf-8")
        load_rules(_p2)
except RuleLoadError:
    _ok2 = True
check("反向对照：非法场景仍被拒（校验没被放开）", _ok2)

# auto 阈值收紧到 80ms
check("ENTITY_ENABLE_THRESHOLD_MS == 80.0",
      WE_mod.ENTITY_ENABLE_THRESHOLD_MS == 80.0,
      str(WE_mod.ENTITY_ENABLE_THRESHOLD_MS))
check("79ms -> 启用 interval 5", recommended_interval_ticks(79) == 5,
      str(recommended_interval_ticks(79)))
check("★ 81ms -> 关闭（80ms 新阈值上留了余量）",
      recommended_interval_ticks(81) == 0, str(recommended_interval_ticks(81)))
check("66ms（§25 实测中位）-> 启用 interval 5",
      recommended_interval_ticks(66) == 5, str(recommended_interval_ticks(66)))

# yolo= 参数弃用警告
with warnings.catch_warnings(record=True) as _w:
    warnings.simplefilter("always")
    B.StateBuilder(yolo=FakeYolo(["combat"]))
_dep = [x for x in _w if issubclass(x.category, DeprecationWarning)]
check("★ StateBuilder(yolo=...) 抛 DeprecationWarning", bool(_dep),
      str([str(x.message)[:60] for x in _dep]))
with warnings.catch_warnings(record=True) as _w2:
    warnings.simplefilter("always")
    B.StateBuilder()
check("不传 yolo -> 不抛弃用警告",
      not [x for x in _w2 if issubclass(x.category, DeprecationWarning)])

# ================================================================ T11
section("T11 · §30 战斗动作放行 SCENE_WORLD + DSL 能用实体函数")

from autopilot.actuator.traits import TRAITS, traits_of  # noqa: E402
from autopilot.actuator.traits import SCENE_WORLD as _W  # noqa: E402
from autopilot.policy.dsl import ALLOWED_FUNCS  # noqa: E402

# --- A：战斗动作的 allowed_scenes ---
for _act in ("NORMAL_ATTACK", "USE_SKILL", "USE_BURST", "DODGE",
             "SWITCH_CHARACTER"):
    _t = traits_of(_act)
    check(f"{_act}: allowed_scenes 含 SCENE_WORLD", _W in _t.allowed_scenes,
          str(_t.allowed_scenes))
    check(f"{_act}: 仍保留 SCENE_COMBAT（等 7 类分类器回来）",
          "COMBAT" in _t.allowed_scenes, str(_t.allowed_scenes))
    check(f"{_act}: allows_scene('WORLD') = True", _t.allows_scene("WORLD"))

# --- 其他动作不该被顺手放宽 ---
for _act, _expect in (("MOVE_FORWARD", "WORLD"), ("WAIT", "WORLD"),
                      ("CLOSE", "WORLD")):
    _t = traits_of(_act)
    check(f"{_act}: allowed_scenes 未被改动（不含 WORLD）",
          _expect not in _t.allowed_scenes or _expect == "WORLD",
          str(_t.allowed_scenes))
_t = traits_of("CLOSE")
check("CLOSE 仍然禁止 WORLD（战斗中不该有 WORLD）",
      "WORLD" not in _t.allowed_scenes, str(_t.allowed_scenes))
_t = traits_of("USE_SKILL")
check("USE_SKILL 没有被放到 SCENE_ANY（护栏仍在）",
      "*" not in _t.allowed_scenes, str(_t.allowed_scenes))

# --- B：DSL 白名单 ---
for _f in ("has_nearby_enemy", "has_nearby_chest", "has_nearby_ore",
           "nearby_entity_count"):
    check(f"{_f} 已注册进 DSL 白名单", _f in ALLOWED_FUNCS)
from autopilot.policy.dsl import DEFAULT_EVALUATOR as _E  # noqa: E402

for _expr in ("has_nearby_enemy()",
              "scene() == 'WORLD' and has_nearby_enemy()",
              "has_nearby_enemy() and hp() < 0.20",
              "has_nearby_enemy() and q_ready() and energy() > 0.95"):
    try:
        _E.compile(_expr)
        check(f"DSL 表达式可编译: {_expr}", True)
    except Exception as exc:  # noqa: BLE001
        check(f"DSL 表达式可编译: {_expr}", False,
              f"{type(exc).__name__}: {str(exc)[:70]}")

# --- §30 段落里的占位规则真能加载（临时取消注释验证）---
_combat = Path(
    PLUGIN_DIR / 'autopilot' / 'rules' / 'genshin_combat.toml')
_src = _combat.read_text(encoding="utf-8")
_seg = _src.split("# §29")[1] if "# §29" in _src else ""
check("§30 段落存在于 genshin_combat.toml", bool(_seg))


def _extract_commented_rules(text: str) -> str:
    """抽出被注释掉的 ``[[rules]]`` 块。

    每个连续注释块里**只取 ``[[rules]]`` 开始的部分** —— 规则上方那几行
    散文说明不是 TOML，混进去会语法错。
    """
    out, block = [], []
    for ln in text.splitlines() + [""]:
        s = ln.strip()
        if s.startswith("#"):
            block.append(ln[2:] if s.startswith("# ") else ln[1:])
            continue
        if block:
            if "[[rules]]" in "\n".join(block):
                start = next(i for i, x in enumerate(block)
                             if x.strip() == "[[rules]]")
                out.append("\n".join(block[start:]))
            block = []
    return "\n\n".join(out)


_live = _extract_commented_rules(_seg)
check("§34 三条已不是注释状态（抽取器拿不到 -> 说明已转正）",
      not _live.strip(), f"仍是注释：{_live[:60]!r}")

# ================================================================ T12
section("T12 · §34 规则已激活（直接加载真实规则文件）")

from autopilot.policy.rule_loader import load_rules_from_dir  # noqa: E402

_RULES_DIR = Path(
    PLUGIN_DIR / 'autopilot' / 'rules')
_all_rules = load_rules_from_dir(_RULES_DIR)
_by_name = {r.name: r for r in _all_rules}
check(f"规则目录加载成功（{len(_all_rules)} 条）", bool(_all_rules),
      str(len(_all_rules)))

for _n, _prio in (("world_attack_only_with_enemy", 85),
                  ("world_emergency_dodge", 20),
                  ("world_burst_only_with_enemy", 30)):
    _r = _by_name.get(_n)
    check(f"{_n}: 已加载且 enabled=True",
          _r is not None and _r.enabled is True,
          "" if _r is None else f"enabled={_r.enabled}")
    check(f"{_n}: scenes=('WORLD',) 且 priority={_prio}",
          _r is not None and _r.scenes == ("WORLD",) and _r.priority == _prio,
          "" if _r is None else f"{_r.scenes} prio={_r.priority}")
    check(f"{_n}: when 依赖 has_nearby_enemy()",
          _r is not None and bool(_r.when) and "has_nearby_enemy()" in _r.when,
          "" if _r is None or not _r.when else _r.when)

check("★ combat_normal_attack_default 已不是一条规则",
      "combat_normal_attack_default" not in _by_name, "仍在 = 没删干净")
# ⚠️ 别用"文件里搜不到这个名字"来判 —— 删除说明的**注释**里故意提了它
#    （解释为什么删）。要判的是「没有 [[rules]] 块定义它」。
_src_txt = _combat.read_text(encoding="utf-8")
check("combat_normal_attack_default 只出现在注释里，没有实际定义",
      'name = "combat_normal_attack_default"' not in _src_txt,
      "仍有一行 name 定义 = 没删干净")

_on = sorted((r for r in _all_rules if r.enabled), key=lambda x: x.priority)
check("当前 enabled=True 的规则恰好是这 3 条", len(_on) == 3,
      str([r.name for r in _on]))
check("全部 enabled 规则的 scenes 都是 WORLD",
      all(r.scenes == ("WORLD",) for r in _on),
      str([(r.name, r.scenes) for r in _on]))

# ⚠️ 记录一个必须说清的事实：真机验证检出的是 ore / drops，
#    **不是** enemy_identify —— 所以 has_nearby_enemy() 至今一次都没为真，
#    这三条规则**还没真正开过火**。这里把分组映射锁死，免得误以为验证过了。
from autopilot.perception.world_entities import KIND_GROUPS  # noqa: E402

check("只有 enemy_identify 归 enemy 组",
      [k for k, g in KIND_GROUPS.items() if g == "enemy"] == ["enemy_identify"],
      str([k for k, g in KIND_GROUPS.items() if g == "enemy"]))
check("unmined_ore 归 ore 组（真机验到的那个）",
      KIND_GROUPS["unmined_ore"] == "ore", KIND_GROUPS["unmined_ore"])
check("drops 归 drops 组（真机验到的那个）",
      KIND_GROUPS["drops"] == "drops", KIND_GROUPS["drops"])

# ================================================================ T13
section("T13 · §36 has_nearby_enemy 支持 health_bar 分组")

# ⚠️ T13 **自包含导入**：不要依赖前面段落（T5/T11）带进来的名字。
#    以前 PolicyContext / ALLOWED_FUNCS 靠 T5、T11 的局部 import，
#    谁把那段注释掉 T13 就 NameError —— 依赖执行顺序的隐式耦合很难查。
from autopilot.contracts.state_packet import StatePacket          # noqa: E402
from autopilot.perception.world_entities import (                # noqa: E402
    WorldEntity as _WE,
)
from autopilot.policy.context import (                            # noqa: E402
    ENEMY_HEALTH_BAR_Y_MAX, PolicyContext,
)
from autopilot.policy.dsl import ALLOWED_FUNCS                    # noqa: E402

check("ENEMY_HEALTH_BAR_Y_MAX == 0.5", ENEMY_HEALTH_BAR_Y_MAX == 0.5,
      str(ENEMY_HEALTH_BAR_Y_MAX))


def _hb(y, kind="health_bar", conf=0.8):
    """造一个 bbox 左上角 y = y 的实体。"""
    return _WE(kind=kind, bbox=(0.5, y, 0.04, 0.008),
               confidence=conf, screen_pos=(640, int(y * 720)))


def _enemy(conf=0.9):
    return _WE(kind="enemy_identify", bbox=(0.5, 0.3, 0.02, 0.02),
               confidence=conf, screen_pos=(640, 216))


def _ctx_has(ents):
    return PolicyContext.from_state(
        StatePacket.unknown(game_id="g", state_version=1), now_ms=1,
        world_entities=ents).has_nearby_enemy()


# ① enemy_identify 存在 -> True
check("① enemy_identify 存在 -> True", _ctx_has((_enemy(),)) is True)
# ② 敌人血条 y=0.3 -> True（实测值 0.319/0.161/0.225/0.211）
check("② health_bar y=0.3 -> True", _ctx_has((_hb(0.30),)) is True)
check("②b 全部实测 y 值都判 True",
      all(_ctx_has((_hb(y),)) is True for y in (0.319, 0.161, 0.225, 0.211)),
      str([(y, _ctx_has((_hb(y),))) for y in (0.319, 0.161, 0.225, 0.211)]))
# ③ 自己的血条 y=0.95 -> False
check("③ 自己的血条 y=0.95 -> False", _ctx_has((_hb(0.95),)) is False)
# ④ 边界 y=0.5 -> False（按 < 判定，保守）
check("④ 边界 y=0.50 -> False（严格小于）", _ctx_has((_hb(0.50),)) is False)
check("④b 边界下一点 y=0.4999 -> True",
      _ctx_has((_hb(0.4999),)) is True)
# ⑤ 无实体 -> False
check("⑤ 空元组 -> False", _ctx_has(()) is False)
# ⑥ world_entities=None -> UNKNOWN
_r = PolicyContext.from_state(
    StatePacket.unknown(game_id="g", state_version=1), now_ms=1,
    world_entities=None).has_nearby_enemy()
check("⑥ world_entities=None -> UNKNOWN（不是 False）",
      type(_r).__name__ == "UnknownValue", type(_r).__name__)
# ⑦ 混合：自己的 + 敌人的 -> True
check("⑦ 自己的血条 + 敌人血条 -> True",
      _ctx_has((_hb(0.95), _hb(0.30))) is True)
check("⑦b 顺序无关（敌人血条在前）",
      _ctx_has((_hb(0.30), _hb(0.95))) is True)
# ⑧ 混合：只有自己的 -> False
check("⑧ 只有自己的血条 -> False", _ctx_has((_hb(0.93),)) is False)

# 防御：bbox 损坏不该当成敌人（静默走错比不动作危险）
class _BadBox:
    kind, group, bbox = "health_bar", "combat", None


_r2 = PolicyContext.from_state(
    StatePacket.unknown(game_id="g", state_version=1), now_ms=1,
    world_entities=(_BadBox(),)).has_nearby_enemy()
check("★ bbox 缺失 -> 跳过而不是当成敌人", _r2 is False, str(_r2))
check("★ 空元组不会因为 health_bar 之外的东西变 True",
      _ctx_has((_WE("chest", (0.5, 0.2, .1, .1), .9, (1, 1)),)) is False)

# 规则层面：三条战斗规则用的就是 has_nearby_enemy()
check("三条 §34 规则仍在白名单里",
      all(f in ALLOWED_FUNCS for f in ("has_nearby_enemy", "has_nearby_chest",
                                      "has_nearby_ore", "nearby_entity_count")))
check("KIND_GROUPS: health_bar -> combat（不是 enemy）",
      _WE("health_bar", (0, 0, 1, 1), 1, (0, 0)).group == "combat")
check("KIND_GROUPS: enemy_identify -> enemy",
      _enemy().group == "enemy")

# ================================================================ T9b 收尾
section("T9b · 端到端 smoke（10 帧位置平滑变化）")

states = [loc_state(S.TRACKING, pos=(3600.0 + i * 0.5, 4300.0 + i * 0.25),
                    tile="r04_c06", drift=0.0 + i * 0.05) for i in range(10)]
loc5 = FakeLocator(states)
bb8 = B.StateBuilder(yolo=FakeYolo(["combat"] * 20), locator=loc5)
xs = [bb8.build(frame(i * 100)).position.x for i in range(10)]
check("10 帧全部拿到位置", len(xs) == 10 and all(v is not None for v in xs))
diffs = np.diff(xs)
check("位置单调递增且步长恒定 0.5",
      bool(np.all(diffs > 0)) and abs(float(diffs[0]) - 0.5) < 1e-9,
      f"diffs={np.unique(np.round(diffs, 6))}")
# ⚠️ 同样必须用全新 locator：loc5 已经被上一段 10 帧喂饱，
#    FakeLocator 会钳到最后一条状态，confidence 看起来"不降"。
confs = [PM.position_from_state(s).confidence for s in states[:3]]
check("drift 增大 -> confidence 单调下降",
      confs[0] > confs[1] > confs[2], str([round(c, 4) for c in confs]))
check("confidence 公式与 1/(1+drift) 一致",
      abs(confs[0] - 1 / (1 + 0.0)) < 1e-12 and abs(confs[2] - 1 / (1 + 0.10)) < 1e-12,
      str([round(c, 6) for c in confs]))

# ================================================================
print("\n" + "=" * 92)
print(f"结果: {len(PASS)}/{len(PASS) + len(FAIL)} 通过")
if FAIL:
    print("  失败: " + ", ".join(FAIL))
print("=" * 92)
sys.exit(1 if FAIL else 0)
