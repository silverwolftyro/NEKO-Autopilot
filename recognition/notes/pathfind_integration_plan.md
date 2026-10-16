# 寻路（e2e_pathfind）接入方案

> 2026-10-11 · 只做设计，**未实现任何代码**（`--delay` 与 `PLUGIN_ROOT` 环境变量除外，见 §6）
> 依据：`e2e_pathfind.py` / `autopilot/tools/run_loop.py` / `<CONSOLE_ROOT>`
> 的**静态源码阅读**。未运行过任何脚本。标「[推断]」的是判断，不是源码事实。

---

## 0 · 先纠正两处前提

### 0.1 Console 的真实路径不是 `N.E.K.O-Console`

```
<WORKSPACE_ROOT>\N.E.K.O-Console   ← 空的，只有一个 runtime 目录
<CONSOLE_ROOT>      ← 真的在这（PySide6 + Nuitka 打包成 build\Console.exe）
```

### 0.2 e2e_pathfind **已经有** `--delay`，但它是死的

`e2e_pathfind.py:611` 的 `_SPEC` 里登记了 `"--delay": ("delay", float, True)`，
但全文件搜不到任何一处读 `A["delay"]` 的代码。**参数被解析然后静默丢弃**，
`--delay 10` 等于没传。本轮已补上实现（§6）。

---

## 1 · e2e_pathfind 的输入输出契约

### 1.1 参数（22 项，不用 argparse，是手写 `_parse_args()`）

目标参数**三选一**，否则 `exit 2`：

| 参数 | 说明 |
|---|---|
| `--target-world X,Z` | 世界坐标目标 |
| `--target-tp "名"` | 按传送点名 |
| `--walk-forward N` | 沿当前朝向前进 N 米（需 `--live`） |

其它：`--start-world X,Z`（**必填**，除 walk-forward 外）、`--seconds`、`--arrive`
（默认 5m）、`--delay`、`--channel`、`--title`（默认「原神」）、`--log-file`、
`--tp-data`、`--live`/`--dry-run`、`--sprint`/`--walk`、`--use-llm`/`--llm-real`/
`--llm-max-calls`、`--waypoints-file`、`--use-route`、`--i-know-start`。

### 1.2 启动依赖

`PLUGIN_ROOT` 原本**硬编码绝对路径**（`:61-62`），所以它能 `import autopilot`
纯粹因为路径写死了，与 cwd 无关。本轮已抽成 `NEKO_PLUGIN_ROOT` 环境变量。

### 1.3 输入 / 输出

| | 内容 |
|---|---|
| 输入 | 游戏窗口像素（Capturer）、路点文件、起点/目标坐标（**命令行必给，不自动猜**） |
| 输出 | `pathfind_state.json`（每帧写 + 收尾写终态，schema v2）、stdout（`--log-file` 时 tee 进文件） |

**返回码**：正常 `0`；参数/UIPI 失败 `2`；未捕获异常 `1`。
⚠️ **`arrive` 和 `panic` 都返回 0** —— 返回码**不能用来判断结果**，
想知道成功与否只能读 `pathfind_state.json` 的 `state` 字段。

### 1.4 停止机制（三条）

1. **F12 急停** —— 自建 200Hz 守护线程，按下先 `release()` 再 `stop.set()`。
   源码注释明说 SafetyGuard 的 F12 在这里**不生效**。
2. **flag 文件** —— 主循环每帧检查 `NAV_STOP_FILE.exists()`，先松键再退出。
3. **`--seconds` 到点**。

### 1.5 前台 / 权限要求

- **`--live` 必须管理员**（`_uipi_require_admin` 不通过直接 `return 2`）
- **必须游戏在前台**（脚本自己在检测焦点翻转并告警）
- 分辨率无硬性要求（走 minimap ROI + 相位相关）

---

## 2 · 能不能和 run_loop 并行：**不能**

