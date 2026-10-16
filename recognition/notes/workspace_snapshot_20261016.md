# 工作区状态快照 · 2026-10-16

> 生成方式：**只读扫描**（未打开原神、未修改任何代码）。
> 依据：磁盘实测 + 本次实跑两套 pytest + 运行时事件流。
> 本文档覆盖到最后一笔工作 **2026-10-15 18:28**（`teleport_ocr.py`）。

---

## 一 · 目录结构

### 1.1 五个目标目录总览

| 目录 | 文件数 | 子目录 | 总量 | 最后修改 |
|---|---:|---:|---:|---|
| `plugin\plugins\genshin_game_buddy` | 788 | 164 | 38.44 MB | 2026-10-16 12:59（pytest 缓存） |
| `NEKO-Console` | 33387 | 3499 | 2184 MB | 2026-10-16 12:59（pytest 缓存） |
| `recognition` | 37422 | 2502 | 2446 MB | 2026-10-15 18:28 |
| `recognition\notes` | 40 | 2 | 1.86 MB | 2026-10-14 22:20 |
| `exports\benchmark\scripts\misc` | 103 | 4 | 3.34 MB | 2026-10-11 01:24 |

⚠️ 后三个目录的容量被 `.venv` / 数据集 / 模型权重主导，**不代表代码量**。
代码实际规模见 §三。

### 1.2 插件顶层（40 项）

| 目录 | 说明 |
|---|---|
| `autopilot/` | **引擎本体**，2026-10-14 22:13，含 11 个子包（见 §三） |
| `tests/` | 68 个测试文件、1216 个测试函数 |
| `vendor/` | 第三方依赖（93 文件，10-11 更新） |
| `ui/` | overlay UI（`overlay/app.py` 793 行为前端入口） |
| `tools/` | 顶层开发工具 |
| `i18n/` `terms/` `docs/` | 文案、术语、历史报告 |
| `autopilot/_runtime/` | **真机运行产物**（事件流 / 快照 / 性能采样 / 截图） |

顶层关键文件：`__init__.py`（169 KB，最大）、`event_to_yui.py`（22.8 KB）、
`screen_capture.py`（23.8 KB）、`CHANGELOG.md`（78 KB）、`plugin.toml`（12.5 KB）。

### 1.3 引擎 `autopilot/` 结构

| 子包 | 文件数 | 最后修改 | 说明 |
|---|---:|---|---|
| `perception/` | 47 | 10-15 17:14 | 截图 / 场景 / 实体 / OCR / 定位，最活跃 |
| `tools/` | 31 | 10-15 00:02 | 25 个 CLI 诊断脚本 |
| `actuator/` | 8 | 10-14 22:42 | 执行器 / 输入模拟 / 人味抖动 / 护栏 |
| `policy/` | 9 | 10-13 01:42 | 规则引擎 + 上下文 + DSL |
| `contracts/` | 4 | 10-14 22:37 | StatePacket / IntentPacket / Event |
| `core/` | 5 | 10-14 22:38 | 主循环 / 事件总线 / 运行时 |
| `ui/` | 12 | 10-13 08:32 | overlay 渲染与读写 |
| `config/` | 2 | 10-15 17:13 | 配置加载 + `defaults.toml` |
| `nav/` | 2 | 10-14 23:44 | `steer.py` 转向律（10-14 新建） |
| `rules/` | 12 | 10-11 23:35 | 规则 TOML + `_future/` |
| `detectors/`（在 perception 下） | 7 | 10-12 13:22 | E / Z 键、血条、队伍、槽位检测器 |

### 1.4 Console（`NEKO-Console`）

`neko_console/` 共 **27 个 .py / 4969 行**：

| 目录 | 行数 | 说明 |
|---|---:|---|
| 顶层 | 1554 | `app.py` 490（主窗口）、`bridge.py` 216、`elevation.py` 99、`main.py` 108 |
| `tabs/` | 2213 | 6 个 tab：pathfind 387、mapdata 428、rules 336、overlay 282、engine 176、mapwidget 178 |
| `channels/` | 1372 | 引擎事件 / 路径寻 / overlay / 规则 / 快照 |
| `config/` | 391 | `schema.py` 291 + `loader.py` 95 |

