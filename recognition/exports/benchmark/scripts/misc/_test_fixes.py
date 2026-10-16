"""anchors_viewer 修复后的集成自测：真开 Tk 窗口，直接驱动事件处理函数。

覆盖：左键拖动 vs 单击的状态机、自动保存+原子写、缩放上限、
      >100% 的 NEAREST、关窗后回调不再崩。
"""

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
import importlib.util, json, os, sys, time, types, glob
from pathlib import Path
from PIL import Image

MOD = RECOG_ROOT / 'exports' / 'benchmark' / 'scripts' / 'misc' / 'anchors_viewer.py'
WORK = Path(_pl.Path(_os.environ.get("TEMP") or _os.environ.get("TMP") or ".") / 'dsh-T5Dl1o')

spec = importlib.util.spec_from_file_location("anchors_viewer", MOD)
m = importlib.util.module_from_spec(spec)
sys.modules["anchors_viewer"] = m
spec.loader.exec_module(m)
import tkinter as tk
print("import OK", flush=True)

ok = lambda c, msg: print(("  PASS  " if c else "  FAIL  ") + msg, flush=True) or c
fails = []

# ============================================================ A. 纯逻辑
print("\n=== A. _filter_for / _pick_level ===", flush=True)
fails.append(not ok(m.ImageSource._filter_for(100, 200, nearest=True)
                    == Image.Resampling.NEAREST, "nearest=True -> NEAREST"))
fails.append(not ok(m.ZOOM_MAX == 2.0, f"ZOOM_MAX == 2.0 (实际 {m.ZOOM_MAX})"))

# ============================================================ B. 小图端到端渲染
print("\n=== B. >100% NEAREST 像素级放大 ===", flush=True)
small = WORK / "_av_small.png"
# 1 像素黑白棋盘：NEAREST 放大后只该有 2 种颜色；插值会造出一堆灰
im = Image.new("RGB", (200, 100))
px = im.load()
for y in range(100):
    for x in range(200):
        px[x, y] = (255, 255, 255) if (x + y) % 2 == 0 else (0, 0, 0)
im.save(small)

src = m.ImageSource(small)
src.load(lambda s: None)
print("  级别:", [(round(s, 4), i.size) for s, i in src.levels], flush=True)

lv = src._pick_level(200, 100, 200 * 2.0)   # zoom=2 -> 修复前会选到最粗一级
fails.append(not ok(lv is src.img,
                    f"zoom=2 选到全分辨率（实际 {lv.size}）"))

out = src.render(m.View(token=1, x0=0, y0=0, zoom=2.0, cw=400, ch=200))
colors = out.getcolors(maxcolors=100000)
print(f"  2x 放大后颜色数 = {len(colors)}", flush=True)
fails.append(not ok(len(colors) <= 4,
                    f"NEAREST 放大后颜色数很少（{len(colors)} <= 4，插值会远大于此）"))
fails.append(not ok(out.getpixel((0, 0)) in ((255, 255, 255), (0, 0, 0)),
                    "放大后仍是纯黑白，没有灰边"))

# ============================================================ C. 真窗口 + 事件驱动
print("\n=== C. 真窗口集成 ===", flush=True)
big = Path(RECOG_ROOT / 'data' / 'mihoyo_maps' / 'teyvat_P0.png')
out_path = WORK / "_av_anchors.json"
for f in glob.glob(str(WORK / "_av_anchors.json*")):
    os.remove(f)

root = tk.Tk()
app = m.AnchorsViewer(root, big, out_path, read_only=False)
t0 = time.time()
while not app.ready and time.time() - t0 < 120:
    root.update()
    time.sleep(0.005)
fails.append(not ok(app.ready, f"底图加载完成（{time.time()-t0:.1f}s）"))

Ev = lambda x, y: types.SimpleNamespace(x=x, y=y)
m.simpledialog.askstring = lambda *a, **k: "锚点A"   # 别弹真对话框

# --- C1. 左键拖动 = 拖地图，且不标点 ---
before_cx, before_cy = app.cx, app.cy
n0 = len(app.anchors)
app._on_lpress(Ev(600, 400))
for i in range(1, 6):
    app._on_ldrag(Ev(600 + i * 20, 400 + i * 10))
