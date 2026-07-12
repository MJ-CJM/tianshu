"""打包资源：不可变默认资源的 canonical 树与定位器。

安装后的包资源只读；运行时/用户自定义一律写入显式 HOME/data overlay
（overlay 机制见 G1.5 Slice A 后半）。禁止任何指向 site-packages 的写入。
"""

from tianshu.resources import catalog

__all__ = ["catalog"]
