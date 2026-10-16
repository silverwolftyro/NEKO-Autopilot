# Console.exe 打包体检报告（2026-10-16）

> 目标：确认 `<CONSOLE_ROOT>\build\Console.exe`（30.3 MB）是不是开箱即用。
> 本轮**只读**：没改 build 脚本、没重新构建。

---

## 结论（一句话）

**打包是完整且自包含的，但这个 exe 现在启动即崩 —— 退出码 1，不是黑屏也不是缺 DLL。**
崩在 `neko_console/main.py:37` 的**相对导入**上，与 Nuitka 参数、PySide6 完整性**都无关**。

---

## 1 · 构建参数（任务 A）

`build/build.ps1:114-129`：

```powershell
$mode = if ($Standalone) { '--standalone' } else { '--onefile' }
```

| 问题 | 答案 |
|---|---|
| `--standalone` 还是 `--onefile`？ | **默认 `--onefile`**（要 standalone 得显式传 `-Standalone`）。本次没传 → onefile |
| `--include-package=PySide6`？ | **没有**，但有 `--enable-plugin=pyside6`（**这是更正确的做法**，插件会自动带上 Qt6 DLL + `qwindows.dll` + 各插件） |
| `--exclude-module`？ | **没有** |
| `--noinclude-qt-translations` 之类裁剪？ | **没有**。Qt 翻译文件是全量的，所以体积偏大 |
| 其他 | `--assume-yes-for-downloads`、`--windows-console-mode=disable`、`--windows-icon-from-ico`、`--output-filename=Console.exe`、2 个 `--include-data-file` |

**判断：参数组合没问题。** `--enable-plugin=pyside6` 是官方推荐路径，比手工 `--include-package` 更可靠。脚本里的自检（venv Python / Nuitka / zstandard / C 编译器 / 资源文件）也都齐全。

---

## 2 · dist 结构（任务 B）

`build/main.dist/` —— **66 个文件 / 108.9 MB**，是完整的 standalone 树：

```
main.dist/
├── assets/
├── neko_console/config/          # default.toml 数据文件在
├── pydantic_core/
├── PySide6/
│   ├── QtCore.pyd  QtGui.pyd  QtNetwork.pyd  QtWidgets.pyd
│   └── qt-plugins/
│       ├── platforms/  qwindows.dll (991 KB) ✅  qdirect2d / qminimal / qoffscreen
│       ├── imageformats/ qjpeg qwebp qico qpdf qsvg ...
│       ├── iconengines/  qsvgicon.dll
│       ├── styles/       qmodernwindowsstyle.dll
│       └── tls/          qopensslbackend / qschannelbackend / qcertonlybackend
├── shiboken6/
├── libcrypto-3-x64.dll  libssl-3-x64.dll  libffi-8.dll   # Python 的 ssl 支持
├── main.dll            (35.0 MB)  # 编译后的 Python 代码
├── python3.dll  python311.dll                            # Python 运行时
├── pyside6.abi3.dll  shiboken6.abi3.dll
├── qt6core.dll  qt6gui.dll  qt6widgets.dll  qt6network.dll  qt6pdf.dll  qt6svg.dll
└── msvcp140.dll  msvcp140_1.dll  msvcp140_2.dll          # VC++ 运行时
```

| 检查项 | 结果 |
|---|---|
| `PySide6/` 目录 | ✅ 有 |
| `python311.dll` | ✅ 有 |
| `plugins/platforms/qwindows.dll` | ✅ 有（PySide6 的原生布局是 `PySide6/qt-plugins/platforms/`，不是 `plugins/`——查的时候别找错路径） |
| 文件数 / 总大小 | **66 个 / 108.9 MB** |

**这是 standalone（一堆文件）。** `main.onefile-build/`（11 文件 / 60.7 MB）是打包中间产物，`main.dist` 被 zstandard 压缩进 `Console.exe`，30.3 MB ≈ 108.9 MB 的 3.6 倍压缩比 —— **对 PySide6 来说是正常水平，不是打包不全**。

> 顺带：exe 只有 30.3 MB 这件事本身不构成"缺东西"的证据。Qt 的 `.dll` 压缩率很高，真要怀疑，对照数应该是 108.9 MB 而不是 300 MB。

---

## 3 · exe 依赖分析（任务 C）

自写 PE 导入表解析（`dumpbin` 不在环境里）：

```
=== Console.exe (30.3 MB) ===
    KERNEL32.dll
    SHELL32.dll
    -> 2 DLLs

=== main.dll (35.0 MB) ===
    KERNEL32, VCRUNTIME140, python311.dll, api-ms-win-crt-*.dll ...

=== qt6widgets.dll (6.2 MB) ===
    Qt6Core.dll, Qt6Gui.dll, MSVCP140.dll, MSVCP140_1.dll, USER32, GDI32, UxTheme, dwmapi ...
```

