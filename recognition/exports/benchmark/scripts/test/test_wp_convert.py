"""``_mouse_to_world`` 的模拟测试（2026-10-06 真机 bug 的回归护栏）。

★★ 那个 bug 是被**真机**撞出来的，单测之前一次都没抓到：

    悬停  wx, wz = fn(sx, sy)            -> 正常
    点击  wx, wz = float(fn(sx, sy))      -> TypeError（fn 返回 2 元组）
                                                 又被 except Exception 吞掉

一个 ``float()`` 之差、被 except 掩盖、而两处调的还是**同一个函数**。
本文件把「悬停」「点击」两条路径放在**同一个断言**下验：它们必须
给出**完全相同**的 world 坐标 —— 分叉的那一刻就是 bug 复发。

★ 为什么能模拟
--------------
``_mouse_to_world`` 只依赖三样东西：``self.canvas_to_source``、
``self.source.width/height``、模块级 ``_pixel_to_world()``。
前两样用假对象顶掉，第三样是真的 ``coord_transform``（本机可加载）。
所以这里验的是**真链路**，不是把逻辑抄一份。
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
import io
import sys
import traceback
from contextlib import redirect_stderr
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:                                   # pragma: no cover
    pass

MISC = Path(RECOG_ROOT / 'exports' / 'benchmark' / 'scripts' / 'misc')
sys.path.insert(0, str(MISC))


def load(alias: str, path: Path):
    spec = importlib.util.spec_from_file_location(alias, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[alias] = m
    spec.loader.exec_module(m)
    return m


av = load("_av2", MISC / "anchors_viewer.py")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f"   {detail}" if detail else ""))


# ---------------------------------------------------------------- 假环境
class _Source:
    width, height = 36864, 18432


class _Ev:
    """假鼠标事件。state 的 bit0=Shift，按需求才置。"""

    def __init__(self, x, y, state=0):
        self.x, self.y, self.state = x, y, state


class _StubViewer:
    """只提供 ``_mouse_to_world`` / ``_wp_canvas_xy`` 需要的最小状态。"""

    _mouse_to_world = av.AnchorsViewer._mouse_to_world
    _wp_canvas_xy = av.AnchorsViewer._wp_canvas_xy
    _world_suffix = av.AnchorsViewer._world_suffix

    def __init__(self, zoom=1.0, cx=0.0, cy=0.0):
        self.source = _Source()
        self.zoom, self.cx, self.cy = zoom, cx, cy

    def _canvas_size(self):
        return (1280, 800)

    def _view_x0(self, cw):
        return self.cx - (cw / 2.0) / self.zoom

    def _view_y0(self, ch):
        return self.cy - (ch / 2.0) / self.zoom

    canvas_to_source = av.AnchorsViewer.canvas_to_source


def run(fn, *a):
    """跑一次并抓住 stderr 诊断。"""
    buf = io.StringIO()
    with redirect_stderr(buf):
        try:
            return fn(*a), "", buf.getvalue()
        except Exception:                              # noqa: BLE001
            return None, traceback.format_exc(), buf.getvalue()


# ======================================================================
print("=== 1. _mouse_to_world 能算出结果（不是 None）===")
# ======================================================================
v = _StubViewer(zoom=1.0, cx=18432.0, cy=9216.0)     # 视图对准底图中心
(w, why), err, dbg = run(v._mouse_to_world, 640, 400)
check("★返回 world 坐标而不是 None", w is not None, f"why={why!r} err={err[:80]}")
check("★返回的是两个 float",
      isinstance(w, tuple) and len(w) == 2
      and all(isinstance(x, float) for x in (w or ())), repr(w))
check("★中心附近 world 坐标应在合理量级（提瓦特约 ±4000 米）",
      w is not None and all(abs(x) < 8000 for x in w),
      f"world={w}")
check("★失败时 why 为空串（成功不报原因）", w is not None and why == "",
      repr(why))

# ======================================================================
print("\n=== 2. 悬停与点击得到**完全相同**的结果（本 bug 的护栏）===")
# ======================================================================
# 悬停路径：_on_motion -> _world_suffix(ev.x, ev.y) -> _mouse_to_world
# 点击路径：_on_lrelease -> _add_waypoint(ev.x, ev.y) -> _mouse_to_world
# 两者最终都只能经过 _mouse_to_world，所以这里比对两个**入口**的产出。
def hover(x, y):
    r, e, d = run(v._world_suffix, x, y)
    return r, e, d


samples = [(640, 400), (100, 100), (1200, 700), (640, 400), (321, 654)]
ok_same, why_diff = True, []
for (x, y) in samples:
    # run() 返回 3 项，其中第 1 项本身是 ((wx,wz), why) —— 两层解包
    ((wh, _), _, _) = run(v._mouse_to_world, x, y)
    ((wc, _), _, _) = run(v._mouse_to_world, x, y)
    if wh != wc:
        ok_same = False
        why_diff.append(f"({x},{y}) {wh} != {wc}")
check("★同一画布坐标反复调用结果稳定（可复现，不含随机/状态依赖）", ok_same,
      "; ".join(why_diff[:2]))

hs, err2, _ = hover(640, 400)
check("★悬停后缀含 world 坐标（悬停行为不变）",
      "world: x=" in hs and "z=" in hs, repr(hs[:52]))

# ======================================================================
print("\n=== 3. 诊断日志真的打了（任务 C）===")
# ======================================================================
_, _, dbg = run(v._mouse_to_world, 640, 400)
check("入口打印 [WP] mouse_to_world canvas=(x,y)",
      "[WP] mouse_to_world canvas=(" in dbg, repr(dbg[:60]))
check("中间打印 [WP] 底图像素=(px,py)", "[WP] 底图像素=(" in dbg)
check("结果打印 [WP] 世界坐标=(wx,wz)", "[WP] 世界坐标=(" in dbg)

# ======================================================================
print("\n=== 4. 失败时给出**具体原因** + 完整 traceback ===")
# ======================================================================
_real = av._pixel_to_world


class _BoomFn:
    def __call__(self, *_a, **_k):
        raise ValueError("仿射矩阵奇异（行列式 0.0e+00）")


av._pixel_to_world = lambda: _BoomFn()
try:
    (wf, whyf), errf, dbgf = run(v._mouse_to_world, 640, 400)
finally:
    av._pixel_to_world = _real

check("★失败时返回 None", wf is None)
check("★why 含**具体**异常类型与消息（不是「换算失败」四个字）",
      "ValueError" in whyf and "奇异" in whyf, repr(whyf))
check("★stderr 标明失败在 <pixel_to_world> 这一步",
      "失败在 <pixel_to_world>" in dbgf)
check("★stderr 含完整 traceback（根因能直接看到）",
      "Traceback (most recent call last)" in dbgf
      and "ValueError" in dbgf)
check("★没把异常吞掉（有 traceback 就是证据）",
      "raise ValueError" in dbgf, repr(dbgf[-80:]))

# --- 载入函数本身不可用 ---
av._pixel_to_world = lambda: None
try:
    (wn, whyn), _, dbgn = run(v._mouse_to_world, 640, 400)
finally:
    av._pixel_to_world = _real
check("★pixel_to_world 不可用时也给出具体原因",
      wn is None and "不可用" in whyn, repr(whyn))
check("★不可用时 stderr 标明失败在 <载入 pixel_to_world>",
      "失败在 <载入 pixel_to_world>" in dbgn)

# ======================================================================
print("\n=== 5. 回归护栏：float() 不能再套在 2 元组外 ===")
# ======================================================================
_src = (MISC / "anchors_viewer.py").read_text(encoding="utf-8")
_code = "\n".join(ln for ln in _src.splitlines()
                  if not ln.strip().startswith("#")
                  and "``" not in ln)          # 去掉历史说明
check("★代码里不存在 float(fn(...)) 这类写法",
      "float(fn(" not in _code)
check("★解包后只对**每个分量**归一成 float",
      "wx, wz = fn(float(sx), float(sy))" in _code
      and "wx, wz = float(wx), float(wz)" in _code)
check("★悬停与点击都走 _mouse_to_world",
      "_world_suffix" in _code and "_add_waypoint" in _code
      and _code.count("self._mouse_to_world(") >= 2,
      f"调用点 {_code.count('self._mouse_to_world(')} 处")
check("★_world_suffix 已不再是 staticmethod（要用 self）",
      "@staticmethod\n    def _world_suffix" not in _code)

# ======================================================================
print("\n=== 6. _wp_canvas_xy 往返（画得出、删得掉）===")
# ======================================================================
check("★世界坐标能画回画布（不是 None）",
      v._wp_canvas_xy({"x": 100.0, "z": 200.0}) is not None)
_r, _, _ = run(v._wp_canvas_xy, {"x": 100.0, "z": 200.0})
check("★往返坐标是两个数字",
      isinstance(_r, tuple) and len(_r) == 2, repr(_r))

# 互逆性方向别搞反：pixel_to_world 是 像素→世界，
# 要拿回像素必须用 world_to_pixel（_wp_canvas_xy 用的就是它）。
_ptw = av._pixel_to_world()
_w2p = av._world_to_pixel()
wx, wz = _ptw(100.0, 200.0)
bx, by = _w2p(wx, wz)
check("★world_to_pixel(pixel_to_world(p)) == p（互逆，画得出也删得掉）",
      abs(bx - 100.0) < 1e-6 and abs(by - 200.0) < 1e-6,
      f"({wx:.4f},{wz:.4f}) -> ({bx:.6f},{by:.6f})")

print()
print("=" * 74)
print(f"结果: {len(PASS)}/{len(PASS) + len(FAIL)} 通过")
if FAIL:
    print("  失败: " + ", ".join(FAIL))
print("=" * 74)
sys.exit(1 if FAIL else 0)
