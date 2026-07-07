# Tools（工具与治理）— 设计总览

## 1. 职责定位

工具子系统是 Agent 与外界交互的唯一通道：文件读写、Shell、网络、敕令、MCP。它在「让 Agent 能做事」与「不让 Agent 越权」之间用**分层 tier + 策略管线 + 人工审批**做平衡。鸿胪寺（hongluisi）专管对外网络能力。

## 2. 核心设计判断

| 判断 | 选择 | 理由 |
|---|---|---|
| 权限模型 | **5 级 tier**（T0–T4），缺失/非法降级为 T4 | fail-secure：宁可多审批不可漏审批 |
| 快路径 | T0 只读工具跳过 hook 链 | 高频只读操作零治理开销 |
| 决策核心 | **PolicyEngine** 规则管线（priority 降序，deny/require_approval 短路） | 单规则 fail-open，整体引擎 fail-secure |
| 审批复用 | 审批通过可升级为 **session rule**（allow-once/edict/always） | 长任务不被同类调用反复打断 |
| 主动预配 | PolicyProfile 在任务启动时展开为 edict-scope rules | proactive 减少审批中断 |
| 网络隔离 | 鸿胪寺独立 profile + SSRF + 凭证系统托管 | 防 SSRF、防凭证泄露、防滥用 |
| 收尾保护 | winding_down 阶段拦截 side_effect 工具 | 软着陆只允许只读总结 |

## 3. 决策链一览

```text
Agent 工具调用 (非 T0)
  → BEFORE_TOOL_CALL hook
      → PolicyHook (priority=5): PolicyEngine.evaluate
          → 规则管线 (tier_escalation→workspace_boundary→bash/lark/network→approval_list→default_tier)
          → allow / deny / require_approval
      → require_approval: 先查 session rule cache 命中则放行
                          否则 ApprovalManager 等人工批红 → Decree → 可升级 session rule
  → ToolRegistry.execute (winding_down 拦截 side_effect)
```

## 4. 与相邻子系统关系

| 子系统 | 关系 |
|---|---|
| executor / agent | `ToolRegistry.execute` 在 ReAct 循环里调；`PolicyHook` / `ApprovalManager` 注册在 `BEFORE_TOOL_CALL` |
| edict / runtime | tier_overrides / approval_required_tools / policy_profile / api_request_hosts 均来自 `EdictRuntime` |
| storage | `session_rules`（always）/ `tool_switches` / `network_credentials` / events |
| notifier | deny / approval_required 广播 WS，飞书机器人接审批 |
| secrets（藏兵阁/vault） | 鸿胪寺 api_request 凭证按 host 系统注入 |

## 5. 本目录子文档

| 文档 | 主题 |
|---|---|
| [registry.md](registry.md) | ToolRegistry、ToolDefinition、tier 分级、builtins、结果截断 |
| [policy.md](policy.md) | PolicyEngine、policy_rules、SessionRuleStore、PolicyHook、ApprovalManager、PolicyProfile、winding_down |
| [network.md](network.md) | 鸿胪寺 web_fetch/web_search/web_extract/api_request、profile/白名单/SSRF/凭证/rate limit |
| [mcp.md](mcp.md) | MCP 服务器集成、配置合并、自动发现与启动 |

**相关实现**：[../../impl/tools/](../../impl/tools/)
