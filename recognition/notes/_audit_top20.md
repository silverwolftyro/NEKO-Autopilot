# 静默失败人工判定 · Top 20

> 审计对象：`_audit_raw.txt` 列出的 `swallow` + `debug_only` 候选。
> 方法：逐条打开源文件读上下文，判断「降级后的返回值/行为会不会让调用方以为成功了」。
> 本轮**只读**，未修改任何源码；未启动原神。
>
> 配套产物（均为只读辅助脚本，可复跑）：
> - `_audit_paths.py` / `_audit_paths.txt` —— 把明细的**裸文件名还原成完整路径**
> - `_audit_recheck.py` / `_audit_recheck.txt` —— 修正分类器误判并量化（见下）

---

## ⚠️ 先说一个影响全局的发现：上一轮的分类器有 bug

`_audit_scan.classify_handler` 的第 36 行：

```python
stmts = [s for s in body if not isinstance(s, ast.Expr)]
```

这行把**所有表达式语句**都过滤掉了。而 `logger.exception(...)` / `logger.error(...)`
在 AST 里正好就是 `ast.Expr`。于是：

```python
except Exception:
    logger.exception("急停检查异常")
```

`stmts` 变成空列表 → 命中 `if not stmts` → 被标成
**「整个 except 体只有 pass —— 出错当没发生」**。

同理 `LOG_MIN_LEVELS = {"debug"}` 只认 `debug`，`exception`/`error`/`warning` 全不计入。

实测（`_audit_recheck.py`，211 文件 / 800 个 except handler）：

| 项 | 数量 |
|---|---|
| 上一轮标为「需人工判定」 | 441 |
| ├─ **handler 内其实有日志调用（误判）** | **113** |
| │　├─ 其中 exception/error 级（最安全的一档） | 37 |
| │　└─ 其中 debug 级 | 30 |
| └─ 确实无任何日志（**真候选**） | **328** |

**结论：上一轮 435 处待判定里，约四分之一（113 处）是误判**，
其中 37 处其实记了 `logger.exception`，属于本审计里最安全的一档。
下面 20 条全部取自**核实过的 328 条真候选**。

> 另：本次复扫为 211 文件 / 70,973 行 / 800 handler，
> 与任务书给的 794 handler 差 6 处、70,825 行差 148 行 —— 代码库在上轮之后有轻微变动，
> 不影响结论。

---

# Top 20（按危害从大到小）

## 1. tick 线程里的急停检查异常被静默吞掉，直接放行本轮动作  [B 类]
位置：`autopilot/core/loop.py:848`
```python
        check = self._panic_check
        if check is not None and not self._panic_event.is_set():
            try:
                if check():
                    logger.info("急停在「%s」处被 tick 线程发现", where)
                    self._on_panic()
                    return False
            except Exception:  # noqa: BLE001
                pass
        return True
```
判定：**B · 静默失败**
会在什么情况下吞掉什么：
`check()` 是 `SafetyGuard.panic_active`。任何异常（Win32 调用异常、guard 内部状态
损坏、测试替身抛错）都会让本函数走到末尾 `return True`，语义是**「检查通过，可以继续」**。
这是安全护栏上的 fail-open，且**一行日志都没有**。
症状：用户按住 F12 想急停，角色继续往前走/继续点击；控制台无任何异常输出，
只有正常的 tick 日志，看起来「一切正常」。
排障者看到：日志里既没有「F12 急停触发」，也没有任何 traceback，
只有一条普通的 tick 记录 —— 与「用户没按 F12」完全无法区分。
补充证据：**同一文件的姊妹代码不一致**。`loop.py:785` 的 watcher 线程做的是
`except Exception: logger.exception("急停检查异常")`（ERROR 级，有留痕）。
两条路径一个记一个不记，说明这里的 `pass` 更像是遗留而非有意设计。

---

