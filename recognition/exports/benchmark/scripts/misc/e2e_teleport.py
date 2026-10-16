"""传送端到端验证（真机）—— LocalizationService 三段式契约。

验证什么
--------
``LocalizationService`` 的传送契约**严格三段全过**才刷新位置：

    ① SELECT         on_teleport_ocr() 挂起目标，**不提交坐标**
    ② LOADING        场景连续非大世界（SceneDetector 自带 N=3.0s 去抖）
    ③ WORLD_RESTORED 场景回大世界且 changed=True -> **此时才**提交
    任一段未满足（含 30s 超时）-> **整次作废，保留旧位置**

初版在 ① 就提交坐标，传送失败时位置已被污染且无法回退。§19 修的就是这个。

★ 你要做什么
------------
1. 命令敲下去后，倒计时期间**切回游戏**并让它在前台
2. 站在任意位置
3. 打开大地图 -> 点一个传送点（信息框会显示地名，**这一步就会被 OCR 到**）
4. **尽快**确认传送（见下面「② LOADING 的坑」）
5. 等落地，然后看脚本输出

★★ ② LOADING 的坑（务必先读）
------------------------------
② 判据是「场景**连续非大世界 3 秒**」。而**大地图打开着的时候场景已经
不是大世界了** —— 于是：

    地图开着磨蹭 >3s  ->  ② 在你还没点传送时就满足了
    随后真的传送 ->  ③ 仍然会满足 -> 位置照样刷新对

所以**位置刷新时刻是对的**（③ 保证了这一点），但 ② 这一段的时长
**不一定是真实的传送加载时长**。本脚本会把「② 成立」和「③ 成立」的
时间点都打出来，你自己判断传送是在哪一段真正发生的。

若想验证干净的 ②：**点完传送点立刻确认**，别在地图里磨蹭。

★ 为什么自己驱动服务，而不是交给 StateBuilder
-------------------------------------------
``StateBuilder._locate()`` 会调 ``locator.on_frame()``，但它**吞掉异常**
（返回 None + 发 LOCATOR_FAILED 事件）。诊断脚本最不能接受的就是
「异常被吞了还继续跑」。所以本脚本自己调 ``on_frame``，拿得到完整的
``LocalizationState``（state / is_world / note / world_pos），
同时仍然用 StateBuilder 出 ``scene.id`` 标签，并把 builder 的事件
逐条打出来 —— 被吞掉的定位异常会从那里露出来。

⚠️ **不 import ultralytics**（AGPL-3.0，运行时链接即传染）。
⚠️ 本脚本**不按任何键** —— 传送由你自己在游戏里点。
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
import time
import traceback
from collections import defaultdict
from pathlib import Path

# ---- 绝对路径（不依赖 cwd）-----------------------------------------------
PLUGIN_ROOT = Path(
    PLUGIN_DIR)
sys.path.insert(0, str(PLUGIN_ROOT))

try:
    # stdout 和 stderr 都要配 —— 参数错误是往 stderr 打的
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 - 老 Python 没这功能，忽略
    pass

DEFAULT_TITLE = "原神"
DEFAULT_SECONDS = 60.0
DEFAULT_DELAY = 8.0
#: 循环频率 5Hz —— 和 core/loop.py 的 tick 一致
TICK_S = 0.200
#: **大世界**时的 OCR 轮询间隔。信息框只可能出现在「刚开地图还没满 3s」
#: 的那段去抖窗口里（见下），所以这个档只是兜底。
OCR_EVERY_WORLD_S = 1.0
#: **非大世界**（= 大地图开着）时**每帧都**跑 OCR —— 信息框就在这儿，
#: 不加密就没有任何理由不试。RapidOCR 只吃一个小 ROI 裁块，实测可接受。
OCR_EVERY_MENU_S = 0.0
#: 离开大世界后，这个时长内**强制每帧** OCR（Task B 的加密窗口）。
#: 覆盖「场景刚翻转」那一段最容易出问题的时刻。
DENSE_AFTER_LEAVE_S = 5.0

#: ROI 特征落盘路径（§43）。**只采集，本轮不做阈值判据。**
#: 用来标定「OCR 之前先判 ROI 有没有东西」这道廉价前置门。
FEAT_CSV = Path(RECOG_ROOT / 'exports' / 'benchmark' / 'datasets' / 'teleport_roi_features.csv')
#: 列：任务要求的 6 列在前，追加的诊断列在后（why/matched 便于事后分组核对）
FEAT_COLS = ("t", "scene", "edge_density", "color_var", "mean_sat",
             "has_textbox", "why", "matched", "gate")

# ---------------------------------------------------------------- 阈值标定
#: ROI 特征门（§44 标定，**基于 8 正 / 31 负样本**）。
#: 判据为假 -> 跳过 OCR，省掉 1.8~2.0s 的引擎开销。
#:
#: 分布（正样本 = 真信息框，n=8）：mean_sat 56.50~63.05
#:                              color_var 13145~14462
#:        负样本 n=31：           mean_sat 45.0~173.7
#:                              color_var 0~14076
#: 混淆矩阵：TP=8 FN=0 FP=1 TN=30 -> P=0.889 R=1.000 F1=0.941
#:
#: ★ **假阴性率 0% 是硬指标** —— 漏掉一次真信息框 = 漏一次传送。
#:   ``color_var >= ROI_VAR_MIN`` 那一项是为了剔掉加载中黑屏
#:   （sat=45 / var=0）造成的假阳性，不是为了提高召回。
#: ★ 阈值在 56~80 区间**混淆矩阵完全不变**，对阈值不敏感。
#:
#: ⚠️ **8 个正样本是薄数据**，且全部来自同一套 UI 美术（同游戏、同分辨率、
#:    同界面）。换分辨率或 UI 重绘后分布可能整体平移 —— 所以配了
#:    :data:`FORCE_OCR_EVERY_N` 逃生通道。
#: ★ **阈值取 75 而不是标定报告里的 65** —— 65 在这份数据上同样 8/8 召回、
#:   同样 1/31 假阳性，但它距实测正样本**最大值 63.05 只有 2 个单位**。
#:   下一个正样本稍微偏一点（66）就会被门挡掉，而假阴性是硬指标。
#:   65/70/75/80 的混淆矩阵**完全一致**，最近负样本在 86.98 ——
#:   所以 75 在不增加任何假阳性的前提下，把安全余量从 2 拉到 **12**。
#:   （sat_max=56 会把 8 个正样本**全挡掉**，可见这个方向不能往低调。）
ROI_SAT_MAX: float = 75.0
ROI_VAR_MIN: float = 5000.0

#: **失效保护**：连续 N 帧判据为假就强制跑一次 OCR。
#: 若阈值在某环境下整体失效，最坏只是退化回逐帧 OCR，而不是永久漏检。
FORCE_OCR_EVERY_N: int = 20
#: 判据总开关。``--no-gate`` 可关掉退回逐帧 OCR ——
#: 阈值只有 8 个正样本撑着，留一条不需要改代码的退路。
GATE_ENABLED: bool = True


class _RoiGate:
    """OCR 前置门 + 失效保护。可脱离游戏单测（§45）。

    判据通过才调 OCR。连续 :data:`FORCE_OCR_EVERY_N` 次判为假则强制放行
    一次并重置计数 —— 这是阈值失效时的唯一逃生通道，**绝不能省**。
    """

    def __init__(self,
                 sat_max: float = ROI_SAT_MAX,
                 var_min: float = ROI_VAR_MIN,
                 force_every: int = FORCE_OCR_EVERY_N) -> None:
        self.sat_max = float(sat_max)
        self.var_min = float(var_min)
        self.force_every = max(1, int(force_every))
        self.skip_streak = 0
        #: 统计（汇总要用）
        self.n_pass = 0        # 放行（不含强制）
        self.n_skip = 0        # 跳过
        self.n_forced = 0      # 失效保护触发

    def check(self, feats: dict) -> tuple[bool, bool]:
        """返回 ``(是否跑 OCR, 是否由失效保护强制放行)``。"""
        sat = feats.get("mean_sat")
        var = feats.get("color_var")
        # ★ 算不出特征（裁剪退化）一律放行：门是**加速器**，
        #   不是正确性的来源，绝不能让它把流程卡死。
        if sat is None or var is None:
            self.skip_streak = 0
            self.n_pass += 1
            return True, False
        if float(sat) <= self.sat_max and float(var) >= self.var_min:
            self.skip_streak = 0
            self.n_pass += 1
            return True, False
        self.skip_streak += 1
        if self.skip_streak >= self.force_every:
            self.skip_streak = 0
            self.n_forced += 1
            return True, True
        self.n_skip += 1
        return False, False

    def stats(self) -> dict:
        return {"pass": self.n_pass, "skip": self.n_skip,
                "forced": self.n_forced}


SEP = "=" * 78


def hr(title: str = "") -> None:
    if title:
        print(f"\n{SEP}\n  {title}\n{SEP}")
    else:
        print(SEP)


def _parse_args() -> tuple[str, float, str | None, float, bool]:
    title, seconds, channel, delay = (
        DEFAULT_TITLE, DEFAULT_SECONDS, None, DEFAULT_DELAY)
    gate = GATE_ENABLED
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else None
        if a == "--seconds" and nxt is not None:
            seconds = float(nxt); i += 2
        elif a == "--channel" and nxt is not None:
            channel = nxt; i += 2
        elif a == "--delay" and nxt is not None:
            delay = max(0.0, float(nxt)); i += 2
        elif a == "--title" and nxt is not None:
            title = nxt; i += 2
        elif a == "--no-gate":
            gate = False; i += 1
        elif a.startswith("-"):
            print(f"未知参数: {a}", file=sys.stderr)
            i += 1
        else:
            title = a; i += 1
    return title, seconds, channel, delay, gate


def _histogram(times, bucket_s: float = 5.0) -> dict[int, int]:
    """时间戳列表 -> ``{桶起点: 次数}``。**纯函数**，不碰游戏，可单独验证。

    ⚠️ 这里必须用 ``defaultdict(int)``（§42）。
       ``d[k] += 1`` 展开是 ``d[k] = d[k] + 1``，对**空 dict 的第一个 key
       必然 KeyError** —— 与 key 的值是 0 还是 5 无关。t 从 0 开始时
       第一个桶正好是 0，所以是「第一次进这个分支就炸」。

       抽成独立函数是为了能脱离游戏跑验证：汇总段的 bug 藏在最末尾，
       而抓帧失败时根本走不到那里（§42 的由来）。
    """
    out: dict[int, int] = defaultdict(int)
    for t in times:
        out[int(t // bucket_s) * int(bucket_s)] += 1
    return dict(out)


def _fmt_pos(p) -> str:
    return "None" if p is None else f"({p[0]:.2f}, {p[1]:.2f})"


def _roi_features(region) -> dict:
    """信息框 ROI 的**廉价**特征。只用 OpenCV + numpy，无新依赖。

    目的：为「OCR 之前先判一下 ROI 里有没有东西」这个前置判据**标定阈值**。
    RapidOCR 本机实测 1.8~2.0s/次，而每帧都跑会让帧率掉到 0.5fps ——
    所以需要一道便宜的前置判断。但**阈值不能拍脑袋**，必须先拿数据。

    ⚠️ 本轮**只采集，不设阈值**。拿到 CSV 之前任何阈值都是猜的。

    三个特征（都刻意选成 O(像素) 且无参数可调，减少过拟合空间）：

    * ``edge_density`` —— Canny 边缘像素占比。文字/图标一定有边缘，
      纯色背景没有。
    * ``color_var``    —— 三通道方差之和。信息框是深蓝底白字，对比度极高。
    * ``mean_sat``     —— HSV 的 S 通道均值。深蓝底偏低饱和，
      换成别的 UI 底色时这个特征才会动。
    """
    import cv2
    import numpy as np

    g = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY) if region.ndim == 3 else region
    edges = cv2.Canny(g, 100, 200)
    b, gr, r = cv2.split(region) if region.ndim == 3 else (g, g, g)
    var = float(b.var() + gr.var() + r.var())
    sat = float(cv2.cvtColor(region, cv2.COLOR_BGR2HSV)[:, :, 1].mean()) \
        if region.ndim == 3 else 0.0
    return {
        "edge_density": round(float(np.count_nonzero(edges)) / float(edges.size), 5),
        "color_var": round(var, 2),
        "mean_sat": round(sat, 2),
    }


def _probe_teleport(frame_image, points,
                    gate: "_RoiGate | None" = None
                    ) -> tuple[str, object, list, dict]:
    """跑一次 OCR 并**分类**结果。

    返回 ``(原因, 目标 | None, 可信行, ROI特征 | None)``。

    为什么要自己分三段而不是直接看 ``recognize_teleport()`` 的返回值：
    它对「裁剪退化」「信息框里没文字」「有文字但没匹配上 936 点」一律
    返回 ``None``，**分不出来**。而这三类的排查方向完全不同 ——
    分别是「画面太小」「OCR 后端/ROI 不对」「地点名不在候选表里」。

    ★ 只用**公开** API，且靠 ``recognize_teleport(lines=...)`` 把已经
      读到的行喂回去 —— 它有这个参数就是为了让调用方别把引擎跑两遍。

    ★ ROI 特征在**裁剪之后、OCR 之前**算，OCR 不会再跑一遍裁剪，
      所以这三个特征几乎不花时间。

    原因取值：``crop_fail`` / ``no_text`` / ``no_match`` / ``hit`` / ``skipped``

    ★ ``gate`` 是 :class:`_RoiGate`。判据不通过时**直接返回 ``skipped``**，
      连 ``ocr_region`` 都不调 —— 省下的就是那 1.8~2.0s。
      传 ``None`` = 不设门（旧行为），单测两种路径都用得上。
    """
    import re

    from autopilot.perception import teleport_ocr as TP

    try:
        region = TP.crop_info_box(frame_image)
    except ValueError:
        return "crop_fail", None, [], {}
    feats = _roi_features(region)
    if gate is not None:
        go, _forced = gate.check(feats)
        if not go:
            return "skipped", None, [], feats
    raw = list(TP.ocr_region(region))
    if not raw:
        return "no_text", None, [], feats
    # 复刻 teleport_ocr._clean_lines 的**最小**部分，只为把「读到了什么」
    # 显示出来给人看 —— 真正的清洗与匹配仍由 teleport_ocr 负责，不重复实现。
    cred = [t for t in raw if re.search(r"[\u4e00-\u9fff]", t or "")
            and len(t) >= 2]
    if not cred:
        return "no_text", None, [], feats
    tgt = TP.recognize_teleport(frame_image, points=points, lines=raw)
    if tgt is None:
        return "no_match", None, cred, feats
    return "hit", tgt, cred, feats


def main() -> int:
    title, seconds, channel, delay, gate_on = _parse_args()

    from autopilot.perception.builder import StateBuilder
    from autopilot.perception.capturer import CaptureError, Capturer
    from autopilot.perception.loaders import teleport_points as TPL
    from autopilot.perception.localization_service import (
        RESET_TIMEOUT_S, LocalizationService,
    )
    from autopilot.perception import teleport_ocr as TP

    # ---- 传送点表 -------------------------------------------------------
    hr("0 · 传送点表")
    # ★ 为什么绕这一下：**teleport_ocr 自己那份 _TP_SEARCH 只有两个
    #   硬编码路径，不含本机 BetterGI 安装**，而它的兄弟加载器
    #   loaders/teleport_points 恰恰实现了 BetterGI 探测（文件名是
    #   tp.json 且埋在 GameTask/AutoTrackPath/Assets 三层下）。
    #   所以：**loader 负责「定位文件」，teleport_ocr 负责「解析成候选表」**。
    #   两边都是公开 API，不需要改 perception 任何模块。
    #   （teleport_ocr.load_teleport_points(path) 显式传路径时直接读该路径。）
    try:
        tp_path = TPL.resolve_path()
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ 定位传送点数据失败：{type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2
    if tp_path is None:
        print("  ✗ 找不到传送点数据。四级来源全查过了：\n", file=sys.stderr)
        # _not_found_detail 会逐级列出「查了什么」，排查时一眼看出是
        # 没配环境变量 / 自有数据没生成 / BetterGI 装在别处。用 try 包住
        # 是因为它是私有的 —— 万一改名，不该让诊断脚本自己先崩。
        try:
            print("    " + TPL._not_found_detail(None).replace("\n", "\n    "),
                  file=sys.stderr)
        except Exception:  # noqa: BLE001
            print("    （细节接口不可用）", file=sys.stderr)
        print("\n  这是 BetterGI 的 GPL-3.0 资产，本项目不内置。", file=sys.stderr)
        print("  装一份 BetterGI，或设 NEKO_TELEPORT_POINTS 指向 tp.json。",
              file=sys.stderr)
        return 2
    try:
        points = TP.load_teleport_points(tp_path)
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ 解析 {tp_path} 失败：{type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2

    n_tp = sum(1 for p in points if p.get("tran_position"))
    # ⚠️ _classify 的返回标注写的是 TeleportPointSource，实际返回的是
    #    裸 str —— getattr 两边都兜住，免得这种不一致把诊断脚本搞崩。
    try:
        _src = TPL._classify(tp_path)
        _src = getattr(_src, "value", _src)
    except Exception:  # noqa: BLE001
        _src = "?"
    print(f"  数据文件         : {tp_path}")
    print(f"  来源             : {_src}")
    print(f"  传送点           : {len(points)} 个")
    print(f"  有落点坐标       : {n_tp}/{len(points)}"
          f"（落点用于定位，不用传送点本体）")
    print(f"  OCR 后端         : {TP.get_backend()}")
    print(f"  RESETTING 超时   : {RESET_TIMEOUT_S:.0f}s")

    # ---- 服务 + builder -------------------------------------------------
    hr("1 · 构造 LocalizationService + StateBuilder")
    svc = LocalizationService()
    # entity_detector=None -> 不加载 bgi_world（GPL 资产 + 1.2s），
    # 本脚本不关心世界实体。scene_detector 各用一个：svc 自己驱动契约，
    # builder 只负责打 scene.id 标签；同一个对象被 update 两次会让去抖
    # 时序难读，不如各留各的。
    builder = StateBuilder(entity_detector=None)
    print(f"  场景检测器       : {type(svc.scene).__name__}（svc）+ "
          f"{type(builder._scene_detector).__name__}（builder）")
    print(f"  初始状态         : {svc._state.value}")
    print(f"  初始位置         : {_fmt_pos(svc.get_position())}")

    # ---- 截图 -----------------------------------------------------------
    hr("2 · 构造 Capturer")
    try:
        cap = Capturer(title, force_channel=channel)
        cap.open()
    except ValueError:
        print(f"\n  ✗ 通道名 {channel!r} 不认识。可选：dxcam / mss / printwindow",
              file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"\n  ✗ 找不到窗口 {title!r}：{type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2
    print(f"  实际通道         : {cap.get_active_channel().value}")
    if cap.window_rect:
        _x, _y, _w, _h = cap.window_rect
        print(f"  客户区           : {_w}x{_h}")
        if _w <= 0 or _h <= 0:
            print("  ⚠ 尺寸为 0 —— 窗口最小化了，抓不到画面。")

    if delay > 0:
        hr("2.5 · 延迟启动")
        end = time.monotonic() + delay
        print(f"\n  [DELAY] {delay:g} 秒后开始 —— ★ 现在切回游戏，让它在前台！")
        print("  然后：开大地图 -> 点一个传送点 -> 尽快确认传送 -> 等落地")
        while True:
            left = end - time.monotonic()
            if left <= 0:
                break
            print(f"    剩余 {left:5.1f} s\r", end="", flush=True)
            time.sleep(0.1)
        print()

    # ---- 主循环 ---------------------------------------------------------
    hr(f"3 · 抓 {seconds:g}s")
    t0 = time.monotonic()
    n_frames = 0
    # ★ ocr_calls 数的是**真正调了引擎**的次数。早先的 ocr_tries 数的是
    #   「通过了节流判断的循环轮次」，其中绝大多数因为 scene 不是 WORLD
    #   而**根本没调引擎** —— 那个数字把人带偏了（§41）。
    ocr_calls = 0
    ocr_hits = 0
    #: 结果分类计数：hit / no_text / no_match / crop_fail
    ocr_hist: dict[str, int] = {}
    #: 每次 OCR 的时间戳，用于汇总里画时间分布
    ocr_times: list[float] = []
    last_ocr_try = 0.0
    last_note = None
    last_state = None
    # 离开大世界后的加密窗口截止时刻
    _dense_until = None
    _last_scene_was_world = True
    #: 写进 CSV 的行数
    _n_feat = 0
    #: 其中 has_textbox=1 / 留空（裁剪退化）的行数，汇总要用
    _feat_true_n = 0
    _feat_blank = 0
    #: OCR 前置门（§45）。``--no-gate`` 可关掉退回逐帧 OCR。
    _gate = _RoiGate() if gate_on else None

    # ---- ROI 特征 CSV（§43）-------------------------------------------
    # ★ **本轮只采集，不拿它做任何判据。** 拿到数据、看清两类分布是否
    #   可分之后，才谈阈值。现在加门 = 拍脑袋。
    # ★ 边跑边 flush：中途 Ctrl+C / 抓帧失败也留下已采到的行 ——
    #   标定数据最怕的就是「跑完了但因为异常没落盘」。
    # ★ csv.writer() 包**内置** open() 的句柄；csv 模块自己没有 open
    #   （曾经写成 _csvmod.open(...) 报 AttributeError，§43）。
    _csv = _fh = None
    try:
        import csv as _csvmod

        FEAT_CSV.parent.mkdir(parents=True, exist_ok=True)
        _fh = open(FEAT_CSV, "w", newline="",
                   encoding="utf-8-sig")        # Excel 友好
        _csv = _csvmod.writer(_fh)
        _csv.writerow(FEAT_COLS)
        _fh.flush()
        print(f"  ROI 特征 CSV     : {FEAT_CSV}")
        if _gate is None:
            print(f"    （前置门已用 --no-gate 关闭：逐帧 OCR）")
        else:
            print(f"    前置门           : mean_sat<={_gate.sat_max:.0f} 且 "
                  f"color_var>={_gate.var_min:.0f} 才跑 OCR")
            print(f"    失效保护         : 连续 {_gate.force_every} 帧判据为假"
                  f" -> 强制放行一次")
        print(f"    阈值来源：8 正 / 31 负样本标定（§44），**每次运行都会"
              f"用 gate 列复验**")
    except Exception as exc:  # noqa: BLE001 - 采集失败不该让验证跑不下去
        _csv = None
        if _fh is not None:
            try:
                _fh.close()
            except Exception:  # noqa: BLE001
                pass
            _fh = None
        print(f"  ⚠ ROI 特征 CSV 打不开：{type(exc).__name__}: {exc}",
              file=sys.stderr)
        print("    采集失败不影响主流程，继续跑 OCR 验证。", file=sys.stderr)

    # 契约三段的时间戳（相对开始，单位秒）
    t_select = t_loading = t_restored = t_abort = None
    pos_before = None
    prev_pos = svc.get_position()

    try:
        while True:
            now = time.monotonic()
            el = now - t0
            if el >= seconds:
                break

            frame = cap.grab()
            ts = now
            state = builder.build(frame)
            ls = svc.on_frame(frame.image, ts)      # ★ 本脚本自己驱动
            n_frames += 1
            pos = svc.get_position()

            # builder 吞掉的定位异常会变成事件 —— 这里必须打出来，
            # 否则「定位挂了但脚本还在跑」根本看不出来
            for ev in builder.drain_events():
                print(f"  ⚠ 事件 {ev.code}: {ev.message}")

            # ---- ① SELECT ------------------------------------------------
            # ★★★ **不要用 scene.id 做 OCR 的前置门**（§41 修掉的 bug）
            #   信息框是**大地图 UI 的一部分** —— 它出现的唯一条件就是
            #   「地图开着」，而地图开着时场景恰恰**不是**大世界。
            #   早先这里写的是 `if state.scene.id == "WORLD":` 才跑 OCR，
            #   等于把引擎挡在它唯一能命中的场景之外。
            #
            #   之所以 60s 里还命中过 2 次，纯属侥幸：SceneDetector 的
            #   进入去抖 N=3.0s 决定了「开地图后前 3 秒 scene 仍是 WORLD」。
            #   也就是说**只有你在 3 秒内点下传送点才会命中** —— 实测
            #   8 次传送命中 2 次，正好是这个比例。手慢就永远命中不了。
            #
            #   现在改成按场景自适应：
            #     · 非大世界（地图开着）-> 每帧都试（信息框就在这儿）
            #     · 大世界              -> 1s 一次（兜底去抖窗口）
            #     · 刚离开大世界 5s 内   -> 强制每帧
            if ls.state.value == "RESETTING":
                pass                      # 契约进行中，不再重复 SELECT
            else:
                _gap = (OCR_EVERY_MENU_S if state.scene.id != "WORLD"
                        else OCR_EVERY_WORLD_S)
                if _dense_until is not None and el <= _dense_until:
                    _gap = 0.0            # 加密窗口：无条件每帧
                if now - last_ocr_try >= _gap:
                    last_ocr_try = now
                    _why, tgt, _lines, _feats = _probe_teleport(
                        frame.image, points, gate=_gate)
                    _skipped = (_why == "skipped")
                    ocr_hist[_why] = ocr_hist.get(_why, 0) + 1
                    if not _skipped:
                        # ★ 只有真跑了引擎才计数 —— 被门挡掉的那些
                        #   不算「OCR 尝试」，否则统计又骗人了。
                        ocr_calls += 1
                        ocr_times.append(el)
                    # ---- ROI 特征落盘（§43/§45）--------------------------
                    # has_textbox 的定义按任务书：「读到含中文行 = True」。
                    # ⚠️ 它是个**偏宽松**的标签 —— 大地图打开但没选点时，
                    #    右上角那块也有中文（地图 UI 文字），同样记 True。
                    #    所以额外写 why / matched 两列，标定时可按更严的
                    #    `matched`（真匹上 936 点之一）再切一刀。
                    # gate 列让**每次运行都在验证阈值** —— 一旦出现
                    # 「gate=skip 但本该命中」，CSV 里立刻可见。
                    if _why in ("crop_fail", "skipped"):
                        _htb = ""          # 无从判断标签，留空而非 False
                        if _why == "crop_fail":
                            _feat_blank += 1
                    else:
                        _htb = "1" if _why in ("hit", "no_match") else "0"
                        if _htb == "1":
                            _feat_true_n += 1
                    _n_feat += 1
                    if _csv is not None:
                        _csv.writerow([
                            f"{el:.2f}", state.scene.id,
                            _feats.get("edge_density", ""),
                            _feats.get("color_var", ""),
                            _feats.get("mean_sat", ""),
                            _htb, _why,
                            "1" if _why == "hit" else "0",
                            "skip" if _skipped else "pass",
                        ])
                    if _skipped:
                        # ★ 跳过时**一行都不打**（§45 约束：保帧率）。
                        #   需要复盘时看 CSV 的 gate 列。
                        pass
                    elif _why == "crop_fail":
                        print(f"t={el:5.1f}s  OCR → ✗ 裁剪退化（画面太小）")
                    elif _why == "no_text":
                        print(f"t={el:5.1f}s  OCR → · 无信息框"
                              f"（信息框内读不到中文行）")
                    elif _why == "no_match":
                        print(f"t={el:5.1f}s  OCR → ✗ 读到文字但没匹配上"
                              f"  行={_lines[:3]}")
                    if tgt is not None:
                        ocr_hits += 1
                        pos_before = pos
                        svc.on_teleport_ocr(tgt)
                        t_select = el
                        # ★★ 每次新的 ① SELECT 都必须**清掉上一次传送的
                        #   ②/③**（§44）。否则连续传送时，汇总会拿
                        #   「最后一次 SELECT」去配「第一次传送的
                        #   LOADING/RESTORED」，拼出一条根本不存在的
                        #   三段 —— 实测报出 ①=105.8s / ②=29.8s / ③=34.0s，
                        #   看起来像契约卡了 76 秒，其实只是记账没清。
                        t_loading = t_restored = t_abort = None
                        print(f"t={el:5.1f}s  OCR → ★ 命中 "
                              f"id={tgt.matched_id} {tgt.name!r} "
                              f"conf={tgt.confidence:.3f}")
                        print(f"      ★① SELECT @ t={el:5.1f}s")
                        print(f"      main/sub     = {tgt.main_title!r} / "
                              f"{tgt.sub_title!r}")
                        print(f"      tran_position= {tgt.tran_position}"
                              f"   ← 落点用它，不是 position")
                        print(f"      传送前位置   = {_fmt_pos(pos_before)}")
                        print(f"      ★ 已进 RESETTING，位置**尚未提交** "
                              f"（等 ②LOADING + ③RESTORED）")
            # 场景离开大世界 -> 打开加密窗口
            if state.scene.id != "WORLD" and _last_scene_was_world:
                _dense_until = el + DENSE_AFTER_LEAVE_S
                print(f"  ▸ t={el:5.1f}s  离开大世界 -> 接下来 "
                      f"{DENSE_AFTER_LEAVE_S:g}s **每帧** OCR（加密窗口）")
            _last_scene_was_world = (state.scene.id == "WORLD")

            # ---- ② LOADING / ③ RESTORED 的时间点 -------------------------
            if t_select is not None and t_loading is None \
                    and ls.state.value == "RESETTING" and not ls.is_world:
                t_loading = el
                print(f"  ★② LOADING @ t={el:5.1f}s  "
                      f"(+{el - t_select:.1f}s)  场景转非大世界")
                print(f"      ⚠ 此刻可能还在大地图里 —— ② 只要求「非大世界 3s」，")
                print(f"        不一定是真正的传送加载。见脚本开头说明。")
            if t_select is not None and t_loading is not None \
                    and t_restored is None and ls.state.value != "RESETTING":
                t_restored = el
                print(f"  ★③ RESTORED @ t={el:5.1f}s  "
                      f"(+{el - t_loading:.1f}s)  离开 RESETTING -> "
                      f"{ls.state.value}")
                if "abort" in (ls.note or ""):
                    t_abort = el
                    print(f"      ✗ 契约作废：{ls.note}")
                    print(f"        位置应保持 {_fmt_pos(pos_before)}")
                else:
                    print(f"      传送后位置   = {_fmt_pos(pos)}")
                    if pos_before is not None and pos != pos_before:
                        dx, dz = pos[0] - pos_before[0], pos[1] - pos_before[1]
                        print(f"      位移         = ({dx:+.1f}, {dz:+.1f})"
                              f"  直线 {((dx*dx+dz*dz) ** 0.5):.1f} m")

            # ---- 每帧状态行 ----------------------------------------------
            # ★ 每帧都打。60s x 5Hz = 300 行看着多，但「哪一帧开始不对」
            #   这个信息一旦丢掉，排查成本翻好几倍。
            tag = ""
            if ls.state is not last_state or (ls.note or "") != last_note:
                tag = f"  ← {ls.state.value}" + (
                    f" / {ls.note}" if ls.note else "")
            print(f"t={el:5.1f}s  state={ls.state.value:13} "
                  f"scene={state.scene.id:8} is_world={str(ls.is_world):5} "
                  f"pos={_fmt_pos(pos):22} age={ls.position_age_s:4.1f}s"
                  f"{tag}")
            last_state = ls.state
            last_note = ls.note
            if pos != prev_pos:
                prev_pos = pos

            sleep_s = TICK_S - ((time.monotonic() - t0) % TICK_S)
            if sleep_s > 0:
                time.sleep(sleep_s)
    except KeyboardInterrupt:
        print("\n  ⚠ 收到 Ctrl+C，提前结束。")
    except CaptureError as exc:
        # 响亮失败，但**给可执行的指引**而不是裸 traceback —— 抓不到画面
        # 绝大多数情况是「窗口最小化」或「游戏没在前台」，两者的处理不同。
        print(f"\n  ✗ 抓帧失败：{exc}", file=sys.stderr)
        if cap.window_rect and cap.window_rect[2] <= 0:
            print("    · 客户区尺寸为 0 —— 窗口**最小化**了。"
                  "最小化的窗口 PrintWindow 只能抓到一条边。", file=sys.stderr)
        else:
            print("    · 确认游戏窗口是**正常显示**的（不是最小化到托盘）。",
                  file=sys.stderr)
        print("    · mss 是屏幕坐标抓取，游戏被遮挡时会抓到遮挡窗口且**不报错**；"
              "看不到正确画面就加 --channel printwindow。", file=sys.stderr)
        print("    · 刚才的 0~2 段输出说明了数据与服务都是好的，"
              "只有抓帧这一环没通。", file=sys.stderr)
        return 2
    finally:
        if _csv is not None and _fh is not None:
            try:
                _fh.flush()
                _fh.close()
            except Exception:  # noqa: BLE001
                pass
        cap.close()

    # ---- 汇总 ----------------------------------------------------------
    hr("4 · 汇总")
    print(f"  抓帧数           : {n_frames}（{seconds:g}s，"
          f"实测 {n_frames/max(seconds,1e-9):.1f} fps）")
    print(f"  OCR 真正调用/命中: {ocr_calls} / {ocr_hits}")
    if ocr_calls:
        print(f"  OCR 结果分类     : "
              f"命中 {ocr_hist.get('hit',0)} / "
              f"无信息框 {ocr_hist.get('no_text',0)} / "
              f"没匹配上 {ocr_hist.get('no_match',0)} / "
              f"裁剪退化 {ocr_hist.get('crop_fail',0)}")
    if ocr_times:
        # 时间分布：哪些时刻在跑 OCR，一眼看出采样有没有跟上你的操作
        _buckets = _histogram(ocr_times, 5.0)
        print(f"  OCR 时间分布(5s一档): "
              f"{', '.join(f'{k}-{k+5}s:{v}' for k, v in sorted(_buckets.items()))}")
    if _gate is None:
        print("  OCR 前置门       : 已用 --no-gate 关闭（逐帧 OCR）")
    else:
        _gs = _gate.stats()
        _skipped_share = (_gs["skip"] / max(1, _gs["skip"] + _gs["pass"]))
        print(f"  OCR 前置门       : 放行 {_gs['pass']} / 跳过 {_gs['skip']} / "
              f"失效保护触发 {_gs['forced']} 次")
        print(f"    · 跳过率         : {_skipped_share*100:.0f}%"
              f"（每次省约 1.9s 的引擎开销）")
        print(f"    · 其中放行后命中 : {ocr_hits}")
        if _gs["forced"]:
            print(f"    ⚠ 失效保护触发 {_gs['forced']} 次 —— 判据可能与当前环境"
                  f"不匹配。")
            print(f"      看 CSV 的 gate/has_textbox 两列：若存在 "
                  f"gate=skip 却本该命中的行，")
            print(f"      说明阈值需要重标（或直接加 --no-gate）。")
        if _gs["skip"] == 0 and ocr_calls > 0:
            print("    · 一次都没跳过 —— 判据在这段数据上没起作用，"
                  "值得查一下阈值。")
    print(f"  ROI 特征行数     : {_n_feat}  ->  {FEAT_CSV}")
    if _n_feat == 0:
        print("    · 一行都没采到 —— 抓帧没成功，或 OCR 一次都没触发。")
    else:
        n_tb = _feat_true_n
        print(f"    · has_textbox=1 的有 {n_tb} 行，"
              f"=0 的有 {_n_feat - n_tb - _feat_blank} 行，"
              f"空标签（裁剪退化）{_feat_blank} 行")
        if n_tb == 0:
            print("    ⚠ **没有一行 has_textbox=1** —— 本次没捕捉到信息框，"
                  "拿不到正样本，")
            print("      无法标定阈值。重跑时要确保「点过传送点」。")
    if ocr_calls and not ocr_hits:
        print("    → 引擎跑过但一次没中。按分类看：")
        if ocr_hist.get("no_text"):
            print(f"      · {ocr_hist['no_text']} 次「无信息框」—— "
                  f"信息框没显示，或 OCR 后端 {TP.get_backend()} 读不出中文")
        if ocr_hist.get("no_match"):
            print(f"      · {ocr_hist['no_match']} 次「有文字没匹配上」—— "
                  f"看上面打印的行，对照 tp.json 里的 name/areas")
        if ocr_hist.get("crop_fail"):
            print(f"      · {ocr_hist['crop_fail']} 次「裁剪退化」—— 画面太小")
    print()
    if t_select is None:
        print("  ✗ 本次没有触发 ① SELECT —— 契约根本没开始。")
    elif t_abort is not None:
        print(f"  ✗ 契约作废（{RESET_TIMEOUT_S:.0f}s 超时或场景没回来）"
              f"—— 位置应保持旧值。")
        print(f"    最终位置       : {_fmt_pos(svc.get_position())}")
    elif t_restored is not None:
        # ⚠️ t_loading 靠「t_restored 只在 t_loading 之后才置位」这个
        #   不变式保证非 None，**没有显式守卫** —— 改上面的判定条件时
        #   很容易连带炸在这里（TypeError: unsupported format string
        #   passed to NoneType）。汇总段是「跑完才看」的部分，
        #   炸掉等于把唯一的验证结果也弄丢，所以显式兜底。
        _tl = t_loading if t_loading is not None else float("nan")
        print("  ✓ 三段全过，位置已刷新：")
        print(f"    ① SELECT       : t={t_select:.1f}s")
        print(f"    ② LOADING      : t={_tl:.1f}s (+{_tl - t_select:.1f}s)")
        print(f"    ③ RESTORED     : t={t_restored:.1f}s "
              f"(+{t_restored - _tl:.1f}s)")
        print(f"    总耗时         : {t_restored - t_select:.1f}s")
        print(f"    最终位置       : {_fmt_pos(svc.get_position())}")
    else:
        print("  … ① 触发了但 ②/③ 没走完（可能脚本先超时退出了）。")
        print(f"    最终状态       : {svc._state.value}")
        print(f"    最终位置       : {_fmt_pos(svc.get_position())}")

    hr()
    print("  ✓ 跑完。")
    print(SEP)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - 响亮失败，不吞
        traceback.print_exc()
        raise SystemExit(1)
