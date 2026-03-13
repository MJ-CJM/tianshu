# Phase 2：引入平台化能力

> 对应架构设计 §1.5 Phase 2、§8.3

---

## 目标

独立化 Memory、CostManager，引入多 Provider 路由和多通道通知，实现统一 PluginApi，使系统具备平台化服务能力。

## 本阶段制度映射

| 部院 | 模块 | 本阶段实现内容 |
|------|------|--------------|
| 文渊阁 | `Memory` | 经验沉淀、历史检索、消息压缩 |
| 户部 | `CostManager` | Token/成本统计、预算熔断、资源账本 |
| 吏部 | `PluginApi` | 统一注册（Tool/Hook/Channel/Provider/Skill/Command） |
| 通政司（增强） | `Notifier` | 飞书/钉钉/邮件多通道、邸报式汇总 |
| 礼部（增强） | `Skills` | 自动安装（brew/apt/pip/go） |
| 御案台（增强） | `Web Dashboard` | 成本报表台 + Provider/插件管理台 |
| 御案台（增强） | `CLI` | 成本查询 + Provider 状态 + 插件列表 |

## 本阶段参考来源

| 参考 | 设计点 | 落点 Step |
|------|--------|----------|
| [OpenClaw-1] | Compaction（分片压缩 + 标识符保留 + Context Window Guard） | Step 2.1 |
| [OpenClaw-7] | 统一 PluginApi 注册 | Step 2.6 |
| [CoPaw-9] | Provider 路由策略（按成本/能力/任务类型） | Step 2.3 |
| [ZeroClaw-7] | Provider 能力声明 | Step 2.3 |
| [PicoClaw-2] | 通道限速与重试 | Step 2.4 |
| [CoPaw] | 高级记忆压缩 | Step 2.1 |

## 运行方式

平台化 Web 服务。在 Phase 1 的 FastAPI + 事件驱动基础上，扩展平台化模块。

## 前置条件

- Phase 1 全部 Step 通过验收

## Phase 验收标准（§8.3）

- [ ] `Memory`、`CostManager` 已独立模块化
- [ ] 成本预算可以熔断
- [ ] 多通道通知与 Provider 路由可配置
- [ ] 统一 `PluginApi` 注册接口可用
- [ ] Web 界面提供成本报表台、Provider/插件管理台
- [ ] CLI 支持 `cost summary`、`provider list`、`plugin list`

---

## Step 拆分

### Step 2.1 — 文渊阁：Memory 模块独立化

**目标**：实现文渊阁 Memory 模块，支持经验沉淀、历史检索和消息压缩。

**涉及文件**
```
src/tianshu/memory/
  __init__.py
  manager.py
  compactor.py
  backends/
    __init__.py
    sqlite_backend.py
```

**依赖**：无

**验收条件**
- [ ] `recall(query)` 检索历史经验（向量或关键词匹配）（§5.6）
- [ ] `store(memorial)` 沉淀终态任务摘要和可复用知识
- [ ] `compact(messages)` 消息压缩：分片压缩 + 标识符保留 + 合并摘要 + 最近保留 [OpenClaw-1]（§4.2）
- [ ] Context Window Guard：监控 Token 使用量，低于硬性最低值时拒绝继续执行（§4.2）
- [ ] 消费 `execution.completed`、`execution.failed`、`audit.completed` 事件（§5.6）
- [ ] 检索结果仅作为参考，不强制约束 Planner 决策（§5.6 边界）
- [ ] 通过 `before_agent_start` 钩子注入历史经验
- [ ] 通过 `before_compaction` 钩子提取重要信息
- [ ] 单元测试覆盖存储、检索、压缩、Context Window Guard

**复杂度**：高

---

### Step 2.2 — 户部：CostManager 独立化

**目标**：实现户部 CostManager 模块，支持 Token/成本统计和预算熔断。

**涉及文件**
```
src/tianshu/cost/
  __init__.py
  manager.py
  tracker.py
  budget.py
```

**依赖**：无

**验收条件**
- [ ] per-request 和 per-task 的 Token 消耗、API 调用费用累计（§5.7）
- [ ] Token 或成本超过 `Edict.runtime.token_budget` / `cost_budget_usd` 时，发射 `cost.budget_exceeded` 事件（§5.7）
- [ ] 预算熔断触发执行终止（§4.5）
- [ ] 按 Edict / submitter / 时间段维度的成本汇总报表（§5.7）
- [ ] 通过 `llm_output` 钩子累计 Token 统计
- [ ] 通过 `before_agent_start` 钩子做预算预检
- [ ] 单元测试覆盖统计、熔断、报表

**复杂度**：中

---

### Step 2.3 — 多 Provider 路由

**目标**：实现 Provider 能力声明和按成本/能力/任务类型选择模型的路由策略。

