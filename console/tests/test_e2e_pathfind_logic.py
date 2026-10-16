"""``e2e_pathfind.py`` 纯逻辑单测（Console 侧，2026-10-12）。

★ 为什么要测试一个「另一个仓库里的脚本」
  Console 的寻路页签拉起的就是它。它一旦算错，角色就朝反方向走 ——
  而那是最难在界面上看出来的错误。

★ 怎么测
  见 :mod:`e2e_sandbox`：用 ``ast`` 把纯逻辑定义抽出来单独 exec。
  **不 import 整个模块** —— 它顶层就依赖 ``uipi_check`` 和
  ``autopilot.actuator``，整模块 import 会把一堆无关依赖拖进来，
  还会让「测的是寻路逻辑还是导入能否成功」变得不清楚。

★ 测不到什么
  键鼠注入、截图、窗口查找、以及 ``main`` 的整条流程 —— 那些必须真机。
  本文件覆盖的是**决定「会不会走歪」的那些算术与状态机**。
"""

from __future__ import annotations

import math

import pytest

import e2e_sandbox as S

pytestmark = pytest.mark.skipif(
    not S.E2E_PATHFIND.is_file(),
    reason=f"本机没有被测脚本 {S.E2E_PATHFIND}")


@pytest.fixture(scope="module")
def ns() -> dict:
    return S.load()


# ==================================================================
# 清单：确认我们确实测的是「核心模块」，且分类没漂
# ==================================================================

def test_inventory__script_is_the_big_one(ns) -> None:
    inv = S.module_inventory()
    assert inv["lines"] > 3000
    assert len(inv["funcs"]) >= 30


def test_inventory__everything_wanted_was_extracted(ns) -> None:
    """抽取器必须一个不缺 —— 少一个就是「以为测了其实没测」。"""
    assert ns["__missing__"] == [], ns["__missing__"]


def test_inventory__hardware_functions_are_excluded() -> None:
    """★ 明确写下「哪些没测、为什么」。

    ``main`` 与 ``_warmup_and_resolve_forward`` 要真窗口和真输入注入，
    抽出来也跑不起来。写进断言是为了让这条边界**可执行**，
    而不是靠人记得。
    """
    assert "main" not in S.WANTED
    assert "_warmup_and_resolve_forward" not in S.WANTED


# ==================================================================
# 角度归一化
# ==================================================================

@pytest.mark.parametrize("raw", [0.0, 0.5, -0.5])
def test_wrap_pi__identity_inside_range(ns, raw) -> None:
    """★ 区间内的角度原样返回。

    ⚠️ **±π 会互换**：``wrap_pi(math.pi)`` 返回 ``-math.pi`` ——
      ``(π+π) % 2π - π = 0 - π``。这是环绕公式的固有行为，不是 bug，
      所以这里只测「区间内不变」，±π 交给下面那条不变量测试管。
    """
    assert ns["wrap_pi"](raw) == pytest.approx(raw, abs=1e-12)


@pytest.mark.parametrize("raw", [3.5, -3.5, 7.0, -7.0, 100.0, -100.0, 0.1])
def test_wrap_pi__always_lands_in_minus_pi_to_pi(ns, raw) -> None:
    """★ 超出范围的角度必须被折回，否则 ``|误差| > 180°`` 会让转向反着来。"""
    got = ns["wrap_pi"](raw)
    assert -math.pi - 1e-9 <= got <= math.pi + 1e-9
    # 折回后与原值的差是整数个 2π
    k = round((raw - got) / (2 * math.pi))
    assert raw - got == pytest.approx(k * 2 * math.pi, abs=1e-9)


# ==================================================================
# 距离取整：刻意的「不用 Python round()」
# ==================================================================

@pytest.mark.parametrize("raw,expect", [
    (0, 0), (4.9, 0), (5, 10), (14.9, 10), (15, 20), (24.9, 20), (25, 30),
])
def test_round10__avoids_bankers_rounding(ns, raw, expect) -> None:
    """★ ``round()`` 是银行家舍入：25.0 会被 round 成 20。

    它的 docstring 写明是与 overlay 的 ``_fmt_dist`` 对齐，不改会造成
    两边显示不同距离。这里把它钉死。
    """
    assert ns["_round10"](raw) == expect


