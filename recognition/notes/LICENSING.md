# 外部资产来源与许可

> 记录时间：2026-09-30

## 已收集的 BetterGI（GPL-3.0）资产

| 文件 | 来源（BetterGI 目录内） | 大小 | 用途 |
|---|---|---|---|
| `data/tp.json` | `GameTask/AutoTrackPath/Assets/tp.json` | 428,769 | 传送点坐标表（7 区域 / 936 点） |
| `GameTask/AutoFight/Assets/combat_avatar.json` | 同左 | 39,440 | 角色元数据（127 个）**尚未复制** |

### BetterGI 预训练模型（2026-10-02 补登）

**来源**：`Assets/Model/World|Mine|Domain|Fish/bgi_*.onnx`
（SHA256 与 BetterGI 内原文件**逐字节相同**，是直接拷贝）

| 文件 | 大小 | SHA256 | 类数 | 用途 |
|---|---|---|---|---|
| `models/pretrained/bgi_world.onnx` | 11,212,849 | `7C0ADD59…3ACA4A` | 7 | 世界实体：health_bar / enemy_identify / drops / ore / unmined_ore / chest / locked_chest |
| `models/pretrained/bgi_mine.onnx` | 10,972,792 | `869719DF…009920E` | 1 | 矿点 |
| `models/pretrained/bgi_tree.onnx` | 6,170,170 | `14FCDA74…F1053EE4` | 1 | 采集物 / 树 |
| `models/pretrained/bgi_fish.onnx` | 12,788,666 | `F8DA8779…4FA545CE` | 5 | 钓鱼点 |

每个模型配一个 `classes_bgi_*.txt`（类名，一行一个）。

**处置方式（用户 2026-10-02 拍板）**：走本文件 §「发布之前必须做的三选一」
的**第 1 条 —— 不随包分发**。具体要求：

* `models/pretrained/` 整个目录进 `.gitignore` 与打包排除清单
* 运行时代码**必须**做「文件不存在就降级」的分支：
  `perception/world_entities.py` 加载不到模型时抛
  `YoloUnavailableError`（响亮失败），由上层决定是否注入
* 发布前不得把它们打进 Apache-2.0 的安装包

> ⚠️ 与 `tp.json` 是**同一类问题**（GPL 数据/资产进 Apache-2.0 仓库），
> 但**处置理由略有不同**：模型权重不是"代码"也不是"数据库内容"，
> 而是**他人训练的产物**。GPL-3.0 §2 的「本程序」通常不涵盖这类产物，
> 但 BetterGI 的分发条款可能另有约定 —— **发布前必须实读其 LICENSE
> 再定，本文件不预判**。

## 许可边界

**BetterGI 是 GPL-3.0；N.E.K.O. 是 Apache-2.0。**

已确立的项目规矩：**BetterGI 禁抄代码、禁复制数据文件**。
把 `tp.json` 复制进 `recognition` 正是踩这条线 —— 这一步是**有意为之**
（数据参考），但必须在发布前处理干净：

### 发布 N.E.K.O.（Apache-2.0）之前必须做的三选一

1. **不随包分发** —— `data/tp.json` 留在本地开发目录，
   在 `.gitignore` 与打包配置里排除；代码里做「文件不存在就降级」的分支
2. **标为独立可选组件** —— 放在单独的、明确标注 GPL 来源的目录，
   与 Apache 代码解耦；用户自行获取
3. **换成自建数据** —— 按 936 点的字段结构自己录一份，
   不复制原文件内容

### 绝对不能做的

- ❌ 把 `tp.json` 打进 Apache-2.0 的安装包
- ❌ 在 N.E.K.O. 的 Python 代码里 `import` 或内联 BetterGI 的 C# 逻辑
- ❌ 改动 `tp.json` 后仍声称是「本地数据」

## 同样的规矩适用于

- **MaaEnd（AGPL-3.0）** —— 比 GPL 更严格，**连读取都要谨慎**
- **ultralytics（AGPL-3.0）** —— 运行时禁止 import，推理只走 ONNX
  （已落到 `perception/yolo.py` 的实现里）

## ONNX Runtime 运行时依赖（2026-10-02 切换记录）

