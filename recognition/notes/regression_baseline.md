# 回归基线

> 2026-10-13 ｜ 配套：`run_all_tests.bat`（一键跑全部并汇总）
> **数字都是实测的**，命令见 §3。

---

## 1 · 各仓库当前状态

| 测试集 | 位置 | 结果 | 耗时 |
|---|---|---|---|
| **插件** | `N.E.K.O-main\...\plugin\plugins\genshin_game_buddy\tests` | **1392 passed, 1 skipped, 1 xfailed**（总计 1393 条） | ~29s |
| **Console** | `NEKO-Console\tests` | **593 passed, 6 xfailed, 1 xpassed**（总计 600 条） | ~29s |
| e2e_pathfind 纯逻辑 | 承载在 Console 套件里 | 83 条 | — |
| 全链路离线集成 | 承载在插件套件里 | 9 条 | — |

⚠️ 插件的 `1 skipped` 是**间歇性**的，见 §4。

---

## 2 . pytest 不会自动发现的目录

⚠️ 有两组测试**不在**上表的默认路径里，加新测试时容易放错地方：

| 目录 | 归属 | 由谁跑 |
|---|---|---|
| `N.E.K.O-Console\tests\e2e_sandbox.py` | Console | 与 Console 套件一起（辅助模块，非测试文件） |
| `plugin\...\tests\integration\` | 插件 | **在**插件默认路径内（`pytest tests`） |
| `plugin\...\tests\unit\` | 插件 | 在默认路径内 |
| `recognition\exports\...\e2e_pathfind.py` | — | **本仓库没有测试目录**，它的纯逻辑测试放在 Console 侧（见下） |

### 为什么 e2e_pathfind 的测试在 Console

`recognition` 下**没有任何测试基础设施**（无 `tests/`、无 runner、无 conftest）。
而 Console 是这个脚本的调用方，所以它的纯逻辑测试放在
`NEKO-Console\tests\test_e2e_pathfind_logic.py` + `e2e_sandbox.py`。

理由写在那个文件的 docstring 里：不值得为一个脚本目录新建整套 runner，
而「保护自己的依赖」正是测试的目的。

---

## 3 · 运行命令

```powershell
# 插件（需要 onnxruntime / pydantic / numpy）
cd <REPO_ROOT>\plugin\plugins\genshin_game_buddy
<REPO_ROOT>\.venv\Scripts\python.exe -m pytest tests -q

# Console（需要 PySide6）
cd <CONSOLE_ROOT>
<CONSOLE_ROOT>\.venv\Scripts\python.exe -m pytest tests -q
```

> ★ **两个仓库用不同的 venv，不要合并。** 插件要 onnxruntime，Console 要 PySide6；
> 混用会出现「本机没装」的假失败。

**一键（推荐）**：

```bat
<RECOGNITION_ROOT>\notes\run_all_tests.bat          :: 详细输出
<RECOGNITION_ROOT>\notes\run_all_tests.bat -q       :: 安静模式
```

它会依次跑三个测试集、把 rc 汇总，并写一份 `regression_last_run.txt`。
任一失败则退出码为 1。

> ⚠️ 跑插件前会 `rmdir .pytest-tmp` —— 那个目录存在时会让 pytest 起不来。

---

## 4 · 已知的不稳定 / 需要注意的地方

### 🟠 插件套件偶发 1 条 skip

**现象**：全量跑时偶尔出现 `1 skipped`（本轮观察到 8 次全量里出现 2 次）。
单独跑疑似文件 4 次**均未复现**（每次 93 条全过）。

**嫌疑**：`tests/unit/test_hp_wiring.py::test_real__extracted_templates_round_trip`
里有一处内层 `pytest.skip`，条件是 `cv2.imread` 对模板资产返回 `None`。

**为什么重要**：一个**会静默跳过**的测试等于半个不存在的测试 ——
它不会让回归变红，只会让「这条断言到底跑没跑」变成不确定的事。

**未确认**：我没能定位到确切是哪一条，也没有确认它是不是只在并发跑时才出现。
**下次全量跑出现 skip 时，用 `-rs` 抓一下具体是哪个**，然后把这里的猜测替换成结论。

### 🟡 真机截图相关的测试靠 skipif 保护

以下测试依赖 `autopilot/_runtime/capdiag_wgc.png`。该目录被 git 忽略、
不随包分发，所以**别的机器 / CI 上它们会被 skip 而不是 fail**：

| 文件 | 依赖 |
|---|---|
| `plugin\tests\unit\test_hp_wiring.py` | 真机截图（5 条 `test_real__*`） |
| `plugin\tests\integration\test_full_pipeline_offline.py` | 真机截图（整模块 `skipif`） |
| `NEKO-Console\tests\test_pathfind_e2e_offline.py` | 假脚本镜像守卫（跳过的是镜像比对） |
| `NEKO-Console\tests\test_e2e_pathfind_logic.py` | 真脚本源文件 |

> ⚠️ 这意味着「插件通过」数里有一部分在本机之外根本没跑。
> 判断覆盖度时要记得这一点。
>
> ⚠️ 另：**「0 skip」不是稳定状态。** `tests/unit/test_change_detect.py:136`
> 抓**真实桌面**三帧算 dHash diff，桌面不静止时**主动 skip 而不是 fail**
> （这是它设计好的诚实语义，docstring 里写着）。实测同一天内出现过
> `1393 passed / 0 skipped` 与 `1392 passed / 1 skipped` 两种结果。

### 🟡 会写文件 / 会起子进程的测试

| 类别 | 例子 | 注意 |
|---|---|---|
| 起真子进程 | `NEKO-Console\tests\test_pathfind_*.py`、`plugin\tests\unit\test_fake_pathfind.py` | 跑得慢；**别和别的重型任务并发跑**，会互相拖超时 |
| 写 `_runtime` | 集成测试用 `tmp_path` | ⚠️ 真跑 `_runtime` 的那些不要手工执行，会污染历史数据 |

---

## 5 · 加新测试时

1. 放进**该仓库自己的** `tests/` 下（`pytest tests` 才会收到）
2. 真机数据依赖的用例**必须** `skipif` 保护，并在文件 docstring 里写明
3. 改了跨仓库共享的东西（例如 `e2e_pathfind.py`、`keymap` 契约），
   **记得两边都改**，并更新相应的镜像守卫 md5
4. 跑一次 `run_all_tests.bat` 确认两侧都没红

---

## 6 · 这份基线不能证明什么

- 不能证明**功能正确** —— 回归只证明「没有回退」。
- 不能证明**真机上能跑** —— 见 `demo_prep_pack.md` 与
  `console_pathfind_offline_test.md` 里「能证明 / 不能证明」那两节。
- 数字会随时间变化。这份文档记录的是 **2026-10-13** 的实测值；
  改动后请更新 §1。
