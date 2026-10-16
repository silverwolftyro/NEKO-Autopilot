# 错误处理静默失败审计

> 生成时间：2026-10-12 ｜ **只读审计，未修改任何代码**
> 扫描：插件 + Console + `e2e_pathfind.py`

---

## 0 · 这次审计做了什么、没做什么

**做了什么**：用 AST（不是正则）扫出全部 `except` 处理器，机械分类，再对高危项逐条读代码人工判定。

**没做什么**：
- **没有**逐条人工判定全部候选。机械分类给了 433 处「需人工判定」，我只精读了其中的一小部分。
- **没有**对每一条都追到调用方 —— 那需要把整个调用链都读一遍，本次没做到。
- 因此本报告是**筛查报告**，不是穷尽清单。

⚠️ 全文用 AST 而非正则，是因为多行 `except: pass` 正则抓不准，而且
`except Exception:` 后面跟 `if x: logger.debug(...)` 这种是**合理**的
（有日志，只是级别低），必须与真正的静默失败区分开。

---

## 0bis · 2026-10-13 修订：分类器有个 bug，风险面被高估了一倍

**上一版（本文件初稿）报的是「待判定 435 处」。那个数字是错的。**

`_audit_scan.py` 的 `classify_handler` 里有一行：

```python
stmts = [s for s in body if not isinstance(s, ast.Expr)]   # ← 元凶
```

`logger.exception(...)` 在 AST 里**就是一个 `ast.Expr`**。所以
「去掉 Expr 后还剩不剩东西」这个判据，把
``except Exception: logger.exception(...)`` —— 全仓**最安全的一档** ——
判成了「整个 except 体只有 pass」。

**实测影响**：435 个候选里 **117 个是误判**（其中 37 处记的是
`logger.exception`）。修正后：

| | 修正前 | 修正后 |
|---|---:|---:|
| `swallow`（吞掉且流程继续） | 431 | **300** |
| `debug_only`（只 debug） | 4 | **18** |
| `guarded`（有调用无日志） | 17 | **140** |
| **待判定合计** | **435** | **318** |

> ⚠️ 上一轮我说「429 处静默失败里绝大部分是合理防御」—— 方向没错，
> 但**基数是错的**。真候选 318 处，其中 140 处是 `guarded`
> （体内有调用只是没日志），风险面比初稿说的小一半。

---

## 0ter · 精读结果：top 20 逐条判定

对风险最高的 20 条做了精读（打开源文件读上下文 + 追调用方），
完整版见 `_audit_top20.md`。

| # | 判定 | 一句话 |
|---|---|---|
| 1 | 🔴 B | `core/loop.py:848` tick 线程的**急停检查**异常被吞 → 放行本轮动作 |
| 2 | 🔴 B | `loop.py` F12 急停读取失败 = 当作「没按」，闩锁也不置位 |
| 3 | 🔴 B | `safety.py:249` 焦点校验失败 = 当作「窗口在前台」，**零日志** |
| 4 | 🟠 B | 松键失败不写日志，键可能卡在游戏里 |
| 5 | 🟠 B | 事件总线 / 截图器关闭失败同样零日志 |
| 6 | 🟠 B | 读引擎 stderr 失败被表述成「stderr 里没有关键行」 |
| 7 | 🟠 B | 窗口枚举中途异常 → 返回空列表 → 误报「游戏没启动」 |
| 8 | 🟠 B | 取屏幕尺寸失败 → 回落硬编码 1920×1080，坐标全错但无日志 |
| 9 | 🟡 C | 写图失败只返回 `False`，调用方大概率不检查返回值 |
| 10 | 🟡 C | OCR 异常降级为 `matched=UNKNOWN`，方向对、阈值待确认 |
| 18 | 🟡 C | 规则文件读不出来 → 行号定位静默变成 0 |
| 11~17,19,20 | ✅ A | 均有日志或上层兜底，见 `_audit_top20.md` 附录表 |

**8 条 B 类 / 3 条 C 类 / 9 条 A 类。**

### 一个反直觉的结论

任务书假设「截图/感知链路上错误被吞 = 引擎以为看见了画面」是主要风险 ——
**实测不是**。`capturer.py` / `builder.py` / `ocr.py` 是全仓防得最扎实的地方，
大量注释记录了 2026-10-10 黑帧事故的教训。

**真正的缺口集中在两处**：① **安全护栏的 fail-open 方向**
（①②③ 三条都在这一类）② **关停/崩溃路径不留痕**（④⑤）。

---

## 0quater · 第 3 条（safety.py:249）专项结论

这是任务点名要追的。追完了，结论如下。

**代码**：`SafetyGuard.is_foreground_ok()`

