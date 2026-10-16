"""路点文件 schema × **真实** ``e2e_pathfind`` 解析器（Console 侧，2026-10-12）。

★ 为什么要专门拿真脚本来验
  ``mapdata.WAYPOINTS_SCHEMA = 1`` 是 Console 自己抄的常量。两边对不上时，
  真脚本的 ``_load_waypoints_file`` 会**整份判废**（它的原话：「读错字段会让
  角色朝反方向走」）。而 Console 单测只能证明「Console 和自己一致」——
  证明不了「Console 和真脚本一致」。这条缝只能拿真源码补。

★ 为什么不 ``import`` 那个 208KB 的脚本
  它顶部就 ``import uipi_check`` 和 ``autopilot.actuator.simulator``，还得把
  recognition 的 scripts 目录塞进 ``sys.path`` —— Console 的 venv 里跑它会把
  跨项目依赖变成硬依赖，正是本项目反复要避开的东西。

  所以这里用 :mod:`ast` **把真源码里的那两个定义抽出来单独执行**：
  测的仍然是脚本的真实逻辑，且脚本一改，这个测试立刻跟着反映。
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

import ast
import json
import math
from pathlib import Path

import pytest

from neko_console.tabs import mapdata as M

#: 真脚本位置（跨项目，缺失就跳过而不是让 Console 依赖它存在）
REAL_SCRIPT = Path(RECOG_ROOT / 'exports' / 'benchmark' / 'scripts' / 'misc' / 'e2e_pathfind.py')

WANTED = ("WAYPOINTS_SCHEMA", "_load_waypoints_file")


def _real_namespace() -> dict:
    """把真脚本里的 ``_load_waypoints_file`` + ``WAYPOINTS_SCHEMA`` 抽出来执行。"""
    if not REAL_SCRIPT.is_file():
        pytest.skip(f"本机没有真脚本 {REAL_SCRIPT}")
    tree = ast.parse(REAL_SCRIPT.read_text(encoding="utf-8"))
    nodes: list = []
    found: set[str] = set()
    for n in tree.body:
        # FunctionDef 有 .name；Assign 没有，得看 .targets 里的 Name
        name = getattr(n, "name", None)
        if name is None and isinstance(n, ast.Assign):
            names = [t.id for t in n.targets if isinstance(t, ast.Name)]
            name = names[0] if len(names) == 1 else None
        if name in WANTED:
            nodes.append(n)
            found.add(name)
    missing = set(WANTED) - found
    assert not missing, f"真脚本里找不到 {missing}（它被改名/删掉了？）"
    ns: dict = {"json": json, "math": math, "Path": Path, "__name__": "e2e_stub"}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(REAL_SCRIPT), "exec"), ns)
    return ns


@pytest.fixture(scope="module")
def real() -> dict:
    return _real_namespace()


def _load(real, p: Path) -> dict:
    return real["_load_waypoints_file"](str(p))


# ==================================================================
# 常量对齐 —— 最高价值的一条
# ==================================================================

def test_our_schema_constant_matches_the_real_script(real) -> None:
    """★ Console 抄的 schema 版本必须与真脚本一致。

    对不上 = 每个路点文件都被判废 = WAYPOINTS 模式 100% 不可用，
    而且报错发生在点「启动」之后。
    """
    assert M.WAYPOINTS_SCHEMA == real["WAYPOINTS_SCHEMA"]


def test_real_script_still_exposes_the_loader(real) -> None:
    """函数名被改的话这里就红 —— 提醒同步抽取名单。"""
    assert callable(real["_load_waypoints_file"])


# ==================================================================
# 正例：我们写出来的文件，真脚本读得动
# ==================================================================

def test_file_we_write_is_accepted_by_the_real_parser(real, tmp_path) -> None:
    """★ 端到端：``write_waypoints_file`` -> 真 ``_load_waypoints_file``。"""
    req = M.NavigationRequest(
        start=(10.0, 20.0), target=(900.5, -300.25),
        mode=M.NavMode.WAYPOINTS,
        waypoints=[(100.0, 200.0), (300.0, 400.0), (500.0, 600.0)])
    p = M.write_waypoints_file(req, tmp_path / "wps.json")

    doc = _load(real, p)
    assert "error" not in doc, f"真脚本判废了我们的文件：{doc.get('error')}"


def test_parsed_values_round_trip_exactly(real, tmp_path) -> None:
    """路点坐标逐个对得上，且终点就是终点。"""
    req = M.NavigationRequest(
        start=(1.5, 2.5), target=(999.75, -888.25),
        mode=M.NavMode.WAYPOINTS,
        waypoints=[(11.25, 22.5), (33.0, 44.0)])
    p = M.write_waypoints_file(req, tmp_path / "wps.json")

    doc = _load(real, p)
    assert doc["waypoints"] == [
        {"x": 11.25, "z": 22.5, "note": ""},
        {"x": 33.0, "z": 44.0, "note": ""},
    ]
    assert doc["target"] == (999.75, -888.25)
    assert doc["start"] == (1.5, 2.5)


def test_integer_coordinates_are_written_as_numbers_not_strings(real, tmp_path) -> None:
    """``"1"`` 那种字符串坐标会被真脚本的 ``_pair`` 拒掉。"""
    req = M.NavigationRequest(start=(1, 2), target=(3, 4),     # 故意传 int
                              mode=M.NavMode.WAYPOINTS, waypoints=[(5, 6)])
    p = M.write_waypoints_file(req, tmp_path / "wps.json")
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert isinstance(raw["waypoints"][0]["x"], float)
    doc = _load(real, p)
    assert "error" not in doc, doc.get("error")


# ==================================================================
# 反例：证明上面那条对齐真的有约束力
# ==================================================================

def test_bumping_our_schema_would_break_the_real_parser(real, tmp_path) -> None:
    """★ 反向验证：版本号一改，真脚本立刻判废。

    换句话说，``test_our_schema_constant_matches_the_real_script`` 不是
    在比较两个恰好都写着 1 的数字 —— 它真的连着「能不能跑」。
    """
    good = M.to_waypoints_doc(M.NavigationRequest(
        start=(0.0, 0.0), target=(1.0, 1.0),
        mode=M.NavMode.WAYPOINTS, waypoints=[(0.5, 0.5)]))
    assert "error" not in _load(real, _write(tmp_path / "ok.json", good))

    bumped = dict(good, schema_version=good["schema_version"] + 1)
    bad = _load(real, _write(tmp_path / "bad.json", bumped))
    assert "error" in bad
    assert "schema_version" in bad["error"]


def test_missing_target_is_rejected_not_silently_guessed(real, tmp_path) -> None:
    """终点写坏了要整份判废 —— 「随便挑一个」比拒绝糟得多。"""
    doc = {"schema_version": M.WAYPOINTS_SCHEMA, "waypoints": [{"x": 1, "z": 2}]}
    got = _load(real, _write(tmp_path / "w.json", doc))
    # target 缺失 -> 从路点序列最后一个补齐（这是真脚本的 null 降级）
    assert "error" not in got
    assert got["target"] == (1.0, 2.0)


def test_broken_target_shape_is_rejected(real, tmp_path) -> None:
    """target 有值但不合规 -> 整份判废（与 null 降级是两回事）。"""
    doc = {"schema_version": M.WAYPOINTS_SCHEMA,
           "waypoints": [{"x": 1, "z": 2}], "target": [1]}
    got = _load(real, _write(tmp_path / "w.json", doc))
    assert "error" in got
    assert "target" in got["error"]


def _write(p: Path, doc: dict) -> Path:
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p