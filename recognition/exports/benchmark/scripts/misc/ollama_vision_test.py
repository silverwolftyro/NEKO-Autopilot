"""Ollama 本地视觉模型能力边界探测（2026-10-06）。

★ 定位：不是「全自动路径规划」，是**预标注可行性**评估
  --------------------------------------------------
  云端 LLM 那条路已经证伪（底图是美术图，不含可通行性信息）。本脚本
  问的是另一个问题：**本地模型在图上到底能看见多少** —— 如果 L1 都过不
  了，本地这条路连辅助标注都做不了；如果 L1/L2 能过而 L3 过不了，
  那它适合做「粗粒度地标标注」，不适合做「路径可通行性判断」。

三层由易到难：

======  ==================================  =========================
层      问什么                              预期
======  ==================================  =========================
L1      红块/绿块在哪个角？中间什么形状？    连本地小模型也该答对
L2      256x256 小图里有什么？              粗粒度分类本地应该能答
L3      红点到绿点之间有山/水要绕吗？        可能和云端一样含糊
======  ==================================  =========================

用法（本地免费，不需要任何 key，也**没有 --real**）::

    python ollama_vision_test.py                     # 跑全部三层
    python ollama_vision_test.py --layers L1 L3      # 只跑某几层
    python ollama_vision_test.py --model qwen2.5vl:7b
    python ollama_vision_test.py 2> ollama_test.log # 留诊断
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:                                   # pragma: no cover
    pass

MISC = Path(__file__).resolve().parent
TMP = Path(os.environ.get("TEMP", "."))
sys.path.insert(0, str(MISC))


def _load(alias: str, path: Path, quiet: bool = True):
    """按文件加载同目录脚本。stderr 压掉（它们 import 期会打警告）。"""
    spec = importlib.util.spec_from_file_location(alias, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[alias] = m
    buf = io.StringIO()
    if quiet:
        with redirect_stderr(buf):
            spec.loader.exec_module(m)
    else:
        spec.loader.exec_module(m)
    return m


oc = _load("ollama_client", MISC / "ollama_client.py")

# ---------------------------------------------------------------- 题目
L1_QUESTION = ("这张图里：红方块在哪个角？绿方块在哪个角？中间是什么形状？"
               "请直接回答三个问题的答案。")
L1_ANSWER = {"红方块": "左上", "绿方块": "右下", "中间": "加号"}

L2_QUESTION = "这张图里有什么？请从城市、水域、山地、森林里选，并简单描述位置。"
#: 蒙德城区域的传送锚点（取自 _ref_tp_sample.json 的 areas 含「蒙德城」那一条）。
#: ★ 不猜坐标 —— 随便编一个「蒙德城附近」大概率裁到海里，测的就不是蒙德城了。
L2_CENTER = (2277.6, -876.1)
L2_HALF_M = 90.0            # 半边长（米）。~180m 跨度 ≈ 250px，再缩到 256。

L3_QUESTION = ("这是原神地图的一块区域。红点=起点，绿点=终点。"
               "从红点到绿点之间，有没有明显的山或水域需要绕行？"
               "有的话指出大致方向，没有就说\"无障碍\"。")
L3_START, L3_TARGET = (1000.0, 1000.0), (3000.0, 3000.0)   # 与云端失败那次同区域

LAYERS = ("L1", "L2", "L3")


def hr(t: str) -> None:
    print()
    print("=" * 70)
    print(t)
    print("=" * 70)


# ---------------------------------------------------------------- L1
def build_l1() -> Path:
    """复用 diag_llm_vision.py 的合成图（红块左上 / 绿块右下 / 加号居中）。"""
    dv = _load("diag_vision_for_l1", MISC / "diag_llm_vision.py")
    im = dv.build_image()
    p = TMP / "ollama_L1_synthetic.png"
    im.save(p, format="PNG")
    return p


# ---------------------------------------------------------------- L2 / L3
_pp = None


def _planner():
    """懒加载 path_planner（要 numpy + PIL + coord_transform，启动慢）。"""
    global _pp
    if _pp is None:
        _pp = _load("path_planner_for_viz", MISC / "path_planner.py")
    return _pp


def build_l2() -> Path:
    """蒙德城附近的 256x256 小图。"""
    pp = _planner()
    cx, cz = L2_CENTER
    start = (cx - L2_HALF_M, cz - L2_HALF_M)
    target = (cx + L2_HALF_M, cz + L2_HALF_M)
    box = pp.crop_box_px(start, target)
    img, _ = pp.read_crop(box)
    img = img.resize((256, 256), getattr(__import__("PIL.Image", fromlist=["Image"]),
                                          "LANCZOS", 256))
    p = TMP / "ollama_L2_mondstadt.png"
    img.save(p, format="PNG")
    return p


def build_l3() -> Path:
    """复杂地形：与云端失败那次**同一片区域**、同一张图。"""
    pp = _planner()
    box = pp.crop_box_px(L3_START, L3_TARGET)
    img, _ = pp.read_crop(box)
    s = pp.MAX_EDGE / float(max(img.width, img.height))
    if s < 1.0:
        img = img.resize((int(img.width * s), int(img.height * s)),
                         __import__("PIL.Image", fromlist=["Image"]).LANCZOS)
    img = pp.annotate(img, box, L3_START, L3_TARGET)
    p = TMP / "ollama_L3_terrain.png"
    img.save(p, format="PNG")
    return p


# ---------------------------------------------------------------- 判定
def judge_l1(text: str) -> str:
    """按关键词粗判，**不装智能**。判不准就直说「看不出」。"""
    t = text
    checks = {
        "红方块": ("左上" in t),
        "绿方块": ("右下" in t),
        "中间": ("加号" in t or "十字" in t or "+" in t),
    }
    ok = sum(1 for v in checks.values() if v)
    if ok == len(checks):
        return "看起来对（3/3）"
    if ok == 0:
        return "看起来错（0/3）"
    bad = [k for k, v in checks.items() if not v]
    return f"部分对（{ok}/3，疑似 {'、'.join(bad)} 没答对）"


def judge_open(text: str) -> str:
    """开放题的粗判：有没有实质内容。**不假装能自动判定对错。**"""
    n = len(text.strip())
    if n == 0:
        return "看不出（空回答）"
    if n < 10:
        return "看不出（回答过短，多半在敷衍）"
    return "有实质回答 —— **对错请你人工看上面那段原文**"


def run_layer(tag: str, img: Path, question: str, judge, *,
              model: str, timeout: float) -> dict:
    print(f"\n--- {tag} ---")
    print(f"  图片 {img}  ({img.stat().st_size // 1024} KB)")
    print(f"  提问：{question}")
    print(f"  模型：{model}")
    print("  等待模型…")
    sys.stdout.flush()
    # ★ model / timeout 必须**逐层传下去**。曾经只传给了上面的
    #   service 检查，chat_vision 仍在用自己的默认值 —— 于是
    #   `--model qwen2.5:7b` 检查说「就位」、实际请求却发给了
    #   qwen2.5vl:7b，报回来的是 404 model not found。
    #   「检查用的模型」和「发送用的模型」不一致，是最难查的一类错。
    text, why, dt = oc.chat_vision(str(img), question, model=model,
                                  timeout=timeout)
    print(f"  耗时 {dt:.1f}s")
    if text is None:
        print(f"  ✗ 调用失败：{why}")
        return {"tag": tag, "ok": False, "text": "", "why": why, "dt": dt}
    for ln in text.splitlines():
        if ln.strip():
            print(f"  │ {ln}")
    print(f"  判断：{judge(text)}")
    return {"tag": tag, "ok": True, "text": text, "why": "", "dt": dt}


def main() -> int:
    ap = argparse.ArgumentParser(description="Ollama 本地视觉模型能力探测")
    ap.add_argument("--model", default=oc.DEFAULT_MODEL,
                    help=f"模型名（默认 {oc.DEFAULT_MODEL}）")
    ap.add_argument("--layers", nargs="+", default=list(LAYERS),
                    choices=list(LAYERS), help="只跑指定层")
    ap.add_argument("--timeout", type=float, default=oc.CHAT_TIMEOUT_S,
                    help="每层超时秒数（默认 180）")
    args = ap.parse_args()

    # ---- 任务 D：先探活，再谈别的 ----
    hr("0 · Ollama 服务检查")
    ok, why, names = oc.service_status()
    if not ok:
        print(f"  ✗ Ollama 服务不可用：{why}")
        print(f"    请先运行：ollama serve")
        print(f"    （或确认 {oc.BASE} 上确实有 Ollama）")
        return 2
    print(f"  ✓ 服务在跑：{oc.BASE}")
    print(f"  已装模型 {len(names)} 个：{names}")
    if not oc.model_present(names, args.model):
        print(f"  ✗ 没找到视觉模型 {args.model!r}")
        print(f"    请先运行：ollama pull {args.model}")
        print(f"    已有的是：{names}")
        return 2
    print(f"  ✓ 视觉模型就位：{args.model}")

    # ---- 造图 ----
    hr("1 · 准备三张测试图")
    try:
        builders = {"L1": build_l1, "L2": build_l2, "L3": build_l3}
        imgs = {}
        for tag in args.layers:
            imgs[tag] = builders[tag]()
            print(f"  {tag}: {imgs[tag]}  "
                  f"({imgs[tag].stat().st_size // 1024} KB)")
    except Exception as exc:                           # noqa: BLE001
        print(f"  ✗ 造图失败：{type(exc).__name__}: {exc}")
        return 2

    # ---- 逐层问 ----
    hr("2 · 逐层提问")
    results = []
    for tag, q, jd in (("L1", L1_QUESTION, judge_l1),
                        ("L2", L2_QUESTION, judge_open),
                        ("L3", L3_QUESTION, judge_open)):
        if tag not in imgs:
            continue
        results.append(run_layer(tag, imgs[tag], q, jd,
                                model=args.model, timeout=args.timeout))

    # ---- 汇总 ----
    hr("3 · 结论")
    for r in results:
        if not r["ok"]:
            print(f"  {r['tag']}: 调用失败 —— {r['why'][:60]}")
        elif r["tag"] == "L1":
            print(f"  {r['tag']}: {judge_l1(r['text'])}  ({r['dt']:.0f}s)")
        else:
            print(f"  {r['tag']}: {judge_open(r['text'])}  ({r['dt']:.0f}s)")
    print()
    print("  怎么读这个结果：")
    print("   · L1 都过不了 -> 本地模型连红绿点都分不清，**预标注也做不了**，")
    print("     先换模型（L1 这么简单的题过不了不像是「复杂地形难」）。")
    print("   · L1 过、L2 过、L3 含糊 -> 可以做**粗粒度地标预标注**，")
    print("     但**不能**指望它判断路径可通行性 —— 预标注要人复核。")
    print("   · 三层全含糊 -> 和云端一个毛病，别在模型上再花时间了。")
    print()
    print("  ★ L2/L3 的对错**请人工看原文**：脚本不会假装能自动判分。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
