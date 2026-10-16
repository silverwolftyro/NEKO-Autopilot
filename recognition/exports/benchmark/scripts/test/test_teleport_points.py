
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
import sys, json
sys.path.insert(0, PLUGIN_DIR)
from autopilot.perception.loaders import teleport_points as tp

print("--- A: BetterGI 探测 ---")
print("detect_bettergi() =", tp.detect_bettergi())
print("resolve_path()    =", tp.resolve_path())

print("\n--- B: 自有数据路径 ---")
print("own_data_path()   =", tp.own_data_path())
print("  存在? ", tp.own_data_path().is_file())

print("\n--- 加载 + 转换 ---")
d = tp.load_teleport_points()
n = sum(len(r["points"]) for r in d["regions"])
print(f"version={d['version']}  regions={len(d['regions'])}  points={n}")

p0 = d["regions"][0]["points"][0]
print("\n--- C: 差集 extra ---")
print("点[0] 键:", list(p0.keys()))
print("extra :", json.dumps(p0["extra"], ensure_ascii=False)[:200])
print("world_pos  =", p0.get("world_pos"))
print("landing_pos=", p0.get("landing_pos"))