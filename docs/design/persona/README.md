# persona 人格系统 · 设计总览

> 制度定义「做什么」，官员定义「谁来做、以什么风格做」。

## 1. 职责定位

persona 子系统用明朝「六部」官员隐喻组织执行主体。制度层（六部职责框架）定义系统**做什么**，官员层（Agent Persona）定义**谁来做、以什么风格做**——每个执行主体的人格、职责、记忆、技能、工具权限与 LLM 配置。两层分离使制度可复用、官员可替换、风格可调控而不改流程逻辑。

子系统负责四件事：定义官员模型、把诏令路由到合适官员、把多层上下文注入 system prompt、周期性合成官员成长画像。

## 2. 核心设计判断

- **模板 / 运行时双层存储**：身份文件 `personas/{department}/`（git 模板，seed 源）与 `~/.tianshu/personas/{id}/`（运行时私有副本，可独立演化）分离。UI 修改只落运行时，git 永不动；`git = 起点，~/.tianshu = 真相源`。
- **SQLite 为主、文件为 seed**：`PersonaLoader` 以 `personas` 表为 primary，文件目录仅作模板源；DB 存的 path 被忽略，运行时目录权威。
- **多层 prompt 注入有序叠加**：从 Base Identity 到 Task Context 约十余层有序拼接，后注入优先级更高；身份卡（Layer 2.5）作权威身份覆盖下文旧描述。
- **路由可解释**：`OfficialSelector` 用任务类型偏好表 + 关键字打分两条路径选官，映射对 UI 可见、可解释。
- **画像自动成长**：`ProfileSynthesizer` 按 AGENT_END 计数 + cron 周期合成 `PROFILE.md`，规则聚合 + LLM 归纳分工。
- **模板库 vendored**：`TemplateLibrary` 把 agency-agents 模板拆成 SOUL/ROLE 两文件，供新建官员快速 seed。

## 3. 六部官员与 court

| id | 部门 | 代码中的角色 |
|---|---|---|
| `neige` | 内阁 | Planner 默认人格；战略规划与跨部门协调 |
| `bingbu` | 兵部 | `DEFAULT_EXECUTOR_ID`；默认执行者，唯一执行工具持有者 |
| `ducha` | 都察院 | 审计、Code Review、verdict |
| `tongzheng` | 通政司 | 渲染、通知、会诊主持 |
| `wenyuan` | 文渊阁 | 文档、知识管理、记忆归纳 |
| `hubu` | 户部 | 成本审查、budget/token |
| `court` | 朝廷 | 共享上下文目录（`COURT.md`/`MEMORY.md`），不是独立 persona |

## 4. 与相邻子系统关系

| 相邻子系统 | 关系 |
|---|---|
| agent | `PromptBuilder` 为 Agent 构建 system prompt；DAG 节点按 `assigned_official` 取 persona |
| planner | neige 作默认规划人格；plan 的 `assigned_official` 决定节点 persona |
| memory | `PromptBuilder` 读 `~/.tianshu/memory/` 记忆层；画像合成消费 DrawerStore |
| skills | prompt 注入 skill 索引；画像健康度读 SkillMetrics |

## 5. 本目录子文档索引

| 文档 | 主题 |
|---|---|
| [officials.md](./officials.md) | 六部官员职责、court 共享、OfficialSelector 路由 |
| [prompt-builder.md](./prompt-builder.md) | PromptBuilder 多层注入顺序 |
| [profile.md](./profile.md) | ProfileSynthesizer 画像合成、TemplateLibrary 模板库 |

**相关实现**：[../../impl/persona/](../../impl/persona/)
