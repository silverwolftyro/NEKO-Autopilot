# 人工核验清单 · 全离线（不开原神）

> 2026-10-12 · 用途：**不打开原神**的前提下，把「只能真机验的东西」之外的部分
> 全部核掉，并留下明确证据。
>
> ⚠️ **本清单里已经能自动验的，不会让你手动重复。** 每一项开头都标了
> 「已有自动化覆盖 / 无覆盖」，无覆盖的才是真需要你动手的。

---

## A · 可离线验证的资产清单

### A1 · 真实游戏截图（2 组）

| 文件 | 大小 | 能验证什么 |
|---|---|---|
| `autopilot/_runtime/capdiag_wgc.png` | 4.5 MB · 2560×1600 | **唯一一张**真机原神大世界画面（WGC 抓取）。可验：血条检测、亮度统计、模板匹配、YOLO 前处理。**已知实测结论**：玩家血条 `y=[1500,1514] H=41 S=215 V=215`；夜石地面 `S=183`（饱和度极高，会淹掉通用饱和度检测）；队友血条与玩家血条**颜色完全相同** |
| `NEKO-Console/runtime/cap*_*.png` | 13 张 | ⚠️ **见下方红字，这批数据大部分是假的** |

> 🔴 **2026-10-12 实测打假：`cap3_*` 通道矩阵是复制品，不能用来验黑帧**
>
> 逐文件 MD5 比对结果：
>
> | 文件 | MD5 前 16 位 |
> |---|---|
> | `cap3_A_正常显示_bitblt` | `190CBFC5BF975A4A` |
> | `cap3_B_屏幕最小化_bitblt` | `190CBFC5BF975A4A` ← **同一份** |
> | `cap3_C_最小化_bitblt` | `190CBFC5BF975A4A` ← **同一份** |
> | `cap_normal_bitblt` | `C1D92A6C0E863EC8` |
> | `cap_offscreen_bitblt` | `C1D92A6C0E863EC8` ← **同一份** |
>
> 所谓「{正常显示, 屏幕最小化, 最小化} × {bitblt,dxcam,mss}」的受控矩阵，
> 其实是**同一帧复制了三份**（A/B/C 逐通道全同）；
> `cap_offscreen_bitblt` 甚至与 `cap_normal_bitblt` 字节级相同，
> 标签「offscreen」是假的。
>
> **全盘唯一真实存在的黑帧样本**：`cap_offscreen_mss.png`（5.1 KB，`luma=0.0`）。
>
> 后果：**黑帧判定在「窗口最小化」这个场景下至今没有任何离线样本可验**。
> 照着文件名去验会得出「最小化的图没被判成 BLACK」，然后花时间去修一个
> **根本没被测过**的东西 —— 这比不验更糟。故本清单**撤销**了原第 8 项。

### A2 · OCR 采样日志（1431 条）

