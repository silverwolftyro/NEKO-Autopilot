"""主控台配置包：数据模型 + 三层加载器。"""

from __future__ import annotations

from .loader import load_config, save_config
from .schema import DEFAULT_CONFIG, ConsoleConfig

__all__ = ["DEFAULT_CONFIG", "ConsoleConfig", "load_config", "save_config"]
