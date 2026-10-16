# 2026-10 · 信号接入与 Autopilot 3.0 排查笔记

> 离线整理，供后续接手的人查「为什么当初这么写」。
> 所有结论都标注了来源：**真机实测** > **源码事实** > **推断**。
> 本文件里没有「猜测」被写成「事实」。
>
> **合并说明**：本文件由两份合并而成，内容均保留，只是重新编排 ——
> 原 §1~§7 是 2026-10-11 的排查记录（原 `session_20261011_autopilot_3_notes.md`），
> §8 之后是 2026-10-12 的信号接入准备。
> 之所以合并成一份：两份讲的是同一条链路（截图 → 状态 → 规则 → 按键）
> 在同两天的连续推进，分开放会让人以为中间断过。

---

## 0 · 一句话

**10-11** 解决了两件事：**全屏黑帧**（加 WGC 通道）和**决策链哑火**（实体检测阈值）；
另外查清了两个「以为坏了其实是诊断错了」的坑（min_gap、去重）。

**10-12** 没有改生产行为，做的是「让下一阶段能开始」的准备：
把模板裁图的人工成本自动化、把真实 OCR 数据摆到场景分类器面前、
给四个零覆盖模块补上 124 条测试、清掉两个会被打进安装包的历史文件。
**新工具默认都不启用**，等真机验证。

---

# 第一部分 · 2026-10-11 Autopilot 3.0 三条链路的排查结论

## 1 · 截图：为什么最后选了 WGC

### 1.1 故障现场（真机实测）

run `20261011_000227` 的事件流：

```json
{"code": "BLACK_FRAME", "context": {"luma": 0.0}}
{"code": "PERCEPTION_DOWN", "message": "感知层判定本帧不可信（PERCEPTION_DOWN），已自动暂停"}
{"code": "CORE_PAUSED", "message": "主循环已暂停（感知层不健康）—— 感知继续，执行已停"}
```

`luma` **逐帧精确为 0.0** —— 不是「偏暗」，是每一个像素都是 0。
而 `capture_failures = 0`、`INTENT_EXECUTED` 一条都没有：引擎真心觉得自己在工作。

**读法提醒**：2655 条 BLACK_FRAME 不是 2655 次失败。引擎 tick 3 就
`PERCEPTION_DOWN` → `CORE_PAUSED`，之后是**暂停态下感知继续跑**，
每 tick 复述同一条。一个故障被播报了 2655 次。

### 1.2 三条老通道为什么都不行（真机实测）

| 通道 | 失败形态 | 关键 |
|---|---|---|
| DXcam | 抛 `Invalid Region: Region should be in 2560x1600` | 窗口越界；且那个 `except Exception` 分支原先**从不调** `_demote_channel()` |
| BitBlt | **静默返回全 0 的合法图** | `GetDC` 缓存句柄在显示模式切换后变僵尸，`BitBlt` 仍返回成功但什么都没写进那块零初始化的 DIB |
| mss | 全屏下抢不到 | 纯屏幕坐标，被遮挡/越界就废 |

**共同点：全部只在「无帧」和「异常」两种情况下失败，没有一种能识别「黑帧」。**
所以 `_init_channel` 的三条判定（`probe is None` / `probe.size == 0` / 根本不探测）
**全部**把一张 2560×1600 的全黑图判成「可用」。

### 1.3 为什么 WGC 能解决（真机实测）

冒烟测试（`autopilot/tools/wgc_smoke.py`），tkinter 阳性对照 + 原神：

```
阳性对照（tk 窗口）: 1442x1128  luma=116.3  颜色种类=12   → 管道通，且 draw_border=False 生效（无黄框）
原神全屏          : luma=124.4   颜色种类=618          → 真机可用
```

**选 WGC 的三个理由**：
1. 走 DWM 合成管道，抓的是 Windows 正在合成的表面 —— 硬件加速窗口是它的主场
2. **零注册表改动**（用户明确拒绝 `SwapEffectUpgradeEnable=0` 那条路）
3. 不需要自己碰 D3D11 COM vtable（BetterGI 走 ctypes/PInvoke 可以，但我们没必要）

### 1.4 WGC 的实现要点（全部来自真机踩坑，不是文档抄的）

`windows-capture` 2.0.1（Rust + PyO3，abi3-py39）三条铁律：

