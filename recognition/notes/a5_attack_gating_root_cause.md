# A5 调查报告：攻击无门控的根因

> 2026-10-16 ｜ **只诊断，未改任何代码**（按任务要求）
> 来源：`recognition/notes/demo_roughness_audit.md` 的 A5 项

## 一 · 现象回顾

真机 run `20261014_215401`（80.5 s，唯一启用的规则是 `world_attack_only_with_enemy`）：

| 时刻 | `ENEMY_DETECTED` 的 count | 期间按键次数 |
|---|---:|---:|
| t=0 ~ 22.2 s | **从未上报过**（检测器还没跑） | **100 次** |
| t=22.2 s | **0** | 30 次 |
| t=28.5 s | 2 | 30 次 |
| t=61.2 s | **0** | **70 次** |

355 次攻击全部 `outcome=OK`，累计 4.62 次/秒。

## 二 · 根因（已实证，非推断）

**`ENEMY_DETECTED` 的计数与规则用的 `has_nearby_enemy()` 读的是两个不同的字段，
且其中一个字段根本不存在。**

### 2.1 规则侧（会触发）

`autopilot/policy/context.py:466-484`：

```python
group = getattr(e, "group", kind)
if group == "combat":
    bb = getattr(e, "bbox", None)
    if bb and float(bb[1]) < ENEMY_HEALTH_BAR_Y_MAX:   # ← bbox[1]，真实归一化 y
        return True
```

### 2.2 事件侧（不计数）

`autopilot/core/loop.py:961-967`：

```python
_k = str(getattr(_e, "kind", "") or "")
if _k == "enemy_identify" or (
        _k == "health_bar"
        and float(getattr(_e, "y", 1.0)) < 0.5):      # ← .y 在 WorldEntity 上不存在
    _c += 1
```

### 2.3 实证

```
WorldEntity 字段 = ['kind', 'bbox', 'confidence', 'screen_pos']
hasattr(WorldEntity, 'y')  -> False

取一个真实的敌人血条：kind='health_bar', bbox=(0.30, 0.22, 0.05, 0.02)

loop.py  判据：getattr(e,'y',1.0) -> 1.0 ；1.0 < 0.5 -> False  ⇒ 不计数
context 判据：e.bbox[1] -> 0.22   ；0.22 < 0.5 -> True   ⇒ 规则触发
```

`getattr(_e, "y", 1.0)` 的**兜底默认值 1.0 让条件恒为 False** ——
`health_bar` 实体**永远**计不进 `ENEMY_DETECTED`。

### 2.4 为什么偏偏是 health_bar

`KIND_GROUPS`（`world_entities.py:176`）只有两个类和战斗有关：

| kind | group |
|---|---|
| `enemy_identify` | `enemy` |
| `health_bar` | `combat` |

而 `context.py:441-444` 自己的注释记着：三次真机 smoke 里
**`enemy_identify` 一次都没检出**，`health_bar` 分别检出 6 / 5 / 10 次。

> 也就是说，生产里**唯一**能让这条规则触发的实体就是 `health_bar`，
> 而 `health_bar` 恰恰是事件计数看不见的那一个。

**闭环成立**：规则触发 → 普攻 → `ENEMY_DETECTED` 的 count 恒为 0。

## 三 · 这不是「攻击没有门控」

规则门控**是有的、也真的在生效** —— 它只是**看不到自己触发的原因**。
所以 A5 的正确描述不是「无门控」，而是：

> **门控正常，但门控的判据与事件的判据用了不同的字段，
> 其中一个字段不存在且被默认值静默兜住了。**

## 四 · 与已知问题的同构性

这与 `core/loop.py:926-935` 那个「`POSITION_CHANGED` / `PATHFIND_ARRIVED`
从未发出过」是**同一类缺陷**：

| | 条件 | 实际取值 | 结果 |
|---|---|---|---|
| 位置事件 | `quality == "TRACKING"` | quality 从不取该值 | 分支恒 False |
| **敌人数** | **`e.y < 0.5`** | **`.y` 不存在 -> 1.0** | **分支恒 False** |

两次都是 `getattr(x, "不存在的键", 兜底值)` 让条件**静默**恒假，
既不报错也不告警。若当时有单测覆盖这个分支，第一次就会红。

## 五 · 最小修复方案（本轮未实施）

**改动点：`autopilot/core/loop.py:961-967` 一处，2 行。**

```python
for _e in _ents:
    _k = str(getattr(_e, "kind", "") or "")
    _bb = getattr(_e, "bbox", None)
    _y = float(_bb[1]) if _bb and len(_bb) >= 2 else 1.0
    if _k == "enemy_identify" or (_k == "health_bar" and _y < 0.5):
        _c += 1
```

**更彻底的做法（推荐，但改动面更大）**：让事件侧**复用规则侧的判据**，
而不是把同一句业务逻辑抄两遍。两处各写一份判据，本身就是这次出事的结构原因。

**配套必须做的两件事**（否则改完仍然看不见）：

1. **补单测**：构造 `health_bar`（`bbox[1]=0.22`）的实体，断言
   `ENEMY_DETECTED` 的 count ≥ 1，且 `has_nearby_enemy()` 与之**一致**。
   最好写成「两者对同一组实体必须给出相同结论」的**一致性测试**，
   而不是各自钉一个期望值 —— 那才挡得住下一次漂移。
2. **兜底默认值不能静默**：`_bb` 缺失时应当发一条 WARN（或至少计入
   `stats` 的异常计数），而不是悄悄按 1.0 处理。

## 六 · 对 demo 的影响评估

| 问题 | 现状 | 修完之后 |
|---|---|---|
| 规则门控 | 正常 | 不变 |
| `ENEMY_DETECTED` count | 恒 0（health_bar 不可见） | 会正确反映 |
| YUI 台词「有怪！N个」 | 几乎只靠 `enemy_identify`，而它从不出现 | 会真正说得出来 |
| 4.62 次/秒连击 | **不是**门控缺失，是节奏无人为干预 | 不变（属 B1，另案） |

⚠️ **修 A5 不会让攻击变少。** 门控本来就是对的，少的是「看得见」。
真正让角色在无敌人时也猛打的原因**另有其人**，本报告未定位。
（`enemy_identify` 从不检出 → 规则长期靠 `health_bar` 撑着，
而 `health_bar` 是「血条」不是「敌人本体」，四只史莱姆和一只 BOSS
在检测器眼里没有区别 —— 这很可能才是无差别开打的真正原因，
属于需要重新标定检测语义的问题，不在本次修复范围内。）

## 七 · 复现方式

```powershell
cd <REPO_ROOT>\plugin\plugins\genshin_game_buddy
<REPO_ROOT>\.venv\Scripts\python.exe -c @"
from autopilot.perception.world_entities import WorldEntity
e = WorldEntity(kind='health_bar', bbox=(0.30,0.22,0.05,0.02),
                confidence=0.9, screen_pos=(100,50))
print('has .y ?', hasattr(e,'y'))          # False  <- 根因
print('e.bbox[1] =', e.bbox[1])             # 0.22   <- 规则真正用的
"@
```
