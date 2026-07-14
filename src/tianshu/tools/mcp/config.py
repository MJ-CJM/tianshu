"""MCP server 配置 schema、YAML 解析与 DB override 合并。

设计要点：
- YAML 种子文件 ``~/.tianshu/mcp_servers.yaml`` 顶级键 ``mcp_servers`` 是
  ``dict[name -> server config]``，name 在 dict key 上，不在 server 体里。
- ``${VAR}`` 语法用于 ``env`` / ``headers`` 值的环境变量插值；找不到变量时
  保留原字面量（避免启动 fail-fast；运行时连接失败更易定位）。
- DB override 表 ``mcp_server_overrides`` 字段为 nullable：
  None 表示「不覆盖，沿用 YAML」；非 None 表示「以 override 为准」。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

_ENV_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _interpolate_env(value: str) -> str:
    """把字符串中的 ``${VAR}`` 替换为环境变量；未定义时保留字面量。"""

    def _sub(match: re.Match[str]) -> str:
        var_name = match.group(1)
        return os.environ.get(var_name, match.group(0))

    return _ENV_PATTERN.sub(_sub, value)


def _interpolate_mapping(mapping: dict[str, str]) -> dict[str, str]:
    return {k: _interpolate_env(v) for k, v in mapping.items()}


class ToolFilter(BaseModel):
    """工具白/黑名单。stdio 的 ``include`` 为空时不会通过 Lean 准入。"""

    model_config = ConfigDict(extra="forbid")

    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class MCPServerConfig(BaseModel):
    """单个 MCP server 的完整配置（merge override 后的最终态）。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    transport: Literal["stdio", "streamable_http"]

    # stdio
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)

    # streamable_http
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)

    # common
    enabled: bool = True
    default_tier: int = 2
    tool_overrides: dict[str, int] = Field(default_factory=dict)
    tools: ToolFilter = Field(default_factory=ToolFilter)
    timeout: int = 120
    connect_timeout: int = 30

    @model_validator(mode="after")
    def _check_transport_fields(self) -> MCPServerConfig:
        if self.transport == "stdio":
            if not self.command:
                raise ValueError(f"server {self.name!r}: stdio transport requires 'command'")
        elif self.transport == "streamable_http" and not self.url:
            raise ValueError(f"server {self.name!r}: streamable_http transport requires 'url'")
        if self.default_tier not in (0, 1, 2, 3, 4):
            raise ValueError(
                f"server {self.name!r}: default_tier must be 0..4, got {self.default_tier}"
            )
        return self

    def with_env_interpolated(self) -> MCPServerConfig:
        """返回一个对 env / headers 完成 ``${VAR}`` 替换的副本。"""
        return self.model_copy(
            update={
                "env": _interpolate_mapping(self.env),
                "headers": _interpolate_mapping(self.headers),
            }
        )


class MCPConfig(BaseModel):
    """整个 ``mcp_servers.yaml`` 文件的反序列化模型。"""

    model_config = ConfigDict(extra="forbid")

    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


@dataclass(frozen=True)
class MCPServerOverride:
    """DB ``mcp_server_overrides`` 表的一行。

    nullable 语义：
      * YAML 中存在同名 server → None = 沿用 YAML，非 None = 覆写
      * YAML 中没有同名 server → 只要含足够字段（transport + 主字段），就在 merge
        时晋级为完整 server（DB 直接定义新 server）
    """

    name: str
    enabled: bool | None = None
    env: dict[str, str] | None = None
    tools_include: list[str] | None = None
    tools_exclude: list[str] | None = None
    transport: str | None = None
    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    default_tier: int | None = None
    timeout: int | None = None
    connect_timeout: int | None = None
    tool_overrides: dict[str, int] | None = None


def _parse_server_dict(name: str, raw: dict[str, Any]) -> MCPServerConfig:
    """把 YAML 单个 server dict 反序列化成 ``MCPServerConfig``。"""
    payload = {**raw, "name": name}
    return MCPServerConfig.model_validate(payload)