## 2. F12 急停读取失败 = 当作「没按」，闩锁也不置位  [B 类]
位置：`autopilot/actuator/safety.py:144`
```python
        try:
            pressed = bool(_user32().GetAsyncKeyState(int(PANIC_KEY)) & 0x8000)
        except Exception:  # noqa: BLE001
            return False
        if pressed:
            self._panic_pressed = True
        elif self._panic_pressed:
```
判定：**B · 静默失败**
会在什么情况下吞掉什么：
`GetAsyncKeyState` 抛异常时返回 `False` = 「急停键没按」，`check()` 于是继续放行动作。
更隐蔽的是 `self._panic_pressed` 闩锁**也不会被置位**，所以连
`:151` 那句 `logger.info("急停键已松开，恢复执行")` 都不会出现 ——
用户侧看不到任何「急停状态异常」的痕迹。
症状：F12 按住但急停不生效，且日志完全无异常。
排障者看到：只有「为什么 F12 没反应」，找不到任何报错。
可能性评估：`ctypes.WinDLL` 已缓存（`simulator.py:333`），该调用实际抛异常的概率低；
但一旦抛就是**静默地失去急停**，属于不该裸奔的护栏。

---

## 3. 焦点校验失败 = 当作「窗口在前台」，放行输入，且零日志  [B 类]
位置：`autopilot/actuator/safety.py:249`
```python
        try:
            fg = win32gui.GetForegroundWindow()
            cur = win32gui.GetWindowText(fg) or ""
        except Exception:  # noqa: BLE001
            return True          # 查不到就放行，不要因为检测失败阻断操作
        self._fg_cache = (title, time.perf_counter())
        return title in cur
```
判定：**B · 静默失败**
会在什么情况下吞掉什么：
注释把设计意图写得很明确 ——「不要因为检测失败阻断操作」，即**有意的 fail-open**。
问题不在方向，而在于**返回值与成功时无法区分**：调用方（`check()` 第 ⑤ 步）
拿到 `True` 就认为「已验证目标窗口在前台」。
症状：用户切到浏览器/微信/另一个游戏窗口，脚本继续往当前焦点窗口发键鼠输入
（在聊天框里打字、在别人的文档里敲东西），而 UI 上一切正常。
排障者看到：没有任何日志。`_fg_cache` 在异常分支**不更新**，
所以缓存时间戳一直是旧的，下一帧还会重试、重试、再返回 `True` ——
故障持续多久都不会有人知道。
补充：`:244` 的 TTL 缓存本身也直接 `return True`，与异常分支的行为完全一样，
进一步掩盖了「有没有真的查过」这个信息。

---

## 4. 松键失败不写日志，键可能卡在游戏里  [B 类]
位置：`autopilot/core/runtime.py:218`
```python
    def _release_keys(self) -> bool:
        sim = getattr(self.executor, "sim", None)
        release = getattr(sim, "release_all", None)
        if release is None:
            return True
        try:
            release()
            return True
        except Exception:  # noqa: BLE001
            return False
```
判定：**B · 静默失败**
会在什么情况下吞掉什么：
关停路径上三选一（`_close_bus` / `_release_keys` / `_close_capturer`）
**全部 `return False` 且全部没有一行日志**。返回值只进事件流的
`context["shutdown"]` 字典（`loop.py:320`），要看到它必须去翻那条
`CORE_STOPPED` 事件的 context 字段。
症状：引擎正常停下、控制台显示「主循环已停止」，
但 W / 空格这类键**物理上仍处于按下状态**，角色在游戏里一直往前走。
排障者看到：日志干净、退出码正常，只有游戏里角色行为异常 —— 极难联想到 shutdown 失败。
缓解事实：`simulator.py:688` 的 `release_all` 内部**自己**有 `logger.exception`，
所以最常见的失败其实会被记下；这里的 `False` 只覆盖「`release_all` 之外」的异常。
即便如此，「唯一的兜底静默失败」这个位置本身值得收紧。

---

## 5. 事件总线 / 截图器关闭失败同样零日志  [B 类]
位置：`autopilot/core/runtime.py:207, 228`
```python
    def _close_bus(self) -> bool:
        try:
            self.bus.close()
            return True
        except Exception:  # noqa: BLE001
            return False

    def _close_capturer(self) -> bool:
        close = getattr(self.capturer, "close", None)
        if close is None:
            return True
        try:
            close()
            return True
        except Exception:  # noqa: BLE001
            return False
```
判定：**B · 静默失败**
会在什么情况下吞掉什么：
与第 4 条同源。截图器 `close()` 失败意味着 DXGI / GDI / WGC 后台线程**可能没停**，
而 `events.py` 的分发线程是 daemon 且独立于主循环 ——
若总线没关干净，进程退出前仍可能有事件在投递。
症状：进程退出时报「Address already in use」/ 残留线程句柄，
或者下一次启动时截图通道初始化莫名失败。
排障者看到：与第 4 条一样，日志里什么都没有。
（`_close_bus` / `_close_capturer` 与 `_release_keys` 是同一段代码的三个复制，
修的时候建议一起加 `logger.exception`，不必分三次改。）

