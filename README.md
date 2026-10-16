# N.E.K.O Autopilot

**让程序看懂屏幕上发生了什么，然后替你按键。**

一个 Windows 桌面游戏自动化框架：截屏 → 识别场景 → 按规则决定动作 → 发按键。
配套一个主控台负责配置、启停和看状态。

> **这是第一版公开发布（v3.1.18）。** 能跑，但离"傻瓜可用"还有距离。
> 这一节下面会把能做什么、**不能**做什么分开写清楚 ——
> 我们更希望你在花时间之前就知道边界在哪。

![状态](https://img.shields.io/badge/python-3.11%20only-3776AB)
![许可](https://img.shields.io/badge/license-Apache--2.0-D22128)
![平台](https://img.shields.io/badge/platform-Windows%2010%2F11-0078D7)

---

## 它是什么

三部分，各管一件事：

| 目录 | 是什么 | 怎么跑 |
|---|---|---|
| `plugin/plugins/genshin_game_buddy/` | **引擎**。截屏、OCR、场景识别、规则决策、按键 | 作为 N.E.K.O 插件运行 |
| `console/` | **主控台**。配置、启停引擎、看实时状态 | `python -m neko_console` |
| `recognition/` | **识别资产 + 147 个离线脚本** | 脚本可单独跑 |

引擎和主控台是**两个进程**，通过状态文件通信。理由见
[架构文档](docs/ARCHITECTURE.md#为什么是插件--主控台两个进程)。

---

## 能做什么

以下是**在真机上验证过**的，不是计划：

- **多通道截屏** —— 硬件加速全屏下 bitblt / dxcam / mss 三个后端
  都只能拿到黑帧；WGC 走 DWM 合成管道，是唯一不碰注册表的解法。
- **场景识别** —— 主菜单 / 战斗 / 对话框 / 地图 等，取三态（是/否/未知），
  未知时**不猜**。
- **规则引擎** —— 声明式 TOML：信号 → 意图 → 动作。
  规则写在 `autopilot/rules/`，不用改代码。
- **实时悬浮卡片** —— tkinter overlay，跟着引擎状态显示，
  显示意图次数、节流状态，不干扰游戏画面。
- **坐标换算** —— 世界坐标 ↔ 底图像素，23 个人工标注锚点拟合，
  残差 RMS 12.6 px。跨进程共享，不受分辨率影响。
- **离线寻路** —— 从路线 JSON 生成 `move_to` 意图；
  有同名点消歧、有护栏、有 `--dry-run`（**默认不发按键**）。

## 不能做什么

这一节和上一节一样重要：

- **不是通用 RPA**。它不理解屏幕上的任意 UI，只认识定义过的场景。
- **不保证适配任何游戏**。当前只有原神一条被真机验证的路径，
  其它游戏见 [ROADMAP](docs/ROADMAP.md)。
- **不开箱即用**。识别底图（133 MB）和 YOLO 模型（GPL）**不在包里**，
  需要你自备。没自备时相关功能明确报错，其余照常。见
  [ASSETS.md](docs/ASSETS.md)。
- **Windows only**，Python 3.11 only。用了 Win32 API、WGC、WinRT 投影。
- **没有开箱的"自动战斗"**。规则引擎给你的是原语，
  组合成策略需要自己写规则并验证。
- **未经大规模用户验证**。第一版公开发布，见下方成熟度。

## 成熟度

| | 数字 |
|---|---|
| 引擎代码 | 113 个模块 / 25,499 行 |
| 引擎测试 | 1,601 passed / 30 skipped / 1 xfailed |
| 主控台代码 | 28 个模块 / 6,435 行 |
| 主控台测试 | 710 passed / 70 skipped / 4 xfailed / 3 xpassed |
| 已证伪的技术路线 | **50 条**（见 `recognition/notes/`） |

> skip 的构成全部是"资产不在本机"（真机截图 + 被刻意排除的版权资产），
> 逐条列在 [`docs/ASSETS.md` 第五节](docs/ASSETS.md)。
> 引擎那边有 1 条用例的结果**随本机桌面是否滚动而变**（±1），
> 所以你跑出来的数字可能和我这里差 1，这是预期内的。

那 50 条比"做了什么"更有价值：这个项目试过很多看起来可行的路子，
在真机上被数据推翻了，全部记在 `recognition/notes/` 里，
包括为什么推翻。下一个人不用重走。

---

## 快速开始

### 前置

- Windows 10 / 11
- Python 3.11（`3.11.x`，其它版本未验证）
- 一个原神客户端（其它游戏需要自己适配）
- 约 1 GB 磁盘（不含自备资产）

### 三步

```powershell
# 1. 建环境并装依赖
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-autopilot.txt
.\.venv\Scripts\pip install -r requirements-console.txt

# 2. 检查环境（会逐项告诉你缺什么、期望路径在哪）
.\.venv\Scripts\python -m autopilot.tools.check_env

# 3. 启动主控台
.\.venv\Scripts\python -m neko_console
```

> ⚠️ `requirements-*.txt` 必须是 **ASCII**。pip 用**系统 locale**
> 读 requirements 文件，中文 Windows 上那是 GBK，一个非 ASCII 字节
> 就会让安装整个失败。本包里的 requirements 文件因此全是英文注释。

### 跑测试确认环境对

```powershell
.\.venv\Scripts\pip install -r requirements-dev.txt

cd plugin\plugins\genshin_game_buddy
..\..\..\.venv\Scripts\python -m pytest tests -q     # 期望 1601 passed

cd ..\..\..\console
..\.venv\Scripts\python -m pytest tests -q           # 期望 710 passed
```

数字对不上说明装漏了依赖 —— 这正是 `requirements-autopilot.txt`
存在的理由（历史上 `opencv` 被 12 个模块在顶层 import，却从未被声明过）。

### 接上引擎

主控台的 `[engine]` 段默认留空，会自动向上找 `plugin/plugins/genshin_game_buddy`。
分开部署时填绝对路径，或设 `NEKO_ENGINE_PATH`。

**默认 `--dry-run` —— 不发按键。** 确认整条链路正常后再改成 `--live`。

---

## 配置

配置是 TOML，首启时从 `console/neko_console/config/default.toml` 复制到用户目录。

```toml
[engine]
path = ""          # 留空 = 自动发现
python = ""        # 留空 = 复用主控台解释器
args = ["--dry-run"]   # ★ 默认不发按键

[overlay]
follow_engine = false   # 引擎启停时是否自动带上悬浮卡片
scale = 1.0
```

非法值会被**收缩到合法区间并记一条 WARN**，而不是拒绝启动 ——
你看到的是"你把宽度写成了 99999，我帮你改成 4000"，不是"程序起不来"。

可用的环境变量见 [ASSETS.md 第三节](docs/ASSETS.md#三怎么检查当前状态)。

---

## 许可与资产

- 代码：**Apache-2.0**
- 仓库内**不含**任何 GPL-3.0 / AGPL-3.0 的源码、数据、模型
- YOLO 模型、传送点坐标库、米游社底图**都要自备** → [`docs/GPL_BOUNDARY.md`](docs/GPL_BOUNDARY.md)
- 缺什么、缺了会怎样、怎么补 → [`docs/ASSETS.md`](docs/ASSETS.md)

---

## 文档

| 文档 | 讲什么 |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 三层怎么分的、为什么这么分、踩过的坑 |
| [docs/ASSETS.md](docs/ASSETS.md) | 哪些随包、哪些自备、缺了会怎样 |
| [docs/GPL_BOUNDARY.md](docs/GPL_BOUNDARY.md) | GPL 资产清单 + 自备指引 |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 接下来做什么 |
| [`recognition/notes/`](recognition/notes/) | **实验记录**：50 条证伪路线、性能基线、异常审计 |

`recognition/notes/` 是这个项目最值得读的部分。
代码告诉你"现在是这样"，它告诉你"为什么是这样，以及什么行不通"。

---

## 截图 / GIF

> 占位。v3.1.18 暂未附演示素材。
> 补齐后：主控台截图、悬浮卡片运行中、场景识别判定过程、
> 寻路 `--dry-run` 轨迹回放。

---

## 贡献

见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。测试是硬门槛 ——
包括 `tests/static/test_redlines.py` 那几条源码静态扫描。

---

## 许可

Apache-2.0，见 [LICENSE](LICENSE)。

本项目与米哈游、米游社无关，未获其认可或背书。
它也不是官方工具，不保证遵守游戏的服务条款 ——
**是否违反你所用游戏的用户协议，由你自己判断并承担后果。**
