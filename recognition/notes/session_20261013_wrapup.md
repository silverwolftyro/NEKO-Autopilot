# 2026-10-13 收尾

> 本文档**覆盖并替换**了同名的早期版本（那份覆盖的是 10-12 晚 → 10-13 凌晨那一段）。
> 数字全部来自当日实测；**不写猜测**。

---

## 一、今日成果（按时间顺序）

> ⚠️ **今日没有任何一次真机验证。** 所有改动都是离线完成的。

| # | 改了什么 | 测试 | 真机 |
|---|---|---:|---|
| 1 | **修 `_slot_of` 真 bug**：角色切换被静默丢弃（`_char_slots` 从未被赋值） | +17 | ❌ |
| 2 | 补 `tab/pathfind` 的「坐标系数漂移」UI 告警（`verify_against_reference` 入口） | — | ❌ |
| 3 | **安全护栏 fail-open**：`core/loop.py:848` 急停检查异常改为**不放行 + 留痕** | +12 | ❌ |
| 4 | 全项目质量扫描 → `quality_scan.md`（223 文件 / 56,555 行） | — | ❌ |
| 5 | 清理 **42 条**确认死导入（另保留 10 条待决策的） | — | ❌ |
| 6 | 离线 demo 预演脚本 `tools/demo_preview.py` | — | ❌ |
| 7 | 补 `humanizer` / `roi_loader`（按键时长、ROI 换算） | +33 | ❌ |
| 8 | **修 `humanizer.extra_delay_ms(-100)`** 崩溃（钳位在 randint 之后） | +3 | ❌ |
| 9 | 补 `teleport_points` / `chunk_loader` / `key_z` | +24 | ❌ |
| 10 | 补 YUI 台词占位符契约（9 条） | +9 | ❌ |
| 11 | 截图矩阵重采 | — | ❌ **失败**（落盘 0 张，脚本自身校验判不通过） |

**新增代码**：`hp_estimator.py`、`fake_pathfind.py`、`extract_hp_template.py`、
`demo_preview.py`、3 张血条参考资产、2 张 demo SVG。
**新增测试文件**：插件 11 个 / Console 13 个。

### 本轮查出的真 bug / 隐患

| 发现 | 状态 |
|---|---|
| `_slot_of` 恒返回 None → **角色切换静默丢弃** | ✅ 已修 |
| `loop.py:848` 急停异常 → 零日志放行 | ✅ 已修（fail-closed） |
| `humanizer.extra_delay_ms(-100)` 抛 `ValueError` | ✅ 已修 |
| 快照写失败只 `logger.debug`（默认不可见） | ✅ 已提为 warning |
| `safety.py:249` 焦点校验 fail-open 且零日志 | ✅ 加日志（**行为未改**，待你拍板） |
| `minimap_locator.py` 286 行已证伪但被 7 个 recognition 探针按路径加载 | ⚠️ 保留原地 + 文档说明 |
| `chunk_loader` 报错指向不存在的 `build/slice_p0_pyramid.py` | ⚠️ 未改 |

### 三次「我以为有 bug，其实没有」

1. **`{count}` 断句** —— 生产代码正确，写方恒带 `count`；断句只出现在我手工造的
   **无 context 合成事件**里。**没有改模板**，改成了把两种行为钉成断言。
2. **`chunks_index.json` 缺失** —— 数据一直都在（`P0_pyramid/`），
   是我测试夹具把目录指到了空 `tmp_path`。修正后 **9 skip → 0 skip**。
3. **`_dxcam_out` 是死代码** —— 子代理推翻了我上一轮的结论：写 5 处、读 6 处，
   是完整的 dxcam 句柄生命周期。**不要动**。

---

## 二、当前状态

| 项 | 数字 |
|---|---|
| 插件回归 | **1392 passed / 0 skipped / 1 xfailed** |
| Console 回归 | **593 passed / 6 xfailed / 1 xpassed** |
| 活跃规则 | **16 条**（`enabled=true` 仅 1 条） |
| 主链 | 全通 —— 证据来自**更早轮次**真机记录（48 run / 7091 事件 / 1352 次按键） |
| 寻路 | 离线链路通（假脚本端到端 + 假截图全链路）；**真机一次没跑过** |
| demo 离线预演 | **5/5 通过**（六个 tab / overlay / bat 步骤 / YUI / preview） |
| 金字塔数据 | 在位（`P0_pyramid/`：`chunks_index.json` + `L0/` + L1~L4） |

