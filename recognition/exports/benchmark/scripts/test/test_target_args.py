"""目标参数三选一（--target-world / --walk-forward / --target-tp）的单测。

★ 背景
--------
``--target-world X,Z`` 对用户**完全不可用** —— 他不知道任何世界坐标，
也不知道 ``(100,0)`` 在地图上是哪儿。于是加了两个「不需要坐标」的入口。

★ 这轮实测到的两个硬事实（决定了实现形态）
------------------------------------------
1. 本机 936 个传送点里 **777 个都叫「传送锚点」**，``name`` 的唯一值
   只有 **106** 个。自动取第一个 = 静默把角色送去地图另一头。
2. 「风起地」这种地名**只存在于 ``areas``**，``name`` 里一个都没有。
   只搜 ``name`` 的话用户打「风起地」永远匹配不到。

★ 断言风格
----------
区间/包含关系**直接判不等式**，不做「按步长采样后查列表」—— 用户明确
反对后者（采样点之间的值必然查不到）。
零 ``def test_``（本仓库 conftest.py 不生效，自带 runner）。
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
import math
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:                                   # pragma: no cover
    pass

ROOT = Path(PLUGIN_DIR)
sys.path.insert(0, str(ROOT))

SCRIPT = Path(RECOG_ROOT / 'exports' / 'benchmark' / 'scripts' / 'misc' / 'e2e_pathfind.py')

_sp = importlib.util.spec_from_file_location("pf", SCRIPT)
S = importlib.util.module_from_spec(_sp)
_sp.loader.exec_module(S)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f"   {detail}" if detail else ""))


# ======================================================================
# 1. _forward_target 的几何
# ======================================================================
print("\n=== 1. _forward_target 几何 ===")
fwd = S._forward_target

x, z = fwd(0.0, 0.0, 0.0, 30.0)
check("θ=0 -> 正 x 方向", math.isclose(x, 30.0, abs_tol=1e-9)
      and math.isclose(z, 0.0, abs_tol=1e-9), f"({x:.6f},{z:.6f})")

x, z = fwd(0.0, 0.0, math.pi / 2, 30.0)
check("θ=π/2 -> 正 z 方向", math.isclose(x, 0.0, abs_tol=1e-9)
      and math.isclose(z, 30.0, abs_tol=1e-9), f"({x:.6f},{z:.6f})")

x, z = fwd(0.0, 0.0, math.pi, 30.0)
check("θ=π -> 负 x 方向", math.isclose(x, -30.0, abs_tol=1e-9)
      and abs(z) < 1e-9, f"({x:.6f},{z:.6f})")

x, z = fwd(0.0, 0.0, -math.pi / 2, 30.0)
check("θ=-π/2 -> 负 z 方向", math.isclose(x, 0.0, abs_tol=1e-9)
      and math.isclose(z, -30.0, abs_tol=1e-9), f"({x:.6f},{z:.6f})")

# 距离守恒：不管什么朝向，落点到原点的距离都必须 == dist
ok_dist = True
detail = []
for k in range(0, 36):
    th = k * math.pi / 18.0
    for d in (1.0, 12.5, 30.0, 250.0):
        px, pz = fwd(3.0, -7.0, th, d)
        got = math.hypot(px - 3.0, pz + 7.0)
        if not math.isclose(got, d, rel_tol=1e-9, abs_tol=1e-9):
            ok_dist = False
            detail.append(f"th={th:.2f} d={d} got={got}")
check("★距离守恒（36 个朝向 × 4 个距离 = 144 组）", ok_dist, "; ".join(detail[:3]))

# 起点平移不影响偏移量
a1 = fwd(100.0, 200.0, 0.7, 33.0)
a2 = fwd(0.0, 0.0, 0.7, 33.0)
check("起点平移只是整体偏移（增量相同）",
      math.isclose(a1[0] - 100.0, a2[0], abs_tol=1e-9)
      and math.isclose(a1[1] - 200.0, a2[1], abs_tol=1e-9))

# 负距离 = 往回走
x1, _z1 = fwd(0.0, 0.0, 0.0, 20.0)
x2, _z2 = fwd(0.0, 0.0, 0.0, -20.0)
check("负距离 -> 反方向", math.isclose(x1, -x2, abs_tol=1e-9),
      f"{x1} vs {x2}")

# ======================================================================
# 2. --target-tp 的字段匹配
# ======================================================================
print("\n=== 2. _pick_teleport_points 字段匹配 ===")

SAMPLE = {
    "version": 1,
    "source": "unit-test",
    "regions": [
        {"region_id": "3", "display_name": "提瓦特大陆", "points": [
            {"id": "1", "name": "七天神像-风", "category": "Goddess",
             "country": "蒙德", "areas": ["风起地"],
             "world_pos": [1928.381, 196.139, -739.9]},
            {"id": "2", "name": "七天神像-风", "category": "Goddess",
             "country": "蒙德", "areas": ["千风神殿"],
             "world_pos": [100.0, 200.0, -300.0]},
            {"id": "3", "name": "传送锚点", "category": "TeleportWaypoint",
             "country": "璃月", "areas": ["荻花洲"],
             "world_pos": [1.0, 2.0, 3.0]},
            {"id": "4", "name": "传送锚点", "category": "TeleportWaypoint",
             "country": "璃月", "areas": ["层岩巨渊"],
             "world_pos": [4.0, 5.0, 6.0]},
        ]},
    ],
}

hit = S._pick_teleport_points(SAMPLE, "七天神像-风")
check("按 name 匹配命中 2 个（重名如实返回）", len(hit) == 2, f"n={len(hit)}")

hit = S._pick_teleport_points(SAMPLE, "风起地")
check("★按 areas 匹配命中 1 个（地名在 areas 不在 name）",
      len(hit) == 1, f"n={len(hit)}")
if len(hit) == 1:
    check("  areas 命中的名字/坐标正确",
          hit[0]["name"] == "七天神像-风"
          and abs(hit[0]["pos"][0] - 1928.381) < 1e-6,
          f"{hit[0]['name']} {hit[0]['pos']}")

hit = S._pick_teleport_points(SAMPLE, "璃月")
check("按 country 匹配命中 2 个", len(hit) == 2, f"n={len(hit)}")

hit = S._pick_teleport_points(SAMPLE, "传送锚点")
check("★重名「传送锚点」返回全部 2 个而非 1 个", len(hit) == 2, f"n={len(hit)}")

check("空查询返回空", S._pick_teleport_points(SAMPLE, "") == [])
check("无匹配返回空", S._pick_teleport_points(SAMPLE, "不存在的地方") == [])
check("空白查询返回空", S._pick_teleport_points(SAMPLE, "   ") == [])

# 坐标取的是 world_pos 的 [x, z]（跳过 y=高度）
hit = S._pick_teleport_points(SAMPLE, "荻花洲")
if hit:
    check("★坐标取 world_pos[0]/[2]（x,z），不是 [0]/[1]",
          hit[0]["pos"] == (1.0, 3.0), f"{hit[0]['pos']}")

# 部分匹配（子串）
hit = S._pick_teleport_points(SAMPLE, "神像")
check("子串匹配可用（「神像」命中 2）", len(hit) == 2, f"n={len(hit)}")

# ======================================================================
# 3. 真实数据：确认「风起地」确实只在 areas
# ======================================================================
print("\n=== 3. 真实传送点数据（只读）===")
try:
    from autopilot.perception.loaders.teleport_points import load_teleport_points
    real = load_teleport_points()
    pts = [p for r in real["regions"] for p in r["points"]]
    names = [str(p.get("name", "")) for p in pts]
    check(f"真实数据可加载（{len(pts)} 点 / "
          f"{len(set(names))} 个不同名字）", len(pts) > 0)

    n_name = sum(1 for p in pts if "风起地" in str(p.get("name", "")))
    n_area = sum(1 for p in pts
                 if any("风起地" in str(a) for a in (p.get("areas") or [])))
    check("★「风起地」在 name 里 0 个", n_name == 0, f"name={n_name}")
    check("★「风起地」在 areas 里有命中", n_area > 0, f"areas={n_area}")

    hit = S._pick_teleport_points(real, "风起地")
    check("★真实数据上按「风起地」能匹配到（若只有 1 个则唯一）",
          len(hit) >= 1, f"n={len(hit)}")
    if len(hit) == 1:
        check("  唯一命中可直接当目标",
              len(hit[0]["pos"]) == 2 and math.isfinite(hit[0]["pos"][0]))

    # 重名规模：证明「自动取第一个」有多危险
    n_anchor = sum(1 for n in names if n == "传送锚点")
    check("★「传送锚点」重名规模（所以绝不自动取第一个）",
          n_anchor > 100, f"x{n_anchor}")
except Exception as exc:                                  # noqa: BLE001
    check("真实数据加载（可能本机没装 BetterGI）", False, f"{type(exc).__name__}: {exc}")

# ======================================================================
# 4. 三个目标参数互斥（跑真脚本，用 --help 之外的路径验证）
# ======================================================================
print("\n=== 4. 目标参数互斥 ===")
PY = sys.executable


def run_argv(argv):
    p = subprocess.run([PY, str(SCRIPT)] + argv, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


# ★ 不加 --live、不加 --seconds：让脚本在**参数校验**阶段就退出，
#   绝不碰窗口 / 绝不注入按键。
rc, out = run_argv(["--start-world", "0,0",
                    "--target-world", "100,0", "--walk-forward", "30"])
check("★同时给 --target-world + --walk-forward -> exit 2", rc == 2, f"rc={rc}")
check("  报错指明互斥", "互斥" in out, [l.strip() for l in out.splitlines() if "互斥" in l][:1])
check("  报错把三个选项都列出来",
      all(k in out for k in ("--target-world", "--walk-forward", "--target-tp")))

rc, out = run_argv(["--start-world", "0,0",
                    "--target-world", "100,0", "--target-tp", "风起地"])
check("★同时给 --target-world + --target-tp -> exit 2", rc == 2, f"rc={rc}")

rc, out = run_argv(["--start-world", "0,0", "--walk-forward", "30",
                    "--target-tp", "风起地"])
check("★同时给 --walk-forward + --target-tp -> exit 2", rc == 2, f"rc={rc}")

rc, out = run_argv(["--start-world", "0,0",
                    "--target-world", "100,0", "--walk-forward", "30",
                    "--target-tp", "风起地"])
check("★三个都给 -> exit 2 且说明是 3 个", rc == 2 and "3 个" in out, f"rc={rc}")

# 一个都不给
rc, out = run_argv(["--start-world", "0,0"])
check("★一个目标都不给 -> exit 2", rc == 2, f"rc={rc}")
check("  提示三选一", "三选一" in out, [l.strip() for l in out.splitlines() if "三选一" in l][:1])

# --start-world：--walk-forward 模式下可省，其余必填
rc, out = run_argv(["--target-world", "100,0"])
check("★--target-world 模式下 --start-world 必填", rc == 2, f"rc={rc}")
check("  说明为什么必填", "不知道它会跑哪去" in out
      or "e2e_teleport" in out,
      [l.strip() for l in out.splitlines() if "start-world" in l][:1])

rc, out = run_argv(["--target-tp", "风起地"])
check("★--target-tp 模式下 --start-world 必填", rc == 2, f"rc={rc}")
check("  说明「目标是绝对坐标，种子错方向就错」",
      "绝对" in out and "种子" in out,
      [l.strip() for l in out.splitlines() if "绝对" in l][:1])
check("  指出 --walk-forward 不需要真实坐标（给条出路）",
      "walk-forward" in out)

# ★ 本轮新增：--walk-forward 不给 --start-world 应能跑通参数校验
rc, out = run_argv(["--walk-forward", "30", "--live"])
check("★★--walk-forward 不给 --start-world 不再报「必填」",
      "必须给 --start-world" not in out, f"rc={rc}")
check("★给出 [WALK-FWD] 默认种子提示", "[WALK-FWD]" in out,
      [l.strip() for l in out.splitlines() if "WALK-FWD" in l][:1])
check("★提示说明只走相对方向", "相对" in out,
      [l.strip() for l in out.splitlines() if "相对" in l][:1])
# dry-run 下它会在更后面被拒（数学上无解），但**不该**死在 start-world 上
# ★ 注意：这条**不能**加 --live —— --live 会先撞 UIPI 管理员门禁，
#   根本走不到 walk-forward 自己的检查。要验「不再死于 start-world」，
#   就得让 dry-run 的 walk-forward 拒绝成为**先命中**的那条。
rc, out = run_argv(["--walk-forward", "30"])
check("★dry-run + 无 start-world：先走到 walk-forward 自己的拒绝理由",
      "必须给 --start-world" not in out
      and ("数学上无解" in out or "atan2" in out or "不动" in out),
      [l.strip() for l in out.splitlines() if "atan2" in l][:1])

# 互斥判定在**任何**注入之前（不该出现 F12/构造等后续阶段）
rc, out = run_argv(["--start-world", "0,0",
                    "--target-world", "100,0", "--walk-forward", "30"])
check("★互斥在构造/抓帧之前就拦下",
      "构造" not in out and "截图通道" not in out)

# ======================================================================
# 5. --walk-forward 的 dry-run 拒绝（数学上无解）
# ======================================================================
print("\n=== 5. --walk-forward 拒绝 dry-run ===")
rc, out = run_argv(["--start-world", "0,0", "--walk-forward", "30"])
check("★dry-run + --walk-forward -> exit 2", rc == 2, f"rc={rc}")
check("  说明是数学上无解（atan2(0,0)）",
      "atan2" in out or "不动" in out,
      [l.strip() for l in out.splitlines() if "atan2" in l][:1])

# ======================================================================
# 6. 放宽 --start-world 的**前提**：种子 (0,0) 得真能用
# ======================================================================
print("\n=== 6. (0,0) 种子不炸（放宽 start-world 的前提）===")
try:
    from autopilot.perception.tile_tracker import TileTracker
    t0 = TileTracker((0.0, 0.0))
    check("★TileTracker((0,0)) 构造成功", True, repr(t0))
    check("★(0,0) 落在真实瓦片里（不是 None）",
          getattr(t0.grid.tile_of(*t0._pixel), "tile_id", None) is not None,
          f"tile={getattr(t0.grid.tile_of(*t0._pixel), 'tile_id', None)}")
    check("★瓦片预加载非空（出图时返回 []，会拖慢但不至于失败）",
          len(t0.nearby_tiles()) > 0, f"n={len(t0.nearby_tiles())}")
    check("★世界坐标仍是 (0,0)（种子没被偷偷改写）",
          t0._world == (0.0, 0.0), f"{t0._world}")
except Exception as exc:                                  # noqa: BLE001
    check("TileTracker((0,0))", False, f"{type(exc).__name__}: {exc}")

# ======================================================================
# 7. 常量自洽
# ======================================================================
print("\n=== 7. 常量自洽 ===")
check("三个目标参数都在 TARGET_ARGS 里",
      set(S.TARGET_ARGS) == {"--target-world", "--walk-forward", "--target-tp"})
check("预热时长 > 0", S.WALK_FWD_WARM_S > 0, f"{S.WALK_FWD_WARM_S}")
check("预热最小位移 > 0", S.WALK_FWD_MIN_MOVE_M > 0, f"{S.WALK_FWD_MIN_MOVE_M}")
check("★预热最小位移远小于预热时长能走的距离（跑步 ~11m/s）",
      S.WALK_FWD_MIN_MOVE_M < 11.0 * S.WALK_FWD_WARM_S * 0.1,
      f"{S.WALK_FWD_MIN_MOVE_M}m vs 跑步 {11.0*S.WALK_FWD_WARM_S:.0f}m")
check("★_SPEC 里三个新参数都注册了",
      all(k in S._SPEC for k in ("--walk-forward", "--target-tp", "--tp-data")))
check("★_SPEC 的转换函数对得上类型",
      S._SPEC["--walk-forward"][1] is float
      and S._SPEC["--target-tp"][1] is str)

# 源码卫生
txt = SCRIPT.read_text(encoding="utf-8")
check("★文件纯 LF", b"\r\n" not in SCRIPT.read_bytes())
check("★无 U+FFFD 损坏字符", txt.count(chr(0xFFFD)) == 0)

print()
print("=" * 70)
print(f"结果: {len(PASS)}/{len(PASS) + len(FAIL)} 通过")
if FAIL:
    print("  失败: " + ", ".join(FAIL))
print("=" * 70)
sys.exit(1 if FAIL else 0)