| 维度 | 结论 |
|---|---|
| cwd | ✅ 不冲突（都用绝对路径） |
| 端口 | ✅ 不冲突（两边都不开端口） |
| 共享文件 | ✅ 不撞（`pathfind_state.json`/`nav_stop.flag` 只有 e2e 写；`commands.jsonl`/`run_current.txt` 只有引擎写） |
| 截图 | ⚠️ 抢同一个窗口。[推断] 多句柄通常可用，但帧率互相拖累（e2e 实测已只有 3~5 fps） |
| **键鼠注入** | ❌ **严重冲突** |

**冲突点不在文件/端口，在同一套键鼠。** 两者都用 `Win32Backend` 注入，
一方 `release_all()` 会**松开对方按住的键**，角色动作不可预测。

`run_loop.py:268-271` 的注释也明说第二个实例会跟第一个抢同一个游戏窗口。

---

## 3 · Console 能不能同时管两个进程：**不能，是架构级单实例**

`EngineManager` 只有**一个** `self._proc`；`is_running()` 就是
`self._proc is not None and self._proc.poll() is None`。

要管两个，缺的东西：

1. `_proc` 改成 dict/list，或**新增一个 `PathfindManager` 类**
   （`OverlayManager` 已经是独立类 + 独立 tab，复制它是现成的样板）
2. 新增 UI 按钮（`tabs/engine.py` 现在只有一套）
3. **停止路径不对称**：引擎走 `STOP` 命令 + 轮询 + 超时 terminate（因为它有
   `release_all`）；**e2e 没有这个命令协议**，只认 flag + F12 → 只能退化成
   「写 flag + 等 + terminate」，丢掉优雅松键
4. flag 语义是「存在即停」，且 e2e 启动时会 `unlink` 残留 → **两个 e2e 实例会互相秒停**

---

## 4 · 三种方案对比

| | A · Console 统一启动 | B · 插件内启动 | C · e2e 自己协调 |
|---|---|---|---|
| 改动量 | 新增 `channels/pathfind.py` + `tabs/` 按钮 + schema 字段 | 极小（`__init__.py` 几行） | 中（e2e 内部补协议） |
| 架构一致性 | ✅ 符合 `__init__.py:1004`「Console 是唯一所有者」 | ❌ **违反既有决策** | ⚠️ 不冲突但**不能独立成立** |
| 优雅停止 | 需退化（flag + 等 + terminate） | 同 A | ✅ 已支持 |
| 子进程回收 | ✅ Console 持有 Popen 句柄 | ❌ 插件卸载时**无法可靠回收**（宿主强杀 → 孤儿进程） | — |
| 能否独立启动 | ✅ | ✅ | ❌ **缺启动侧 flag**，仍需有人 spawn |

**方案 B 被现有设计直接否决**，`__init__.py:1003-1006` 原文：

> 「为什么不直接在这里启停引擎 —— 主控台才是引擎和卡片的**唯一所有者**…插件这边 fork
> 一个出来，那就是又一份孤儿进程来源，正是这套东西一开始要消灭的问题。」

（R8 红线**不会**拦 B：`subprocess.Popen` 写在方法里不算模块顶层副作用。）

---

## 5 · 推荐：**方案 A**，照 `OverlayManager` 的模式复制一个 `PathfindManager`

flag / JSON 机制**已经存在且被 overlay 验证过**（`layout.py` 有完整 TTL 与 schema
版本），不用重新发明；但 Console 必须持有 Popen 句柄才能做优雅停止和崩溃回收 ——
这是 `__init__.py:1003-1006` 明确划的线。

### 五个前提（按重要性排序）

1. **绝不能与 `run_loop --live` 并行** —— 这是能否落地的**第一前提**，比生命周期
   管理重要得多。必须串行或互斥启动。
2. **停止必须走 flag 而非 terminate**：先写 `nav_stop.flag`（e2e 会松键退出），
   轮询等它自己退；terminate 只能兜底。
3. **管理员权限要和 Console 一致**，否则 spawn 出来 3 秒内 `return 2`。
4. **游戏必须前台** —— 本轮已给 e2e 补上 `--delay`（§6）。
5. **返回码不可用于判定结果** —— Console 想知道结果必须读 `pathfind_state.json`。

