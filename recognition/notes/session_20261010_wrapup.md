# 2026-10-10 凌晨排查收尾

> 范围：`genshin_game_buddy` 插件。只记**已验证**事实，未验证的推断单独标注。
> 基线：`6 failed / 611 passed / 1 xfailed`（插件目录下跑 pytest）。

---

## 一、今天修了什么

### 1 · `win32process` typo（3 处）—— 已修 + 真机验证

`GetWindowThreadProcessId` 属于 **`win32process`**，代码写成了 `win32gui.`。
每个窗口抛 `AttributeError`，又被枚举里的裸 `except Exception: return True` 全吞掉：

| 行 | 函数 | 修之前的下场 |
|---|---|---|
| L124 | `find_genshin_window` | 恒返回 `None` |
| L167 | `_enum_large_visible_windows` | 恒返回 `[]` |
| L200 | `find_foreground_window` | 恒失败，兜底路径也死 |

第 2、3 条是顺带挖出来的：**`拍板 2.0.15` 的云游戏兜底从来没生效过**，因为兜底函数自己也是空的。

**单测为什么没抓到**：`tests/unit/test_screen_capture.py:436` 把这个 API 塞进了**假 `win32gui`**。
替身"假装"该 API 存在，把 bug 一起认证成正确的。已挪到假 `win32process`，并加断言：假 `win32gui` 里不许再有它。

**新增**：`_warn_window_skip()`（按 `位置/异常类型/消息` 去重，预算 3 条/类）；
三条测试（真机 pywin32 布局守卫、**AST** 静态守卫、告警可见性+去重）。
静默兜底保留，但不再不留痕迹。

**真机验证**：

```
find_genshin_window()        -> {'hwnd': 4065252, 'pid': 155424, 'title': '原神', 'rect': (-21333, ...)}
find_foreground_window()     -> {'hwnd': 67228, 'pid': 21928, 'title': 'MiniMax Code', 'rect': (278, 206, 1347, 965)}
_enum_large_visible_windows() ->  10 个
去重告警集合 = set()          # 零异常，修复完整
```

**顺带修掉一条"靠 bug 才通过"的测试**：`test_no_window_throttle_reopens_after_window`
不 stub 窗口、直接调真函数——能过只是因为真函数是坏的。已补 `_stub_window`。

### 2 · `session_probe` 会话自动建立 —— 已部署，**但仍未生效**

`@timer_interval(id="session_probe", seconds=5)`，扫到窗口就登记会话。日志确认已上线：

```
01:22:06 … Started timer 'session_probe' every 5s
01:22:06 … 会话探测（timer）没找到游戏窗口，引擎继续空转；启动原神后会自动建立会话
（此后每 60s 精确一条：01:23:06 / 01:24:06 / 01:25:06 / 01:26:06）
```

节流 `_PROBE_LOG_THROTTLE_S = 60.0`（`__init__.py:946`）。`_probe_last_log_at` 只在
no_window 和成功两个分支更新，**失败分支不更新**——真故障反而每 5s 刷 WARNING，藏不掉。

**但 `会话已建立` 至今 0 条，`no_session` 持续刷。** 原因见第二节第 1 条。

### 3 · overlay 卡片会话状态 —— 已完成

新增 `autopilot/_runtime/session_state.json`（`SESSION_SCHEMA_VERSION=1`），
插件用 `atomic_write_text` 写、`layout.load_session_state()` 读。
`build_panel()` 只占第 1 行标签位，行数恒为 5；过期（>90s）返回 `None` 而非旧值。

```
● 无会话 · 运行中   12.5 Hz   88ms P95     (行数=5, 第1行色=#fab387 橙)
● 原神 · 运行中     12.5 Hz   88ms P95     (行数=5, 第1行色=#a6e3a1 绿)
```
**有会话走相位绿、无会话用橙**——用绿色会让人以为一切正常。

### 4 · `event_to_yui` 的 `visibility=["chat"]` —— 已完成

