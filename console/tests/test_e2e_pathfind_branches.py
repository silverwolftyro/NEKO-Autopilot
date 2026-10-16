"""``e2e_pathfind.py`` 纯逻辑**第二批**（Console 侧，2026-10-13）。

第一批（``test_e2e_pathfind_logic.py``，83 条）覆盖的是「每个函数的基本行为」。
这一批补的是**分支深度** —— 一个函数有多少条拒绝/边界路径，就有多少条测试。

★ 重点四处（按「错了会怎样」排）
  1. :func:`_load_waypoints_file` —— 路点文件校验。**错一条就是「读错字段
     让角色朝反方向走」**（脚本自己的原话）。
  2. :class:`_WaypointCursor` —— 路点队列。``pop_reached`` 与 ``pop_abandoned``
     必须分别记账：全是 abandon 的话路线静默走形都没人发现。
  3. :class:`_NavAdvisor` —— 相位滞回。跳变会让 overlay 每帧重渲染。
  4. :class:`_EscapeState` —— 脱困状态。等级算错会让它提前放弃或死磕。
"""

from __future__ import annotations

import json

import pytest

import e2e_sandbox as S

pytestmark = pytest.mark.skipif(
    not S.E2E_PATHFIND.is_file(),
    reason=f"本机没有被测脚本 {S.E2E_PATHFIND}")


@pytest.fixture(scope="module")
def ns() -> dict:
    return S.load()


def _write(tmp_path, name: str, obj) -> str:
    p = tmp_path / name
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj),
                 encoding="utf-8")
    return str(p)


def _wp(x, z, note=""):
    return {"x": x, "z": z, "note": note}


# ==================================================================
# 1 · _load_waypoints_file：全部拒绝分支
# ==================================================================

def test_wpfile__valid_minimal_document(ns, tmp_path) -> None:
    p = _write(tmp_path, "w.json", {
        "schema_version": 1,
        "start": [0.0, 0.0], "target": [10.0, 20.0],
        "waypoints": [_wp(1.0, 2.0), _wp(3.0, 4.0)]})
    doc = ns["_load_waypoints_file"](p)
    assert "error" not in doc, doc.get("error")
    assert doc["target"] == (10.0, 20.0)
    assert len(doc["waypoints"]) == 2
    assert doc["waypoints"][0] == {"x": 1.0, "z": 2.0, "note": ""}


def test_wpfile__missing_file_is_reported(ns, tmp_path) -> None:
    got = ns["_load_waypoints_file"](str(tmp_path / "根本没有.json"))
    assert "error" in got and "读不到" in got["error"]


def test_wpfile__malformed_json_is_reported(ns, tmp_path) -> None:
    got = ns["_load_waypoints_file"](_write(tmp_path, "w.json", "{不是 JSON"))
    assert "error" in got and "不是合法 JSON" in got["error"]


def test_wpfile__top_level_must_be_object(ns, tmp_path) -> None:
    got = ns["_load_waypoints_file"](_write(tmp_path, "w.json", [1, 2, 3]))
    assert "error" in got and "顶层不是对象" in got["error"]


def test_wpfile__schema_version_mismatch_is_rejected(ns, tmp_path) -> None:
    """★ 版本对不上就整份判废 —— 脚本的原话是「别靠尽力读」。"""
    got = ns["_load_waypoints_file"](_write(tmp_path, "w.json", {
        "schema_version": 99, "target": [1.0, 2.0],
        "waypoints": [_wp(1, 2)]}))
    assert "error" in got
    assert "schema_version" in got["error"]


def test_wpfile__missing_schema_version_is_rejected(ns, tmp_path) -> None:
    got = ns["_load_waypoints_file"](_write(tmp_path, "w.json", {
        "target": [1.0, 2.0], "waypoints": [_wp(1, 2)]}))
    assert "error" in got and "schema_version" in got["error"]


