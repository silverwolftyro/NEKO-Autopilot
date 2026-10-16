# 引擎 × 寻路：架构重叠分析

> 2026-10-14 ｜ **只诊断，未改任何代码**
> 结论先行：用户提出的「两套感知」**与磁盘实际不符** —— 寻路已经在复用引擎的感知与执行原语。
> 真正重叠的是**编排骨架 / 安全 / 决策**，而其中「安全」还是**有据可依的刻意分叉**。

---

## 一 · 重叠清单

### 1.1 其实**没有**重叠的部分（已在复用）

`e2e_pathfind.py` 不是自带一套感知栈，它直接 import 引擎模块：

| 环节 | 用的是引擎的 | 出处 |
|---|---|---|
| 截图 | `Capturer` | `e2e_pathfind.py:2315, 2562` |
| 感知 | `StateBuilder(entity_detector=None)` | `:2314, 2553` |
| 定位 | `LocalizationService` + `TileTracker` | `:2552, 2557` |
| 按键 | `InputSimulator` / `Win32Backend` / `DryRunBackend` / `VK` | `:75, 2320, 2554` |

⚠️ 所以「两套感知」这个判断需要修正。**`entity_detector=None` 是刻意关掉的**，
不是另起一套。

### 1.2 真正重叠的部分

| # | 重叠项 | 引擎侧 | 寻路侧 | 后果 |
|---|---|---|---|---|
| R1 | **tick 主循环骨架** | `core/loop.py` 的 grab→phase→decide→send | `e2e_pathfind.py:2920+` 同样的四段 | 两套节奏、两套状态机 |
| R2 | **事件流写入** | `RUN_*` / `INTENT_*` / `SCENE_CHANGED` | `PATHFIND_START/ARRIVED/STUCK` | 同一 `.stream.jsonl`，两套语义 |
| R3 | **状态文件** | `state_snapshot.json` | `pathfind_state.json`（overlay 读） | 两份「当前在干什么」 |
| R4 | **停止信号** | `CoreLoop._stop_event`（进程内） | `nav_stop.flag`（文件跨进程） | 两套停止语义 |
| R5 | **急停** | `SafetyGuard.panic_active`（**拉取式属性**） | `_PanicWatcher`（**独立监听线程**） | 见 §1.3 |
| R6 | **转向分段** | `Humanizer` 的人味抖动 | `_split_turn` / `_turn_plan`（几何） | 两套「怎么转」 |
| R7 | **按键注入** | `InputSimulator` / `Win32Backend` | **同一个** | ★ **这就是「抢键鼠」冲突的根源** |

### 1.3 R5 是**刻意分叉**，不是疏忽

`e2e_pathfind.py:471-479` 的注释把理由写得很清楚：

- `SafetyGuard.panic_active` 是个**属性**，每次访问才去问 `GetAsyncKeyState`
- 全仓库**没有** `RegisterHotKey`、没有 `SetWindowsHookEx`、**没有监听线程** —— 纯拉取式
- 寻路脚本**根本不创建 SafetyGuard**，所以 F12 在那里一个字节都不生效
- 且主循环 5Hz（200ms/帧）追不上一次快速点按

→ 合并时**不能简单删掉 `_PanicWatcher`**，除非先给 SafetyGuard 加监听式急停。

---

## 二 · 各自独有的部分

**只有寻路有**：

- 转向到目标角的几何（`_split_turn` / `_turn_plan` / heading 估计）
- 路点游标 `_WaypointCursor`（`goal()` / `pop_reached` / `pop_abandoned`）
- LLM 路径规划（`path_planner`，可换）
- 传送点解析（`--target-tp` / `tp.json`）
- `pathfind_state.json`（overlay 正在读）

**只有引擎有**：

- 场景判定 / 世界实体检测 / OCR
- 规则引擎（DSL + 6 个规则文件 / 16 条活跃）
- `SafetyGuard`（熔断 / 急停 / 频率 / 会话上限）
- `Humanizer`（人味抖动）
- 快照归档 + 性能采样（2026-10-13 新增）

---

## 三 · 合并路径评估

### 3.1 前置事实：引擎**没有**坐标级导航意图

现有 22 个 `ActionType` 里，导航相关只有 `MOVE_FORWARD` / `MOVE_BACK` /
`MOVE_LEFT` / `MOVE_RIGHT` / `SPRINT` / `JUMP` —— **全是相对动作**。

**没有 `MOVE_TO(x, z)` 之类带目标点的意图。**

→ 所以「寻路变成一个决策模块」的**第一步不是搬代码，是加意图类型**。

