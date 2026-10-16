#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
probe_tiles.py —— 米游社互动地图瓦片元数据探测（阶段 1）· v2

v2 相对 v1 的两个关键修正
----------------------------------------------------------------------------
1. 【必须是多锚点】v1 用单一锚点 (28,25) 测所有图层，结果把 N3 误判成 404 ——
   而 11_7_N3 / 4_6_N3 明明是 200。原因：每个图层是**独立的一张图**，
   坐标原点、范围、有效区域都不同，(28,25) 在 N3 上恰好是空洞。
   v2 对每个图层依次尝试全部已知可用锚点，命中 200 才算该图层可用，
   并记录"哪个锚点在哪个图层可用"。
2. 【每个图层独立边界】P0 和 N1（地下）范围完全不同。v2 对每个可用图层
   用自己的锚点分别二分出 x/y 上下界，写进 layer_bounds。

这个脚本只做"问路"：发少量 HEAD 请求（不下载瓦片内容）。它不下载瓦片，也不拼接。

================================================================================
用法示例
================================================================================
# 全量重新探测（图层可用性 + 每层边界 + map_id + hash），输出覆盖原 meta
D:\\NEKO\\NEKO-Console\\.venv\\Scripts\\python.exe probe_tiles.py

# 只补"每个图层的边界"（图层可用性沿用之前结论，最省请求）
python probe_tiles.py --skip-map-ids --skip-hash

# 只探 N1 一层，锚点用已知可用的 28_25
python probe_tiles.py --layers N1 --anchors 28,25 --skip-map-ids --skip-hash

# 多锚点、多图层（推荐：N3 靠自己的锚点才能测出来）
python probe_tiles.py --layers P0,N1,N3 --anchors 57,51;28,25;11,7;4,6

# 顺带粗扫 N1 的真实形状（请求量 = 格点数，默认关闭）
python probe_tiles.py --layers N1 --anchors 28,25 --coarse-step 10

# 指定输出到别处，别覆盖已确认的元数据
python probe_tiles.py --out D:\\NEKO\\recognition\\data\\mihoyo_meta_v2.json

================================================================================
预期输出
================================================================================
1. 逐条打印探测过程：
   [09:01:02] 图层 N1：锚点 57,51 -> 404，换下一个
   [09:01:03] 图层 N1：锚点 28,25 -> 200（命中）
   [09:02:10] 图层 N1 x 上界估计 = ...（首个缺失 x=...）
2. 末尾摘要：每层的可用性 + 各自的 x/y 范围 + 请求总数
3. 落盘 JSON（默认覆盖 D:\\NEKO\\recognition\\data\\mihoyo_meta.json）

新增/变更字段：
   layers.anchors.<layer> ....... 该图层实测可用的锚点 {x, y}
   layer_bounds.<layer> ......... 该图层自己的 x/y 范围（核心新增）
   bounds ....................... 保持兼容 = 主图层（P0）的边界，等于 layer_bounds.P0

================================================================================
可能的错误 + 排查提示
================================================================================
[A] 某图层全部锚点都 404
    -> 两种可能：(1) 该图层确实不存在；(2) 它的有效区域不包含任何一个已知锚点。
    -> 换成你手头确认过 200 的坐标：--anchors 57,51;28,25;11,7;4,6
    -> 结果会记进 layers.tested.<layer>.anchors_tried，可以看到试了哪些点。

[B] 全部请求 403
    -> 降速 --min-interval 1.0；仍 403 就看 header_probe 一节，
       必要时 --referer https://act.mihoyo.com/。
    -> hash 过期也会整棵目录 404：回互动地图 F12 重抓一条真实瓦片 URL。

[C] 全部 404（包括已知可用的 57_51_P0）
    -> hash 失效。重新抓 hash 后用 --hash 传入。

[D] HEAD 返回 405 / 501
    -> 脚本自动降级 GET(Range: bytes=0-0)，206/200 都算存在。
       想禁用：--no-get-fallback

[E] 边界比真实地图大一圈
    -> 预期行为。二分法假设有效区连续，结果为"上界估计"：
       真瓦片一定在范围内，但范围内可能有空洞（404）。
       下载脚本会把 404 当正常情况跳过。

