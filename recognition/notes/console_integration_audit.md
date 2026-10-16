# Console 集成状态审计 —— 断裂点清单

> 2026-10-16 ｜ 面向 demo：Console 操作引擎 / overlay / 寻路
> 方法：静态代码追踪 + 磁盘产物核对。**只读，未改任何代码，未开原神。**
> 结论先行：**不是「各自不能跑」，是「只有一条护栏，且是单向的」。**

---

## 一 · 模块关系矩阵

图例：**✅ 直接调用** ／ **📄 文件通道** ／ **❌ 断裂** ／ **⚠️ 单向**

| A ↓ ＼ B → | Engine | Overlay | Pathfind | Snapshot | Events | Rules |
|---|---|---|---|---|---|---|
| **app.py** | ✅ `:183` | ✅ `:186` | ✅ `:190` | ✅ `:202` | — | — |
| **Engine** | — | ❌ | ❌ **双向皆无** | 📄 `:424` | 📄 `:428` | — |
| **Overlay** | ❌ | — | ❌ | 📄 共享 `_runtime` | — | — |
| **Pathfind** | ⚠️ `:213` 由 tab 传入 | ❌ | — | — | — | — |
| **Snapshot** | 📄 读 `state_snapshot.json` | 📄 读同一文件 | ❌ | — | — | — |
| **插件面板** | 📄 `console_bridge.json` | 📄 同上 | ❌ **bridge 无该字段** | — | — | — |

### 各组件实际通信方式

| 组件 | 唯一真值来源 | 陈旧检测 |
|---|---|---|
| `EngineManager` | 自己的 `Popen.poll()`（`engine.py:120`） | — |
| `SnapshotReader` | `state_snapshot.json`（`snapshot.py:52`） | **❌ 无** |
| `EventReader` | `<run_id>.stream.jsonl` | ✅ 游标 + `reset()` |
| `OverlayManager` | `Popen.poll()` | — |
| `PathfindChannel` | `pathfind_state.json` | ✅ `STALE_AFTER_S=5.0`（`:61`） |
| 插件面板 | `console_bridge.json` | ✅ `CONSOLE_ALIVE_TIMEOUT_MS=3000` |

> **只有寻路和桥接有陈旧判定，引擎快照两边都没有。**

---

## 二 · 断裂点清单（按 demo 严重度排序）

### 🔴 F1 · 引擎 ↔ 寻路的互斥护栏是**单向**的

**证据**：

| 方向 | 有护栏？ | 位置 |
|---|---|---|
| 寻路启动 → 检查引擎 | ✅ 有 | `tabs/pathfind.py:401` 传 `engine_running=self.engine.is_running()`；`channels/pathfind.py:228` 拦截 |
| 引擎启动 → 检查寻路 | ❌ **无** | `channels/engine.py:133-136` 只查 `self.is_running()` |

**根因在组装层**（`app.py:216-220`）：

```python
("寻路", PathfindTab(self.pathfind, self.engine, ...)),   # 寻路页拿到了引擎引用
("引擎", EngineTab(self.engine)),                          # 引擎页没拿到寻路引用
```

`channels/pathfind.py:216-218` 的注释把这条写成了**刻意设计**：
> 「刻意做成**参数**而不是本对象去查引擎 —— channel 之间不互相持有」

设计本身没错，**但护栏只落在了一个方向上**。

**三个绕过入口**（都会在寻路运行时启动引擎、且无一检查）：

| # | 入口 | 位置 |
|---:|---|---|
| 1 | 引擎页「启动」按钮 | `tabs/engine.py:106` → `_on_start` → `mgr.start()` |
| 2 | 插件面板发 `START_ENGINE` | `app.py:424-425` |
| 3 | 插件面板发 `RESTART_ENGINE` | `app.py:428-438` |

**后果**：先从寻路页跑起导航 → 切到引擎页点「启动」→ 两个进程同时用
`Win32Backend` 注入同一套键鼠 → 一方 `release_all()` 松掉对方的键 → 角色乱走。
`channels/pathfind.py:19-25` 自己写了「那个错一旦发生，现象和病因隔着十万八千里」——
**而这条护栏正是为了防它，只防住了一半。**

| 最小修复 | 成本 |
|---|---|
| `EngineManager.start()` 加一个 `blocked_reason: str \| None = None` 形参；`app.py:425/438` 与 `tabs/engine.py:_on_start` 三处调用前先查 `self.pathfind.is_running()`，非空则拒绝并 `log_line.emit` 说明 | **分钟级** |

---

### 🔴 F2 · `console_bridge.json` 里**没有寻路字段**

**证据**：`app.py:395-412` 的 payload 只有四个键：

```python
{"schema_version":…, "written_at_ms":…,
 "console": {running, version, pid},
 "engine":  {state, pid},
 "overlay": {state, pid},
 "stats":   {tick_hz, tick_p95_ms, scene, intent_count, idle_streak}}
```

插件侧 `__init__.py:1001-1008` 透出的也只有 `console_alive` / `engine_state` /
`overlay_state` / `stats`。

