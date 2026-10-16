"""窗口自由缩放的单测（2026-10-16）。

背景
----
用户报告：Console 窗口缩不到合理尺寸，**Y 轴有硬性最小值**。

根因不是某个控件写了 ``setFixedHeight``，而是 Qt 的地板规则：

    没显式调 ``setMinimumSize`` 时，Qt 拿 ``minimumSizeHint()`` 当硬地板。

实测（改之前）::

    MainWindow.minimumSize()     = (0, 0)        <- 没设，于是用 hint
    MainWindow.minimumSizeHint() = (2020, 961)   <- 这就是拖不下去的真实原因

而 ``QStackedWidget.minimumSizeHint()`` 取**所有页的最大值**：

    宽度 1876 <- 引擎 tab 的表格
    高度 961  <- 寻路 tab（MapPicker 340 + 事件流 120 + 其余控件逐项相加）

修法两条，缺一不可：

1. ``MainWindow.setMinimumSize(320, 240)`` —— 显式值**接管**地板；
2. 每个 tab 包 ``QScrollArea`` —— 只降地板的话，内容会被压扁/裁掉，
   表现是控件重叠、按钮点不到。包滚动区后不够高就出滚动条。

★★ 为什么全部在子进程里量
---------------------------
本仓 Qt 测试跑在 offscreen 下，且**已经因为 app 实例问题崩过两次**
（见 ``test_engine_start_guard.py`` 的注释）。本仓其它 widget 测试用的是
module 级 fixture ``QApplication.instance() or QApplication([])``，
模块一结束引用就被丢掉；下一个模块再 ``QApplication([])`` 就是第二个实例，
而前一个模块的 widget 还活着。

实测后果不是断言失败，而是「用例全绿、进程退出时
``STATUS_STACK_BUFFER_OVERRUN``(0xC0000374)」，且**依赖文件顺序**：
``test_mapwidget + 本文件`` 崩，反过来不崩；插在其它文件中间跑又正常。
这种顺序相关的崩溃没法靠"我小心点清理"根治。

所以这里把测量整个放进子进程：子进程建自己的 QApplication、量完就退，
**不往 pytest 进程里留任何 Qt 状态**，对文件顺序完全免疫。
代价是每次测量多 ~1.5 秒，可以接受。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: 在子进程里跑的测量脚本。输出以 ``PROBE_JSON:`` 开头的一行 JSON。
#: 只打印这一行，其余（Qt 的 font 警告、日志）走 stderr，不干扰解析。
_PROBE = r'''
import json, os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QScrollArea
app = QApplication.instance() or QApplication([])

from neko_console.app import MainWindow
from neko_console.config import ConsoleConfig
from neko_console.tabs.mapwidget import MapPicker

w = MainWindow(ConsoleConfig())

out = {
    "min_w": w.minimumWidth(),
    "min_h": w.minimumHeight(),
    "hint_w": w.minimumSizeHint().width(),
    "hint_h": w.minimumSizeHint().height(),
    "tabs": [],
}
for i in range(w._stack.count()):
    page = w._stack.widget(i)
    inner = page.widget() if hasattr(page, "widget") else page
    out["tabs"].append({
        "name": w._list.item(i).text(),
        "scrolled": isinstance(page, QScrollArea),
        "inner_min_w": inner.minimumSize().width(),
        "inner_min_h": inner.minimumSize().height(),
        "inner_hint_h": inner.minimumSizeHint().height(),
    })

resizes = {}
for tw, th in [(400, 300), (360, 260), (320, 240)]:
    w.resize(tw, th)
    app.processEvents()
    resizes["%dx%d" % (tw, th)] = [w.size().width(), w.size().height()]

w.resize(320, 240)
app.processEvents()
for i in range(w._stack.count()):
    w._stack.setCurrentIndex(i)
    app.processEvents()
    resizes["after_switch_%d" % i] = [w.size().width(), w.size().height()]

w.resize(1100, 720)
app.processEvents()
resizes["regrown"] = [w.size().width(), w.size().height()]

w.showMaximized()
app.processEvents()
resizes["maximized"] = [w.size().width(), w.size().height()]
out["resizes"] = resizes

picker = MapPicker()
out["map_min_height"] = picker._view.minimumHeight()

w.close()
app.processEvents()
print("PROBE_JSON:" + json.dumps(out, ensure_ascii=False))
'''

_MARKER = "PROBE_JSON:"


@pytest.fixture(scope="module")
def probe() -> dict:
    """在子进程里量一次 MainWindow，返回解析后的 dict。

    整个模块共用一次测量 —— 建 MainWindow 要拉起 Tray / BridgeWriter /
    三个 QTimer，重复建既慢又没必要。
    """
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(PROJECT_ROOT), env=env, timeout=180,
    )
    for line in (proc.stdout or "").splitlines():
        if line.startswith(_MARKER):
            return json.loads(line[len(_MARKER):])
    raise AssertionError(
        "子进程测量失败：\n"
        f"  returncode={proc.returncode}\n"
        f"  stdout={(proc.stdout or '')[-2000:]}\n"
        f"  stderr={(proc.stderr or '')[-2000:]}"
    )


# =============================================================================
# 地板本身
# =============================================================================


def test_mainwindow_minimum_is_small_enough(probe) -> None:
    """★ 用户能不能拖到 400x300 以下。"""
    assert probe["min_w"] <= 400, (
        f"宽度地板 {probe['min_w']} —— 用户报告的「缩不下去」")
    assert probe["min_h"] <= 300, (
        f"高度地板 {probe['min_h']} —— 用户报告的「Y 轴硬性最小值」")


def test_mainimum_is_explicit_not_inherited_from_hint(probe) -> None:
    """地板必须是**显式**设的，不是 minimumSizeHint 顺过来的。

    Qt 的规则是：没显式调 ``setMinimumSize`` 时拿 ``minimumSizeHint`` 当地板。
    改之前就是 (0,0) 显式值 + 2020x961 的 hint，于是缩不动。
    """
    assert (probe["min_w"], probe["min_h"]) != (0, 0), \
        "minimumSize 是 (0,0) —— 说明没显式设，Qt 会退回 minimumSizeHint 当地板"


def test_minimum_size_hint_does_not_blow_up_again(probe) -> None:
    """守住 hint 不反弹。地板比 hint 低才生效，所以 hint 高了也没用，
    但 hint 涨回 900+ 说明有控件又被加回了硬最小值，属于回归信号。"""
    assert probe["hint_h"] < 400, \
        f"minimumSizeHint 高度 {probe['hint_h']} —— 有控件被加回了硬最小值"


def test_minimum_size_hint_is_below_explicit_minimum(probe) -> None:
    """修复后 hint 应已低于显式地板（212x70 vs 320x240），即完全由地板说了算。"""
    assert probe["hint_w"] <= probe["min_w"] and probe["hint_h"] <= probe["min_h"]


# =============================================================================
# 真的缩下去（行为，不是数字）
# =============================================================================


@pytest.mark.parametrize("target", ["400x300", "360x260", "320x240"])
def test_window_can_actually_resize_to(probe, target) -> None:
    """★ 真 resize 到目标尺寸并处理事件，尺寸必须**停得住**。

    Qt 缩不动时不会报错，只会把 resize 请求丢掉，所以必须回读实际尺寸。
    """
    got = probe["resizes"][target]
    want = [int(v) for v in target.split("x")]
    assert got == want, f"请求 {target}，实际停在 {got[0]}x{got[1]}"


def test_window_survives_shrink_then_grow(probe) -> None:
    """缩到最小再拉回默认，中间不许崩（滚动区重建布局容易在这里炸）。"""
    assert probe["resizes"]["regrown"] == [1100, 720]


def test_window_can_maximize(probe) -> None:
    """缩小能力不能把最大化搞坏。"""
    mw, mh = probe["resizes"]["maximized"]
    assert mw > 0 and mh > 0, "最大化后尺寸为 0"


@pytest.mark.parametrize("idx", range(6))
def test_switching_tabs_after_shrink_keeps_size(probe, idx) -> None:
    """缩到最小后逐个切 tab，窗口尺寸不许被切回去。

    单个 tab 的 minimumSizeHint 高过地板时，``QStackedWidget`` 取最大值，
    切过去就会把整窗顶大 —— 所以每个 tab 都要单独验一遍。
    """
    got = probe["resizes"][f"after_switch_{idx}"]
    assert got == [320, 240], (
        f"切到 tab[{idx}]（{probe['tabs'][idx]['name']}）后尺寸变成 {got[0]}x{got[1]}")


# =============================================================================
# 每个 tab 都得能缩
# =============================================================================


def test_every_tab_is_wrapped_in_scroll_area(probe) -> None:
    """所有 tab 都必须在 QScrollArea 里。

    少包一个，那个 tab 的 minimumSizeHint 就会经 QStackedWidget 取最大值
    把整窗地板重新顶回去，而且很难一眼看出是谁干的。
    """
    not_scrolled = [t["name"] for t in probe["tabs"] if not t["scrolled"]]
    assert not not_scrolled, f"这些 tab 没有包滚动区：{not_scrolled}"


def test_each_tab_content_has_zero_explicit_minimum(probe) -> None:
    """被包的 widget 自己不能还声明着非零 minimumSize。"""
    offenders = [(t["name"], t["inner_min_w"], t["inner_min_h"])
                 for t in probe["tabs"]
                 if (t["inner_min_w"], t["inner_min_h"]) != (0, 0)]
    assert not offenders, f"这些 tab 仍声明显式最小尺寸：{offenders}"


def test_map_picker_view_minimum_height_is_not_the_old_340(probe) -> None:
    """MapPicker 的 _view 曾经 setMinimumHeight(340)，是高度地板的大头。

    自由缩放后降到 120；MapPicker 现在整体在 QScrollArea 里，
    高度不够会出滚动条。
    """
    assert probe["map_min_height"] <= 120, (
        f"地图最小高度回到 {probe['map_min_height']} 了")


def test_pathfind_tab_height_hint_dropped(probe) -> None:
    """寻路 tab 的内部 hint 应从 961 明显降下来（340 -> 120 省了 220）。"""
    tab = next(t for t in probe["tabs"] if t["name"] == "寻路")
    assert tab["inner_hint_h"] < 800, (
        f"寻路 tab 内部 hint 仍是 {tab['inner_hint_h']}px")
