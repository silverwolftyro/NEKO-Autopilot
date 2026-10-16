"""端到端 dry-run：感知 -> 决策 -> 执行，**一个键都不发**。

跑法::

    python e2e_dryrun.py                          # 默认 30 帧 / mss
    python e2e_dryrun.py --frames 10
    python e2e_dryrun.py --channel printwindow    # 游戏被遮挡时用
    python e2e_dryrun.py --delay 8                # 构造完倒数 8 秒再抓
    python e2e_dryrun.py --entity auto            # 想复现 auto 的随机性才用
    python e2e_dryrun.py --live                   # ★ 发真键（会警告 + 倒数 5 秒）

★ 这个脚本解决什么
------------------
目前**没有任何一条命令**能同时回答这四个问题：

    1. 抓到的到底是不是游戏画面？          （scene.id）
    2. bgi_world 到底检出了什么？          （world_entities）
    3. ``has_nearby_enemy()`` 到底是几？   （True / False / **UNKNOWN**）
    4. 规则**有没有真的产出意图**？        （intent + 执行结果）

``smoke_end_to_end.py`` 只走到第 2 步（它不接决策层），
``run_loop.py`` 三步都走但**默认发的是真键**（--live）且输出被日志淹没。
本脚本把三段串起来，走 ``core/loop.py`` 的同一条路径，但把执行层换成
``DryRunBackend`` —— **只记录、不注入**，所以可以放心对着游戏跑。

★ 为什么默认 ``--entity force_on``
---------------------------------
默认的 ``entity_detector="auto"`` 判据是「实测 p50 < 80 ms」，而 DirectML
的探测值在这台机器上实测漂移 **53~116 ms** —— 骑在阈值上，**同一台机器
会随机给出相反结论**。对端到端验证来说这是致命的：跑一次有敌人、
跑一次没敌人，而你无法区分是「系统坏了」还是「这次没探测到」。

``force_on`` 跳过探测直接启用，让结果**可复现**。想看 auto 的行为就
显式加 ``--entity auto``。

★ ``--live`` 的三道锁
--------------------
``--live`` 会把执行后端从 ``DryRunBackend`` 换成 ``Win32Backend``，
也就是**真的往游戏里发键**。为此设了三道锁，缺一道都不发：

1. **默认关**。不加 ``--live`` 时走 ``DryRunBackend``，一个键都不发。
   判定方式对齐 ``run_loop.py:197`` 的精神：默认永远是安全的那一侧。
2. **显式倒数**。在 ``--delay``（切游戏用）**之后**另起一个 5 秒倒计时，
   给你反悔的窗口。Ctrl+C 可在倒计时期间零副作用退出。
3. **退出必松键**。``finally`` 里调 ``executor.release_held()`` ——
   ``NORMAL_ATTACK`` 是长按，最后一帧按下去之后没人再松它；不松就会把
   「左键一直按住」这个 bug 留在真机上（那正是 §37 修的那个问题）。

运行中的唯一刹车是 **F12 急停**（SafetyGuard 持有，与 ``run_loop.py`` 同一套）。

⚠️ ``--live`` 走的是本脚本这条**独立**路径，不含 ``core/loop.py`` 的
   状态机、事件总线与崩溃护栏。它是「验证按键真的能打出去」的工具，
   不是常驻机器人 —— 常驻请用 ``run_loop.py --live``。

★ 为什么每帧都打
----------------
30 帧的输出就是要给人看的证据流。静默汇总看起来干净，但「哪一帧开始
不对」这个信息一旦丢掉，排查成本会翻好几倍。

⚠️ **不 import ultralytics**（AGPL-3.0，运行时链接即传染）。
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
from uipi_check import check as _uipi_check  # noqa: E402
from uipi_check import probe_channels as _probe_channels  # noqa: E402
from uipi_check import require_admin as _uipi_require_admin  # noqa: E402

try:
    # ⚠️ stdout **和 stderr** 都要配 —— 参数错误是往 stderr 打的，
    #    只配 stdout 的话错误信息在 GBK 控制台上是乱码。
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 - 老 Python 没这功能，忽略
    pass

# ---- 参数默认值 -----------------------------------------------------------
DEFAULT_TITLE = "原神"
DEFAULT_FRAMES = 30
DEFAULT_DELAY = 8.0
#: 默认 0.200 s —— 和 ``core/loop.py`` 的 5 Hz tick 预算一致。
MIN_INTERVAL_S = 0.200
#: ★ 默认 force_on，不 auto。理由见模块 docstring。
DEFAULT_ENTITY = "force_on"
VALID_ENTITY = ("force_on", "force_off", "auto", "off")
#: ★ **默认 False = 干跑**。要发真键必须显式给 ``--live``。
#: 判定方式对齐 ``run_loop.py:197``（``live = args.live and not args.dry_run``）
#: 的精神：默认永远是安全的那一侧。
DEFAULT_LIVE = False
#: --live 之后额外的**最后确认**倒计时（秒）。--delay 是给切游戏用的，
#: 这个是给「你还有 5 秒反悔」用的，两者是不同的等待。
LIVE_COUNTDOWN_S = 5

SEP = "=" * 78


def hr(title: str = "") -> None:
    if title:
        print(f"\n{SEP}\n  {title}\n{SEP}")
    else:
        print(SEP)


def _parse_args() -> tuple[str, int, str | None, float, str, bool]:
    title, frames, channel, delay = (
        DEFAULT_TITLE, DEFAULT_FRAMES, None, DEFAULT_DELAY)
    entity, live = DEFAULT_ENTITY, DEFAULT_LIVE
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else None
        if a == "--frames" and nxt is not None:
            frames = int(nxt); i += 2
        elif a == "--channel" and nxt is not None:
            channel = nxt; i += 2
        elif a == "--delay" and nxt is not None:
            delay = max(0.0, float(nxt)); i += 2
        elif a == "--title" and nxt is not None:
            title = nxt; i += 2
        elif a == "--entity" and nxt is not None:
            # 「off」是给人用的简写，映射到 force_off
            entity = "force_off" if nxt == "off" else nxt
            if entity not in VALID_ENTITY:
                print(f"✗ --entity 取值 {nxt!r} 不认识。可选："
                      f"{' / '.join(VALID_ENTITY)}", file=sys.stderr)
                raise SystemExit(2)
            i += 2
        elif a == "--live":
            live = True; i += 1
        elif a == "--no-live":
            # 显式关掉：脚本被包在别处调用时用来强制回到干跑
            live = False; i += 1
        elif a.startswith("-"):
            print(f"未知参数: {a}", file=sys.stderr)
            i += 1
        else:
            title = a; i += 1
    return title, frames, channel, delay, entity, live


def _fmt_has_enemy(value) -> str:
    """``has_nearby_enemy()`` 的三态。**UNKNOWN 不并进 False**。"""
    if value is True:
        return "True  "
    if value is False:
        return "False "
    return "UNKNOWN"


def _why_no_intent(trace) -> str:
    """没产意图时，挑**一条**最有信息量的原因。

    跳过 ``enabled=false`` 的条目 —— 那 18 条占满了 trace，对排查毫无帮助。
    """
    for e in getattr(trace, "entries", ()):
        reason = getattr(e, "reason", "") or ""
        if getattr(e, "verdict", "") == "SKIPPED" \
                and "enabled=false" not in reason:
            return f"{getattr(e, 'name', '?')}: {reason}"
    entries = getattr(trace, "entries", ())
    if entries:
        return getattr(entries[0], "reason", "") or "条件为假"
    return "没有可评估的规则"


def main() -> int:
    title, n_frames, channel, delay, entity, live = _parse_args()
    # ★ --live 的**唯一**门禁：必须是管理员（游戏内核级反作弊 ->
    #   UIPI 隔离永久存在 -> 脚本只能跟着提权）。dry-run 不需要。
    if _uipi_require_admin(live) != 0:
        return 2

    from autopilot.perception.builder import StateBuilder
    from autopilot.perception.capturer import Capturer
    from autopilot.tools._stack import build_policy
    from autopilot.policy.context import PolicyContext
    from autopilot.actuator import (
        DryRunBackend, InputSimulator, IntentExecutor, SafetyConfig,
        SafetyGuard, Win32Backend,
    )

    hr("1 · 构造 StateBuilder（不探测）")
    t0 = time.perf_counter()
    builder = StateBuilder(entity_detector=entity)
    init_ms = (time.perf_counter() - t0) * 1000
    print(f"  entity_detector  : {entity!r}")
    print(f"  构造耗时         : {init_ms:.0f} ms")
    det = builder._entity_detector
    print(f"  检测器           : {type(det).__name__ if det else 'None（已关闭）'}")
    print(f"  节流间隔         : 每 {builder._entity_interval} tick")
    print(f"  状态说明         : {builder._entity_note}")
    for ev in builder.drain_events():
        print(f"  ⚠ 事件 {ev.code}: {ev.message}")
        print(f"      context={ev.context}")
    if det is None:
        print("  ★ 检测器没起来 —— has_nearby_enemy() 会**恒为 UNKNOWN**，")
        print("    三条战斗规则（enabled=true）永远不会触发。这是本次要查的第一件事。")

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
    # ★ 通道探针 + UIPI 自检放在**窗口定位到之后**（不是 main 开头）——
    #   开头时前台是终端，按前台查权限查的是终端而不是游戏。
    for _ch, _ok, _why in _probe_channels(cap.window_rect):
        print(f"  [通道] {_ch:11} {'✓' if _ok else '✗'}  {_why}")
    _uipi_check(live)                 # 纯诊断，只打印，不拦
    if cap.window_rect:
        _x, _y, _w, _h = cap.window_rect
        print(f"  客户区           : {_w}x{_h}")
        if _w <= 0 or _h <= 0:
            print("  ⚠ 尺寸为 0 —— 窗口最小化了。PrintWindow 抓不到最小化窗口。")

    hr(f"3 · 构造决策层 + 执行层（{'Win32Backend' if live else 'DryRunBackend'}）")
    policy, rules_dir, rule_count = build_policy()
    # ★ 与 run_loop.py:197 同一个判定精神：默认永远是安全的那一侧。
    #   backend 只在这里二选一，脚本里没有第三处能发键的地方。
    sim = InputSimulator(backend=Win32Backend() if live else DryRunBackend())
    guard = SafetyGuard(SafetyConfig())
    executor = IntentExecutor(simulator=sim, guard=guard)
    print(f"  规则目录         : {rules_dir}")
    print(f"  规则数           : {rule_count}")
    if live:
        print("  执行后端         : ★★ Win32Backend —— **会向游戏发送真实键鼠** ★★")
    else:
        print("  执行后端         : DryRunBackend（只记录，不注入任何输入）")
    print(f"  急停键           : F12（SafetyGuard 持有）")

    # 倒计时放在**构造完之后** —— 模型加载要 1.2 s，放在构造前倒会被吃掉
    if delay > 0:
        hr("3.5 · 延迟启动")
        end = time.monotonic() + delay
        print(f"\n  [DELAY] {delay:g} 秒后开始抓帧 —— ★ 现在切回游戏，让它在前台！")
        while True:
            left = end - time.monotonic()
            if left <= 0:
                break
            print(f"    剩余 {left:5.1f} s\r", end="", flush=True)
            time.sleep(0.1)
        print()

    # ★ --live 的最后一道闸：醒目警告 + 独立倒计时。
    #   和上面的 --delay 是**两回事** —— --delay 是给切游戏用的，
    #   这个是给「你还有几秒反悔」用的，所以放在 delay **之后**。
    if live:
        hr("3.6 · ★★ LIVE 模式确认 ★★")
        print()
        print("  " + "!" * 70)
        print("  !!  接下来会向游戏发送**真实的键盘 / 鼠标**事件              !!")
        print("  !!  机器人会真的按键、真的普攻、真的走位。                  !!")
        print("  !!  唯一的刹车是 F12 急停 —— 请确认你知道它在哪。           !!")
        print("  " + "!" * 70)
        print()
        print("  · 按 Ctrl+C 可在倒计时期间立刻退出（此时一个键都还没发）")
        print("  · 运行中按 F12 触发急停：释放全部按键并停止执行")
        print("  · 确认只想干跑就不加 --live（默认就是干跑）")
        print()
        end = time.monotonic() + LIVE_COUNTDOWN_S
        n = LIVE_COUNTDOWN_S
        while n > 0:
            print(f"    {n} 秒后开始发送真实键鼠 —— Ctrl+C 取消 ...\r",
                  end="", flush=True)
            time.sleep(1.0)
            n -= 1
        print("    开始发送。                        ")

    hr(f"4 · 抓 {n_frames} 帧")
    rows: list[dict] = []
    try:
        for i in range(1, n_frames + 1):
            t_frame = time.perf_counter()
            frame = cap.grab()
            state = builder.build(frame)
            build_ms = (time.perf_counter() - t_frame) * 1000

            # ★ 与 core/loop.py:457-459 完全一致：侧信道搬运，且**保留 None**
            ents = builder.last_world_entities
            policy.last_world_entities = (
                None if ents is None else tuple(ents))

            # has_nearby_enemy() 真值。与 policy 内部建的是同一个
            # PolicyContext（同样的 state + 同样的 world_entities），
            # 纯函数，结果必然一致 —— 这里只是为了能把它打出来。
            has = PolicyContext.from_state(
                state, now_ms=state.timestamp_ms or None,
                world_entities=ents).has_nearby_enemy()

            intent = policy.poll(state)

            if intent is not None:
                res = executor.execute(intent, state)
                tail = (f"→ {intent.action.value} (prio={intent.priority})"
                        f"  exec={res.outcome.value} {res.elapsed_ms:.0f}ms")
                if res.outcome.value != "OK":
                    tail += f"  [{res.reason}]"
                rule = intent.action.value
            else:
                res = None
                tail = f"→ (无意图: {_why_no_intent(policy.decide(state))})"
                rule = None

            stale = ("" if builder.last_entity_age_ticks == 0 else
                     f"  [age={builder.last_entity_age_ticks}]")
            print(f"帧 {i:>2}: scene={state.scene.id:8} "
                  f"has_enemy={_fmt_has_enemy(has)}  {tail}{stale}")

            rows.append({
                "scene": state.scene.id,
                "has": has,
                "n_ent": None if ents is None else len(ents),
                "build_ms": build_ms,
                "rule": rule,
                "outcome": None if res is None else res.outcome.value,
            })

            rest = MIN_INTERVAL_S - (time.perf_counter() - t_frame)
            if rest > 0:
                time.sleep(rest)
    except KeyboardInterrupt:
        # ★ Ctrl+C 立刻停 —— finally 仍会执行，所以键一定会被松开
        print("\n  ⚠ 收到 Ctrl+C，正在松开全部按键并退出……")
        raise
    finally:
        # ★★★ 退出时**必须**松键 ★★★
        #   NORMAL_ATTACK 是长按，最后一帧按下去之后没人再松它。
        #   不在这里 release_held() 的话，脚本退出瞬间会把「左键一直
        #   按住」的 bug 留在真机上 —— 那正是本次要修的那个问题。
        held = executor.release_held()
        if held:
            print(f"  ✓ 退出前松开了 {len(held)} 个键（长按动作遗留）")
        cap.close()

    # ---- 汇总 ----------------------------------------------------------
    hr("5 · 汇总")
    n = len(rows)
    if n == 0:
        print("  一帧都没抓到（--frames 0？）—— 没有可汇总的东西。")
        hr()
        return 0
    print(f"  帧数             : {n}")
    avg = sum(r["build_ms"] for r in rows) / n
    print(f"  平均 build 耗时  : {avg:.0f} ms"
          f"  （tick 预算 200 ms 的 {avg / 200 * 100:.0f}%）")

    scenes = Counter(r["scene"] for r in rows)
    print(f"  场景分布         : {dict(scenes)}")
    if set(scenes) == {"UNKNOWN"}:
        print("    ★ 全是 UNKNOWN —— 打开一张诊断图确认抓到的是不是游戏画面。")
        print("      是别的窗口/桌面 -> 加 --channel printwindow")
        print("      是黑屏/纯色     -> 硬件加速渲染，改窗口化")

    he = Counter(_fmt_has_enemy(r["has"]).strip() for r in rows)
    print(f"  has_nearby_enemy(): {dict(he)}")
    if he.get("UNKNOWN"):
        print("    ★ 有 UNKNOWN 帧 —— 实体检测没跑（未启用 / 场景不允许 /")
        print("      节流未到点）。UNKNOWN 时三条战斗规则**不会**触发。")

    with_intent = [r for r in rows if r["rule"]]
    if with_intent:
        rules = Counter(r["rule"] for r in with_intent)
        outs = Counter(r["outcome"] for r in with_intent)
        print(f"  产意图的帧       : {len(with_intent)}/{n}")
        print(f"  意图分布         : {dict(rules)}")
        print(f"  执行结果         : {dict(outs)}")
        print(f"\n汇总: {n} 帧中 {len(with_intent)} 帧产意图 —— "
              f"{'、'.join(f'{k}×{v}' for k, v in rules.items())}")
    else:
        print("\n汇总: 0 帧产意图 —— 三条战斗规则一次都没触发。")
        if he.get("UNKNOWN"):
            print("  最可能的原因：实体检测没跑（看上面 UNKNOWN 那段）。")
        elif scenes.get("WORLD"):
            print("  场景是 WORLD 但没意图：画面里没有敌人标识，")
            print("  或者敌人血条不在 y<0.5。按 Tab 锁定目标会出 enemy_identify。")
        else:
            print("  场景不是 WORLD —— 规则 scenes=[\"WORLD\"] 直接不匹配。")

    if live:
        print("\n  ★ 本次是 **LIVE**：上面的按键**已经真的发进游戏**了。"
              "退出时已调用 release_held() 松开长按。")
    else:
        print("\n  （执行全程走 DryRunBackend，**没有向游戏发送任何输入**）")
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
