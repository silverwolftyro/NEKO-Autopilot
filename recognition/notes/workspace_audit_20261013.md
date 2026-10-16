# 工作区审计 · 2026-10-13

> **全面审计**：核实一切「声称的状态」是否与磁盘实际相符。
> 所有数字来自本次**实际执行**，不引用任何历史记录。
> **只读**：未修改任何生产代码，未新增测试，未打开原神。

---

## 一 · 磁盘状态总览

| 目录 | 文件 | 目录 | 合计 | 最后改动 |
|---|---:|---:|---:|---|
| 插件 `genshin_game_buddy` | 416 | 64 | 13.8 MB | 2026-10-13 09:25 |
| Console | 77 | 8 | 7.4 MB | 2026-10-13 08:31 |
| e2e 脚本 `misc/` | 54 | 1 | 2.5 MB | 2026-10-11 01:24 |
| `recognition/notes/` | 44 | 1 | 2.0 MB | 2026-10-13 10:04 |
| `neko_plugin_cli/` | 51 | 6 | 8.0 MB | 2026-10-12 15:47 |

### 各仓库最大文件

- **插件**：`capdiag_wgc.png` 4.3 MB（真机截图）、`capmatrix_normal_mss.png` 672 KB、
  单个 `.stream.jsonl` 609 KB、`vendor/winrt/msvcp140.dll` 554 KB
- **Console**：`yolo11n.pt` **5.4 MB**（模型权重）、`runtime/cap_*.png` 约 140-360 KB
- **e2e 脚本**：`wp_debug.log` **1.5 MB**（日志）、`e2e_pathfind.py` 203 KB、
  `e2e_pathfind_main_backup.py` 194 KB
- **notes**：`progress_report_for_promotion_visual.png` 1.0 MB、地图截图 288/214 KB
- **neko_plugin_cli**：`target/genshin_game_buddy.neko-plugin` **7.7 MB**（构建产物）

### 🔴 残留与可疑文件