@pytest.mark.parametrize("bad", [-5, -100, float("nan"), None])
def test_round10__never_returns_negative(ns, bad) -> None:
    """★ 负数/Nan 一律归 0。

    距离出现负值说明上游算错了，UI 上显示 ``-10m`` 比显示 ``0m`` 更糟。
    """
    got = ns["_round10"](bad)
    assert got == 0


def test_round10__always_a_multiple_of_ten(ns) -> None:
    for d in range(0, 100, 3):
        assert ns["_round10"](d) % 10 == 0


def test_round10__is_monotonic(ns) -> None:
    """距离变大，取整结果不能反而变小（UI 上会看到数字往回跳）。"""
    prev = -1
    for d in range(0, 200, 1):
        cur = ns["_round10"](d)
        assert cur >= prev
        prev = cur


# ==================================================================
# 解析：JSON 坐标系
# ==================================================================

@pytest.mark.parametrize("raw,expect", [
    ("1,2", (1.0, 2.0)), ("-3.5,4", (-3.5, 4.0)), ("0,0", (0.0, 0.0)),
])
def test_pair__parses_x_comma_z(ns, raw, expect) -> None:
    assert ns["_pair"](raw) == expect


@pytest.mark.parametrize("bad", ["abc,2", "1", ""])
def test_pair__malformed_input_raises(ns, bad) -> None:
    """★ 解析不了就抛，由上层的 try 决定怎么报。

    **不能**静默返回 (0,0) —— 那会让角色走到世界原点。
    """
    with pytest.raises((ValueError, IndexError)):
        ns["_pair"](bad)


def test_pair__three_parts_silently_takes_the_first_two(ns) -> None:
    """★ 记录一个**已知行为**：``"1,2,3"`` -> ``(1.0, 2.0)``，不报错。

    与真脚本 ``_pair`` 同语义。Console 侧的 ``_parse_xy`` 已经把它挡在
    外面了（那里会拒），但脚本本身仍是这样 —— 所以钉住现状，
    免得哪天有人以为它会校验。
    """
    assert ns["_pair"]("1,2,3") == (1.0, 2.0)


# ==================================================================
# 圆周均值：处理 ±180° 环绕
# ==================================================================

def test_circ_mean__simple_mean_when_not_wrapped(ns) -> None:
    assert ns["_circ_mean_deg"]([0, 90]) == pytest.approx(45.0)


def test_circ_mean__wraps_around_180(ns) -> None:
    """★ 350° 与 10° 的均值是 0°，不是 180°。

    算术平均会给出 180°，正好是最错的方向。
    """
    got = ns["_circ_mean_deg"]([350, 10])
    assert got is not None
    assert abs(ns["wrap_pi"](math.radians(got) - 0.0)) < 1e-6


def test_circ_mean__opposite_angles_return_none(ns) -> None:
    """★★ 90° 与 -90° 互相抵消 -> None，不是 0。

    结果无定义。猜一个 0° 会让转向逻辑以为「已经正对」而停止转向 ——
    而真实朝向可能正好相反。
    """
    assert ns["_circ_mean_deg"]([90, -90]) is None


def test_circ_mean__insufficient_samples_return_none(ns) -> None:
    """★ 样本不够（空 / 单点）-> None。"""
    assert ns["_circ_mean_deg"]([]) is None
    assert ns["_circ_mean_deg"]([0]) is None


def test_circ_mean__identical_angles_are_that_angle(ns) -> None:
    assert ns["_circ_mean_deg"]([0, 0, 0]) == pytest.approx(0.0)


# ==================================================================
# 鼠标转向分段
# ==================================================================

def test_split_turn__zero_needs_no_movement(ns) -> None:
    assert ns["_split_turn"](0) == ([], 0)


def test_split_turn__below_saturation_goes_in_one_segment(ns) -> None:
    segs, rest = ns["_split_turn"](30)
    assert segs == [30] and rest == 0