| 时间 | 包 | 版本 | 许可 | 状态 |
|---|---|---|---|---|
| 初始 | `onnxruntime` | 1.25.0 | MIT | 纯 CPU |
| 试 GPU | `onnxruntime-gpu` | 1.30.0 | MIT | ⚠️ **已卸载**（见下） |
| **当前** | **`onnxruntime-directml`** | **1.24.4** | **MIT** | ✅ 在用 |

### 为什么从 onnxruntime-gpu 换成 directml

* `onnxruntime-gpu` 走 CUDA，需要 **CUDA 13.x + cuDNN 9.x** 运行时。
  本机（RTX 5070 Laptop / 驱动 591.86）**没有 CUDA Toolkit**，
  缺 `cublas64_13.dll` / `cublasLt64_13.dll` / `cudart64_13.dll`。
* 装了 `onnxruntime-gpu` 后 ORT 会**静默回退 CPU 且不抛异常** ——
  `ort.get_available_providers()` 照样列 `CUDAExecutionProvider`，
  但 `session.get_providers()` 只有 `CPUExecutionProvider`，
  连 `ort.get_device()` 都还返回 `"GPU"` 骗人。
* **官方文档从未声明支持 Blackwell（sm_120）**。NVIDIA 官方迁移指南明说
  CUDA EP **不含 PTX**（不能 JIT 兜底），猜错就是
  `no kernel image is available` 硬报错。
* **DirectML 只需 R570+ 驱动**，NVIDIA 官方称「开箱全速」，
  且 `onnxruntime-directml` 是 **MIT**（比走 CUDA 更干净）。

### ⚠️ 三者互斥，只能装一个 —— 而且**卸载也会互相破坏**

`onnxruntime` / `onnxruntime-gpu` / `onnxruntime-directml` **装在同一个
环境里会互相覆盖同名文件**（都写进 `site-packages/onnxruntime/`）。
切换必须**先卸载再安装**：

```powershell
uv pip uninstall onnxruntime-gpu
uv pip install onnxruntime-directml
```

⚠️ **两个实测踩过的坑**：

1. **`pip install onnxruntime-gpu` 不会自动卸掉 `onnxruntime`。**
   装完后 `importlib.metadata` 里**同时**有两个 dist-info，
   实际生效的却是最后写入的那份文件。
2. **`uv pip uninstall onnxruntime` 会删掉 directml 正在用的文件。**
   卸载后 `import onnxruntime` 仍然「能 import」，但
   `onnxruntime.__version__` 已经不存在了 —— **半死不活的状态**。
   修复：`uv pip install --reinstall onnxruntime-directml`

**正确做法**：切到 directml 后，务必确认

```powershell
uv pip list | Select-String onnxruntime
# 只应看到 onnxruntime-directml 一行
```

回退到纯 CPU：`uv pip uninstall onnxruntime-directml; uv pip install onnxruntime`

### provider 判据必须用 `session.get_providers()`

`perception/yolo.py` 里已落地：`current_provider()` 读的是**实际加载**了什么，
`fallback_reason()` 说明为什么没用上更快的。见 §23.3。

## `archive/` 目录：禁止运行

`exports/benchmark/scripts/archive/` 下的这些脚本在**模块级**执行
`from ultralytics import YOLO`（AGPL-3.0）：

```
archive/bgi_world_verify.py
archive/bgi_range.py
archive/bgi_world_1280.py
archive/extract_classes.py
```

**禁止在任何运行时 / 评测 / 构建流程中调用它们。** 保留仅为历史参考
（它们记录了当初怎么确定这批模型的可信度）。需要跑 bgi_* 模型的评测时，
走 `perception/yolo.py` 的 ONNX Runtime 路径。

> 详见 `exports/benchmark/scripts/archive/README.md`。

## 数据引用 vs 抄数据

| 行为 | 判断 |
|---|---|
| 读它的字段**结构**来设计自己的 schema | 可以（结构不是版权表达） |
| 把它的**数据内容**复制进自己的仓库 | **不可以** |
| 抄它 C# 代码的检测算法 | **不可以**（GPL 传染） |
