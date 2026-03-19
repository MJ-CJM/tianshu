# 天枢官员系统（Agent Persona）设计

> 制度定义"做什么"，官员定义"谁来做、以什么风格做"。

---

## 一、设计目标与定位

### 1.1 核心洞察

天枢的"制度层"（六部职责框架）定义了系统**做什么**——任务如何接入、如何执行、如何审计、如何通知。而"官员层"（Agent Persona）定义了**谁来做、以什么风格做**——每个执行主体的人格特质、专业技能、记忆经验和工具权限。

两层分离的好处：

- **制度可复用**：同一套治理流程适用于不同官员组合
- **官员可替换**：升级或替换某个官员的能力不影响制度框架
- **风格可调控**：通过调整 Persona 改变输出风格而不改变流程逻辑

### 1.2 约束

| Phase | 官员实现方式 | 说明 |
|-------|------------|------|
| Phase 0 | 单一通用 system prompt | 无官员概念 |
| Phase 1-2 | System prompt 注入 | 官员是 prompt 维度的差异化，不是独立进程 |
| Phase 3 | 独立 Agent 实例 | 各官员成为独立并发的 Agent Worker |

### 1.3 设计参考

- **OpenClaw SOUL.md + AGENTS.md**：单 Agent 人格定义模式，天枢适配为多角色组织结构
- **NanoBot MEMORY.md + HISTORY.md**：双层记忆模式，天枢扩展为官员私有 + 朝堂共享
- **NanoBot context.py**：4 层 context 注入，天枢扩展为 8 层注入顺序

---

## 二、官员模型（AgentPersona）

### 2.1 五维模型

每个官员由五个维度定义：

| 维度 | 含义 | 来源 |
|------|------|------|
| **SOUL**（人格） | 性格特质、思维方式、沟通风格 | `SOUL.md` |
| **ROLE**（职责） | 职责范围、决策权限、输出标准 | `ROLE.md` |
| **Memory**（记忆） | 私有经验、共享知识、历史教训 | `MEMORY.md` + 日志 |
| **Skills**（技能） | 专属 SKILL.md，指导特定任务的执行方式 | `skills/` |
| **Tools**（工具集） | 允许/禁止的工具、工具权限上限 | `AgentPersona` 模型字段 |

### 2.2 数据模型

```python
class AgentPersona(BaseModel):
    """官员定义模型"""
    id: str                          # 官员唯一标识，如 "neige"
    name: str                        # 官员名称，如 "内阁首辅"
    department: str                  # 所属部院，如 "内阁"

    # Bootstrap 文件路径
    soul_path: Path                  # SOUL.md 路径
    role_path: Path                  # ROLE.md 路径
    memory_path: Path                # MEMORY.md 路径

    # 技能与工具
    skills_dir: Path | None = None   # 专属 Skills 目录
    tools_allowed: list[str] = []    # 允许的工具列表（空 = 使用部院默认）
    tools_denied: list[str] = []     # 禁止的工具列表
    tool_tier_max: int = 0           # 最高可用工具 Tier（T0-T3）

    # 委派能力
    can_delegate: bool = False       # 是否可以委派子任务给其他官员
    delegates_to: list[str] = []     # 可委派的目标官员 ID 列表
```

---

## 三、六位核心官员定义

| 官员 | ID | 部院 | 人格关键词 | 工具 Tier | 引入阶段 |
|------|-----|------|----------|----------|---------|
| 内阁首辅 | `neige` | 内阁 | 缜密、全局视野、善于拆解 | T0 只读 | Phase 1 |
| 兵部尚书 | `bingbu` | 兵部 | 果断、行动导向、务实 | T2 执行工具 | Phase 1 |
| 都察院左都御史 | `ducha` | 都察院 | 严谨、怀疑、规则导向 | T0 只读 | Phase 1 |
| 通政使 | `tongzheng` | 通政司 | 善于表达、条理清晰 | T0 只读 | Phase 1 |
| 文渊阁大学士 | `wenyuan` | 文渊阁 | 博闻强记、善于关联 | T0 知识管理 | Phase 2 |
| 户部尚书 | `hubu` | 户部 | 精打细算、数据敏感 | T0 只读 | Phase 2 |

