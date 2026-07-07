"""tianshu.tools.mcp.config 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tianshu.tools.mcp.config import (
    MCPConfig,
    MCPServerConfig,
    MCPServerOverride,
    ToolFilter,
    load_config_from_yaml,
    merge_overrides,
)

_FULL_YAML = """
mcp_servers:
  filesystem:
    transport: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    env:
      LOG_LEVEL: info
      TOKEN: "${MCP_TEST_TOKEN}"
    enabled: true
    default_tier: 2
    tool_overrides:
      read_file: 0
    tools:
      include: [read_file, write_file]
      exclude: []
    timeout: 90
    connect_timeout: 20

  github:
    transport: streamable_http
    url: https://api.example.com/mcp
    headers:
      Authorization: "Bearer ${MCP_GITHUB_TOKEN}"
    enabled: false
    default_tier: 3
"""


@pytest.mark.unit
class TestLoadConfigFromYaml:
    def test_full_yaml_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "mcp_servers.yaml"
        path.write_text(_FULL_YAML)

        cfg = load_config_from_yaml(path)

        assert isinstance(cfg, MCPConfig)
        assert set(cfg.mcp_servers) == {"filesystem", "github"}

        fs = cfg.mcp_servers["filesystem"]
        assert fs.name == "filesystem"
        assert fs.transport == "stdio"
        assert fs.command == "npx"
        assert fs.args[0] == "-y"
        assert fs.tool_overrides == {"read_file": 0}
        assert fs.tools.include == ["read_file", "write_file"]
        assert fs.timeout == 90
        assert fs.connect_timeout == 20

        gh = cfg.mcp_servers["github"]
        assert gh.transport == "streamable_http"
        assert gh.url == "https://api.example.com/mcp"
        assert gh.enabled is False
        assert gh.default_tier == 3

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        cfg = load_config_from_yaml(tmp_path / "does_not_exist.yaml")
        assert cfg.mcp_servers == {}

    def test_empty_file_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.yaml"
        path.write_text("")
        cfg = load_config_from_yaml(path)
        assert cfg.mcp_servers == {}

    def test_top_level_not_mapping_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("- a\n- b\n")
        with pytest.raises(ValueError, match="mapping"):
            load_config_from_yaml(path)

    def test_mcp_servers_not_mapping_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("mcp_servers: [1,2,3]\n")
        with pytest.raises(ValueError, match="mcp_servers"):
            load_config_from_yaml(path)


@pytest.mark.unit
class TestServerValidation:
    def test_stdio_requires_command(self) -> None:
        with pytest.raises(ValidationError):
            MCPServerConfig(name="x", transport="stdio")

    def test_http_requires_url(self) -> None:
        with pytest.raises(ValidationError):
            MCPServerConfig(name="x", transport="streamable_http")

    def test_default_tier_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            MCPServerConfig(name="x", transport="stdio", command="npx", default_tier=99)

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MCPServerConfig(
                name="x",
                transport="stdio",
                command="npx",
                unknown="oops",  # type: ignore[call-arg]
            )


@pytest.mark.unit
class TestEnvInterpolation:
    def test_interpolate_env_in_stdio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_TEST_TOKEN", "secret-123")
        cfg = MCPServerConfig(
            name="fs",
            transport="stdio",
            command="npx",
            env={"TOKEN": "${MCP_TEST_TOKEN}", "LITERAL": "no_subst"},
        )
        out = cfg.with_env_interpolated()
        assert out.env == {"TOKEN": "secret-123", "LITERAL": "no_subst"}

    def test_interpolate_env_in_http_headers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_GH_TOKEN", "ghp_xxx")
        cfg = MCPServerConfig(
            name="gh",
            transport="streamable_http",
            url="https://x",
            headers={"Authorization": "Bearer ${MCP_GH_TOKEN}"},
        )
        out = cfg.with_env_interpolated()
        assert out.headers == {"Authorization": "Bearer ghp_xxx"}

    def test_missing_env_keeps_literal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_NOT_SET", raising=False)
        cfg = MCPServerConfig(
            name="fs",
            transport="stdio",
            command="npx",
            env={"X": "${MCP_NOT_SET}"},
        )
        out = cfg.with_env_interpolated()
        # 找不到时保留原字面量，避免启动 fail-fast
        assert out.env == {"X": "${MCP_NOT_SET}"}


@pytest.mark.unit
class TestMergeOverrides:
    def _base(self) -> MCPConfig:
        return MCPConfig(
            mcp_servers={
                "fs": MCPServerConfig(
                    name="fs",
                    transport="stdio",
                    command="npx",
                    enabled=True,
                    env={"A": "1"},
                    tools=ToolFilter(include=["read_file"], exclude=[]),
                ),
                "gh": MCPServerConfig(
                    name="gh",
                    transport="streamable_http",
                    url="https://x",
                    enabled=False,
                ),
            }
        )

    def test_no_overrides_returns_unchanged(self) -> None:
        cfg = self._base()
        out = merge_overrides(cfg, [])
        assert out.mcp_servers["fs"].enabled is True
        assert out.mcp_servers["gh"].enabled is False

    def test_override_enabled(self) -> None:
        cfg = self._base()
        out = merge_overrides(cfg, [MCPServerOverride(name="gh", enabled=True)])
        assert out.mcp_servers["gh"].enabled is True
        # fs 不受影响
        assert out.mcp_servers["fs"].enabled is True

    def test_override_env_replaces_completely(self) -> None:
        cfg = self._base()
        out = merge_overrides(cfg, [MCPServerOverride(name="fs", env={"B": "2"})])
        # override 是完整替换，而非 merge 字典
        assert out.mcp_servers["fs"].env == {"B": "2"}

    def test_override_tools_partial(self) -> None:
        cfg = self._base()
        out = merge_overrides(
            cfg,
            [MCPServerOverride(name="fs", tools_exclude=["write_file"])],
        )
        # include 沿用 YAML，exclude 用 override
        assert out.mcp_servers["fs"].tools.include == ["read_file"]
        assert out.mcp_servers["fs"].tools.exclude == ["write_file"]

    def test_override_for_unknown_server_ignored(self) -> None:
        cfg = self._base()
        out = merge_overrides(cfg, [MCPServerOverride(name="ghost", enabled=True)])
        assert "ghost" not in out.mcp_servers

    def test_returns_new_instance(self) -> None:
        cfg = self._base()
        out = merge_overrides(cfg, [MCPServerOverride(name="gh", enabled=True)])
        assert out is not cfg
        # 原对象未变
        assert cfg.mcp_servers["gh"].enabled is False


@pytest.mark.unit
class TestMergeDbOnlyServers:
    """YAML 中没有但 DB 完整定义的 server 应被晋级为完整配置。"""

    def test_db_only_stdio_promoted(self) -> None:
        cfg = MCPConfig()
        ov = MCPServerOverride(
            name="fs",
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            enabled=True,
            default_tier=1,
            env={"K": "V"},
            timeout=90,
        )
        out = merge_overrides(cfg, [ov])
        assert "fs" in out.mcp_servers
        s = out.mcp_servers["fs"]
        assert s.transport == "stdio"
        assert s.command == "npx"
        assert s.args[0] == "-y"
        assert s.enabled is True
        assert s.default_tier == 1
        assert s.env == {"K": "V"}
        assert s.timeout == 90
        # 默认值（没指定的）
        assert s.connect_timeout == 30

    def test_db_only_http_promoted(self) -> None:
        cfg = MCPConfig()
        ov = MCPServerOverride(
            name="gh",
            transport="streamable_http",
            url="https://x.example.com/mcp",
            headers={"Authorization": "Bearer x"},
        )
        out = merge_overrides(cfg, [ov])
        assert out.mcp_servers["gh"].url == "https://x.example.com/mcp"
        assert out.mcp_servers["gh"].headers == {"Authorization": "Bearer x"}

    def test_db_only_missing_transport_skipped(self) -> None:
        cfg = MCPConfig()
        ov = MCPServerOverride(name="ghost", enabled=True)  # 无 transport
        out = merge_overrides(cfg, [ov])
        assert "ghost" not in out.mcp_servers

    def test_db_only_invalid_skipped(self) -> None:
        """transport=stdio 但缺 command → validator 抛错，merge 应静默忽略。"""
        cfg = MCPConfig()
        ov = MCPServerOverride(name="bad", transport="stdio")  # 缺 command
        out = merge_overrides(cfg, [ov])
        assert "bad" not in out.mcp_servers

    def test_partial_override_preserves_other_db_fields(self, tmp_path) -> None:
        """PATCH enabled=False 不应该清空 transport / command / args 等其他字段。

        历史 bug：upsert_mcp_override 用 INSERT ON CONFLICT 暴力覆写，单字段
        PATCH 把其他列写成 NULL，下次 lifespan reload 时 transport 丢失，
        DB-only server 直接消失。
        """
        from tianshu.storage import Storage

        db_path = tmp_path / "test.db"
        storage = Storage(str(db_path))
        storage.init_db()

        # 1) 完整 create
        storage.upsert_mcp_override(
            "ctx7",
            enabled=True,
            transport="stdio",
            command="npx",
            args=["-y", "@upstash/context7-mcp"],
            env={"K": "V"},
            default_tier=0,
        )
        rows = storage.list_mcp_overrides()
        assert len(rows) == 1
        assert rows[0]["transport"] == "stdio"
        assert rows[0]["command"] == "npx"
        assert rows[0]["args"] == ["-y", "@upstash/context7-mcp"]

        # 2) 只 PATCH enabled，其他字段不传
        storage.upsert_mcp_override("ctx7", enabled=False)
        rows = storage.list_mcp_overrides()
        assert len(rows) == 1
        # 关键断言：transport / command / args / env 仍在
        assert rows[0]["enabled"] is False
        assert rows[0]["transport"] == "stdio"
        assert rows[0]["command"] == "npx"
        assert rows[0]["args"] == ["-y", "@upstash/context7-mcp"]
        assert rows[0]["env"] == {"K": "V"}
        assert rows[0]["default_tier"] == 0

        storage.close()

    def test_yaml_existing_uses_override_path_not_promotion(self) -> None:
        """YAML 中已有的 server 走 override 合并，不会被 DB 完整重建覆盖。"""
        cfg = MCPConfig(
            mcp_servers={
                "fs": MCPServerConfig(
                    name="fs",
                    transport="stdio",
                    command="npx",
                    args=["seed-arg"],
                )
            }
        )
        ov = MCPServerOverride(
            name="fs",
            # 整体重写 args 字段
            args=["override-arg"],
        )
        out = merge_overrides(cfg, [ov])
        assert out.mcp_servers["fs"].args == ["override-arg"]
        # 其他 YAML 字段保留
        assert out.mcp_servers["fs"].command == "npx"
