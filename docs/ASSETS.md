# 资产清单 · 哪些随包、哪些要自备、缺了会怎样

公开发布包把**版权资产和超大文件**全部排除在外（理由见
[`GPL_BOUNDARY.md`](GPL_BOUNDARY.md)）。这份文档让你在**缺资产**时
知道自己缺什么、为什么缺、怎么补。

设计原则：**能降级的就降级，该响亮失败的绝不静默**。
下表最后一列就是这个取舍的落点。

---

## 一、随包分发的（你 clone 完就有了）

| 路径 | 内容 | 谁在用 |
|---|---|---|
| `recognition/configs/rois.toml` | UI ROI 归一化坐标 + 阈值 | `roi_loader` / `minimap_mask` |
| `recognition/configs/minimap_scale.toml` | 小地图像素 ↔ 世界单位换算 | `TileTracker` |
| `recognition/templates/ui/*.png` | 15 张 UI 模板裁剪图 | 6 个 detector 的模板匹配 |
| `recognition/data/mihoyo_maps/teyvat_P0.png.anchors.json` | 23 个人工标注锚点（自产） | 地图缩放/坐标换算基准 |
| `recognition/data/routes*/` | 21 条路线 JSON（自产 schema） | 路线分类与导航 |
| `recognition/data/user_waypoints.json` | 用户自存路点（空模板） | 路线导入 |
| `recognition/exports/benchmark/scripts/` | 147 个识别/寻路/标定脚本 | 产品与研究双用 |
| `recognition/exports/benchmark/datasets/` | 41 个特征表 CSV（自产） | 阈值标定、分类器评估 |
| `plugin/plugins/genshin_game_buddy/vendor/winrt/` | Windows 投影运行时（含 `.pyd`） | OCR / WGC 截图通道 |

> `vendor/winrt/` 里是 **CPython 3.11 + win_amd64** 的预编译二进制。
> 换 Python 版本需要自己 `pip install winrt` 或换宿主环境。

---

## 二、要自备的

| 资产 | 体积 | 为什么不给 | 缺了会怎样 |
|---|---:|---|---|
| `P0_pyramid/` | 89 MB | miHoYo 版权 + 体量 | **响亮失败**：`FileNotFoundError` 指出期望路径（`ChunkLoader` / `TileTracker` / Console 地图缩放） |
| `teyvat_P0_thumb.png` | 825 KB | miHoYo 版权 | **降级**：地图 tab 显示占位图，点选/缩放不可用，其余照常 |
| `teyvat_P0.png` | 133 MB | miHoYo 版权 | **降级**：只在离线标定脚本里用 |
| `bgi_world.onnx` | 11 MB | GPL-3.0 | **响亮失败**：`YoloUnavailableError`（不静默降级） |
| `teleport_points.json` | — | 用户数据（可选） | **降级**：传送点 OCR 不可用，其余照常 |

### 放哪

```
<仓库根>/
├── recognition/
│   ├── models/pretrained/          ← bgi_*.onnx + classes_bgi_*.txt
│   ├── data/
│   │   ├── teleport_points.json    ← 自建或自备
│   │   └── mihoyo_maps/
│   │       ├── teyvat_P0_thumb.png        ← 自备
│   │       └── P0_pyramid/                ← 自备（含 chunks_index.json）
│   └── configs/                     ← 随包，一般不用动
├── plugin/plugins/genshin_game_buddy/
└── console/
```

---

## 三、怎么检查当前状态

```powershell
# 引擎侧：逐项列出资产的实际探测路径与是否存在
python -m autopilot.tools.check_env
```

它按 `环境变量 > 目录发现 > 空` 的优先级解析路径，
输出每个资产的**最终路径**，而不是笼统的"缺少资产"。

### 可用的环境变量

| 变量 | 作用 | 例 |
|---|---|---|
| `NEKO_RECOGNITION_ROOT` | 识别资产根 | `D:\my\neko\recognition` |
| `NEKO_ENGINE_PATH` | Console 侧插件目录 | 插件与主控台分开部署时 |
| `NEKO_ENGINE_PYTHON` | 引擎解释器 | 引擎单独一套 venv 时 |
| `NEKO_CONSOLE_DIR` | 主控台目录（插件侧用） | `D:\my\neko\console` |
| `NEKO_P0_PYRAMID` | 金字塔目录 | 放别的盘时 |
| `NEKO_COORD_TRANSFORM` | `coord_transform.py` 位置 | 离线脚本 |
| `NEKO_SCENE_TREE` | `coord_scene_tree.py` 位置 | 离线脚本 |
| `NEKO_TELEPORT_POINTS` | 传送点数据 | 自定义位置 |
| `NEKO_PLUGIN_ROOT` | 插件根（假寻路脚本） | 测试 |

**优先级**：环境变量 > 从代码位置向上找 `recognition/configs/rois.toml` >
空（空 = 响亮失败，报错会写明"请设 `NEKO_RECOGNITION_ROOT`"）。

> 刻意**不提供**任何绝对路径兜底。早年这里写死了开发机的
> `D:\NEKO\...`，它在开发机上永远成立，于是没人发现它是坏的；
> 换个用户就静默指向一个不存在的目录，报错是"文件不存在"——
> 看不出是路径配错了还是文件丢了。

---

## 四、缺资产时程序的表现

**能跑的照跑，缺的只影响自己那一块。** 具体到每条链路：

| 场景 | 行为 |
|---|---|
| 没有底图缩略图 | Console 地图 tab 显示占位区，**其余 tab 正常** |
| 没有金字塔 | 地图缩放到 2x 以上时报 `FileNotFoundError` 并给出期望路径；1x/2x 可用 |
| 没有 YOLO 模型 | 世界实体检测抛 `YoloUnavailableError`，规则引擎其余部分照跑 |
| 没有传送点数据 | 传送点搜索返回空，坐标输入寻路仍可用 |
| 没有 `rois.toml` | **启动即失败**（这是必需资产，不是可选的） |

---

## 五、测试为什么有一批是 skip 而不是 pass

```powershell
pytest plugin/plugins/genshin_game_buddy/tests -q
# 1601 passed, 30 skipped, 1 xfailed
```

31 个 skip 全部是"资产不在本机"，且 skip reason 直接写明缺什么：

- 19 个 —— `autopilot/_runtime/capdiag_wgc.png` 真机截图
  （运行期产物，本来就不进版本库）
- 10 个 —— `P0_pyramid/`（89 MB）
- 1 个 —— "桌面不是静止画面"（by-design，本机在跑终端）

**这是刻意的**：缺可选资产不是代码回归。混进失败里会让人以为
地图/传送点功能坏了，而实际只是这台机器没自备数据。
按上表补齐资产后，这些用例会自动恢复执行。

Console 侧同理：`710 passed, 70 skipped`。
