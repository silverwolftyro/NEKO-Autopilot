"""直线寻路 demo（真机）—— 不需要三角标。

核心闭环：位移向量**本身就是"当前实际移动方向"**。

    ① 目标方向 θ_t = atan2(tz-wz, tx-wx)
    ② 移动方向 θ_m = atan2(Δwz, Δwx)      ← 窗口位移，见 WINDOW_N
    ③ 夹角     Δ   = wrap(θ_t - θ_m) 到 [-π, π]
    ④ |Δ| > 死区 -> mouse_move_relative(-gain(|Δ|) * Δ_deg)   ← 负号见下
    ⑤ 同时按住 Shift+W（跑步）

★★ 为什么用**世界坐标差**而不是 phase step_px
---------------------------------------------
``LocalizationState.step_px`` 是**小地图像素**空间的位移，且
``TileTracker`` 内部还会做轴转置（``dwx=-dy, dwz=-dx``）。拿它和世界
空间的 ``atan2(tz-wz, tx-wx)`` 求夹角是轴+单位双重错配。

★★ 为什么转向要**取负**
-------------------
``Δ`` 是**数学上**「该往哪转」，而 ``mouse_move(+x)`` 在游戏里是
**逆时针**转视角。两者物理方向相反。真机证据（目标在正东、角色偏北）::

    θ_t=  1.7°  θ_m=-55.2°  Δ=+57°  turn=+28.5px   <- 越转越偏
    θ_t=  7.1°  θ_m=-87.1°  Δ=+94°  turn=+47.1px   <- 越转越偏

**别"顺手修正"这个负号。**

★ 卡住检测用采样队列，不是逐帧计数
----------------------------------
逐帧计数的旧逻辑有两个致命问题：
  1. 「连续 N 帧不动」在**静止**和**卡住**之间无法区分（起步阶段也满足）
  2. 旧的「dist 连续增加 -> 方向反了 -> 强制转 180°」会**清空方向窗口**
     ``hist``，而它是 ``θ_m`` 的唯一来源 —— 清掉后方向修正不再触发，
     角色继续朝错方向跑、dist 继续增加、再次触发脱困、再次清空 ……
     **死循环**（真机实测 40 秒里 |win|=0.00 持续了 31.5 秒、脱困触发 8+ 次）。

现在改成：每 1s 采一个位置，队列 8 个，首尾位移 ``|dx|+|dz| < 3`` 才算卡住。
**脱困时绝不清 ``hist``**，只清 ``prev_pos``。

⚠️ 本脚本**会真的按 Shift+W 和鼠标**（--live 时）。请把游戏切到前台、
   并用**管理员 PowerShell** 跑（UIPI 隔离，见 uipi_check.py）。
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

import atexit
import json
import math
import os
import random
import sys
import tempfile
import time
import traceback
from collections import deque
from pathlib import Path

# ★ 支线目录 misc_bgi/ 里**没有**这些同目录模块（uipi_check /
#   coord_transform / anchors_viewer 都在主线 misc/）。显式把主线加进
#   sys.path，支线才能 import —— 但只**读**，主线一个字都不改。
#   顺序：先本目录（支线自己的优先），再主线。
sys.path.insert(0, str(Path(__file__).resolve().parent))
_MAIN_MISC = Path(__file__).resolve().parent.parent / "misc"
if _MAIN_MISC.is_dir() and str(_MAIN_MISC) not in sys.path:
    sys.path.insert(0, str(_MAIN_MISC))
from uipi_check import check as _uipi_check  # noqa: E402
from uipi_check import probe_channels as _probe_channels  # noqa: E402
from uipi_check import require_admin as _uipi_require_admin  # noqa: E402

PLUGIN_ROOT = Path(
    PLUGIN_DIR)
sys.path.insert(0, str(PLUGIN_ROOT))
# ★ ``VK`` 必须在**模块级**导入：_PanicWatcher 是模块级类，它引用 VK.F12；
#   而 VK 原本只在 main() 内部 import，于是监听线程里抛 NameError，
#   被 ``except Exception: return`` 静默吞掉 —— **急停线程悄悄死掉，
#   F12 永远不生效，且没有任何报错**。
from autopilot.actuator.simulator import VK  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

DEFAULT_TITLE = "原神"
DEFAULT_SECONDS = 60.0
DEFAULT_DELAY = 8.0
DEFAULT_ARRIVE = 5.0
SEP = "=" * 78

# ---------------------------------------------------------------- 方向估计
#: 方向估计的**滑动窗口帧数**。单帧位移仅 0.3~0.7m 而相位相关噪声约
#: 0.1m —— 噪声占 1/3，单帧 atan2 被噪声主导，走 S 型。10 帧（2 秒）
#: 把噪声压到 ~0.03m。
WINDOW_N = 10
#: 窗口位移低于它就**跳过方向修正**（还没起步 / 被挡 / 数据无效）。
WINDOW_MOVE_MIN = 1.0
#: 判定「已经在动」的单帧世界位移。
MOVE_EPS = 0.30

# ---------------------------------------------------------------- 转向增益
# ★★★ 2026-10-04 整段重做：分档绝对增益 -> **误差比例**控制
# ------------------------------------------------------------------
#
# ★ 为什么必须重做（真机数据）
# ----------------------------
# 40 秒 live 出现：转向 156 次、θ_m 在 ±100° 之间**极限环震荡**、
# 打滑恢复误触发 14 次。三个症状是**同一个根因**。
#
# 设 ``G`` = 每 1 像素鼠标位移实际转过的视角（度/px），玩家实测 ≈ 1.0。
# 旧控制律 ``turn_px = -gain * |Δ|``，每帧误差演化是::
#
#     |Δ_next| / |Δ| = |1 + G * gain|
#
#     档位   gain   G*gain   |Δ_next/Δ|   行为
#     <15    1.0    1.0       2.00        发散
#     <40    2.0    2.0       3.00        发散
#     <90    3.0    3.0       4.00        发散
#     >=90   4.0    4.0       5.00        发散
#
# **四档全部落在 |1+G·gain| > 1，一帧就把误差放大一倍以上** —— 这在
# 控制论里叫发散，现象就是极限环震荡。而旧 clamp 按像素定：500 px
# × 1 度/px = **一帧转 500 度**。
#
# ★ 为什么「绝对增益」这个思路本身就是错的
# -----------------------------------------
# ``G`` 不是常数，它由**玩家游戏内鼠标灵敏度档位**决定
# （BetterGI 的 ``GenshinGameInputSettings.cs`` 里就是
# ``mouseSenseIndex`` 0~10 档 + ``mouseSensitivity``）。仓库里从来
# 没有标定过这个值。拿「px/度」的绝对增益去乘一个未知比例，
# 无论调成几都是赌。
#
# ★ 新控制律：按**误差的比例**转，与 G 无关
# ------------------------------------------
#     want_deg = -TURN_FRACTION * Δ        （无量纲比例）
#     turn_px  = clamp(want_deg) / MOUSE_DEG_PER_PX
#
# 实际每帧旋转 = G * FRACTION * Δ，于是
#     |Δ_next| / |Δ| = |1 - G * FRACTION|
# 只要 ``G * FRACTION < 2`` 就稳定；取 FRACTION=0.45 后，
# **G 落在 [0.5, 4.0] 区间里都收敛** —— 对未标定的 G 天然鲁棒。
# 这才是「先收敛，再标定」的正确顺序。

#: 每 1 像素鼠标相对移动 = 多少度视角旋转。
#: **真机标定 2026-10-04**（管理员 PowerShell，2560x1600，游戏内默认灵敏度）：
#:   累计下发 ~1500px -> θ_m 变化 ~90°  =>  1px ≈ 0.06°
#:   逐对实测中位数 Δθ_m/turn_px = -0.07 °/px
#: 取 0.07（逐对直接测量；与累积估计 0.06 相差 15%，在同量级）。
#:
#: ★ 之前取 1.0 是**猜测值**，比真实值大 ~15 倍，等效环路增益只有
#:   0.45 × 0.07 = 0.031 -> 每帧只减 3%，40 帧才降到 28%。
#:   真机表现：Δ 从 -169° 花了 8 秒才到 -61°。填对之后每帧减 45%，
#:   约 4~5 帧收敛。
#: ⚠️ 这是**玩家游戏内鼠标灵敏度档位**的函数（BetterGI
#:    ``GenshinGameInputSettings.cs`` 里就是 ``mouseSenseIndex`` 0~10 档）。
#:    **改游戏内灵敏度就必须回来重标这个值。**
MOUSE_DEG_PER_PX = 0.07

#: 每帧最多纠正 |Δ| 的这个比例。**自拍定标**：
#: 0.45 -> 误差每帧剩 55%，约 5~6 帧收敛到死区；再大就压不住了。
TURN_FRACTION = 0.45

#: 单帧转向的**角度**上限（小角度时），防一步转飞。
TURN_MAX_DEG = 45.0
#: |Δ| 很大时（起步背对目标等）放宽到多少度 —— 但**仍有界**。
#: 旧代码在这里是 500px(=500°)，完全失控。
TURN_MAX_DEG_BIG = 60.0
TURN_BIG_ERR_DEG = 90.0
#: 死区：|Δ| 小于它就不转。**自拍定标**（比旧 5° 略宽，收敛更干净）。
TURN_DEADZONE_DEG = 8.0
# ---------------------------------------------------------------- 大角度断续站定
# ★★★ 2026-10-08 真机：179° 转向走了 60s，绕路系数 **1.82**（直线 40m 实走 72.9m）
# ------------------------------------------------------------------
# 根因不是"边走边转"本身，而是**转向角速度 vs 移动速度**之比：
#
#     弧长 = v × (Δ/ω) = π·v/ω        （Δ=179° ≈ π rad）
#
# 当前 ω = 2.8°/帧 ÷ 0.2s = 14°/s。按走路 v≈3m/s 反推弧长 38.6m，
# 与实测反推的 32.8m 同量级 —— **1.82 是 w=2.8°/帧 的必然结果，不是 bug**。
# 站定能把弧长打到 0，但站定第 8 帧起朝向估计就断了（见下方
# TURN_STAND_SEGMENTS 与 TURN_STAND_BLIND_M）。
TURN_STAND_DEG = 60.0
#: 退出阈值。**与进入阈值差 40° 是滞回**：若都用 60°，转到 59° 就恢复走，
#: 一走偏回 61° 又站定，来回抖。只靠 TURN_STAND_DEG 单阈值必然震荡。
TURN_RESUME_DEG = 20.0
#: ★★ 2026-10-08 换机制：站定期间**拆段加速转**（废掉 3:1 断续站定）
#:
#:   旧机制（断续站定 3:1）：站 3 帧 / 走 1 帧。真机 1 段 77 帧、占空比 64%，
#:   绕路只从 1.82 降到 1.72（5%），等效速度 -12%，而用户视觉是
#:   「突然冲刺一下，突然再转」—— 收益小、副作用大。
#:
#:   新机制：进入站定就**一直不按 W**，每帧把转向拆成多段、段间留够
#:   一个游戏帧的间隔，靠「单位时间转更多角度」把整次转向压缩到 ~8 帧。
TURN_STAND_SEGMENTS = 8
#: 段间隔（毫秒）。★ 必须**大于**游戏一帧，否则多段会被并进同一渲染帧、
#:   重新触发游戏的单次位移上限，拆分等于没拆（2026-10-04 踩过：20ms < 33.3ms）。
#:   40ms 覆盖到 25fps，比我们观测到的最差帧率（30fps）还安全一档。
#:   ⚠️ 代价：每帧最多 8×40 = **320ms 阻塞**，主循环帧间隔会从 200ms
#:   涨到 ~520ms。这会**加速位移窗口见底**（见下方安全阀）。
TURN_STAND_SEG_SLEEP_MS = 40.0
#: ★ 安全阀：站定期间窗口净位移低于它，就必须**走一帧**把 θ_m 养回来。
#:   这不是可选项 —— θ_m 的唯一来源是位移窗口，窗口一死就没有 Δ，
#:   转向判据直接拿不到输入，整套站定逻辑会静默失效。
#:   ★ 取 :data:`WINDOW_MOVE_MIN` 的 1.5 倍（而不是相等）：要**赶在**门禁
#:     掐死之前主动走一帧。等跌到门禁再救就晚了 —— 那一帧 θ_m 已经没了。
#:   ⚠️ 这条与 TURN_RESUME_DEG 是**两个独立出口**：一个按角度收，一个按
#:     窗口保命。站定转不完是常态（见 TURN_STAND_SEGMENTS 的说明）。
TURN_STAND_BLIND_M = 1.5
#: ★★ C2 启动探测腿的**硬上限**（米，2026-10-09）
#:
#:   θ_m 只能从**位移窗口**算出来（:data:`WINDOW_N` 帧的位置差，门禁
#:   :data:`WINDOW_MOVE_MIN`），所以开局必然有「还没有朝向」的阶段。
#:   这个阶段只能靠**走**来把窗口填上 —— 不发 W 就是死锁（位移恒 0 ->
#:   窗口永远空 -> θ_m 永远是 None）。
#:
#:   区别在于**盲走必须有上限**。没有上限时，真机表现为「盲走十几米、
#:   方向还错了 99°、撞墙、连触发 12 次 L3 脱困，最后 panic」——
#:   产出一堆没法归因的垃圾数据。定上限后，超了就带明确报错退出。
#:
#:   15m 的来由：按走路 ~3 m/s 算约 5s。10 帧窗口（2s）绰绰有余，
#:   剩下的余量留给「起步慢/有加速度」的情况。
BLIND_PROBE_MAX_M = 15.0
#: 小于 1 像素的移动没有意义（会被取整成 0，白发一次）。
#: ⚠️ **刻意不做「最小 20px」**：那会破坏稳定性 —— 见下方注释。
MIN_TURN_PX = 1
#: 相邻两次转向的最小间隔（秒），**自拍定标**。主循环 200ms/帧，
#: 正常情况下不会触发；留它是防「同一帧内重复注入」。
TURN_MIN_INTERVAL_S = 0.08
#: 用于实测 G 的样本对上限（每对 = 一次转向 + 下一帧的 θ_m 变化）
TURN_MEASURE_SAMPLES = 200

# ---------------------------------------------------------------- 鼠标饱和
# ★★★ 2026-10-04 真机实测：游戏对**单次**鼠标相对移动有上限
# ------------------------------------------------------------------
# 实测（风起地，8570px 累计只转 24°）：
#     下发 100~200px  -> 达成率 70%（等效 74px）
#     下发 800~999px  -> 达成率 **11%**（等效 ~94px）
# 也就是说：**单次发得越多，单位像素的收益越低**，线性区大约在 100px 以内。
#
# ★ 为什么必须拆段而不是「限幅到 100px 就完事」
#   限幅只会让 177° 的大转向变成 60°/帧被砍到 7°/帧，30+ 帧才转得完，
#   而且这 30 帧里每帧都在重复同样的过冲。**拆分是把同样的角度在
#   同一帧内分多次送达**，靠的是「每个事件都在线性区」这一事实。
#
# ★ 20ms 的间隔不是随便取的
#   原神 60fps = 16.7ms/帧。间隔 **大于** 一帧，拆出来的每一段才会
#   落在**不同的渲染帧**里被游戏处理。若间隔小于 16.7ms，多段会被
#   合并进同一帧重新触发同一个上限，拆分等于没拆。
#: 单次注入的**像素上限**（= 饱和阈值）。
#:
#: ★ 2026-10-04 改 60 -> 40：上一轮分桶达成率
#:     40~60px  -> **74%**
#:     60~80px  -> 55%
#:     80+      -> 23%
#:   取 40px 落在达成率最高的那一档。
#:
#: ★★★ 这个上限**与拆不拆无关**，永远生效
#: ------------------------------------------------
#: 上一轮把它写成了「只有拆分路径才生效的每段上限」，
#: 而 :data:`SPLIT_MAX_SEGMENTS` 默认 1（不拆）—— 于是 ``_split_turn``
#: 直接 ``return [total], 0``，**289px 原样打出去**，落进饱和区。
#: 「不拆」和「不设上限」被混成了一件事，这是逻辑漏洞，不是调参问题。
#:
#: 现在：无论拆几段，**单次注入一律 <= 这个值**；超出的部分退回队列，
#: 下一帧继续（队列存的是**角度**，见 :func:`_turn_dispatch`）。
MOUSE_SATURATION_PX = 40        # 单次上限（真机分桶：40~60px 达成率 74%，60~80 掉到 55%）
SPLIT_SLEEP_MS = 20.0            # 段间隔（仅拆分启用时才有意义）
#: ★★ 每帧最多**拆成几段**注入（2026-10-04 改：与上限解耦）
#:
#: 默认 **1 = 每帧一次注入**。
#:
#: ★ 这个数**只决定「一帧发几次」**，不再决定「一次发多少」——
#:   单次多少由 :data:`MOUSE_SATURATION_PX` 单独管，且永远生效。
#:   两者是完全独立的两个量（上一轮把它们混在一起，才产生了
#:   「不拆 = 不限幅」的漏洞）。
#:
#: 拆分只有在「段间隔 > 游戏一帧」时才**额外**有效：每一段都必须落在
#: **不同的渲染帧**里被游戏处理，否则多段会被合并回同一帧、
#: 重新触发同一个上限，**拆分等于没拆**。
#:
#: 真机打脸记录（风起地，**30fps**）：游戏一帧 = 33.3ms，而
#: ``SPLIT_SLEEP_MS = 20ms < 33.3ms`` -> 8 段全被并进同一帧，
#: 反倒把主循环从 213ms/帧撑到 625ms（4.7fps -> 1.6fps），
#: 路径长度从 40 掉到 5，最后 give_up。
#:
#: 而**游戏帧率是未知变量** —— 用户会随时改画质（极低/中/高），
#: 30fps 时 33.3ms，60fps 时 16.7ms，120fps 时 8.3ms。在一个会变的
#: 量上赌「间隔足够大」，就是上一轮犯的错。
#: 所以**默认 1 段**，保留实现作为开关：将来若能拿到稳定的游戏帧率
#: （或改用比帧更细的注入方式），把它调到 3~8 再验证。
#: 想启用时改这里，并且**必须**先确认 SPLIT_SLEEP_MS > 该帧率的一帧。
SPLIT_MAX_SEGMENTS = 1
#: 排队余量最多跨几帧。**2026-10-08 从 30 提到 300 —— 本条的语义变了**。
#:
#: ★★ 为什么原来的 30 是**必炸**而不是"偏紧"（大角度转向实测打脸）
#:   --------------------------------------------------------
#: 每帧能注入的转向量是硬上限::
#:
#:     budget = MOUSE_SATURATION_PX(40) * SPLIT_MAX_SEGMENTS(1) * 0.07 = 2.8°/帧
#:
#: 而欠账每帧都会被 ``want_deg = -0.45 * Δ`` **重新灌满**（队列归零条件是
#:     |want| <= 2.8  ->  |Δ| <= 6.22°）。但转向死区
#: :data:`TURN_DEADZONE_DEG` 是 **8°** —— 比 6.22° 还大。
#:
#: ==> 只要 |Δ| > 8°（也就是**只要在转向**），队列在数学上就**永不归零**，
#:     稳态欠账恒为 ``|0.45*Δ| - 2.8``（Δ=120° 时是 51.2°）。
#:
#: 所以「挂了 30 帧就作废」不是一道安全阀，而是**每一次超过 30 帧的
#: 转向都必然触发的假阳性**。真机 179° 转向第 31 帧起每帧作废，就是它。
#:
#: ★ 现在的判据是**方向反号**（见主循环 `_turned_around`），本常量退居
#:   「panic 兜底」。它也**不再需要**挡住"队列无限增长"：队列幅值被
#:   ``|want| - budget <= 57.2°`` 自限，跑不掉。300 帧（≈60s）还挂着，
#:   那就不是转向慢，是卡死了 —— 这时才该报出来。
TURN_QUEUE_MAX_FRAMES = 300
#: [TURN-EFF] 的噪声地板：实测降幅低于它就算测不准，不进统计。
#: θ_m 是 2 秒窗口的方向估计，噪声约 1°，0.3° 那种读数没有意义。
TURN_EFF_NOISE_DEG = 2.0
#: [TURN] 标定可信所需的最小样本数。**低于它，中位数只作参考**
#: （真机两轮 -0.047 / -0.128 °/px 的 2.7 倍波动就是这么来的 ——
#:   样本只有个位数到十几条，不是标定值在变）。
TURN_CAL_MIN_N = 30

# ---------------------------------------------------------------- 动态跳变阈值
# ★ 「每帧 XX px」这个量**依赖帧率**：跑得越慢，单帧位移越大。
#   30fps 的游戏配上我们 200ms 的主循环，跑步每帧约 5.7px；
#   要是主循环掉到 1.6fps，同样速度每帧就是 17.7px。
#   写死 50 在已观测到的帧率下够用（1.6fps 时才 17.7px），
#   但继续掉帧就会顶到天花板，所以按实测帧间隔动态放大。
#: 跑步最高速（世界单位/秒）。自拍定标，Sprint 约 6~11 m/s 取上界。
SPEED_RUN_MPS = 11.0
#: 动态阈值相对「正常运动一帧位移」的安全倍数。
STEP_LIMIT_SAFETY = 3.0
#: 动态阈值的下限 / 上限。下限保住旧的 50（走路/静止的裕量），
#: 上限 500 远低于真传送的 100~5000px，不会把传送放进合法区。
STEP_LIMIT_MIN_PX = 50.0
STEP_LIMIT_MAX_PX = 500.0
#: 帧间隔滑动平均的窗口（帧数）
FRAME_DT_WINDOW = 10

#: 旧的分档绝对增益，**已废弃**，只为旧测试 / 历史日志对照保留。
#: 见上面长注释：四档全部落在 ``|1 + G*gain| > 1``，必然震荡。
LEGACY_TURN_TIERS: tuple[tuple[float, float], ...] = (
    (15.0, 1.0),
    (40.0, 2.0),
    (90.0, 3.0),
    (math.inf, 4.0),
)

# ---------------------------------------------------------------- 卡住检测
# 参考 BetterGI PathExecutor 的设计意图：定周期采样 + 定长队列 + 首尾
# 位移阈值，比逐帧计数更抗噪、且天然区分「静止」与「卡住」。独立实现。
SAMPLE_MS = 1000.0            # 每 1 秒采一个位置
SAMPLE_N = 8                  # 队列长度（首尾跨 7 个间隔 = 7 秒窗口）
STUCK_DIST = 3.0              # |dx|+|dz| < 3 世界单位 -> 卡住
#: 脱困后「快速成功」窗口：这段时间里明显走远了就立刻归零，
#: **不用等 8 个样本攒满**（Q1 决定：队列位移 >=3 归零 + 快速成功）
ESCAPE_FAST_WIN_S = 3.0
#: 三级脱困，第三次放弃当前路点
ESCAPE_MAX_LEVEL = 3
#: 一级脱困：Shift 冲刺下坠 + S 后退
ESCAPE_T1_SPRINT_MS = 400
ESCAPE_T1_BACK_MS = 700
#: 二级脱困：后跳 / 左跳 / 右跳
ESCAPE_T2_JUMP_MS = 260
ESCAPE_T2_MOVE_MS = 320


class _EscapeState:
    """脱困状态机的当前阶段（**非阻塞**，见 :func:`do_escape`）。

    ★ 只需记住「手里还按着哪个键」和「什么时候松」—— 阶段推进是
      纯时间驱动的，不需要额外状态。
    """

    __slots__ = ("name", "hold", "due", "stage", "level", "why")

    def __init__(self, name: str, hold: int | None, due: float,
                 stage: int, level: int, why: str) -> None:
        self.name = name
        self.hold = hold          # 还按着的键（None = 空档期）
        self.due = due            # 该阶段结束的绝对时刻（monotonic 秒）
        self.stage = stage        # 1 = 第一段, 2 = 第二段/末段
        self.level = level
        self.why = why

    def __repr__(self) -> str:   # pragma: no cover - 仅调试
        return (f"<esc {self.name} stage={self.stage} "
                f"剩 {max(0.0, self.due - time.monotonic()):.2f}s>")

# ---------------------------------------------------------------- 攀爬/卡墙
#: 「完全归零」判定：位移小于它才算彻底不动。
CLIMB_EPS = 0.02
#: 连续这么多帧都归零才判攀爬/卡墙。**原值 3 太激进** —— 真机日志里
#: t=5.4/6.4/14.2/31.7s 都误触发，其实只是慢速或被墙卡住。
#: 提高到 20（约 4 秒），先这样，误触发再加到 30。
CLIMB_FRAMES = 20

# ---------------------------------------------------------------- 打滑检测
# 参考 BetterGI 的转向打滑判据（连续 |diff| 超阈值且 >=N 帧且 >Ns 时，
# 松开移动键站定转再按 —— 边走边转时游戏可能把转向前进抵消）。
# 独立实现，未复制源码/函数名/数值。
#
#: 打滑的**最小误差**门限（度）。
#:
#: ★ 2026-10-04 由 5° 提到 12°。真机 60s 三次打滑：
#:     #1 t= 4.4s  Δ=-168.4°  起步背对      —— 合理
#:     #2 t=11.0s  Δ= -91.0°  在收敛但慢    —— 勉强
#:     #3 t=59.7s  Δ=  +7.1°  ★ **已对准，却被打断**
#:
#:   旧门限 5° **小于**死区 8°，于是 5°~8° 这段同时满足两件事：
#:     · |Δ| <= 死区 -> **不转向**（角色已经对准了）
#:     · |Δ| >  门限 -> **判打滑**，因为收敛慢（每帧只降 1~2°）
#:   结果是「明明对准了却被按停 250ms 纯转」，纯属自找的打断。
#:   Δ=+7.1° 正好落在 (5°, 8°] 里 —— 就是真机 #3。
#:
#: ⚠️ 提高门限**不是消灭**这个问题，只是把荒谬区间从 (5°,8°]
#:   **挪到** (8°,12°]。12° 这个值让真机那个 Δ=7.1° 出了区间，
#:   但 (8°,12°] 仍然是同类区间。
#:   —— 要真正消灭，得让「未在收敛」的判定**排除死区内的 Δ**
#:   （见 :func:`_is_converging` 调用处的说明），而不是只调门限。
#: 12° = 死区 8° + 一个可观的余量。**自拍定标**。
SLIP_MIN_ERR_DEG = 12.0
#: 兼容旧名：BetterGI 的判据原文用的是 5°，保留别名避免别处引用断裂。
#: ⚠️ **不要**用它做判据 —— 门限以 :data:`SLIP_MIN_ERR_DEG` 为准。
SLIP_DEG = SLIP_MIN_ERR_DEG
SLIP_FRAMES = 3
SLIP_SECONDS = 2.0
SLIP_PURE_GAIN = 1.0          # 站定转向用最小增益
#: 打滑恢复时的转向比例。**自拍定标**。比常规 0.45 更大 —— 站定时没有
#: 「前进抵消」，可以转得更狠一点；但仍然**有界**，且依旧走同一条
#: 比例控制律（旧的 ``-SLIP_PURE_GAIN * d_deg`` 是绝对增益且**不限幅**，
#: Δ=90° 时会一帧发 90px，是遗留的不一致）。
SLIP_PURE_FRACTION = 0.70
SLIP_PURE_MAX_DEG = 40.0
#: 松开 W 后的**站定**时长（毫秒）。这是打滑恢复能生效的物理前提：
#: 边走边转时游戏会把转向与前进**抵消**，所以必须先站定、转完、再按 W。
#: 原来写死成 ``panic.stop.wait(0.25)``，拆成多段发送后必须提出来
#: —— 否则改站定时长要翻代码。**同步阻塞**主循环（见 ``_slip_block``）。
SLIP_STAND_MS = 250.0
#: 收敛检查用的 |Δ| 历史长度。**2026-10-08 从 6 帧提到 10 帧**。
#:
#: ★ 为什么加长（大角度转向实测打脸）
#:   6 帧 = 前后各 3 帧求均值。大角度时 Δ 每帧只降 1~2°，
#:   两段均值的差最多 3×2 = 6°，而 :data:`SLIP_CONVERGE_MIN_DEG` 曾是 5°
#:   —— **卡在阈值边缘**：快一点判收敛、慢一点判打滑，全看运气。
#:   10 帧 = 前后各 5 帧，同样每帧 1~2° 能攒到 5~10°，
#:   稳稳越过任何合理的幅度阈值；而且窗口越长，**单帧噪声越容易被平均掉**。
SLIP_CONVERGE_N = 10
#: 判定「正在收敛」所需的**绝对**降幅（度）。
#: ★ 为什么不能用相对比例（2026-10-04 修）
#:   旧判据是「窗口内均值降 5% 以上」。但收敛速度取决于
#:   ``G·TURN_FRACTION``：在 G 被误当成 1.0 的那轮里，每帧只降 3%，
#:   1.2 秒窗口内只降 3.5% —— **永远够不到 5% 阈值**，
#:   于是「一直在收敛」却被判成「未在收敛」，真机 40 秒误触发 3 次
#:   （|Δ| 从 -169° 收到 -13.9°，一路都在降，却仍被判打滑）。
#:   绝对量与收敛快慢无关，且在 |Δ| 很小的时候更灵敏。
#:   取 5° = 与 SLIP_DEG 同量级：窗口内误差至少要降一个死区那么多。
SLIP_CONVERGE_MIN_DEG = 5.0
#: 打滑恢复的次数上限。**自拍定标**。反复打断本身就是故障信号，
#: 到顶就停手并报出来，别无限刷 —— 上一版 40 秒误触发 14 次。
SLIP_MAX_RECOVERIES = 3

# ---------------------------------------------------------------- 到达判据
#: 「进入方向」的平均窗口（秒）。**不能用最后一帧 θ_m** —— 停下之后
#: 窗口位移归零、θ_m 冻结，用它判朝向等于判噪声。
HEADING_WINDOW_S = 2.5
#: 中间路点的朝向容差（度）。**最终目标豁免朝向，只判距离。**
ARRIVE_HEADING_TOL = 30.0
#: ★ 本 demo **只有一个路点**（--target-world 给的最终目标），所以
#:   到达判据恒走「豁免朝向、只判距离」分支（Q2 决定 b）。
#:   下面的朝向判据（Q2 决定 a）代码是全的，等接多路点时把这里改成
#:   「当前路点不是最后一个」即可。**现在别打开** —— 只有一个路点时
#:   打开 = 拿停止后的噪声当朝向判据，纯属自伤。
WAYPOINT_MODE = False

#: 起步时 Shift 先落、等它生效再按 W 的间隔（秒）。
#: ``simulator.py:12`` 明确警告过「Shift+W 分两次注入，游戏可能只收到 W」。
SPRINT_LEAD_S = 0.02
#: 「按着 W 却几乎没动」的判定阈值（世界单位/帧）。4.8fps 下跑步一帧
#: 约 1.1m；0.05m 远低于任何正常移动，用来发现「按了键但角色没响应」。
MOVE_STALL_EPS = 0.05

class _PanicWatcher:
    """F12 急停监听 —— **独立线程**。

    ★ 为什么不用 ``SafetyGuard``
    --------------------------
    ``SafetyGuard.panic_active`` 是个**属性**：调用时才去问
    ``GetAsyncKeyState``。全仓库**没有** ``RegisterHotKey``、没有
    ``SetWindowsHookEx``、**没有任何监听线程** —— 它是纯拉取式轮询。

    而本脚本**根本不创建 SafetyGuard**（只用 InputSimulator），
    所以 F12 在这里**一个字节都不生效**。而且主循环 5Hz（200ms/帧）
    追不上一次快速点按。

    所以这里自己起一个**独立守护线程**，以 5ms 轮询。按下的瞬间就：
      ① 立刻松掉所有按住的键（**这一步必须在监听线程里做**）
      ② 置位 stop 事件，主循环下一次 wait 立刻返回并退出
    """

    POLL_S = 0.005          # 5ms -> 200Hz
    #: 连松几次。间隔要短：检测到的那一刻**第一下就该松开**，
    #: 重复只是为了跨过「主线程恰好在监听器松手之后按下」的竞态。
    REL_ROUNDS = 3
    REL_GAP_S = 0.01

    def __init__(self, release_cb, on_panic=None, on_error=None) -> None:
        import threading

        self.stop = threading.Event()
        self._rel = release_cb
        self._cb = on_panic
        self._err = on_error
        self._thread: threading.Thread | None = None
        self.fired = False
        self.detected_at = 0.0
        self.error: str = ""

    def _fail(self, exc: BaseException) -> None:
        self.error = f"{type(exc).__name__}: {exc}"
        if self._err is not None:
            try:
                self._err(self.error)
            except Exception:  # noqa: BLE001
                pass
        else:
            print(f"  ✗ F12 急停线程异常退出：{self.error}", file=sys.stderr)

    def _poll(self) -> None:
        from autopilot.actuator.simulator import _user32

        try:
            u32 = _user32()
        except BaseException as exc:  # noqa: BLE001
            self._fail(exc)
            return
        # 原始返回值节流打印（诊断用，1s 一次）
        _last_raw = 0.0
        while not self.stop.is_set():
            try:
                _now = time.monotonic()
                _raw = u32.GetAsyncKeyState(int(VK.F12))
                # ⚠️ 掩码到 0xFFFF：GetAsyncKeyState 返回 SHORT，ctypes 默认
                #   restype 会把高位寄存器垃圾一起带回来。
                if _raw != 0 and (_now - _last_raw) > 1.0:
                    print(f"[WATCHER] raw GetAsyncKeyState(F12) = "
                          f"0x{_raw & 0xFFFF:04X}", flush=True)
                    _last_raw = _now
                if _raw & 0x8000:
                    self.fired = True
                    self.detected_at = time.monotonic()
                    print(f"[WATCHER] F12 HIT t={self.detected_at:.3f} "
                          f"fired={self.fired}", flush=True)
                    print("[WATCHER] releasing...", flush=True)
                    for _ in range(self.REL_ROUNDS):
                        try:
                            self._rel()
                            print(f"[WATCHER] _rel() ok "
                                  f"(round {_ + 1}/{self.REL_ROUNDS})", flush=True)
                        except Exception as exc:  # noqa: BLE001
                            print(f"[WATCHER] _rel() EXC "
                                  f"{type(exc).__name__}: {exc}",
                                  file=sys.stderr, flush=True)
                        time.sleep(self.REL_GAP_S)
                    if self._cb is not None:
                        try:
                            self._cb()
                        except Exception:  # noqa: BLE001
                            pass
                    print("[WATCHER] stop.set() called", flush=True)
                    self.stop.set()
                    print("[WATCHER] stop.set() returned", flush=True)
                    return
            except BaseException as exc:  # noqa: BLE001
                self._fail(exc)
                return
            time.sleep(self.POLL_S)

    def start(self) -> str:
        """启动监听线程。**返回就绪状态**：空串 = 正常，非空 = 失败原因。"""
        import threading

        self._thread = threading.Thread(
            target=self._poll, name="pathfind-panic", daemon=True)
        self._thread.start()
        t_end = time.monotonic() + 0.1
        while time.monotonic() < t_end:
            if self.error or not self._thread.is_alive():
                break
            time.sleep(0.002)
        print(f"[WATCHER] thread alive: {self._thread.is_alive()} "
              f"error={self.error!r}", flush=True)
        return self.error

    def shutdown(self) -> None:
        self.stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)


def wrap_pi(a: float) -> float:
    """把角度归一化到 [-π, π]。atan2 出来的差值可能跨 ±π。"""
    return (a + math.pi) % (2 * math.pi) - math.pi


def _fmt2(p) -> str:
    return "None" if p is None else f"({p[0]:.1f},{p[1]:.1f})"


def _pair(s: str) -> tuple[float, float]:
    return float(s.split(",")[0]), float(s.split(",")[1])


def hr(title: str = "") -> None:
    """分段横线。给了 title 就连标题一起框起来。

    ★ 这个函数**曾经被漏掉**：A~H 那轮重写只留下了 6 处 ``hr(...)``
      调用没留下定义，``main()`` 第一行就 NameError。是靠 ruff 的
      F821 扫出来的 —— ``py_compile`` 查不出未定义名。
    """
    if title:
        print(f"\n{SEP}\n  {title}\n{SEP}")
    else:
        print(SEP)


#: ``参数名 -> (目标键, 转换函数, 是否带值)``
_SPEC = {
    "--target-world": ("target", _pair, True),
    "--start-world": ("start", _pair, True),
    "--seconds": ("seconds", float, True),
    "--arrive": ("arrive", float, True),
    "--delay": ("delay", float, True),
    "--channel": ("channel", str, True),
    "--title": ("title", str, True),
    "--log-file": ("log_file", str, True),
    "--walk-forward": ("walk_forward", float, True),
    "--target-tp": ("target_tp", str, True),
    "--tp-data": ("tp_data", str, True),
    "--live": ("live", bool, False),
    "--sprint": ("sprint", bool, False),
    "--walk": ("walk", bool, False),
    # ── LLM 路径规划（2026-10-05）──────────────────────────────────────
    "--use-llm": ("use_llm", bool, False),
    "--llm-max-calls": ("llm_max_calls", int, True),
    # ★ 真发请求的开关。与 --live 是同一套哲学：**默认不做**。
    #   环境里恰好有个 key 就默默把整张底图发出去、把钱花了，
    #   不是能默认接受的行为。
    "--llm-real": ("llm_real", bool, False),
    # ★ 用户点选的路点文件（anchors_viewer.py 产出）
    "--waypoints-file": ("waypoints_file", str, True),
    # ★★ 2026-10-08 起点体检的逃生门。差 >START_MISMATCH_BLOCK_M 时
    #   默认**拒绝启动** —— 相位相关是相对定位，走错地方不会报错，
    #   只会安静地把你带到错误的坐标。这个 flag 是唯一的「我知道我在干什么」。
    "--i-know-start": ("i_know_start", bool, False),
    # ★★ --use-route 是 --waypoints-file 的**别名**（2026-10-08）
    #   区别只有一个：给**裸文件名**时，自动去 data/routes/ 里找。
    #   bgi_route_import.py 抽出来的路线本来就是 user_waypoints 的 schema，
    #   所以解析期把 A 改写成 B 之后，main() 一个字都不用动 ——
    #   _WaypointCursor / WAYPOINT_MODE / 路点命中判定全部原样复用。
    "--use-route": ("use_route", str, True),
    # ════════════════════════════════════════════════════════════════
    # 支线专用：BetterGI 路线诊断（2026-10-09）
    # ════════════════════════════════════════════════════════════════
    # 这两个是**只诊断、不执行**的子模式：不启动寻路主循环、不发任何按键。
    # 目的是先把「BetterGI 路点跑不通」这件事**归因**清楚，再谈改执行逻辑。
    "--diag-route": ("diag_route", bool, False),
    "--diag-anchor": ("diag_anchor", str, True),
}

#: ``--use-route`` 裸文件名在这个目录里找。
ROUTES_DIR = Path(RECOG_ROOT / 'data' / 'routes')

#: ``--llm-max-calls`` 的默认值。**当前只调一次**（规划在启动时一次完成，
#: 跑的过程中不重新规划）。这个字段先留着：将来要做「卡住了重新问模型」
#: 时它就是那个预算闸门，届时不用再改参数表。
DEFAULT_LLM_MAX_CALLS = 20

#: DeepSeek 密钥的环境变量名。**必须与 path_planner.py 里的同名常量一致**。
#:
#: ★ 为什么不从 path_planner 读
#:   --------------------------
#:   本文件**不 import** path_planner（它要 numpy + PIL + coord_transform，
#:   绝大多数跑法根本不需要它）。要读就得在启动时把它 import 一遍 ——
#:   为了一个字符串付整个依赖树的代价，不划算。
#:
#:   ★ 漂移风险已用单测钉住：``test_llm_waypoints.py`` 会读
#:     path_planner 的常量并断言两边相等。改名时两边一起改即可。
DEEPSEEK_KEY_ENV = "DEEPSEEK_API_KEY"

#: 路点命中半径（米）。到当前路点这么近就算到达、换下一个。
#:
#: ★ 为什么不用 ``A["arrive"]``
#:   ------------------------------
#:   ``--arrive`` 是**最终目标**的到达阈值（默认 5m），而且是可调参数 ——
#:   用户把它调到 30m 的时候，你不会想让中间路点也放宽到 30m：那是
#:   「离目标 30m 就宣布完成」，和「路过这个路口了没有」是两回事。
WAYPOINT_REACH_M = 5.0
#: ★★ 2026-10-08 起点体检的两档阈值（**仅 --waypoints-file 模式生效**）。
#:
#: 背景：相位相关是**相对**定位 —— 起点给错它照样能收敛、照样能 arrive，
#: 只是到了一个错的地方，**全程零报错**。所以起点错了必须在这里拦，
#: 不能靠游戏或感知层报错（它们没有能力知道自己错了）。
#:
#: 阈值怎么定的（自拍定标，非统计）：
#:   * 500m  —— 同一张地图内、点选与开跑之间挪动的合理量级。超过只提示。
#:   * 2000m —— 已经跨大区了。城与城的直线距离量级就在这里上下，
#:               差这么多不可能是「挪了挪」，只能是换了个地方点选。
START_MISMATCH_WARN_M = 500.0
START_MISMATCH_BLOCK_M = 2000.0

#: 三个互斥的**目标**参数。给了 >=2 个就是自相矛盾（不知道该听谁的）。
TARGET_ARGS = ("--target-world", "--walk-forward", "--target-tp")

#: 三个互斥的**起点**参数。``--walk-forward`` 内部需要真实起点来播种
#: tracker，所以它**不豁免** ``--start-world``。
START_ARGS = ("--start-world",)

#: ``--walk-forward`` 预热阶段需要多少有效位移样本才够算朝向。
#: 与主循环 ``WINDOW_N`` 同量级 —— 样本太少时 θ_m 会被单帧噪声主导，
#: 算出来的「前方」是个随机方向，角色会朝完全错误的地方走。
WALK_FWD_WARM_S = 2.5
#: 预热期间**最短位移**（米）。低于这个值说明角色没动 / 被墙挡住，
#: 此时算出的朝向是噪声，直接报错而不是拿噪声当方向。
WALK_FWD_MIN_MOVE_M = 0.5


def _forward_target(x: float, z: float, theta_m: float,
                    dist_m: float) -> tuple[float, float]:
    """从 ``(x, z)`` 沿朝向 ``theta_m``（弧度）走 ``dist_m`` 米的落点。

    纯函数 —— 单测直接调它，不碰窗口/定位/输入。

    ★ 为什么要有这个函数
      「前方 N 米」这一步一旦内联进主循环，就没法在单测里验证：
      真机窗口都不一定有。提成纯函数后，单位向量、
      正负方向、距离守恒都能离线断言。
    """
    return (x + dist_m * math.cos(theta_m),
            z + dist_m * math.sin(theta_m))


def _pick_teleport_points(data: dict, query: str) -> list[dict]:
    """按名字模糊匹配传送点。**返回全部命中**（可能多个）。

    ★ 三个字段都要参与匹配
    ----------------------
    实测本机 936 个点里 777 个都叫「传送锚点」，``name`` 的唯一值只有
    106 个；而「风起地」这种地名**只存在于 ``areas``**，``name`` 里一个
    都没有。所以只搜 ``name`` 的话，用户打「风起地」永远匹配不到。

      · ``name``    —— 「七天神像-风」这种正式名
      · ``areas``   —— 「风起地」这种地名标签（可多个）
      · ``country`` —— 「蒙德」这种国家名

    ★ 为什么返回列表而不是单个
    ------------------------
    「传送锚点」命中 777 个。自动挑第一个 = 静默把角色送去
    地图另一头的锚点，用户跑到天荒地老才发现不对。**宁可不跑。**
    """
    q = (query or "").strip()
    if not q:
        return []
    hits: list[dict] = []
    for region in data.get("regions", []):
        for pt in region.get("points", []):
            nm = str(pt.get("name", ""))
            areas = [str(a) for a in (pt.get("areas") or [])]
            country = str(pt.get("country", ""))
            if q in nm or any(q in a for a in areas) or q in country:
                # ★ 坐标必须取 [x, z] = world_pos[0] / world_pos[2]，
                #   **不是** [0]/[1] —— world_pos 是 [x, y=高度, z]，
                #   取 [1] 会把**海拔**当成 Z，目标直接偏出去几百米。
                #   （单测就是抓这一条：world_pos=[1,2,3] 必须得 (1,3)）
                _wp = list(pt.get("world_pos") or (0.0, 0.0, 0.0))
                hits.append({
                    "name": nm,
                    "category": pt.get("category", ""),
                    "country": country,
                    "areas": areas,
                    "region": region.get("display_name", ""),
                    "pos": (float(_wp[0]), float(_wp[2])) if len(_wp) >= 3
                           else (float(_wp[0]), float(_wp[-1])),
                })
    return hits


def _parse_args() -> dict:
    a = {"title": DEFAULT_TITLE, "seconds": DEFAULT_SECONDS, "channel": None,
         "delay": DEFAULT_DELAY, "arrive": DEFAULT_ARRIVE, "live": False,
         "target": None, "start": None, "sprint": True, "log_file": None,
         "walk_forward": None, "target_tp": None, "tp_data": None,
         # ★ 目标的**显示名**（--target-tp 才有）。写寻路状态文件时给
         #   overlay 用；没有名字就退回显示坐标。
         "target_tp_name": None, "target_name": None,
         # ★ LLM 路径规划（2026-10-05）
         "use_llm": False, "llm_max_calls": DEFAULT_LLM_MAX_CALLS,
         "llm_real": False,
         "waypoint_queue": None, "waypoints_file": None,
        # ★ 起点体检的逃生门。**必须显式给默认值** —— 别的 flag 都在
        #   FLAGS 表里带了默认值，这个是 2026-10-08 后加的、漏了。
        #   漏了不会立刻出事（A.get() 兜住返回 None，也是 falsy），
        #   但任何写成 A["i_know_start"] 的地方都会 KeyError。
        "i_know_start": False,
        # ★ --use-route 的裸名；解析期会被改写成 waypoints_file 的绝对路径
        "use_route": None}
    #: 显式出现过哪些目标/起点参数（供 main 判断「是不是用户给的真坐标」）
    _seen: set[str] = set()
    a["_seen_args"] = _seen          # ★ 同一份对象，不复制
    args, i = sys.argv[1:], 0
    while i < len(args):
        k = args[i]
        if k == "--sprint":
            a["sprint"] = True; i += 1; continue
        if k == "--walk":
            a["sprint"] = False; i += 1; continue
        spec = _SPEC.get(k)
        if spec is None:
            if not k.startswith("-"):
                a["title"] = k
            i += 1
            continue
        key, conv, takes = spec
        if k in TARGET_ARGS or k in START_ARGS:
            _seen.add(k)
        if not takes:
            a[key] = True
            i += 1
            continue
        v = args[i + 1] if i + 1 < len(args) else None
        try:
            val = conv(v)
        except (ValueError, IndexError, TypeError):
            print(f"✗ {k} 的取值不合法: {v!r}", file=sys.stderr)
            raise SystemExit(2)
        a[key] = max(0.0, val) if key == "delay" else val
        i += 2
    # ★ 任务 C：三个目标参数**互斥**（2026-10-04）
    #   同时给两个等于自相矛盾 —— 不知道该听谁的，而「随便挑一个」是
    #   最糟的：用户以为走向 A、实际走向 B，跑到天荒地老才发现。
    #   宁可拒绝启动。
    _given = [g for g in TARGET_ARGS if g in _seen]
    if len(_given) >= 2:
        print(f"✗ 目标参数互斥，却给了 {len(_given)} 个：{', '.join(_given)}",
              file=sys.stderr)
        print("  --target-world  给死坐标（你已知坐标时用）", file=sys.stderr)
        print("  --walk-forward  沿当前朝向前进 N 米（你不知道坐标时用）",
              file=sys.stderr)
        print("  --target-tp     按传送点名字定位（你只知道地名时用）",
              file=sys.stderr)
        print("  只能给一个。", file=sys.stderr)
        raise SystemExit(2)
    # ★ --start-world **必填**（§51 任务 H）。曾经做成「缺省从 tracker
    #   自动取」——用户明确反对：「不知道它会跑哪去」。宁可报错也不猜。
    #   ★ --walk-forward **不豁免**这一条：它内部要把真实起点喂给
    #     TileTracker 才能算相位相关，缺了起点连「当前在哪」都答不出。
    # ★ --waypoints-file 与三个目标参数**互斥**（2026-10-06）
    # ★ --use-route 解析期就改写成 --waypoints-file（2026-10-08）：
    #     裸文件名 -> data/routes/<名>；给绝对路径就用给的。
    #     放在**这里**而不是 main()，是为了让后面所有互斥/必填校验
    #     自动生效 —— 两条路从此完全同构，不会分叉。
    if a.get("use_route"):
        if a["waypoints_file"]:
            print("  ✗ --use-route 与 --waypoints-file 同时给了，"
                  "不知道听谁的。", file=sys.stderr)
            raise SystemExit(2)          # ★ 本文件的约定是 raise，不是 return
        _u = str(a["use_route"])
        _p = Path(_u)
        if not _p.is_absolute() and not _p.is_file():
            _cand = ROUTES_DIR / _u
            if not _cand.is_file() and not _u.endswith(".json"):
                _cand = ROUTES_DIR / (_u + ".json")
            if _cand.is_file():
                _p = _cand
        if not _p.is_file():
            print(f"  ✗ --use-route 找不到文件：{_u}", file=sys.stderr)
            print(f"    试过：{_p}", file=sys.stderr)
            print(f"    {ROUTES_DIR} 里现有的：", file=sys.stderr)
            try:
                for _f in sorted(ROUTES_DIR.glob("*.json"))[:20]:
                    print(f"      {_f.name}", file=sys.stderr)
            except OSError:
                pass
            raise SystemExit(2)
        a["waypoints_file"] = str(_p)
        a["use_route"] = None
        print(f"  路线: {_p}")
    #   路点文件自带终点（target 字段），再给 --target-world 就是两份
    #   互相矛盾的终点 —— 而「不知道该听谁的」正是寻路最容易跑飞的
    #   起点。这里直接 exit 2，不给「静默用其中一个」的机会。
    if a["waypoints_file"]:
        _conflict = [n for n in TARGET_ARGS
                     if n in a.get("_seen_args", ())]
        if _conflict:
            print(f"  ✗ --waypoints-file 与 {', '.join(_conflict)} 互斥。",
                  file=sys.stderr)
            print("    路点文件里已经带了 target（最终目标），"
                  "再给一个就是两份矛盾。", file=sys.stderr)
            raise SystemExit(2)
    # ★ 这里查的是 **target**，不是 start（2026-10-06 修正）。
    #   手滑写成 a["start"] 的后果是：只给 --start-world 不给任何目标时
    #   这条检查不触发，直接掉到下面的起点校验里，报出来的却是
    #   「必须给 --start-world」—— 错误信息与真实原因**完全无关**。
    # ★ 诊断模式豁免：--diag-route / --diag-anchor **不寻路**，
    #   它们只读文件或只做定位，所以不需要起点也不需要目标。
    #   不豁免的话，用户按文档敲的第一条命令就会撞「必须给 --start-world」。
    _diag = bool(a.get("diag_route") or a.get("diag_anchor"))
    if a["target"] is None and a["walk_forward"] is None and \
            a["target_tp"] is None and not a["waypoints_file"] and not _diag:
        print("✗ 必须给一个目标，三选一：", file=sys.stderr)
        print("  --target-world X,Z   例：--target-world 2566,-1323",
              file=sys.stderr)
        print("  --walk-forward 30    沿当前朝向前进 30 米（不知道坐标时用）",
              file=sys.stderr)
        print('  --target-tp "风起地"  按传送点名定位（只知道地名时用）',
              file=sys.stderr)
        print("  --waypoints-file F.json  用 anchors_viewer 点选的路点"
              "（自带终点）", file=sys.stderr)
        print("  --i-know-start          路点 start 与 --start-world 差 >2km 时"
              "仍然开跑", file=sys.stderr)
        raise SystemExit(2)
    # ★ --start-world 必填，但 **--walk-forward 例外**（2026-10-04）。
    #   曾经一律必填，那是过度保守：--walk-forward 的整条链路
    #   （θ_m -> 落点 -> 转向 -> 到达判据）全是**相对量**，种子值
    #   只要求「非 None」，具体多少不影响走向。强制用户填坐标，
    #   等于把「我不知道自己在哪」的负担又还给了用户 ——
    #   而那正是 --walk-forward 要消灭的负担。
    # ★ dry-run + --walk-forward **一律拒绝**（2026-10-04）。
    #   与「是否给了 --start-world」**无关** —— 原因在于 dry-run 本身：
    #   DryRunBackend 不注入按键，角色不动，位移恒为 0，朝向
    #   ``atan2(0, 0)`` 恒为 0，算出来的「前方」是个与真实朝向无关的
    #   假方向。与其让 dry-run 报「一切正常」，不如直接说做不到。
    #
    #   ★ 必须放在这里（**任何 I/O 之前**）
    #     原本这个检查在 :func:`_warmup_and_resolve_forward` 里，而那函数
    #     要等 ``cap.open()`` 之后才被调 —— 本机没开游戏时用户看到的是
    #     「找不到窗口」，**掩盖了真正的原因**。纯逻辑门禁必须先于 I/O。
    # ★ --use-llm 与 --walk-forward **互斥**（2026-10-05）
    #   两者对「目标」的含义根本不同：
    #     --walk-forward  沿**当前朝向**前进 N 米 —— 起点在预热结束时
    #                    才由 tracker 推出来，是一个**追着相机**的活目标
    #     --use-llm       需要**固定**的起终点：先把地图裁出来发给模型，
    #                    模型看的是一张静图
    #   同时给，LLM 规划要么拿不到起点（walk-forward 还没跑），要么
    #   规划完的终点又被朝向覆盖 —— 两种结果都是「看起来配了、其实没用」。
    #   这里直接 exit 2，比默默忽略其中一个诚实。
    if a["use_llm"] and a["walk_forward"] is not None:
        print("  ✗ --use-llm 与 --walk-forward 互斥。", file=sys.stderr)
        print("    --walk-forward 的目标在预热**结束后**才由朝向推出，",
              file=sys.stderr)
        print("    那时地图还没裁、也没发给模型，规划无从谈起。",
              file=sys.stderr)
        print("    想用 LLM 规划就同时给 --start-world 和 --target-world X,Z"
              "（或 --target-tp）。", file=sys.stderr)
        raise SystemExit(2)
    # ★ --llm-real 单独给没意义（2026-10-05）
    #   它只是 --use-llm 的「真发请求」子开关，不带 --use-llm 就永远
    #   不会被读到。放行等于让用户以为自己开着什么 —— 而实际情况是
    #   那个参数被**默默忽略**。宁可报错。
    if a["llm_real"] and not a["use_llm"]:
        print("  ✗ --llm-real 必须配合 --use-llm。", file=sys.stderr)
        print("    它只是 --use-llm 的「真发请求」子开关，单独给不生效。",
              file=sys.stderr)
        print("    想要：--use-llm --llm-real", file=sys.stderr)
        print("    想看 prompt 不花钱：--use-llm（不加 --llm-real）",
              file=sys.stderr)
        raise SystemExit(2)
    # ★ --llm-real 但没 key -> 直接拒，不去跑一遍才发现
    #   走到 _call_llm 才会返回 None，而那已经读完了底图（几十秒）
    #   才告诉你「没配 key」。这里提前，纯逻辑、先于任何 I/O。
    if a["llm_real"] and not os.environ.get(DEEPSEEK_KEY_ENV, "").strip():
        print(f"  ✗ --llm-real 需要环境变量 {DEEPSEEK_KEY_ENV}。",
              file=sys.stderr)
        print(f"    PowerShell：$env:{DEEPSEEK_KEY_ENV} = 'sk-...'",
              file=sys.stderr)
        print("    不发请求、只看 prompt 的话，去掉 --llm-real。",
              file=sys.stderr)
        raise SystemExit(2)
    if a["walk_forward"] is not None and not a["live"]:
        print("  ✗ --walk-forward 需要 --live。", file=sys.stderr)
        print("    它要先走一段拿到朝向，而 dry-run 不注入按键、角色不动，",
              file=sys.stderr)
        print("    位移恒为 0，算不出朝向（atan2(0,0)=0）。",
              file=sys.stderr)
        print("    dry-run 下想验目标，请用 --target-world X,Z。",
              file=sys.stderr)
        raise SystemExit(2)
    if a["start"] is None:
        if a["walk_forward"] is not None:
            a["start"] = (0.0, 0.0)
            print("\n  [WALK-FWD] 未给 --start-world，tracker 种子默认 (0,0)",
                  file=sys.stderr)
            print("           （本次只走相对方向，绝对坐标无意义）",
                  file=sys.stderr)
        elif _diag:
            # 诊断模式自己会播种（--diag-anchor 用锚点落点，
            # --diag-route 根本不定位），给个占位免得下游 NoneType。
            a["start"] = (0.0, 0.0)
        else:
            print("✗ 必须给 --start-world X,Z（你当前所在的世界坐标）",
                  file=sys.stderr)
            if a["target_tp"] is not None:
                print("  --target-tp 需要**真实**起点：目标是绝对世界坐标，",
                      file=sys.stderr)
                print("  种子错了，起点到目标的直线距离和方向都会错。",
                      file=sys.stderr)
                print("  不知道怎么用 --target-tp：先用 --walk-forward 测方向",
                      file=sys.stderr)
                print("  闭环，或用 e2e_teleport.py 跑一次拿真实坐标。",
                      file=sys.stderr)
            print("  这个值可以跑 e2e_teleport.py 拿到（它会打印传送后的坐标），",
                  file=sys.stderr)
            print("  或看 e2e_dryrun.py 汇总里的 pos=。**不会**自动从定位猜 —— ",
                  file=sys.stderr)
            print("  「不知道它会跑哪去」比多一个参数糟糕得多。", file=sys.stderr)
            print("  （例外：--walk-forward 不需要，它只走相对方向）",
                  file=sys.stderr)
            raise SystemExit(2)
    return a


def _resolve_target_tp(a: dict) -> int:
    """``--target-tp`` -> ``a["target"]``。失败返回非 0（**不抛**）。

    ★ 为什么不自动纠错、不自动挑第一个
    ----------------------------------
    实测本机 936 个传送点里 777 个都叫「传送锚点」，``name`` 的唯一值
    只有 106 个。「传送锚点」命中 777 个时自动取第一个，等于静默把
    角色送去地图另一头 —— 用户跑到天荒地老才发现不对。这里一律
    列出候选并退出，由人决定。
    """
    query = a["target_tp"]
    try:
        from autopilot.perception.loaders.teleport_points import (
            load_teleport_points)
        data = load_teleport_points(a.get("tp_data") or None)
    except FileNotFoundError as exc:
        print(f"\n  ✗ 读不到传送点数据：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:                                  # noqa: BLE001
        print(f"\n  ✗ 传送点数据读取失败：{exc}", file=sys.stderr)
        return 2

    hits = _pick_teleport_points(data, query)
    if not hits:
        print(f"\n  ✗ 没有名字匹配 {query!r} 的传送点。", file=sys.stderr)
        _print_tp_candidates(data)
        return 2
    if len(hits) > 1:
        print(f"\n  !! {query!r} 匹配到 {len(hits)} 个传送点，"
              f"**不自动选**（第 1 个可能在地图另一头）。",
              file=sys.stderr)
        for i, h in enumerate(hits[:15], 1):
            print(f"       {i:>3}. {h['name']!r:<20} {h['region']:<10} "
                  f"{h['country']:<6} pos=({h['pos'][0]:.0f},{h['pos'][1]:.0f})"
                  f"  {'/'.join(h['areas'][:2])}", file=sys.stderr)
        if len(hits) > 15:
            print(f"       ... 还有 {len(hits) - 15} 个", file=sys.stderr)
        print("  换个更具体的名字，或直接用 --target-world 给坐标。",
              file=sys.stderr)
        return 2

    h = hits[0]
    a["target"] = (float(h["pos"][0]), float(h["pos"][1]))
    # ★ 记下名字：overlay 悬浮卡片显示「目标: 七天神像-风」比一堆坐标
    #   有用得多。--target-world / --walk-forward 没有名字，overlay
    #   会退回显示坐标（layout._fmt_target）。
    a["target_tp_name"] = h["name"]
    a["target_name"] = h["name"]
    print(f"  目标(传送点) : {h['name']!r} {a['target']}  "
          f"[{h['region']} / {h['country']}]")
    # ★ 这个目标是**绝对**世界坐标，所以 --start-world 必须是真坐标。
    #   （与 --walk-forward 相反：那个只走相对方向，种子可以随便填。）
    print("  ⚠ --target-tp 需要**真实**起点 --start-world：")
    print("    目标坐标是绝对的，种子错则「起点→目标」的距离和方向都会错，")
    print("    角色会朝一个和目的地无关的方向走。")
    print("    不知道怎么查当前位置：")
    print("      · e2e_teleport.py 传一次，它会打印传送后的坐标")
    print("      · e2e_dryrun.py 汇总里的 pos=")
    print("      · 或先用 --walk-forward 测方向闭环（那个不需要真实坐标）")
    return 0


def _print_tp_candidates(data: dict) -> None:
    """匹配失败时列出**唯一名字**（不是 936 条，去重后 100 出头）。"""
    names: dict[str, int] = {}
    for region in data.get("regions", []):
        for pt in region.get("points", []):
            nm = str(pt.get("name", ""))
            names[nm] = names.get(nm, 0) + 1
    uniq = sorted(names)
    print(f"  数据里共 {sum(names.values())} 个点、{len(uniq)} 个不同名字。",
          file=sys.stderr)
    print("  名字样例（**不含地名标签**，地名在 areas 里）：",
          file=sys.stderr)
    for nm in uniq[:20]:
        print(f"       {nm!r}{'' if names[nm] == 1 else f'  x{names[nm]}'}",
              file=sys.stderr)
    if len(uniq) > 20:
        print(f"       ... 还有 {len(uniq) - 20} 个", file=sys.stderr)
    print("  ★ 地名（如「风起地」）通常在 areas 标签里，不是 name。",
          file=sys.stderr)
    print("    可以试试 --target-tp \"七天神像-风\"，或直接给 --target-world。",
          file=sys.stderr)


def _warmup_and_resolve_forward(a: dict, svc, cap, builder, sim,
                                panic=None) -> int:
    """``--walk-forward`` 的预热：攒够位移历史 -> 算 θ_m -> 定目标。

    失败返回非 0，**不抛**（主流程统一 return 2）。

    ★ 为什么必须先「走一段」
    ------------------------
    θ_m 来自 ``hist`` 窗口里首尾两点的连线。没位移就没有 θ_m，
    ``atan2(0, 0) = 0`` 会让「前方」恒定指向 +x —— 那是**假方向**，
    角色会朝一个和实际朝向无关的位置走。所以这里先按住 W 走
    ``WALK_FWD_WARM_S`` 秒，位移不够就**明确报错**，绝不硬编一个方向。

    ★ dry-run 为什么直接拒绝
    ------------------------
    DryRunBackend 不注入按键，角色不会动，位移恒为 0。这个模式下
    ``--walk-forward`` 在数学上就是无解的，与其给一个假方向让
    dry-run 报告「一切正常」，不如直接说做不到。
    """
    if not a["live"]:
        print("\n  ✗ --walk-forward 需要 --live。", file=sys.stderr)
        print("    它要先走一段拿到朝向，而 dry-run 不注入按键、角色不动，",
              file=sys.stderr)
        print("    位移恒为 0，算不出朝向（atan2(0,0)=0 会指向固定的假方向）。",
              file=sys.stderr)
        print("    dry-run 下想验目标，请用 --target-world X,Z。",
              file=sys.stderr)
        return 2

    dist = float(a["walk_forward"])
    if dist <= 0:
        print(f"\n  ✗ --walk-forward 的距离必须 > 0（当前 {dist:g}m）。",
              file=sys.stderr)
        return 2

    hr("1.5 · 朝向预热（--walk-forward）")
    print(f"\n  要沿**当前朝向**前进 {dist:g}m，先走 "
          f"{WALK_FWD_WARM_S:g}s 攒出朝向窗口…")
    print("  ★ 确认角色面朝一个开阔方向（别对着墙），预热结束会自动松键。")

    sim.key_down(VK.SHIFT)
    sim.key_down(VK.W)
    hist_w: list[tuple[float, float, float]] = []
    p0: tuple[float, float] | None = None
    p_last: tuple[float, float] | None = None
    t_start = time.monotonic()
    try:
        while time.monotonic() - t_start < WALK_FWD_WARM_S:
            now = time.monotonic()
            frame = cap.grab()
            svc.on_frame(frame.image, now)
            pos = svc.get_position()
            if pos is not None:
                if p0 is None:
                    p0 = pos
                p_last = pos
                hist_w.append((now, pos[0], pos[1]))
                if len(hist_w) > WINDOW_N + 1:
                    hist_w.pop(0)
            time.sleep(0.05)
    finally:
        # ★ 无论怎么退出（异常/超时）都必须松键 —— 否则角色一直朝前冲。
        sim.key_up(VK.W)
        sim.key_up(VK.SHIFT)

    if p0 is None or p_last is None:
        print("\n  ✗ 预热期间一次定位都没拿到。", file=sys.stderr)
        print("    定位要靠 --start-world 播种 + 相位相关；检查它是否正确。",
              file=sys.stderr)
        return 2

    moved = math.hypot(p_last[0] - p0[0], p_last[1] - p0[1])
    if moved < WALK_FWD_MIN_MOVE_M:
        print(f"\n  ✗ 预热 {WALK_FWD_WARM_S:g}s 只移动了 {moved:.2f}m"
              f"（< {WALK_FWD_MIN_MOVE_M:g}m）。", file=sys.stderr)
        print("    角色没动 = 朝向不可知，「前方 N 米」无从谈起。", file=sys.stderr)
        print("    检查：是否被墙挡住 / 是否对着不可通行地形。", file=sys.stderr)
        return 2

    if len(hist_w) < 2:
        print(f"\n  ✗ 预热只拿到 {len(hist_w)} 个定位样本，算不出朝向。",
              file=sys.stderr)
        return 2
    dx = hist_w[-1][1] - hist_w[0][1]
    dz = hist_w[-1][2] - hist_w[0][2]
    theta_m = math.atan2(dz, dx)
    a["target"] = _forward_target(p0[0], p0[1], theta_m, dist)
    print(f"  预热位移 {moved:.1f}m  朝向 θ_m = {math.degrees(theta_m):+.0f}°")
    print(f"  起点 {_fmt2(p0)}  ->  沿该方向 {dist:g}m = {_fmt2(a['target'])}")
    return 0


# ---------------------------------------------------------------- 寻路状态
# ★ overlay 悬浮卡片读这个文件显示「目标 / 下一步 / 距离」（2026-10-04）。
#   路径必须与 layout.NAV_STATE_FILE 一致；那边是
#   ``<插件根>/autopilot/_runtime/pathfind_state.json``。
NAV_STATE_FILE = (PLUGIN_ROOT / "autopilot" / "_runtime"
                  / "pathfind_state.json")
#: 停止信号。overlay 的单击/双击/长按写它，本脚本主循环每帧看它。
#: 路径必须与 layout.NAV_STOP_FILE 一致。
#:
#: ★ 存在即停，内容毫无意义
#:   两边是互不 import 的独立进程，文件是唯一不需要握手的通道。
#:   多写内容只会带来「读到了半截」这种没必要的问题。
NAV_STOP_FILE = (PLUGIN_ROOT / "autopilot" / "_runtime"
                 / "nav_stop.flag")
#: schema 版本。与 layout.NAV_SCHEMA_VERSION 必须一致 —— 对不上 overlay
#: 会**不显示**（宁可少显示，也不要拿错字段名渲染出一堆错的文字）。
#:
#: ★ v2（2026-10-05）：加 ``phase`` / ``hint`` / ``eta_s`` —— 提前展现意图。
#:   v1 的第 3 行显示 ``next_action``，那是**执行态**：用户看到「右转」时
#:   人已经在转了。v2 改成写方算好的一整句 ``hint``，是**计划态**。
NAV_SCHEMA_VERSION = 2
#: 写入序号。每帧 +1，overlay 可据此判断「这确实是新状态」。
_nav_seq = 0

#: 阶段阈值（米）。与下面的模板、滞回是一套的，改一个要全看一遍。
PHASE_FAR_M = 50.0        # > 50m        -> "far"
PHASE_NEAR_M = 10.0       # 10~50m       -> "approach"

#: phase 滞回帧数。**进出都要连续这么多帧**才认。
#:
#: ★ 为什么必须有滞回
#: ------------------
#: ``dist`` 会在 50m / 10m 阈值附近来回抖：上一帧 50.1、这一帧 49.9、
#: 下一帧 50.2 —— phase 每帧换一次，浮窗第 3 行跟着每帧重画，
#: ``PanelModel.content_key()`` 的省电机制当场报废。
PHASE_STREAK_FRAMES = 3

#: ETA 用的速度估计（米/秒）。**故意取固定值，不做滑动窗口。**
#:
#: ★ 为什么不滑动窗口
#: ------------------
#: ETA 本来就要取 5 秒桶，抖动早就被压掉了；滑动窗口要多一份状态、
#: 多一条失败路径（窗口没满、原地打滑算出负速度），换来的精度在
#: 5 秒桶里**根本看不见**。实测直行约 1.40 m/s，固定 1.5 偏高约 7% ——
#: 宁可**稳定地**差 7%，不要**不稳定地**准 3%。
ETA_SPEED_MPS = 1.5
#: ETA 取桶宽度（秒）。
ETA_BUCKET_S = 5


def _raw_phase(dist_m: float, d_deg: float | None, arrived: bool) -> str:
    """**不考虑滞回**的裸阶段判定。

    :returns: ``"far"`` / ``"approach"`` / ``"final_turn"`` /
              ``"align"`` / ``"arrived"``
    """
    if arrived:
        return "arrived"
    try:
        d = float(dist_m)
    except (TypeError, ValueError):
        return "far"
    if d != d:                                   # NaN
        return "far"
    if d > PHASE_FAR_M:
        return "far"
    if d > PHASE_NEAR_M:
        return "approach"
    # ★ 只有贴近了才谈「转向」：80m 外就算 Δ=30°，用户也不关心
    #   什么时候开始转，那时的预告应该是「还有多远」。
    if d_deg is not None and abs(d_deg) > TURN_DEADZONE_DEG:
        return "final_turn"
    return "align"


def _pick_phase(current: str, pending: str | None, streak: int,
                raw: str) -> tuple[str, str | None, int]:
    """滞回：连续 :data:`PHASE_STREAK_FRAMES` 帧同一结论才换阶段。

    ★ 进出都要 N 帧
      ------------
      只给「进入」加滞回是不够的 —— 出场同样会在阈值上抖。两条路
      都得等 N 帧，代价只是切换慢 2 帧（约 0.4 秒），用户看不出来。

    :returns: ``(新 current, 新 pending, 新 streak)``
    """
    if raw == current:
        # 回到当前阶段了。**计数必须清零** —— 否则「A→B→A」这种
        # 一帧一抖的序列会攒着旧计数，下次进 B 时一帧就切过去。
        return current, None, 0
    if raw != pending:
        pending, streak = raw, 1                 # 换了目标阶段，从 1 数起
    else:
        streak += 1
    if streak >= PHASE_STREAK_FRAMES:
        return raw, None, 0                      # 认了
    return current, pending, streak              # 还不够，继续观察


def _eta_s(dist_m: float) -> int:
    """剩余秒数，**取 5 秒桶**（向下取整，不虚报）。"""
    try:
        d = max(0.0, float(dist_m))
    except (TypeError, ValueError):
        return 0
    return int(d / ETA_SPEED_MPS / ETA_BUCKET_S) * ETA_BUCKET_S


def _round10(d: float) -> int:
    """距离取整到 10m。**不能用 round()** —— 那是银行家舍入。

    与 ``layout._fmt_dist`` 同理（25.0m 会被 round 成 20m）。
    这里**必须自己算一遍**：overlay 侧的约定是「整句由写方生成，
    那边一个字都不加工」，所以共享一份逻辑反而做不到 ——
    这是**有意重复**，不是漏了抽象。
    """
    try:
        v = float(d)
    except (TypeError, ValueError):
        return 0
    if v != v:
        return 0
    return max(0, int(v / 10.0 + 0.5) * 10)


def _fmt_hint(phase: str, action: str, dist_m: float, eta_s: int,
              wp_label: str = "") -> str:
    """生成整句预告。**只有写方生成，overlay 一个字都不改。**

    :param wp_label: LLM 路点进度，形如 ``"2/3"``；非空时**取代**其余文案。

    ★ 为什么路点模式下只剩这一句
      ----------------------
      浮窗第 3 行的可用宽度有限（面板 480px、文字区约 238px，见
      ``app._compute_metrics``）。「前往路点 2/3」已经 6 个汉字加 3 个
      ASCII，再塞距离和秒数就会被裁。而距离在**第 4 行**本来就有，
      重复一遍只是挤掉别的信息。模型的 ``reason``（「沿山脊绕行」这类）
      值得看，但它的长度不受控 —— 放进一行里必然触发裁切或抖动，
      所以只打到终端和 ``--log-file``。
    """
    if wp_label:
        return f"前往路点 {wp_label}"
    if phase == "arrived":
        return "已到达"
    if phase == "final_turn":
        turn = action if action in ("左转", "右转") else "转向"
        return f"{turn}中 · 约 {eta_s} 秒后到达"
    if phase == "align":
        return "正在对准 · 即将到达"
    # ★ ``_round10`` 只在 far / approach 分支被用到，而这两个阶段
    #   按定义 dist > 10，所以不会出现「还有 0 米」这种话。
    near = _round10(dist_m)
    if phase == "approach":
        return f"接近目标 · 还有 {near} 米"
    return f"直行中 · 还有 {near} 米"


class _NavAdvisor:
    """phase 滞回 + hint 缓存。

    ★ 为什么要缓存 hint，而不是每帧重算
      ---------------------------------
      hint 里带距离（「还有 30 米」），距离每帧都在变 —— 每帧重算就是
      每帧改一次 ``content_key``。规则很简单：**只有 phase 或
      next_action 变了才重算**，其余帧原样沿用。于是行进途中第 3 行
      是**冻结**的，等阶段或动作切换才更新。这不是偷懒，是这套面板
      省电设计能成立的前提。

    ★ 为什么 eta_s 也跟着冻结
      -----------------------
      文件里 ``hint`` 和 ``eta_s`` 是同一次计算的产物，必须**同生共死**。
      否则会出现「hint 说约 40 秒、eta_s 字段却是 45」—— 同一份文件里
      两个数字打架，下一个读它的人没法判断哪个是真的。
    """

    def __init__(self) -> None:
        self.phase = "far"
        self._pending: str | None = None
        self._streak = 0
        self._hint = ""
        self._eta = 0
        self._hint_key: tuple[str, str] | None = None

    def update(self, dist_m: float, d_deg: float | None,
               state: str, action: str,
               force: bool = False, wp_label: str = "") -> dict:
        """推进一帧，返回要写进文件的 ``phase`` / ``hint`` / ``eta_s``。

        :param force: **绕过滞回**立即切换阶段。只给「终态那一次落笔」
            用 —— 那里只发生一次，「连续 3 帧」在时间上根本不存在，
            不绕过的话 state 已经是 ``arrived`` 而 phase 还停在
            ``approach``，两行自相矛盾。
        :param wp_label: LLM 路点进度（``"2/3"``）。**进缓存键** ——
            路点一变，hint 必须立刻重算，否则浮窗会一直显示上一段的
            「前往路点 1/3」直到 phase 或 action 恰好也变了。
        """
        arrived = state == "arrived"
        raw = _raw_phase(dist_m, d_deg, arrived)
        if force:
            self.phase, self._pending, self._streak = raw, None, 0
        else:
            self.phase, self._pending, self._streak = _pick_phase(
                self.phase, self._pending, self._streak, raw)

        key = (self.phase, action, wp_label)
        if key != self._hint_key:
            eta = _eta_s(dist_m)
            self._hint_key = key
            self._hint = _fmt_hint(self.phase, action, dist_m, eta, wp_label)
            self._eta = eta
        return {"phase": self.phase, "hint": self._hint, "eta_s": self._eta}


#: 模块级单例 —— phase 滞回必须**跨帧**记忆，不能每帧新建。
_NAV_ADVISOR = _NavAdvisor()


def _next_action(d_deg: float | None, state: str) -> str:
    """下一个动作的**类型**（不含角度）。

    ★ 为什么刻意不含数字
    -------------------
    overlay 的省电机制是 ``PanelModel.content_key()`` 逐帧比对指纹，
    不变就完全不碰 tk。而角度**每帧都在变**（转向是连续调整的），
    写进 next_action 就等于每帧改一次指纹 -> 10Hz 无条件重绘全部
    5 个 Label，正好把那个机制废掉。

    距离同理：显示到 ``20.5m`` 也会每帧变。距离取整到 10m 放在
    overlay 侧做（``_fmt_dist``），这里只管动作。

    :param d_deg: 本帧 Δ（度），None = 本帧没算出来。
    :param state: 当前状态。非 ``navigating`` 时由状态决定动作。
    :returns: ``"已到达"`` / ``"脱困"`` / ``"暂停"`` /
              ``"右转"`` / ``"左转"`` / ``"直行"``
    """
    if state == "arrived":
        return "已到达"
    if state == "escaping":
        return "脱困"
    if state in ("panic", "paused", "stuck"):
        return "暂停"
    if d_deg is None:
        return "直行"
    # ★ 只看**符号**，不看大小。死区内的 Δ 不该显示成转向 ——
    #   那边角色根本没转，显示「右转」是骗人。
    if d_deg > TURN_DEADZONE_DEG:
        return "右转"
    if d_deg < -TURN_DEADZONE_DEG:
        return "左转"
    return "直行"


def _write_nav_state(state: str, target_name: str | None,
                     target_pos: tuple[float, float], dist_m: float,
                     d_deg: float | None, advisor=None,
                     force: bool = False, wp_label: str = "") -> None:
    """原子写 ``pathfind_state.json``。**失败绝不打断寻路。**

    ★ 为什么要原子写
    --------------
    overlay 是**另一个进程**，它 ``read_text()`` 的瞬间正好撞上
    我们在写，就会读到半截 JSON -> 解析失败 -> 那一帧寻路行整块消失，
    表现为「行在闪」。所以：先写 ``.tmp``，再 ``os.replace()`` ——
    后者在同一卷上是原子的，读者要么看到旧全份要么看到新全份。

    ★ 为什么静默失败
    --------------
    这只是一块**便利显示**，写不进去不该让寻路崩掉。所以异常一律
    吞掉（debug 日志），继续跑。

    :param advisor: :class:`_NavAdvisor` 实例，默认用模块级单例
        （phase 滞回要跨帧记忆）。单测传自己的实例，互不干扰。
    :param force: 绕过 phase 滞回，见 :meth:`_NavAdvisor.update`。
    """
    global _nav_seq
    _nav_seq += 1
    action = _next_action(d_deg, state)
    adv = advisor if advisor is not None else _NAV_ADVISOR
    look = adv.update(dist_m, d_deg, state, action, force=force,
                      wp_label=wp_label)
    doc = {
        "schema_version": NAV_SCHEMA_VERSION,
        "written_at_ms": int(time.time() * 1000),
        "seq": _nav_seq,
        "state": state,
        "target_name": target_name,
        "target_pos": [round(float(target_pos[0]), 1),
                       round(float(target_pos[1]), 1)],
        "dist": round(float(dist_m), 1),
        "delta_deg": (round(float(d_deg), 2)
                      if d_deg is not None else None),
        # ★ next_action 保留：v2 的 hint 必填，但它是 hint 的输入，
        #   拿不到 hint 的消费方（老版本 overlay、调试脚本）还有得看。
        "next_action": action,
        # ★ 下面三个是「提前展现意图」。全部由写方算好、量化好，
        #   overlay 只做字符串直出 —— 那边**一个字都不加工**，
        #   这样 content_key 才天然稳定。
        "phase": look["phase"],
        "hint": look["hint"],
        "eta_s": look["eta_s"],
    }
    try:
        NAV_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = NAV_STATE_FILE.with_suffix(NAV_STATE_FILE.suffix + ".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, NAV_STATE_FILE)      # 原子
    except (OSError, TypeError, ValueError) as exc:
        # 便利显示写不进去 -> 继续寻路
        print(f"  [warn] 寻路状态写入失败（不影响寻路）：{exc}")


def _dynamic_max_step_px(px_to_world: float, dt_avg: float) -> float:
    """按**实测帧间隔**算这一帧允许的帧间位移上限（小地图像素）。

    ★ 为什么必须动态
    --------------
    ``TileTracker.MAX_STEP_PX`` 是个**每帧**上限，而「每帧位移」正比于
    帧间隔：同样跑 11 m/s，200ms 一帧是 5.7px，1.6fps 一帧就是 17.7px。
    写死 50 在已观测的帧率下够用，但主循环再掉就会顶到天花板 ——
    而掉帧恰恰是本项目反复遇到的事（dxcam 降级、脱困阻塞都会拖慢循环）。

    :param px_to_world: 小地图像素 -> 世界单位（取自 tracker）
    :param dt_avg: 最近若干帧的**平均帧间隔**（秒）
    """
    # ★ 比例读不出来（<=0）时走**保守**分支：取下限。
    #   反过来（放行到上限）会让一个坏配置把跳变检测整个放开。
    if not px_to_world or float(px_to_world) <= 0.0:
        return STEP_LIMIT_MIN_PX
    px_per_s = SPEED_RUN_MPS / float(px_to_world)
    raw = px_per_s * max(1e-6, float(dt_avg)) * STEP_LIMIT_SAFETY
    return max(STEP_LIMIT_MIN_PX, min(STEP_LIMIT_MAX_PX, raw))


def _split_turn(turn_px: float,
                max_segments: int | None = None) -> tuple[list[int], int]:
    """把一次转向拆成若干段，**每段都不超过饱和阈值**。

    :param max_segments: 本次最多拆几段。``None`` = 用模块级
        :data:`SPLIT_MAX_SEGMENTS`（常态 1）。站定加速转传
        :data:`TURN_STAND_SEGMENTS`。
    :returns: ``(本帧要发的段列表, 发不掉的余量)``

    ★★ 2026-10-04 重写：去掉「段数<=1 就不套上限」这条捷径
    ------------------------------------------------------
    旧代码::

        if SPLIT_MAX_SEGMENTS <= 1:
            return [total], 0        # ← 857px 原样打出去

    这行把**「不拆」**（一帧发几次）和**「不限幅」**（一次发多少）
    混成了一件事。而 :data:`SPLIT_MAX_SEGMENTS` 默认就是 1，
    于是「不拆」= 「不限幅」= 289px 直接落进饱和区（达成率 < 23%）。

    这是逻辑漏洞，不是调参问题。现在两个量彻底解耦：
      - 发几次  = ``SPLIT_MAX_SEGMENTS``（默认 1）
      - 一次多少 = ``MOUSE_SATURATION_PX``（**永远**生效）

    ★ 余量必须**退回队列**而不是丢弃或硬塞
    --------------------------------------
    60° 的大误差限幅后是 857px，而本帧只准发 40px。剩下的 817px
    如果丢掉，转向就永远差一截；如果硬塞进最后一段，就又回到
    「单段超阈值 -> 收益打折」的老问题。退回队列、下一帧继续。
    """
    total = int(round(turn_px))
    if total == 0:
        return [], 0
    sgn = -1 if total < 0 else 1
    # ★ 注意这里是 MOUSE_SATURATION_PX × **段数**，且**没有** <=1 的旁路：
    #   段数少只是「发得少」，不是「不设上限」。
    # ★★ 段数必须**传进来**（2026-10-08 修）。上一版这里写死模块级
    #   SPLIT_MAX_SEGMENTS，站定传进来的 8 段被无视 -> room 仍是 40px
    #   -> 每帧只拆出 1 段 40px = 2.8°，加速**完全没生效**。
    #   harness 实测证据：31 段 / 87° = 2.8°/帧，和常态一模一样。
    room = MOUSE_SATURATION_PX * max(
        1, SPLIT_MAX_SEGMENTS if max_segments is None else int(max_segments))
    take = min(abs(total), room)
    n = max(1, math.ceil(take / MOUSE_SATURATION_PX))
    base = take // n
    segs = [sgn * base] * n
    segs[-1] += sgn * (take - base * n)      # 尾段吸收取整余数
    segs = [p for p in segs if p != 0]
    return segs, total - sgn * take


def _turn_dispatch(owed_deg: float,
                   max_segments: int | None = None) -> tuple[list[int], float]:
    """本帧该发多少像素 -> ``(段列表, 仍然欠着的角度)``。

    ★ 队列为什么必须存**角度**而不是像素（2026-10-04）
    ---------------------------------------------------
    旧队列存的是像素，隐患是**跨帧过冲**::

        第 N 帧  |Δ|=45° -> 计划转 857px -> 发 40px，剩 817px 入队
        第 N+1 帧 |Δ| 已收到 10° -> 仍把 817px 发出去 = **过冲 10 倍**

    存角度后，每帧的欠账是「还欠多少**度**」，而下发前会拿它和
    **当前**的 ``want_deg`` 取小值 —— 方向/幅度一变，欠账自动缩小，
    不会拿着旧计划去转一个已经不存在的目标。

    :param owed_deg: 累计还欠转多少度（带符号）
    :param max_segments: 本帧最多拆几段。``None`` = 用常态的
        :data:`SPLIT_MAX_SEGMENTS`（1，即一帧一发）；站定加速转传
        :data:`TURN_STAND_SEGMENTS`（8）。**每段仍受 MOUSE_SATURATION_PX
        限幅** —— 段数只决定"发几次"，不放松"一次多少"。
    :returns: ``(本帧要发的段列表, 扣掉本帧后仍欠着的角度)``
    """
    # 本帧最多能转多少度 = 本帧最多能注入的像素 × 每像素度数
    seg_n = max(1, SPLIT_MAX_SEGMENTS if max_segments is None
                else int(max_segments))
    budget_px = MOUSE_SATURATION_PX * seg_n
    budget_deg = budget_px * MOUSE_DEG_PER_PX
    # 欠账不能超过本帧预算；用带符号的 clamp 保住方向
    send_deg = max(-budget_deg, min(budget_deg, float(owed_deg)))
    segs, _rest_px = _split_turn(send_deg / MOUSE_DEG_PER_PX, seg_n)
    # ★ 段数上限真的生效了吗？_split_turn 内部用的是模块级 SPLIT_MAX_SEGMENTS，
    #   这里传进去的 seg_n 只管**预算**。段数必须单独限制，否则预算算 8 段、
    #   实际拆出 40 段，段间隔会把主循环堵死（2026-10-04 的 625ms 事故）。
    #   截断掉的段不算进 sent_px，下面的 rest_deg 会把它们**原样留成欠账**。
    if len(segs) > seg_n:
        segs = segs[:seg_n]
    # ★ 用**实际发出的**像素回算欠账，而不是用 send_deg：
    #   取整 + 拆段会让实际发出量与 send_deg 有微小出入，
    #   账本必须和现实一致，否则误差会逐帧累积。
    sent_px = sum(abs(p) for p in segs)
    rest_deg = float(owed_deg) - math.copysign(sent_px * MOUSE_DEG_PER_PX,
                                              owed_deg if owed_deg else 1.0)
    # ★★ 残差地板：小于半像素的欠账**直接结清**（2026-10-04 修）
    #   欠 1° -> 发 14px(0.98°) -> 剩 0.02° = 0.28px。下一帧
    #   0.28px 取整成 0 -> 一像素也发不出 -> 欠账原地不动，
    #   **无限拖尾**：队列永远归不了零，每帧都白跑一次 _turn_dispatch。
    #   这 0.28px 折合 0.02°，远小于死区 8°，留着毫无意义。
    if abs(rest_deg) < MOUSE_DEG_PER_PX * 0.5:
        rest_deg = 0.0
    return segs, rest_deg


def _is_converging(d_hist: list[float], n: int = SLIP_CONVERGE_N) -> bool:
    """``|Δ|`` 历史的后半段是否比前半段**更小** -> 正在收敛。

    ★ 为什么必须有这个判断
    ----------------------
    旧打滑判据只有「|Δ|>5° 且持续 2s」，而**任何一次正常转弯**都满足
    —— 真机 40 秒误触发 14 次。区分「正常转弯」和「真打滑」的物理
    依据是：**正常转弯 |Δ| 逐帧变小，真打滑不降反升。**

    ★★ 2026-10-08：**判据只留方向，不再比降幅**（大角度转向实测打脸）
      --------------------------------------------------------------
    旧判据是 ``drop >= SLIP_CONVERGE_MIN_DEG and new < old``（5°）。
    那个 5° 的本意是"别被单帧噪声骗成收敛"，但它把**收敛速度**当成了
    判据的一部分 —— 而收敛速度是**环路增益的函数**，不是"是不是在收敛"
    的证据。真机 Δ 从 +179° 往 0° 收敛、每帧降 1~2°，6 帧窗口内
    两段均值差最多 3×2 = 6°，被 5° 卡在边缘，于是：

        t=4.2s  打滑 #1  Δ=+170.8°   ← 正在从 179 往 170 走
        t=10.6s 打滑 #2  Δ=+102.3°   ← 正在从 102 往 95走
        t=18.8s 打滑 #3  Δ=+18.0°    ← 正在从 18 往 12 走

    三次打滑时 **Δ 全都在下降**，却都被判"未在收敛"。角色一路好好地转着，
    机器人却反复停下来打断它 —— 而且每次打断都会松 W 站定 250ms，
    打断本身又拖慢收敛，形成正反馈。

    现在只要求 ``new < old``：**"在降就行，不管降多少"**。
    降速慢（每帧 1°）本来就是**在收敛**的证据，把它判成打滑才是 bug。
    抗噪声靠的是加长到 :data:`SLIP_CONVERGE_N`=10 帧（前后各 5 帧求均值），
    而不是靠一个和速度耦合的幅度门限。

    :param d_hist: 最近的 ``|Δ|`` 序列（旧 -> 新）
    :returns: 样本不足时返回 False（证据不够，不豁免）
    """
    if len(d_hist) < n:
        return False
    tail = d_hist[-n:]
    half = len(tail) // 2 or 1
    old = sum(tail[:half]) / half
    new = sum(tail[half:]) / max(1, len(tail) - half)
    # ★ 只看方向：后半段均值更小 = 在收敛。
    #   严格小于（不是 <=）：持平和反弹都不算收敛。
    return new < old


def _stand_turn_step(active: bool, d_deg: float) -> tuple[bool, str]:
    """大角度站定转的**纯状态机**，每帧推进一步。

    ★ 2026-10-08 重写：废掉 3:1 断续站定，改成「进入就一直不按 W，
      每帧拆多段把转向压到 ~8 帧」。断续版真机收益只有 5%（1.82→1.72），
      副作用是视觉顿挫，净亏。

    ★ 语义
      ------
      * 进入：``|Δ| > TURN_STAND_DEG``（60°）
      * 退出：``|Δ| <= TURN_RESUME_DEG``（20°）—— **滞回**，带宽 40°
      * 期间：**什么都不做**，保持站定（拆段加速在调用方做）

    ⚠️ 这里**没有**窗口安全阀：安全阀要能在 ``d_deg`` 为 ``None`` 时也
    触发（窗口见底那一刻正是没有 Δ 的时候），所以它必须留在调用方 ——
    放进这个纯函数就变成了永远走不到的死代码。

    :param active: 本帧之前是否处于站定模式
    :param d_deg: 本帧的 Δ（**必须**有值）
    :returns: ``(新 active, 事件)``，事件取 ``"start"`` / ``"done"`` / ``""``
    """
    if not active:
        return (True, "start") if abs(d_deg) > TURN_STAND_DEG else (False, "")
    # ★ 已在站定：只有降到恢复阈值才退出。中间那一大段（20°~60°）
    #   **绝不退出** —— 这就是滞回防抖的全部意义。
    if abs(d_deg) <= TURN_RESUME_DEG:
        return False, "done"
    return True, ""


def _turn_plan(d_deg: float) -> tuple[float, float, float]:
    """把夹角变成 ``(想转多少度, 限幅度数, 实际下发像素)``。

    ★ 这是**无量纲比例控制**，不是绝对增益 —— 见常量段的长注释。
    返回的像素数已经除过 :data:`MOUSE_DEG_PER_PX`；限幅**按度数**做，
    不按像素做（旧代码 500px 在 1 度/px 下就是一帧 500 度）。
    """
    want = -TURN_FRACTION * float(d_deg)
    cap = (TURN_MAX_DEG_BIG if abs(d_deg) >= TURN_BIG_ERR_DEG
           else TURN_MAX_DEG)
    want = max(-cap, min(cap, want))
    px = want / MOUSE_DEG_PER_PX
    if 0 < abs(px) < MIN_TURN_PX:          # 取整会变 0 的那一小撮
        px = float(MIN_TURN_PX if px > 0 else -MIN_TURN_PX)
    return want, cap, px


def _gain_for(deg: float) -> tuple[float, int]:
    """**已废弃**，仅保留给旧测试与历史日志对照。

    旧的分档绝对增益就是震荡根因（四档全部 ``|1+G*gain| > 1``）。
    现在请用 :func:`_turn_plan`。
    """
    a = abs(deg)
    for i, (hi, g) in enumerate(LEGACY_TURN_TIERS):
        if a < hi:
            return g, i
    return LEGACY_TURN_TIERS[-1][1], len(LEGACY_TURN_TIERS) - 1


def _clamp_for(deg: float) -> int:
    """**已废弃**。旧的三档像素限幅（50/200/500）。

    保留只为让旧断言还能跑；新代码走 :func:`_turn_plan` 的度数限幅。
    """
    a = abs(deg)
    if a >= 100.0:
        return 500
    if a >= 30.0:
        return 200
    return 50


def _tier_text() -> str:
    """启动横幅里的转向律描述。"""
    return (f"比例 {TURN_FRACTION:.2f}x|Δ|，限幅 ±{TURN_MAX_DEG:.0f}°"
            f"（大误差 ±{TURN_MAX_DEG_BIG:.0f}°），死区 {TURN_DEADZONE_DEG:.0f}°"
            f"，{MOUSE_DEG_PER_PX:g}°/px")



def _circ_mean_deg(angles_deg: list[float]) -> float | None:
    """一组**角度**（度）的圆周均值（度）。样本 <2 或向量和退化返回 None。

    ★ 不能用算术平均：角度跨 ±180° 时（比如 +179 和 -179，其实是
      几乎同向）算术平均会得到 0°，完全错。必须先转成单位向量、
      相加、最后 atan2。
    """
    if len(angles_deg) < 2:
        return None
    sx = sum(math.cos(math.radians(a)) for a in angles_deg)
    sy = sum(math.sin(math.radians(a)) for a in angles_deg)
    if math.hypot(sx, sy) < 1e-9:
        return None
    return math.degrees(math.atan2(sy, sx))


def _heading_deg(hist: list[tuple[float, float, float]],
                 now: float, window_s: float) -> float | None:
    """「进入方向」= 最近 ``window_s`` 秒内各段位移方向的圆周均值（度）。

    ``hist`` 是 ``(t, x, z)`` 时间戳窗口（方向估计用的那个）。

    * 用**滑动窗口平均**，**不用最后一帧 θ_m** —— 停下之后窗口位移
      归零、θ_m 冻结，拿它判朝向等于判噪声。
    * 单段位移 <= ``CLIMB_EPS`` 就跳过，避免停在原地时把噪声当方向。
    * 窗口内有效段 <2 -> None（判不出朝向，交给调用方按距离放行）。
    """
    segs: list[float] = []
    t_floor = now - window_s
    for i in range(1, len(hist)):
        ta, xa, za = hist[i - 1]
        tb, xb, zb = hist[i]
        if ta < t_floor:
            continue
        dx, dz = xb - xa, zb - za
        if math.hypot(dx, dz) <= CLIMB_EPS:
            continue
        segs.append(math.degrees(math.atan2(dz, dx)))
    return _circ_mean_deg(segs)


#: 相位相关输入图落盘文件名（落在 ``%TEMP%`` 下）。**纯诊断用**。
DIAG_MINIMAP_PNG = "minimap_capture.png"


class _Tee:
    """把输出同时写到原 stream 和一个**显式指定编码**的文件。

    ★ 为什么不让 PowerShell 去做这件事
    ----------------------------------
    ``| Tee-Object -FilePath run.log`` 写文件用的编码**取决于 PowerShell
    版本**：5.1 默认 ANSI（中文系统 = GBK），7+ 默认 utf8NoBOM。而读取时
    又可能按另一套编码解 —— 于是「控制台看着正常、文件里全是乱码」。
    编码必须在**写的那一端**就定死，shell 不参与。

    ★ 用法
    ----
    ``--log-file run.log``。文件固定 UTF-8 / LF（``newline="\\n"``），
    不管宿主是什么 Windows 区域设置。
    """

    def __init__(self, stream, fh) -> None:
        self._s = stream
        self._f = fh

    def write(self, data):
        self._s.write(data)
        self._f.write(data)
        return len(data)

    def flush(self):
        self._s.flush()
        self._f.flush()

    def isatty(self):
        return getattr(self._s, "isatty", lambda: False)()

    def fileno(self):
        return self._s.fileno()

    def reconfigure(self, **kw):
        if hasattr(self._s, "reconfigure"):
            self._s.reconfigure(**kw)

    @property
    def encoding(self):
        return getattr(self._s, "encoding", "utf-8")


def _open_log(path: str) -> str:
    """开日志文件并接管 stdout/stderr。返回**绝对路径**。"""
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    # ★ encoding 显式 utf-8、newline 显式 \n —— 这是绕开 shell 编码的全部意义
    # ★ buffering=1 = **行缓冲**：每 print 一行立刻落盘。诊断日志最怕的就是
    #   脚本崩了 / 被 Ctrl+C / 被任务管理器杀掉，缓冲里的日志全丢。
    #   行缓冲多花几微秒，换「崩了也能看到崩之前发生了什么」，值。
    fh = p.open("w", encoding="utf-8", newline="\n", buffering=1)
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = _Tee(old_out, fh)
    sys.stderr = _Tee(old_err, fh)

    def _restore() -> None:
        sys.stdout, sys.stderr = old_out, old_err
        try:
            fh.flush()
            fh.close()
        except Exception:  # noqa: BLE001
            pass

    atexit.register(_restore)      # 覆盖所有退出路径（含 SystemExit）
    return str(p)


class _MinimapDiag:
    """小地图 ROI / 相位相关诊断。**纯旁路，不参与任何决策。**

    ★ 为什么必须存 ROI 裁块
    ----------------------
    「角色视觉上在动、定位位移恒为 0」有两种完全不同的成因：

      A. ROI 抓错了区域（抓到了天空 / 墙面 / UI）—— 相关必然失败
      B. ROI 抓对了，但圆内太平或峰值太低 —— 相关被门禁挡掉

    光看 ``pos`` 区分不了 A 和 B。**必须把相位相关真正吃进去的那张图
    存下来看一眼。** 注意存的是**裁切后**的 crop，不是整屏 —— 整屏上
    那个小地图只有几十像素，看不出抓偏没。

    ★ 为什么这里读得到门禁原因
    ------------------------
    ``LocalizationService.on_frame()`` **返回** ``LocalizationState``，
    里面带 ``note`` / ``peak_response`` / ``step_px``。门禁失败时
    ``note`` 会写明是哪一道（非大世界 / circle too flat / peak below
    threshold / tracker rejected…）。本脚本原先直接丢掉了返回值，
    所以只看到「位移 0」而不知道卡在哪 —— 这里把它接出来。
    """

    def __init__(self) -> None:
        self.saved = False
        self.png = ""
        self._focus_ok: bool | None = None
        self.focus_flips = 0
        self.focus_lost_frames = 0
        self._fg = 0
        # ---- 任务 C：截图通道健康度 ----
        self._ch: str | None = None
        self.ch_first_el = 0.0
        self.ch_events: list[tuple[float, str, str]] = []   # (t, from, to)
        self.degraded_frames = 0
        self.empty_streak_max = 0
        self.dxcam_frames = 0

    def channel(self, cap, el: float) -> None:
        """跟踪截图通道切换 + dxcam 空帧。**只诊断，不改降级策略。**

        ★ 已知事实（读 capturer.py 得出）
        ----------------------------------
        ``Capturer._active`` 只在三处被赋值：``close()`` 置 None、
        ``open()`` 选初始通道、``_demote_channel()`` **向前**降一级。
        **没有任何回退/提升路径** —— 一旦 dxcam 降级到 mss，
        本次运行剩下的时间就一直是 mss。
        所以这里盯的是「什么时候降的、降了之后损失了多少帧」。
        """
        try:
            ch = cap.get_active_channel().value
        except Exception:  # noqa: BLE001
            return
        if ch != self._ch:
            if self._ch is not None:
                self.ch_events.append((el, self._ch, ch))
                print(f"[CHAN] t={el:5.1f}s  截图通道 {self._ch} -> {ch}"
                      f"  ★ **无回退路径**，此后全程用 {ch}", flush=True)
            self._ch = ch
            self.ch_first_el = el
        if ch == "dxcam":
            self.dxcam_frames += 1
        else:
            self.degraded_frames += 1
        try:
            self.empty_streak_max = max(self.empty_streak_max,
                                        int(getattr(cap, "_empty_streak", 0)))
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _foreground() -> int:
        """当前前台窗口句柄；读不到返回 0。"""
        import ctypes
        u = ctypes.windll.user32
        u.GetForegroundWindow.restype = ctypes.c_void_p
        return int(u.GetForegroundWindow() or 0)

    @staticmethod
    def _win_title(hwnd: int) -> str:
        import ctypes
        u = ctypes.windll.user32
        u.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                     ctypes.c_int]
        buf = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(ctypes.c_void_p(int(hwnd)), buf, 256)
        return buf.value or f"hwnd={hwnd}"

    def focus(self, game_hwnd) -> None:
        """游戏窗口是否在前台。**只在状态翻转时打印**，不刷屏。

        ★ 为什么值得单独查
        ------------------
        ``SendInput`` 把按键发给**当前前台窗口**。开着 PowerShell 敲完命令
        再 Alt+Tab 回来看输出，游戏就失了焦 —— 这时 W 进了 PowerShell，
        角色纹丝不动，小地图当然也不动。**这跟 ROI / 相位相关毫无关系**，
        但现象完全一样：``|win| = 0``。
        """
        fg = self._foreground()
        ok = bool(game_hwnd) and fg == int(game_hwnd)
        if not ok:
            self.focus_lost_frames += 1
        if ok == self._focus_ok:
            return
        self._focus_ok = ok
        self.focus_flips += 1
        if ok:
            print("[FOCUS] ✓ 游戏窗口在前台 —— 按键会进游戏", flush=True)
        else:
            who = self._win_title(fg) if fg else "（无）"
            print(f"[FOCUS] ✗ 前台不是游戏，当前是「{who}」—— "
                  f"按键发给它，角色不会动", flush=True)

    def first_frame(self, frame) -> None:
        """第一帧：算 ROI、存裁块、打印基准。**只跑一次**。"""
        if self.saved:
            return
        self.saved = True
        try:
            import cv2
            from autopilot.perception import minimap_mask

            cfg = minimap_mask.load_circle_cfg()
            h, w = frame.shape[:2]
            x0, y0, x1, y1 = minimap_mask.minimap_rect(frame, cfg)
            crop, mask = minimap_mask.make_minimap_roi(frame, cfg)
            out = Path(tempfile.gettempdir()) / DIAG_MINIMAP_PNG
            self.png = str(out)
            cv2.imwrite(self.png, crop)
            cv2.imwrite(str(out.with_suffix(".mask.png")), mask)

            cx = float(cfg["cx"]) * w
            cy = float(cfg["cy"]) * h
            r = float(cfg["r_norm_by_width"]) * w + minimap_mask.DEFAULT_ROI_PAD
            print(f"[MINIMAP] ROI = ({x0}, {y0}, {x1 - x0}, {y1 - y0})  "
                  f"缩放来源 = {w}x{h}", flush=True)
            print(f"[MINIMAP] 圆心=({cx:.0f}, {cy:.0f}) 半径={r:.0f}px  "
                  f"标定基准={cfg.get('ref_width')}x{cfg.get('ref_height')}  "
                  f"pad=+{minimap_mask.DEFAULT_ROI_PAD}", flush=True)
            print(f"[MINIMAP] 裁块(crop) -> {self.png}   "
                  f"尺寸 {crop.shape[1]}x{crop.shape[0]}", flush=True)
            print(f"[MINIMAP] 掩码(mask) -> {out.with_suffix('.mask.png')}"
                  f"   （圆内白=参与相关）", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[MINIMAP] 诊断失败（**不影响寻路**）："
                  f"{type(exc).__name__}: {exc}", flush=True)

    def phase(self, st, win_mag: float) -> None:
        """每帧：打印相位结果 + 门禁原因。"""
        sp = getattr(st, "step_px", None)
        pk = getattr(st, "peak_response", None)
        note = getattr(st, "note", "") or ""
        if sp is None and pk is None:
            print(f"[PHASE] |win|={win_mag:5.2f}  峰值= ---  峰位=( ---)   "
                  f"{note}", flush=True)
        else:
            d = sp if sp else (0.0, 0.0)
            print(f"[PHASE] |win|={win_mag:5.2f}  峰值={pk:5.2f}  "
                  f"峰位=({d[0]:+.2f},{d[1]:+.2f})   {note}", flush=True)


# ---------------------------------------------------------------- LLM 路点
class _WaypointCursor:
    """路点队列游标 —— **纯逻辑，零 I/O**。

    为什么要单独一个类，而不是在主循环里散着两个变量
    -----------------------------------------------
    「当前该去哪个」「到了没有」「显示第几个」这三件事必须在**一处**改。
    散开就会出现「队列空了但 index 还显示 1/3」「弹出了却没换目标」
    这类只在真机上偶发、且现场已经过去的错。单测能直接驱动它，
    不用起游戏、不用抓帧。

    ★ ``pop_reached`` 的比较用 ``>``
      ----------------------------
      「距离 **>** 阈值才算没到」= 等号归到达。浮点上 ``hypot`` 的结果
      很少正好等于阈值，但「我明明踩到了线却被判没到」用户会记很久。
    """

    def __init__(self, waypoints: list[dict] | None,
                 final: tuple[float, float]) -> None:
        self._queue = [dict(w) for w in (waypoints or [])]
        self._final = (float(final[0]), float(final[1]))
        self._total = len(self._queue)
        self._done = 0

    @property
    def active(self) -> bool:
        """还有中间路点没走完吗？"""
        return bool(self._queue)

    @property
    def total(self) -> int:
        return self._total

    @property
    def done(self) -> int:
        return self._done

    @property
    def index(self) -> str:
        """``"2/3"`` 这样的进度串。队列空了就是空串。"""
        return f"{self._done + 1}/{self._total}" if self._queue else ""

    def goal(self) -> tuple[float, float]:
        """本帧该往哪走。队列非空取队首，否则是最终目标。"""
        if self._queue:
            w = self._queue[0]
            return (float(w["x"]), float(w["z"]))
        return self._final

    def pop_reached(self, dist: float,
                    reach_m: float = WAYPOINT_REACH_M) -> dict | None:
        """当前路点在 ``reach_m`` 内 -> 弹出并返回；否则 ``None``。"""
        if not self._queue or dist > reach_m:
            return None
        w = self._queue.pop(0)
        self._done += 1
        return w


def _load_path_planner():
    """按文件加载 ``path_planner``（同目录脚本，无包上下文）。

    ★ 单独一层是为了能单测替换
      ----------------------
      单测要跑「有路点 / 没路点 / 不可达」三条分支，不能真调 API。
      把加载收敛到这一个函数，测试只要换掉它，主循环那几行一行都不用动。
    """
    import importlib.util                        # noqa: PLC0415
    p = Path(__file__).resolve().parent / "path_planner.py"
    if not p.is_file():
        raise FileNotFoundError(f"找不到 {p}")
    spec = importlib.util.spec_from_file_location("_path_planner", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _plan_with_llm(start: tuple[float, float],
                   target: tuple[float, float], *,
                   real: bool = False) -> list[dict] | None:
    """调 LLM 规划路点。返回 ``None`` / ``[]`` / ``[..]`` 三态。

    :param real: 是否真发请求。**由 ``--llm-real`` 决定**，默认 False。
        这里保留默认值 False 而不是必填：单测直接调它时不该被迫想这件事，
        而生产路径上 :func:`_parse_args` 已经保证了「配了 --llm-real
        必有 key」，缺 key 的情况在参数解析阶段就 exit 2 了。

    永不抛异常：规划是锦上添花，失败就该安静地退回「直接朝终点走」。
    """
    try:
        pp = _load_path_planner()
        return pp.get_waypoints(start, target, real=real)
    except Exception as exc:                         # noqa: BLE001
        print(f"  [LLM] 规划失败（{type(exc).__name__}: {exc}），"
              f"回退到直接朝目标走")
        return None


# ---------------------------------------------------------------- 用户路点
#: 用户路点文件格式版本。**与 anchors_viewer.py 的 WAYPOINTS_SCHEMA 必须一致**；
#: 对不上就报错退出，绝不"尽力读" —— 读错字段会让角色朝反方向走。
WAYPOINTS_SCHEMA = 1


def _load_waypoints_file(path_str: str) -> dict:
    """读 ``anchors_viewer.py`` 产出的路点文件。

    :returns: ``{"start": (x,z)|None, "waypoints": [...], "target": (x,z)}``；
        失败返回 ``{"error": "原因"}``。

    ★ 为什么不接受「部分可用」
      ----------------------
      路点少一个、顺序错一个，角色就会走错路 —— 而错路比跑不动更难
      发现。任何一处**写坏了**就整份判废，宁可让用户重选。

    ★★ 唯一的例外：null 降级（2026-10-08）
      ------------------------------------
      ``start`` / ``target`` 是 ``null`` 表示「**没写**」，不表示「写坏了」，
      所以从路点序列的首/尾补齐。这不是"部分可用"，是补全缺失项。

      判废的分界线因此很清楚：
        * ``null``       -> 降级（当没写）
        * 有值但不合规   -> 整份判废（当写坏了）

      修这个是因为 :func:`anchors_viewer.dump_waypoints` 以前把这两个字段
      恒写成 ``null``，于是**每一个**老文件都在这里撞
      「target 必须是 [x, z] 两元素」被拒 —— 而文件里明明有 2 个可用路点。
    """
    p = Path(path_str)
    try:
        # ★ utf-8-sig 而不是 utf-8
        #   Windows 上用记事本 / PowerShell Set-Content -Encoding UTF8 存过的
        #   JSON 会带 BOM，utf-8 读直接抛 "Unexpected UTF-8 BOM"。用户手改
        #   这个文件是常事，不能因为一个 BOM 就拒绝跑。
        #   utf-8-sig 对「有 BOM」和「无 BOM」都能正常解析。
        doc = json.loads(p.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        return {"error": f"读不到路点文件：{exc}"}
    except ValueError as exc:
        return {"error": f"路点文件不是合法 JSON：{exc}"}
    if not isinstance(doc, dict):
        return {"error": "路点文件顶层不是对象"}
    ver = doc.get("schema_version")
    if ver != WAYPOINTS_SCHEMA:
        return {"error": f"路点文件 schema_version={ver!r}，本脚本只认 "
                         f"{WAYPOINTS_SCHEMA}；请用 anchors_viewer 重存一次。"}

    def _pair(v, name):
        if not isinstance(v, (list, tuple)) or len(v) != 2:
            raise ValueError(f"{name} 必须是 [x, z] 两元素")
        return (float(v[0]), float(v[1]))

    try:
        wps = []
        for i, w in enumerate(doc.get("waypoints") or [], 1):
            if not isinstance(w, dict):
                raise ValueError(f"第 {i} 个路点不是对象")
            x, z = _pair([w.get("x"), w.get("z")], f"第 {i} 个路点")
            if not (math.isfinite(x) and math.isfinite(z)):
                raise ValueError(f"第 {i} 个路点坐标不是有限数：({x}, {z})")
            wps.append({"x": x, "z": z,
                        "note": str(w.get("note", "") or "")})
    except (TypeError, ValueError) as exc:
        return {"error": f"路点文件不合规：{exc}"}

    # ★★ null 降级，非 null 不合规仍然拒绝（2026-10-08）
    #   ------------------------------------------------
    #   anchors_viewer 以前把 start / target 写死成 null，于是每个老文件
    #   都在这里撞「target 必须是 [x, z] 两元素」被整份拒掉。null 的意思
    #   本来就是「没写」，不是「写坏了」—— 降级到路点序列的首/尾。
    #
    #   但**写坏了不等于没写**：target=[1] / start=["坏",1] 这类仍然照旧
    #   处理（见下面各自的口径）。错终点会让角色朝反方向走，这个代价
    #   比"重存一次文件"大得多。

    # start 只是参考值（用来提醒"点选时站的地方可能不是现在这里"），
    # 坏了不致命，沿用旧口径：只是不采用。
    start = None
    if doc.get("start") is not None:
        try:
            start = _pair(doc["start"], "start")
        except (TypeError, ValueError):
            start = None
    elif wps:
        start = (wps[0]["x"], wps[0]["z"])

    # target 是终点，必须有。null -> 序列最后一个；非 null 而不合规 -> 整份判废。
    try:
        tgt = None if doc.get("target") is None else _pair(doc["target"], "target")
    except (TypeError, ValueError) as exc:
        return {"error": f"路点文件不合规：{exc}"}
    if tgt is None:
        tgt = (wps[-1]["x"], wps[-1]["z"]) if wps else None
    if tgt is None:
        # ★ 到这一步说明 waypoints 空、start 与 target 也都是 null，
        #   既没有终点也没有路点 —— 真的什么都不知道，整份判废。
        return {"error": "路点文件不合规：target 缺失且 waypoints 为空，"
                         "无法确定终点；用 anchors_viewer.py 点选路点后 Ctrl+S。"}

    return {"start": start, "waypoints": wps, "target": tgt}


def _do_llm_planning(a: dict) -> None:
    """执行 LLM 规划并把结果写进 ``a["waypoint_queue"]``。**失败一律降级。**

    单独一个函数而不是内联在 :func:`main` 里，有两个理由：
    一是它有 40 行、内联会把 ``main`` 的骨架淹没；二是单测可以直接
    驱动它，不必先把整个 ``main`` 跑起来。
    """
    print()
    print("─" * 60)
    print("[LLM] 路径规划")
    print("─" * 60)
    # ★ 真调与否由 --llm-real 决定。默认不发：环境里恰好有个 key
    #   就默默把整张底图发出去、把钱花了，不是能默认接受的行为。
    if not a["llm_real"]:
        print("  [LLM] 未给 --llm-real，将只打印 prompt 不真发请求")
    wps = _plan_with_llm(a["start"], a["target"], real=bool(a["llm_real"]))
    if wps is None:
        print("  [LLM] 不可用或返回失败，回退到直接朝目标走")
    elif len(wps) == 0:
        print("  [LLM] 返回空路点列表（模型判定不可达），回退")
    else:
        print(f"  [LLM] 得到 {len(wps)} 个路点：")
        for i, wp in enumerate(wps):
            why = str(wp.get("reason", ""))
            print(f"    {i + 1}. ({float(wp['x']):.1f}, "
                  f"{float(wp['z']):.1f})" + (f" — {why}" if why else ""))
        a["waypoint_queue"] = list(wps)
        # ★★ 刻意**不开** ``WAYPOINT_MODE``。
        #   那个开关是个半成品的占位：它一开，主循环里
        #   「连续 3 次脱困失败」的分支就会从
        #       if not WAYPOINT_MODE: result="give_up"; break
        #   变成「打印『放弃当前路点』然后继续 do_escape」——
        #   而**没有任何代码真的弹出那个路点**。结果就是撞死的路点
        #   被无限重试到 --seconds 结束。
        #   开它之前必须先实现「弹出当前路点 + 复位 escape_level +
        #   作废 hist」，那是改脱困逻辑，明确不在本轮范围内。
        print(f"  [LLM] 目标拆成 {len(wps)} 段，逐段直线走；"
              f"路点命中半径 {WAYPOINT_REACH_M:g}m")
        print(f"  [LLM] ⚠ 某段若连续 {ESCAPE_MAX_LEVEL} 次脱困失败，"
              f"本轮仍按「无下一个路点」处理并退出（give_up）——"
              f"「跳过被卡路点」还没实现。")
    print()


# ══════════════════════════════════════════════════════════════════════
# 支线 · BetterGI 路线诊断（2026-10-09）
# ══════════════════════════════════════════════════════════════════════
# ★ 为什么先诊断不改执行逻辑
#   ---------------------------
#   用户跑 BetterGI 路点 08（26 路点 / 195m 跨度）得到：
#       结果 panic / 直线距离 6.5m / 卡住脱困 L1×3 L2×2 L3×12 / 绕路 4.94
#   只走了 12m 就 panic。**第一号路点距起点才 6.5m** —— 说明第一个路口
#   就有障碍。BetterGI 的路点是「人走出来的**采样点**」，两点之间人绕开了
#   东西；我们只能 A→B 直走，中间那堵墙我们看不见。
#
#   但「墙看不见」有两种完全不同的成因，处置方式相反：
#     分支 A · 坐标映射整体不准 -> 连位置都不对，谈路点没意义
#     分支 B · 映射准、路点本身指向障碍 -> 需要「路点外扩找可站立点」
#     分支 C · 映射准、路点也站得住 -> 是执行逻辑（死磕单点）的问题
#   所以**先量，别猜**。下面两个子模式分别量 A 和 B/C。
TP_JSON = Path(RECOG_ROOT / 'data' / '_ref_tp_sample.json')
BOTTOM_MAP = Path(RECOG_ROOT / 'data' / 'mihoyo_maps' / 'teyvat_P0.png')
#: 相邻路点间隔超过它，就标成「潜在障碍点」—— 间隔大 = 中间大概率有东西绕。
DIAG_GAP_M = 30.0


def _tp_all() -> list[dict]:
    """读传送点真值，落点用 ``tranPosition``（传送后**真正站的位置**）。"""
    doc = json.loads(TP_JSON.read_text(encoding="utf-8"))
    out = []
    for e in doc.get("data", []):
        for p in e.get("points", []):
            tr = p.get("tranPosition")
            pos = p.get("position")
            if not (isinstance(tr, (list, tuple)) and len(tr) == 3):
                continue
            out.append({
                "name": str(p.get("name", "")),
                "country": str(p.get("country", "")),
                "areas": p.get("areas") or [],
                "x": float(tr[0]), "z": float(tr[2]),
                "px": float(pos[0]) if pos else float(tr[0]),
                "pz": float(pos[2]) if pos else float(tr[2]),
            })
    return out


def _tp_find(key: str) -> list[dict]:
    k = key.strip()
    return [t for t in _tp_all()
            if k in t["name"] or k in t["country"]
            or any(k in str(x) for x in t["areas"])]


def diag_route(a: dict) -> int:
    """``--diag-route``：静态体检一条 BetterGI 路线。**不发按键**。"""
    wl = _load_waypoints_file(a["waypoints_file"])
    if "error" in wl:
        print(f"\n  ✗ {wl['error']}", file=sys.stderr)
        return 2
    wps = wl["waypoints"]
    sx, sz = wl["start"] if wl["start"] else (wps[0]["x"], wps[0]["z"])
    tx, tz = wl["target"]
    span = math.hypot(tx - sx, tz - sz)

    print()
    print("=" * 74)
    print(f"[DIAG] 路线: {Path(a['waypoints_file']).name}")
    print(f"[DIAG] 起点: ({sx:.1f}, {sz:.1f})")
    print(f"[DIAG] 终点: ({tx:.1f}, {tz:.1f})   直线跨度 {span:.1f}m")
    print(f"[DIAG] {len(wps)} 个路点：")
    prev = (sx, sz)
    gaps: list[tuple[int, float]] = []
    for i, w in enumerate(wps, 1):
        d0 = math.hypot(w["x"] - sx, w["z"] - sz)
        d1 = math.hypot(w["x"] - prev[0], w["z"] - prev[1])
        gaps.append((i, d1))
        flag = "  ←★ 间隔 %.1fm，疑似障碍" % d1 if d1 > DIAG_GAP_M else ""
        print(f"  #{i:<3} ({w['x']:9.1f}, {w['z']:9.1f}) "
              f"距起点 {d0:6.1f}m   距上一点 {d1:5.1f}m{flag}")
        prev = (w["x"], w["z"])

    ds = [g for _, g in gaps]
    print(f"[DIAG] 相邻路点距离：min {min(ds):.1f} / "
          f"mean {sum(ds) / len(ds):.1f} / max {max(ds):.1f} m")
    big = [(i, g) for i, g in gaps if g > DIAG_GAP_M]
    print(f"[DIAG] 路点间隔 > {DIAG_GAP_M:g}m 的有 {len(big)} 处"
          + ("（这些是潜在障碍点）" if big else ""))
    for i, g in big[:12]:
        print(f"        #{i}: {g:.1f}m")

    # ---- 最近的传送锚点：用户得先能走到起点 ----
    tps = _tp_all()
    if tps:
        b = min(tps, key=lambda t: math.hypot(sx - t["x"], sz - t["z"]))
        d = math.hypot(sx - b["x"], sz - b["z"])
        loc = "/".join(x for x in [b["country"]] + list(b["areas"]) if x)
        print(f"[DIAG] 起点最近的传送锚点：「{b['name']}」{loc} "
              f"({b['x']:.1f}, {b['z']:.1f})  相距 {d:.1f}m")

    # ---- 底图 + 路点叠加图 ----
    out_png = Path(os.environ.get("TEMP", r"C:\Windows\Temp")) / \
        f"route_diag_{Path(a['waypoints_file']).stem[:12]}.png"
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "misc"))
        from coord_transform import world_to_pixel
        from PIL import Image, ImageDraw
        # ★ teyvat_P0.png 有 **6.79 亿像素**，PIL 默认的解压炸弹保护
        #   （1.79 亿）会直接抛 DecompressionBombError。这是一张我们自己
        #   生成的本地底图，不是不可信输入 —— 关掉保护是安全的。
        Image.MAX_IMAGE_PIXELS = None
        import PIL.Image  # noqa: F401
        xs = [w["x"] for w in wps] + [sx, tx]
        zs = [w["z"] for w in wps] + [sz, tz]
        px = [world_to_pixel(x, z) for x, z in zip(xs, zs)]
        X0, Y0 = min(p[0] for p in px), min(p[1] for p in px)
        X1, Y1 = max(p[0] for p in px), max(p[1] for p in px)
        pad = 120
        box = (int(X0) - pad, int(Y0) - pad, int(X1) + pad, int(Y1) + pad)
        im = Image.open(BOTTOM_MAP)
        box = (max(0, box[0]), max(0, box[1]),
               min(im.width, box[2]), min(im.height, box[3]))
        crop = im.crop(box).convert("RGB")
        # 缩到最长边 1400，看得清又不太大
        sc = min(1.0, 1400.0 / max(crop.width, crop.height))
        if sc < 1.0:
            crop = crop.resize((int(crop.width * sc), int(crop.height * sc)))
        dr = ImageDraw.Draw(crop)

        def _pt(x, z):
            p = world_to_pixel(x, z)
            return ((p[0] - box[0]) * sc, (p[1] - box[1]) * sc)

        dr.line([_pt(wps[i]["x"], wps[i]["z"]) for i in range(len(wps) - 1)],
                fill=(255, 210, 0), width=2)
        for i, w in enumerate(wps, 1):
            cx, cy = _pt(w["x"], w["z"])
            dr.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=(0, 200, 255))
            dr.text((cx + 6, cy - 6), str(i), fill=(255, 255, 255))
        a0 = _pt(sx, sz)
        dr.ellipse([a0[0] - 9, a0[1] - 9, a0[0] + 9, a0[1] + 9],
                   outline=(0, 255, 0), width=3)
        dr.text((a0[0] + 12, a0[1] - 8), "START", fill=(0, 255, 0))
        a1 = _pt(tx, tz)
        dr.rectangle([a1[0] - 9, a1[1] - 9, a1[0] + 9, a1[1] + 9],
                     outline=(255, 0, 0), width=3)
        dr.text((a1[0] + 12, a1[1] - 8), "TARGET", fill=(255, 0, 0))
        crop.save(out_png)
        print(f"[DIAG] 底图已保存：{out_png}")
        print(f"       绿圈=起点 红框=终点 蓝点=路点（带编号） 黄线=直连路径")
    except Exception as exc:                                # noqa: BLE001
        print(f"[DIAG] 底图没画出来（不影响上面的统计）："
              f"{type(exc).__name__}: {exc}")
    return 0


def diag_anchor(a: dict) -> int:
    """``--diag-anchor``：量「坐标映射准不准」。**不发按键**。

    ★ 实验设计（为什么必须走出去再走回来）
      ------------------------------------
    本地化是**相对**的 —— 起点是播种进去的（``TileTracker(start)``）。
    所以「原地站着看 pos」量不出任何东西：pos 会被钉在种子上，偏差恒为 0，
    哪怕比例标定错了 20% 也一样。

    有判别力的实验是**走出去再走回同一个点**：
    走一圈回到原点，净位移**应该 ≈ 0**。如果净位移有十几米，
    说明每米换算里有系统性的比例或方向误差 —— 那就是**分支 A**，
    得先修映射，路点的事往后说。

    :param a: 解析后的参数（``diag_anchor`` = 锚点名或关键字）
    """
    from autopilot.perception.capturer import Capturer
    from autopilot.perception.localization_service import (
        LocalizationService, LocalizationStateMachine,
    )
    from autopilot.perception.tile_tracker import TileTracker

    key = a["diag_anchor"]
    hits = _tp_find(key)
    if not hits:
        print(f"\n  ✗ 「{key}」在 {TP_JSON.name} 里没找到。", file=sys.stderr)
        print("    换个关键字试试（名字 / 国家 / 二级区域都能匹配）。",
              file=sys.stderr)
        return 2
    if len(hits) > 1:
        print(f"\n  匹配到 {len(hits)} 个锚点：")
        for h in hits[:10]:
            print(f"    「{h['name']}」 {'/'.join(h['areas']) or h['country']} "
                  f"({h['x']:.1f}, {h['z']:.1f})")
        h = hits[0]
        print(f"  → 取第一个")
    else:
        h = hits[0]

    seed = (h["x"], h["z"])          # ★ 播种用 tranPosition（真落点）
    print()
    print("=" * 74)
    print("[ANCHOR] 锚点    : 「%s」" % h["name"])
    print("[ANCHOR] 地区    : %s" % ("/".join(h["areas"]) or h["country"]))
    print("[ANCHOR] 落点    : (%.1f, %.1f)   <- 用 tranPosition 播种"
          % seed)
    print("[ANCHOR] 标称    : (%.1f, %.1f)   <- position，仅供对照"
          % (h["px"], h["pz"]))
    print()
    print(f"  ★ 现在请传送到这个锚点，然后**走出去再走回同一个点**。")
    print(f"    走一个圈大概 {a['seconds']:g}s。脚本全程只**看**不按键。")
    for t in range(int(a["delay"]), 0, -1):
        print(f"    剩余 {t}s")
        time.sleep(1.0)

    svc = LocalizationService()
    svc.tracker = TileTracker(seed)
    svc._state = LocalizationStateMachine.TRACKING
    try:
        cap = Capturer(a["title"], force_channel=a["channel"])
        cap.open()
    except Exception as exc:                                # noqa: BLE001
        print(f"\n  ✗ 找不到窗口 {a['title']!r}：{exc}", file=sys.stderr)
        return 2
    print(f"  截图通道: {cap.get_active_channel().value}")
    print()

    t0 = time.monotonic()
    trace: list[tuple[float, float, float]] = []
    try:
        while time.monotonic() - t0 < a["seconds"]:
            fr = cap.grab()
            st = svc.on_frame(fr.image, fr.timestamp_ms / 1000.0)
            p = svc.get_position()
            if p is not None:
                trace.append((time.monotonic() - t0, float(p[0]), float(p[1])))
                print(f"    t={trace[-1][0]:6.1f}s  pos=({p[0]:9.1f}, {p[1]:9.1f})"
                      f"  偏离播种点 {math.hypot(p[0] - seed[0], p[1] - seed[1]):6.1f}m"
                      f"  {st.state.name}")
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n  ⚠ Ctrl+C，提前结束。")
    finally:
        cap.close()

    print()
    print("=" * 74)
    if not trace:
        print("[ANCHOR] 一帧都没定位到 —— 先解决抓屏/定位问题。")
        return 2
    path = sum(math.hypot(trace[i][1] - trace[i - 1][1],
                          trace[i][2] - trace[i - 1][2])
               for i in range(1, len(trace)))
    net = math.hypot(trace[-1][1] - seed[0], trace[-1][2] - seed[1])
    print(f"[ANCHOR] 走了 {path:.1f}m（累计位移）")
    print(f"[ANCHOR] 净位移 {net:.1f}m（应当 ≈ 0 —— 你是走回原地的）")
    print()
    if net > 10.0:
        print(f"  ✗✗ 净位移 {net:.1f}m > 10m —— **分支 A：坐标映射整体不准**。")
        print("     比例常数（或轴互换公式）有问题，先修映射，路点的事往后说。")
    elif net > 5.0:
        print(f"  ⚠ 净位移 {net:.1f}m（5~10m）—— 映射大体对，但有可观的系统误差。")
        print("     走之前记下现在的 K 是多少（0.389 还是 0.42），回头一起算。")
    else:
        print(f"  ✓ 净位移 {net:.1f}m < 5m —— **映射没问题**，问题在路点用法。")
        print("     → 走 --diag-route 那条的结论去分辨分支 B（路点在障碍里）")
        print("       还是分支 C（执行逻辑死磕单点）。")
    print()
    print("  ★ 判读要点：net/path 越接近 0 越好。比例错了的话，")
    print("    走得越远偏得越多（net 会随 path 线性涨）。")
    return 0


def main() -> int:
    # ★ global 必须在这里 —— 本函数**后面**的横幅会读 WAYPOINT_MODE，
    #   而 Python 要求 global 出现在该名字的**任何**引用之前，
    #   写在使用点旁边会直接 SyntaxError。
    global WAYPOINT_MODE
    A = _parse_args()
    # ══ 支线诊断：只诊断、不执行。必须在开窗口/抓屏/按键**之前**分流 ══
    if A.get("diag_route"):
        return diag_route(A)
    if A.get("diag_anchor"):
        return diag_anchor(A)
    # ★ 日志接管必须在**任何输出之前**（含 hr("0 · 参数")）
    if A["log_file"]:
        A["log_file"] = _open_log(A["log_file"])
    from autopilot.perception.builder import StateBuilder
    from autopilot.perception.capturer import Capturer
    from autopilot.perception.localization_service import (
        LocalizationService, LocalizationStateMachine,
    )
    from autopilot.perception.tile_tracker import TileTracker
    from autopilot.actuator.simulator import (
        DryRunBackend, InputSimulator, Win32Backend,
    )

    # ★ 用户点选的路点（anchors_viewer.py 产出）—— **必须在横幅之前载入**
    #   否则横幅那行 `{A['walk_forward']:g}` 会拿到 None 直接 TypeError
    #   （2026-10-06 实跑撞到：--waypoints-file 时 target 和 walk_forward
    #   都还没定，报的是 `unsupported format string passed to NoneType`，
    #   跟「路点文件」八竿子打不着 —— 这种错最难查）。
    #   载入后 A["target"] 立刻有真值，横幅显示的也是**真正要去的终点**。
    if A["waypoints_file"]:
        _wl = _load_waypoints_file(A["waypoints_file"])
        if "error" in _wl:
            print(f"\n  ✗ {_wl['error']}", file=sys.stderr)
            print(f"    文件：{A['waypoints_file']}", file=sys.stderr)
            print("    用 anchors_viewer.py 点选路点后 Ctrl+S 另存。",
                  file=sys.stderr)
            return 2
        A["target"] = _wl["target"]
        A["waypoint_queue"] = list(_wl["waypoints"])
        # WAYPOINT_MODE 只管「中间路点要不要判朝向」，与 LLM 那套无关；
        # 用户点选的路点同样是中间路点，所以打开。
        WAYPOINT_MODE = True
        print()
        print("─" * 60)
        print(f"[WAYPOINTS] 从 {Path(A['waypoints_file']).name} 载入 "
              f"{len(A['waypoint_queue'])} 个路点")
        for _i, _w in enumerate(A["waypoint_queue"], 1):
            _nt = str(_w.get("note") or "")
            print(f"    {_i}. ({_w['x']:9.1f}, {_w['z']:9.1f})"
                  + (f"  {_nt}" if _nt else ""))
        print(f"    最终目标 ({_wl['target'][0]:.1f}, {_wl['target'][1]:.1f})")
        # ★★ 2026-10-08 起点体检：原来只有 >50m 的软提醒。问题是
        #   「提醒了也照跑」——差 2000m 时角色会朝一个完全不相干的方向走，
        #   而 phase correlation 的位移窗口是**相对**的，它照样能收敛、
        #   照样能 arrive，只是到了一个错的地方。**静默走错比跑不动更糟。**
        #   分三档：
        #     <= 500m   只提示（点选时和开跑时站的地方略有出入很正常）
        #     <= 2000m  醒目警告，但继续（可能是故意的绕路起点）
        #     >  2000m  **拒绝启动**，除非显式加 --i-know-start
        if _wl["start"] is not None:
            _d = math.hypot(_wl["start"][0] - A["start"][0],
                            _wl["start"][1] - A["start"][1])
            if _d > START_MISMATCH_WARN_M:
                print()
                print("  " + "!" * 62)
                print(f"  !! 起点体检未通过：路点文件里的 start 与本次 "
                      f"--start-world 相距 {_d:.0f}m")
                print(f"  !!   文件里的 start = ({_wl['start'][0]:.1f}, "
                      f"{_wl['start'][1]:.1f})   （点选路点时你站的位置）")
                print(f"  !!   本次 --start-world = ({A['start'][0]:.1f}, "
                      f"{A['start'][1]:.1f})   （这次开跑时你站的位置）")
                if _d > START_MISMATCH_BLOCK_M:
                    print(f"  !! 差 {_d:.0f}m > {START_MISMATCH_BLOCK_M:g}m —— "
                          f"**这已经不是'挪了挪'，是换了一张地图的量级**。")
                    print(f"  !! 继续跑的结果：相位相关照样收敛、照样 arrive，"
                          f"但到的是**错的地方**，而且不会有任何报错。")
                    print(f"  !! 确认你知道自己在干什么，就加 --i-know-start 继续；")
                    print(f"  !! 否则请改 --start-world，或重开 anchors_viewer "
                          f"Ctrl+S 重存路点。")
                    print("  " + "!" * 62)
                    if not A.get("i_know_start"):
                        print()
                        print("  ✗ 已拒绝启动（缺 --i-know-start）。")
                        print("    文件："
                              f"{A['waypoints_file']}")
                        return 2
                else:
                    print(f"  !! {_START_MISMATCH_BLOCK_M:g}m 以内，只提醒不拦 —— "
                          f"点选后挪动位置是正常的。")
                print()
        else:
            print(f"    ⚠ 路点文件没有 start（老格式或没填），"
                  f"无法做起点体检")
        print("─" * 60)

    hr("0 · 参数")
    # --live 的**唯一**门禁：必须是管理员（游戏内核级反作弊 -> UIPI
    # 隔离永久存在 -> 脚本只能跟着提权）。dry-run 不需要。
    if _uipi_require_admin(A["live"]) != 0:
        return 2
    # ★ 目标可能还没定（--walk-forward 要等 tracker 就绪），
    #   这里不能直接解包 A['target'] —— 那会让 dry-run 之外的
    #   walk-forward 模式在第一行就 TypeError。
    _tgt_desc = (_fmt2(A["target"]) if A["target"] is not None
                 else f"待定(--walk-forward {A['walk_forward']:g}m)")
    # ★ 起点是 (0,0) 且不是用户给的 -> 标出来，免得以为那是真坐标
    _st_seeded = (A["start"] == (0.0, 0.0)
                  and A["walk_forward"] is not None
                  and "--start-world" not in A.get("_seen_args", ()))
    _st_desc = (f"{_fmt2(A['start'])}（默认种子，仅相对）" if _st_seeded
                else _fmt2(A["start"]))
    print(f"  起点 {_st_desc}  目标 {_tgt_desc}  "
          f"到达阈值 {A['arrive']}  时长 {A['seconds']:g}s")
    print(f"  移动 : {'Shift+W 跑步' if A['sprint'] else 'W 走路'}   "
          f"增益分档 {_tier_text()}")
    print(f"  卡住 : 每 {SAMPLE_MS:g}ms 采样 / 队列 {SAMPLE_N}（首尾跨 "
          f"{SAMPLE_N - 1}s）  阈值 |dx|+|dz| < {STUCK_DIST:g}")
    print(f"  脱困 : 最多 {ESCAPE_MAX_LEVEL} 级（冲刺下坠 -> 跳跃 -> 放弃路点）")
    # ★★ 2026-10-08：**启动横幅必打**，不管有没有用上。
    #   这一行存在的唯一理由是「一眼看出跑的这份代码里到底有没有这个功能」。
    #   上一轮就因为跑的是旧文件、而新功能不吭声，白排查了一轮。
    #   dry-run 也打 —— 免得看到没这行就以为功能没加载（它只在 live 生效）。
    print(f"  大角度 : |Δ|>{TURN_STAND_DEG:g}° **站定转**（松 W，每帧拆 "
          f"{TURN_STAND_SEGMENTS} 段×{MOUSE_SATURATION_PX}px / 段间隔 "
          f"{TURN_STAND_SEG_SLEEP_MS:g}ms），降到 {TURN_RESUME_DEG:g}° 恢复"
          f"   [{'live 生效' if A['live'] else 'dry-run 不生效（无真实位移）'}]")
    print(f"  到达 : {'按朝向+距离' if WAYPOINT_MODE else '只按距离（最终目标豁免朝向）'}")
    if A["use_llm"]:
        print(f"  LLM  : {'真发请求' if A['llm_real'] else '只打印 prompt（无 --llm-real）'}"
              f"  预算 {A['llm_max_calls']} 次")
    if A["log_file"]:
        print(f"  日志  : {A['log_file']}  （UTF-8/LF，已接管 stdout+stderr）")

    # ---- 目标解析：--target-tp 在这里定（--walk-forward 稍后）--------
    #   放在「距离体检」**之前** —— 体检要拿 A["target"] 算直线距离。
    if A["target_tp"] is not None:
        _rc = _resolve_target_tp(A)
        if _rc != 0:
            return _rc

    # ---- 距离体检（§47 任务 C，A~H 重写时一度被误删，这里补回）------
    #   直线寻路只在「短距离 + 目标不在墙里」时成立。起点和目标挨太近
    #   时自动寻路没有意义（肉眼就到得了，而且极易在原地抽搐）；距离
    #   太远时相位相关会一路漂，纯直线跑必然跑飞。
    #   ★ --walk-forward 的目标要等 tracker 就绪、算出朝向才知道，
    #     这时还没定，所以跳过体检（等它定下来后自己再查一次）。
    if A["target"] is None:
        print("  距离体检  : 跳过（--walk-forward 的目标尚未算出）")
    else:
        _straight_m = math.hypot(A["target"][0] - A["start"][0],
                                 A["target"][1] - A["start"][1])
        if _straight_m < 5.0:
            print(f"\n  ✗ 目标太近（起点→目标 {_straight_m:.1f}m < 5m），不启动。",
                  file=sys.stderr)
            print("    5m 以内肉眼就能确认到位，开寻路只会让角色原地抽搐。",
                  file=sys.stderr)
            return 2
        if _straight_m > 300.0:
            print(f"  ⚠ 直线距离 {_straight_m:.0f}m > 300m —— **不阻止**，但")
            print("    直线寻路只适合短距离：远处建筑/地形会让相位相关漂移，")
            print("    建议改用多路点分段。")

    # ---- LLM 路径规划（2026-10-05）----------------------------------
    #   ★ 位置在**距离体检之后**
    #   -------------------------------------------------
    #   原来放在体检**之前**，而我自己在注释里写着「先规划等于为一次
    #   注定被拒的运行白花一次 API 调用」—— 注释说的和代码做的正好相反。
    #   实跑 --start-world 1,2 --target-world 3,4 --use-llm --llm-real
    #   复现了：体检报「目标太近 2.8m，不启动」，规划却已经跑完。
    #
    #   三条约束把它夹住，顺序也就定了：
    #     · **目标解析之后** —— --target-tp 的坐标到这里才定。
    #     · **距离体检之后** —— 别为注定被拒的运行花掉一次真调用。
    #       体检对 --walk-forward 会跳过，而 --use-llm 本就和它互斥，
    #       所以走到这里一定有确定的目标可用。
    #     · **构造 Capturer 之前** —— 规格要求；顺带保证规划失败时
    #       用户还没开窗口，没白等。
    if A["use_llm"]:
        _do_llm_planning(A)

    # ---- 延时：★ 必须在 cap.open() **之前**（§51 任务 C）------------
    #   开着 PowerShell 敲命令时原神还没在前台，GetWindowRect 返回哨兵
    #   矩形（实测 158x26 @ (-21333,-21333)）。Capturer.open() 会在
    #   open() 里就按当时的 _win_rect 选通道（loop:363 逐个试，
    #   _init_channel(DXCAM) 拿哨兵矩形去 grab -> 失败 -> 降级 mss ->
    #   mss 用同一个坏矩形也失败 -> 降级 printwindow）。
    #   也就是说「dxcam 明明可用却选了 mss」是因为**通道在游戏还
    #   没进前台的时候就已经定死了**，之后没有重选机制。
    if A["delay"] > 0:
        hr("0.5 · 延迟启动")
        end = time.monotonic() + A["delay"]
        print(f"\n  [DELAY] {A['delay']:g}s 后开窗 —— ★ 现在切回游戏，"
              f"**保持它在前台**！")
        while time.monotonic() < end:
            print(f"    剩余 {end - time.monotonic():5.1f} s\r",
                  end="", flush=True)
            time.sleep(0.1)
        print()

    hr("1 · 构造")
    svc = LocalizationService()
    builder = StateBuilder(entity_detector=None)   # 寻路不需要世界实体
    sim = InputSimulator(backend=Win32Backend() if A["live"] else DryRunBackend())
    # 手动播种起点：UNINITIALIZED 下 on_frame() 会提前返回、不做相位相关，
    # 位置永远不更新。公开途径只有走完整个传送契约，对 demo 太重。
    svc.tracker = TileTracker(A["start"])
    svc._state = LocalizationStateMachine.TRACKING

    cap = None
    try:
        cap = Capturer(A["title"], force_channel=A["channel"])
        cap.open()
    except ValueError:
        print(f"\n  ✗ 通道名 {A['channel']!r} 不认识。可选：dxcam / mss / printwindow",
              file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"\n  ✗ 找不到窗口 {A['title']!r}：{type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2
    print(f"  截图通道         : {cap.get_active_channel().value}")
    if cap.window_rect and cap.window_rect[2] > 0:
        print(f"  客户区           : {cap.window_rect[2]}x{cap.window_rect[3]}")
    for _ch, _ok, _why in _probe_channels(cap.window_rect):
        print(f"  [通道] {_ch:11} {'✓' if _ok else '✗'}  {_why}")
    _uipi_check(A["live"])          # 纯诊断，只打印，不拦
    # ★★ 任务 B 的不变量：打滑门限必须**大于**转向死区（2026-10-04）。
    #   否则会存在一段「既不转向、却反复触发打滑恢复」的区间 ——
    #   角色本来已经对准了（死区内不转），却被判定「没在收敛」而按停
    #   250ms 纯转，纯属自找的打断。真机就踩过：
    #   t=59.7s Δ=+7.1°（死区 8° 以内）触发了第 3 次打滑恢复。
    #   这类配置错误**响亮失败**，不要静默跑出一堆假数据。
    if SLIP_MIN_ERR_DEG <= TURN_DEADZONE_DEG:
        print(f"\n  ✗✗ 打滑门限 {SLIP_MIN_ERR_DEG:g}° **不大于**死区 "
              f"{TURN_DEADZONE_DEG:g}° —— {TURN_DEADZONE_DEG:g}~"
              f"{SLIP_MIN_ERR_DEG:g}° 之间会「不转向却反复打滑」。",
              file=sys.stderr)
        print(f"     请把 SLIP_MIN_ERR_DEG 调到 > {TURN_DEADZONE_DEG:g}°。",
              file=sys.stderr)
        return 2

    # ---- 任务 A · --walk-forward 预热（必须在 cap 就绪之后）----------
    #   「前方 N 米」需要 θ_m，而 θ_m 需要一段**位移历史**。所以这里
    #   先原地走一小段攒样本，再把目标定下来。
    #   ★ dry-run 走不了这一步：DryRunBackend 不注入按键，角色不动，
    #     攒不出位移 -> 直接报错，而不是拿零位移算出一个假方向。
    if A["walk_forward"] is not None:
        _rc = _warmup_and_resolve_forward(
            A, svc, cap, builder, sim, panic=None)
        if _rc != 0:
            if cap is not None:
                cap.close()
            return _rc
        print(f"  目标(前方)     : {A['target']}  "
              f"（沿当前朝向前进 {A['walk_forward']:g}m）")

    # ---- 主循环状态 ---------------------------------------------------
    hr(f"2 · 寻路 {A['seconds']:g}s")
    if A["target"] is None:
        print("  ✗ 目标仍未确定 —— 这是内部不一致，请报 issue。",
              file=sys.stderr)
        if cap is not None:
            cap.close()
        return 2
    twx, twz = A["target"]
    # 路点游标。**没有 LLM 规划时它的队列是空的**，goal() 永远等于最终
    # 目标 —— 所以下面所有改动对原路径**零影响**，不需要 if 分支。
    wp_cursor = _WaypointCursor(A.get("waypoint_queue"), (twx, twz))
    # gx/gz = **本帧**该去的地方（可能是中间路点）。
    # twx/twz 保持为最终目标、**永不改变** —— 汇总段要写它进状态文件，
    # 而且最终目标的到达判据要在队列空之后才做。
    gx, gz = wp_cursor.goal()
    t0 = time.monotonic()
    prev = None
    hist: list[tuple[float, float, float]] = []      # (t, x, z) 方向窗口
    w_down = False
    ever_moved = False
    zero_streak = 0                   # 攀爬 / 卡墙（完全归零）
    slip_streak = 0                   # 打滑（|Δ| 超阈值且持续）
    slip_since = None
    slip_recoveries = 0
    d_hist: list[float] = []          # |Δ| 历史，用于收敛检查（任务 A1）
    last_turn_px = None               # 上次实际下发（本帧各段之和）
    last_max_seg = 0                  # 上次**单段**最大像素（饱和的自变量）
    last_want_deg = None              # 上次计划转的角度（按实发比例折算）
    last_turn_el = -1e9               # 上次转向时刻（任务 C 限频）
    prev_d_deg = None                 # 上一帧 Δ（任务 D）
    #: 任务 B · 饱和检测：(单段最大px, 本帧总px, 实际降了多少度, 计划降多少度)
    turn_eff: list[tuple[float, float, float, float]] = []
    eff_noise = 0                     # 低于噪声地板被剔除的样本数
    #: 转向欠账队列，单位是**角度**（不是像素，见 _turn_dispatch 的说明）
    _turn_queue_deg = 0.0
    _turn_queue_age = 0               # 余量已挂几帧
    #: ---- 大角度断续站定（2026-10-08，砍绕路系数 1.82）----
    _stand_active = False             # 是否处于大角度站定转模式
    # ★ 以下全是**全程累计**（2026-10-08 修：曾在每次 active 变化时清零，
    #   而 active 变化包含 done -> 汇总行永远打不出来）。
    _stand_frames = 0                 # 站定模式累计帧数
    _stand_nofoot = 0.0               # ★ 站定期间角色**实走**了多少米
    _stand_deg_sent = 0.0             # 站定期间累计下发的转向度数
    _stand_seg_sent = 0               # 站定期间累计下发的段数
    _stand_cycles = 0                 # 站定段数（start 次数）
    _stand_cur = 0                    # 当前这一段已持续几帧
    _stand_p0 = None                  # 本段起点的 pos（算实走 / 算段起点）
    _stand_pseg = None                # 上一个站定帧的 pos（逐帧累加位移）
    _stand_force = 0                  # 安全阀强制走的次数
    _stand_eff: list[tuple[float, float]] = []   # 站定帧的 (下发px, 实降°)
    _last_turn_stand = False          # 上一帧的转向是不是站定期发的
    _stand_hist: dict[int, int] = {}  # 段长直方图 {帧数: 次数}
    _plan_px = 0.0                    # 原始计划总量（折算达成率用）
    # ---- C2 启动探测腿（2026-10-09）----
    _probe_active = False             # 正在走探测腿
    _probe_done = False               # 朝向一旦拿到就不再进探测
    _probe_pos = None                 # 探测腿起点 pos（算走了多远）
    #: ---- 任务 C · 分段计时（找「每帧 625ms」的 400ms 在哪）----
    _dt: deque = deque(maxlen=FRAME_DT_WINDOW)      # 帧间隔滑动窗口
    t_acc = {"grab": 0.0, "phase": 0.0, "decide": 0.0,
             "send": 0.0, "log": 0.0, "wait": 0.0}
    t_max = dict(t_acc)
    _esc_block = 0.0                  # 脱困**实际阻塞**秒数（非阻塞化后应≈0，仅 key_press 残留）
    _slip_block = 0.0                 # 打滑恢复实际阻塞秒数
    esc_state: _EscapeState | None = None   # 脱困状态机（非阻塞）
    _prev_tick = None
    samples: deque = deque(maxlen=SAMPLE_N)           # 卡住检测采样队列
    last_sample_t = 0.0
    escape_level = 0
    escape_marks: list[tuple[float, float, float]] = []   # (t, x, z) 脱困时快照
    climb_escapes = 0
    escape_counts = [0, 0, 0]
    turns = 0
    path_len = 0.0
    # ★ 任务 A · 移动诊断计数器
    mv_frames = 0                # 有定位的帧数
    mv_w_frames = 0              # 其中 W 按着的帧数
    # ★★ Shift 账本必须**独立于 w_down**（2026-10-04 真机打脸）。
    #   w_down 是 sprint_on/off 的账本，但 do_escape 里的
    #   ``sim.key_down(VK.SHIFT)`` **绕过**它直接按 Shift。拿 w_down
    #   推断「Shift 有没有按住」就会漏掉脱困期间的真实状态 ——
    #   而「W 按着 100% 却只走路 1.40m/s」正是要找的那个矛盾。
    shift_state = False          # 任何路径按下 Shift 都会置 True
    shift_held_frames = 0        # Shift 实际按住的帧数
    sprint_calls = 0             # sprint_on 真正执行（非闸门早退）次数
    sprint_off_calls = 0
    mv_esc_frames = 0            # 其中因脱困故意没按 W 的帧数
    mv_dmag = 0.0                # 逐帧位移绝对值之和
    mv_stall_frames = 0          # 位移 < MOVE_STALL_EPS 的帧数（按了 W 却没动）
    pending_len = 0.0
    straight = None
    result = "timeout"
    n = 0
    diag = _MinimapDiag()          # 诊断旁路，不参与决策

    def sprint_on() -> None:
        """Shift（冲刺）先落，W 后落 —— 修饰键优先。

        ★ 原神没有「跑」这个独立动作：keymap 里 ``SPRINT`` 就是
          ``VK.SHIFT``，单独按住即冲刺；跑 = Shift + W 同时按住。
        ★ ``key_combo`` 是本模块**唯一**的原子组合 API（一次 SendInput
          发全 mod↓key↓key↑mod↑）。先用它敲一下把组合键打进游戏输入
          缓冲，再转成持续按住；那个 W 点按无害（W 是移动键不是攻击键）。
        """
        # ⚠️ 必须 nonlocal：写了 w_down 不声明会被当局部变量而 UnboundLocal
        # ★ 急停后**绝不再按键**：F12 触发时 sprint_off() 已经在监听
        #   线程里执行过了，这里再按下去 = 急停完又踩了一脚油门。
        #   三处调用（打滑恢复 / 两级脱困 / 稳态）都走这一个闸门。
        # ⚠️ sprint_calls/sprint_off_calls 也必须 nonlocal：闭包里写
        #   ``x += 1`` 会被当成**局部变量**赋值（UnboundLocalError），
        #   不声明 nonlocal 就等于把外层计数器写坏。
        nonlocal w_down, shift_state, sprint_calls, sprint_off_calls
        if w_down or panic.fired:
            return
        if A["sprint"]:
            sim.key_combo([VK.SHIFT], VK.W)
            time.sleep(SPRINT_LEAD_S)
            sim.key_down(VK.SHIFT)
            shift_state = True
        sim.key_down(VK.W)
        w_down = True
        sprint_calls += 1
        # ★ 任务 C：sprint 完整生命周期（2026-10-04）
        print(f"    [SPRINT] on  t={time.monotonic() - t0:6.1f}s  "
              f"w_down {int(w_down)} -> True  shift={int(shift_state)}  "
              f"mode={'sprint' if A['sprint'] else 'walk'}", flush=True)

    def sprint_off() -> None:
        """释放顺序与按下相反：先 W 后 Shift。"""
        nonlocal w_down, shift_state, sprint_calls, sprint_off_calls
        if not w_down:
            return
        sim.key_up(VK.W)
        if A["sprint"]:
            sim.key_up(VK.SHIFT)
            shift_state = False
        w_down = False
        sprint_off_calls += 1
        # ★ 任务 C：off 也打 —— 「Shift 掉了」可能就发生在 off 这一侧
        print(f"    [SPRINT] off t={time.monotonic() - t0:6.1f}s  "
              f"w_down 1 -> 0  shift=0", flush=True)

    def now_abs() -> float:
        """脱困状态机统一用**绝对** monotonic 时刻。"""
        return time.monotonic()

    def do_escape(level: int, why: str) -> None:
        """三级脱困 —— **非阻塞状态机**（2026-10-04 重写）。

        参考 BetterGI 的两级脱困结构（Climb 时冲刺下坠+后退 / 按序
        循环跳跃），独立实现，命名与数值均自定。**绝不碰 ``hist``** ——
        那是 θ_m 的唯一来源。

        ★★ 为什么要状态机化
        ------------------
        旧版是**同步阻塞**：``panic.stop.wait(0.4) + wait(0.7)`` 一口气
        等完 1100ms，主循环这 1.1 秒里**什么都不做** —— 不抓帧、不定位、
        不转向。真机 60s 实测脱困阻塞 **2813ms**（L1×2 + L2 + L3），
        直接把帧率从 3.8fps 拖成更慢，而卡住检测恰恰是**因为帧率慢
        才误触发**的 —— 阻塞与误触发互为因果，越堵越慢。

        关键洞察：**按键只要按下去就会一直生效**，根本不需要「等」——
        等的只是「什么时候松开、什么时候进下一阶段」。所以：
          1. ``key_down`` 之后**立即返回**，主循环继续跑；
          2. 用**绝对时间戳**记下每个阶段的到期时刻；
          3. 主循环每帧开头调 ``escape_tick()``，到期就推进阶段。

        这样脱困期间帧率不掉，定位/转向照常，而且 ``panic.stop``
        仍能被立即响应（每帧都查）。

        :param level: 1~3
        :param why: 触发原因，仅用于日志
        """
        nonlocal w_down, esc_state, shift_state
        sign = 1 if random.random() < 0.5 else -1
        sprint_off()
        if level <= 1:
            print(f"      动作 L1 冲刺下坠 {ESCAPE_T1_SPRINT_MS}ms + "
                  f"后退 {ESCAPE_T1_BACK_MS}ms")
            sim.key_down(VK.SHIFT)
            # ★★ 这里直接按 Shift，**绕过了 sprint_on/w_down 账本**。
            #   不同步 shift_state 的话，「Shift 实际按住」的统计就会
            #   在整个脱困期间（最长 1.1s）谎报 False —— 而脱困恰好是
            #   Shift 最该被按住的时刻。任务 A 的统计要能如实反映它。
            shift_state = True
            esc_state = _EscapeState(
                name="L1_shift", hold=VK.SHIFT,
                due=now_abs() + ESCAPE_T1_SPRINT_MS / 1000.0,
                stage=1, level=level, why=why)
        else:
            kind = ("后跳", "左跳", "右跳")[(level - 2) % 3]
            print(f"      动作 L{level} {kind} "
                  f"{ESCAPE_T2_JUMP_MS}ms + 移动 {ESCAPE_T2_MOVE_MS}ms")
            # ⚠️ key_press 是**阻塞**的（内部 sleep），这里仍用它：
            #   跳跃只有 260ms，且必须精确按放；改成状态机要拆成
            #   down/wait/up 三态，收益不抵复杂度。真机实测它只占
            #   2813ms 里的一小部分。
            sim.key_press(VK.SPACE, hold_ms=ESCAPE_T2_JUMP_MS)
            _mv = {"后跳": VK.S, "左跳": VK.A, "右跳": VK.D}[kind]
            sim.key_down(_mv)
            sim.mouse_move_relative(-sign * 25, 0)
            esc_state = _EscapeState(
                name=f"L{level}_{kind}", hold=_mv,
                due=now_abs() + ESCAPE_T2_MOVE_MS / 1000.0,
                stage=2, level=level, why=why)

    def escape_tick() -> None:
        """主循环每帧开头调用：推进脱困状态机。**不阻塞**。"""
        nonlocal esc_state, shift_state
        st = esc_state
        if st is None:
            return
        t = now_abs()
        # ★ 急停优先：F12 之后立刻收手，不再往下推进
        if panic.stop.is_set():
            if st.hold is not None:
                sim.key_up(st.hold)
                if st.hold == VK.SHIFT:
                    shift_state = False
            esc_state = None
            return
        if t < st.due:                      # 还没到期 -> 什么都不做
            return
        # 到期：松开当前键，推进到下一阶段
        if st.hold is not None:
            sim.key_up(st.hold)
            if st.hold == VK.SHIFT:
                shift_state = False
        if st.stage == 1 and st.level <= 1:
            # L1 第二段：后退
            sim.key_down(VK.S)
            esc_state = _EscapeState(
                name="L1_back", hold=VK.S,
                due=t + ESCAPE_T1_BACK_MS / 1000.0,
                stage=2, level=st.level, why=st.why)
            return
        # 全部阶段完成
        esc_state = None

    def escape_active() -> bool:
        return esc_state is not None

    # ★ F12 急停：独立线程，必须早于任何按键就位
    panic = _PanicWatcher(
        release_cb=sprint_off,
        on_panic=lambda: print("\n  ★★ F12 急停：已松开全部按键，停止寻路"),
    )
    _panic_err = panic.start()
    print(f"  F12 急停 : 独立线程已启动"
          f"（{1 / _PanicWatcher.POLL_S:.0f}Hz 轮询，与主循环解耦）")
    if _panic_err:
        print(f"  ✗✗ F12 急停**不可用**：{_panic_err}", file=sys.stderr)
        print("     没有急停 = 跑飞了没法刹车。请修好再 --live。", file=sys.stderr)
        panic.shutdown()
        if cap is not None:
            cap.close()
        return 2

    # ★★ 启动时先删一次停止信号（2026-10-05）。
    #   overlay 写信号时**不删**（删了会跟别的写方打架），所以上一次跑
    #   剩下的文件会一直躺在 _runtime 里 —— 不在这里清掉，这次启动
    #   一进主循环就秒停。
    #
    #   ★ 位置很讲究：**紧贴主循环之前**
    #     --------------------------------
    #     早了不行 —— 上面这段初始化（开窗口、探标定、起 F12 线程）要花
    #     几百毫秒，用户在这段时间点「停」就会被我们随后删掉，请求丢失。
    #     晚一行也不行 —— 主循环第一帧就该看到。
    try:
        NAV_STOP_FILE.unlink(missing_ok=True)
    except OSError as exc:
        # 删不掉**不拦停**。最坏情况是「上一次残留导致这次秒停」，
        # 那也比「起不来」强得多，而且下面会打出来。
        print(f"  ⚠ 清停止信号失败（若上一跑残留，本次会秒停）：{exc}",
              file=sys.stderr)

    try:
        while True:
            now = time.monotonic()
            print(f"[MAIN] tick t={now - t0:6.2f} fired={panic.fired} "
                  f"stop={panic.stop.is_set()}", flush=True)
            if panic.fired:
                print(f"t={now - t0:5.1f}s  ★ F12 急停，主循环退出")
                result = "panic"
                break
            # ★ 浮窗停止信号（2026-10-05）：单击=轻停 / 双击·长按=全停，
            #   都由 overlay 写这个文件。**放在 F12 检查之后** —— F12 是
            #   进程内急停、不经过任何文件，优先级更高；两个都不满足
            #   才轮到这里。
            #   注意检查粒度是「一帧」（真机 3~5fps），所以最多有
            #   ~250ms 延迟；停止本来就是低频操作，够用。
            if NAV_STOP_FILE.exists():
                print(f"t={now - t0:5.1f}s  ★ 收到停止信号"
                      f"（{NAV_STOP_FILE.name}），退出", flush=True)
                # 立刻松键，不等 finally —— 用户点「停」的直觉是
                # 「现在就停下来」，角色继续朝前冲半秒就很难解释了。
                sprint_off()
                result = "stopped"
                break
            el = now - t0
            if el >= A["seconds"]:
                break
            # ★ 任务 A：脱困状态机推进（**非阻塞**）。必须在抓帧**之前** ——
            #   脱困期间定位照常跑，才能一边脱困一边知道有没有走出来。
            #   这一句现在是 O(1)，不 sleep。
            escape_tick()
            # ★ 脱困进行中时**不重新触发**脱困，否则状态机被自己顶掉、
            #   手里按着的键泄漏。已进行的脱困自然走完。
            _esc_busy = escape_active()
            # ★ 任务 C：帧间隔统计（动态跳变阈值要用，也是「慢在哪」的底数）
            if _prev_tick is not None:
                _dt.append(now - _prev_tick)
            _prev_tick = now
            dt_avg = (sum(_dt) / len(_dt)) if _dt else 0.0
            _mt = time.monotonic()
            frame = cap.grab()
            _t1 = time.monotonic()
            t_acc["grab"] += _t1 - _mt
            # ★ 诊断旁路：接住 on_frame 的返回值（带 note / peak / step_px）。
            #   门禁失败时 note 会写明是哪一道。**不改变返回值的使用方式**，
            #   下面仍然只靠 svc.get_position() 驱动决策。
            _st = svc.on_frame(frame.image, now)
            _t2 = time.monotonic()
            t_acc["phase"] += _t2 - _t1
            diag.first_frame(frame.image)
            diag.focus(getattr(cap, "_hwnd", None))
            diag.channel(cap, el)
            for ev in builder.drain_events():
                print(f"  ⚠ 事件 {ev.code}: {ev.message}")
            pos = svc.get_position()
            n += 1

            if pos is None:
                print(f"t={el:5.1f}s  dist=   ---  pos=None（定位未初始化）")
                prev = None
                hist.clear()
                panic.stop.wait(max(0.0, 0.2 - (time.monotonic() - now)))
                print(f"[MAIN] wait ret fired={panic.fired} "
                      f"stop={panic.stop.is_set()} (pos-None branch)", flush=True)
                continue
            d = (0.0, 0.0)
            if prev is not None:
                d = (pos[0] - prev[0], pos[1] - prev[1])
                pending_len += math.hypot(*d)
            prev = pos
            dmag = math.hypot(*d)

            # ★★ 任务 A：移动诊断（2026-10-04）
            #   真机 60s 走了 95.1m = **1.58 m/s**，比走路（~2）还慢，
            #   更是远低于跑步（5~6）。而打滑+脱困的阻塞加起来只占 8% ——
            #   算术上**解释不了 3 倍差距**。
            #   所以必须回答：那一秒里 **W 到底按着没有**？
            #   ★ 2026-10-04：Shift 也**已经**在测了（shift_state 独立账本）。
            #     旧注释说「Shift 按不住所以测不了」—— 那时候确实没测，
            #     现在测了，别再按「测不了」的假设推演。
            mv_frames += 1
            if w_down:
                mv_w_frames += 1
                if _esc_busy:
                    mv_esc_frames += 1          # 脱困中：故意不按 W
            # ★★ 任务 A：Shift 独立计数（2026-10-04）
            #   口径与 w_down **分开**：do_escape 会绕过 w_down 直接
            #   按/放 Shift（那是脱困动作，本就不该记在 W 账本上），
            #   但「Shift 到底按住没有」必须独立回答。
            #   真机矛盾正在这里：A 策略单跑=冲刺，e2e 同样做法=1.40m/s
            #   走路，而 W 按住 100%。只有把 Shift 的真实账本打出来，
            #   才能区分「Shift 中途掉了」和「Shift 一直按着但游戏不认」。
            if shift_state:
                shift_held_frames += 1
            mv_dmag += dmag
            # ★ 口径要和 w_down 对齐：只统计「**按着 W 却没动**」的帧。
            #   （早先版本在这里无条件计数，汇总却拿 mv_w_frames 当分母，
            #    dry-run 下 W 不按住 -> 47/40 = **118%**，占比能超 100%。）
            if w_down and dmag < MOVE_STALL_EPS:
                mv_stall_frames += 1

            # 路径长度每 WINDOW_N 帧并一次（逐帧累加会把 0.1m 噪声
            # 当路程，150 帧凭空多 15m，绕路系数失真）
            hist.append((el, pos[0], pos[1]))
            if len(hist) > WINDOW_N + 1:
                hist.pop(0)
            if len(hist) == WINDOW_N + 1 and n % WINDOW_N == 0:
                path_len += pending_len
                pending_len = 0.0

            win = (0.0, 0.0)
            if len(hist) == WINDOW_N + 1:
                win = (pos[0] - hist[0][1], pos[1] - hist[0][2])
            win_mag = math.hypot(*win)
            diag.phase(_st, win_mag)          # 诊断旁路：相位结果 + 门禁原因

            # ---- 当前目标：优先走路点队列 ------------------------------
            #   没有队列时 goal() 就等于最终目标，下面几行与旧版逐字等价。
            gx, gz = wp_cursor.goal()

            dist = math.hypot(gx - pos[0], gz - pos[1])
            if straight is None:
                straight = dist
            theta_t = math.atan2(gz - pos[1], gx - pos[0])

            # ---- 路点命中：换下一个，不算「到达」 ------------------------
            _wp = wp_cursor.pop_reached(dist)
            if _wp is not None:
                print(f"t={el:5.1f}s  ★ 路点 {wp_cursor.done}/{wp_cursor.total}"
                      f" 完成（dist={dist:.1f}m <= {WAYPOINT_REACH_M:g}m）"
                      f" -> ({float(_wp['x']):.1f}, {float(_wp['z']):.1f})",
                      flush=True)
                _why = str(_wp.get("reason", ""))
                if _why:
                    print(f"           （{_why}）")
                if wp_cursor.active:
                    print(f"           下一个：路点 "
                          f"{wp_cursor.index} @ "
                          f"({wp_cursor.goal()[0]:.1f}, "
                          f"{wp_cursor.goal()[1]:.1f})")
                else:
                    print(f"           路点走完，转向最终目标 "
                          f"({twx:.1f}, {twz:.1f})")
                # ★ 朝向窗口必须作废：hist 装的是「朝上一个路点走」时的
                #   位移。带着它去判下一段，_heading_deg 会拿上一段的
                #   朝向去和新目标比，算出一个毫无意义的夹角。
                hist.clear()
                prev = pos
                # 本帧不再决策：方向已经变了，这一帧发出去的任何按键
                # 都是朝着刚够到的那个路点去的。下一帧重新算。
                continue

            # ---- 到达判据（§51 任务 G）--------------------------------
            #   中间路点：距离 + 「进入方向」窗平均朝向（Q2 决定 a）
            #   最终目标：豁免朝向，只判距离（Q2 决定 b）
            # ★ 队列非空时**不做**最终目标的到达判定 —— dist 量的是到
            #   当前路点的距离，拿它去判「到终点了」会误报。
            #   队列一空，上面的 pop_reached 也不会再触发，这里就只剩
            #   「最终目标」这一种情况，语义正好干净。
            _arrive_ok = (dist <= A["arrive"]) and not wp_cursor.active
            if _arrive_ok and WAYPOINT_MODE:
                _hd = _heading_deg(hist, el, HEADING_WINDOW_S)
                if _hd is None:
                    print("  → 距离已到，但窗口内有效位移段 <2，"
                          "按距离判定到达")
                else:
                    _hd_err = math.degrees(wrap_pi(math.radians(
                        _hd - math.degrees(theta_t))))
                    if _hd_err > ARRIVE_HEADING_TOL:
                        _arrive_ok = False
                        print(f"  → 距离已到（{dist:.1f}）但朝向差 "
                              f"{_hd_err:.0f}° > {ARRIVE_HEADING_TOL:.0f}°，"
                              f"先转正")
            if _arrive_ok:
                result = "arrive"
                print(f"t={el:5.1f}s  ★ 到达：dist={dist:.2f} <= {A['arrive']}")
                break

            turn_px = None
            d_deg = None
            theta_m = None
            if A["live"]:
                # ---- 攀爬 / 卡墙（完全归零，§51 任务 B）------------
                if dmag <= CLIMB_EPS:
                    zero_streak += 1
                else:
                    zero_streak = 0
                if dmag >= MOVE_EPS:
                    ever_moved = True

                # ---- 卡住检测：采样队列（§51 任务 F）-----------------
                stuck_now = False
                if el - last_sample_t >= SAMPLE_MS / 1000.0:
                    last_sample_t = el
                    samples.append((el, pos[0], pos[1]))
                    if len(samples) == SAMPLE_N:
                        _sx, _sz = samples[0][1], samples[0][2]
                        _ex, _ez = samples[-1][1], samples[-1][2]
                        moved = abs(_ex - _sx) + abs(_ez - _sz)
                        stuck_now = moved < STUCK_DIST
                        if not stuck_now:
                            # ★ 走出来了 -> 脱困级别归零（Q1）
                            if escape_level:
                                print(f"  → 队列位移 {moved:.1f} >= "
                                      f"{STUCK_DIST:g}，脱困级别归零")
                            escape_level = 0
                            escape_marks.clear()
                        elif escape_level:
                            print(f"  ⚠ 仍卡住（队列位移 {moved:.1f} < "
                                  f"{STUCK_DIST:g}）")
                # 快速成功：脱困后 3s 内明显走远，不等队列攒满
                # ★★ 变量名**不能**叫 _mt：1268 行的 ``_mt`` 是「grab 开始
                #   时刻」（帧内相对），而这里解包出来的是「脱困时刻」
                #   （绝对秒级）。同名会在 1611 行算 grab 峰值时变成
                #   ``_t1 - 绝对时间戳`` ≈ 1.1e9，直接污染整张诊断表。
                if escape_level and escape_marks:
                    _emk, _emx, _emz = escape_marks[-1]
                    if el - _emk <= ESCAPE_FAST_WIN_S and \
                            abs(pos[0] - _emx) + abs(pos[1] - _emz) >= STUCK_DIST:
                        print(f"  → 脱困后 {el - _emk:.1f}s 内已移动 "
                              f"{abs(pos[0]-_emx)+abs(pos[1]-_emz):.1f}，级别归零")
                        escape_level = 0
                        escape_marks.clear()

                # ══════════════════════════════════════════════════════
                # ★★ C2 · 启动探测腿（2026-10-09）
                # ────────────────────────────────────────────────────
                # 背景：θ_m 的唯一来源是**位移窗口**（WINDOW_N 帧前后的
                # 位置差，门禁 WINDOW_MOVE_MIN）。开局头 10 帧（2s）窗口
                #   是空的 -> θ_m = None -> 转向分支整段不走，但 W 照发。
                #   角色在**完全没有朝向反馈**的情况下盲走 6~10m。真机
                #   实测走了 12.3m 才 panic，而当时该去的方向在 95° 外。
                #
                # 为什么不能简单地「窗口未就绪就不发 W」
                #   ----------------------------------
                #   **会死锁**：窗口是靠位移填满的，角色不动 -> 位移恒 0
                #   -> 窗口永远为空 -> θ_m 永远是 None。所以「等待」不是
                #   选项，「盲走」才是。区别在于**盲走必须有上限**。
                #
                # 所以 C2 做三件事：
                #   1. 把前几帧明确标成「探测腿」，打日志说清楚在干什么
                #   2. 探测腿**上限 15m**（:data:`BLIND_PROBE_MAX_M`）——
                #      超过就带明确报错退出，不产出「盲走 200m 才 panic」
                #      这种没法归因的垃圾数据
                #   3. 卡住（stuck_now）时**提前**退出并说明原因
                #
                # 探测腿本身**不发 mouse_move**（转向分支的入口条件就
                # 要求 θ_m is not None，天然满足），只按 W+Shift。
                if theta_m is not None and _probe_active:
                    _probe_moved = (math.hypot(pos[0] - _probe_pos[0],
                                               pos[1] - _probe_pos[1])
                                    if _probe_pos is not None else 0.0)
                    print(f"  ★ 启动探测腿完成：走了 {_probe_moved:.1f}m，"
                          f"θ_m = {math.degrees(theta_m):+.1f}°，"
                          f"Δ = {d_deg:+.1f}°", flush=True)
                    _probe_active = False
                elif theta_m is None and not _probe_done:
                    if not _probe_active:
                        _probe_active = True
                        _probe_pos = pos
                        print()
                        print("  ★ 启动探测腿：窗口未就绪，先盲走一段攒朝向"
                              f"（上限 {BLIND_PROBE_MAX_M:g}m）。")
                        print("    ★ 这段**不转向**，是故意的 —— "
                              "θ_m 靠位移窗口算，没窗口就没有转向依据。")
                    _pm = (math.hypot(pos[0] - _probe_pos[0],
                                      pos[1] - _probe_pos[1])
                           if _probe_pos is not None else 0.0)
                    if stuck_now and ever_moved:
                        _probe_done = True
                        print(f"\n  ✗ 启动探测腿中止：{_pm:.1f}m 处判定为"
                              f"**卡住**（按着 W 但队列位移 < {STUCK_DIST:g}m）"
                              f" —— 起点前面就有墙，别往这个方向探了。",
                              flush=True)
                        result = "startup_stuck"
                        break
                    if _pm > BLIND_PROBE_MAX_M:
                        _probe_done = True
                        print(f"\n  ✗ 启动探测腿失败：盲走 {_pm:.1f}m"
                              f" > {BLIND_PROBE_MAX_M:g}m，位移窗口仍然不足。")
                        print("    可能：① 起点在室内/狭窄处走不动 "
                              "② 缩放档变了 ③ 小地图被遮挡")
                        print("    换个开阔的起点，或先跑 --diag-anchor 量定位。",
                              flush=True)
                        result = "startup_failed"
                        break
                if theta_m is not None:
                    _probe_done = True

                # ---- 打滑检测（§51 任务 E）--------------------------
                if win_mag >= WINDOW_MOVE_MIN:
                    theta_m = math.atan2(win[1], win[0])
                    d_deg = math.degrees(wrap_pi(theta_t - theta_m))
                    # ★★ 死区内的 Δ 一律**不计入**打滑 streak（2026-10-04）。
                    #   这才是「已对准却被按停」的真正修法：|Δ| <= 死区时
                    #   角色本来就不转向，打滑恢复没有任何可恢复的东西 ——
                    #   停 250ms 纯转纯属自伤。
                    #   调高 SLIP_MIN_ERR_DEG 只是把区间挪走，没有消灭它；
                    #   这里才是从判据上堵死。真机 Δ=+7.1° 就是这么修的。
                    if abs(d_deg) > TURN_DEADZONE_DEG:
                        if abs(d_deg) > SLIP_MIN_ERR_DEG:
                            slip_streak += 1
                            slip_since = el if slip_since is None else slip_since
                        else:
                            slip_streak = 0
                            slip_since = None
                    else:
                        # 死区内：已对准，既不转向也不该判打滑
                        slip_streak = 0
                        slip_since = None
                        d_hist.clear()
                    d_hist.append(abs(d_deg))
                    while len(d_hist) > SLIP_CONVERGE_N:
                        d_hist.pop(0)

                # ★ 收敛检查（任务 A1）——判据提成纯函数 _is_converging，
                #   便于直接单测（构造「收敛路径」喂进整条主循环来验反而
                #   很难，因为 θ_t 会随角色移动一起变）。
                _converging = _is_converging(d_hist, SLIP_CONVERGE_N)

                # ★ 次数上限（任务 A2）：反复打断本身就是故障信号，
                #   到顶就停手并在汇总里报出来，别无限刷。
                _slip_allowed = slip_recoveries < SLIP_MAX_RECOVERIES

                slipping = (slip_streak >= SLIP_FRAMES
                            and slip_since is not None
                            and el - slip_since > SLIP_SECONDS
                            and not _converging
                            and _slip_allowed
                            # ★ 必须有**本帧**的 Δ 才有转向依据。
                            #   slip_streak/slip_since 是历史累积的，而当
                            #   win_mag < WINDOW_MOVE_MIN 时 d_deg 保持 None
                            #   —— 少了这一句就是 None * float 直接 TypeError
                            #   把脚本打死（本轮由单测抓出）。
                            and d_deg is not None)

                if slipping:
                    # 松 W 站定纯转，再按 W —— 边走边转时游戏可能把转向
                    # 抵消掉，那样 Δ 永远降不下来。
                    # ★ 持续时长要在清 slip_since **之前**取好。
                    _slip_el = slip_since if slip_since is not None else el
                    slip_streak = 0
                    slip_since = None
                    d_hist.clear()
                    slip_recoveries += 1
                    sprint_off()
                    _turn = max(-SLIP_PURE_MAX_DEG,
                                min(SLIP_PURE_MAX_DEG,
                                    -SLIP_PURE_FRACTION * d_deg))
                    # ★★ 发送路径必须与常态**统一**走 _turn_dispatch（2026-10-04）
                    #   真机打脸：t=4.3s「松 W 站定纯转 +40°/+571px」——
                    #   这一发 571px **完全绕过了** MOUSE_SATURATION_PX=40，
                    #   直落饱和区。与上一轮 SPLIT_MAX_SEGMENTS=1 绕过 60px
                    #   是同一类 bug：每条「自己算一遍 turn」的分支都会漏掉限幅。
                    #   打滑恢复可以有**自己的 fraction/限幅**（站定时没有
                    #   「前进抵消」，可以转得更狠），但**发送**必须走同一条路。
                    _segs, _slip_rest = _turn_dispatch(_turn)
                    print(f"t={el:5.1f}s  ★ 打滑恢复 #{slip_recoveries}/"
                          f"{SLIP_MAX_RECOVERIES}：连续 {SLIP_FRAMES} 帧 "
                          f"|Δ|>{SLIP_MIN_ERR_DEG:g}° 持续 {el - _slip_el:.1f}s "
                          f"且**未在收敛** —— 松 W 站定纯转 "
                          f"{_turn:+.0f}°/{_turn / MOUSE_DEG_PER_PX:+.0f}px "
                          f"分 {len(_segs)} 段发（每段 <= "
                          f"{MOUSE_SATURATION_PX}px）"
                          f"（Δ={d_deg:+.1f}°）")
                    for _spx in _segs:
                        sim.mouse_move_relative(_spx, 0)
                    _ts = time.monotonic()
                    # 段间隔用 panic.stop.wait（可被 F12 打断），不用 time.sleep
                    if len(_segs) > 1 and panic.stop.wait(
                            SPLIT_SLEEP_MS / 1000.0 * (len(_segs) - 1)):
                        pass                       # 急停已触发，后续动作自行拒绝
                    # ★ 站定 250ms 不能省：这是打滑恢复**能生效的物理前提**
                    #   —— 松开 W 让转向不被前进抵消。上一版是一发打完
                    #   立刻等 250ms；现在拆成多段后必须**等完段间隔再站定**，
                    #   否则最后一段还在转向中就按回 W，等于又边走边转。
                    if not panic.stop.wait(SLIP_STAND_MS / 1000.0):
                        pass
                    _slip_block += time.monotonic() - _ts
                    # ★ 欠账记入**常态**转向队列：打滑是「一次性动作」，
                    #   不跨帧排队；但若 40px 上限导致没发完，剩下的角度
                    #   不能凭空消失 —— 并进常态队列，下一帧继续补完。
                    # ★★ 账龄必须**一起清零**（2026-10-08）：这里是**覆盖**
                    #   队列而不是追加，新账本从 0 开始算帧数。
                    #   不清的话，打滑前攒下的帧数会被算到打滑后的新账上。
                    _turn_queue_deg = _slip_rest
                    _turn_queue_age = 0
                    prev = pos
                    sprint_on()      # 急停后自动拒绝（见 sprint_on 闸门）
                elif zero_streak >= CLIMB_FRAMES and ever_moved and not _esc_busy:
                    _zs = zero_streak      # ★ 先存后清，否则日志打「第 0 帧」
                    zero_streak = 0
                    climb_escapes += 1
                    if climb_escapes > ESCAPE_MAX_LEVEL:
                        print(f"t={el:5.1f}s  ✗ 连续 {ESCAPE_MAX_LEVEL} "
                              f"级脱困仍不动 -> 放弃当前路点")
                        result = "climb"
                        break
                    escape_level = min(escape_level + 1, ESCAPE_MAX_LEVEL)
                    escape_counts[min(escape_level, 3) - 1] += 1
                    print(f"t={el:5.1f}s  ★ 卡墙/攀爬（连续 {_zs} 帧归零）"
                          f" -> L{escape_level} 脱困")
                    escape_marks.append((el, pos[0], pos[1]))
                    do_escape(escape_level, "climb")
                    prev = pos
                    # ★ 不调 sprint_on：脱困帧状态机正按着 Shift，
                    #   叠 Shift+W 会让下坠失效。脱困结束后由稳态分支恢复。
                elif stuck_now and ever_moved and not _esc_busy:
                    escape_level = min(escape_level + 1, ESCAPE_MAX_LEVEL)
                    escape_counts[min(escape_level, 3) - 1] += 1
                    print(f"t={el:5.1f}s  ★ 卡住 -> L{escape_level} 脱困"
                          f"（第 {escape_level}/{ESCAPE_MAX_LEVEL} 级）")
                    escape_marks.append((el, pos[0], pos[1]))
                    if escape_level >= ESCAPE_MAX_LEVEL:
                        print(f"t={el:5.1f}s  ✗ 连续 {ESCAPE_MAX_LEVEL} 次"
                              f"脱困失败，**放弃当前路点**")
                        if not WAYPOINT_MODE:
                            print("      无下一个路点 -> 退出")
                            result = "give_up"
                            break
                    do_escape(escape_level, "stuck")
                    prev = pos
                    # ★ 同上：脱困帧不叠 Shift+W
                else:
                    # ---- 大角度站定转（2026-10-08 重写）------------------
                    #   物理式：边走边转 Δ=179° 的弧长 = π·v/ω。ω=2.8°/帧
                    #   ÷0.2s = 14°/s，按 v≈3m/s 算弧长 38.6m —— 真机反推
                    #   32.8m，同量级。**所以绕路不是 bug，是 ω 太小的必然。**
                    #   站定把弧长打到 0；加速转把 ω 提上去，两者都不靠
                    #   「走得更少」，而是「转得更快 + 走得更少」。
                    #
                    #   ★★ 为什么必须留安全阀（不是可选项）
                    #     θ_m 唯一来源是位移窗口（:data:`WINDOW_N` 帧的位置差，
                    #     门禁 :data:`WINDOW_MOVE_MIN`）。站定第 8 帧起窗口里
                    #     **全是静止样本**，净位移归零 -> d_deg=None -> 判据
                    #     拿不到输入。所以「一直站到 20°」在结构上做不到，
                    #     必须有一条**保命**的走一帧出口。它和按角度收尾的
                    #     TURN_RESUME_DEG 是两个独立出口。
                    if _stand_active and win_mag < TURN_STAND_BLIND_M:
                        # ★ 安全阀要在 d_deg 可能为 None 时也能触发，所以放在
                        #   门禁判断**之前**。赶在 θ_m 死掉前主动走一帧。
                        _stand_active = False
                        _stand_force += 1
                        _stand_hist[_stand_cur] = (
                            _stand_hist.get(_stand_cur, 0) + 1)
                        print(f"t={el:5.1f}s  ⚠ 大角度站定：位移窗口 "
                              f"{win_mag:.2f}m < {TURN_STAND_BLIND_M:g}m -> "
                              f"强制走一帧（第 {_stand_force} 次，本段 "
                              f"{_stand_cur} 帧）", flush=True)
                    elif (win_mag >= WINDOW_MOVE_MIN and theta_m is not None
                            and d_deg is not None):
                        _sa, _sev = _stand_turn_step(_stand_active, d_deg)
                        _stand_active = _sa
                        if _sev == "start":
                            _stand_cycles += 1
                            _stand_cur = 0
                            _stand_p0 = pos
                            _stand_pseg = pos
                            print(f"t={el:5.1f}s  ★ 大角度转向：站定转"
                                  f"（Δ={d_deg:+.1f}°）—— 松 W，"
                                  f"每帧最多 {TURN_STAND_SEGMENTS} 段 × "
                                  f"{MOUSE_SATURATION_PX}px"
                                  f"  [第 {_stand_cycles} 段]", flush=True)
                        elif _sev == "done":
                            _stand_hist[_stand_cur] = (
                                _stand_hist.get(_stand_cur, 0) + 1)
                            _moved = (math.dist(pos, _stand_p0)
                                      if _stand_p0 is not None else 0.0)
                            print(f"t={el:5.1f}s  ★ 大角度转向：完成"
                                  f"（Δ={d_deg:+.1f}°，用时 {_stand_cur} 帧）"
                                  f"—— 恢复行走；本段角色实走 {_moved:.2f}m",
                                  flush=True)
                        if _stand_active:
                            _stand_frames += 1
                            _stand_cur += 1
                            if (_stand_pseg is not None and pos is not None):
                                _stand_nofoot += math.dist(pos, _stand_pseg)
                            _stand_pseg = pos

                    # ---- 方向修正：误差比例控制 --------------------------
                    #   ★ 与旧的分档绝对增益**完全不同**，见常量段长注释：
                    #   旧式 |1 + G*gain| > 1 必然震荡；现在是
                    #   |1 - G*0.45|，对未标定的 G 天然稳定。
                    if win_mag >= WINDOW_MOVE_MIN and theta_m is not None:
                        # ★ 任务 D：先记下**上一帧**的 Δ，用来实测 G。
                        if (last_turn_px is not None and d_deg is not None
                                and prev_d_deg is not None
                                and last_want_deg is not None):
                            # d_deg = θ_t - θ_m，所以 Δd_deg 就是「误差
                            # 实际被压掉了多少度」（正值 = 在收敛）
                            _got_deg = d_deg - prev_d_deg
                            if len(turn_eff) < TURN_MEASURE_SAMPLES:
                                # ★ 任务 B：实测降幅 < 噪声地板的样本**不入
                                #   统计**（θ_m 是 2 秒窗口方向，0.3° 那种
                                #   读数是噪声不是效果），单独计数。
                                if abs(_got_deg) >= TURN_EFF_NOISE_DEG:
                                    turn_eff.append((last_max_seg,
                                                     abs(last_turn_px),
                                                     abs(_got_deg),
                                                     abs(last_want_deg)))
                                    # ★ 站定帧的样本单独留一份：常态和站定的
                                    #   达成率不是一回事（站定拆段 vs 常态单段），
                                    #   混在一起算出来的 23% 没有可解释性。
                                    if _last_turn_stand:
                                        _stand_eff.append((
                                            abs(last_turn_px),
                                            abs(_got_deg)))
                                else:
                                    eff_noise += 1
                        prev_d_deg = d_deg

                        if abs(d_deg) <= TURN_DEADZONE_DEG:
                            # ★ 到位了就别再发排队的旧计划了
                            _turn_queue_deg = 0.0
                        elif (el - last_turn_el) < TURN_MIN_INTERVAL_S:
                            pass                      # ★ 任务 C：限频
                        else:
                            # ★★ 2026-10-04 重写：队列不再是「本帧发多少」的闸门
                            #
                            # 真机打脸（60s / 4.7fps）：转向 262 次，却只转了
                            # 26° = **0.093°/帧**，而 40px 应该给 ~3°/帧 ——
                            # **慢了 32 倍**。根因就在下面这行 min()：
                            #
                            #     _owed = min(|queue|, |want|)
                            #
                            # 队列是**上一帧剩下的单笔欠账**，want 是**本帧重算的
                            # 增量**，两者量纲相同但语义不同。取小之后：
                            #     Δ=15°  -> want=6.75 -> 扣 2.8 剩 3.95
                            #     Δ=13.5 -> want=6.10 -> min(3.95,6.10)=3.95
                            #             -> 扣 2.8 剩 1.15
                            #     Δ=12   -> want=5.40 -> min(1.15,5.40)=1.15  ← 榨干
                            # 之后每帧只发 1.15°/0.07 = **16px**，而不是 40px。
                            # 欠账越欠越小，队列反而成了**限流阀**。
                            #
                            # 正确语义：want_deg 是比例律给出的「本帧该转多少」，
                            # 它已经隐含了「转完这一帧就该重新评估」。**不该被
                            # 历史欠账夹住**。欠账只在两种情况有意义：
                            #   1. 本帧 want 被单次上限截断（剩的要下帧补）
                            #   2. 打滑恢复是一次性动作，没发完的要补
                            # 这两种都由 _turn_dispatch 内部的 rest 自然表达，
                            # **不需要**再和外层 want 取 min。
                            want_deg, cap_deg, turn_px = _turn_plan(d_deg)
                            _qb = _turn_queue_deg          # 诊断用：账本原值
                            # ★★ A2：作废判据 = **方向是否真反号**（2026-10-08）
                            #   「队列挂了多久」根本不是「方向变了」的证据 ——
                            #   见 TURN_QUEUE_MAX_FRAMES 的注释：只要 |Δ|>死区，
                            #   队列就数学上永不归零，按帧数判废等于**每次转向
                            #   都会炸**。真机 179° 转向第 31 帧起每帧作废。
                            #   「方向真变了」的精确语义就是：目标跑到另一边去了。
                            _turned_around = (_qb != 0
                                              and (want_deg >= 0) != (_qb >= 0))
                            # ★ 发多少 = 本帧 want 与「本帧预算」的较小者。
                            #   欠账只在 want 更大时合并（补上帧没发完的），
                            #   绝不用来**缩小**本帧。
                            _target = want_deg
                            if (not _turned_around
                                    and _qb != 0
                                    and (_qb >= 0) == (want_deg >= 0)
                                    and abs(_qb) > abs(want_deg)):
                                _target = _qb             # 旧账更大 -> 先还它
                            # ★ 达成率折算的分母必须跟**实际能发的上限**一致。
                            #   站定期预算被放大到 8 段，若还用常态的
                            #   |turn_px| 当分母，abs(_sent)/_plan_px 会 >1，
                            #   turn_eff 的「计划降幅」被系统性高估。
                            _plan_px = min(
                                abs(turn_px),
                                MOUSE_SATURATION_PX * (TURN_STAND_SEGMENTS
                                                       if _stand_active
                                                       else max(1,
                                                                SPLIT_MAX_SEGMENTS)))
                            # ★ 站定期间**拆段加速转**（2026-10-08）：
                            #   常态 1 段 40px = 2.8°/帧；站定 8 段 = 22.4°/帧。
                            #   每段仍受 MOUSE_SATURATION_PX 限幅 —— 段数只决定
                            #   「发几次」，不放宽「一次多少」。
                            _segn = TURN_STAND_SEGMENTS if _stand_active \
                                else None
                            _segs, _rest = _turn_dispatch(_target, _segn)
                            if _stand_active:
                                _stand_deg_sent += sum(
                                    abs(x) for x in _segs) * MOUSE_DEG_PER_PX
                                _stand_seg_sent += len(_segs)
                            if _turned_around:
                                # ★ 本帧的 want 已经把旧账整个盖掉了
                                #   （_target 走的是 want_deg，旧账没参与发送），
                                #   所以这里必须**丢掉** _rest —— 否则反号那一帧
                                #   会把 want 的残差当成旧计划继续挂着。
                                print(f"  ★ 转向余量作废：目标**跑到反方向**了"
                                      f"（旧账 {_qb:+.1f}° vs 本帧 want "
                                      f"{want_deg:+.1f}°）—— 不再追旧计划")
                                _turn_queue_deg = 0.0
                                _turn_queue_age = 0
                            else:
                                _turn_queue_deg = _rest
                                if _rest != 0:
                                    _turn_queue_age += 1
                                    if _turn_queue_age > TURN_QUEUE_MAX_FRAMES:
                                        # ★ panic 兜底，不是调参旋钮。
                                        #   同时把 age 清零 —— 旧代码漏了这一点，
                                        #   导致一旦越过上限就**每帧都打印**
                                        #   （真机日志里 31/32/33… 连着刷的原因）。
                                        print(f"  ✗ 转向余量挂了 "
                                              f"{_turn_queue_age} 帧仍未消化，"
                                              f"作废 {_rest:+.1f}°（疑似卡死，"
                                              f"不是转向慢）")
                                        _turn_queue_deg = 0.0
                                        _turn_queue_age = 0
                                else:
                                    _turn_queue_age = 0
                            # ★ 任务 A 诊断：把每帧的完整账目摊开
                            #   （真机 0.093°/帧 vs 预期 3°/帧 就是靠这行定位的）
                            _sent_px = sum(abs(x) for x in _segs)
                            if A["live"] and turns % 1 == 0:
                                print(f"    [TURN-EXEC] Δ={d_deg:+.1f}° "
                                      f"want={want_deg:+.2f}° "
                                      f"budget={MOUSE_SATURATION_PX * MOUSE_DEG_PER_PX:.2f}° "
                                      f"q_before={_qb:+.2f}° "
                                      f"target={_target:+.2f}° "
                                      f"segs={_segs} "
                                      f"sent={_sent_px}px/{_sent_px * MOUSE_DEG_PER_PX:+.2f}° "
                                      f"q_after={_rest:+.2f}°")
                            _sent = 0
                            _max_seg = 0
                            # ★★ 段间隔必须**夹在段与段之间**，不能攒到最后
                            #   一次性等完。攒到最后 = 8 段在同一瞬间发出去
                            #   = 全落在同一个渲染帧 = 被游戏的单帧位移上限
                            #   **整体**裁掉，拆分等于没拆 —— 这正是
                            #   2026-10-04「8 段全并进同一帧、主循环撑到
                            #   625ms」的成因。
                            _gap_ms = (TURN_STAND_SEG_SLEEP_MS
                                       if _stand_active else SPLIT_SLEEP_MS)
                            for _i, _px in enumerate(_segs):
                                sim.mouse_move_relative(_px, 0)
                                _sent += _px
                                _max_seg = max(_max_seg, abs(_px))
                                if _i < len(_segs) - 1:
                                    # ★ 必须用 panic.stop.wait，**不能**
                                    #   time.sleep：站定一次拆 8 段最多堵
                                    #   7×40 = 280ms，用 time.sleep 的话
                                    #   F12 急停会被硬生生拖住 280ms。
                                    #   返回 True = 急停已触发 -> 余量作废。
                                    if panic.stop.wait(_gap_ms / 1000.0):
                                        _turn_queue_deg = 0.0
                                        break
                            turns += 1
                            # ★★ 2026-10-08 诊断：站定帧把**实际发出去的**
                            #   段数 / 像素 / 角度逐帧打出来。
                            #   真机 TURN-EFF 报「计划 16.1°、实际 3.7°、
                            #   达成率 23%」时，有两种完全不同的解释：
                            #     (a) 我们**只发了 1 段 40px**（派发端的问题）
                            #     (b) 我们发了 8 段 320px，**游戏只认 1 段**
                            #         （段间隔不够 / 游戏每帧只处理一次移动）
                            #   这行日志就是用来区分这两者的 —— 不加它，
                            #   光看 TURN-EFF 会误以为是 (b)。
                            _last_turn_stand = bool(_stand_active)
                            if A["live"] and _stand_active:
                                _s_px = sum(abs(x) for x in _segs)
                                print(f"    [STAND-SEG] Δ={d_deg:+.1f}° "
                                      f"want={want_deg:+.2f}° "
                                      f"target={_target:+.2f}° "
                                      f"-> 实发 {len(_segs)} 段 / {_s_px}px "
                                      f"= {_s_px * MOUSE_DEG_PER_PX:+.1f}° "
                                      f"(段间隔 {_gap_ms:g}ms, 阻塞 "
                                      f"{_gap_ms * max(0, len(_segs) - 1):.0f}ms)",
                                      flush=True)
                            # 按**本帧实发比例**折算计划降幅，这样跨帧分
                            # 发的样本也能和达成率对齐
                            last_want_deg = want_deg * (
                                abs(_sent) / max(1e-9, _plan_px))
                            last_turn_px = _sent
                            last_max_seg = _max_seg
                            last_turn_el = el
                    # ★ 脱困进行中**不按 W**：状态机手里正按着 Shift/S，
                    #   这里再叠 Shift+W 会让脱困动作彻底失效（非阻塞化
                    #   之后主循环每帧都会走到这，旧版不会）。
                    if _esc_busy:
                        pass
                    elif _stand_active:
                        # ★ 大角度站定转：**彻底松 W + 松 Shift**。
                        #   用 sprint_off 而不是只放 W，是因为站定是**连续**
                        #   若干帧而不是 3:1 断续，按住 Shift 只会让游戏一直
                        #   保持「冲刺待命」状态；一次 sprint_on 的组合键开销
                        #   （SPRINT_LEAD_S 20ms + key_combo）只在进/出站定
                        #   各付一次，可以忽略。
                        sprint_off()
                    else:
                        sprint_on()

            _t3 = time.monotonic()
            t_acc["decide"] += _t3 - _t2
            if A["live"] and dt_avg:
                # ★ 任务 B：按实测帧间隔放大跳变阈值。写死 50 在低帧率下
                #   会被顶到天花板（1.6fps 时跑步每帧已 17.7px）。
                _lim = _dynamic_max_step_px(svc.tracker.px_to_world, dt_avg)
                if abs(svc.tracker.max_step_px - _lim) > 0.5:
                    svc.tracker.max_step_px = _lim
            _t4 = time.monotonic()
            t_acc["send"] += _t4 - _t3

            # ★★ 寻路状态写盘（2026-10-04）：overlay 悬浮卡片读它。
            #   放在**决策之后、日志之前**：此时 d_deg / dist / _esc_busy
            #   全部就绪，而 _t4 已经打完 —— 写盘耗时**不会**污染
            #   t_acc["send"] 的计时（写盘只有几十字节，代价可忽略，
            #   但按本文件「打点必须在 print 之后」的规矩，别混进 IO 段）。
            if pos is not None:
                if panic.fired:
                    _nav_state = "panic"
                elif result == "arrive":
                    _nav_state = "arrived"
                elif _esc_busy or escape_level > 0:
                    _nav_state = "escaping"
                else:
                    _nav_state = "navigating" if A["live"] else "dry_run"
                _write_nav_state(
                    _nav_state,
                    # ★ --target-tp 有名字；--target-world / --walk-forward
                    #   没有，overlay 会退回显示坐标（layout._fmt_target）。
                    A.get("target_tp_name") or A.get("target_name"),
                    (twx, twz), dist, d_deg,
                    # 有路点时浮窗第 3 行显示「前往路点 2/3」；
                    # 目标行仍然是**最终目标** —— 两者各说一件事。
                    wp_label=wp_cursor.index)

            td = "---" if theta_t is None else f"{math.degrees(theta_t):6.1f}"
            tm = "  --- " if theta_m is None else f"{math.degrees(theta_m):6.1f}"
            dd = "  --- " if d_deg is None else f"{d_deg:+6.1f}"
            tp = "  --- " if turn_px is None else f"{turn_px:+7.1f}"
            # ★ 任务 C：补上 **log** 这一段（2026-10-04）
            #   旧代码在 ``_t4`` 之后一直到 ``_tw`` 之间**没有任何计时点**，
            #   中间夹着两次 print（主行 + [MAIN]）。3.8fps 下每帧都要打，
            #   字符串格式化 + 控制台 IO 在慢终端上能吃掉上百毫秒 ——
            #   上一轮「分段加总 221ms 但帧间隔 438ms」的 217ms 缺口就在这。
            #   ⚠️ 打点必须在 print **之后**才能量到 IO 本身；打在之前
            #   量的是「空隙」，会把 IO 又漏出去。
            sq = "  --- " if len(samples) < SAMPLE_N else \
                f"{abs(samples[-1][1]-samples[0][1])+abs(samples[-1][2]-samples[0][2]):5.1f}"
            print(f"t={el:5.1f}s  dist={dist:7.1f}  θ_t={td}°  θ_m={tm}°  "
                  f"Δ={dd}°  turn={tp}px  |win|={win_mag:5.2f}  "
                  f"队列={sq}  esc=L{escape_level}  pos={_fmt2(pos)}")
            _tlog = time.monotonic()
            t_acc["log"] += _tlog - _t4
            # ★ 任务 C：分段计时收口。「每帧 625ms」的 400ms 到底在哪，
            #   就看这张表 —— 上一轮靠猜（以为只有拆分的 140ms），错了。
            _tw = time.monotonic()
            _wk = max(0.0, 0.2 - (time.monotonic() - now))
            panic.stop.wait(_wk)
            _tw2 = time.monotonic()
            t_acc["wait"] += _tw2 - _tw
            for _k, _v in (("grab", _t1 - _mt), ("phase", _t2 - _t1),
                           ("decide", _t3 - _t2), ("send", _t4 - _t3),
                           ("log", _tlog - _t4), ("wait", _tw2 - _tw)):
                if _v > t_max[_k]:
                    t_max[_k] = _v
            print(f"[MAIN] wait ret fired={panic.fired} "
                  f"stop={panic.stop.is_set()} "
                  f"grab={_t1-_mt:.0f} phase={_t2-_t1:.0f} "
                  f"decide={_t3-_t2:.0f} send={_t4-_t3:.0f} "
                  f"wait={_tw2-_tw:.0f}ms", flush=True)
    except KeyboardInterrupt:
        print("\n  ⚠ Ctrl+C，提前结束。")
        result = "interrupt"
    finally:
        panic.shutdown()
        # ★★★ 状态机非阻塞化后，退出时**可能正按着** Shift/S。
        #   旧版是阻塞的，do_escape 返回时键一定已经松开；现在不保证。
        #   不在这里松开 = 脚本退出后角色一直往后退 —— 真机灾难。
        if esc_state is not None and esc_state.hold is not None:
            try:
                sim.key_up(esc_state.hold)
            except Exception:                    # noqa: BLE001
                pass
            esc_state = None
        if A["live"]:
            try:
                sprint_off()
            except Exception:  # noqa: BLE001
                pass
        if cap is not None:
            cap.close()

    # ★★ 最终状态写盘（2026-10-04 真机打脸后补）
    #   真机跑出 arrive，overlay 里显示的却是「● 寻路中」——
    #   因为 ``break`` 之后主循环不再有下一帧，``_write_nav_state``
    #   也就再没被调用，文件里停在上**一帧**的 "navigating"。
    #   主循环每帧写的那个只反映「写入那一刻」，不代表「跑完时的结果」。
    #
    #   ★ 为什么放在汇总**之前**而不是 finally 里
    #     finally 跑在所有退出路径上（含异常），但那时 twx/twz/dist
    #     可能还没定义（构造阶段就抛的话）。这里已经是「循环正常结束
    #     并进入汇总」的位置，三个量都必然有值。
    #   ★ 优先级：panic > arrived > escaping > navigating/dry_run
    #     panic 必须压过 arrived —— 用户按了 F12 就意味着「不是成功到达」。
    _final_state = "navigating" if A["live"] else "dry_run"
    if panic.fired:
        _final_state = "panic"
    elif result == "stopped":
        # ★ 浮窗点的停止。也算终态，但**不是** arrived/panic，
        #   所以 TTL 走活跃态那档（5s）—— 「已停止」不需要挂 5 分钟。
        _final_state = "stopped"
    elif result == "arrive":
        _final_state = "arrived"
    elif escape_level > 0:
        _final_state = "escaping"
    try:
        _write_nav_state(
            _final_state,
            A.get("target_tp_name") or A.get("target_name"),
            (twx, twz),
            locals().get("dist", 0.0),
            locals().get("d_deg"),
            # ★ force=True：这一笔是**一次性落笔**，只发生一次，
            #   「连续 3 帧才切换」在这里根本不存在。不绕过的话
            #   state 已经是 arrived、phase 却还停在 approach，
            #   同一份文件里第 1 行和第 3 行自相矛盾。
            force=True)
        print(f"  寻路状态    : 已写 {NAV_STATE_FILE.name} "
              f"（state={_final_state}）")
    except Exception as exc:                             # noqa: BLE001
        print(f"  寻路状态    : 写失败（不影响结果）：{exc}")

    # ---- 汇总 --------------------------------------------------------
    hr("3 · 汇总")
    endpos = svc.get_position()
    print(f"  帧数        : {n}（{A['seconds']:g}s，"
          f"实测 {n/max(A['seconds'],1e-9):.1f} fps）")
    # ★ 任务 C：分段计时。「每帧 625ms」的 400ms 在哪，看这张表。
    if n:
        _avg = "  ".join(f"{k}={v / n * 1000:.0f}" for k, v in t_acc.items())
        _pk = "  ".join(f"{k}={v * 1000:.0f}" for k, v in t_max.items())
        print(f"  分段耗时    : {_avg} ms/帧   峰值 {_pk}")
        if dt_avg:
            _slow = sum(1 for x in _dt if x > 0.5) / len(_dt)
            print(f"  帧间隔      : 均值 {dt_avg*1000:.0f}ms（目标 200ms）"
                  f"  慢于 500ms 的帧占比 {_slow:.0%}")
        if _esc_block or _slip_block:
            _blk = _esc_block + _slip_block
            print(f"  ★ 阻塞来源  : 脱困 {_esc_block*1000:.0f}ms"
                  f" + 打滑 {_slip_block*1000:.0f}ms = {_blk*1000:.0f}ms"
                  f"（占总时长 {_blk/max(1e-9, A['seconds']):.0%}）"
                  f"  ← 这就是掉帧的大头")
    if A["live"]:
        print(f"  跳变阈值    : 动态，当前 {svc.tracker.max_step_px:.0f}px"
              f"（按帧间隔算，下限 {STEP_LIMIT_MIN_PX:g}）")
    print(f"  模式        : "
          f"{'LIVE（真按了键）' if A['live'] else 'dry-run（没按任何键）'}")
    print(f"  结果        : {result}")
    if A["live"]:
        print(f"  前台焦点    : 翻转 {diag.focus_flips} 次，"
              f"失焦 {diag.focus_lost_frames}/{n} 帧"
              f"{'  ★ 大量失焦 = 按键发给了别的窗口' if diag.focus_lost_frames else ''}")
    print(f"  截图通道    : 起始 {diag._ch}，dxcam {diag.dxcam_frames} 帧 / "
          f"降级后 {diag.degraded_frames} 帧，"
          f"dxcam 连续空帧峰值 {diag.empty_streak_max}"
          f"（阈值 {8}）")
    for _t, _a, _b in diag.ch_events:
        print(f"                t={_t:.1f}s  {_a} -> {_b}（无回退路径）")
    print(f"  转向次数    : {turns}")
    # ★ 实测「1 像素 = 多少度」，用来标定 MOUSE_DEG_PER_PX。
    #   从 turn_eff 反算：Δθ_m / 本帧总px = got/tot（符号取负，见测量处注释）
    if turn_eff:
        _gs = sorted(-t[2] / t[1] for t in turn_eff if t[1] > 0)
        _g_med = _gs[len(_gs) // 2]
        print(f"  [TURN] 实测 {len(_gs)} 样本：1px ≈ {_g_med:+.3f}° "
              f"(中位)  范围 {_gs[0]:+.3f}~{_gs[-1]:+.3f}°")
        # ★★ 任务 D：样本量不足时**必须**明说中位数不可信
        #   真机两轮的中位分别是 -0.047 和 -0.128 °/px，差 2.7 倍 ——
        #   而有效样本只有个位数/十几条。这个波动是**样本不足**，
        #   不是标定值在变。拿它去回填 MOUSE_DEG_PER_PX 会把代码
        #   标歪两次。故：< TURN_CAL_MIN_N 时只作参考，不建议回填。
        if len(_gs) < TURN_CAL_MIN_N:
            print(f"  [TURN] ★ 样本 N={len(_gs)} < {TURN_CAL_MIN_N}，"
                  f"中位数不可信，标定只作参考（波动主要来自样本不足）")
        print(f"  [TURN] 假定值 {MOUSE_DEG_PER_PX:g}°/px"
              f"{'  ★ 差 3 倍以上，建议回填后再跑' if abs(_g_med) > 0 and abs(MOUSE_DEG_PER_PX / _g_med - 1) > 3 else ''}")
    else:
        print(f"  [TURN] 无有效样本（全部低于噪声地板 {TURN_EFF_NOISE_DEG:g}°）")
    # ★ 任务 B：饱和检测。**分桶自变量是「单段最大像素」而不是本帧总量** ——
    #   饱和发生在**每个输入事件**上，拆成 8×80 的 640px 和一发 640px
    #   总量一样，收益却完全不同。按总量分桶会把两者混在一起，看不出膝盖。
    if turn_eff:
        print(f"  [TURN-EFF] 有效样本 {len(turn_eff)}，"
              f"噪声剔除 {eff_noise}（实测降幅 < {TURN_EFF_NOISE_DEG:g}°）")
        print(f"    {'单段px':>10}  {'n':>4}  {'本帧总px':>9}  {'实际降幅':>9}  "
              f"{'计划降幅':>9}  {'等效px':>8}  {'达成率':>7}")
        _edges = (0, 20, 40, 60, 80, 120, 200, 400, 10 ** 9)
        _sat = None
        for _lo, _hi in zip(_edges, _edges[1:]):
            _b = [t for t in turn_eff if _lo <= t[0] < _hi]
            if not _b:
                continue
            _seg = sorted(t[0] for t in _b)[len(_b) // 2]
            _tot = sorted(t[1] for t in _b)[len(_b) // 2]
            _got = sorted(t[2] for t in _b)[len(_b) // 2]
            _want = sorted(t[3] for t in _b)[len(_b) // 2]
            _eq = _got / MOUSE_DEG_PER_PX
            _rate = (_got / _want) if _want > 1e-9 else float("nan")
            print(f"    {_lo:>4}~{_hi if _hi < 10**8 else 999:>4}  "
                  f"{len(_b):>4}  {_tot:>8.0f}  {_got:>8.1f}°  "
                  f"{_want:>8.1f}°  {_eq:>8.0f}  {_rate:>6.0%}")
            if _sat is None and _rate < 0.80 and _lo >= 20:
                _sat = _lo
        if _sat is None:
            print(f"    饱和阈值：未检出（达成率全程 ≥80%）")
        else:
            print(f"    ★ 饱和阈值 ≈ {_sat}px —— 当前 MOUSE_SATURATION_PX="
                  f"{MOUSE_SATURATION_PX}，"
                  f"{'可再调大' if _sat > MOUSE_SATURATION_PX else '应调小'}")
    else:
        print(f"  [TURN-EFF] 无有效样本（噪声剔除 {eff_noise} 条）")
    print(f"  转向规律    : 比例 {TURN_FRACTION:.2f}x|Δ|，"
          f"限幅 ±{TURN_MAX_DEG:.0f}°/±{TURN_MAX_DEG_BIG:.0f}°，"
          f"死区 {TURN_DEADZONE_DEG:.0f}°，"
          f"{MOUSE_DEG_PER_PX:g}°/px")
    print(f"  打滑恢复    : {slip_recoveries} 次"
          f"（|Δ|>{SLIP_MIN_ERR_DEG:g}° 连续 {SLIP_FRAMES} 帧且 >{SLIP_SECONDS:g}s）")
    # ★ 大角度站定转的账（2026-10-08 重写）
    #   报**实测**：段数/帧数/实走距离/实际下发角度，不报由常量硬算的设计值。
    if _stand_cycles or _stand_force:
        _avg = _stand_frames / max(1, _stand_cycles)
        _rate = _stand_deg_sent / max(1, _stand_frames)
        _nominal = MOUSE_SATURATION_PX * MOUSE_DEG_PER_PX
        print(f"  大角度站定  : {_stand_cycles} 段共 {_stand_frames} 帧"
              f"（平均 {_avg:.1f} 帧/段，占全程 "
              f"{100.0 * _stand_frames / max(1, mv_frames):.0f}%）")
        print(f"                单帧实转 {_rate:.1f}°"
              f"（常态预算 {_nominal:.1f}°/帧，站定预算 "
              f"{MOUSE_SATURATION_PX * TURN_STAND_SEGMENTS * MOUSE_DEG_PER_PX:.1f}°/帧）"
              f"，共发 {_stand_seg_sent} 段 / {_stand_deg_sent:.0f}°")
        print(f"                站定期间角色实走 {_stand_nofoot:.2f}m"
              f"（{_stand_nofoot / max(1, _stand_frames):.2f}m/帧）；"
              f"安全阀强制走 {_stand_force} 次")
        if _stand_hist:
            print("                段长直方图: "
                  + "  ".join(f"{k}帧×{v}" for k, v in sorted(_stand_hist.items()))
                  + f"   共 {sum(_stand_hist.values())} 段")
        # ★★ 站定拆段的达成率（任务 A/C）：站定帧**单独**统计，不与常态混算。
        if _stand_eff:
            _s_px_tot = sum(p for p, _ in _stand_eff)
            _s_got_tot = sum(g for _, g in _stand_eff)
            _s_plan_tot = _s_px_tot * MOUSE_DEG_PER_PX
            print(f"  [TURN-SPLIT] 站定帧达成率: {len(_stand_eff)} 个样本")
            print(f"                下发 {_s_px_tot:.0f}px = 计划 "
                  f"{_s_plan_tot:.1f}°，实际只降 {_s_got_tot:.1f}°")
            print(f"                达成率 {_s_got_tot / max(1e-9, _s_plan_tot) * 100:.0f}%"
                  f"，每 px 实际 {_s_got_tot / max(1.0, _s_px_tot) * 100:.2f}°"
                  f"（常态标称 {MOUSE_DEG_PER_PX:g}°/px）")
            print(f"                ★ 判读：逐帧 [STAND-SEG] 看实发段数 —— "
                  f"只有 1 段 = **派发端**问题；"
                  f"满 {TURN_STAND_SEGMENTS} 段而达成率仍低 = **游戏**只认 1 段")
        else:
            print(f"  [TURN-SPLIT] 站定帧无有效达成率样本"
                  f"（降幅都低于噪声地板 {TURN_EFF_NOISE_DEG:g}°）")
    else:
        print(f"  大角度站定  : 未触发"
              f"（|Δ| 全程未超过 {TURN_STAND_DEG:g}°，或本帧拿不到 Δ）")
    print(f"  卡住脱困    : L1 {escape_counts[0]} / L2 {escape_counts[1]} / "
          f"L3 {escape_counts[2]}"
          f"（每 {SAMPLE_MS:g}ms 采样 × {SAMPLE_N}，阈值 {STUCK_DIST:g}）")
    print(f"  攀爬/卡墙   : {climb_escapes} 次（连续 {CLIMB_FRAMES} 帧归零）")
    if straight is not None and endpos is not None:
        print(f"  直线距离    : {straight:.1f}")
        print(f"  实际路径    : {path_len:.1f}"
              f"   绕路系数 {path_len/max(straight,1e-9):.2f}")
    # ★★ 任务 A · 移动诊断：把「1.58 m/s」拆开看
    #   真机 95.1m/60s = 1.58 m/s，比走路（~2）还慢。阻塞只占 8%，
    #   解释不了 3 倍差距 —— 所以要看**W 到底按了多久**、
    #   以及**按着的时候到底走没走**。
    if mv_frames:
        _w_ratio = mv_w_frames / mv_frames
        _dtm = dt_avg or (n / max(A["seconds"], 1e-9))
        _spd = (mv_dmag / mv_frames) / max(_dtm, 1e-9)
        print(f"  ★ 移动诊断  : W 按住 {mv_w_frames}/{mv_frames} 帧"
              f"（{_w_ratio:.0%}）"
              f"   其中脱困中 {mv_esc_frames} 帧")
        print(f"                  逐帧均位移 {mv_dmag/mv_frames:.3f} m"
              f"   帧间隔 {_dtm*1000:.0f}ms"
              f"   **等效速度 {_spd:.2f} m/s**")
        _stall_r = mv_stall_frames / max(1, mv_w_frames)
        print(f"                  按着 W 但位移<{MOVE_STALL_EPS:g}m 的帧 "
              f"{mv_stall_frames}（占按住帧 {_stall_r:.0%}）")
        # ★★ 任务 A：Shift 账本（2026-10-04 真机打脸后新增）
        _sh_ratio = shift_held_frames / mv_frames
        print(f"                : Shift 按住 {shift_held_frames}/{mv_frames} 帧"
              f"（{_sh_ratio:.0%}）"
              f"   sprint_on 实执行 {sprint_calls} 次 / off {sprint_off_calls} 次")
        if _sh_ratio < 0.8:
            print(f"    ★★ Shift 有 {1-_sh_ratio:.0%} 的时间没按住 —— "
                  f"**这就是走路速度的直接原因**")
        elif _spd < 3.0:
            print(f"    ★★ Shift 全程按住（{_sh_ratio:.0%}）却只有 {_spd:.2f} m/s")
            print(f"       -> Shift 账本没问题，**问题在游戏是否接受这次输入**。")
            print(f"       -> 对照 diag_sprint.py：A 策略单跑是冲刺。")
            print(f"       -> 差异只可能来自 e2e 的**上下文**（连续 mouse_move /")
            print(f"          反复 combo / 长时按住），不是注入方式本身。")
        if _w_ratio < 0.8:
            print(f"    ★ W 有 {_w_ratio:.0%} 的时间没按住 —— "
                  f"**这就是速度不足的主因**")
        if _stall_r > 0.3:
            print(f"    ★ 按住 W 却 {_stall_r:.0%} 的帧几乎不动 —— "
                  f"查按键是否真的进了游戏（UIPI/焦点）")
    print(f"  终点        : {_fmt2(endpos)}")
    if not A["live"]:
        print()
        print("  ★ dry-run 没按任何键，角色不会动 —— 打滑/卡住/脱困")
        print("    这三项**无法验证**。它们需要 --live。")
    hr()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(1)
