# 传送点数据 schema

> 本项目自有的传送点数据格式。**字段名与 BetterGI 的 `tp.json` 故意不同**，
> 避免把外部数据的 schema 缝进代码。映射逻辑集中在
> `perception/loaders/_tp_converter.py`，换数据源只改那里。

## 合规前提

**本仓库不内置任何传送点数据。** BetterGI 是 GPL-3.0，
其 `tp.json` 是它的数据文件。完整口径见 [`../notes/LICENSING.md`](../notes/LICENSING.md)。

本 schema 描述的是**我们自己的数据格式**。要填数据，就得自己录、或取得授权。

---

## 顶层

```jsonc
{
  "version": 1,              // int，本 schema 版本
  "source": "...",           // string，数据来源说明（元信息，不是数据）
  "regions": [ /* 区域 */ ]
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `version` | int | schema 版本，消费方据此判断是否需要迁移 |
| `source` | string | 来源标识。用来看这份数据从哪来，便于审计与替换 |
| `regions` | array | 区域列表，通常按地图分 |

---

## 区域 region

```jsonc
{
  "region_id": "3",                    // string，稳定标识
  "display_name": "提瓦特大陆",          // string，给人看的
  "points": [ /* 传送点 */ ]
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `region_id` | string | **字符串**。源数据里可能是 int，统一成 str 便于做主键 |
| `display_name` | string | 人类可读名。源里缺失时回退到地图代号（如 `Teyvat`） |
| `points` | array | 该区域下的传送点 |

---

## 传送点 point

```jsonc
{
  "id": "1",                            // string，主键
  "name": "北风之狼的庙宇",               // string
  "category": "OneTimeDomain",          // string，传送点类型
  "country": "蒙德",                     // string，国家/地区
  "areas": ["坠星山谷", "千风神殿"],       // string[]，所属区域标签
  "world_pos":   [0.0, 0.0, 0.0],       // number[3]  传送点**本体**坐标
  "landing_pos": [0.0, 0.0, 0.0],       // number[3]  传送**落点**坐标
  "extra": {}                            // object，未知字段的收容处
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 主键。源里类型不定，统一成 str |
| `name` | string | 传送点名称 |
| `category` | string | 类型（神像 / 秘境 / 洞天 / boss 点…） |
| `country` | string | 国家 / 地区 |
| `areas` | string[] | 所属区域标签，可多值 |
| `world_pos` | number[3] | **本体**坐标 `[x, y=高度, z]` |
| `landing_pos` | number[3] | **落点**坐标，和本体不同（差几米） |
| `extra` | object | 源格式的未知字段全部收进这里，**不丢弃** |

### ★ `world_pos` 与 `landing_pos` 为什么必须分开

传送之后角色站在 `landing_pos`，**不是** `world_pos`。
两个混用会让角色卡进墙里或悬空。做端到端时这个区别是硬伤。

### ★ 坐标是 3D 世界坐标，不是屏幕坐标

`[x, y, z]`，`y` 是高度（实测值 100～400 量级）。
**转屏幕坐标需要相机矩阵** —— BetterGI 靠截屏 + 特征点匹配定位，
我们没有现成方案，暂列为未解决问题。

---

## 与 BetterGI `tp.json` 的字段映射

| BetterGI | 本项目 | 说明 |
|---|---|---|
| `data[].sceneId` | `regions[].region_id` | int → str |
| `data[].description` | `regions[].display_name` | 缺则回退 `mapName` |
| `data[].mapName` | *(丢弃)* | 本 schema 无对应字段；可读性已由 display_name 承担 |
| `data[].points` | `regions[].points` | 同名 |
| `point.id` | `point.id` | → str |
| `point.type` | `point.category` | 改名，避免与源绑定 |
| `point.name` | `point.name` | 同名 |
| `point.country` | `point.country` | 同名 |
| `point.areas` | `point.areas` | 同名 |
| `point.position` | `point.world_pos` | 改名 |
| `point.tranPosition` | `point.landing_pos` | 改名 |
| `point.rewards` 等 | `point.extra` | 收容，不丢 |
| `version` | `version` | 直传 |
| *(无)* | `source` | 我们补的元信息 |

映射表在代码里只有一份：`_tp_converter.py` 的 `_REGION_KEYS` / `_POINT_KEYS`。
改 schema 时改那张表，别改转换逻辑。

---

## 填充数据的三种来源（按优先级）

1. 显式传入 `user_path`
2. 环境变量 `NEKO_TELEPORT_POINTS`
3. `recognition/data/teleport_points.json`（自有数据）
4. 本机 BetterGI 安装（**只读探测，仅供开发期对照**）

四种都找不到 → **抛 `FileNotFoundError`**，不返回空表。
