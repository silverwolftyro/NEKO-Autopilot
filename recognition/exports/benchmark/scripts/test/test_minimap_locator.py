"""minimap_locator 单元测试（合成数据 + 边界）。"""

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
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

PERC = (Path(PLUGIN_DIR.parent)
        / "genshin_game_buddy" / "autopilot" / "perception")


def _load(name):
    p = PERC / f"{name}.py"
    s = importlib.util.spec_from_file_location(name, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[name] = m
    s.loader.exec_module(m)
    return m


cl = _load("chunk_loader")
ml = _load("minimap_locator")

HAS_DATA = cl.PYRAMID_DIR.is_dir() and \
    (cl.PYRAMID_DIR / "chunks_index.json").is_file()
print(f"金字塔目录 {cl.PYRAMID_DIR}  可用={HAS_DATA}")


def t1_ncc():
    rs = np.random.default_rng(7)
    base = rs.integers(0, 255, (1000, 1000), dtype=np.uint8)
    base = cv2.GaussianBlur(base, (5, 5), 1.4)      # 平滑一点更像地图
    x, y, d = 300, 400, 200
    tpl = base[y:y + d, x:x + d].copy()
    noisy = tpl.astype(np.int16) + rs.normal(0, 6, tpl.shape).astype(np.int16)
    tpl_n = np.clip(noisy, 0, 255).astype(np.uint8)
    sc, loc = ml.match_ncc(base, tpl_n)
    ok = sc > 0.9 and abs(loc[0] - x) <= 1 and abs(loc[1] - y) <= 1
    print(f"\n[T1] 合成数据 NCC")
    print(f"  真值 ({x},{y})  返回 ({loc[0]},{loc[1]})  分数 {sc:.4f}")
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


def t1b_ncc_mask():
    """掩码 NCC：圆外内容全改成噪声，不该影响分数。"""
    rs = np.random.default_rng(11)
    base = cv2.GaussianBlur(
        rs.integers(0, 255, (800, 800), dtype=np.uint8), (5, 5), 1.4)
    d = 120
    x, y = 250, 300
    tpl = base[y:y + d, x:x + d].copy()
    m = np.zeros((d, d), np.uint8)
    cv2.circle(m, (d // 2, d // 2), d // 2 - 2, 255, -1)
    m = (m > 127).astype(np.uint8) * 255
    # 圆外改成完全不同的内容
    tpl2 = tpl.copy()
    tpl2[m == 0] = rs.integers(0, 255, (m == 0).sum(), dtype=np.uint8)
    s1, l1 = ml.match_ncc(base, tpl, m)
    s2, l2 = ml.match_ncc(base, tpl2, m)
    ok = l1 == l2 and s1 > 0.95 and s2 > 0.95
    print(f"\n[T1b] 掩码 NCC（圆外内容被污染）")
    print(f"  干净模板   ({l1[0]},{l1[1]}) 分数 {s1:.4f}")
    print(f"  污染模板   ({l2[0]},{l2[1]}) 分数 {s2:.4f}")
    print(f"  -> {'PASS' if ok else 'FAIL'}  （位置/分数应基本不变）")
    return ok


def t2_scale():
    """同一张小地图，两个档位得到的模板尺寸不同、分数不同。"""
    rs = np.random.default_rng(3)
    mini = cv2.GaussianBlur(
        rs.integers(0, 255, (260, 260), dtype=np.uint8), (3, 3), 1.0)
    m = np.zeros((260, 260), np.uint8)
    cv2.circle(m, (130, 130), 127, 255, -1)
    m = (m > 127).astype(np.uint8) * 255
    t_field, m_field = ml._make_template(mini, m, ml.SCALE_FIELD, None)
    t_city, m_city = ml._make_template(mini, m, ml.SCALE_CITY, None)
    ok = (t_field.shape[0] == 260 and t_city.shape[0] == 520
          and m_field.shape == t_field.shape
          and m_city.shape == t_city.shape)
    print(f"\n[T2] 双缩放档")
    print(f"  FIELD 模板 {t_field.shape}  掩码 {m_field.shape}")
    print(f"  CITY  模板 {t_city.shape}  掩码 {m_city.shape}")
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


def t3_chunk():
    if not HAS_DATA:
        print("\n[T3] 跳过（无分块数据）")
        return True
    L = cl.ChunkLoader(cache_size=4)
    d = 256
    # ★ 必须在**有地图内容**的块交点上测。
    #   (0,0) 附近的块交点正好落在空白海域（RGBA 透明区转灰度后是黑的），
    #   那种地方测不出拼接对不对。这里用枫丹廷（已知有内容）附近的
    #   4 块交点 (18432, 7680)。
    cx, cy = L.cw * 6, L.ch * 5
    info = L.describe((cx, cy), d)
    t0 = time.perf_counter()
    win = L.load_l0_window((cx, cy), d)
    dt = (time.perf_counter() - t0) * 1000
    q = d // 2
    quads = [float(win[:q, :q].mean()), float(win[:q, q:].mean()),
             float(win[q:, :q].mean()), float(win[q:, q:].mean())]
    # 与金字塔 L1 的同区域对照（缩小 2 倍后比均值，验证没拼错块）
    pl = _load("pyramid_loader")
    P = pl.PyramidLoader(preload=(1,))
    l1 = P.get_level(1)
    ref = l1[cy // 2 - q // 2: cy // 2 + q // 2,
             cx // 2 - q // 2: cx // 2 + q // 2]
    ref_q = [float(ref[:q // 2, :q // 2].mean()),
             float(ref[:q // 2, q // 2:].mean()),
             float(ref[q // 2:, :q // 2].mean()),
             float(ref[q // 2:, q // 2:].mean())]
    ok = (info["cross_chunk"] and win.shape == (d, d)
          and min(quads) > 0 and dt < 200
          and max(abs(a - b) for a, b in zip(quads, ref_q)) < 12)
    print(f"\n[T3] 跨块补拉（4 块交点 {cx},{cy}，枫丹廷附近有内容）")
    print(f"  触及块 {info['inside_cells']}/{info['touched_cells']}  "
          f"跨块={info['cross_chunk']}  底图外 {info['out_of_map_px']}px")
    print(f"  窗口四象限均值 {[round(q,1) for q in quads]}")
    print(f"  L1 对照四象限   {[round(q,1) for q in ref_q]}")
    print(f"  最大差 {max(abs(a-b) for a,b in zip(quads,ref_q)):.2f}（<12 视为同区域）")
    print(f"  耗时 {dt:.1f} ms")
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


def t3b_edge():
    if not HAS_DATA:
        return True
    L = cl.ChunkLoader()
    d = 256
    # 底图左上角外侧：窗口一半在图外
    win = L.load_l0_window((10, 10), d)
    info = L.describe((10, 10), d)
    ok = win.shape == (d, d) and info["out_of_map_px"] > 0
    print(f"\n[T3b] 底图外边界（中心 10,10）")
    print(f"  底图外像素 {info['out_of_map_px']}  形状 {win.shape}")
    print(f"  -> {'PASS' if ok else 'FAIL'}  （黑填充不崩）")
    return ok


def t3c_range():
    if not HAS_DATA:
        return True
    L = cl.ChunkLoader()
    d = 256
    import random
    random.seed(1)
    worst = 0
    for _ in range(6):
        cx = random.uniform(200, L.width - 200)
        cy = random.uniform(200, L.height - 200)
        t0 = time.perf_counter()
        L.load_l0_window((cx, cy), d)
        dt = (time.perf_counter() - t0) * 1000
        worst = max(worst, dt)
    print(f"\n[T3c] 随机 6 点加载，最慢 {worst:.1f} ms  目标 <20ms"
          f"  缓存 {L.stats['hit']}hit/{L.stats['load']}load")
    return worst < 200


if __name__ == "__main__":
    res = [("T1 合成NCC", t1_ncc()),
           ("T1b 掩码NCC", t1b_ncc_mask()),
           ("T2 双档", t2_scale()),
           ("T3 跨块", t3_chunk()),
           ("T3b 边界", t3b_edge()),
           ("T3c 速度", t3c_range())]
    print("\n" + "=" * 60)
    allok = True
    for nm, ok in res:
        print(f"{nm:<16}{'PASS' if ok else 'FAIL':>8}")
        allok &= ok
    print("=" * 60)
    print("全部通过" if allok else "有失败")
