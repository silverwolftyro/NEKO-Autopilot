# 黑屏来源调查报告（2026-10-16）

> 现象：屏幕录制或 OCR 显示黑屏，但实际上有画面。
> 本轮**只查不改**（除新增诊断脚本外）。结论分两部分：
> **插件自己的黑屏** vs **外部录屏工具的黑屏**。

---

## 0. 一句话结论

**两套独立的抓屏代码，OCR 走的不是引擎那套。**

引擎 `autopilot/perception/capturer.py` 在 2026-10-10 已经把 **WGC 提到链首**并
在注释里写明「BitBlt / DXcam / mss 在硬件加速+全屏下**全部**只能拿到黑帧」。
但 OCR 走的 `screen_capture.py` 是**另一份代码**，链子是
`PrintWindow → mss → bridge`，**里面一个 WGC 字都没有**（grep 确认 0 命中）。

所以「引擎看得到画面、OCR 说是黑屏」是**可以同时成立**的，
不需要任何外部录屏软件参与。**黑屏的主因在插件内部。**

项目里**没有任何 OBS / 录屏软件的配置或使用记录**（见 §4）。

---

## 1 · 通道策略对比（核心）

| | OCR 路径 | 引擎路径 |
|---|---|---|
| 入口 | `screen_capture.capture_active_frame()` | `autopilot/perception/capturer.Capturer` |
| 调用方 | `__init__.py:2955` `ocr_perceive` | `perception/builder.py` → `loop.py` |
| 降级链 | `PrintWindow → mss → bridge` | **`WGC → BitBlt → DXcam → mss`** |
| 有 WGC 吗 | **没有**（0 命中） | 有，且排链首 |
| 黑帧守卫 | `is_black_frame()`：`luma < 12` 全局均值 | 空帧检测 + `_demote_channel()` 自动降级 |
| 自动降级 | 无（降级链一次性跑完） | 有，连续失败自动降到下一路 |

两套**没有共用任何一行代码**。引擎侧 2026-10-10 的修复对 OCR 侧完全无效。

### 1.1 OCR 路径的降级链（`screen_capture.py:492-512`）

```
routes = (
    ("printwindow", _capture_printwindow),   # ← 第一路，硬件加速窗口的老问题重灾区
    ("mss",         _capture_mss),           # ← 屏幕坐标，游戏被遮挡时抓到遮挡者
    ("bridge",      _capture_bridge),        # ← HTTP 抓宿主自己的屏
)
for name, fn in routes:
    res = fn()
    if res.ok and res.image_base64 and not is_black_frame(res.image_base64):
        return res
```

三路都黑 → 返回 `ok=False, image_base64=None`（**没有图，不是黑图**）。

---

## 2 · 实测证据：残帧（partial frame）会**绕过**黑帧守卫

### 2.1 量了 `_runtime` 里已有的全部截图

阈值参考 `screen_capture.BLACK_FRAME_LUMA_THRESHOLD = 12.0`。

| 文件 | 尺寸 | luma(32²均) | max | **dead%(<12)** |
|---|---|---|---|---|
| capdiag_wgc.png | 2560x1600 | 150.04 | 255 | 0.1% |
| capdiag_dxcam.png | 2560x1600 | 149.96 | 255 | 0.1% |
| capdiag_mss.png | 2560x1600 | 149.96 | 255 | 0.1% |
| capdiag_bitblt.png | 2560x1600 | 149.88 | 255 | 0.1% |
| capmatrix_normal_wgc.png | 1352x948 | 51.72 | 255 | 0.0% |
| capmatrix_normal_mss.png | 1350x900 | 143.85 | 255 | 2.8% |
| capmatrix_normal_dxcam.png | 888x553 | 236.37 | 255 | 0.2% |
| **capmatrix_normal_bitblt.png** | 1350x900 | **21.26** | 255 | **54.6%** |
| **capmatrix_minimize_bitblt.png** | 1350x900 | **18.66** | 255 | **55.6%** |
| **capmatrix_offscreen_bitblt.png** | 876x541 | **20.19** | 255 | **53.9%** |

**三张 BitBlt 帧的 dead% 都在 54% 上下，而完整帧是 0.0%~2.8%。**

### 2.2 肉眼确认

