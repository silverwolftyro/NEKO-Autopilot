# 导航接口设计（Console ↔ `e2e_pathfind.py`）

> 记录时间：2026-10-12
> 状态：**接口已落地并有单测；live 全链路本轮未开原神验收**
> 代码：`<CONSOLE_ROOT>\neko_console\tabs\mapdata.py`（纯逻辑）+ `channels\pathfind.py`（进程）

---

## 〇、一句话

`e2e_pathfind.py` 是个命令行脚本；Console 要用它，就得先把「去哪」翻译成它的参数、
把「跑起来/停下来」管起来。本文记录这一层的接口契约，以及每个字段为什么长这样。

---

## 一、分层

```
pathfind.py   (QWidget)        UI：地图点选、搜索框、下拉框
   │
   ├─ mapdata.py               ★ 纯逻辑：坐标换算、传送点、起点/目标解析
   │                           无 GUI、无副作用、不碰游戏 → 可直接单测
   │
   └─ channels/pathfind.py     进程生命周期：起子进程、flag 停止、terminate 兜底
       │
       └─ e2e_pathfind.py     被调用的脚本（真发 Shift+W 和鼠标）
```

**为什么中间要垫一层 `mapdata.py`**：它不含任何执行逻辑，所以「解析得对不对」
可以在不开游戏的情况下用真实数据单测。一旦把解析和发按键混在一起，能测的就只剩
「有没有崩」，而寻路出错的典型现象是**不崩、只是走错地方**。

---

## 二、前置约定：坐标与地图

| 项 | 值 |
|---|---|
| 底图原图 | 36864 × 18432 px（提瓦特大陆 P0） |
| 底图缩略图 | 2000 × 1000 px（`THUMB_TO_FULL = 18.432`） |
| 世界坐标 | 游戏内的**米**，与引擎一致 |
| 换算 | 仿射：`px = A·wx + B·wz + C`，`py = D·wx + E·wz + F` |
| 标定质量 | 23 个人工锚点，RMS **12.65 px** / 最大 28.64 px |

⚠️ **选点精度受缩略图限制**：1 缩略图像素 ≈ **18.4 米**。要精确定位就用手动输入框。
这是刻意的取舍——原图 139 MB / 6.79 亿像素，PIL 会直接抛 `DecompressionBomb`，
而**一层没法离线验证的图像管线比没有更危险**。

⚠️ 仿射系数是**抄**自 `recognition\exports\benchmark\scripts\misc\coord_transform.py`
的（Console 不跨项目 import 它：别人单独 clone Console 会 ImportError）。
两边一致性由 `verify_against_reference()` 守着，**只改一边就是回归**。

---

## 三、起点：`StartSource`

```python
class StartSource(Enum):
    MANUAL        = "手动输入"        # 可用
    ANCHOR        = "从地图选点"      # 可用
    SCREENSHOT_OCR = "截图识别（预留）"
    LOCALIZATION   = "定位服务（预留）"
```

```python
def get_start_position(req: StartRequest) -> tuple[float, float] | None
```

**拿不到就返回 `None`，不抛、不猜。** UI 据此把「启动」置灰并说明原因。

两个预留来源各自的前置条件（`.unavailable_reason` 会原样显示给用户）：

| 来源 | 需要什么才能启用 |
|---|---|
| `SCREENSHOT_OCR` | 一个能从截图读出角色位置的 OCR 链路，目前未接 |
| `LOCALIZATION` | **必须先在游戏里传送一次**建立原点，否则拿不到任何位置 |

> 为什么这么保守：定位链路不可用时，一个**错的**起点比一个**没有**起点危险得多
> —— 错的会让角色朝反方向走一路，而「拿不到」用户看得见。

---

## 四、目标：`TargetSpec`

```python
resolve_target_from_description(text: str) -> TargetSpec
```

`TargetSpec.kind` 三选一，**互斥**：

