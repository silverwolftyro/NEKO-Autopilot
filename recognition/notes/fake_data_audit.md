# 假数据审计报告

> 2026-12 12 扫 · **只报告，未删除任何文件**
> 起因：盘点时发现 `NEKO-Console/runtime/cap3_*` 是同一帧的复制品，
> 想确认还有没有别的「看起来像证据、其实没测过」的文件。

---

## 1 · 图片：哪些是真独立采集的

### ✅ `autopilot/_runtime/`（11 张）—— 全部有效

| 分组 | 张数 | MD5 |
|---|---|---|
| `capdiag_wgc.png` | 1 | `a464077b2732` |
| `capmatrix_*`（2026-10-12 新采） | 10 | 10 个**互不相同** |

`capmatrix_*` 是上一轮用 `capture_matrix` 工具新采的，工具自带 MD5 自检
（9~10 张里有重复就报「采集无效」并返回非 0）。**与 `capdiag_wgc.png` 也不同。**

**能证明什么**：WGC/BitBlt/DXcam/mss 四条通道在
{正常显示 / 拖到屏幕外 / 最小化} 三种窗口状态下的**真实表现**。
其中三条是硬结论：
- WGC 在窗口**最小化**时仍抓到完整画面（不是黑帧）
- 窗口拖出屏幕时 **dxcam 和 mss 都失败**，只有 bitblt / wgc 可用
- **dxcam 抓帧尺寸（888×553）与其他三条（1350×900）不同**

**不能证明什么**：这是 **tkinter 测试窗口**，不是硬件加速的游戏窗口。
WGC 在原神上的行为另有 `capdiag_wgc.png`（2560×1600 真机）佐证。
两者不能互相替代 —— **不要用 capmatrix 推断游戏场景**。

---

### ⚠️ `NEKO-Console/runtime/`（19 张）—— 8 张是复制品

| MD5 | 张数 | 文件（磁盘上的真实名，注意是乱码） |
|---|---|---|
| `190cbfc5bf97` | **3** | `cap3_A_*bitblt` / `cap3_B_*bitblt` / `cap3_C_*bitblt` |
| `f7329049b69d` | **2** | `cap3_A_*dxcam` / `cap3_B_*dxcam` |
| `cdd0504ff53e` | **3** | `cap3_A_*mss` / `cap3_B_*mss` / `cap3_C_*mss` |
| `c1d92a6c0e86` | **2** | `cap_normal_bitblt` / `cap_offscreen_bitblt` |

**19 张里只有 11 张是独立的**，8 张是同一帧的复制品。

更麻烦的是**文件名本身是坏的**：磁盘上的名字是
`cap3_A_姝ｅ父鏄剧ず_bitblt.png`（GBK↔UTF-8 双重编码的乱码），
而 `cap3_B_*`（屏幕最小化）、`cap3_C_*`（最小化）**根本没各自采集**，
它们和 A（正常显示）逐通道完全相同。

**它们能证明什么**：什么都不证明。
「最小化的窗口被抓成什么样」这个问题，**至今没有任何有效样本**。

---

## 2 · 事件流：48 个 run，19 个有实质内容

| 类别 | 数量 |
|---|---|
| 总数 | 48 |
| 空文件 | 0 |
| < 2 KB（空壳，只有起止事件） | **29** |
| > 2 KB（有实质内容） | **19** |

最大的几个：

| run | 大小 |
|---|---|
| `20261010_225316` | 609 KB |
| `20261010_224759` | 356 KB |
| `20261011_180732` | 210 KB ← PickMark 排查那轮 |
| `20261011_000227` | 152 KB ← 黑帧事故那轮 |

**29 个空壳**：只有 `RUN_STARTED` / `CORE_STARTED` / `RUN_STOPPED` 三条，
是「起了就停」的调试 run。**拿它们证明任何功能都是错的**。

**时间线自检**：抽 12 个有内容的 run 比对「run_id 内嵌时间戳 vs 文件 mtime」，
**只有 1 个差 10.8 小时**（`20261010_013718`，用户中途离开过）。
其余都在 1 分钟内 —— 说明这些事件流是**当场写的，不是后来伪造的**。