---

## 6. 读引擎 stderr 失败被表述成「stderr 里没有关键行」，把排障者导向一个打不开的文件  [B 类]
位置：`NEKO-Console/neko_console/channels/engine.py:378`（调用点在同文件 `:414-420`）
```python
        try:
            size = path.stat().st_size
            with open(path, "rb") as f:
                if size > max_bytes:
                    f.seek(size - max_bytes)
                raw = f.read()
        except OSError as exc:
            logger.debug("读引擎 stderr 失败（忽略）：%s", exc)
            return ""
```
```python
            reason = self._read_stderr_reason()
            if reason:
                self.log_line.emit(f"引擎退出（code={code}）：{reason}")
            elif code not in (0, None):
                self.log_line.emit(
                    f"引擎退出（code={code}）：stderr 里没有关键行，"
                    f"完整输出见 {self.stderr_log_path()}")
```
判定：**B · 静默失败**
会在什么情况下吞掉什么：
「读不到文件」和「文件里没有关键行」被压成同一个返回值 `""`。
第二个分支于是对用户宣称「stderr 里没有关键行，完整输出见 <path>」——
但真相可能是这个 path 根本打不开（权限/被占用/路径错）。
症状：引擎崩溃后，用户按提示去看那个文件，什么也看不到，
于是得出「引擎没报过错」的结论，转而去查完全错误的方向。
排障者看到：`logger.debug` —— 默认 INFO 级别下不输出，
所以用户侧**只有那句误导性的提示**，没有任何「我读不到」的字样。
本条特别值得修：它属于「坏消息被包装成好消息」，比单纯的丢失更费时间。

---

## 7. 窗口枚举中途异常 → 返回空列表 → 误报「游戏没启动」  [B 类]
位置：`autopilot/perception/capturer.py:1356`
```python
        win32gui.EnumWindows(_cb, None)
    except Exception:  # noqa: BLE001
        pass
    return out
```
判定：**B · 静默失败**
会在什么情况下吞掉什么：
`EnumWindows` 抛异常时，`out` 只包含**枚举到一半**的结果（通常就是空的），
函数照常返回。上层 `_find_window` 拿到 `(None, None)`，
最终抛 `WindowNotFoundError("请确认游戏已启动且未最小化")`。
症状：游戏明明开着，却报「找不到窗口」；用户按提示去重启游戏，
问题依旧（因为根因是枚举调用本身挂了，不是窗口不存在）。
排障者看到：一条措辞完全正确但**指向错误根因**的报错，且没有任何异常线索。
（对比 `capturer.py:554` 的 `open()`：那里对「等不到窗口」补了 last-rect 信息、
DPI 诊断、黑帧把关，排查链路是齐的 —— 唯独这条枚举异常把线索全丢了。）

---

## 8. 取屏幕尺寸失败 → 回落到硬编码 1920×1080，坐标全错但无日志  [B 类]
位置：`autopilot/actuator/simulator.py:947`（回落值在 `:936`）
```python
def _primary_screen_size() -> tuple[int, int]:
    try:
        return int(_user32().GetSystemMetrics(0)), int(_user32().GetSystemMetrics(1))
    except Exception:  # noqa: BLE001
        return 1920, 1080


def _virtual_screen_size() -> tuple[int, int]:
    ...
    except Exception:  # noqa: BLE001
        pass
    return _primary_screen_size()
```
判定：**B · 静默失败**
会在什么情况下吞掉什么：
非 1920×1080 的机器（2560×1600 是本项目的实测主力分辨率）上，
一旦 `GetSystemMetrics` 异常，所有坐标换算会**静默地按 1920×1080 算**。
症状：点击/移动落点整体偏移，动作像是「手滑了」，但没有任何报错。
排障者看到：什么异常都没有 —— 返回值就是个合法的 `(1920, 1080)` 元组，
调用方没有任何办法区分「实测值」和「兜底常量」。
建议：兜底值至少记一条 warning，或改用 `None` 让调用方显式处理。

---

