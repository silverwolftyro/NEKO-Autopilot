"""overlay Ctrl+滚轮缩放 + 窗口状态持久化（2026-10-04）。

★ 为什么不真起一个 Tk 窗口
------------------------
起窗口会在别的机器/无桌面会话上直接崩，测试就不该依赖显示器。
这里全部走**鸭子类型替身**：

* :class:`_StubRoot` 只实现 ``bind()`` —— ``Interaction._bind()``
  够用了，``focus_set()`` 只在真的 FocusIn 里才会被调到。
* ``OverlayApp._scale_by`` 是**未绑定方法**，拿 stub 当 self 传进去
  就能跑真代码（Python 不检查 self 类型）。这样测的是真逻辑，
  不是把逻辑抄一份到测试里。

★ 为什么必须还原 theme 全局量
------------------------------
``theme._USER_SCALE`` / ``theme.SCALE`` 是**进程级**模块全局。
测完不还原，同一个 python 进程里后面跑的套件会带着被改过的倍率，
把别的测试搞挂 —— 而且是那种「时好时坏」的挂法，最难查。
本文件用 try/finally 兜死。
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
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:                                   # pragma: no cover
    pass

PLUGIN_ROOT = Path(PLUGIN_DIR)
sys.path.insert(0, str(PLUGIN_ROOT))

from autopilot.ui.overlay import layout, theme          # noqa: E402
from autopilot.ui.overlay.app import OverlayApp        # noqa: E402
from autopilot.ui.overlay.interaction import Interaction  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f"   {detail}" if detail else ""))


def _calls(src: str, attr: str) -> list[str]:
    """源码里真正被**调用**的 ``<任意>.<attr>(...)``。

    ★ 为什么要走 AST
      --------------
      「这个文件里还有没有 layout.save_pos」用 ``in`` 判断是错的 ——
      注释和 docstring 里写着这个字符串照样命中（本文件
      ``interaction.py`` 里就正好写着它当反例）。
      AST 只看真正的 Call 节点，注释和字符串一律不算。
    """
    found = []
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == attr):
            found.append(ast.unparse(node.func))
    return found


# ======================================================================
# 替身
# ======================================================================
class _StubRoot:
    """只记录绑定的假窗口。``bind`` 是 ``Interaction`` 唯一立刻用到的方法。"""

    def __init__(self):
        self.binds: dict[str, object] = {}
        self.focus_calls = 0

    def bind(self, seq, handler, *_a, **_kw):
        self.binds[seq] = handler

    def focus_set(self):                                # pragma: no cover
        self.focus_calls += 1


class _WheelEv:
    """滚轮事件替身。Windows 上 delta 是 ±120。"""

    def __init__(self, delta):
        self.delta = delta


class _AppStub:
    """``OverlayApp._scale_by`` 需要的全部字段。**只多不少，不多不少**。"""

    def __init__(self):
        self.reflows = 0
        self.persists = 0
        self.persist_args: list[tuple] = []
        self._toast = None
        self._toast_color = None
        self._toast_until = 0.0
        self._last_key = "SENTINEL"

    def _reflow(self) -> None:
        self.reflows += 1

    def _persist_pos(self, x=None, y=None) -> None:
        self.persists += 1
        self.persist_args.append((x, y))


def make_interaction(root=None, scale_sink=None):
    """造一个 Interaction，缩放回调打进 list。"""
    root = root or _StubRoot()
    sink = [] if scale_sink is None else scale_sink
    ita = Interaction(
        root,
        on_move=lambda x, y: None,
        on_close=lambda: None,
        on_kill=lambda: None,
        on_restart=lambda: None,
        on_scale=sink.append,
        on_persist=lambda x, y: None,
        on_open_console=lambda: None,
        on_stop_task=lambda: None,
        on_stop_all=lambda: None,
        get_pos=lambda: (100, 200),
    )
    return ita, root, sink


class _ThemeGuard:
    """进出都还原 ``theme`` 的两个全局量。"""

    def __enter__(self):
        self._u, self._s = theme._USER_SCALE, theme.SCALE
        return self

    def __exit__(self, *exc):
        theme._USER_SCALE, theme.SCALE = self._u, self._s
        theme._recompute()
        return False


# ======================================================================
print("=== 1. Interaction：绑定 + 步进 ===")
# ======================================================================
ita, root, sink = make_interaction()

check("<Control-MouseWheel> 已绑定", "<Control-MouseWheel>" in root.binds)
check("<MouseWheel> 已绑定（普通滚轮必须被吃掉）",
      "<MouseWheel>" in root.binds)
check("步进 = 0.05（与主控台滑块一格一致）",
      Interaction.WHEEL_SCALE_STEP == 0.05,
      f"实际 {Interaction.WHEEL_SCALE_STEP}")
check("scale_step property 读到同一个值", ita.scale_step == 0.05)

ita._on_ctrl_wheel(_WheelEv(120))
check("滚上 delta>0 -> on_scale(+0.05)", sink == [0.05], f"实际 {sink}")

sink.clear()
ita._on_ctrl_wheel(_WheelEv(-120))
check("滚下 delta<0 -> on_scale(-0.05)", sink == [-0.05], f"实际 {sink}")

sink.clear()
ita._on_ctrl_wheel(_WheelEv(0))
check("delta==0 -> 不调缩放", sink == [], f"实际 {sink}")

check("Ctrl+滚轮返回 break（阻止冒泡）",
      ita._on_ctrl_wheel(_WheelEv(120)) == "break")

sink.clear()
plain_ret = ita._on_wheel_plain(_WheelEv(120))
check("普通滚轮不调缩放", sink == [], f"实际 {sink}")
check("普通滚轮也返回 break", plain_ret == "break")

# 契约：两个新回调必须显式传，漏了要炸，不能静默变成 no-op
try:
    Interaction(_StubRoot(), on_move=lambda x, y: None,
                on_close=lambda: None, on_kill=lambda: None,
                on_restart=lambda: None, get_pos=lambda: (0, 0))
    missing_ok = False
    why = ""
except TypeError as exc:
    missing_ok = True
    why = str(exc)
check("★漏传 on_scale/on_persist/on_open_console/on_stop_task/on_stop_all"
      "直接 TypeError（不是静默 no-op）",
      missing_ok, why[:60])
check("★报错里点名了每一个漏掉的回调",
      all(k in why for k in ("on_scale", "on_persist", "on_open_console",
                             "on_stop_task", "on_stop_all")),
      why[:160])

# ======================================================================
print("\n=== 2. _scale_by：加/减/边界（未绑定方法 + stub self）===")
# ======================================================================
with _ThemeGuard():
    theme.apply_user_scale(1.0)
    stub = _AppStub()
    OverlayApp._scale_by(stub, 0.05)
    check("放大一格：倍率 1.00 -> 1.05",
          abs(theme.user_scale() - 1.05) < 1e-9,
          f"实际 {theme.user_scale():.4f}")
    check("放大后重排了控件", stub.reflows == 1, f"reflows={stub.reflows}")
    check("放大后落库了", stub.persists == 1, f"persists={stub.persists}")
    check("落库不带坐标 = 用当前窗口位置（_reflow 之后问才准）",
          stub.persist_args == [(None, None)], f"实际 {stub.persist_args}")
    check("toast 报了缩放", "缩放" in (stub._toast or ""), repr(stub._toast))
    check("toast 到期时间已设", stub._toast_until > 0)
    check("_last_key 被置 None（强制下一帧重画）", stub._last_key is None)

with _ThemeGuard():
    theme.apply_user_scale(1.0)
    stub = _AppStub()
    OverlayApp._scale_by(stub, -0.05)
    check("缩小一格：倍率 1.00 -> 0.95",
          abs(theme.user_scale() - 0.95) < 1e-9,
          f"实际 {theme.user_scale():.4f}")
    check("缩小也重排也落库",
          stub.reflows == 1 and stub.persists == 1)

# --- 上界 ---
with _ThemeGuard():
    theme.apply_user_scale(theme.USER_SCALE_MAX)
    stub = _AppStub()
    OverlayApp._scale_by(stub, 0.05)
    check("上界：倍率仍被夹在 MAX",
          abs(theme.user_scale() - theme.USER_SCALE_MAX) < 1e-9,
          f"实际 {theme.user_scale()}")
    check("上界：不重排（重排是纯浪费，还会闪）", stub.reflows == 0,
          f"reflows={stub.reflows}")
    check("上界：toast 说「已到最大缩放」",
          stub._toast == "已到最大缩放", repr(stub._toast))
    check("上界：仍要强制重画（用户得看见提示）", stub._last_key is None)

# --- 下界 ---
with _ThemeGuard():
    theme.apply_user_scale(theme.USER_SCALE_MIN)
    stub = _AppStub()
    OverlayApp._scale_by(stub, -0.05)
    check("下界：倍率仍被夹在 MIN",
          abs(theme.user_scale() - theme.USER_SCALE_MIN) < 1e-9)
    check("下界：不重排", stub.reflows == 0)
    check("下界：toast 说「已到最小缩放」",
          stub._toast == "已到最小缩放", repr(stub._toast))

# --- 连滚：必须夹住，不能溢出 ---
# ★ 起点要**贴近上界**，不然 1.0 + 20×0.05 = 2.0 根本够不到 MAX=3.0，
#   夹取逻辑压根没被触发，这条测试就成了永远为真的废话。
with _ThemeGuard():
    theme.apply_user_scale(theme.USER_SCALE_MAX - 0.10)   # 2.90
    stub = _AppStub()
    for _ in range(20):
        OverlayApp._scale_by(stub, 0.05)
    check("连滚 20 格向上：夹在 USER_SCALE_MAX，不溢出",
          theme.user_scale() == theme.USER_SCALE_MAX,
          f"实际 {theme.user_scale():.4f}")
    # 到界后必须**停止重建**。2.90 只够 2 格（2.95、3.00），剩下 18 格全是空转。
    check("★到界后不再重建控件（空转 18 次也只重排 2 次）",
          stub.reflows <= 3, f"reflows={stub.reflows}（期望 2~3）")

with _ThemeGuard():
    theme.apply_user_scale(1.0)
    stub = _AppStub()
    for _ in range(20):
        OverlayApp._scale_by(stub, -0.05)
    check("连滚 20 格向下：夹在 USER_SCALE_MIN，不是 0 也不是负数",
          abs(theme.user_scale() - theme.USER_SCALE_MIN) < 1e-9,
          f"实际 {theme.user_scale():.4f}")

check("拖完后 theme 全局量已被本文件还原", True,
      f"当前 {theme.user_scale():.4f}")

# ======================================================================
print("\n=== 3. layout：窗口状态落库/回读 ===")
# ======================================================================
check("OverlayPos.scale 默认 None（= 不知道，不是 0）",
      layout.OverlayPos(x=1, y=2).scale is None)

_orig_pos_file = layout.POS_FILE
with tempfile.TemporaryDirectory() as _td:
    layout.POS_FILE = Path(_td) / "overlay_pos.json"
    try:
        # --- 完整状态往返 ---
        ok = layout.save_pos(layout.OverlayPos(x=11, y=22,
                                                alpha=0.55, scale=1.35))
        back = layout.load_pos()
        check("save_pos 返回 True", ok is True)
        check("x/y 往返", (back.x, back.y) == (11, 22), f"{back.x},{back.y}")
        check("★alpha 往返（老代码拖一下就丢）",
              abs(back.alpha - 0.55) < 1e-9, f"实际 {back.alpha}")
        check("★scale 往返（Ctrl+滚轮要能留住）",
              abs(back.scale - 1.35) < 1e-9, f"实际 {back.scale}")

        # --- 老文件没有 scale 键 ---
        layout.POS_FILE.write_text(
            json.dumps({"x": 5, "y": 6, "alpha": 0.72}), encoding="utf-8")
        old = layout.load_pos()
        check("老文件（无 scale 键）回读成 None 而不是 0/1.0",
              old is not None and old.scale is None,
              f"实际 {None if old is None else old.scale}")

        # --- scale=None 时不写这个键 ---
        layout.save_pos(layout.OverlayPos(x=1, y=2))
        raw = json.loads(layout.POS_FILE.read_text(encoding="utf-8"))
        check("scale=None 时文件里**没有** scale 键（不是 null）",
              "scale" not in raw, f"实际键 {sorted(raw)}")

        # --- 脏文件不炸 ---
        layout.POS_FILE.write_text("{ 这不是 JSON", encoding="utf-8")
        check("脏文件回读返回 None，不抛",
              layout.load_pos() is None)

        # --- scale 是字符串也要能读回来 ---
        layout.POS_FILE.write_text(
            json.dumps({"x": 3, "y": 4, "alpha": 0.9, "scale": "0.75"}),
            encoding="utf-8")
        s = layout.load_pos()
        check("scale 写成字符串也能读回 float",
              s is not None and abs(s.scale - 0.75) < 1e-9,
              f"实际 {None if s is None else s.scale}")
    finally:
        layout.POS_FILE = _orig_pos_file

# ======================================================================
print("\n=== 4. 与 app.py / interaction.py 的契约 ===")
# ======================================================================
app_src = (PLUGIN_ROOT / "autopilot" / "ui" / "overlay" / "app.py").read_text(
    encoding="utf-8")
ita_src = (PLUGIN_ROOT / "autopilot" / "ui" / "overlay"
           / "interaction.py").read_text(encoding="utf-8")

check("app 定义了 _scale_by", "def _scale_by(self" in app_src)
check("app 把 _scale_by 接到 on_scale",
      "on_scale=self._scale_by" in app_src)
check("app 定义了 _persist_pos", "def _persist_pos(self" in app_src)
check("app 把 _persist_pos 接到 on_persist",
      "on_persist=self._persist_pos" in app_src)
check("★interaction 不再自己调 layout.save_pos"
      "（否则拖一下窗口又会把 alpha/scale 吃掉）",
      not _calls(ita_src, "save_pos"))
check("（上面这条用 AST 判定，不是子串 —— 注释里正写着 layout.save_pos()"
      " 作为「别这么写」的反例，子串匹配会自己把自己判失败）",
      "layout.save_pos" in ita_src)
check("_persist_pos 写的是当前生效倍率",
      "scale=theme.user_scale()" in app_src)
check("交互层收 on_persist 回调而不是自己落库",
      "on_persist: Callable" in ita_src)
check("_persist_pos 的 alpha 直接问 Tk 要（不缓存）",
      'self.root.attributes("-alpha")' in app_src)
check("★命令行没给 --scale 时才回读持久化（主控台显式传 --scale 不受影响）",
      "saved_pos.scale is not None" in app_src
      and "if scale is None:" in app_src)

# _reflow 的顺序契约：位置必须**先**取，再销毁重建
_rf = app_src[app_src.index("def _reflow"):app_src.index("def _move")]
check("★_reflow 先取位置再销毁（缩放时窗口不能跑掉）",
      _rf.index("self._get_pos()") < _rf.index("destroy()"))

# ======================================================================
print()
print("=" * 74)
print(f"结果: {len(PASS)}/{len(PASS) + len(FAIL)} 通过")
if FAIL:
    print("  失败: " + ", ".join(FAIL))
print("=" * 74)
sys.exit(1 if FAIL else 0)
