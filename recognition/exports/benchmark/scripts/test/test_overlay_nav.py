"""overlay 寻路状态显示的单测（2026-10-04）。

★ 为什么这些值得单测
--------------------
``layout.build_panel`` 是**纯函数**（不碰 tkinter），寻路模式下 5 行
的**行数、顺序、文案**全是硬约定。控件数在 ``app._build_widgets`` 里
是写死的 ``range(5)``，一旦这里返回 4 行或 6 行，
``zip(self._lines, model.lines)`` 会**静默截断** —— 不报错，只是
少显示一行。这种 bug 在实机上看不出来（就是「少了一行」）。

``_fmt_dist`` 的 10m 取整更是省电机制的命门：显示到小数点会让
``content_key()`` 每帧都变，10Hz 无条件重绘，直接废掉 overlay 的
省电设计。必须钉死。

★ 不碰 GUI
----------
零 ``def test_``（本仓库 conftest.py 不生效，自带 runner）。
只调纯函数，**不起 Tk 窗口**、**不碰寻路状态文件**（全部用临时目录）。
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

import json
import os
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:                                   # pragma: no cover
    pass

PLUGIN_ROOT = Path(PLUGIN_DIR)
sys.path.insert(0, str(PLUGIN_ROOT))

from autopilot.ui.overlay import layout  # noqa: E402
from autopilot.ui.overlay.layout import (  # noqa: E402
    NAV_SCHEMA_VERSION, Line, build_panel, load_nav_state,
)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f"   {detail}" if detail else ""))


class _Snap:
    """极简快照替身。build_panel 只用 getattr 读字段。"""

    health = "OFFLINE"          # 默认走「离线」分支，最容易区分
    state = "STOPPED"
    tick_hz = 12.5
    tick_p95_ms = 88.0
    scene = "Teyvat"
    panic_key = "F12"
    intent = None
    capture = None


class _RunningSnap(_Snap):
    """非离线的快照。**必须**用它验「nav=None 时现状不变」——
    拿 _Snap（health=OFFLINE）去验会走离线分支，那 5 行里
    本来就没有「通道:」，断言必然假失败（本文件第一版栽在这）。"""

    health = "OK"
    state = "RUNNING"


def nav_doc(**over):
    d = {
        "schema_version": NAV_SCHEMA_VERSION,
        "written_at_ms": int(time.time() * 1000),
        "seq": 1,
        "state": "navigating",
        "target_name": "七天神像-风",
        "target_pos": [1928.4, -739.9],
        "dist": 20.5,
        "delta_deg": 1.3,
        "next_action": "直行",
        # ★ v2（2026-10-05）：第 3 行改显示写方算好的整句 hint。
        #   overlay 这边**一个字都不加工** —— 取整/量化/滞回全在写方做完。
        "phase": "approach",
        "hint": "接近目标 · 还有 20 米",
        "eta_s": 10,
    }
    d.update(over)
    return d


# ======================================================================
# 1. 行数：两种模式都必须**恰好 5 行**
# ======================================================================
print("\n=== 1. 行数（控件数写死 5，少一行=静默截断）===")
m_nav = build_panel(_Snap(), nav=nav_doc())
check("nav 模式恰好 5 行", len(m_nav.lines) == 5, f"n={len(m_nav.lines)}")
m_off = build_panel(_Snap())
check("离线模式恰好 5 行", len(m_off.lines) == 5, f"n={len(m_off.lines)}")
m_run = build_panel(_Snap(), nav=None)
check("nav=None 时不改变原有 5 行", len(m_run.lines) == 5, f"n={len(m_run.lines)}")

# ======================================================================
# 2. nav 模式的 5 行内容与顺序
# ======================================================================
print("\n=== 2. nav 模式 5 行内容 ===")
txt = [ln.text for ln in m_nav.lines]
print(f"     实际输出：")
for t in txt:
    print(f"       {t!r}")
check("第 1 行是状态标签（● 开头）", txt[0].startswith("● "), repr(txt[0]))
check("第 1 行含「寻路中」", "寻路中" in txt[0])
check("第 2 行是目标", txt[1].startswith("目标: "), repr(txt[1]))
check("第 2 行含目标名", "七天神像-风" in txt[1])
check("★第 3 行是「下一步」（v2：写方算好的整句，不是本地的动作类型）",
      txt[2].startswith("下一步: "), repr(txt[2]))
check("第 3 行内容就是 hint 原样（overlay 不做任何加工）",
      txt[2] == "下一步: 接近目标 · 还有 20 米", repr(txt[2]))
check("第 4 行是距离", txt[3].startswith("距离: "), repr(txt[3]))
check("第 5 行是急停（唯一的刹车，必须在）", txt[4].startswith("急停: "), repr(txt[4]))
check("第 5 行含 F12", "F12" in txt[4])

# ======================================================================
# 3. nav 存在时**完全替换**，不是追加
# ======================================================================
print("\n=== 3. nav 存在时完全替换 ===")
m = build_panel(_Snap(), nav=nav_doc())
allt = " | ".join(ln.text for ln in m.lines)
for engine_only in ("场景:", "意图:", "通道:", "Hz"):
    check(f"nav 模式下不含引擎行 {engine_only!r}", engine_only not in allt, allt)
check("nav 模式下不含「离线」", "离线" not in allt, allt)

m = build_panel(_RunningSnap(), nav=None)
allt0 = " | ".join(ln.text for ln in m.lines)
print(f"     nav=None 时（引擎在线）：")
for t in allt0.split(" | "):
    print(f"       {t!r}")
check("nav=None 时含引擎行（现状不变）", "通道:" in allt0, allt0)
check("nav=None 时含「场景:」", "场景:" in allt0, allt0)
check("nav=None 时是 5 行", len(m.lines) == 5, f"n={len(m.lines)}")

# ======================================================================
# 4. _fmt_dist 的 10m 取整（省电命门）
# ======================================================================
print("\n=== 4. _fmt_dist 10m 取整 ===")
f = layout._fmt_dist
cases = [
    (20.5, "20m"), (24.9, "20m"), (25.0, "30m"), (4.9, "已到位"),
    (0.0, "已到位"), (99.9, "100m"), (100.0, "100m"), (1004.0, "1000m"),
]
for d, want in cases:
    got = f({"dist": d})
    check(f"dist={d} -> {want}", got == want, f"got={got!r}")
check("dist 缺失 -> —", f({}) == "—", repr(f({})))
check("dist 非数字 -> —", f({"dist": "abc"}) == "—", repr(f({"dist": "abc"})))
check("dist=NaN -> —（不能显示 'nan'）",
      f({"dist": float("nan")}) == "—", repr(f({"dist": float("nan")})))

# ★ 变异测试：去掉取整，这一列就每帧变 -> 指纹每帧变
raw_m = build_panel(_Snap(), nav=nav_doc(dist=20.5))
raw_m2 = build_panel(_Snap(), nav=nav_doc(dist=20.7))
check("★20.5 与 20.7 指纹相同（0.2m 变化不触发重绘）",
      raw_m.content_key() == raw_m2.content_key())
raw_m3 = build_panel(_Snap(), nav=nav_doc(dist=24.0))
check("★20.5 与 24.0 指纹相同（仍在 20m 档）",
      raw_m.content_key() == raw_m3.content_key())
raw_m4 = build_panel(_Snap(), nav=nav_doc(dist=25.0))
check("★20.5 与 25.0 指纹不同（跨档 -> 允许重绘）",
      raw_m.content_key() != raw_m4.content_key())

# ======================================================================
# 5. _fmt_target：无名退回坐标
# ======================================================================
print("\n=== 5. _fmt_target ===")
t = layout._fmt_target
check("有名字 -> 显示名字", t(nav_doc()) == "七天神像-风", repr(t(nav_doc())))
check("名字为空串 -> 退回坐标",
      t(nav_doc(target_name="")) == "(1928, -740)", repr(t(nav_doc(target_name=""))))
check("名字为 None -> 退回坐标",
      t(nav_doc(target_name=None)) == "(1928, -740)", repr(t(nav_doc(target_name=None))))
check("名字和坐标都没有 -> 未知", t({}) == "未知", repr(t({})))
check("坐标不是数字 -> 未知（不崩）", t({"target_pos": ["a", "b"]}) == "未知",
      repr(t({"target_pos": ["a", "b"]})))

# ======================================================================
# 6. 状态标签与颜色
# ======================================================================
print("\n=== 6. 状态标签 ===")
for st, label in (("navigating", "寻路中"), ("arrived", "已到达"),
                  ("escaping", "脱困中"), ("stuck", "卡住"),
                  ("panic", "已急停"), ("paused", "已暂停"),
                  ("dry_run", "空跑")):
    m = build_panel(_Snap(), nav=nav_doc(state=st))
    got = m.lines[0].text
    check(f"{st} -> 「{label}」", label in got, repr(got))
check("未知状态原样显示（不崩）",
      "XYZ" in build_panel(_Snap(), nav=nav_doc(state="XYZ")).lines[0].text)
check("state 缺失 -> 「未知」",
      "未知" in build_panel(_Snap(), nav=nav_doc(state="")).lines[0].text)

# panic 状态应该让边框变红
m = build_panel(_Snap(), nav=nav_doc(state="panic"))
check("state=panic -> PanelModel.panic=True（边框闪红）", m.panic is True)
m = build_panel(_Snap(), nav=nav_doc(state="navigating"))
check("state=navigating -> panic=False", m.panic is False)

# ======================================================================
# 7. next_action 只含类型，不含数字（省电）
# ======================================================================
print("\n=== 7. next_action 不含数字 ===")
m1 = build_panel(_Snap(), nav=nav_doc(next_action="右转"))
m2 = build_panel(_Snap(), nav=nav_doc(next_action="右转"))
check("next_action 相同 -> 指纹相同", m1.content_key() == m2.content_key())
# ★ 只改 delta_deg，**其余字段全同**（含 next_action）——
#   delta_deg 刻意不进显示，所以指纹必须不变。
#   （本文件第一版写成「只改 delta_deg 但沿用默认 next_action」，
#     那条其实也该通过；真正的原因是断言对象用错了 _Snap 组合。）
_m_a = build_panel(_Snap(), nav=nav_doc(delta_deg=1.3, next_action="右转"))
_m_b = build_panel(_Snap(), nav=nav_doc(delta_deg=45.0, next_action="右转"))
check("★只变 delta_deg -> 指纹不变（不显示内部状态）",
      _m_a.content_key() == _m_b.content_key())
# v2：第 3 行显示的是 hint。两条路径都要覆盖 ——
#   正常路径（写方给了 hint）与兜底路径（hint 缺失，退回 next_action）。
for hint, exp in (("接近目标 · 还有 20 米", "接近目标 · 还有 20 米"),
                  ("右转中 · 约 5 秒后到达", "右转中 · 约 5 秒后到达"),
                  ("已到达", "已到达")):
    m = build_panel(_Snap(), nav=nav_doc(hint=hint))
    check(f"hint 直出：{hint}", m.lines[2].text == f"下一步: {exp}",
          repr(m.lines[2].text))
for act in ("直行", "右转", "左转", "脱困", "已到达", "暂停"):
    m = build_panel(_Snap(), nav=nav_doc(next_action=act, hint=""))
    check(f"hint 缺失 -> 退回 next_action「{act}」",
          m.lines[2].text == f"下一步: {act}", repr(m.lines[2].text))
check("hint 与 next_action 都缺 -> —",
      build_panel(_Snap(), nav={}).lines[2].text == "下一步: —")

# ======================================================================
# 8. content_key 指纹逻辑不变（回归保护）
# ======================================================================
print("\n=== 8. content_key 回归 ===")
k1 = m_nav.content_key()
check("指纹是 tuple", isinstance(k1, tuple))
check("状态变化 -> 指纹变化",
      k1 != build_panel(_Snap(), nav=nav_doc(state="arrived")).content_key())
check("hint 变化 -> 指纹变化（v2 里第 3 行的渲染输入是 hint）",
      k1 != build_panel(_Snap(), nav=nav_doc(hint="右转中 · 约 5 秒后到达")).content_key())
# ★ v2 的一个**刻意**的行为：next_action 不再参与渲染，所以单独改它
#   不会改指纹。这不是漏掉了 —— 动作一变，写方就会重算 hint 并写出新的
#   hint（缓存键是 (phase, action)），指纹照样会变。这里只是记录
#   「overlay 不再从 next_action 取文本」这条事实。
check("next_action 单独变化 -> 指纹**不变**（它已不是渲染输入）",
      k1 == build_panel(_Snap(), nav=nav_doc(next_action="左转")).content_key())
check("目标名变化 -> 指纹变化",
      k1 != build_panel(_Snap(), nav=nav_doc(target_name="别的")).content_key())
check("离线/寻路切换 -> 指纹变化", k1 != m_off.content_key())

# ======================================================================
# 9. load_nav_state：时效 / 版本 / 损坏
# ======================================================================
print("\n=== 9. load_nav_state（用临时目录，不碰真文件）===")
import tempfile
tmpdir = Path(tempfile.mkdtemp(prefix="navstate_"))
_real = layout.NAV_STATE_FILE
layout.NAV_STATE_FILE = tmpdir / "pathfind_state.json"
try:
    check("文件不存在 -> None（正常：没在寻路）",
          load_nav_state() is None)

    layout.NAV_STATE_FILE.write_text(
        json.dumps(nav_doc(), ensure_ascii=False), encoding="utf-8")
    got = load_nav_state()
    check("文件存在且新鲜 -> 读到 dict", isinstance(got, dict), repr(got))
    check("读到 state 正确", got and got.get("state") == "navigating")

    # ★ 过期：mtime 改成 30 秒前
    old = time.time() - 30
    os.utime(layout.NAV_STATE_FILE, (old, old))
    check("★mtime 超 5s -> None（写进程死了）", load_nav_state() is None)
    check("调大 max_age_s 后又能读到",
          isinstance(load_nav_state(max_age_s=60.0), dict))

    # schema 版本不符
    layout.NAV_STATE_FILE.write_text(
        json.dumps(nav_doc(schema_version=999), ensure_ascii=False),
        encoding="utf-8")
    check("★schema 版本不符 -> None（不拿错字段渲染）",
          load_nav_state() is None)

    # 缺 schema_version
    layout.NAV_STATE_FILE.write_text(
        json.dumps({"state": "navigating"}), encoding="utf-8")
    check("缺 schema_version -> None", load_nav_state() is None)

    # 半截 JSON（写方正在 replace 前的瞬间）
    layout.NAV_STATE_FILE.write_text('{"state": "navig', encoding="utf-8")
    check("半截 JSON -> None（不抛）", load_nav_state() is None)

    # 顶层不是 dict
    layout.NAV_STATE_FILE.write_text("[1,2,3]", encoding="utf-8")
    check("顶层是数组 -> None", load_nav_state() is None)

    # 空文件
    layout.NAV_STATE_FILE.write_text("", encoding="utf-8")
    check("空文件 -> None", load_nav_state() is None)

    # 正常文件再确认一次（前面被各种脏数据覆盖了）
    layout.NAV_STATE_FILE.write_text(
        json.dumps(nav_doc(), ensure_ascii=False), encoding="utf-8")
    check("恢复正常后仍能读到", isinstance(load_nav_state(), dict))
finally:
    layout.NAV_STATE_FILE = _real
    try:
        for f in tmpdir.iterdir():
            f.unlink()
        tmpdir.rmdir()
    except OSError:
        pass

# ======================================================================
# 10. 与 app.py 的契约：控件数与行数必须一致
# ======================================================================
print("\n=== 10. 与 app.py 的契约 ===")
app_src = (PLUGIN_ROOT / "autopilot" / "ui" / "overlay" / "app.py").read_text(
    encoding="utf-8")
check("app._build_widgets 仍写死 range(5)（与 5 行契约一致）",
      "for i in range(5):" in app_src)
check("app._compute_metrics 仍是 5 正文 + 1 提示（* 6）",
      "self._line_h * 6" in app_src)
check("app.tick 传了 nav=", "nav=layout.load_nav_state()" in app_src)
check("★theme.py 未被改动（本轮不加行数常量）",
      "MAX_PANEL_LINES" not in
      (PLUGIN_ROOT / "autopilot" / "ui" / "overlay" / "theme.py")
      .read_text(encoding="utf-8"))

# ======================================================================
print()
print("=" * 74)
print(f"结果: {len(PASS)}/{len(PASS) + len(FAIL)} 通过")
if FAIL:
    print("  失败: " + ", ".join(FAIL))
print("=" * 74)
sys.exit(1 if FAIL else 0)
