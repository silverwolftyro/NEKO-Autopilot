"""无窗口自测：直接拿真实巨图跑 anchors_viewer 的渲染核心。

不写盘（本沙箱禁止受限进程写工作区），只读真实底图做端到端校验。
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
import importlib.util, json, sys, time
from pathlib import Path

MOD = RECOG_ROOT / 'exports' / 'benchmark' / 'scripts' / 'misc' / 'anchors_viewer.py'
PNG = Path(RECOG_ROOT / 'data' / 'mihoyo_maps' / 'teyvat_P0.png')

spec = importlib.util.spec_from_file_location("anchors_viewer", MOD)
m = importlib.util.module_from_spec(spec)
sys.modules["anchors_viewer"] = m
spec.loader.exec_module(m)
print("import OK ; 默认锚点路径 =",
      m.default_out_path(PNG).name, flush=True)

src = m.ImageSource(PNG)
t0 = time.perf_counter()
src.load(lambda s: print("   status:", s, flush=True))
print("真实底图 %dx%d  解码 %.1fs  金字塔 %.1fs  合计 %.1fs"
      % (src.width, src.height, src.decode_seconds, src.pyramid_seconds,
         time.perf_counter() - t0), flush=True)
print("金字塔级别:", [(round(s, 5), im.size) for s, im in src.levels], flush=True)

def render(x0, y0, zoom, cw, ch, tag):
    t = time.perf_counter()
    out = src.render(m.View(token=1, x0=x0, y0=y0, zoom=zoom, cw=cw, ch=ch))
    assert out.size == (cw, ch), out.size
    assert out.mode == "RGB", out.mode
    print("  %-26s %5.2fs  %s  中心像素=%s"
          % (tag, time.perf_counter() - t, out.size, out.getpixel((cw//2, ch//2))),
          flush=True)
    return out

print("\n--- 渲染 ---", flush=True)
render(0, 0, 1280/src.width, 1280, 800, "适应窗口(fit)")
render(18000, 9000, 1.0, 1280, 800, "1:1 全分辨率")
render(18000, 9000, 4.0, 1280, 800, "4x 放大")
render(18000, 9000, 0.3, 1280, 800, "0.3x")
render(36000, 18000, 2.0, 1280, 800, "右下角(夹边界)")
render(-50000, -50000, 1.0, 1280, 800, "完全在图外")
render(18432, 9216, 0.02, 1280, 800, "极度缩小")

print("\n--- mipmap 选级 ---", flush=True)
for zoom in (1.0, 0.5, 0.25, 0.125, 0.1, 0.0625, 0.04, 0.02, 0.01):
    need = 1280 / zoom
    lv = src._pick_level(need, need * 800/1280, need * zoom)
    kind = "全分辨率" if lv is src.img else "1/%d" % round(src.width / lv.width)
    print("  zoom=%-8s 需要原图宽 %-9.0f -> %s %s" % (zoom, need, kind, lv.size),
          flush=True)

# 图外视图应当整屏底色
out = src.render(m.View(token=1, x0=-90000, y0=-90000, zoom=1.0, cw=64, ch=64))
assert out.getpixel((1, 1)) == m.CANVAS_BG, out.getpixel((1, 1))
print("\n图外填充 =", out.getpixel((1, 1)), "期望", m.CANVAS_BG, flush=True)

# 边缘场景：图片比画布小 -> 应留底色（取角落）
out = src.render(m.View(token=1, x0=-2000, y0=-2000, zoom=0.02, cw=1280, ch=800))
corner = out.getpixel((1, 1)); center = out.getpixel((640, 400))
print("缩小到比画布小: 角落=%s 中心=%s (角落应为底色)" % (corner, center), flush=True)
assert corner == m.CANVAS_BG

# --- 覆盖 viewer 的坐标换算（不建 Tk 窗口，只测纯数学）---
class FakeViewer:
    canvas_to_source = m.AnchorsViewer.canvas_to_source
    _view_x0 = m.AnchorsViewer._view_x0
    _view_y0 = m.AnchorsViewer._view_y0
    _canvas_size = m.AnchorsViewer._canvas_size

fv = FakeViewer()
fv.zoom = 0.5
fv.cx, fv.cy = 10000.0, 5000.0
fv.source = src
class C:
    def winfo_width(self): return 1200
    def winfo_height(self): return 700
fv.canvas = C()
sx, sy = fv.canvas_to_source(600, 350)
print("\n坐标换算：画布中心(600,350) -> 原图 (%.1f, %.1f)  期望 (10000, 5000)" % (sx, sy), flush=True)
assert abs(sx - 10000) < 0.01 and abs(sy - 5000) < 0.01
sx, sy = fv.canvas_to_source(0, 0)
print("画布左上(0,0) -> 原图 (%.1f, %.1f)  期望 (8800, 4300)" % (sx, sy), flush=True)
assert abs(sx - 8800) < 0.01 and abs(sy - 4300) < 0.01

# --- 保存载荷（不写盘）---
fv.anchors = [m.Anchor("a1", 123, 456), m.Anchor("传送点", 36864-1, 18432-1)]
fv.image_path = PNG
fv.read_only = False
fv.source = src
payload = {"image": str(PNG), "image_size": [src.width, src.height],
           "saved_at": "x", "points": [{"name": a.name, "x": a.x, "y": a.y} for a in fv.anchors]}
txt = json.dumps(payload, ensure_ascii=False, indent=2)
print("\nJSON 载荷示例:\n" + txt, flush=True)

print("\n=== ALL CHECKS PASSED ===")
