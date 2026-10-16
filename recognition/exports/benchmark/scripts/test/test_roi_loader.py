"""roi_loader 冒烟测试（临时验证脚本，不属于正式测试套件）。

★ 为什么绕过 ``perception/__init__.py``
----------------------------------------
直接 ``import autopilot.perception.roi_loader`` 会先执行包的
``__init__``，而它 import 了 capturer / ocr / yolo —— 后者会拉起
WinRT 和 onnxruntime。一句"读个配置"的验证，拖上整套重依赖没有意义，
还让"脚本跑不起来"和"代码有 bug"两件事混在一起分不清。

所以这里手工造一个轻量包壳 ``apkg``，只把 paths / roi_loader 两个模块
按文件路径装进去。相对导入 ``from .paths import ...`` 依然能解析。

用法::

    D:\\NEKO\\N.E.K.O-main\\N.E.K.O-main\\.venv\\Scripts\\python.exe ^
        D:\\NEKO\\recognition\\exports\\benchmark\\scripts\\test_roi_loader.py
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
import types
from pathlib import Path

# 插件根：…\plugin\plugins\genshin_game_buddy
PLUGIN_ROOT = Path(
    PLUGIN_DIR)
PERCEPTION_DIR = PLUGIN_ROOT / "autopilot" / "perception"

# 参考帧：rois.toml 的 [meta].reference_frame 就是它
FRAME = Path(
    RECOG_ROOT / 'raw' / 'frames' / 'batch_20260929b' / 'f00121.jpg')


def _load_pkg_shell() -> None:
    """造一个只有路径、没有 __init__ 副作用的轻量包。"""
    pkg = types.ModuleType("apkg")
    pkg.__path__ = [str(PERCEPTION_DIR)]  # type: ignore[attr-defined]
    sys.modules["apkg"] = pkg


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法为 {path} 建立 module spec")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _read_image(p: Path):
    """读图。优先 cv2（感知层本来就在用），退回 PIL。"""
    try:
        import cv2
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError(f"cv2.imread 返回 None（解码失败）：{p}")
        return img
    except ImportError:
        pass
    from PIL import Image
    import numpy as np
    return np.array(Image.open(p).convert("RGB"))


def main() -> int:
    _load_pkg_shell()
    paths = _load("apkg.paths", PERCEPTION_DIR / "paths.py")
    loader = _load("apkg.roi_loader", PERCEPTION_DIR / "roi_loader.py")

    print("=" * 68)
    print("perception 资产路径")
    print("=" * 68)
    print(f"  RECOGNITION_ROOT : {paths.RECOGNITION_ROOT}")
    print(f"  ROIS_TOML        : {paths.ROIS_TOML}  "
          f"[存在={paths.ROIS_TOML.is_file()}]")
    print(f"  TEMPLATES_UI     : {paths.TEMPLATES_UI}  "
          f"[存在={paths.TEMPLATES_UI.is_dir()}]")
    print(f"  FRAMES_BATCH_B   : {paths.FRAMES_BATCH_B}  "
          f"[存在={paths.FRAMES_BATCH_B.is_dir()}]")

    meta = loader.load_meta()
    base = meta.get("base_resolution") or list(
        paths.DEFAULT_BASE_RESOLUTION)
    print(f"\n  基准分辨率       : {base[0]}x{base[1]}")
    print(f"  参考帧           : {meta.get('reference_frame', '（未声明）')}")
    print(f"  阈值             : {loader.load_thresholds()}")
    declared = loader.load_templates_dir()
    if declared:
        exists = Path(declared).is_dir()
        # ★ 标记一律用 ASCII。控制台是 GBK，U+26A0 / U+2717 编码不了，
        #   print 时直接抛 UnicodeEncodeError —— 本脚本第一版就栽在这。
        print(f"  [templates].dir  : {declared}   "
              f"[过期] 应改指 recognition/templates/ui，"
              f"当前存在={exists}")

    print()
    print("=" * 68)
    print("ROI 加载")
    print("=" * 68)
    rois = loader.load_rois()
    print(f"  ROI 数量 : {len(rois)}")
    print(f"  名称     : {', '.join(sorted(rois))}")

    print()
    print("=" * 68)
    print(f"crop 验证  {FRAME}")
    print("=" * 68)
    if not FRAME.is_file():
        print(f"  [NG] 参考帧不存在：{FRAME}")
        return 1
    img = _read_image(FRAME)
    h, w = img.shape[0], img.shape[1]
    print(f"  图像尺寸 : {w} x {h}")
    print()
    print(f"  {'名称':<14}{'归一化 (l,t,r,b)':<34}{'像素框':<26}crop")
    print("  " + "-" * 86)
    for name in sorted(rois):
        r = rois[name]
        x1, y1, x2, y2 = r.to_pixels(w, h)
        sub = r.crop(img)
        ch, cw = sub.shape[0], sub.shape[1]
        print(f"  {name:<14}"
              f"({r.left:.4f},{r.top:.4f},{r.right:.4f},{r.bottom:.4f})  "
              f"({x1},{y1},{x2},{y2})".ljust(26)
              + f"{cw} x {ch}")

    print()
    print("=" * 68)
    print("缓存行为验证")
    print("=" * 68)
    a = loader.load_rois()
    b = loader.load_rois()
    print(f"  连续两次 load_rois() 返回同一对象 : {a is b}  "
          f"（True = lru_cache 生效）")
    c = loader.reload_rois()
    print(f"  reload_rois() 后是新对象         : {c is not a}  "
          f"（True = 缓存已清）")
    print(f"  重载后内容一致                   : {c == a}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