def load_config_from_yaml(path: str | Path) -> MCPConfig:
    """读取 YAML 配置文件；文件不存在时返回空配置。"""
    p = Path(path).expanduser()
    if not p.exists():
        return MCPConfig()
    with p.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{p}: top-level must be a mapping")

    servers_raw = raw.get("mcp_servers") or {}
    if not isinstance(servers_raw, dict):
        raise ValueError(f"{p}: 'mcp_servers' must be a mapping")

    servers: dict[str, MCPServerConfig] = {}
    for name, server_raw in servers_raw.items():
        if not isinstance(server_raw, dict):
            raise ValueError(f"{p}: server {name!r} must be a mapping")
        servers[name] = _parse_server_dict(name, server_raw)

    return MCPConfig(mcp_servers=servers)


def merge_overrides(
    config: MCPConfig,
    overrides: list[MCPServerOverride],
) -> MCPConfig:
    """把 DB override 应用到 YAML 种子，并把 DB-only server 晋级为完整配置。

    1. YAML 中已有的 server：override 中非 None 字段覆写 YAML，None 字段沿用。
    2. YAML 中没有的 server：若 override 含足够字段（transport + 主字段），构造
       完整 ``MCPServerConfig`` 加入；字段不全的忽略并记日志。
    3. 返回新的 ``MCPConfig`` 实例（不修改原对象）。
    """
    by_name = {o.name: o for o in overrides}
    merged: dict[str, MCPServerConfig] = {}

    # 第 1 段：YAML 已有 server 应用 override
    for name, server in config.mcp_servers.items():
        ov = by_name.get(name)
        if ov is None:
            merged[name] = server
            continue
        update: dict[str, Any] = {}
        if ov.enabled is not None:
            update["enabled"] = ov.enabled
        if ov.env is not None:
            update["env"] = dict(ov.env)
        if ov.tools_include is not None or ov.tools_exclude is not None:
            update["tools"] = ToolFilter(
                include=list(
                    ov.tools_include if ov.tools_include is not None else server.tools.include
                ),
                exclude=list(
                    ov.tools_exclude if ov.tools_exclude is not None else server.tools.exclude
                ),
            )
        if ov.transport is not None:
            update["transport"] = ov.transport
        if ov.command is not None:
            update["command"] = ov.command
        if ov.args is not None:
            update["args"] = list(ov.args)
        if ov.url is not None:
            update["url"] = ov.url
        if ov.headers is not None:
            update["headers"] = dict(ov.headers)
        if ov.default_tier is not None:
            update["default_tier"] = ov.default_tier
        if ov.timeout is not None:
            update["timeout"] = ov.timeout
        if ov.connect_timeout is not None:
            update["connect_timeout"] = ov.connect_timeout
        if ov.tool_overrides is not None:
            update["tool_overrides"] = dict(ov.tool_overrides)
        merged[name] = server.model_copy(update=update) if update else server

    # 第 2 段：YAML 中没有但 DB 完整定义的 server
    import logging

    log = logging.getLogger(__name__)
    for name, ov in by_name.items():
        if name in merged:
            continue
        if not ov.transport:
            log.debug("[mcp] override %s has no transport and no YAML seed; skipping", name)
            continue
        try:
            tools_filter = ToolFilter(
                include=list(ov.tools_include or []),
                exclude=list(ov.tools_exclude or []),
            )
            cfg = MCPServerConfig(
                name=name,
                transport=ov.transport,  # type: ignore[arg-type]
                command=ov.command,
                args=list(ov.args or []),
                url=ov.url,
                headers=dict(ov.headers or {}),
                env=dict(ov.env or {}),
                enabled=False if ov.enabled is None else ov.enabled,
                default_tier=2 if ov.default_tier is None else ov.default_tier,
                tool_overrides=dict(ov.tool_overrides or {}),
                tools=tools_filter,
                timeout=120 if ov.timeout is None else ov.timeout,
                connect_timeout=30 if ov.connect_timeout is None else ov.connect_timeout,
            )
            merged[name] = cfg
        except Exception as exc:
            log.warning("[mcp] DB-defined server %s is invalid, skipping: %s", name, exc)

    return MCPConfig(mcp_servers=merged)