新增 `VERBATIM_CHAT = True`，`push_one()` 改用 `visibility=["chat"]`，让 YUI 原样说出对话内容，
而不是只进 LLM 上下文、用户只看 AI 复述。
`direct_reply`（真正逐字语音）走不到——`proactive_bridge.py:421-446` 构造
`proactive_message` 时**从不设这个键**。未改。

---

## 二、今天没解决的阻塞

### 1 · 宿主进程里 `find_genshin_window()` 返回 None ← **头号阻塞**

**同一份字节码，两种结果：**

| 场景 | 结果 |
|---|---|
| 独立进程直载安装副本那份 `screen_capture.py` | ✅ 正常返回原神窗口 |
| N.E.K.O.exe 的插件子进程 | ❌ 返回 `None` |

已排除：部署问题（INST sha256 与源码一致 `9f30f6dabdad07fb`，mtime `01:22:03`）、
.pyc 陈旧（编译于 `01:22:05`，含 win32process）、多份文件（只有 1 份）、vendor 遮蔽、
异常被吞（`_ENUM_SEEN_ERRORS` 为空，零异常）。
→ 差异只在**进程环境**（window station / 会话 / 权限）。**这一步尚未验证。**

### 2 · 引擎启动后截图失败 → 主循环暂停

`20261010_013124` 轮事件流原文：

```
CAPTURE_FAILED   WARN   截图失败：截图重试 25 次仍未成功：PrintWindow 返回失败
CORE_PAUSED      INFO   主循环已暂停（连续 10 次截图失败（游戏窗口可能已关闭））—— 感知继续，执行已停
```

用户观察：即使原神在前台显示，PrintWindow 仍失败。
**可能与阻塞 1 同根**（都指向"这个进程拿不到游戏窗口"）——**推断，未验证**。

附带事实：原神窗口 rect 是 `(-21333, -21333, -21175, -21307)`，屏幕外 158×26，
**最小化**窗口的经典坐标。测抓屏前先还原窗口。

---

## 三、明天第一步

```powershell
# 1) 重新打包
cd <REPO_ROOT>
python -m plugin.neko_plugin_cli build genshin_game_buddy

# 2) 插件中心导入 plugin\neko_plugin_cli\target\genshin_game_buddy.neko-plugin
# 3) 重启 NEKO，等 15 秒（≥3 个 tick）

# 4) 贴这段输出
Get-Content %LOCALAPPDATA%\Local\N.E.K.O\logs\plugin\N.E.K.O_Plugin_genshin_game_buddy_YYYYMMDD.log -Tail 60 |
  Select-String '窗口普查|会话已建立|会话探测|扫窗口失败|跳过一个窗口'
```

**判读 `[窗口普查]` 那几行**（格式：模块/pid/python/回调N 可见N 有标题N/win32gui有API win32process有API 进程名表N/标题样本）：

- **样本里有「原神」** → 枚举正常，问题在读标题/取 pid 的权限，查宿主完整性级别（UIPI）。
- **样本里没有「原神」** → 宿主压根看不见游戏窗口，怀疑不同 window station；比对
  `GetProcessWindowStation` / `GetThreadDesktop`。
- **`回调=0` 或 `可见=0`** → 没有可用窗口站，坐实上一条。
- **`win32gui有API=True`** → 宿主 pywin32 版本不同，typo 修复没生效，先查它用哪个 python。
- 对照（普通进程已实测）：`回调=410 可见=39 有标题=25`，正常返回原神。

---

## 四、关键文件与路径

```
插件源码     <REPO_ROOT>\plugin\plugins\genshin_game_buddy\
安装副本     %LOCALAPPDATA%\Local\N.E.K.O\.neko-plugin-installations\plugins\genshin_game_buddy\
运行时目录   <插件>\autopilot\_runtime\   session_state.json / state_snapshot.json / run_current.txt / *.stream.jsonl
插件日志     %LOCALAPPDATA%\Local\N.E.K.O\logs\plugin\N.E.K.O_Plugin_genshin_game_buddy_YYYYMMDD.log
主控台日志   <CONSOLE_ROOT>\console.log
```