## 9. 写图（截图存证/素材导出）失败只返回 False，调用方大概率不检查  [C 类 · 需确认]
位置：`autopilot/perception/image_io.py:82, 88`
```python
    try:
        ok, buf = cv2.imencode(p.suffix or ext, img)
    except cv2.error:
        return False
    if not ok:
        return False
    try:
        buf.tofile(str(p))
    except OSError:
        return False
    return True
```
判定：**C · 需人工确认**
会在什么情况下吞掉什么：
函数本身契约清晰（返回 bool、docstring 明说「与 cv2.imwrite 一致」），
`except cv2.error` 那条注释也解释了为什么必须 try。
**但我没能确认所有调用方是否检查了这个返回值**：
若存在 `imwrite_unicode(path, frame)` 裸调用，磁盘满 / 路径含非法字符 /
文件被占用时会**静默丢图**，而排障者要靠「素材目录里少了图」反推。
需要的信息：`imwrite_unicode` 的全部调用点清单，以及各自是否判返回值。
（我没有做全仓调用点统计 —— 那超出了本次「逐条读 handler」的授权范围。）

---

## 10. OCR 读取异常降级为 `matched=UNKNOWN` —— 方向对，但阈值行为需确认  [C 类]
位置：`autopilot/perception/ocr.py:164`
```python
        try:
            text, ok = self._do_read(img)
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"

        elapsed = (time.perf_counter() - t0) * 1000

        if not ok:
            self._fail_streak += 1
            self._last_error = err or "engine returned empty"
            if self._fail_streak >= self._fail_threshold:
                self._available = False
            return OcrResult(text="", matched=TriState.UNKNOWN, ...)
```
判定：**C · 需人工确认**
会在什么情况下吞掉什么：
异常被完整存进 `_last_error`（有 `last_error` 属性可查），
并计入 `fail_streak`，达到阈值后把 `_available` 置 False ——
**这套机制本身是健全的**，我判它偏 A。
之所以列 C，是因为：`_fail_threshold` 之前累计的失败**永远不会自动清零**，
所以一次偶发失败 + 一次真失败就可能触发「OCR 永久不可用」。
需要的信息：`fail_streak` / `_available` 是否有复位路径，以及
下游规则把 `UNKNOWN` 当成「条件为假」还是「条件为真」——
后者才是真正的危险（详见 `local_tree.py:288` `if not out.value`）。

---

## 11. 规则求值异常后的 `continue` —— 上一轮分类器误报，实为 handled  [A 类]
位置：`autopilot/policy/local_tree.py:269`
```python
            except RuleEvalError as exc:
                t.verdict = "ERRORED"
                t.reason = str(exc)
                trace.entries.append(t)
                self._last_error = str(exc)
                self._emit(Event(severity=Severity.ERROR, ...,
                                 code="RULE_EVAL_ERROR", ...))
                logger.error("规则 %s 求值失败：%s", rule.name, exc)
                continue
```
判定：**A · 合理防御**
安全在哪：`continue` **不是静默**。它同时做了四件事：写 trace（`verdict="ERRORED"`）、
记 `_last_error`、发一条 `Severity.ERROR` 事件、**并且有 `logger.error`**。
坏规则被隔离在单条规则内，不会毁掉整棵决策树 ——
这正是 `:261` 注释「异常转事件后继续，绝不静默也绝不让一条坏规则毁整棵树」的设计。
分类器把它列进候选纯粹是因为 `continue` 的机械规则。

---

## 12. 通道探测 `return False` 的那些 —— 上层有 `errors` 兜底  [A 类]
位置：`autopilot/perception/capturer.py:653, 668`（同类还有 `:704, 711`）
```python
        if ch is CaptureChannel.DXCAM:
            try:
                import dxcam  # noqa: PLC0415 - 懒加载，未装不影响其他通道
            except Exception:
                return False
```
判定：**A · 合理防御**
安全在哪：`return False` 在这里**不等于静默**。`open()` 的 `:568-584`
对每条通道都记 `errors.append(...)`，全部失败时会把每条通道的失败原因
一起抛给用户；`:576` 的注释还专门记录了「以前这里不记，导致报错写着
『各通道错误：（无）』」这个真实事故。
而且 `:585-604` 还有黑帧把关（10 秒内必须交出亮画面）。
唯一的小遗憾：`:653`/`:668` 这两处**确实没写日志**（`:678` 的 mss 那条有 `logger.debug`），
但因为上层一定会把该通道记进 `errors`，信息没有真正丢失。

---