---

## 6 · 本轮已落地的小改动

### 6.1 `--delay` 补实现

`e2e_pathfind.py`：参数早就在 `_SPEC` 里，但 `main()` 从没读过 → 传了等于没传。
现在它在**管理员门禁之后**做倒计时（不是管理员就别让人白等），逐行打印，
`\r` 刷新，不刷屏。

> 放在门禁之后是刻意的：门禁失败 return 2 是最快的失败路径，
> 先让用户白等 15 秒再看报错是糟糕的体验。

### 6.2 `PLUGIN_ROOT` 抽成环境变量

```
NEKO_PLUGIN_ROOT = <REPO_ROOT>\plugin\plugins\genshin_game_buddy
```

默认值保持原硬编码路径，行为不变。换机器/换 checkout 时不必再改代码。

---

## 7 · 分阶段实施计划

每阶段都能**独立验证**，失败时不会把后面的阶段一起带崩。

| 阶段 | 做什么 | 独立验证方式 |
|---|---|---|
| **0** | 串行跑一次：`run_loop --live` → 停 → `e2e_pathfind --live` | **确认键鼠交接干净**（`release_all` 后没有残留按键，角色不会自己走）。这是全案最关键的一次实测 |
| **1** | `channels/pathfind.py`：`PathfindManager`（spawn / 停止 / 崩溃回收 / 状态） | Console 单元测试 + 手动 spawn 一次，看 `pathfind_state.json` 有没有更新 |
| **2** | `tabs/` 加「启动寻路 / 停止寻路」两个按钮 + 状态灯 | 点按钮 → 任务管理器里出现对应进程 → 停止后进程消失 |
| **3** | 互斥：Console 检测到引擎在跑时**禁用**寻路按钮（而不是两个都起） | 两个都开 → 按钮应该是灰的 |
| **4** | 停止路径打**异步化**：`spawn` 后立刻 `release_all()`，让 e2e 拥有键鼠 | 长按 Shift+W 跑的过程中停引擎 → 角色立刻停下，而不是继续走 |
| **5** | 浮窗显示寻路状态（`pathfind_state.json` 已有，overlay 只读即可） | 跑寻路时浮窗显示目标/距离/卡住层级 |

**阶段 0 必须先做。** 后面四个阶段全建立在「两个进程串行时键鼠能干净交接」这个
假设上；这个假设没验证过就往上盖东西，等于把不确定性埋进地基。

---

## 8 · YUI 陪玩链路的环境变量（与寻路无关，但一并记在这里）

事件流适配器默认盯**插件安装副本**的 `_runtime`，而引擎写的是**源码树**的
`_runtime` —— 两套互不相干的文件。安装副本那份是 `plugin build` 打的**快照**，
每次打包刷新，之后就不再动。

**设一个系统级环境变量**让它跟活的 run：

```
NEKO_AUTOPILOT_RUNTIME = <REPO_ROOT>\plugin\plugins\genshin_game_buddy\autopilot\_runtime
```

**必须是系统级**，不能只在当前 PowerShell 里 `$env:` 设一次 ——
N.E.K.O 是你**双击启动**的，进程继承的是 `explorer.exe` 的环境，
不是某个终端会话的。设完**重启 N.E.K.O** 才生效。

验证：日志里这一行应该变成

```
事件流->YUI 已启动，目录 ...\autopilot\_runtime（来源：环境变量 NEKO_AUTOPILOT_RUNTIME；进程 cwd=...）
```

---

## 9 · 未知 / 风险

| 项 | 状态 |
|---|---|
| 两个 dxcam 句柄能否同时抓同一窗口 | **未验证** |
| Console 是否有 UAC 权限继承机制 | **未查**（`engine.py` 只注入 `PYTHONIOENCODING`/`PYTHONUTF8`） |
| `_runtime/` 三方共写（引擎 / 插件 / e2e）的并发安全 | **未完整审计** |
| e2e_pathfind 的 flag 协议有无单测 | **未查** |
| 阶段 0 的键鼠交接 | **必须实测** |