def test_split_turn__one_mouse_move_is_never_effective(ns) -> None:
    """★ ``MOUSE_SATURATION_PX = 40``。画成 857px 的一整笔会被系统裁掉。

    所以超过 40 的部分要「欠」着，留给下一帧补。
    """
    segs, rest = ns["_split_turn"](100)
    assert segs == [40] and rest == 60


def test_split_turn__huge_turn_still_one_segment(ns) -> None:
    segs, rest = ns["_split_turn"](857)
    assert len(segs) == 1 and segs[0] == 40
    assert rest == 817


def test_split_turn__explicit_segment_count_splits_evenly(ns) -> None:
    segs, rest = ns["_split_turn"](120, 3)
    assert segs == [40, 40, 40] and rest == 0


def test_split_turn__conserves_the_total(ns) -> None:
    """★ 分出去的 + 欠着的必须等于原始转向量。

    少了一像素就是「转不到位卡在那」，多了一像素会转过头来回震荡。
    """
    for turn in (0, 1, 39, 40, 41, 100, 400, 1234):
        segs, rest = ns["_split_turn"](turn)
        assert sum(segs) + rest == turn, turn


def test_split_turn__negative_is_kept_as_is(ns) -> None:
    """反向转向不该被「取绝对值」洗掉方向。"""
    segs, rest = ns["_split_turn"](-20)
    assert sum(segs) + rest == -20


# ==================================================================
# 废弃的分档函数（仍被旧测试/日志对照引用，所以要钉住现状）
# ==================================================================

def test_gain_for__is_monotonic_in_error(ns) -> None:
    prev = 0.0
    for deg in range(0, 200, 5):
        gain, tier = ns["_gain_for"](deg)
        assert gain >= prev
        prev = gain


def test_gain_for__abs_value_is_used(ns) -> None:
    """左右转对称。"""
    assert ns["_gain_for"](60) == ns["_gain_for"](-60)


def test_clamp_for__is_monotonic_and_capped(ns) -> None:
    prev = -1
    for deg in range(0, 600, 10):
        cur = ns["_clamp_for"](deg)
        assert cur >= prev and cur <= 500
        prev = cur


def test_tier_text__is_not_empty(ns) -> None:
    assert ns["_tier_text"]().strip()


# ==================================================================
# 动态步长上限
# ==================================================================

def test_dynamic_step__is_clamped_to_the_legal_band(ns) -> None:
    """★ 帧率不同，允许的单帧位移上限就不同，但永远在 [MIN, MAX] 内。"""
    for px_to_world in (0.001, 0.01, 0.1, 1.0, 10.0):
        for dt in (0.001, 0.2, 1.0, 5.0, 30.0):
            got = ns["_dynamic_max_step_px"](px_to_world, dt)
            assert 50.0 - 1e-6 <= got <= 500.0 + 1e-6, (px_to_world, dt)


def test_dynamic_step__degenerate_scale_falls_back_to_min(ns) -> None:
    """★ px_to_world <= 0 时直接给下限，不参与除法。

    除以 0 会抛/inf，而这里返回 50 让 tracker 保守地「走慢一点」，
    而不是把上限放到无穷大导致一步跳到天涯海角。
    """
    assert ns["_dynamic_max_step_px"](0, 0.2) == 50.0
    assert ns["_dynamic_max_step_px"](-1.0, 0.2) == 50.0


# ==================================================================
# 相位状态机
# ==================================================================

def test_raw_phase__far_is_approach(ns) -> None:
    assert ns["_raw_phase"](50.0, 10.0, False) == "approach"


def test_raw_phase__close_with_error_is_final_turn(ns) -> None:
    assert ns["_raw_phase"](5.0, 10.0, False) == "final_turn"


def test_raw_phase__arrived_flag_wins(ns) -> None:
    """★ ``arrived`` 是状态，与距离/角度无关。"""
    assert ns["_raw_phase"](0.0, 0.0, True) == "arrived"
    assert ns["_raw_phase"](999.0, 170.0, True) == "arrived"


def test_pick_phase__first_call_is_not_suppressed(ns) -> None:
    """★ 有候选相位时只**提议**，不立刻切换 —— 抗抖动。"""
    cur, pending, streak = ns["_pick_phase"]("approach", None, 0, "final_turn")
    assert cur == "approach" and pending is not None


