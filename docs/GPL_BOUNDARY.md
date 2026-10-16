# GPL 边界说明

本仓库采用 **Apache-2.0**。仓库里有几样东西**不是** Apache-2.0 的，
它们**不在本仓库里**，需要你自己准备。这份文档说明：哪些、为什么、
从哪拿、放哪、以及你怎么知道自己拿对了。

---

## 一句话规则

> **Apache-2.0 的代码可以引用 GPL-3.0 的资产来"读数据"，
> 但不能把它们"打包分发"给别人。**

所以凡是 GPL 的东西，本仓库只保留**接口**（怎么读），
不保留**内容**（读什么）。

---

## 不随包分发的资产清单

| 资产 | 体积 | 许可 | 为什么不能进 |
|---|---:|---|---|
| `bgi_world.onnx` | 11.2 MB | GPL-3.0（BetterGI 训练产出） | 模型权重随其训练数据与代码许可一并继承 |
| `bgi_fish.onnx` | 12.8 MB | GPL-3.0 | 同上 |
| `bgi_mine.onnx` | 11.0 MB | GPL-3.0 | 同上 |
| `bgi_tree.onnx` | 6.2 MB | GPL-3.0 | 同上 |
| `classes_bgi_*.txt` | < 1 KB | GPL-3.0 | 随模型 |
| `tp.json`（传送点坐标库，428,769 字节） | 419 KB | GPL-3.0 | BetterGI 的数据文件 |

### ⚠️ 一个容易漏掉的坑：改过名的 GPL 数据

本仓库历史上有一个文件叫 `_ref_tp_sample.json`（428,769 字节）。
它的文件名不含 `tp`，字节数与 BetterGI 的 `tp.json` **完全一致**，
内容是 `TeleportWaypoint` / `BlessDomain` / `MasteryDomain` 条目，
带 `position` / `tranPosition`。

**改文件名不改变许可归属。** 这份数据没有随包分发，
下面的"自备"部分会告诉你放哪。

### ⚠️ 另一个：整套第三方源码克隆

开发机上曾有一份 BetterGI 的完整 Git 克隆（19,273 个文件 / 109.6 MB）
放在 `recognition/exports/benchmark/_bgi_cache/repo/`。
GPL **源码仓库的副本**，比分发单个数据文件性质更重，已整体排除。

---

## 自备指引

### 1. YOLO 世界实体模型（可选）

**没有它会怎样**：世界实体检测（采矿、伐木、采集物识别）不可用。
引擎在需要时会抛 `YoloUnavailableError` —— **响亮失败，不静默降级**，
你会看到明确报错而不是"检测结果一直是空"。

**怎么拿**：

1. 从 [BetterGI 官方发布页](https://github.com/BetterGI/BetterGI/releases)
   下载安装包，**不要** clone 源码仓库到本项目里；
2. 或者到 [BetterGI-MaaEnd/Assets](https://github.com/BetterGI/MaaEnd) 看模型说明；
3. 装完后，把 `bgi_world.onnx` 和 `classes_bgi_world.txt` 放到：

```
<仓库根>/recognition/models/pretrained/
```

**怎么确认放对了**：

```powershell
python -m autopilot.tools.check_env
```

它会列出每个资产的实际探测路径和是否存在。

---

### 2. 传送点坐标库（可选，但影响一个功能）

**没有它会怎样**：传送点 OCR / 按地名寻路**不可用**。
引擎会抛 `FileNotFoundError` 并指出期望路径；
Console 的地图 tab 仍可用，只是没有"输入地名跳转"。

**推荐做法：自建**（Apache-2.0，与 GPL 无关）

把你的传送点导出成下面这个结构，放到
`<仓库根>/recognition/data/teleport_points.json`：

```json
{
  "schema_version": 1,
  "language": "CHS",
  "points": [
    {
      "id": 1001,
      "name": "七天神像-风",
      "type": " Statue",
      "areas": ["蒙德", "风起地"],
      "position": [1859.34, 0.0, -575.60],
      "tranPosition": [1855.10, 0.0, -571.20]
    }
  ]
}
```

字段含义见 `recognition/data/schema.md`。

**或者**：你手上有 BetterGI 且接受自己承担 GPL 义务，
就把它的 `tp.json` 放到 `<仓库根>/recognition/data/_ref_tp_sample.json`。
引擎的搜索顺序是 `teleport_points.json` → `_ref_tp_sample.json`。

---

## 本仓库**确实使用**了 GPL 项目（但不构成传染）

| 项目 | 许可 | 用法 | 为什么安全 |
|---|---|---|---|
| BetterGI | GPL-3.0 | 读它的 `tp.json`（用户自备） | 只读数据，**不链接、不 import** |
| MaaEnd | | 只在注释里引用设计思路 | 零 import；`ultralytics`（AGPL）只在 `tools/` 的训练脚本里，运行期零 import |

**怎么自己验证**（不要只信文档）：

```powershell
# 运行期路径里有没有 ultralytics / MaaEnd？
python -c "import ast,pathlib;[print(p) for p in pathlib.Path('plugin').rglob('*.py') if 'ultralytics' in p.read_text(encoding='utf-8',errors='ignore')]"
# 预期：只列出 autopilot/tools/train_yolo.py 和 convert_to_onnx.py
```

---

## 关于米游社资产（**不是** GPL，但同样不分发）

| 资产 | 体积 | 许可状态 | 处置 |
|---|---:|---|---|
| `teyvat_P0.png` | 133 MB | miHoYo 版权 | 排除 |
| `teyvat_N1.png` | 40 MB | miHoYo 版权 | 排除 |
| `teyvat_P0_thumb.png` | 825 KB | miHoYo 版权（降采样衍生） | 排除 |
| `teyvat_N1_thumb.png` | 839 KB | miHoYo 版权（降采样衍生） | 排除 |
| `P0_pyramid/` | 89 MB | miHoYo 版权 + 体量 | 排除 |
| `P0_color/` | 124 MB | miHoYo 版权 + 体量 | 排除 |
| `mihoyo_tiles/` | 67 MB | miHoYo 版权 + 大量损坏 webp | 排除 |

这些和 GPL 无关，是**单纯的版权问题**：Apache-2.0 授权不了别人的美术资产。

**随包保留了**：`teyvat_P0.png.anchors.json`（2.3 KB，23 个**人工标注**的坐标锚点）——
那是我们自己的标定成果，且是地图缩放/换算功能的精度基准。

缺底图时 Console 的地图 tab 会显示"缺底图"而不是崩掉，
详见 [`ASSETS.md`](ASSETS.md)。

---

## 结论

- 本仓库**不含**任何 GPL-3.0 或 AGPL-3.0 的源码、数据、模型；
- 运行期**零** import GPL/AGPL 包（`tools/` 的训练脚本除外，它们不是运行期）；
- 引入 GPL 资产是**用户自备行为**，仓库只提供读取接口；
- 如果你把 GPL 资产放进本仓库再分发，**你的分发受 GPL-3.0 约束**——
  这是 GPL 的传染性，与本仓库的许可无关。