### 1.5 `recognition\exports\benchmark\scripts\misc`（37 项）

| 类别 | 代表文件 |
|---|---|
| **主脚本** | `e2e_pathfind.py` **2755 行**（最大）、`path_planner.py` 65595 B、`anchors_viewer.py` 63285 B |
| 诊断 | `diag_sprint.py`、`diag_llm_vision*.py`、`diag_env.py`、`diag_f12*.py` 共 6 个 |
| LLM | `ollama_client.py`、`ollama_vision_test.py` |
| 地形 | `terrain_planner.py`、`train_terrain.py`、`label_terrain.py` |
| 测试脚本 | `_test_compat.py` / `_test_fixes.py` / `_test_viewer.py` / `_test_nokeys.py` |
| **备份残留** | `e2e_pathfind.py.prev`（36 KB）、`e2e_pathfind_main_backup.py`（198 KB）、`uipi_check.py.bak` |
| 日志 | `wp_debug.log` **1.6 MB**、`ollama_test.log`（0 字节） |

---

## 二 · 文档索引

### 2.1 `recognition/notes/` 共 30 份 `.md`（不含 `INDEX.md` 自身）

| 文件 | 最后更新 | 用途 |
|---|---|---|
| `move_to_intent_design.md` | 10-14 22:20 | **`MOVE_TO` 意图设计草案**（只设计，未注册） |
| `arch_pathfind_overlap_analysis.md` | 10-14 22:04 | **引擎 × 寻路架构重叠分析**（只诊断，未改码） |
| `workspace_audit_20261013.md` | 10-13 10:17 | 工作区审计报告 |
| `demo_prep_pack.md` | 10-13 10:35 | demo 准备包（哪些能演、哪些只能讲） |
| `regression_baseline.md` | 10-13 10:35 | 回归基线数字 ⚠️ **已过期**（见 §2.3） |
| `quality_scan.md` | 10-13 10:34 | 代码质量扫描（未用导入 / 死代码） |
| `signal_integration_roadmap.md` | 10-13 10:33 | **常读**。信号接入路线图 |
| `session_20261013_wrapup.md` | 10-13 09:26 | 10-13 会话收尾 |
| `INDEX.md` | 10-13 01:46 | 本目录索引 ⚠️ **未收录 3 份新文档** |
| `exception_audit.md` | 10-13 00:57 | 静默失败审计（794 处 except） |
| `_audit_top20.md` | 10-13 00:53 | Top 20 精读明细 |
| `performance_baseline.md` | 10-13 00:29 | 性能基线 |
| `event_analysis.md` | 10-12 23:52 | 48 次运行 / 7091 事件 / 22 事件码统计 |
| `console_pathfind_offline_test.md` | 10-12 23:42 | 不开原神验证 Console 寻路 |
| `navigation_interface.md` | 10-12 19:49 | Console ↔ `e2e_pathfind` 接口契约 |
| `fake_data_audit.md` | 10-12 15:04 | 哪些数据是假的 |
| `console_elevation_test.md` | 10-12 15:00 | 提权（UAC）验证记录 |
| `progress_report_for_promotion_visual.md` | 10-12 14:02 | 汇报图说明 |
| `progress_report_for_promotion.md` | 10-12 13:56 | 对外进度说法 |
| `offline_verification_checklist.md` | 10-12 11:11 | **人工核验清单**（不开原神） |
| `session_20261012_signals.md` | 10-12 01:41 | 10-12 信号接入排查 |
| `pathfind_integration_plan.md` | 10-11 01:25 | 寻路接入方案 |
| `session_20261010_wrapup.md` | 10-10 23:09 | 10-10 收尾 |
| `user_waypoints_guide.md` | 10-08 23:31 | 用户点路点指南 |
| `companion_app_design.md` | 10-03 18:49 | 配套应用架构设计 |
| `LICENSING.md` | 10-02 18:21 | 资产许可（`bgi_*.onnx` 是 GPL） |
| `phase_correlation_report.md` | 10-01 23:04 | 相位相关定位 ✅ 验证通过 |
| `minimap_localization_verdict.md` | 10-01 22:30 | 小地图定位 ORB/SIFT **全部证伪** |
| `teleport_contract.md` | 10-01 20:25 | 传送契约 ⚠️ **结论已过期**（见 §2.3） |
| `scene_detector_report.md` | 10-01 19:07 | 场景检测器完成报告 |

