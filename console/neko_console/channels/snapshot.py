"""读引擎的 ``state_snapshot.json``（引擎每 200ms 覆盖写）。

★ 省电：stat 短路
----------------
UI 刷新 500ms 一次，而引擎 200ms 写一次。如果每次都 ``open + read +
json.loads``，即使引擎不动也要白白解析 5 次/秒。

这里先 ``os.stat`` 比对 ``(st_size, m_time_ns)``，**没变就直接返回上次
解析好的那份**，一次 IO 都不做。

★ 为什么只比 size 不够
---------------------
引擎可能写出两个**长度相同**的快照（比如 ``"state":"RUNNING"`` 换成
``"state":"PAUSED"`` 恰好等长）。只比 size 会永远读不到变化。
所以必须带 mtime_ns。
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SNAPSHOT_NAME = "state_snapshot.json"

#: 快照超过这么久没更新就算陈旧。
#:
#: ★ 为什么需要它（2026-10-16 补，集成审计 F3）
#:   引擎每 200ms 覆盖写一次快照。这个类带 stat 短路 —— **没变就直接返回
#:   缓存那份**。于是「引擎已经死了但文件停在最后一次的内容」和
#:   「引擎活着只是这一轮没变化」在调用方眼里**完全一样**。
#:
#:   后果实测过：引擎停掉之后首页照样显示 `4.8 Hz / 223ms P95 / 场景 UNKNOWN`，
#:   数字全是五分钟前的。用户会以为引擎还在跑 —— 这就是
#:   `PathfindChannel` 早就说过的那句：「**陈旧比没有状态更危险**」。
#:
#: 取 5.0s 与 ``PathfindChannel.STALE_AFTER_S`` 一致：引擎 5Hz 写，
#: 5s 是 25 个周期的余量，正常绝不会误判；而 UI 刷新 500ms 一次，
#: 真断了的话 5 秒内就能看出来。
STALE_AFTER_S = 5.0


class SnapshotReader:
    """状态快照读取器（带 stat 短路 + 缓存）。"""

    def __init__(self, runtime_dir: Path) -> None:
        self._dir = Path(runtime_dir)
        self._stamp: tuple[int, int] | None = None
        self._cached: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    @property
    def path(self) -> Path:
        return self._dir / SNAPSHOT_NAME

    def exists(self) -> bool:
        return self.path.is_file()

    # ------------------------------------------------------------------
    # 陈旧判定（2026-10-16 补，集成审计 F3）
    # ------------------------------------------------------------------
    def age_s(self) -> float:
        """快照文件**最后修改**距今多少秒。

        ★ 用文件的 mtime，不信 JSON 里的时间戳
          引擎每 200ms 原子替换一次快照，mtime 就是「它还在动」的可靠证据；
          而 JSON 内部的时间字段格式各家不一，出错时恰好是最需要它准的时候。

        读不到文件返回 ``inf``（= 最陈旧），让调用方统一走「不可信」分支
        —— 与 ``PathfindChannel.state_age_s`` 同一约定。
        """
        try:
            return max(0.0, time.time() - self.path.stat().st_mtime)
        except OSError:
            return float("inf")

    def is_stale(self) -> bool:
        """快照是否已陈旧（超过 :data:`STALE_AFTER_S` 没更新）。

        ★ 与「有没有读到过」是两件事
          ``read()`` 返回 None 只说明「从没读到过」。但**读到过之后引擎死掉**
          仍然返回上一份 —— 那一份可能已经很旧了。调用方要判断「手上这份
          还能不能信」，必须问这里，而不是问 ``read()`` 是不是 None。
        """
        return self.age_s() > STALE_AFTER_S

    def invalidate(self) -> None:
        """丢弃缓存，下次 read 强制重新读。"""
        self._stamp = None

    # ------------------------------------------------------------------
    def read(self) -> dict[str, Any] | None:
        """读当前快照。

        :returns: 解析后的 dict；**从未读到过返回 None**；
                  读得到但内容没变时返回**上一次那份**（同一对象）。
        """
        p = self.path
        try:
            st = os.stat(p)
        except OSError:
            # 引擎没跑 / 文件还没建
            return self._cached

        stamp = (st.st_size, st.st_mtime_ns)
        if stamp == self._stamp:
            return self._cached          # ★ 没变，零 IO

        try:
            text = p.read_text(encoding="utf-8")
            data = json.loads(text)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            # ★ 引擎正在写、或上次崩在写一半 —— 这一帧跳过，不崩 UI
            logger.debug("快照读取失败（跳过本帧）：%s", exc)
            # 不更新 stamp：下轮还会重试，引擎写完就能读到
            return self._cached

        if not isinstance(data, dict):
            logger.debug("快照不是 JSON 对象（跳过）")
            return self._cached

        self._stamp = stamp
        self._cached = data
        return data


__all__ = ["SNAPSHOT_NAME", "STALE_AFTER_S", "SnapshotReader"]