[F] 报"x=0 不存在"
    -> 该图层原点不在 0，或锚点落点不对。检查该层的 anchor 是否命中 200。

[G] 网络错误刷屏（SSLError / ProxyError / timeout）
    -> 代理或网络问题，与本脚本无关。试 --timeout 30；
       走了境外出口的 VPN 可能被 CDN 拒绝，关掉再试。

[H] 写盘报 PermissionError
    -> 输出目录不可写。用 --out 换路径，或修正目录权限。

================================================================================
合规与容量边界
================================================================================
* 瓦片是米哈游版权资产：本地开发使用可以，随项目分发不行。
  下载目录放在 recognition/data/ 下，该目录应在 .gitignore 里。
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

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "[FATAL] 缺少 requests 库。\n"
        "        请先安装：D:\\NEKO\\NEKO-Console\\.venv\\Scripts\\python.exe -m pip install requests\n"
    )
    raise SystemExit(2)

# ---------------------------------------------------------------------------
# 常量（来自用户已验证 200 的样本 URL）
# ---------------------------------------------------------------------------
DEFAULT_BASE = "https://act-webstatic.mihoyo.com/ys-map-op/map"
DEFAULT_HASH = "eea752b746ae1f2e0c1988a574f2b7b0"
DEFAULT_MAP_ID = 2
DEFAULT_LAYERS = "P0,P1,P2,P3,N0,N1,N2,N3,N4,L0,L1"
DEFAULT_MAP_IDS = "1,2,3,4,5,6"
DEFAULT_OUT = RECOG_ROOT / 'data' / 'mihoyo_meta.json'

# 用户已验证返回 200 的样本坐标。v2 把它们全部作为"候选锚点"逐层尝试，
# 这是修掉"N3 被误判 404"的关键。
DEFAULT_ANCHORS = "57,51;28,25;11,7;4,6;4,7"

# layer -> 已知最可能命中的锚点（只用于排序，让常见图层第一个就命中，省请求）
PREFERRED_ANCHOR = {
    "P0": (57, 51),
    "N1": (28, 25),
    "N3": (11, 7),
}

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

OK_STATUS = (200, 206)
FALLBACK_STATUS = (403, 405, 501)


# ---------------------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------------------
class RateLimiter:
    """单线程限速器。默认 0.5s/请求 = 2 QPS。绝不并发。"""

    def __init__(self, min_interval: float) -> None:
        self.min_interval = max(0.0, float(min_interval))
        self._last = 0.0
        self.requests_made = 0

    def wait(self) -> None:
        if self.min_interval > 0:
            delta = time.monotonic() - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
        self._last = time.monotonic()

    def tick(self) -> None:
        self.requests_made += 1


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def build_url(base: str, map_id: int, hash_token: str | None, x: int, y: int, layer: str) -> str:
    base = base.rstrip("/")
    if hash_token:
        return f"{base}/{map_id}/{hash_token}/{x}_{y}_{layer}.webp"
    return f"{base}/{map_id}/{x}_{y}_{layer}.webp"


def http_request(
    session: requests.Session,
    limiter: RateLimiter,
    method: str,
    url: str,
    timeout: float,
    retries: int,
    extra_headers: dict | None = None,
    want_body_bytes: int = 0,
    quiet: bool = False,
):
    """
    发一个请求，带重试与指数退避。

    返回 (status_code | None, headers dict, body bytes)
    status_code 为 None 表示网络层失败（超时/DNS/SSL），不是 HTTP 错误。
    """
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if extra_headers:
        headers.update(extra_headers)

    last_err = None
    for attempt in range(retries + 1):
        limiter.wait()
        limiter.tick()
        try:
            resp = session.request(
                method,
                url,
                headers=headers,
                timeout=timeout,
                allow_redirects=True,
                # 始终流式读取：需要 body 时只取指定字节，不把整张瓦片拉下来
                stream=True,
            )
            body = b""
            if want_body_bytes > 0 and resp.status_code in OK_STATUS:
                body = resp.raw.read(want_body_bytes, decode_content=True) or b""
            if not quiet:
                log(f"{method} {resp.status_code} {shorten(url)}")
            resp.close()
            return resp.status_code, dict(resp.headers), body
        except requests.RequestException as exc:
            last_err = exc
            if attempt < retries:
                backoff = 2 ** attempt
                log(f"  网络错误({exc.__class__.__name__})，{backoff}s 后重试 {attempt + 1}/{retries}")
                time.sleep(backoff)
    log(f"  [FAIL] {method} {shorten(url)} -> {last_err}")
    return None, {}, b""


