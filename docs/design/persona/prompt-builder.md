# PromptBuilder 多层注入

## 1. 设计意图

一个官员的 system prompt 不是单块文本，而是由身份、职责、记忆、技能、任务上下文等多个来源**有序叠加**而成。设计目标：每一层来源清晰、可单独预览（chars/tokens）、可缺省降级；**后注入层优先级更高**，并用权威身份卡覆盖下文里可能残留的旧身份描述，防止人格漂移。

## 2. 注入层顺序

`PromptBuilder.build(edict, persona, skills_char_budget=30000)` 按序拼接（仅 persona 非空时注入官员相关层）：

| Layer | 内容 | 来源 | 条件 |
|---|---|---|---|
| 1 | Base Identity | `_BASE_IDENTITY` 常量 | 恒有 |
| 2 | Court Protocol（COURT.md） | `resolve_court_read(~/.tianshu/personas/)`（overlay 优先，否则 `packaged_defaults()`） | persona 非空 |
| 2.5 | Identity Card（权威身份卡） | persona name+department+title | persona 非空 |
| 3 | SOUL.md（人格身份） | `~/.tianshu/personas/{id}/SOUL.md`（运行时） | persona 非空 |
| 4 | ROLE.md（角色职责） | `~/.tianshu/personas/{id}/ROLE.md`（运行时） | persona 非空 |
| 5 | MEMORY.md（核心长期记忆） | `~/.tianshu/memory/{id}/MEMORY.md` | persona 非空 |
| 5.1 | L1 Critical Facts | `DrawerStore.get_l1(id)` | `drawer_store + l1_enabled` |
| 5.5 | Recent Activity（近 2 天日志） | `~/.tianshu/memory/{id}/`（char_budget=2000） | persona 非空 |
| 5.6 | Department Memory（部门同侪池） | `~/.tianshu/memory/_dept/{department}/MEMORY.md` | persona 非空 |
| 6 | Court Memory | `~/.tianshu/memory/court/MEMORY.md` | persona 非空 |
| 6.5 | Peer Profiles（同僚近况） | 各 persona PROFILE，截 600 chars | `include_peer_profiles` |
| 7a | Skills 索引 | `SkillsLoader.load_index` | 恒有 |
| 7b | always=true skills 全文 | `SkillsLoader.load_always` | 恒有 |
| 7c | 网络能力说明 | `_NETWORK_CAPABILITY_HINT` 常量 | 恒有 |
| 8 | Task Context（`Current task ID: {edict.id}`） | edict | 恒有 |

## 3. 关键契约

- **身份卡覆盖**：Layer 2.5 在 SOUL.md 之前注入权威身份（id/name/department/title），覆盖任何下文中可能的旧身份描述——这保证 SOUL overlay 被编辑或实验性变异时身份不漂；不代表 SOUL 已进入生产 Candidate activation/rollback 闭环。
- **打包默认只读**：Layer 2 的默认 COURT 来自 `src/tianshu/resources/personas/court/COURT.md` 的打包视图；自定义协议只写 `~/.tianshu/personas/court/COURT.md` overlay。
- **SOUL 缺省降级**：SOUL.md 不存在时退化为 `You are {name}, serving in the {department} department...` 并打 warning，prompt 不中断。
- **记忆分层来源**：个人记忆（Layer 5）、部门同侪池（5.6）、朝堂共享（6）三级，从私有到公共逐级注入。
- **skills 按 persona 过滤**：`persona.skills_allowed` 非空时只注入白名单内 skills，索引（渐进加载）与 always 全文分开注入。
- **网络安全提示**：Layer 7c 固定注入 `api_request` 安全规则（禁止自带 Authorization、白名单 host、写方法需审批）。

## 4. build_layers 预览

`build_layers(edict, persona)` 返回每层 `{layer, name, source, chars, tokens_est}`（`tokens_est = chars // 4`），供 PersonaDetailPage 的 prompt preview 逐层展示各来源体量。

## 5. 边界

- 注入只读不写——PromptBuilder 不修改任何记忆文件，记忆写入由 memory 子系统的 MemoryManager 在 AGENT_END 承接。
- `_SANITIZE_PATTERNS`（```、[INST]、<|system|> 等）用于清洗注入内容，防 prompt 注入越权。

**相关实现**：[../../impl/persona/](../../impl/persona/)