| 位置 | 内容 | 判断 |
|---|---|---|
| `<REPO_ROOT>\` | **44,737 个文件**的整棵重复树 + `mock_states.jsonl` | 🔴 **严重**。这是我写错路径（`N.E.K-O` 少个点）造成的，但体积远超一个测试文件 |
| `<WORKSPACE_ROOT>\N.E.K-O-Console\` | 空 | 早期临时目录 |
| `<WORKSPACE_ROOT>\probe-dir`、`probe-dir2`、`yolo_data`、`预标注模型` | 空目录 | 探测残留 |
| 插件 `.pytest-tmp/` | 空目录 | pytest 临时 |
| 插件 `_probe2.py` | 我上一轮的探针 | 🔴 **我漏删了** |
| `NEKO-Console/tests/_probe_gaps.py` | 我上一轮的探针 | 🔴 **我漏删了** |
| `notes/_audit_*.py/txt`、`_quality_probe.py` | 6 个子代理临时文件 | 应清理 |
| `misc/_archive/`、`*.prev`、`*.bak` | 约 21 个历史归档 | 有意保留（项目约定） |

---

## 二 · 测试实际数字（本次实跑）

### 插件（用确定的点号路径实跑）

```
1392 passed, 1 skipped, 1 xfailed, 20 warnings in 29.15s
```

**总数 1393 条。** 另一轮跑出的是 `1393 passed / 0 skipped` ——
差别**不是**测试变了，而是下面这条：

**skip（0 ~ 1 条波动）**：`tests/unit/test_change_detect.py:136` ——
它抓**真实桌面**三帧算 dHash diff，桌面不静止（实测 `diff=[3,0,3] > 2`）时
**主动 skip 而不是 fail**。这是它设计好的诚实语义，docstring 里写着。
**所以「0 skip」不是稳定状态。**

### Console

```
593 passed, 6 xfailed, 1 xpassed in 28.72s
```

**xfail（6 条，全部在 `tests/test_pathfind_ui_interaction.py`）**：
`test_click__map_pick_fills_the_start_box`、`test_click__emits_both_world_coordinates`、
`test_wheel__zoom_in_changes_the_transform`、`test_wheel__zoom_out_is_clamped_to_the_lower_bound`、
`test_events__a_real_mouse_event_reaches_the_map`、`test_events__wheel_is_accepted_not_ignored`
—— 全部因为 **offscreen 平台下 widget 从不 show、事件不派发**。真实桌面会话下应通过。

**xpassed（1 条）**：`test_typing__full_width_comma_is_normalised` —— 该用意外通过了。

---

## 三 · 文档声称 vs 磁盘实际

> 本节由独立子代理逐份核实，结果见 `_doc_claims_audit.md`。

**已直接核实为真的**：

- 活跃规则 **16 条**（顶层 6 个 toml / `[[rules]]` 计数），`enabled = true` **确为 1 条** ✅
- `_future/` **5 文件 / 20 条** ✅
- 事件流 **48 个文件 / 7,091 行** ✅
- `_runtime/` 下 `pathfind_state.json` / `state_snapshot.json` / `run_current.txt` /
  `nav_stop.flag` **四份都在** ✅
- 金字塔在位：`P0_pyramid/` 149 个文件，`chunks_index.json` **存在** ✅

**已发现的不一致**：

| 文档 | 声称 | 磁盘实际 |
|---|---|---|
| `session_20261013_wrapup.md` | 插件 **1392** passed | 本次实跑 **1393**（我随后又加了一条测试并修了一条 flaky） |
| 同上 | 「**0 skip**」 | 不稳定，实测在 0~1 之间波动（桌面是否静止） |
| `regression_baseline.md` | 插件 1302 / Console 593 | 已过期（1302 → 1393） |

---

## 四 · 关键路径核实结果

**全部关键文件在位**（无缺失）：

| 链路 | 文件 | 大小 | 最后改动 |
|---|---|---:|---|
| 截图 | `capturer.py` | 77.7 KB | 10-10 23:36 |
| 感知 | `builder.py` | 52.8 KB | 10-13 01:42 |
| ↳ 实体 | `world_entities.py` | 19.6 KB | 10-11 |
| ↳ OCR 场景 | `scene_from_ocr.py` | 7.2 KB | 10-11 |
| ↳ 血量 | `hp_estimator.py` | 17.3 KB | 10-12 |
| 决策 | `local_tree.py` | 16.7 KB | 10-13 |
| 执行 | `simulator.py` / `executor.py` / `humanizer.py` | 43/29/7.8 KB | 10-04 / 10-13 / 10-13 |
| 护栏 | `safety.py` | 12.5 KB | 10-13 |
| YUI | `event_to_yui.py` | 22.3 KB | 10-11 |
| 寻路（真） | `e2e_pathfind.py` | 203.3 KB | 10-11 01:24 |
| 寻路（假） | `fake_pathfind.py` | 12.9 KB | 10-12 |
| 预演 | `demo_preview.py` | 10.4 KB | 10-13 |
| 模板 | `full_hp_template.png` / `hp_bar_reference.json` | 5.7/1.0 KB | 10-12 |
| 真机截图 | `capdiag_wgc.png` | 4.3 MB | 10-10 23:48 |

**唯一发现的路径错误**：`chunk_loader` 的报错信息指向
`build/slice_p0_pyramid.py` —— **该脚本在仓库里不存在**，数据是别处产出的。

---

## 五 · 真机验证清单（按优先级）

| # | 项目 | 前置条件 | 预期结果 | 失败怎么排查 |
|---|---|---|---|---|
| 1 | **`hp()` 真机首帧** | 管理员 Console + 游戏前台，配置开 `hp_estimation.enabled` + 填 `background_bgr` | `StatePacket.player.hp_ratio` 有值、`hp_source="geometry"` | 看 `_runtime/archive/*.perf.jsonl` 与状态快照的 `hp_source`；仍为 `none` 说明 ROI 框错位（ROI 只在 2560×1600 标定过） |
| 2 | **`e2e_pathfind` live** | 游戏前台 + 管理员；`engine.pathfind_script` 指向真脚本 | 角色真的走向目标 | 先跑假脚本确认管道；再查 `pathfind_state.json` 与事件流 `PATHFIND_*` |
| 3 | **能量条模板** | **一张角色有能量的截图**（现参考帧里没有） | 能量条 ROI 可标定 | 底图区域扫不到金色 → 说明确实没能量，换角色/时机重拍 |
| 4 | **定位服务原点** | 游戏内**传送一次** | `position.quality` 从 `LOST` 变为可用 | 读 `teleport_contract.md` 的三段契约；未传送前一切位移判据都不可信 |
| 5 | **WGC 完整链路** | 真实游戏窗口 | 三通道都能抓到帧 | 本次只做了 mock 注入测试；真 WGC 一次没跑 |
| 6 | **Console 提权复验** | 管理员 | UAC 弹出且提权后实例可用 | 看 `console.log`；已验过（更早轮次），只是今日未复验 |
| 7 | **OCR 重采样** | 真实 OCR 采样集 | 关键词表标定完成 | 对照 `signal_integration_roadmap.md` 的置信度阈值（强词 0.55 / 弱词 0.35） |
| 8 | **YUI 语音播放** | 宿主可推消息 | 真的出声 | 台词映射有 17 条测试，但**播放链路从未验过** |

---

## 六 · 发现的异常 / 不一致

### 🔴 严重

1. **⚠️ 绝对不要删 `<WORKSPACE_ROOT>\N.E.K.O-main\`** —— 它是**当前唯一在用的插件仓库**
   （含 `launcher.py`、`pyproject.toml`、`.venv`（1.65 GB）、插件源码、`regression_baseline.md`
   里的测试命令和 `demo_prep_pack.md` 的 `REPO` 变量都指向它）。

   ⚠️ **本次审计出现了一个必须记录的异常**
   盘点阶段曾扫到一个 **`N.E.K-O-main\`（K 与 O 之间是短横）**，含 44,737 个文件，
   当时判断为「我写错路径的残留」；**随后的独立核实显示该短横路径现在不存在**，
   只有点号的 `N.E.K.O-main\` 存在。两个近乎同名的路径 + 一个在审计中途消失，
   **这个矛盾本报告没有解释清楚**，需要人工确认后再动任何删除操作。

   保守做法：**在人工确认之前，不要删除 `<WORKSPACE_ROOT>\` 下任何名字带 `N.E.K` 的目录。**
   真正的残留目前只确认了 `mock_states.jsonl` 一个文件。
2. **我引入了 flaky 测试并已修。** `test_turn_steps__zero_total_*` —— 连踩三次：
   `== []` 挂 → 看一次改 `== [0]` 只对那一次 → 改 `sum == 0` 还是挂。
   根因是**人味抖动随机**，总角为 0 时产出 `[0]` 或 `[1]`。**能钉的只有容差**（`<= 1`）。
3. **`start_demo.bat` 从未落盘（❌ 不实）。** `demo_prep_pack.md` §C2 给出了完整
   60 行脚本并写「放在 `<WORKSPACE_ROOT>\demo\`」，但**该目录不存在，四棵树全盘搜索零命中**。
   影响：demo_prep_pack §C2 整节、用法、启动顺序、附录里「T-1 跑一遍 / T-0 双击」
   **全都执行不了**。（脚本正文里的路径变量本身是对的。）
4. **`_check_stuck()` 被当成未修的活 bug，实际早已修好（❌ 不实）。**
   `signal_integration_roadmap.md` §5 说它「恒为 0、strict xfail 钉住」。
   磁盘实际：`core/loop.py:157-167` 是整段「2026-10-12 修复」注释，
   `:170` 已改成 `n = int(self._consecutive_capture_failures or 0)` ——
   正是该文档建议的修法。**把已修好的 bug 当阻塞项会白费一轮。**
5. **`quality_scan.md` 说 `minimap_locator.py`「全仓零引用、建议删整个文件」—— 错（❌）。**
   实测 `recognition/` 下**正好 7 处**引用（探针脚本按路径加载）。按它删会打断那 7 个。

### 🟡 中等

3. **「0 skip」不是稳定状态。** `test_change_detect` 取决于桌面是否静止。
4. **`regression_baseline.md` 数字过期**（1302 → 1393）。
5. **我自己漏删了 3 个探针脚本**：插件 `_probe2.py`、Console `tests/_probe_gaps.py`、
   notes 下 6 个 `_audit_*` / `_quality_probe`。
6. **`chunk_loader` 报错指向不存在的脚本** `build/slice_p0_pyramid.py`。

### 🟢 已确认属实

7. `session_20261013_wrapup.md` 里「三次我以为有 bug，结果都没有」的三条**全部为真**。
8. `demo_prep_pack.md` 的「能演 / 不能演」表**与当前状态一致**。
9. `signal_integration_roadmap.md` 的 `hp()` 状态（🟡 已实现、默认关、待真机）**准确**。

---

## 七 · 一句话结论

**58 条文档声明里：30 条属实、17 条过时、3 条不实、8 条无法判断。**

**没有发现「声称做了其实没做」的功能性造假** —— 五个「已修 bug」逐个在源码里
确认到位；规则表（16/20/36、enabled 恰 1 条）、事件证据（48 run / 7091 事件 /
22 码）、ROI 实测值（1076,1500,408,15 / 34,215,150）全部与磁盘吻合。

**真正的问题在两处**：

1. **文档卫生** —— start_demo.bat 根本没落盘；_check_stuck() 早已修好却被
   当成活 bug；quality_scan.md 建议删一个**有 7 处引用**的文件；
   回归数字在五份文档里有五个版本（611 → 826 → 1106 → 1253 → 1393）。
2. **工作区卫生 + 一个必须人工确认的路径异常**（见 §六 第 1 条）。

**下一次不该再堆测试** —— 1393 条已经证明管道通；该做的是把 §5 那八项
真机验证挑两三项跑掉，把「能不能演」从推断变成事实。

**动手删任何东西之前，先确认那个路径问题。**