缩略图（见 §6 交付物）：

- `capmatrix_normal_bitblt.png` —— **左上角约 45% 有内容，右下整块纯黑 padding**。
  测试窗口是 1352x948，BitBlt 只 blit 出一个 1350x900 且内容只覆盖左上，
  尺寸对不上导致下半/右半是未绘制的黑区。
- `capmatrix_normal_wgc.png` —— 完整窗口，含标题栏，内容齐全。

### 2.3 为什么这正好等于用户的症状

残帧的**全图 luma 是 18~25**，因为亮的那 45% 把均值拉上去了。
而 `is_black_frame()` 的判据是 `luma < 12`：

```python
def is_black_frame(image_base64, ocr_len=None) -> bool:
    if ocr_len is not None:
        return int(ocr_len) == 0
    return _avg_luma_from_b64(image_base64) < BLACK_FRAME_LUMA_THRESHOLD   # 12.0
```

**21.26 > 12 → 判「不是黑帧」→ 原样送进 OCR。**
OCR 拿到的是一张左上有字、右下全黑的图，识出的东西自然是碎的。

这就是「**实际上有画面、但 OCR 说是黑屏**」的机制。

**守卫的洞：它只看全局均值，不看「有多少比例是黑的」。**
一个 54% 纯黑的残帧和一个 45% 内容的正常帧，全局均值可以一样。

---

## 3 · 第二个洞：foreground 兜底路径**完全没有黑帧守卫**

`screen_capture.capture_active_frame()`（`:515-561`）：

```python
target = find_genshin_window()
if target:
    return capture_window_base64(target), "local_process"    # ← 有守卫（三路都判黑）

# ... foreground 路径 1：
result = _capture_mss(rect)
if result.ok:
    return result, "foreground"        # ★ 没有任何 is_black_frame 检查
```

`find_genshin_window()` 拿不到窗口时（云游戏、宿主沙箱、
本机 `win32gui` 无 API——**本次探针实测就是这个状态**：`win32gui有API=False`），
走 foreground，**mss 抓到啥就是啥，黑的也返回 ok=True**。

到 `__init__.py:3020` 才有一层：

```python
if len(text) == 0 and is_black_frame(b64):
    return    # black_frame 早退
```

但这里的 `is_black_frame(b64)` 是**不给 `ocr_len` 的那次调用**，又回到 `luma < 12`。
残帧照样漏（text 非空时连这道都不走）。

---

## 4 · 外部录屏工具（OBS 等）：项目内**无任何记录**

全仓 grep `obs|OBS Studio|XSplit|ShadowPlay|nvenc|游戏捕获|录屏`：

- `文档等的报告参考这些/N.E.K.O.3.0-架构设计文档-v1.0.md:124`
  —— 仅作类比：「同性质参照：OBS（录屏）、NVIDIA ShadowPlay」。
- `:2434` —— 仅作定位类比：「定位为『辅助工具』而非『作弊器』——同 OBS」。
- 其余 `obs` 命中全是 `re.split(maxsplit=1)`、`obs` 变量名之类的误命中。

**结论：项目里没有任何关于录屏软件的配置、脚本或使用记录。**
Demo 走的是插件自己的抓屏（`_runtime/*.stream.jsonl`），
不是外部录屏。所以外部工具这条线**目前没有证据支持**，
§5 的 OBS 清单是「排除法后仍需用户自查」的兜底，不是已确认结论。

---

## 5 · 给用户的自查清单（外部工具侧，仅当 §2/§3 排除后仍黑才用）

1. **原神显示模式 → 无边框窗口化**（不是独占全屏）。
   独占全屏绕过 DWM，WGC 抓不到，mss/DXcam 也可能只拿到黑。
2. **OBS 采集方式 → 游戏捕获（Game Capture）**，不要用显示器捕获。
   显示器捕获在 HDR 下很容易出黑帧。
3. **关 HDR**（Windows 显示设置 + 显卡控制面板 + 游戏内三处都要关）。
4. **编码器**：先试 **NVENC**（硬件编码，对 HDR/多平面支持更好），
   黑就换 **x264**（软件编码，兼容性最高，代价是掉帧）。
5. OBS 里 `视频 → 缩放滤镜` 不要开 YUV420P 以外的特殊格式；
   `输出 → 录像` 分辨率与游戏一致最稳。