def test_wpfile__waypoint_item_must_be_object(ns, tmp_path) -> None:
    got = ns["_load_waypoints_file"](_write(tmp_path, "w.json", {
        "schema_version": 1, "target": [1.0, 2.0],
        "waypoints": [_wp(1, 2), "我不是对象"]}))
    assert "error" in got and "不是对象" in got["error"]


def test_wpfile__waypoint_coordinates_must_be_pair(ns, tmp_path) -> None:
    """★ ``x`` 只有一维 -> 整份判废。

    ⚠️ 错误信息是 ``_pair`` 抛出来的原文（``float() argument must be...``）
      而不是「两元素」—— 因为缺 ``z`` 时 ``w.get("z")`` 是 ``None``，
      ``float(None)`` 先抛了 TypeError。判据是**判废了**，不是那句措辞。
    """
    got = ns["_load_waypoints_file"](_write(tmp_path, "w.json", {
        "schema_version": 1, "target": [1.0, 2.0],
        "waypoints": [{"x": 1.0}]}))
    assert "error" in got
    assert "不合规" in got["error"]


def test_wpfile__non_finite_coordinates_are_rejected(ns, tmp_path) -> None:
    """★ NaN / inf 必须挡住 —— 它们能通过 float() 但会让寻路发疯。"""
    got = ns["_load_waypoints_file"](_write(
        tmp_path, "w.json",
        '{"schema_version":1,"target":[1.0,2.0],'
        '"waypoints":[{"x":NaN,"z":1.0}]}'))
    assert "error" in got and "有限数" in got["error"]


def test_wpfile__broken_start_degrades_to_none(ns, tmp_path) -> None:
    """★ ``start`` 坏了**只降级**（它是参考值，不是终点）。"""
    got = ns["_load_waypoints_file"](_write(tmp_path, "w.json", {
        "schema_version": 1, "start": ["坏", 1], "target": [1.0, 2.0],
        "waypoints": [_wp(3, 4)]}))
    assert "error" not in got, got.get("error")
    assert got["start"] is None


def test_wpfile__null_start_falls_back_to_first_waypoint(ns, tmp_path) -> None:
    """★ ``null`` = 没写（不是写坏了）-> 从路点序列首项补齐。"""
    got = ns["_load_waypoints_file"](_write(tmp_path, "w.json", {
        "schema_version": 1, "start": None, "target": [1.0, 2.0],
        "waypoints": [_wp(3, 4), _wp(5, 6)]}))
    assert got["start"] == (3.0, 4.0)


def test_wpfile__null_target_falls_back_to_last_waypoint(ns, tmp_path) -> None:
    got = ns["_load_waypoints_file"](_write(tmp_path, "w.json", {
        "schema_version": 1, "target": None,
        "waypoints": [_wp(3, 4), _wp(5, 6)]}))
    assert "error" not in got, got.get("error")
    assert got["target"] == (5.0, 6.0)


def test_wpfile__broken_target_rejects_the_whole_file(ns, tmp_path) -> None:
    """★★ 与 ``start`` 相反：``target`` 坏了 = **整份判废**。

    终点错会让角色朝反方向走；补一个默认值比报错糟得多。
    """
    got = ns["_load_waypoints_file"](_write(tmp_path, "w.json", {
        "schema_version": 1, "target": [1], "waypoints": [_wp(3, 4)]}))
    assert "error" in got and "target" in got["error"]


def test_wpfile__no_target_and_no_waypoints_is_unusable(ns, tmp_path) -> None:
    """★ 既没有终点也没有路点 —— 真的什么都不知道，整份判废。"""
    got = ns["_load_waypoints_file"](_write(tmp_path, "w.json", {
        "schema_version": 1, "target": None, "start": None, "waypoints": []}))
    assert "error" in got
    assert "无法确定终点" in got["error"]


def test_wpfile__missing_waypoints_key_is_fine(ns, tmp_path) -> None:
    """只有终点没有路点 = 直接奔终点，是合法的。"""
    got = ns["_load_waypoints_file"](_write(tmp_path, "w.json", {
        "schema_version": 1, "target": [7.0, 8.0]}))
    assert "error" not in got, got.get("error")
    assert got["waypoints"] == []
    assert got["target"] == (7.0, 8.0)


