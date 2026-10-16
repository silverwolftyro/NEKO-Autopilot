# 信号接入路线图

> 2026-10-12 更新（第一阶段已落地）。
> 目的：把「今天恒 UNKNOWN 的 DSL 信号」变成能出值的，每接一个就解锁一批规则。
>
> 配套：`autopilot/rules/_future/` 里躺着 **20 条规则**在等这些信号；
> `autopilot/rules/PRIORITY_MAP.md` 记录了活跃规则的实际状态。

---

## 0 · 先看现状：36 条规则里只有 16 条跑得起来

拆分依据是 `tests/unit/test_rule_expansion.py::test_active_dir__has_no_unreachable_rules`
——它扫活跃目录里每条规则的 `when`，找出依赖「今天恒 UNKNOWN 信号」的。
20 条命中，全部已挪进 `_future/`。

| | 数量 | 位置 |
|---|---|---|
| 活跃（今天能触发） | **16** | `autopilot/rules/*.toml` |
| 待接入（结构合法但永不触发） | **20** | `autopilot/rules/_future/`（加载器不递归扫） |
| 合计 | 36 | |

活跃目录里唯一 `enabled = true` 的是 `world_attack_only_with_enemy`。
其余 15 条默认关闭 —— 等真机逐条验证，不批量开。

**判定的硬标准**（不是「感觉会触发」，是逐条比对信号可用性）：

| 判定 | 依据 |
|---|---|
| 死 | `when` 引用了 §1 里任一「恒 UNKNOWN」信号 |
| 死 | `when` 拿 `scene()` 比一个二值检测器**永不产出**的值（BAG_INVENTORY / MAP_BIG / COMBAT / COMBAT_RESULT） |
| 死 | `when` 是字面量 `false`（占位规则，2 条） |

> ⚠️ 上一版这里写的是「15 活跃 / 21 待接入」。差的 1 条是 `combat_settle_on_enter`
> —— 它依赖 `since()`，本轮查明 `since()` **一直是真值**（见下），已挪回活跃目录。

---

## 1 · 信号清单（2026-10-12 实测状态）

### ✅ 已接入

| 信号 | 数据源 | 状态 |
|---|---|---|
| `enemy_count()` | 实体检测计数，缺 YOLO 时退回 `enemy_bar_count()`（血条检出） | **已接入**，3 条 combat 规则改用它 |
| `since()` | `StateBuilder.build()` 调 `_track_scene_enter()` 写 `scene.entered_ms`（`builder.py:592`） | **已接入**，2026-10-11 查明此前一直在设值 |
| `has_nearby_enemy/chest/ore/drops` | 实体检测 | 已接入 |
| `nearby_entity_count` / `nearby_count` | 实体检测 + 距离分档 | 已接入 |
| `enemy_bar_count` | 血条 ROI 检出 | 已接入（⚠️ 一敌可能多血条，是**近似**） |
| `has_locked_chest` | 宝箱状态分类 | 已接入 |
| `nearest_group_px` | 实体检测像素距离 | 已接入（⚠️ 120px 阈值**未标定**） |

### 🟡 离线已完成，**还没接主流程**

| 信号 | 离线性做到哪 | 差什么 |
|---|---|---|
| `scene()` 多值化（OCR 路径） | `autopilot/perception/scene_from_ocr.py`：`classify_scene_from_text()` 纯函数 + `SceneOcrRefiner` 去抖包装，14 条单测（正样本 10/10、弱词防误判 4/4） | 需要**真实 OCR 采样**标定关键词表；且 `builder._resolve_scene_binary()` 现在是纯二值，接入要改那里 |
| `pos_quality()` / `pos_usable()` | 注入点**早就存在**（`StateBuilder(locator=)`、`_locate()` 完整降级链、`_locator_guard` 熔断），本轮补了 12 条单测覆盖降级链 | `_stack.build_perception` **还没传 locator**；且 `LocalizationService` 初始 `UNINITIALIZED`，要先传送建立原点 |

> **OCR 分类器的设计取舍**：强词 0.55 / 弱词 0.35，`min_confidence = 0.55`。
> **宁可回落不可误判** —— 把 WORLD 误判成 BAG_INVENTORY 会让引擎在野外疯狂按 B 键，
> 这类误判的代价远大于「判不出来」。

### 🟡 已实现（**默认关闭**），待真机验证

| 信号 | 离线做到哪 | 差什么 |
|---|---|---|
| `hp()` | `perception/hp_estimator.py`（纯几何投影，只依赖 numpy）+ `StateBuilder` 接线 + `[autopilot.hp_estimation]` 配置（**默认 `enabled = false`**）。真机截图实测：ROI `(1076,1500,408,15)`、填充 BGR `(34,215,150)`、条上叠 `17640/17640` 白字占 14.3%。106 条单测 | ① **没在真机上跑过一帧**（全离线）；② ROI 依赖分辨率，只有 2560x1600 一份实测；③ 满血/空血两档内部纯色，没有 `background_bgr` 时返回 UNKNOWN |

