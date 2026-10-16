"""扫描 Sam5440/Genshin_Impact_Teleport：许可 + 矿物坐标结构。

★ 只读
----
不修改仓库、不复制数据、不安装任何依赖。
只报告：许可原文、矿物类 JSON 清单、坐标数值分布。

★ 为什么先看许可
--------------
N.E.K.O. 是 Apache-2.0。**能不能用**取决于这个仓库的 LICENSE，
在确认之前不把它的坐标写进任何文件。
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

import json
import os
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(_REPO_ROOT.parent / 'Genshin_Impact_Teleport')
KEYWORDS = re.compile(r"mineral|ore|矿|水晶|晶体|crystal", re.IGNORECASE)


# ---------------------------------------------------------------- 许可
def probe_license() -> None:
    print("=" * 78)
    print("[1] 仓库根目录")
    if not ROOT.is_dir():
        print(f"!! 仓库不存在: {ROOT}")
        return
    for p in sorted(ROOT.iterdir()):
        kind = "DIR " if p.is_dir() else f"{p.stat().st_size:>9} B"
        print(f"  {kind}  {p.name}")

    print()
    print("[2] 许可文件")
    found = False
    for p in sorted(ROOT.iterdir()):
        if p.is_file() and re.search(r"licen|copying|notice",
                                     p.name, re.IGNORECASE):
            found = True
            txt = p.read_text(encoding="utf-8", errors="replace")
            print(f"  --- {p.name} ({len(txt)} 字符) ---")
            print("  " + "\n  ".join(txt.splitlines()[:24]))
    if not found:
        print("  !! 根目录没有任何 LICENSE / COPYING / NOTICE 文件")


# ---------------------------------------------------------------- 分布
def probe_layout() -> list[Path]:
    print()
    print("=" * 78)
    print("[3] 目录结构（两层）")
    hits: list[Path] = []
    for top in sorted(ROOT.iterdir()):
        if not top.is_dir():
            continue
        print(f"  [L1] {top.name}")
        for mid in sorted(top.iterdir())[:14]:
            n = 0
            try:
                n = sum(1 for _ in mid.iterdir())
            except OSError:
                pass
            print(f"        {mid.name}   (直接子项 {n})")
        for d in top.rglob("*.json"):
            if KEYWORDS.search(d.name):
                hits.append(d)
    return hits


# ---------------------------------------------------------------- 坐标
def numeric_triples(obj, out: list, budget: list[int]) -> None:
    """递归收集所有长度为 3 的纯数字列表 —— 坐标候选。"""
    if budget[0] <= 0:
        return
    budget[0] -= 1
    if isinstance(obj, dict):
        for v in obj.values():
            numeric_triples(v, out, budget)
    elif isinstance(obj, list):
        if (len(obj) == 3
                and all(isinstance(x, (int, float))
                        and not isinstance(x, bool) for x in obj)):
            out.append(tuple(float(x) for x in obj))
            return
        for v in obj:
            numeric_triples(v, out, budget)


def describe_json(p: Path) -> None:
    print()
    print("=" * 78)
    print(f"[5] 结构分析: {p.name}")
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception as exc:                    # noqa: BLE001
        print(f"  !! 解析失败: {exc}")
        return

    top = type(data).__name__
    print(f"  顶层: {top}")

    if isinstance(data, dict):
        print(f"  键数: {len(data)}")
        for i, (k, v) in enumerate(data.items()):
            if i >= 8:
                print(f"    ... 还有 {len(data) - 8} 个键")
                break
            if isinstance(v, list):
                print(f"    {k!r}: list x{len(v)}")
                if v and isinstance(v[0], (list, dict)):
                    print(f"        样本[0] = {v[0]}")
            else:
                print(f"    {k!r} = {v!r}")
    elif isinstance(data, list):
        print(f"  元素数: {len(data)}")
        for i, e in enumerate(data[:3]):
            print(f"    [{i}] = {e}")

    triples: list = []
    numeric_triples(data, triples, [200000])
    print()
    if not triples:
        print("  坐标: 未找到长度为 3 的纯数字数组")
        return
    print(f"  找到 {len(triples):,} 个三分量数值（坐标候选）")
    for ax, name in enumerate(("x", "y", "z")):
        col = [t[ax] for t in triples]
        print(f"    {name}: min={min(col):>12.2f}  max={max(col):>12.2f}")
    neg = sum(1 for t in triples if any(v < 0 for v in t))
    print(f"  含负值的坐标: {neg:,} / {len(triples):,}"
          f"  ({100.0 * neg / len(triples):.1f}%)")
    mx = max(max(abs(v) for v in t) for t in triples)
    print(f"  绝对值最大分量: {mx:.2f}")
    verdict = []
    if neg:
        verdict.append("有负值")
    if 100 <= mx <= 100000:
        verdict.append("量级符合世界坐标")
    if all(0 <= v <= 1 for t in triples for v in t):
        verdict.append("!! 疑似 0~1 归一化坐标（不是世界坐标）")
    if all(0 <= v <= 5000 and not neg for t in triples for v in t):
        verdict.append("?? 无负值且量级偏小，可能不是世界坐标")
    print(f"  判定: {' / '.join(verdict) if verdict else '特征不明确'}")


def main() -> None:
    probe_license()
    hits = probe_layout()
    print()
    print("=" * 78)
    print(f"[4] 文件名含 mineral/ore/矿/水晶/晶体/crystal 的 JSON: {len(hits)}")
    for p in hits[:60]:
        try:
            print(f"  {p.stat().st_size:>9} B  {p.relative_to(ROOT)}")
        except OSError:
            print(f"  {'':>9}   {p.relative_to(ROOT)}  (stat 失败)")
    if len(hits) > 60:
        print(f"  ... 还有 {len(hits) - 60} 个")

    if hits:
        # 优先挑体积最大的，信息量通常最足
        target = max(hits, key=lambda x: x.stat().st_size
                     if x.exists() else 0)
        describe_json(target)
    print()
    print("=== DONE ===")


if __name__ == "__main__":
    main()
