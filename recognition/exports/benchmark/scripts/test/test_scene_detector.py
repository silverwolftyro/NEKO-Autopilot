"""SceneDetector 时序去抖单元测试（不依赖真实帧）。

用注入的假分类器驱动，所以跑得快、结果确定。
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

DET = Path(PLUGIN_DIR / 'autopilot' / 'perception' / 'scene_detector.py')
_spec = importlib.util.spec_from_file_location("scene_detector", DET)
_sd = importlib.util.module_from_spec(_spec)
sys.modules["scene_detector"] = _sd
_spec.loader.exec_module(_sd)

SceneDetector = _sd.SceneDetector

FPS = 10.0
DT = 1.0 / FPS


def feed(det, truths):
    """按 truths 列表逐帧喂入，返回 (states, changed_indices)。"""
    states, changes = [], []
    t = 0.0
    for i, truth in enumerate(truths):
        t += DT
        st = det.update(None, t)
        states.append(st)
        if st.changed:
            changes.append(i)
    return states, changes


def make_det(truths):
    """造一个按 truths[i] 返回判定的检测器（帧内容忽略）。"""
    seq = list(truths)
    box = {"i": 0}

    def clf(_frame):
        i = min(box["i"], len(seq) - 1)
        box["i"] += 1
        return seq[i]

    return SceneDetector(classifier=clf), box


# ------------------------------------------------------------ 测试
def t1_glitch():
    """单帧抖动过滤：100 世界 + 1 非世界 + 100 世界 -> 始终世界，无切换。"""
    truths = [True] * 100 + [False] + [True] * 100
    det, _ = make_det(truths)
    states, changes = feed(det, truths)
    bad = [i for i, s in enumerate(states) if not s.is_world]
    ok = (not changes) and not bad
    print(f"\n[T1] 单帧抖动过滤")
    print(f"  201 帧里 raw 出现非世界 1 次")
    print(f"  changed 次数 = {len(changes)}  (期望 0)")
    print(f"  稳定态变成非世界的帧 = {bad}  (期望 [])")
    print(f"  峰值 pending = "
          f"{max(s.pending_seconds for s in states):.2f}s  "
          f"(应 < N=3.0s)")
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


def t2_real_switch():
    """真实切换：5s 世界 + 10s 非世界 -> 非世界段开始后 3.0s 处切一次。"""
    truths = [True] * 50 + [False] * 100
    det, _ = make_det(truths)
    states, changes = feed(det, truths)
    ok = len(changes) == 1
    if ok:
        ci = changes[0]
        t_switch = (ci + 1) * DT
        t_nonworld_start = 50 * DT
        ok = abs((t_switch - t_nonworld_start) - 3.0) < 1e-6
    print(f"\n[T2] 真实切换（先 5s 世界建立状态，再 10s 非世界）")
    print(f"  changed 次数 = {len(changes)}  (期望 1)")
    if changes:
        ci = changes[0]
        print(f"  切换发生在第 {ci} 帧 = t={((ci+1)*DT):.2f}s"
              f"（非世界段起点 t={50*DT:.2f}s，+3.0s）")
        print(f"  该帧 stable={states[ci].is_world} raw={states[ci].raw_prediction}"
              f" pending={states[ci].pending_seconds:.2f}")
    print(f"  末尾 stable = {states[-1].is_world}  (期望 False)")
    print(f"  -> {'PASS' if ok and not states[-1].is_world else 'FAIL'}")
    return ok and not states[-1].is_world


def t3_back_and_forth():
    """来回切换：5s 世界 + 5s 非世界 + 5s 世界 -> 恰好 2 次切换。"""
    truths = ([True] * 50) + ([False] * 50) + ([True] * 50)
    det, _ = make_det(truths)
    states, changes = feed(det, truths)
    ok = len(changes) == 2 and states[-1].is_world
    print(f"\n[T3] 来回切换 5s 世界 / 5s 非世界 / 5s 世界")
    print(f"  changed 次数 = {len(changes)}  (期望 2)")
    for ci in changes:
        print(f"    第 {ci} 帧 t={((ci+1)*DT):.2f}s -> "
              f"stable={states[ci].is_world}")
    print(f"  末尾 stable = {states[-1].is_world}  (期望 True)")
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


def t4_bad_timestamps():
    """时间戳重复 / 倒退：不崩，步长钳到 0，pending 不倒退。"""
    seq = [True] * 5 + [False] * 200
    det, _ = make_det(seq)
    states, _ = feed(det, seq[:5])          # 先建立世界状态
    # 接下来手工喂异常时间戳
    t = 5 * DT
    for ts in (t, t, t - 1.0, t - 5.0, t, t + 0.5, t + 0.5):
        st = det.update(None, ts)
        states.append(st)
    pend = [s.pending_seconds for s in states]
    mono = all(pend[i] >= pend[i - 1] - 1e-12 for i in range(1, len(pend)))
    ok = all(s is not None for s in states) and mono
    print(f"\n[T4] 时间戳重复 / 倒退")
    print(f"  喂入序列: t, t, t-1.0, t-5.0, t, t+0.5, t+0.5")
    print(f"  未抛异常  (期望 True)")
    print(f"  pending 序列 = {[round(p,2) for p in pend]}")
    print(f"  pending 单调不减  {mono}  (期望 True；倒退时间戳步长钳 0)")
    print(f"  最终 stable = {states[-1].is_world}  (期望 False："
          f"非世界累计 1.0s 未达 N=3.0)")
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


def t5_reset():
    """reset() 清空状态：再次调用回到"首次调用"语义。"""
    det, _ = make_det([True, False] * 50)
    feed(det, [True] * 60)
    before = det.stable_state
    det.reset()
    after = det.stable_state
    ok = after is None
    st = det.update(None, 999.0)
    ok = ok and st.is_first and st.is_world and not st.changed
    print(f"\n[T5] reset()")
    print(f"  reset 前 stable = {before}；reset 后 = {after}  (期望 None)")
    print(f"  reset 后首帧 is_first={st.is_first} stable={st.is_world}"
          f" changed={st.changed}  (期望 True/True/False)")
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


def main():
    print("=" * 66)
    print("SceneDetector 时序去抖单元测试")
    print(f"N=3.0s  M=1.0s   帧率 {FPS}fps  步长 {DT:.2f}s")
    print("=" * 66)
    res = [("T1 单帧抖动过滤", t1_glitch()),
           ("T2 真实切换", t2_real_switch()),
           ("T3 来回切换", t3_back_and_forth()),
           ("T4 异常时间戳", t4_bad_timestamps()),
           ("T5 reset", t5_reset())]
    print("\n" + "=" * 66)
    print(f"{'测试':<20}{'判定':>10}")
    print("-" * 66)
    allok = True
    for nm, ok in res:
        print(f"{nm:<20}{'PASS' if ok else 'FAIL':>10}")
        allok &= ok
    print("=" * 66)
    print("全部通过" if allok else "有测试失败")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
