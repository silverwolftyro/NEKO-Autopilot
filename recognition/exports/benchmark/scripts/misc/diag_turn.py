"""鼠标转向符号诊断（§47）—— 确定 ``mouse_move(+x)`` 让角色往哪转。

★ 为什么必须实测
----------------
``e2e_pathfind.py`` 里的转向律是 ``turn = k * Δ``，其中
``Δ = θ_target - θ_move``。**这个正负号是猜的**：把鼠标往右推，
角色是右转（视角顺时针）还是左转（逆时针），取决于游戏怎么映射鼠标
增量，**读代码看不出来**。

上一轮的现象正是符号反了：θ_move 稳定在 ~180°、Δ 长期 150°+、
最后卡在墙角 —— 典型的「往反方向修正，越修越偏」。

★ 怎么用
--------
1. 找一块**开阔地**，面朝一个方向（比如面朝北）
2. 跑：
   ``python diag_turn.py --channel mss --delay 5 --seconds 12``
3. 倒计时时切回游戏，保持前台
4. 脚本会：按 W 走 → 记方向 → 鼠标 +50px → 再走 → 记方向 → 报告差值

★ 怎么看结果
------------
```
Δθ = +12.3°  ->  鼠标 +x  角色往**逆时针**转（视角左）  -> turn 要取负
Δθ = -12.3°  ->  鼠标 +x  角色往**顺时针**转（视角右）  -> turn 保持正
```

⚠️ 站到开阔地！撞墙/卡住会让第二次测不出方向（位移窗口不够）。
⚠️ 不 import ultralytics（AGPL-3.0）。
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

import math
import sys
import time
import traceback
from pathlib import Path

PLUGIN_ROOT = Path(
    PLUGIN_DIR)
sys.path.insert(0, str(PLUGIN_ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

DEFAULT_TITLE = "原神"
DEFAULT_DELAY = 5.0
DEFAULT_SECONDS = 12.0
#: 与 e2e_pathfind 共用同一套窗口/阈值，**必须一致** ——
#: 否则这里测出的方向和寻路时用的方向不是一回事。
WINDOW_N = 10
WINDOW_MOVE_MIN = 0.6
#: 诊断用的转向量。够大到能测出角度变化，又不至于转过头。
PROBE_PX = 50
#: 每一段走多久。至少要够窗口攒满 + 几个帧。
LEG_S = 2.0
SEP = "=" * 78


def hr(t: str = "") -> None:
    print(f"\n{SEP}\n  {t}\n{SEP}" if t else SEP)


def _pair(s: str) -> tuple[float, float]:
    return float(s.split(",")[0]), float(s.split(",")[1])


def _parse_args():
    a = {"title": DEFAULT_TITLE, "channel": None, "delay": DEFAULT_DELAY,
         "seconds": DEFAULT_SECONDS, "sprint": False, "live": True,
         "start": None}
    args, i = sys.argv[1:], 0
    while i < len(args):
        k = args[i]
        v = args[i + 1] if i + 1 < len(args) else None
        try:
            if k == "--channel" and v:
                a["channel"] = v; i += 2
            elif k == "--delay" and v:
                a["delay"] = max(0.0, float(v)); i += 2
            elif k == "--seconds" and v:
                a["seconds"] = float(v); i += 2
            elif k == "--title" and v:
                a["title"] = v; i += 2
            elif k == "--start-world" and v:
                a["start"] = _pair(v); i += 2
            elif k == "--walk":
                a["sprint"] = False; i += 1
            elif k == "--sprint":
                a["sprint"] = True; i += 1
            elif k == "--dry-run":
                a["live"] = False; i += 1
            elif not k.startswith("-"):
                a["title"] = k; i += 1
            else:
                print(f"未知参数: {k}", file=sys.stderr); i += 1
        except (ValueError, IndexError, TypeError):
            print(f"✗ {k} 的取值不合法: {v!r}", file=sys.stderr)
            raise SystemExit(2)
    return a


def main() -> int:
    A = _parse_args()

    from autopilot.perception.builder import StateBuilder
    from autopilot.perception.capturer import Capturer
    from autopilot.perception.localization_service import (
        LocalizationService, LocalizationStateMachine,
    )
    from autopilot.actuator.simulator import (
        DryRunBackend, InputSimulator, VK, Win32Backend,
    )

    hr("0 · 参数")
    print(f"  移动方式 : {'Shift+W 跑步' if A['sprint'] else 'W 走路'}")
    print(f"  探测转向 : mouse_move({PROBE_PX:+d}, 0)")
    print(f"  每段时长 : {LEG_S:g}s   总时长 {A['seconds']:g}s")
    print(f"  模式     : {'LIVE（真按键）' if A['live'] else 'dry-run（不按键）'}")
    if A["live"]:
        print("  ⚠ 干跑测不出转向符号 —— 角色不会动，方向无从变化。")

    svc = LocalizationService()
    builder = StateBuilder(entity_detector=None)
    sim = InputSimulator(backend=Win32Backend() if A["live"] else DryRunBackend())

    # 起手式：需要一个已知的、正在 TRACKING 的起点。
    # ⚠️ ``on_frame()`` 在 UNINITIALIZED 下会**提前返回，根本不做相位相关**，
    #    位置永远不更新。公开途径只有走完整个传送契约，对诊断太重。
    if A["start"] is not None:
        from autopilot.perception.localization_service import (
            LocalizationStateMachine,
        )
        from autopilot.perception.tile_tracker import TileTracker

        svc.tracker = TileTracker(A["start"])
        svc._state = LocalizationStateMachine.TRACKING
        print(f"  已指定起点 : ({A['start'][0]:.1f}, {A['start'][1]:.1f})")
    elif svc.get_position() is None:
        print("  ✗ 定位服务没有位置（UNINITIALIZED）。两种办法：")
        print("    1) 先跑一次 e2e_teleport.py（传送后位置即起点，推荐）")
        print("    2) 给 --start-world X,Z 手动指定起点")
        return 2
    else:
        _p = svc.get_position()
        print(f"  用当前定位 : ({_p[0]:.1f}, {_p[1]:.1f})")

    try:
        cap = Capturer(A["title"], force_channel=A["channel"])
        cap.open()
    except Exception as exc:  # noqa: BLE001
        print(f"\n  ✗ 找不到窗口 {A['title']!r}：{type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2
    print(f"  截图通道 : {cap.get_active_channel().value}")

    if A["delay"] > 0:
        hr("0.5 · 延迟启动")
        end = time.monotonic() + A["delay"]
        print(f"\n  [DELAY] {A['delay']:g}s 后开始 —— ★ 切回游戏，"
          f"站在**开阔地**，面朝任意方向！")
        while time.monotonic() < end:
            print(f"    剩余 {end - time.monotonic():5.1f} s\r",
                  end="", flush=True)
            time.sleep(0.1)
        print()

    # ---- 三段式 -----------------------------------------------------
    hr("1 · 测量")
    pos0 = svc.get_position()
    if pos0 is None:
        print("  ✗ 定位服务没有位置（UNINITIALIZED）。")
        print("    先跑 e2e_teleport.py 做一次传送，或改用 --start-world。")
        return 2
    print(f"  起点 : ({pos0[0]:.1f}, {pos0[1]:.1f})")

    legs: list[tuple[str, list]] = []
    keys_down: list[int] = []
    try:
        for leg_name in ("A · 直线走（基准）", "B · 鼠标转后走"):
            if leg_name.startswith("B"):
                print(f"\n  ▸ 发 mouse_move({PROBE_PX:+d}, 0) 一次")
                if A["live"]:
                    sim.mouse_move_relative(PROBE_PX, 0)
                time.sleep(0.6)          # 让视角稳定
            print(f"  ▸ {leg_name} —— 开始")
            if A["live"]:
                if A["sprint"]:
                    sim.key_down(VK.SHIFT)
                    keys_down.append(int(VK.SHIFT))
                sim.key_down(VK.W)
                keys_down.append(int(VK.W))

            samples: list = []
            hist: list = []
            t_leg = time.monotonic()
            while time.monotonic() - t_leg < LEG_S:
                now = time.monotonic()
                frame = cap.grab()
                svc.on_frame(frame.image, now)
                for ev in builder.drain_events():
                    print(f"    ⚠ {ev.code}: {ev.message}")
                p = svc.get_position()
                if p is not None:
                    hist.append((now, p[0], p[1]))
                    if len(hist) > WINDOW_N + 1:
                        hist.pop(0)
                    if len(hist) == WINDOW_N + 1:
                        w = (p[0] - hist[0][1], p[1] - hist[0][2])
                        wm = math.hypot(*w)
                        if wm >= WINDOW_MOVE_MIN:
                            samples.append(
                                (math.degrees(math.atan2(w[1], w[0])), wm))
                time.sleep(0.2)
            # 抬起
            if A["live"]:
                sim.key_up(VK.W)
                if A["sprint"]:
                    sim.key_up(VK.SHIFT)
            print(f"  ▸ {leg_name} —— 结束，"
                  f"{len(samples)} 个窗口样本")
            legs.append((leg_name, samples))
    except KeyboardInterrupt:
        print("\n  ⚠ Ctrl+C")
    finally:
        cap.close()

    # ---- 报告 -------------------------------------------------------
    hr("2 · 报告")
    if len(legs) < 2:
        print("  ✗ 没跑完两段，无法比较。")
        return 1

    def summarize(samples):
        if not samples:
            return None, 0.0
        angs = [a for a, _ in samples]
        # 圆周均值：直接取算术平均会在 ±180° 附近被拉歪
        sx = sum(math.cos(math.radians(a)) for a in angs)
        sy = sum(math.sin(math.radians(a)) for a in angs)
        mean = math.degrees(math.atan2(sy, sx))
        avg_w = sum(m for _, m in samples) / len(samples)
        return mean, avg_w

    a_mean, a_w = summarize(legs[0][1])
    b_mean, b_w = summarize(legs[1][1])

    for (name, samples), mean, w in zip(legs, (a_mean, b_mean), (a_w, b_w)):
        if mean is None:
            print(f"  {name:18} : ✗ 没采到足够位移（站住了？）")
        else:
            print(f"  {name:18} : θ_move = {mean:+7.2f}°   "
                  f"|win| = {w:5.2f}   （{len(samples)} 样本）")

    if a_mean is None or b_mean is None:
        print("\n  ✗ 至少一段没测到位移。换开阔地重试。")
        return 1

    d = b_mean - a_mean
    d = (d + 180.0) % 360.0 - 180.0
    print()
    print(f"  ▸ mouse_move({PROBE_PX:+d}, 0) 造成 θ_move 变化 Δθ = {d:+.2f}°")
    print()
    if abs(d) < 3.0:
        print("  ✗ 变化太小，测不出来。可能：")
        print("    · 鼠标灵敏度太低 -> 加大 PROBE_PX")
        print("    · 第二次没走起来 -> 换开阔地")
        return 1
    if d > 0:
        print("  ★ 结论：mouse_move(+x) 让角色往**逆时针**转（视角左转）")
        print("    -> e2e_pathfind 的 turn 取**负号**：turn = -k * Δ")
    else:
        print("  ★ 结论：mouse_move(+x) 让角色往**顺时针**转（视角右转）")
        print("    -> e2e_pathfind 的 turn 保持**正号**：turn = +k * Δ")

    if a_w and b_w:
        ratio = b_w / a_w if a_w else 0
        print()
        print(f"  · 两段 |win| 之比 = {ratio:.2f}"
              f"（{'跑步生效' if ratio > 1.3 else '两次速度接近，Shift 可能没生效'}）")

    hr()
    print("  ✓ 诊断跑完。把上面的★结论告诉 Agent 即可。")
    print(SEP)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(1)