## 13. 原子写的临时文件清理失败 —— 教科书式正确  [A 类]
位置：`autopilot/ui/protocol.py:101`
```python
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text); fh.flush(); os.fsync(fh.fileno())
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
```
判定：**A · 合理防御**
安全在哪：这是本次审计里**范式最标准**的一处。清理临时文件是 best-effort，
失败不该掩盖主错误；关键是末尾的 **`raise` 把原始异常原样抛出**。
所以状态文件写入永远不会「看起来成功了」——
`os.replace` 前的任何失败都会一路上抛。
（`NEKO-Console/neko_console/channels/rules.py:138` 的 `_write_atomic` 是同一写法，同样 A。）

---

## 14. 事件订阅者异常隔离 —— 有计数有日志  [A 类]
位置：`autopilot/core/events.py:179`
```python
        for name, fn in targets:
            try:
                fn(ev)
                self.delivered += 1
            except Exception as exc:  # noqa: BLE001 - 一个订阅者挂了不影响别的
                self.subscriber_errors += 1
                self.last_error = f"{name}: {type(exc).__name__}: {exc}"
                logger.error("事件订阅者 %s 异常：%s", name, exc)
```
判定：**A · 合理防御**
安全在哪：隔离是对的（一个订阅者挂掉不该拖垮事件总线），
但**没有牺牲可观测性**：计数器 + `last_error` 字段 + `logger.error` 三重留痕。
事件流里也能查到。这与第 1 条（同样隔离、但什么都不留）形成鲜明对照 ——
说明项目里已经有更好的写法，只是没统一。

---

## 15. watcher 线程的 `except` 被误判成 `pass`  [A 类]
位置：`autopilot/core/loop.py:785`
```python
        try:
            if self._panic_check():  # type: ignore[misc]
                self._panic_event.set()
                self._on_panic()
                return
        except Exception:  # noqa: BLE001
            logger.exception("急停检查异常")
        time.sleep(PANIC_POLL_S)
```
判定：**A · 合理防御**
安全在哪：`logger.exception` 记全栈，ERROR 级，默认可见。
这条同时也是**分类器 bug 的活证据**（它被标成「整个 except 体只有 pass」）。
把第 1 条和第 15 条放在一起看，结论很清楚：
**同一份急停逻辑，监视线程记得住，tick 线程记不住** —— 该补的是第 1 条，不是第 15 条。

---

## 16. GDI / dxcam 资源释放失败 —— best-effort 且状态已清  [A 类]
位置：`autopilot/perception/capturer.py:1556-1582`
```python
        try:
            if self._old and self._mem_dc:
                g.SelectObject(self._mem_dc, self._old)
            self._old = None
        except Exception:
            self._old = None
        ...
        try:
            if self._bmp:
                g.DeleteObject(self._bmp)
            self._bmp = None
        except Exception:
            self._bmp = None
```
判定：**A · 合理防御**
安全在哪：释放 GDI 对象失败（如句柄已失效）通常意味着**它已经没了**，
再记日志也只是噪音；关键设计是每个分支都**无条件把句柄置 None**
（`__init__` 之外的 `except` 里也置），保证不会二次释放、不会死循环。
这是「清理路径的异常不该污染主流程」的正当用法。
（同文件 `:400/406/412` 的 `Capturer.close()`、`:1029` 的 `_restart_dxcam`
用的是同一模式，同样 A —— 后者有一点点资源泄漏风险，见下条备注。）

---

## 17. `self._dxcam_out = None` 被 try 包住 —— 死代码，但暴露了一个重构信号  [A 类（有备注）]
位置：`autopilot/perception/capturer.py:786`
```python
        if changed and self._active is CaptureChannel.DXCAM:
            # dxcam 绑定了区域，窗口移动/缩放后必须重建
            try:
                self._dxcam_out = None  # 交给下次 grab 重建
            except Exception:
                pass
```
判定：**A · 合理防御**（但这个 `try` 是多余的）
安全在哪：给属性赋 `None` **不可能抛异常**，所以这个 `except` 不可达，
不构成静默失败。**备注**：它唯一的实际价值是提醒「窗口变了要重建 dxcam」这件事本身
是对的；但把一行赋值包在 try 里，会让后来者以为这里可能出错。
建议直接去掉 try（属于清理，不是修 bug）。

---