| kind | 含义 | 何时产生 |
|---|---|---|
| `point` | 直接给了坐标 | `parse_xz(text)` 成功 |
| `anchor` | 匹配到唯一传送点 | 地名**完全相等**命中 1 条，或**包含**匹配唯一命中 |
| `unresolved` | 解析不出来 | 0 个命中 / **多个**同名命中 |

⚠️ **同名绝不猜**。真实数据里「七天神像-风」有 **6 个**同名、「无名遗迹」各地都有。
命中多个时返回 `unresolved` + `entity_type="有 6 个同名地点"`，交给 UI 让用户选，
而不是随便挑一个——挑错就是「走反方向但看起来一切正常」。

### 将来怎么接 LLM（预留，不实现）

把 `text` 交给宿主模型，让它回 `{"anchor_name": "风起地"}` 或 `{"x": .., "z": ..}`，
**然后仍然走上面这些解析分支**。

> 模型只负责「自然语言 → 一个名字或坐标」，**坐标换算和合法性判断留在本地**。
> 这样模型出幻觉时最多是「找不到」，不会是「走错」。

---

## 五、导航：`NavMode` / `NavigationRequest` / `NavigationHandle`

```python
class NavMode(Enum):
    DIRECT    = "直达"              # 可用
    WAYPOINTS = "路点"              # 可用
    AUTO      = "自动规划（预留）"   # 不可用
```

`AUTO` 的 `unavailable_reason`：自动规划需要**地形可通行性分类器** —— 现在只能
直达或走预设路点，没有 A* 那样绕障的能力。

```python
@dataclass
class NavigationRequest:
    start: tuple[float, float]
    target: tuple[float, float]
    mode: NavMode = NavMode.DIRECT
    waypoints: list[tuple[float, float]] = []   # WAYPOINTS 的中转点
    live: bool = True          # 真发按键 vs 只规划
    delay_s: float = 10.0      # 启动前等待，给用户切窗口

@dataclass
class NavigationHandle:
    running: bool = False
    pid: int | None = None
    mode: NavMode = NavMode.DIRECT
    request: NavigationRequest | None = None
    note: str = ""
```

`NavigationRequest` 是**纯数据**，不含执行逻辑；`NavigationHandle` 是运行态。
现在 `PathfindChannel` 就是这个角色的**命令行版本**，dataclass 是把它抽象成接口的
第一步——将来换实现（进程内调用、远程下发）时调用方不用改。

---

## 六、`nav_to_cli_args()` —— 接口到实现的唯一接缝

```python
nav_to_cli_args(req, *, runtime_dir=None, waypoints_name="console_waypoints.json") -> list[str]
```

### ★★ 已修的 bug：`--waypoint` 这个 flag 不存在

**早先的实现给 WAYPOINTS 拼的是 `--waypoint X,Z`。而 `e2e_pathfind.py` 的参数表
`_SPEC` 里根本没有这个 flag**，它的解析循环对未知参数是：

```python
spec = _SPEC.get(k)
if spec is None:
    if not k.startswith("-"):
        a["title"] = k      # ← 不以 - 开头的东西被当成【窗口标题】
    i += 1
    continue                # ← 不报错、不提示，直接跳过
```

于是实际发生的是：

1. `--waypoint` 被**静默丢弃**；
2. 后面的 `"100.00,200.00"` 不以 `-` 开头，被当成**窗口标题**吞掉；
3. 请求退化成一条普通的 `--target-world` **直线**。

**用户以为角色在沿路点走，实际在直线冲向终点。** 中间隔一堵墙就是站着不动，
而 UI 上一切正常——这正是本项目反复强调要避免的那类失败（见
`e2e_pathfind.py` 里大量「宁可拒绝启动也不猜」的注释）。

### 现在的正确契约

| 模式 | 产出 |
|---|---|
| `DIRECT` | `--start-world X,Z --target-world X,Z [--live] [--delay N]` |
| `WAYPOINTS` | `--start-world X,Z --waypoints-file <path> [--live] [--delay N]` |
| `AUTO` | 抛 `ValueError` |

