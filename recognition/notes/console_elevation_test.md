# Console 提权实测清单（需要你执行）

> 2026-10-12 ｜ 全程约 5 分钟
> **这是给你执行的，不是 Agent 执行的。** 提权会弹 UAC，Agent 没法替你点。

---

## 为什么必须由你做

`elevation.ensure_admin()` 全靠 Win32 的 `ShellExecuteW(runas)` 发起 UAC，
**成功发起不等于你点了「是」** —— 用户点「否」时提权进程压根不会启动，
调用方察觉不到（`elevation.py:94-97` 的注释写明了这一点）。

所以这条路径**只能真机实测**。目前状态：
`is_admin()` / `_relaunch_argv()` / `ensure_admin(False)` 有逻辑可离线验，
**「点『是』之后新实例能不能起来」零测试、零实测**。

而它的失败后果很隐蔽：Console 显示「已启动」，两秒后进程自己没了，
用户完全想不到是权限问题（`config/schema.py:262-263` 记的就是这个坑）。

---

## 前置

- 当前配置 `require_admin = true`（`<CONSOLE_ROOT>\config.toml:9`）✅ 已经是开的
- 建议先关掉正在跑的 N.E.K.O 宿主和 Console 托盘图标，避免 `single_instance` 干扰
- **不需要打开原神**

---

## 步骤 1 · 确认当前不是管理员

打开 PowerShell（**普通权限**，不要「以管理员身份运行」）：

```powershell
cd <CONSOLE_ROOT>
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe -c "from neko_console.elevation import is_admin; print('is_admin =', is_admin())"
```

**判据**：
- ✅ 通过：输出 `is_admin = False`
- ❌ 不通过：输出 `True` → 说明**这个 PowerShell 本身就是管理员**，
  第 2 步不会弹 UAC。改用「开始菜单 → PowerShell → 右键 → 以管理员身份运行」
  之外的那个普通 PowerShell 再来一次

---

## 步骤 2 · 启动 Console，观察 UAC

同一个 PowerShell 里：

```powershell
.\.venv\Scripts\python.exe -m neko_console
```

**判据**：
- ✅ 通过：屏幕中央弹出 UAC 确认框（深色/蓝色「用户账户控制」窗口，
  显示「Windows 管理员」之类的发布者名）
- ❌ 不通过 A：**没有弹 UAC，Console 直接开了** →
  说明 `is_admin()` 返回了 True（走的是「已是管理员就直接跑」那条路），
  贴回第 1 步的输出
- ❌ 不通过 B：Console 窗口一闪就没了，什么都没弹 →
  贴回 `<CONSOLE_ROOT>\console.log` 的**最后 30 行**
- ❌ 不通过 C：报了 traceback → 贴回**完整 traceback**

> 弹 UAC 时**原窗口会立刻关闭**是**正常且正确**的行为 ——
> `ensure_admin` 发起提权成功后 `raise SystemExit(0)`（`elevation.py:147`），
> 免得留下两个实例。

---

## 步骤 3 · 点「是」，验证新实例起来

在 UAC 上点「是」，然后观察。

**判据**：
- ✅ 通过：几秒后 Console 主窗口出现，**没有再弹第二个 UAC**
  （防重复提权靠 `is_admin()`：提权成功的新进程必然为 True）
- ❌ 不通过 A：Console 起来了但**又弹了一次 UAC** →
  无限提权循环，`is_admin()` 判定有问题。贴回 console.log 最后 30 行
- ❌ 不通过 B：UAC 点了「是」但**什么都没发生** → 贴回 console.log 最后 30 行
  + 任务管理器里有没有残留的 `python.exe` 进程
- ❌ 不通过 C：窗口出现但卡在启动画面 → 贴回
  `<CONSOLE_ROOT>\runtime\console_bridge.json` 的内容

---

## 步骤 4 · 验证提权标识与寻路可用

在 Console 里：

1. 看标题栏或「关于」页，有没有**管理员**标识
2. 切到 **「寻路」** tab

**判据**：
- ✅ 通过：寻路 tab 能打开，**启动按钮不是灰的**，
  护栏提示区没有出现红色的「⚠ 当前不是管理员 —— 寻路不可用」
- ❌ 不通过 A：按钮是灰的，且护栏区有红色警告 →
  说明**实际没提权成功**。贴回那个警告区的**完整文字**
- ❌ 不通过 B：按钮可用但点下去报「路径问题」 →
  那是脚本路径问题不是权限问题，贴回弹出的**完整文字**

---

## 步骤 5 · 验证「点否」时的降级

再做一遍，这次点「否」：

```powershell
.\.venv\Scripts\python.exe -m neko_console
```

UAC 弹窗上点**「否」**。

**判据**：
- ✅ 通过：Console **照常启动**（不是崩溃、不是空白），
  寻路 tab 的启动按钮**变灰**，
  护栏区明确写着「⚠ 当前不是管理员 —— **寻路不可用**」
  （`tabs/pathfind.py:218-225` 就是这段文案）
- ❌ 不通过 A：Console 直接崩了或起不来 → 贴回 console.log 最后 30 行
  （`elevation.py:148-149` 的设计是「提权未成功 → 继续以非提权模式启动」，
  不该崩）
- ❌ 不通过 B：按钮**不是灰的**，点了才报错 → 护栏没接上，贴回截图

---

## 每一步要贴回什么

| 步骤 | 贴回什么 |
|---|---|
| 1 | `is_admin()` 那一行文字 |
| 2 | 有/无 UAC 弹窗（有的话截一张图） |
| 3 | Console 是否起来 + 有没有二次 UAC + 窗口截图 |
| 4 | 寻路 tab 的**完整截图**（要能看到按钮状态和护栏区） |
| 5 | 寻路 tab 的**完整截图**（要点「否」之后的） |

出错时统一规则：**贴原始输出，不转述、不裁剪**。
命令类贴完整 stdout + stderr（含 traceback 全文），GUI 类贴窗口截图。

---

## 附：为什么必须提权（给不熟悉的人）

Windows 里，游戏本体是以**内核级反作弊**运行的。**低权限进程不能给它的
窗口发按键** —— Windows 会直接拦下来，这就是所谓的 UIPI 门禁。

结果就是：寻路脚本以低权限启动 → 按键发不出去 → 3 秒内自己退出 →
Console 却显示「已启动」。

所以不是「提权显得厉害」，而是**不��权这个功能根本用不了**。

---

## 附：想临时关掉提权

把 `<CONSOLE_ROOT>\config.toml` 里这一行改成：

```toml
require_admin = false
```

关掉后 Console 照常启动、寻路按钮置灰并说明原因
（`config/schema.py:265-267`：只用来看状态 / 改规则的用户不需要管理员，
而且 UAC 弹窗本身很烦）。

**改完记得改回来** —— 演示要用寻路的话必须开着。