## 18. 规则文件读不出来 → 行号定位静默变 0  [C 类]
位置：`autopilot/policy/rule_loader.py:316`
```python
    text_cache: dict[str, int] = {}
    try:
        file_text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        file_text = ""

    def loc(key: str) -> int:
        if key not in text_cache:
            text_cache[key] = _line_of(file_text, key) if file_text else 0
        return text_cache[key]
```
判定：**C · 需人工确认**
会在什么情况下吞掉什么：文件删了 / 被占用 / 编码不是 UTF-8 时，
`file_text=""`，于是**所有规则的 `line_no` 都变成 0**。
规则本身仍会被正常解析和加载 —— 所以「规则生效了，但错误信息里
所有行号都是 0」，排障者拿不到定位信息。
需要的信息：读失败是否应该直接抛（该文件紧接着的 `_check_top_level` 走的是
「收集 problems 然后整体抛」的严格路线，这里单独放宽是否有意）；
以及 `RuleLoadError` 的 message 是否依赖 `line_no` 才能定位。
危害低于前 17 条，因为**不影响规则是否被执行**，只影响报错质量。

---

## 19. `enable_failure` 探测不到 BetterGI 数据 —— 逐个候选降级  [A 类]
位置：`autopilot/perception/loaders/teleport_points.py:127, 148`
```python
    for root in BETTERGI_CANDIDATES:
        cand = root / BETTERGI_RELPATH
        try:
            if cand.is_file():
                logger.info("探测到 BetterGI 传送点数据：%s", cand)
                return cand
        except OSError as exc:
            logger.debug("探测 %s 失败（忽略）：%s", cand, exc)
    logger.info("未探测到 BetterGI 安装（已查 %d 个候选位置）", ...)
    return None
```
判定：**A · 合理防御**
安全在哪：返回值 `None` 在本模块是**一等的合法状态**（docstring 明写
「找不到返回 None」），调用方据此走「没有编队数据」的正常路径，
不是「以为找到了」。而且 `:129` 有 INFO 级汇总日志，
`:153` 的 `_not_found_detail` 还会逐级列出查过什么。
附带说明：这两个 `except OSError` 在当前 Python 版本里其实**不可达**
（`Path.is_file()` 自 3.8 起内部已吞 `OSError` 并返回 `False`），
属于无害的历史遗留。

---

## 20. 首页「引擎是否存活」读取失败 → 当作没在跑  [A 类]
位置：`NEKO-Console/neko_console/tabs/home.py:124`
```python
    def _engine_alive(self) -> bool:
        try:
            return bool(self.mgr.is_running())
        except Exception:  # noqa: BLE001 - 读不到就当没跑
            return False
```
判定：**A · 合理防御**
安全在哪：失败方向是**保守的** —— 显示「未运行」而不是谎报「运行中」。
对照本审计的判定标准（「会不会让调用方以为**成功**了」），
谎报失败不会诱发误操作，最坏情况只是 UI 卡片显示 `—`，
且下次刷新（5Hz）就会自愈。属于可接受的防御。

---

# 判定为 A 的那些（简表）

以下是我读过上下文后判为**合理防御**的候选。列出来是为了说明它们**被看过**，不是漏了。
理由栏里凡写「上层有兜底」的，都指明了兜底在哪个文件哪一行。