def shorten(url: str, keep: int = 72) -> str:
    return url if len(url) <= keep else "..." + url[-keep:]


class ExistsProbe:
    """封装"某个 (x, y, layer) 是否存在"，带缓存避免重复请求。"""

    def __init__(self, session, limiter, base, map_id, hash_token, timeout, retries,
                 allow_get_fallback: bool):
        self.session = session
        self.limiter = limiter
        self.base = base
        self.map_id = map_id
        self.hash_token = hash_token
        self.timeout = timeout
        self.retries = retries
        self.allow_get_fallback = allow_get_fallback
        self._cache: dict[tuple[int, int, str], bool] = {}
        self._methods: dict[str, int] = {}

    def status_of(self, x: int, y: int, layer: str):
        """返回 (是否存在, 状态码, 实际用的方法)。"""
        url = build_url(self.base, self.map_id, self.hash_token, x, y, layer)
        status, _, _ = http_request(self.session, self.limiter, "HEAD", url,
                                    self.timeout, self.retries)
        method_used = "HEAD"
        if status in FALLBACK_STATUS and self.allow_get_fallback:
            log(f"  HEAD 返回 {status}，降级为 GET(Range 1 字节)")
            status, _, _ = http_request(
                self.session, self.limiter, "GET", url, self.timeout, self.retries,
                extra_headers={"Range": "bytes=0-0"}, want_body_bytes=1,
            )
            method_used = "GET(range)"
        self._methods[method_used] = self._methods.get(method_used, 0) + 1
        return (status in OK_STATUS), status, method_used

    def exists(self, x: int, y: int, layer: str) -> bool:
        key = (x, y, layer)
        if key not in self._cache:
            ok, _, _ = self.status_of(x, y, layer)
            self._cache[key] = ok
        return self._cache[key]

    def method_summary(self) -> dict:
        return dict(self._methods)


