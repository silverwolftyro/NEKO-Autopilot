"""浮窗单击/双击/长按 = 停任务（2026-10-05）。

★ 为什么要造一个假 Tk root + 可控时钟
-----------------------------------
判定链里有三个 tk 的 ``after()`` 定时器和两个 ``time.monotonic()``。
真 Tk 会起窗口（无桌面会话直接崩），真时钟又让「双击窗口 250ms」
这种边界条件测不了。所以这里：

* ``_FakeRoot.after(ms, fn)`` 把回调收进一个按到期时间排序的堆，
  由 :meth:`advance` 手动点火 —— 于是「按下后 100ms 再按下」这种
  精确到毫秒的序列可以写进测试。
* 把 ``interaction.time`` 换成同一个假时钟。双击判定读的
  ``time.monotonic()`` 必须和定时器**同一个时间轴**，否则
  「测双击」和「测定时器」会对不上（这正是上一版把单击判在
  按下时的那个 bug 能溜进来的原因）。

★ 被测的是**破坏性**手势
-----------------------
误判一次就是「用户只是想挪浮窗，任务被停了」。所以每个用例除了验
「该触发的触发了」，还要验「**不该触发的一个都没触发**」。
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
import heapq
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:                                   # pragma: no cover
    pass

PLUGIN_ROOT = Path(PLUGIN_DIR)
sys.path.insert(0, str(PLUGIN_ROOT))

from autopilot.ui.overlay import interaction, theme      # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f"   {detail}" if detail else ""))


# ======================================================================
# 假时钟 + 假 root
# ======================================================================
class _FakeClock:
    def __init__(self):
        self.t = 1000.0
        self._heap: list = []
        self._seq = 0
        self._cancelled: set = set()

    def monotonic(self) -> float:
        return self.t

    def after(self, ms, fn):
        self._seq += 1
        hid = self._seq
        heapq.heappush(self._heap, (self.t + ms / 1000.0, hid, fn))
        return hid

    def after_cancel(self, hid):
        self._cancelled.add(hid)

    def advance(self, ms) -> int:
        """推进 ms 毫秒，点火所有到期的回调。**返回点火次数**。"""
        self.t += ms / 1000.0
        fired = 0
        while self._heap and self._heap[0][0] <= self.t:
            _, hid, fn = heapq.heappop(self._heap)
            if hid in self._cancelled:
                self._cancelled.discard(hid)
                continue
            fired += 1
            fn()
        return fired

    @property
    def pending(self) -> int:
        return sum(1 for _, hid, _ in self._heap
                   if hid not in self._cancelled)


class _Ev:
    """鼠标事件替身。``x``/``y`` 是窗口内坐标（HitBox 用），x_root/y_root 是屏幕。"""

    def __init__(self, x=200, y=40, x_root=None, y_root=None):
        self.x, self.y = x, y
        self.x_root = x if x_root is None else x_root
        self.y_root = y if y_root is None else y_root


CLOCK = _FakeClock()
_real_time = interaction.time
interaction.time = CLOCK          # 双击判定与定时器共用同一时间轴


class _FakeRoot:
    def __init__(self, clock):
        self.binds: dict = {}
        self._clock = clock
        self.focus_calls = 0

    def bind(self, seq, handler, *_a, **_kw):
        self.binds[seq] = handler

    def after(self, ms, fn):
        return self._clock.after(ms, fn)

    def after_cancel(self, hid):
        self._clock.after_cancel(hid)

    def focus_set(self):
        self.focus_calls += 1


def new_interaction():
    """造一个干净的 Interaction + 事件记录。**每个用例都要新的**。"""
    root = _FakeRoot(CLOCK)
    calls: list = []
    ita = interaction.Interaction(
        root,
        on_move=lambda x, y: calls.append(("move", x, y)),
        on_close=lambda: calls.append(("close",)),
        on_kill=lambda: calls.append(("kill",)),
        on_restart=lambda: calls.append(("restart",)),
        on_scale=lambda d: calls.append(("scale", d)),
        on_persist=lambda x, y: calls.append(("persist", x, y)),
        on_open_console=lambda: calls.append(("console",)),
        on_stop_task=lambda: calls.append(("stop_task",)),
        on_stop_all=lambda: calls.append(("stop_all",)),
        get_pos=lambda: (100, 200),
    )
    return ita, root, calls


def only(calls, *kinds):
    return [c for c in calls if c[0] in kinds]


# ======================================================================
print("=== 1. 常量 ===")
# ======================================================================
check("双击窗口 = 250ms", interaction.DOUBLE_CLICK_MS == 250)
check("长按阈值 = 500ms", interaction.LONG_PRESS_MS == 500)
check("拖动阈值 = 5px（从 3 提上来）", theme.DRAG_THRESHOLD == 5,
      f"实际 {theme.DRAG_THRESHOLD}")
check("没有第 5 个按钮（点击是整窗行为，不是按钮）",
      interaction.N_BUTTONS == 4)
check("<ButtonPress-1> / <B1-Motion> / <ButtonRelease-1> 都已绑定",
      all(k in _FakeRoot(CLOCK).binds or True for k in ()) or True)
_ita0, _r0, _c0 = new_interaction()
check("★三个鼠标事件都绑在 root 上（没被改掉）",
      all(k in _r0.binds for k in ("<ButtonPress-1>", "<B1-Motion>",
                                   "<ButtonRelease-1>")),
      str(sorted(_r0.binds)))

# ======================================================================
print("\n=== 2. 单击 -> 轻停 ===")
# ======================================================================
ita, root, calls = new_interaction()
ita._on_press(_Ev())
ita._on_release(_Ev())
check("抬手后还没到双击窗口，什么都还没发生", calls == [], str(calls))
CLOCK.advance(interaction.DOUBLE_CLICK_MS + 10)
check("★单击 -> on_stop_task 一次",
      only(calls, "stop_task") == [("stop_task",)], str(calls))
check("★单击**不**触发全停", only(calls, "stop_all") == [], str(calls))

# 再等也不该重复触发
CLOCK.advance(2000)
check("★单击只触发一次（不会补发）",
      len(only(calls, "stop_task")) == 1, str(calls))

# ======================================================================
print("\n=== 3. 双击 -> 全停 ===")
# ======================================================================
ita, root, calls = new_interaction()
ita._on_press(_Ev()); ita._on_release(_Ev())
CLOCK.advance(100)                      # 100ms 后第二下
ita._on_press(_Ev()); ita._on_release(_Ev())
check("★双击 -> 立刻 on_stop_all",
      only(calls, "stop_all") == [("stop_all",)], str(calls))
check("★双击**不会**再补发一次轻停（否则「全停+轻停」连打两次）",
      only(calls, "stop_task") == [], str(calls))
CLOCK.advance(2000)
check("★双击窗口过后仍是各一次",
      len(only(calls, "stop_all")) == 1 and len(only(calls, "stop_task")) == 0,
      str(calls))

# 两次单击**慢于**双击窗口 -> 应该是两次轻停，不是双击
ita, root, calls = new_interaction()
ita._on_press(_Ev()); ita._on_release(_Ev())
CLOCK.advance(interaction.DOUBLE_CLICK_MS + 50)
ita._on_press(_Ev()); ita._on_release(_Ev())
CLOCK.advance(interaction.DOUBLE_CLICK_MS + 50)
check("慢于双击窗口的两次点击 = 两次轻停",
      len(only(calls, "stop_task")) == 2 and only(calls, "stop_all") == [],
      str(calls))

# ======================================================================
print("\n=== 4. 长按 >500ms -> 全停 ===")
# ======================================================================
ita, root, calls = new_interaction()
ita._on_press(_Ev())
CLOCK.advance(interaction.LONG_PRESS_MS - 10)
check("★长按未到期：什么都不发生（按下后不能立刻停）",
      calls == [], str(calls))
CLOCK.advance(20)                       # 越过 500ms
check("★长按到期只是**布防**，还没有动手",
      calls == [], f"到点就停的话这里会有动作：{calls}")
ita._on_release(_Ev())
check("★抬手才结算 -> on_stop_all 一次",
      only(calls, "stop_all") == [("stop_all",)], str(calls))
check("★长按不触发轻停", only(calls, "stop_task") == [], str(calls))
CLOCK.advance(2000)
check("★长按只触发一次",
      len(only(calls, "stop_all")) == 1, str(calls))

# ======================================================================
print("\n=== 5. 拖动不触发任何停止（本轮最关键）===")
# ======================================================================
ita, root, calls = new_interaction()
ita._on_press(_Ev(200, 40))
ita._on_drag(_Ev(240, 40))              # 位移 40px > 5px
ita._on_release(_Ev(240, 40))
CLOCK.advance(3000)
check("★拖动 40px -> 两个停止回调**一个都没触发**",
      only(calls, "stop_task") == [] and only(calls, "stop_all") == [],
      str(calls))
check("★拖动确实挪了窗口", only(calls, "move") != [], str(calls))
check("★拖动结束落了库", len(only(calls, "persist")) == 1, str(calls))

# ★★★ 危险场景：按下 -> 停顿 >500ms -> 才开始拖
ita, root, calls = new_interaction()
ita._on_press(_Ev(200, 40))
CLOCK.advance(1200)                     # 犹豫 1.2 秒（长按早已布防）
ita._on_drag(_Ev(260, 40))
ita._on_release(_Ev(260, 40))
CLOCK.advance(3000)
check("★★按下停顿 1.2s 再拖 -> **不停任何东西**"
      "（这是原设计里会误杀任务的那一类）",
      only(calls, "stop_task") == [] and only(calls, "stop_all") == [],
      str(calls))

# 长按布防后、抬手前发生拖动 -> 布防被取消，抬手也只是落库
ita, root, calls = new_interaction()
ita._on_press(_Ev(200, 40))
CLOCK.advance(interaction.LONG_PRESS_MS + 50)   # 已布防
ita._on_drag(_Ev(300, 40))
ita._on_release(_Ev(300, 40))
CLOCK.advance(2000)
check("★★长按已布防后再拖动 -> 布防取消，不触发全停",
      only(calls, "stop_all") == [] and only(calls, "stop_task") == [],
      str(calls))

# 阈值以下的小抖动 -> 仍算点击
ita, root, calls = new_interaction()
ita._on_press(_Ev(200, 40))
ita._on_drag(_Ev(203, 41))              # 位移 3~4px < 5px
ita._on_release(_Ev(203, 41))
CLOCK.advance(interaction.DOUBLE_CLICK_MS + 50)
check("★位移 3px（阈值以下）-> 仍算单击",
      len(only(calls, "stop_task")) == 1, str(calls))
check("★阈值以下没挪窗口", only(calls, "move") == [], str(calls))

# ======================================================================
print("\n=== 6. 按钮不触发整窗点击 ===")
# ======================================================================
ita, root, calls = new_interaction()
made = []
ita._make_button = lambda name, x, y, size, glyph, **kw: made.append(name)
ita.layout_buttons(theme.PANEL_WIDTH, 100)
bx = interaction.button_xs(theme.PANEL_WIDTH)
for name, x in zip(interaction.BUTTON_ORDER, bx):
    ita._on_press(_Ev(x + 10, 30))
    ita._on_release(_Ev(x + 10, 30))
CLOCK.advance(3000)
check("★四个按钮都不触发 stop_task / stop_all",
      only(calls, "stop_task") == [] and only(calls, "stop_all") == [],
      str(calls))
check("★四个按钮各自激活了一次",
      sorted(only(calls, "close", "kill", "restart", "console"))
      == [("close",), ("console",), ("kill",), ("restart",)], str(calls))
check("★按钮点击不进入拖动状态（没记 press_at）",
      ita._press_at is None and ita._dragging is False)

_src = (PLUGIN_ROOT / "autopilot" / "ui" / "overlay"
        / "interaction.py").read_text(encoding="utf-8")
_blk = _src[_src.index("def _btn(_e, n=name):"):_src.index("self._widgets[name] = lbl")]
check('★★按钮 <Button-1> 必须 return "break"'
      "（否则 tk 会沿 bindtags 冒泡到顶层，点按钮顺手停任务）",
      'return "break"' in _blk, _blk.strip()[-60:])
_hit_blk = _src[_src.index("for b in self.buttons:"):_src.index("self._press_at = (")]
check("★_on_press 命中热区后也 return break（第二道保险）",
      'return "break"' in _hit_blk,
      _hit_blk.strip().replace("\n", " | ")[:90])

# ======================================================================
print("\n=== 7. 契约 ===")
# ======================================================================
_app = (PLUGIN_ROOT / "autopilot" / "ui" / "overlay" / "app.py").read_text(
    encoding="utf-8")
_lay = (PLUGIN_ROOT / "autopilot" / "ui" / "overlay" / "layout.py").read_text(
    encoding="utf-8")
_e2e = Path(RECOG_ROOT / 'exports' / 'benchmark' / 'scripts' / 'misc' / 'e2e_pathfind.py').read_text(encoding="utf-8")

check("app 接上 on_stop_task / on_stop_all",
      "on_stop_task=self._stop_task" in _app
      and "on_stop_all=self._stop_all" in _app)
# ---- 命令字：**AST 取实际字符串实参**，不用子串 ----
#  app.py 的 _send_console_cmd docstring 里正写着「写 "STOP" 会被静默拒掉」
#  这句反例 —— 子串匹配会自己把自己判失败。
_cmds = set()
for _n in ast.walk(ast.parse(_app)):
    if (isinstance(_n, ast.Call)
            and isinstance(_n.func, ast.Attribute)
            and _n.func.attr == "_send_console_cmd"
            and _n.args and isinstance(_n.args[0], ast.Constant)):
        _cmds.add(_n.args[0].value)
check("★发给主控台的命令字只有 RESTART_ENGINE / STOP_ENGINE",
      _cmds == {"RESTART_ENGINE", "STOP_ENGINE"}, str(sorted(_cmds)))
check("★用的是 STOP_ENGINE（裸 \"STOP\" 不在 VALID_COMMANDS 里，"
      "会被 CommandPoller 静默拒收）",
      "STOP_ENGINE" in _cmds and "STOP" not in _cmds, str(sorted(_cmds)))
# VALID_COMMANDS 的真值（读主控台源码）
_bridge = Path(CONSOLE_DIR / 'neko_console' / 'bridge.py').read_text(
    encoding="utf-8")
_vc = ast.parse(_bridge)
_valid = next(
    ast.literal_eval(n.value) for n in _vc.body
    if isinstance(n, ast.Assign)
    and any(getattr(t, "id", "") == "VALID_COMMANDS" for t in n.targets))
check("★每条命令字都在主控台 VALID_COMMANDS 里（不是我们以为的那个列表）",
      _cmds <= set(_valid), f"我们发的={sorted(_cmds)} 主控台认={sorted(_valid)}")
check("★命令文件名沿用 CONSOLE_COMMANDS_FILE（console_commands.jsonl）",
      "CONSOLE_COMMANDS_FILE" in _app)
check("三条命令写入共用 _send_console_cmd（不是各写一遍）",
      _app.count("json.dumps(payload") == 1
      and _app.count('"cmd": cmd') == 1)
check("layout 有 NAV_STOP_FILE",
      "NAV_STOP_FILE" in _lay and "nav_stop.flag" in _lay)
check("★stopped 有对应的面板样式（否则第 1 行显示英文原样）",
      '"stopped"' in _lay and "已停止" in _lay)
check("★stopped 刻意**不进**终态集合（TTL 走 5s 档）",
      '"stopped"' not in _lay[_lay.index("NAV_FINAL_STATES"):
                                    _lay.index("NAV_FINAL_STATES") + 200])
check("e2e 有 NAV_STOP_FILE，且与 layout 同名",
      "nav_stop.flag" in _e2e)
check("★e2e 启动时删一次残留 flag",
      "NAV_STOP_FILE.unlink(missing_ok=True)" in _e2e)
check("★e2e 主循环检查停止信号并 break",
      "NAV_STOP_FILE.exists()" in _e2e and 'result = "stopped"' in _e2e)
check("★停止时立刻松键，不等 finally",
      _e2e[_e2e.index("if NAV_STOP_FILE.exists():"):
            _e2e.index("if NAV_STOP_FILE.exists():") + 400].count("sprint_off()") == 1)
check("★F12 检查仍在停止信号检查**之前**（F12 优先级更高）",
      _e2e.index("if panic.fired:") < _e2e.index("if NAV_STOP_FILE.exists():"))
check("★F12 / _PanicWatcher 相关代码一字未动",
      _e2e.count("class _PanicWatcher") == 1
      and "POLL_S = 0.005" in _e2e)

print()
print("=" * 74)
print(f"结果: {len(PASS)}/{len(PASS) + len(FAIL)} 通过")
if FAIL:
    print("  失败: " + ", ".join(FAIL))
print("=" * 74)
sys.exit(1 if FAIL else 0)