`%LOCALAPPDATA%\Local\N.E.K.O\logs\plugin\` 下
`N.E.K.O_Plugin_genshin_game_buddy_2026101{0,1}.log` 里的
`ocr_perceive sample (scenes=N) text_head='...'`，每条附带 `state=` 与 `scenes=`。

可验：场景分类器在真实文字上的行为。**已知实测结论**（2026-10-12）：

- 准确率**算不出来**（日志无 ground truth）
- 触发率 **0 / 1431 = 0.00%**
- 36% 的「IN_GAME」样本其实抓到的是 **Windows 桌面/文件管理器**
- `BAG_INVENTORY` / `COMBAT_RESULT` / `SHOP` 三个场景**一次都没出现**

### A3 · 事件流样本（60+ 个 `.stream.jsonl`）

`autopilot/_runtime/*.stream.jsonl`，最大 624 KB（`20261010_225316`）。

可验：事件码在真实运行里到底发出过哪些、频率如何、黑帧刷屏的形态。
**已知样本**：`20261011_180732`（215 KB）对应 PickMark 排查那轮 ——
`state_snapshot.json` 显示 `intents_executed=151 / intents_rejected=603`。

### A4 · 状态快照样本

| 文件 | 内容 |
|---|---|
| `state_snapshot.json` | 一个完整 run 的收尾状态（`run_id=20261011_180732`、`health=OK`、`tick_hz=4.9`、`raw_stats` 全字段） |
| `session_state.json` | 会话层状态（`active=true`、`plugin_version=3.1.18`、`probe_count=0`） |
| `pathfind_state.json` | 寻路中间态（`state=navigating`、`target_pos=[900,0]`、`eta_s=595`） |
| `overlay_pos.json` / `console_bridge.json` / `run_current.txt` | overlay 位置 / 进程桥 / 当前 run_id 指针 |
| `engine_stderr.log`（152 KB） | 引擎 stderr 全文 |

### A5 · overlay / Console 的历史截图

`NEKO-Console/runtime/` 下 `overlay_dev.png` / `overlay_product.png` /
`dev_fresh2.png` / `dev_stale.png` / `probe_card.png`。

可验：与实机截图**并排比对**，确认渲染没跑偏。

### A6 · 自动化测试资产

`tests/unit/` 共 44 个文件；`<CONSOLE_ROOT>\tests\test_pathfind_channel.py` 18 条。
**下面 B 节里凡是标「已有自动化覆盖」的，依据就是它们。**

---

## B · 人工核验清单（按优先级）

> 所有命令的工作目录都是
> `<REPO_ROOT>\plugin\plugins\genshin_game_buddy`，
> Python 一律用
> `<REPO_ROOT>\.venv\Scripts\python.exe`。
>
> ⚠️ **控制台必须先设 UTF-8**，否则中文输出会 `UnicodeEncodeError`：
> ```powershell
> $env:PYTHONIOENCODING = "utf-8"
> ```

---

## 1 · 规则加载数（离线，1 分钟）

**已有自动化覆盖**：有守卫（`test_active_dir__has_no_unreachable_rules`），
但**「加载数是不是 16」没人肉眼确认过**，而规则数会随改动漂移。

> ⚠️ `run_loop --dry-run` **跑不了** —— 它仍然要建截图器，
> 没有原神窗口会直接失败。所以用下面这条只加载规则、不碰截图器的命令。

**前置**：无

**步骤**：
```powershell
$env:PYTHONIOENCODING = "utf-8"
cd <REPO_ROOT>\plugin\plugins\genshin_game_buddy
<REPO_ROOT>\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from autopilot.tools._stack import build_policy; src,rd,n=build_policy(None); print('rules_dir:', rd.name); print('count:', n); print('enabled:', [r.name for r in src.rules if getattr(r,'enabled',False)])"
```

**判据**（对照 2026-10-12 基线）：
- ✅ 通过：`rules_dir: rules` / `count: 16` /
  `enabled: ['world_attack_only_with_enemy']`
- ✅ 通过：`count` 是 **16** 而不是 36 —— 36 说明 `_future/` 里那 20 条死规则
  被加载了（那是灾难）
- ✅ 通过：`enabled` **只有一个** —— 多了说明有人把 `USE_PICKUP` / `interaction`
  那批开了（你明确说了先不开）
- ❌ 不通过：`count` 不是 16，或 `enabled` 超过一个 → 贴回完整输出

---

## 2 · 黑帧样本抽查（1 分钟）

> ⚠️ **这一项范围比你想的小得多。** 原计划的「通道矩阵回归」已被撤销 ——
> `cap3_*` 那 12 张是同一帧的复制品（见 A1 红字）。下面是全盘**唯一**
> 真实存在的黑帧样本。

**已有自动化覆盖**：有（`test_screen_capture.py` 25 条），但这条路径本身没被真数据走过。

**前置**：无

**步骤**：
```powershell
$env:PYTHONIOENCODING = "utf-8"
cd <REPO_ROOT>\plugin\plugins\genshin_game_buddy
<REPO_ROOT>\.venv\Scripts\python.exe -c @"
from pathlib import Path
import cv2, numpy as np, sys
sys.path.insert(0, '.')
from autopilot.perception.builder import _luma, BLACK_FRAME_LUMA
p = Path(r'<CONSOLE_ROOT>\runtime\cap_offscreen_mss.png')
# cv2.imread 在 Windows 上按 ANSI 码页解路径，中文名直接打不开
# （cap3_* 那批文件名全是中文）。必须走 imdecode + fromfile。
img = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)
L = _luma(img)
print(f'{p.name}  luma={L:.1f}  ' + ('BLACK' if L < BLACK_FRAME_LUMA else 'ok'))
"@
```

**判据**：
- ✅ 通过：`luma=   0.0  BLACK` —— `BLACK_FRAME_LUMA=8.0` 抓住了这一张
- ❌ 不通过：判成 `ok` → 黑帧判定真的坏了 → 贴回完整输出

> ⚠️ **通过不等于验过了**。全盘只有这一张真黑帧样本；
> 「窗口最小化时各通道分别是什么表现」**至今没有任何离线证据**。
> 要补这个数据必须**开着原神**把窗口最小化，让 `capture_diagnose`
> 各通道各存一张 —— 存完**先跑 MD5 确认三组真的不同**，再看亮度。
> 这次就是因为没先查 MD5，才差点把复制品当成回归基线。

---

## 3 · 模板裁图工具 · 自动检测（3 分钟）

**已有自动化覆盖**：33 条单测（合成图），但**真机截图上的效果只有一条人工结论**。

**前置**：无

**步骤**：
```powershell
$env:PYTHONIOENCODING = "utf-8"
cd <REPO_ROOT>\plugin\plugins\genshin_game_buddy
<REPO_ROOT>\.venv\Scripts\python.exe -m autopilot.tools.template_cropper --image autopilot\_runtime\capdiag_wgc.png --no-save --save-preview <CONSOLE_ROOT>\runtime\cropper_check.png
```
然后打开 `<CONSOLE_ROOT>\runtime\cropper_check.png` 看框。

**判据**（照抄 2026-10-12 的实测基线）：
- ✅ 通过：`玩家血条 找到 1 个，最佳 box=(1076, 1500, 408, 15) 覆盖率=98%`
  → 预览图里底部那条绿框**完整包住**血条（含中间 `17640 / 17640` 文字）
- ✅ 通过：`元素能量条 没找到` —— 这是**正确结果**。那张截图里角色能量为 0，
  能量条是空的。「没找到」不是 bug
- ❌ 不通过：血条 `覆盖率` 明显低于 90%，或框宽明显 ≠ 408px
  → 贴回终端原文那一行 + `cropper_check.png`
- ❌ 不通过：脚本报异常 → 贴回**完整 traceback**（别裁剪）

---

## 4 · overlay 状态卡片（开发者版，3 分钟）

**已有自动化覆盖**：无 —— overlay **纯读文件**渲染（源码原话：「关掉它不影响
run_loop，跑_loop 崩了 overlay 照显示，离线也行」），离线可跑，但渲染效果没人眼看过。

**前置**：无（不依赖引擎在跑）

**步骤**：
1. 记下当前 run_id：
   ```powershell
   Get-Content <REPO_ROOT>\plugin\plugins\genshin_game_buddy\autopilot\_runtime\run_current.txt
   ```
   → 期望看到 `20261011_180732`
2. 启动开发者版 overlay：
   ```powershell
   $env:PYTHONIOENCODING = "utf-8"
   cd <REPO_ROOT>\plugin\plugins\genshin_game_buddy
   <REPO_ROOT>\.venv\Scripts\python.exe -m autopilot.tools.overlay --mode dev
   ```
3. 和历史基线图并排比：
   `<CONSOLE_ROOT>\runtime\dev_fresh2.png` / `dev_stale.png` / `overlay_dev.png`

**判据**：
- ✅ 通过：overlay 窗口出现，**状态行有内容**（不是空白/全 `—`），
  中文字正常显示（**没有方块/问号**）
- ✅ 通过：卡片上能看到 `health=OK` / `tick_hz≈4.9` 这类**真实数字**
  （来自 `state_snapshot.json`），不是 0 也不是空
- ❌ 不通过：窗口空白或全是 `—` → 贴回 `run_current.txt` 内容 +
  该 run_id 对应的 `.stream.jsonl` 文件大小
- ❌ 不通过：中文变成方块/问号 → 贴回一张 overlay 窗口截图
- ❌ 不通过：根本起不来 → 贴回**完整 traceback**

---

## 5 · OCR 场景分类器 · 真实数据评估（2 分钟）

**已有自动化覆盖**：14 条合成单测。真实数据评估工具已写好但**结论存疑**，需要你确认。

**前置**：无（日志在磁盘上）

**步骤**：
```powershell
$env:PYTHONIOENCODING = "utf-8"
cd <REPO_ROOT>\plugin\plugins\genshin_game_buddy
<REPO_ROOT>\.venv\Scripts\python.exe -m autopilot.tools.ocr_scene_eval --log-dir "%LOCALAPPDATA%\Local\N.E.K.O\logs\plugin" --show 5
```

**判据**（对照 2026-10-12 基线）：
- ✅ 通过：样本数 ≈ **1431**，标签分布 `WORLD 947 / UNKNOWN 484`，
  触发率 **0.00%**，桌面污染 **516 条 = 36.1%**
- ✅ 通过：「完全没出现的场景」列出 `BAG_INVENTORY, COMBAT_RESULT, SHOP`
  —— 这三项「没出现」**是对的**，说明日志里压根没有对应画面
- ❌ 不通过：数字差很多（比如样本数上千）→ 贴回终端**完整输出**
  （多半是日志被轮转了或路径写错）

**你要回答的一个问题**：这批日志里**有没有你想验的界面**？
如果没有 → 这项就到此为止，补采必须开着原神。

---

## 6 · YUI 台词映射表（2 分钟）

**已有自动化覆盖**：**已有，不需要你动手验映射**。
`test_core_primitives.py` 里有一条守卫：任何事件码既没有台词、也不在
`SKIP_CODES`、也不在 `_PENDING_HUMAN_DECISION` 里 → 测试红。
2026-10-11 你已拍板 8 个码全部落地，该集合现在**刻意为空**。

所以只剩一件事值得跑一下，确认守卫还活着：

**前置**：无

**步骤**：
```powershell
$env:PYTHONIOENCODING = "utf-8"
cd <REPO_ROOT>\plugin\plugins\genshin_game_buddy
<REPO_ROOT>\.venv\Scripts\python.exe -m pytest tests/unit/test_core_primitives.py -q
```

**判据**：
- ✅ 通过：`17 passed`
- ❌ 不通过：红，且信息里列出了没被覆盖的码 → 那就是**真待决事项**，
  贴回红字里 `uncovered` 的完整列表

---

## 7 · PathfindChannel 停止逻辑（1 分钟）

**已有自动化覆盖**：✅ **18 条，不需要你动手**。

`<CONSOLE_ROOT>\tests\test_pathfind_channel.py` 已覆盖：
flag 路径、terminate 兜底、写 flag 先于 terminate、未在运行时 stop、
半截 JSON、陈旧状态文件、进程退出后状态变化等。

**前置**：无

**步骤**：
```powershell
cd <CONSOLE_ROOT>
<CONSOLE_ROOT>\.venv\Scripts\python.exe -m pytest tests -q
```

**判据**：
- ✅ 通过：`18 passed`
- ❌ 不通过：贴回完整输出

---

## 8 · Console 提权（UAC，2 分钟）

**已有自动化覆盖**：无 —— `is_admin()` / `_relaunch_argv()` 只能验逻辑，
**提权后新实例能否启动、UAC 是否弹，没人验过。**

**前置**：关掉正在跑的 N.E.K.O 宿主和 Console（托盘图标右键退出）——
关掉原神更好，避免误触发。

**步骤**：
1. 开一个 PowerShell：
   ```powershell
   cd <CONSOLE_ROOT>
   $env:PYTHONIOENCODING = "utf-8"
   .\.venv\Scripts\python.exe -m neko_console
   ```
2. 观察屏幕中央是否弹出 UAC 确认框

**判据**：
- ✅ 通过：UAC 弹窗出现 → 点「是」→ Console 窗口打开 → 标题栏或「关于」页
  显示**管理员**标识 → 寻路 tab 能点开
- ❌ 不通过 A：**没有 UAC 弹窗**，Console 直接开了 → 贴回
  `<CONSOLE_ROOT>\console.log` 的**最后 30 行**
- ❌ 不通过 B：UAC 弹了但点「是」后**什么都没发生** → 贴回
  `console.log` 最后 30 行 + 任务管理器里有没有残留的 `python.exe`
- ❌ 不通过 C：Console 开了但**一直卡在启动画面** → 贴回
  `<CONSOLE_ROOT>\runtime\console_bridge.json` 的内容

---

## C · 建议的执行顺序（今晚）

按「**最便宜 × 失败后果最严重**」排：

| 顺位 | 项 | 耗时 | 为什么是它 |
|---|---|---|---|
| 1 | **1 · 规则加载数** | 1 min | 最便宜。直接回答你上轮强调的两条：死规则有没有复活、规则有没有被误开 |
| 2 | **2 · 黑帧样本抽查** | 1 min | 一条命令出结果。⚠️ 但它只覆盖一张图，别当成「黑帧验过了」 |
| 3 | **3 · 模板裁图** | 3 min | 有明确历史基线（408px / 98%）可比对，不靠主观判断 |
| 4 | **4 · overlay 卡片** | 3 min | 纯读文件零风险，且有 4 张历史截图可并排比 |
| 5 | **5 · OCR 评估** | 2 min | 结论已知（触发率 0%），跑一遍确认没漂移即可 |
| 6 | **6 / 7 · YUI + Pathfind** | 3 min | 已有自动化覆盖，顺手跑一下确认守卫还活着 |
| 7 | **8 · Console 提权** | 2 min | 重要但**放最后**：它要弹 UAC，会打断你手头的事；失败也不影响前 6 项 |

**前 6 项约 11 分钟**，全部不碰原神、不弹 UAC。

---

## D · 本清单管不到的（必须真机）

明确列出来，免得跑完这 11 分钟就以为万事大吉：

| 项 | 卡在哪 |
|---|---|
| 黑帧判定 · 窗口最小化 | `cap3_*` 矩阵是复制品，**必须重采**；且要先 MD5 验证再采信 |
| OCR 场景分类器准确率 | 需要在游戏里依次打开背包/地图/对话/商店并停留采样 |
| 能量条模板参数 | 那张真机截图里角色能量为 0，能量条是空的 —— 需要**有能量**的画面 |
| 定位服务建原点 | `LocalizationService` 必须先**传送一次**才能有起点 |
| `USE_PICKUP` / `interaction` 规则 | 信号已全活，但你说先不开；开之前需真机跑一轮看实际触发频率 |
| WGC 集成路径 | 需要原神在**无边框全屏**下跑主循环 |
| Console 提权后的实际功能 | UAC 之外的部分（寻路 tab 真操作）仍需原神 |

---

## E · 贴回规范

不通过时，**贴回原始输出**，不要转述、不要裁剪：

- 命令类 → **完整 stdout + stderr**，含 traceback 全文
- 图片类 → 说明**哪个文件** + 你的观察（框在哪 / 缺什么 / 什么颜色异常）
- GUI 类 → **窗口截图**
- 只贴**相关的那几行**是可以的，但**不许改写数字**

---

## F · 这次核验里发现的数据问题（存档）

| 发现 | 处置 |
|---|---|
| `cap3_*` 通道矩阵是同一帧的三份复制品 | 原第 8 项「黑帧矩阵回归」**已撤销**，替换为单文件抽查 |
| `cap_offscreen_bitblt` 与 `cap_normal_bitblt` 字节级相同，标签「offscreen」是假的 | 同上 |
| `cv2.imread` 在 Windows 上按 ANSI 码页解路径，中文文件名直接打不开 | 清单里的命令一律改用 `cv2.imdecode(np.fromfile(...))`；**这条对项目代码可能同样成立，值得单独立一条** |
| OCR 日志里 36% 的「IN_GAME」样本抓到的是 Windows 桌面/文件管理器 | 该批数据验证不了场景分类器，需重采 |
