"""5 分钟判死 deepseek-flash 的视觉能力（2026-10-06）。

``path_planner.py`` 让模型规划路点，返回的点**基本是起终点等距插值**
—— 像「没看到图」也像「看了不懂」。两者修法完全不同，先用**任何视觉模型
都不该答错的图**分开：红块左上、绿块右下、中央加号。全对 = 图片链路正常
（问题在复杂地形）；全错 = 链路断了。

四条取舍写在代码里（免得以后被"优化"掉）：不 import path_planner（共用
代码的话它俩会一起错）；key 检查放在生成图之后（没 key 也能看图排除「图
画错了」）；**不**拿 reasoning 顶替 content（要查的正是 content 为空，
救回来就抹掉证据）；图刻意极简（答不对只剩一个解释）。

    python diag_llm_vision.py              # 需要 $env:DEEPSEEK_API_KEY
    python diag_llm_vision.py --no-color   # 保守终端
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

# ★ 必须强制 UTF-8（2026-10-06 实跑抓到）
#   中文 Windows 的控制台是 cp936，编不出 ✗ / ✓ / → 这些符号 ——
#   打印时直接 UnicodeEncodeError，脚本在**错误处理路径上**崩掉：
#   也就是说 401 之后你看到的是 traceback，而不是「key 不对」。
#   errors="replace" 保证再遇到编不出的字符也只是变问号，不会抛。
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

ENDPOINT = "https://api.deepseek.com/v1/chat/completions"
KEY_ENV, MODEL_ENV = "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL"
DEFAULT_MODEL, TIMEOUT_S, MAX_TOKENS = "deepseek-flash", 30.0, 500
TMP = Path(os.environ.get("TEMP", "."))
OUT_PNG, OUT_REASON = TMP / "diag_vision_input.png", \
    TMP / "diag_vision_reasoning.txt"
SIDES, SQ, C = 512, 80, 256
QUESTION = """请回答这张图里的三个问题，只输出 JSON：
{
  "red_square_position": "左上/右上/左下/右下/中央",
  "green_square_position": "左上/右上/左下/右下/中央",
  "center_shape": "加号/十字/方块/圆/三角形/什么都没画"
}"""
CHECKS = (("red_square_position", "红方块位置", "左上"),   # 键, 标签, 答案
          ("green_square_position", "绿方块位置", "右下"),
          ("center_shape", "中央形状", "加号"))
NO_COLOR = False


def _c(k):
    return "" if NO_COLOR else {"r": "\033[31m", "g": "\033[32m",
                                "y": "\033[33m", "d": "\033[2m",
                                "x": "\033[0m"}.get(k, "")


def build_image():
    """白底 + 红块(左上) + 绿块(右下) + 黑加号(中央)。

    颜色必须用纯 RGB：ImageDraw 在 RGB 模式下把颜色名当灰度值，白底会
    变黑。``rectangle`` 两端都含，-1 才是准确的 SQ 像素宽。"""
    im = Image.new("RGB", (SIDES, SIDES), (255, 255, 255))
    d = ImageDraw.Draw(im)
    for (x, y), c in (((40, 40), (220, 30, 30)), ((392, 392), (30, 160, 60))):
        d.rectangle([x, y, x + SQ - 1, y + SQ - 1], fill=c)
    d.line([C - 60, C, C + 60, C], fill=(0, 0, 0), width=12)
    d.line([C, C - 60, C, C + 60], fill=(0, 0, 0), width=12)
    return im


def extract_json(t):
    """抠 JSON，**故意宽松**。第二招（截 ``{`` 到 ``}``）能救
    「好的，答案是：{...}」这种开场白。"""
    if not isinstance(t, str) or not t.strip():
        return None
    s = t.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1]
        s = s.rstrip()[:-3] if s.rstrip().endswith("```") else s
    i, j = s.find("{"), s.rfind("}")
    for cand in (s.strip(), s[i:j + 1] if 0 <= i < j else None):
        try:
            d = json.loads(cand) if cand else None
            if isinstance(d, dict):
                return d
        except ValueError:
            pass
    return None


def call_api(b64, model, key):
    """发一次请求。

    :returns: ``(content, reasoning, err)``。``err`` 非空表示**根本没拿到
        响应**（key / 网络 / 结构问题）—— 这和「拿到了但 content 是空的」
        是两回事，混为一谈会把人引到完全错误的方向上去查。
    """
    req = urllib.request.Request(
        ENDPOINT, method="POST",
        data=json.dumps({
            "model": model, "max_tokens": MAX_TOKENS, "temperature": 0.0,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": QUESTION},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/png;base64,{b64}"}}]}],
        }).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            det = e.read().decode("utf-8", "replace")[:400]
        except Exception:                                       # noqa: BLE001
            det = "(读不到)"
        print(f"  {_c('r')}✗ HTTP {e.code} {e.reason}\n  响应体：{det}{_c('x')}")
        if e.code in (401, 402):
            print(f"  → {KEY_ENV} 不对或余额不足。")
        return None, "", f"HTTP {e.code} {e.reason}"
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"  {_c('r')}✗ 网络/超时：{e}{_c('x')}")
        return None, "", f"网络/超时：{e}"
    try:
        doc = json.loads(raw)
        ch, msg = doc["choices"][0], doc["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        print(f"  {_c('r')}✗ 结构/JSON 不符预期：{e}\n"
              f"  原始前 300 字：{raw[:300]}{_c('x')}")
        return None, "", f"响应结构不符预期：{e}"
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or ""
    print(f"  {_c('d')}[DEBUG] 顶层 keys {sorted(doc.keys())}\n"
          f"  [DEBUG] finish_reason {ch.get('finish_reason')!r}"
          f" / message keys {sorted(msg.keys())}\n"
          f"  [DEBUG] content {len(content)} 字 / reasoning {len(reasoning)} 字"
          f"{_c('x')}")
    return content, reasoning, ""


def main():
    global NO_COLOR
    ap = argparse.ArgumentParser(description="诊断 deepseek-flash 视觉能力")
    ap.add_argument("--no-color", action="store_true", help="不输出 ANSI 颜色")
    NO_COLOR = ap.parse_args().no_color

    print("=== 生成测试图 ===")
    im = build_image()
    im.save(OUT_PNG, format="PNG")
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode()
    print(f"  已保存 {OUT_PNG}  ({SIDES}x{SIDES})")
    print(f"  {_c('d')}base64 {len(b64)} 字符 / PNG {len(b64) * 3 // 4} 字节"
          f"{_c('x')}")

    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        print(f"\n{_c('r')}[!] 没设 {KEY_ENV}，无法调用。{_c('x')}",
              file=sys.stderr)
        print(f"    PowerShell：$env:{KEY_ENV} = 'sk-...'", file=sys.stderr)
        return 2
    model = os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL
    print(f"\n=== 调用 {model} ===")
    content, reasoning, err = call_api(b64, model, key)
    if err:
        # ★ 没拿到响应 ≠ 拿到了但 content 空。前者查 key/网络，后者查
        #   图片链路 —— 把两者混成同一句话，会把人引到错的方向。
        print(f"\n{_c('r')}[!] 根本没拿到响应：{err}{_c('x')}")
        print(f"    {_c('d')}三问一个都没发出去，谈不上视觉能力。{_c('x')}")
        return 1
    if reasoning:
        OUT_REASON.write_text(reasoning, encoding="utf-8")
        print(f"  {_c('y')}reasoning 已 dump：{OUT_REASON}{_c('x')}")
    if not content.strip():
        print(f"\n{_c('r')}[!] content 为空，reasoning {len(reasoning)} 字。"
              f"图片很可能没送到，或模型不支持图片。{_c('x')}")
        return 1
    print(f"  content: {content[:200]}")
    ans = extract_json(content)
    if ans is None:
        print(f"  {_c('r')}✗ 抠不出 JSON。{_c('x')}")
        return 1

    print()
    ok = 0
    for i, (k, label, want) in enumerate(CHECKS, 1):
        got = str(ans.get(k, "")).strip()
        ok += got == want
        mark = f"{_c('g')}✓ 正确{_c('x')}" if got == want \
            else f"{_c('r')}✗ 应为 '{want}'{_c('x')}"
        print(f"[{i}/3] {label}：模型答 '{got}'  {mark}")
    good = ok == len(CHECKS)
    tail = "path_planner 的问题在复杂地形，不在图片链路。" if good \
        else "图片可能没送到，或模型不支持图片。"
    print(f"\n{_c('g' if good else 'r')}结论：视觉能力"
          f"{'正常' if good else '异常'}（{ok}/3）{_c('x')} —— {tail}")
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