def test_wpfile__integers_are_coerced_to_float(ns, tmp_path) -> None:
    """配置里写整数也要能用 —— 用户手改 JSON 时很常见。"""
    got = ns["_load_waypoints_file"](_write(tmp_path, "w.json", {
        "schema_version": 1, "target": [3, 4], "waypoints": [_wp(1, 2)]}))
    assert got["target"] == (3.0, 4.0)
    assert got["waypoints"][0]["x"] == 1.0


def test_wpfile__note_is_preserved(ns, tmp_path) -> None:
    """``note`` 会显示在 overlay 上，丢了就少一条诊断信息。"""
    got = ns["_load_waypoints_file"](_write(tmp_path, "w.json", {
        "schema_version": 1, "target": [1.0, 2.0],
        "waypoints": [_wp(1, 2, "第二个弯")] }))
    assert got["waypoints"][0]["note"] == "第二个弯"


def test_wpfile__negative_coordinates_are_legal(ns, tmp_path) -> None:
    """★ 负坐标不是错 —— 原神世界有大片负数坐标。

    早先一批代码用 ``if not x`` 判「没填」，那会把 0 和负数一起误伤。
    """
    got = ns["_load_waypoints_file"](_write(tmp_path, "w.json", {
        "schema_version": 1, "target": [-406.24, -3834.78],
        "waypoints": [_wp(-1.5, -2.5)]}))
    assert "error" not in got, got.get("error")
    assert got["target"] == (-406.24, -3834.78)


# ==================================================================
# 2 · _WaypointCursor：边界情况
# ==================================================================

def _cursor(ns, pts, final=(99.0, 99.0)):
    return ns["_WaypointCursor"]([_wp(x, z) for x, z in pts], final)


def test_cur__exactly_at_threshold_counts_as_reached(ns) -> None:
    """★ 正好等于 ``WAYPOINT_REACH_M`` 算「到了」—— 边界含等号。"""
    reach = ns["WAYPOINT_REACH_M"]
    c = _cursor(ns, [(0, 0)])
    assert c.pop_reached(reach) is not None


def test_cur__just_outside_threshold_is_not_reached(ns) -> None:
    reach = ns["WAYPOINT_REACH_M"]
    c = _cursor(ns, [(0, 0)])
    assert c.pop_reached(reach + 0.01) is None


def test_cur__draining_to_empty_returns_to_final_target(ns) -> None:
    """★ 路点走完必须回到**最终目标**，不能停在队列尾。"""
    c = _cursor(ns, [(0, 0), (1, 1)], final=(50.0, 60.0))
    c.pop_reached(0.0)
    c.pop_reached(0.0)
    assert c.goal() == (50.0, 60.0)
    assert c.done


def test_cur__pop_on_empty_queue_returns_none(ns) -> None:
    c = _cursor(ns, [])
    assert c.pop_reached(0.0) is None
    assert c.pop_abandoned() is None


def test_cur__abandon_and_reach_are_counted_separately(ns) -> None:
    """★★ 两本账必须分开，而且**必须记得调第二步**。

    ⚠️ ``pop_abandoned()`` **只弹队列、不加计数** —— 计数由单独的
      ``_mark_abandoned()`` 负责（脚本注释写得很清楚：``_done`` 是
      「走完了几个」，放弃的不能混进去，否则进度串会显示成「已完成」）。

      调用方漏掉第二步的话，进度条分子会一直少算 —— 症状是
      「26 个路点，放弃 12 个，浮窗还显示 1/26」。这是个真陷阱，
      所以本测试把两步一起断言。
    """
    c = _cursor(ns, [(0, 0), (1, 1), (2, 2)])
    c.pop_reached(0.0)              # 走到了
    dropped = c.pop_abandoned()     # 够不着
    assert dropped is not None
    assert c.done == 1 and c.abandoned == 0, "pop 本身不加计数"
    c._mark_abandoned()             # ← 第二步
    assert c.done == 1 and c.abandoned == 1

    c.pop_reached(0.0)
    assert c.done == 2 and c.abandoned == 1


