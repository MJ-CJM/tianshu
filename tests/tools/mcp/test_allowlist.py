"""MCP 治理·准入清单(迭代 3「深防御」D15)——非清单 server 拒绝加载。"""

from __future__ import annotations

from tianshu.executor.execution_gateway import ExecutionGateway
from tianshu.tools.mcp.manager import MCPManager
from tianshu.tools.registry import ToolRegistry


def _mgr(allowlist: str | None) -> MCPManager:
    return MCPManager(ToolRegistry(), ExecutionGateway(), allowlist=allowlist)


class TestAllowlist:
    def test_no_allowlist_admits_all(self):
        m = _mgr(None)
        assert m._admitted("anything")
        assert m._admitted("other")

    def test_empty_string_admits_all(self):
        assert _mgr("")._admitted("anything")
        assert _mgr("   ")._admitted("anything")

    def test_allowlist_gates(self):
        m = _mgr("github, filesystem")
        assert m._admitted("github")
        assert m._admitted("filesystem")
        assert not m._admitted("evil-server")

    def test_whitespace_trimmed(self):
        m = _mgr("  github ,  fs  ")
        assert m._admitted("github") and m._admitted("fs")
