"""传送 OCR 匹配单元测试（方案 A 语义：类型标签 + 地点）。

★ 与旧版的区别
--------------
实测信息框内容是 ``传送锚点`` + ``枫丹 枫丹廷·灰河``。
tp.json 里 777 个传送锚点的 ``name`` 就叫 ``传送锚点``，
所以"主标题=类型标签、副标题=地点"天然就是联合主键匹配。
真正要防的是**噪声行**（``8ms`` / ``X`` / ``!``）混进来当主标题。
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
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MOD = (Path(PLUGIN_DIR.parent)
       / "genshin_game_buddy" / "autopilot" / "perception" / "teleport_ocr.py")
_s = importlib.util.spec_from_file_location("teleport_ocr", MOD)
to = importlib.util.module_from_spec(_s)
sys.modules["teleport_ocr"] = to
_s.loader.exec_module(to)

PTS = to.load_teleport_points()
TW = [p for p in PTS if p["name"] == "传送锚点"]
print(f"候选表 {len(PTS)} 点，其中 name=='传送锚点' 的 {len(TW)} 个")

# 挑一个"名字唯一性够"的样本：其 areas 组合在库里只出现一次
SAMPLE, SUB = None, None
for p in TW:
    for a in p["areas"]:
        cand = f"枫丹{p['areas'][0]}" if p["areas"] else ""
        if a and len(a) >= 3:
            same = [q for q in TW if a in q["areas"]]
            if len(same) == 1:
                SAMPLE, SUB = p, a
                break
    if SAMPLE:
        break
if SAMPLE is None:
    SAMPLE = TW[0]
    SUB = SAMPLE["areas"][0]
print(f"测试素材: id={SAMPLE['id']} areas={SAMPLE['areas']}  副标题用 {SUB!r}")


def t1_exact():
    r = to.match_teleport("传送锚点", SUB, PTS)
    ok = r is not None and r.matched_id == SAMPLE["id"]
    print(f"\n[T1] 类型+地点 精确命中")
    print(f"  main='传送锚点'  sub={SUB!r}")
    print(f"  -> {r.matched_id if r else None}  conf={r.confidence if r else '-'}")
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


def t2_main_typo():
    r = to.match_teleport("传送铺锚点", SUB, PTS)
    ok = r is not None and r.matched_id == SAMPLE["id"]
    print(f"\n[T2] 主标题错 1 字（'传送铺锚点'）")
    print(f"  -> {r.matched_id if r else None}  conf={r.confidence if r else '-'}")
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


def t3_sub_typo():
    if len(SUB) < 2:
        return True
    typo = SUB[:-1] + "X"
    r = to.match_teleport("传送锚点", typo, PTS)
    ok = r is not None and r.matched_id == SAMPLE["id"]
    print(f"\n[T3] 副标题错 1 字")
    print(f"  -> {r.matched_id if r else None}  conf={r.confidence if r else '-'}")
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


def t4_total_garbage():
    r = to.match_teleport("乱码乱码", "完全不存在的地方名", PTS)
    ok = r is None
    print(f"\n[T4] 全错 -> None")
    print(f"  -> {r}   {'PASS' if ok else 'FAIL'}")
    return ok


def t5_noise_filter():
    raw = ["!", "8ms", "X", "传送锚点", "枫丹 枫丹廷·灰河", "12 ms"]
    clean = to._clean_lines(raw)
    ok = ("8ms" not in clean and "X" not in clean and "!" not in clean
          and "传送锚点" in clean
          and any("枫丹" in c for c in clean))
    print(f"\n[T5] 噪声行过滤（★ 这是旧版 0% 命中的真凶）")
    print(f"  输入 {raw}")
    print(f"  过滤后 {clean}")
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    # 再验证：用过滤后的行做主副，顺序无关
    a = to.match_teleport(clean[-1], clean[0], PTS)
    b = to.match_teleport(clean[0], clean[-1], PTS)
    print(f"  顺序 A(地点->类型) 命中: {a.matched_id if a else None}")
    print(f"  顺序 B(类型->地点) 命中: {b.matched_id if b else None}")
    return ok and (a is not None or b is not None)


def main():
    print("=" * 70)
    print(f"编辑距离 {to.MATCH_EDIT_DISTANCE}  相似度 {to.MATCH_SIMILARITY}")
    print("=" * 70)
    res = [("T1 精确命中", t1_exact()),
           ("T2 主标题错1字", t2_main_typo()),
           ("T3 副标题错1字", t3_sub_typo()),
           ("T4 全错", t4_total_garbage()),
           ("T5 噪声过滤", t5_noise_filter())]
    print("\n" + "=" * 70)
    allok = True
    for nm, ok in res:
        print(f"{nm:<18}{'PASS' if ok else 'FAIL':>8}")
        allok &= ok
    print("=" * 70)
    print("全部通过" if allok else "有测试失败")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