---

## 3 · 🔴 状态文件：陈旧度差异极大

| 文件 | 最后写入 | 判定 |
|---|---|---|
| `autopilot/_runtime/session_state.json` | 见下（**假新鲜**） | ❌ 不可信 |
| `autopilot/_runtime/state_snapshot.json` | 20.7 小时前 | 🟡 陈旧但**内容自洽** |
| `autopilot/_runtime/overlay_pos.json` | 38.8 小时前 | 🟡 陈旧 |
| `NEKO-Console/runtime/console_bridge.json` | 38.5 小时前 | 🟡 陈旧 |
| `autopilot/_runtime/pathfind_state.json` | 63.1 小时前 | 🔴 寻路早就不在了 |
| `autopilot/_runtime/run_current.txt` | 20.7 小时前 | 指向 `20261011_180732` |

### 🔴 `session_state.json` 是「假新鲜」—— **测试污染**

审计时发现它显示「0.5 小时前写入」，但**当时没有任何进程在跑**，
今天（10-12）也**没有任何日志写入**（最新日志是 10-11 22:05）。

做了对照实验才定位到根因：

```
跑测试前: mtime=14:20:49   md5=61FD792BB975
   pytest tests -q  →  1106 passed
跑测试后: mtime=14:54:25   md5=DF06802E3593
>>> 测试确实写进了真实 runtime
```

再二分定位到**具体 3 个测试文件**（它们在构造插件实例时会走
`_publish_session`，而写盘路径来自模块级常量 `protocol.DEFAULT_RUN_DIR`，
**没有被这些测试 monkeypatch 到 tmp_path**）：

```
tests/integration/test_plugin_entries.py
tests/unit/test_config_path.py
tests/unit/test_session_probe.py
```

**后果**：
1. `session_state.json` 会被测试改写，**不能当作「插件最近跑过」的证据**
2. 更糟的是它的 `display_name` 字段写进去的是 **`鍘熺`**（原神的乱码），
   说明这条写盘路径本身还有编码问题 —— 值得单独查

**建议**（本轮未做，按要求只报告）：给这 3 个测试补
`monkeypatch.setattr(protocol, "DEFAULT_RUN_DIR", tmp_path)`，
和 `test_session_state_overlay.py` 已经做对的那个保持一致。

---

## 4 · 其他

**`capdiag_wgc.png` vs `capmatrix_*`**：11 张 MD5 **全部互不相同**，
不存在复用。新矩阵没有污染真机截图。

---

## 5 · 汇总：哪些文件能当证据

| 资产 | 能证明 | 不能证明 |
|---|---|---|
| `capdiag_wgc.png` | 原神全屏下 WGC 能抓到完整画面 | 其它三条通道在游戏里的表现 |
| `capmatrix_*`（10 张） | 四通道 × 三窗口状态，用 tkinter 窗口测的 | 游戏场景的任何事 |
| 19 个有内容的事件流 | 48 次真实运行、7091 条事件、1352 次真实按键 | 29 个空壳 run 什么也证明不了 |
| `state_snapshot.json` | 那一次 run 的收尾统计（health=OK 等） | 当前状态（已 20 小时） |
| `session_state.json` | **什么都证明不了** | ❌ 测试写的 |
| `cap3_*`（8 张） | **什么都证明不了** | ❌ 复制品 + 乱码名 |
| `pathfind_state.json` | 63 小时前有过一次寻路中间态 | 寻路现在能不能用 |

---

## 6 · 一条方法论

盘点这一轮再次验证了那条结论：**文件名会骗人，MD5 不会。**

`cap3_A_正常显示` / `cap3_B_屏幕最小化` / `cap3_C_最小化` 三个名字
清清楚楚写着三种状态，实际是同一帧复制了三份。
如果照着名字去验「最小化时黑帧判定会不会失效」，会得出一条**完全基于
假数据**的结论，然后花时间去修一个根本没被测过的功能。

所以现在每个采集工具都自带 MD5 自检（`capture_matrix` 会在重复时
直接报「采集无效」并返回非 0），这是踩过一次的教训。