**后果**：**插件面板永远不知道寻路在跑。** demo 里若用面板下发 `START_ENGINE`，
面板不会给出任何冲突提示 —— 面板显示「引擎已停止」，实际寻路正在注入按键。

| 最小修复 | 成本 |
|---|---|
| payload 加 `"pathfind": {"state": self.pathfind.state(), "pid": _pid_of(self.pathfind)}`；面板 context 加 `pathfind_state` | **分钟级**（两侧同改，否则静默失联） |

---

### 🟠 F3 · 引擎快照**没有陈旧检测**，引擎死了两边都还显示运行中

**证据**：

| 读取方 | 有无 age 判定 |
|---|---|
| `channels/snapshot.py:52 read()` | ❌ 只有 `(size, mtime_ns)` 短路**缓存**，不判断年龄 |
| `autopilot/ui/reader.py:63 read_snapshot()` | ❌ 同上 |

对比 `PathfindChannel`（`:61` `STALE_AFTER_S = 5.0`）与
`autopilot_bridge`（`:53` `CONSOLE_ALIVE_TIMEOUT_MS = 3000`）—— **只有引擎快照这一路没有**。

**后果（这正是任务 C 问的第 4 问，两个方向都会发生）**：

| 场景 | Console | 插件面板 | overlay 卡片 | 真实 |
|---|---|---|---|---|
| 引擎被 `kill()` 强杀 | 「已崩溃」 | 同 Console | **仍显示「运行中」**（最后一个快照写的是 RUNNING） | 已死 |
| Console 被杀，引擎存活 | 窗口没了 | 「主控台未运行」 | **仍显示「运行中」并持续刷新** | 在跑 |

`console_bridge.json` 里的 `stats` 直接抄自快照（`app.py:376`），
所以**插件面板会跟着显示一个已经不存在的引擎的 Hz 和场景**。

| 最小修复 | 成本 |
|---|---|
| `snapshot.py:52` 返回值加 `stale: bool`（`now - mtime > 5s`）；`tabs/home.py:159` 与 overlay 侧读到 stale 时显示「数据已过期」 | **分钟级** |

---

### 🟠 F4 · overlay 与引擎之间**没有直接感知**，Console 不知道卡片在显示什么

**证据**：

- `app.py:186` `OverlayManager(cfg.overlay, cfg.engine, ...)` —— 传的是 **EngineConfig**（为了拿 `runtime_dir`），**不是 EngineManager**
- `channels/overlay.py` 全文**没有任何 `EngineManager` 引用**
- overlay 进程靠**自己**去读 `state_snapshot.json`（`ui/reader.py`），与 Console 的读取是两条独立通路

**后果**：`OverlayManager.start()` **不要求引擎在跑**。引擎没启动时也能起卡片，
卡片读到的是**上一次运行遗留的快照**，照常显示「运行中 / 4.9Hz / 场景 WORLD」。
这与 F3 叠加后，观感是「卡片一直在动，但引擎早就没了」。

| 最小修复 | 成本 |
|---|---|
| `OverlayTab` 启动前查 `engine.is_running()`，未运行则黄字提示「引擎未启动，卡片显示的是上次数据」；或 overlay 侧把 `state=STOPPED` 的快照映射成「未就绪」 | **分钟级** |

---

### 🟡 F5 · 引擎启动是**盲启动**：没有就绪确认

**证据**：`channels/engine.py:244` `self._emit_state("running")` 在 `Popen` 返回后
**立即**置为 running —— 那时引擎进程刚创建，截图器还没打开、YOLO 还没加载。

```
L216  self._proc = subprocess.Popen(...)
L244  self._emit_state("running")     ← 此刻引擎还没 tick 过一下
```

`app.py:433` 用 `self.engine.is_running()`（即 `Popen.poll()`）判断，
**从不校验 `state_snapshot.json` 有没有真的开始刷新**。

**后果**：demo 点「启动引擎」→ Console 立刻显示绿灯「运行中」→ 但引擎可能
3 秒后就因 `WindowNotFoundError` 崩掉（`engine.py:354` 那套 stderr 提炼就是为这个准备的）。
观众看到的是「绿灯 → 突然变红 + 一行英文 traceback」。

| 最小修复 | 成本 |
|---|---|
| 启动后轮询 ≤5 s 等 `state_snapshot.json` 的 `run_id` 变化，变化才算真 running；否则显示「启动中…」并附 stderr 摘要 | **小时级** |

---

### 🟡 F6 · 寻路状态**不会写进任何 Console 面板**

**证据**：`PathfindChannel.read_state()` 只喂给 `tabs/pathfind.py:450` 自己的标签。
`app.py:395-412` 的 bridge payload 无 pathfind；首页 / 引擎页都不读 `pathfind_state.json`。

**后果**：demo 时若从别处启动寻路，**Console 首页和引擎页完全看不出寻路在跑**。
叠加 F1 就是「两个注入源同时跑，三个界面都看不出来」。

| 最小修复 | 成本 |
|---|---|
| 首页卡片加一行「寻路：运行中 / —」，数据源 `self.pathfind.read_state()` | **分钟级** |