# ---------------------------------------------------------------------------
# WebP 尺寸解析（不依赖 Pillow）
# ---------------------------------------------------------------------------
def webp_size(data: bytes):
    """从 WebP 容器头部解析宽高。返回 (w, h) 或 None。"""
    if len(data) < 16 or data[0:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    pos = 12
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        chunk = data[pos + 8:pos + 8 + size]
        try:
            if cid == b"VP8X" and len(chunk) >= 10:
                return (int.from_bytes(chunk[4:7], "little") + 1,
                        int.from_bytes(chunk[7:10], "little") + 1)
            if cid == b"VP8 " and len(chunk) >= 10 and chunk[3:6] == b"\x9d\x01\x2a":
                return (int.from_bytes(chunk[6:8], "little") & 0x3FFF,
                        int.from_bytes(chunk[8:10], "little") & 0x3FFF)
            if cid == b"VP8L" and len(chunk) >= 5:
                b = chunk[1:5]
                return (1 + (((b[1] & 0x3F) << 8) | b[0]),
                        1 + (((b[3] & 0x0F) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6)))
        except Exception:
            return None
        pos += 8 + size + (size & 1)
    return None


def detect_tile_size(session, limiter, base, map_id, hash_token, timeout, retries, anchors):
    """下载一个已知可用样本，读出瓦片像素尺寸。"""
    result = {"width": None, "height": None, "source": None, "sample_url": None}
    for x, y, layer in anchors:
        url = build_url(base, map_id, hash_token, x, y, layer)
        status, _, body = http_request(session, limiter, "GET", url, timeout, retries,
                                      want_body_bytes=200_000)
        if status not in OK_STATUS or not body:
            continue
        result["sample_url"] = url
        log(f"  样本 {x}_{y}_{layer} 大小 {len(body)} 字节")
        try:
            from PIL import Image
            import io
            with Image.open(io.BytesIO(body)) as im:
                result.update(width=im.width, height=im.height, source="pillow")
            return result
        except ImportError:
            pass
        except Exception as exc:
            log(f"  Pillow 打开失败({exc.__class__.__name__})，改用头部解析")
        size = webp_size(body)
        if size:
            result.update(width=size[0], height=size[1], source="webp_header")
            return result
        log("  无法解析该样本的尺寸，试下一个样本")
    return result


# ---------------------------------------------------------------------------
# 边界搜索
# ---------------------------------------------------------------------------
def search_axis(probe: ExistsProbe, axis_name: str, axis: str, fixed: int,
                cap: int, layer: str):
    """
    沿一个轴找最大有效坐标：先倍增找到一个"不存在"的坐标，再在
    (最后存在的, 首个不存在的) 之间二分。
    """
    def ok_fn(k: int) -> bool:
        return probe.exists(k, fixed, layer) if axis == "x" else probe.exists(fixed, k, layer)

    detail = {"anchor": {("y" if axis == "x" else "x"): fixed}, "cap": cap}
    log(f"  搜索 {layer}.{axis_name} 上界（锚点 {'y' if axis == 'x' else 'x'}={fixed}, 上限 {cap}）")

    if not ok_fn(0):
        detail["note"] = f"{axis_name}=0 不存在；该图层原点可能不从 0 开始"
        log(f"    {axis_name}=0 不存在，放弃该轴搜索")
        return None, detail

    last_ok = 0
    k = 1
    first_bad = None
    while k <= cap:
        if ok_fn(k):
            last_ok = k
            k *= 2
        else:
            first_bad = k
            break

    if first_bad is None:
        detail["note"] = f"{axis_name} 在 {cap} 以内全部存在，未找到上界（真实范围可能更大）"
        detail["capped_at"] = cap
        log(f"    {axis_name} 在 {cap} 内全部存在，取 {last_ok} 作为下限参考")
        return last_ok, detail

    lo, hi = last_ok, first_bad
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if ok_fn(mid):
            lo = mid
        else:
            hi = mid

    detail["first_missing"] = hi
    detail["note"] = "二分法结果；不规则地图下这是'上界估计'，范围内可能仍有空洞"
    log(f"    {axis_name} 上界估计 = {lo}（首个缺失 {axis_name}={hi}）")
    return lo, detail


def probe_layer_bounds(probe: ExistsProbe, layer: str, anchor: tuple[int, int],
                       cap: int, coarse_step: int):
    """用该图层自己的锚点，分别二分 x/y 上界。"""
    ax, ay = anchor
    bounds = {
        "map_id": probe.map_id,
        "layer": layer,
        "anchor": {"x": ax, "y": ay},
        "method": "多锚点定位 + 倍增 + 二分法（HEAD）",
        "monotonic_assumption": True,
        "bounds_are_estimates": True,
        "note": "该图层独立坐标系；结果为上界估计，范围内可能有 404 空洞",
    }
    x_max, x_detail = search_axis(probe, "x", "x", fixed=ay, cap=cap, layer=layer)
    y_max, y_detail = search_axis(probe, "y", "y", fixed=ax, cap=cap, layer=layer)
    bounds["x"] = {"min": 0, "max": x_max, **x_detail}
    bounds["y"] = {"min": 0, "max": y_max, **y_detail}
    if coarse_step > 0 and x_max is not None and y_max is not None:
        bounds["coarse_scan"] = coarse_scan(probe, layer, x_max, y_max, coarse_step)
    return bounds


def coarse_scan(probe: ExistsProbe, layer: str, x_cap: int, y_cap: int, step: int):
    """可选粗扫：按 step 走网格，找出真实存在瓦片的包围盒。"""
    log(f"  粗扫 {layer} 网格 step={step}（x 0..{x_cap}, y 0..{y_cap}）")
    found = []
    total = 0
    for y in range(0, y_cap + 1, step):
        for x in range(0, x_cap + 1, step):
            total += 1
            if probe.exists(x, y, layer):
                found.append((x, y))
    log(f"    粗扫完成，{total} 次请求，命中 {len(found)} 个格点")
    if not found:
        return {"step": step, "requests": total, "hit": 0,
                "note": "粗扫无命中；step 可能过大跳过整个地图，或范围不对"}
    xs = [p[0] for p in found]
    ys = [p[1] for p in found]
    return {
        "step": step, "requests": total, "hit": len(found),
        "bbox": {"x_min": min(xs), "x_max": max(xs), "y_min": min(ys), "y_max": max(ys)},
        "sample_hits": found[:20],
        "note": "按 step 采样的包围盒，真实边界可能外扩 step-1 个瓦片",
    }


# ---------------------------------------------------------------------------
# 各项探测
# ---------------------------------------------------------------------------
def probe_hash_required(session, limiter, base, map_id, timeout, retries,
                        allow_get_fallback, layer="P0", x=57, y=51):
    log("测试 hash 是否必须（不带 hash 的 URL）")
    url_no_hash = build_url(base, map_id, None, x, y, layer)
    status, _, _ = http_request(session, limiter, "HEAD", url_no_hash, timeout, retries)
    used = "HEAD"
    if status in FALLBACK_STATUS and allow_get_fallback:
        status, _, _ = http_request(session, limiter, "GET", url_no_hash, timeout, retries,
                                    extra_headers={"Range": "bytes=0-0"}, want_body_bytes=1)
        used = "GET(range)"
    if status is None:
        return None, {"url": url_no_hash, "status": None, "method": used,
                      "note": "网络失败，无法判断"}
    required = status not in OK_STATUS
    log(f"  无 hash URL -> {status} => hash {'必须' if required else '可省略'}")
    return required, {"url": url_no_hash, "status": status, "method": used}


def probe_layers(session, limiter, base, map_id, hash_token, layers, anchors,
                 timeout, retries, allow_get_fallback):
    """
    v2 核心：每个图层依次尝试**全部**候选锚点，命中 200 即判定可用，
    并记录该图层的可用锚点。这修掉了 v1 单锚点把 N3 误判为 404 的问题。
    """
    log(f"测试图层（每层依次尝试 {len(anchors)} 个锚点）")
    available, tested, layer_anchors = [], {}, {}
    for layer in layers:
        tried = []
        hit = None
        for (x, y) in anchors:
            url = build_url(base, map_id, hash_token, x, y, layer)
            status, _, _ = http_request(session, limiter, "HEAD", url, timeout, retries,
                                        quiet=True)
            used = "HEAD"
            if status in FALLBACK_STATUS and allow_get_fallback:
                status, _, _ = http_request(session, limiter, "GET", url, timeout, retries,
                                            extra_headers={"Range": "bytes=0-0"},
                                            want_body_bytes=1, quiet=True)
                used = "GET(range)"
            tried.append({"anchor": f"{x},{y}", "status": status, "method": used})
            if status in OK_STATUS:
                hit = (x, y)
                log(f"  图层 {layer}：锚点 {x},{y} -> {status}（命中）")
                break
            log(f"  图层 {layer}：锚点 {x},{y} -> {status}，换下一个")
        tested[layer] = {
            "ok": hit is not None,
            "available_anchor": f"{hit[0]},{hit[1]}" if hit else None,
            "anchors_tried": tried,
        }
        if hit:
            available.append(layer)
            layer_anchors[layer] = {"x": hit[0], "y": hit[1]}
    log(f"  可用图层：{available if available else '（无）'}")
    return available, tested, layer_anchors


def probe_map_ids(session, limiter, base, hash_token, map_ids, timeout, retries,
                  allow_get_fallback, x=57, y=51, layer="P0"):
    """逐个 map_id 试同一坐标，并对比前 2KB 的内容指纹区分是否同一张图。"""
    log(f"测试 map_id：{map_ids}（锚点 {x}_{y}_{layer}）")
    entries, fingerprints = {}, {}
    for mid in map_ids:
        url = build_url(base, mid, hash_token, x, y, layer)
        status, headers, _ = http_request(session, limiter, "HEAD", url, timeout, retries)
        used = "HEAD"
        if status in FALLBACK_STATUS and allow_get_fallback:
            status, headers, _ = http_request(
                session, limiter, "GET", url, timeout, retries,
                extra_headers={"Range": "bytes=0-0"}, want_body_bytes=1)
            used = "GET(range)"
        entry = {
            "status": status, "ok": status in OK_STATUS, "method": used,
            "content_length": headers.get("Content-Length"),
            "etag": headers.get("ETag"),
            "region_name": None,
            "region_name_note": "瓦片本身不含区域名；需人工看拼接图后回填",
        }
        if status in OK_STATUS:
            st2, _, body = http_request(session, limiter, "GET", url, timeout, retries,
                                        want_body_bytes=2048)
            if st2 in OK_STATUS and body:
                fp = hashlib.sha256(body).hexdigest()[:16]
                entry["sample_sha256_2k"] = fp
                fingerprints[mid] = fp
        entries[str(mid)] = entry

    reference = fingerprints.get(DEFAULT_MAP_ID)
    for entry in entries.values():
        fp = entry.get("sample_sha256_2k")
        entry["same_as_map_2"] = None if (fp is None or reference is None) else (fp == reference)
    ok_ids = [int(k) for k, v in entries.items() if v["ok"]]
    log(f"  可用 map_id：{ok_ids if ok_ids else '（无）'}")
    return ok_ids, entries


def probe_headers(session, limiter, base, map_id, hash_token, timeout, retries, referer):
    """观察不同 header 组合的状态码，回答'是否需要特定 header'。"""
    url = build_url(base, map_id, hash_token, 57, 51, "P0")
    combos = {
        "ua_only": {},
        "ua_referer": {"Referer": referer},
        "ua_referer_origin": {"Referer": referer, "Origin": "https://act.mihoyo.com"},
        "no_ua": {"User-Agent": ""},
    }
    results = {}
    for name, extra in combos.items():
        status, _, _ = http_request(session, limiter, "HEAD", url, timeout, retries,
                                    extra_headers=extra, quiet=True)
        results[name] = status
        log(f"  header 组合 {name} -> {status}")
    return results


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def parse_anchors(text: str):
    """解析 '57,51;28,25' -> [(57,51),(28,25)]，并按图层偏好排序。"""
    out = []
    for part in str(text).split(";"):
        part = part.strip()
        if not part:
            continue
        x_s, y_s = part.split(",")
        out.append((int(x_s), int(y_s)))
    return out


def order_anchors_for_layer(anchors, layer):
    """让该图层最可能命中的锚点排在最前，省请求。"""
    pref = PREFERRED_ANCHOR.get(layer)
    if pref and pref in anchors:
        return [pref] + [a for a in anchors if a != pref]
    return list(anchors)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="探测米游社互动地图瓦片元数据（多锚点 + 每图层独立边界），输出 mihoyo_meta.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--base", default=DEFAULT_BASE, help="URL 前缀")
    ap.add_argument("--hash", dest="hash_token", default=DEFAULT_HASH,
                    help="版本令牌；传空字符串则用无 hash 模板")
    ap.add_argument("--map-id", type=int, default=DEFAULT_MAP_ID, help="主 map_id")
    ap.add_argument("--map-ids", default=DEFAULT_MAP_IDS, help="要试的 map_id 列表")
    ap.add_argument("--layers", default=DEFAULT_LAYERS, help="要试的图层列表")
    ap.add_argument("--anchors", default=DEFAULT_ANCHORS,
                    help="候选锚点，分号分隔，如 57,51;28,25;11,7")
    ap.add_argument("--out", default=DEFAULT_OUT, help="输出 JSON 路径")
    ap.add_argument("--min-interval", type=float, default=0.5,
                    help="每次请求最小间隔秒数，默认 0.5 = 2 QPS")
    ap.add_argument("--timeout", type=float, default=15.0, help="单请求超时秒数")
    ap.add_argument("--retries", type=int, default=2, help="网络失败重试次数（指数退避）")
    ap.add_argument("--axis-cap", type=int, default=512, help="x/y 搜索上限")
    ap.add_argument("--referer", default="https://act.mihoyo.com/", help="header 测试用 Referer")
    ap.add_argument("--coarse-step", type=int, default=0,
                    help="可选粗扫步长；0=关闭。开启产生 (x/step)*(y/step) 次请求/图层")
    ap.add_argument("--no-get-fallback", action="store_true", help="禁止 HEAD 失败时降级 GET")
    ap.add_argument("--skip-layer-bounds", action="store_true", help="跳过每个图层的边界探测")
    ap.add_argument("--skip-hash", action="store_true", help="跳过 hash 是否必须的探测")
    ap.add_argument("--skip-headers", action="store_true", help="跳过 header 敏感性探测")
    ap.add_argument("--skip-layers", action="store_true", help="跳过图层可用性探测")
    ap.add_argument("--skip-map-ids", action="store_true", help="跳过 map_id 探测")
    args = ap.parse_args(argv)

    hash_token = args.hash_token.strip() or None
    layers = [p.strip() for p in args.layers.split(",") if p.strip()]
    map_ids = [int(p) for p in str(args.map_ids).replace(" ", "").split(",") if p]
    anchors = parse_anchors(args.anchors)
    allow_get_fallback = not args.no_get_fallback
    if not anchors:
        sys.stderr.write("[FATAL] --anchors 为空，至少给一个已知可用的坐标，如 --anchors 57,51\n")
        return 2

    session = requests.Session()
    limiter = RateLimiter(args.min_interval)
    started = time.time()

    log("=" * 72)
    log("阶段 1 · 瓦片元数据探测 v2（多锚点 + 每图层独立边界）")
    log(f"base={args.base}  map_id={args.map_id}  hash={hash_token}")
    log(f"候选锚点={anchors}")
    log(f"限速={args.min_interval}s/请求（约 {1/max(args.min_interval,1e-9):.1f} QPS，单线程）")
    log("=" * 72)

    meta: dict = {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "script": "probe_tiles.py",
        "base_url": args.base,
        "hash": hash_token,
        "primary_map_id": args.map_id,
        "rate_limit_seconds": args.min_interval,
        "anchors_used": [f"{x},{y}" for (x, y) in anchors],
        "notes": [],
    }

    # 1) 瓦片尺寸（用带图层的锚点，便于确认坐标系）
    log("[1/7] 瓦片像素尺寸")
    anchor_triples = []
    for layer in layers:
        for (x, y) in order_anchors_for_layer(anchors, layer)[:2]:
            anchor_triples.append((x, y, layer))
    meta["tile_size"] = detect_tile_size(session, limiter, args.base, args.map_id,
                                         hash_token, args.timeout, args.retries, anchor_triples)
    if meta["tile_size"]["width"]:
        log(f"  => {meta['tile_size']['width']} x {meta['tile_size']['height']} "
            f"({meta['tile_size']['source']})")
    else:
        meta["notes"].append("瓦片尺寸未测出：可能 hash/map_id 已失效，或网络不通")
        log("  => 未测出，后续结果可能不可靠")

    # 2) hash 是否必须
    if args.skip_hash:
        log("[2/7] hash 是否必须：已跳过")
        meta["hash_required"] = None
        meta["hash_required_detail"] = {"skipped": True}
    else:
        log("[2/7] hash 是否必须")
        required, detail = probe_hash_required(session, limiter, args.base, args.map_id,
                                               args.timeout, args.retries, allow_get_fallback)
        meta["hash_required"] = required
        meta["hash_required_detail"] = detail

    # 3) header 敏感性
    if args.skip_headers:
        log("[3/7] header 敏感性：已跳过")
        meta["header_probe"] = {"skipped": True}
    else:
        log("[3/7] header 敏感性")
        meta["header_probe"] = probe_headers(session, limiter, args.base, args.map_id,
                                             hash_token, args.timeout, args.retries, args.referer)
    meta["user_agent"] = UA

    # 4) 图层可用性（多锚点）
    if args.skip_layers:
        log("[4/7] 图层可用性：已跳过")
        available = []
        meta["layers"] = {"tested": {}, "available": [], "anchors": {},
                          "list_tested": layers, "skipped": True}
    else:
        log("[4/7] 图层可用性（多锚点）")
        available, tested, layer_anchors = probe_layers(
            session, limiter, args.base, args.map_id, hash_token, layers, anchors,
            args.timeout, args.retries, allow_get_fallback)
        meta["layers"] = {"tested": tested, "available": available,
                          "anchors": layer_anchors, "list_tested": layers}
        if not available:
            meta["notes"].append("没有任何图层返回 200：优先怀疑 hash 过期")

    # 5) map_id
    if args.skip_map_ids:
        log("[5/7] map_id 列表：已跳过")
        meta["map_ids"] = {"tested": map_ids, "available": [], "entries": {}, "skipped": True}
    else:
        log("[5/7] map_id 列表")
        ok_ids, entries = probe_map_ids(session, limiter, args.base, hash_token, map_ids,
                                        args.timeout, args.retries, allow_get_fallback)
        meta["map_ids"] = {"tested": map_ids, "available": ok_ids, "entries": entries}
        if len({e.get("sample_sha256_2k") for e in entries.values()
                if e.get("sample_sha256_2k")}) > 1:
            meta["notes"].append("不同 map_id 的样本内容指纹不同，说明是不同区域的地图")

    # 6) 每个图层独立边界（v2 核心）
    probe = ExistsProbe(session, limiter, args.base, args.map_id, hash_token,
                        args.timeout, args.retries, allow_get_fallback)
    layer_bounds: dict = {}
    if args.skip_layer_bounds:
        log("[6/7] 每图层边界：已跳过")
    else:
        log("[6/7] 每个图层各自的 x/y 边界")
        for layer in (available or []):
            anchor = meta["layers"]["anchors"].get(layer)
            if not anchor:
                continue
            log(f"  图层 {layer}：锚点 {anchor['x']},{anchor['y']}")
            layer_bounds[layer] = probe_layer_bounds(
                probe, layer, (anchor["x"], anchor["y"]), args.axis_cap, args.coarse_step)
        if not layer_bounds:
            meta["notes"].append("没有探到任何图层边界；下载脚本需要手动传范围")

    meta["layer_bounds"] = layer_bounds
    # 7) 兼容旧字段：bounds = 主图层（P0，退化为第一个可用图层）的边界
    primary_layer = "P0" if "P0" in layer_bounds else (next(iter(layer_bounds), "P0"))
    meta["bounds"] = layer_bounds.get(primary_layer, {
        "map_id": args.map_id, "layer": primary_layer,
        "note": "未探到边界；见 layer_bounds", "x": {"min": 0, "max": None},
        "y": {"min": 0, "max": None},
    })
    log(f"[7/7] 兼容字段 bounds = layer_bounds.{primary_layer}")
    meta["probe_methods_used"] = probe.method_summary()

    # 写盘
    out_path = Path(args.out)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        sys.stderr.write(f"[FATAL] 写不了 {out_path}：{exc}\n"
                         "        排查：目录是否存在/可写，或用 --out 换路径\n")
        return 2

    log("=" * 72)
    log("探测摘要")
    log(f"  请求总数   : {limiter.requests_made}")
    log(f"  耗时       : {time.time() - started:.1f}s")
    log(f"  瓦片尺寸   : {meta['tile_size']['width']} x {meta['tile_size']['height']}")
    log(f"  hash 必须  : {meta.get('hash_required')}")
    log(f"  可用图层   : {meta.get('layers', {}).get('available')}")
    log(f"  可用 map_id: {meta.get('map_ids', {}).get('available')}")
    for layer, b in layer_bounds.items():
        log(f"  {layer:>3} 范围   : x 0..{b['x']['max']}  y 0..{b['y']['max']}"
            f"  (锚点 {b['anchor']['x']},{b['anchor']['y']})")
    for n in meta["notes"]:
        log(f"  [注意] {n}")
    log(f"  元数据已写入: {out_path}")
    log("=" * 72)
    log("下一步：download_tiles.py 会自动按图层读取各自的范围（layer_bounds）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
