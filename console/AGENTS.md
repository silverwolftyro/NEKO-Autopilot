# NEKO-Console 开发约定

给 AI 助手和后续维护者看的。**违反了不会立刻出事，但会变成线上问题。**

---

## 版本号

- **唯一来源**：`neko_console/__init__.py` 的 `__version__`
- `pyproject.toml` **不写死版本号**，通过
  `[tool.hatch.version] path = "neko_console/__init__.py"` 动态读
- **每完成一个任务包，patch 号 +1**（0.1.15 → 0.1.16 → …）
- **不要进位到 0.2.x** —— 直到正式发布
- 改动报告里必须写明"版本号 X → Y"

三处必须一起变（都从 `__version__` 读，不要各写一份）：

| 位置 | 代码 |
|---|---|
| 窗口标题 | `app.py` → `f"NEKO-Console v{__version__}"` |
| 启动日志首行 | `main.py` → `_write_banner()` |
| 关于页 | `tabs/about.py` → `from .. import __version__` |

### 坑：`__compiled__` 不在当前模块的 globals 里

Nuitka 注入的是 `sys.modules["__main__"].__compiled__`，不是导入它的模块。
所以**永远不要**写 `"__compiled__" in globals()` —— 那在打包模式下
也永远是 False，会让日志里的 `packaged=` 恒为 `no`。

一律用 `neko_console.paths.is_frozen()`。

---

## 路径纪律

- 所有修改**只允许**在 `<CONSOLE_ROOT>\` 内
- **禁止触碰** `<WORKSPACE_ROOT>\N.E.K.O-main\`（那是引擎，独立仓库，Apache-2.0）
- **禁止触碰** `<WORKSPACE_ROOT>\_archive\`
- **禁止创建** `<WORKSPACE_ROOT>\` 根目录下的任何文件

### 高危目录（历史踩过）

| 目录 | 真相 |
|---|---|
| `<REPO_ROOT>\`（**连字符**） | 误建目录，已归档。**严禁写入** |
| `<WORKSPACE_ROOT>\N.E.K.O-main\`（**圆点**） | 真仓库 |

肉眼几乎分不出来。每轮任务开始前先确认一遍路径。

---

## base_dir 与资源根不是一回事

打包成 Nuitka onefile 之后，这两个目录会分道扬镳：

| | 开发模式 | 打包模式 |
|---|---|---|
| `paths.base_dir()` | 项目根 | **exe 所在目录** |
| `paths._resource_root()` | 项目根 | **onefile 临时解压目录** |

- 用户数据（`config.toml` / `runtime/` / `console.log`）→ `base_dir()`
- 打包内置资源（`assets/`）→ `asset()`（走 `_resource_roots()` 候选表）

**混用会静默失效**：托盘图标曾经整个消失，根因就是按 `base_dir()`
去解压目录找 `assets/icon.ico`，`QIcon` 拿到 null，而 null 图标在
Windows 托盘里什么都不画 —— 进程活着、窗口正常，就是看不见图标。

---

## 文件日志

- 关键路径的日志一律走 `logger`，**不要 `print`**
  （只有 `main.py::setup_file_logging` 的兜底分支允许 print，
  因为那时 logger 还没装好）
- exe 模式的日志落到 `<exe 目录>/console.log`；
  该目录不可写时回退 `%LOCALAPPDATA%\NEKO-Console\console.log`
- 超过 2MB 自动改名成 `.log.1`（只留一代）
- 启动横幅**每次都写**，用来给日志分段

### FileHandler 挂 `neko_console`，不要挂 root

`app.run()` 里有 `logging.basicConfig(level=cfg.log_level)`，
而 `basicConfig` 在 **root 已有 handler 时整个空操作**。
挂到 root 上会让那句不执行、`cfg.log_level` 失效，日志量悄悄掉到 WARNING。

---

## 退出必须"死透"

这是一个能截屏的高权限程序，用户点完退出却在任务管理器看到它还在 ——
那是**信任问题**。

- 退出走 `app.quit_app()`：停引擎 → 停卡片 → 隐托盘 → 武装兜底
- 兜底必须用 **`threading.Timer(daemon=False)`**，不能用 `QTimer`
  （QTimer 挂在事件循环上，事件循环一死它就死，而它要救的恰恰是
  "事件循环已死/解释器关闭卡住"这种情况）
- 关窗口（X）= 缩托盘，进程继续跑。菜单项写「**完全退出**」，
  气泡必须点明"仍在运行"

---

## 写文件的前提：一条真相源

- 配置、日志、运行时通道文件：能用文本级操作就**不要**重新序列化
  （tomli-w 会毁掉注释和字段顺序）
- 规则 TOML 的启停切换走 `channels/rules.py::set_enabled`，
  只改那一行，注释一个字不动
- 写引擎侧文件前先确认路径：`layout.POS_FILE` 和主控台的
  `resolved_runtime_dir()` 必须指向同一个目录
