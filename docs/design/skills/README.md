# Skills（技能系统）— 设计总览

## 1. 职责定位

技能系统让 Agent 拥有「可渐进加载、可自我学习、可安全治理」的复用知识库。每个 skill 是一个目录 + 一份 `SKILL.md`（frontmatter + body），Agent 按需查看、执行后沉淀、周期性策展。大量借鉴 hermes-agent（见 `guard.py` / `fuzzy_match.py` 头部 "Ported from hermes-agent"）。

## 2. 核心设计判断

| 判断 | 选择 | 理由 |
|---|---|---|
| 注入策略 | **渐进加载**：默认只注入 index（名+描述），全文用 `skill_view` 按需取 | 省 context；`always=true` 才注入全文 |
| 三层来源 | builtin < user < workspace（后者覆盖同名） | 内建可被工作区定制覆盖 |
| 学习时机 | 前台**实时**（Agent 在任务中即时 create）+ 后台复盘（reviewer）+ 周期策展（curator） | 不等任务结束，发现即存 |
| 自建 vs 内建 | curator 只动 `created_by=='agent'` 的技能 | 永不碰 builtin / 人工技能 |
| 删除策略 | **归档非删除**（`.archive/`，可 restore） | 自动化决策可逆 |
| 安全边界 | 安装/写入前过 Guard（13 类威胁 + 无形 Unicode） | trust level 决定 block/ask/allow |

## 3. 三套学习机制对比

| 机制 | 触发 | 范围 | 动作 |
|---|---|---|---|
| 前台实时 | Agent 调 `skill_manage(create)` | 任意时刻 | 立即可用（写文件 + metrics） |
| SkillReviewHandler | `AGENT_END`（间隔 N 次任务） | 单次任务复盘 | LLM 判断 create/update/skip |
| SkillCurator（修撰） | idle 周期 | 全部 agent 自建技能 | 合并伞技能 / 归档 / 单条迭代 |

## 4. 与相邻子系统关系

| 子系统 | 关系 |
|---|---|
| persona / PromptBuilder | Layer 7 注入 skill index + always-on 全文 |
| executor / hooks | `SkillReviewHandler.on_agent_end` 注册在 `AGENT_END` |
| tools | `skill_list` / `skill_view` / `skill_manage` 是 Agent 入口 |
| storage | `skill_metrics` 表存评分与 curator lifecycle |
| EventBus | `skill.learned` / `curate.*` 审计事件 |

## 5. 本目录子文档

| 文档 | 主题 |
|---|---|
| [loader.md](loader.md) | 三层加载、SKILL.md 解析、渐进加载、模糊匹配 8 策略、三层缓存 |
| [learning.md](learning.md) | SkillReviewHandler、SkillCurator、SkillMetricsStore、前台实时学习 |
| [guard.md](guard.md) | 技能安全 Guard（多类威胁 regex + 无形 Unicode + 信任矩阵） |

**相关实现**：[../../impl/skills/](../../impl/skills/)