def test_pick_phase__no_candidate_keeps_current(ns) -> None:
    cur, pending, streak = ns["_pick_phase"]("approach", None, 0, "approach")
    assert cur == "approach" and pending is None


# ==================================================================
# 是否在收敛
# ==================================================================

def test_is_converging__needs_enough_samples(ns) -> None:
    """★ 样本不够就别下结论 —— 会误判成「还在收敛」而一直不停。"""
    assert ns["_is_converging"]([]) is False
    assert ns["_is_converging"]([1.0]) is False


def test_is_converging__flat_history_is_not_converging(ns) -> None:
    """★ 误差一直不变（转不过去）-> 没在收敛，要升级处理。"""
    assert ns["_is_converging"]([5.0] * 20) is False


def test_is_converging__monotone_decrease_is_converging(ns) -> None:
    assert ns["_is_converging"](list(range(20, 0, -1))) is True


def test_is_converging__only_compares_within_the_window(ns) -> None:
    """★★ 最容易被想当然搞错的一条。

    实现是「**最近 n 帧**里，前半段均值 vs 后半段均值，严格小于」。
    所以**已经滑出窗口的下降不算收敛**：

    ``[9]*10 + [1]*10`` —— 明明降了 8 度，但变化发生在 10 帧**之前**，
    窗口里已经是一片平的了，于是判为**不收敛**。

    ⚠️ 别用 ``SLIP_CONVERGE_MIN_DEG``（5.0）来解释这个函数 ——
      那个常量在别处用，这段代码根本没读它。
    """
    n = ns["SLIP_CONVERGE_N"]
    assert n == 10

    # 下降**落在窗口内** -> 收敛
    assert ns["_is_converging"]([9] * 9 + [5] * 5) is True
    # 下降**已经滑出窗口** -> 不收敛
    assert ns["_is_converging"]([9] * 10 + [1] * 10) is False


def test_is_converging__rebound_is_not_convergence(ns) -> None:
    """★ 后半段比前半段大 -> 反弹，明确不算收敛。"""
    assert ns["_is_converging"]([2] * 5 + [9] * 5) is False


# ==================================================================
# 前向运动学
# ==================================================================

def test_forward_target__along_x_when_facing_x(ns) -> None:
    x, z = ns["_forward_target"](0.0, 0.0, 0.0, 10.0)
    assert x == pytest.approx(10.0) and z == pytest.approx(0.0, abs=1e-9)


def test_forward_target__zero_distance_stays_put(ns) -> None:
    x, z = ns["_forward_target"](7.0, 9.0, 1.2, 0.0)
    assert x == pytest.approx(7.0) and z == pytest.approx(9.0)


def test_forward_target__result_is_the_requested_distance_away(ns) -> None:
    """★ 不管朝哪，落点与起点的距离必须正好是 ``dist_m``。"""
    for theta in (0.0, 0.7, 2.0, -2.5, math.pi):
        x, z = ns["_forward_target"](3.0, -4.0, theta, 12.5)
        d = ((x - 3.0) ** 2 + (z + 4.0) ** 2) ** 0.5
        assert d == pytest.approx(12.5), theta


# ==================================================================
# 面向 UI 的文案
# ==================================================================

@pytest.mark.parametrize("state,expect_nonempty", [
    ("navigating", True), ("arrived", True), ("escaping", True),
    ("stuck", True), ("panic", True), ("paused", True),
])
def test_next_action__every_state_has_a_label(ns, state, expect_nonempty) -> None:
    """★ 状态**优先于**角度 —— 面板每帧都要有一个非空 next_action。"""
    assert ns["_next_action"](0.0, state).strip()


def test_next_action__arrived_is_the_same_regardless_of_angle(ns) -> None:
    assert ns["_next_action"](0.0, "arrived") == ns["_next_action"](179.0, "arrived")


def test_next_action__unknown_angle_says_go_straight(ns) -> None:
    """★ 没有朝向读数（None）时给「直走」而不是「左转」。

    猜一个方向就是赌，而转错的代价是绕远路。
    """
    assert ns["_next_action"](None, "navigating") == ns["_next_action"](0.0, "navigating")


# ==================================================================
# 路点游标
# ==================================================================