1. **必须 `start_free_threaded()`**。`start()` 会进 Win32 消息循环且不返回 —— 主循环直接卡死。
2. **`@capture.event` 按函数名分派**：函数名必须正好是 `on_frame_arrived` /
   `on_closed`，且两个 handler 都要在 `start_*` **之前**注册，少一个就是 `RuntimeError`。
3. **`frame.frame_buffer` 是零拷贝视图**，换帧即释放旧内存。
   `grab()` 必须返回 **拷贝**；而且 `convert_to_bgr()` 返回的是**新的 `Frame` 对象**，
   不是 ndarray —— 第一版写成数组直接 AttributeError。

另外：`draw_border=False` 和 `cursor_capture=False` 都**必须**显式关 ——
WGC 默认给捕获内容加一圈黄框，不关的话每帧带黄边，直接毁掉模板匹配和亮度统计。

### 1.5 WGC 的边界

- **抓不到真·独占全屏**（那种模式绕过 DWM）。本机原神注册表里
  `Screenmanager Is Fullscreen mode=1` + 分辨率 2560×1600 = 原生，
  推断是无边框全屏 —— 实测也印证了。
- 依赖 `windows-capture`，已 vendored 进 `vendor/`（abi3，通用）。

---

## 2 · 决策链：实体检测为什么哑火

### 2.1 故障现场（真机实测）

```json
{"code": "ENTITY_DETECTOR_UNAVAILABLE",
 "message": "auto：93ms 超阈值，保持关闭（DmlExecutionProvider）",
 "context": {"measured_ms": 93, "threshold_ms": 80.0, "reason": "over_budget"}}
```

**结论**：阈值 80 < 实测 93 → 整个世界实体检测被关 → `has_nearby_enemy()` 恒
UNKNOWN → WORLD 场景下**唯一三条能触发的规则全部哑火**。

已把阈值提到 120，并加了 `NEKO_ENTITY_THRESHOLD_MS` 环境变量覆盖 ——
因为**这个数按机器而异**（DirectML 空闲 63ms / 负载 93ms / CPU 路线 202ms）。

### 2.2 检出能力（真机实测，来自扫描数据集）

DirectML，580 帧：

```
enemy_identify 344   health_bar 249   drops 78
locked_chest    64   unmined_ore 47   ore 30   chest 15
ms: min 49.2  p50 63.0  p90 74.1  max 84.2
```

⚠️ **CPU 路线 p50 = 202.6ms**，在 120 阈值下会被判超预算整个关掉 ——
**必须走 DirectML**。

⚠️ 早期文档里写的「`ore` / `enemy_identify` 检出为 0」**只对 batch_b 成立**，
batch_a 和 DML 扫描里都有检出。别拿那条当结论。

---

## 3 · PickMark：为什么按位置记而不是按动作记

### 3.1 故障现场（真机实测，run `20261011_180732`，154.8 秒）

```
151  真实执行（USE_PICKUP）        ≈ 0.98 次/秒，平均间隔 ~1025ms
603  真实拒绝（min_gap 挡掉）
```

根因：宝箱被打开后 `has_nearby_chest()` **不消失**（检测框没刷新，或箱子的视觉
特征本来就跟「已开过」的一样），规则每 tick 重新瞄上同一个箱子。
`min_gap` 只是把它从 4.9 次/秒压到 1 次/秒，**压不掉重复**。

### 3.2 设计决策

| 候选 | 结论 | 依据 |
|---|---|---|
| `PolicyContext` 实例变量 | ❌ **行不通** | 本类每个 tick 都被 `from_state()` 重建，实例变量活不过一轮 |
| **`LocalPolicyTree`**（选了它） | ✅ | `create_policy_source()` 建一次、用到进程结束；`_last_fire_ms` / `_idle_streak` 都在那 |
| 外部文件 | ❌ | 5Hz 写文件，自找 IO 瓶颈 |

标记时机选「**规则命中**」而不是「执行成功」：命中就意味着「对某东西按了 F」，
效果等价；等执行层回报要改 `core/loop.py`（引擎核心）。

参数 `PICK_MARK_COOLDOWN_S = 8.0` / `PICK_MARK_RADIUS_PX = 120.0` **未标定**，
真机觉得不合适就直接调。

### 3.3 三条必须同时成立的守卫（`tests/unit/test_pick_mark.py`）

