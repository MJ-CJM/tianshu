# Skills（技能系统）— 当前设计

## 1. 当前能力边界

Skill 是一个目录和其中的 `SKILL.md`。运行时按 builtin < user < workspace 三层加载，
默认只向 Agent 注入名称和描述；全文由 `skill_view` 按需读取，`always=true` 才常驻。

当前公开能力分为两条：

| 路径 | 当前行为 |
|---|---|
| 读取与使用 | `GET /api/skills`、`GET /api/skills/{name}`、`skill_list`、`skill_view` 可用 |
| 变更与生效 | HTTP 新建/修改只创建治理候选，不直接改 live；门禁、canary、晋升和回滚走 Evolution Candidate / `PromotionService` |

Web 将人工技能和 Agent 来源技能合并成一个只读目录，仅保留详情与 Agent 技能 Pin。
它不会把候选提案提示成“已保存”，也不展示固定返回 409 的删除、归档或编辑按钮。

## 2. 为什么不直接写 live

Skill 会进入后续 Agent prompt，直接写入等同于改变平台行为。当前采用候选治理：

1. `POST /api/skills` 或 `PUT /api/skills/{name}` 生成 candidate；
2. `POST /api/skills/candidates/{id}/gate/evaluate` 校验证据与门禁；
3. stage 只准备候选，不改变 live；
4. 真实 activation / rollback 由
   `/api/evolution/candidates/{id}/{canary,promote,rollback}` 和
   `PromotionService` 持有权威。

`DELETE /api/skills/{name}` 与 `/api/skills/{name}/archive` 在治理删除链未接通前固定
fail closed。兼容的 `skill_manage` handler 仍保留单元测试，但生产 Agent 不注册该工具。

## 3. 自动学习现状

旧设计中的“任务中即时写 Skill”“任务后 LLM 复盘自动写入”和“每周修撰自动合并”
尚未接入上述候选与晋升链：

- `skill_review_enabled` 与 `skill_curator_enabled` 默认关闭；
- reviewer 即使读到旧配置为开启，也会在 LLM 调用前因治理写路径不可用而跳过；
- curator 的非 dry-run 同样在 LLM 调用前返回
  `governed_skill_service_required`；
- 人工显式 `dry_run` 可生成整理预览，但不会写 live。

这样不会为一个必然无法生效的结果产生后台 LLM 费用。

## 4. 仍然有效的基础设施

- `SkillsLoader`：三层加载、缓存、热重载和 workspace overlay；
- `SkillValidator` / `SkillsGuard`：名称、frontmatter、内容和包安全检查；
- `SkillMetricsStore`：使用次数、成功率、来源、状态与 Pin；
- Skill Candidate：证据绑定、门禁、stage、canary、晋升与回滚；
- `skill_list` / `skill_view`：Agent 的只读发现与加载入口。

## 5. 子文档

| 文档 | 内容 |
|---|---|
| [loader.md](loader.md) | 加载层级、缓存、热重载与底层写原语 |
| [learning.md](learning.md) | 自动学习的当前禁用边界、候选提案与费用保护 |
| [guard.md](guard.md) | Guard、信任等级与候选安装安全 |

**相关实现**：[../../impl/skills/](../../impl/skills/)