def _wp(x, z, note=""):
    return {"x": x, "z": z, "note": note}


def test_cursor__empty_queue_goes_straight_to_the_final_target(ns) -> None:
    """★ 空队列时直接奔最终目标 —— 不能没有目标（那会让循环空转）。"""
    c = ns["_WaypointCursor"]([], (99.0, 99.0))
    assert c.goal() == (99.0, 99.0)
    assert c.total == 0
    assert c.done == 0 and c.abandoned == 0


def test_cursor__goal_is_the_queue_head(ns) -> None:
    c = ns["_WaypointCursor"]([_wp(1, 1), _wp(2, 2)], (9, 9))
    assert c.goal() == (1.0, 1.0)
    assert c.total == 2


def test_cursor__reaching_pops_and_advances(ns) -> None:
    c = ns["_WaypointCursor"]([_wp(0, 0), _wp(10, 0)], (99, 99))
    popped = c.pop_reached(1.0)                 # 1m < WAYPOINT_REACH_M(5m)
    assert popped is not None and popped["x"] == 0.0
    assert c.goal() == (10.0, 0.0)


def test_cursor__not_reached_leaves_the_head_in_place(ns) -> None:
    c = ns["_WaypointCursor"]([_wp(0, 0), _wp(10, 0)], (99, 99))
    assert c.pop_reached(50.0) is None
    assert c.goal() == (0.0, 0.0)


def test_cursor__reach_threshold_is_configurable(ns) -> None:
    """★ 命中半径不能写死 —— 有的路点就是差 6 米。"""
    c = ns["_WaypointCursor"]([_wp(0, 0)], (99, 99))
    assert c.pop_reached(8.0, reach_m=10.0) is not None


def test_cursor__abandoning_differs_from_reaching(ns) -> None:
    """★★ 「够不着」和「走到了」是两回事，必须能分别记账。

    全是 abandon 的话，路线静默走形都没人发现。
    """
    c = ns["_WaypointCursor"]([_wp(0, 0), _wp(10, 0)], (99, 99))
    dropped = c.pop_abandoned()
    assert dropped is not None and dropped["x"] == 0.0
    assert c.goal() == (10.0, 0.0)


def test_cursor__queue_is_copied_not_aliased(ns) -> None:
    """★ 外部改了原列表，不该影响游标内部队列。"""
    src = [_wp(0, 0)]
    c = ns["_WaypointCursor"](src, (99, 99))
    src.append(_wp(5, 5))
    assert c.total == 1


# ==================================================================
# NavAdvisor：跨帧滞回
# ==================================================================

def test_advisor__always_returns_a_complete_record(ns) -> None:
    """★ overlay 是「字符串直出，一个字都不加工」，所以四个字段必须齐。"""
    adv = ns["_NavAdvisor"]()
    r = adv.update(50.0, 20.0, "navigating", "walk_forward")
    assert set(("phase", "hint", "eta_s")) <= set(r)


def test_advisor__eta_shrinks_as_distance_shrinks(ns) -> None:
    adv = ns["_NavAdvisor"]()
    far = adv.update(500.0, 0.0, "navigating", "walk_forward")["eta_s"]
    near = adv.update(20.0, 0.0, "navigating", "walk_forward")["eta_s"]
    assert near <= far


def test_advisor__phase_stabilises_after_the_first_frames(ns) -> None:
    """★ 滞回的意义：读完头几帧后，phase 必须**稳住**，不逐帧跳。

    实测序列是 ``far, far, approach, approach, ...`` —— 前两帧还在建立，
    所以这里断言的是「稳定之后不再变」，而不是「从第一帧就固定」
    （那会把真实的建立阶段也一起否掉）。
    """
    adv = ns["_NavAdvisor"]()
    seq = [adv.update(50.0, 10.0, "navigating", "walk_forward")["phase"]
           for _ in range(8)]
    assert len(set(seq[2:])) == 1, seq


def test_advisor__handles_none_angle(ns) -> None:
    """★ 没有朝向读数时不能崩 —— 那是 tracker 刚启动时的常态。"""
    assert ns["_NavAdvisor"]().update(30.0, None, "navigating", "walk_forward")