⚠️ **`--waypoints-file` 与 `--target-world` / `--walk-forward` / `--target-tp` 互斥**
（脚本 `exit 2`）：路点文件自带终点，同时给等于「两份互相矛盾的终点」。
所以 WAYPOINTS 模式下**不拼** `--target-world`。

### 三种「宁可报错也不静默降级」

| 情况 | 行为 |
|---|---|
| `AUTO` | `ValueError` |
| `WAYPOINTS` 但 `runtime_dir=None` | `ValueError`（没有落地路径就不能拼参数） |
| `WAYPOINTS` 但一个路点都没有 | `ValueError`（那和 DIRECT 没区别，会静默退化成直线） |

---

## 七、路点文件 schema

`write_waypoints_file()` 落盘的格式，与 `e2e_pathfind.load_waypoints_file` 对齐：

```json
{
  "schema_version": 1,
  "start":  [0.0, 0.0],
  "target": [9.0, 9.0],
  "waypoints": [{"x": 1.0, "z": 1.0, "note": ""}]
}
```

- `schema_version` 必须等于 `WAYPOINTS_SCHEMA = 1`，**对不上整份判废**（脚本不
  「尽力读」——读错字段会让角色朝反方向走）。
- `start` 只是参考值（起点体检比对用，坏了不致命）；`target` 是终点，坏了整份判废。
- 用 `utf-8` 无 BOM 写出；脚本读的是 `utf-8-sig`，两种都能读，但我们自己产出的
  文件不该给下游留一个「兼容 BOM」的活。
- 点选工具：`anchors_viewer.py`，见 `user_waypoints_guide.md`。

---

## 八、停止语义（为什么 `stop()` 要三级）

`e2e_pathfind` **正在往游戏里注入按键**。直接 `terminate` 会让按键状态不明——
角色可能松开鼠标后还一直往前走，而根因（有个进程被强杀）离症状非常远。

所以 `PathfindChannel.stop()` 走三级：

1. 写 `nav_stop.flag` —— 脚本每帧检查，收到后**先 `sprint_off()` 松键**再退出。
   这是唯一能让按键状态确定的路径。
2. 轮询等它自己退（默认 10 秒）。
3. 超时才 `terminate()`，且**在返回值里如实说明**「按键状态可能不明」。

⚠️ **绝不能与引擎并行**：引擎（autopilot）和寻路都用 `Win32Backend` 注入同一套
键鼠，一方 `release_all()` 会松开对方按住的键。所以 `start()` 会**拒绝**在引擎
运行时启动，而不是「启动了再说」。

⚠️ **状态陈旧**：`pathfind_state.json` 每帧重写，文件存在 ≠ 进程还活着。
超过 `STALE_AFTER_S = 5.0` 秒没更新就判陈旧——**陈旧比没有状态更危险**，
因为它会让人以为寻路还在努力工作。

---

## 九、当前可用性矩阵

| 能力 | 状态 | 卡在哪 |
|---|---|---|
| 图形化选点（DIRECT 起点/终点） | ✅ 可用 | — |
| 传送点搜索（936 条） | ✅ 可用 | — |
| 起点来源 = MANUAL / ANCHOR | ✅ 可用 | — |
| 起点来源 = OCR / LOCALIZATION | ⛔ 置灰 | 缺 OCR 链路 / 缺原点 |
| 目标描述 → 唯一传送点 | ✅ 可用 | — |
| 目标描述 → 同名多点 | ⚠️ 返回 unresolved | 需用户在地图上选 |
| 目标描述 → 实体（"附近的宝箱"） | ⛔ 预留 | 等 LLM 接入 |
| NAV = DIRECT | ✅ 参数就绪 | — |
| NAV = WAYPOINTS | ✅ 参数就绪，schema 已用真脚本验过 | — |
| NAV = AUTO | ⛔ 抛异常 | 缺地形分类器 |
| **live 真机发按键** | ❌ **未验证** | 见 §十一 |

---

## 十、寻路脚本路径（已修，2026-10-12）

