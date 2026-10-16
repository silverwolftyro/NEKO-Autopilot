# 架构

三个目录，各自有明确的职责边界。它们**共享一棵资产树**，但
**不共享虚拟环境**。

```
<仓库根>/
├── plugin/plugins/genshin_game_buddy/   引擎（autopilot）：看屏幕、按键
├── console/                              主控台：配置、启停、看状态
└── recognition/                          识别资产 + 离线脚本
```

---

## 为什么是"插件 + 主控台"两个进程

不是过度设计，是三条硬约束逼出来的：

1. **依赖不同**。引擎要 `opencv` / `onnxruntime` / `winrt`；
   主控台只要 `PySide6` / `pydantic`。塞进一个 venv 的话，
   主控台的冷启动会拖着一堆用不上的二进制。
2. **权限不同**。引擎 `--live` 模式要发按键，被游戏内核级的 UIPI
   门禁拦着，必须管理员；主控台只想看状态的人不需要。
   进程拆开才能一个提权一个不提权。
3. **崩溃隔离**。引擎的截图 / OCR 链路很容易被驱动更新或分辨率变化
   打崩。它崩了不该拖着主控台一起没。

两者通过**文件**通信，不走 socket：

```
autopilot/autopilot/_runtime/
├── state_snapshot.json     引擎写，主控台轮询读（~5 Hz）
├── run_current.txt          当前 run_id
├── pathfind_state.json      寻路子进程状态
└── console_commands.jsonl   主控台写，浮窗读（单向命令）
```

选文件而不是 socket，是因为它天然跨进程边界、能用文本工具排查、
崩了之后残骸还在现场。

---

## 引擎分层（`autopilot/`，113 个模块 / 25,499 行）

```
         ┌─────────────────────────────────────────┐
         │  core/           事件循环、状态机、主循环  │
         └───────────────┬─────────────────────────┘
                         │
      ┌──────────────────┼──────────────────┐
      ▼                  ▼                  ▼
┌───────────┐      ┌──────────┐      ┌──────────────┐
│perception │      │ actuator │      │    policy    │
│  看        │─────▶│  动      │◀─────│  决定        │
│           │ 状态 │          │ 意图 │              │
└─────┬─────┘      └────┬─────┘      └──────┬───────┘
      │                 │                   │
      ▼                 ▼                   ▼
 ┌─────────┐      ┌──────────┐       ┌───────────┐
 │capturer │      │ keymap / │       │ LocalPolicy│
 │ WGC/MSS │      │ actuator  │       │  Tree     │
 └─────────┘      └──────────┘       └───────────┘
```

| 包 | 职责 | 关键文件 |
|---|---|---|
| `core/` | 事件循环、状态快照写入 | 状态机是这个系统的中心 |
| `perception/` | 截图、OCR、场景识别、坐标换算 | `paths.py` 是**全仓唯一的资产路径出处** |
| `actuator/` | 按键发送、护栏、鼠标 | 护栏逻辑独立于决策，便于单独测试 |
| `policy/` | 规则引擎：信号 → 意图 | 声明式 TOML 规则 |
| `contracts/` | 接口定义 | |
| `config/` | 配置加载与默认值 | 缺文件用默认值，不报错 |
| `ui/` | 悬浮卡片（tkinter overlay） | |
| `nav/` | 寻路 | |
| `tools/` | 训练、标定、诊断脚本 | `check_env` 在这里 |

### 一条铁律：资产路径只从一处出

```python
# autopilot/perception/paths.py
RECOGNITION_ROOT = env("NEKO_RECOGNITION_ROOT") \
                   or 从 __file__ 向上找 recognition/configs/rois.toml \
                   or Path("recognition")          # 响亮失败
ROIS_TOML          = RECOGNITION_ROOT / "configs" / "rois.toml"
MINIMAP_SCALE_TOML = RECOGNITION_ROOT / "configs" / "minimap_scale.toml"
BGI_WORLD_ONNX     = RECOGNITION_ROOT / "models" / "pretrained" / "bgi_world.onnx"
```

早年在十几个文件里各写一遍绝对路径。代价不是"多打几个字"：
换机器要全仓搜索替换，**漏一处就静默用错文件**；拼错一个反斜杠，
报错是"文件不存在"而不是"路径写错了"；没人说得清某个资源到底从哪来。

---

## 主控台分层（`console/`，28 个模块 / 6,435 行）

