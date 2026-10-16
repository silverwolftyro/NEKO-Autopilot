# Console 寻路：不开原神怎么验证

> 2026-10-12 ｜ 配套：`navigation_interface.md`（接口契约）、`demo_prep_pack.md`（demo 准备）
> **本文档描述的验证全部离线，不开原神、不注入按键。**

---

## 0 · 为什么需要它

寻路链路要真跑起来才说得清对错，但它平时只有一条验证路径：

```
Console 点「启动」-> 拉起 e2e_pathfind.py -> 它注入 Shift+W -> 角色真的走
```

这条路径要开原神、要管理员、要游戏在**前台**。于是「寻路功能能不能用」
这件事**没法离线回答**，只能靠感觉 —— 而「感觉」正是最不可靠的一种证据。

`fake_pathfind.py` 就是给这件事造的一条**第二条路径**：它复刻真脚本对外可见的
全部行为，但不碰游戏。

---

## 1 · 假脚本做了什么（以及刻意没做什么）

`autopilot/tools/fake_pathfind.py`

| 复刻了真脚本的 | 值 |
|---|---|
| 参数 | `--start-world X,Z` `--target-world X,Z` `--delay N` `--seconds N` `--live`（+ 真脚本没有的 `--fake-*`） |
| 状态文件 | `<root>/autopilot/_runtime/pathfind_state.json`，`schema_version = 2` |
| 停止信号 | `<root>/autopilot/_runtime/nav_stop.flag`，「存在即停」 |
| 运行时定位 | `NEKO_PLUGIN_ROOT` 环境变量（**与真脚本同名同义**） |
| 写入方式 | **先写 `.tmp` 再 `os.replace`**（原子，与真脚本一致） |
| 事件流 | `<root>/autopilot/_runtime/<run_id>.stream.jsonl`，`PATHFIND_START` / `PATHFIND_ARRIVED` / `PATHFIND_STOPPED` |
| 参数风格 | 直接读 `sys.argv`，**不用 argparse**，未知参数静默跳过 |

**刻意没有**：不注入按键、不截图、不检测窗口、不读小地图。

状态文件里多了一个 `"fake": true` 字段 —— 一眼能看出这份状态是假脚本写的，
避免「拿测试数据当真机证据」。

### 四种模式

| `--fake-mode` | 模拟什么 | 用来验什么 |
|---|---|---|
| `arrive`（默认） | 走完 `--seconds` 后 `arrived` | 正常全流程 |
| `stuck` | 永远 `navigating`，不前进也不退出 | 「卡住」的用户体验 + 停止按钮 |
| `crash` | 3 个 tick 后 `exit 9`，**状态文件停在 navigating** | ★ 进程死了但状态文件还在 |
| `ignore-stop` | 无视 `nav_stop.flag` | ★ Console 的 terminate 兜底 |

---

## 2 · 三种用法

### 2.1 手动起一个，看 Console 的 UI

**先让 Console 指向假脚本**（`config.toml`，只需一行）：

```toml
[engine]
pathfind_script = "D:\\NEKO\\N.E.K.O-main\\N.E.K.O-main\\plugin\\plugins\\genshin_game_buddy\\autopilot\\tools\\fake_pathfind.py"
```

然后 Console 里照常点「启动寻路」。它会把这个脚本当寻路脚本拉起来。

> ⚠️ 假脚本默认 8 秒后 `arrived` 就退出了。想让它多待一会儿，加参数 ——
>   但 Console 不发 `--seconds`，所以要久待的话请在**插件侧**改默认值，
>   或改用 `stuck` 模式（一直待着，等你点停止）。

**前台单独跑**（Debug 窗口能看到它的打印）：

```
cd <REPO_ROOT>\plugin\plugins\genshin_game_buddy
python -m autopilot.tools.fake_pathfind --live --seconds 8
python -m autopilot.tools.fake_pathfind --live --fake-mode stuck
python -m autopilot.tools.fake_pathfind --live --fake-mode crash
```

### 2.2 跑自动化测试（推荐先跑这个）

```
cd <CONSOLE_ROOT>
.venv\Scripts\python.exe -m pytest tests/test_pathfind_e2e_offline.py -q
```

