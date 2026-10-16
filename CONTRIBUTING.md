# 贡献指南

## 先读这个

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) —— 三层怎么分、为什么这么分
- [`docs/ASSETS.md`](docs/ASSETS.md) —— 哪些随包、哪些自备
- [`docs/GPL_BOUNDARY.md`](docs/GPL_BOUNDARY.md) —— 什么不能进包
- [`recognition/notes/`](recognition/notes/) —— **尤其是那 50 条已证伪的路线**。
  如果你正打算做某件事，先翻翻有没有人已经做过、为什么不行。

---

## 环境

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
```

Windows 10/11 + Python 3.11。其它版本未验证。

> `requirements-*.txt` **必须保持纯 ASCII**。pip 用系统 locale 读它们，
> 中文 Windows 上是 GBK，一个非 ASCII 字节就让安装失败。
> 中文说明请写在文档里，不要写进 requirements。

---

## 提交前必须过

```powershell
cd plugin\plugins\genshin_game_buddy
python -m pytest tests -q          # 期望 1601 passed

cd ..\..\..\console
python -m pytest tests -q          # 期望 710 passed
```

**数字对不上就是没跑对。** 当前基线：

| 套件 | 期望 |
|---|---|
| 引擎 | 1601 passed / 30 skipped / 1 xfailed |
| 主控台 | 710 passed / 70 skipped / 4 xfailed / 3 xpassed |

skip 的构成见 [`docs/ASSETS.md` 第五节](docs/ASSETS.md#五测试为什么有一批是-skip-而不是-pass)。
**不要为了让 CI 变绿而把 skip 改成 pass，也不要反过来把资产守卫删掉。**

### 红线测试不是摆设

`plugin/tests/static/test_redlines.py` 做源码静态扫描：

- **R8** 模块顶层只允许纯定义（白名单：`re.compile`、`logging.getLogger`）。
  顶层做 IO 是 import 期副作用，宿主进程会先炸。
- `ultralytics`（AGPL）运行期零 import
- 禁用 token（键盘钩子库等）
- i18n 调用点只允许 `_t` / `tr`

这条真的拦下过东西——给脚本注入路径解析 preamble 时，
preamble 在顶层调函数，当场点红。改动这类地方请先读
[ARCHITECTURE 的"测试"一节](docs/ARCHITECTURE.md#测试)。

---

## 几条硬规矩

### 1. 资产路径只从 `autopilot/perception/paths.py` 出

```python
# ✅
from ..paths import RECOGNITION_ROOT
ROIS = RECOGNITION_ROOT / "configs" / "rois.toml"

# ❌ 任何地方都不许再写死
ROIS = Path(r"D:\...\configs\rois.toml")
```

代价不是"多打几个字"：换机器要全仓搜索替换，**漏一处就静默用错文件**；
拼错一个反斜杠，报错是"文件不存在"而不是"路径写错了"；
没人说得清某个资源到底从哪来。

需要新资产？加到 `paths.py`，加一个环境变量口子。

### 2. 缺资产要么响亮失败，要么优雅降级——**不要静默**

```python
# ✅ 响亮失败
if not model.is_file():
    raise YoloUnavailableError(f"模型不在 {model}，见 docs/ASSETS.md")

# ✅ 优雅降级：只有一个明确的降级出口
# ❌ 只 logger.error 然后让功能静默失效
```

真实的反面教材：`key_z.py` 模板缺失时只打一条 error 日志，
检测器就**永久返回 UNKNOWN**，UI 和规则层看不出异常。

### 3. 别碰这三块（除非你就是要改它们）

- `autopilot/actuator/safety.py` 的护栏逻辑
- 窗口缩放 / DPI 换算
- 地图坐标换算精度

它们各自有几十条测试钉着当前行为。改动请连测试一起改，
并在 PR 里说明**为什么现在是对的**变成了**为什么现在是对的**。

### 4. 不要提交

`.venv/`、`__pycache__/`、`_runtime/`（含**真实游戏画面**）、
`config.toml`、任何凭据、任何 `.onnx`。

`.gitignore` 已经覆盖，但**别依赖它兜底**——跑一次
`git status --ignored` 确认。

---

## 别提交的东西

| 东西 | 为什么 |
|---|---|
| GPL 资产（`bgi_*.onnx`、`tp.json`、BetterGI 源码） | 见 `docs/GPL_BOUNDARY.md` |
| 米游社地图资产 | 版权 |
| 真机截图、状态流 | 含游戏画面；也是隐私 |
| 别人的 API key | 即使是 `sk-should-never-be-saved` 这种占位符，**也别放进非测试文件** |

---

## PR 描述请写清

1. **改了什么**（一句话）
2. **为什么**（问题是什么，不是你想怎么改）
3. **怎么验证的** —— 测试数字，或者真机操作步骤
4. **有没有动到"缺资产时的行为"**（响亮失败 ↔ 降级）

> 这个项目历史上最值钱的产出是 `recognition/notes/` 里
> **被推翻的方案**。如果你试了某条路没走通，
> 请把它写进 notes，哪怕只有一段 —— 那是给下一个人省下的时间。

---

## 行为准则

对事不对人。评审针对代码，不针对写代码的人。
不同意就摆数据；数据不出来就一起做实验，别在评论区吵。

---

## 许可

贡献即表示你同意你的贡献以 **Apache-2.0** 授权。
不要提交你无权授权的内容 —— 尤其是从别处复制的代码或数据。
