"""验证：键盘快捷键已全部删除，底部按钮栏替代其功能。"""

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
import importlib.util, json, sys, time, types
from pathlib import Path
from PIL import Image

MOD = RECOG_ROOT / 'exports' / 'benchmark' / 'scripts' / 'misc' / 'anchors_viewer.py'
WORK = Path(_pl.Path(_os.environ.get("TEMP") or _os.environ.get("TMP") or ".") / 'dsh-T5Dl1o')
spec = importlib.util.spec_from_file_location("anchors_viewer", MOD)
m = importlib.util.module_from_spec(spec); sys.modules["anchors_viewer"] = m
spec.loader.exec_module(m)
import tkinter as tk

fails = []
def ok(c, msg):
    print(("  PASS  " if c else "  FAIL  ") + msg, flush=True)
    if not c: fails.append(msg)

# 小图，跑得快
img = WORK / "_nk.png"
im = Image.new("RGB", (4000, 2000), (40, 60, 80))
for i in range(0, 4000, 250):
    for j in range(0, 2000, 250):
        for y in range(j, min(j + 250, 2000)):
            for x in range(i, min(i + 250, 4000)):
                im.putpixel((x, y), ((i // 250 * 31) % 256, (j // 250 * 47) % 256, 90))
im.save(img)

out = WORK / "_nk.json"
if out.exists(): out.unlink()

root = tk.Tk()
app = m.AnchorsViewer(root, img, out, read_only=False)
t0 = time.time()
while not app.ready and time.time() - t0 < 90:
    root.update(); time.sleep(0.005)
ok(app.ready, f"加载完成（{time.time()-t0:.1f}s）")

# ---------- 1. 没有任何全局/键盘绑定 ----------
allb = tuple(root.bind_all() or ())
print(f"  root.bind_all() -> {allb}", flush=True)
# 注意：空的 tk.Tk() 本身就带一批 Tk 内建的 all-tag 绑定
# （<<PrevWindow>> / <Key-F10> / <Alt-Key> 之类，标签切换和菜单键）。
# 所以不能断言"为空"，要断言"我们要删的那几个一个都不在"。
tk_builtin = {"<<PrevWindow>>", "<<NextWindow>>", "<KeyRelease-F10>", "<Key-F10>",
              "<Alt-KeyRelease>", "<Alt-Key>", "<KeyRelease-Alt_R>", "<Key-Alt_R>",
              "<KeyRelease-Alt_L>", "<Key-Alt_L>"}
ok(set(allb) <= tk_builtin, f"all-tag 上只剩 Tk 内建绑定，没有应用加的：{sorted(set(allb)-tk_builtin)}")

removed = ["<Key-s>", "<Key-S>", "<Key-r>", "<Key-R>", "<Key-z>", "<Key-Z>",
           "<Key-q>", "<Key-Q>", "<KeyPress-s>", "<KeyPress-z>", "<Escape>"]
still = [s for s in removed if s in allb]
ok(not still, f"已删掉的快捷键一个都不在 all-tag 上（残留：{still}）")
ok(tuple(root.bind() or ()) == (), f"root 控件级也没有绑定：{root.bind()}")

canv = tuple(app.canvas.bind() or ())
bad = [s for s in canv if "Key" in s or "Escape" in s]
print(f"  canvas 绑定 -> {canv}", flush=True)
ok(not bad, f"canvas 上没有键盘绑定（{len(canv)} 个鼠标事件绑定保留）")
for must in ("<Motion>", "<Button-1>", "<B1-Motion>", "<ButtonRelease-1>",
             "<Button-2>", "<B2-Motion>", "<MouseWheel>", "<Configure>"):
    ok(must in canv, f"鼠标绑定保留：{must}")

# ---------- 2. 底部按钮栏 ----------
def all_buttons(w):
    out = []
    for c in w.winfo_children():
        if isinstance(c, tk.Button): out.append(c)
        out.extend(all_buttons(c))
    return out

btns = all_buttons(root)
texts = [b.cget("text") for b in btns]
print(f"  按钮 -> {texts}", flush=True)
ok(len(btns) == 4, f"底部有 4 个按钮（实际 {len(btns)}）")
ok(texts == ["重置视图", "撤销", "保存", "退出"], f"按钮文字与顺序正确：{texts}")

# 布局：画布 / 按钮栏 / 状态栏 自上而下，别把按钮塞到看不见的地方
root.update_idletasks(); root.update()
cv, bf, sb = app.canvas, btns[0].master, app.lbl_info.master
geo = lambda w: (w.winfo_y(), w.winfo_height())
print(f"  几何 canvas={geo(cv)} 按钮栏={geo(bf)} 状态栏={geo(sb)}", flush=True)
ok(cv.winfo_y() + cv.winfo_height() <= bf.winfo_y() + 2,
   "按钮栏在画布下方")
ok(bf.winfo_y() + bf.winfo_height() <= sb.winfo_y() + 2,
   "状态栏在最底部（按钮栏在它上方）")
ok(bf.winfo_height() > 10 and bf.winfo_width() > 100,
   f"按钮栏真的占了地方：{bf.winfo_width()}x{bf.winfo_height()}")

# ---------- 3. 按钮真的能用 ----------
Ev = lambda x, y: types.SimpleNamespace(x=x, y=y)
m.simpledialog.askstring = lambda *a, **k: "钮点A"

app._on_lpress(Ev(500, 400)); app._on_lrelease(Ev(500, 400))
root.update()
ok(len(app.anchors) == 1, f"标点成功（{len(app.anchors)} 个）")
ok(out.exists(), "自动保存仍在（没按任何键）")

btns[1].invoke()          # 撤销
root.update()
ok(len(app.anchors) == 0 and json.loads(out.read_text("utf-8"))["points"] == [],
   "「撤销」按钮生效，且盘上同步")

app._on_lpress(Ev(500, 400)); app._on_lrelease(Ev(500, 400))
app._zoom_at(500, 400, 1.5)
root.update()
btns[0].invoke()          # 重置视图
root.update()
ok(abs(app.zoom - app._fit_zoom()) < 1e-9, "「重置视图」按钮生效（回到 fit）")

app._flash("")            # 清掉提示，便于看保存是否留下痕迹
btns[2].invoke()          # 保存
root.update()
ok(app._save_note.startswith("已保存"), f"「保存」按钮生效（{app._save_note}）")

# ---------- 4. 按键盘不该有任何反应（输入法场景的直证） ----------
n_before, zoom_before = len(app.anchors), app.zoom
for key in ("z", "Z", "s", "S", "r", "R", "q", "Q"):
    root.event_generate("<KeyPress>", keysym=key)
    root.update()
ok(len(app.anchors) == n_before, f"按 z/Z 不再撤销（锚点仍 {len(app.anchors)} 个）")
ok(abs(app.zoom - zoom_before) < 1e-12, "按 r/R 不再重置视图")
ok(not app._closed, "按 q/Q 不再退出程序")
ok(app.out_path.exists(), "按 s/S 不介入（本来就有自动保存）")

# Esc 也不该退出
root.event_generate("<KeyPress>", keysym="Escape"); root.update()
ok(not app._closed, "按 Esc 不再退出程序")

# ---------- 5. 关窗后按钮回调被吞掉 ----------
ok(app._alive(), "_alive() 关窗前为真")
app.quit()
ok(not app._alive(), "_alive() 关窗后为假")
try:
    for fn in (app.reset_view, app.undo, app.save, app.quit):
        app._button_cmd(fn)()      # 复现"关窗后残留按钮回调"
    ok(True, "关窗后调 4 个按钮回调均不抛异常")
except Exception as e:
    ok(False, f"关窗后按钮回调崩了：{type(e).__name__}: {e}")

for f in (img, out, WORK / "_nk.json.tmp"):
    try:
        if f.exists(): f.unlink()
    except OSError:
        pass

print("=" * 56, flush=True)
print(f"!!! {len(fails)} 项失败" if fails else "=== NO-KEYS TESTS PASSED ===", flush=True)
sys.exit(1 if fails else 0)