**涉及文件**
```
src/tianshu/providers/
  __init__.py
  protocol.py
  manager.py
  litellm_provider.py
```

**依赖**：Step 2.2

**验收条件**
- [ ] Provider 能力声明：是否支持原生工具调用、视觉、流式等 [ZeroClaw-7]（§6.3）
- [ ] 路由策略接口：按成本、能力、任务类型选择模型 [CoPaw-9]（§6.3）
- [ ] 默认路由策略：便宜模型做简单任务，贵模型做复杂任务（§5.7）
- [ ] API 配额追踪：RPM/TPM 配额监控，接近限额时降速或切换（§5.7）
- [ ] CostManager 提供成本数据辅助路由决策
- [ ] Provider 切换对上层透明（统一接口）
- [ ] 单元测试覆盖路由选择、配额降速、Provider 切换

**复杂度**：中

---

### Step 2.4 — 通政司：多通道通知扩展

**目标**：扩展 Notifier 支持飞书/钉钉/邮件通道，实现邸报式定期汇总。

**涉及文件**
```
src/tianshu/notifier/
  notifier.py（增强）
  renderer.py（增强）
  rate_limiter.py
```

**依赖**：Notifier 基础（Phase 1 Step 1.7）

**验收条件**
- [ ] 飞书通道：通过飞书 Bot API 发送消息
- [ ] 钉钉通道：通过钉钉 Bot API 发送消息
- [ ] 邮件通道：通过 SMTP 发送通知
- [ ] 各通道的渲染格式适配（Markdown / 富文本 / HTML）
- [ ] per-channel 速率限制 [PicoClaw-2]（§5.5）
- [ ] 邸报式定期汇总：日报/周报任务执行摘要（§5.5）
- [ ] 通道配置通过 ConfigManager 管理
- [ ] 单元测试覆盖各通道渲染、限速、汇总

**复杂度**：中

---

### Step 2.5 — 都察院/文渊阁/户部：Phase 2 钩子扩展

**目标**：补充 Phase 2 引入的生命周期钩子。

**涉及文件**
```
src/tianshu/executor/agent.py（增强）
```

**依赖**：Step 2.1

**验收条件**
- [ ] 新增 `llm_input` 钩子：发送 LLM 请求前，内容检查和修改（§5.4）
- [ ] 新增 `before_compaction` 钩子：消息压缩前，提取重要信息（§5.4）
- [ ] 新增 `session_start` / `session_end` 钩子：会话级统计初始化和清理（§5.4）
- [ ] Memory 模块通过 `before_compaction` 钩子保留关键标识符
- [ ] CostManager 通过 `session_start` / `session_end` 做会话级统计
- [ ] 单元测试覆盖新钩子触发和消费

**复杂度**：低

---

### Step 2.6 — 吏部：Skills 自动安装与统一 PluginApi

**目标**：实现 Skills 自动安装（brew/apt/pip/go）和统一的 PluginApi 注册接口。

**涉及文件**
```
src/tianshu/skills/loader.py（增强）
src/tianshu/plugins/
  __init__.py
  api.py
```

**依赖**：Step 2.3 + Step 2.4

**验收条件**
- [ ] Skills 自动安装：根据 SKILL.md 的 `metadata.openclaw.install` 字段，自动安装缺失的依赖（§6.4）
- [ ] 支持 `brew`、`apt`、`pip`、`go` 四种安装方式
- [ ] 安装源安全校验（防止恶意包）
- [ ] 统一 `PluginApi` 注册接口 [OpenClaw-7]（§6.4）：
  - `register_tool`：注册 Agent 工具
  - `register_hook`：注册生命周期钩子
  - `register_channel`：注册通知通道
  - `register_provider`：注册 LLM Provider
  - `register_skill`：注册 Skill 文档
  - `register_command`：注册用户命令
- [ ] 独占槽位机制：同一类型同一时间只能有一个活跃实例（§6.4）
- [ ] 单元测试覆盖自动安装、注册、独占槽位

**复杂度**：高

---

### Step 2.7 — 户部：成本报表台

**目标**：可视化成本/用量报表，支持按 Edict、提交者、时间段维度查看。

**涉及文件**
```
web/src/
  api/cost.ts
  stores/costStore.ts
  pages/CostDashboardPage.tsx
  components/cost/
    CostChart.tsx                 # 趋势图（recharts 或 antd Charts）
    UsageSummary.tsx              # 汇总卡片
    CostTable.tsx                 # 明细表
  components/layout/Sidebar.tsx   # 修改：加"户部"导航
  App.tsx                         # 修改：加 /cost 路由
```

**依赖**：Step 2.2（CostManager 报表 API）