### 3.1 内阁首辅（neige）

- **人格**：缜密周全，全局视野，善于将复杂问题拆解为可执行的子任务
- **职责**：任务规划、子任务拆解、官员选择、优先级排定、资源协调
- **特殊权限**：可读所有官员的记忆（Phase 2+），可委派任务给所有其他官员
- **工具 Tier**：T0（只读分析工具），不直接执行业务工具

### 3.2 兵部尚书（bingbu）

- **人格**：果断务实，行动导向，注重效率和结果
- **职责**：执行子任务、工具调用、代码操作、命令执行
- **特殊权限**：唯一拥有 T2 执行工具的官员
- **工具 Tier**：T2（外部副作用工具），是天枢的"行动之手"

### 3.3 都察院左都御史（ducha）

- **人格**：严谨细致，天生怀疑，规则导向，不放过任何可疑之处
- **职责**：审计执行结果、检查越权、评估风险、决定 pass/flag/block
- **特殊权限**：可拦截任何执行流（block 权力）
- **工具 Tier**：T0（只读审计工具）

### 3.4 通政使（tongzheng）

- **人格**：善于表达，条理清晰，能将复杂结果转化为用户易懂的汇报
- **职责**：渲染结果、格式适配、通知投递、待批推送、汇总报告
- **特殊权限**：无特殊权限
- **工具 Tier**：T0（只读渲染工具）

### 3.5 文渊阁大学士（wenyuan）

- **人格**：博闻强记，善于关联不同领域的知识，擅长归纳总结
- **职责**：经验沉淀、知识检索、记忆归纳、Reflect 循环执行
- **特殊权限**：唯一可写入朝堂共享记忆的官员；负责 Reflect 循环
- **工具 Tier**：T0（知识管理工具）

### 3.6 户部尚书（hubu）

- **人格**：精打细算，数据敏感，对成本和效率有天然的警觉
- **职责**：Token 统计、成本追踪、预算熔断、资源配额监控
- **特殊权限**：可触发预算熔断中断执行
- **工具 Tier**：T0（只读统计工具）

---

## 四、Bootstrap 文件体系

### 4.1 目录结构

```
personas/
  court/                           # 朝堂共享
    COURT.md                       # 朝堂协议、官员间规则
    MEMORY.md                      # 共享长期记忆
    memory/
      YYYY-MM-DD.md                # 共享日志
  neige/                           # 内阁首辅
    SOUL.md                        # 人格定义
    ROLE.md                        # 职责说明
    MEMORY.md                      # 个人长期记忆
    memory/
      YYYY-MM-DD.md                # 个人日志
    skills/                        # 专属技能（可选）
  bingbu/                          # 兵部尚书
    SOUL.md
    ROLE.md
    MEMORY.md
    memory/
  ducha/                           # 都察院左都御史
    SOUL.md
    ROLE.md
    MEMORY.md
    memory/
  tongzheng/                       # 通政使
    SOUL.md
    ROLE.md
    MEMORY.md
    memory/
  wenyuan/                         # 文渊阁大学士（Phase 2）
    SOUL.md
    ROLE.md
    MEMORY.md
    memory/
  hubu/                            # 户部尚书（Phase 2）
    SOUL.md
    ROLE.md
    MEMORY.md
    memory/
```

### 4.2 注入顺序

系统提示按以下顺序构建，后注入的内容优先级更高：

```
1. Base Identity          → 天枢系统基础身份（所有官员共享）
2. COURT.md               → 朝堂协议、官员间规则和沟通约束
3. SOUL.md                → 当前官员的人格定义
4. ROLE.md                → 当前官员的职责说明
5. Per-agent MEMORY.md    → 当前官员的个人长期记忆
6. Court MEMORY.md        → 朝堂共享长期记忆
7. Skills                 → 通过资格检查的 SKILL.md
8. Task Context           → 当前任务的 Edict + Plan + 相关上下文
```

### 4.3 向后兼容

无 Persona 时（Phase 0 或未配置官员的任务），系统回退到通用 system prompt，行为与 Phase 0 完全一致。Persona 注入是增量能力，不改变已有行为。

---

