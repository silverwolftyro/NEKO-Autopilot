"""寻路状态 v2：TTL 分档 + 提前展现意图（2026-10-05）。

★ 两边都要测，因为这是一个**跨进程协议**
--------------------------------------
``e2e_pathfind.py`` 写、``layout.py`` 读，两边**互不 import**（约定如此），
所以「版本号对不上」只能靠测试发现 —— 编译通过、ruff 通过、运行时
静默不显示，没有任何一处会报错。

★ 为什么 eta_s 的断言用 21.0 而不是 23.0
--------------------------------------
5 秒桶在 1.5 m/s 下每 **7.5m** 换一档（边界 7.5 / 15 / 22.5 / 30 …）。
23m 落在第 3 档、21m 落在第 2 档 —— **本来就不该相等**。
拿一对跨档的数字去断言「不变」，测的就不是取整逻辑而是运气了。
本文件按同档取样（21.0 / 20.0），并另外显式验一次跨档确实会变。
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
import tempfile
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:                                   # pragma: no cover
    pass

ROOT = Path(PLUGIN_DIR)
sys.path.insert(0, str(ROOT))

from autopilot.ui.overlay import layout                 # noqa: E402

# 写方：按 test_pathfind_logic.py 同样的方式加载（只改 sys.path，安全）
SCRIPT = Path(RECOG_ROOT / 'exports' / 'benchmark' / 'scripts' / 'misc' / 'e2e_pathfind.py')
_spec = importlib.util.spec_from_file_location("_pathfind_intent", SCRIPT)
pf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pf)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f"   {detail}" if detail else ""))


class _Snap:
    """build_panel 的快照替身。非离线分支，免得走进离线那套文案。"""

    health = "OK"
    state = "RUNNING"
    tick_hz = 12.5
    tick_p95_ms = 88.0
    scene = "Teyvat"
    panic_key = "F12"
    intent = None
    capture = None


def _nav_doc(state="navigating", **over):
    doc = {
        "schema_version": layout.NAV_SCHEMA_VERSION,
        "written_at_ms": int(time.time() * 1000),
        "seq": 1,
        "state": state,
        "target_name": "七天神像-风",
        "target_pos": [1928.4, -739.9],
        "dist": 20.0,
        "delta_deg": 0.0,
        "next_action": "直行",
        "phase": "approach",
        "hint": "接近目标 · 还有 20 米",
        "eta_s": 10,
    }
    doc.update(over)
    return doc


def _write_nav(age_s=0.0, state="navigating", **over):
    """写一份状态文件并把 mtime 往前推 ``age_s`` 秒。"""
    f = layout.NAV_STATE_FILE
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(_nav_doc(state, **over), ensure_ascii=False),
                 encoding="utf-8")
    t = time.time() - age_s
    os.utime(f, (t, t))


# ======================================================================
print("=== 1. TTL 分档（任务 1）===")
# ======================================================================
check("活跃态 TTL = 5s", layout.NAV_STATE_MAX_AGE_S == 5.0,
      f"实际 {layout.NAV_STATE_MAX_AGE_S}")
check("终态 TTL = 300s", layout.NAV_STATE_FINAL_MAX_AGE_S == 300.0,
      f"实际 {layout.NAV_STATE_FINAL_MAX_AGE_S}")
check("终态集合 = arrived + panic",
      layout.NAV_FINAL_STATES == frozenset({"arrived", "panic"}),
      f"实际 {sorted(layout.NAV_FINAL_STATES)}")

_orig_nav = layout.NAV_STATE_FILE
with tempfile.TemporaryDirectory() as _td:
    layout.NAV_STATE_FILE = Path(_td) / "pathfind_state.json"
    try:
        # ---- 终态：长 TTL ----
        _write_nav(age_s=10.0, state="arrived")
        r = layout.load_nav_state()
        check("arrived 写完 10 秒后**仍然有效**（v1 这里是 None）",
              r is not None and r.get("state") == "arrived",
              f"实际 {None if r is None else r.get('state')}")

        _write_nav(age_s=120.0, state="arrived")
        check("arrived 120 秒后仍有效（< 300s）",
              layout.load_nav_state() is not None)

        _write_nav(age_s=400.0, state="arrived")
        check("arrived 400 秒后失效（> 300s）",
              layout.load_nav_state() is None)

        _write_nav(age_s=10.0, state="panic")
        r = layout.load_nav_state()
        check("panic 写完 10 秒后仍然有效",
              r is not None and r.get("state") == "panic")

        _write_nav(age_s=350.0, state="panic")
        check("panic 350 秒后失效", layout.load_nav_state() is None)

        # ---- 活跃态：仍是 5s，崩溃保护不能放松 ----
        _write_nav(age_s=3.0, state="navigating")
        check("navigating 3 秒后有效", layout.load_nav_state() is not None)

        _write_nav(age_s=10.0, state="navigating")
        check("★navigating 10 秒后失效（崩溃保护没被终态档带松）",
              layout.load_nav_state() is None)

        _write_nav(age_s=10.0, state="escaping")
        check("escaping 10 秒后失效（也是活跃态）",
              layout.load_nav_state() is None)

        _write_nav(age_s=10.0, state="dry_run")
        check("dry_run 10 秒后失效（也是活跃态）",
              layout.load_nav_state() is None)

        # ---- 粗筛：比两档都老的文件连 JSON 都不该读 ----
        _write_nav(age_s=10_000.0, state="arrived")
        check("10000 秒后直接 None", layout.load_nav_state() is None)

        # ---- 自定义档位仍然可用（测试可注入）----
        _write_nav(age_s=3.0, state="arrived")
        check("显式传 final_max_age_s 生效",
              layout.load_nav_state(final_max_age_s=1.0) is None)
    finally:
        layout.NAV_STATE_FILE = _orig_nav

# ======================================================================
print("\n=== 2. phase 裸判定（任务 2）===")
# ======================================================================
check("schema 版本两边都是 2",
      layout.NAV_SCHEMA_VERSION == 2 == pf.NAV_SCHEMA_VERSION,
      f"layout={layout.NAV_SCHEMA_VERSION} writer={pf.NAV_SCHEMA_VERSION}")

raw = pf._raw_phase
check("120m -> far", raw(120.0, None, False) == "far")
# ★ 边界归属照的是需求原文：「dist > 50 -> far」「10 < dist ≤ 50 -> approach」。
#   所以 50.0 本身属于 approach，**不是** far。
check("★50.0m -> approach（规格是 dist > 50 才 far，边界归 approach）",
      raw(50.0, None, False) == "approach", raw(50.0, None, False))
check("50.1m -> far", raw(50.1, None, False) == "far")
check("49m -> approach", raw(49.0, None, False) == "approach")
check("★10.0m + 无欠账 -> align（规格：dist ≤ 10 且无欠账）",
      raw(10.0, None, False) == "align", raw(10.0, None, False))
check("10.0m + Δ=20° -> final_turn（dist ≤ 10 且有欠账）",
      raw(10.0, 20.0, False) == "final_turn")
check("10.1m -> approach（边界上还没进最后阶段）",
      raw(10.1, None, False) == "approach")
check("9m + Δ=20° -> final_turn", raw(9.0, 20.0, False) == "final_turn")
check("9m + Δ=-20° -> final_turn（左转也是 final_turn）",
      raw(9.0, -20.0, False) == "final_turn")
check("9m + Δ 在死区内 -> align",
      raw(9.0, 3.0, False) == "align")
check("9m + Δ=None -> align", raw(9.0, None, False) == "align")
check("arrive -> arrived（压过距离）",
      raw(120.0, 30.0, True) == "arrived")
check("★80m 外就算 Δ=30° 也是 far（远处不预告转向）",
      raw(80.0, 30.0, False) == "far")
check("死区用的是同一个 TURN_DEADZONE_DEG",
      pf.TURN_DEADZONE_DEG == 8.0, f"实际 {pf.TURN_DEADZONE_DEG}")

# ======================================================================
print("\n=== 3. phase 滞回（任务 2 关键）===")
# ======================================================================
adv = pf._NavAdvisor()
p1 = adv.update(40.0, None, "navigating", "直行")
p2 = adv.update(40.0, None, "navigating", "直行")
p3 = adv.update(40.0, None, "navigating", "直行")
check("进新阶段：第 1 帧不动", p1["phase"] == "far", p1["phase"])
check("进新阶段：第 2 帧仍不动", p2["phase"] == "far", p2["phase"])
check("★进新阶段：第 3 帧才切", p3["phase"] == "approach", p3["phase"])

adv = pf._NavAdvisor()
adv.phase = "approach"
adv._pending, adv._streak = None, 0
q1 = adv.update(120.0, None, "navigating", "直行")
q2 = adv.update(120.0, None, "navigating", "直行")
q3 = adv.update(120.0, None, "navigating", "直行")
check("退旧阶段：第 1 帧不动", q1["phase"] == "approach", q1["phase"])
check("退旧阶段：第 2 帧仍不动", q2["phase"] == "approach", q2["phase"])
check("★退旧阶段：第 3 帧才切（出场也要滞回）",
      q3["phase"] == "far", q3["phase"])

# 最强的一条：在两个阶段之间**每帧一抖**，谁也不该被带走
adv = pf._NavAdvisor()
seen = set()
for i in range(30):
    d = 40.0 if i % 2 else 120.0
    seen.add(adv.update(d, None, "navigating", "直行")["phase"])
check("★来回抖动 30 帧，phase 一次都不换（恒为 far）",
      seen == {"far"}, f"实际 {sorted(seen)}")

# force=True 必须绕过滞回（终态一次性落笔）
adv = pf._NavAdvisor()
adv.update(40.0, None, "navigating", "直行")
forced = adv.update(0.0, None, "arrived", "已到达", force=True)
check("★force=True 绕过滞回（终态不能卡在 approach）",
      forced["phase"] == "arrived", forced["phase"])
check("force=True 时 hint 也立刻换成「已到达」",
      forced["hint"] == "已到达", forced["hint"])

# ======================================================================
print("\n=== 4. hint 缓存 + eta 桶（任务 2）===")
# ======================================================================
adv = pf._NavAdvisor()
hints = {adv.update(120.0 - i * 0.5, None, "navigating", "直行")["hint"]
         for i in range(50)}
check("★phase 不变时 hint 字符串**冻结**（50 帧只出 1 种）",
      len(hints) == 1, f"实际 {len(hints)} 种: {sorted(hints)[:3]}")
check("冻结的 hint 里带着**首次算出时的**距离（120 米，不是最后一帧的 95 米）",
      "120 米" in next(iter(hints)), next(iter(hints)))

adv = pf._NavAdvisor()
adv.update(120.0, None, "navigating", "直行")
h1 = adv.update(119.0, None, "navigating", "直行")["hint"]
check("action 变了 hint 立刻重算（不是死缓存）",
      "接近目标" not in h1, h1)

# --- eta 桶：1.5 m/s 下边界在 7.5m（7.5 / 15 / 22.5 / 30 …）---
check("★21.0m 与 20.0m 同档，eta_s 不变",
      pf._eta_s(21.0) == pf._eta_s(20.0) == 10,
      f"21->{pf._eta_s(21.0)} 20->{pf._eta_s(20.0)}")
check("23.0m 与 22.5m 同档",
      pf._eta_s(23.0) == pf._eta_s(22.5) == 15,
      f"23->{pf._eta_s(23.0)}")
check("★跨档时 eta_s **确实**会变（23.0 vs 21.0 本来就不该相等）",
      pf._eta_s(23.0) != pf._eta_s(21.0),
      f"23->{pf._eta_s(23.0)} 21->{pf._eta_s(21.0)}")
check("eta_s 永远是 5 的倍数",
      all(pf._eta_s(float(d)) % 5 == 0 for d in range(0, 400)))
check("eta_s 不虚报（向下取整，不向上）",
      all(pf._eta_s(float(d)) <= d / pf.ETA_SPEED_MPS
          for d in range(1, 400)))
check("负距离不会炸", pf._eta_s(-5.0) == 0)
check("None/脏输入不炸", pf._eta_s(float("nan")) == 0)

# --- hint 与 eta 必须同生共死 ---
adv = pf._NavAdvisor()
r = adv.update(120.0, None, "navigating", "直行")
h_eta = int(r["hint"].split("还有")[0].split("约")[1].split("秒")[0]) \
    if "秒" in r["hint"] else None
check("★hint 与 eta_s 同源（文件里两个数字不自相矛盾）",
      h_eta is None or h_eta == r["eta_s"],
      f"hint={r['hint']!r} eta_s={r['eta_s']}")

check("far 模板", pf._fmt_hint("far", "直行", 120.0, 80) == "直行中 · 还有 120 米",
      pf._fmt_hint("far", "直行", 120.0, 80))
check("approach 模板",
      pf._fmt_hint("approach", "直行", 40.0, 25) == "接近目标 · 还有 40 米",
      pf._fmt_hint("approach", "直行", 40.0, 25))
check("final_turn 右转模板",
      pf._fmt_hint("final_turn", "右转", 9.0, 5) == "右转中 · 约 5 秒后到达",
      pf._fmt_hint("final_turn", "右转", 9.0, 5))
check("final_turn 左转模板",
      pf._fmt_hint("final_turn", "左转", 9.0, 5) == "左转中 · 约 5 秒后到达",
      pf._fmt_hint("final_turn", "左转", 9.0, 5))
check("align 模板",
      pf._fmt_hint("align", "直行", 5.0, 0) == "正在对准 · 即将到达",
      pf._fmt_hint("align", "直行", 5.0, 0))
check("arrived 模板", pf._fmt_hint("arrived", "已到达", 0.0, 0) == "已到达")
check("★_round10 是普通四舍五入，不是银行家舍入",
      (pf._round10(25.0), pf._round10(35.0)) == (30, 40),
      f"25->{pf._round10(25.0)} 35->{pf._round10(35.0)}")

# ======================================================================
print("\n=== 5. content_key 稳定性（任务 2 收口）===")
# ======================================================================
# 模拟 300 帧直行：dt=0.1s、1.4 m/s -> 走 42m（200m -> 158m），
# 全程在 far 阶段，不会跨 50m 阈值。
adv = pf._NavAdvisor()
keys, line3 = set(), set()
d = 200.0
for i in range(300):
    d -= 1.4 * 0.1
    look = adv.update(d, None, "navigating", "直行")
    doc = _nav_doc("navigating", dist=round(d, 1), phase=look["phase"],
                   hint=look["hint"], eta_s=look["eta_s"])
    model = layout.build_panel(_Snap(), nav=doc)
    keys.add(model.content_key())
    line3.add(model.lines[2].text)

check("★300 帧里第 3 行**一字不变**（hint 缓存生效）",
      len(line3) == 1, f"实际 {len(line3)} 种: {sorted(line3)[:3]}")
# 200->158 跨了 200/190/180/170/160 五个距离档 -> 期望 5~6 次重绘。
# ★ 不是 1：第 4 行「距离: xxxm」按 10m 取整，档位变了就该重绘 ——
#   那是**应该**变的部分。这里的断言是「重绘次数被距离档位数**封顶**」，
#   而不是「完全不重绘」。
check("★300 帧重绘次数被距离档位数封顶（省电设计成立）",
      len(keys) <= 7, f"唯一 content_key = {len(keys)}（距离档 5 个 + 余量）")
check("重绘次数远小于帧数（不是每帧重画）",
      len(keys) < 300 / 10, f"{len(keys)} 次 / 300 帧")

# 换个更强的检查：距离**完全不动**时，指纹必须一动不动
adv = pf._NavAdvisor()
keys2 = set()
for _ in range(300):
    look = adv.update(120.0, None, "navigating", "直行")
    doc = _nav_doc("navigating", dist=120.0, phase=look["phase"],
                   hint=look["hint"], eta_s=look["eta_s"])
    keys2.add(layout.build_panel(_Snap(), nav=doc).content_key())
check("★输入完全不变时 300 帧指纹唯一值 == 1",
      len(keys2) == 1, f"实际 {len(keys2)}")

# ======================================================================
print("\n=== 6. 面板文案 + 跨进程契约 ===")
# ======================================================================
nav = _nav_doc("navigating")
lines = layout._nav_lines(nav, "F12")
check("仍是 5 行（控件数写死 5，不能变）", len(lines) == 5, f"实际 {len(lines)}")
check("第 1 行是状态标签", lines[0].text.startswith("● "), lines[0].text)
check("第 3 行是「下一步:」而不是「动作:」",
      lines[2].text.startswith("下一步: ") and "动作:" not in lines[2].text,
      lines[2].text)
check("第 3 行内容就是 hint（overlay 不加工）",
      lines[2].text == "下一步: 接近目标 · 还有 20 米", lines[2].text)
check("hint 缺失时退回 next_action，而不是空着",
      layout._nav_lines(_nav_doc(next_action="右转", hint=""), "F12")[2].text
      == "下一步: 右转")
check("hint 和 next_action 都缺时显示 —",
      layout._nav_lines(_nav_doc(next_action="", hint=""), "F12")[2].text
      == "下一步: —")
# ★ 版本判定只能在**临时目录**里做：_write_nav 写的是 layout.NAV_STATE_FILE，
#   指向真实运行目录，在那儿测会把用户机器上的状态文件冲掉。
with tempfile.TemporaryDirectory() as _td:
    _saved = layout.NAV_STATE_FILE
    layout.NAV_STATE_FILE = Path(_td) / "n.json"
    try:
        _write_nav(age_s=1.0, schema_version=1)
        check("★v1 文件被 load_nav_state 拒掉（返回 None）",
              layout.load_nav_state() is None)
        _write_nav(age_s=1.0, schema_version=99)
        check("未来版本也被拒（不猜字段含义）",
              layout.load_nav_state() is None)
        _write_nav(age_s=1.0)
        _r = layout.load_nav_state()
        check("v2 文件正常读出 phase / hint / eta_s",
              _r is not None and _r["phase"] == "approach"
              and bool(_r["hint"]) and _r["eta_s"] == 10)
    finally:
        layout.NAV_STATE_FILE = _saved

# 写方源码契约
src = SCRIPT.read_text(encoding="utf-8")
check("写方 doc 里含 phase / hint / eta_s 三个键",
      all(f'"{k}"' in src for k in ("phase", "hint", "eta_s")))
check("★终态那次落笔传了 force=True",
      "locals().get(\"d_deg\"),\n            # ★ force=True" in src
      or "force=True" in src)
check("写方常量 PHASE_STREAK_FRAMES = 3", pf.PHASE_STREAK_FRAMES == 3)
check("写方常量 ETA_BUCKET_S = 5", pf.ETA_BUCKET_S == 5)
check("写方常量 ETA_SPEED_MPS = 1.5", pf.ETA_SPEED_MPS == 1.5)

# ======================================================================
print()
print("=" * 74)
print(f"结果: {len(PASS)}/{len(PASS) + len(FAIL)} 通过")
if FAIL:
    print("  失败: " + ", ".join(FAIL))
print("=" * 74)
sys.exit(1 if FAIL else 0)
