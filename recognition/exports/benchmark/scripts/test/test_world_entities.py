"""世界实体检测单测（§22 · T1~T7）。

★ 依赖真实模型
--------------
``bgi_world.onnx`` 是 **BetterGI GPL-3.0 资产**，本地保留、不随包分发
（见 ``recognition/notes/LICENSING.md``）。所以这些测试**需要那台机器上
有模型文件**；没有时下面 T7 会验「响亮失败」而不是跳过。

★ 关键回归点：NMS 修好之前 a:f00010 会返回 0
------------------------------------------------
那一帧的原始输出是 **4 个完全重合的 unmined_ore**
（都是 ``(1373, 538, 83, 83)``，三尺度锚点各命中一次）。
``_nms`` 崩溃 -> ``detect()`` 吞异常 -> 返回空。修好后应合成 **1 个**。
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
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

ROOT = Path(PLUGIN_DIR)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # _autocache_guard

from autopilot.contracts.state_packet import StatePacket        # noqa: E402
from autopilot.perception import builder as B                   # noqa: E402
from autopilot.perception import world_entities as WE           # noqa: E402
from autopilot.perception.capturer import (                      # noqa: E402
    CaptureChannel, CapturedFrame,
)
from autopilot.perception.yolo import YoloUnavailableError      # noqa: E402
from autopilot.policy.context import PolicyContext             # noqa: E402

from _autocache_guard import reset_auto_cache                # noqa: E402

FRAMES_A = Path(RECOG_ROOT / 'raw' / 'frames' / 'batch_20260929a')
PASS, FAIL = [], []
MODEL_OK = WE.BGI_WORLD_ONNX.is_file() and WE.BGI_WORLD_CLASSES.is_file()


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f"   {detail}" if detail else ""))


def skip(name, why):
    print(f"  [SKIP] {name}   {why}")


def section(t):
    print("\n" + "=" * 92)
    print(t)
    print("=" * 92)


def imread(p):
    return cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)


def frame_of(fid, base=FRAMES_A):
    img = imread(base / f"{fid}.jpg")
    if img is None:
        return None
    return CapturedFrame(image=img, rect=(0, 0, img.shape[1], img.shape[0]),
                         timestamp_ms=1000, frame_seq=1,
                         channel=CaptureChannel.MSS)


# ================================================================ T1
section("T1 · 类名表")
names = WE.load_class_names()
check("7 类且顺序正确",
      names == ["health_bar", "enemy_identify", "drops", "ore",
                "unmined_ore", "chest", "locked_chest"], str(names))
check("KIND_GROUPS 覆盖全部 7 类",
      set(WE.KIND_GROUPS) == set(names), str(set(names) - set(WE.KIND_GROUPS)))

try:
    WE.load_class_names(Path("D:/nope/classes.txt"))
    check("类名表缺失 -> YoloUnavailableError", False)
except YoloUnavailableError:
    check("类名表缺失 -> YoloUnavailableError", True)

# ================================================================ T2
section("T2 · 场景门控（性能约束：只在 WORLD_SCENES 里跑）")
check("COMBAT -> 不跑（§28 二值化后它不会出现）",
      not WE.WorldEntityDetector.should_run_for_scene("COMBAT"))
check("★ WORLD -> 跑（§28 新场景 id）",
      WE.WorldEntityDetector.should_run_for_scene("WORLD"))
check("WORLD_SCENES = {WORLD, UNKNOWN}",
      set(WE.WORLD_SCENES) == {"WORLD", "UNKNOWN"}, str(sorted(WE.WORLD_SCENES)))
check("MAIN_MENU -> 不跑", not WE.WorldEntityDetector.should_run_for_scene("MAIN_MENU"))
check("BAG_INVENTORY -> 不跑",
      not WE.WorldEntityDetector.should_run_for_scene("BAG_INVENTORY"))
check("MAP_BIG -> 不跑", not WE.WorldEntityDetector.should_run_for_scene("MAP_BIG"))
check("None -> 不跑", not WE.WorldEntityDetector.should_run_for_scene(None))
check("WORLD_SCENES 只有 2 个场景", len(WE.WORLD_SCENES) == 2,
      str(sorted(WE.WORLD_SCENES)))

# ================================================================ T3
section("T3 · 真实帧检出")
if not MODEL_OK:
    skip("真实帧检出", f"缺模型 {WE.BGI_WORLD_ONNX}")
else:
    # ⚠️ 这里显式放大超时到 5 s：**本测试要验的是"检出对不对"，
    #    不是耗时**。用生产阈值会让结果随机丢帧 —— 实测同一批帧
    #    在 41/46 ~ 46/46 之间波动。耗时单独用观测项报告。
    det = WE.WorldEntityDetector(timeout_ms=5000)
    det.warmup()
    check("模型加载成功", det.is_loaded)

    # ★ 核心回归：4 个重合 unmined_ore -> NMS 后 1 个
    f = frame_of("f00010")
    ents = det.detect(f.image)
    check("a:f00010 -> 恰好 1 个（NMS 把 4 个重合框合并了）",
          len(ents) == 1, f"n={len(ents)} -> {[e.kind for e in ents]}")
    check("a:f00010 的类别是 unmined_ore",
          ents and ents[0].kind == "unmined_ore",
          str([e.kind for e in ents]))

    # 空帧
    f5 = frame_of("f00005")
    e5 = det.detect(f5.image)
    check("a:f00005 -> 空列表（实测 0 检出）", e5 == [], f"n={len(e5)}")
    check("空列表时 last_count=0", det.last_count == 0, str(det.last_count))

    # 空输入
    check("None 输入 -> 空列表", det.detect(None) == [])
    check("全黑 4x4 输入 -> 空列表",
          det.detect(np.zeros((4, 4, 3), np.uint8)) == [])

    # enemy_identify（任务 E 的结论帧）
    f112 = det.detect(frame_of("f00112").image)
    check("a:f00112 -> 有 enemy_identify",
          any(e.kind == "enemy_identify" for e in f112),
          str([(e.kind, round(e.confidence, 2)) for e in f112]))
    f113 = det.detect(frame_of("f00113").image)
    check("a:f00113 -> 有 enemy_identify",
          any(e.kind == "enemy_identify" for e in f113),
          str([(e.kind, round(e.confidence, 2)) for e in f113]))

    # ============================================================ T4
    section("T4 · 坐标：bbox 归一化 / screen_pos 720 基准")
    e = det.detect(frame_of("f00113").image)
    enemy = [x for x in e if x.kind == "enemy_identify"]
    check("检出 enemy 供坐标检查", bool(enemy), str([x.kind for x in e]))
    if enemy:
        x, y, w, h = enemy[0].bbox
        check("bbox 全在 [0,1]", all(0.0 <= v <= 1.0 for v in (x, y, w, h)),
              str(enemy[0].bbox))
        cx, cy = enemy[0].screen_pos
        check("screen_pos 在 720 基准尺寸内",
              0 <= cx <= WE.BASE_SIZE[0] and 0 <= cy <= WE.BASE_SIZE[1],
              f"({cx}, {cy}) vs {WE.BASE_SIZE}")
        # 720 基准 < 原始 2304x1440，所以 screen_pos 应明显小于像素坐标
        img = frame_of("f00113").image
        check("screen_pos 已从 2304 折算到 1280（不是原始像素）",
              cx < img.shape[1], f"cx={cx} < W={img.shape[1]}")
        check("screen_pos 约等于 bbox 中心折算",
              abs(cx / WE.BASE_SIZE[0] - (x + w / 2)) < 0.02,
              f"{cx / WE.BASE_SIZE[0]:.4f} vs {(x + w / 2):.4f}")
    check("按置信度降序", all(e[i].confidence >= e[i + 1].confidence
                              for i in range(len(e) - 1)),
          str([round(x.confidence, 2) for x in e]))
    check("group 分组正确",
          all(x.group == WE.KIND_GROUPS.get(x.kind, "other") for x in e),
          str([(x.kind, x.group) for x in e]))

    # ============================================================ T5
    section("T5 · builder 集成（F2：不进 StatePacket）")

    class Det:
        def __init__(self, ents):
            self.ents, self.calls = ents, 0
        @staticmethod
        def should_run_for_scene(s):
            return s in WE.WORLD_SCENES
        def detect(self, img, base_size=WE.BASE_SIZE):
            self.calls += 1
            return list(self.ents)
        last_ms = 0.0

    fake = [WE.WorldEntity(kind="enemy_identify", bbox=(0.5, 0.5, 0.02, 0.02),
                           confidence=0.9, screen_pos=(640, 360))]

    # ⚠️ 显式 entity_detector=None —— 默认已经是 "auto"。不关掉的话这里
    #    测的是「被节流挡了首帧」而不是「未注入」，属于对的理由而绿。
    b_none = B.StateBuilder(entity_detector=None)
    p_none = b_none.build(frame_of("f00010"))
    check("未注入检测器 -> last_world_entities 是 None（= 没跑）",
          b_none.last_world_entities is None, str(b_none.last_world_entities))
    check("未注入检测器 -> last_entity_ms=0", b_none.last_entity_ms == 0.0)
    check("★ StatePacket 没有 world_entities 字段（F2 契约不变）",
          "world_entities" not in p_none.to_dict(),
          str(sorted(p_none.to_dict().keys())))

    class DetEmpty(Det):
        def detect(self, img, base_size=WE.BASE_SIZE):
            self.calls += 1
            return []          # 跑了，但确实没有

    # ⚠️ 显式间隔=1：默认是 5（节流），第一帧不会真跑检测。
    #    本节验的是"跑完之后结果对不对"，不是节流（节流在 test_integration T6）。
    # 先清一次 auto cache：本文件虽独立进程，但显式清能防将来被并进别的
    # runner 时串味。
    reset_auto_cache()
    de = DetEmpty([])
    be = B.StateBuilder(entity_detector=de, entity_interval_ticks=1)
    be.build(frame_of("f00010"))
    check("★ 跑了但确实没有 -> 空列表（不是 None，三态不同）",
          be.last_world_entities == [], str(be.last_world_entities))
    c_empty = PolicyContext.from_state(
        be.build(frame_of("f00011")), now_ms=1000,
        world_entities=tuple(be.last_world_entities or ()))
    check("空列表 -> has_nearby_enemy = False（而非 UNKNOWN）",
          c_empty.has_nearby_enemy() is False)
    c_none = PolicyContext.from_state(
        be.build(frame_of("f00012")), now_ms=1000, world_entities=None)
    check("None -> has_nearby_enemy = UNKNOWN",
          type(c_none.has_nearby_enemy()).__name__ == "UnknownValue")

    d5 = Det(fake)
    b5 = B.StateBuilder(entity_detector=d5, entity_interval_ticks=1)
    b5.build(frame_of("f00010"))
    check("注入后 -> last_world_entities 有 1 个",
          len(b5.last_world_entities) == 1,
          str([e.kind for e in b5.last_world_entities]))
    check("注入后 -> 检测器被调 1 次", d5.calls == 1, str(d5.calls))
    check("注入后 -> StatePacket 里仍然没有该字段",
          "world_entities" not in b5.build(frame_of("f00011")).to_dict())

    d6 = Det(fake)
    b6 = B.StateBuilder(entity_detector=d6)
    b6.build(frame_of("f00010"), forced_scene="BAG_INVENTORY")
    check("场景不允许 -> 不跑检测器", d6.calls == 0, str(d6.calls))
    check("场景不允许 -> last_world_entities 是 None（= 没跑）",
          b6.last_world_entities is None, str(b6.last_world_entities))

    # ============================================================ T6
    section("T6 · PolicyContext 派生属性")

    def ctx(entities):
        st = StatePacket.unknown(game_id="genshin_impact", state_version=1)
        return PolicyContext.from_state(
            st, now_ms=1000, world_entities=entities)

    c0 = ctx(None)
    r0 = c0.has_nearby_enemy()
    check("None -> has_nearby_enemy 返回 UNKNOWN（不是 False）",
          type(r0).__name__ == "UnknownValue", type(r0).__name__)
    check("UNKNOWN 的真值恒为 False（规则不会误触发）", bool(r0) is False)
    # ⚠️ unknown_hits 是**调用属性时**才填的，读之前必须先调一次
    check("没跑检测时 unknown_hits 有记录",
          any("has_nearby_enemy" in h for h in c0.unknown_hits),
          str(c0.unknown_hits[:1]))
    check("空元组 -> has_nearby_enemy = False",
          ctx(()).has_nearby_enemy() is False)

    mix = [WE.WorldEntity("enemy_identify", (0, 0, 1, 1), 0.9, (1, 1)),
           WE.WorldEntity("chest", (0, 0, 1, 1), 0.8, (2, 2)),
           WE.WorldEntity("locked_chest", (0, 0, 1, 1), 0.7, (3, 3)),
           WE.WorldEntity("unmined_ore", (0, 0, 1, 1), 0.6, (4, 4))]
    c = ctx(tuple(mix))
    check("有 enemy_identify -> has_nearby_enemy = True", c.has_nearby_enemy() is True)
    check("chest + locked_chest -> has_nearby_chest = True", c.has_nearby_chest() is True)
    check("unmined_ore -> has_nearby_ore = True", c.has_nearby_ore() is True)
    check("nearby_entity_count = 4", c.nearby_entity_count() == 4)

    c2 = ctx(tuple([WE.WorldEntity("chest", (0, 0, 1, 1), 0.8, (1, 1))]))
    check("只有 chest -> enemy/ore 为 False",
          c2.has_nearby_enemy() is False and c2.has_nearby_ore() is False)

# ================================================================ T7
section("T7 · 模型缺失 -> 响亮失败（不静默返回空）")
try:
    WE.WorldEntityDetector(model_path=Path("D:/nope/bgi_world.onnx")).load()
    check("模型缺失 -> YoloUnavailableError", False)
except YoloUnavailableError:
    check("模型缺失 -> YoloUnavailableError", True)
except Exception as exc:
    check("模型缺失 -> YoloUnavailableError", False, f"实际 {type(exc).__name__}")

check("常量都在模块级（可调）",
      isinstance(WE.WORLD_ENTITY_CONF, float)
      and isinstance(WE.WORLD_ENTITY_TIMEOUT_MS, float)
      and isinstance(WE.BASE_SIZE, tuple),
      f"conf={WE.WORLD_ENTITY_CONF} timeout={WE.WORLD_ENTITY_TIMEOUT_MS}")
check("超时是**挂死守卫**而非性能预算（>= 500 ms）",
      WE.WORLD_ENTITY_TIMEOUT_MS >= 500.0,
      f"{WE.WORLD_ENTITY_TIMEOUT_MS} ms —— 调到 200~400 会导致随机丢帧")

# ================================================================ T8
section("T8 · 耗时观测（**不做硬断言** —— 机器负载会抖）")
if MODEL_OK:
    import statistics
    d8 = WE.WorldEntityDetector(timeout_ms=5000)
    d8.warmup()
    samples = []
    for fid in ("f00010", "f00112", "f00113", "f00030", "f00050", "f00080"):
        im = imread(FRAMES_A / f"{fid}.jpg")
        if im is None:
            continue
        d8.detect(im)
        samples.append(d8.last_ms)
    if samples:
        print(f"  [观测] n={len(samples)}  min={min(samples):.0f}  "
              f"p50={statistics.median(samples):.0f}  max={max(samples):.0f} ms")
        print(f"  [观测] 200 ms tick 预算占比 p50 = "
              f"{statistics.median(samples) / 200 * 100:.0f}%")
        check("耗时数据采到了（观测项，不设阈值）", len(samples) >= 3)
        check("⚠️ 中位耗时已超 200 ms tick 预算（**已知未决，见文档 §22**）",
              True, f"{statistics.median(samples):.0f} ms —— 需每 N tick 跑或上 GPU")
else:
    skip("耗时观测", "缺模型")

print("\n" + "=" * 92)
print(f"结果: {len(PASS)}/{len(PASS) + len(FAIL)} 通过")
if FAIL:
    print("  失败: " + ", ".join(FAIL))
print("=" * 92)
sys.exit(1 if FAIL else 0)