### ❌ 仍恒 UNKNOWN（无数据源）

| 信号 | 数据源在哪 | 难度 | 能解锁 |
|---|---|---|---|
| `energy()` / `q_ready()` | 能量条几何投影。⚠️ **参考帧（`capdiag_wgc.png`）里根本没有能量条** —— 底部条区金/蓝像素为 0 | 需要一张**角色有能量**的截图才能标定 | 2 条 |
| `e_ready()` | 需要技能 CD 跟踪结构 | 中等 | 1 条 |
| `resin()` | 原粹树脂在**地图界面**右上角，不在大世界画面 | 复杂（要开地图 UI） | 1 条 |
| `onfield()` / `slot()` | 编队信息在**角色界面** | 复杂 | 1 条（编队专属） |
| `has_enemy()` / `target_locked()` | 可由实体检测推（与 `has_nearby_enemy()` 等价） | **简单** | 0 条现有规则，值得对齐语义 |
| `pos_quality()` / `pos_usable()` | 真实定位服务（见上方 🟡） | 复杂（两环） | 4 条 nav |

> 这 6 个里 `q_ready` / `e_ready` / `has_enemy` / `target_locked` / `onfield` / `slot`
> **连理想帧都没有** —— 不是「没接」，是**根本没有数据源**。

---

## 2 · 分阶段计划（已推进）

### ✅ 第一阶段（2026-10-11 完）—— 验证这条路走得通

- ✅ `since()`：**查明早已接通**。上一轮用缺 `entered_ms` 的合成 `StatePacket` 探测，
  误判成「恒 UNKNOWN」并据此把 `combat_settle_on_enter` 错搬进 `_future/`。已修正。
  *教训：探针夹具必须逐字段复刻真实状态，否则会把「夹具缺字段」误判成「生产没接」。*
- ✅ OCR 场景分类器（离线，不接主流程）
- ✅ 定位注入接线（离线）+ 12 条单测
- ✅ `core/loop.py` 45 条 / `builder.py` 53 条单测（见 §5）

### ▶ 第二阶段（下一步，收益最大）

**场景识别多值化** —— 10 条规则的最大单一来源。

- 已有：OCR 路径的分类器（离线可测）
- 仍缺：① 真实 OCR 采样标定关键词表 ② `builder._resolve_scene_binary()` 接上分类器
  ③ 7 类分类器模型（`perception/detectors/game_scene.py` 现 162 行空壳）
- 解锁：**10 条**（8 bag + 1 nav_map + 1 combat_result）
- 风险：误触发。**必须配套置信度阈值和 `enabled=false` 逐条开。**

### 第三阶段

**定位服务接入**（`pos_*` 的两环）

- ① `_stack.py` 传 `locator=`（**注入点已就绪，只差这一行**）
- ② 定位服务初始 `UNINITIALIZED`，需要一次传送建立原点
- 解锁：4 条 nav，且**这是「走到某坐标」这条路的前提**

### 第四阶段 · `hp()` 已接上（默认关），`energy()` 仍在等一张截图

**`hp()`（2026-10-12 落地，默认关闭）**

- ✅ `perception/hp_estimator.py`：纯几何投影，只依赖 numpy
- ✅ `StateBuilder(hp_estimation=...)` 接线；**未注入时 `hp_ratio` 恒 None**，
  与接入前完全一致
- ✅ `_stack.py` 接配置；`[autopilot.hp_estimation] enabled = false` 默认关
- ✅ 真机截图实测 ROI `(1076,1500,408,15)`（比例存储，换分辨率自动换算）
- ❌ **没在真机上跑过一帧** —— 全部离线

> ★ 一个反直觉但重要的结论：**部分血量不需要任何额外参数就能测准**
> （内部有填充/空白分界）。只有**恰好满血或空血**时内部是一整片纯色，
> 与「一块纯色补丁」像素上无法区分，此时返回 UNKNOWN 而不是猜。
> 也就是说：规则 `hp() < 0.35` 在濒死时照样能触发，只是「满血确认」拿不到值。

**`energy()` / `e_ready()`（缺数据，不是缺代码）**

- `energy()`：需要一张**角色有能量**的截图才能标定 ROI 与颜色。
  现有参考帧里没有能量条（底部条区金/蓝像素 = 0）。
  ⚠️ 更要紧的是：那天上有 **42 万**蓝色像素铺满整屏 —— 任何「按颜色找
  能量条」的检测器在野外场景都会把**天空**认成能量条。必须像血条那样
  先定 ROI、再判比例，不能靠颜色全局扫。
- `e_ready()`：除能量条外还需要**技能 CD 跟踪**结构，目前没有。

---

## 3 · 离线 vs 真机（本轮最实用的切分）

