# `recognition/notes/` 索引

> 2026-10-12 建立。这个目录里的东西有个共同点：**都是「为什么当初这么写」的
> 答案，不是「现在该怎么做」的手册。** 想看当前状态，去源码和
> `signal_integration_roadmap.md`；想查某个决策的来龙去脉，来这里。

## 怎么读这个目录

| 你想知道 | 去读 |
|---|---|
| 现在该接哪个信号、按什么顺序 | [signal_integration_roadmap.md](signal_integration_roadmap.md) |
| **不打开游戏能验什么、怎么验** | [offline_verification_checklist.md](offline_verification_checklist.md) |
| 这次为什么这么定 / 哪里踩过坑 | [session_20261012_signals.md](session_20261012_signals.md) |
| 寻路怎么接、现在卡在哪 | [pathfind_integration_plan.md](pathfind_integration_plan.md) |
| **寻路接口契约（坐标/起点/目标/导航模式）** | [navigation_interface.md](navigation_interface.md) |
| **不开原神怎么验 Console 寻路（假脚本）** | [console_pathfind_offline_test.md](console_pathfind_offline_test.md) |
| **demo 视觉素材（架构图 / 时间轴 / 三档演讲词）** | [demo_assets/](demo_assets/README.md) |
| **回归基线 + 一键跑全部** | [regression_baseline.md](regression_baseline.md) ／ `run_all_tests.bat` |
| **性能基线（含「为什么算不出分布」）** | [performance_baseline.md](performance_baseline.md) |
| **代码质量扫描（未用导入 / 死代码 / 过时注释 / 重复）** | [quality_scan.md](quality_scan.md) |
| **本轮会话收尾（成果 / 现状 / 待决策 / 明天做什么）** | [session_20261013_wrapup.md](session_20261013_wrapup.md) |
| **demo 当天怎么准备、哪些能演哪些不能** | [demo_prep_pack.md](demo_prep_pack.md) |
| **对外怎么说（已验证 vs 未验证）** | [progress_report_for_promotion.md](progress_report_for_promotion.md) |
| **Console 提权 / UAC 怎么验的** | [console_elevation_test.md](console_elevation_test.md) |
| **数据里哪些是假的** | [fake_data_audit.md](fake_data_audit.md) |
| 我该怎么点路点 | [user_waypoints_guide.md](user_waypoints_guide.md) |
| 这些外部资产能不能用 | [LICENSING.md](LICENSING.md) |

---

## 索引

### 会话记录（时间序，最新在前）

| 文件 | 最后更新 | 用途 |
|---|---|---|
| [session_20261012_signals.md](session_20261012_signals.md) | 2026-10-12 | **信号接入准备 + 10-11 排查合并**。WGC 为何入选、实体检测为何哑火、PickMark 为何按位置记；`since()` 误判的教训、OCR 分类器真实数据结论、模板裁图实测标定、本轮修掉的 4 个 bug、死代码扫描取舍 |
| [session_20261010_wrapup.md](session_20261010_wrapup.md) | 2026-10-10 | 凌晨排查收尾。Autopilot 3.0 落地前的那一轮 |

### 方案与规划

| 文件 | 最后更新 | 用途 |
|---|---|---|
| [signal_integration_roadmap.md](signal_integration_roadmap.md) | 2026-10-12 | **常读**。当前 UNKNOWN 的 DSL 信号清单、分阶段计划、离线 vs 真机切分。配套守卫在 `tests/unit/test_rule_expansion.py` |
| [pathfind_integration_plan.md](pathfind_integration_plan.md) | 2026-10-11 | 寻路（e2e_pathfind）接入方案。`nav_stop_at_target` 仍是注释占位 |
| [companion_app_design.md](companion_app_design.md) | 2026-10-03 | 配套应用（Companion App）架构设计 |
| [navigation_interface.md](navigation_interface.md) | 2026-10-12 | **Console ↔ `e2e_pathfind` 的接口契约**。`NavMode` / `NavigationRequest` / `NavigationHandle`、路点文件 schema、停止语义、寻路脚本路径的三段优先级。⚠️ 含「未验证」清单 |
| [console_pathfind_offline_test.md](console_pathfind_offline_test.md) | 2026-10-12 | **不开原神验证 Console 寻路**。`fake_pathfind.py` 复刻真脚本契约；离线能证明什么 / 不能证明什么；两份镜像的漂移守卫 |

