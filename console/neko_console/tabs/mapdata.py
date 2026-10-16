"""寻路 tab 的数据层：坐标换算、传送点、地名解析、导航接口。

放在 ``tabs/`` 下而不是复用引擎那边的东西，原因有两个：

1. ``coord_transform.py`` 在**另一个项目**里
   （``D:\\NEKO\\recognition\\exports\\benchmark\\scripts\\misc\\``）。
   Console 引它等于跨项目硬依赖，别人单独 clone Console 就直接 ImportError。
   所以这里**抄一份仿射系数**，并在 :func:`verify_against_reference` 里
   保留一条「两边必须一致」的检查 —— 系数改了而 Console 没跟，就是回归。
2. 传送点数据（936 条）在 ``D:\\NEKO\\recognition\\data``，同样不该成为
   硬依赖。读不到时降级成「没有锚点可选」，而不是抛异常。

坐标约定（与引擎一致，**别改**）
--------------------------------
底图像素 36864 x 18432（提瓦特大陆 P0），世界坐标是游戏内的米。
``px = A*wx + B*wz + C`` / ``py = D*wx + E*wz + F``，
标定残差 RMS 12.6 px / 最大 28.6 px，用 23 个人工标注的锚点拟合出来的。
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
# 底图数据（只给路径，读不到就降级）
# ══════════════════════════════════════════════════════════════

def _recognition_data() -> Path:
    """``<recognition>/data``。环境变量 > 目录发现 > 空。

    ★ 读不到时**不抛异常**
      缩略图、锚点、传送点都各有降级路径（见本模块各 load_* 函数），
      缺数据只是"地图 tab 少几张图"，不该让主控台起不来。
    """
    env = os.environ.get("NEKO_RECOGNITION_ROOT", "").strip()
    if env:
        return Path(env) / "data"
    for parent in Path(__file__).resolve().parents:
        cand = parent / "recognition" / "configs" / "rois.toml"
        if cand.is_file():
            return parent / "recognition" / "data"
    return Path("recognition") / "data"


RECOGNITION_DATA = _recognition_data()
MAP_THUMB = RECOGNITION_DATA / "mihoyo_maps" / "teyvat_P0_thumb.png"
MAP_ANCHORS = RECOGNITION_DATA / "mihoyo_maps" / "teyvat_P0.png.anchors.json"
TELEPORT_JSON = RECOGNITION_DATA / "_ref_tp_sample.json"

#: 原图尺寸（来自 anchors.json 的 image_size，此处兜底写死）
IMAGE_WIDTH = 36864
IMAGE_HEIGHT = 18432

#: 缩略图实际尺寸。比例必须和 IMAGE_* 一致（2000/36864 = 1000/18432）。
THUMB_WIDTH = 2000
THUMB_HEIGHT = 1000

#: 缩略图像素 -> 原图像素的倍率
THUMB_TO_FULL = IMAGE_WIDTH / THUMB_WIDTH

# ══════════════════════════════════════════════════════════════
# 仿射换算（抄自 recognition/.../coord_transform.py）
# ══════════════════════════════════════════════════════════════

_A = -0.0005251239
_B = -0.9995176200
_C = 23460.212244
_D = -0.9982704816
_E = -0.0004493965
_F = 11242.579297
_DETERMINANT = -0.9977886999

#: 标定质量（抄自源文件，供 UI 提示用）
FIT_RMS_PX = 12.6483
FIT_MAX_RESIDUAL_PX = 28.6371
FIT_ANCHOR_COUNT = 23


def world_to_pixel(world_x: float, world_z: float) -> tuple[float, float]:
    """世界坐标 (米) -> 底图像素坐标。"""
    return (_A * world_x + _B * world_z + _C,
            _D * world_x + _E * world_z + _F)


def pixel_to_world(pixel_x: float, pixel_y: float) -> tuple[float, float]:
    """底图像素坐标 -> 世界坐标 (米)。:func:`world_to_pixel` 的精确逆。

    :raises ValueError: 矩阵奇异（标定数据被改坏）
    """
    if abs(_DETERMINANT) < 1e-9:
        raise ValueError(
            f"仿射矩阵奇异（行列式 {_DETERMINANT:.3e}），无法 pixel->world；请重新标定")
    dx = pixel_x - _C
    dy = pixel_y - _F
    return ((_E * dx - _B * dy) / _DETERMINANT,
            (-_D * dx + _A * dy) / _DETERMINANT)


def thumb_to_world(tx: float, ty: float) -> tuple[float, float]:
    """**缩略图**像素 -> 世界坐标。选点走这条路（底图 139 MB 不能直接加载）。"""
    return pixel_to_world(tx * THUMB_TO_FULL, ty * THUMB_TO_FULL)


def world_to_thumb(world_x: float, world_z: float) -> tuple[float, float]:
    """世界坐标 -> 缩略图像素。"""
    px, py = world_to_pixel(world_x, world_z)
    return px / THUMB_TO_FULL, py / THUMB_TO_FULL


def is_inside(pixel_x: float, pixel_y: float) -> bool:
    """像素是否落在底图范围内。"""
    return 0 <= pixel_x < IMAGE_WIDTH and 0 <= pixel_y < IMAGE_HEIGHT


def verify_against_reference() -> dict:
    """拿源模块的系数对一遍。**两边不一致就说明有人只改了一边。**

    源文件不在时返回 ``{"checked": False}`` —— 不让一个跨项目可选检查
    变成 Console 的启动依赖。
    """
    src = (RECOGNITION_DATA.parent / "exports" / "benchmark" / "scripts"
           / "misc" / "coord_transform.py")
    if not src.is_file():
        return {"checked": False, "reason": "源模块不在本机"}
    ns: dict = {"__file__": str(src), "__name__": "neko_coord_transform"}
    # ★ 必须显式给 __file__ / __name__：``exec(code, ns)`` 传裸 dict 时
    #   Python **不会**自动补上模块级名字。而 coord_transform.py 现在
    #   靠 __file__ 向上找仓库根（见 recognition 侧的路径解析 preamble），
    #   少了它就是 ``name '__file__' is not defined``。
    exec(compile(src.read_text(encoding="utf-8"), str(src), "exec"), ns)  # noqa: S102
    diffs = []
    for name in ("_A", "_B", "_C", "_D", "_E", "_F", "_DETERMINANT"):
        if abs(ns[name] - globals()[name]) > 1e-12:
            diffs.append((name, ns[name], globals()[name]))
    # 用随机点验证换算一致，而不只是比系数
    sample = world_to_pixel(4634.52, 4687.13)
    ref = ns["world_to_pixel"](4634.52, 4687.13)
    if any(abs(a - b) > 1e-6 for a, b in zip(sample, ref)):
        diffs.append(("world_to_pixel", ref, sample))
    return {"checked": True, "diffs": diffs,
            "rms_px": ns.get("FIT_RMS_PX")}


# ══════════════════════════════════════════════════════════════
# 传送点
# ══════════════════════════════════════════════════════════════

#: 传送点类型 -> 人话（直接显示在图例里）
POINT_TYPE_CN = {
    "TeleportWaypoint": "普通传送锚点",
    "Goddess": "七天神像",
    "BlessDomain": "祝福神像",
    "OneTimeDomain": "秘境",
    "TrounceDomain": "岩中幽谷",
    "MasteryDomain": "精通秘境",
    "ForgeryDomain": "伪造怪鸟",
    "NatlanObsidianTotemPole": "黑曜石图腾柱",
    "NodKraiMeetingPoint": "挪德卡莱会面点",
    "Other": "其他",
}


@dataclass(frozen=True)
class TeleportPoint:
    """一个可点击的目的地。"""

    id: int
    name: str
    type: str
    country: str
    areas: tuple[str, ...]
    #: 站位坐标 (x, y, z)，y 是高度
    position: tuple[float, float, float]
    #: 传送后实际落点。★ 寻路要的是这个，不是 position ——
    #: 神像图标所在格子和角色站的地方不是同一个点。
    tran_position: tuple[float, float, float]

    @property
    def xz(self) -> tuple[float, float]:
        """世界坐标 (x, z) —— 寻路只用平面。"""
        return self.position[0], self.position[2]

    @property
    def tran_xz(self) -> tuple[float, float]:
        return self.tran_position[0], self.tran_position[2]

    @property
    def type_cn(self) -> str:
        return POINT_TYPE_CN.get(self.type, self.type)

    @property
    def search_blob(self) -> str:
        """搜索用的拼接串：名字 + 国家 + 地区 + 类型。"""
        parts = [self.name, self.country, self.type_cn, *self.areas]
        return normalize_name(" ".join(p for p in parts if p))


_PUNCT = str.maketrans({c: "-" for c in "，、－—· ・　"})
_SPACES = re.compile(r"\s+")


def normalize_name(s: str) -> str:
    """名字归一化，让「七天神像 - 风」和「七天神像-风」能对上。

    与 ``coord_transform._norm`` 保持同一套规则（含那两条错别字修正）。
    """
    s = str(s or "").strip()
    for bad, good in (("七天神仙", "七天神像"), ("安绕", "安饶")):
        s = s.replace(bad, good)
    s = s.translate(_PUNCT)
    s = _SPACES.sub("", s)
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")


def _as_floats(v) -> tuple[float, float, float]:
    try:
        a, b, c = (float(x) for x in v)
    except Exception:  # noqa: BLE001 - 坏数据不该拖垮整个列表
        return (0.0, 0.0, 0.0)
    return (a, b, c)


@lru_cache(maxsize=1)
def load_teleport_points(path: str | None = None) -> tuple[TeleportPoint, ...]:
    """读传送点。**读不到返回空元组**，不抛 —— 图上只是没有可点的点。

    :returns: 不可变元组，缓存；数据文件变了要调 :func:`reload_points`
    """
    p = Path(path) if path else TELEPORT_JSON
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("传送点数据读不到（%s）：%s", p, exc)
        return ()
    out: list[TeleportPoint] = []
    for scene in doc.get("data", []) or ():
        for pt in scene.get("points", []) or ():
            if not isinstance(pt, dict):
                continue
            name = str(pt.get("name", "")).strip()
            if not name:
                continue
            out.append(TeleportPoint(
                id=int(pt.get("id", 0) or 0),
                name=name,
                type=str(pt.get("type", "Other")),
                country=str(pt.get("country", "")),
                areas=tuple(str(a) for a in (pt.get("areas") or ())),
                position=_as_floats(pt.get("position")),
                tran_position=_as_floats(pt.get("tranPosition")),
            ))
    out.sort(key=lambda t: (t.country, t.name))
    return tuple(out)


def reload_points() -> tuple[TeleportPoint, ...]:
    """强制重新读盘（数据文件换了之后用）。"""
    load_teleport_points.cache_clear()
    return load_teleport_points()


def search_points(text: str, limit: int = 200) -> list[TeleportPoint]:
    """按地名/地区/国家搜传送点。空查询返回空列表。

    匹配是「归一化后包含」，命中即返回，不做相关度排序 —— 936 条里
    精确包含已经够用，真要排序再说（YAGNI）。
    """
    key = normalize_name(text)
    if not key:
        return []
    hits = [p for p in load_teleport_points() if key in p.search_blob]
    return hits[:limit]


# ══════════════════════════════════════════════════════════════
# 任务 B · 起点来源
# ══════════════════════════════════════════════════════════════


class StartSource(Enum):
    """起点坐标从哪来。

    ⚠️ **只有 ``MANUAL`` 和 ``ANCHOR`` 现在真的能用。**

    ``SCREENSHOT_OCR`` / ``LOCALIZATION`` 是接口预留：前者要一个能读出
    屏幕坐标的 OCR 链路，后者要 ``LocalizationService`` 先建立原点
    （必须先在游戏里传送一次）。两者都不通时 ``get_start_position``
    返回 ``None``，UI 据此把「启动」置灰并说明原因 —— 宁可明确说
    「拿不到」，也不要拿一个假坐标去让角色乱走。
    """

    MANUAL = "手动输入"
    ANCHOR = "从地图选点"
    SCREENSHOT_OCR = "截图识别（预留）"
    LOCALIZATION = "定位服务（预留）"

    @property
    def available(self) -> bool:
        return self in (StartSource.MANUAL, StartSource.ANCHOR)

    @property
    def unavailable_reason(self) -> str:
        if self.available:
            return ""
        if self is StartSource.LOCALIZATION:
            return ("需要定位服务先建立原点 —— 必须先在游戏里传送一次，"
                    "否则拿不到任何位置")
        return "需要屏幕坐标 OCR 链路，目前未接"


@dataclass
class StartRequest:
    """一次起点解析请求。"""

    source: StartSource = StartSource.MANUAL
    manual: str = ""              # "x, z" 文本
    anchor: TeleportPoint | None = None
    #: 预留给未来的两个来源；当前恒为 None
    ocr_hint: str | None = None
    localization_hint: str | None = None


def get_start_position(req: StartRequest) -> tuple[float, float] | None:
    """按 ``req.source`` 解析起点世界坐标。

    :returns: ``(x, z)``；拿不到返回 ``None``（**不抛、不猜**）

    预留扩展点：``SCREENSHOT_OCR`` 分支将来接
    ``ocr_hud_position(frame) -> (x, z) | None``；
    ``LOCALIZATION`` 分支将来接
    ``LocalizationService.get_position() -> (x, z) | None``。
    两个都是「返回 None 而不是抛」，这样上层只用处理一种失败。
    """
    if req.source is StartSource.MANUAL:
        return parse_xz(req.manual)
    if req.source is StartSource.ANCHOR:
        return req.anchor.xz if req.anchor else None
    if req.source is StartSource.SCREENSHOT_OCR:
        # 预留：return ocr_hud_position(req.ocr_hint)
        logger.info("截图识别起点尚未接入，返回 None")
        return None
    if req.source is StartSource.LOCALIZATION:
        # 预留：return localization_position(req.localization_hint)
        logger.info("定位服务尚未建立原点，返回 None")
        return None
    return None


def parse_xz(text: str) -> tuple[float, float] | None:
    """解析「4634.52, 4687.13」这类文本。

    :returns: ``(x, z)``；拿不准一律 ``None``。

    ⚠️ **必须恰好两段**。早先的实现只取前两段，于是 ``"1,2,3"`` 被静默
    解析成 ``(1.0, 2.0)`` —— 用户以为填了三个数（可能后面跟了别的），
    实际按前两个走。这是「悄悄按错的坐标跑」，比直接报错糟得多。
    """
    raw = str(text or "").strip().replace("，", ",").replace(" ", ",")
    parts = [p for p in raw.split(",") if p]
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


def fmt_xz(x: float, z: float) -> str:
    return f"{x:.2f}, {z:.2f}"


# ══════════════════════════════════════════════════════════════
# 任务 C · 目标描述
# ══════════════════════════════════════════════════════


@dataclass(frozen=True)
class TargetSpec:
    """「要去哪」的结果。三种形态互斥。"""

    kind: str                       # "point" | "anchor" | "unresolved"
    x: float | None = None
    z: float | None = None
    anchor: TeleportPoint | None = None
    entity_type: str = ""           # "预留：'附近的宝箱' 这类描述"
    query: str = ""                 # 原始文本，排障时看

    @property
    def ok(self) -> bool:
        return self.kind in ("point", "anchor")

    @property
    def label(self) -> str:
        if self.kind == "anchor" and self.anchor:
            return f"{self.anchor.name}（{self.anchor.type_cn}）"
        if self.kind == "point" and self.x is not None and self.z is not None:
            return fmt_xz(self.x, self.z)
        return self.entity_type or "（没解析出来）"


def resolve_target_from_description(text: str) -> TargetSpec:
    """把「我想去 风起地」解析成目标。

    **当前实现只支持传送点精确/包含匹配。**

    未来接 LLM 的位置（预留，不实现）：
    把 ``text`` 交给宿主模型，让它回
    ``{"anchor_name": "风起地"}`` 或 ``{"x": .., "z": ..}``，
    然后仍走 :func:`resolve_target_from_description` 的解析分支 ——
    模型只负责「把自然语言变成一个名字/坐标」，**坐标换算和合法性判断
    仍然在这里做**。这样模型出幻觉时最多是「找不到」，不会是「走错」。

    :returns: 永远返回一个 ``TargetSpec``，解析不出来是
              ``kind="unresolved"``，不抛。
    """
    q = str(text or "").strip()
    if not q:
        return TargetSpec("unresolved", query=q)

    # ① 直接当坐标
    as_xz = parse_xz(q)
    if as_xz is not None:
        return TargetSpec("point", x=as_xz[0], z=as_xz[1], query=q)

    # ② 传送点：优先完全相等，其次包含
    key = normalize_name(q)
    if key:
        pts = load_teleport_points()
        exact = [p for p in pts if normalize_name(p.name) == key]
        if len(exact) == 1:
            return TargetSpec("anchor", anchor=exact[0], query=q)
        if len(exact) > 1:
            # 同名多个（如各地都有「无名遗迹」）——不猜，交回 UI 让用户选
            return TargetSpec("unresolved", entity_type=f"有 {len(exact)} 个同名地点",
                              query=q)
        loose = search_points(q, limit=20)
        if len(loose) == 1:
            return TargetSpec("anchor", anchor=loose[0], query=q)
        if len(loose) > 1:
            return TargetSpec("unresolved",
                              entity_type=f"匹配到 {len(loose)} 个候选，请选",
                              query=q)
    return TargetSpec("unresolved", query=q)


# ══════════════════════════════════════════════════════════════
# 任务 D · 导航接口
# ══════════════════════════════════════════════════════════════


class NavMode(Enum):
    """导航模式。"""

    #: 起点直线走向终点 —— 现在 e2e_pathfind 的 ``--start-world/--target-world``
    DIRECT = "直达"
    #: 给一串路点逐个走 —— 对应 ``user_waypoints_guide.md`` 那套
    WAYPOINTS = "路点"
    #: 自动规划（找路/避障）—— **未实现**，需要地形分类器
    AUTO = "自动规划（预留）"

    @property
    def available(self) -> bool:
        return self is not NavMode.AUTO

    @property
    def unavailable_reason(self) -> str:
        if self.available:
            return ""
        return ("自动规划需要地形可通行性分类器 —— "
                "现在只能直达或走预设路点，没有 A* 那样的绕障能力")


@dataclass
class NavigationRequest:
    """一次导航请求。纯数据，不含执行逻辑。"""

    start: tuple[float, float]
    target: tuple[float, float]
    mode: NavMode = NavMode.DIRECT
    #: WAYPOINTS 模式的中转点
    waypoints: list[tuple[float, float]] = field(default_factory=list)
    #: 真发按键还是只规划（对应 --live）
    live: bool = True
    #: 启动前等待秒数（给用户切窗口）
    delay_s: float = 10.0


@dataclass
class NavigationHandle:
    """导航的运行态句柄。

    现在 :class:`~neko_console.channels.pathfind.PathfindChannel` 已经是
    这个角色的**命令行版本**（起子进程、flag 停止、terminate 兜底）。
    这个 dataclass 是把它抽象成接口的第一步 —— 将来换实现（进程内调用、
    远程下发）时调用方不用改。
    """

    running: bool = False
    pid: int | None = None
    mode: NavMode = NavMode.DIRECT
    request: NavigationRequest | None = None
    note: str = ""

    def describe(self) -> str:
        if not self.running:
            return "未运行"
        return f"运行中（pid={self.pid}，{self.mode.value}）"


#: e2e_pathfind 认的路点文件 schema 版本。
#:
#: ★ 必须与 e2e_pathfind.py 的 ``WAYPOINTS_SCHEMA`` 一致 —— 对不上脚本会
#:   整份判废并 exit 2（它的原话是「别靠『尽力读』，读错字段会让角色朝
#:   反方向走」）。
WAYPOINTS_SCHEMA = 1


def to_waypoints_doc(req: NavigationRequest) -> dict:
    """把 WAYPOINTS 请求转成 e2e_pathfind 认的路点文件内容。**纯函数**。

    schema 见 ``e2e_pathfind.load_waypoints_file``：顶层必须有
    ``schema_version``；``waypoints`` 是 ``{x, z, note}`` 列表；
    ``start`` 只作参考（起点体检比对用），``target`` 是终点。
    """
    return {
        "schema_version": WAYPOINTS_SCHEMA,
        "start": [float(req.start[0]), float(req.start[1])],
        "target": [float(req.target[0]), float(req.target[1])],
        "waypoints": [{"x": float(x), "z": float(z), "note": ""}
                      for x, z in req.waypoints],
    }


def write_waypoints_file(req: NavigationRequest, path) -> Path:
    """把路点写到 ``path``（schema :data:`WAYPOINTS_SCHEMA`）。返回该路径。

    用 ``utf-8`` 写、**不带 BOM**：脚本读的是 ``utf-8-sig``，两种都能读，
    但我们自己产出的文件不该给下游留一个「兼容 BOM」的活。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(to_waypoints_doc(req), ensure_ascii=False,
                            indent=2), encoding="utf-8")
    return p


