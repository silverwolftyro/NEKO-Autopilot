"""向后兼容：旧 .anchors.json（用 "anchors" 键）必须还能读。"""

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
import sys

# ---- console encoding ----------------------------------------------
# chcp 65001 only changes the console code page; Python still writes bytes
# in its own encoding (cp936/GBK on Chinese Windows), so every Chinese
# string in this script comes out as mojibake. Force both streams UTF-8.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):   # redirected to StringIO -> no method
        _s.reconfigure(encoding="utf-8", errors="replace")
import importlib.util, json, sys, types
from pathlib import Path

MOD = RECOG_ROOT / 'exports' / 'benchmark' / 'scripts' / 'misc' / 'anchors_viewer.py'
WORK = Path(_pl.Path(_os.environ.get("TEMP") or _os.environ.get("TMP") or ".") / 'dsh-T5Dl1o')
spec = importlib.util.spec_from_file_location("anchors_viewer", MOD)
m = importlib.util.module_from_spec(spec); sys.modules["anchors_viewer"] = m
spec.loader.exec_module(m)

fails = []
def ok(c, msg):
    print(("  PASS  " if c else "  FAIL  ") + msg, flush=True)
    if not c: fails.append(msg)

def fresh(path):
    o = types.SimpleNamespace(out_path=path, anchors=[], anchor_seq=0,
                              _save_note="", _dirty=False, read_only=False,
                              _update_info=lambda *a, **k: None,
                              _flash=lambda *a, **k: None)
    # SimpleNamespace 不做描述符绑定，得手工把 self 传进去
    o.build_payload = lambda: m.AnchorsViewer.build_payload(o)
    return o

def write(name, obj):
    p = WORK / name
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return p

# 1. 旧格式：只有 anchors 键、没有 image_size / saved_at
old = write("_old.json", {"anchors": [{"name": "a", "x": 1, "y": 2},
                                      {"name": "b", "x": 3, "y": 4}]})
o = fresh(old); m.AnchorsViewer._load_existing_anchors(o)
ok([(a.name, a.x, a.y) for a in o.anchors] == [("a", 1, 2), ("b", 3, 4)],
   f"旧格式(anchors 键)可读：{[(a.name,a.x,a.y) for a in o.anchors]}")
ok(o.anchor_seq == 2, "anchor_seq 接续旧点数，新点不会重名")

# 2. 新格式：points 键
new = write("_new.json", {"image": "x", "image_size": [10, 10],
                          "saved_at": "t", "points": [{"name": "c", "x": 5, "y": 6}]})
o = fresh(new); m.AnchorsViewer._load_existing_anchors(o)
ok([(a.name, a.x, a.y) for a in o.anchors] == [("c", 5, 6)], "新格式(points 键)可读")

# 3. 空文件 / 坏 JSON / 缺文件：warn 但不崩，锚点为空
for name, content in (("_empty.json", ""), ("_bad.json", "{ 不是 json"),
                      ("_nopts.json", '{"image":"x"}')):
    p = WORK / name
    p.write_text(content, encoding="utf-8")
    o = fresh(p)
    try:
        m.AnchorsViewer._load_existing_anchors(o)
        ok(o.anchors == [], f"{name}: 容错为空，不崩")
    except Exception as e:
        ok(False, f"{name}: 抛异常 {e!r}")

o = fresh(WORK / "_nonexistent_xyz.json")
m.AnchorsViewer._load_existing_anchors(o)
ok(o.anchors == [], "文件不存在：不崩")

# 4. 写出去的新文件，键名和旧版一致（points 一直是旧版就有的键）
o = fresh(WORK / "_fmt.json"); o.image_path = Path("p.png")
o.source = types.SimpleNamespace(width=8, height=9)
m.AnchorsViewer.save(o, auto=True)
d = json.loads((WORK / "_fmt.json").read_text(encoding="utf-8"))
ok(set(d) == {"image", "image_size", "saved_at", "points"},
   f"写出格式与旧版一致：{sorted(d)}")
ok(not (WORK / "_fmt.json.tmp").exists(), "无残留 .tmp")

print("=" * 50)
print("FAILED" if fails else "=== BACKWARD-COMPAT PASSED ===")
sys.exit(1 if fails else 0)