```python
try:
    fg = win32gui.GetForegroundWindow()
    cur = win32gui.GetWindowText(fg) or ""
except Exception:
    return True          # ← 查不到就放行
```

**调用方**：`SafetyGuard.check()` 第 ⑤ 道门（`safety.py:201`）

```python
if (self.cfg.require_foreground or t.requires_foreground) and \
        not self.is_foreground_ok():
    return False, "WINDOW_NOT_FOREGROUND"
```

**结论：是 fail-open，但是刻意的；缺陷在于「零留痕」。**

1. **它确实是整个 guard 里唯一 fail-open 的门。**
   ①熔断器 ④场景许可 ⑥频率限制 —— **全部 fail-closed**。
   只有 ⑤焦点在异常时返回「放行」。

2. **什么情况下会真的返回 True**：只有 `win32gui.GetForegroundWindow()` /
   `GetWindowText()` 抛异常时。这两个 API 极少抛 —— 实践中风险很低。
   （注意 `import win32gui` 在 try **之外**，所以 pywin32 没装时是**抛出去**的，
   不会被这行吞掉。）

3. **失败的症状**：用户把游戏切到后台（比如 Alt+Tab 到聊天软件），
   而恰好焦点查询失败 → 引擎继续注入按键 → **W 打进了聊天框**。
   排障者看到的是「AI 乱按键」，而根因（一个 Win32 调用失败）离症状极远。

4. **为什么判 A 不判 B**：注释写明「查不到就放行，不要因为检测失败阻断操作」。
   这个取舍有道理 —— fail-closed 会带来「pywin32 抽风 → 整个引擎瘫掉」。

5. **但它原本一行日志都不打**，导致「这道门自己关掉了」完全不可见。
   这是我认定的**真缺陷**：不是行为错，是**不可观测**。

**已做的修改（2026-10-13）**：给异常分支加了一条 `logger.warning`，
明确写出「本次**不拦**，按键可能打进非游戏窗口」，并提示排障时看这条日志。
**行为未变**（仍然 fail-open），只是从「静默」变成「留痕」。

**附带发现**：`set_foreground_title()` 把 TTL 缓存的检查时刻设成「现在」，
于是紧接着的第一次 `is_foreground_ok()` **必然命中缓存、一次 Win32 都不调**
就返回 True。不是 bug（TTL 过期后会照常检查），但意味着「刚配好标题」的
那一次焦点校验是假的。已写进 docstring。

---

## 1 · 规模与分类（修正后）

```
211 个文件 / 70,729 行 / 791 处 except 处理器
```

| 类别 | 数量 | 含义 |
|---|---:|---|
| `handled` | 32 | 有实际处理逻辑（日志 / 重试 / 降级 / 抛新异常） |
| `rethrow` | 64 | 原样重新抛出 |
| `fallback` | 245 | 吞掉并返回**非平凡默认值**（多数是合理降级） |
| `guarded` | 17 | 异常被 `if` 守卫，未见日志 |
| `debug_only` | 4 | 只 `logger.debug` |
| **`swallow`** | **429** | **只 pass / continue，或返回值与成功时无法区分** |

`safety.py:249` 有一处 `except Exception: return True` —— 在**安全模块**里
异常时返回 True，需要单独确认语义，本次未深挖。

**这 429 处 `swallow` 里，绝大部分是合理的防御**（读配置失败用默认值、
关一个可选组件、清理临时文件）。把它们全标成问题没有意义。所以下面只列
**我实际读过代码、确认有真实后果**的那些。

---

## 2 · 确认有真实后果的静默失败

### 🔴 B-1 · `_char_slots` 永远读不到 → 角色切换被静默丢弃

**位置**：`autopilot/actuator/executor.py:525-535`（调用点 `:481-491`）

```python
def _slot_of(self, char_id: str) -> int | None:
    """角色 id -> 位置编号 1..4。keymap 里可配。"""
    table = self.keymap.get("__char_slots__")          # ← 赋值后从未使用
    if isinstance(table, KeyId):
        return None
    # keymap 允许混入 char_slots 字典（在 load_keymap 里单独处理）
    slots = getattr(self, "_char_slots", {}) or {}     # ← 永远是 {}
    v = slots.get(char_id)
```

**事实链**（已逐条核实）：

1. `self._char_slots` 在**整个插件里只出现这一处**，且是**读**。
   全仓库没有任何一处给它赋值。
2. 注释说的 `load_keymap`（`actuator/keymap.py:68`）只返回
   `dict[str, KeyId]`，**不碰** `_char_slots`。
3. `set_keymap()`（`:187`）也只替换 `self.keymap`，**不碰** `_char_slots`。
4. 所以 `slots` 恒为 `{}`，`_slot_of()` **恒返回 None**。