def test_cur__index_is_a_progress_string(ns) -> None:
    """★ ``index`` 返回 ``"2/3"`` 这样的**展示串**，不是计数器。

    分子 = 已处理（走到 + 放弃）+ 1；队列空时返回**空串**。
    拿它当 int 用会炸，所以这里把形状钉住。
    """
    c = _cursor(ns, [(0, 0), (1, 1), (2, 2)])
    assert c.index == "1/3"
    c.pop_reached(0.0)
    assert c.index == "2/3"
    c.pop_abandoned()
    c._mark_abandoned()
    assert c.index == "3/3"
    c.pop_reached(0.0)
    assert c.index == ""


def test_cur__total_reflects_the_original_count(ns) -> None:
    """★ ``total`` 要是**原始**数量，不能随 pop 变小 ——
    否则「3/3」这种进度显示会永远到不了终点。"""
    c = _cursor(ns, [(0, 0), (1, 1), (2, 2)])
    assert c.total == 3
    c.pop_reached(0.0)
    c.pop_reached(0.0)
    assert c.total == 3


def test_cur__goal_equals_first_remaining_waypoint(ns) -> None:
    c = _cursor(ns, [(5, 5), (6, 6), (7, 7)])
    assert c.goal() == (5.0, 5.0)
    c.pop_reached(0.0)
    assert c.goal() == (6.0, 6.0)


def test_cur__queue_is_copied_not_aliased(ns) -> None:
    """★ 外部改了原列表，不该影响游标。

    浅拷贝只复制 dict 容器、内部 ``w`` 还是同一个对象 —— 这里逐字段都测，
    因为「只复制一层」正是这类代码的典型疏漏。
    """
    src = [_wp(1.0, 2.0)]
    c = ns["_WaypointCursor"](src, (9, 9))
    src.append(_wp(3.0, 4.0))
    src[0]["x"] = 999.0
    assert c.total == 1
    assert c.goal() == (1.0, 2.0)


def test_cur__none_waypoints_behaves_like_empty(ns) -> None:
    c = ns["_WaypointCursor"](None, (9, 9))
    assert c.total == 0
    assert c.goal() == (9.0, 9.0)
    assert c.done == 0 and c.abandoned == 0
    assert c.index == ""


def test_cur__extra_keys_in_waypoint_are_ignored(ns) -> None:
    """路点里带自定义字段不该让它读不了。"""
    pts = [{"x": 1.0, "z": 2.0, "note": "n", "my_tag": "abc"}]
    c = ns["_WaypointCursor"](pts, (9, 9))
    assert c.goal() == (1.0, 2.0)


# ==================================================================
# 3 · _NavAdvisor：相位滞回
# ==================================================================

def test_adv__no_title_gets_no_suffix(ns) -> None:
    """★ 没传 wp_label 时 hint 不能多出空白尾巴。"""
    r = ns["_NavAdvisor"]().update(50.0, 10.0, "navigating", "walk_forward")
    assert r["hint"] == r["hint"].strip()
    assert "（）" not in r["hint"]


def test_adv__waypoint_label_appears_in_hint(ns) -> None:
    r = ns["_NavAdvisor"]().update(50.0, 10.0, "navigating", "walk_forward",
                                  wp_label="路点 2/5")
    assert "路点 2/5" in r["hint"]


def test_adv__force_bypasses_hysteresis(ns) -> None:
    """★ ``force=True`` 是「别等了，立刻切」—— 终态要立刻生效。"""
    adv = ns["_NavAdvisor"]()
    for _ in range(4):
        adv.update(50.0, 10.0, "navigating", "walk_forward")
    forced = adv.update(0.0, 0.0, "arrived", "stop", force=True)
    assert forced["phase"] == "arrived"


def test_adv__eta_is_zero_or_positive(ns) -> None:
    adv = ns["_NavAdvisor"]()
    for d in (0.0, 0.5, 3.0, 50.0, 1000.0):
        assert adv.update(d, 0.0, "navigating", "walk_forward")["eta_s"] >= 0