## 五、记忆架构

### 5.1 两层记忆

| 层级 | 存储位置 | 读权限 | 写权限 | 内容类型 |
|------|---------|--------|--------|---------|
| **官员私有** | `personas/<id>/MEMORY.md` + `memory/` | 本人 + 内阁 | 本人 | 域内经验、个人教训、执行心得 |
| **朝堂共享** | `personas/court/MEMORY.md` + `memory/` | 所有官员 | 仅文渊阁 | 跨域知识、重要决策、组织经验 |

### 5.2 Source of Truth

- **Markdown 为真相来源**：所有记忆以 Markdown 文件存储，人类可读可编辑
- **派生索引为加速层**：SQLite FTS5 全文检索（Phase 2）+ 可选向量索引（Phase 3），均从 Markdown 派生，可随时重建

### 5.3 记忆分类标记

每条记忆条目携带分类标记和置信度：

| 标记 | 含义 | 示例 |
|------|------|------|
| `W` | 世界知识（World） | "Python 3.12 支持 type parameter syntax" |
| `B` | 经验知识（Best practice） | "并发 HTTP 请求应设置超时" |
| `O` | 观点判断（Opinion） | "FastAPI 比 Flask 更适合异步场景" |
| `S` | 观察事实（Observation） | "用户偏好 JSON 格式输出" |

置信度：`high` / `medium` / `low`，影响检索时的排序权重。

### 5.4 实体引用

记忆中的实体使用 `@entity_name` 格式引用，便于检索和关联：

```markdown
- [B/high] @web_search 工具在 @bingbu 执行搜索任务时，应先用关键词精确搜索再扩展
- [S/medium] @user_chen 偏好结果以表格形式呈现
```

---

## 六、Retain / Recall / Reflect 循环

### 6.1 Retain（保留 — 执行后）

**触发时机**：`agent_end` 钩子

**执行流程**：

1. 各官员从自己的执行上下文中提取域内经验
2. 经验按分类标记（W/B/O/S）+ 置信度格式化
3. 写入官员个人日志（`personas/<id>/memory/YYYY-MM-DD.md`）
4. 关键经验同时追加到官员 `MEMORY.md`

**作用域隔离**：每个官员只提取和存储自己职责域内的经验。内阁不会存储工具调用细节，兵部不会存储审计判断。

### 6.2 Recall（回忆 — 执行前）

**触发时机**：`before_agent_start` 钩子

**执行流程**：

1. 根据当前任务上下文构建检索查询
2. 查询官员私有记忆（`personas/<id>/MEMORY.md`）
3. 查询朝堂共享记忆（`personas/court/MEMORY.md`）
4. 按相关性和置信度排序，截取 top-K 条目
5. 注入到系统提示的 Task Context 段

**跨层检索**：Recall 同时查询私有和共享两层，确保官员既有自己的经验又有组织的知识。

### 6.3 Reflect（反思 — 定期）

**执行者**：文渊阁大学士（`wenyuan`）

**触发时机**：

- Phase 2：通过 `agent_end` 钩子在每次执行后判断是否需要反思
- Phase 2+：可选定期调度（heartbeat），如每日凌晨归纳

**执行流程**：

1. 扫描各官员近期日志（`personas/*/memory/YYYY-MM-DD.md`）
2. 识别跨官员的共性模式和重要教训
3. 归纳沉淀到朝堂共享记忆（`personas/court/MEMORY.md`）
4. 淘汰过时、低置信度或已被更新的条目
5. 更新官员个人 `MEMORY.md` 中的摘要

**集中化**：Reflect 只由文渊阁执行，避免多个官员同时修改共享记忆导致冲突。

### 6.4 多 Agent 适配

| R/R/R 环节 | 单 Agent（Phase 0） | 多 Persona（Phase 1-2） | 多 Agent 实例（Phase 3） |
|-----------|-------------------|----------------------|----------------------|
| Retain | 无 | 各官员独立提取，写入各自日志 | 各 Worker 并发写入，通过文件锁隔离 |
| Recall | 无 | 单层检索 → 双层检索（私有+共享）| 双层检索 + 向量索引加速 |
| Reflect | 无 | 文渊阁执行后触发式反思 | 文渊阁定期调度 + 并发安全 |

