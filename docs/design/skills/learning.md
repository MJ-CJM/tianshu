# 技能学习 — 实时学习、执行后复盘、周期修撰、评分

> 设计意图：让技能库随使用自我进化——发现即存、用后复盘、闲时策展，且全程不碰内建/人工技能。

## 1. 三层学习机制

| 机制 | 触发 | 范围 | LLM | 写入 |
|---|---|---|---|---|
| 前台实时 | Agent 调 `skill_manage(action=create)` | 即时 | 无（Agent 自己写） | 立即可用 |
| SkillReviewHandler | `AGENT_END` 每 N 次任务 | 单任务复盘 | 1 次轻量调用 | create / update |
| SkillCurator（修撰） | idle 周期 / 手动 | 全部 agent 自建技能 | plan + iterate | 合并 / 归档 / 迭代 |

## 2. 前台实时学习

`skill_manage` 工具让 Agent 在任务执行中即时落库（不等任务结束）。index footer 明确提示：「发现非显而易见的可复用方法时，**当下**就 `skill_manage(action='create')` 保存，立即可经 `skill_view` 取用」。

action：create / edit / patch / delete / activate / write_file / remove_file。create 时打 `created_by='agent'` 标记（供 curator 识别），写资源文件经 Guard 扫描，emit `skill.learned` 事件。

## 3. SkillReviewHandler — 执行后复盘

注册在 `AGENT_END`。`_should_review` 门控：`exit_reason==COMPLETED` 且 `iteration_count>=3` 且距上次复盘 `>=skill_review_interval` 次任务。命中后跑一次轻量 LLM：

输入 = goal + exit_reason + iteration + 工具调用摘要 + 现有技能 index；输出 JSON `{action: create|update|skip, skill_name, reason, content?, patch_old?, patch_new?}`。

规则：只存需 trial-and-error 或非显而易见的方法；已有技能覆盖则 update；否则 skip。create 走 `SkillValidator.validate(source="agent-created")`，通过才 `create_skill` + `metrics.ensure_exists(created_by="agent")` + emit `skill.learned`。复盘永不 block hook（异常吞掉）。

## 4. SkillCurator（修撰）— 周期策展

显示名「修撰」（仿翰林院修撰）。骨架对齐 `ProfileSynthesizer`：**gate → 生命周期迁移 → 收集候选 → 一次结构化 JSON LLM plan → 确定性 apply → 审计报告 + 事件**。

### 安全边界（关键判断）

- 只操作 `created_by=='agent'` 的技能，**永不碰** builtin / 人工技能
- **归档非删除**（可 `loader.restore_skill` 恢复）；pinned 技能豁免；`dry_run` 只预览

### gate

非 dry_run 时需：`_idle_ok`（`last_activity_at` 距今 ≥ `skill_curator_idle_hours`，默认 2）+ `try_acquire_synthesis_lock`（与画像合成共用锁机制）。

### 三步动作

1. **生命周期迁移**（纯函数 `apply_automatic_transitions`，无 LLM）：按 `last_used_at`(fallback `created_at`) 年龄 — `>archive_after_days(90)` 归档；`>stale_after_days(30)` 标 stale；回到窗口内则 reactivate
2. **consolidation / archival**（≥2 候选时 LLM plan）：把近似技能合并成上位「伞」技能（`into` + `into_content` + `absorb` 列表），或单纯归档陈旧技能。apply 确定性执行，校验 `into` 不撞非 agent 技能、过 validator，absorb 跳过 pinned/非 agent
3. **单条迭代**（`_iterate_pass`）：对低成功率技能（`list_iteration_candidates`）让 LLM 产出改进版 SKILL.md，过 validator 后 `save_skill`

报告写 `{runtime_dir}/curator/{ts}/`（run.json + REPORT.md），全程 emit `curate.started/completed/skipped/failed`。

## 5. SkillMetricsStore — 评分

SQLite `skill_metrics` 表，`SkillMetrics`（frozen dataclass）字段：

| 分组 | 字段 |
|---|---|
| 使用 | `usage_count` / `success_count` / `failure_count` / `last_used_at` |
| 来源 | `created_by`（manual/agent） / `source_edict_id` / `created_at` |
| curator 生命周期 | `state`（active/stale/archived） / `pinned` / `archived_at` / `absorbed_into` |
| 人在环 | `human_curated`（golden，curator 跳过迭代） / `last_human_action` |

派生属性：
- `success_rate`：`usage<3` 返 None（数据不足）
- `status`：`rate<0.3 且 usage>=5` → retire_suggested；`rate<0.6` → warning；否则 healthy（驱动 index 的状态标）
- `is_dormant(90)`：超 90 天未用

`skill_view` 工具调用时 `increment_usage`；成功/失败归因由 `_active_skills` 模块级集合在执行结束时结算。

**相关实现**：[../../impl/skills/](../../impl/skills/)
