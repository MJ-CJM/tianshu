# 自改进能力统一视图（当前实现）

本页区分“采集信号”“形成候选”“改变 live”三个状态。产生分析报告、candidate 或推荐，
都不自动等于运行时已经改变。

## 1. 当前能力表

| 机制 | 采集/分析 | 形成候选 | 改变 live |
|---|---:|---:|---:|
| Persona 画像合成 | 可用 | 不适用 | 原子更新 `PROFILE.md` |
| Skill 使用指标 | 可用 | 不适用 | 不改内容 |
| HTTP Skill 提案 | 可用 | 可用 | 仅经 PromotionService 晋升后 |
| Agent Skill 即时写入 | 不公开 | 否 | 否 |
| 任务后 Skill reviewer | 默认关闭，LLM 前跳过 | 否 | 否 |
| 周期 Skill curator | 默认关闭；dry-run 可预览 | dry-run 仅计划 | 否 |
| 行为 Universe 演化 | 可生成快照并做沙箱配对评估 | `evaluated/recommended` | 当前无生产 activation adapter |
| Code variant | 可提案、门禁、配对评估 | `evaluated/recommended` | 当前无生产 activation adapter |
| Governed Skill Candidate | 证据与 gate 可用 | 可用 | canary / promote / rollback 可用 |

## 2. 当前时间线

```text
任务执行
  ├─ skill_list / skill_view：读取 live Skill，记录使用指标
  └─ AGENT_END：画像计数与其他已接通 hook

后台定时
  ├─ profile.daily_synthesis：画像合成
  ├─ skill.weekly_curate：默认关闭；非 dry-run 在 LLM 前 fail fast
  ├─ universe.daily_evolve：实验性快照、变异、沙箱评估与推荐
  └─ universe.daily_code_propose：实验性代码提案与评估

治理候选
  提案 → 证据 → gate → stage → canary → 人工决定 → promote / rollback
```

系统 cron 的存在不代表对应功能默认开启或能改变 live。Scheduler 会记录系统 job
运行状态，并收敛上个进程遗留的 running；同名 job 不并发重入。

## 3. Skill 的费用保护

过去的设计让 reviewer 和 curator 先调用 LLM，再因治理写路径缺失而丢弃结果。当前：

- 两个自动开关默认关闭；
- reviewer 即使被旧配置打开，也在 LLM 前跳过；
- curator 非 dry-run 在 LLM 前返回
  `governed_skill_service_required`；
- Agent prompt 不再引导调用未注册的 `skill_manage`。

人工可显式执行 curator dry-run 获取计划，但它明确不写 live。

## 4. Universe 与 Candidate 的边界

Legacy Universe 负责快照、分支、diff、归档、恢复和评估记录。旧 switch、rollback 和
promote-code 入口固定 fail closed，不能把推荐伪装成上线。

通用 Evolution Candidate 使用不可变 `RunAssignmentV1` 和 effective overlay 绑定运行。
当前生产只有 Skill Candidate 具备真实 activation/rollback adapter；行为 Universe 与
Code variant 均止于评估和推荐。

## 5. 共享信号

| 数据 | 当前用途 |
|---|---|
| `SkillMetrics` | 使用/成功率、Pin、候选评估信号 |
| Memorial / Audit / Cost | 画像、诊断、配对评估 |
| Evidence Bundle | candidate gate 与人工决策证据 |
| RunAssignment + effective overlay | 受管 canary 的不可变运行归因 |
| synthesis lock / idle gate | 后台任务互斥与空闲保护 |

## 6. 阅读入口

- Skill：[../skills/](../skills/)
- Persona 画像：[../persona/profile.md](../persona/profile.md)
- Universe：[../universe/](../universe/)
- 受治理自进化目标架构：[../self-evolving-agent-os/](../self-evolving-agent-os/)
- 当前能力事实：[../../CURRENT-STATE.md](../../CURRENT-STATE.md)