---

## 6 · 修复方案（**本轮未改**，供决策）

### 方案 A：把黑帧判据从「全局均值」换成「死像素占比」（最小、最对症）

改 `screen_capture.is_black_frame`，增加 `dead_frac` 判据：

```
dead% = 亮度 < 12 的像素占比
判黑条件：  luma < 12  OR  dead% > 0.20
```

依据就是 §2.1 的实测分布：残帧 53.9%~55.6%，完整帧 0.0%~2.8%，
**阈值取 0.20 两侧都有 30 个百分点以上的余量**，不是拍脑袋。

- 改动面：`screen_capture.py` 一个函数 + foreground 路径补一次调用。
- 代价：真正很暗的场景（夜晚/洞里）dead% 会偏高，可能被误判。
  缓解：`ocr_len` 那条严格判据（0 字即黑）本来就优先，交互时不受影响。
- 风险：低。有 `test_screen_capture.py` 400+ 行现有用例兜底。

### 方案 B：给 OCR 路径补 WGC（治本，但改动大）

把 `capturer.py` 的 `_WgcSession` 提到 `screen_capture` 的降级链首位：

```
routes = (wgc, printwindow, mss, bridge)
```

- 收益：引擎侧已验证 WGC 在无边框全屏下能抓对（`capdiag_wgc.png` / `capmatrix_normal_wgc.png` 都是完整帧）。
- 代价：需要 `windows-capture>=2.0.1`；依赖缺失时必须**能继续往下走**
  （`capturer.py:699-702` 已有这个先例：`return False` 让 `open()` 往下走）。
- 风险：中。建议 A 先上、B 后跟——A 是止血，B 是根治。

### 方案 C（可选）：foreground 路径补守卫

`capture_active_frame` 的 `_capture_mss(rect)` 返回前也过一次
`is_black_frame`，黑了就继续往下走 bridge。与 A 配套。

**建议顺序：C（顺手）→ A（止血）→ B（根治）。**

---

## 7 · 新增诊断脚本

`plugin/plugins/genshin_game_buddy/autopilot/tools/ocr_frame_luma.py`

```powershell
# 游戏在前台显示时跑（最小化状态下跑没有意义）
python -m autopilot.tools.ocr_frame_luma --save

# 游戏只能全屏、来不及切：用 --delay 倒计时 8 秒再跑
python -m autopilot.tools.ocr_frame_luma --delay 8 --save

# 额外跑引擎那套通道做对照（慢）
python -m autopilot.tools.ocr_frame_luma --engine
```

三段输出：

1. **OCR 实际路径** `capture_active_frame()` —— `luma` / `dead%` / 四角亮度 / `is_black_frame` 判定。
2. **降级链逐路** —— `PrintWindow` / `mss` / `bridge` 各自单独跑，标出哪一路在交残帧。
3. **引擎对照**（`--engine`）—— `WGC` / `BitBlt` / `DXcam` / `mss` 的同口径数字。

判读：`dead% > 20%` 就是残帧。

### 本次无游戏环境的实测（供对照基线）

```
find_genshin_window -> None（会走 foreground 兜底）
capture_path = foreground
ok=True  method=mss  error=''  size=2560x1600
luma= 212.9  dead= 6.9%  角[150/0/151/154]
is_black_frame() 判定 -> not-black（会送去 OCR）
结论：完整帧
```

顺带印证了 §3：本机 `win32gui有API=False`，
`find_genshin_window()` 直接返回 None，**必然走 foreground 兜底**——
也就是说在这台机器上，OCR 抓的是**整块主显示器**，不是原神窗口。
游戏窗口一旦被别的窗口盖住，OCR 看到的是遮挡者。

---

## 8 · 与本次任务的边界

- 本轮**没有改任何生产代码**。§6 的方案 A/B/C 一行都没落地。
- 唯一新增文件是 §7 的诊断脚本（只读，不进生产路径）。
- `_luma_scan_tmp.py` / `_thumb_tmp.py` / 4 张 `thumb_*.png` 是本次分析的临时产物，
  已移入 `<WORKSPACE_ROOT>\_recycle\tmp_black_screen_probe\`，未删除。
