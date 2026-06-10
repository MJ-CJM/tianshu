# MCP 集成 — 服务器配置、自动发现与启动

> 设计意图：把外部 MCP（Model Context Protocol）服务器的工具无缝接入 ToolRegistry，统一受 tier + 治理管线约束。

## 1. 配置来源与合并

两层配置，DB override 优先于 YAML 种子：

| 来源 | 内容 |
|---|---|
| YAML 种子 | `~/.tianshu/mcp_servers.yaml`，顶级键 `mcp_servers`（dict[name → config]） |
| DB override | `mcp_server_overrides` 表，字段 nullable |

`merge_overrides` 合并语义：
- YAML 已有 server：override 非 None 字段覆写，None 字段沿用 YAML
- YAML 没有但 DB 完整定义（含 transport + 主字段）→ 晋级为完整 server（DB 直接定义新 server）
- 字段不全的 DB-only override 忽略并记日志

`${VAR}` 语法用于 env / headers 值的环境变量插值；找不到变量保留字面量（避免启动 fail-fast，运行时连接失败更易定位）。

## 2. MCPServerConfig

| 字段 | 说明 |
|---|---|
| `transport` | `stdio`（需 `command`）/ `streamable_http`（需 `url`） |
| `command` / `args` / `env` | stdio 进程参数 |
| `url` / `headers` | streamable_http 端点 |
| `enabled` | 是否启用 |
| `default_tier` | 工具默认 tier（0–4，默认 2 = T2_NETWORK） |
| `tool_overrides` | per-tool tier 覆盖 |
| `tools`（include/exclude） | 工具白/黑名单（include 为空 = 全开） |
| `timeout` / `connect_timeout` | 调用 / 连接超时 |

transport 字段一致性由 `model_validator` 校验（stdio 必须有 command，streamable_http 必须有 url）。

## 3. 自动发现与启动

`MCPManager.start()`：
- **并行启动**所有 enabled server（`asyncio.gather`），避免慢 server（如 npx 首次拉包）拖延整体
- **degrade 模式**：单个 server 启动失败 / 未连接不影响其他，跳过并记日志
- 连接后 `_register_session_tools` 把发现的工具注册进 ToolRegistry

## 4. 工具注册映射

每个发现的 MCP 工具 → `ToolDefinition`：
- **命名**：`encode_tool_name(server, tool)` 加 server 前缀，避免跨 server 撞名
- **描述**：前缀 `[via MCP/{server}] ` + 原描述
- **tier**：`tool_overrides.get(tool) or default_tier`
- **side_effect**：`tier > 0`（保守置 True 让 winding_down 拦截；tier=0 视为只读）
- **过滤**：先过 `tools.include/exclude`

handler（`_make_handler`）把 MCP 调用结果的 content text 拼接为 `ToolResult`，`structuredContent` 放 `details.structured`，`isError` 映射 `is_error`。调用异常转 error_result 不抛。

## 5. 治理一致性

MCP 工具注册后与内建工具**完全同构**——同样受 PolicyEngine / tier / winding_down / 禁用列表约束。这是关键判断：外部工具不开后门，统一走治理管线。

**相关实现**：[../../impl/tools/](../../impl/tools/)