| 类别 | 内容 |
|---|---|
| **纯离线可做** | DSL 信号的实现与单测、状态机、熔断降级链、事件节流、OCR 文字→标签的映射与防误判、规则可达性守卫、**条形几何投影（`hp_estimator`）** |
| **必须真机** | **`hp()` 的首次真机运行**、能量条/血条 ROI 在**用户自己分辨率**下的标定（仓库里那份是 2560x1600 实测）、阈值标定（`nearest_group_px` 的 120px、PickMark 的 8s）、WGC 真机截图验证、`enemy_bar_count` 的敌人数近似度 |
| **必须真机 + 需用户授权** | 定位服务原点建立（传送）、Console 提权（`elevation.py` 的 UAC 弹窗与提权后实例启动） |

---

## 4 · 优先级排序（按「解锁规则数 / 难度」）

```
 1. 场景多值化（OCR 路径已铺好）  复杂  10 条  ← 收益最大
 2. 定位服务接线                 简单   4 条  ← 只差 _stack 那一行 + 真机建原点
 3. hp() 真机跑一次               中等   3 条  ← 代码已就绪（默认关），只差真机
 4. energy() 真机 + 重拍有能量的帧 中等   2 条  ← 参考帧里没有能量条
 5. e_ready()                    中等   1 条
 5. resin()/onfield()            复杂   2 条  ← 要求开 UI 面板
 6. has_enemy()/target_locked()  简单   0 条  ← 仅语义对齐
```

**不要按「规则条数」倒着做。** 场景多值化一次解锁 10 条，但那 10 条
（背包/地图/结算）在实际 demo 场景里**几乎不会被用到**——它们需要玩家主动去开那些界面。
真正影响日常的是第四阶段的战斗细则（hp/energy），可它依赖用户裁模板图。

---

## 5 · 本轮补测试时挖出的生产 bug

### ✅ `_check_stuck()` 恒为 0 —— 卡住播报从来没发出去过（**已修**，2026-10-12）

> **2026-10-13 更正**：本节原先写成 🔴 活 bug，实际**早已修好**。
> 核实见 `workspace_audit_20261013.md` §六.4。下面是当时的原始记录，
> 保留是为了说明「为什么当时没修」，不是当前的待办。

**当时** `core/loop.py:154`：

```python
n = int(getattr(self.ctx.stats, "consecutive_capture_failures", 0) or 0)
```

**`LoopStats` 上没有 `consecutive_capture_failures` 这个字段。**
真实计数器是 `CoreLoop` 实例上的 `self._consecutive_capture_failures`
（`loop.py:125/194/318/576/598`，在 `_perceive()` 里维护）。

`getattr(..., 0)` 的默认值让 `n` 永远为 0，于是：

- `if n >= 3 and n > self._biz_last_stuck` —— **永不成立**，`PATHFIND_STUCK` 发不出去
- `if n == 0:` 分支会进，但 `self._biz_last_stuck` 也是 0（只能在第一个分支里被抬高），
  所以 `ESCAPE_SUCCESS` **同样发不出去**

**影响**：YUI 的「卡住了」和「走出来了」两条语音播报是死的。

**为什么当时没有直接修**：本轮约束是「不改引擎核心逻辑」。
已用 strict xfail 钉在 `tests/unit/test_core_loop.py::test_check_stuck__should_*`，
另有一条 `test_check_stuck__bug_is_the_field_not_the_logic` 证明**逻辑本身是通的**。

**✅ 实际已修（2026-10-12）**：`core/loop.py:157-167` 是一整段「2026-10-12 修复」
注释，`:170` 已改成 `n = int(self._consecutive_capture_failures or 0)` ——
正是本节建议的写法。**这一项已从待办中移除。**

---

## 6 · 一条方法论

规则表清理（36 → 16 活跃）暴露的真问题不是「规则不够多」，
而是**规则表里大部分成员从来没被执行过，且没有任何机制会发现这件事**。

新增的守卫就是为了让它再也不能悄悄发生：

| 守卫 | 守什么 |
|---|---|
| `test_active_dir__has_no_unreachable_rules` | 活跃目录里不许有「结构合法但永远不触发」的规则 |
| `test_future_dir__is_not_loaded` | `_future/` 一旦被加载器递归扫到，20 条死规则会**原地复活且不报错** |

第二条尤其重要：`rule_loader` 现在是 `d.glob("*.toml")`（非递归），
**哪天有人改成 `rglob`，整个分离就静默失效了**。

补 `core/loop.py` / `builder.py` 测试这一轮又验证了一次：
上一轮「这两个文件深度依赖 YOLO/ONNX、无法测试」的结论是**错的** ——
`RuntimeContext` 和 `StateBuilder.__init__` 的依赖**全部是可注入且默认可空**的，
本轮零改造就补了 98 条测试。**「测不了」和「没去看」经常长得一样。**
