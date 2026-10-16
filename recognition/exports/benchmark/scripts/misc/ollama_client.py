"""Ollama 本地视觉模型客户端（**纯标准库**，2026-10-06）。

★ 为什么不用 requests / ollama 官方 SDK
  ------------------------------------
  一是这个项目整体零新依赖（overlay / e2e / 看图工具都是 stdlib + PIL +
  numpy）；二是 ollama 的 HTTP 接口就是一层 ``POST /api/chat``，
  官方 SDK 带来的价值远小于它引入的依赖树。

★★ 图片字段和 OpenAI 格式**不一样**（踩过才知道）
  --------------------------------------------------
  OpenAI 兼容端点用 ``content: [{"type":"image_url","image_url":{"url":"data:..."}}]``；
  Ollama 用 ``messages[].images: ["<裸 base64>"]`` —— **不带 data: 前缀**，
  也不放在 content 数组里。照抄 OpenAI 的写法会得到「模型看不见图」的
  静默失败。

★ ``stream: false`` 必须显式给
  ----------------------------
  默认 ``stream=true``，响应是**换行分隔的 JSON 流**，直接
  ``json.loads`` 整个 body 会炸。关掉它才拿到单个 JSON。
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

#: Ollama 默认端口。本机跑，不跨机器，所以不给配置项。
BASE = "http://localhost:11434"
TAGS_URL = f"{BASE}/api/tags"
CHAT_URL = f"{BASE}/api/chat"

#: 视觉模型名。★ 是 ``qwen2.5vl``，**不是** ``qwen2.5-vl``。
DEFAULT_MODEL = "qwen2.5vl:7b"

#: 本地 7B 视觉模型冷启动 + 生成，实测 30~120 秒很正常。
#: 180 是「慢但还在动」与「卡死了」的分界。
CHAT_TIMEOUT_S = 180.0

#: 服务探活超时。探活要**快**：服务没起时不该让人干等 180 秒。
TAGS_TIMEOUT_S = 5.0


def _get_json(url: str, timeout: float):
    """GET 一个 JSON。失败抛，调用方决定怎么翻译成给人看的话。"""
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def list_models(timeout: float = TAGS_TIMEOUT_S) -> list[str]:
    """列出已安装的模型名。**服务没跑就抛**，不做静默兜底。"""
    doc = _get_json(TAGS_URL, timeout)
    return [str(m.get("name", "")) for m in (doc.get("models") or [])]


def model_present(names: list[str], model: str) -> bool:
    """模型在不在。

    ★ 要匹配「**前缀**」，不能全等
      ---------------------------
      ``qwen2.5vl:7b`` 在 ``/api/tags`` 里可能登记成
      ``qwen2.5vl:7b``、``qwen2.5vl:latest`` 或 ``qwen2.5vl``。
      全等会漏掉后两种，用户明明 pull 了却被告知没装。
    """
    want = model.strip().lower()
    base = want.split(":")[0]
    for n in names:
        nl = str(n).strip().lower()
        if nl == want or nl.split(":")[0] == base:
            return True
    return False


def service_status(timeout: float = TAGS_TIMEOUT_S) -> tuple[bool, str, list[str]]:
    """探活 + 顺带列模型。

    :returns: ``(服务是否可用, 失败原因, 模型名列表)``。
        可用时失败原因为空串。**不给空列表冒充成功** —— 探活失败就
        老实返回 False 和原因。
    """
    try:
        return True, "", list_models(timeout)
    except urllib.error.URLError as exc:
        return False, f"连不上 {BASE}（{exc.reason}）", []
    except TimeoutError:
        return False, f"{BASE} 超过 {timeout:g}s 没响应", []
    except Exception as exc:                          # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}", []


def encode_image(image_path: str | Path) -> str:
    """图片 -> **裸** base64（**无 data: 前缀**）。

    前缀是 OpenAI 格式的规矩；Ollama 的 ``images[]`` 只要裸 base64，
    多给前缀会被当成图片内容的一部分。
    """
    return base64.b64encode(
        Path(image_path).read_bytes()).decode("ascii")


def chat_vision(image_path: str | Path, prompt: str, *,
                model: str = DEFAULT_MODEL,
                timeout: float = CHAT_TIMEOUT_S,
                temperature: float = 0.0) -> tuple[str | None, str, float]:
    """问一张图。返回 ``(回答, 失败原因, 耗时秒)``。

    ★ temperature 固定 0
      ------------------
      这是「能力边界探测」，要的是**可复现**的答案。随机性会把
      「模型有时对有时错」和「模型一直错」混成一类。

    ★ 失败一律不抛
      ------------
      探边界的脚本必须能把「模型不行」和「服务挂了」分开报出来；
      抛异常会打断整轮测试，只剩一个 traceback。
    """
    t0 = time.monotonic()
    try:
        b64 = encode_image(image_path)
    except OSError as exc:
        return None, f"读不到图片：{exc}", time.monotonic() - t0

    payload = {
        "model": model,
        "stream": False,                    # ★ 否则是 JSON 流，loads 不了
        "messages": [{
            "role": "user",
            "content": prompt,
            "images": [b64],                # ★ 裸 base64，不带 data: 前缀
        }],
        "options": {"temperature": temperature},
    }
    req = urllib.request.Request(
        CHAT_URL, method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:                          # noqa: BLE001
            detail = "(读不到响应体)"
        return None, f"HTTP {exc.code} {exc.reason} | {detail}", \
            time.monotonic() - t0
    except TimeoutError:
        return None, f"{timeout:g}s 超时（本地模型慢，可调大 timeout）", \
            time.monotonic() - t0
    except urllib.error.URLError as exc:
        return None, f"连不上 ollama（{exc.reason}）", time.monotonic() - t0
    except Exception as exc:                         # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}", time.monotonic() - t0

    try:
        doc = json.loads(raw)
    except ValueError as exc:
        return None, f"响应不是合法 JSON：{exc} | 前 200 字：{raw[:200]}", \
            time.monotonic() - t0

    try:
        text = str(doc["message"]["content"])
    except (KeyError, TypeError) as exc:
        return None, f"响应结构不符预期（{exc}）| 前 200 字：{raw[:200]}", \
            time.monotonic() - t0
    if not text.strip():
        return None, "模型返回了空内容", time.monotonic() - t0

    # token 数不是必需但很有用：一次视觉请求的 prompt 里塞了大图，
    # 数字大得离谱就说明它确实把图编码进去了。
    n_in = doc.get("prompt_eval_count")
    n_out = doc.get("eval_count")
    if n_in or n_out:
        text = f"{text}\n\n[tokens in={n_in} out={n_out}]"
    return text, "", time.monotonic() - t0


__all__ = [
    "BASE", "CHAT_TIMEOUT_S", "CHAT_URL", "DEFAULT_MODEL", "TAGS_URL",
    "TAGS_TIMEOUT_S", "chat_vision", "encode_image", "list_models",
    "model_present", "service_status",
]
