"""LocalizationService 单元测试（T1~T8 + 边界）。

★ 为什么不贴真图
------------------
单测要**确定性**的位移序列。注入 ``phase_fn`` 让"这帧走了多少"由测试决定，
``minimap_fn`` 返回固定裁块/掩膜，场景判定用真 :class:`SceneDetector`
+ 注入的 classifier。测的是**编排与状态机**，不是图像算法。

★ 场景检测器必须用真实去抖参数
--------------------------------
初版用 ``n_seconds=0.0`` 想让状态"立刻响应"，结果**每帧都抖**：
单帧判据一翻就切状态，状态在 PAUSED/TRACKING 高频跳，
位移从未被累计。去抖参数是"连续 N 秒一致才切"，不是"立即"。

    进非大世界需 N=3.0s = 30 帧（10fps）
    回大世界需 M=1.0s = 10 帧

★ 传送契约：三段全过才刷新位置
--------------------------------
初版测试假设 ``on_teleport_ocr()`` 一调用就提交坐标。新契约下
**SELECT 只是挂起**，必须再等 ② LOADING（非大世界 3s）和
③ WORLD_RESTORED（回大世界 + ``changed``）才提交。所以所有需要
tracker 的测试都改用 :meth:`Rig.teleport` / :meth:`Rig.finish_reset`。

★ 断言必须取"提交那一帧"
----------------------
提交后 ``_commit_reset`` 只清空 ``_last_mini``，**下一帧就恢复相位相关**
开始累积位移。断言"位置 == 新目标"时若多喂一帧，位置已经漂走，会假失败。
所以用 :meth:`Rig.wait_tracking` 返回**首次离开 RESETTING 的那一帧**快照。

★ 超时断言同理
--------------
``_abort_reset`` 在超时那一帧就 return，状态是回退后的目标态；再喂一帧
场景检测器会把 TRACKING 翻成 PAUSED。所以超时也必须只取一帧。
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

import importlib.util
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

PERC = (Path(PLUGIN_DIR.parent)
        / "genshin_game_buddy" / "autopilot" / "perception")

N_SEC, M_SEC, DT = 3.0, 1.0, 0.1        # 真实去抖参数
N_FRAMES, M_FRAMES = 35, 15             # 各留 5 帧余量


def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


ls_mod = _load("localization_service", PERC / "localization_service.py")
tt = _load("tile_tracker", PERC / "tile_tracker.py")
sd_mod = _load("scene_detector", PERC / "scene_detector.py")
LS = ls_mod.LocalizationService
S = ls_mod.LocalizationStateMachine

# 固定的小地图裁块/掩膜。
# ⚠️ 掩膜内必须有**纹理**：服务的 min_circle_std 守卫（默认 20.0）会拒绝
#    圆内标准差过低的帧（黑屏/纯水/纯色）。初版这里塞的是纯色圆盘，
#    标准差 = 0 -> 相位相关一次都没被调用，全部位移被守卫挡掉。
#    用确定性伪随机纹理（固定种子）保证 std 足够大且每帧可复现。
_rng = np.random.default_rng(20261002)
_CROP = _rng.integers(40, 200, (261, 261, 3), dtype=np.uint8)
_MASK = np.zeros((261, 261), np.uint8)
_yy, _xx = np.ogrid[:261, :261]
_MASK[((_yy - 130) ** 2 + (_xx - 130) ** 2) <= 128 ** 2] = 255
print(f"  [夹具] 圆内标准差 = {_CROP[_MASK > 0].std():.1f} "
      f"(需 > 20.0 才能过守卫)")

BLANK = np.zeros((4, 4, 3), np.uint8)
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f"   {detail}" if detail else ""))


class FakeTarget:
    def __init__(self, wx, wz):
        pos = (float(wx), 0.0, float(wz))
        self.matched_id = "841"
        self.name = "测试锚点"
        self.position = pos
        self.tran_position = pos
        self.confidence = 1.0


class Rig:
    """一套可控的服务 + 场景开关 + 相位调用计数。"""

    def __init__(self, steps=None):
        self.steps = list(steps or [])
        self.phase_calls = 0
        self.world = True
        det = sd_mod.SceneDetector(
            n_seconds=N_SEC, m_seconds=M_SEC,
            classifier=lambda f: self.world)
        self.svc = LS(scene_detector=det, minimap_fn=lambda f: (_CROP, _MASK),
                      phase_fn=self._phase)
        self.t = 0.0

    def _phase(self, prev, curr):
        self.phase_calls += 1
        if self.steps:
            return self.steps.pop(0)
        return 0.0, 0.0, 0.95

    def frame(self):
        st = self.svc.on_frame(BLANK, self.t)
        self.t += DT
        return st

    def hold(self, n):
        for _ in range(n):
            st = self.frame()
        return st

    # ---- 传送契约三段（① 已发出后走 ②③）
    def wait_tracking(self, limit=200):
        """喂帧直到离开 RESETTING，返回**首次离开那一帧**的快照。

        ★ 必须在提交的**那一帧**取状态。提交之后 ``_commit_reset`` 只清空
           ``_last_mini``，下一帧就又开始相位相关累积位移了 —— 多喂一帧
           位置就已经漂走，"位置 = 新目标"这条断言会假失败。
        """
        st = None
        for _ in range(limit):
            st = self.frame()
            if st.state is not S.RESETTING:
                return st
        return st

    def finish_reset(self):
        """走完 ② LOADING + ③ WORLD_RESTORED，返回提交那一帧的快照。

        ⚠️ ② 必须真的等场景检测器的 N=3.0s 去抖确认，不能靠"第一帧就
           采信"的冷启动捷径 —— 那样测不到去抖逻辑本身。
        """
        self.world = False                        # ② LOADING
        self.hold(N_FRAMES + 5)                   # 等 N=3.0s 去抖确认非大世界
        self.world = True                         # ③ WORLD_RESTORED
        return self.wait_tracking()               # 等 M=1.0s 去抖 + changed 沿

    def teleport(self, wx, wz):
        """完整一次传送：① SELECT -> ② LOADING -> ③ RESTORED。"""
        if self.t == 0.0:                         # 冷启动：先以"大世界"初始化检测器
            self.frame()
        self.svc.on_teleport_ocr(FakeTarget(wx, wz))
        return self.finish_reset()


# ================================================================ T1
print("=" * 92)
print("T1 · 状态机转移")
print("=" * 92)
r = Rig([(2.0, 0.0, 0.95)] * 200)
st = r.frame()
check("初始 = UNINITIALIZED", st.state is S.UNINITIALIZED, str(st.state))
check("未初始化 get_position() = None", r.svc.get_position() is None)

r.svc.on_teleport_ocr(FakeTarget(3600.0, 4300.0))
st = r.frame()
check("SELECT 后 = RESETTING（不是 TRACKING）", st.state is S.RESETTING, str(st.state))
check("SELECT 后起点未提交", r.svc.get_position() is None,
      str(r.svc.get_position()))
st = r.finish_reset()
check("三段全过后 = TRACKING", st.state is S.TRACKING, str(st.state))
check("起点已设置", r.svc.get_position() == (3600.0, 4300.0),
      str(r.svc.get_position()))

r.hold(8)
moved = r.svc.get_position()
check("TRACKING 中位置随位移累加", moved != (3600.0, 4300.0), str(moved))

r.world = False                            # 进菜单，需连续 3s 才切
r.hold(N_FRAMES)
st = r.frame()
check("连续非大世界 3s 后 = PAUSED", st.state is S.PAUSED, str(st.state))
frozen = r.svc.get_position()
st = r.hold(3)
check("PAUSED 中位置冻结", r.svc.get_position() == frozen)
check("PAUSED 中 position_age_s 增长", st.position_age_s > 0,
      f"age={st.position_age_s:.2f}s")

r.world = True                             # 回大世界，需连续 1s
r.hold(M_FRAMES)
st = r.frame()
check("恢复大世界 1s 后 = TRACKING", st.state is S.TRACKING, str(st.state))

r.svc.on_teleport_ocr(FakeTarget(150.0, 300.0))
st = r.frame()
check("再次传送 SELECT = RESETTING", st.state is S.RESETTING, str(st.state))
check("再次传送期间位置冻结在旧值", r.svc.get_position() != (150.0, 300.0),
      str(r.svc.get_position()))
st = r.finish_reset()
check("再次传送三段全过 = TRACKING", st.state is S.TRACKING, str(st.state))
check("位置重置到新目标", r.svc.get_position() == (150.0, 300.0),
      str(r.svc.get_position()))

# ================================================================ T2
print("\n" + "=" * 92)
print("T2 · 非大世界期间不计位移")
print("=" * 92)
r2 = Rig([(2.0, 0.0, 0.95)] * 300)
r2.teleport(3600.0, 4300.0)                        # 走完三段才建 tracker
r2.hold(6)
steps_world = r2.svc.tracker.step_count

# ⚠️ 去抖要 N=3.0s 才确认"进了菜单"。这 3 秒内 scene_detector 仍判
#    大世界，**继续追踪是正确行为**（§任务 B 的 LOADING 阶段）。
#    所以要等状态真的变成 PAUSED 之后再断言"不再调用"。
r2.world = False
st = None
for _ in range(N_FRAMES + 5):
    st = r2.frame()
    if st.state is S.PAUSED:
        break
check("进菜单后最终到达 PAUSED", st.state is S.PAUSED, str(st.state))
calls_at_pause = r2.phase_calls
steps_at_pause = r2.svc.tracker.step_count

r2.hold(30)                                # PAUSED 之后继续喂 30 帧
check("PAUSED 之后 phase_fn 不再被调用",
      r2.phase_calls == calls_at_pause,
      f"PAUSED时={calls_at_pause} 之后={r2.phase_calls}")
check("PAUSED 之后步数不增",
      r2.svc.tracker.step_count == steps_at_pause,
      f"steps {steps_at_pause} -> {r2.svc.tracker.step_count}")
pos_a = r2.svc.get_position()
r2.hold(10)
check("PAUSED 期间位置持续冻结", r2.svc.get_position() == pos_a)

r2.world = True
r2.hold(M_FRAMES + 5)
r2.hold(5)
steps_after = r2.svc.tracker.step_count
check("恢复后继续累计（= 离开段 + 回程段）",
      steps_after > steps_at_pause, f"steps {steps_at_pause} -> {steps_after}")
print(f"      位置 {pos_a} -> {r2.svc.get_position()}")

# ================================================================ T3
print("\n" + "=" * 92)
print("T3 · 传送重置")
print("=" * 92)
r3 = Rig([(2.0, 0.0, 0.95)] * 300)
r3.teleport(3600.0, 4300.0)                        # 走完三段才建 tracker
r3.hold(12)
before = r3.svc.get_position()
drift_before = r3.svc.tracker.drift_bound
check("走段后位置已偏离起点", before != (3600.0, 4300.0), str(before))
check("走段后有漂移累积", drift_before > 0, f"{drift_before:.3f}")

NEW = (-1200.0, 800.0)
r3.svc.on_teleport_ocr(FakeTarget(*NEW))
check("重置 SELECT 期间位置未变", r3.svc.get_position() == before,
      str(r3.svc.get_position()))
r3.finish_reset()
check("重置后位置 = 新目标", r3.svc.get_position() == NEW,
      str(r3.svc.get_position()))
check("重置后步数清零", r3.svc.tracker.step_count == 0,
      f"{r3.svc.tracker.step_count}")
check("重置后漂移清零", r3.svc.tracker.drift_bound == 0.0,
      f"{r3.svc.tracker.drift_bound}")
check("重置后瓦片缓存作废", r3.svc._last_mini is None)
check("重置用的是 tran_position 而非 position",
      getattr(FakeTarget(1.0, 2.0), "tran_position")[0] == 1.0)

# ================================================================ 边界
print("\n" + "=" * 92)
print("边界与异常")
print("=" * 92)
r4 = Rig([(0.0, 0.0, 0.02)] * 50)          # 峰值极低
r4.teleport(3600.0, 4300.0)
r4.hold(3)
st = r4.frame()
check("峰值低于阈值 -> 跳过位移",
      "below threshold" in st.note and r4.svc.tracker.step_count == 0,
      st.note)

r5 = Rig([(99.0, 0.0, 0.99)] * 50)          # 传送级跳变
r5.teleport(3600.0, 4300.0)
r5.hold(3)
st = r5.frame()
check("超阈值跳变 -> tracker 拒绝，位置不动",
      "rejected" in st.note and r5.svc.get_position() == (3600.0, 4300.0),
      st.note)

r6 = Rig()
r6.teleport(3600.0, 4300.0)
r6.hold(3)
check("附近瓦片可返回 9 块", len(r6.svc.nearby_tiles()) == 9,
      str(len(r6.svc.nearby_tiles())))

try:
    r6.svc.on_teleport_ocr(object())
    check("无落点坐标 -> ValueError", False)
except ValueError:
    check("无落点坐标 -> ValueError", True)

try:
    r6.svc.on_frame(BLANK, None)
    check("timestamp=None -> TypeError", False)
except TypeError:
    check("timestamp=None -> TypeError", True)

# ================================================================ T4
print("\n" + "=" * 92)
print("T4 · SELECT 后不应立即回 TRACKING（必须等三段）")
print("=" * 92)
r7 = Rig([(2.0, 0.0, 0.95)] * 300)
r7.svc.on_teleport_ocr(FakeTarget(3600.0, 4300.0))
r7.hold(5)
st = r7.frame()
check("SELECT 后状态 = RESETTING（不是 TRACKING）",
      st.state is S.RESETTING, str(st.state))
check("RESETTING 期间位置仍是 None（未初始化时不提交）",
      r7.svc.get_position() is None, str(r7.svc.get_position()))
check("SELECT 后不立即提交坐标",
      r7.svc.tracker is None or r7.svc.get_position() is None)

# ================================================================ T5
print("\n" + "=" * 92)
print("T5 · 三段全过才刷新位置")
print("=" * 92)
r8 = Rig([(2.0, 0.0, 0.95)] * 400)
r8.svc.on_teleport_ocr(FakeTarget(3600.0, 4300.0))
r8.hold(5)
st = r8.frame()
check("SELECT -> RESETTING", st.state is S.RESETTING, str(st.state))
check("未三段全过时位置未提交", r8.svc.get_position() is None,
      str(r8.svc.get_position()))

r8.world = False                          # ② LOADING
r8.hold(N_FRAMES + 5)                     # 检测器确认非大世界 3s
st = r8.frame()
check("LOADING 后仍 RESETTING（未收到 ③）", st.state is S.RESETTING,
      str(st.state))
check("LOADING 阶段位置仍未提交", r8.svc.get_position() is None,
      str(r8.svc.get_position()))

r8.world = True                           # ③ WORLD_RESTORED (changed=True)
st = r8.wait_tracking()                   # ★ 取"提交那一帧"，多喂一帧位置就漂了
check("三段全过 -> TRACKING", st.state is S.TRACKING, str(st.state))
check("三段全过 -> 位置 = 新目标", st.world_pos == (3600.0, 4300.0),
      str(st.world_pos))
check("三段全过 -> 步数从零开始", r8.svc.tracker.step_count == 0,
      f"{r8.svc.tracker.step_count}")

# ================================================================ T6
print("\n" + "=" * 92)
print("T6 · 三段中断/超时 -> 作废，保留旧位置")
print("=" * 92)
r9 = Rig([(2.0, 0.0, 0.95)] * 400)
r9.teleport(3600.0, 4300.0)                # 先建立起点 -> TRACKING
r9.hold(5)
r9.svc.on_teleport_ocr(FakeTarget(150.0, 300.0))   # 第二次传送挂起
r9.world = False                           # 进 LOADING（② 满足）
r9.hold(N_FRAMES + 5)
r9.frame()
check("超时前在 RESETTING", r9.svc._state is S.RESETTING, str(r9.svc._state))
pos_frozen = r9.svc.get_position()
# 直接把 deadline 推到过去，模拟超时
r9.svc._reset_deadline = r9.t - 31.0
r9.world = True                            # 恢复但已超时
st = r9.frame()                            # ★ 恰好在超时那一帧取状态
check("超时 -> 作废回原状态 TRACKING", st.state is S.TRACKING, str(st.state))
check("超时 -> 待提交目标被清空", r9.svc._pending_target is None)
check("超时 -> 待提交坐标被清空", r9.svc._pending_pos is None)
check("超时 -> 位置保留旧值（未被污染）",
      r9.svc.get_position() == pos_frozen,
      f"{r9.svc.get_position()} vs {pos_frozen}")

# TRACKING 中途超时，保留旧位置（更贴近真实）
r10 = Rig([(2.0, 0.0, 0.95)] * 400)
r10.teleport(3600.0, 4300.0)
r10.hold(6)
pos_before = r10.svc.get_position()        # 追踪到某处
check("走段后确实不在起点", pos_before != (3600.0, 4300.0), str(pos_before))
r10.svc.on_teleport_ocr(FakeTarget(150.0, 300.0))   # 挂起新目标
r10.svc._reset_deadline = r10.t - 31.0     # 超时
st = r10.frame()                           # ★ 恰好在超时那一帧取状态
check("追踪中超时 -> 回 TRACKING", st.state is S.TRACKING, str(st.state))
check("追踪中超时 -> 旧位置保留", r10.svc.get_position() == pos_before,
      f"{r10.svc.get_position()} vs {pos_before}")
check("追踪中超时 -> 新目标未提交", r10.svc._pending_pos is None)

# ================================================================ T7
print("\n" + "=" * 92)
print("T7 · RESETTING 期间不累积位移")
print("=" * 92)
r11 = Rig([(2.0, 0.0, 0.95)] * 400)
r11.svc.on_teleport_ocr(FakeTarget(3600.0, 4300.0))
r11.hold(5)
r11.frame()
steps_at_reset = r11.svc.tracker.step_count if r11.svc.tracker else 0
pos_at_reset = r11.svc.get_position()
calls_at_reset = r11.phase_calls
# 在 RESETTING 期间持续喂 40 帧（scene 保持 world，不推进契约）
r11.hold(40)
check("RESETTING 期间步数不增",
      (r11.svc.tracker.step_count if r11.svc.tracker else 0) == steps_at_reset,
      f"{steps_at_reset} -> {r11.svc.tracker.step_count if r11.svc.tracker else 0}")
check("RESETTING 期间位置不变", r11.svc.get_position() == pos_at_reset)
check("RESETTING 期间不做相位相关", r11.phase_calls == calls_at_reset,
      f"{calls_at_reset} -> {r11.phase_calls}")

# ================================================================ T8
print("\n" + "=" * 92)
print("T8 · PAUSED 期间收到新传送 OCR")
print("=" * 92)
r12 = Rig([(2.0, 0.0, 0.95)] * 400)
r12.teleport(3600.0, 4300.0)
r12.hold(6)
r12.world = False                          # 进菜单 -> PAUSED
r12.hold(N_FRAMES + 5)
st = r12.frame()
check("先到 PAUSED", st.state is S.PAUSED, str(st.state))
pos_paused = r12.svc.get_position()
r12.svc.on_teleport_ocr(FakeTarget(150.0, 300.0))   # 菜单里按传送
st = r12.frame()
check("PAUSED 中 SELECT -> RESETTING", st.state is S.RESETTING, str(st.state))
check("PAUSED 起传送 ② 天然满足（_loading_seen 预置）",
      r12.svc._loading_seen is True)
check("PAUSED 中 SELECT -> 位置未提交", r12.svc.get_position() == pos_paused,
      f"{r12.svc.get_position()} vs {pos_paused}")
# ② LOADING 已在 PAUSED 期间满足（进入 RESETTING 时 _loading_seen=True），
# 只需等 ③：恢复大世界 + changed
r12.world = True
st = r12.wait_tracking()                   # ★ 取"提交那一帧"
check("三段全过 -> TRACKING(新起点)", st.state is S.TRACKING, str(st.state))
check("三段全过 -> 位置 = 新目标", st.world_pos == (150.0, 300.0),
      str(st.world_pos))

# ================================================================ T9
print("\n" + "=" * 92)
print("T9 · RESETTING 期间重复 OCR（防永久卡死）")
print("=" * 92)
# ⚠️ 这是修完 §19 后复核时发现的洞：重复 SELECT 若照写不误，
#    _reset_prev_state 会被覆盖成 RESETTING，超时 abort 后又回到 RESETTING，
#    而 deadline 每帧被重新武装 -> 服务永远出不来。
r13 = Rig([(2.0, 0.0, 0.95)] * 400)
r13.teleport(3600.0, 4300.0)                # 建立起点 -> TRACKING
r13.hold(5)
r13.svc.on_teleport_ocr(FakeTarget(150.0, 300.0))
deadline_1 = r13.svc._reset_deadline
r13.hold(3)
r13.svc.on_teleport_ocr(FakeTarget(-50.0, -60.0))     # 重复命中
check("重复 OCR -> 回退目标不被覆盖成 RESETTING",
      r13.svc._reset_prev_state is S.TRACKING, str(r13.svc._reset_prev_state))
check("重复 OCR -> 不重置超时锚点", r13.svc._reset_deadline == deadline_1,
      f"{deadline_1} -> {r13.svc._reset_deadline}")
check("重复 OCR -> 目标换成最新一次",
      r13.svc._pending_pos == (-50.0, -60.0), str(r13.svc._pending_pos))
st = r13.finish_reset()
check("重复 OCR 后三段全过 -> TRACKING", st.state is S.TRACKING, str(st.state))
check("重复 OCR 后提交的是最新目标", st.world_pos == (-50.0, -60.0),
      str(st.world_pos))

# 端到端证明"不会卡死"：重复 OCR 后超时，必须回到 TRACKING
r14 = Rig([(2.0, 0.0, 0.95)] * 400)
r14.teleport(3600.0, 4300.0)
r14.hold(5)
pos_14 = r14.svc.get_position()
r14.svc.on_teleport_ocr(FakeTarget(150.0, 300.0))
r14.hold(3)
r14.svc.on_teleport_ocr(FakeTarget(-50.0, -60.0))     # 重复命中
r14.svc._reset_deadline = r14.t - 31.0
st = r14.frame()
check("重复 OCR 后超时 -> 回 TRACKING（不是卡死）",
      st.state is S.TRACKING, str(st.state))
check("重复 OCR 后超时 -> 旧位置保留", r14.svc.get_position() == pos_14,
      f"{r14.svc.get_position()} vs {pos_14}")

print("\n" + "=" * 92)
print(f"结果: {len(PASS)}/{len(PASS)+len(FAIL)} 通过")
if FAIL:
    print("  失败: " + ", ".join(FAIL))
print("=" * 92)
sys.exit(1 if FAIL else 0)
