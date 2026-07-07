# 六部官员与诏令路由

## 1. 设计意图

天枢用「六部」官员把执行主体组织成一个有分工的朝廷：每个官员有固定职责域与工具权限上限，诏令按性质路由到对口官员。设计目标是让「谁来做」可解释、可配置、可演化——同一制度框架下，替换或升级某个官员不影响流程。

## 2. AgentPersona 模型

每个官员由 `AgentPersona`（pydantic）定义，关键字段：

| 字段 | 作用 |
|---|---|
| `id` / `name` / `department` / `title` | 身份与部门内职务 |
| `soul_path` / `role_path` / `memory_path` | SOUL/ROLE（运行时副本）+ MEMORY（模板）路径 |
| `tools_allowed` / `tools_denied` / `tool_tier_max` | 工具白/黑名单与最高 tier |
| `skills_allowed` / `skills_dir` | 技能过滤与专属技能目录 |
| `can_delegate` / `delegates_to` | 委派能力与目标官员 |
| `memory_global_read` | 高权限：绕过记忆访问控制读所有 persona 记忆 |
| `llm_config_name` | persona 级 LLM 配置覆盖（None=全局） |

`DEFAULT_EXECUTOR_ID = "bingbu"` 是全局默认执行者，DAG 节点 persona 缺失时回退到它。

## 3. 六部职责

| 官员 | 部门 | 人格 | 职责 | 工具定位 |
|---|---|---|---|---|
| neige 内阁首辅 | 内阁 | 缜密、全局视野、善拆解 | 规划、子任务拆解、官员选择、协调 | 只读分析 |
| bingbu 兵部尚书 | 兵部 | 果断、行动导向、务实 | 执行、工具调用、代码/命令操作 | 唯一执行工具持有者 |
| ducha 都察院 | 都察院 | 严谨、怀疑、规则导向 | 审计结果、查越权、评风险、verdict | 只读审计 |
| tongzheng 通政使 | 通政司 | 善表达、条理清晰 | 渲染、通知、待批推送、会诊主持 | 只读渲染 |
| wenyuan 文渊阁 | 文渊阁 | 博闻强记、善关联 | 知识检索、记忆归纳、文档 | 知识管理 |
| hubu 户部尚书 | 户部 | 精打细算、数据敏感 | 成本追踪、预算熔断、token 统计 | 只读统计 |

## 4. court 朝廷共享

`court` 不是独立 persona，而是共享上下文目录：
- `personas/court/COURT.md` —— 朝廷协议、官员间规则（PromptBuilder Layer 2）。
- `personas/court/MEMORY.md` + `~/.tianshu/memory/court/` —— 朝堂共享长期记忆（Layer 6）。

## 5. OfficialSelector 路由

诏令→官员两条路径：

**TASK_DEPARTMENT_PREFERENCE**（task_type → department）：

| task_type | department |
|---|---|
| plan | neige |
| execute | bingbu |
| audit | ducha |
| notify | tongzheng |
| memory | wenyuan |
| cost | hubu |

**_DEPARTMENT_KEYWORDS**（关键字打分，用于 `select_for_task(description)`）：

| department | keywords |
|---|---|
| bingbu | execute / run / deploy / build / implement / create |
| ducha | audit / review / check / inspect / verify / validate |
| hubu | cost / budget / finance / expense / token |
| wenyuan | memory / knowledge / search / recall / document |
| tongzheng | notify / alert / message / report / communicate |
| neige | plan / strategy / coordinate / decide / synthesize |

API 契约：
- `select(task_type)` —— 按偏好表取对口部门首个 persona。
- `select_for_task(description)` —— 关键字打分取最高分 department。
- `_fallback_persona()` —— 优先非 neige 的任一 persona，都无则 neige。
- `get_default_map()` / `get_keyword_map()` —— UI 展示当前任务→官员映射，缺对口官员时标 `is_fallback`。

## 6. 委派边界

persona 可声明 `can_delegate=True` + `delegates_to` 列表，但内建工具当前不消费这两个字段，委派由 plugin / skill 自行实现。多任务的真正并发分派由 DAG（agent 子系统）承接，而非 persona 层委派。

**相关实现**：[../../impl/persona/](../../impl/persona/)
