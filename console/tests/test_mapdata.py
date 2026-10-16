"""``tabs/mapdata.py`` 的单测（Console 侧，2026-10-12）。

全是纯逻辑 + 本机真实数据，**不依赖 GUI、不依赖原神**。

最要紧的两条：
1. **坐标往返必须无损** —— 抄一份仿射系数过来，抄错就是「点哪都走错地方」，
   而且很难在界面上看出来。
2. **解析不出来时必须返回 None / unresolved，绝不猜** —— 猜错坐标 = 角色
   一路往反方向走，比报错糟糕得多。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from neko_console.tabs import mapdata as M

#: 真实传送点数据集（936 条）**不随发布包分发**：它是 BetterGI 的
#: ``tp.json`` 逐字节拷贝，GPL-3.0（见 ``docs/GPL_BOUNDARY.md``）。
#: 下面凡是直接断言"搜得到 / 有几条 / 坐标对得上"的用例都依赖它。
#:
#: ★ 用 skip 而不是改断言
#:   缺可选资产不是代码回归。混进失败里会让人以为传送点功能坏了，
#:   而实际只是这台机器没自备数据 —— 自备后这些用例自动恢复。
_HAS_TP = bool(M.load_teleport_points())
needs_real_points = pytest.mark.skipif(not _HAS_TP, reason='真实传送点数据不可用 —— BetterGI tp.json 衍生数据，GPL-3.0，不随包分发；自备指引见 docs/ASSETS.md')


# ==================================================================
# 坐标换算
# ==================================================================

@pytest.mark.parametrize("wx,wz", [
    (4634.52, 4687.13), (0.0, 0.0), (-406.24, -3834.78),
    (1859.34, -575.60), (9000.0, 9000.0),
])
def test_coord__round_trip_is_lossless(wx, wz) -> None:
    """世界 -> 像素 -> 世界，误差必须远小于 1 米。"""
    px, py = M.world_to_pixel(wx, wz)
    bx, bz = M.pixel_to_world(px, py)
    assert bx == pytest.approx(wx, abs=1e-3)
    assert bz == pytest.approx(wz, abs=1e-3)


def test_coord__thumb_round_trip_is_lossless() -> None:
    tx, ty = M.world_to_thumb(4634.52, 4687.13)
    wx, wz = M.thumb_to_world(tx, ty)
    assert wx == pytest.approx(4634.52, abs=0.05)
    assert wz == pytest.approx(4687.13, abs=0.05)


def test_coord__thumb_and_full_use_the_same_scale() -> None:
    """两图比例必须一致，否则点选会整体偏移。"""
    assert M.THUMB_TO_FULL == pytest.approx(36864 / 2000)
    assert M.THUMB_TO_FULL == pytest.approx(18432 / 1000)
    assert M.IMAGE_WIDTH / M.IMAGE_HEIGHT == pytest.approx(
        M.THUMB_WIDTH / M.THUMB_HEIGHT)


def test_coord__known_point_lands_inside_the_image() -> None:
    """蒙德城七天神像-风 应当落在底图范围内。"""
    px, py = M.world_to_pixel(1859.34, -575.60)
    assert M.is_inside(px, py), f"({px:.0f}, {py:.0f}) 跑出底图了"


@pytest.mark.parametrize("px,py,inside", [
    (0, 0, True), (36863, 18431, True), (-1, 0, False),
    (36864, 0, False), (0, 18432, False), (5000, -3, False),
])
def test_coord__is_inside_boundaries(px, py, inside) -> None:
    assert M.is_inside(px, py) is inside


def test_coord__copied_affine_matches_the_reference_module() -> None:
    """★ 我们抄了 recognition 那边的一份系数 —— 两边必须一致。

    不一致就是「只改了一边」，而症状是「点哪都走错地方」，界面上完全看不出来。
    源模块不在本机时跳过（不能让跨项目可选检查变成 Console 的启动依赖）。
    """
    r = M.verify_against_reference()
    if not r.get("checked"):
        pytest.skip(f"源模块不在：{r.get('reason')}")
    assert r["diffs"] == [], f"仿射系数和源模块不一致：{r['diffs']}"


# ==================================================================
# 名字归一化
# ==================================================================

@pytest.mark.parametrize("a,b", [
    ("七天神像 - 风", "七天神像-风"),
    ("七天神像－风", "七天神像-风"),          # 全角减号
    ("七天神像·风", "七天神像-风"),
    ("  七天神像 风  ", "七天神像-风"),
])
def test_norm__unifies_separators(a, b) -> None:
    assert M.normalize_name(a) == M.normalize_name(b)


def test_norm__fixes_known_typos() -> None:
    """和引擎那边同款的两条错别字修正。

    ⚠️ 注意规则是「替换整个子串」，所以 ``七天神仙像``（带像）会变成
    ``七天神像像`` —— 引擎那边就是这样的怪行为，这里保持一致，
    免得 Console 和引擎对同一个名字给出不同匹配结果。
    """
    assert M.normalize_name("七天神仙-风") == M.normalize_name("七天神像-风")
    assert M.normalize_name("安绕") == M.normalize_name("安饶")


def test_norm__empty_input_is_safe() -> None:
    assert M.normalize_name("") == ""
    assert M.normalize_name(None) == ""


# ==================================================================
# 传送点数据
# ==================================================================

@needs_real_points
def test_points__load_from_real_data() -> None:
    pts = M.load_teleport_points()
    assert len(pts) > 100, "真实数据有 936 条，量级差太多说明读错了"


def test_points__missing_file_degrades_to_empty_not_exception() -> None:
    """★ 读不到必须降级，不能抛 —— 否则整个寻路 tab 起不来。"""
    assert M.load_teleport_points(r"D:\不存在的路径\points.json") == ()


@needs_real_points
def test_points__each_point_exposes_flat_xz() -> None:
    p = M.load_teleport_points()[0]
    assert len(p.xz) == 2
    assert p.xz == (p.position[0], p.position[2])


@needs_real_points
def test_points__tran_position_is_almost_always_different() -> None:
    """落点（tranPosition）应当和图标位置（position）不同。

    ★ 这里**不要求 100%**：实测 936 条里有 1 条（``隐居处`` / id=2119 /
      type=Other）两者完全相同。那是**游戏数据本身如此**，不是我们的锅 ——
      对那类点，落点和图标重合本来就是对的。
      阈值放在 99%，是为了抓住「取错了字段」这种系统性错误，
      而不是被个例卡住。
    """
    same = [p for p in M.load_teleport_points() if p.xz == p.tran_xz]
    assert len(same) <= len(M.load_teleport_points()) * 0.01, (
        f"{len(same)} 个点落点等于图标位置 —— 像取错了字段")


@needs_real_points
def test_points__type_has_a_chinese_label() -> None:
    for p in M.load_teleport_points():
        assert p.type_cn and p.type_cn != p.type or p.type in M.POINT_TYPE_CN


@needs_real_points
def test_search__finds_by_area_name() -> None:
    hits = M.search_points("风起地")
    assert hits, "「风起地」应当能搜到七天神像-风"
    assert any("风起地" in p.areas for p in hits)


@needs_real_points
def test_search__finds_by_country() -> None:
    assert len(M.search_points("蒙德")) > 10


def test_search__empty_query_returns_empty() -> None:
    assert M.search_points("") == []
    assert M.search_points("   ") == []


def test_search__garbage_returns_empty_not_everything() -> None:
    assert M.search_points("zzzz不存在的地名zzzz") == []


@needs_real_points
def test_search__is_capped() -> None:
    assert len(M.search_points("锚点", limit=5)) <= 5


# ==================================================================
# 坐标文本解析
# ==================================================================

@pytest.mark.parametrize("text,xz", [
    ("4634.52, 4687.13", (4634.52, 4687.13)),
    ("4634.52,4687.13", (4634.52, 4687.13)),
    ("4634.52，4687.13", (4634.52, 4687.13)),      # 全角逗号
    (" 4634.52 , 4687.13 ", (4634.52, 4687.13)),
    ("-1.5,2", (-1.5, 2.0)),
])
def test_parse_xz__accepts_real_formats(text, xz) -> None:
    assert M.parse_xz(text) == pytest.approx(xz)


@pytest.mark.parametrize("text", ["", "4634", "a,b", "1,2,3", "abc, def"])
def test_parse_xz__rejects_garbage_with_none(text) -> None:
    """★ 解析不了就 None —— 绝不返回 (0,0)，那会让角色往原点跑。"""
    assert M.parse_xz(text) is None


def test_fmt_xz__round_trips_through_parse() -> None:
    assert M.parse_xz(M.fmt_xz(4634.5, -575.6)) == pytest.approx((4634.5, -575.6))


# ==================================================================
# 任务 B · 起点来源
# ==================================================================

def test_start_source__only_two_are_available() -> None:
    """★ 「预留」的两个必须明确不可用 —— 否则用户选了却拿不到坐标。"""
    avail = {s.name for s in M.StartSource if s.available}
    assert avail == {"MANUAL", "ANCHOR"}


def test_start_source__unavailable_ones_explain_why() -> None:
    for s in M.StartSource:
        if not s.available:
            assert s.unavailable_reason, f"{s.name} 不可用但没写原因"


def test_start_source__localization_says_it_needs_a_teleport() -> None:
    """用户最需要知道的是「先传送一次」，不是「不可用」。"""
    assert "传送" in M.StartSource.LOCALIZATION.unavailable_reason


def test_get_start__manual_parses_text() -> None:
    req = M.StartRequest(source=M.StartSource.MANUAL, manual="4634.52, 4687.13")
    assert M.get_start_position(req) == pytest.approx((4634.52, 4687.13))


def test_get_start__manual_garbage_gives_none_not_zero() -> None:
    """★ 绝不能返回 (0,0) —— 那会让角色朝世界原点走。"""
    req = M.StartRequest(source=M.StartSource.MANUAL, manual="乱填")
    assert M.get_start_position(req) is None


@needs_real_points
def test_get_start__anchor_uses_the_anchor() -> None:
    p = M.load_teleport_points()[0]
    req = M.StartRequest(source=M.StartSource.ANCHOR, anchor=p)
    assert M.get_start_position(req) == pytest.approx(p.xz)


def test_get_start__anchor_without_selection_gives_none() -> None:
    assert M.get_start_position(
        M.StartRequest(source=M.StartSource.ANCHOR, anchor=None)) is None


@pytest.mark.parametrize("src", [M.StartSource.SCREENSHOT_OCR,
                                M.StartSource.LOCALIZATION])
def test_get_start__reserved_sources_return_none(src) -> None:
    """预留分支当前恒为 None，但**必须是 None 而不是抛异常或假坐标**。"""
    req = M.StartRequest(source=src, ocr_hint="1,2", localization_hint="x")
    assert M.get_start_position(req) is None


# ==================================================================
# 任务 C · 目标描述
# ==================================================================

def test_target__coordinate_input_is_parsed() -> None:
    t = M.resolve_target_from_description("4634.52, 4687.13")
    assert t.ok and t.kind == "point"
    assert (t.x, t.z) == pytest.approx((4634.52, 4687.13))


@needs_real_points
def test_target__exact_unique_name_resolves_to_the_anchor() -> None:
    t = M.resolve_target_from_description("北风之狼的庙宇")
    assert t.ok and t.kind == "anchor" and t.anchor is not None
    assert t.anchor.name == "北风之狼的庙宇"


@needs_real_points
def test_target__ambiguous_name_is_not_guessed() -> None:
    """★ 「七天神像-风」有 6 个同名 —— 必须让用户选，不能随便挑一个。"""
    t = M.resolve_target_from_description("七天神像-风")
    assert t.ok is False
    assert t.kind == "unresolved"
    assert "同名" in t.entity_type


@needs_real_points
def test_target__many_candidates_is_reported_as_candidates() -> None:
    t = M.resolve_target_from_description("七天神像")
    assert t.ok is False
    assert "候选" in t.entity_type


@pytest.mark.parametrize("q", ["", "   ", "瞎写的", "zzzz不存在zzzz"])
def test_target__garbage_never_raises(q) -> None:
    t = M.resolve_target_from_description(q)
    assert t.ok is False and t.kind == "unresolved"


def test_target__never_falls_back_to_zero() -> None:
    """★ 解析不出来时绝不能给出 (0,0) 这种「看起来有效」的答案。"""
    for q in ("", "瞎写的"):
        t = M.resolve_target_from_description(q)
        assert not (t.x == 0.0 and t.z == 0.0)


@needs_real_points
def test_target__label_is_human_readable() -> None:
    assert "北风之狼的庙宇" in M.resolve_target_from_description(
        "北风之狼的庙宇").label
    assert M.resolve_target_from_description("").label == "（没解析出来）"


# ==================================================================
# 任务 D · 导航接口
# ==================================================================

def test_navmode__only_auto_is_reserved() -> None:
    assert {m.name for m in M.NavMode if m.available} == {"DIRECT", "WAYPOINTS"}
    assert not M.NavMode.AUTO.available
    assert "地形" in M.NavMode.AUTO.unavailable_reason


def test_nav_args__direct_shape() -> None:
    req = M.NavigationRequest(start=(1.0, 2.0), target=(3.0, 4.0),
                              mode=M.NavMode.DIRECT, live=True, delay_s=5.0)
    args = M.nav_to_cli_args(req)
    assert args[args.index("--start-world") + 1] == "1.00,2.00"
    assert args[args.index("--target-world") + 1] == "3.00,4.00"
    assert "--live" in args
    assert args[args.index("--delay") + 1] == "5.0"


def test_nav_args__omits_live_and_delay_when_zero() -> None:
    req = M.NavigationRequest(start=(0.0, 0.0), target=(1.0, 1.0),
                              live=False, delay_s=0.0)
    args = M.nav_to_cli_args(req)
    assert "--live" not in args
    assert "--delay" not in args


def test_nav_args__waypoints_use_a_file_not_per_point_flags(tmp_path) -> None:
    """WAYPOINTS 必须走 ``--waypoints-file``。

    ★ 曾经的 bug：拼 ``--waypoint X,Z``，而 e2e_pathfind 的 ``_SPEC`` 里
      没有这个 flag，解析循环对未知参数直接 ``continue`` —— flag 被静默
      丢弃，后面的坐标值还被当成窗口标题吞掉。于是角色**直线冲向终点**，
      而 UI 上一切正常。所以这里钉死「只能出文件」。
    """
    req = M.NavigationRequest(start=(0.0, 0.0), target=(9.0, 9.0),
                              mode=M.NavMode.WAYPOINTS,
                              waypoints=[(1.0, 1.0), (2.0, 2.0)])
    args = M.nav_to_cli_args(req, runtime_dir=tmp_path)
    assert "--waypoint" not in args
    assert "--waypoints-file" in args
    # ★ 与 --target-world 互斥（脚本 exit 2）：路点文件自带终点
    assert "--target-world" not in args
    assert args[args.index("--start-world") + 1] == "0.00,0.00"

    doc = json.loads(Path(args[args.index("--waypoints-file") + 1])
                     .read_text(encoding="utf-8"))
    assert doc["schema_version"] == M.WAYPOINTS_SCHEMA
    assert [w["x"] for w in doc["waypoints"]] == [1.0, 2.0]
    assert [w["z"] for w in doc["waypoints"]] == [1.0, 2.0]
    assert doc["target"] == [9.0, 9.0]


def test_nav_args__waypoints_refuses_without_runtime_dir() -> None:
    """没有落地路径就**拒绝**，不返回一个必然失效的参数表。"""
    req = M.NavigationRequest(start=(0.0, 0.0), target=(9.0, 9.0),
                              mode=M.NavMode.WAYPOINTS, waypoints=[(1.0, 1.0)])
    with pytest.raises(ValueError, match="runtime_dir"):
        M.nav_to_cli_args(req)


def test_nav_args__waypoints_refuses_when_empty() -> None:
    """一个路点都没有 = 静默退化成直线。必须报错。"""
    req = M.NavigationRequest(start=(0.0, 0.0), target=(9.0, 9.0),
                              mode=M.NavMode.WAYPOINTS, waypoints=[])
    with pytest.raises(ValueError, match="路点"):
        M.nav_to_cli_args(req, runtime_dir="/tmp/whatever")


def test_nav_args__auto_mode_is_rejected() -> None:
    req = M.NavigationRequest(start=(0.0, 0.0), target=(9.0, 9.0),
                              mode=M.NavMode.AUTO)
    with pytest.raises(ValueError, match="地形"):
        M.nav_to_cli_args(req, runtime_dir="/tmp/whatever")


def test_nav_args__direct_ignores_waypoints(tmp_path) -> None:
    req = M.NavigationRequest(start=(0.0, 0.0), target=(1.0, 1.0),
                              mode=M.NavMode.DIRECT, waypoints=[(5.0, 5.0)])
    args = M.nav_to_cli_args(req, runtime_dir=tmp_path)
    assert "--waypoint" not in args
    assert "--waypoints-file" not in args
    assert not list(tmp_path.iterdir())      # DIRECT 不该留下路点文件


def test_nav_handle__describes_itself() -> None:
    h = M.NavigationHandle()
    assert h.describe() == "未运行"
    h.running, h.pid = True, 4321
    assert "4321" in h.describe()


def test_nav_request__defaults_are_conservative() -> None:
    req = M.NavigationRequest(start=(0.0, 0.0), target=(1.0, 1.0))
    assert req.live is True and req.delay_s > 0
    assert req.waypoints == []


# ==================================================================
# 底图常量
# ==================================================================

def test_map_constants__match_the_anchor_file() -> None:
    """原图尺寸要跟 anchors.json 一致 —— 差一像素全局偏移。"""
    import json
    if not M.MAP_ANCHORS.is_file():
        pytest.skip("anchors.json 不在本机")
    doc = json.loads(M.MAP_ANCHORS.read_text(encoding="utf-8"))
    w, h = doc["image_size"]
    assert (M.IMAGE_WIDTH, M.IMAGE_HEIGHT) == (w, h)