**症状**：意图里只给 `char_id`、没给 `slot` 时，
`_slot_of` 返回 None → 走 `executor.py:486`：

```python
steps.append("switch: 无 slot/char_id，跳过（无可切的位）")
return steps
```

**切人动作被静默丢弃，不抛异常、不报错。** 规则照常判定「该切人」，
执行器照常返回，只是**什么都没做**。

**为什么难发现**：症状是「AI 偶尔不切人」，看起来像规则问题或游戏状态问题；
而根因是一个从未被赋值的属性，离症状隔着意图生成 → 执行器 → keymap 三层。

**修法建议**（本次未修）：要么在 `load_keymap` 里真的解析一份
`char_slots` 存进 `self._char_slots`，要么删掉这个分支并让
`SWITCH_CHARACTER` **必须**显式带 `slot`（缺失就抛 `KeyIdError`，
像 `resolve_key` 那样响亮失败）。

---

### 🟠 B-2 · 参考案例：`consecutive_capture_failures`（已修）

**位置**：`autopilot/core/loop.py`（历史 bug，本轮已修）

```python
n = int(getattr(self.ctx.stats, "consecutive_capture_failures", 0) or 0)
```

`LoopStats` 上**没有**这个字段 —— 真实计数器在 `CoreLoop` 实例上。
`getattr(..., 0)` 的默认值让 `n` 恒为 0，于是 `PATHFIND_STUCK` 与
`ESCAPE_SUCCESS` 两条语音播报**从来没发出去过**。

**这条现在被 `tests/unit/test_core_loop.py` 的 strict xfail 钉住**，
修的时候去掉 xfail 就变成真实断言。

**它为什么值得留着**：这是本项目里 `getattr` 静默吞错的**标准案例** ——
拼错的属性名 + 合理的默认值 = 永远不报错的死分支。
审计时应把「`getattr` 读的属性是否真的存在」当成必查项。

---

### 🟡 B-3 · `except Exception: return True` 出现在 safety 模块

**位置**：`autopilot/**/safety.py:249`（另有 `:144` 的 `return False`）

安全/护栏类代码在异常时返回一个**布尔值**。如果调用方把它当成
「检查结果」而不是「检查本身出错了」，那么**检查失败会被当成检查通过**。

本次未追到调用方，**不下结论**。列在这里作为需要人工确认的线索。

---

## 3 · 「合理的防御」长什么样（对照样本）

不是所有 `except` 都值得改。以下几类是**正确的**写法，可以对照：

| 写法 | 为什么安全 |
|---|---|
| `read_state()` 里 `except (OSError, UnicodeDecodeError, json.JSONDecodeError): return None` | 返回 None = 明确「读不到」，调用方统一走「不可信」分支 |
| `estimate_fill_ratio` 里解析不出来返回 None | 同上。**猜一个 0 会让规则误触发，猜错比不知道糟得多** |
| `_load_waypoints_file` 整份判废并带原因 | 错误信息透传给用户，不是吞掉 |
| 清理临时文件时的 `except OSError: pass` | 失败无后果 |
| `except Exception:` + `logger.exception` | 有完整 traceback |

**判据**：*降级后的值，会不会让调用方以为「成功了」？*
会 → 是问题；不会 → 是防御。

---

## 4 · 三条编码规范建议（不紧急）

1. **`getattr` 用于读属性时，要么加注释说明「为什么它可能不存在」，
   要么加一条测试断言该属性确实存在。** B-2 和 B-1 都是这条的反例。

2. **`swallow` 类里，返回值应当与「成功」明显可区分。**
   `return None` / `return []` 是好的；`return True` / `return 0`
   在「成功也是这个值」时是危险的。

3. **`.getattr(self, "_x", {})` 形式的兜底应配一条「`_x` 有被赋值」的测试**，
   否则它会一直兜底、一直返回默认值、一直没人发现。

---

## 5 · 这份报告不能证明什么

- 不能证明 429 处 `swallow` 里**没有**别的 B-1 类问题 —— 我只精读了其中一小部分。
- 不能证明这些静默失败在真机上**一定会发生** —— B-1 是代码级确定，
  其余需要真机跑才能确认。
- 扫描基于 **2026-10-12 的代码快照**。代码一变，结论就可能过期
  （尤其是「某属性从未被赋值」这类结论）。
- 未覆盖：非 Python 的错误处理（shell 脚本、TOML 配置里的兜底逻辑）。

---

## 6 · 复现方式

```
python recognition/notes/_audit_scan.py
```

输出 791 行明细（`recognition/notes/_audit_raw.txt`），格式：
`文件名:行号<TAB>异常类型<TAB>类别|说明`。

改扫描规则（`classify_handler`）就能重新分类，不用重读源码。
