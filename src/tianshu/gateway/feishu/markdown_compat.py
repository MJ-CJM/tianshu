"""飞书 markdown 兼容层：实现已迁至 core/markdown_compat.py，此处仅做飞书侧薄别名。

保留原导入路径，供既有调用点（如遗留测试）继续
`from tianshu.gateway.feishu.markdown_compat import ...` 不变。
"""

from __future__ import annotations

from tianshu.gateway.core.markdown_compat import (  # noqa: F401
    DEFAULT_CHUNK_SIZE,
    convert_tables_to_lists,
    split_long,
)

__all__ = ["convert_tables_to_lists", "split_long", "DEFAULT_CHUNK_SIZE"]
