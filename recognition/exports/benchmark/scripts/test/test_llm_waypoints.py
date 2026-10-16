"""LLM 路径规划接入（2026-10-05）。

★ 为什么不用真 API
---------------
本轮明确「不真调 API」。而且就算配了 key，单测也不该花钱、也不该有网络
依赖。所以 :func:`_load_path_planner` 被换成一个假对象 —— 这正是把它
单独抽成一个函数的理由。

★ 本文件验的**最关键的一条**
-------------------------
「LLM 不可用时退回直接朝目标走」。这是整个功能的**降级路径**：
规划是可选增强，它挂了不该让寻路跑不起来。测它不靠 mock 的调用次数，
而是看**最终目标是否仍然是终点**。
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

import importlib.util
import json
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:                                   # pragma: no cover
    pass

MISC = Path(RECOG_ROOT / 'exports' / 'benchmark' / 'scripts' / 'misc')
PLUGIN_ROOT = Path(PLUGIN_DIR)
sys.path.insert(0, str(PLUGIN_ROOT))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


pp = _load("_path_planner", MISC / "path_planner.py")
pf = _load("_pathfind_llm", MISC / "e2e_pathfind.py")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f"   {detail}" if detail else ""))


# ======================================================================
print("=== 1. _parse_waypoints 边界（规格 §4.1）===")
# ======================================================================
P = pp._parse_waypoints

ok3 = json.dumps({"waypoints": [
    {"x": 1200.5, "z": 1100.0, "reason": "沿河谷"},
    {"x": 1800, "z": 1900, "reason": "过桥"},
    {"x": 2400.25, "z": 2300, "reason": "上山道"},
]})
r = P(ok3)
check("合法 JSON（3 个路点）-> 返回 list",
      isinstance(r, list) and len(r) == 3, str(type(r)))
check("★坐标被转成 float（模型给字符串也能用）",
      all(isinstance(w["x"], float) for w in (r or [])), str(r[:1] if r else None))
check("reason 字段保留",
      [w["reason"] for w in (r or [])] == ["沿河谷", "过桥", "上山道"])

r = P(json.dumps({"waypoints": []}))
check("★空 waypoints 数组 -> 返回**空 list**（不是 None）",
      r == [], f"实际 {r!r}")

r = P(json.dumps({"waypoints": [], "error": "被山挡住"}))
check("★不可达（空列表 + error）-> 也是空 list",
      r == [], f"实际 {r!r}")

check("缺 x -> None", P(json.dumps({"waypoints": [{"z": 1.0}]})) is None)
check("缺 z -> None", P(json.dumps({"waypoints": [{"x": 1.0}]})) is None)
check("坐标不是数字 -> None",
      P(json.dumps({"waypoints": [{"x": "abc", "z": 1.0}]})) is None)
check("路点不是对象 -> None",
      P(json.dumps({"waypoints": ["x,y,z"]})) is None)
check("★坐标超绝对范围 -> None",
      P(json.dumps({"waypoints": [{"x": 999999.0, "z": 0.0}]})) is None)
check("★z 超绝对范围 -> None",
      P(json.dumps({"waypoints": [{"x": 0.0, "z": -99999.0}]})) is None)
check("★NaN 坐标 -> None（NaN 与任何数比较都是 False，"
      "不显式挡就会静默穿过范围检查）",
      P('{"waypoints": [{"x": NaN, "z": 0.0}]}') is None)
check("非 JSON -> None", P("这不是 JSON") is None)
check("半截 JSON -> None", P('{"waypoints": [{"x": 1,') is None)
check("没有 waypoints 字段 -> None", P(json.dumps({"foo": 1})) is None)
check("顶层是数组 -> None", P(json.dumps([1, 2, 3])) is None)
check("空字符串 -> None", P("") is None)
check("None -> None", P(None) is None)
check("★半份不收：一个点坏掉就整份判废",
      P(json.dumps({"waypoints": [{"x": 1.0, "z": 1.0},
                                  {"x": 5.0, "z": 5.0},
                                  {"z": 9.0}]})) is None)
check("★markdown 围栏被剥掉",
      P("```json\n" + ok3 + "\n```") is not None)

# ======================================================================
print("\n=== 2. 绝对范围常量 ===")
# ======================================================================
check("WORLD_COORD_LIMIT = 30000",
      pp.WORLD_COORD_LIMIT == 30000.0, str(pp.WORLD_COORD_LIMIT))
check("边界值 30000 本身**合法**（<= 不是 <）",
      P(json.dumps({"waypoints": [{"x": 30000.0, "z": -30000.0}]})) is not None)
check("30001 越界",
      P(json.dumps({"waypoints": [{"x": 30001.0, "z": 0.0}]})) is None)
check("端点写死 DeepSeek",
      pp.DEEPSEEK_ENDPOINT == "https://api.deepseek.com/v1/chat/completions",
      pp.DEEPSEEK_ENDPOINT)
check("环境变量名是 DEEPSEEK_API_KEY / DEEPSEEK_MODEL",
      pp.DEEPSEEK_KEY_ENV == "DEEPSEEK_API_KEY"
      and pp.DEEPSEEK_MODEL_ENV == "DEEPSEEK_MODEL")
check("默认模型 deepseek-flash", pp.DEFAULT_MODEL == "deepseek-flash")
_pp_src = (MISC / "path_planner.py").read_text(encoding="utf-8")
check("默认 timeout=60s（2026-10-06 真机后从 10 提到 60）",
      pp.API_TIMEOUT_S == 60, str(pp.API_TIMEOUT_S))
check("默认 max_tokens（2026-10-06 对照实验后为 4000）",
      pp.DEFAULT_MAX_TOKENS == 4000, str(pp.DEFAULT_MAX_TOKENS))
check("★build_request 里 temperature 固定 0.3（路径要可复现）",
      pp.build_request("AAA", "hi", "m")["temperature"] == 0.3)
check("max_tokens 可覆盖",
      pp.build_request("AAA", "hi", "m", 77)["max_tokens"] == 77)

# ======================================================================
print("\n=== 3. _WaypointCursor：按顺序切目标（规格 §4.2）===")
# ======================================================================
C = pf._WaypointCursor
WPS = [{"x": 100.0, "z": 0.0, "reason": "a"},
       {"x": 200.0, "z": 0.0, "reason": "b"},
       {"x": 300.0, "z": 0.0, "reason": "c"}]
FINAL = (999.0, 999.0)

c = C(WPS, FINAL)
check("初始 active", c.active is True)
check("初始 index = 1/3", c.index == "1/3", c.index)
check("★初始目标是路点 1，不是终点", c.goal() == (100.0, 0.0), str(c.goal()))
check("离得远时不弹出", c.pop_reached(50.0) is None)
check("★到达第 1 个 -> 弹出", (c.pop_reached(3.0) or {}).get("reason") == "a")
check("index 变 2/3", c.index == "2/3", c.index)
check("★目标切到路点 2", c.goal() == (200.0, 0.0), str(c.goal()))
c.pop_reached(1.0)
check("index 变 3/3", c.index == "3/3", c.index)
check("目标切到路点 3", c.goal() == (300.0, 0.0), str(c.goal()))
c.pop_reached(5.0)
check("★队列走完后 active=False", c.active is False)
check("★队列空时目标是**最终终点**", c.goal() == FINAL, str(c.goal()))
check("★队列空时 index 是空串（overlay 不该显示「前往路点 4/3」）",
      c.index == "", repr(c.index))
check("队列空时 pop_reached 返回 None（不越界）",
      c.pop_reached(0.0) is None)
check("done/total 计数", (c.done, c.total) == (3, 3), f"{c.done}/{c.total}")

c = C(WPS, FINAL)
check("★距离恰好等于阈值 -> 算到达（等号归到达）",
      c.pop_reached(pf.WAYPOINT_REACH_M) is not None)
check("比阈值大 0.01 -> 不算到达",
      c.pop_reached(pf.WAYPOINT_REACH_M + 0.01) is None)

c = C(None, FINAL)
check("无路点时 active=False", c.active is False)
check("★无路点时目标就是终点（降级路径的唯一情形）",
      c.goal() == FINAL)
check("★无路点时 index 为空串", c.index == "")

c = C([], FINAL)
check("空列表等价于无路点", c.active is False and c.goal() == FINAL)

_src = (MISC / "e2e_pathfind.py").read_text(encoding="utf-8")
check("★游标**拷贝**入参，不改调用方的 list",
      "_queue = [dict(w) for w in" in _src)

# ======================================================================
print("\n=== 4. 降级路径：mock path_planner.get_waypoints（规格 §4.2）===")
# ======================================================================
_orig_loader = pf._load_path_planner
_calls = []


class _FakePlanner:
    def __init__(self, ret):
        self._ret = ret

    def get_waypoints(self, start, target, *, real=False):
        _calls.append((tuple(start), tuple(target), real))
        if isinstance(self._ret, Exception):
            raise self._ret
        return self._ret


def _with(ret, fn):
    pf._load_path_planner = lambda: _FakePlanner(ret)
    try:
        return fn()
    finally:
        pf._load_path_planner = _orig_loader


try:
    _calls.clear()
    r = _with(None, lambda: pf._plan_with_llm((0.0, 0.0), (10.0, 10.0)))
    check("★返回 None（不可用）-> 调��方退回直接朝目标走",
          r is None, repr(r))
    check("★★e2e 侧默认 real=False（不配 key 也不会偷偷发请求）",
          _calls and _calls[0][2] is False, str(_calls))
    check("起终点原样传下去", _calls[0][:2] == ((0.0, 0.0), (10.0, 10.0)))

    r = _with([], lambda: pf._plan_with_llm((0.0, 0.0), (10.0, 10.0)))
    check("★返回 []（判定不可达）-> 与 None 一样走降级",
          r == [], repr(r))

    r = _with(WPS, lambda: pf._plan_with_llm((0.0, 0.0), (10.0, 10.0)))
    check("返回 3 个路点 -> 原样透传", isinstance(r, list) and len(r) == 3)

    r = _with(RuntimeError("模拟 path_planner 崩了"),
              lambda: pf._plan_with_llm((0.0, 0.0), (10.0, 10.0)))
    check("★path_planner 抛异常 -> 返回 None 而不是往上冒",
          r is None, repr(r))

    # 端到端：拿到 3 个路点后走一遍，目标必须依次是 w1/w2/w3/终点
    cur = pf._WaypointCursor(_with(WPS, lambda: pf._plan_with_llm(
        (0.0, 0.0), (999.0, 999.0))), (999.0, 999.0))
    seq = [cur.goal()]
    while cur.active:
        cur.pop_reached(0.0)
        seq.append(cur.goal())
    check("★端到端：目标依次为 w1/w2/w3/终点",
          seq == [(100.0, 0.0), (200.0, 0.0), (300.0, 0.0),
                  (999.0, 999.0)], str(seq))
finally:
    pf._load_path_planner = _orig_loader

# ======================================================================
print("\n=== 5. 参数互斥（规格 §4.3）===")
# ======================================================================
_argv = sys.argv


def _parse(argv):
    sys.argv = ["e2e_pathfind.py"] + argv
    try:
        return pf._parse_args(), None
    except SystemExit as exc:
        return None, exc.code
    finally:
        sys.argv = _argv


a, code = _parse(["--walk-forward", "30", "--use-llm",
                  "--start-world", "0,0", "--live"])
check("★--walk-forward 30 --use-llm -> exit 2", code == 2, f"code={code}")

# ======================================================================
print("\n=== 5b. --llm-real 四条新组合（2026-10-05 追加）===")
# ======================================================================
_key_env = pf.DEEPSEEK_KEY_ENV
_saved_key = os.environ.get(_key_env)


def _with_key(value):
    if value is None:
        os.environ.pop(_key_env, None)
    else:
        os.environ[_key_env] = value


# --- 组合 1：--llm-real 不带 --use-llm ---
_with_key("sk-test")
a, code = _parse(["--start-world", "1,2", "--target-world", "3,4",
                  "--llm-real"])
check("★组合1 --llm-real 单独给 -> exit 2（不生效的参数要报错，不默默忽略）",
      code == 2, f"code={code}")
a, code = _parse(["--start-world", "1,2", "--target-world", "3,4"])
check("组合1 对照：不给 --llm-real 时正常放行", code is None, f"code={code}")

# --- 组合 2：--llm-real + --use-llm 但没 key ---
_with_key(None)
a, code = _parse(["--start-world", "1,2", "--target-world", "3,4",
                  "--use-llm", "--llm-real"])
check("★组合2 有 --llm-real 但没 DEEPSEEK_API_KEY -> exit 2",
      code == 2, f"code={code}")
_with_key("   ")
a, code = _parse(["--start-world", "1,2", "--target-world", "3,4",
                  "--use-llm", "--llm-real"])
check("★组合2 纯空白 key 也算没配 -> exit 2", code == 2, f"code={code}")

# --- 组合 3：两者都给 + 有 key -> 放行，且 real 透传 ---
_with_key("sk-test")
a, code = _parse(["--start-world", "1,2", "--target-world", "3,4",
                  "--use-llm", "--llm-real"])
check("★组合3 --use-llm --llm-real + 有 key -> 放行",
      code is None and a is not None, f"code={code}")
check("★组合3 llm_real 解析为 True", a and a["llm_real"] is True)
check("★组合3 use_llm 解析为 True", a and a["use_llm"] is True)

_calls.clear()
r = _with(None, lambda: pf._plan_with_llm((0.0, 0.0), (10.0, 10.0),
                                         real=True))
check("★★real=True 真的透传到了 path_planner.get_waypoints",
      _calls and _calls[0][2] is True, str(_calls))
r = _with(None, lambda: pf._plan_with_llm((0.0, 0.0), (10.0, 10.0)))
check("★★默认仍是 real=False（不传就绝不真调）",
      _calls and _calls[-1][2] is False, str(_calls))

# --- 组合 4：--use-llm 不带 --llm-real（有 key 也只是打印） ---
a, code = _parse(["--start-world", "1,2", "--target-world", "3,4",
                  "--use-llm"])
check("★组合4 --use-llm 单独给（即使有 key）-> 放行", code is None)
check("★组合4 llm_real 解析为 False", a and a["llm_real"] is False)
check("★组合4 llm_real 默认值也是 False",
      _parse(["--start-world", "1,2", "--target-world", "3,4"])[0][
          "llm_real"] is False)

if _saved_key is None:
    os.environ.pop(_key_env, None)
else:
    os.environ[_key_env] = _saved_key

a, code = _parse(["--walk-forward", "30", "--start-world", "0,0"])
check("单独的 --walk-forward（配 --live）仍正常解析",
      _parse(["--walk-forward", "30", "--start-world", "0,0",
              "--live"])[1] is None)
check("★--walk-forward 不配 --live 仍被拒（既存行为，没被新参数破坏）",
      _parse(["--walk-forward", "30", "--start-world", "0,0"])[1] == 2)

a, code = _parse(["--start-world", "1,2", "--target-world", "3,4",
                  "--use-llm"])
check("★--use-llm + --target-world -> 放行",
      code is None and a["use_llm"] is True, f"code={code}")
check("★--use-llm 默认关",
      _parse(["--start-world", "1,2", "--target-world", "3,4"])[0]["use_llm"]
      is False)
check("llm_max_calls 默认 20",
      _parse(["--start-world", "1,2", "--target-world", "3,4"])[0][
          "llm_max_calls"] == 20)
check("llm-max-calls 可覆盖",
      _parse(["--start-world", "1,2", "--target-world", "3,4",
              "--llm-max-calls", "5"])[0]["llm_max_calls"] == 5)
check("waypoint_queue 默认 None",
      _parse(["--start-world", "1,2", "--target-world", "3,4"])[0][
          "waypoint_queue"] is None)

# ======================================================================
print("\n=== 6. 契约 ===")
# ======================================================================
check("★WAYPOINT_MODE 模块默认值仍是 False（半成品开关）",
      "WAYPOINT_MODE = False" in _src)
# ★ 只禁「**LLM 路径**」设 True，不禁全文：
#   2026-10-06 的用户点选路点工作流要合法地打开它 —— 那不是 LLM 规划，
#   没有「撞死路点被无限重试」那个问题（那是 do_escape 不弹路点导致的）。
#   真正的约束是：_do_llm_planning 那段**不许**设 True。
_llm_blk = _src[_src.index("def _do_llm_planning"):_src.index("def main()")]
check("★LLM 路径**不**设 WAYPOINT_MODE = True（否则撞死路点会无限重试）",
      "WAYPOINT_MODE = True" not in _llm_blk)
check("★用户点选路点**会**设 WAYPOINT_MODE = True（那是合法的）",
      'if A["waypoints_file"]:' in _src
      and "WAYPOINT_MODE = True" in _src[_src.index('if A["waypoints_file"]:'):])
check("最终目标 twx/twz 不被主循环改写（新增 gx/gz）",
      "gx, gz = wp_cursor.goal()" in _src
      and "twx, twz = A[\"target\"]" in _src)
check("dist/theta_t 改用当前目标 gx/gz",
      "dist = math.hypot(gx - pos[0], gz - pos[1])" in _src
      and "theta_t = math.atan2(gz - pos[1], gx - pos[0])" in _src)
check("★最终目标到达判定在队列空时才做",
      "(dist <= A[\"arrive\"]) and not wp_cursor.active" in _src)
check("换路点时作废朝向窗口 hist（否则拿上一段朝向比新目标）",
      "hist.clear()" in _src)
check("waypoint 半径常量独立于 --arrive",
      "WAYPOINT_REACH_M = 5.0" in _src)
check("★hint 支持 wp_label（浮窗显示「前往路点 i/N」）",
      "wp_label: str = \"\"" in _src
      and "前往路点 {wp_label}" in _src)
check("★wp_label 进缓存键（路点变了 hint 必须立刻重算）",
      "key = (self.phase, action, wp_label)" in _src)
check("两处写盘都传了 wp_label", _src.count("wp_label=wp_cursor.index") == 1,
      f"出现 {_src.count('wp_label=wp_cursor.index')} 次")
check("★没碰 F12 / _PanicWatcher",
      _src.count("class _PanicWatcher") == 1 and "POLL_S = 0.005" in _src)
check("没碰 _stop_all / 急停相关代码",
      'result = "stopped"' in _src and "sprint_off()" in _src)
check("path_planner 没引入新依赖（只 stdlib + numpy + PIL）",
      "import requests" not in (MISC / "path_planner.py").read_text(
          encoding="utf-8"))
# ★ 两边各存了一份密钥环境变量名（e2e 不 import path_planner），
#   这条断言就是防它们漂移的唯一手段 —— 漂了的话 --llm-real 的门禁
#   会去查一个 path_planner 根本不读的名字，于是永远 exit 2。
check("★★DEEPSEEK_API_KEY 两个文件里拼写一致（防常量漂移）",
      pf.DEEPSEEK_KEY_ENV == pp.DEEPSEEK_KEY_ENV == "DEEPSEEK_API_KEY",
      f"e2e={pf.DEEPSEEK_KEY_ENV} planner={pp.DEEPSEEK_KEY_ENV}")
check("★启动时打印了「未给 --llm-real」的提示",
      "未给 --llm-real，将只打印 prompt 不真发请求" in _src)
check("★提示只在该提示分支里出现（给了 --llm-real 就别再说没给）",
      'if not a["llm_real"]:\n        print("  [LLM] 未给 --llm-real' in _src,
      "锚点应指向 _do_llm_planning 里的 a[\"llm_real\"]")
check("★参数横幅里也报了 LLM 模式（一眼看出调没调）",
      "'真发请求' if A['llm_real']" in _src)
check("★path_planner.py 本轮**未改动**（没动 _call_llm 的 real 语义）",
      "real=real" in (MISC / "path_planner.py").read_text(encoding="utf-8"))

# ======================================================================
print("\n=== 7. 响应诊断：截断 / reasoning_content / 真空（2026-10-06）===")
# ======================================================================
# ★ 为什么单独测「响应后处理」而不是 mock urlopen
#   ------------------------------------------------
#   要验的三条分支全在**拿到 doc 之后**：finish_reason 判定、
#   content 空判定、reasoning 兜底提取。而 urlopen 之前的那些
#   （HTTPError / URLError / 超时）本轮没动。所以把后半段单独拎出来
#   驱动，既不用起网络，也不必重跑一遍读底图。

_check = pp._call_llm
_real_urlopen = None


def _run_with_response(resp: dict, prompt: str = "prompt", **kw):
    """造一个假的 urlopen，让 _call_llm 走完真实的后处理。"""
    import urllib.request

    class _Resp:
        def __enter__(self_inner):
            return self_inner

        def __exit__(self_inner, *a):
            return False

        def read(self_inner):
            return json.dumps(resp).encode("utf-8")

    orig = urllib.request.urlopen
    urllib.request.urlopen = lambda req, timeout=None: _Resp()
    saved_key = os.environ.get(pp.DEEPSEEK_KEY_ENV)
    os.environ[pp.DEEPSEEK_KEY_ENV] = "sk-test"
    try:
        # ★ real=True 是必须的：不给的话 _call_llm 会在 [SKIP] 闸门就
        #   返回 None，后面的分支一条都跑不到 —— 那样所有「返回 None」
        #   的断言都会**假通过**，看不出任何区别。
        return _check("AAAA", prompt, real=True, **kw)
    finally:
        urllib.request.urlopen = orig
        if saved_key is None:
            os.environ.pop(pp.DEEPSEEK_KEY_ENV, None)
        else:
            os.environ[pp.DEEPSEEK_KEY_ENV] = saved_key


_OK = json.dumps({"waypoints": [{"x": 1.0, "z": 2.0}]})

# --- 任务 A（2026-10-06 更新：2000 -> 4000，配套 minimal effort）---
check("★DEFAULT_MAX_TOKENS = 4000（500 被真机证明不够，2000 配 low 也被推理吃光）",
      pp.DEFAULT_MAX_TOKENS == 4000, str(pp.DEFAULT_MAX_TOKENS))

# --- 正常响应不受影响 ---
r = _run_with_response({
    "id": "x", "usage": {"prompt_tokens": 939, "completion_tokens": 88,
                         "total_tokens": 1027},
    "choices": [{"finish_reason": "stop",
                 "message": {"role": "assistant", "content": _OK}}]})
check("正常响应（finish_reason=stop）原样返回 content",
      r == _OK, repr(r)[:70])

# --- 任务 D：被 max_tokens 截断 ---
r = _run_with_response({
    "usage": {"prompt_tokens": 939, "completion_tokens": 2000,
              "total_tokens": 2939},
    "choices": [{"finish_reason": "length",
                 "message": {"role": "assistant", "content": ""}}]})
check("★任务D 截断（finish_reason=length + content 空）-> None", r is None)

# --- 任务 C：content 空但 reasoning 里有 JSON ---
r = _run_with_response({
    "choices": [{"finish_reason": "length",
                 "message": {"role": "assistant", "content": "",
                             "reasoning_content":
                                 "我先看看地形…所以应该输出 "
                                 + _OK + " 这样"}}]})
check("★任务C content 空但 reasoning 里有 JSON -> **救回来了**",
      r == _OK, repr(r)[:70])

# --- 任务 C：reasoning 里没有 JSON ---
r = _run_with_response({
    "choices": [{"finish_reason": "length",
                 "message": {"role": "assistant", "content": "",
                             "reasoning_content": "我想了很久，没想出来。"}}]})
check("★reasoning 里没有 JSON -> None（不瞎救）", r is None, repr(r))

# --- 真机原样复刻：completion 恰好等于 max_tokens，content 空，无 reasoning ---
r = _run_with_response({
    "usage": {"prompt_tokens": 939, "completion_tokens": 500,
              "total_tokens": 1439},
    "choices": [{"finish_reason": "length",
                 "message": {"role": "assistant", "content": ""}}]})
check("★真机原样复刻（completion==max_tokens、content 空、无 reasoning）"
      "-> None", r is None, repr(r))

# --- content_filter ---
r = _run_with_response({
    "choices": [{"finish_reason": "content_filter",
                 "message": {"role": "assistant", "content": ""}}]})
check("★content_filter -> None", r is None)

# --- 结构不对 ---
r = _run_with_response({"id": "x", "usage": {}})
check("★没有 choices -> None（不炸）", r is None)
r = _run_with_response({"choices": [{"message": {}}]})
check("★message 里啥都没有 -> None", r is None)

# --- tool_calls ---
r = _run_with_response({
    "choices": [{"finish_reason": "tool_calls",
                 "message": {"role": "assistant", "content": "",
                             "tool_calls": [{"type": "function"}]}}]})
check("★只回 tool_calls -> None", r is None)

# --- content 是 None 而不是字符串 ---
r = _run_with_response({
    "choices": [{"finish_reason": "stop",
                 "message": {"role": "assistant", "content": None}}]})
check("★content 是 JSON null -> None（不 AttributeError）", r is None)

# ======================================================================
print("\n=== 8. reasoning_content 里的 JSON 提取 ===")
# ======================================================================
X = pp._extract_json_from_reasoning
check("能提取出带 waypoints 的 JSON",
      X("所以输出 " + _OK) == _OK)
check("★从后往前找（前面有花括号干扰也不怕）",
      X('{"a":1} 中间思考 {"b":2} 最终 ' + _OK) == _OK)
check("带 markdown 围栏也能提",
      X("```json\n" + _OK + "\n```") == _OK)
check("★没有 waypoints 键 -> None（不把无关 JSON 当答案）",
      X('{"foo": 1}') is None)
check("花括号不成对 -> None", X("开头有个 { 没闭合") is None)
check("没有花括号 -> None", X("完全没有 JSON") is None)
check("空串 / None -> None", X("") is None and X(None) is None)
# ★ 刻意与 _parse_waypoints **不一致**，且是故意的：
#   那个函数是「校验」，任何不合规都判废；这个是「救命」—— content 全空
#   了，答案就在思考里，模型把对象包在数组里（输出 JSON 时很常见）
#   不该让它连救都救不回来。所以这里允许往数组里挖。
check("★rescue 路径允许从数组里挖出 waypoints（救命要紧）",
      X("[" + _OK + "]") == _OK, repr(X("[" + _OK + "]")))
check("★同一份文本在 _parse_waypoints 里被严格判废（两函数口径不同）",
      P("[" + _OK + "]") is None)

# ======================================================================
print("\n=== 9. 诊断走 stderr，不污染 stdout ===")
# ======================================================================
check("★_dbg 存在且走 stderr",
      "def _dbg(msg: str) -> None:" in _pp_src
      and 'print(msg, file=sys.stderr)' in _pp_src)
check("★响应结构诊断函数存在",
      "def _dump_response_shape(doc: dict) -> None:" in _pp_src)
for _frag, _name in (
        ("顶层 keys:", "顶层 keys"),
        ("finish_reason:", "finish_reason"),
        ("content 长度:", "content 长度"),
        ("content 前 200 字:", "content 前 200 字"),
        ("reasoning_content: 存在", "reasoning_content 存在/不存在"),
        ("tool_calls: 存在", "tool_calls 存在/不存在")):
    check(f"★诊断包含「{_name}」", _frag in _pp_src)
check("★content 空时明确打印 (空)",
      '(空)' in _pp_src)
check("★只打印键名不打印值（脱敏）",
      "sorted(doc.keys())" in _pp_src and "sorted(choice.keys())" in _pp_src)

# ======================================================================
print("\n=== 10. prompt 改造（任务 A/B）===")
# ======================================================================
_pr = pp.build_prompt((100, 200, 900, 800), (150.0, 300.0), (850.0, 700.0))
check("★任务A 明确禁止像素/坐标换算",
      "不要尝试从图片里推算像素坐标或做坐标换算" in _pr)
check("★任务A 说明红绿点坐标已给出",
      "红点和绿点的世界坐标已在上方给出" in _pr)
check("★任务A 说清图片只看地形",
      "图片只是让你看**地形**" in _pr)
check("★两步要求存在【执行顺序】", "【执行顺序】" in _pr)
check("★先描述地形、再出路点（这正是 2026-10-06 真机要修的那条）",
      _pr.index("第一步：观察图片") < _pr.index("第二步：基于你第一步的描述")
      and "terrain_analysis" in _pr)
check("★要求路点必须反映地形判断（说一套画另一套比不写更糟）",
      "路点必须反映你在 terrain_analysis 里描述的绕行策略" in _pr)
check("★'不要长篇推理' 已删除（与两步要求直接矛盾）",
      "不要长篇推理" not in _pr)
check("★'直接输出 N 个中间路点的世界坐标' 已删除",
      "直接输出 3-6 个中间路点的世界坐标" not in _pr)
check("★仍要求以 { 开头、} 结束", "你的回答必须以 { 开头，} 结束" in _pr)
check("★任务A 出现在「要求」之前（否定指令要先立规矩）",
      _pr.index("不要尝试从图片里推算") < _pr.index("要求："))
check("★任务B few-shot 示例带 terrain_analysis",
      "terrain_analysis" in _pr[_pr.index("输出示例"):])
# ★ f-string 里 }} 渲染成 }，所以示例收尾是 "]}" 而不是 "]}}"
_ex_i = _pr.index("{", _pr.index("输出示例"))
_ex = _pr[_ex_i:_pr.index("（你的真实输出", _ex_i)].strip()
check("★任务B 示例本身是**合法 JSON**（能被模型照抄格式）",
      (lambda d: isinstance(d, dict) and len(d.get("waypoints", [])) == 3
       and bool(d.get("terrain_analysis")))(json.loads(_ex) if _ex else None),
      repr(_ex)[:60])
check("★示例能同时解出路点和地形（两条解析路径都通）",
      len(pp._parse_waypoints(_ex) or []) == 3
      and bool(pp._extract_terrain(_ex)))
check("★任务B 明确提醒示例是假的",
      "坐标和地形都是假的" in _pr)
check("★原有要求一条没丢",
      all(k in _pr for k in ("路点稀疏", "沿道路/河谷/山谷走",
                             "只要 JSON", "如果判断不可达")))
check("★不可达分支也带 terrain_analysis（仍能解析成 []）",
      '"waypoints": [], "error"' in _pr
      and pp._parse_waypoints(
          '{"terrain_analysis":"被瀑布截断","waypoints":[],"error":"瀑布"}') == [])
check("★_extract_terrain 缺字段时返回空串而不报错（v1 格式照常走）",
      pp._extract_terrain('{"waypoints":[{"x":1.0,"z":2.0}]}') == "")

# ======================================================================
print("\n=== 14. 坐标系修正（--flip，2026-10-06）===")
# ======================================================================
_box = pp.crop_box_px((1000.0, 1000.0), (3000.0, 3000.0))
_p0 = pp.build_prompt(_box, (1000.0, 1000.0), (3000.0, 3000.0), flip=False)
_p1 = pp.build_prompt(_box, (1000.0, 1000.0), (3000.0, 3000.0), flip=True)

# --- 线分类提示（2026-10-06：模型把等高线当成了可走的路）---
# 断言放在这一节，因为 _p0/_p1 在这里才定义（第 10 节用它们会被 F821 抓到）。
check("★prompt 教模型区分等高线 / 水陆边界 / 道路",
      all(k in _p0 for k in ("【如何区分地图上的线】", "**等高线**",
                             "**水陆边界**", "**可通行的道路**")))
check("★明说等高线**不可通行**、不要当成路",
      "**不可通行**，不要把它当成路" in _p0)
check("★给了「分不清就保守」的退路（宁可不走）",
      "宁可不走也不要把等高线当路" in _p0)
check("★只有「明显更粗、更连续」的线才算路",
      "只有**明显更粗、更连续**的线才算道路" in _p0)
check("★线分类段在【执行顺序】**之前**（先立判据再让它描述）",
      _p0.index("【如何区分地图上的线】") < _p0.index("【执行顺序】"))
check("★两版坐标系说明都带线分类段",
      "【如何区分地图上的线】" in _p1)
check("★加段后原有要求一条没丢",
      all(k in _p0 for k in ("路点稀疏", "沿道路/河谷/山谷走",
                             "只要 JSON", "如果判断不可达",
                             "不要尝试从图片里推算像素坐标")))
check("两版 prompt 都有【坐标系说明】",
      "【坐标系说明】" in _p0 and "【坐标系说明】" in _p1)
check("★不翻：起点右下 / 终点左上（实测确认）",
      "起点(1000,1000) 在图片的**右下角**" in _p0
      and "终点(3000,3000) 在图片的**左上角**" in _p0)
check("★翻了：起点左下 / 终点右上（**实测看图确认**，非想象）",
      "起点(1000,1000) 在图片的**左下角**" in _p1
      and "终点(3000,3000) 在图片的**右上角**" in _p1)
check("★逐轴说明（不说「一起增大就往哪边」——那是糊弄）",
      "世界 X 增大" in _p0 and "世界 Z 增大" in _p0
      and "世界 X 增大" in _p1 and "世界 Z 增大" in _p1)
check("不翻版：X 向上、Z 向左", "向上" in _p0 and "向左" in _p0)
check("★翻版：X 向上、**Z 改为向右**（水平镜像只动横向轴）",
      "向上" in _p1 and "向右" in _p1 and "向左" not in _p1.split("【坐标系说明】")[1].split("所以")[0])
check("两版都带真实起终点坐标（不是写死的模板串）",
      all("(1000,1000)" in x and "(3000,3000)" in x for x in (_p0, _p1)))
check("★apply_flip 只做水平镜像（需求预测=left下/右上；180° 会得到左上/右下）",
      "Image.FLIP_LEFT_RIGHT" in _pp_src
      and "Image.FLIP_TOP_BOTTOM" not in _pp_src)
check("★默认不翻（--flip 是 store_true）",
      '"--flip", action="store_true"' in _pp_src)
check("flip 一路透传到 get_waypoints",
      "flip=bool(args.flip)" in _pp_src and "flip: bool = False" in _pp_src)
check("★翻转在标注**之后**、编码之前（坐标计算全程不碰 flip）",
      _pp_src.index("img = annotate(img, box, start, target)")
      < _pp_src.index("shot = apply_flip(img, flip)")
      < _pp_src.index("b64 = encode_png(shot)"))
check("★发给模型的是翻转后那张", "encode_png(shot)" in _pp_src)
check("★路径图也翻转，且用未翻转的底图画（同一套坐标）",
      "apply_flip(draw_route(img.copy(), box, start, target, wps),\n                                flip)"
      in _pp_src or "draw_route(img.copy()" in _pp_src)
check("★启动时打印翻转状态", "图像翻转:" in _pp_src)
check("★timeout 默认 60（10s 会经常掐断推理中的请求）",
      pp.API_TIMEOUT_S == 60, str(pp.API_TIMEOUT_S))
check("★真跑失败不再说「dry-run 下这是预期」",
      "调用失败了" in _pp_src and "dry-run 下这是**预期**" in _pp_src)
check("★起终点世界坐标仍在 prompt 里",
      "(150.0, 300.0)" in _pr and "(850.0, 700.0)" in _pr)
check("★裁剪区世界范围仍在",
      "世界坐标范围" in _pr)

# ======================================================================
print("\n=== 11. reasoning_effort（任务 C）===")
# ======================================================================
# ★ 2026-10-06 对照实验后由 low 改为 **minimal**：同一张复杂地图，
#   effort=minimal 时 completion=687 / finish_reason=stop 且内容正确，
#   而 low 下 2000 与 8000 都被推理吃光。根因是「档位选错」，不是「没送到」。
check("★默认 effort = minimal（low 压不住长推理）",
      pp.DEFAULT_REASONING_EFFORT == "minimal", pp.DEFAULT_REASONING_EFFORT)
_b = pp.build_request("AAA", "p", "m")
check("★请求体默认带 reasoning_effort=minimal",
      _b.get("reasoning_effort") == "minimal", str(_b.get("reasoning_effort")))
check("★默认 max_tokens 与 effort 配套（4000 + minimal）",
      _b["max_tokens"] == pp.DEFAULT_MAX_TOKENS == 4000
      and _b["reasoning_effort"] == "minimal")
check("★minimal 在 CLI 允许取值里（否则会被参数校验拒掉）",
      "low/medium/high/minimal/off" in _pp_src)
check("★自愈逻辑仍保留（换一个不认该字段的兼容层也不会死）",
      "_REASONING_EFFORT_OK" in _pp_src)
check("effort=off 时**不发**该字段",
      "reasoning_effort" not in pp.build_request("AAA", "p", "m", 100, "off"))
check("effort 空串时不发",
      "reasoning_effort" not in pp.build_request("AAA", "p", "m", 100, ""))
check("effort 大写会归一（小写）",
      pp.build_request("AAA", "p", "m", 100, "LOW").get(
          "reasoning_effort") == "low")
check("★MAX_TOKENS_WARN_AT = 16000", pp.MAX_TOKENS_WARN_AT == 16000)
check("★警告语点明「加预算可能没用」",
      "加预算可能没用" in _pp_src)

# --- 自愈重试：400 且点名 reasoning_effort -> 去掉后重试 ---
_sent: list = []


def _make_send(script):
    """script: 依次返回 ('ok', body) 或 ('400', detail)。记录每次请求体。"""
    state = {"i": 0}

    def _send(image_b64, prompt, model, max_tokens, effort):
        _sent.append(effort)
        kind, payload = script[min(state["i"], len(script) - 1)]
        state["i"] += 1
        if kind == "ok":
            return json.dumps(payload)
        import urllib.error as _ue

        raise _ue.HTTPError("u", 400, "Bad Request", {}, None)
    return _send


_ok_resp = {"id": "x", "usage": {"prompt_tokens": 1, "completion_tokens": 2,
                                  "total_tokens": 3},
            "choices": [{"finish_reason": "stop",
                         "message": {"role": "assistant", "content": _OK}}]}


def _run_with_script(script, **kw):
    import urllib.error
    import urllib.request

    saved_flag = pp._REASONING_EFFORT_OK
    pp._REASONING_EFFORT_OK = None
    _sent.clear()
    state = {"i": 0}

    def _urlopen(req, timeout=None):
        _sent.append(json.loads(req.data.decode()).get("reasoning_effort"))
        kind, payload = script[min(state["i"], len(script) - 1)]
        state["i"] += 1
        if kind == "ok":
            class R:
                def __enter__(s2):
                    return s2

                def __exit__(s2, *a):
                    return False

                def read(s2):
                    # ok 分支传的是 **dict**，必须自己序列化；
                    # 400 分支传的是已经序列化好的字符串。
                    return (payload if isinstance(payload, str)
                            else json.dumps(payload)).encode()
            return R()
        # ★ HTTPError(url, code, msg, hdrs, fp) —— 第 5 个才是「响应体」。
        #   把它塞到 hdrs 上，fp 就是 None，调用方 exc.read() 会读不到，
        #   于是「响应体里点名了 reasoning_effort」这个判定永远为假。
        class E:
            def __init__(s2, body):
                s2._b = body

            def read(s2):
                return s2._b.encode() if isinstance(s2._b, str) \
                    else json.dumps(s2._b).encode()

            def close(s2):
                pass
        raise urllib.error.HTTPError("u", 400, "Bad Request", {}, E(payload))

    orig = urllib.request.urlopen
    urllib.request.urlopen = _urlopen
    saved_key = os.environ.get(pp.DEEPSEEK_KEY_ENV)
    os.environ[pp.DEEPSEEK_KEY_ENV] = "sk-test"
    try:
        return pp._call_llm("AAA", "p", real=True, **kw)
    finally:
        urllib.request.urlopen = orig
        pp._REASONING_EFFORT_OK = saved_flag
        if saved_key is None:
            os.environ.pop(pp.DEEPSEEK_KEY_ENV, None)
        else:
            os.environ[pp.DEEPSEEK_KEY_ENV] = saved_key


_reject = json.dumps({"error": {"message": "unknown parameter: "
                                          "reasoning_effort"}})

r = _run_with_script([("400", _reject), ("ok", _ok_resp)])
check("★任务C 端点拒收 reasoning_effort -> 自动去掉后重试并**救回**",
      r == _OK, repr(r)[:60])
_show = lambda v: v if v else "(不带)"          # noqa: E731
check("★重试时确实不再带该字段",
      _sent == [pp.DEFAULT_REASONING_EFFORT, None],
      f"实际发出 {[(_show(v)) for v in _sent]}")

# 第二次调用：已知不支持 -> 不该再发、也不该有多余往返
r2 = _run_with_script([("ok", _ok_resp)], reasoning_effort="off")
check("★effort=off -> 直接不带该字段", _sent == [None], str(_sent))

# 401 不该触发自愈（重试也没用）
r = _run_with_script([("400", json.dumps({"error": {"message": "invalid api key"}})),
                      ("ok", _ok_resp)])
check("★400 但没点名 reasoning_effort -> **不**自愈重试（不白烧往返）",
      r is None and len(_sent) == 1, f"发了 {len(_sent)} 次")

# ======================================================================
print("\n=== 12. max_tokens 提醒 + dump-prompt（任务 D/E）===")
# ======================================================================
check("★--reasoning-effort 与 --dump-prompt 都已注册",
      '"--reasoning-effort"' in _pp_src and '"--dump-prompt"' in _pp_src)
check("★--dump-prompt 是 store_true（不给路径，落 %TEMP%）",
      '"--dump-prompt", action="store_true"' in _pp_src)
check("★--reasoning-effort 默认值引用常量",
      'default=DEFAULT_REASONING_EFFORT' in _pp_src)
check("★effort 取值有校验（low/medium/high/minimal/off）",
      "low/medium/high/minimal/off" in _pp_src)
check("★dump_prompt 一路透传到 _call_llm",
      "dump_prompt=dump_prompt" in _pp_src)

import tempfile as _tf

_real_prompt = pp.build_prompt((100, 200, 900, 800), (150.0, 300.0),
                               (850.0, 700.0))
with _tf.TemporaryDirectory() as _td:
    _pf = Path(_td) / "llm_prompt.txt"
    _run_with_response({"choices": [{"finish_reason": "stop",
                                    "message": {"content": _OK}}]},
                       prompt=_real_prompt, dump_prompt=str(_pf))
    check("★--dump-prompt 真的把 prompt 写到磁盘",
          _pf.is_file() and _pf.read_text(encoding="utf-8") == _real_prompt,
          f"文件存在={_pf.is_file()}")
with _tf.TemporaryDirectory() as _td:
    _pf = Path(_td) / "llm_prompt.txt"
    _run_with_response({"choices": [{"finish_reason": "stop",
                                    "message": {"content": _OK}}]})
    check("★不给 dump_prompt 时**不**落盘（不偷偷写文件）",
          not _pf.exists())

# ======================================================================
print("\n=== 13. --viz 路径可视化 ===")
# ======================================================================
from PIL import Image as _PILImage                     # noqa: E402

# ★ 裁剪框必须**真的包含**起终点的世界坐标 —— _world_to_img 是
#   「世界 -> 底图像素 -> 落在框内哪」，框和坐标对不上就会算出
#   框外几百像素，直接 IndexError。所以框由坐标反推，而不是手编。
_ST = (150.0, 300.0)
_TG = (900.0, 800.0)
_WPS = [{"x": 350.0, "z": 420.0, "reason": "a"},
        {"x": 620.0, "z": 640.0, "reason": "b"}]
_sp, _tp = pp.world_to_px(*_ST), pp.world_to_px(*_TG)
_pad = 200
_BOX = (min(_sp[0], _tp[0]) - _pad, min(_sp[1], _tp[1]) - _pad,
        max(_sp[0], _tp[0]) + _pad, max(_sp[1], _tp[1]) + _pad)
_BW = _BOX[2] - _BOX[0]
_BH = _BOX[3] - _BOX[1]
_IMW = 256
_IMH = max(1, int(_IMW * _BH / _BW))

check("★--viz 已注册", '"--viz", action="store_true"' in _pp_src)
check("★--viz 一路透传到 get_waypoints(viz=...)",
      "viz=bool(args.viz)" in _pp_src)
check("★draw_route / _world_to_img / _load_font 都存在",
      all(f"def {f}(" in _pp_src
          for f in ("draw_route", "_world_to_img", "_load_font")))
check("★坐标换算只有 _world_to_img 一处真源（annotate 与 draw_route 共用）",
      _pp_src.count("_world_to_img(img, box,") >= 3
      and "scale = max(1e-9, img.width" not in _pp_src,
      f"出现 {_pp_src.count('_world_to_img(img, box,')} 次")

# --- 功能：annotate 画出的红点，必须正好落在 _world_to_img 算的位置 ---
_base = _PILImage.new("RGB", (_IMW, _IMH), (30, 30, 30))
_ann = pp.annotate(_base.copy(), _BOX, _ST, _TG)
_sx, _sy = pp._world_to_img(_ann, _BOX, _ST[0], _ST[1])
_pix = _ann.load()
_ix, _iy = int(round(_sx)), int(round(_sy))
check("★annotate 的起点红点**正好**落在 _world_to_img 算出的像素上",
      _pix[_ix, _iy] == (255, 60, 60),
      f"期望红 (255,60,60) 实际 {_pix[_ix, _iy]}")

# --- draw_route 会真的改像素 ---
_clean = _ann.copy()
_plain = _ann.copy()
_routed = pp.draw_route(_plain, _BOX, _ST, _TG, _WPS)
check("★draw_route 真的画上去了（像素有变化）",
      _routed.tobytes() != _clean.tobytes())
check("★draw_route 是**就地**绘制并返回同一对象（声明与实现一致）",
      _routed is _plain,
      "docstring 已于 2026-10-06 从「返回新图」更正为「就地」")
check("★没传副本的话入参会被改 —— 所以调用点必须 .copy()",
      "draw_route(img.copy(), box, start, target, wps)" in _pp_src)
check("空路点时 draw_route 原样返回（不画空气线）",
      pp.draw_route(_clean.copy(), _BOX, _ST, _TG, []).tobytes()
      == _clean.tobytes())
_wx, _wy = pp._world_to_img(_routed, _BOX, _WPS[0]["x"], _WPS[0]["z"])
check("★路点圆心是黄点色",
      _routed.load()[int(round(_wx)), int(round(_wy))] == pp.ROUTE_COLOR,
      f"实际 {_routed.load()[int(round(_wx)), int(round(_wy))]}")
check("★编号是白字（深浅底色都读得出）",
      'fill=(255, 255, 255), font=font' in _pp_src
      and "stroke_fill=(0, 0, 0)" in _pp_src)
check("★annotate / draw_route 的声明都已从「返回新图」更正为「就地」",
      "**就地绘制**，返回同一对象" in _pp_src
      # 只查**声明句**消失了；更正说明里引用旧措辞是应该保留的
      and '"""在缩放后的图上画起点(红)/终点(绿)。**返回新图**，不改入参。"""'
      not in _pp_src
      and "**同一对象**（就地绘制" in _pp_src)
check("★折线包含起点与终点（不只连中间路点）",
      "pts = [_world_to_img(img, box, start[0], start[1])]" in _pp_src
      and "pts.append(_world_to_img(img, box, target[0], target[1]))" in _pp_src)


# ★ 规划必须在**距离体检之后** —— 否则「太近/太远」这种硬伤会先花掉
#   一次真调用。这条曾经写反过（注释说「别白花」，代码却排在体检前），
#   实跑 --start-world 1,2 --target-world 3,4 --use-llm --llm-real
#   复现过：体检报「目标太近 2.8m，不启动」，规划却已经跑完。
_i_plan = _src.index("_do_llm_planning(A)")
_i_health = _src.index("# ---- 距离体检")
_i_build = _src.index('hr("1 · 构造")')
_i_tp = _src.index("# ---- 目标解析：--target-tp 在这里定")
check("★★_do_llm_planning 的调用排在距离体检**之后**（不白花 API 调用）",
      _i_plan > _i_health, f"规划@{_i_plan} 体检@{_i_health}")
check("★规划仍排在构造 Capturer 之前（规格要求）",
      _i_plan < _i_build, f"规划@{_i_plan} 构造@{_i_build}")
check("★规划仍排在目标解析之后（--target-tp 的坐标到这里才定）",
      _i_plan > _i_tp, f"规划@{_i_plan} 目标解析@{_i_tp}")
check("★规划逻辑抽成了函数（40 行不该内联在 main 里）",
      "def _do_llm_planning(a: dict) -> None:" in _src
      and _src.count("_do_llm_planning(A)") == 1)

print()
print("=" * 74)
print(f"结果: {len(PASS)}/{len(PASS) + len(FAIL)} 通过")
if FAIL:
    print("  失败: " + ", ".join(FAIL))
print("=" * 74)
sys.exit(1 if FAIL else 0)