### 2.2 `INDEX.md` 覆盖检查

**结论：索引存在 4 处缺口。**

| 缺失文档 | 说明 |
|---|---|
| `move_to_intent_design.md` | 10-14 新增，**未收录** |
| `arch_pathfind_overlap_analysis.md` | 10-14 新增，**未收录** |
| `workspace_audit_20261013.md` | 10-13 新增，**未收录** |
| `INDEX.md` 自身 | 正常，非缺陷 |

索引维护约定第 4 条要求「新增文件请在索引加一行」——最近三天**未执行**。

### 2.3 ⚠️ 已过期 / 与磁盘不符的文档（4 处）

| # | 文档声称 | 磁盘实际 | 影响 |
|---|---|---|---|
| 1 | `INDEX.md:74`：「`teleport_ocr.py` **整个模块从未被 import**」 | **已被 import**：`builder.py:926` `from autopilot.perception import teleport_ocr`，并在 `:927` 调 `recognize_teleport()` | **高**。会让人以为传送 OCR 未接线而重复开发 |
| 2 | `regression_baseline.md:12`：插件 **1392 passed** | 本次实测 **1560 passed**（+168） | 中。基线数字过期 |
| 3 | `session_20261013_wrapup.md` §3.2「真机全部未做」 | **10-14 21:54 有真机运行**（80.5 s、382 tick、WGC 通道），见 §六 | 中 |
| 4 | `move_to_intent_design.md` §二：「平面轴名 `x/y` vs `x/z` **待拍板**」 | **已在代码里决定并落地**：`loop.py:922` 注释「2026-10-14：`.z` → `.y`」，全仓统一为 `x/y` | 中。草案比代码旧，Q1 已无待决 |

另有 4 个 `recognition/` 根目录下的临时补丁脚本尚未清理：
`_append_tp_tests.py`、`_patch_tp_tests.py`、`_nav_bench.py`、`_patch_testmove.py`。

---

## 三 · 代码模块状态

插件引擎与 `recognition` 寻路脚本两处。路径前缀：
`P = N.E.K.O-main\N.E.K.O-main\plugin\plugins\genshin_game_buddy`

### 3.1 感知层

| 模块 | 行数 | 最后修改 | 关键类 / 函数 | 单测 |
|---|---:|---|---|---|
| `P\autopilot\perception\capturer.py` | 1459 | 10-10 23:36 | `Capturer`、`CaptureChannel`、`_WgcSession`、`_BitBltSession`、`ensure_dpi_awareness` | `test_capturer_wgc.py`(16)、`test_capturer_wgc_deep.py`(31)、`test_capture_and_providers.py`(22) |
| `P\autopilot\perception\builder.py` | 875 | **10-15 17:14** | `StateBuilder.build()`、`_feed_teleport_ocr()`、`_detect_entities()` | `test_state_builder.py`(49)、`test_hp_wiring.py`(37)、`test_locator_wiring.py`(17) |
| `P\autopilot\perception\scene_detector.py` | 197 | 10-01 20:25 | `SceneDetector.update()`、`SceneState` | `test_scene_and_merge.py`、`test_scene_switch.py` |
| `P\autopilot\perception\world_entities.py` | 304 | 10-14 21:14 | `WorldEntityDetector.detect()`、`probe_speed()` | `test_entity_thresholds.py`、`test_yolo_postprocess.py`(34) |
| `P\autopilot\perception\teleport_ocr.py` | 404 | **10-15 18:28** | `recognize_teleport()`、`match_teleport()`、`TeleportTarget` | `test_teleport_ocr.py`(41)、`test_teleport_ocr_wiring.py`(16) |
| `P\autopilot\perception\localization_service.py` | 326 | 10-13 01:42 | `LocalizationService.on_teleport_ocr()`、`LocalizationStateMachine` | `test_locator_injection.py`、`test_teleport_chunk_keyz.py`(31) |

### 3.2 决策层