def nav_to_cli_args(req: NavigationRequest, *, runtime_dir=None,
                    waypoints_name: str = "console_waypoints.json") -> list[str]:
    """把请求翻译成 ``e2e_pathfind.py`` 的命令行参数。

    这是「接口 -> 现有实现」的唯一接缝：将来 ``AUTO`` 落地时，
    这里多拼一个 ``--auto-plan`` 之类，上层调用方不用动。

    ★★ WAYPOINTS 走的是 ``--waypoints-file``，不是 ``--waypoint X,Z``
       ----------------------------------------------------------
       早先这里拼的是 ``--waypoint X,Z``，而 e2e_pathfind 的参数表
       ``_SPEC`` 里**根本没有这个 flag**。它的解析循环对未知参数是
       ``continue``（既不报错也不提示），于是：

       1. ``--waypoint`` 被**静默丢弃**；
       2. 后面的 ``"100.00,200.00"`` 不以 ``-`` 开头，被当成**窗口标题**
          吞掉（``a["title"] = k``）；
       3. 请求退化成一条普通的 ``--target-world`` 直线。

       结果是：用户以为角色在沿路点走，实际在**直线冲向终点**。中间隔了
       一堵墙就是站着不动，而 UI 上一切正常。这比直接报错糟得多，所以
       现在按脚本的真实契约来。

    ⚠️ ``--waypoints-file`` 与三个目标参数（``--target-world`` /
       ``--walk-forward`` / ``--target-tp``）**互斥** —— 路点文件自带
       终点，同时给等于「两份互相矛盾的终点」，脚本 exit 2。所以
       WAYPOINTS 模式下这里**不拼** ``--target-world``。

    :param runtime_dir: WAYPOINTS 模式下路点文件写哪儿。``None`` 且需要
        写文件时抛 :class:`ValueError` —— 没有落地路径就不该返回参数。
    :raises ValueError: 模式不可用（``AUTO``）、或 WAYPOINTS 却没给
        ``runtime_dir``、或 WAYPOINTS 但一个路点都没有。
    """
    if req.mode is NavMode.AUTO:
        raise ValueError(f"{req.mode.value} 尚不可用：{req.mode.unavailable_reason}")
    if req.mode is NavMode.WAYPOINTS:
        return _nav_args_waypoints(req, runtime_dir, waypoints_name)
    return _nav_args_direct(req)