def test_adv__hint_is_never_empty(ns) -> None:
    """★ overlay 是「字符串直出，一个字都不加工」——
    空字符串会让面板上出现一个空的提示框。"""
    adv = ns["_NavAdvisor"]()
    for d, deg in ((0.0, 0.0), (5.0, 30.0), (50.0, 170.0), (1e6, None)):
        assert adv.update(d, deg, "navigating", "walk_forward")["hint"].strip()


def test_adv__unknown_state_does_not_crash(ns) -> None:
    """状态机被塞进表外的字符串时不能崩。"""
    assert ns["_NavAdvisor"]().update(10.0, 5.0, "WEIRD_STATE", "x")


def test_adv__non_numeric_distance_does_not_crash(ns) -> None:
    assert ns["_NavAdvisor"]().update(None, 0.0, "navigating", "walk_forward")


def test_adv__same_input_yields_same_record(ns) -> None:
    """★ 同样输入必须得到同样输出（overlay 按 content_key 判断要不要刷新）。"""
    a = ns["_NavAdvisor"]().update(30.0, 20.0, "navigating", "walk_forward")
    b = ns["_NavAdvisor"]().update(30.0, 20.0, "navigating", "walk_forward")
    assert a == b


# ==================================================================
# 4 · _EscapeState：脱困状态转移
# ==================================================================

def _esc(ns):
    return ns["_EscapeState"]


def test_escape_state__carries_all_fields(ns) -> None:
    """★ 六个字段都是被下游读的，少一个就有 display 拿不到值。"""
    e = _esc(ns)("t1", 250, 1000.0, 1, 2, "因为墙")
    assert e.name == "t1"
    assert e.hold == 250
    assert e.due == 1000.0
    assert e.stage == 1
    assert e.level == 2
    assert e.why == "因为墙"


def test_escape_state__repr_is_readable(ns) -> None:
    """repr 会进日志 —— 排障时至少要看到「卡在哪个阶段」。

    ⚠️ repr 里**没有** ``why``（只有 name / stage / 剩余时间）——
      所以「为什么卡住」得从别处（事件流 / L3 打印）拿，别指望这条。
    """
    e = _esc(ns)("t2", 250, 1000.0, 1, 2, "因为墙")
    text = repr(e)
    assert "t2" in text
    assert "1" in text          # stage


def test_escape_state__hold_may_be_none(ns) -> None:
    """★ ``hold=None`` 表示「这一档不按时长」—— 不能假定它有值。"""
    e = _esc(ns)("t3", None, 500.0, 0, 0, "")
    assert e.hold is None


def test_escape_state__slots_reject_extra_attributes(ns) -> None:
    """``__slots__`` 意味着拼错字段名会**立刻**抛，而不是悄悄存进去。"""
    e = _esc(ns)("t", 1, 1.0, 1, 1, "w")
    with pytest.raises(AttributeError):
        e.oops = 1                 # type: ignore[attr-defined]


# ==================================================================
# 5 · 常量契约
# ==================================================================

def test_contract__sitting_and_resume_bands_form_a_hysteresis(ns) -> None:
    """★ 进/出阈值必须留出**带宽**。

    进 60° 出 20°：若两者相等，噪声会让它在「站定/不站定」之间每帧翻转。
    """
    assert ns["TURN_STAND_DEG"] > ns["TURN_RESUME_DEG"]
    assert ns["TURN_RESUME_DEG"] >= 0


def test_contract__mouse_saturation_is_one_step(ns) -> None:
    """★ 超过一帧能动的像素量就该欠着，分给下一帧。"""
    assert ns["MOUSE_SATURATION_PX"] > 0


def test_contract__measure_samples_exceed_two(ns) -> None:
    """方向估计至少要 3 个样本，否则「转了多少」无从判断。"""
    assert ns["TURN_MEASURE_SAMPLES"] >= 3


def test_contract__waypoint_reach_is_positive(ns) -> None:
    assert ns["WAYPOINT_REACH_M"] > 0