| 模块 | 行数 | 最后修改 | 关键类 / 函数 | 单测 |
|---|---:|---|---|---|
| `P\autopilot\policy\local_tree.py` | 330 | 10-13 01:42 | `LocalPolicyTree.decide()`、`DecisionTrace` | `test_rule_expansion.py`(17) |
| `P\autopilot\policy\context.py` | 485 | 10-11 23:36 | `PolicyContext.from_state()`、`PolicySignals`、`has_nearby_enemy()` | `test_pick_mark.py`、`test_reflect_layer.py` |

### 3.3 执行层

| 模块 | 行数 | 最后修改 | 关键类 / 函数 | 单测 |
|---|---:|---|---|---|
| `P\autopilot\actuator\executor.py` | 670 | 10-14 23:45 | `IntentExecutor.execute()`、`_NavWindow`、`ExecutionResult` | `test_intent_validator.py`(33)、`test_switch_character_slots.py`(17) |
| `P\autopilot\actuator\simulator.py` | 870 | 10-04 16:18 | `InputSimulator`、`Win32Backend`、`DryRunBackend`、`VK`、`KeyId` | `test_mss_lock.py`、`test_keymap.py`(25) |
| `P\autopilot\actuator\humanizer.py` | 159 | 10-13 08:52 | `Humanizer.jitter_heading()`、`turn_steps()`、`apply_turn()` | `test_humanizer_roi_loader.py`(36) |

### 3.4 导航 / YUI / 寻路

| 模块 | 行数 | 最后修改 | 关键类 / 函数 | 单测 |
|---|---:|---|---|---|
| `P\autopilot\nav\steer.py` | 311 | 10-14 23:44 | `steer()`、`SteerPlan`、`turn_plan()`、`split_turn()`、`bearing_deg()` | **`test_nav_steer.py` 72 条，全绿** |
| `P\event_to_yui.py` | 326 | 10-11 22:52 | `EventToYui.read_new()`、`render()`、`push_one()` | `test_event_to_yui.py`(17)、`test_yui_speech_templates.py`(9) |
| `exports\...\misc\e2e_pathfind.py` | **2755** | 10-11 01:24 | `_NavAdvisor`、`_PanicWatcher`、`_split_turn()`、`_heading_deg()`、`_parse_args()` | 逻辑测试在 Console 侧：`test_e2e_pathfind_logic.py`(57) + `_branches.py`(45) = **102 条** |

⚠️ `recognition/` 下**没有任何测试基础设施**（无 `tests/`、无 conftest、无 runner），
`e2e_pathfind` 的测试全部寄居在 Console 套件。

### 3.5 Console `neko_console/`（27 文件 / 4969 行）

| 模块 | 行数 | 最后修改 | 说明 |
|---|---:|---|---|
| `app.py` | 490 | 10-13 01:42 | 主窗口与 tab 装配 |
| `tabs/pathfind.py` | 387 | 10-13 08:31 | 寻路 tab（调独立进程） |
| `tabs/mapdata.py` | 428 | 10-12 19:25 | 地图数据 tab |
| `tabs/rules.py` | 336 | 09-29 09:17 | 规则查看 tab |
| `channels/pathfind.py` | 265 | 10-12 19:32 | 寻路进程管理 + `nav_stop.flag` |
| `config/schema.py` | 291 | 10-12 19:32 | 配置 schema |
| `elevation.py` | 99 | 10-11 22:47 | UAC 提权 |

测试：23 个文件 / 539 个测试函数。

### 3.6 测试总量与最后结果

| 套件 | 测试文件 | 测试函数 | **本次实测结果** | 耗时 |
|---|---:|---:|---|---|
| 插件 | 68 | 1216 | **1560 passed, 1 skipped, 1 xfailed** | 32.30 s |
| Console | 23 | 539 | **593 passed, 6 xfailed, 1 xpassed** | 28.26 s |

两套全绿，退出码均为 0。20 条警告（`mss.mss` 与 `PLUGIN_CONFIG_ROOT` 弃用），
不影响结果。Console 的 6 个 xfailed 是 offscreen 环境下 widget 从不 show，**非间歇失败**。

---

## 四 · 测试实际数字