def _nav_args_direct(req: NavigationRequest) -> list[str]:
    args = ["--start-world", f"{req.start[0]:.2f},{req.start[1]:.2f}",
            "--target-world", f"{req.target[0]:.2f},{req.target[1]:.2f}"]
    if req.live:
        args.append("--live")
    if req.delay_s > 0:
        args += ["--delay", str(req.delay_s)]
    return args


def _nav_args_waypoints(req: NavigationRequest, runtime_dir, name: str) -> list[str]:
    if not req.waypoints:
        raise ValueError(
            "WAYPOINTS 模式一个路点都没有 —— 那和 DIRECT 没区别，"
            "会静默退化成直线走向终点。请补路点，或改用 DIRECT。")
    if runtime_dir is None:
        raise ValueError(
            "WAYPOINTS 模式需要 runtime_dir：路点要落成文件给 "
            "--waypoints-file 用，没有落地路径就不能拼参数。")
    wp_path = write_waypoints_file(req, Path(runtime_dir) / name)
    args = ["--start-world", f"{req.start[0]:.2f},{req.start[1]:.2f}",
            "--waypoints-file", str(wp_path)]
    if req.live:
        args.append("--live")
    if req.delay_s > 0:
        args += ["--delay", str(req.delay_s)]
    return args


__all__ = [
    "FIT_ANCHOR_COUNT", "FIT_MAX_RESIDUAL_PX", "FIT_RMS_PX", "IMAGE_HEIGHT",
    "IMAGE_WIDTH", "MAP_ANCHORS", "MAP_THUMB", "POINT_TYPE_CN",
    "TELEPORT_JSON", "THUMB_HEIGHT", "THUMB_TO_FULL", "THUMB_WIDTH",
    "WAYPOINTS_SCHEMA",
    "NavMode", "NavigationHandle", "NavigationRequest", "StartRequest",
    "StartSource", "TargetSpec", "TeleportPoint", "fmt_xz", "is_inside",
    "load_teleport_points", "nav_to_cli_args", "normalize_name",
    "parse_xz", "pixel_to_world", "reload_points", "resolve_target_from_description",
    "search_points", "thumb_to_world", "to_waypoints_doc",
    "verify_against_reference", "world_to_pixel", "world_to_thumb",
    "write_waypoints_file",
]
