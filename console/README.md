# NEKO-Console

N.E.K.O. 3.0 的**主控台**。独立进程，与引擎（autopilot）、悬浮卡片（overlay）
三者互不依赖，通过 `runtime/` 目录通信。

**许可证：Apache-2.0**（与 N.E.K.O. 主仓库一致）。
图形栈用 **PySide6（LGPL）** 而非 PyQt6（GPL）—— 主控台要随 Apache-2.0 分发，
PySide6 的 LGPL 允许，PyQt6 不允许。

## 当前状态：第一块 · 主控台空壳

已实现：主窗口 + 5 个空 tab、系统托盘、关窗缩托盘、单例保护、三层配置加载、
Nuitka 打包脚本。

**未实现**：不连引擎、不连卡片、不做任何业务功能。

| 页签 | 状态 |
|---|---|
| 首页 | 空壳 |
| 引擎 | 空壳 |
| 规则 | 空壳 |
| 悬浮卡片 | 空壳 |
| 关于 | 有版本 / 许可 / 运行模式 / 关键路径 |

## 目录

```
neko_console/
├── paths.py      路径解析（开发模式 vs Nuitka onefile）★ 最容易踩的坑
├── app.py        QApplication + 主窗口 + 单例保护
├── tray.py       系统托盘
├── main.py       main()，Nuitka 打包入口
└── config/       三层加载：用户 TOML > 内置 default.toml > 代码兜底
```

## 三个设计决定

**1. 配置写在"用户双击 exe 的目录"，不是临时目录。**
Nuitka `--onefile` 会把程序解压到 `%TEMP%`，`__file__` 全指向那里。
配置若跟着走，用户改一次、退出、临时目录被清 —— 配置就没了。
`paths.py` 用 `__compiled__.containing_dir` 拿到真正的 exe 所在目录。

**2. 非法配置值收缩到边界，不拒绝启动。**
用户手写 `window_width = 99999` 应该看到"已帮你改成 3840"，
而不是程序起不来。`schema.ConsoleConfig.clamped()` 负责这件事。

**3. 单例用 QSharedMemory，不用"找进程"或"占端口"。**
QSharedMemory 由操作系统在进程退出时回收（含崩溃 / 强杀），不留僵尸锁。

## 开发模式运行

```bat
cd /d <CONSOLE_ROOT>
uv venv
uv pip install -e .
.venv\Scripts\python.exe -m neko_console
```

## 打包

```bat
cd /d <CONSOLE_ROOT>
uv pip install nuitka
.\build\build.ps1
```

需要 C 编译器（MSVC Build Tools 或 MinGW-w64），脚本会先检查并给出指引。
产物：`build\Console.exe`。

## 快捷键

- 点主窗口 X → 缩到托盘（首次弹气泡提示，之后不再打扰）
- 托盘双击 / 右键「打开」→ 恢复窗口
- 托盘右键「退出」→ 真正退出

## 不做的事

- 不用 PyQt6（GPL）
- 不在 config.toml 里放注释以外的哑键
- 不 git init（.gitignore 只是备着）