```
插件   : 1560 passed, 1 skipped, 1 xfailed, 20 warnings in 32.30s   (exit 0)
Console:  593 passed, 6 xfailed, 1 xpassed             in 28.26s   (exit 0)
```

与 10-13 基线对比：插件 **1392 → 1560（+168）**，Console **593 → 593（持平）**。

> 说明：插件测试函数 1216 个而通过 1560 条，差额来自参数化。
> Console 539 个函数 / 593 条，同样是参数化所致。

---

## 五 · 规则状态

### 5.1 总量

| 范围 | 文件 | 规则数 |
|---|---:|---:|
| **活跃目录**（`rules/*.toml`） | 6 | **16** |
| **`_future/`** | 5 | **20** |
| **合计** | 11 | **36** |

### 5.2 enabled 状态

| 范围 | `enabled=true` | `enabled=false` |
|---|---:|---:|
| 活跃目录 | **1** | 15 |
| `_future/` | 2 | 18 |

**唯一启用**：`genshin_combat.toml` → `world_attack_only_with_enemy`
（世界场景且有敌人时普通攻击）。

> 即：当前 16 条活跃规则里，**只有 1 条真正生效**。

### 5.3 `_future/` 待接入（20 条 / 5 文件）

| 文件 | 规则数 | 备注 |
|---|---:|---|
| `genshin_combat_signals_pending.toml` | 8 | 其中 2 条 `enabled=true`，**依赖尚未接线的信号** |
| `genshin_bag.toml` | 7 | 背包相关 |
| `genshin_nav.toml` | 4 | 导航 |
| `genshin_bag_advanced.toml` | 1 | |
| `genshin_world_signals_pending.toml` | 0 | **无 `rules` 键**，需补 `rules = []` 否则加载报错 |

### 5.4 最近改动的规则文件

全部集中在 **2026-10-11 23:26 ~ 23:50**：

| 文件 | 时间 |
|---|---|
| `genshin_combat_signals_pending.toml` | 10-11 23:50 |
| `genshin_combat.toml` | 10-11 23:50 |
| `PRIORITY_MAP.md` | 10-11 23:40 |
| `genshin_world.toml` / `genshin_combat_advanced.toml` / `genshin_recovery.toml` | 10-11 23:35 ~ 23:37 |
| `genshin_interaction.toml` / `genshin_team.toml` | 10-11 23:27 |
| `genshin_bag_advanced.toml` | 10-11 23:26 |

⚠️ 规则层已 **5 天无改动**，而引擎代码改到 10-15 —— 代码在推进，规则层停滞。

---

## 六 · 真机验证状态

### 6.1 ✅ 有真机验证记录

| 功能 | 证据 |
|---|---|
| **主循环 + 战斗攻击（最新）** | **10-14 21:54 真机运行**：80.5 s、382 tick、4.75 Hz、`capture=0` 失败、`perception_failures=0`、`policy_errors=0`、`tick_crashes=0`；执行 `NORMAL_ATTACK` **355 次，全部 outcome=OK**；抓取通道 **wgc** 持续在线；**ENEMY_DETECTED 实测到 0/2/1/1/1/0 个敌人**（实体检测真在跑） |
| 早期主链 | 48 次运行 / 7091 事件 / 1352 次按键（`event_analysis.md`） |
| 抓取矩阵 | 10-12 ~ 10-14 多通道截图产物（WGC / MSS / BitBlt / dxcam，含最小化与离屏） |
| Console 提权（UAC） | `console_elevation_test.md`，更早轮次已验 |
| 相位相关定位 | `phase_correlation_report.md` 验证通过 |
| 场景检测 | `scene_detector_report.md` 完成报告 |

### 6.2 ⚠️ 只有离线测试，未上真机

| 功能 | 说明 |
|---|---|
| `e2e_pathfind` **live** | 假脚本（`fake_pathfind.py`）+ 假截图全链路验过；**真脚本从未端到端跑通** |
| 寻路 DIRECT 真机 | 路径已修、假脚本已验，真机未验 |
| `MOVE_TO` 意图 | **仅设计**，枚举 / traits / executor 分支**均未实现** |
| `nav/steer.py` | 72 条单测全绿，但**从未接入执行路径** |
| Console 寻路 tab | 端到端离线测试通过（`test_pathfind_e2e_offline.py`） |
| `hp()` 血量 | 代码就绪、**默认关闭**，真机未跑过一帧 |
| YUI 播报 | `event_to_yui` 有单测，真机未接 |
| 传送 OCR 接线 | **10-15 刚接上**（`builder.py:926`），仅单测，**真机未验** |

