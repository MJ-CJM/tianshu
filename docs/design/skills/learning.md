# 技能学习 — 当前治理边界

## 1. 状态总览

| 机制 | 当前状态 | 是否调用 LLM | 是否改变 live |
|---|---|---:|---:|
| Agent `skill_list` / `skill_view` | 可用 | 否 | 否 |
| Agent `skill_manage` 写操作 | 生产不注册 | 否 | 否 |
| HTTP 新建 / 修改 | 创建治理候选 | 否 | 否 |
| `SkillReviewHandler` | 默认关闭；治理写路径不可用时提前跳过 | 否 | 否 |
| `SkillCurator` 非 dry-run | 默认关闭；提前返回治理服务未接通 | 否 | 否 |
| `SkillCurator` 人工 dry-run | 可显式预览 | 是 | 否 |
| Skill Candidate 晋升 | 由 Evolution / `PromotionService` 管理 | 视门禁而定 | 通过治理后才是 |

当前不存在“Agent 写完立即生效”的公开路径。

## 2. 候选提案

HTTP 新建或修改会快照当前 Skill 包并产生不可变 candidate identity，返回
`candidate_id` 与 lifecycle。提案必须经过证据绑定和 gate；stage 明确返回
`live_changed=false`。真正生效与回滚使用通用 Evolution Candidate API。

Web 目前只展示 live 目录和详情。候选工作台未形成完整的普通用户旅程，因此不在 Skill
页暴露新建/保存操作。

## 3. Reviewer 费用保护

`SkillReviewHandler` 的历史逻辑仍可解析任务、生成 create/update/skip 建议，但写入端尚未
连接 `SkillInstallService` 和 `PromotionService`。生产装配传入
`governed_writes_available=False`；handler 在 `_should_review` 和 LLM 调用之前返回。
`skill_review_enabled=True` 的 API 写入也会返回 409，避免用户误以为开关生效。

## 4. Curator 费用保护

`SkillCurator` 保留候选收集、结构化计划、低成功率迭代、报告与事件代码，用于后续接线和
人工 dry-run。生产非 dry-run 在治理写路径不可用时直接返回：

```json
{"skipped": "governed_skill_service_required"}
```

它不会先做 LLM 规划再丢弃结果。`skill_curator_enabled` 默认关闭。

## 5. Metrics

`SkillMetricsStore` 继续记录 `usage_count`、成功/失败、来源、状态、Pin 和人工标记。
`skill_view` 会增加使用计数；执行结束可对本轮实际加载的 Skill 结算效果。这些指标是候选
评估信号，不等于自动获得 live 写权限。

**相关实现**：[../../impl/skills/](../../impl/skills/)
