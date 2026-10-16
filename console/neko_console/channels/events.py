"""追引擎的事件流 ``<run_id>.stream.jsonl``（引擎追加写）。

``run_id`` 从 ``run_current.txt`` 读 —— 引擎每次启动换一个新 run_id，
事件流文件名跟着变，所以每次 poll 都要确认 run_id 有没有变。

★ 换 run 时 offset 重置
----------------------
换了 run_id 就意味着**另一个文件**。如果沿用旧的 offset，新文件很可能
从中间开始读，漏掉开头的事件。所以按 (run_id, 文件名) 判定是否重置。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

RUN_ID_NAME = "run_current.txt"


class EventReader:
    """事件流追尾读取器。"""

    def __init__(self, runtime_dir: Path) -> None:
        self._dir = Path(runtime_dir)
        self._offset = 0
        self._run_id = ""
        self._rid_mtime_ns: int | None = None
        self._rid_size: int | None = None
        self._stream: Path | None = None

    # ------------------------------------------------------------------
    def run_id(self) -> str:
        """当前 run_id。**按 mtime 短路**，run_current.txt 没变时零 IO。

        ★★ 这里曾经有一个只增不减的缓存，是"事件流显示昨晚旧数据"的元凶
          ----------------------------------------------------------------
          旧实现是::

              if self._run_id:      # 一旦读到就永远返回旧值
                  return self._run_id

          配合 ``EngineManager.start()`` 调的 ``reset()``（只清 offset 和
          stream、**不清 run_id**），结果是：主控台进程启动时缓存了昨晚那次
          运行的 run_id，用户点「启动引擎」，引擎写了**新**的 run_id，
          而 EventReader 永远读不到 —— 继续追昨晚那个 ``.stream.jsonl``。
          表现就是事件流里时间戳停在昨晚，而引擎明明是新起的。

          现在改成按 mtime+size 判定：变了就重读（run 切换能被立刻发现），
          没变就走缓存（热路径仍然零 IO）。
        """
        p = self._dir / RUN_ID_NAME
        try:
            st = p.stat()
        except OSError:
            # ★ run_current.txt 没了（首次运行 / 引擎清理过目录）
            #   必须把缓存一并作废 —— 否则会一直拿着一个已经不存在的
            #   run_id 去找文件，永远返回空。
            self._forget_run()
            return ""

        if (self._run_id and st.st_mtime_ns == self._rid_mtime_ns
                and st.st_size == self._rid_size):
            return self._run_id           # 没变，零 IO

        try:
            rid = p.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            logger.debug("读 run_current.txt 失败（忽略）：%s", exc)
            return self._run_id            # 保持上一次的有效值

        if rid and rid != self._run_id:
            logger.info("run_id 变化：%s -> %s", self._run_id or "(空)", rid)
            # ★ 不在这里清 offset：换了 run 就换了文件，:meth:`poll` 会靠
            #   ``path != self._stream`` 自己重置。在 getter 里偷偷改游标
            #   会让"谁负责换 run"这件事变得不透明。
        self._run_id = rid
        self._rid_mtime_ns = st.st_mtime_ns
        self._rid_size = st.st_size
        return self._run_id

    def _forget_run(self) -> None:
        self._run_id = ""
        self._rid_mtime_ns = None
        self._rid_size = None

    def stream_path(self) -> Path | None:
        rid = self.run_id()
        if not rid:
            return None
        return self._dir / f"{rid}.stream.jsonl"

    def reset(self) -> None:
        """丢弃 run_id 记忆，下次 poll 重新确认当前是哪一次运行。

        ★ 只清 run_id，**不动** offset / stream
          --------------------------------------
          ``start()`` 调它的时候，引擎往往**还没来得及**写新的
          run_current.txt。如果这里顺手把 offset 也清了，下次 poll 就会
          对着**旧 run 的文件从头再读一遍** —— 于是刚点启动，
          昨晚那些事件又被倒灌回事件流（这正是要修的那个现象的另一种写法）。

          正确分工：本方法只负责"别再信缓存"；真正换文件时由
          :meth:`poll` 的 ``path != self._stream`` 判定并重置 offset。
        """
        self._forget_run()

    # ------------------------------------------------------------------
    def poll(self) -> list[dict[str, Any]]:
        """读新增事件行。**没有新增时零 IO**（stat 短路）。"""
        path = self.stream_path()
        if path is None:
            return []

        if path != self._stream:
            # 换了 run / 第一次读 -> 从头
            self._stream = path
            self._offset = 0

        try:
            size = os.stat(path).st_size
        except OSError:
            return []                    # 文件还没建
        if size <= self._offset:
            # ★ 常见路径：stat 完就返回，一次 read 都不做
            return []

        out: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                fh.seek(self._offset)
                for raw in fh:
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        # ★ 半行 JSON：引擎正在写这一行。
                        #   跳过，并且**不推进 offset 到这一行之后** ——
                        #   下轮会把完整的这行重新读到。
                        logger.debug("事件行不完整（跳过本轮）")
                        continue
                    if isinstance(item, dict):
                        out.append(item)
                self._offset = fh.tell()
        except OSError as exc:
            logger.debug("事件流读取失败：%s", exc)
            return out
        return out


__all__ = ["RUN_ID_NAME", "EventReader"]