1. 同一实体连续在附近 → **只有第一次触发**
2. **冷却过后必须能再触发** —— 否则是永久失明，比原 bug 更糟
3. 坐标读不到 → **不过滤** —— 宁可多按一次，也不要因为坐标缺失永久压制

---

## 4 · 两个「以为坏了其实是诊断错了」的坑

### 4.1 min_gap 根本没坏

我一度断言「`min_gap_ms=800` 形同虚设，只拒了 31 次」。**错。**

- 事件总线 `publish()` 带 `dedupe_key`，**默认 5 秒去重窗口** ——
  同一 key 在窗口内只发一条，计数走 `suppressed`。31 = 154.8s ÷ 5s，
  正好是去重后的条数，**不是真实次数**。
- 真实次数在 `state_snapshot.json` 的 `raw_stats.intents_rejected` = **603**。
- 而且 151 次执行 / 154.8 秒 = 1025ms 平均间隔 ≈ 800ms 下限 + 开销，
  **完全吻合** —— min_gap 工作正常。

**教训**：判断「某闸门有没有生效」要看**计数器**，不是事件流。
事件流是为「人读」去重过的，用它做统计必然得出偏小的数。

### 4.2 `PolicyContext` 实例变量跨 tick 不存活

我一度以为可以把「最近拾取」直接挂在 `PolicyContext` 实例上。
**错** —— 它每个 tick 由 `decide()` 内部重建。这类跨 tick 的记忆必须挂在
长生命周期对象上（`LocalPolicyTree` / `CoreLoop` / `RuntimeContext`）。

---

## 5 · 事件流 → YUI 陪玩链路的三个坑

1. **目录分家**：适配器盯**插件安装副本**的 `_runtime`，引擎写**源码树**的
   `_runtime`。安装副本那份还是 `plugin build` 打的**快照**（每次打包刷新）。
   解法见 `__init__.py:_resolve_run_dir`（env 硬覆盖 > sidecar 硬覆盖 >
   探测候选按 mtime 竞争）。
   ⚠️ 踩过两次：先是环境变量在 Steam 继承链里没生效（要重启 Steam），
   二是 sidecar 混进 mtime 竞争被「打包快照的时间戳」打败。
   **教训：用户显式声明的东西要硬覆盖，不能让它和启发式规则竞争。**
2. **台词表漏洞**：`CORE_STARTED` 漏映射 → 走兜底 → 播出「游戏那边主循环已启动
   （目标 5 Hz ≈ 200ms/帧）」，把工程参数念给用户听。
3. **黑帧刷屏**：`BLACK_FRAME` 一个 run 2655 条（暂停态下感知继续跑）。
   `SKIP_CODES` 里它和 `INTENT_EXECUTED` 是必须的。

---

## 6 · 10-11 当时还没解决的

| 项 | 状态（10-11） | 10-12 进展 |
|---|---|---|
| 真·独占全屏 | WGC 抓不到。需要用户切无边框全屏 | 未动，仍需用户 |
| `position` 恒 None | `StateBuilder` 没接 `locator` | 注入点早已存在；补了 12 条单测覆盖降级链；`_stack` **仍未接线**（需真机建原点） |
| 寻路 | 没有「目标点」概念，`nav_stop_at_target` 是注释占位 | 未动，见 `pathfind_integration_plan.md` |
| `SCREENSHOT_FAIL` 等 8 个码 | 没有 YUI 台词，等用户定夺 | 未动 |
| 打包把 `_runtime` 整个收进去 | 16 MB 包里 73% 是运行时产物 | 未动，方案见 §9 |
| `since()` 恒 UNKNOWN（路线图判断） | 路线图这么写 | **误判**：查明 builder 一直在设 `entered_ms`，见 §8.1 |

---

## 7 · 待办：build 排除 `_runtime/`

方案（**未实施**，需要改 `neko_plugin_cli` 的打包规则）：

在打包 include/exclude 里排除 `payload/plugins/*/autopilot/_runtime/` 下：

- `*.png`（诊断截图，单个就 4.3 MB）
- `*.log`
- `*.stream.jsonl`（历史事件流，可选；保留最新一个对 overlay 有用）

理由：`_runtime/` 是**运行时输出目录**，内容随每轮运行变化，不该进分发包。
本机实测：`_runtime/` 16.06 MB → 清理后 6.08 MB；打包产物 16.22 MB 里
`_runtime/` 占 73%、PNG 占 65%。