**两条加载路径不能混：**
- **引擎子进程** `cwd` = **源码树** → `autopilot/` 永远跑最新源码，**不需要打包**。
- **插件宿主**从**安装副本**加载 `__init__.py` / `screen_capture.py` / `ui/*.tsx`
  → **必须重新打包 + 导入 + 重启**。
- `ui/` 无构建产物（无 `package.json` / `dist/`），宿主直接加载 `.tsx`。

```powershell
# overlay 独立跑（cwd 必须是插件目录，否则 ModuleNotFoundError: autopilot）
cd <REPO_ROOT>\plugin\plugins\genshin_game_buddy
<REPO_ROOT>\.venv\Scripts\python.exe -m autopilot.tools.overlay

# 主控台（推荐源码模式；build\Console.exe 构建于 09-29 10:47，落后 14 个源文件）
cd <CONSOLE_ROOT>
.\.venv\Scripts\python.exe -m neko_console

# 跑插件测试（必须在插件目录下跑）
cd <REPO_ROOT>\plugin\plugins\genshin_game_buddy
<REPO_ROOT>\.venv\Scripts\python.exe -m pytest tests -q
```

---

## 五、测试基线

**历史遗留 6 红**（非今日引入，保持原样）：
`test_manifest__identity_is_consistent` ×1（`pyproject.toml` `2.0.73b0`
vs `plugin.toml` `3.1.16`）；`test_redline__no_removed_result_module_or_requirements_txt` ×4
（`requirements-autopilot.txt` 违反红线）；`test_redline__no_import_time_side_effects` ×1
（`__init__.py` 顶层 `logging.getLogger`）。

**环境污染 3 红**（跑过真引擎就会红，与代码无关）：
`tests/integration/test_plugin_entries.py` 的 `test_refresh_game_context__reuses_current_game` /
`test_push__injection_visibility_is_empty` / `test_push__injection_uses_read_not_respond`。
原因：`tests/conftest.py:276-282` 的 `storage_root` 只重定向 `NEKO_STORAGE_SELECTED_ROOT`
（宿主存储根），**没重定向 `autopilot/_runtime`**；`startup()` 里启动的事件适配器读真实
运行时目录，按 `run_current.txt` 重播最近一轮事件流 → 多 6 次 push（断言要 2、实际 7）。
**修法**：让 `storage_root` 连带把 `_runtime` 指到 scratch。**未做。**

---

## 六、其他已记录但未处理

- **面板按钮加错文件**：「🎯 检测并激活游戏」加在 `ui/panel.tsx`（设置面板），用户看的是
  `ui/autopilot_panel.tsx`（自动驾驶面板，含"启动引擎"）。两 surface 独立可同时打开
  （`plugin.toml:100/112`）。**未搬。**
- **编码损坏 10 处**（注释内替换字符，Console 2 + 插件 8），不影响运行；有几处原字
  无法可靠还原，未改。
- **`find_genshin_window()` 对鸣潮是盲的**：`_GAME_TITLE_HINTS` 没有鸣潮，而它被复用为
  第二个注册游戏 `wuthering_waves` 的探测。**未改。**
- **`_crop_hp_bar` 退化帧直接 `ValueError`**（`<RECOGNITION_ROOT>\exports\benchmark\scripts\misc\coord_scene_tree.py:116-119`）：
  血条 ROI 高度只占帧高 1.04%，帧高 < 110 必崩，`core.loop` 当致命错误。
  该文件**不属于这两个仓库**，改前先确认归属。**未改。**

---

## 七、开发者版 overlay 排障速查

面板不更新时，**先看最后一行摘要**（三种状态各有处置）：

| 摘要行前缀 | 含义 | 处置 |
|---|---|---|
| `⚠N秒无新事件（引擎可能已停）` | overlay 活着，**引擎**停了 | 拉起引擎，别动 overlay |
| `⚠reader异常(...)` | **我们自己的读侧**炸了（文件轮转/句柄失效/JSON 坏行） | 重启 overlay；stderr 有完整 traceback |
| `⏸已暂停` | 你自己按了空格 | 再按空格 |

