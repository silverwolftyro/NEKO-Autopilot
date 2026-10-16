"""传送点模糊搜索的单测（2026-10-16）。

覆盖任务书列的五类输入：**精确 / 单字错误 / 同音字 / 拼音 / 语音识别**
（漏字、多字、标点、数字），外加地区限定、空输入、完全无匹配。

★ 为什么大部分用**合成数据**而不是真实 936 条
  真实数据会随游戏更新变名、变数量，拿它写死断言，下一次数据更新就是一片
  红灯，而红灯往往和代码无关。合成数据里的点名字面可控，
  「这个 bug 是不是还在」才测得准。
  另有一组测试跑真实数据，但只断言**结构性**性质（不崩、有结果、分数递减），
  不钉具体名字 —— 那些交给 :mod:`test_mapdata` 之类的数据层测试管。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from neko_console.tabs.mapdata import TeleportPoint          # noqa: E402
from neko_console.tabs.tpsearch import (AMBIGUITY_TOP_SCORE,  # noqa: E402
                                        SCORE_FLOOR, Candidate,
                                        build_disambiguation_question,
                                        canonical_homophone, needs_disambiguation,
                                        normalize_query, search_teleport,
                                        to_initials, to_pinyin)


# =============================================================================
# 合成传送点表
# =============================================================================

def _pt(pid, name, country, areas, ptype="Goddess"):
    return TeleportPoint(
        id=pid, name=name, type=ptype, country=country,
        areas=tuple(areas), position=(1000.0, 0.0, 2000.0),
        tran_position=(1001.0, 0.0, 2001.0),
    )


POINTS = (
    _pt(1, "七天神像-风", "蒙德", ["苍风高地"]),
    _pt(2, "七天神像-风", "蒙德", ["风啸山坡", "风起地"]),
    _pt(3, "七天神像-岩", "璃月", ["碧水原", "荻花洲"]),
    _pt(4, "七天神像-水", "枫丹", ["白露区", "海露港"]),
    _pt(5, "传送锚点", "蒙德", ["坠星山谷", "千风神殿"], "TeleportWaypoint"),
    _pt(6, "传送锚点", "璃月", ["珉林", "绝云间"], "TeleportWaypoint"),
    _pt(7, "新月神像", "璃月", ["望舒 Yacht".split()[0], "荻花洲"]),
    _pt(8, "北风之狼的庙宇", "蒙德", ["坠星山谷", "千风神殿"], "OneTimeDomain"),
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication_stub()


def QApplication_stub():
    """本文件不碰 Qt —— :mod:`tpsearch` 是纯逻辑模块，没有窗口。

    留这个 fixture 只是为了让「万一以后有人加了 QWidget 测试」时
    有个统一的落点，且不会与其它文件的 app 实例打架。
    """
    return None


# =============================================================================
# 1 · 精确匹配
# =============================================================================

@pytest.mark.parametrize("query,expect_id", [
    ("七天神像-风", 1),
    ("七天神像-岩", 3),
    ("北风之狼的庙宇", 8),
    ("新月神像", 7),
])
def test_exact_match_returns_1_0(query, expect_id) -> None:
    r = search_teleport(query, points=POINTS)
    assert r, f"「{query}」应有结果"
    assert r[0].point.id == expect_id
    assert r[0].score == 1.0
    assert "exact" in r[0].reasons


def test_exact_match_with_missing_dash_is_still_exact() -> None:
    """语音识别常把横杠吞掉 —— 归一化后必须**完全相等**，不能掉分。"""
    assert normalize_query("七天神像-风") == normalize_query("七天神像风")
    r = search_teleport("七天神像风", points=POINTS)
    assert r[0].score == 1.0 and r[0].point.id == 1


# =============================================================================
# 2 · 单字错误 / 乱序 / 漏字
# =============================================================================

@pytest.mark.parametrize("query,expect_id,why", [
    ("七天神仙-风", 1, "错别字表已含「七天神仙」"),
    ("七天神相-风", 1, "像->相 同音错字"),
])
def test_single_typo_is_unique_top(query, expect_id, why) -> None:
    r = search_teleport(query, points=POINTS)
    assert r, f"「{query}」应有结果（{why}）"
    assert r[0].point.id == expect_id, (
        f"「{query}」top1 = #{r[0].point.id} {r[0].label}（{why}）")


@pytest.mark.parametrize("query", ["七天神像", "风像", "风神"])
def test_partial_query_contains_the_right_point(query) -> None:
    """★ 少字 / 漏字 / 字序颠倒 —— 必须出现在结果里。

    这里**不断言 top1 是谁**：「七天神像」同时命中三个神像，同分时按
    名字排序（岩 < 水 < 风），top1 是 #3 而不是 #1。
    那是**确定的、可预期的**行为，不是 bug；钉 top1 只会把排序规则
    写死成"永远返回岩"，反而挡住真正的修复。
    """
    r = search_teleport(query, points=POINTS)
    assert r, f"「{query}」应有结果"
    ids = {c.point.id for c in r}
    assert 1 in ids, f"「{query}」应包含 #1 七天神像-风，实得 {ids}"


def test_ties_are_broken_deterministically_by_name() -> None:
    """同分时按 name 升序 —— 排序规则本身要被钉住，否则 UI 每次跳。"""
    r = search_teleport("七天神像", points=POINTS, limit=10)
    tied = [c for c in r if c.score == r[0].score]
    names = [c.point.name for c in tied]
    assert names == sorted(names), f"同分未按名字排序：{names}"


# =============================================================================
# 3 · 同音字（语音识别）
# =============================================================================

@pytest.mark.parametrize("query,expect_id", [
    ("封神像-风", 1),      # 风 -> 封
    ("七天神像-枫", 1),    # 风 -> 枫
    ("七天神像-冰", 1),    # 冰/风 折叠组外，但"七天神像X"结构相同
])
def test_homophone_typo(query, expect_id) -> None:
    r = search_teleport(query, points=POINTS)
    assert r, f"「{query}」应有结果"
    assert r[0].point.id == expect_id, f"「{query}」top1 = {r[0].label}"


def test_canonical_homophone_folds_known_variants() -> None:
    """同音折叠表本身。"""
    assert canonical_homophone("封神像") == canonical_homophone("风神像")
    assert canonical_homophone("梦度") == canonical_homophone("蒙度")
    assert canonical_homophone("离月") == canonical_homophone("璃月")


# =============================================================================
# 4 · 拼音
# =============================================================================

@pytest.mark.parametrize("query,expect_id", [
    ("qtsxf", 1),                   # 七天神像风 首字母，唯一
    ("ly", 3),                      # 璃月
    ("md", 1),                      # 蒙德
    ("qitianshenxiangfeng", 1),     # 七天神像风 全拼，唯一
])
def test_pinyin_query_unique_top(query, expect_id) -> None:
    r = search_teleport(query, points=POINTS)
    assert r, f"拼音「{query}」应有结果"
    assert r[0].point.id == expect_id, (
        f"拼音「{query}」top1 = #{r[0].point.id} {r[0].label}")


def test_pinyin_table_covers_names_and_countries_only() -> None:
    """★ 已知范围边界：拼音表覆盖 name + country，**不覆盖 areas**。

    「风起地」是 areas，起/地都不在表里，于是拼音维度整体作废
    （而不是给出错误答案）。这是刻意的取舍：areas 的汉字集合有 657 个，
    全表手写不现实，写错几个字就会造出比"搜不到"更糟的错匹配。

    扩展方式很直接：往 :data:`_PINYIN` 里补字即可，
    :func:`_views` 的"覆盖不全就作废"契约会自动接手，不需要改逻辑。
    """
    from neko_console.tabs.tpsearch import _views
    assert _views("七天神像") != ("", "")     # name：覆盖
    assert _views("蒙德") != ("", "")         # country：覆盖
    assert _views("风起地") == ("", "")       # area：**不**覆盖（当前边界）


@pytest.mark.parametrize("query", ["qtsx", "qitianshenxiang"])
def test_pinyin_query_ambiguous_still_returns_all(query) -> None:
    """「七天神像」的拼音同时命中三个神像 —— 都得在，不能只给一个。"""
    r = search_teleport(query, points=POINTS, limit=10)
    ids = {c.point.id for c in r}
    assert {1, 3, 4} <= ids, f"拼音「{query}」漏掉了神像，实得 {ids}"
    assert all(c.point.name.startswith("七天神像") for c in r)


def test_short_initials_do_not_use_edit_distance() -> None:
    """★ 2 个字母的首字母不许用编辑距离。

    实测 ``风像`` 的首字母 "fx" 与 **枫丹** 的 "fd" 编辑距离 1，
    于是"风像"在 0.75 上匹配上了枫丹的神像 —— 纯噪声，
    而且分数还盖过了真正该答的 #1（0.672）。
    """
    r = search_teleport("风像", points=POINTS)
    assert r
    assert r[0].point.id == 1, f"「风像」top1 = #{r[0].point.id} {r[0].label}"


def test_pinyin_helpers() -> None:
    assert to_initials("七天神像") == "qtsx"
    assert to_pinyin("七天神像") == "qitianshenxiang"


def test_pinyin_view_disables_when_coverage_incomplete() -> None:
    """★ 表外字必须让**整条**拼音维度作废，不能"能算几个算几个"。

    初版是跳过表外字，结果 ``to_initials("特别温暖的地方")`` 与
    ``to_initials("zzzz不存在的地名")`` 都塌成 ``"dd"``，
    两句毫不相干的话被判成 0.95 的强命中。这比匹配不到严重得多。
    """
    from neko_console.tabs.tpsearch import _views
    # 「苎」不在表里；「特」也不在 -> 整条作废
    assert _views("苎麻") == ("", "")
    assert _views("七天神像") == ("qtsx", "qitianshenxiang")


# =============================================================================
# 5 · 语音识别特有形态
# =============================================================================

@pytest.mark.parametrize("raw,normalized", [
    ("七天神像 - 风", "七天神像风"),   # 多余空格
    ("七天神像—风", "七天神像风"),      # 破折号
    ("七天神像·风", "七天神像风"),      # 间隔号
    ("　七天神像风　", "七天神像风"),    # 全角空格
])
def test_speech_punctuation_normalized(raw, normalized) -> None:
    assert normalize_query(raw) == normalized


def test_chinese_numeral_only_after_di() -> None:
    """★ 中文数字**只在「第」后面**转。

    整表转换会把「七天神像」变成「7天神像」—— 936 条数据全被毁，
    而搜索框看不出任何异常。
    """
    assert normalize_query("第八层") == "第8层"
    assert normalize_query("七天神像-风") == "七天神像风"   # 七 不能被转
    assert normalize_query("七天神像") != "7天神像"


def test_speech_extra_char_still_matches() -> None:
    """多一个字（语音把「七天神像风」听成「七天神像风神像」）。"""
    r = search_teleport("七天神像风神像", points=POINTS)
    assert r and r[0].point.id == 1


# =============================================================================
# 6 · 地区感知
# =============================================================================

def test_region_query_boosts_matching_country() -> None:
    """「蒙德」应该只把蒙德的点排到前面。"""
    r = search_teleport("蒙德", points=POINTS)
    assert r
    assert all(c.point.country == "蒙德" for c in r[:2]), \
        f"地区限定失效：{[c.label for c in r[:3]]}"


def test_region_boost_is_applied() -> None:
    """地区加分字段必须真的非零（否则这条分支是死代码）。"""
    r = search_teleport("蒙德 七天神像", points=POINTS)
    assert r and r[0].region_boost > 0.0


def test_region_does_not_drop_other_countries_entirely() -> None:
    """限定地区不等于只剩一个国家 —— 用户可能想跨。"""
    r = search_teleport("璃月 七天神像", points=POINTS, limit=10)
    ids = {c.point.id for c in r}
    assert 3 in ids, "璃月的点必须在结果里"


# =============================================================================
# 7 · 空输入 / 无匹配
# =============================================================================

@pytest.mark.parametrize("query", ["", "   ", "　", None, "!!!", "---"])
def test_empty_or_punctuation_only_returns_nothing(query) -> None:
    assert search_teleport(query, points=POINTS) == []


@pytest.mark.parametrize("query", ["zzzzzzz", "abcdefghij", "完全无关的东西"])
def test_garbage_returns_nothing(query) -> None:
    assert search_teleport(query, points=POINTS) == []


def test_empty_points_returns_nothing() -> None:
    assert search_teleport("七天神像", points=()) == []


# =============================================================================
# 8 · 结果质量（结构性）
# =============================================================================

def test_scores_are_sorted_descending() -> None:
    r = search_teleport("七天神像", points=POINTS, limit=10)
    scores = [c.score for c in r]
    assert scores == sorted(scores, reverse=True)


def test_all_results_above_score_floor() -> None:
    r = search_teleport("七天神像", points=POINTS, limit=10)
    assert all(c.score >= SCORE_FLOOR for c in r)


def test_results_are_stable_across_calls() -> None:
    """同分结果的顺序必须稳定，否则用户每次看到的不一样。"""
    a = [c.point.id for c in search_teleport("七天神像", points=POINTS, limit=10)]
    b = [c.point.id for c in search_teleport("七天神像", points=POINTS, limit=10)]
    assert a == b


def test_limit_is_respected() -> None:
    r = search_teleport("传送锚点", points=POINTS, limit=1)
    assert len(r) <= 1


def test_label_includes_area_not_just_name() -> None:
    """★ 936 条里 777 条都叫「传送锚点」，只显示 name 等于没显示。"""
    r = search_teleport("千风神殿", points=POINTS)
    assert r
    assert "千风神殿" in r[0].label


# =============================================================================
# 9 · NEKO 消歧接口（B3）
# =============================================================================

def test_needs_disambiguation_on_close_scores() -> None:
    """两个七天神像-风 分数完全一样 -> 必须问。"""
    r = search_teleport("七天神像-风", points=POINTS)
    assert len(r) >= 2
    assert needs_disambiguation(r) is True


def test_no_ask_when_single_unambiguous_hit() -> None:
    r = search_teleport("北风之狼的庙宇", points=POINTS)
    assert r
    assert needs_disambiguation(r[:1]) is False


def test_no_ask_on_empty() -> None:
    assert needs_disambiguation([]) is False


def test_needs_disambiguation_on_weak_top_score() -> None:
    """top1 分数低于阈值也要问 —— 匹配得不好本身就是不确定。"""
    weak = [Candidate(point=POINTS[0], score=AMBIGUITY_TOP_SCORE - 0.05,
                      reasons=("similar",), matched="x")]
    assert needs_disambiguation(weak) is True


def test_question_mentions_each_candidate() -> None:
    r = search_teleport("七天神像-风", points=POINTS)
    q = build_disambiguation_question(r)
    assert "七天神像-风" in q
    assert "还是" in q


def test_question_for_single_candidate_is_a_confirmation() -> None:
    q = build_disambiguation_question(search_teleport("北风之狼的庙宇",
                                                      points=POINTS))
    assert q.endswith("吗？"), q


def test_question_on_empty_is_empty_string() -> None:
    assert build_disambiguation_question([]) == ""


# =============================================================================
# 10 · 跑真实数据（只断言结构性性质）
# =============================================================================

@pytest.fixture(scope="module")
def real_points():
    from neko_console.tabs.mapdata import load_teleport_points
    pts = load_teleport_points()
    if not pts:
        pytest.skip("真实传送点数据不可用")
    return pts


@pytest.mark.parametrize("query", [
    "七天神像", "qtsx", "蒙德", "璃月", "风起地", "枫丹",
    "封神像", "离月", "七天神像风", "", "七", "绝", "zzz",
])
def test_real_dataset_is_stable_and_sorted(query, real_points) -> None:
    """真实 936 条：只断言「不崩 / 有序 / 分数合法」，不钉具体名字。

    名字会随游戏更新变，那是数据的事，不该让这份测试跟着变红。
    """
    r = search_teleport(query, points=real_points, limit=10)
    scores = [c.score for c in r]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= s <= 1.0 for s in scores)


@pytest.mark.parametrize("query", [
    "七天神像", "qtsx", "蒙德", "璃月", "枫丹", "离月", "七天神像风",
])
def test_real_dataset_common_queries_return_something(query, real_points) -> None:
    """真实数据上，**常用**输入必须搜得到东西。

    刻意用一组确定的、用户真会打的词；像 ``zzz不存在`` 这种纯噪声
    单独归到 :func:`test_real_dataset_is_stable_and_sorted` 里只验"不崩"。
    """
    assert search_teleport(query, points=real_points, limit=5), \
        f"「{query}」在真实数据上应有结果"


def test_real_dataset_pinyin_never_raises(real_points) -> None:
    """拼音维度对表外字必须安静作废，而不是抛异常。"""
    for q in ("qqqq", "zzzz", "七", "七七七七七"):
        search_teleport(q, points=real_points, limit=3)
