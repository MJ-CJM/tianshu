"""藏兵阁 · MCP 外挂子系统。

把外部 MCP server 暴露的工具以 ``mcp_<server>_<tool>`` 命名注入既有
``ToolRegistry``，自动复用 tier / policy / persona 体系。

公共入口：
- :class:`MCPManager` — 生命周期与注册总线
- :func:`load_config_from_yaml` — 从 YAML 解析配置
- :func:`encode_tool_name` / :func:`decode_tool_name` — 工具名编解
"""

from __future__ import annotations

from tianshu.tools.mcp.config import (
    MCPConfig,
    MCPServerConfig,
    MCPServerOverride,
    ToolFilter,
    load_config_from_yaml,
    merge_overrides,
)
from tianshu.tools.mcp.manager import MCPManager
from tianshu.tools.mcp.naming import (
    NAME_PREFIX,
    decode_tool_name,
    encode_tool_name,
    is_mcp_tool,
)

__all__ = [
    "MCPConfig",
    "MCPManager",
    "MCPServerConfig",
    "MCPServerOverride",
    "NAME_PREFIX",
    "ToolFilter",
    "decode_tool_name",
    "encode_tool_name",
    "is_mcp_tool",
    "load_config_from_yaml",
    "merge_overrides",
]