### 6.3 ❌ 完全未验证

| 功能 | 说明 |
|---|---|
| 能量条 / `resin` / `q_ready` / `e_ready` | 参考帧里**没有能量条**（底部条区金/蓝像素 = 0） |
| 编队切人（`onfield`） | `PlayerInfo.party` **恒为空**，契约无角色元素字段。`genshin_team.toml` 是**有说明的空文件** |
| 小地图特征定位 | ORB / SIFT **已证伪**（`minimap_localization_verdict.md`） |
| 监听式急停 | 全仓无 `RegisterHotKey` / `SetWindowsHookEx`；寻路侧靠独立线程 `_PanicWatcher` |

### 6.4 真机验证的一个关键发现

**`POSITION_CHANGED` 与 `PATHFIND_ARRIVED` 从未从引擎发出过。**

`core/loop.py:926-935` 的注释写明：`PositionInfo.quality` 的取值是
`GOOD / DEGRADED / LOST`，但该分支的判据要求 `== "TRACKING"`——
`TRACKING` 是 `LocalizationStateMachine` 的**状态名**，不是 quality。
**该条件恒为 False**，所以位置类业务事件一直是哑的。

10-14 已把 `.z` → `.y` 统一（该行以前恒为 None），但 **quality 判据未改**，
所以事件**仍然不发**。这是已知且**刻意保留**的决策，不是本次新引入的 bug。

---

## 七 · 当前最紧急的 3 件事

### 1. 🔴 提交代码 —— 18 天的工作没有版本控制

- 插件最后一次提交：**2026-09-27 17:29**（`7ee8e91`）
- 当前工作树：**205 个未跟踪文件**（`autopilot/` 140 个、`tests/` 42 个）
  ＋ 11 个已修改文件 ＋ 1 个删除
- `<WORKSPACE_ROOT>`、`recognition`、`NEKO-Console`、`demo` **均无 `.git`**
  （Console 与 recognition 完全没有版本控制）

> 一次磁盘故障 / 误删 / 环境重建，2026-09-27 之后全部工作将不可恢复。
> 这是当前**唯一无法靠写代码弥补**的风险。

### 2. 🟠 修文档与磁盘不符的 4 处断言

`teleport_ocr` 已接线（INDEX 仍称「从未 import」）、
插件测试 1560（基线仍写 1392）、
10-14 有真机运行（收尾文档仍称「真机全部未做」）、
轴名已统一 `x/y`（设计草案仍称「待拍板」）。

> 这个项目反复踩的坑是**「声称做了其实没做」的镜像——「声称没做其实做了」**。
> 后者同样会导致重复开发和错误决策。`INDEX.md` 已 3 天未收录新文档。

### 3. 🟡 把 10-15 接线的传送 OCR 和 nav/steer 推上真机

当前 16 条活跃规则只有 1 条生效，`MOVE_TO` 未注册，
`nav/steer.py` 72 条单测全绿但**从未接入执行路径**，
传送 OCR 刚接线（10-15）仅单测。

> 离线能证明管道通，证明不了水干净。下一步的价值在真机，不在更多单测。

---

## 八 · 一句话结论

**骨架完整、双测试全绿（插件 1560 / Console 593）、10-14 真机跑通 80 秒战斗循环，
但 18 天工作未提交、文档落后磁盘 4 处断言、活跃规则 16 条只启用 1 条——
当前最该做的不是继续写功能，而是先把成果固化（提交）、把文档对齐磁盘、
再把已就位的能力推上真机。**

---

### 附：本次扫描的边界

- 全程只读；**未打开原神**；**未修改任何代码或配置**（唯一写入是本文件）。
- pytest 会写 `.pytest_cache`，属正常副产物。
- `NEKO-Console` / `recognition` 的容量数字含 `.venv` 与数据集，不代表代码规模。
- 真机结论基于 `_runtime/` 既有产物，**本次未产生新的真机运行**。
