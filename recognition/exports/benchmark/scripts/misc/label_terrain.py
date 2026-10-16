#!/usr/bin/env python
"""地形可通行性标注工具（2026-10-08）—— 阶段 A。

★ 背景
------
原神美术图**不含可通行性信息**。通用 VL 模型（deepseek / qwen）全部证伪 ——
它们把等高线当路。所以走「专用小模型」路线：人标一批 patch，训一个像素级
分割器，输出「可通行概率图」。**目标完全自动**，手动标注只是训练数据来源。

★★ 掩码的取值语义（三态，别搞混）
--------------------------------------
    255 = 未标注（训练时**忽略**，不算正也不算负）
      1 = 可通行
      0 = 不可通行

    255 那一档是关键：人只标了路附近几笔，整张 patch 其余部分**不是**
    「不可通行」，而是「没标过」。当成负样本会把模型教成「没画过的一律
    不能走」，学出来的图全是黑。

★ 操作
------
    浏览模式（大图）
        左键单击        -> 以该点为中心裁一块 patch，进入标注模式
        滚轮            -> 缩放
        中键拖          -> 平移
        n / Enter       -> 去下一个未标注的位置
        q / Esc         -> 退出

    标注模式（512x512）
        左键拖          -> 涂「可通行」（绿）
        右键拖          -> 涂「不可通行」（红）
        中键拖          -> 橡皮（回到 255 未标注）
        滚轮            -> 笔刷粗细
        n               -> 存盘 + 下一张
        b               -> 回浏览模式（换地方标，不存）
        s               -> 立刻存盘（不换）
        z               -> 撤销上一笔
        q / Esc         -> 退出（未存的会提示）

★ 快是硬要求
------------
每个 patch 目标 5~15 秒。所以：默认笔刷 9px、`n` 存盘下一张、
已标过的 patch **自动跳过**、状态栏常驻告诉你「这张还剩多少没标」。

用法::

    python label_terrain.py                    # 默认参数
    python label_terrain.py --patch 768        # 换个 patch 尺寸
    python label_terrain.py --browse-only      # 只浏览，不标
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
import os
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageTk

try:
    import tkinter as tk
except ImportError:                                        # pragma: no cover
    sys.stderr.write("✗ 本机没有 tkinter，标注界面跑不起来。\n")
    raise

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:                                     # pragma: no cover
    pass

# ---------------------------------------------------------------- 路径
BASE_MAP = Path(RECOG_ROOT / 'data' / 'mihoyo_maps' / 'teyvat_P0.png')
#: 金字塔兜底：底图整张解码要 ~2GB 内存，顶不住就退到 L1（1/4）。
FALLBACK_MAP = Path(RECOG_ROOT / 'data' / 'mihoyo_maps' / 'P0_pyramid' / 'L1_full.png')
LABEL_DIR = Path(RECOG_ROOT / 'data' / 'terrain_labels')
PATCH_DIR = Path(RECOG_ROOT / 'data' / 'terrain_patches')

IGNORE, PASS, BLOCK = 255, 1, 0
COLORS = {PASS: (0, 220, 90), BLOCK: (235, 60, 60), IGNORE: (255, 255, 255)}


def atomic_save_png(arr: np.ndarray, path: Path) -> None:
    """原子写 PNG。

    ★ 为什么必须原子
    ----------------
    直接 ``Image.save(path)`` 崩在中途会留下**半张** PNG —— 而且文件名
    是完整的，下一轮扫描会当成「已标注」跳过，等于永久丢掉这一张。
    写成 .tmp 再 ``os.replace`` 是原子的：要么完整，要么不存在。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    Image.fromarray(arr).save(tmp, format="PNG")
    os.replace(tmp, path)


def load_base(path: Path) -> tuple[Image.Image, int]:
    """开底图。返回 (图, 实际用了第几号金字塔层, 0=原图)。"""
    for level, cand in ((0, path), (1, FALLBACK_MAP)):
        if not cand.is_file():
            continue
        try:
            t0 = time.monotonic()
            img = Image.open(cand)
            img.load()
            print(f"  底图: {cand.name}  {img.width}x{img.height}"
                  f"  （解码 {time.monotonic() - t0:.1f}s）")
            if level:
                print(f"  ⚠ 原图解码失败/内存不足，退到 {cand.name}"
                      f"（1/{4 ** level}）—— 标注坐标会按这个比例记")
            return img.convert("RGB"), level
        except Exception as exc:                            # noqa: BLE001
            print(f"  ⚠ 打开 {cand.name} 失败：{exc}")
    raise SystemExit(f"✗ 底图都打不开：{path} / {FALLBACK_MAP}")