**为什么需要前两条**：引擎停了就再也没有新事件，面板永远停在最后一屏——
而「停在最后一屏」和「overlay 卡死」在画面上**一模一样**。2026-10-10 13:42 真机踩过：
引擎 `STOPPED` 之后 overlay 被误判成卡死，实际它一直在正确地读一个不再更新的文件。

### 事件流跟的是哪个 run

摘要行里的 `run:xxx` 来自 **`state_snapshot.json` 的 `run_id`**，**不是** `run_current.txt`。
后者是 Console 侧写的另一套，两者不一致时**以快照为准**——引擎换 run 时先写新快照再写事件。

### 怎么认出僵尸 overlay

重复启动会留下多个 overlay，都显示同一块区域（`overrideredirect` + `-topmost`），
肉眼只看得见最上面那个，而底下那个可能早就死了——于是「改了没生效」。**按命令行认，不按窗口认：**

```powershell
# 列出
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*autopilot.tools.overlay*' } |
  ForEach-Object { "pid=$($_.ProcessId) 启动=$($_.CreationDate)" }

# 一次清空
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*autopilot.tools.overlay*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

⚠️ 一条 `.venv\Scripts\python.exe -m ...` 会显示成**两个**进程（venv 启动器 + 真实解释器），
启动时刻相同——那是**同一个** overlay，别当成两个僵尸去杀。
产品版窗口标题是 `N.E.K.O.`，开发者版是 `N.E.K.O. [dev]`。

### 三通道抓图体检

```powershell
python -m autopilot.tools.capture_diagnose --title 原神 --save
```

**必须在游戏前台显示时跑**（最小化时客户区退化成 0x0，三条通道全都拿不到，
这不是 bug）。看「平均亮度」列：`>=8` 有内容、`<8` 黑帧、`None` 打不开。
BitBlt 抓黑屏是 Win11 已知问题，修复需在**游戏启动前**执行
`apply_win11_bitblt_fix(apply=True)`。

---

# 2026-10-10 晚间：重启流程 + 颜色主题

> 范围：`autopilot/perception/capturer.py`、`autopilot/ui/overlay/theme.py`、
> `autopilot/ui/overlay/app.py`、`autopilot/tools/overlay.py`、`autopilot/config/defaults.toml`。
> 约束：不引入新依赖。

## 一、引擎轮询等窗口（最多 30 秒）

`Capturer.WINDOW_WAIT_S = 30.0` / `WINDOW_POLL_S = 0.5`，构造参数 `window_wait_s` 可覆盖
（`0` = 不等，单测/CI 路径）。`open()` 入口先过 `_wait_for_window()`。

判**客户区可用**而不是判「找到」——最小化的窗口 `_refresh_window` 返回 `True`，
只看返回值会误判成可以开工。新增 `_rect_usable()` 拦住 `(0,0,0,0)` 这种**非空但不可用**
的元组，以及 `(-32000,-32000,...)` 最小化哨兵。

等待循环里必须用 `_refresh_window(force=False)`：`force=True` 在找不到窗口时是
**抛** `WindowNotFoundError` 而不是返回 False，用 `force=True` 会让等待循环第一轮就炸。

**实测**（3.02s / 0.000s / 4.51s 见当轮终端输出）：窗口永不出现 → 等满上限后带
最后矩形报错；`window_wait_s=0` → 0.000s 立即抛；窗口 4 秒后出现 → 等 4.51s 自动接上，
激活 `bitblt`，首帧 1440x1080 luma 30.6。

## 二、dxcam 抛异常也降级

根因：`Invalid Region: Region should be in 2560x1600` 是**普通异常**，落在 `grab()` 的
`except Exception` 分支，而那个分支原先只刷新窗口、**从不调** `_demote_channel()`，
于是空转到「截图重试 25 次仍未成功」，通道永远不降级。

推论写进注释了：一条通道「抛异常」和「返回空」一样都是它在当前窗口上不可用；
只有 `WindowNotFoundError` 才是窗口本身没了（那个真没救，重抛）。
降不了级且矩形不可用时，直接抛说人话的 `CaptureError`，不再让用户去猜 dxcam 内部报错。

## 三、默认链改成 bitblt 优先

`capturer._normalize_order(None)` / `defaults.toml` / `loader.py` 三处统一为
`["bitblt", "dxcam", "mss"]`。理由不只是「更可靠」，而是**故障类型不覆盖**：
BitBlt 走窗口自己的 GDI DC，完全不看屏幕坐标，`Invalid Region` 这类问题发生在它身上。

`--force-channel`（`run_loop.py`）用「把 `channel_order` 收成单元素」实现，
避免改 `_stack.py`。别名表实测为 `bit_blt / gdi / dx / dxcam / mss`——**没有 `dxgi`**，
上一轮口头汇报说有过，是记错了。非法值在 argparse 层就拦，退出码 2。

## 四、三套颜色主题

`theme.THEMES` = dark / light / high-contrast，每套一套颜色常量。
`apply_theme()` 写模块全局（消费方都是 `theme.BG` 这种属性访问，改完立刻生效）。
`next_theme()` 给 `Alt+T` 用。`tools/overlay.py` 的 `--theme` **必须在 `build()` 之前**
应用——widget 颜色是构造时烤进去的；运行中切换靠 `app._force_theme_repaint()` 逐控件改背景色
（Label 前景/背景是构造时烤的，`root.configure` 只改根窗口，子控件会盖住）。

`_theme_key()` 归一化要同时去掉 `-` `_` 和空格：只 replace 连字符的话
`"high-contrast"` → `"high_contrast"` 而键名还是连字符，**匹配不到自己那套主题**。

**实测**：`dark/light/high-contrast/HIGH_CONTRAST/high contrast` 五种写法全部正确命中；
`layout` 和 `dev_panel` 都是运行时读 `theme.ACCENT_*`，切主题后跟着变。

## 五、回归

`8 failed / 609 passed / 1 xfailed`（连跑两次一致）。构成：4×`requirements-autopilot.txt`
违规 + 1×`__init__.py` 顶层 `logging.getLogger` + 3×被真实 `_runtime` 状态污染的集成测试。
全为既有基线，零新增。

**一条真 flaky**：`tests/unit/test_change_detect.py::test_light_capture_dhash__static_screen_returns_same_hash`
会真抓两次**桌面**屏幕比 dHash，桌面内容一变就红（某轮 9 failed 就是它）。单独复跑 3 次
全绿。它不碰本轮改动的任何代码。

## 六、仍未验证 / 未修

- **原神未运行**（`YuanShen.exe` 进程数 0），真机验证全用 tkinter 测试窗代替。
  硬件加速游戏特有的 BitBlt 黑屏行为未验证。
- `screen_capture.find_genshin_window()`（主路径）仍依赖 pywin32+psutil，在宿主里依然失败，
  本轮只让兜底路径不依赖第三方。
- 主路径假阳性未修：会命中 `genshin_game_buddy - 文件资源管理器`（标题含 "genshin"）。
- `tools/overlay.py` 落在本轮「只改 4 个文件」的约束之外，为的是 `--theme` 能落地，已标注。

---

# 2026-10-10 深夜：黑帧「判可用」而不是「判不可用」

> 现象：引擎启动后连续 2655 tick `BLACK_FRAME`，**零** `CAPTURE_FAILED`；
> 同时段独立跑 `capture_diagnose` 三条通道全亮。范围：只改 `capturer.py`。

## 一、证据（全部来自运行时文件，不是推断）

`20261010_225316.stream.jsonl`（22:53:16 起，207 秒，2655 tick）：

```json
{"code": "BLACK_FRAME", "context": {"luma": 0.0}}
{"code": "PERCEPTION_DOWN", "message": "感知层判定本帧不可信（PERCEPTION_DOWN），已自动暂停"}
{"code": "CORE_PAUSED", "message": "主循环已暂停（感知层不健康）—— 感知继续，执行已停"}
```

`state_snapshot.json`：`capture_failures: 0`、`states_built: 0`、
`perception_failures: 2655`、`capture.active: ""`。

两个关键点：

1. **`luma` 是逐帧精确的 `0.0`**，不是「偏暗」。真·全黑图每一个像素都是 0，
   暗场景（夜晚）平均亮度在十几到几十。阈值 8 足够分开。
2. **「2655 个 BLACK_FRAME」不等于「2655 次失败」**。引擎 tick 3 就
   `PERCEPTION_DOWN` → `CORE_PAUSED`，之后是**暂停态下感知继续跑**，
   每 tick 继续发一条 BLACK_FRAME。所以用户看到 2655 条，是**一个**故障
   被播报了 2655 次，不该按「2655 次失败」去查重试/阈值。

## 二、根因

`open()` 判「通道可用」只有两条：**抓不抛异常** + **尺寸非 0**。
一张 2560x1600 的纯黑图两条都能过 —— 尺寸对、内容空。

- DXCAM：`probe is not None and probe.size > 0`
- BITBLT：`probe is None or probe.size == 0` 才 return False
- MSS：**根本不探测**，直接 `return True`（三条里唯一「从没验证过就宣布可用」的）

所以没有任何一层会降级，引擎真心觉得自己在工作。

最可能的**机制**（未在真机复现，见下）：`_BitBltSession` 的
`GetDC(hwnd)` 是**构造时缓存、永不刷新**的。窗口在 `open()` 之后换了显示模式
（无边框↔全屏）时，这个 DC 变成僵尸句柄 —— `BitBlt` 仍然**返回成功**，
但什么都没写进那块**零初始化**的 DIB，于是每帧都是一张合法的全黑图。

## 三、修法

`_init_channel` 只判「抓得到」（mss 补上探测）；**黑帧判定挪到 `open()` 的通道循环里**，
每条通道有 `VISIBLE_WAIT_S = 10.0` 秒交出亮画面的宽限期，超时换下一条。

宽限期是必须的：第一版把「一帧黑就否决」写在 `_init_channel` 里，
结果**正在正常加载的游戏会被立刻打死** —— 那比现状更糟。
门在循环里，才能「等它亮」。

新增 `BLACK_FRAME_LUMA = 8.0`（与 `builder.BLACK_FRAME_LUMA` 同值，两层口径一致，
免得出现「截图器说有画面、感知层说全黑」的互相打架）、`VISIBLE_WAIT_S = 10.0`、
构造参数 `visible_wait_s`、`_wait_for_visible_frame()`、模块函数 `_frame_luma()`。
启动失败信息新增第 5 条：画面全黑 → 指向 `apply_win11_bitblt_fix` / 无边框窗口。

## 四、实测（tkinter 子进程造时序，18/18 PASS）

| 场景 | 结果 |
|---|---|
| 窗口一出现就是亮的 | `open()` 0.01s 成功，bitblt，帧 luma 51.7 |
| 窗口先黑，4 秒后变亮 | **等 4.08s 自动接上**，不等也不否决 |
| 窗口永远全黑 | 6.69s 后抛错，三条通道各记一条「画面一直全黑」，**不再空转** |
| `visible_wait_s=0` | 0.00s 直接接受（单测/CI 语义） |

回归 `8 failed / 609 passed / 1 xfailed`，与基线一致，零新增；ruff 全过。

## 五、仍未验证 / 已知缺口

- **僵尸 GetDC 只是最可能的机制，没有在真机复现**（原神未运行）。
  这次改的是「症状出口」：黑帧不再被当成可用，最坏结果是引擎带明确报错退出，
  而不是跑满 2655 个 BLACK_FRAME。
- **通道名仍然看不见**：引擎确实 `logger.info("capture channel active: %s")`，
  但只进 **stderr**，不进事件流也不进快照。`capture.active` 读的是
  `get_active_channel()`，引擎停止后 capturer 已释放就抛异常 → 快照里为空串
  → 卡片显示「通道: —」。**这条要改 `run_loop.py`，超出本轮约束，只记录。**
- **黑帧只在 `open()` 这一次把关**。运行中某帧转黑（游戏切屏、加载）不会降级，
  仍由 builder 发 BLACK_FRAME。要做运行期降级得改 `builder.py`。
- `capdiag_*.png` 是 **18:19** 的，引擎那次跑是 **22:53**，
  「诊断亮 / 引擎黑」这个对比本身跨了 4.5 小时、显示模式可能已经变了。