**`Console.exe` 只直接依赖 2 个系统 DLL —— 这是 onefile 引导壳的正常形态，不是缺依赖。**
它运行时把压缩 payload 解到 `%TEMP%\onefile_<pid>_<rand>_<hash>\` 再启动，里面的 `main.dll` 才去链 `python311.dll`，`qt6widgets.dll` 才去链 `Qt6Core/Qt6Gui`。

| 关键依赖 | 在 dist 里？ |
|---|---|
| `python311.dll` | ✅ |
| `Qt6Core.dll` / `Qt6Gui.dll` / `Qt6Widgets.dll` | ✅ |
| `qwindows.dll` | ✅ |
| `msvcp140*.dll` | ✅ |
| `VCRUNTIME140.dll` | ❌ 不在 dist，但它是 Win10+ 系统自带 DLL，不需要打包 |

**依赖闭包完整，无缺失。**

---

## 4 · 实跑验证（任务 D）—— 这里出事了

环境事实：

```
where python  ->  Python310 / Python313（系统）
系统级 PySide6 -> 未安装（import 直接失败）
```

**系统根本没装 PySide6**，所以如果 exe 能跑起来，就不可能是借了系统环境 —— 这本来是个很干净的验证环境。

### 结果：启动后 ~12 秒退出，exit code 1

```powershell
cmd /c "Console.exe > out.txt 2> err.txt"
exit=1

--- stderr ---
Traceback (most recent call last):
  File "%LOCALAPPDATA%\Local\Temp\onefile_25300_315910_AbipPi9gmqE\main.py", line 37, in <module>
ImportError: attempted relative import with no known parent package
```

### 根因（与 Nuitka 无关）

`neko_console/main.py:37`：

```python
from . import __version__      # ← 相对导入
```

Nuitka 把 `neko_console/main.py` 当**顶层脚本**编译（traceback 里的路径是裸 `main.py`，不是 `neko_console\main.py`），此时 `__package__` 为空，相对导入必然失败。

**用 venv 直接跑可以完全复现，与 Nuitka 无关：**

```
$ python neko_console\main.py
  File "<CONSOLE_ROOT>\neko_console\main.py", line 37, in <module>
    from . import __version__
ImportError: attempted relative import with no known parent package     ← 一模一样

$ python -m neko_console.main          # __package__ = "neko_console"
（GUI 正常启动）                          ← 相对导入在有父包时是好的
```

也就是说：**只要有人执行 `python neko_console/main.py`，无论开发还是打包，都会炸。**
Nuitka 只是恰好走了这条路。

### 顺带发现：仓库自己的约定已经被违反

`neko_console/__init__.py` 的 docstring 写着：

> 版本号约定的**唯一指导原则**……因此关于**从哪 import** `from neko_console import __version__`。
> 改动规则见 `AGENTS.md`

`pyproject.toml` 的入口也是 `[project.scripts] neko-console = "neko_console.main:main"` —— **绝对导入路径**。

只有 `main.py:37` 一处用了相对导入，与全仓库约定相反。

### 另一个现场线索

`build/console.log`（2026-10-12 15:11）里是一次**成功启动并正常退出**的记录：

```
[INFO] neko_console.app: 引擎流程开始
[INFO] neko_console.app: 引擎未在运行，跳过引擎开关
[INFO] neko_console.app: 主控台已退出（code=0，托盘=True）
```

说明这个入口**曾经能跑通**。10-16 这次重建后失效，指向 main.py 在 10-12 之后被改过（或之前那次是 standalone 构建）。这一条属于推断，需要查 git log 才能坐实。

---

## 5 · 最小修复方案

### 首选：把相对导入改成绝对导入（1 行，不碰构建配置）

`neko_console/main.py:37`：

```diff
-from . import __version__
+from neko_console import __version__
```

**为什么首选这条：**

- 与仓库自己的约定一致（`__init__.py` docstring + `pyproject.toml` 都写的是绝对导入）；
- 开发、打包、`-m`、`pip install` 后四种入口行为一致；
- Nuitka 认得 `neko_console` 是包，会把整个包跟着编译（`main.dist/neko_console/` 现在只有 `config/`，说明包代码全进了 `main.dll`，这条路径已经通了）；
- 不需要动 build 脚本，也不需要 `--include-package`。

改完后**不需要重新构建逻辑**，直接 `.\build\build.ps1` 即可。

### 备选（不推荐）：改 Nuitka 参数

如果坚持不动源码，理论上可以用 `--module=neko_console.main` 让 Nuitka 以模块而非脚本方式编译，从而带上 `__package__`。
但这会让 onefile 的入口语义变复杂（需要额外的 `--main` 组合），且**偏离了 `pyproject.toml` 声明的入口**，属于为绕过一个 1 行的源码问题而把构建变复杂。不建议。

### 与本问题无关、但值得知道的

`build.ps1:141` 有一句 `if ($mb -gt 300) { "Size over 300MB - Qt plugins are probably not trimmed" }`。
**这个阈值对 onefile 毫无意义** —— onefile 压缩后根本到不了 300 MB，dist 展开才 108.9 MB。
真要监控打包完整度，应该看 `main.dist` 的体积，不是 exe 的。这条属于建议，本轮没改。

---

## 6 · 边界声明

- **没改 build 脚本**，没重新构建，没改任何源码。
- 本轮新增的唯一文件是本报告。
- 临时 PE 解析脚本已移入 `<WORKSPACE_ROOT>\_recycle\tmp_black_screen_probe\_pe_deps_tmp.py`，未删除。
- 测试用的 exe 副本在 `%TEMP%\neko_exe_test_1335265941\`，可自行删除。
- 实跑验证过程中启动的 GUI 进程已全部结束（`Get-Process` 复查：none）。