def labeled_ids() -> set[str]:
    """已标注的 patch id 集合（用来自动跳过）。"""
    if not LABEL_DIR.is_dir():
        return set()
    return {p.stem for p in LABEL_DIR.glob("*.png")}


class Labeler:
    def __init__(self, base: Image.Image, level: int, patch: int, brush: int):
        self.base = base
        self.level = level
        self.patch = patch
        self.brush = brush
        self.scale = 1.0                       # 浏览模式缩放
        self.cx = base.width // 2
        self.cy = base.height // 2
        self.mode = "browse"
        self.mask: np.ndarray | None = None
        self.patch_id: str | None = None
        self.pil_patch: Image.Image | None = None
        self.undo: list[np.ndarray] = []
        self._img_ref = None
        self._mk()

    # ---------------------------------------------------------- 界面
    def _mk(self) -> None:
        self.root = tk.Tk()
        self.root.title("地形可通行性标注 · 阶段 A")
        self.root.configure(bg="#1e1e2e")
        self.canvas = tk.Canvas(self.root, bg="#11111b",
                                highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.status = tk.Label(self.root, text="", anchor="w", bg="#1e1e2e",
                               fg="#cdd6f4", font=("Consolas", 10), padx=8,
                               pady=4)
        self.status.pack(fill="x")

        self.canvas.bind("<Configure>", lambda e: self._redraw())
        self.canvas.bind("<Button-1>", self._on_l1)
        self.canvas.bind("<Button-3>", self._on_l3)
        self.canvas.bind("<Button-2>", self._on_l2)
        self.canvas.bind("<B1-Motion>", self._paint)
        self.canvas.bind("<B3-Motion>", self._paint)
        self.canvas.bind("<B2-Motion>", self._paint)
        self.canvas.bind("<ButtonRelease-1>", lambda e: self._undo_push())
        self.canvas.bind("<ButtonRelease-3>", lambda e: self._undo_push())
        self.canvas.bind("<ButtonRelease-2>", lambda e: self._undo_push())
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Motion>", self._hover)
        self.root.bind("<Key>", self._key)
        self.root.focus_set()

    # ---------------------------------------------------------- 浏览
    def _overview(self) -> Image.Image:
        w = max(1, int(self.base.width * self.scale))
        h = max(1, int(self.base.height * self.scale))
        return self.base.resize((w, h), Image.BILINEAR)

    def _to_img(self, x: int, y: int) -> tuple[float, float] | None:
        """canvas 坐标 -> 原图像素坐标。"""
        if self.mode == "label" or self.pil_patch is None:
            return None
        ow = self._overview().width
        if not ow:
            return None
        iw, ih = self.canvas.winfo_width(), self.canvas.winfo_height()
        ox, oy = (iw - ow) // 2, (ih - self._overview().height) // 2
        fx = self.base.width / ow
        fy = self.base.height / max(1, self._overview().height)
        return ((x - ox) * fx, (y - oy) * fy)

    # ---------------------------------------------------------- 标注
    def enter_label(self, cx: float, cy: float) -> None:
        p = self.patch
        x0 = int(max(0, min(self.base.width - p, cx - p / 2)))
        y0 = int(max(0, min(self.base.height - p, cy - p / 2)))
        pid = f"p_{x0}_{y0}_{p}"
        self.patch_id = pid
        self.pil_patch = self.base.crop((x0, y0, x0 + p, y0 + p))
        self.mask = np.full((p, p), IGNORE, dtype=np.uint8)
        self.undo.clear()
        self.mode = "label"
        lbl = LABEL_DIR / f"{pid}.png"
        if lbl.is_file():                                 # 续标
            self.mask = np.array(Image.open(lbl).convert("L"))
        self._redraw()
        self._set_status()

    def back_to_browse(self) -> None:
        self.mode = "browse"
        self.mask = None
        self.pil_patch = None
        self.patch_id = None
        self.undo.clear()
        self._redraw()
        self._set_status()

    def save(self, quiet: bool = False) -> bool:
        if self.mask is None or self.patch_id is None:
            return False
        m = self.mask
        if (m == IGNORE).all():
            if not quiet:
                self._set_status("⚠ 整张都没标，不保存")
            return False
        atomic_save_png(m, LABEL_DIR / f"{self.patch_id}.png")
        atomic_save_png(np.array(self.pil_patch), PATCH_DIR / f"{self.patch_id}.png")
        if not quiet:
            done = (m != IGNORE).sum()
            self._set_status(f"✓ 已存 {self.patch_id}"
                             f"（标了 {done / m.size * 100:.0f}%）"
                             f"  按 n 继续")
        return True

    def next_unlabeled(self) -> None:
        """跳到下一个还没标过的位置（网格扫描，跳过已标）。"""
        p = self.patch
        step = p // 2
        done = labeled_ids()
        for gy in range(0, self.base.height - p + 1, step):
            for gx in range(0, self.base.width - p + 1, step):
                if f"p_{gx}_{gy}_{p}" not in done:
                    self.cx, self.cy = gx + p / 2, gy + p / 2
                    self.enter_label(gx + p / 2, gy + p / 2)
                    return
        self._set_status("🎉 全图按当前 patch 尺寸都标过了")

    def _undo_push(self) -> None:
        if self.mode == "label" and self.mask is not None:
            self.undo.append(self.mask.copy())
            self.undo = self.undo[-30:]

    def _paint(self, ev) -> None:
        if self.mode != "label" or self.mask is None:
            return
        val = {1: PASS, 3: BLOCK, 2: IGNORE}.get(ev.num, None)
        if val is None:
            return
        cv = self.canvas
        x0 = self.pil_patch.width * self.mask.shape[1] / max(1, cv.winfo_width())
        y0 = self.pil_patch.height * self.mask.shape[0] / max(1, cv.winfo_height())
        px = int(ev.x * x0)
        py = int(ev.y * y0)
        if not (0 <= px < self.mask.shape[1] and 0 <= py < self.mask.shape[0]):
            return
        b = self.brush
        self.mask[max(0, py - b):py + b, max(0, px - b):px + b] = val
        self._redraw()

    # ---------------------------------------------------------- 事件
    def _on_l1(self, ev):
        if self.mode == "browse":
            xy = self._to_img(ev.x, ev.y)
            if xy:
                self.cx, self.cy = xy
                self.enter_label(*xy)
        else:
            self._paint(ev)

    def _on_l2(self, ev):
        self._paint(ev)

    def _on_l3(self, ev):
        if self.mode == "browse":
            self._to_img(ev.x, ev.y)
            return
        self._paint(ev)

    def _wheel(self, ev):
        d = 1.1 if ev.delta > 0 else 1 / 1.1
        if self.mode == "browse":
            self.scale = max(0.002, min(1.0, self.scale * d))
            self._redraw()
        else:
            self.brush = max(1, min(80, int(self.brush * (1.15 if ev.delta > 0
                                                         else 1 / 1.15))))
            self._set_status()

    def _hover(self, ev):
        if self.mode != "browse":
            return
        xy = self._to_img(ev.x, ev.y)
        if xy:
            self._set_status(f"原图 ({xy[0]:.0f}, {xy[1]:.0f})   "
                             f"缩放 {self.scale * 100:.0f}%   左键=裁 patch")

    def _key(self, ev):
        k = (ev.keysym or "").lower()
        if k in ("q", "escape"):
            if self.mode == "label" and self.mask is not None:
                if (self.mask != IGNORE).any():
                    self.save(quiet=True)
            self.root.destroy()
        elif self.mode == "label":
            if k == "n":
                if self.save():
                    self.next_unlabeled()
            elif k == "s":
                self.save()
            elif k == "b":
                self.back_to_browse()
            elif k == "z":
                if self.undo:
                    self.mask = self.undo.pop()
                    self._redraw()
                    self._set_status(f"↶ 撤销（还剩 {len(self.undo)} 步）")
            elif k in ("return", "kp_enter"):
                if self.save():
                    self.next_unlabeled()
        else:
            if k in ("n", "return", "kp_enter"):
                self.next_unlabeled()
            elif k == "b":
                pass

    # ---------------------------------------------------------- 画
    def _redraw(self) -> None:
        cv = self.canvas
        cv.delete("all")
        if self.mode == "label" and self.pil_patch is not None:
            comp = np.array(self.pil_patch).astype(np.int16)
            ov = np.zeros((*self.mask.shape, 3), dtype=np.uint8)
            for val, col in COLORS.items():
                ov[self.mask == val] = col
            a = np.where(self.mask[..., None] == IGNORE, 0.35, 0.72).astype(float)
            out = (comp * (1 - a) + ov * a).clip(0, 255).astype(np.uint8)
            im = Image.fromarray(out).resize((cv.winfo_width(),
                                              cv.winfo_height()),
                                             Image.NEAREST)
        else:
            ov = self._overview()
            vw, vh = cv.winfo_width(), cv.winfo_height()
            k = min(vw / max(1, ov.width), vh / max(1, ov.height))
            ov = ov.resize((max(1, int(ov.width * k)),
                            max(1, int(ov.height * k))), Image.BILINEAR)
            im = ov
        self._img_ref = ImageTk.PhotoImage(im)
        cv.create_image((cv.winfo_width() - im.width) // 2,
                        (cv.winfo_height() - im.height) // 2,
                        image=self._img_ref, anchor="nw")
        if self.mode == "browse" and im.width > 20:
            p = self.patch * self.scale * (im.width
                                          / max(1, self._overview().width))
            if p >= 8:
                ox = (cv.winfo_width() - im.width) // 2
                oy = (cv.winfo_height() - im.height) // 2
                cx = ox + (self.cx / self.base.width) * im.width
                cy = oy + (self.cy / self.base.height) * im.height
                cv.create_rectangle(cx - p / 2, cy - p / 2, cx + p / 2,
                                    cy + p / 2, outline="#f9e2af", width=2)
        self._set_status()

    def _set_status(self, extra: str = "") -> None:
        done = len(labeled_ids())
        if self.mode == "label" and self.mask is not None:
            m = self.mask
            s = (f"【{self.patch_id}】 已标 {(m != IGNORE).sum() / m.size * 100:.0f}%"
                 f"  可通行 {(m == PASS).sum()}  不可通行 {(m == BLOCK).sum()}"
                 f"  笔刷 {self.brush}px  |  左=通行 右=不通 中=擦 n=存+下一张"
                 f" b=返回 z=撤销")
        else:
            s = f"已标 {done} 张  |  左键=裁 patch  n=下一个未标注  q=退出"
        self.status.config(text=(s + "   " + extra).strip())


def main() -> int:
    ap = argparse.ArgumentParser(description="地形可通行性标注（阶段 A）")
    ap.add_argument("--map", default=str(BASE_MAP), help="底图路径")
    ap.add_argument("--patch", type=int, default=512, help="patch 边长（像素）")
    ap.add_argument("--brush", type=int, default=9, help="初始笔刷半径（像素）")
    ap.add_argument("--browse-only", action="store_true", help="只浏览不标注")
    args = ap.parse_args()

    LABEL_DIR.mkdir(parents=True, exist_ok=True)
    PATCH_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 70)
    print("地形可通行性标注 · 阶段 A")
    print("=" * 70)
    print(f"  底图  : {args.map}")
    print(f"  标签  : {LABEL_DIR}")
    print(f"  patch : {PATCH_DIR}")
    print(f"  已标  : {len(labeled_ids())} 张")
    print()
    print("  标注模式：左键=可通行  右键=不可通行  中键=擦  n=存+下一张")
    print("  浏览模式：左键=裁 patch  n=下一个未标注  q=退出")
    print()

    base, level = load_base(Path(args.map))
    if args.browse_only:
        lab = Labeler(base, level, args.patch, args.brush)
        lab.root.title("地形底图浏览")
        lab.root.mainloop()
        return 0
    Labeler(base, level, args.patch, args.brush).root.mainloop()
    print(f"\n✓ 退出。本轮共 {len(labeled_ids())} 张已标注。")
    print("  下一步：python train_terrain.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