| 位置 | 一句话理由 |
|---|---|
| `autopilot/core/events.py:168` | `popleft()` 的 `IndexError` 是「队列空了」的正常控制流，`break` 正确。 |
| `autopilot/core/loop.py:817` | 急停时状态转移被并发抢先，`logger.debug` + 注释说明「已停机不是故障」，正确。 |
| `autopilot/perception/capturer.py:1198` | WGC 会话启动失败 → 先 `close()` 再 `raise`，错误照样上抛。 |
| `autopilot/perception/capturer.py:139` | `GetSystemMetrics` 失败时 `after=before`，配合 `_DPI_AWARE` 实测判据最终仍返回 `_DPI_AWARE`，且有 warning。 |
| `autopilot/perception/capturer.py:525` | 等亮画面时抓帧失败 → `logger.debug` + `img=None`，外层有 10 秒超时和 ERROR 兜底，不会永远等。 |
| `autopilot/perception/capturer.py:581` | 通道探测异常记入 `errors` 并继续试下一条（见第 12 条）。 |
| `autopilot/perception/capturer.py:887 / 903` | 抓帧异常 → 先尝试 `_demote_channel()` 再 `continue`，有完整 INFO 日志，且注释记录了 2026-10-10 真机事故的教训。 |
| `autopilot/perception/capturer.py:1029` | `_restart_dxcam` 的 `stop/release/close` 失败忽略 —— 与第 16 条同模式；**唯一备注**：可能泄漏 DXGI 对象。 |
| `autopilot/perception/capturer.py:1615/1617` | 注册表探测失败写进 `state["error"]` 返回，调用方拿到完整 dict。 |
| `autopilot/perception/ocr.py:121` | `close()` 里 legacy 引擎关闭失败忽略；`read()` 主路径（第 10 条）另有 fail_streak 机制兜底。 |
| `autopilot/perception/debug_overlay.py:70` | 纯调试绘制路径，失败不影响感知判定。 |
| `autopilot/policy/rule_loader.py:205` | `p.resolve()` 失败退回 `str(p)`，只影响「重复文件检测」的 key，不影响规则加载。 |
| `autopilot/config/loader.py:372/377/387` | 全部收进 `problems` 列表，函数 docstring 明写「任何一项不通过都抛，不降级」。 |
| `autopilot/actuator/keymap.py:160` | 返回 `{}` 是 docstring 声明的契约，且注释说明 `load_keymap` 已报过错，不重复刷屏。 |
| `autopilot/actuator/simulator.py:821/843` | VK↔扫描码查询失败回落到内置 fallback 表（**备注**：表里没有的键会得到 `scan=0`，建议补一条 warning）。 |
| `autopilot/actuator/simulator.py:1129` | `--diagnose` 诊断路径，失败写进 `rep.problems` 由用户显式查看。 |
| `autopilot/ui/protocol.py:143` | `stat()` 失败 → `return False`（不轮转），后续 `append_jsonl` 会正常写，只是文件偏大。 |
| `autopilot/ui/writer.py:157` | 归档清理单个文件失败 → `logger.debug` 后继续；外层 `:170` 还有 `logger.warning` 兜底。 |
| `autopilot/ui/schema.py:212/252` | `Enum(...)` 的 `ValueError` → `False`，是「这个 code 不在已知集合内」的正常判断（开放枚举设计）。 |
| `NEKO-Console/neko_console/channels/engine.py:116` | stderr 日志轮转失败 → `logger.warning`，注释明写「轮转失败绝不能挡住引擎启动」。 |
| `NEKO-Console/neko_console/channels/engine.py:237` | `finally` 里关父进程句柄失败忽略 —— Popen 已复制句柄给子进程，关不掉无实际后果。 |
| `NEKO-Console/neko_console/channels/engine.py:315` | 发命令失败 → `logger.error` + `return False`，状态明确。 |
| `NEKO-Console/neko_console/channels/engine.py:326/331/333/351` | 强杀失败有 `logger.exception`；`_reap` 的关流失败有 `logger.debug`。 |
| `NEKO-Console/neko_console/channels/events.py:59` | `run_current.txt` 不存在 → `_forget_run()` + `return ""`，注释详细说明了这正是「显示昨晚旧数据」那个 bug 的修复点。 |
| `NEKO-Console/neko_console/channels/events.py:126` | 事件流文件还没建 → `return []`，注释标为「文件还没建」的正常态。 |
| `NEKO-Console/neko_console/channels/pathfind.py:185/192/201` | 纯 UI 用的「最近事件」读取，docstring 明说是上一次 run 的残留，失败返回 `[]` 可接受。 |
| `NEKO-Console/neko_console/channels/rules.py:135` | 与 `protocol.py:101` 同款原子写 + `raise`（见第 13 条）。 |
| `NEKO-Console/neko_console/channels/rules.py:264` | 规则目录探测，失败 → 不认这个目录，`scan()` 会如实报告扫到几个文件。 |

---

## 一句话小结

- **真正需要动手的是 8 条**（第 1~8 条），全部集中在**安全护栏 fail-open** 和
  **关停/崩溃路径无日志**两类，没有一条属于「感知算错了但看不出来」——
  感知链路（`capturer` / `builder` / `ocr`）反而是整个仓库防得最扎实的地方，
  大量注释记录了 2026-10-10 那次真机黑帧事故的教训。
- 修复优先级建议：**第 1 → 第 2/3 → 第 6 → 第 4/5 → 第 7 → 第 8**。
  第 4/5/6 是同一个模式（返回值吞掉错误但没留痕），可以一起改。
- **分类器本身建议先修**：`_audit_scan.classify_handler` 过滤 `ast.Expr`
  导致 113 处误判，其中 37 处是「记了 `logger.exception` 的好代码」。
  在这个 bug 修掉之前，任何基于这份明细的统计都会高估风险。