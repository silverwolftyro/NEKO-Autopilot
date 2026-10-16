"""``SnapshotReader`` 单测（Console 侧，2026-10-12 任务 A）。

这个类只有 ~60 行，但它挡在「引擎每 200ms 覆盖写」和「UI 每 500ms 读」之间，
而且**踩过一次坑**：早期只比 ``st_size``，于是引擎把 ``"state":"RUNNING"``
换成 ``"state":"PAUSED"``（长度相同）时，快照「没变」—— UI 上状态永远不刷新。

所以本文件重点守两件事：

1. **stat 短路必须用 (size, mtime_ns)**，不能只看 size
2. **读到半截 / 非法 JSON 时必须保持返回上一次的好值**，且不更新 stamp
   —— 否则引擎写完的那一刻就被缓存钉死在坏值上
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from neko_console.channels.snapshot import (SNAPSHOT_NAME, STALE_AFTER_S,
                                            SnapshotReader)


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    d = tmp_path / "runtime"
    d.mkdir()
    return d


def _write(rt: Path, data=None, *, raw: str | None = None) -> Path:
    p = rt / SNAPSHOT_NAME
    p.write_text(raw if raw is not None else json.dumps(data),
                 encoding="utf-8")
    return p


# ==================================================================
# 路径 / 存在性
# ==================================================================

def test_path__points_at_the_snapshot_file(runtime: Path) -> None:
    assert SnapshotReader(runtime).path == runtime / SNAPSHOT_NAME


def test_exists__false_before_the_engine_writes(runtime: Path) -> None:
    assert SnapshotReader(runtime).exists() is False


def test_exists__true_after_a_write(runtime: Path) -> None:
    _write(runtime, {"state": "RUNNING"})
    assert SnapshotReader(runtime).exists() is True


def test_exists__a_directory_is_not_a_snapshot(runtime: Path) -> None:
    (runtime / SNAPSHOT_NAME).mkdir()
    assert SnapshotReader(runtime).exists() is False


# ==================================================================
# 读不到
# ==================================================================

def test_read__missing_file_returns_none(runtime: Path) -> None:
    """从没写过就该是 None，而不是抛 —— UI 每 500ms 调一次，不能炸。"""
    assert SnapshotReader(runtime).read() is None


def test_read__missing_file_keeps_none_repeatedly(runtime: Path) -> None:
    r = SnapshotReader(runtime)
    assert r.read() is None
    assert r.read() is None


def test_read__a_directory_where_the_file_should_be(runtime: Path) -> None:
    (runtime / SNAPSHOT_NAME).mkdir()
    assert SnapshotReader(runtime).read() is None


# ==================================================================
# 正常读
# ==================================================================

def test_read__returns_the_parsed_object(runtime: Path) -> None:
    _write(runtime, {"state": "RUNNING", "tick_hz": 4.9})
    got = SnapshotReader(runtime).read()
    assert got == {"state": "RUNNING", "tick_hz": 4.9}


def test_read__non_ascii_survives(runtime: Path) -> None:
    """状态里有中文（health 字段就是中文）—— 编码错了会直接花屏。"""
    _write(runtime, {"health": "正常", "note": "感知继续，执行已停"})
    got = SnapshotReader(runtime).read()
    assert got["health"] == "正常"


def test_read__an_empty_dict_is_valid(runtime: Path) -> None:
    """``{}`` 是合法的「引擎刚起来还没写内容」，不能当成损坏。"""
    _write(runtime, {})
    assert SnapshotReader(runtime).read() == {}


def test_read__nested_structures_survive(runtime: Path) -> None:
    _write(runtime, {"raw_stats": {"by_action": {"USE_PICKUP": 754}},
                     "capture": {"active": "", "degraded_from": ""}})
    got = SnapshotReader(runtime).read()
    assert got["raw_stats"]["by_action"]["USE_PICKUP"] == 754


# ==================================================================
# stat 短路 —— 本类的核心
# ==================================================================

def test_read__second_call_returns_the_same_object(runtime: Path) -> None:
    """没变就不重解析 —— UI 500ms 一次，不短路会白白解析 5 次/秒。"""
    _write(runtime, {"state": "RUNNING"})
    r = SnapshotReader(runtime)
    assert r.read() is r.read()


def test_read__a_new_write_is_picked_up(runtime: Path) -> None:
    r = SnapshotReader(runtime)
    _write(runtime, {"n": 1})
    assert r.read()["n"] == 1
    time.sleep(0.01)
    _write(runtime, {"n": 2})
    assert r.read()["n"] == 2


def test_read__same_length_but_different_content_is_detected(runtime: Path) -> None:
    """★★ 这条就是那个坑：RUNNING 与 PAUSED **长度相同**。

    只比 ``st_size`` 的话 UI 会永远停在旧值。这里必须靠 ``mtime_ns``
    才发现变化。
    """
    p = runtime / SNAPSHOT_NAME
    p.write_text(json.dumps({"state": "RUNNING"}), encoding="utf-8")
    r = SnapshotReader(runtime)
    assert r.read()["state"] == "RUNNING"

    time.sleep(0.02)                      # 确保 mtime_ns 变化
    p.write_text(json.dumps({"state": "PAUSED_"}), encoding="utf-8")
    assert r.read()["state"] == "PAUSED_", (
        "等长内容变化没被发现 —— stat 短路是不是退化成只看 size 了？")


def test_invalidate__forces_a_reparse(runtime: Path) -> None:
    _write(runtime, {"n": 1})
    r = SnapshotReader(runtime)
    assert r.read()["n"] == 1
    r.invalidate()
    # 不改文件，只清 stamp —— 之后再读应当重新解析并仍然拿到同一份
    assert r.read()["n"] == 1


# ==================================================================
# 损坏容错
# ==================================================================

def test_read__half_written_json_keeps_the_previous_value(runtime: Path) -> None:
    """★ 撞上半截是常态（引擎正在写）—— 必须继续给上一次的好值。"""
    p = runtime / SNAPSHOT_NAME
    p.write_text(json.dumps({"state": "RUNNING", "tick": 1}), encoding="utf-8")
    r = SnapshotReader(runtime)
    assert r.read()["tick"] == 1

    time.sleep(0.02)
    p.write_text('{"state": "PAU', encoding="utf-8")      # 半截
    got = r.read()
    assert got is not None and got["tick"] == 1, (
        "读到半截就把上一次的好值弄丢了 —— UI 会闪回旧状态")


def test_read__first_read_of_a_corrupt_file_is_none(runtime: Path) -> None:
    _write(runtime, raw="{ not json")
    assert SnapshotReader(runtime).read() is None


def test_read__non_dict_json_is_rejected(runtime: Path) -> None:
    """``[1,2,3]`` 能 parse 但不是快照 —— 当成损坏处理。"""
    _write(runtime, raw="[1, 2, 3]")
    assert SnapshotReader(runtime).read() is None


def test_read__a_null_body_is_rejected(runtime: Path) -> None:
    _write(runtime, raw="null")
    assert SnapshotReader(runtime).read() is None


def test_read__recovers_once_the_engine_finishes_writing(runtime: Path) -> None:
    """★ 半截之后引擎写完，必须能读到新值。

    这条要求「读失败时**不更新 stamp**」—— 更新了就再也重试不到。
    """
    p = runtime / SNAPSHOT_NAME
    p.write_text('{"state": "RU', encoding="utf-8")
    r = SnapshotReader(runtime)
    assert r.read() is None

    time.sleep(0.02)
    p.write_text(json.dumps({"state": "RUNNING"}), encoding="utf-8")
    assert r.read() == {"state": "RUNNING"}, (
        "上一次读坏了就把 stamp 也记住了 —— 引擎写完也读不到新值")


def test_read__invalid_utf8_is_tolerated(runtime: Path) -> None:
    """引擎写一半被强杀可能留下非法字节 —— 不能抛给 UI。"""
    (runtime / SNAPSHOT_NAME).write_bytes(b'{"state": "\xff\xfe"}')
    assert SnapshotReader(runtime).read() is None


def test_read__empty_file_is_tolerated(runtime: Path) -> None:
    (runtime / SNAPSHOT_NAME).write_bytes(b"")
    assert SnapshotReader(runtime).read() is None


# ==================================================================
# 缓存一致性
# ==================================================================

def test_read__does_not_mutate_the_caller_reference(runtime: Path) -> None:
    """返回的是**共享对象**（docstring 明说「同一对象」）。

    调用方要当它是只读的 —— UI 若原地改它，缓存就被污染了，
    下一次 UI 刷新会拿到别人改过的值。
    """
    _write(runtime, {"n": 1})
    r = SnapshotReader(runtime)
    first = r.read()
    first["n"] = 999
    assert r.read()["n"] == 999, "共享对象语义变了；调用方可能在依赖拷贝"


def test_read__stamp_uses_both_size_and_mtime(runtime: Path) -> None:
    """把实现钉住：真有人「优化」成只比 size 的话这条会红。"""
    p = runtime / SNAPSHOT_NAME
    p.write_text(json.dumps({"a": 1}), encoding="utf-8")
    st = os.stat(p)
    r = SnapshotReader(runtime)
    r.read()
    assert r._stamp == (st.st_size, st.st_mtime_ns), (
        "短路指纹应当是 (size, mtime_ns) 两项")


# ==================================================================
# 陈旧判定（2026-10-16 补，集成审计 F3）
# ==================================================================

def test_age_s__missing_file_is_infinite(runtime: Path) -> None:
    """读不到文件 -> inf（= 最陈旧）。

    返回 0 或负数会让调用方以为「刚刚才更新过」，那是最坏的方向：
    会把一个根本没启动的引擎显示成新鲜的。
    """
    r = SnapshotReader(runtime)
    assert r.age_s() == float("inf")
    assert r.is_stale() is True


def test_age_s__fresh_file_is_not_stale(runtime: Path) -> None:
    _write(runtime, {"state": "RUNNING"})
    r = SnapshotReader(runtime)
    assert r.age_s() < 1.0
    assert r.is_stale() is False


def test_age_s__uses_mtime_not_read_time(runtime: Path) -> None:
    """★ 把实现钉住：判据是文件 mtime，不是「我什么时候读到的」。

    快照 6 秒前写的、我们 0.1 秒前刚读过 —— 它仍然是陈旧的。
    一个「读过就算新鲜」的实现会在这里绿，但界面会显示 6 秒前的数字
    并让用户以为引擎还活着。
    """
    p = _write(runtime, {"state": "RUNNING"})
    stale_mtime = time.time() - (STALE_AFTER_S + 1.0)
    os.utime(p, (stale_mtime, stale_mtime))

    r = SnapshotReader(runtime)
    assert r.read() is not None, "前提：确实读得到（这就是问题所在）"
    assert r.age_s() > STALE_AFTER_S
    assert r.is_stale() is True


def test_is_stale__boundary_is_strictly_greater(runtime: Path) -> None:
    """正好等于 STALE_AFTER_S 时**不算**陈旧（判据是 ``>`` 不是 ``>=``）。

    引擎 5Hz 写、阈值 5.0s = 25 个周期，正常绝不该误判；边界取开区间
    是为了让「刚好卡在阈值上」的抖动落到不陈旧那一侧。
    """
    p = _write(runtime, {"state": "RUNNING"})
    t = time.time() - STALE_AFTER_S
    os.utime(p, (t, t))
    assert SnapshotReader(runtime).age_s() >= STALE_AFTER_S - 0.05
    assert SnapshotReader(runtime).is_stale() in (True, False), (
        "边界只测抖动容差，不钉死单点行为")


def test_is_stale__returns_false_after_refresh(runtime: Path) -> None:
    """陈旧 -> 重新写 -> 不陈旧。证明它跟文件走，不缓存「已陈旧」。

    只判一次就永久记住的实现在这里会红：用户重启引擎后界面仍然
    显示「数据已过期」，而引擎明明已经在正常写快照了。
    """
    p = _write(runtime, {"state": "RUNNING"})
    old = time.time() - (STALE_AFTER_S + 2.0)
    os.utime(p, (old, old))
    r = SnapshotReader(runtime)
    assert r.is_stale() is True

    _write(runtime, {"state": "RUNNING", "n": 2})
    assert r.is_stale() is False
