# Tools（工具与治理）— 实现现状

**相关设计**：[../../design/tools/](../../design/tools/)

覆盖 `src/tianshu/tools/`（registry / builtins / policy / policy_rules / hongluisi / mcp）+ `src/tianshu/executor/{policy_hook,approvals}.py`。

## 1. 关键类 / 文件路径

| 文件 | 关键类 / 函数 | 职责 |
|---|---|---|
| `tools/registry.py` | `ToolRegistry` / `ToolDefinition` | 注册中心 + execute（tier 校验 / 快路径 / hook / winding_down） |
| `tools/types.py` | `ToolResult` / `ToolHook` / `ToolTier` | 结果与 tier 枚举（T0–T4） |
| `tools/builtins.py` | `register_builtins` | shell_exec / read_file / write_file + 套件 + 鸿胪寺 + 敕令工具挂接 |
| `tools/policy.py` | `PolicyEngine` / `PolicyContext` / `PolicyDecision` / `PolicyRule` | 规则管线决策核心 |
| `tools/policy_rules/__init__.py` | `build_default_rules` | 7 条内建规则按 priority 组装 |
| `tools/policy_store.py` | `SessionRule` / `*SessionRuleStore` / `compute_fingerprint` / `assert_can_grant` | session rule 信任缓存 |
| `tools/policy_profile.py` | `PolicyProfile` / `BUILTIN_TEMPLATES` / `resolve_network_for_edict` / `expand_profile_to_rules` | 任务级权限预配 |
| `executor/policy_hook.py` | `PolicyHook` | `BEFORE_TOOL_CALL` priority=5，委托 PolicyEngine + session rule + 审批 |
| `executor/approvals.py` | `ApprovalManager` | tool-call / memorial / outer-loop 三类审批 |
| `tools/hongluisi/tools.py` | `register_hongluisi` + 4 工具 | 网络工具注册 |
| `tools/hongluisi/{policy,ssrf_guard,rate_limiter,router,engine_registry,api_request}.py` | NetworkPolicy / validate_url / RateLimiter / FetchRouter / build_engines | 网络策略与引擎 |
| `tools/mcp/{manager,config,client}.py` | `MCPManager` / `MCPServerConfig` / `MCPServerSession` | MCP 集成 |

## 2. policy_rules 规则表

| 文件 | 规则 | priority | verdict 倾向 |
|---|---|---|---|
| `tier_escalation.py` | `TierEscalationRule` | 100 | tier 提升 → require_approval |
| `workspace_boundary.py` | `WorkspaceBoundaryRule` | 90 | 越界 → deny |
| `bash_safety.py` | `BashSafetyRule` | 80 | 黑名单 deny / 白名单 allow / 其余 require_approval |
| `lark_cli_safety.py` | `LarkCliSafetyRule` | 80 | 写操作 require_approval |
| `network_safety.py` | `NetworkSafetyRule` | 75 | profile/host 校验 + 写方法审批 |
| `approval_required_list.py` | `ApprovalRequiredListRule` | 70 | 命中列表 require_approval |
| `default_tier.py` | `DefaultTierRule` | 10 | T3/T4 require_approval 兜底 |

## 3. 核心流程

### 工具执行

```text
ToolRegistry.execute(name, args, lifecycle_phase)
  → 存在/禁用检查 → tier 校验(非法→T4)
  → winding_down + side_effect → 拦截
  → JSON 解析 + jsonschema.validate → drop 未声明字段
  → T0: func(**args) 直返 | 非T0: before hooks → func → after hooks
```

### 审批决策

```text
BEFORE_TOOL_CALL → PolicyHook.on_before_tool_call (priority=5)
  → PolicyEngine.evaluate(ctx)
  → require_approval: session_rule_store.find_match 命中→allow
  → emit policy.decision / policy.session_rule_matched
  → deny: HookResult(block) | require_approval: _request_approval
      → emit tool.approval_required (EventBus.fire + WS broadcast)
      → ApprovalManager.wait_for_approval (asyncio.Event, 300s)
      → Decree approve→放行 / reject/timeout→block
```

### 网络调用

handler 内 `_resolve_edict_context`（ambient edict + `resolve_network_for_edict`）→ profile/host 校验 → `RateLimiter.check` → engine dispatch（SSRF validate_url 在 router/engine 内）。

## 4. 数据库表

| 表 | 用途 |
|---|---|
| `session_rules` | always-scope session rule（`SqliteSessionRuleStore`） |
| `tool_switches` | 工具禁用列表（`apply_disabled`） |
| `network_credentials` | 网络凭证（CredentialStore） |
| `mcp_server_overrides` | MCP 配置 override |
| `events` | policy.decision / tool.approval_required / decree.* 审计 |

## 5. 扩展点

- **新策略规则**：实现 `PolicyRule` Protocol，加入 `build_default_rules`（注意 priority）
- **新 fingerprint 算法**：`policy_store._FINGERPRINT_FUNCS` 增映射
- **新 PolicyProfile 模板**：`policy_profile.BUILTIN_TEMPLATES` 增条目
- **新网络 engine**：`hongluisi/engine_registry.build_engines` 注册，`NetworkPolicy` fetch_engines 引用
- **新 SSRF 黑名单**：`ssrf_guard` 的 deny 常量
- **新 MCP server**：写 YAML 或 DB override，`MCPManager.start` 自动发现
- **新内建工具**：`register_*` 函数 + `register_builtins` 挂接

## 6. 注意点（与旧 impl 文档纠偏）

- `PolicyHook` priority 是 **5**（不是 0/50）；ApprovalManager 旧 `on_before_tool_call` 已退化为 no-op，实时审批由 PolicyHook 在 require_approval 分支直接调
- session rule scope 为 `edict`(InMemory) / `always`(Sqlite)，allow-once 体现为不升级 session rule
- 网络 profile 解析统一走 `resolve_network_for_edict`（NetworkSafetyRule 与 hongluisi 共用）