---

# 第二部分 · 2026-10-12 深度信号接入准备

> 本轮**不改生产行为**，全部新东西默认不启用。

## 8 · `since()`：路线图的判断是错的

路线图（`signal_integration_roadmap.md`）把 `since()` 列为「几行代码就能解锁」
的信号，并据此把 `combat_settle_on_enter` 挪进 `_future/`。**这个判断基于一次
错误的探测**：

上一轮用**合成的 `StatePacket`**（缺 `entered_ms` 字段）去验证，
看到 `since()` 返回 UNKNOWN，就断定「生产没接」。实际上
`builder.py:592` 一直在写：

```python
scene["entered_ms"] = self._track_scene_enter(scene["id"], scene_changed)
```

已修正：规则挪回 `rules/genshin_combat.toml`，活跃规则 15 → **16**。

**教训（值得单独记住）**：**探针夹具必须逐字段复刻真实状态**，
否则会把「夹具缺字段」误判成「生产没接」。这类错误的方向性很恶劣 ——
它会让你去改本来就对的东西。

同样的教训在 10-12 又出现一次（见 §8.4 的 `position_from_state`）。

## 9 · OCR 场景分类器：真实数据验证的结论是「数据不够」

新工具 `autopilot/tools/ocr_scene_eval.py`，跑真实日志里的 OCR 采样：

| 指标 | 值 |
|---|---|
| 样本量 | **1431** 条（2026-10-10 / 10-11 两份日志，仅本插件） |
| 准确率 | **无法计算** |
| 触发率 | **0 / 1431 = 0.00%** |
| 桌面污染 | 516 条 = 36.1%，无一误报 |

**准确率为什么算不出来**：日志里每条采样旁边**没有场景标签**。
那两天跑的还是二值检测 —— `state=IN_GAME/UNKNOWN/AWAY` 是「游戏是否在前台」
不是场景，`scenes=N` 是计数，而 `BAG_INVENTORY/MAP_BIG/DIALOG/COMBAT`
全日志出现 **0 次**。没有 ground truth，硬凑的数字只会看起来精确、实际无意义。

**更要紧的是样本代表性**：36% 的「IN_GAME」样本其实抓到的是
**Windows 桌面 / 文件管理器** —— `capture_path=foreground` 抓的是**前台窗口**，
而前台是资源管理器，不是游戏。文本长这样：

```
N.E.KO-main > plugin > plugins > genshin-game-buddy
新建文件夹   迅雷   GitHub   DéSktop   PCL
```

关键词命中 10 次里有 7 次是这类误命中（`世界` ×3、`设置` ×3、`好友` ×1、
`选项` ×3，其中 `选项` 那条是唯一一条真游戏对话）。

**结论**：这批数据**验证不了分类器**，既没有正样本也没有有意义的负样本。
`BAG_INVENTORY` / `COMBAT_RESULT` / `SHOP` 三个场景**一次都没出现**。

**下一步**：重采一批 —— 进游戏后依次打开 背包 / 地图 / 对话 / 商店，
每个界面停 10 秒以上，让 `ocr_perceive` 跑到采样，再用本工具复跑。
阈值**未做任何调整**（`min_confidence=0.55`），按要求只报告。

## 10 · 模板裁图：把人工成本降到一条命令

新工具 `autopilot/tools/template_cropper.py`。原先 `hp()` / `energy()` 要手动裁
模板图，流程是「截图 → 自己找位置 → 画图工具裁 → 命名存盘 → 改 defaults.toml」。

**HSV 参数是真机实测的**，样本 `autopilot/_runtime/capdiag_wgc.png`
（WGC 实拍 2560×1600）：玩家血条 `y=[1500,1514] H=41 S=215 V=215`，
检出 `box=(1076,1500,408,15)` 覆盖率 98%，与人工逐行扫描一致。

由实测推导出三条**不可省**的约束（缺任一条就误报/漏报）：

1. **必须按色相门控** —— 夜石地面 `S=183`，比血条 `S=215` 只低一点。
   用「饱和且亮」当判据会把整块地砖框进来（实测整帧 3.2% 的像素都满足
   `S>=80 & V>=80`）。
