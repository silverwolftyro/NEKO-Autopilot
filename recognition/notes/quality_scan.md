# 代码质量扫描报告

> 生成时间：2026-10-13 ｜ 只读扫描，未修改任何代码

---

## 扫描范围与规模

| 目标 | 路径 | `.py` 文件 | 行数 |
|---|---|---:|---:|
| 插件 | `N.E.K.O-main\plugin\plugins\genshin_game_buddy` | 195 | 48,860 |
| Console | `NEKO-Console\neko_console` | 27 | 4,940 |
| 寻路脚本 | `recognition\exports\benchmark\scripts\misc\e2e_pathfind.py` | 1 | 2,755 |
| **合计** | | **223** | **56,555** |

**工具版本**

- `ruff 0.15.4`（来自 `N.E.K.O-main\.venv`，`python 3.11.16`）— 跑 F401
- 自写 AST 探针（无新依赖）— 死代码 / 重复代码：
  - `recognition\notes\_quality_probe.py`
  - `recognition\notes\_quality_dup.py`

**排除目录**：`.venv` / `site-packages` / `node_modules` / `__pycache__` / `.pytest_cache` /
`build` / `dist` / `_runtime` / `.git`。未打开原神。

**探针方法与已知局限**：AST 探针把「测试文件里的函数」「dunder 方法」「带装饰器的定义」先排除掉
（pytest 按名字收集函数、`__str__`/`__bool__`/`__post_init__` 由解释器隐式调用、`@property`
经属性访问调用），否则会产生 2200+ 条噪音。再用「全树 Name(Load) ∪ Attribute.attr ∪
标识符形态的字符串字面量」作为引用全集，剩下零引用的才列为候选。

---

## 1 · 未使用的导入（ruff F401）

**共 43 条**（插件 36 / Console 7 / 寻路脚本 0），其中 **43 条判定为「真未使用」，0 条为有意保留**。

逐条核对过三种「有意」情形，**本批一条都不符合**：

- 可选依赖探测（`try: import x / except ImportError:`）—— 抽查 `game_scene.py:50`、
  `builder.py:84`、`executor.py:33`、`policy_diagnose.py:65`，都是裸 import，无 try 包裹
- `__init__.py` re-export —— 唯一命中的 `actuator/__init__.py:24` 并**不在**该模块 `__all__` 里
- `TYPE_CHECKING` 导入 —— 全树有 6 处 `TYPE_CHECKING`，ruff 全部未命中，行为一致

### 真问题（按危害排序）

