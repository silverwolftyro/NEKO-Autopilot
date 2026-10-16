"""三层配置加载：用户 TOML > 内置 default.toml > 代码默认。

优先级与行为（刻意这样设计）::

    a) <BASE>/config.toml          用户文件，最高优先级
    b) <BUNDLE>/neko_console/config/default.toml    打包内置
    c) schema.DEFAULT_CONFIG       最终兜底，必存在

    a 存在   -> 读 a；某字段非法 -> **收缩到边界**，不拒绝启动
    a 不存在 -> 用 b 的值，并**自动创建一份 a**（用户下次就能改）
    b 也读不到 -> 用 c（理论上不会发生）

"自动创建 a" 是刻意的：用户第一次双击就看到一个可编辑的
``config.toml`` 躺在 exe 旁边，比让他去翻打包目录找模板强得多。
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any

from ..paths import bundled_default_config, config_path
from .schema import DEFAULT_CONFIG, ConsoleConfig

logger = logging.getLogger(__name__)

#: 写 TOML 用 tomli-w；读用标准库 tomllib（3.11+ 自带）
try:  # pragma: no cover - 依赖缺失时降级为"只读不写"
    import tomli_w
    _HAS_TOMLI_W = True
except ImportError:  # pragma: no cover
    tomli_w = None  # type: ignore[assignment]
    _HAS_TOMLI_W = False


def _read_toml(path: Path) -> dict[str, Any] | None:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as exc:
        # 配置坏了**不能挡住启动**：当它不存在，走下一层。
        logger.warning("配置文件读取失败（降级到下一层）：%s —— %s", path, exc)
        return None


def _write_toml(path: Path, data: dict[str, Any]) -> bool:
    if not _HAS_TOMLI_W:
        logger.warning("未安装 tomli-w，跳过自动创建 %s", path)
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            tomli_w.dumps(_with_comments(data)), encoding="utf-8")
        return True
    except OSError as exc:
        logger.warning("写入配置失败：%s —— %s", path, exc)
        return False


def _with_comments(data: dict[str, Any]) -> dict[str, Any]:
    """加上文件头注释。tomli-w 不支持注释，用一个哑键承载。"""
    out = {
        "#": "NEKO-Console 配置文件。"
             "删掉任意一行即回退默认；非法值会被自动收缩到边界。",
    }
    out.update(data)
    return out


def load_config(*, write_back: bool = True) -> tuple[ConsoleConfig, str]:
    """加载配置。

    :returns: ``(配置, 来源说明)``。来源说明形如 ``用户文件 /path/config.toml``，
              便于首启动那行日志直接打印。
    """
    user_path = config_path()

    # ── a) 用户文件 ───────────────────────────────────────────
    user_data = _read_toml(user_path)
    if user_data is not None:
        cfg, notes = _build(user_data)
        for n in notes:
            logger.warning("配置收缩：%s", n)
        if write_back:
            _write_toml(user_path, cfg.model_dump())
        return cfg, f"用户文件 {user_path}"

    # ── b) 打包内置 default.toml ─────────────────────────────
    bundled = bundled_default_config()
    bundled_data = _read_toml(bundled)
    if bundled_data is not None:
        cfg, notes = _build(bundled_data)
        for n in notes:
            logger.warning("内置配置收缩：%s", n)
        if write_back:
            # ★ 首次启动：把内置值落成用户文件，用户下次就能改
            ok = _write_toml(user_path, cfg.model_dump())
            logger.info("已创建用户配置：%s%s", user_path, "" if ok else "（写入失败）")
        return cfg, f"内置默认 {bundled}"

    # ── c) 代码兜底 ──────────────────────────────────────────
    cfg, _ = _build(dict(DEFAULT_CONFIG))
    return cfg, "代码兜底 schema.DEFAULT_CONFIG"


def _build(data: dict[str, Any]) -> tuple[ConsoleConfig, list[str]]:
    """pydantic 校验 + 手动收缩，返回合法配置。"""
    try:
        cfg = ConsoleConfig(**data)
    except Exception as exc:
        # 字段级错误已经由 clamped() 兜住了，走到这里说明类型本身坏了
        logger.warning("配置整体非法，逐项收缩：%s", exc)
        merged = dict(DEFAULT_CONFIG)
        for k, v in data.items():
            if k in DEFAULT_CONFIG:
                merged[k] = v
        data = merged
        cfg = ConsoleConfig(**data)
    return cfg.clamped()


def save_config(cfg: ConsoleConfig) -> bool:
    """显式保存（设置页改完调）。"""
    return _write_toml(config_path(), cfg.model_dump())


__all__ = ["load_config", "save_config"]