---

### ⚪ F7 · 打包 exe 模式下 bridge 路径会漂（demo 用源码模式，暂不触发）

`paths.py:62-71` `base_dir()` 在打包后 = **exe 所在目录**；
插件侧 `autopilot_bridge.py:66` 默认写死 `<CONSOLE_ROOT>`。
`__init__.py:142-170` 的 `_bridge_candidates()` 会兜底探测 `build/` `dist/`，
**但只在文件已存在时才选中** —— Console 还没启动时，插件拿不到任何路径信息。

demo 若从 `python -m neko_console` 启动则**不受影响**。

---

## 三 · Demo 流程逐步推演（任务 B）

| 步 | Console 显示 | overlay 显示 | 插件面板 | 真实 | 一致？ |
|---|---|---|---|---|---|
| 1 打开 Console | — | 未启动 | 「主控台 v3.1.x」 | — | ✅ |
| 2 点「启动引擎」 | **立刻**绿灯「运行中」 | 未启动 | `engine_state=running` | 引擎刚 spawn，尚未 tick | ⚠️ **F5 盲启动** |
| 3 切回原神打怪 | 快照 200ms 刷新 | 快照 200ms 刷新 | stats 200ms 刷新 | 同源 | ✅ |
| 4 看引擎 tab | 实时反映 | — | — | — | ✅ |
| 5 点「启动 overlay」 | overlay 页显示运行中 | 浮窗出现 | `overlay_state=running` | — | ✅ |
| 6 看 overlay 是否显示引擎 | — | **显示引擎状态**（独立读文件） | — | — | ⚠️ **F4 无感知** |
| 7 停止引擎 | 「已停止」 | **最后快照**（多为「已停止」） | `engine_state=stopped` | — | ⚠️ **F3 走 terminate 兜底时不一致** |

**只有第 7 步会真的分裂**：`engine.py:272` 超时后走 `_terminate()`，
引擎**来不及写收尾快照** → overlay 停在 `RUNNING` 的最后一帧，
而 Console / 面板已经显示「已停止」。

> 这一格的分裂**必然发生在 demo 收尾**，因为收尾往往是观众最注意的时刻。

---

## 四 · 建议：现在修 vs demo 后

### 现在修（合计约 30 分钟，全部是加判断不是改架构）

| 优先 | 项 | 成本 | 理由 |
|:---:|---|:---:|---|
| 1 | **F1** 引擎启动查寻路 | 分钟 | 唯一的**功能性**断裂 —— 角色乱走是硬事故，且三个入口都要堵 |
| 2 | **F2** bridge 加 pathfind 字段 | 分钟 | 面板看不到寻路 = F1 无从提示 |
| 3 | **F4** overlay 启动前提示引擎状态 | 分钟 | 防「卡片显示上次数据」 |
| 4 | **F6** 首页加寻路行 | 分钟 | 让寻路状态在任何页面可见 |

> 这 4 项改完，**引擎 / 寻路 / overlay 三者在 Console 内就有了完整的互斥与可见性**，
> 插件面板也能通过 F2 的补字段看到寻路。断裂点从 6 降到 2。

### demo 后再修（需要碰读取器或改架构，回归面大）

| 项 | 成本 | 理由 |
|---|:---:|---|
| **F3** 快照陈旧检测 | 分钟（改起来）但要动 `snapshot.py` + overlay 两侧 | 收敛期（引擎停了但快照还在）可以靠 F1/F4 缓解 |
| **F5** 启动就绪确认 | 小时 | 真实收益大，但改动在启动主路径上，demo 前动风险大于收益 |
| **F7** 打包路径 | 小时 | demo 走源码模式，不触发 |

---

## 五 · 一句话结论

**Console 的三个子单元不是「没集成」，是「只集成了一半」——
寻路页拿了引擎引用所以有护栏，引擎页没拿寻路引用所以没护栏，
而这条本该双向的互斥关系还漏掉了插件面板那两个下发入口；
F1 + F2 + F4 + F6 四个分钟级补丁就能把断裂点从 6 个降到 2 个。**

---

### 附：关键行号索引

| 事实 | 位置 |
|---|---|
| 寻路→引擎护栏存在 | `tabs/pathfind.py:401` + `channels/pathfind.py:228` |
| 引擎→寻路护栏缺失 | `channels/engine.py:133-136` |
| 两个绕过入口 | `app.py:425`、`app.py:438` |
| 组装层不对称 | `app.py:217` vs `app.py:220` |
| bridge payload 无 pathfind | `app.py:395-412` |
| 面板透出字段 | `__init__.py:1001-1008` |
| 快照无陈旧检测 | `channels/snapshot.py:52`、`ui/reader.py:63` |
| 对照组：有陈旧检测 | `channels/pathfind.py:61`、`autopilot_bridge.py:53` |
| 盲启动 | `channels/engine.py:244` |
| overlay 与 engine 无引用 | `channels/overlay.py`（全文无 `EngineManager`） |
| demo 收尾分裂点 | `channels/engine.py:272` terminate 兜底 |