---

## 七、官员间通信协议

### 7.1 Phase 1-2：EventBus 结构化消息

官员间通信通过 `EventBus` 结构化消息实现，而非自由文本 LLM 对话。这确保通信可审计、可追踪、语义明确。

核心通信事件：

| 事件 | 发起方 | 接收方 | 说明 |
|------|--------|--------|------|
| `plan.task_assigned` | 内阁 | 兵部/都察院/通政使 | 分配子任务给指定官员 |
| `execution.result` | 兵部 | 都察院/通政使/内阁 | 执行结果报告 |
| `audit.verdict` | 都察院 | 内阁/通政使 | 审计结论 |
| `memory.insight` | 文渊阁 | 内阁/相关官员 | 检索到的历史经验 |
| `cost.alert` | 户部 | 内阁/兵部 | 成本预警 |

### 7.2 Phase 3：会商协议

Phase 3 引入"会商"机制——内阁召集多位官员并行提供视角，综合决策：

```
内阁发起会商请求
  → 多官员并行分析（各自视角）
    → 兵部：可行性评估
    → 都察院：风险评估
    → 户部：成本评估
    → 文渊阁：历史经验
  → 内阁综合各方意见
  → 输出最终决策
```

会商规则：

- 只有内阁可以发起会商
- 各官员独立并行分析，不相互干扰
- 内阁综合时以制度框架为准绳，不完全取决于多数意见
- 会商记录写入朝堂共享记忆

---

## 八、演进路径

| Phase | Persona | Memory | Communication |
|-------|---------|--------|---------------|
| 0 | 单一通用 prompt | 无 | 函数调用 |
| 1 | 4 官员 persona 注入（neige, bingbu, ducha, tongzheng） | 文件读写 MEMORY.md | EventBus 结构化消息 |
| 2 | 6 官员 + 完整 R/R/R 循环（新增 wenyuan, hubu） | FTS5 检索 + 分类标记 | EventBus + hooks |
| 3 | 并发多官员实例 + 会商协议 | 向量索引 + 跨官员共享 | Lane-based 并发 + 会商 |

### 8.1 Phase 0 → Phase 1 迁移

- **无破坏性变更**：Persona 注入是纯增量能力
- **渐进开启**：可逐个开启官员，先从内阁 + 兵部开始
- **回退安全**：禁用 Persona 后行为与 Phase 0 完全一致

### 8.2 Phase 1 → Phase 2 迁移

- **新增 2 位官员**：文渊阁大学士和户部尚书
- **记忆升级**：从文件读写升级为 FTS5 检索
- **Reflect 循环引入**：文渊阁开始主动归纳经验

### 8.3 Phase 2 → Phase 3 迁移

- **实例化**：官员从 prompt 维度升级为独立 Agent Worker
- **并发**：多官员可真正并行执行
- **会商**：引入多官员协商决策机制

---

## 九、参考采纳矩阵

| 参考项目 | 来源 | 采纳点 | 天枢落点 | 引入阶段 |
|---------|------|--------|---------|---------|
| OpenClaw | SOUL.md / AGENTS.md | 单 Agent 人格定义 | 多角色 Bootstrap 文件体系 | Phase 1 |
| OpenClaw | Memory R/R/R | Retain/Recall/Reflect 循环 | 多官员适配的 R/R/R | Phase 2 |
| OpenClaw | Markdown-as-SOT + SQLite FTS5 | 记忆存储与检索 | 两层记忆 + FTS5 派生索引 | Phase 2 |
| NanoBot | MEMORY.md + HISTORY.md | 双层记忆模式 | 官员私有 + 朝堂共享 | Phase 1 |
| NanoBot | context.py 4-layer | 多层 context 注入 | 8 层注入顺序 | Phase 1 |
| NanoBot | heartbeat | 定期巡检/反思 | Reflect 调度 | Phase 2 |
| DeepAgents | SubAgent context isolation | 子代理上下文隔离 | 官员间作用域隔离 | Phase 1 |
| ZeroClaw | Memory trait | 记忆协议抽象 | 记忆接口统一抽象 | Phase 2 |
