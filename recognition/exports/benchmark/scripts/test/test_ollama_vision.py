"""Ollama 本地视觉探测脚本的单测（2026-10-06）。

★ 本机能验什么、不能验什么
------------------------
* **能验**：服务检测的三条分支、模型前缀匹配、图片编码格式、
  请求体形状、错误翻译、三张图能正常造出来。
  其中「服务在跑但模型没拉」这条**在本机 ollama 上真验过**
  （exit=2 + 提示 ollama pull）。
* **不能验**：模型到底答不答得对 —— ``qwen2.5vl:7b`` 还没 pull。
  那是真机的事，不是单测能替代的。

★ 为什么要盯「请求体形状」
------------------------
Ollama 的图片字段和 OpenAI 格式**完全不同**：前者是
``messages[].images: [裸 base64]``，后者是
``content[].image_url.url = "data:...;base64,..."``。
写错的症状是「模型看不见图但不会报错」—— 静默失败，最难发现。
"""

from __future__ import annotations

# ── 发布包路径解析 ─────────────────────────────────────────────────
# ★ 这段由发布构建注入，替代原先散落各处的本机绝对路径。
#   优先级：环境变量 > 从本文件向上找仓库根 > 相对当前目录（响亮失败）。
#   刻意用 ``_pl`` / ``_os`` 私有别名：这些脚本五花八门，
#   用 ``Path`` / ``os`` 直接定义会和它们各自的 import 打架。
import os as _os
import pathlib as _pl


def _neko_repo_root() -> "_pl.Path":
    for _p in _pl.Path(__file__).resolve().parents:
        if (_p / "recognition" / "configs" / "rois.toml").is_file():
            return _p
    return _pl.Path(__file__).resolve().parents[0]


_REPO_ROOT = _neko_repo_root()
RECOG_ROOT = _pl.Path(_os.environ.get("NEKO_RECOGNITION_ROOT", "").strip()
                      or (_REPO_ROOT / "recognition"))
PLUGIN_DIR = _pl.Path(_os.environ.get("NEKO_PLUGIN_DIR", "").strip()
                      or (_REPO_ROOT / "plugin" / "plugins" / "genshin_game_buddy"))
CONSOLE_DIR = _pl.Path(_os.environ.get("NEKO_CONSOLE_DIR", "").strip()
                       or (_REPO_ROOT / "console"))
import os

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:                                   # pragma: no cover
    pass

MISC = Path(RECOG_ROOT / 'exports' / 'benchmark' / 'scripts' / 'misc')
sys.path.insert(0, str(MISC))


def load(alias: str, name: str):
    spec = importlib.util.spec_from_file_location(alias, MISC / name)
    m = importlib.util.module_from_spec(spec)
    sys.modules[alias] = m
    spec.loader.exec_module(m)
    return m


oc = load("_oc_t", "ollama_client.py")
t = load("_ovt_t", "ollama_vision_test.py")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f"   {detail}" if detail else ""))


# ======================================================================
print("=== 1. 端点与常量（规格硬性要求）===")
# ======================================================================
check("端点是 http://localhost:11434",
      oc.BASE == "http://localhost:11434", oc.BASE)
check("chat 用 /api/chat", oc.CHAT_URL.endswith("/api/chat"))
check("探活用 /api/tags", oc.TAGS_URL.endswith("/api/tags"))
check("★默认模型是 qwen2.5vl:7b（**不是** qwen2.5-vl）",
      oc.DEFAULT_MODEL == "qwen2.5vl:7b", oc.DEFAULT_MODEL)
check("★超时 180 秒（本地模型慢）", oc.CHAT_TIMEOUT_S == 180.0,
      str(oc.CHAT_TIMEOUT_S))
check("探活超时独立且短（5s）—— 没起服务时不该干等",
      oc.TAGS_TIMEOUT_S == 5.0 and oc.TAGS_TIMEOUT_S < oc.CHAT_TIMEOUT_S)

# ======================================================================
print("\n=== 2. model_present 前缀匹配 ===")
# ======================================================================
for names, want, exp in ((["qwen2.5vl:7b"], "qwen2.5vl:7b", True),
                         (["qwen2.5vl:latest"], "qwen2.5vl:7b", True),
                         (["qwen2.5vl"], "qwen2.5vl:7b", True),
                         (["qwen2.5vl:3b"], "qwen2.5vl:7b", True),
                         (["qwen2.5:7b"], "qwen2.5vl:7b", False),
                         ([], "qwen2.5vl:7b", False)):
    got = oc.model_present(names, want)
    check(f"{names} 里找 {want} -> {exp}", got is exp, f"实际 {got}")

# ======================================================================
print("\n=== 3. 图片编码：裸 base64，无 data: 前缀 ===")
# ======================================================================
with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "x.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nFAKE")
    b = oc.encode_image(p)
    check("★不含 data: 前缀（Ollama 的规矩，OpenAI 才带）",
          not b.startswith("data:"), b[:14])
    r, why, dt = oc.chat_vision(str(Path(td) / "nope.png"), "q")
    check("图片读不到 -> (None, 原因) 而不是抛异常",
          r is None and "读不到" in why, why[:50])

# ======================================================================
print("\n=== 4. 服务不可用分支（本机真 ollama 在跑，故指向死端口验）===")
# ======================================================================
_b, _u = oc.BASE, oc.TAGS_URL
try:
    oc.BASE, oc.TAGS_URL = "http://localhost:59999", \
        "http://localhost:59999/api/tags"
    ok, why, names = oc.service_status(2.0)
finally:
    oc.BASE, oc.TAGS_URL = _b, _u
check("★服务不可用 -> ok=False 且给出**原因**",
      ok is False and bool(why), why[:52])
