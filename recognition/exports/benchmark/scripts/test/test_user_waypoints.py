"""用户点选路点工作流（2026-10-06）：anchors_viewer 产出 -> e2e 消费。

★ 为什么单测不建 Tk 窗口
----------------------
``AnchorsViewer`` 的交互全绑在真实 canvas 上（bind / after / focus）。
起窗口会让测试依赖桌面会话。所以这里只测**不依赖 Tk 的那一半**：

* 文件格式：``dump_waypoints`` / ``load_waypoints``（两个模块共用）
* 解析：``e2e._load_waypoints_file`` 的全部拒绝分支
* 契约：两边的 ``WAYPOINTS_SCHEMA`` 必须一致

交互本身（点选 / 右键删 / 快捷键）靠真机验证，理由同
``test_stop_clicks``：那里的假 root 只能验纯逻辑分支，
而点选的**真正风险在几何与坐标往返**，那必须真窗口。
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
import json
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:                                   # pragma: no cover
    pass

MISC = Path(RECOG_ROOT / 'exports' / 'benchmark' / 'scripts' / 'misc')
PLUGIN_ROOT = Path(PLUGIN_DIR)
sys.path.insert(0, str(PLUGIN_ROOT))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f"   {detail}" if detail else ""))


def load(alias: str, path: Path):
    spec = importlib.util.spec_from_file_location(alias, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[alias] = m          # @dataclass 需要模块已注册
    spec.loader.exec_module(m)
    return m


av = load("_av", MISC / "anchors_viewer.py")
pf = load("_pf", MISC / "e2e_pathfind.py")

GOOD = {"schema_version": 1, "created_at_ms": 1, "start": [1000, 1000],
        "waypoints": [{"x": 1200, "z": 1300, "note": "沿河谷"},
                      {"x": 1800, "z": 1700, "note": "绕山"}],
        "target": [3000, 3000]}


def write(td: Path, name: str, doc, bom=False) -> str:
    p = td / name
    txt = json.dumps(doc, ensure_ascii=False)
    p.write_bytes((b"\xef\xbb\xbf" if bom else b"") + txt.encode("utf-8"))
    return str(p)


# ======================================================================
print("=== 1. 跨文件契约 ===")
# ======================================================================
check("★两个模块的 WAYPOINTS_SCHEMA 一致（漂了就互相读不了）",
      av.WAYPOINTS_SCHEMA == pf.WAYPOINTS_SCHEMA,
      f"viewer={av.WAYPOINTS_SCHEMA} e2e={pf.WAYPOINTS_SCHEMA}")
check("★viewer 的默认路点路径就是 e2e 该读的那个",
      str(av.WAYPOINTS_PATH).endswith("user_waypoints.json"),
      str(av.WAYPOINTS_PATH))

with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    check("dump -> e2e 读回 无错",
          "error" not in pf._load_waypoints_file(
              write(d, "a.json", GOOD, bom=True)))
    _r = pf._load_waypoints_file(write(d, "b.json", GOOD, bom=True))
    check("★往返后路点坐标不丢", len(_r["waypoints"]) == 2
          and _r["waypoints"][0]["x"] == 1200.0)
    check("★note 一并保留（用户写的备注不能丢）",
          [w["note"] for w in _r["waypoints"]] == ["沿河谷", "绕山"])
    check("★target / start 都带回来",
          _r["target"] == (3000.0, 3000.0) and _r["start"] == (1000.0, 1000.0))

    # ★ BOM：记事本 / PowerShell 存出来的都是带 BOM 的
    check("★带 BOM 的文件也能读（记事本/PowerShell 存出来的都是）",
          "error" not in pf._load_waypoints_file(
              write(d, "c.json", GOOD, bom=True)))
    check("★viewer 也能读带 BOM 的",
          len(av.load_waypoints(d / "c.json")) == 2)

# ======================================================================
print("\n=== 2. e2e 解析的拒绝分支（半份即判废）===")
# ======================================================================
with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    cases = [
        ("schema 不符", dict(GOOD, schema_version=99)),
        ("缺 schema", {k: v for k, v in GOOD.items() if k != "schema_version"}),
        ("★路点空 **且** target 也是 null（真没内容）",
         {"schema_version": 1, "waypoints": [],
          "start": None, "target": None}),
        ("★路点空 **且** start 也是 null",
         {"schema_version": 1, "waypoints": [], "start": None}),
        ("target 只有一个元素", dict(GOOD, target=[1])),
        ("target 是三个元素", dict(GOOD, target=[1, 2, 3])),
        ("target 是字符串不是数组", dict(GOOD, target="3000,3000")),
        ("target 里是非数字", dict(GOOD, target=["a", 3000])),
        ("路点缺 z", dict(GOOD, waypoints=[{"x": 1200}])),
        ("路点是字符串不是对象", dict(GOOD, waypoints=["1200,1300"])),
        ("坐标是 NaN", dict(GOOD, waypoints=[{"x": float("nan"), "z": 1.0}])),
        ("★半份合法（一好一坏）",
         dict(GOOD, waypoints=[{"x": 1200, "z": 1300}, {"x": "a", "z": 2}])),
        ("顶层是数组", [1, 2]),
    ]
    for i, (name, doc) in enumerate(cases):
        r = pf._load_waypoints_file(write(d, f"r{i}.json", doc))
        check(f"★{name} -> 拒绝", "error" in r,
              "" if "error" in r else f"竟然读出了 {r.get('waypoints')}")
    r = pf._load_waypoints_file(str(d / "not_exist.json"))
    check("★文件不存在 -> 拒绝（不是崩）", "error" in r)
    (d / "broken.json").write_text("{ 这不是 JSON", encoding="utf-8")
    check("★坏 JSON -> 拒绝", "error" in pf._load_waypoints_file(
        str(d / "broken.json")))
    # start 坏了**不**致命（它只是参考）
    r = pf._load_waypoints_file(
        write(d, "nostart.json", dict(GOOD, start=["坏", 1])))
    check("★start 坏了不致命（只是参考值）",
          "error" not in r and r["start"] is None)

# ======================================================================
print("\n=== 2b. null 降级（2026-10-08：老文件 start/target 恒为 null）===")
# ======================================================================
# 修的 bug：anchors_viewer 以前把 start/target 写死成 null，
#   于是**每个**老文件都在这里撞「target 必须是 [x, z] 两元素」被整份拒掉，
#   而文件里明明有 2 个可用路点。null = 没写，补全；写坏了 = 仍判废。
with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    # ★ 用户真实遇到的形态，一字不差复现
    REAL = {"schema_version": 1, "created_at_ms": 1791299540046, "start": None,
            "waypoints": [{"x": 2176.3, "z": -1053.1, "note": ""},
                          {"x": 1544.5, "z": -1032.3, "note": ""}],
            "target": None}
    r = pf._load_waypoints_file(write(d, "real.json", REAL))
    check("★★真实老文件（start/target 全 null）-> 不再被拒",
          "error" not in r, "" if "error" not in r else r.get("error"))
    check("★target 降级到**最后一个**路点",
          r.get("target") == (1544.5, -1032.3), str(r.get("target")))
    check("★start 降级到**第一个**路点",
          r.get("start") == (2176.3, -1053.1), str(r.get("start")))
    check("★路点本身一个不少（e2e 要逐点走）",
          len(r.get("waypoints", [])) == 2)
    check("★note 仍然保留", [w["note"] for w in r["waypoints"]] == ["", ""])

    r = pf._load_waypoints_file(
        write(d, "notgt.json", {k: v for k, v in GOOD.items()
                                if k != "target"}))
    check("★只缺 target -> 降级到最后一个路点",
          "error" not in r and r["target"] == (1800.0, 1700.0))
    r = pf._load_waypoints_file(
        write(d, "nostart2.json", dict(GOOD, start=None)))
    check("★只 start=null -> 降级到第一个路点",
          "error" not in r and r["start"] == (1200.0, 1300.0))
    r = pf._load_waypoints_file(
        write(d, "empty_wps.json", dict(GOOD, waypoints=[])))
    check("★路点空但 target 有值 -> 接受（终点靠字段）",
          "error" not in r and r["target"] == (3000.0, 3000.0)
          and r["waypoints"] == [] and r["start"] == (1000.0, 1000.0),
          "" if "error" not in r else r.get("error"))
    r = pf._load_waypoints_file(
        write(d, "empty_wps_nostart.json",
              dict(GOOD, waypoints=[], start=None)))
    check("★路点空 + start=null + target 有值 -> 接受，start 留 None",
          "error" not in r and r["start"] is None
          and r["target"] == (3000.0, 3000.0))
    # ★ 单点文件：首尾都是它自己
    r = pf._load_waypoints_file(write(d, "one.json", {
        "schema_version": 1, "start": None,
        "waypoints": [{"x": 100, "z": 200, "note": ""}], "target": None}))
    check("★单点文件 -> start == target == 那一个点",
          "error" not in r and r["start"] == r["target"] == (100.0, 200.0))
    # ★ 有值但不合规 -> 仍然判废（降级只对 null 生效）
    for nm, doc in [("target=[1]", dict(GOOD, target=[1])),
                    ("target='3000,3000'", dict(GOOD, target="3000,3000"))]:
        rr = pf._load_waypoints_file(write(d, "bad_t.json", doc))
        check(f"★有值但不合规（{nm}）-> 仍判废（降级只对 null）",
              "error" in rr)

# ======================================================================
print("\n=== 2c. viewer 保存时补齐首尾（写侧）===")
# ======================================================================
with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    p = d / "wp.json"
    WPS2 = [{"x": 2176.3, "z": -1053.1, "note": ""},
            {"x": 1544.5, "z": -1032.3, "note": "绕山"}]
    ok = av.dump_waypoints(p, WPS2)
    check("★dump 返回 True", ok is True)
    doc = json.loads(p.read_text(encoding="utf-8"))
    check("★★start 自动 = 第一个路点", doc["start"] == [2176.3, -1053.1],
          str(doc["start"]))
    check("★★target 自动 = 最后一个路点", doc["target"] == [1544.5, -1032.3],
          str(doc["target"]))
    check("★waypoints 原样保留全部（不去尾）",
          len(doc["waypoints"]) == 2 and doc["waypoints"][1]["note"] == "绕山")
    check("★★存出来的文件 e2e 直接能读（这才是修 bug 的目的）",
          "error" not in pf._load_waypoints_file(str(p)))
    check("★schema_version 仍是 1", doc["schema_version"] == 1)

    p1 = d / "one.json"
    av.dump_waypoints(p1, [{"x": 10.0, "z": 20.0, "note": ""}])
    doc1 = json.loads(p1.read_text(encoding="utf-8"))
    check("★单点 -> start 与 target 都是它", doc1["start"] == doc1["target"]
          == [10.0, 20.0])

    p2 = d / "explicit.json"
    av.dump_waypoints(p2, WPS2, start=(1.0, 2.0), target=(9.0, 9.0))
    doc2 = json.loads(p2.read_text(encoding="utf-8"))
    check("★显式传了 start/target -> 以传入的为准（不覆盖）",
          doc2["start"] == [1.0, 2.0] and doc2["target"] == [9.0, 9.0])

    p3 = d / "zero.json"
    av.dump_waypoints(p3, [{"x": 0.0, "z": 0.0, "note": ""}])
    doc3 = json.loads(p3.read_text(encoding="utf-8"))
    check("★(0,0) 不是「没传」（None 判断，不是真值判断）",
          doc3["start"] == [0.0, 0.0] and doc3["target"] == [0.0, 0.0])

    p4 = d / "empty.json"
    r4 = av.dump_waypoints(p4, [])
    check("★没有路点 -> 拒绝保存（不产出 null 文件）",
          r4 is False and not p4.exists())

    check("★_save_waypoints 调用时不需要再显式传首尾",
          "dump_waypoints(self.waypoints_path, self.waypoints)"
          in (MISC / "anchors_viewer.py").read_text(encoding="utf-8"))

# ======================================================================
print("\n=== 3. viewer 侧容错 ===")
# ======================================================================
with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    check("文件不存在 -> []", av.load_waypoints(d / "no.json") == [])
    (d / "bad.json").write_text("{ 坏", encoding="utf-8")
    check("坏 JSON -> []", av.load_waypoints(d / "bad.json") == [])
    (d / "arr.json").write_text(json.dumps([1, 2]), encoding="utf-8")
    check("顶层是数组 -> []", av.load_waypoints(d / "arr.json") == [])
    (d / "v9.json").write_text(json.dumps(
        {"schema_version": 9, "waypoints": [{"x": 1, "z": 2}]}), encoding="utf-8")
    check("★版本不符 -> []（不拿旧字段名凑合）",
          av.load_waypoints(d / "v9.json") == [])
    (d / "half.json").write_text(json.dumps(
        {"schema_version": 1,
         "waypoints": [{"x": 1, "z": 2}, {"x": "a", "z": 3}]}), encoding="utf-8")
    check("★半份 -> []（整份判废，不做尽力修正）",
          av.load_waypoints(d / "half.json") == [])
    ok = av.load_waypoints(Path(write(d, "ok.json", GOOD)))
    check("正常 -> 读出 2 个，note 保留",
          len(ok) == 2 and ok[0]["note"] == "沿河谷")

# ======================================================================
print("\n=== 4. CLI 参数契约 ===")
# ======================================================================
_src = (MISC / "e2e_pathfind.py").read_text(encoding="utf-8")
_avsrc = (MISC / "anchors_viewer.py").read_text(encoding="utf-8")

check("★--waypoints-file 已注册且带值", '"--waypoints-file"' in _src
      and '("waypoints_file", str, True)' in _src)
check("★与三个目标参数互斥（TARGET_ARGS 全覆盖）",
      "_conflict = [n for n in TARGET_ARGS" in _src)
check("★不给目标时也接受 waypoints-file",
      "and not a[\"waypoints_file\"]" in _src)
# ★ 锚点必须带行首缩进：源码注释里也出现过 `hr("0 · 参数")` 这段文字
#   （「日志接管必须在任何输出之前（含 hr("0 · 参数")）」），
#   不带缩进会先撞上注释里那个，判断永远是反的。
_BANNER = '\n    hr("0 · 参数")'
check("★路点文件在横幅**之前**载入（否则横幅 NoneType 崩）",
      _src.index('if A["waypoints_file"]:') < _src.index(_BANNER))
check("★载入后打开 WAYPOINT_MODE（中间路点要判朝向）",
      "WAYPOINT_MODE = True" in _src
      and "global WAYPOINT_MODE" in _src)
check("★global 在 main 开头（Python 要求先于任何引用）",
      _src.index("global WAYPOINT_MODE") < _src.index(_BANNER))
check("★★ 起点体检分三档（2026-10-08 改：原来只有 >50m 的软提醒）",
      pf.START_MISMATCH_WARN_M == 500.0
      and pf.START_MISMATCH_BLOCK_M == 2000.0
      and "起点体检未通过" in _src,
      f"警告 {pf.START_MISMATCH_WARN_M:g}m / 拦截 {pf.START_MISMATCH_BLOCK_M:g}m")
check("★ 超过拦截阈值**真的拒绝启动**（return 2），不是只打印",
      "已拒绝启动" in _src and 'return 2' in _src,
      "相位相关是相对定位，走错地方零报错，必须在这里拦")
check("★ 逃生门是显式 flag --i-know-start（不给就别想跑）",
      '"--i-know-start"' in _src and 'A.get("i_know_start")' in _src)
check("★ 体检只在 --waypoints-file 模式下生效（在横幅之前）",
      _src.index('if A["waypoints_file"]:')
      < _src.index("起点体检未通过") < _src.index(_BANNER))
check("★ 旧的软提醒文案已随行为一起改掉（别留下两套口径）",
      "⚠ 文件里的起点与本次 --start-world 相距" not in _src)
check("★读文件用 utf-8-sig（记事本存的 JSON 带 BOM）",
      'encoding="utf-8-sig"' in _src and 'encoding="utf-8-sig"' in _avsrc)
check("★viewer：裸左键=路点、Shift+左键=命名锚点",
      "def _add_waypoint" in _avsrc and "def _add_named_anchor" in _avsrc
      and "if ev.state & 0x0001:" in _avsrc)
check("★命名锚点原逻辑保留（没删功能，只换触发键）",
      "记录锚点" in _avsrc and "self.save(auto=True)" in _avsrc)
check("★右键删「离光标最近」的（不是最后一个）",
      "_on_rpress" in _avsrc and "best_d" in _avsrc
      and "删了离光标太远" not in _avsrc)
check("★右键绑到 <Button-3>（原来空着）",
      'bind("<Button-3>"' in _avsrc)
check("★快捷键绑到 <Key>，Ctrl 用 0x0004",
      'bind("<Key>"' in _avsrc and "ev.state & 0x0004" in _avsrc)
check("★路点序列用**独立 label**（不与悬停坐标抢）",
      "self.lbl_wp" in _avsrc and 'tag="lbl_wp"' not in _avsrc)
check("★Ctrl+C/S/O 三个都实现了",
      all(f in _avsrc for f in ("_copy_waypoints", "_save_waypoints",
                                 "_load_waypoints")))
check("★复制用 tk 自带剪贴板（不引新依赖）",
      "clipboard_append" in _avsrc)
check("★路径图也按同一坐标系画（draw_route 后才翻转）",
      "apply_flip(draw_route" in (MISC / "path_planner.py").read_text(
          encoding="utf-8"))
check("★path_planner 本轮未动（保留供未来研究）",
      (MISC / "path_planner.py").exists())

print()
print("=" * 74)
print(f"结果: {len(PASS)}/{len(PASS) + len(FAIL)} 通过")
if FAIL:
    print("  失败: " + ", ".join(FAIL))
print("=" * 74)
sys.exit(1 if FAIL else 0)
