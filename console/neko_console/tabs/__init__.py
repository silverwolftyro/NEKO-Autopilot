"""六个页签的空壳。本块全部只有标题文字，不接任何数据。"""

from __future__ import annotations

from .about import AboutTab
from .engine import EngineTab
from .home import HomeTab
from .overlay import OverlayTab
from .pathfind import PathfindTab
from .rules import RulesTab

__all__ = ["AboutTab", "EngineTab", "HomeTab", "OverlayTab", "PathfindTab",
           "RulesTab"]