**早先的问题**：`PathfindChannel.script_path()` 把脚本写死成
`<engine.path>\autopilot\tools\e2e_pathfind.py`。那个目录**存在**，但里面没有
`e2e_pathfind.py` —— 脚本住在 `recognition` 仓库。于是点「启动」永远被
`path_problems()` 拒掉，而报错指向一个看起来完全正常的目录。

**采用的方案**：加配置项 `[engine].pathfind_script`，**不改 Console 以外的东西**
（不迁移 208KB 的脚本、不动 `e2e_pathfind.py` 本体）。

解析规则是 `EngineConfig.resolved_pathfind_script()` 的**三段择一**：

| 优先 | 来源 | 说明 |
|---|---|---|
| 1 | 配置 `pathfind_script` 非空 | 用户说了算；**不校验存在性** |
| 2 | `<engine.path>\autopilot\tools\e2e_pathfind.py` 存在 | 脚本迁进插件后自动接管 |
| 3 | `<RECOGNITION_ROOT>\exports\benchmark\scripts\misc\e2e_pathfind.py` 存在 | 当前实况 |
| 4 | 都不存在 → 返回第 2 条的位置 | 报错信息指向「本来该在哪」 |

⚠️ 第 1 条刻意**不**做存在性校验：配置错了应该报「找不到寻路脚本：<用户填的
那个>」，而不是悄悄换成另一份脚本去跑。悄悄换 = 用户以为在跑 A，实际跑的是 B。

第 2 条是**为什么不写死路径**：脚本终归要并进插件。写死任何一边，迁移那天
Console 就静默失效；而「第 2 条优先」让迁移本身不需要改 Console。

换机器时改 `config.toml` 的这一行即可：

```toml
[engine]
pathfind_script = ""     # 留空 = 自动查找
```

---

## 十一、验证到什么程度（诚实边界）

✅ **已验证**：

- 坐标往返误差 1.74e-08（世界→像素→世界）
- 仿射系数与源模块一致（`verify_against_reference()` → `diffs: []`）
- 936 条传送点加载、搜索
- 全部参数/解析逻辑 —— Console 回归 **297 passed**
- 离屏截图肉眼确认：真实提瓦特底图、中文渲染正常、选点高亮正确
- **路点文件 schema 喂真实脚本**：`tests/test_waypoints_schema_real.py` 用
  `ast` 抽出 `e2e_pathfind._load_waypoints_file` 的**真实源码**执行，
  确认 Console 写出的文件能解析，且 `WAYPOINTS_SCHEMA` 两边一致（1 == 1）；
  反向也验了：版本号 +1 真脚本立刻判废 —— 说明这条约束真的连着「能不能跑」
- **live 链路的进程级行为**：假脚本边跑边写状态文件，验证了「每帧重写」这个
  前提本身、flag 能让脚本先松键再退出、以及**进程死了状态文件不会消失只会变
  陈旧**（`tests/test_pathfind_live.py`）

❌ **未验证**：

- **没有开原神**。真脚本的 live 发按键、`sprint_off()` 松键、引擎互斥，
  这些只测了**Console 侧的机制**（flag 写没写、进程起没起、状态读没读对），
  没有验证真脚本收到 flag 后真的松了键
- 寻路 tab 的 UI 交互是离屏跑的（`QT_QPA_PLATFORM=offscreen`），
  真实窗口下的观感、滚轮缩放手感未验
- WAYPOINTS 的落盘文件**只验到解析层**，没有真的喂给 `e2e_pathfind` 跑一遍
  （那需要游戏在前台）
- `AUTO` 按设计抛异常，「抛得对不对」只在单测里验过

---

## 十二、相关文档

- `user_waypoints_guide.md` —— 点选路点 → live 寻路操作指南
- `../exports/benchmark/scripts/misc/e2e_pathfind.py` —— 被调脚本（参数表在 `_SPEC`）
- `../exports/benchmark/scripts/misc/coord_transform.py` —— 仿射系数源头