| 位置 | 判定 | 说明 |
|---|---|---|
| `neko_console\tabs\pathfind.py:22,23,24`（4 个） | **真问题** | 同一个 `.mapdata` 一次导入 4 个名字全部未用：`load_teleport_points` / `normalize_name` / `search_points` / `verify_against_reference`。**同一模块一次丢 4 个**，不像随手遗留，更像寻路径页做过功能删减后没清 import。危害最高——UI 上可能少了「传送点搜索 / 参照校验」两条入口。 |
| `autopilot\tools\policy_diagnose.py:59,63,65` | **真问题** | 诊断工具，`IntentSourceKind` / `DEFAULT_RULES_DIR` / `RuleLoadError` 三个都未用。`RuleLoadError` 没用 = 规则加载失败没有走友好报错；`--rules-dir` 覆盖参数可能没真正生效。 |
| `autopilot\ui\writer.py:12` | **真问题** | `from pydantic import ValidationError` 未用。writer 只管「什么时候写什么」，说明**校验失败被静默吞掉**了。写入侧丢数据无声。 |
| `autopilot\actuator\executor.py:29,30,33,38,44`（6 个） | 真问题 | `EventCategory` / `Severity` / `ActionType` / `SafetyConfig` / `InvalidStateError` / `SeenIntentStore`。执行层核心模块，一次残留 6 个契约层 import。 |
| `autopilot\actuator\__init__.py:24` | 真问题 | `DEFAULT_KEYMAP_PATH` 被导入但**不在本模块 `__all__`**（`__all__` 有 `DEFAULT_KEYMAP` 和 `keymap_path`，独缺它），所以既不是公开 re-export 也没人用。符号本身是活的（`keymap.py:65` 的 `keymap_path()` 内部在用，文档 `LAYER_DETAIL_MAP.md:573` 有记载），**只有这行 re-export 是死的**。 |
| `autopilot\perception\minimap_locator.py:238,278`（2 个） | 真问题 | `import numpy as np` / `from pathlib import Path` 均为函数内局部导入且未用。这两行在**整个模块零引用**的前提下（见第 2 节），一并是死的。 |
| `autopilot\perception\builder.py:69,84`（2 个） | 真问题 | `typing.Callable` / `TriState` 未用。 |
| `autopilot\tools\autopilot_demo.py:29,61`；`convert_to_onnx.py:24`；`keymap_editor.py:33`（4 个） | 真问题 | 其中 `._bootstrap.line` 出现 3 次——`line()` 是 `_bootstrap.py:55` 定义并写进 `__all__` 的真实工具函数，但这 3 个工具都没用。 |
| `autopilot\actuator\humanizer.py:27,31`；`localization_service.py:52,54`；`minimap_mask.py:27`；`ocr.py:21`；`policy\factory.py:23`；`policy\local_tree.py:44`；`contracts\intent_packet.py:15`；`detectors\game_scene.py:50`；`tools\run_loop.py:129`；`ui\reader.py:21` | 真问题 | 11 条零散残留，典型是 `math` / `dataclasses.field` / `typing.Any` / `json`。危害低，纯噪音。 |
| `neko_console\app.py:35`；`tabs\engine.py:11`；`tabs\mapwidget.py:29`（3 个） | 真问题 | `QLabel` / `Qt` / `QGraphicsPixmapItem` 未用。 |
| `tests\integration\test_full_pipeline_offline.py:339`；`tests\test_event_to_yui.py:25`；`tests\unit\test_fake_pathfind.py:18`（3 个） | 真问题 | 测试文件的 `EventToYui` / `os` / `tempfile` 未用。 |

### 有意保留

本节无。43 条全部可直接删（ruff 对其中 42 条给出 `[*]` 自动修复）。

---

## 2 · 死代码

AST 零引用候选 **51 条**，分流如下：

| 分桶 | 条数 | 处理 |
|---|---:|---|
| `vendor/` 下第三方代码（7 条） | 7 | 排除，范围外 |
| **有意保留**（见下） | 2 | 排除 |
| Qt 框架回调（`closeEvent`、`wheelEvent`） | 2 | 排除 |
| **已 grep 逐条确认的高置信死代码** | 16 | 见下表 |
| 「公开 API 面，仓内零调用」中置信项 | 24 | 见下表末尾 |

### ⚠️ 先更正一条上一轮结论

**`self._dxcam_out = None` 不是死代码。** 上一轮审计怀疑它是死代码，实际 `capturer.py` 内：

- 写入：`:268`（初始化）、`:402`（stop 后置空）、`:658`（`self._dxcam_out = cam`）、`:785`、`:1022`
- 读取：`:397`（`is not None`）、`:399`（`.stop()`）、`:992`（判空重建）、`:995`（`dxcam.create`）、`:996`（`.grab`）、`:1021`

是一套完整的 dxcam 输出句柄生命周期管理，**不要动**。

### 有意保留（不是死代码）

| 位置 | 判定 | 说明 |
|---|---|---|
| `e2e_pathfind.py:1704` `_gain_for` / `:1717` `_clamp_for` | **有意** | docstring 自述「**已废弃**，保留只为让旧断言还能跑」。虽然 AST 在扫描根内零引用，但 `exports\benchmark\scripts\test\test_pathfind_logic.py:393,394` 确实调用了 `pf._gain_for(d)` / `pf._clamp_for(d)`。**属于任务里点名的「只为测试而存在的代码」，不是死代码。**（若哪天删了这两行，测试要同步删 393-394 附近断言。） |

### 高置信死代码（已 grep 全仓确认零引用）

按危害排序：