这是**真的起真子进程**的端到端测试，覆盖「启动 → 写状态 → 状态变化 →
停止 → 退出」的完整闭环，以及崩溃/卡住/terminate 兜底。

### 2.3 脚本化的临时验证

```
python -m autopilot.tools.fake_pathfind --live --seconds 30 &
```
配合 `NEKO_PLUGIN_ROOT` 指到临时目录，可以在**不碰真实 `_runtime`** 的前提下
反复演练。

---

## 3 · 离线验证能证明什么、不能证明什么

这是本文档最重要的一节。

### ✅ 能证明（Console 侧，全部已自动化）

- Console 拼出来的命令行**假脚本认得**（参数名对不对）
- `PathfindChannel` 能真的起进程、状态文件能真的写出来并被读到
- 状态从 `navigating` → `arrived` 的推进，Console 全程看得到
- **`stop()` 的优雅路径**：写 flag → 脚本响应 → 写 `state=stopped` → 退出，
  Console 报「已停止（flag 生效，脚本自己退的）」
- **`stop()` 的兜底路径**：脚本无视 flag → terminate → **如实提示「按键状态
  可能不明」**
- 崩溃后状态文件**仍在、内容仍是 navigating** → 陈旧判定
- 启动前清残留 flag（否则新实例一起来就秒停）
- 缺参数时 `exit 2` 而不是崩

### ❌ 不能证明（必须真机）

- **真脚本收到 flag 后是否真的松开了按键**。假脚本只是「按约定写了
  `state=stopped`」，它当然不需要真的按键。Console 那句「flag 生效」目前
  是**基于约定的信任**，不是基于观测。
- **真脚本的几何寻路是否真能把角色带到目标**
- UIPI / 权限 / 游戏前台 这些真机才有的变量
- `nearest_group_px`、PickMark 这类阈值

> 一句话：**这条链路证明「管道是通的」，不证明「水是干净的」。**

---

## 4 · 镜像与漂移守卫

假脚本有两份，两仓库各一份：

| 位置 | 角色 |
|---|---|
| `plugin/.../autopilot/tools/fake_pathfind.py` | **正本**，也是给用户用的工具 |
| `NEKO-Console/tests/fixtures/fake_e2e_pathfind.py` | 镜像，供 Console 测试起进程 |

必须**逐字节相同**。Console 侧有两条守卫：

- `test_mirror__fixture_matches_the_plugin_tool` —— 逐字节比对（插件侧不存在时跳过）
- `test_mirror__fixture_content_is_pinned` —— 比对钉住的 md5（插件侧不存在也能发现漂移）

改了一边必须同步另一边，**并更新 `FIXTURE_MD5`**。

> 为什么要守卫：两份逻辑各自演进几个月后，不会有人知道哪份才是当前行为 ——
> 而这类漂移**不会报错**，只会在某天悄悄给出与真脚本不同的结论。
> 做法照搬仓库既有约定（配置层镜像感知层常量 + 单测钉住）。

---

## 5 · demo 当天怎么用

见 `demo_prep_pack.md` §C1.5。要点：

1. **T-1 天**先用假脚本把 Console 的寻路 UI 走一遍（这部分不需要原神，
   能提前发现「按钮灰着」「点了没反应」这类问题）
2. **T-1 天**再用真脚本跑一次 DIRECT 模式 —— 这才是准入条件
3. **当天**只演 DIRECT；WAYPOINTS（要预先裁路点）别碰

假脚本验证通过**不等于**可以演寻路：它只说明 Console 的管道通，真能不能把
角色带到终点，仍要 T-1 天真机那一次。

---

## 6 · 相关文件

| 文件 | 是什么 |
|---|---|
| `autopilot/tools/fake_pathfind.py` | 假脚本正本 |
| `NEKO-Console/tests/fixtures/fake_e2e_pathfind.py` | 镜像（被守卫钉住） |
| `NEKO-Console/tests/test_pathfind_e2e_offline.py` | 端到端测试（真子进程） |
| `.../tests/unit/test_fake_pathfind.py` | 工具自身的参数/行为单测 |
| `recognition/notes/navigation_interface.md` | 接口契约（`NavMode` / 路点 schema / 停止语义） |
| `recognition/exports/benchmark/scripts/misc/e2e_pathfind.py` | 真脚本（`_SPEC` 是参数的事实来源） |
