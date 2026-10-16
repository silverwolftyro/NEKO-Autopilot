"""感知层端到端 smoke 测试（真机）。

⚠️ **只读，不动游戏状态**：不按键、不动鼠标、不发送任何输入。
   只做「截图 -> builder.build() -> 打印 StatePacket 摘要」。

跑法::

    python smoke_end_to_end.py                      # 默认窗口标题「原神」
    python smoke_end_to_end.py "Genshin Impact"    # 指定标题子串
    python smoke_end_to_end.py --frames 5           # 少抓几帧
    python smoke_end_to_end.py --channel printwindow # 强制 PrintWindow 通道
    python smoke_end_to_end.py --delay 5             # 构造完后倒数 5 秒再抓
    python smoke_end_to_end.py --entity auto         # 用 auto 探测（默认 force_on）

★ 为什么默认 --entity force_on（§37）
------------------------------------
``StateBuilder`` 的默认 ``entity_detector="auto"`` 判据是「实测 p50 < 80 ms」，
而 DirectML 探测值在这台机器上实测漂移 **53~116 ms** —— 正好骑在阈值上。
后果不是"慢一点"，而是**同一台机器、不同负载给出相反结论**：
跑一次检出实体、跑一次检不出，而你无法区分是系统坏了还是这次没探测到。

smoke 是用来建立信任的工具，**结果必须可复现**，所以默认 ``force_on``
（跳过探测直接启用）。想观察 auto 的真实行为就显式加 ``--entity auto``，
输出会额外警告探测值可能在阈值两侧翻转。

可选值：``force_on``（默认）/ ``force_off``（或 ``off``）/ ``auto``。

★ 为什么会有 --delay
-------------------
游戏是**全屏**时，切到 PowerShell 会让游戏切到后台，mss（屏幕坐标抓取）
就抓到桌面了。``--delay N`` 让脚本在**构造完之后**再倒数 N 秒 ——
这段时间你把游戏切回前台，然后别再碰这个窗口。

⚠️ 倒计时**故意放在 StateBuilder / Capturer 构造之后**：构造本身要
1.5 秒（加载 bgi_world 模型 + DirectML 会话）。你是在命令敲下去之后
才开始切游戏的，所以等构造完再倒数，给的时间才作数；放在构造前倒，
那 1.5 秒会被模型加载吃掉。

★ 为什么会有 --channel
---------------------
默认降级链是 ``[dxcam, mss, printwindow]``。**mss 是屏幕坐标抓取** ——
游戏失焦/被遮挡时，它抓到的是遮挡窗口的画面（PowerShell、浏览器…），
而且**不会报错**，所以永远降不到 printwindow。

实测踩过：脚本在 PowerShell 里跑 → 游戏失焦 → dxcam 空帧 →
降级到 mss → 抓到 PowerShell 窗口 → SceneDetector 全判 UNKNOWN。

``--channel printwindow`` 强制走**窗口级**抓取，游戏被遮挡也能拿到内容。
详见 ``perception/capturer.py`` 的 ``force_channel``。

⚠️ **PrintWindow 抓不到最小化窗口**。最小化时 ``GetWindowRect`` 返回
哨兵矩形（约 158×26），抓回来的就是一条边而不是画面内容。跑之前
先确认游戏是**正常显示**的，不是最小化到托盘。

★ 诊断帧落盘（§32）
------------------
``recognition/exports/benchmark/diagnosis/smoke_frame_{1,N}.png``

**只存第一帧和最后一帧**，而且**先存图再 build** —— 因为最需要证据的
恰恰是「``builder.build()`` 直接抛」的情况（画面太小、SceneDetector
判据退化）。先 build 后存图的话，那种场景下恰恰什么都留不下。

★ 为什么绝对路径
----------------
脚本可能被从任意 cwd 调用。所有 import 路径都写死，
不依赖 ``os.getcwd()``。

★ 为什么不吞异常
----------------
任何异常都打印完整 traceback 后退出（exit 1）。
smoke 测试的价值就在于「有问题立刻炸出来」，吞掉就失去意义了。

⚠️ **不 import ultralytics**（AGPL-3.0，运行时链接即传染）。
   推理走 perception/yolo.py 的 ONNX Runtime 路径。
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
from collections import Counter
from pathlib import Path

# ---- 绝对路径（不依赖 cwd）-----------------------------------------------
PLUGIN_ROOT = Path(
    PLUGIN_DIR)
sys.path.insert(0, str(PLUGIN_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 - 老 Python 没这功能，忽略
    pass

# ---- 决策层（纯 Python，无模型加载，可提到模块级）-------------------------
# ⚠️ 引入这两个只为打印 has_nearby_enemy() 的**真实返回值**（§36）。
#    以前这里是硬编码文案「没有 enemy 分组 -> False」，而实现已经改成
#    「enemy_identify **或** health_bar(y<0.5)」—— 文案与实现脱节。
#    policy/context.py 只依赖 stdlib + 两个内部模块，提到模块级不会
#    提前拉起任何模型。
from autopilot.policy.context import (  # noqa: E402
    ENEMY_HEALTH_BAR_Y_MAX,
    PolicyContext,
)

# ---- 参数 ----------------------------------------------------------------
DEFAULT_TITLE = "原神"
DEFAULT_FRAMES = 10
MIN_INTERVAL_S = 0.200          # 帧间隔下限（tick_rate_hz=5 的 200ms）
#: ★ 默认 force_on，不 auto（§37）。auto 的判据是「实测 p50 < 80ms」，
#: 而 DirectML 探测值实测漂移 53~116ms —— 骑在阈值上会让 smoke 的
#: 结果**不可复现**。想看 auto 行为就显式加 ``--entity auto``。
DEFAULT_ENTITY = "force_on"
VALID_ENTITY = ("force_on", "force_off", "auto", "off")

#: 诊断用帧的落盘目录。**只存第一帧和最后一帧**（§32）——
#: 诊断「为什么 scene.id 全是 UNKNOWN」只需要看头尾，全存纯属浪费磁盘。
DIAG_DIR = Path(
    RECOG_ROOT / 'exports' / 'benchmark' / 'diagnosis')

SEP = "=" * 78


def hr(title: str = "") -> None:
    if title:
        print(f"\n{SEP}\n  {title}\n{SEP}")
    else:
        print(SEP)


def _parse_args() -> tuple[str, int, str | None, float, str]:
    args = sys.argv[1:]
    title, frames, channel, delay = DEFAULT_TITLE, DEFAULT_FRAMES, None, 0.0
    entity = DEFAULT_ENTITY
    i = 0
    while i < len(args):
        a = args[i]
        # 「off」是给人用的简写，映射到 force_off
        val = a.split("=", 1)[1] if "=" in a else (
            args[i + 1] if i + 1 < len(args) else None)
        if a == "--frames" and val is not None:
            frames = int(val); i += 1 if "=" in a else 2
        elif a == "--channel" and val is not None:
            channel = val; i += 1 if "=" in a else 2
        elif a == "--delay" and val is not None:
            delay = max(0.0, float(val)); i += 1 if "=" in a else 2
        elif a == "--entity" and val is not None:
            entity = "force_off" if val == "off" else val
            if entity not in VALID_ENTITY:
                print(f"✗ --entity 取值 {val!r} 不认识。可选："
                      f"{' / '.join(VALID_ENTITY)}", file=sys.stderr)
                raise SystemExit(2)
            i += 1 if "=" in a else 2
        elif not a.startswith("-"):
            title = a; i += 1
        else:
            print(f"未知参数: {a}")
            i += 1
    return title, frames, channel, delay, entity


def countdown(seconds: float) -> None:
    """倒数打印，让用户有时间切回游戏。

    ★ 为什么必须在 builder/capturer **构造完之后**再倒数：
      ``StateBuilder(entity_detector="auto")`` 实测要 1.5 秒（加载
      bgi_world 模型 + DirectML 会话）。用户是在命令敲下去**之后**才开始
      切游戏的，所以等构造完再开始倒数，给的时间才作数。
    """
    if seconds <= 0:
        return
    end = time.monotonic() + seconds
    print(f"\n  [DELAY] {seconds:g} 秒后开始抓帧 —— ★ 现在切回游戏，让它在前台！")
    while True:
        left = end - time.monotonic()
        if left <= 0:
            break
        print(f"    剩余 {left:5.1f} s", end="\r", flush=True)
        time.sleep(min(0.5, left))
    print("    剩余   0.0 s —— 开始抓帧")


def list_window_titles(limit: int = 40) -> list[str]:
    """列出可见窗口标题。**只用于错误提示**，不参与截图逻辑。"""
    try:
        import win32gui  # noqa: PLC0415

        out: list[str] = []

        def _cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                t = win32gui.GetWindowText(hwnd)
                if t.strip():
                    out.append(t)
            return True

        win32gui.EnumWindows(_cb, None)
        return out[:limit]
    except Exception:  # noqa: BLE001 - 没有 pywin32 就放弃，不影响主流程
        return []


def save_frame(image, index: int) -> str | None:
    """把抓到的一帧存成 PNG。返回路径；失败返回 None（不打断主流程）。

    ⚠️ 用 ``imencode`` + ``tofile`` 而不是 ``cv2.imwrite`` ——
       ``imwrite`` 在**含非 ASCII 字符的路径**上会静默失败返回 False
       （本项目的目录路径带中文时不踩这个坑，但保持一致）。
    """
    import cv2  # noqa: PLC0415

    try:
        DIAG_DIR.mkdir(parents=True, exist_ok=True)
        p = DIAG_DIR / f"smoke_frame_{index}.png"
        ok, buf = cv2.imencode(".png", image)
        if not ok:
            print(f"    ⚠ 图像编码失败（帧 {index}）")
            return None
        buf.tofile(str(p))
        return str(p)
    except Exception as exc:  # noqa: BLE001 - 存图失败不该中断诊断
        print(f"    ⚠ 存图失败（帧 {index}）：{type(exc).__name__}: {exc}")
        return None


def _enemy_breakdown(ents) -> tuple[int, int]:
    """``(enemy_identify 个数, 敌人血条个数)``。

    ⚠️ 计数逻辑**故意逐条对齐** ``PolicyContext.has_nearby_enemy()``：
       同样的 ``getattr`` 兜底、同样的 bbox 合法性校验、同样的严格小于。
       两边判据一旦漂移，就会打出「计数为 0 但返回 True」这种自相矛盾的
       话 —— 那比不打印更糟，因为它看起来像证据。
    """
    n_enemy = n_hb = 0
    for e in ents or ():
        kind = getattr(e, "kind", None)
        if kind is None:
            continue
        group = getattr(e, "group", kind)
        if group == "enemy":
            n_enemy += 1
        elif group == "combat":
            bb = getattr(e, "bbox", None)
            if not bb or len(bb) < 2:
                continue          # bbox 坏 -> 跳过，和实现一致
            if float(bb[1]) < ENEMY_HEALTH_BAR_Y_MAX:
                n_hb += 1
    return n_enemy, n_hb


def _fmt_has_enemy(result, n_enemy: int, n_hb: int) -> str:
    """``has_nearby_enemy()`` 的真实返回值 + 判据计数，拼成一行。

    ⚠️ UNKNOWN **不并进 False** —— 两者语义不同：False 是「跑了，确实
       没有敌人」，UNKNOWN 是「没跑，判据不可得」。混在一起会让「检测器
       没启动」看起来像「附近没敌人」。
    """
    if result is True:
        verdict = "True"
    elif result is False:
        verdict = "False"
    else:
        return "UNKNOWN  (本帧没跑实体检测，判据不可得 —— 规则会保守不触发)"
    return (f"{verdict}   (enemy_identify: {n_enemy} 个 / "
            f"health_bar y<{ENEMY_HEALTH_BAR_Y_MAX}: {n_hb} 个)")


def main() -> int:
    title, n_frames, channel, delay, entity = _parse_args()

    from autopilot.perception.builder import StateBuilder
    from autopilot.perception.capturer import Capturer, WindowNotFoundError

    hr("0 · 环境")
    import onnxruntime as ort  # noqa: PLC0415

    print(f"  onnxruntime    : {ort.__version__}")
    print(f"  声明的 provider : {ort.get_available_providers()}")
    print(f"  窗口标题子串   : {title!r}")
    print(f"  截图通道       : "
          f"{channel or '默认降级链 dxcam->mss->printwindow'}")
    print(f"  抓帧数         : {n_frames}")
    print(f"  启动延迟       : {delay:g} 秒")
    print(f"  诊断帧落盘     : {DIAG_DIR}  （只存第 1 帧和第 {n_frames} 帧）")
    if channel is None:
        print("  ⚠ 默认链里的 **mss 是屏幕坐标抓取** —— 游戏失焦/被遮挡时会抓到")
        print("    遮挡窗口的画面且**不报错**。看不到正确画面就加 --channel printwindow")
    if delay > 0:
        print("  ⚠ 全屏游戏切到本窗口会切到后台。--delay 就是给你切回游戏用的：")
        print("    构造完成后会倒数，请在那之前把游戏切回前台并**不要**再碰这个窗口")

    # ---- 1 · builder ----------------------------------------------------
    hr("1 · 构造 StateBuilder")
    print(f"  entity_detector  : {entity!r}")
    if entity == "auto":
        print("    auto 会加载 bgi_world 并探速（约 1.2 s）后按实测决定开不开")
        print("    ⚠ 探测值实测漂移 53~116ms，骑在 80ms 阈值上 —— 结果可能翻转")
    else:
        print("    force_on/off **不探测**，结果可复现")
    t0 = time.perf_counter()
    builder = StateBuilder(entity_detector=entity)
    build_init_ms = (time.perf_counter() - t0) * 1000
    print(f"  ✓ 构造完成：{build_init_ms:.0f} ms")
    if build_init_ms > 1000 and entity == "auto":
        print("    （>1 s：这是首次 auto 探测的正常开销，后续构造不会重复付）")
    if builder._entity_detector is None:
        print("  ★ 检测器没起来 —— has_nearby_enemy() 会**恒为 UNKNOWN**，")
        print("    三条战斗规则永远不会触发。")
    print(f"  实体检测器      : "
          f"{type(builder._entity_detector).__name__ if builder._entity_detector else 'None（已关闭）'}")
    print(f"  实体间隔        : 每 {builder._entity_interval} tick")
    print(f"  实体状态说明    : {builder._entity_note}")
    for ev in builder.drain_events():
        print(f"  ⚠ 事件 {ev.code}: {ev.message}  context={ev.context}")

    # ---- 2 · capturer -------------------------------------------------
    hr("2 · 构造 Capturer")
    try:
        cap = Capturer(title, force_channel=channel)
        cap.open()
    except ValueError:
        print(f"\n  ✗ 通道名 {channel!r} 不认识。可选：dxcam / mss / printwindow")
        return 2
    except WindowNotFoundError:
        print(f"\n  ✗ 找不到标题含 {title!r} 的窗口。\n")
        cands = list_window_titles()
        if cands:
            print("    当前可见窗口标题（前 40 个），请用其中一个做子串：")
            for t in cands:
                print(f"      - {t}")
        else:
            print("    拿不到窗口列表（可能缺 pywin32）。")
        print("\n    也确认一下：游戏是不是最小化 / 在另一个桌面 / 开着但没渲染。")
        print(f"    也可以直接指定：python smoke_end_to_end.py \"<标题子串>\"")
        return 2
    except Exception as exc:  # noqa: BLE001 - 明确报错，不要 traceback 糊一脸
        print(f"\n  ✗ Capturer 初始化失败：{type(exc).__name__}: {exc}")
        print("\n    常见原因：dxcam / mss 缺失，或没有可用的截图通道。")
        if channel == "printwindow":
            print("    ★ printwindow 对「硬件加速 + 独占全屏」可能返回黑屏 ——")
            print("      把游戏改成窗口化 / 无边框窗口再试。")
        return 2

    print(f"  ✓ 窗口已定位，截图通道 = {cap._active}")
    if channel and cap.get_active_channel().value != channel:
        print(f"  ⚠ 你强制了 {channel}，实际用的是 {cap.get_active_channel().value}")
    if cap.window_rect:
        _x, _y, _w, _h = cap.window_rect
        if _w <= 0 or _h <= 0:
            print(f"  ⚠ 客户区尺寸 {_w}x{_h} —— 窗口可能**最小化**了。")
            print("    PrintWindow 对最小化窗口只能抓到一条边，抓不到画面。")
            print("    请把游戏恢复成正常显示（不是最小化到托盘）再跑。")

    # ★ 倒计时放在**这里**：builder 和 capturer 都已构造完。
    #   用户是在命令敲下去之后才开始切游戏的，所以等构造完再开始倒数，
    #   给的时间才作数。放在构造前倒，等于白等 —— 那 1.5 秒会被
    #   StateBuilder(auto) 的模型加载吃掉。
    if delay > 0:
        hr("2.5 · 延迟启动")
        countdown(delay)

    # ---- 3 · 抓帧 -----------------------------------------------------
    hr(f"3 · 抓 {n_frames} 帧")
    rows: list[dict] = []
    saved_paths: list[str] = []   # 独立于 rows —— rows 在 build 抛异常时是空的
    save_frames = True            # §32：始终存头尾两帧用于诊断
    try:
        for i in range(n_frames):
            t_frame = time.perf_counter()
            frame = cap.grab()

            # ★★ **必须先存图，再 build**。§31 实测里最关键的那次失败就是
            #   ``builder.build()`` 直接抛（画面 158×26 过小，SceneDetector
            #   的血条 ROI 退化）—— 如果先 build 后存图，恰恰在最需要证据的
            #   场景下什么都留不下。抓帧本身是成功的，丢的是分析结果。
            saved = None
            if save_frames and (i == 0 or i == n_frames - 1):
                saved = save_frame(frame.image, i + 1)
                if saved:
                    saved_paths.append(saved)
                    print(f"  帧 {i + 1:>2}: 已存图 = {saved}")

            state = builder.build(frame)
            dt = (time.perf_counter() - t_frame) * 1000

            pos = state.position
            ents = builder.last_world_entities
            rows.append({
                "i": i + 1,
                "scene": state.scene.id,
                "ok": state.resource.perception_ok,
                "pos": (pos.x, pos.y) if pos else None,
                "quality": pos.quality if pos else None,
                "n_ent": None if ents is None else len(ents),
                "age": builder.last_entity_age_ticks,
                "ms": dt,
                "saved": saved,
                "ents": list(ents) if ents else None,
            })

            r = rows[-1]
            pstr = (f"({r['pos'][0]:.1f}, {r['pos'][1]:.1f})"
                    if r["pos"] else "None")
            estr = ("未跑" if r["n_ent"] is None else
                    f"{r['n_ent']} 个 (age={r['age']})")
            print(f"  帧 {r['i']:>2}:")
            print(f"    scene.id       = {r['scene']}")
            print(f"    perception_ok  = {r['ok']}")
            print(f"    position       = {pstr}  quality={r['quality']}")
            print(f"    world_entities = {estr}")
            print(f"    build_ms       = {r['ms']:.0f}")

            # ★★ **真调** has_nearby_enemy()（§36）。以前这里是硬编码文案，
            #   只扫「有没有 group=enemy」，而实现早已改成
            #   「enemy_identify **或** health_bar(y<0.5)」—— 文案说 False，
            #   函数说 True，诊断就废了。返回值才是判据，计数只是解释
            #   「这一帧是哪个判据起了作用」。
            _n_enemy, _n_hb = _enemy_breakdown(ents)
            _has = PolicyContext.from_state(
                state, now_ms=state.timestamp_ms or None,
                world_entities=ents).has_nearby_enemy()
            r["has_enemy"] = _has
            r["n_enemy"] = _n_enemy
            r["n_hb"] = _n_hb
            # 节流命中时这份实体是**上次的**，不标出来会让人以为是本帧新检出
            _stale = ("" if r["age"] == 0 else
                      f"  [age={r['age']}，节流复用上次检出结果]")
            print(f"      ★ has_nearby_enemy() = "
                  f"{_fmt_has_enemy(_has, _n_enemy, _n_hb)}{_stale}")

            # 检出到实体就**逐个打印明细** —— 下次跑能直接看出
            #   检出的到底是敌人标识还是矿/掉落（§34 只验证过后者）。
            if ents:
                for e in ents:
                    _g = getattr(e, "group", "?")
                    print(f"      · {e.kind:16} conf={e.confidence:.2f} "
                          f"group={_g:8} bbox=({e.bbox[0]:.3f},"
                          f"{e.bbox[1]:.3f},{e.bbox[2]:.3f},{e.bbox[3]:.3f}) "
                          f"screen={e.screen_pos}")

            # 帧间隔下限：build 花的时间要从等待里扣掉
            rest = MIN_INTERVAL_S - (time.perf_counter() - t_frame)
            if rest > 0:
                time.sleep(rest)
    finally:
        cap.close()
        # build() 抛异常时 rows 是空的，但**已存的图不能丢** —— 用独立的
        # saved_paths 兜住，让用户在 traceback 之外也能找到证据。
        if saved_paths:
            print(f"\n  [诊断帧已落盘] {len(saved_paths)} 张：")
            for _p in saved_paths:
                print(f"    - {_p}")

    # ---- 4 · 汇总 ------------------------------------------------------
    hr("4 · 汇总")
    avg = sum(r["ms"] for r in rows) / len(rows)
    print(f"  帧数                  : {len(rows)}")
    print(f"  平均 build 耗时       : {avg:.0f} ms"
          f"  （tick 预算 200 ms 的 {avg / 200 * 100:.0f}%）")
    print(f"  最慢一帧              : {max(r['ms'] for r in rows):.0f} ms")

    scenes = [r["scene"] for r in rows]
    changes = sum(1 for a, b in zip(scenes, scenes[1:]) if a != b)
    print(f"  scene.id 变化次数     : {changes}  -> {scenes}")

    all_ok = all(r["ok"] for r in rows)
    print(f"  perception_ok 全程 True: {all_ok}")

    with_pos = [r for r in rows if r["pos"]]
    print(f"  有位置的帧            : {len(with_pos)}/{len(rows)}")
    if with_pos:
        qs = sorted({r["quality"] for r in with_pos})
        print(f"  出现过的 quality      : {qs}")

    ran = [r for r in rows if r["n_ent"] is not None]
    print(f"  实体检测真正跑的帧    : {len(ran)}/{len(rows)}"
          f"  （间隔 {builder._entity_interval} tick）")
    if ran:
        print(f"  检出实体数            : {[r['n_ent'] for r in ran]}")

    # ★ 跨帧汇总 has_nearby_enemy() 的**真实**返回值（§36）——
    #   按**帧**统计，不按实体累加。
    _vals = [rr.get("has_enemy") for rr in rows]
    _n_true = sum(1 for v in _vals if v is True)
    _n_false = sum(1 for v in _vals if v is False)
    _n_unk = sum(1 for v in _vals if not isinstance(v, bool))
    print(f"  has_nearby_enemy()     : True {_n_true} 帧 / "
          f"False {_n_false} 帧 / UNKNOWN {_n_unk} 帧   （共 {len(rows)} 帧）")

    # ★ 实体种类**只统计新鲜帧**（age==0，即本 tick 真跑过检测）。
    #   节流期间 builder.last_world_entities 保留上次结果
    #   （builder.py:716 `return 0.0` 那条分支），无脑累加会把
    #   同一个敌人重复计好几次 —— 之前那版就是这么算的。
    _fresh = [rr for rr in rows if rr.get("ents") and rr.get("age") == 0]
    _stale_n = sum(1 for rr in rows if rr.get("ents") and rr.get("age"))
    all_ents = [e for rr in _fresh for e in rr["ents"]]
    if all_ents:
        kinds = Counter(e.kind for e in all_ents)
        groups = Counter(getattr(e, "group", "?") for e in all_ents)
        print(f"  新鲜帧实体种类        : {dict(kinds)}"
              f"   （{len(_fresh)} 帧新检出，另有 {_stale_n} 帧节流复用）")
        print(f"  新鲜帧实体分组        : {dict(groups)}")
        print(f"  判据计数（新鲜帧合计）: enemy_identify "
              f"{sum(rr['n_enemy'] for rr in _fresh)} / health_bar "
              f"y<{ENEMY_HEALTH_BAR_Y_MAX} {sum(rr['n_hb'] for rr in _fresh)}")
    elif any(rr["n_ent"] is not None for rr in rows):
        print("  本次实体种类          : （跑了检测但一个都没检出）")
    else:
        print("  本次实体              : 检测从未运行（未启用 / 场景不允许 / 节流未到点）")

    # 结论按**真实返回值**给，不再按「有没有 enemy 分组」猜
    if _n_true:
        print(f"    ★ 有 {_n_true}/{len(rows)} 帧 has_nearby_enemy() 为真 —— "
              f"三条战斗规则在这些帧可触发")
    elif _n_unk == len(rows):
        print("    ★ 全部 UNKNOWN —— 实体检测一次都没跑，规则不会触发。"
              "先查检测器有没有被注入 / 场景是否允许。")
    elif _n_unk:
        print(f"    ★ 没有一帧为真（其中 {_n_unk} 帧 UNKNOWN 判据不可得）—— "
              f"三条战斗规则不会触发。没检出敌人是正常结果，不是故障。")
    else:
        print("    ★ 没有一帧为真 —— 三条战斗规则不会触发。"
              "没检出敌人是正常结果，不是故障。")

    saved = saved_paths          # 不从 rows 取 —— 正常路径下等价，异常路径下才救场
    if saved:
        print(f"\n  诊断帧已保存（{len(saved)} 张，看这几张就知道 scene 为什么是 {scenes[0]}）：")
        for p in saved:
            print(f"    - {p}")
        if not all_ok or set(scenes) == {"UNKNOWN"}:
            print("    ★ 全是 UNKNOWN —— 打开这几张图确认**抓到的是不是游戏画面**：")
            print("      是别的窗口/桌面 -> 加 --channel printwindow 并把游戏切回前台")
            print("      是黑屏/纯色     -> 硬件加速渲染，改成窗口化或无边框窗口")
            print("      是游戏画面       -> 是 SceneDetector 判据的问题，抓帧发我")
    else:
        print(f"\n  （没有帧被保存到 {DIAG_DIR}）")

    hr()
    print("  ✓ smoke 跑完 —— 感知层在真机上端到端通了。")
    print(SEP)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - 任务要求：打印 traceback，不吞
        traceback.print_exc()
        raise SystemExit(1)