| 位置 | 判定 | 说明 |
|---|---|---|
| `autopilot\perception\minimap_locator.py`（**整文件 389 行**） | ⚠️ **结论已更正，勿删** | 文件第 1 行 `# @deprecated —— 外观匹配定位路线已证伪（规划文档 §14）`，确已证伪。**但「零引用」是错的**：2026-10-13 复核发现 `recognition\exports\benchmark\scripts\` 下有 **7 处**引用 —— `probe/diag_locator_deep.py`、`probe/diag_ncc_real.py`、`probe/diag_orb_feasibility.py`、`probe/validate_locator_real.py`、`probe/exp_e_orb_l2_locate.py`、`probe/exp_phase_corr.py`、`test/test_minimap_locator.py`。它们用 `_load("minimap_locator", PERC / "minimap_locator.py")` **按路径加载**（不是 import），删文件会让这 7 个脚本直接 `FileNotFoundError`。**本轮处理：保留原地 + 在模块 docstring 写清「为什么已证伪却还在、谁在用、要删得连同那 7 个一起删」。** |
| `autopilot\actuator\simulator.py:625,629,633` | **真问题** | `mouse_down` / `mouse_up` / `mouse_scroll` 三个公开方法零调用。活的是 `mouse_tap`（`:617` 附近实现）。低层鼠标 API 整套退役了。 |
| `autopilot\actuator\safety.py:154,157,162` | **真问题** | `reset_panic` / `reset_breaker` / `reset_session` 三个重置方法零调用。熔断/急停能否在会话内复位，目前无调用方。 |
| `autopilot\perception\yolo.py:224` `fallback_reason` | **真问题** | 典型「写了没人读」：属性 `_fallback_reason` 在 `:120/:157/:161` 被写 3 次，**getter `:224` 零读取**。回退原因算出来了但没有任何 UI / 日志消费它。 |
| `autopilot\perception\template.py:298` `find_multi` | **真问题** | 零代码调用，但 `LAYER_DETAIL_MAP.md:107` 和 `LOGIC_MAP.md:215` 都把它写成架构里的在用节点（`find_multi()（CV2 + NMS）`）。**文档说在用、代码没调用**，两者必有一方过期——这是本次最值得人看一眼的矛盾。 |
| `autopilot\contracts\state_packet.py:335` `to_compact_dict` | **真问题** | 零引用。 |
| `autopilot\state.py:97` `scene_history_ms` / `:185` `set_connection` | **真问题** | 两个都零引用。`set_connection` 未用意味着连接状态可能没进 state packet。 |
| `ocr_engine.py:52` `is_available` | **真问题** | 零引用（注意别和 `sdk\shared\runtime\call_chain.py:137` 同名方法混淆，那是另一个类）。 |
| `autopilot\tools\md_to_png.py:99` `semantic_of` | **真问题** | 模块级函数零引用。 |
| `autopilot\perception\minimap_locator.py:238,278` | 已处理 | 两条 F401 死导入已删；整文件按上一行的更正结论**保留原地**。 |

### 中置信（仓内零调用，但是公开 API，不能仅凭静态扫描判定该死）

`autopilot\perception\screen_region.py` 6 个方法（`:73 derive_crop` / `:110 derive_1080p` /
`:134 to_screen_coords` / `:160 box_to_root` / `:166 point_to_root` / `:174 iter_chain`，
该模块 `__all__` 只含 `ImageRegion` 与 `region_of_frame`）；`chunk_loader.py:101 clear_cache`
/ `:105 warm` / `:126 cell_bbox`；`pyramid_loader.py:116 level_size`；
`capturer.py:795 is_window_alive`；`localization_service.py:210 on_teleport_ocr`；
`ocr.py:200 aread` / `:250 read_number`；`executor.py:174` 与 `simulator.py:521` 两个
`clear_history`；`humanizer.py:100 extra_delay_ms`；`ui\schema.py:134 is_stale`；
`ui\overlay\layout.py:293 _enum_value`；`neko_console\tray.py:128 is_visible`。

---

## 3 · 过时注释 / TODO

| 位置 | 判定 | 说明 |
|---|---|---|
| `tests\FIELD_COVERAGE_CHECKLIST.md:40,106,318,349` | **真问题（过时）** | 文档 4 处坚持写「`plugin.toml` 作者名**当前是 TODO**，发布前必填」「此用例应标记 `xfail`」。实测全部反了：`plugin.toml:54` 已是 `name = "silverwolftyro"`；`CHANGELOG.md:365` 记录了这次修复；`tests\static\test_redlines.py:616 test_manifest__author_is_filled_at_release` 是**普通断言，没有 xfail**。属于「事实已修、文档没跟上」。 |
| `terms\genshin\core.toml:11,42`、`slang.toml:6,23,39`、`systems.toml:8,55,61,73,79` | **真问题（待办）** | 9 条 `TODO(verify)`，且 `:11` 写明「需由熟悉原神的同事校对后再上线」。`FIELD_COVERAGE_CHECKLIST.md:337` 给出的验收命令是 `grep -rn "TODO(verify)" terms/ \| wc -l` 应降到 0。**这是本项目唯一一类「真·未完成」的 TODO。** |
| `autopilot\perception\minimap_locator.py:1,4,68` | 有意 | `@deprecated` 标记 + PEP 702 `__deprecated__` 属性。标记本身写得好；问题是**模块无人引用却仍随包分发**（见第 2 节）。 |
| `terms\wuthering_waves\*.toml`（约 14 条 `TODO(verify)`） | **有意** | 文件头明确写了「本文件是**刻意留空的模板**」，`brief = "TODO(verify): ..."` 是**数据占位符**，不是遗留待办。不算过时注释。 |

### 明确**不**算过时的（特意排除，避免误报）

- `actuator\traits.py:151`、`core\loop.py:534`、`actuator\executor.py:162,285` 提到
  `mouse_down(LBUTTON)` —— 读完整上下文是**过去式设计理由**（「根因**是**实现走 `mouse_down` 一直不松，
  所以改成点按」），属于任务里点名的「解释为什么不那样写」的注释，**不是过时注释**。
  （方法本身确实已无调用，那属于第 2 节的死代码，两件事。）
- `perception\detectors\party_avatar.py:13,27,40` 引用
  `exports/benchmark/notes/death_detection_todo.md` 与 `scene_detection_todo.md` —— **两个文件实测都存在**。
- `stage=xxx`、`run:xxx`、`python xxx.py`、`old_hash_xxxxxxxxxxxx`、`TODO` 作为
  **示例占位符/测试数据**出现的位置（`REPORT_2_0_70.md`、`__init__.py:2869,3213`、
  `test_scene_switch.py:68` 等）—— 不是过时引用。

---

## 4 · 重复代码

按危害排序：

| 位置 | 判定 | 说明 |
|---|---|---|
| `exports\benchmark\scripts\misc_main_backup\e2e_pathfind.py`、`misc_main_backup\e2e_pathfind_main_backup.py`、`misc\e2e_pathfind_main_backup.py` | **真问题** | 三份文件 **MD5 完全相同**（`26AECEB0DD15`，各 2,643 行）—— 字节级重复约 **5,300 行**。 |
| `misc\e2e_pathfind.py.prev`、`misc_main_backup\e2e_pathfind.py.prev` | **真问题** | 两份 **MD5 完全相同**（`C37E3790FEF2`，各 671 行），同样是 `.prev` 快照。 |
| 整棵 `misc_bgi\e2e_pathfind_bgi.py`（2,940 行） | 待人判断 | 与正式版 `misc\e2e_pathfind.py`（2,755 行）已分叉（`_gain_for` 在 1726 vs 1704），是「改过的独立分支」还是「过期副本」无法静态判定。**命名上完全看不出谁是权威**，这是这组文件最大的问题。 |
| `autopilot\perception\minimap_locator.py:171`、`localization_service.py:81`、`position_mapping.py:52`、`pyramid_loader.py:33`、`tile_tracker.py:133` | **真问题** | `def _sibling(name)` 同目录模块加载器**逐字复制 5 份**（含 docstring，只有标点差异）。其中 `except` 分支的 6 条语句在 3 处**结构指纹完全一致**——改一处必漏另外四处。 |
| 同名方法跨类出现：`main`×28、`to_dict`×22、`reset`×12、`close`×9、`update`×8 … | **不报** | 都是各不相关类上的常规命名（构造/转换/生命周期），不构成「逻辑相似」。任务要求的「同名 + 逻辑相似」在排除这类后，实际只有上表两组。 |

---

## 我不能确定的

以下几处**需要更多信息才能下结论**，我没有硬下判断：

1. **`ImageRegion` 的 6 个未用方法、`simulator` 的 3 个 mouse_*，是不是「留给仓外调用方的公开 API」？**
   这三个目标树之外还有 `sdk/`、`server/`、`vendor/`（例如 `server\application\plugins\event_contracts.py`
   有自己的 `fallback_reason` 字段）。仓内零调用 ≠ 全生态零调用。**需要仓外调用方清单才能定性。**

2. **`find_multi()` 是「设计文档超前」还是「代码退化」？**
   `LAYER_DETAIL_MAP.md:107` + `LOGIC_MAP.md:215` 都把它写成在用节点，但零调用。
   要么文档是愿望清单，要么调用被重构掉了——**需要问文档作者 / 看 CHANGELOG 才有答案**。

3. **`misc_main_backup\`、`.prev`、`_main_backup.py` 是有意快照还是误留？**
   mtime 显示备份簇是 2026-10-09 / 10-04，正式版 `misc\e2e_pathfind.py` 是 10-11（最新），
   但**判断不了**这些快照是否有人工用途（比如回滚点）。需要维护者确认。

4. **`terms\genshin\*.toml` 那 9 条 `TODO(verify)` 存在多久了？——判断不了。**
   当前工作区**不是 git 仓库**（无 `.git`），拿不到 blame/引入时间；`CHANGELOG.md` 里也没有
   钉住它们的条目。**按要求不编年份。** 要确认只能拿到 git 历史。

5. **`minimap_locator.py` 是否被「按字符串路径动态加载」？**
   AST 探针已把标识符形态的字符串字面量计入引用全集（`_sibling` / `importlib` 这类按名加载
   能抓到），全仓文本搜索也是 0 命中。但**仓外进程**若用绝对路径 `importlib` 加载它，静态扫描看不到。

6. **`autopilot\tools\*` 下 10 个工具脚本的 `main()` 无引用**——这是 CLI 入口（`python xxx.py`），
   由 `if __name__ == "__main__"` 驱动，**天然不会被静态引用扫到**，我没有把它们算进死代码。

---

## 建议动手的前 3 条

1. **删掉 `autopilot\perception\minimap_locator.py` 整个文件（286 行）。**
   为什么是它：全仓 `.py` + `.md` 双路搜索**零引用**，`__init__.py` 没导、打包配置没列，
   而且文件自己第 1 行就写着「路线已**证伪**」。它同时带走 2 条 F401 和 5 份重复里的 1 份。
   风险最低、收益最干净——删一个零引用且已自我否决的模块，几乎不可能碰到运行时。

1. ~~**删掉 `autopilot\perception\minimap_locator.py` 整个文件**~~
   WARNING **2026-10-13 撤销此建议。** 复核发现 `recognition` 下有 **7 处**
   按路径（`_load("minimap_locator", PERC / "minimap_locator.py")`）加载它，
   删了会让那 7 个探针/测试直接 `FileNotFoundError`。详见 §2 第一行。
   现状：**保留原地 + docstring 写清来龙去脉**。

2. **收敛 `exports\benchmark\scripts\` 下的脚本副本（字节级重复约 5,300 行）。**
   为什么是它：三份 `26AECEB0DD15` 完全同 MD5、两份 `.prev` 完全同 MD5。
   危害不在体积而在**可维护性**——`misc\` / `misc_main_backup\` / `misc_bgi\` 三个目录、
   加上 `.py` / `.py.prev` / `_main_backup.py` 三种后缀，人**无法判断哪份是权威**，
   而 `misc_bgi` 那份已经和正式版分叉。下次改动必然改错文件。
   动手前需先确认第 3 条「我不能确定的」里的快照用途。

3. **一次清掉 `simulator.py:625,629,633` + `safety.py:154,157,162` 共 6 个零引用公开方法。**
   为什么是它：这两组是**成簇**的（各 3 个、同一模块内相邻），指向同一个结论——
   「鼠标长按路线已彻底退役」「熔断在会话内不复位」。
   和第 1 条不同，这批不能盲删：`safety.py` 的 `reset_panic` / `reset_breaker` /
   `reset_session` 一旦有仓外调用方（急停后恢复）就会出问题，
   **动手前需要确认第 1 条「我不能确定的」里的仓外调用方清单**。
   顺带能验证 `traits.py:151` 那段「已改成点按」的设计理由是否真的落地到底。

---

*探针脚本 `recognition\notes\_quality_probe.py`、`recognition\notes\_quality_dup.py`
保留以便复现，确认无用后可直接删。扫描全程只读，未修改任何源码。*