check("★服务不可用时**不返回空列表冒充成功**",
      names == [], f"实际 {names}")

# ======================================================================
print("\n=== 5. 请求体形状（Ollama != OpenAI）===")
# ======================================================================
# ★ 必须用 AST 查**真实的字典字面量**，不能用子串搜源码 ——
#   模块 docstring 里正写着 "image_url" 做**反例说明**，子串搜索会
#   撞上自己的注释然后判失败（这坑本轮已经踩过一次）。
import ast                                              # noqa: E402

_cl = ast.parse((MISC / "ollama_client.py").read_text(encoding="utf-8"))
_dict_keys = set()
for _n in ast.walk(_cl):
    if isinstance(_n, ast.Dict):
        for _k in _n.keys:
            if isinstance(_k, ast.Constant) and isinstance(_k.value, str):
                _dict_keys.add(_k.value)
check('★请求体含 images，且**不含** image_url（Ollama != OpenAI 格式）',
      "images" in _dict_keys and "image_url" not in _dict_keys,
      str(sorted(_dict_keys)))
check('★带 stream=False（默认 true 是 JSON 流，loads 不了）',
      "stream" in _dict_keys)
check('★带 temperature 且 chat_vision 默认 0.0（能力探测要可复现）',
      "temperature" in _dict_keys
      and "temperature: float = 0.0" in
      (MISC / "ollama_client.py").read_text(encoding="utf-8"))
_src = (MISC / "ollama_client.py").read_text(encoding="utf-8")
check("失败一律返回 (None, 原因) 而不是抛",
      "except urllib.error.HTTPError" in _src
      and _src.count("return None,") >= 4)
check("HTTP 错误把响应体也带出来（400 的原因都在 body 里）",
      "detail = exc.read()" in _src)

# ======================================================================
print("\n=== 6. 三张测试图能造出来 ===")
# ======================================================================
try:
    p1 = t.build_l1()
    check("L1 合成图造出（复用 diag_llm_vision 的图）", p1.is_file(),
          f"{p1.name} {p1.stat().st_size // 1024}KB")
    from PIL import Image as _I
    check("L1 是 512x512", _I.open(p1).size == (512, 512))
    p2 = t.build_l2()
    check("L2 蒙德城裁出", p2.is_file(),
          f"{p2.name} {p2.stat().st_size // 1024}KB")
    check("★L2 是 256x256（规格要求）", _I.open(p2).size == (256, 256))
    p3 = t.build_l3()
    check("L3 复杂地形裁出（与云端失败同区域）", p3.is_file(),
          f"{p3.name} {p3.stat().st_size // 1024}KB")
except Exception as exc:                                  # noqa: BLE001
    check("造图", False, f"{type(exc).__name__}: {exc}")

check("★L2 坐标取自 tp 数据（areas 含「蒙德城」），不是瞎编的",
      abs(t.L2_CENTER[0] - 2277.6) < 1 and abs(t.L2_CENTER[1] + 876.1) < 1,
      str(t.L2_CENTER))
check("★L3 用的是云端失败那次同一片区域",
      t.L3_START == (1000.0, 1000.0) and t.L3_TARGET == (3000.0, 3000.0))

# ======================================================================
print("\n=== 7. CLI 契约 ===")
# ======================================================================
_ovsrc = (MISC / "ollama_vision_test.py").read_text(encoding="utf-8")
_vt = ast.parse(_ovsrc)
_args = set()
for _n in ast.walk(_vt):
    if (isinstance(_n, ast.Call) and isinstance(_n.func, ast.Attribute)
            and _n.func.attr == "add_argument"
            and _n.args and isinstance(_n.args[0], ast.Constant)):
        _args.add(str(_n.args[0].value))
check("★--model 逐层透传到 chat_vision（曾只用于探活 → 真 bug）",
      "model=model," in _ovsrc and "timeout=timeout)" in _ovsrc)
check("★--layers 可选层", "--layers" in _args, str(sorted(_args)))
check("★--timeout 可覆盖", "--timeout" in _args)
check("★服务没跑 -> exit 2 + 提示 ollama serve",
      "return 2" in _ovsrc and "ollama serve" in _ovsrc)
check("★模型没拉 -> exit 2 + 提示 ollama pull", "ollama pull" in _ovsrc)
# ★ 用 AST 看**实际注册的参数**，而不是搜源码 —— 模块 docstring 里
#   写着「也**没有 --real**」，子串搜索会撞上自己的说明。
check("★确实没有注册 --real（本地免费直接发）",
      "--real" not in _args, str(sorted(_args)))
check("★L2/L3 明说不自动判分（不假装能判对错）",
      "请人工看上面那段原文" in _ovsrc or "请人工看原文" in _ovsrc)
check("★L1 关键词判定：红左上/绿右下/加号",
      all(k in t.L1_ANSWER.values() for k in ("左上", "右下", "加号")))

# 判定函数的行为
check("judge_l1 全对 -> 看起来对",
      "看起来对" in t.judge_l1("红方块在左上角，绿方块在右下角，中间是一个加号"))
check("judge_l1 全错 -> 看起来错",
      "看起来错" in t.judge_l1("都在中间"))
check("judge_l1 部分 -> 部分对",
      "部分对" in t.judge_l1("红方块在左上角，其余我不知道"))
check("judge_open 空 -> 看不出",
      "看不出" in t.judge_open(""))
check("judge_open 过短 -> 看不出（敷衍）",
      "看不出" in t.judge_open("不知道"))

print()
print("=" * 74)
print(f"结果: {len(PASS)}/{len(PASS) + len(FAIL)} 通过")
if FAIL:
    print("  失败: " + ", ".join(FAIL))
print("=" * 74)
sys.exit(1 if FAIL else 0)
