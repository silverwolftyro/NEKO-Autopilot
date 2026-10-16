# `MOVE_TO` 意图设计草案

> 2026-10-14 ｜ **只是设计，没有注册任何东西**
> 配套：`recognition/notes/arch_pathfind_overlap_analysis.md`
> 算法库已就位：`autopilot/nav/steer.py`（72 条单测全绿）

---

## 〇 · 动手前必须知道的三个既有事实

这三条把上一轮估的「工作量」大幅改写了，也是本草案的立足点。

### 1. 坐标槽位**已经存在**，而且有校验器和单测

`IntentPacket.target.world_position`（`contracts/intent_packet.py:113`）
早就是为这件事留的字段，而且**已有两道消费方**：

| 位置 | 行为 |
|---|---|
| `contracts/intent_packet.py:243` | `world_position` 非空但定位不可用 -> 拒绝（`POSITION_UNAVAILABLE`） |
| `actuator/validator.py:233` `_check_position` | 同上 |

`tests/unit/test_intent_validator.py:282-319` **已经用 `world_position` 写了三条单测**，
其中一条的注释写着「也就是『这个意图指名了要去的地方』」。

→ **不需要新造管道。** `MOVE_TO` 只要往这个槽位里放坐标。

### 2. 朝向**已经在感知包里**

`PositionInfo`（`contracts/state_packet.py:97-125`）已经有 `heading_deg`。
这正是 `autopilot.nav.steer.steer()` 需要的那个输入。

→ 不需要新建朝向估计器。但要注意：`e2e_pathfind.py` 用的是**滑动窗口圆周均值**，
而 `PositionInfo.heading_deg` 是单点值。两者语义**不等价**（见 §四·Q2）。

### 3. ⚠️ 平面轴名全仓不一致：`x/y` vs `x/z`

这是**本轮发现的真缺陷**，不修的话 `MOVE_TO` 一接线就会踩：

| 位置 | 用的是 |
|---|---|
| `contracts/state_packet.py:112-113` `PositionInfo` | **`x`, `y`** |
| `contracts/schemas/intent_packet.schema.json:43-44` `world_position` | **`x`, `y`** |
| `core/loop.py:568` PATHFIND_START 取目标 | **`.x`, `.z`** |
| `core/loop.py:968-969` PATHFIND_ARRIVED 判到达 | **`.x`, `.z`** |
| `e2e_pathfind.py` 全篇 | **`x`, `z`** |
| `tests/unit/test_intent_validator.py:292` `world_position` | **`x`, `z`** |
| `tests/unit/test_intent_validator.py:313` `PositionInfo(...)` | **`x`, `y`** |

同一个测试文件里，第 292 行用 `z`、第 313 行用 `y`。

**为什么一直没被发现**：现有校验只判断 `world_position` **是否为真**，
**从来不读里面的键名**。所以写成 `x/z` 还是 `x/y` 都能过校验，
错误被推迟到「某个 executor 真的去读坐标」的那一刻。

→ **这是 `MOVE_TO` 之前必须先定的第一件事。** 见 §二。

---

## 一 · 字段布局

### 方案对比

| 方案 | 放哪 | 评价 |
|---|---|---|
| **甲**：`target.world_position` + `params` | 复用既有槽位 | ✅ **推荐**。零新增管道，两道既有校验自动生效 |
| 乙：全塞 `params` | `params["target_x"]` 等 | ❌ 会**绕过** `POSITION_UNAVAILABLE` 校验，定位丢失时照样走 |
| 丙：给 `IntentPacket` 顶层加字段 | `intent.target_x` | ❌ 破坏「坐标属于 target」的既有语义 |

**采用甲。** 具体：

```python
IntentPacket(
    action=ActionType.MOVE_TO,
    target=IntentTarget(
        world_position={
            "x": 4634.0,
            "z": 4687.0,        # ⚠️ 键名待定，见 §二
            "radius": 1.5,      # 到达半径（米）
        },
    ),
    params={
        "sprint": True,        # 可选：是否冲刺
        "waypoints": [...],    # 可选：中间路点（未来 LLM 规划用）
    },
)
```

⚠️ **`arrive_radius` 不建议单独开一个顶层字段** —— schema 里 `world_position`
已经预留了 `radius`，且语义天然属于「这个点要多近才算到」。
放 `params` 会和它重复，两个真值源必然漂。

---

## 二 · ⚠️ 待拍板：平面用 `x/z` 还是 `x/y`

**必须先定，否则不要开始接线。** 两条路：

| | 统一到 `x/z` | 统一到 `x/y` |
|---|---|---|
| 改动面 | 改 `PositionInfo` + schema + 若干测试 | 改 `e2e_pathfind.py` 全篇 + `core/loop.py` + 测试 |
| 风险 | **会破坏序列化契约**（`state_snapshot.json` 里已有 `y` 键） | 改动多，但**不碰持久化格式** |
| 与真机一致吗 | 原神世界坐标普遍记作 `(x, z)` 地面平面 | — |

