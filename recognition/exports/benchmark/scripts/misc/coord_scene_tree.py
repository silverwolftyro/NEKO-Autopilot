"""大世界场景判定 —— 手写 ID3 训练出的二分类判定树。

★ 训练来源
----------
- 特征表: ``exports/benchmark/datasets/labeled_features.csv``
- 标注源: 用户对 7 张簇拼图的逐格标注（105 帧）
- 训练器: ``scripts/probe/train_scene_tree.py``（ID3，无 sklearn）
- 5-fold 交叉验证准确率 **0.9429**（各折 0.952/0.905/1.000/0.905/0.952）
- 全量训练集准确率 1.0000，**树深 2**

★ 判定规则（文字形式，可直接调试）
------------------------------------
::

    if hp_bar__edge_density <= 0.1177:
        非大世界
    else:
        if hp_bar__mean_s <= 104.3326:
            非大世界
        else:
            大世界

**只用了血条 ROI 的两个特征** —— 边缘密度 + 平均饱和度。
物理含义：血条在**大世界**里一定有内容（数字文字、边框、色块），
边缘密度高且有彩色；在菜单里那一块是空的/纯灰，边缘密度低、饱和度低。

★ 为什么不用 minimap / skill_bar / party
---------------------------------------
训练时 ID3 选中了 hp_bar 的两个特征就够分开了（熵增益最大）。
其它 ROI 的特征仍在表里，但没被树选中。
**这不是说它们没用**，而是本轮 105 帧样本下 hp_bar 已饱和。

★ 已知边界情况（**不修复，记账**）
------------------------------------
1. **HUD 淡出态**（冲刺 / 交互时游戏隐藏 HUD）
   血条 ROI 全黑 -> ``edge_density`` 约等于 0 -> 误判为"非大世界"
   实测误判帧：``a:f00071``、``a:f00079``、``a:f00083``

2. **阈值贴边**
   ``b:f00108`` 的 ``mean_s = 104.33``，与阈值 ``104.3326`` 只差 0.0026，
   浮点误差就会掉到错误一侧

3. **根本原因**
   判定树**只有一个特征源**（血条 ROI）。单点故障。
   本轮已试过加 avatar / paimon_panel / 派蒙面板静止度三个特征源，
   5-fold 反而从 0.9726 掉到 0.9544 —— ID3 选不到它们，
   因为那些特征分开的是"派蒙 vs 其它"，不是"大世界 vs 非大世界"。

修复方向（未来）：**架构层加时序去抖** —— 连续 N 帧判"非大世界"才真正切换
状态，单帧误判不触发切换。

风险方向：目前所有已知误判都是"大世界 -> 非大世界"，**落在安全方向**
（误判成非世界 = 暂停操作 = 顶多多等两秒）。
"非大世界 -> 大世界"（危险方向，会乱按键鼠）当前 **0 误判**。

★ ⚠️ 其它局限
-----------
1. **只做二分类**。16 个场景类型的多分类不可训（多数类只有 1 个样本）；
   子类（背包物品 / 武器 / 圣遗物 / 食物）**没有**判别。
2. **验证集偏小**：228 帧、5 个场景族。换一批数据可能掉到 0.95 以下。
3. **阈值是标定值**。换分辨率 / 换 UI 缩放 / 游戏改版都要重标。
4. **未纳入** ``*__ncc_world`` 特征 —— 它的参考中值就是用训练帧算的，
   留着会数据泄漏（实测 5-fold 会假性 100%）。
5. **本文件自 2026-10-01 起冻结**。上面的"已知边界情况"是记账，不是待办。
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
import sys

# ---- console encoding ----------------------------------------------
# chcp 65001 only changes the console code page; Python still writes bytes
# in its own encoding (cp936/GBK on Chinese Windows), so every Chinese
# string in this script comes out as mojibake. Force both streams UTF-8.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):   # redirected to StringIO -> no method
        _s.reconfigure(encoding="utf-8", errors="replace")

from dataclasses import dataclass
from pathlib import Path

#: 血条 ROI（归一化）—— 与 recognition/configs/rois.toml 的 hp_player 一致
HP_BAR_ROI = (0.4201, 0.9368, 0.5807, 0.9472)

#: 判定阈值（原始量纲，训练时还原自 z-score）
EDGE_DENSITY_MAX = 0.1177      # <= 此值 -> 非大世界
MEAN_S_MAX = 104.3326          # <= 此值 -> 非大世界

#: 帧根目录
FRAME_ROOT = Path(RECOG_ROOT / 'raw' / 'frames')
BATCH_DIRS = {"a": FRAME_ROOT / "batch_20260929a",
              "b": FRAME_ROOT / "batch_20260929b"}

#: 训练时的统计（供 debug / 复算）
TRAIN_N = 105
TRAIN_ACC_FULL = 1.0000
TRAIN_ACC_CV = 0.9429
TRAIN_ACC_CV_FOLDS = (0.952, 0.905, 1.000, 0.905, 0.952)


@dataclass
class SceneResult:
    is_world: bool
    edge_density: float
    mean_s: float
    rule: str
    frame_id: str = ""


def _crop_hp_bar(frame_bgr):
    """按归一化 ROI 裁出血条。输入必须是整帧 BGR。"""
    import cv2
    h, w = frame_bgr.shape[:2]
    x0, y0, x1, y1 = HP_BAR_ROI
    px0, py0 = int(x0 * w), int(y0 * h)
    px1, py1 = int(x1 * w), int(y1 * h)
    if px1 - px0 < 2 or py1 - py0 < 2:
        raise ValueError(
            f"画面尺寸 {w}x{h} 过小，裁出的血条 ROI 退化："
            f"x[{px0},{px1}) y[{py0},{py1})")
    return frame_bgr[py0:py1, px0:px1]


def hp_bar_features(frame_bgr) -> tuple[float, float]:
    """血条 ROI 的两个特征：(edge_density, mean_s)。

    :param frame_bgr: 整帧 BGR numpy 数组
    """
    import cv2
    import numpy as np
    c = _crop_hp_bar(frame_bgr)
    g = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
    edge = float((cv2.Canny(g, 60, 160) > 0).mean())
    return edge, float(hsv[:, :, 1].mean())


def classify_frame(frame_bgr, frame_id: str = "") -> SceneResult:
    """判定一帧是否在大世界。

    :param frame_bgr: 整帧 BGR
    :param frame_id: 仅用于回填
    :returns: :class:`SceneResult`
    """
    edge, ms = hp_bar_features(frame_bgr)
    if edge <= EDGE_DENSITY_MAX:
        return SceneResult(False, edge, ms,
                           f"edge_density({edge:.4f}) <= {EDGE_DENSITY_MAX} "
                           f"-> 非大世界", frame_id)
    if ms <= MEAN_S_MAX:
        return SceneResult(False, edge, ms,
                           f"edge ok({edge:.4f}) but mean_s({ms:.2f}) <= "
                           f"{MEAN_S_MAX} -> 非大世界", frame_id)
    return SceneResult(True, edge, ms,
                       f"edge({edge:.4f}) > {EDGE_DENSITY_MAX} and "
                       f"mean_s({ms:.2f}) > {MEAN_S_MAX} -> 大世界", frame_id)


def resolve_frame_path(frame_id: str) -> Path:
    """``b:f00023`` -> 实际 jpg 路径。找不到抛 FileNotFoundError。"""
    if ":" not in frame_id:
        raise ValueError(f"frame_id 必须带批次前缀，形如 'b:f00023'，"
                         f"收到 {frame_id!r}")
    tag, stem = frame_id.split(":", 1)
    if tag not in BATCH_DIRS:
        raise FileNotFoundError(f"未知批次 {tag!r}，"
                                f"可选 {sorted(BATCH_DIRS)}")
    p = BATCH_DIRS[tag] / f"{stem}.jpg"
    if not p.is_file():
        raise FileNotFoundError(f"帧不存在: {p}")
    return p


def classify_frame_id(frame_id: str) -> SceneResult:
    """``b:f00023`` -> :class:`SceneResult`。"""
    import cv2
    p = resolve_frame_path(frame_id)
    im = cv2.imread(str(p))
    if im is None:
        raise RuntimeError(f"读不出图像（文件损坏？）: {p}")
    return classify_frame(im, frame_id)


__all__ = [
    "EDGE_DENSITY_MAX",
    "HP_BAR_ROI",
    "MEAN_S_MAX",
    "SceneResult",
    "TRAIN_ACC_CV",
    "TRAIN_ACC_FULL",
    "TRAIN_N",
    "classify_frame",
    "classify_frame_id",
    "hp_bar_features",
    "resolve_frame_path",
]