```
neko_console/
├── main.py          入口
├── paths.py         开发模式 / Nuitka onefile 的用户目录判定
├── config/
│   ├── schema.py    pydantic 模型 + 出厂默认 + **边界收缩**
│   ├── loader.py    读 TOML
│   └── default.toml 首次启动时复制成用户的 config.toml
├── channels/        与引擎的文件通道（snapshot / engine / pathfind / overlay）
├── tabs/            UI 页面（home / pathfind / rules / logs / map）
└── tray.py          托盘 + 单实例
```

### 为什么配置是"收缩到边界"而不是"拒绝启动"

配置是用户手写的。写错一个数字就把主控台拒之门外，是最差的体验——
用户看到的是"程序起不来"，而不是"你把宽度写成了 99999，我帮你改成 4000"。

所以：**任何非法值都缩回合法区间，并记一条 WARN**。程序永远能起来。

### 默认路径留空 = 自动发现

`default.toml` 里 `[engine].path` 和 `.python` **留空**，
由 `schema.py` 沿目录树往上找 `plugin/plugins/genshin_game_buddy`。

> 这里曾经写死了开发机的绝对路径。首次启动时 `default.toml` 会被
> 复制成用户的 `config.toml`——也就是说**每个新用户拿到的第一份配置
> 都是坏的**。

---

## 识别侧（`recognition/`）

```
recognition/
├── configs/          rois.toml / minimap_scale.toml
├── templates/ui/     15 张 UI 模板裁剪图
├── data/             anchors.json、routes/、自备数据位
├── exports/benchmark/
│   ├── scripts/      147 个脚本（38,246 行）
│   │   ├── misc/     坐标换算、场景判定树、端到端寻路
│   │   ├── misc_bgi/ BetterGI 格式路线诊断（只读数据，不含其源码）
│   │   ├── probe/    一次性研究脚本
│   │   └── test/     离线脚本的测试
│   ├── datasets/     41 个特征表 CSV
│   └── notes/        方法论与实验记录
└── notes/            审计、回归、性能、许可等 md
```

`notes/` 是这个项目最值钱的部分之一：**50 条已证伪的路线**、
性能基线、异常审计都在这里。它回答的是"为什么现在是这样"，
而代码只回答"现在是这样"。

---

## `vendor/`：为什么把 winrt 打进仓

`plugin/plugins/genshin_game_buddy/vendor/winrt/` 里是
Windows 投影运行时的预编译 `.pyd`。

理由：OCR 和 WGC 截图通道都依赖 WinRT。要求用户先装
"Microsoft.Windows.SDK.NET.dll" 之类的运行时，
等于把"能不能跑"交给一个发布频率和用户机器绑定的外部组件。

代价：这些二进制绑死 **CPython 3.11 + win_amd64**。
换 Python 版本需要自己处理 —— 这是明确的取舍，不是疏忽。

> ⚠️ 别把 `vendor/` 里的 `runtime/` 当成构建产物删掉。
> 早年的发布脚本用 "runtime" 做目录名黑名单，一刀切把这个垫片
> 也删了，结果 WGC 截图通道在别的机器上直接崩。

---

## 测试

| | 模块数 | 行数 | 用例 | 现状 |
|---|---:|---:|---:|---|
| `plugin/.../tests/` | 71 | 16,770 | 1,281 | 1601 passed / 30 skipped / 1 xfailed |
| `console/tests/` | 29 | 6,829 | 655 | 710 passed / 70 skipped / 4 xfailed / 3 xpassed |

（用例数是 `def test_` 计数；parametrize 后实际执行的更多。）

skip 的原因全部是"资产不在本机"，逐条列在
[`ASSETS.md` 第四节](ASSETS.md#四缺资产时程序的表现)。

### 红线测试

`plugin/tests/static/test_redlines.py` 是**源码静态扫描**，
把项目自己的约定钉成回归：

- R8：模块顶层只允许纯定义（白名单：`re.compile`、`logging.getLogger`）
  —— 顶层做 IO 是 import 期副作用，宿主会先炸
- `ultralytics` 运行期零 import（AGPL 边界）
- 禁用 token（键盘钩子库等）
- i18n 调用点只允许 `_t` / `tr` 两个入口

> R8 这条真的拦下过东西：给脚本注入路径解析 preamble 时，
> preamble 在顶层调 `_neko_repo_root()`，当场点红。
> 后来改成"红线扫描名单里的模块禁止注入 preamble，
> 逐条精确替换"，名单直接从 `conftest.py` 的 `PLUGIN_MODULES` 读，
> 不写死副本（写死会漂移，漂移的方向恰好是最糟的）。
