"""变体实验：复杂地形是**图**的问题还是**任务**的问题（2026-10-06）。

``diag_llm_vision.py`` 判定 deepseek-flash 的视觉**链路**正常（3/3），
但让它规划同一片区域的路点时，输出是起点到终点的**等距插值** ——
典型的「没在看地形」。这里把矛盾拆开：**同一片区域、同一个问题、
5 种不同简化程度的图**。答案一致 -> 模型看到了内容，差异来自任务表述；
答案各不相同 -> 模型在猜，图里的细节它压根没读进去。

★ 复用 path_planner 的读图，而不是重写
------------------------------------
  变体 A 必须和 ``path_planner.py`` 实际发出去的那张图**逐像素一致**，
  否则「对照组」就不对照了。读法、缩放档位、标注全部走同一个函数。

    python diag_llm_vision2.py            # 需要 $env:DEEPSEEK_API_KEY
    python diag_llm_vision2.py --no-color
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

import argparse
import base64
import importlib.util
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(Path(PLUGIN_DIR)))
ENDPOINT = "https://api.deepseek.com/v1/chat/completions"
KEY_ENV, MODEL_ENV = "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL"
DEFAULT_MODEL, TIMEOUT_S = "deepseek-flash", 30.0
#: ★ 500 → 4000（2026-10-06 真机）
#:   5 个变体全部「content 空 / completion=500（恰好等于 max_tokens）」，
#:   而同 max_tokens 的 diag_llm_vision.py（简单图）是 3/3。
#:   结论很清楚：**复杂图让模型陷入长推理**，500 全被思考吃掉、正文没轮到。
MAX_TOKENS = 4000
#: 推理强度。complex 图 + 长推理 -> 压到最低。端点不认时自动去掉并重试。
REASONING_EFFORT = "minimal"
#: 进程内标记：端点认不认 reasoning_effort。None=没试过 / False=被拒。
_REASONING_EFFORT_OK: bool | None = None
TMP = Path(os.environ.get("TEMP", "."))
#: 起点 / 终点。★ 与 path_planner 失败那次**完全相同**。
START, TARGET = (1000.0, 1000.0), (3000.0, 3000.0)
#: 对应底图像素框 2801x2798 —— 与 path_planner.crop_box_px 的输出核对过。
BOX = (20060, 7846, 22861, 10644)
GRID_M = 100.0                 # 网格间距（世界单位）
SLEEP_S = 2.0                  # 两次调用的间隔，防限流
NO_COLOR = False
QUESTION = f"""这是原神地图的一个区域。红点=起点{START[0]:.0f},{START[1]:.0f}，绿点=终点{TARGET[0]:.0f},{TARGET[1]:.0f}。
从红点到绿点，中间有没有明显的障碍（大山/大水域）？
如果有，请指出大致在哪个方向；如果没有，说"无障碍"。
用 JSON 回答：
{{"has_obstacle": true/false, "obstacle_direction": "北方/东北/东/东南/南/西南/西/西北/无", "reason": "简短说明"}}"""


def _c(k):
    return "" if NO_COLOR else {"r": "\033[31m", "g": "\033[32m",
                                "y": "\033[33m", "b": "\033[1m",
                                "d": "\033[2m", "x": "\033[0m"}.get(k, "")


for _s in (sys.stdout, sys.stderr):          # 中文控制台是 cp936，编不出符号
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def load_path_planner():
    """按文件加载 path_planner（无包上下文）。"""
    spec = importlib.util.spec_from_file_location(
        "_pp", HERE / "path_planner.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def base_image(pp):
    """变体 A 的底图：读图 -> 缩到长边 1024 -> 标注红绿点。

    ★ 与 path_planner.get_waypoints 的图像流程**逐行一致**（缩放档位、
      LANCZOS、标注顺序都相同）。A 要是差几个像素，对照就废了。
    """
    img, _ = pp.read_crop(BOX)
    s = pp.MAX_EDGE / float(max(img.width, img.height))
    img = img.resize((int(img.width * s), int(img.height * s)), Image.LANCZOS)
    return pp.annotate(img, BOX, START, TARGET)


# ------------------------------------------------------------------ 变体
def v_b_gray(img, _pp):
    """B · 纯灰度。很多模型训练时灰度才是主流输入分布。"""
    return img.convert("L").convert("RGB")


def _pct_thr(gray_img, pct=0.70) -> int:
    """按直方图**分位数**取阈值。

    ★ 为什么不是固定 128
      -----------------
      固定 128 把这张图压成了几乎全黑（实测只剩 48 KB 的黑白噪声），
      红绿点一起消失 —— 而这张实验要问的恰恰是「起点到终点之间有没有
      障碍」，没有起终点的图**这个问题就无从谈起**。
      分位数只保留亮部（山脊/高光/岸线），正好是要突出的那一半。

      70 分位是调出来的：85 分位只剩 38 KB、近乎全黑，模型几乎看不到
      东西，这一档就变成了「故意送一张废图」而不是一个有效变体。

    ★ 为什么不是 Otsu
      --------------
      Otsu 更「自适应」，但要引 numpy 或写 16 行；这张图只有单峰偏暗的
      分布，分位数一刀就够。诊断脚本优先保证**能跑、图能用**。
    """
    hist = gray_img.histogram()
    total, acc = sum(hist), 0
    for v, cnt in enumerate(hist):
        acc += cnt
        if acc >= total * pct:
            return v
    return 128


def v_c_binary(img, pp):
    """C · 二值化 + 边缘强化：灰度 -> 自动对比度 -> 分位数阈值分水陆 ->
    FIND_EDGES 描边界 -> 把边界**乘**进底图。

    ★ 两件事是必须的，少一件这变体就废了
      ----------------------------------
      1. ``ImageChops.multiply`` 要求两张图**模式一致**；``invert(edge)``
         出来还是 L 模式，一乘就抛 "images do not match"。
      2. 二值化会把红绿点**一起吃掉** —— 所以之后必须**重新标注**。
    """
    g = ImageOps.autocontrast(img.convert("L"))
    t = _pct_thr(g)
    bw = g.point(lambda p, t=t: 255 if p > t else 0)
    edge = bw.filter(ImageFilter.FIND_EDGES).point(lambda p: 255 if p > 40 else 0)
    out = ImageChops.multiply(bw.convert("RGB"),
                              ImageOps.invert(edge).convert("RGB"))
    return pp.annotate(out, BOX, START, TARGET)      # ★ 重新画红绿点


def v_d_blur(img, _pp):
    """D · 降分辨率到 256 再放大回 1024：地形**大块化**，细节变噪声但山脉
    /水域的大轮廓更醒目。测的是「模型是不是被细节淹没了」。

    ★ 用浮点比例，不能写 ``256 // max(...)``
      --------------------------------------
      长边 1024 时 ``256 // 1024`` 是 **0**，缩放直接抛 "must be > 0"。
    """
    k = 256.0 / max(img.width, img.height)
    small = img.resize((max(1, int(img.width * k)),
                        max(1, int(img.height * k))), Image.LANCZOS)
    return small.resize((img.width, img.height), Image.LANCZOS)


def v_e_grid(img, pp):
    """E · 每 100 世界单位一条淡灰网格 + 世界坐标刻度。

    ★ 用 _world_to_img 逐点算，不按比例画
      ------------------------------------
      底图的轴是**转置**的（像素 X 跟随 world_z、像素 Y 跟随 world_x）。
      按比例画网格会画出**旋转过**的参考系 —— 比没有参考系更糟。逐点走
      同一个换算函数，网格与红绿点用同一套坐标，不会错位。
    """
    out = img.copy()
    d = ImageDraw.Draw(out)
    (x0, z0), (x1, z1) = START, TARGET
    at = lambda wx, wz: pp._world_to_img(out, BOX, wx, wz)      # noqa: E731
    n = int((x1 - x0) / GRID_M) + 1
    for i in range(n + 1):
        z, x = z0 + i * GRID_M, x0 + i * GRID_M
        d.line([at(x0, z), at(x1, z)], fill=(150, 150, 150), width=1)
        d.line([at(x, z0), at(x, z1)], fill=(150, 150, 150), width=1)
    try:
        f = ImageFont.truetype("segoeui.ttf", 13)
    except Exception:                                     # noqa: BLE001
        f = ImageFont.load_default()
    for i in range(2, n, 2):                              # 每 200 米标一次
        # ★ 标签必须跟着**各自那条线**走。早期版本把 x 轴族全钉在画布
        #   顶边、z 轴族全钉在左边，结果多条标签 clamp 到同一角落，
        #   看着像「3000 出现两次」。网格是给模型当参考系的，标错比不标
        #   更糟 —— 那是在给一个错误的坐标系发信号。
        gx, gy = at(x0 + i * GRID_M, z0)
        d.text((min(gx + 4, out.width - 52), max(2, gy - 18)),
               f"X={x0 + i * GRID_M:.0f}", fill=(255, 235, 60), font=f,
               stroke_width=2, stroke_fill=(0, 0, 0))
        gx, gy = at(x0, z0 + i * GRID_M)
        d.text((min(gx + 4, out.width - 52), max(2, gy - 18)),
               f"Z={z0 + i * GRID_M:.0f}", fill=(255, 235, 60), font=f,
               stroke_width=2, stroke_fill=(0, 0, 0))
    return out


VARIANTS = (("A", "原始彩色", lambda i, p: i.copy()),
            ("B", "纯灰度", v_b_gray),
            ("C", "二值化+边缘", v_c_binary),
            ("D", "降分辨率256", v_d_blur),
            ("E", "网格+坐标标注", v_e_grid))


# ------------------------------------------------------------------ 调用
def _send(b64, model, key, effort, t0):
    """发一次。``effort`` 为空串 = 不带 reasoning_effort 字段。"""
    payload = {"model": model, "max_tokens": MAX_TOKENS, "temperature": 0.0,
               "messages": [{"role": "user", "content": [
                   {"type": "text", "text": QUESTION},
                   {"type": "image_url", "image_url": {
                       "url": f"data:image/png;base64,{b64}"}}]}]}
    if effort:
        payload["reasoning_effort"] = effort
    req = urllib.request.Request(
        ENDPOINT, method="POST", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        raw = r.read().decode("utf-8", "replace")
    return raw, time.monotonic() - t0


def call_api(b64, model, key):
    """返回 ``(content, reasoning, err, 秒, completion_tokens)``。"""
    global _REASONING_EFFORT_OK
    effort = "" if _REASONING_EFFORT_OK is False else REASONING_EFFORT
    t0 = time.monotonic()
    try:
        raw, dt = _send(b64, model, key, effort, t0)
    except urllib.error.HTTPError as e:
        try:
            det = e.read().decode("utf-8", "replace")[:300]
        except Exception:                                 # noqa: BLE001
            det = "(读不到)"
        # ★ 只有 400/422 且响应体**点名** reasoning_effort 才自愈。
        #   401(key 错)、429(限流) 重试一万次也一样，白烧一个来回。
        if e.code in (400, 422) and "reasoning_effort" in det and effort:
            _REASONING_EFFORT_OK = False
            print(f"  {_c('y')}⚠ 端点不认 reasoning_effort（HTTP {e.code}）"
                  f" —— 去掉后重试一次，本进程后续不再发送。{_c('x')}")
            try:
                raw, dt = _send(b64, model, key, "", t0)
            except urllib.error.HTTPError as e2:
                try:
                    d2 = e2.read().decode("utf-8", "replace")[:300]
                except Exception:                         # noqa: BLE001
                    d2 = "(读不到)"
                return None, "", f"HTTP {e2.code} {e2.reason} | {d2}", \
                    time.monotonic() - t0, 0
            except (urllib.error.URLError, TimeoutError) as e2:
                return None, "", f"网络/超时：{e2}", time.monotonic() - t0, 0
        else:
            return None, "", f"HTTP {e.code} {e.reason} | {det}", \
                time.monotonic() - t0, 0
    except (urllib.error.URLError, TimeoutError) as e:
        return None, "", f"网络/超时：{e}", time.monotonic() - t0, 0

    try:
        doc = json.loads(raw)
        ch, msg = doc["choices"][0], doc["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        return None, "", f"响应结构不符预期：{e}", dt, 0
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or ""
    print(f"  {_c('d')}[DEBUG] effort={effort or '(不发)'} "
          f"finish_reason={ch.get('finish_reason')!r}\n"
          f"  [DEBUG] content {len(content)} 字 / reasoning {len(reasoning)} 字"
          f"{_c('x')}")
    return content, reasoning, "", dt, \
        (doc.get("usage") or {}).get("completion_tokens", 0)


def parse_answer(text):
    """抠三个字段。**缺 has_obstacle 判 None** —— 半份答案不能当结论。"""
    if not isinstance(text, str) or "{" not in text:
        return None
    i, j = text.find("{"), text.rfind("}")
    try:
        d = json.loads(text[i:j + 1])
    except ValueError:
        return None
    if not isinstance(d, dict) or "has_obstacle" not in d:
        return None
    return {"has_obstacle": bool(d.get("has_obstacle")),
            "obstacle_direction": str(d.get("obstacle_direction") or "?"),
            "reason": str(d.get("reason") or "")}


def analyze(rows):
    """★ 核心判据：5 个变体的答案**一致还是不一致**。

    ★ 顺序有讲究：两个具体判据必须排在「完全一致」**前面**
      ---------------------------------------------------
      「都答无障碍」和「都答有障碍且同向」都是「5 个一致」的**特例**，
      但结论完全不同：前者说明图太模糊，后者说明模型看图有效。
      先判「一致」的话，这两个更有信息量的结论就永远出不来
      （实测：都答无障碍被报成「完全一致 -> 模型看到了内容」，
      这个结论是**反的**）。
    """
    got = [r for r in rows if r["ans"]]
    if len(got) < len(rows):
        return (f"只有 {len(got)}/{len(rows)} 个变体答了 —— 结果不可比，"
                f"先修错误再下结论。")
    pairs = {(r["ans"]["has_obstacle"], r["ans"]["obstacle_direction"])
             for r in got}
    if all(p[0] is False for p in pairs):
        return "都答「无障碍」-> 图太模糊（或根本没送到位），模型看不到障碍。"
    # 走到这里 pairs 里全是 has_obstacle=True；只有一个元组 = 方向也一致。
    if len(pairs) == 1:
        return (f"都答「有障碍」且方向一致（{next(iter(pairs))[1]}）-> "
                f"模型看图有效，只是输出有随机性。")
    dirs = {p[1] for p in pairs if p[0]}
    if len(dirs) == 1:
        return (f"都答「有障碍」且方向一致（{dirs.pop()}）-> "
                f"模型看图有效，只是随机。")
    return "答案各不相同 -> 模型在猜，图里的细节它压根没读进去。"


def main():
    global NO_COLOR
    ap = argparse.ArgumentParser(description="地形图简化度变体实验")
    ap.add_argument("--no-color", action="store_true", help="不输出 ANSI 颜色")
    ap.add_argument("--only", default=None, metavar="ABC",
                    help="只跑指定变体，如 --only A 或 --only A,C。"
                         "省调用：5 个变体一个一个试")
    args = ap.parse_args()
    NO_COLOR = args.no_color
    only = None
    if args.only:
        only = {s.strip().upper() for s in args.only.split(",") if s.strip()}
        bad = only - {t for t, _, _ in VARIANTS}
        if bad:
            print(f"{_c('r')}[!] --only 含未知变体 {sorted(bad)}；"
                  f"可选 {[t for t, _, _ in VARIANTS]}{_c('x')}",
                  file=sys.stderr)
            return 2
    key = os.environ.get(KEY_ENV, "").strip()
    model = os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL

    # ★ 变体生成在 key 检查**之前**（与 diag_llm_vision 同一条理由）：
    #   5 张图本身是有用产物。没 key 时也能打开逐张看，从而先排除
    #   「变体 C/D/E 画错了」这种变量，而不是直接卡在 exit 2。
    print("=== 准备 5 个变体 ===")
    pp = load_path_planner()
    base = base_image(pp)
    imgs = {}
    for tag, name, fn in VARIANTS:
        p = TMP / f"vision2_variant_{tag}.png"
        try:
            imgs[tag] = fn(base, pp)
            imgs[tag].save(p, format="PNG")
            print(f"  {tag} · {name:<14} {p.name}  {imgs[tag].width}x"
                  f"{imgs[tag].height}  {p.stat().st_size // 1024} KB")
        except Exception as exc:                          # noqa: BLE001
            print(f"  {_c('r')}{tag} 生成失败：{exc}{_c('x')}")
    if not key:
        print(f"\n{_c('r')}[!] 没设 {KEY_ENV}，变体已生成但无法调用。"
              f"{_c('x')}", file=sys.stderr)
        print(f"    PowerShell：$env:{KEY_ENV} = 'sk-...'", file=sys.stderr)
        return 2

    rows = []
    for i, (tag, name, _) in enumerate(VARIANTS):
        if tag not in imgs or (only and tag not in only):
            continue
        if rows:
            time.sleep(SLEEP_S)      # ★ 别连发：限流 + 服务端超时叠加
        buf = io.BytesIO()
        imgs[tag].save(buf, format="PNG", optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode()
        print(f"\n=== 变体 {tag}（{name}）  base64 {len(b64)} 字符 ===")
        content, reasoning, err, dt, ct = call_api(b64, model, key)
        ans = None if err else parse_answer(content)
        rows.append({"tag": tag, "name": name, "ans": ans, "err": err})
        if err:
            print(f"  {_c('r')}✗ {err}{_c('x')}")
            continue
        # ★ reasoning 每变体一个文件 —— 这正是要看的东西：模型到底在
        #   推理些什么、为什么 500（现 4000）token 全被吃掉。
        #   变体 A 的文件名用规格给的 vision2_A_reasoning.txt。
        if reasoning:
            rp = TMP / f"vision2_{tag}_reasoning.txt"
            rp.write_text(reasoning, encoding="utf-8")
            print(f"  {_c('y')}reasoning {len(reasoning)} 字已保存到 "
                  f"{rp}{_c('x')}")
        print(f"  content: {(content or '')[:220]}")
        if ans:
            print(f"  has_obstacle={ans['has_obstacle']}  "
                  f"direction={ans['obstacle_direction']}\n"
                  f"  reason={ans['reason']}")
        else:
            print(f"  {_c('r')}✗ 抠不出 JSON（reasoning 里可能有答案，"
                  f"翻上面的文件）{_c('x')}")
        print(f"  耗时 {dt:.1f}s / completion={ct} tokens")

    print("\n" + "=" * 66)
    print(f"{_c('b')}汇总（起点 {START} → 终点 {TARGET}）{_c('x')}")
    print("=" * 66)
    for r in rows:
        if r["ans"]:
            mark = "有障碍" if r["ans"]["has_obstacle"] else "无障碍"
            print(f"  {r['tag']} {r['name']:<14} {mark} · "
                  f"{r['ans']['obstacle_direction']:<4} · "
                  f"{r['ans']['reason'][:40]}")
        else:
            print(f"  {r['tag']} {r['name']:<14} {_c('r')}无有效答案{_c('x')}")
    print(f"\n{_c('b')}分析：{analyze(rows)}{_c('x')}")
    # ★ 别在**一个都没答上来**的时候返回 0。
    #   401 / 限流 / 解析全失败时，exit 0 会让「脚本跑完了」和「拿到结论
    #   了」看起来一模一样 —— 而这正是诊断脚本最不该搞混的两件事。
    failed = [r["tag"] for r in rows if r["err"]]
    if failed:
        print(f"{_c('r')}变体 {failed} 调用失败 —— 没有结论。{_c('x')}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