dragged_cx, dragged_cy = app.cx, app.cy
app._on_lrelease(Ev(700, 450))
root.update()
moved = abs(dragged_cx - before_cx) > 1
exp_cx = before_cx - 100 / app.zoom
fails.append(not ok(moved, f"拖动改变了视图中心（{before_cx:.0f} -> {dragged_cx:.0f}）"))
fails.append(not ok(abs(dragged_cx - exp_cx) < 1.0,
                    f"拖动量正确，跟手（期望 {exp_cx:.1f}，实际 {dragged_cx:.1f}）"))
fails.append(not ok(len(app.anchors) == n0, "拖动后【没有】标点"))

# --- C2. 左键单击（≤4px）= 标点 + 自动保存 ---
app._on_lpress(Ev(600, 400))
app._on_ldrag(Ev(602, 401))          # 2px，在阈值内
app._on_lrelease(Ev(602, 401))
root.update()
fails.append(not ok(len(app.anchors) == n0 + 1,
                    f"单击后标了 1 个点（{n0} -> {len(app.anchors)}）"))
fails.append(not ok(out_path.exists(), "自动保存已写盘（没按 S）"))
data = json.loads(out_path.read_text(encoding="utf-8"))
fails.append(not ok(len(data["points"]) == 1, "落盘内容 = 1 个点"))
fails.append(not ok(set(data) == {"image", "image_size", "saved_at", "points"},
                    f"JSON 字段未变、向后兼容：{sorted(data)}"))
fails.append(not ok(data["image_size"] == [36864, 18432], "image_size 正确"))
fails.append(not ok(list(WORK.glob("_av_anchors.json.tmp")) == [],
                    "原子写没有留下 .tmp"))
fails.append(not ok(app._dirty is False and app._save_note.startswith("已保存"),
                    f"状态栏标记 = {app._save_note!r}"))

# --- C3. 撤销也自动保存 ---
app.undo()
root.update()
data = json.loads(out_path.read_text(encoding="utf-8"))
fails.append(not ok(len(data["points"]) == 0 and len(app.anchors) == 0,
                    "撤销后盘上也同步为 0 个点"))

# --- C4. 缩放上限 200% ---
for _ in range(60):
    app._zoom_at(600, 400, 1.18)
root.update()
fails.append(not ok(abs(app.zoom - 2.0) < 1e-9,
                    f"缩放上限 = 200%（实际 {app.zoom*100:.1f}%）"))
for _ in range(60):
    app._zoom_at(600, 400, 1 / 1.18)
root.update()
fit = app._fit_zoom()
fails.append(not ok(abs(app.zoom - fit * m.ZOOM_MIN_FACTOR) < 1e-9,
                    f"缩放下限保持 fit*{m.ZOOM_MIN_FACTOR}"))

# --- C5. 保存失败：只打日志、不抛、不弹窗 ---
app.out_path = Path(RECOG_ROOT / 'exports' / 'benchmark' / 'scripts' / 'misc' / '_nope.json')
app.anchors.append(m.Anchor("x", 1, 2))
try:
    r = app.save(auto=True)
    root.update()
    fails.append(not ok(r is False and app._save_note == "保存失败!",
                        f"保存失败被吞掉并标记（note={app._save_note!r}）"))
except Exception as e:
    fails.append(not ok(False, f"保存失败不应抛异常，却抛了 {e!r}"))
app.anchors.pop()
app.out_path = out_path

# --- C6. 关窗后回调不再崩 ---
destroyed_trace = None
app.quit()
fails.append(not ok(app._closed, "quit() 置位 _closed"))
fails.append(not ok(len(app._after_ids) == 0,
                    f"quit() 取消了全部 after（剩 {len(app._after_ids)}）"))
try:
    app._flash("after destroy")      # 复现原始崩溃路径
    app._update_info()
    app._set_hint("x")
    app._redraw_anchors()
    app._on_motion(Ev(10, 10))
    app.save(auto=True)
    fails.append(not ok(True, "销毁后调 _flash/_update_info/save 均不抛异常"))
except Exception as e:
    destroyed_trace = e
    fails.append(not ok(False, f"销毁后回调仍崩：{type(e).__name__}: {e}"))

# --- C7. root.destroy() 之后再来一次 quit() 也不崩 ---
try:
    app.quit()
    fails.append(not ok(True, "重复 quit() 安全"))
except Exception as e:
    fails.append(not ok(False, f"重复 quit() 崩了：{e!r}"))

if small.exists():
    small.unlink()

print("\n" + "=" * 60, flush=True)
if fails and any(fails):
    print(f"!!! {sum(1 for f in fails if f)} 项失败", flush=True)
    sys.exit(1)
print("=== ALL CHECKS PASSED ===", flush=True)