2. **必须水平闭运算** —— 血条上的白色数字 `17640 / 17640` 把连续绿色切成碎段，
   不桥接的话最宽只剩 **126px**（真机实测），桥接后还原为完整 408px。
3. **必须靠 ROI 隔离** —— 队友血条与玩家血条**颜色完全相同**（同为 `H=41 S=215`），
   只能靠位置区分。

⚠️ `energy_bar` **未实测**：那张真机截图里角色能量为 0，能量条是空的。
参数来自 UI 常识，spec 里 `calibrated=False`，预览用橙框标注。

## 11 · 本轮发现并修掉的 bug

| # | 位置 | 问题 |
|---|---|---|
| 1 | `actuator/keymap.py:load_keymap` | 回落路径返回 `VK` 成员而非 `KeyId`；`VK` 没有 `to_vk()` —— 偏偏「没配 keymap.toml 的新用户」正走这条路 |
| 2 | `actuator/validator.py:_check_id` | 查重自己取 `time.time()`，不用 `validate()` 传入的 `now_ms` —— 两条时钟混用，重复意图在回放/确定性调用下**漏过** |
| 3 | `tests/conftest.py:SCRATCH_ROOT` | 注释写「打包规则会跳过 `.pytest-tmp`」是**错的**；实际会让 build **跳过 metadata 推导**，包内没有 `plugin.meta.json` |
| 4 | `core/loop.py:_check_stuck`（上一轮发现，未修） | 读 `ctx.stats.consecutive_capture_failures`，但 `LoopStats` 上没这个字段 → `PATHFIND_STUCK` / `ESCAPE_SUCCESS` **永远发不出去** |

## 12 · 死代码扫描：一条真发现，两个刻意保留

**删掉**（全仓零引用、且不在 `.gitignore` 里 → 会被打进 6.7 MB 安装包）：

- `__init__.py.__e2y_backup`（135 KB，`__init__.py` 的旧快照）
- `pyproject.toml.bak`（2.0.73b0 时期的老依赖声明：那时 winrt 走 pip，
  现在改为 vendored `vendor/winrt/`）

**刻意保留**：

1. **`printwindow` 相关注释** —— 它们是「为什么删掉 PrintWindow」的踩坑记录
   （2026-10-10 真机实证），不是过时残留。另外
   `screen_capture.py`（YUI/OCR 用）与 `autopilot/perception/capturer.py`
   是两个模块，前者**仍然合法地**使用 PrintWindow。
2. **`autopilot/perception/teleport_ocr.py`** —— 整个模块**从未被 import**，
   但它实现的是 `teleport_contract.md` 写明的传送契约，
   `LocalizationService.on_teleport_ocr(target)` 期待它的 `TeleportTarget`。
   这是「先写好待接线」的设计点，删了等于扔掉一份契约实现。
   （模块内 `get_backend` / `set_ocr` 两个访问器确实零引用，但它们是注入钩子，
   与 `StateBuilder(locator=)` 同类。）

## 13 · 本轮回归

`826 passed` → **`949 passed, 1 skipped, 3 xfailed`**（skip 是下面那条）。

补测试 124 条：`template_cropper` 33 / `yolo_postprocess` 34 /
`intent_validator` 32 / `keymap` 25。

⚠️ `test_light_capture_dhash__static_screen_returns_same_hash` 被改成
**桌面在动就 skip**：它抓真实桌面并断言两次抓屏几乎一致，
只要桌面上有东西在动就会假红（实测全屏播视频时两帧差 39.6% 像素）。
诚实说明：在不受控的桌面环境里它基本只会 skip 而不会红 ——
这是它应有的语义（前提不成立时不该报假警），不是它变强了。

## 14 · 还欠真机的

| 项 | 需要什么 |
|---|---|
| WGC 走集成路径的真机验证 | 原神在**无边框全屏**下跑一次主循环 |
| 血条/能量条模板 | `template_cropper --image shot.png`，能量条需角色有能量 |
| OCR 分类器准确率 | 重采一批：背包/地图/对话/商店各停 10s |
| 定位服务原点 | 一次传送 |
| Console 提权 | UAC 弹窗 + 提权后新实例 |
| `_check_stuck` 修复 | 需改 `core/loop.py`（本轮约束是不碰引擎核心） |
| `USE_PICKUP` / `interaction` 规则是否开 | 等用户拍板（信号已全活） |