### 交付与对外

| 文件 | 最后更新 | 用途 |
|---|---|---|
| [demo_prep_pack.md](demo_prep_pack.md) | 2026-10-12 | **demo 前必读**。哪些能演哪些只能讲、三个演示场景、fallback 方案、演讲稿 |
| [progress_report_for_promotion.md](progress_report_for_promotion.md) | 2026-10-12 | 对外进度说法。配 `progress_report_for_promotion_visual.png` |
| [progress_report_for_promotion_visual.md](progress_report_for_promotion_visual.md) | 2026-10-12 | 上面那张图的说明（画的是什么、每块对应哪项已验证能力） |
| [console_elevation_test.md](console_elevation_test.md) | 2026-10-12 | Console 提权（UAC）验证记录与复现步骤 |

### 数据可信度

| 文件 | 最后更新 | 结论 |
|---|---|---|
| [fake_data_audit.md](fake_data_audit.md) | 2026-10-12 | 仓库里哪些数据是**假的/占位的**，用到时必须知道 |
| [event_analysis.md](event_analysis.md) | 2026-10-12 | **48 次运行 / 7091 条事件 / 22 种事件码**的统计。故障码趋势、罕见事件码、以及「改善」结论里的**基线陷阱** |
| [exception_audit.md](exception_audit.md) | 2026-10-13 | 静默失败审计。794 处 except 的分类 + **top 20 逐条精读结论**（含分类器自身 bug 的更正）+ `safety.py:249` 专项追踪。配套 `_audit_top20.md`（精读明细）、`_audit_scan.py`（可复现扫描） |

### 技术裁决（结论多为「此路不通」，同样重要）

| 文件 | 最后更新 | 结论 |
|---|---|---|
| [minimap_localization_verdict.md](minimap_localization_verdict.md) | 2026-10-01 | 小地图定位：ORB / SIFT **全部证伪** |
| [phase_correlation_report.md](phase_correlation_report.md) | 2026-10-01 | 相位相关测位移 —— ✅ 验证通过，是当前定位主力 |
| [scene_detector_report.md](scene_detector_report.md) | 2026-10-01 | 场景检测器（大世界 UI 检测器）完成报告。二值 WORLD/UNKNOWN |
| [teleport_contract.md](teleport_contract.md) | 2026-10-01 | 传送位置更新契约（三段全过才刷新）。⚠️ `autopilot/perception/teleport_ocr.py` **整个模块从未被 import** —— 契约已定，实现待接线 |

### 面向用户

| 文件 | 最后更新 | 用途 |
|---|---|---|
| [user_waypoints_guide.md](user_waypoints_guide.md) | 2026-10-08 | 用户点选路点 → live 寻路的操作指南 |
| [LICENSING.md](LICENSING.md) | 2026-10-02 | 外部资产来源与许可。`bgi_*.onnx` 是 GPL，不随包分发 |

---

## 维护约定

1. **结论必须标来源**：`真机实测` > `源码事实` > `推断`。本目录里
   没有「猜测」被写成「事实」。
2. **证伪的结论同样保留**（`minimap_localization_verdict.md`）——
   删掉它们，下一个人会把同样的路再走一遍。
3. **踩坑记录不要清理**。代码里那些「为什么删掉了 PrintWindow」的注释
   和本目录同源；2026-10-12 专门扫过一次并确认它们**不是**过时残留。
4. 新增文件请在本索引加一行，写清**用途**和**最后更新**。

## 未收录

- `<RECOGNITION_ROOT>\exports\benchmark\` —— 数据集与脚本产物，非人工笔记
- `plugin\plugins\genshin_game_buddy\docs\REPORT_2_0_*.md` —— 插件侧的历史变更报告
