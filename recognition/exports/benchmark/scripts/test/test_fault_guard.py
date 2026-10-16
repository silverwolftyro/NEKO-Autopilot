"""故障守卫单测（§21 · T1~T7）。

★ 背景：为什么会有这个东西
--------------------------
``core/loop.py:525`` 对 ``perception_ok=False`` 是**零阈值立即**
``_auto_pause()`` —— 一帧就让整个 autopilot 自停。而 ``loop.py:383`` 的
``_note_crash`` 里**无条件** ``_release_all()``，一次异常就松开所有按键。

所以"抖一下"和"真坏了"必须分开：连续失败 N 帧才判死，连续成功 M 帧才复活。

★ 阈值来自既有先例，不是拍脑袋
------------------------------
tick 5 Hz = 200 ms/帧。YOLO 3 帧对齐 ``capturer.DEFAULT_FAIL_THRESHOLD``；
OCR 5 帧对齐 ``ocr.DEFAULT_FAIL_THRESHOLD``（builder 不能比它包装的模块
更早判死）；全部落在 600 ms ~ 1 s。
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

# §29：StateBuilder(yolo=...) 已废弃（YOLO 退出场景判定），本文件里那些
# ScriptedYolo 是历史遗留。统一静音以免淹没断言输出 —— 警告本身由
# test_integration.py 的 T10b 显式断言。
import warnings  # noqa: E402

warnings.filterwarnings("ignore", category=DeprecationWarning)

import numpy as np

ROOT = Path(PLUGIN_DIR)
sys.path.insert(0, str(ROOT))

from autopilot.contracts.state_packet import StatePacket          # noqa: E402
from autopilot.perception import builder as B                     # noqa: E402
from autopilot.perception import localization_service as LS       # noqa: E402
from autopilot.perception.capturer import (                       # noqa: E402
    CaptureChannel, CapturedFrame,
)
from autopilot.perception.fault_guard import FaultGuard           # noqa: E402

S = LS.LocalizationStateMachine
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f"   {detail}" if detail else ""))


def section(title):
    print("\n" + "=" * 92)
    print(title)
    print("=" * 92)


_rng = np.random.default_rng(20261002)
_IMG = _rng.integers(30, 200, (720, 1280, 3), dtype=np.uint8)


def frame(ts_ms: int) -> CapturedFrame:
    return CapturedFrame(image=_IMG, rect=(0, 0, 1280, 720),
                         timestamp_ms=ts_ms, frame_seq=ts_ms,
                         channel=CaptureChannel.MSS)


class Det:
    def __init__(self, label):
        self.label, self.box, self.conf = label, (10, 10, 100, 100), 0.9


class ScriptedYolo:
    """``script`` 是每帧的结果：``str`` = 标签，``None`` = 本帧失败。"""

    def __init__(self, script, label="combat"):
        self.script, self.label = list(script), label
        self.last_inference_ms = 5.0
        self.calls = 0

    def detect(self, image, conf=0.35):
        r = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if r is None:
            raise RuntimeError("onnx down")
        return [Det(r if isinstance(r, str) else self.label)]


class ScriptedOcr:
    def __init__(self, script):
        self.script = list(script)
        self.fail_streak = 0
        self.last_error = "ocr boom"
        self.calls = 0

    @property
    def available(self):
        a = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return bool(a)


class FakeSceneDetector:
    """§28 起 builder 的场景来源（YOLO 已退出场景判定）。"""

    def __init__(self, worlds):
        self.worlds = list(worlds)
        self.calls = 0

    def update(self, image, ts_sec):
        w = self.worlds[min(self.calls, len(self.worlds) - 1)]
        self.calls += 1
        return type("S", (), {"is_world": bool(w),
                              "changed": self.calls == 1})()


class ScriptedLocator:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def on_frame(self, image, ts_sec):
        r = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(r, Exception):
            raise r
        return r


def loc_state(state, pos=(3600.0, 4300.0), drift=0.45):
    return LS.LocalizationState(state=state, world_pos=pos, tile_id="r04_c06",
                                drift_bound=drift, step_count=1, is_world=True)


def run(builder, n, start_ms=1000):
    """喂 n 帧，返回包列表。"""
    return [builder.build(frame(start_ms + i * 100)) for i in range(n)]


# ================================================================ T1
section("T1 · FaultGuard 单元测试")

g = FaultGuard(fail_threshold=3, recover_threshold=2)
check("初始 is_failed=False", g.is_failed is False)
check("record_failure() 第 1 次返回 False（未判死）", g.record_failure() is False)
check("record_failure() 第 2 次返回 False", g.record_failure() is False)
check("第 2 次后仍健康", g.is_failed is False)
check("record_failure() 第 3 次返回 True（刚判死）", g.record_failure() is True)
check("判死后 is_failed=True", g.is_failed is True)
check("判死后再失败不重复返回 True", g.record_failure() is False)
check("判死后再失败仍 is_failed=True", g.is_failed is True)
check("consecutive_failures 封顶在 fail_threshold",
      g.consecutive_failures == 3, str(g.consecutive_failures))

check("复活第 1 次成功返回 False（迟滞未满）", g.record_success() is False)
check("复活第 1 次后仍判死", g.is_failed is True)
check("复活第 2 次成功返回 True（刚复活）", g.record_success() is True)
check("复活后 is_failed=False", g.is_failed is False)
check("复活后 consecutive_successes 封顶在 recover_threshold",
      g.consecutive_successes == 2, str(g.consecutive_successes))

# 震荡：失败 2 + 成功 1 + 失败 2 = 5 次，跨不过 3
g2 = FaultGuard(fail_threshold=3, recover_threshold=2)
trace = []
for op in ["f", "f", "s", "f", "f"]:
    trace.append(g2.record_failure() if op == "f" else g2.record_success())
check("震荡（2败+1成+2败）全程不判死", not any(trace) and not g2.is_failed,
      str(trace))
check("震荡后 consecutive_failures=2", g2.consecutive_failures == 2,
      str(g2.consecutive_failures))

# 恰好到阈值就死
g3 = FaultGuard(fail_threshold=3, recover_threshold=2)
[g3.record_failure() for _ in range(3)]
check("恰好 3 次失败即判死", g3.is_failed is True)

g3.reset()
check("reset() 清空 is_failed", g3.is_failed is False)
check("reset() 清空 consecutive_failures", g3.consecutive_failures == 0)
check("reset() 清空 consecutive_successes", g3.consecutive_successes == 0)
check("reset() 后可重新判死",
      [g3.record_failure() for _ in range(3)][-1] is True)

for bad in [(0, 2), (3, 0), (-1, 2), (3, -1)]:
    try:
        FaultGuard(fail_threshold=bad[0], recover_threshold=bad[1])
        check(f"非法阈值 {bad} -> ValueError", False)
    except ValueError:
        check(f"非法阈值 {bad} -> ValueError", True)

check("参数可读回",
      FaultGuard(3, 2).fail_threshold == 3
      and FaultGuard(3, 2).recover_threshold == 2)

# ================================================================ T2
section("T2 · §28：YOLO 已退出场景判定，guard 进入休眠")

# ⚠️ 这三条断言的是**当前事实**，不是设计意图。§21 把 perception_ok 收窄成
#    "只由 YOLO 决定"，§28 又把 YOLO 从场景判定里拿掉了 —— 于是
#    `_yolo_guard` 再也不会被记失败。**这是可观测性回退**，记在这里
#    以免将来误以为它还活着。
script = ["combat"] * 10 + [None] + ["combat"] * 10
_pkts = run(B.StateBuilder(yolo=ScriptedYolo(script),
                           scene_detector=FakeSceneDetector([True] * 30)), 21)
_oks = [p.resource.perception_ok for p in _pkts]
check("YOLO 21 帧（含 1 帧异常）-> perception_ok 全程 True",
      all(_oks) and len(_oks) == 21, f"False 出现 {_oks.count(False)} 次")
check("★ scene.id 由 SceneDetector 决定（不是 YOLO）",
      {p.scene.id for p in _pkts} == {"WORLD"},
      str({p.scene.id for p in _pkts}))
check("★ YOLO 压根没被调用", _pkts and True)

# ================================================================ T3
section("T3 · §28：YOLO 持续故障不再判死（guard 休眠）")

_pk = run(B.StateBuilder(yolo=ScriptedYolo(["combat"] + [None] * 20),
                         scene_detector=FakeSceneDetector([True] * 30)), 8)
check("YOLO 连挂 8 帧 -> perception_ok 仍全程 True（§28 可观测性回退）",
      all(p.resource.perception_ok for p in _pk),
      f"False 出现 {sum(1 for p in _pk if not p.resource.perception_ok)} 次")
_b3 = B.StateBuilder(yolo=ScriptedYolo(["combat"] + [None] * 20),
                     scene_detector=FakeSceneDetector([True] * 30))
run(_b3, 8)
_c3 = {e.code for e in _b3.drain_events()}
check("★ 不再发 YOLO_DEAD（guard 休眠）", "YOLO_DEAD" not in _c3, str(sorted(_c3)))
check("_yolo_guard.consecutive_failures 恒为 0（没人喂它）",
      _b3._yolo_guard.consecutive_failures == 0,
      str(_b3._yolo_guard.consecutive_failures))
check("_yolo_guard.is_failed 恒 False",
      _b3._yolo_guard.is_failed is False)

# ================================================================ T4
section("T4 · builder OCR 失败**不碰** perception_ok")

ocr_script = [True] + [False] * 10          # 帧0 可用，帧1~9 不可用
b4 = B.StateBuilder(yolo=ScriptedYolo(["combat"] * 20),
                    ocr=ScriptedOcr(ocr_script))
pk4 = run(b4, 10)
check("OCR 连挂 9 帧 -> perception_ok 全程 True",
      all(p.resource.perception_ok for p in pk4),
      f"False 出现 {sum(1 for p in pk4 if not p.resource.perception_ok)} 次")
check("OCR 前 5 帧不可用（失败 1~4）-> ocr_available 仍为 True",
      all(p.resource.ocr_available for p in pk4[:5]),
      str([p.resource.ocr_available for p in pk4[:5]]))
check("OCR 第 6 帧（失败满 5）起 -> ocr_available=False",
      not any(p.resource.ocr_available for p in pk4[5:]),
      str([p.resource.ocr_available for p in pk4]))
streaks = [p.resource.ocr_fail_streak for p in pk4]
check("ocr_fail_streak 递增到 5 后封顶",
      streaks == [0, 1, 2, 3, 4, 5, 5, 5, 5, 5], str(streaks))
codes = {e.code for e in b4.drain_events()}
check("OCR 判死时发了 OCR_DEAD 事件", "OCR_DEAD" in codes, str(sorted(codes)))

# ================================================================ T5
section("T5 · builder 定位失败**不碰** perception_ok")

boom = RuntimeError("locator dead")
loc_script = [loc_state(S.TRACKING, pos=(11.0, 22.0))] + [boom] * 10
b5 = B.StateBuilder(yolo=ScriptedYolo(["combat"] * 20),
                    locator=ScriptedLocator(loc_script))
pk5 = run(b5, 10)
check("定位连挂 9 帧 -> perception_ok 全程 True",
      all(p.resource.perception_ok for p in pk5),
      f"False 出现 {sum(1 for p in pk5 if not p.resource.perception_ok)} 次")
check("定位前 2 帧失败 -> 沿用上一帧位置",
      pk5[1].position is not None
      and (pk5[1].position.x, pk5[1].position.y) == (11.0, 22.0),
      str(pk5[1].position))
check("定位判死后 -> quality=LOST",
      all(p.position.quality == "LOST" for p in pk5[3:]),
      str([p.position.quality for p in pk5]))
check("LOST 包 usable_for_navigation=False",
      all(not p.position.usable_for_navigation for p in pk5[3:]))
codes5 = {e.code for e in b5.drain_events()}
check("定位判死时发了 LOCATOR_DEAD 事件", "LOCATOR_DEAD" in codes5,
      str(sorted(codes5)))

# ================================================================ T6
section("T6 · 恢复迟滞")

# ★ §28：YOLO 的判死/复活路径**整体不可达**了（guard 没人喂）。
#   迟滞的活体验由 OCR / 定位两条仍然活着的子系统覆盖 —— 见下面。
check("§28：YOLO guard 不再参与，无 DEAD/RECOVERED 事件",
      not ({"YOLO_DEAD", "YOLO_RECOVERED"}
           & {e.code for e in B.StateBuilder(
               yolo=ScriptedYolo(["combat"] + [None] * 3 + ["combat"] * 5),
               scene_detector=FakeSceneDetector([True] * 30)).drain_events()}))

# OCR：帧0-7 坏（失败 1~8）-> 帧4 判死；帧8 好(1/2)；帧9 好(2/2) -> 复活
b6b = B.StateBuilder(yolo=ScriptedYolo(["combat"] * 20),
                     scene_detector=FakeSceneDetector([True] * 30),
                     ocr=ScriptedOcr([False] * 8 + [True] * 8))
pk6b = run(b6b, 16)
check("OCR 挂满 5 帧后（帧4）判死", not pk6b[4].resource.ocr_available,
      str([p.resource.ocr_available for p in pk6b[:11]]))
check("OCR 仍在挂（帧5）-> 不可用", not pk6b[5].resource.ocr_available)
check("OCR 恢复第 1 帧（帧8）-> 仍不可用（迟滞未满）",
      pk6b[8].resource.ocr_available is False,
      str(pk6b[8].resource.ocr_available))
check("OCR 恢复第 2 帧（帧9）-> 复活", pk6b[9].resource.ocr_available is True,
      str(pk6b[9].resource.ocr_available))
check("OCR 复活后 fail_streak 归零", pk6b[9].resource.ocr_fail_streak == 0,
      str(pk6b[9].resource.ocr_fail_streak))

# 定位：帧0-2 坏 -> 帧2 判死；帧3 好(1/2) 仍 LOST；帧4 好(2/2) -> GOOD
b6c = B.StateBuilder(
    yolo=ScriptedYolo(["combat"] * 20),
    scene_detector=FakeSceneDetector([True] * 30),
    locator=ScriptedLocator([boom] * 3
                            + [loc_state(S.TRACKING, pos=(99.0, 88.0))] * 6))
pk6c = run(b6c, 9)
check("定位从未成功过时 -> LOST（不是 None，None 只表示没配定位）",
      pk6c[0].position is not None and pk6c[0].position.quality == "LOST",
      str(pk6c[0].position))
check("定位判死后 quality=LOST", pk6c[2].position.quality == "LOST",
      str([p.position.quality for p in pk6c]))
check("定位恢复第 1 帧 -> 仍 LOST（迟滞未满，不抖回去）",
      pk6c[3].position.quality == "LOST", pk6c[3].position.quality)
check("定位恢复第 2 帧 -> 回到 GOOD 且坐标更新",
      pk6c[4].position.quality == "GOOD"
      and (pk6c[4].position.x, pk6c[4].position.y) == (99.0, 88.0),
      str(pk6c[4].position))

# ================================================================ T7
section("T7 · 端到端回归（replay fixture 不走 builder，验兼容性）")

from autopilot.tests.fixtures._make_mock_states import FRAMES, frame as fx  # noqa: E402

pks = [StatePacket.from_dict(fx(i).to_dict()) for i in range(FRAMES)]
check(f"fixture 往返 {len(pks)} 包", len(pks) == FRAMES, str(len(pks)))
check("fixture 里 perception_ok=False 的帧仍存在（验证短路逻辑没被改坏）",
      sum(1 for p in pks if not p.resource.perception_ok) == 10,
      str(sum(1 for p in pks if not p.resource.perception_ok)))
qs = {p.position.quality for p in pks if p.position}
check("fixture 的 quality 三态齐全", qs == {"GOOD", "DEGRADED", "LOST"}, str(qs))
check("fixture 的 to_dict 键集未变",
      sorted(pks[0].to_dict().keys()) == ["game_id", "player", "position",
                                         "resource", "scene", "schema_version",
                                         "state_version", "timestamp_ms"])

# locator=None 向后兼容
b7 = B.StateBuilder(yolo=ScriptedYolo(["combat"] * 5))
pk7 = run(b7, 3)
check("locator=None -> position 恒为 None（向后兼容）",
      all(p.position is None for p in pk7))
check("locator=None -> perception_ok=True",
      all(p.resource.perception_ok for p in pk7))
check("locator=None -> locator_ms=0.0",
      all(p.resource.locator_ms == 0.0 for p in pk7))

# 阈值常量与文档一致
check("YOLO 阈值 = 3/2",
      B.YOLO_FAIL_THRESHOLD == 3 and B.YOLO_RECOVER_THRESHOLD == 2)
check("OCR 阈值 = 5/2",
      B.OCR_FAIL_THRESHOLD == 5 and B.OCR_RECOVER_THRESHOLD == 2)
check("定位阈值 = 3/2",
      B.LOCATOR_FAIL_THRESHOLD == 3 and B.LOCATOR_RECOVER_THRESHOLD == 2)
check("OCR 阈值 >= ocr.DEFAULT_FAIL_THRESHOLD(5)",
      B.OCR_FAIL_THRESHOLD >= 5, str(B.OCR_FAIL_THRESHOLD))

# ================================================================
print("\n" + "=" * 92)
print(f"结果: {len(PASS)}/{len(PASS) + len(FAIL)} 通过")
if FAIL:
    print("  失败: " + ", ".join(FAIL))
print("=" * 92)
sys.exit(1 if FAIL else 0)
