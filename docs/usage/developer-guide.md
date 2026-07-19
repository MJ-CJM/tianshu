# 开发者扩展指南

二次开发的常见扩展点。每节给「最小步骤 + 代码落点 + 详细文档」。实现细节见各 [`../impl/<子系统>/`](../impl/)，设计意图见各 [`../design/<子系统>/`](../design/)。

## 扩展工具（Tool）

- 落点：`src/tianshu/tools/builtins.py`（实现）+ `tools/registry.py`（用 `ToolDefinition` 注册到 `ToolRegistry`）。
- 关键字段：`ToolTier`（风险分级，决定是否需人工裁决）、`max_result_chars`（结果自动截断）。
- 治理：注册后自动经 `PolicyEngine` / `ApprovalManager`，无需自己写权限逻辑。
- 详见 [../design/tools/registry.md](../design/tools/registry.md)、[../design/tools/policy.md](../design/tools/policy.md)、[../impl/tools/](../impl/tools/)。

## 扩展技能（Skill）

- 落点：内建放 `src/tianshu/skills/builtin/<name>/SKILL.md`；用户级放 `~/.tianshu/skills/`（workspace / user 层）。
- 格式：`SKILL.md`（frontmatter 元数据 + 正文）；`always` 决定是否常驻注入，否则渐进加载（`load_index` 只注入名称+描述，LLM 按需 `skill_view`）。
- 三层加载优先级 builtin < user < workspace。
- 详见 [../design/skills/loader.md](../design/skills/loader.md)、[../impl/skills/](../impl/skills/)。

## 扩展人格（Persona）

- 落点：Git 模板 `personas/{id}/`（`SOUL.md` 性格 / `ROLE.md` 职责 / `MEMORY.md` 记忆）；运行时覆盖在 `~/.tianshu/personas/{id}/`。
- 路由：`OfficialSelector` 按部门关键词把诏令分派给官员；默认执行官 `bingbu`。
- 详见 [../design/persona/officials.md](../design/persona/officials.md)、[../impl/persona/](../impl/persona/)。

## 改 Prompt 注入层

- 落点：`persona/prompt_builder.py` 的 `PromptBuilder.build()`（多层有序注入：system / court / 身份 / role / 记忆 / L1 抽屉 / 部门记忆 / 同僚画像 / skills / 任务上下文）。
- 详见 [../design/persona/prompt-builder.md](../design/persona/prompt-builder.md)。

## 扩展通知渠道（Channel）

- 落点：`notifier/channels/`，继承 `base.py` 的 `NotificationChannel`，在 `channel_registry.py` 注册。
- 已有 `FeishuChannel` / `DingTalkChannel` / `EmailChannel` 可参照。
- 详见 [../design/interfaces/channels.md](../design/interfaces/channels.md)、[../impl/interfaces/](../impl/interfaces/)。

## 写插件（Plugin）

- 落点：`plugins/`（`PluginLoader` 扫描插件目录下的 `manifest.json` → `PluginManifest`），经 `PluginApi` 统一注册 Tool / Hook / Channel / Provider / Skill。
- 端到端示例见 [extension-guide.md](extension-guide.md)；机制见 [../design/plugins/README.md](../design/plugins/README.md)、[../impl/plugins/README.md](../impl/plugins/README.md)。

## 加 LLM Provider

- 落点：`providers/`（LiteLLM 适配）+ `config_manager.py`（多配置）。`ProviderManager` 负责能力/配额/限速/fallback。
- 详见 [../design/llm/client.md](../design/llm/client.md)、[../impl/llm/](../impl/llm/)。

## 前端加页面

- 落点：`web/src/pages/` 新增组件 + `web/src/App.tsx` 注册路由。
- 详见 [../design/interfaces/web.md](../design/interfaces/web.md)、[frontend-dev.md](frontend-dev.md)。

## 加 Agent 生命周期 Hook

- 落点：实现 `HookType`（`before_tool_call` / `after_tool_call` / `agent_end` / `before_iteration` 等）对应回调，注册到 `HookRegistry`，按 priority 排序。
- 详见 [../design/agent/hooks.md](../design/agent/hooks.md)。