**验收条件**
- [ ] 顶部汇总卡片：总 Token、总成本（USD）、活跃任务数、平均每任务成本
- [ ] 折线图：近 30 天每日 Token 用量与成本趋势
- [ ] 明细表：按 Edict 维度展示（目标、提交者、Token、成本、时长、状态）
- [ ] 筛选器：日期范围、提交者、状态
- [ ] 导出 CSV
- [ ] 预算进度条：有 `token_budget` / `cost_budget_usd` 的 Edict 显示使用率
- [ ] Provider 成本对比柱状图（Phase 2.3 数据）

**复杂度**：中

---

### Step 2.8 — 吏部：Provider 与插件管理台

**目标**：管理界面展示 LLM Provider 状态和已注册插件。

**涉及文件**
```
web/src/
  api/providers.ts / api/plugins.ts
  pages/ProviderManagePage.tsx / pages/PluginRegistryPage.tsx
  components/layout/Sidebar.tsx   # 修改：加"吏部"导航
  App.tsx                         # 修改：加 /providers、/plugins 路由
```

**依赖**：Step 2.3（多 Provider 路由）+ Step 2.6（PluginApi）

**验收条件**
- [ ] Provider 列表：展示所有 Provider 及能力声明（原生工具调用、视觉、流式、每千 Token 成本）
- [ ] Provider 健康状态指示器（healthy / degraded / offline）
- [ ] 路由策略展示：当前使用的路由策略（成本优先 / 能力优先）
- [ ] 插件注册表：列出所有已注册插件（tool / hook / channel / provider / skill / command）
- [ ] 每个插件项展示：类型、名称、描述、状态、独占槽位标记
- [ ] Phase 2 为只读展示（不通过 UI 增删改）

**复杂度**：低

---

### Step 2.9 — 御案台：CLI 平台指令

**目标**：扩展 CLI，支持成本查询、Provider 状态、插件列表。

**涉及文件**
```
src/tianshu/cli/commands/
  cost.py                         # tianshu cost summary
  provider.py                     # tianshu provider list
  plugin.py                       # tianshu plugin list
```

**依赖**：Step 0.12（CLI 基础）+ Step 2.2（CostManager）+ Step 2.3（多 Provider）+ Step 2.6（PluginApi）

**验收条件**
- [ ] `tianshu cost summary [--period day|week|month] [--submitter USER]` 展示成本汇总表格
- [ ] 汇总包含：总 Token、总成本（USD）、按 Provider 分组、按时间段分组
- [ ] `tianshu provider list` 展示 Provider 列表（名称、状态、能力标签、配额使用率）
- [ ] Provider 状态着色：healthy=绿色、degraded=黄色、offline=红色
- [ ] `tianshu plugin list` 展示插件列表（名称、类型、状态）
- [ ] 所有命令支持 `--format json` 输出

**复杂度**：低

---

## Step 依赖关系图

```
          2.1 Memory ──────────> 2.5 Phase 2 钩子扩展
                                                       ┐
          2.2 CostManager ──> 2.3 多 Provider 路由 ────┤
                                                       ├──> 2.6 PluginApi
          2.4 多通道通知（基于 Phase 1 Notifier）──────┘

          2.2 CostManager ──────────────────────> 2.7 成本报表台
          2.3 多 Provider ──┐
                            ├──> 2.8 Provider/插件管理台
          2.6 PluginApi ───┘

          0.12 CLI 基础 ──┐
          2.2 CostManager ┼──> 2.9 CLI 平台指令
          2.3 多 Provider ─┤
          2.6 PluginApi ───┘
```

**可并行组**：
- 组 A：Step 2.1（Memory）、Step 2.2（CostManager）— 无依赖
- 组 B：Step 2.4（多通道通知）— 独立，基于 Phase 1 Notifier
- 组 C：Step 2.5（钩子扩展）— 依赖 2.1
- 组 D：Step 2.7（成本报表台）— 依赖 2.2
- 组 E：Step 2.8（Provider/插件管理台）— 依赖 2.3 + 2.6
- 组 F：Step 2.9（CLI 平台指令）— 依赖 0.12 + 2.2 + 2.3 + 2.6

Step 2.7 可在 2.2 完成后立即开始。Step 2.8 和 2.9 需等待 2.6 完成。

## 测试策略

| 层次 | 覆盖范围 | 工具 |
|------|---------|------|
| 单元测试 | 每个模块独立验证 | pytest |
| 集成测试 | 完整链路含 Memory/CostManager | pytest + mock |
| 性能测试 | SQLite 并发读写、事件日志吞吐 | pytest-benchmark |

## 风险

| 风险 | 缓解措施 |
|------|---------|
| Memory 检索质量不稳定 | 先用关键词匹配跑通，后续可引入向量检索 |
| 多 Provider 的 function calling 行为差异 | Provider 能力声明显式标注，路由时自动匹配 |
| Skills 自动安装的安全风险 | 安装源白名单 + 校验 + 用户确认机制 |
