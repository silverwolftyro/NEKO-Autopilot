"""从 ``e2e_pathfind.py`` 里**抽取纯逻辑函数**的执行沙箱。

★ 为什么用 ast 抽取而不是 ``import``
  ------------------------------------
  ``e2e_pathfind.py`` 顶层就 ``import uipi_check`` 和
  ``from autopilot.actuator.simulator import VK``。整模块 import 会把
  一大堆跟被测对象无关的依赖拖进来 —— 既慢，又让「测的是寻路逻辑还是
  测的是导入能否成功」变得不清楚。

  所以这里只把**要测的定义**（``FunctionDef`` / ``ClassDef`` / 简单
  ``Assign``）摘出来，在装好标准库的沙箱里单独 exec。测的确实是那份源码
  的真实实现 —— 脚本一改，测试立刻跟着反映。

★ 为什么它值得测
  这份文件 3885 行 / 34 个顶层函数 / 6 个类，其中**只有** ``main`` 与
  ``_warmup_and_resolve_forward`` 碰键鼠和截图。剩下的是转向分段、圆周
  均值、ETA 分桶、路点文件校验、参数解析等等 —— 全是纯逻辑，
  而它们决定了「角色会不会走歪」。
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

import ast
import json
import math
import os
import re
import sys
import time
from pathlib import Path

#: 被测脚本真身。跨项目依赖，用绝对路径；不存在时测试整体 skip。
E2E_PATHFIND = Path(
    RECOG_ROOT / 'exports' / 'benchmark' / 'scripts' / 'misc' / 'e2e_pathfind.py')

#: 要抽取的定义。**刻意不含** ``main`` 与 ``_warmup_and_resolve_forward``
#: —— 它们要真窗口、真输入注入，抽取了也跑不起来。
WANTED = (
    # -- 纯函数 ------------------------------------------------------
    "wrap_pi", "_fmt2", "_pair", "hr",
    "_forward_target", "_pick_teleport_points", "_parse_args",
    "_resolve_target_tp",
    "_raw_phase", "_pick_phase", "_eta_s", "_round10", "_fmt_hint",
    "_next_action", "_dynamic_max_step_px", "_split_turn", "_turn_dispatch",
    "_is_converging", "_stand_turn_step", "_turn_plan",
    "_gain_for", "_clamp_for", "_tier_text",
    "_circ_mean_deg", "_heading_deg",
    "_load_waypoints_file",
    # -- 类（纯逻辑部分）---------------------------------------------
    "_EscapeState", "_NavAdvisor", "_WaypointCursor",
)

#: 抽取时一并带上的模块级常量 —— 上面的函数全靠它们做阈值判断。
WANTED_CONSTS = (
    "DEFAULT_SECONDS", "DEFAULT_DELAY", "DEFAULT_ARRIVE",
    "TURN_FRACTION", "TURN_MAX_DEG", "TURN_BIG_ERR_DEG", "TURN_DEADZONE_DEG",
    "TURN_STAND_DEG", "TURN_RESUME_DEG", "TURN_STAND_SEGMENTS",
    "TURN_MIN_INTERVAL_S", "TURN_MEASURE_SAMPLES", "TURN_EFF_NOISE_DEG",
    "TURN_CAL_MIN_N", "MOUSE_SATURATION_PX", "SPLIT_SLEEP_MS",
    "SPLIT_MAX_SEGMENTS", "MIN_TURN_PX",
    "SPEED_RUN_MPS", "STEP_LIMIT_SAFETY", "STEP_LIMIT_MIN_PX",
    "STEP_LIMIT_MAX_PX", "FRAME_DT_WINDOW", "SAMPLE_MS", "SAMPLE_N",
    "STUCK_DIST", "MOVE_EPS", "WINDOW_MOVE_MIN",
    "ETA_SPEED_MPS", "ETA_BUCKET_S",
    "SLIP_CONVERGE_N", "SLIP_CONVERGE_MIN_DEG",
    "WAYPOINTS_SCHEMA", "NAV_SCHEMA_VERSION",
)


def _sandbox() -> dict:
    ns: dict = {
        "__name__": "e2e_sandbox",      # 别让被抽的代码触发 __main__ 分支
        "__builtins__": __builtins__,
        "math": math, "json": json, "time": time, "os": os,
        "re": re, "sys": sys, "Path": Path,
    }
    return ns


def _literal_consts(tree: ast.Module) -> dict[str, ast.stmt]:
    """模块里所有「字面量常量」的语句，按名字索引。

    接受两种形态：
      ``X = 1``            -> :class:`ast.Assign`
      ``X: tuple = (...)`` -> :class:`ast.AnnAssign`（带类型标注）
    以及值里的 ``math.inf``（:class:`ast.Attribute`）—— ``LEGACY_TURN_TIERS``
    就靠它表示「以上皆适用」。

    只认 ``math.`` 前缀的属性，不放行任意 Attribute：后者可能依赖
    抽不到的对象。
    """
    out: dict[str, ast.stmt] = {}

    def _literal(node: ast.AST) -> bool:
        # ★ 必须**递归**：``LEGACY_TURN_TIERS`` 的元素本身就是元组
        #   ``((15.0, 1.0), (40.0, 2.0), ...)``，只看一层会误判成非字面量。
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return all(_literal(e) for e in node.elts)
        if isinstance(node, (ast.Constant, ast.UnaryOp)):
            return True
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == "math"):
            return True
        return False

    for n in tree.body:
        if isinstance(n, ast.Assign):
            names = [t.id for t in n.targets if isinstance(t, ast.Name)]
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            names = [n.target.id]
        else:
            continue
        if len(names) != 1 or not _literal(n.value):
            continue
        out[names[0]] = n
    return out


def _stmt_name(n: ast.stmt) -> str | None:
    name = getattr(n, "name", None)
    if name is not None:
        return name
    if isinstance(n, ast.Assign):
        names = [t.id for t in n.targets if isinstance(t, ast.Name)]
        return names[0] if len(names) == 1 else None
    if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
        return n.target.id
    return None


def load(*extra_names: str) -> dict:
    """把 :data:`WANTED`（加 ``extra_names``）抽出来 exec，返回命名空间。

    ★ 会**自动补齐**被抽函数的默认参数所引用的模块级常量
      （例如 ``_WaypointCursor.pop_reached`` 的默认实参 ``WAYPOINT_REACH_M``）。
      手工列全这些名字既啰嗦又容易漏 —— 漏一个就是一条看不懂的 NameError。

    :raises FileNotFoundError: 脚本不存在（调用方应 skip 而不是让测试红）
    """
    src = E2E_PATHFIND.read_text(encoding="utf-8")
    tree = ast.parse(src)
    want = set(WANTED) | set(extra_names) | set(WANTED_CONSTS)
    consts = _literal_consts(tree)

    nodes: list[ast.stmt] = []
    found: set[str] = set()
    for n in tree.body:
        name = _stmt_name(n)
        if name is None or name not in want:
            continue
        if isinstance(n, (ast.Assign, ast.AnnAssign)) and name not in consts:
            continue            # 非字面量，跳过（记进 __missing__）
        nodes.append(n)
        found.add(name)

    # ★ 主动补齐被抽节点**引用**的模块级常量（函数体、默认参数都算）。
    #   两个坑都要避开：
    #     ① ``_gain_for`` 体内的 ``LEGACY_TURN_TIERS`` 是**调用时**才抛
    #        NameError —— exec 本身成功，「exec 时捕获再补」救不了，只能预扫；
    #     ② ``_WaypointCursor`` 的默认实参 ``WAYPOINT_REACH_M`` 是**类定义
    #        时**求值 —— 补的常量必须放在使用它的节点**之前**，否则照样炸。
    referenced: set[str] = set()
    for n in nodes:
        for sub in ast.walk(n):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                referenced.add(sub.id)
    extra = [consts[n] for n in sorted(referenced)
             if n not in found and n in consts]
    for st in extra:
        found.add(_stmt_name(st))
        want.add(_stmt_name(st))
    nodes = extra + nodes          # ★ 顺序：常量在前

    ns = _sandbox()
    mod = ast.Module(body=nodes, type_ignores=[])
    exec(compile(mod, str(E2E_PATHFIND), "exec"), ns)
    ns["__missing__"] = sorted(want - found)
    return ns


def module_inventory() -> dict:
    """整个脚本的结构清单，供「清单测试」断言分类结果。"""
    src = E2E_PATHFIND.read_text(encoding="utf-8")
    tree = ast.parse(src)
    funcs = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    classes = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
    return {"funcs": funcs, "classes": classes, "lines": len(src.splitlines())}
