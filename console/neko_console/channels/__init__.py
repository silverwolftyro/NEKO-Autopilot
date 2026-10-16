"""与引擎 / 卡片的通道：读状态、追事件流、管理两个子进程。

三个模块的共同约定：**永不抛异常打断 UI**。
引擎没跑、文件不存在、JSON 写了一半 —— 读侧一律返回空/None，
把「读不到」和「读到了但是空」区分清楚就够了，异常交给上层显示。
"""

from __future__ import annotations

from .engine import EngineManager
from .events import EventReader
from .overlay import OverlayManager
from .pathfind import PathfindChannel
from .snapshot import SnapshotReader

__all__ = ["EngineManager", "EventReader", "OverlayManager",
           "PathfindChannel", "SnapshotReader"]
