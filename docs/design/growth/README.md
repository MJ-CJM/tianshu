# 自改进多环统一视图（跨子系统）

天枢让 agent 随使用变强，靠的不是一个总控演化器，而是**四条独立运转、时间尺度各异的自改进环**：前台实时学技能、任务后复盘、后台周期策展、位面进化。本篇是跨子系统的**元文档（design-only）**——只讲四条线如何在时间轴上分工与协作，机制细节各回其主文档。

> 这是一篇 cross-cutting 视图，不引入新实现。各机制的设计/契约见：技能学习 [../skills/learning.md](../skills/learning.md)、画像合成 [../persona/profile.md](../persona/profile.md)、位面演化 [../universe/evolution.md](../universe/evolution.md)、代码变体回放评估 [../universe/code-variant.md](../universe/code-variant.md)。

## 1. 为什么分多环

一个改进动作的**代价**与**反馈延迟**差异巨大：写一条技能笔记几乎零成本、当下可用；归纳一份官员画像要积累几十次任务才有信号；切换一份行为配置（位面）会影响后续所有诏令，必须先在小流量上验证。把它们塞进一个环，要么慢动作拖累快反馈，要么快动作绕过了该有的验证。

因此天枢按**反馈周期**切成四环——越靠前越轻、越即时、越局部；越靠后越重、越周期、越全局。每环只做自己尺度内合适的事，彼此通过**共享数据底座**（技能库 + SkillMetrics + memorial 事件）松耦合协作，而非直接调用。

| 环 | 时间尺度 | 触发 | LLM 用量 | 改动范围 | 可逆性 |
|---|---|---|---|---|---|
| ① 前台实时 | 秒级（任务内） | Agent 主动调 `skill_manage` | 无 | 单条技能 | 删除即撤 |
| ② post-task 复盘 | 任务级（每 N 次） | `AGENT_END` hook | 1 次轻量调用 | 单条技能 create/update | 可归档 |
| ③ 后台策展 | 周级（idle） | cron / 手动 | 1 次结构化 plan + 迭代 | 全部 agent 自建技能 | 归档非删除 |
| ④ 位面进化 | 日级 + 滚动样本 | cron / 手动 | 1 次定向变异 | 整份行为配置（人格/技能/config） | 切换/回滚 |

## 2. 四条线在时间轴上的位置

```text
任务执行中 ──┐
            │  ① 前台实时：Agent 发现可复用方法 → skill_manage(create)
            │     立即落库、当下 skill_view 可取用（不等任务结束）
            ▼
任务结束 ───┤  AGENT_END hook 扇出两个独立 handler：
            │  ② SkillReviewHandler.on_agent_end  → 满足门控则复盘本任务，create/update 技能
            │  ② ProfileTrigger.handle_agent_end  → persona 任务计数每满 20 触发画像合成
            ▼
系统空闲 ───┤  cron 定时（共享 synthesis lock + idle 闸，互斥不打架）：
            │  ③ skill.weekly_curate   (0 4 * * 0) SkillCurator.run     合并/归档/迭代自建技能
            │  ③ profile.daily_synthesis (0 3 * * *) ProfileTrigger.run_for_all_personas
            │  ④ universe.daily_evolve  (0 5 * * *) UniverseEvolver.run 变异→分支→选优→晋升
            ▼
        （所有写入沉回技能库 / SkillMetrics / PROFILE.md / 位面快照，喂给下一轮）
```

注册点：`AGENT_END` 两个 handler 在 `app.py` lifespan 装配；三个 cron 由 `Scheduler.register_system_jobs` 注册（`scheduler.py`）。

## 3. 四条线各自做什么

### ① 前台实时（tools/skill_tools.py）

任务执行中 Agent 通过 `skill_manage` 工具即时增删改技能，`action=create` 打 `created_by='agent'` 标记、emit `skill.learned`，立即可经 `skill_view` 取用。这是唯一**无 LLM 额外调用**的环——Agent 用本就在跑的那次推理顺手记下方法。

详见 [../skills/learning.md](../skills/learning.md) §2。

### ② post-task 复盘（AGENT_END hook，两个 handler）

任务一结束，`AGENT_END` 同时唤起两个互不相干的复盘 handler：

- **技能复盘** `SkillReviewHandler.on_agent_end`：门控 `_should_review`（`exit_reason==COMPLETED` 且 `iteration_count>=3` 且距上次 ≥ `skill_review_interval` 次任务）命中后跑一次轻量 LLM，输出 `{action: create|update|skip}`，把这次任务里值得沉淀但 Agent 当下没存的方法补成技能。复盘**永不 block hook**（异常吞掉）。见 [../skills/learning.md](../skills/learning.md) §3。
- **画像计数** `ProfileTrigger.handle_agent_end`：`increment_persona_task_counter` 每满 `PROFILE_TRIGGER_THRESHOLD`(20) 异步 `create_task` 触发一次该 persona 的画像合成。见 [../persona/profile.md](../persona/profile.md) §4。

