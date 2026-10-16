"""配置数据模型 + 默认值 + 边界收缩。

★ 为什么是「收缩到边界」而不是「拒绝启动」
-----------------------------------------
配置是用户手写的。写错一个数字就把主控台拒之门外，是最差的体验 ——
用户看到的是"程序起不来"，而不是"你把宽度写成了 99999，我帮你改成 4000"。

所以：**任何非法值都缩回合法区间，并记一条 WARN**。程序永远能起来。
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
ThemeName = Literal["dark", "light"]

#: 引擎仓库的**出厂默认位置**。
#: 换机器时只要改这一个常量（或直接改 config.toml），不用动其它代码。
def _discover_plugin_dir() -> str:
    """沿目录树往上找 ``<某层>/plugin/plugins/genshin_game_buddy``。

    引擎和主控台在同一个发布包里时，这个发现必然成功；分开 clone 时
    靠 ``NEKO_ENGINE_PATH`` 指过来。**不给写死的绝对路径兜底** ——
    那只会让报错变成"路径不存在"，看不出是没配还是文件没了。
    """
    marker = Path("plugin") / "plugins" / "genshin_game_buddy"
    for parent in Path(__file__).resolve().parents:
        cand = parent / marker
        if (cand / "plugin.toml").is_file():
            return str(cand)
    return ""


DEFAULT_ENGINE_PATH = os.environ.get(
    "NEKO_ENGINE_PATH", "").strip() or _discover_plugin_dir()
#: 引擎解释器。留空时 :meth:`EngineConfig.validate_paths` 会把"没配解释器"
#: 报成一条可读的问题，程序照常启动。
DEFAULT_ENGINE_PYTHON = os.environ.get("NEKO_ENGINE_PYTHON", "").strip()
#: 寻路脚本 ``e2e_pathfind.py`` 的出厂位置。
#:
#: ★ 为什么它不在插件里
#:   它是 ``recognition`` 仓库下的开发期脚本（208KB，且依赖同目录的
#:   ``uipi_check.py`` 等）。早先 Console 按 ``<engine.path>/autopilot/tools/
#:   e2e_pathfind.py`` 去找它 —— 那个目录**存在，但里面没有这个文件**，
#:   于是点「启动」永远被 :meth:`PathfindChannel.path_problems` 拒掉。
#:   这里把它显式化，不再假装它住在插件里。
#:
#: ★ 终态是迁进插件（Console 与插件同源）
#:   一旦迁完，:meth:`EngineConfig.resolved_pathfind_script` 的第 2 优先级
#:   会自动接管，本常量退休。
DEFAULT_PATHFIND_SCRIPT = ""  # 留空 = 交给 resolved_pathfind_script 自动发现
#: 引擎把运行时文件放在 <autopilot> 下的这个子目录里
ENGINE_RUNTIME_SUBDIR = ("autopilot", "_runtime")

#: 卡片大小倍率的合法区间。
#:
#: ★ 这份数字是引擎侧 ``autopilot/ui/overlay/theme.py`` 里
#:   ``USER_SCALE_MIN / USER_SCALE_MAX`` 的**镜像**。
#:   主控台不 import 引擎（独立进程、独立仓库、独立 venv），所以只能各存
#:   一份。改范围时**两边都要改**，否则会有一边夹、一边不夹。
OVERLAY_SCALE_MIN = 0.5
OVERLAY_SCALE_MAX = 3.0


class OverlayConfig(BaseModel):
    """悬浮卡片（tkinter overlay）配置。

    overlay 是**独立的 tkinter 进程**，没有 IPC 通道 —— 主控台只能靠
    「进程存活 + 轮询状态文件」判断它。所以这里只有启停相关配置。
    """

    model_config = ConfigDict(extra="ignore")

    #: 引擎启停时是否自动启停卡片。默认**关** ——
    #: 用户可能只想看引擎数据、不想屏幕上多一个浮窗。
    follow_engine: bool = False
    #: 跑 overlay 用的 Python。**空串 = 复用 [engine].python**
    #: （overlay 需要 import tkinter + PIL，用引擎那个 venv 更稳）
    python: str = ""
    #: 额外命令行参数，例如 ["--alpha", "0.6"]
    args: list[str] = Field(default_factory=list)
    #: 卡片大小倍率。``1.0`` = 系统默认大小；``1.25`` = 大两成半。
    #:
    #: ★ 这不是绝对缩放 —— 引擎会再乘一次它自己探测到的系统 DPI 缩放。
    #:   所以这里的含义是「比系统默认大/小多少倍」。1.0 在 150% 屏上
    #:   实��就是 1.5 倍物理像素，这是对的，不是 bug。
    scale: float = 1.0

    # -- 派生 ------------------------------------------------------------
    def resolved_python(self, engine_cfg: "EngineConfig") -> Path:
        """实际使用的 Python。空串时复用引擎那个。"""
        raw = (self.python or "").strip()
        return Path(raw) if raw else engine_cfg.resolved_python()

    def resolved_runtime_dir(self, engine_cfg: "EngineConfig") -> Path:
        """卡片位置文件所在目录 —— 和引擎共用同一个 runtime 目录。"""
        return engine_cfg.resolved_runtime_dir()

    def resolved_args(self) -> list[str]:
        """拼给 ``python -m autopilot.tools.overlay`` 的完整参数。

        ``scale`` 在这里被翻译成 ``--scale``。**只在不等于 1.0 时才加** ——
        默认值不需要占命令行；用户想「恢复默认大小」时把 config 里的
        ``scale`` 改回 1.0（或删掉这行）即可。

        用户在 ``args`` 里手写了 ``--scale`` 也没关系：本函数追加在后面，
        argparse 取**最后一个**生效，所以字段值优先。
        """
        args = [str(a) for a in self.args]
        if abs(self.scale - 1.0) > 1e-6:
            args += ["--scale", f"{self.scale:g}"]
        return args


def _flatten_nested(
    raw: Any,
    model: type[BaseModel],
    fallback: dict[str, Any],
    notes: list[str],
    label: str,
    numeric_bounds: dict[str, tuple[float, float]] | None = None,
) -> BaseModel:
    """把嵌套段摊平成 dict 做收缩，收缩完构回原类型。

    ★ 为什么必须有这一步
    ------------------
    ``data = dict(self)`` 只做**一层**浅拷贝：嵌套的 BaseModel 原样保留。
    而 ``ConsoleConfig(**data)`` 在更早的 :func:`_build` 里已经把 TOML 的
    ``[engine]`` 表校验成了 ``EngineConfig`` 实例。所以到了收缩阶段，
    ``isinstance(x, dict)`` 恒为 ``False`` —— 一旦按 dict 判断，
    整段会被判"非法"退回默认，**用户改该段任何字段都不生效**。

    BaseModel -> model_dump() -> 收缩 -> model_validate() 往返，
    类型不丢、字段值保住。
    """
    if isinstance(raw, BaseModel):
        data: dict[str, Any] = dict(raw.model_dump())
    elif isinstance(raw, dict):
        data = dict(raw)
    else:
        if raw is not None:
            notes.append(f"{label}={raw!r} 不是表，回退默认段")
        data = dict(fallback)

    # 数值字段的区间收缩（如 overlay.scale）
    #
    # ★ 为什么遍历 numeric_bounds 而不是遍历 data
    #   EngineConfig 没有 scale 字段。若按 data 判断，"缺这个键" 会被当成
    #   "值非法"，于是**每次启动**都记一条 "engine.scale 不是有限数字"
    #   的假警告，日志里全是噪音，真正的警告反而被埋掉。
    #   所以只管「调用方明确声明了上下限」的字段。
    for key, (lo, hi) in (numeric_bounds or {}).items():
        v = data.get(key)
        default = fallback.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) \
                or not math.isfinite(float(v)):
            notes.append(f"{label}.{key} 不是有限数字，回退 {default}")
            data[key] = default
            continue
        if lo <= v <= hi:
            data[key] = float(v)
        else:
            fixed = lo if v < lo else hi
            notes.append(f"{label}.{key}={v} 超出 [{lo}, {hi}]，收缩为 {fixed}")
            data[key] = fixed

    # 通用字段清洗：args 必须是字符串数组，字符串字段必须是字符串
    args = data.get("args")
    if not isinstance(args, list) or \
            not all(isinstance(a, str) for a in args):
        notes.append(f"{label}.args 不是字符串数组，回退默认")
        data["args"] = list(fallback.get("args", []))
    for key, value in list(data.items()):
        if isinstance(value, bool) or isinstance(value, (int, float)):
            continue
        if isinstance(value, list):
            continue
        default = getattr(model, key, None)
        if default is not None and not isinstance(value, type(default)):
            notes.append(f"{label}.{key} 类型不对（{type(value).__name__}），"
                         f"回退默认")
            data[key] = default
            continue
        if default is None and not isinstance(value, str):
            notes.append(f"{label}.{key} 类型不对，回退空串")
            data[key] = ""

    # 该段里是字符串的字段，空串时用出厂默认补上
    for key in ("path", "python", "runtime_dir"):
        if key in data and isinstance(data[key], str) and not data[key]:
            if data[key] is not None and key in fallback:
                data[key] = fallback.get(key, data[key]) or ""
    if "path" in data and not data.get("path"):
        data["path"] = fallback.get("path", "")
    if "python" in data and not data.get("python"):
        data["python"] = fallback.get("python", "")

    try:
        return model.model_validate(data)
    except Exception as exc:  # noqa: BLE001 - 兜底不能让整个配置失败
        notes.append(f"{label} 段校验失败（{exc}），回退默认段")
        return model.model_validate(dict(fallback))


class EngineConfig(BaseModel):
    """引擎（autopilot）连接配置。"""

    model_config = ConfigDict(extra="ignore")

    #: 引擎仓库目录（绝对路径）。spawn 子进程时的 cwd。
    path: str = DEFAULT_ENGINE_PATH
    #: 跑引擎用的 Python 解释器（绝对路径）。
    python: str = DEFAULT_ENGINE_PYTHON
    #: 引擎启动参数。默认 --dry-run（引擎不会发按键）。
    args: list[str] = Field(default_factory=lambda: ["--dry-run"])
    #: 引擎写状态文件的地方。**留空 = 自动推导**，见
    #: :attr:`resolved_runtime_dir`。
    runtime_dir: str = ""
    #: 启动引擎后主控台是否缩到托盘。
    auto_tray_on_start: bool = False
    #: 寻路脚本 ``e2e_pathfind.py`` 的位置。**留空 = 自动查找**，
    #: 见 :attr:`resolved_pathfind_script`。显式填绝对路径则只认这个。
    pathfind_script: str = ""

    # -- 派生（不改用户填的值）-------------------------------------
    def resolved_runtime_dir(self) -> Path:
        """实际使用的运行时目录。

        ``runtime_dir`` 非空就用它；否则推导为
        ``<path> / autopilot / _runtime``。

        ★ 刻意**不覆盖** ``runtime_dir`` 字段本身 ——
          用户填了空就是"让我自动"，把推导值写回去会让他下次读配置
          时分不清这是自己填的还是算出来的。
        """
        raw = (self.runtime_dir or "").strip()
        if raw:
            return Path(raw)
        return Path(self.path).joinpath(*ENGINE_RUNTIME_SUBDIR)

    def resolved_path(self) -> Path:
        return Path(self.path)

    def resolved_python(self) -> Path:
        return Path(self.python)

    def resolved_pathfind_script(self) -> Path:
        """实际使用的寻路脚本位置。

        ★ 为什么是「三段择一」而不是一条死路径
          ``e2e_pathfind.py`` 现在住在 ``recognition`` 仓库（开发期脚本），
          但它终归是要并进插件的。硬编码任何一边，另一边迁移那天就会静默
          失效 —— 而「启动按钮突然说找不到脚本」是在用户点下去之后才发现的。

          所以优先级是：

          1. 配置显式指定（``pathfind_script`` 非空）—— 用户说了算；
          2. ``<path>/autopilot/tools/e2e_pathfind.py`` 存在就用它（插件内）；
          3. :data:`DEFAULT_PATHFIND_SCRIPT` 存在就用它（当前机器实况）；
          4. 都不存在时返回第 2 条的位置，让报错信息指向「本来该在哪」。

          同 :meth:`resolved_runtime_dir` 的理由：**不把推导值写回字段**，
          用户填了空就是「让我自动」，写回去他就分不清哪个是自己填的了。
        """
        raw = (self.pathfind_script or "").strip()
        if raw:
            return Path(raw)
        in_plugin = self.resolved_path() / "autopilot" / "tools" / "e2e_pathfind.py"
        if in_plugin.is_file():
            return in_plugin
        fallback = Path(DEFAULT_PATHFIND_SCRIPT)
        if fallback.is_file():
            return fallback
        return in_plugin

    def validate_paths(self) -> list[str]:
        """检查路径是否可用。**返回问题列表，不抛** ——
        UI 要能把问题显示出来，而不是让程序起不来。"""
        problems: list[str] = []
        p = self.resolved_path()
        if not p.is_dir():
            problems.append(f"引擎路径无效（不存在）：{p}")
        elif not (p / "autopilot" / "tools" / "run_loop.py").is_file():
            problems.append(f"引擎路径下找不到 autopilot\\tools\\run_loop.py：{p}")
        py = self.resolved_python()
        if not py.is_file():
            problems.append(f"Python 解释器无效（不存在）：{py}")
        return problems


class ConsoleConfig(BaseModel):
    """主控台配置。

    ``extra="ignore"``：不认识��键直接忽略。配置向前兼容比向后
    兼容更重要 —— 老版本程序读新配置不该崩。
    """

    model_config = ConfigDict(extra="ignore")

    # ── 窗口 ──────────────────────────────────────────────────
    window_width: int = Field(default=1100, ge=640, le=3840)
    window_height: int = Field(default=720, ge=480, le=2160)
    start_minimized: bool = False

    # ── 托盘 ──────────────────────────────────────────────────
    #: 首次缩到托盘时是否弹过气泡提示。用户被提示过一次就不该再被打扰。
    tray_notice_shown: bool = False

    # ── 运行时 ────────────────────────────────────────────────
    single_instance: bool = True
    log_level: LogLevel = "INFO"
    theme: ThemeName = "dark"

    #: 启动时是否要求管理员权限。
    #:
    #: ★ 为什么默认 True
    #:   ``e2e_pathfind.py --live`` 有 UIPI 门禁（游戏内核级反作弊禁止低完整性
    #:   级别进程注入按键），不���权就是 3 秒内 ``exit 2``。而症状是
    #:   「Console 显示已启动，两秒后进程自己没了」——极难联想到权限。
    #:
    #: ★ 为什么保留关闭开关
    #:   只用来看状态 / 改规则的用户不需要管理员，而且 UAC 弹窗本身很烦。
    #:   关掉之后 Console 照常启动，寻路按钮置灰并说明原因。
    require_admin: bool = True

    # ── 引擎 / 卡片 ────────────────────────────────────────────
    #: 引擎进程配置（spawn autopilot）
    engine: EngineConfig = Field(default_factory=EngineConfig)
    #: 悬浮卡片配置（spawn tkinter overlay）
    overlay: OverlayConfig = Field(default_factory=OverlayConfig)
    #: 卡片通道名（悬浮卡片走它和主控台通信）
    card_channel: str = "default"

    def clamped(self) -> tuple["ConsoleConfig", list[str]]:
        """返回 ``(合法配置, 收缩说明列表)``。

        先按 schema 校验拿到合法的部分值，再对**仍然不合法**的值做手动
        收缩（pydantic 会在校验时报错而不是返回收缩后的值，所以这里要
        自己来一遍）。
        """
        notes: list[str] = []
        data: dict[str, Any] = dict(self)

        def clamp_int(key: str, lo: int, hi: int) -> None:
            v = data.get(key)
            if isinstance(v, bool) or not isinstance(v, int):
                notes.append(f"{key}={v!r} 不是整数，回退默认")
                data[key] = getattr(ConsoleConfig, key).default
                return
            if not (lo <= v <= hi):
                fixed = lo if v < lo else hi
                notes.append(f"{key}={v} 超出 [{lo}, {hi}]，收缩为 {fixed}")
                data[key] = fixed

        def clamp_choice(key: str, allowed: tuple[str, ...]) -> None:
            v = data.get(key)
            if v not in allowed:
                notes.append(f"{key}={v!r} 不在 {list(allowed)}，回退默认")
                data[key] = getattr(ConsoleConfig, key).default

        clamp_int("window_width", 640, 3840)
        clamp_int("window_height", 480, 2160)
        clamp_choice("log_level", ("DEBUG", "INFO", "WARNING", "ERROR"))
        clamp_choice("theme", ("dark", "light"))

        for key in ("start_minimized", "tray_notice_shown",
                    "single_instance"):
            if not isinstance(data.get(key), bool):
                notes.append(f"{key}={data.get(key)!r} 不是布尔，回退默认")
                data[key] = getattr(ConsoleConfig, key).default

        # ── 嵌套段：engine / overlay ──────────────────────────────
        # ★ 抽成通用函数的原因
        #   上一版把 engine 的处理写死，于是同一个 bug（BaseModel 实例
        #   被当成"不是表" -> 整段退回默认 -> 用户改配置不生效）只修了
        #   一处。加 overlay 段时如果再抄一遍，早晚会再犯。
        #   现在两段共用 _flatten_nested + _revalidate。
        data["engine"] = _flatten_nested(
            data.get("engine"), EngineConfig, DEFAULT_CONFIG["engine"],
            notes, "engine")
        data["overlay"] = _flatten_nested(
            data.get("overlay"), OverlayConfig, DEFAULT_CONFIG["overlay"],
            notes, "overlay",
            numeric_bounds={"scale": (OVERLAY_SCALE_MIN, OVERLAY_SCALE_MAX)})

        return ConsoleConfig(**data), notes


#: 最终兜底。**必须永远存在** —— 即使 TOML 全丢，程序也要能起来。
DEFAULT_CONFIG: dict[str, Any] = {
    "window_width": 1100,
    "window_height": 720,
    "start_minimized": False,
    "tray_notice_shown": False,
    "single_instance": True,
    "log_level": "INFO",
    "theme": "dark",
    # ★ 与 default.toml 的 [engine] 段保持一致，但**独立存在** ——
    #   TOML 全丢了、schema 被改坏时，这里仍是最后一道兜底。
    "engine": {
        "path": DEFAULT_ENGINE_PATH,
        "python": DEFAULT_ENGINE_PYTHON,
        "args": ["--dry-run"],
        "runtime_dir": "",
        "auto_tray_on_start": False,
        # 留空 = 自动查找（见 resolved_pathfind_script）。想钉死就填绝对路径。
        "pathfind_script": "",
    },
    # python 留空 = 复用 engine.python（overlay 需要 import tkinter，
    # 跟引擎同一个 venv 最稳，不用装第二套依赖）
    "overlay": {
        "follow_engine": False,
        "python": "",
        "args": [],
        "scale": 1.0,
    },
    "card_channel": "default",
}


__all__ = [
    "DEFAULT_CONFIG",
    "DEFAULT_ENGINE_PATH",
    "DEFAULT_ENGINE_PYTHON",
    "DEFAULT_PATHFIND_SCRIPT",
    "ENGINE_RUNTIME_SUBDIR",
    "OVERLAY_SCALE_MAX",
    "OVERLAY_SCALE_MIN",
    "ConsoleConfig",
    "EngineConfig",
    "LogLevel",
    "OverlayConfig",
    "ThemeName",
]
