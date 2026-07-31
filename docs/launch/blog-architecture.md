# 深度技术文：治理 × 可验证成长，一个 Agent OS 的架构取舍

> 当前版本：v0.4.2。本稿解释现有实现与长期方向的边界，不是正式发布稿。每项成熟度和非保证以[能力事实矩阵](capability-matrix.md)为准。

## 一句话

天枢是一个可治理、可验证、持续成长的自进化 Agent OS。长期方向是让治理证据约束成长；当前版本先在可信本地、单机单节点范围内建立 Native 执行闭环，并把外部执行与演化能力明确标为实验。

## 一、为什么是异步事件链，而不是又一个对话 Agent

对话式 Agent 解决“人在场”的即时交互；日报、巡检和多轮长任务更需要后台推进、状态查询与事后复盘。天枢把受支持的主链里程碑组织为：

```text
Edict → Scheduler → Planner → Native Agent / DAG / long-task loop
      → Auditor → Notifier → Memory / Profile / Skill candidates
```

里程碑事件携带 `edict_id`，并可写入 SQLite 时间线。子系统内部仍允许直接调用，避免为了“全事件驱动”制造额外复杂度。这里的边界也必须说清：当前 EventBus 不是持久消息队列，不保证分布式投递或任意故障点的 exactly-once。

## 二、治理不是一个开关，执行器边界决定保证

Native 路径的已注册工具调用经过 tier、策略规则和人工裁决钩；这些检查发生在工具真正执行前。运行时层还提供出站脱敏、bash 分段风险分级、子进程 clean-env 与分级急停。

这些机制组成 defense-in-depth，但不是绝对安全承诺：

- HTTP、WebSocket 与 MCP 已共用认证上下文；secure-remote 普通主体按任务 owner 隔离，
  admin 才能读取全局审计与平台配置，但正式支持范围仍是 trusted-local；
- Decision 请求和裁决结果已经持久化；受支持的 managed continuation 能从持久状态
  恢复，但不把任意旧版/外部等待协程都提升为耐重启保证；
- 成本门禁依据 provider 已上报用量，是 best-effort，可能在越过阈值后才停止；
- clean-env、独立目录与本地子进程都不是容器或 OS 安全沙箱。

影子快照使用独立 `GIT_DIR`，不会把快照仓写进用户项目的 `.git`。当可信 Git 可用且
治理工作区准备成功时，客卿运行前恢复点会被强制建立；运行后仍可产生用于检查的快照。
这不等于可靠的事前动作拦截、Provider 侧硬成本上限或受治理的 staging/apply 协议。

## 三、“成长”先产生候选，“进化”必须有证据门

v0.4.2 已有三类成长载体：记忆与画像积累、受治理技能候选、Universe 行为/代码快照。
当前 Lean Core 已实现 evidence-bound 技能候选 Gate、持久 assignment/effective overlay
和受控回滚；Legacy Universe 可以做 snapshot、branch、diff、评估与推荐，但不能切换
live 行为或部署代码。

它们仍是实验能力：评估子进程共享宿主 OS 权限与网络；代码候选不会自动晋升；当前
实现的 Lean 技能路径不能外推成任意代码变体或 external executor 的统一 G4。OpenHands、
executor compatibility、ROI、成本校准与 full G4 仍需外部证据。

因此“持续成长”描述候选与证据的累积，“自进化闭环已成立”必须等 G4 的端到端证据。默认关闭演化只是必要条件，不是安全证明。

## 四、与 Claude Code/Codex 的双向连接并不对称

第一条路径是本地 MCP：MCP host 可以在共用认证上下文中向天枢提交 Edict、查询状态和
读取受支持结果。remote MCP 与 open stdio MCP 的正式开放面保持 disabled；它不是公共
互联网服务。

第二条路径是 Keqing：天枢可以启动 Claude Code/Codex CLI。当前 adapter 为
`contained + experimental`，默认关闭且不占默认导航；凭据由外部 CLI self-managed，
生产 credential gateway 不可启用。它保证独立工作目录、clean-env、外围 timeout、
事后结果归一，以及在可信 Git 与治理工作区条件满足时建立运行前恢复点；不保证 CLI
内部事件完整性、可靠的事前工具拦截、Provider 侧硬成本上限、网络隔离、耐重启或受治理
的 apply/merge。

把这两条路径分开描述很重要：MCP 接入天枢不等于天枢能看见外部 CLI 的内部工具流。G4 的目标是让 Native 与至少一个 external managed adapter 遵守同一可验证契约。

## 五、明制隐喻是信息架构，不是安全模型

内阁、兵部、都察院、通政司等名称帮助用户理解规划、执行、审计和通知的职责。真正形成产品边界的是模块、权限、事件、持久状态和能力矩阵，而不是隐喻本身。

这也是新中式 UI 应保持克制的原因：文化感来自秩序、留白、材质和命名；核心状态仍要使用可验证的数字、证据来源和明确动作。完整映射见[隐喻对照表](metaphor-map.md)。

## 尾声

天枢选择的差异化方向是“治理 × 可验证成长”。v0.4.2 已提供有限但可运行的本地主链、
持久治理和 desktop Web，并把 external managed executor、full G4、官方发行与跨平台
外部复验留在后续 Gate。开源竞争力最终来自这些边界能否被第三方复验，而不是把路线图
写成现成功能。

代码、测试、设计和 ADR 均在仓库中；公开承诺从[能力事实矩阵](capability-matrix.md)开始阅读。
