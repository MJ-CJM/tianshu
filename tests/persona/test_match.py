"""tianshu.persona.match 通配符 ACL 单测。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tianshu.persona.match import matches_any, persona_can_use
from tianshu.persona.model import AgentPersona


def _make_persona(
    *,
    tools_allowed: list[str] | None = None,
    tools_denied: list[str] | None = None,
    tool_tier_max: int = 0,
) -> AgentPersona:
    return AgentPersona(
        id="test",
        name="Test",
        department="test",
        soul_path=Path("/tmp/soul.md"),
        role_path=Path("/tmp/role.md"),
        memory_path=Path("/tmp/mem.md"),
        tools_allowed=list(tools_allowed or []),
        tools_denied=list(tools_denied or []),
        tool_tier_max=tool_tier_max,
    )


@pytest.mark.unit
class TestMatchesAny:
    def test_exact_match(self) -> None:
        assert matches_any(["read_file"], "read_file") is True

    def test_no_patterns(self) -> None:
        assert matches_any([], "read_file") is False

    def test_star_wildcard(self) -> None:
        assert matches_any(["mcp_github_*"], "mcp_github_create_issue") is True
        assert matches_any(["mcp_github_*"], "mcp_filesystem_read") is False

    def test_question_wildcard(self) -> None:
        assert matches_any(["read_?"], "read_a") is True
        assert matches_any(["read_?"], "read_ab") is False

    def test_charset_wildcard(self) -> None:
        assert matches_any(["tier_[123]"], "tier_2") is True
        assert matches_any(["tier_[123]"], "tier_4") is False

    def test_case_sensitive(self) -> None:
        # fnmatchcase: 大小写敏感
        assert matches_any(["MCP_*"], "mcp_github_x") is False
        assert matches_any(["mcp_*"], "MCP_GITHUB_X") is False


@pytest.mark.unit
class TestPersonaCanUse:
    def test_deny_blocks_even_if_in_allow(self) -> None:
        p = _make_persona(
            tools_allowed=["mcp_github_*"],
            tools_denied=["mcp_github_delete_*"],
        )
        assert persona_can_use(p, "mcp_github_create_issue", 2) is True
        assert persona_can_use(p, "mcp_github_delete_repository", 2) is False

    def test_allow_wildcard_grants_whole_server(self) -> None:
        p = _make_persona(tools_allowed=["mcp_filesystem_*"], tool_tier_max=0)
        # 通配符命中 → 即便 tier > tier_max 也允许
        assert persona_can_use(p, "mcp_filesystem_write", 3) is True
        # 不命中通配符 + allow 非空 → 拒绝（即使 tier 合规）
        assert persona_can_use(p, "read_file", 0) is False

    def test_empty_allow_falls_back_to_tier(self) -> None:
        p = _make_persona(tool_tier_max=2)
        # tier 合规 → 允许
        assert persona_can_use(p, "read_file", 0) is True
        assert persona_can_use(p, "web_search", 2) is True
        # tier 超限 → 拒绝
        assert persona_can_use(p, "delete_repo", 4) is False

    def test_deny_only_denies_matched(self) -> None:
        p = _make_persona(tools_denied=["bash"], tool_tier_max=4)
        assert persona_can_use(p, "bash", 4) is False
        assert persona_can_use(p, "read_file", 0) is True

    def test_deny_wildcard(self) -> None:
        p = _make_persona(tools_denied=["mcp_*_delete_*"], tool_tier_max=4)
        assert persona_can_use(p, "mcp_github_delete_repository", 2) is False
        assert persona_can_use(p, "mcp_filesystem_delete_file", 2) is False
        assert persona_can_use(p, "mcp_github_create_issue", 2) is True

    def test_combined_allow_deny_wildcards(self) -> None:
        """SOUL.md 典型例子：开 GitHub 整个 server，但禁所有 delete 操作。"""
        p = _make_persona(
            tools_allowed=["mcp_github_*", "mcp_filesystem_read_*"],
            tools_denied=["mcp_github_delete_*"],
        )
        # github create → allow 命中、deny 不命中 → 允许
        assert persona_can_use(p, "mcp_github_create_issue", 2) is True
        # github delete → deny 优先 → 拒绝
        assert persona_can_use(p, "mcp_github_delete_repo", 2) is False
        # filesystem read → allow 命中 → 允许
        assert persona_can_use(p, "mcp_filesystem_read_file", 2) is True
        # filesystem write → allow 没命中 → 拒绝
        assert persona_can_use(p, "mcp_filesystem_write_file", 2) is False
        # 内置工具 → allow 没命中 → 拒绝
        assert persona_can_use(p, "read_file", 0) is False
