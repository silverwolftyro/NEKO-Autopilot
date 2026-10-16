"""「打开主控台」：命名管道唤起 + 启动 + 第 4 个按钮（2026-10-05）。

★ 为什么全套 mock 掉 kernel32
---------------------------
``_ipc_show_console`` 真跑起来会 ``CreateFileW`` 一条**真实**的命名管道。
在本机上这条管道多半不存在（主控台没开），于是「成功路径」根本测不到 ——
而成功路径恰恰是唯一有逻辑的那条（短写判失败、CloseHandle 一定要调用）。
所以这里换成一个假 kernel32，把四种返回组合都走一遍。

★ 为什么按钮测试不起 Tk 窗口
---------------------------
``layout_buttons`` 会 ``tk.Label(...)``。无桌面会话上直接崩，测试不该
依赖显示器。做法是把 :meth:`Interaction._make_button` 换成记录器 ——
这样验的是**几何与分发**，控件创建留给真机。
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
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:                                   # pragma: no cover
    pass

PLUGIN_ROOT = Path(PLUGIN_DIR)
sys.path.insert(0, str(PLUGIN_ROOT))

from autopilot.ui.overlay import console_launcher as cl   # noqa: E402
from autopilot.ui.overlay import interaction, theme        # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f"   {detail}" if detail else ""))


INVALID_HANDLE = 0xFFFFFFFFFFFFFFFF        # wintypes.HANDLE(-1).value (64 位)


class _FakeKernel32:
    """假 kernel32。四种组合：句柄无效 / 写成功 / 短写 / 写失败。"""

    def __init__(self, handle=42, write_ok=True, written=None):
        self._handle = handle
        self._write_ok = write_ok
        self._written = written
        self.opened = []
        self.writes = []
        self.closed = []

    def CreateFileW(self, name, access, share, sec, disp, flags, templ):
        self.opened.append(name)
        return self._handle

    def WriteFile(self, handle, data, n, written_ptr, overlapped):
        self.writes.append((bytes(data), n))
        written_ptr._obj.value = (self._written if self._written is not None
                                  else n)
        return self._write_ok

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1


def _with_kernel(fake, fn):
    """临时把 _kernel32 换成假实现，跑 fn，最后还原。"""
    orig = cl._kernel32
    cl._kernel32 = lambda: fake
    try:
        return fn()
    finally:
        cl._kernel32 = orig


# ======================================================================
print("=== 1. 管道唤起 _ipc_show_console ===")
# ======================================================================
check("管道名与 Qt 服务名对得上（app.py:54 的 NekoConsole.IPC）",
      cl.CONSOLE_IPC_SERVER_NAME == "NekoConsole.IPC"
      and cl.CONSOLE_IPC_PIPE == r"\\.\pipe\NekoConsole.IPC",
      cl.CONSOLE_IPC_PIPE)
check("命令字是 SHOW（app.py:57 的 CMD_SHOW）",
      cl.CONSOLE_IPC_COMMAND == "SHOW")

k = _FakeKernel32(handle=INVALID_HANDLE)
check("★句柄无效（=主控台没跑）-> False",
      _with_kernel(k, cl._ipc_show_console) is False)
check("★无效句柄时**不**去 WriteFile（没句柄可写）",
      k.writes == [], f"实际 {k.writes}")
check("无效句柄时不 CloseHandle（本来就没打开）", k.closed == [])

k = _FakeKernel32(handle=42)
check("写入成功 -> True", _with_kernel(k, cl._ipc_show_console) is True)
check("★打开的是命名管道，不是别的",
      k.opened == [r"\\.\pipe\NekoConsole.IPC"], f"实际 {k.opened}")
check("★写的是 ASCII 的 SHOW",
      k.writes == [(b"SHOW", 4)], f"实际 {k.writes}")
check("★成功时 CloseHandle 一定被调用（否则句柄泄漏）",
      k.closed == [42], f"实际 {k.closed}")

k = _FakeKernel32(handle=42, written=2)      # 短写：只写了 "SH"
check("★短写 -> False（Qt 那边 strip().upper() 比整条，半条会被默默丢掉）",
      _with_kernel(k, cl._ipc_show_console) is False)
check("★短写时 CloseHandle **仍然**被调用",
      k.closed == [42], f"实际 {k.closed}")

k = _FakeKernel32(handle=42, write_ok=False)
check("WriteFile 返回 0 -> False",
      _with_kernel(k, cl._ipc_show_console) is False)
check("写失败时 CloseHandle 仍然被调用", k.closed == [42])

check("★kernel32 拿不到（非 Windows）-> False 而不是崩",
      _with_kernel(None, cl._ipc_show_console) is False)


def _boom():
    def _raise(*_a, **_kw):
        raise OSError("管道炸了")
    return _raise


orig_k = cl._kernel32
cl._kernel32 = _boom()          # ★ 赋「返回的假 kernel32」，不是工厂本身
try:
    _raised = False
    try:
        cl._ipc_show_console()
    except OSError:
        _raised = True
    # ★ 低层函数**不吞**异常：CreateFileW 炸了跟「主控台没在跑」是两回事，
    #   吞掉就再也分不出「没跑」和「出故障」了。兜底是 launch_console 的事。
    check("★CreateFileW 抛异常时 _ipc_show_console **不吞**（如实上抛）",
          _raised)
    # launch_console 侧的容错见第 3 节「管道抛异常也不阻断降级到启动」。
finally:
    cl._kernel32 = orig_k

# ======================================================================
print("\n=== 2. 启动 _spawn_console / _console_exe ===")
# ======================================================================
check("环境变量名是 NEKO_CONSOLE_EXE", cl.CONSOLE_EXE_ENV == "NEKO_CONSOLE_EXE")
# ★ 原来这里断言兜底路径等于某个**开发机绝对路径**，换台机器就不成立。
#   现在只断言形状：兜底必须指向 <某个 console 目录>/build/Console.exe。
check("兜底路径是 build\\Console.exe",
      str(cl.CONSOLE_EXE_FALLBACK).replace("/", "\\").endswith(
          "\\build\\Console.exe"),
      str(cl.CONSOLE_EXE_FALLBACK))

_saved_env = os.environ.get(cl.CONSOLE_EXE_ENV)
_saved_fb = cl.CONSOLE_EXE_FALLBACK
_saved_popen = cl.subprocess.Popen
_popen_calls = []


def _fake_popen(argv, **kw):
    _popen_calls.append((list(argv), kw))
    return object()


try:
    with tempfile.TemporaryDirectory() as _td:
        td = Path(_td)
        real_exe = td / "Console.exe"
        real_exe.write_bytes(b"MZ")            # 只是个「存在的文件」

        # --- 环境变量指向真实文件 -> 优先用它 ---
        os.environ[cl.CONSOLE_EXE_ENV] = str(real_exe)
        cl.CONSOLE_EXE_FALLBACK = td / "不存在.exe"
        got, why = cl._console_exe()
        check("环境变量优先于兜底", got == real_exe and why == "",
              f"{got} / {why!r}")

        # --- 环境变量指向不存在的文件 -> 回落 ---
        os.environ[cl.CONSOLE_EXE_ENV] = str(td / "也没有.exe")
        cl.CONSOLE_EXE_FALLBACK = real_exe
        got, why = cl._console_exe()
        check("环境变量指向的文件不存在 -> 回落兜底", got == real_exe)

        # --- 环境变量空 -> 兜底 ---
        os.environ[cl.CONSOLE_EXE_ENV] = "   "
        got, why = cl._console_exe()
        check("环境变量是空白 -> 回落兜底", got == real_exe)

        # --- 两边都没有 -> 报清楚两条路径 ---
        cl.CONSOLE_EXE_FALLBACK = td / "也没有.exe"
        os.environ.pop(cl.CONSOLE_EXE_ENV, None)
        got, why = cl._console_exe()
        check("★两边都没有 -> 返回 None", got is None)
        check("★报错里点名环境变量和兜底路径（用户知道该改哪）",
              cl.CONSOLE_EXE_ENV in why and "也没有.exe" in why, why)

        # --- 真启动：三个标志位 ---
        cl.CONSOLE_EXE_FALLBACK = real_exe
        cl.subprocess.Popen = _fake_popen
        _popen_calls.clear()
        ok, detail = cl._spawn_console()
        check("启动成功", ok is True, detail)
        check("只启动 exe 本身（不塞任何参数）",
              _popen_calls[0][0] == [str(real_exe)],
              f"实际 {_popen_calls[0][0]}")
        kw = _popen_calls[0][1]
        check("★cwd = exe 所在目录（Nuitka base_dir 靠它推导）",
              kw["cwd"] == str(real_exe.parent), kw.get("cwd"))
        check("★DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP",
              kw["creationflags"] == 0x00000008 | 0x00000200,
              f"实际 0x{kw['creationflags']:X}")
        check("★stdin/stdout/stderr 全 DEVNULL（建管道没人读会把子进程堵死）",
              kw["stdin"] is cl.subprocess.DEVNULL
              and kw["stdout"] is cl.subprocess.DEVNULL
              and kw["stderr"] is cl.subprocess.DEVNULL)
        check("close_fds=True", kw["close_fds"] is True)

        # --- Popen 抛 OSError -> 报出来，不炸 ---
        def _raise_oserror(*_a, **_kw):
            raise OSError("拒绝访问")

        cl.subprocess.Popen = _raise_oserror
        ok, detail = cl._spawn_console()
        check("★Popen 失败 -> 返回 False + 人话原因",
              ok is False and "拒绝访问" in detail, detail)
finally:
    cl.subprocess.Popen = _saved_popen
    cl.CONSOLE_EXE_FALLBACK = _saved_fb
    if _saved_env is None:
        os.environ.pop(cl.CONSOLE_EXE_ENV, None)
    else:
        os.environ[cl.CONSOLE_EXE_ENV] = _saved_env

# ======================================================================
print("\n=== 3. 对外接口 launch_console ===")
# ======================================================================
_spawn = cl._spawn_console
try:
    cl._ipc_show_console = lambda: True
    cl._spawn_console = lambda: (_ for _ in ()).throw(
        AssertionError("走了 IPC 就不该再启动"))
    check("管道通了 -> 返回 ipc", cl.launch_console() == cl.RESULT_IPC)
    check("RESULT_IPC / RESULT_SPAWN 是可辨识的字面量",
          cl.RESULT_IPC == "ipc" and cl.RESULT_SPAWN == "spawn")

    cl._ipc_show_console = lambda: False
    cl._spawn_console = lambda: (True, r"D:\...\Console.exe")
    check("管道不通 + 启动成功 -> spawn", cl.launch_console() == cl.RESULT_SPAWN)

    cl._ipc_show_console = lambda: False
    cl._spawn_console = lambda: (False, "找不到 Console.exe：…")
    r = cl.launch_console()
    check("★两边都失败 -> 返回错误描述（不是 ipc/spawn）",
          r not in (cl.RESULT_IPC, cl.RESULT_SPAWN) and "找不到" in r, r)

    def _boom2():
        raise RuntimeError("管道模块炸了")

    cl._ipc_show_console = _boom2
    cl._spawn_console = lambda: (True, "ok")
    check("★管道抛异常也**不阻断**降级到启动",
          cl.launch_console() == cl.RESULT_SPAWN)

    def _boom3():
        raise RuntimeError("启动也炸")

    cl._ipc_show_console = lambda: False
    cl._spawn_console = _boom3
    r = cl.launch_console()
    check("★启动也抛异常 -> 变成返回值而不是往上冒",
          "异常" in r and r not in (cl.RESULT_IPC, cl.RESULT_SPAWN), r)
finally:
    cl._ipc_show_console = cl.__dict__.get("_ipc_show_console")
    import autopilot.ui.overlay.console_launcher as _cl
    cl._ipc_show_console = _cl.__dict__["_ipc_show_console"]
    cl._spawn_console = _spawn

# ======================================================================
print("\n=== 4. 第 4 个按钮：几何 ===")
# ======================================================================
check("按钮数 = 4", interaction.N_BUTTONS == 4)
check("从右往左的顺序 = close/kill/restart/console",
      interaction.BUTTON_ORDER == ("close", "kill", "restart", "console"),
      str(interaction.BUTTON_ORDER))
check("字形用的是 ▶（theme.CONSOLE_GLYPH）", theme.CONSOLE_GLYPH == "▶")
check("CONSOLE_GLYPH 已在 theme.__all__",
      "CONSOLE_GLYPH" in theme.__all__)

W = theme.PANEL_WIDTH
xs = interaction.button_xs(W)
check("button_xs 给出 4 个坐标", len(xs) == interaction.N_BUTTONS, str(xs))
check("最右的是 close", xs[0] == W - theme.BUTTON_HIT - theme.BTN_MARGIN, str(xs))
check("★四个按钮全部不越界（左边界 > 0）",
      all(x > 0 for x in xs), f"最左 x={min(xs)}，宽度 {W}")
check("★最左按钮的右边缘也在面板内",
      max(xs) + theme.BUTTON_HIT <= W,
      f"最右右边缘={max(xs) + theme.BUTTON_HIT} / {W}")
check("按钮不重叠（间隔 >= 1px）",
      all(xs[i] - xs[i + 1] >= theme.BUTTON_HIT + 1
          for i in range(len(xs) - 1)), str(xs))
check("★PANEL_WIDTH 不需要改（仍为 px(480)）",
      W == theme.px(480), f"实际 {W}")
check("按钮行只占面板一小半",
      min(xs) > W * 0.5, f"最左 {min(xs)} / 宽 {W} = {min(xs) / W:.0%}")

# 缩放无关性：改 SCALE 后重算，最左 x 必须恒为正
_saved_scale = (theme.BASE_SCALE, theme._USER_SCALE, theme.SCALE)
try:
    for usr in (theme.USER_SCALE_MIN, 1.0, 1.5, 2.0, theme.USER_SCALE_MAX):
        theme.apply_user_scale(usr)
        w = theme.PANEL_WIDTH
        x2 = interaction.button_xs(w)
        check(f"用户倍率 {usr:.2f}（SCALE={theme.SCALE:.2f}，宽={w}）"
              f"下四按钮不越界", all(v > 0 for v in x2), str(x2))
finally:
    theme.BASE_SCALE, theme._USER_SCALE, theme.SCALE = _saved_scale
    theme._recompute()

# ======================================================================
print("\n=== 5. 第 4 个按钮：HitBox 与分发 ===")
# ======================================================================
class _StubRoot:
    def __init__(self):
        self.binds = {}

    def bind(self, seq, handler, *_a, **_kw):
        self.binds[seq] = handler


_presses = []
ita = interaction.Interaction(
    _StubRoot(),
    on_move=lambda x, y: None,
    on_close=lambda: _presses.append("close"),
    on_kill=lambda: _presses.append("kill"),
    on_restart=lambda: _presses.append("restart"),
    on_scale=lambda d: None,
    on_persist=lambda x, y: None,
    on_open_console=lambda: _presses.append("console"),
    on_stop_task=lambda: _presses.append("stop_task"),
    on_stop_all=lambda: _presses.append("stop_all"),
    get_pos=lambda: (0, 0),
)

made = []
ita._make_button = lambda name, x, y, size, glyph, **kw: made.append(
    (name, x, glyph))
ita.layout_buttons(theme.PANEL_WIDTH, 100)

check("★建了 4 个按钮控件", len(made) == 4, str([m[0] for m in made]))
check("按钮名集合与 BUTTON_ORDER 一致",
      sorted(m[0] for m in made) == sorted(interaction.BUTTON_ORDER),
      str(sorted(m[0] for m in made)))
check("★console 按钮用的是 theme.CONSOLE_GLYPH",
      any(m[0] == "console" and m[2] == theme.CONSOLE_GLYPH for m in made))
check("建了 4 个 HitBox", len(ita.buttons) == 4, str(len(ita.buttons)))
check("★HitBox 的 action 覆盖全部 4 个按钮名",
      sorted(b.action for b in ita.buttons) == sorted(interaction.BUTTON_ORDER),
      str(sorted(b.action for b in ita.buttons)))
check("★console 的 HitBox 在最左（x 最小）",
      min(ita.buttons, key=lambda b: b.x).action == "console")
check("close 的 HitBox 在最右",
      max(ita.buttons, key=lambda b: b.x).action == "close")
check("★每个 HitBox 都能被点到（contains 命中自己的中心）",
      all(b.contains(b.x + b.w // 2, b.y + b.h // 2)
          for b in ita.buttons))
_centers = {}
for _b in ita.buttons:
    _hit = [o.action for o in ita.buttons
            if o.contains(_b.x + _b.w // 2, _b.y + _b.h // 2)]
    _centers[_b.action] = _hit
check("★四个 HitBox 互不重叠（每个中心只命中自己）",
      all(v == [k] for k, v in _centers.items()),
      f"SCALE={theme.SCALE} 命中表={_centers}")

for name in interaction.BUTTON_ORDER:
    ita._activate(name)
check("★四个按钮各自分发到正确回调",
      _presses == ["close", "kill", "restart", "console"],
      str(_presses))

try:
    interaction.Interaction(
        _StubRoot(), on_move=lambda x, y: None, on_close=lambda: None,
        on_kill=lambda: None, on_restart=lambda: None,
        on_scale=lambda d: None, on_persist=lambda x, y: None,
        get_pos=lambda: (0, 0))
except TypeError as exc:
    check("★漏传 on_open_console 直接 TypeError（不是静默 no-op）",
          "on_open_console" in str(exc), str(exc)[:70])
    check("★报错里也点名了两个停止回调",
          all(k in str(exc) for k in ("on_stop_task", "on_stop_all")),
          str(exc)[:150])
else:
    check("★漏传 on_open_console 直接 TypeError（不是静默 no-op）",
          False, "居然构造成功了 —— ▶ 按钮会变成哑巴")

# 源码契约：app.py 必须把回调接上、必须有 toast
_app_src = (PLUGIN_ROOT / "autopilot" / "ui" / "overlay" / "app.py").read_text(
    encoding="utf-8")
check("app 把 _open_console 接到 on_open_console",
      "on_open_console=self._open_console" in _app_src)
check("app 的 _open_console 三种结果都有 toast 文案",
      all(s in _app_src for s in ("已唤起运行中的主控台", "已启动主控台")))
check("★失败分支用 ACCENT_ERROR（成功用 ACCENT_RUN，状态要分得开）",
      "self._toast, self._toast_color = (result, theme.ACCENT_ERROR)"
      in _app_src)
check("app import 了 console_launcher",
      "import console_launcher" in _app_src
      or "from . import console_launcher" in _app_src)
_cl_src = (PLUGIN_ROOT / "autopilot" / "ui" / "overlay"
           / "console_launcher.py").read_text(encoding="utf-8")


def _top_imports(src):
    """模块**顶层**的 import（含函数内 import，一并列出便于核对）。"""
    import ast

    out = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
    return sorted(set(out))


check("★overlay 没有 import PySide6 / PyQt（AST 判，忽略注释与 docstring）",
      not ({"PySide6", "PyQt5", "PyQt6"}
           & {m.split(".")[0] for m in _top_imports(_app_src)
              + _top_imports(_cl_src)}),
      f"app={_top_imports(_app_src)}")
check("★console_launcher 只用标准库（含 __future__）",
      set(_top_imports(_cl_src)) <= {"__future__", "logging", "os",
                                     "subprocess", "pathlib",
                                     "ctypes", "wintypes"},
      str(_top_imports(_cl_src)))

print()
print("=" * 74)
print(f"结果: {len(PASS)}/{len(PASS) + len(FAIL)} 通过")
if FAIL:
    print("  失败: " + ", ".join(FAIL))
print("=" * 74)
sys.exit(1 if FAIL else 0)