两者都是「任务尺度」：单次任务太偶然，攒到一定次数才有归纳价值。

### ③ 后台策展（idle 周期）

空闲时把前两环零散攒下的产物**整理成体系**，只在系统闲、且抢到锁时跑：

- **SkillCurator（修撰，skills/curator.py）** `run`：只动 `created_by=='agent'` 的技能，把近似技能合并成上位「伞」技能、归档陈旧技能、迭代低成功率技能；**永不碰 builtin / 人工技能**，归档可恢复。见 [../skills/learning.md](../skills/learning.md) §4。
- **ProfileSynthesizer（persona/profile_synthesizer.py）** `run`：按 14 天窗口收集抽屉 + 事件 + SkillMetrics，归纳官员「擅长什么 / 状态健康度 / 退化迹象」，原子写 `PROFILE.md`，marker 之后的人工补注保留。见 [../persona/profile.md](../persona/profile.md) §2-3。

两者骨架同构：**gate（idle + lock）→ 采信号 → 一次结构化 JSON LLM plan → 确定性 apply → 审计报告 + 事件**。

### ④ 位面进化（universe/evolver.py）

最重的一环，改的是**整份行为配置**而非单点。`UniverseEvolver.run`：gate（`parallel_universe_enabled` + idle + lock）→ 退役连败候选 → 选优晋升满足 margin 的候选 → LLM 提**一处**定向变异 → 从冠军分支出候选位面 + mutator 落地。配套 **EvalHarness** 在沙箱回放评估集给代码变体打分（[../universe/code-variant.md](../universe/code-variant.md) §5.3）。

与前三环的本质区别：前三环改的产物**立即对所有任务生效**；位面进化把改动隔离进候选位面，先用 `universe_explore_ratio` 小流量探索 + 滚动适应度验证，达标才晋升为冠军——见 [../universe/evolution.md](../universe/evolution.md) §5-8。

## 4. 四环如何协作

四环不互相调用，靠**共享数据底座**接力——前环的产出是后环的输入：

| 共享底座 | 谁写 | 谁读 |
|---|---|---|
| 技能库（SKILL.md 文件） | ① skill_manage、② 复盘 create/update | ③ SkillCurator 整理、④ 位面快照纳入 `skills/` |
| `SkillMetrics`（usage/success/state） | `skill_view` 调用 + 执行结算 | ③ Curator 选迭代候选、③ ProfileSynthesizer 健康度、④ fitness |
| memorial 事件 + `universe_memorial_stats` | 每次执行/审计 | ③ 画像任务分布、④ `compute_fitness` 适应度 |
| synthesis lock + `last_activity_at` idle 闸 | — | ③④ 三个 cron 共用，保证后台环串行、不在系统忙时抢资源 |

**协作链举例**：Agent 在任务里 `skill_manage` 存一条技能（①）→ 复盘补全/纠正它（②）→ 多次使用后 SkillMetrics 累积成功率 → Curator 把它和近似技能合并成伞技能（③）→ 这份更优的技能集被某个候选位面快照、若适应度胜出则晋升为冠军（④）。一条方法就这样从「一次性灵感」逐级沉淀为「全局默认行为」。

**为何串行而非并行**：③④ 都改全局产物，且都需 LLM；synthesis lock 让任一时刻只有一个后台环在写，避免 Curator 改技能的同时 Evolver 又把旧技能集快照进位面这类竞态。

## 5. agent 如何随时间变强

把四环叠起来看，agent 的成长是一条**由快到慢、由局部到全局、由即兴到固化**的流水线：

- **分钟级**：遇到新方法当下就记（①），下个工具调用即可复用。
- **小时/任务级**：复盘补全遗漏、纠正错误技能（②）；画像计数累积。
- **天/周级**：策展去重归并、退役陈旧、迭代低效技能（③）；官员画像沉淀「擅长什么、是否退化」。
- **滚动样本级**：把以上所有产物组合成行为配置，在小流量上做 A/B，胜出者晋升为新基线（④）。

每一环都遵循项目同一套安全纪律：**派生可重算、改动可回退、人工可补注、自动永不碰人工产物**。这让「持续自改进」不以「不可控漂移」为代价——任一环出问题都能在它自己的尺度上撤销（删技能 / 归档 / 切回旧位面），不会污染其余三环。
