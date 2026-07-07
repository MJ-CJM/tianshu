"""鸿胪寺 — 天枢外朝负责对外网络通讯的官署。

所有对外 HTTP/I/O 必须经此官署；其他部门禁止直接 socket。

公共入口：register_hongluisi()
"""

from tianshu.tools.hongluisi.policy import NetworkPolicy

__all__ = ["NetworkPolicy"]