### 3.2 寻路的核心逻辑能否抽成「产出意图的函数」

**能，而且相当干净。** 它的每 tick 决策就是：

> 给定当前位置与目标点 → 算出夹角 `d_deg` → 决定「转 / 走 / 到」

这正好是一个 `MOVE_TO(x, z)` 意图的语义。`executor` 里 `NORMAL_ATTACK`
已经有 `approach_by_walking=True` 的先例 —— **执行器本来就会「边走边转直到到位」**。
所以 `MOVE_TO` 可以复用同一套 sequence 机制，不必新写。

### 3.3 三个方案对比

| | **A · 完全合并** | **B · 意图桥** | **C · 库化** |
|---|---|---|---|
| 做法 | 寻路逻辑搬进 `autopilot/policy/pathfind.py`，产出 `MOVE_TO`，引擎执行 | 寻路仍是独立进程，但只吐意图，引擎执行 | 核心算法抽成库，两边都能调 |
| 新增意图类型 | 必须 | 必须 | **不需要** |
| 抢键鼠风险 | **消除**（单进程串行） | **仍在**（两进程仍共享 `Win32Backend`） | **仍在** |
| 急停 | 需先给 SafetyGuard 加监听式 | 同 A | 不解决 |
| 工作量 | 中~大：意图 + traits + 执行序列 + 循环编排 | 大：要加跨进程意图通道 | 小：只搬算法 |
| 风险 | 中。改执行路径 | **高**。跨进程意图同步比现在的 flag 复杂得多 | 低 |
| 收益 | 架构正确、`PATHFIND_*` 事件与主循环合并、护栏统一 | 编排统一 | 无架构改善 |

**推荐：C 先、A 后。**

- **C（库化）** 先做：把「目标点 → 转向/行走序列」抽成 `autopilot/nav/steer.py`，
  纯函数、无 IO、可单测。不动任何现有流程。风险最低。
- **拿到 C 之后 A 就变得很轻**：库的输出喂给新加的 `MOVE_TO` 意图，
  寻路的 tick 循环、事件写入、F12 线程**全部可以退役**，因为那些是编排不是算法。
- **B 不要做**：它保留了「两个进程抢同一套键鼠」这个根本问题，
  还要额外发明跨进程意图同步 —— 比现状复杂，且不解决真问题。

⚠️ **R5（急停分叉）是 A 的隐藏工作量**，需要进一步调查：
给 `SafetyGuard` 加监听式 F12 要动 `actuator/safety.py` 的公共契约，
影响引擎主循环的停机语义。

---

## 四 · Console 寻路 tab 怎么办

**目标形态**：给引擎发一个「去 X,Z」请求，而不是启动独立进程。

### 工作量评估：中等

| 步骤 | 改动 | 备注 |
|---|---|---|
| 1 | 新增 `MOVE_TO(x,z)` 意图 + traits | 前置项，无捷径 |
| 2 | 寻路 tab 改为「写请求 → 等引擎处理」 | 需要一个跨进程请求通道 |
| 3 | 请求通道 | ⚠️ **需要进一步调查**：现有 `commands.jsonl`（overlay→引擎）可复用吗？需确认其 schema 是否容得下坐标 |
| 4 | 删除 Console 的 `PathfindChannel` 进程管理 | 连带 `stop()` 三级逻辑、`nav_stop.flag` 联动一起退役 |
| 5 | overlay 改读 `state_snapshot.json` | 现在读 `pathfind_state.json` |

**过渡期建议**：在 A 落地前，Console tab 维持现状（起独立进程）。
**不要**现在改成「假装能发请求」—— 那会让 tab 看起来能用、实际发不出去，
和本项目反复踩的「声称做了其实没做」是同一类问题。

---

## 五 · 一句话结论

**用户的直觉方向对（两个进程抢键鼠确实是架构错误），但归因错了** ——
重复的不是感知与执行原语（那些早就共用了），而是**编排骨架、安全急停、决策入口**，
且「安全分叉」是**有文档依据的刻意选择**。

**最小正确路径是 C → A，不要做 B。**
第一步是把转向算法抽成无 IO 的库，而不是急着改架构。

---

## 六 · 需要进一步调查

1. `commands.jsonl` 能否承载 `MOVE_TO` 坐标请求（决定 Console tab 改造量）
2. 给 `SafetyGuard` 加监听式 F12 的影响面（决定 A 的真实成本）
3. `_PanicWatcher` 的释放按键顺序能否整体搬到 `SafetyGuard`
4. `PATHFIND_*` 四个事件码在 `_future/` 规则里有没有被引用（决定事件合并的破坏面）
