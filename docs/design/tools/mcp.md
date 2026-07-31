# MCP 集成 — 服务器配置、自动发现与启动

> 当前实现边界：MCP 工具接入 ToolRegistry 后仍走 tier + 治理管线，但这不是“所有 MCP
> 传输都安全开放”。`secure-remote` 下 remote `streamable_http` MCP 明确拒绝；
> `stdio` 必须有显式非空 `tools.include`。公开支持边界见
> [安全威胁模型](../../security/lean-preview-threat-model.md)。

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
| `tools`（include/exclude） | 工具白/黑名单；stdio 的 include 必须显式非空，否则不准入 |
| `timeout` / `connect_timeout` | 调用 / 连接超时 |

transport 字段一致性由 `model_validator` 校验（stdio 必须有 command，streamable_http 必须有 url）。

## 3. 自动发现与启动

`MCPManager.start()` 先做 Lean admission，再启动通过的 server：

- disabled server 不启动；
- `secure-remote + streamable_http` 拒绝，reason=`trusted_egress_unavailable`；
- `stdio + tools.include=[]` 拒绝，reason=`approved_tools_required`；
- 准入拒绝写入 SystemAudit，但“拒绝路径已验证”不等于远程 MCP 安全已实现。

对通过准入的 server：

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

MCP 工具注册后与内建工具走同一 PolicyEngine / tier / winding_down / 禁用列表。当前
证明范围只到这里：

- `tools.include` 约束注册名，不持久绑定 executable realpath/digest、argv、env、cwd、
  actor/reason/expiry 或 discovered-tool drift；
- trusted-local 的 stdio 子进程不是 OS 安全沙箱；
- secure-remote 缺少受验证 sandbox/egress 时 fail closed，不静默回退宿主；
- 不能因为配置模型中存在 URL/streamable_http 字段，就宣称 remote MCP 可对公网开放。

**相关实现**：[../../impl/tools/](../../impl/tools/)
