"""NEKO-Console：N.E.K.O. 3.0 的主控台（独立进程）。

★ 版本号的**唯一来源**
---------------------
整个仓库里只有这一行写版本号。``pyproject.toml`` 通过
``[tool.hatch.version] path`` 从这里动态读，窗口标题、启动日志、
关于页全部 ``from neko_console import __version__``。

改动规则见 ``AGENTS.md``：每完成一个任务包 patch +1，不进位到 0.2.x。
"""

from __future__ import annotations

# ★ 版本号规则：与插件 plugin.toml **同步**。
#     3    = N.E.K.O. 3.0 项目代号
#     1    = 主迭代号
#     18   = 小迭代号（每完成一个任务包 +1）
#   两边必须一致 —— 用户一眼判断"插件和主控台是配套的"。
#   pyproject.toml 通过 [tool.hatch.version] 从这里动态读，不另写一份。
__version__ = "3.1.18"
__all__ = ["__version__"]