⚠️ Console 那 6 个 xfailed 是 offscreen 平台下 widget 从不 show、
事件不派发（真实桌面会话下应通过）。**不是**间歇性失败。

---

## 三、待决策

### 3.1 需要你拍板

1. **那 10 条保留的 F401** —— 每条背后都是「该有但没有的功能」：
   - `neko_console/tabs/pathfind.py:22-24` →（已补坐标校验入口，剩 3 条纯残留）
   - `autopilot/tools/policy_diagnose.py` 3 条 → 实测 `--rules-dir` **生效**、
     友好报错**已在**，属纯残留，可直接删
   - `autopilot/ui/writer.py:12` → 已删死 import + 日志提级
   - `minimap_locator.py` 2 条 → 保留文件，仅删死 import
2. **`safety.py:249` 要不要改成 fail-closed** —— 它是 guard 五道门里**唯一**
   fail-open 的（①熔断 ④场景 ⑥频率全 fail-closed）。我倾向保持 fail-open
   （pywin32 抽风不该让引擎瘫掉），已补日志使其可观测。
3. **`minimap_locator.py`（286 行）** —— 已证伪、被 7 个 recognition 探针按路径
   加载。删 / 留原地（现状）/ 移到 `_deprecated/`（会让那 7 条失效）。
4. **`_runtime` 打包排除规则** —— 一直没加。
5. **`USE_PICKUP` 规则是否打开** —— 一直关着。
6. **残��目录 `<REPO_ROOT>\`** —— 我写错路径留下的，可恢复删除被
   安全策略拦下。**需要你手动删**。

### 3.2 需要真机（**全部未做**）

| 项 | 为什么离线做不了 |
|---|---|
| Console 提权复验 | 已验过（更早轮次），今日未复验 |
| `hp()` 真机首帧 | 代码就绪、默认关闭，**真机一次没跑** |
| OCR 重采样 | 需要真实 OCR 采样 |
| 能量条模板 | 参考帧里**没有能量条**（底部条区金/蓝像素 = 0）；需一张角色有能量的截图 |
| 定位服务原点 | 必须先在游戏里传送一次 |
| WGC 完整链路 | 今日只做了 **mock 注入**测试，真 WGC 一次没跑 |
| `e2e_pathfind` live | 假脚本验过管道，真脚本从未端到端跑通 |
| 寻路 DIRECT 真机 | 路径已修、假脚本已验，**真机未验** |

---

## 四、下一次能做的（按优先级）

1. **真机清单**（§3.2）—— 优先 `hp()` 首帧、`e2e_pathfind` live、能量条截图。
   这三项决定「能演什么」。
2. 删掉 `policy_diagnose.py` 那 3 条纯残留 import（已查实：功能都在）。
3. 补 `run_loop.py`（625 行，仍零覆盖）—— 它是集成入口。
4. 处理重复代码：`misc_main_backup/` 三份 MD5 相同的 2643 行脚本 +
   两份相同的 671 行 `.prev`（约 5300 行字节级重复）；
   `_sibling()` 在 `perception/` 下逐字复制 5 份。
5. 补剩余零覆盖模块：`teleport_points` 之外还有 40+ 个（多数是 `tools/` 下的
   CLI 脚本，优先级低）。
6. 更新 `regression_baseline.md` 的数字（本文档产出后它已过期）。

---

## 五、一句话结论

**能做**：看画面、判断场景、发现敌人、按键攻击、写事件流、宠物播报，
以及一整条**离线可验证**的决策与执行管道（含寻路的参数拼装、状态读写、
优雅停止、terminate 兜底）。

**不能承诺**：说出「你在地图哪里」、自动绕障寻路、读能量、编队切人、
读血量（代码就绪但真机未跑过一帧）、`q_ready` / `e_ready` / `resin`。

**今天最大的收获不是功能**，是三次「我以为有 bug / 缺数据，结果都没有」——
**离线能证明管道通，证明不了水干净**。下一步不该再堆测试，
而是把 §3.2 那八项真机验证挑几项跑掉。