**倾向统一到 `x/z`**（寻路侧和真机数据都是 `x/z`，且 `PositionInfo` 的 `y`
这个命名本身可能就是二维图坐标的遗留），但**必须先确认**
`state_snapshot.json` 的兼容性 —— ⚠️ **需要进一步调查**。

保守折中：本次只在 `MOVE_TO` 的**新代码**里统一用 `x/z`，
同时给 `PositionInfo` 加一个**只读别名属性** `z`（`property z: return self.y`），
不做破坏性改名。零风险，且新代码干净。

---

## 三 · 需要改的四处（**尚未执行**）

| # | 文件 | 改动 | 风险 |
|---|---|---|---|
| 1 | `contracts/intent_packet.py` | `ActionType` 加 `MOVE_TO = "MOVE_TO"` | 低。枚举是安全边界，加值即扩边界 |
| 2 | 同上 | `_ACTION_KIND_MAP` 加 `MOVE_TO: "NAV"` | 低 |
| 3 | `actuator/traits.py` `TRAITS` | 加 `"MOVE_TO": ActionTraits(...)` | 低 |
| 4 | `actuator/executor.py` | 加执行分支，调 `nav.steer.steer()` | **中**。唯一真正动执行路径的地方 |

traits 建议值（**草案，未定稿**）：

```python
"MOVE_TO": ActionTraits(
    strict_arrival=True,            # 到点必须停住
    approach_by_walking=True,       # 复用既有「边走边转」机制
    abort_on_target_lost=True,      # ★ 定位一丢立刻停，不许预测填补
    allowed_scenes=(SCENE_WORLD,),  # ⚠️ 见下方警告
    min_gap_ms=0,                   # 连续 tick 重复下发是正常的
    max_duration_ms=120_000,        # 导航是长程动作，不能用 5s 默认值
    allow_move_while_turning=True,  # 移动中转向（原则 5）
    requires_foreground=True,
),
```

⚠️ **`allowed_scenes` 待定**：`traits.py:62-66` 记载，7 类场景分类器没有模型，
场景判定降级为 `SceneDetector` 的**二值**结果（WORLD / UNKNOWN），
**永不产出 COMBAT**。而真机上导航很可能始于 `SCENE_UNKNOWN`。
若只写 `SCENE_WORLD`，会出现「定位正常但一条 MOVE_TO 都放不出来」。
**需要进一步调查** —— 建议先跟 `MOVE_FORWARD` 的实际取值对齐。

---

## 四 · 执行器分支（思路，**未写代码**）

```
executor 收到 MOVE_TO
  ├─ 从 target.world_position 取 (x, z, radius)
  ├─ 从 state.position 取 (x, y/ z) 与 heading_deg
  ├─ 调 autopilot.nav.steer.steer(...)  ->  SteerPlan
  ├─ 按 plan.segments 注入鼠标（段间隔 > 游戏一帧）
  ├─ plan.walk  -> 按住 W；plan.sprint -> 按住 Shift
  └─ plan.action == "ARRIVED" -> 释放全部，返回成功
```

**站定状态是跨帧的**（`SteerPlan.stand_active`），执行器必须把它存下来
并在下一帧传回去 —— 这一点库的单测
`test_stand_state_must_be_fed_back` 已经钉住了。

---

## 五 · 需要进一步调查

1. **Q1**：`world_position` 的键名统一方案，以及 `state_snapshot.json`
   里 `PositionInfo.y` 的历史数据兼容性。
2. **Q2**：`PositionInfo.heading_deg`（单点）vs `steer.heading_deg()`
   （滑动窗口圆周均值）。前者是后者的**特例**吗？能不能直接喂？
   如果不能，`MOVE_TO` 就得自带一个位移历史窗口 —— 这会显著增加执行器复杂度。
3. **Q3**：`MOVE_TO` 的 `allowed_scenes` 该跟谁对齐（见 §三警告）。
4. **Q4**：`min_gap_ms=0` 意味着同一动作每 tick 都会被 `rate_limit_ms=50`
   放行一次（5Hz < 20Hz）。导航意图是否需要豁免频率记账，
   否则 `INTENT_EXECUTED` 里会全是 MOVE_TO，淹没战斗统计。

---

## 六 · 一句话结论

**比上一轮估的便宜。** 坐标槽位、朝向字段、两道安全校验、
甚至「指名要去哪」的 validator 单测 —— 全都已经在位了。
真正要写的只有 `ActionType` 一个枚举值 + traits + executor 一个分支。

**但有两个必须先解决的问题**：
① 平面轴名 `x/y` vs `x/z` 全仓不一致（现有校验读不到，所以一直没暴露）；
② `PositionInfo.heading_deg` 与转向律要的窗口均值是否兼容。

这两个不定就接线，出来的会是「能跑但方向随机」的版本 ——
那比报错更难查。
