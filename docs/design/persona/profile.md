# 画像合成与模板库

## 1. 设计意图

官员不应是静态人设：随着执行积累，系统应能归纳「这个官员擅长什么、近期在做什么、状态是否健康、有无退化迹象」，并把结论沉淀成可读、可人工补注的 `PROFILE.md`。模板库则解决「新建官员从哪来」——把外部 agency-agents 模板拆成天枢的 SOUL/ROLE 双文件供 seed。两者共同支撑官员的**成长**与**繁衍**。

## 2. ProfileSynthesizer 流水线

`ProfileSynthesizer.run(persona_id)` 四步（spec §4.3）：

| 步 | 动作 | 来源 |
|---|---|---|
| 1 Collect | 收集数据 | DrawerStore 抽屉 + Storage 事件 + SkillMetrics + 上一版 PROFILE |
| 2 Rule agg | 规则聚合（无 LLM） | 任务分布、健康度、退化候选 |
| 3 LLM | 两次独立调用（可并发） | 擅长领域归纳 + 退化原因 |
| 4 Persist | 原子写 + 归档 + 剪枝 | `~/.tianshu/personas/{id}/PROFILE.md` |

输出四章节（`ProfileSections`）：擅长领域 / 任务分布 / 健康度 / 退化迹象。默认数据窗口 14 天，合成模型默认 `claude-sonnet-4-6`。

## 3. PROFILE.md 结构

`ProfileFrontmatter`（YAML frontmatter）记录元数据：`persona_id`、`version`、`last_synthesized`、`synthesizer_model`、`data_window`、`data_sources`、`manually_edited`、`degraded`。

**人工补注保护**：正文以 `AUTO_SECTION_MARKER`（`<!-- Auto-generated section ends. Manual notes below preserved. -->`）分隔。`parse_profile` 把正文切成 auto / manual 两段，重新合成时只覆盖 auto 段，marker 之后的人工笔记保留——自动化与人工注记互不覆盖。

## 4. 触发机制

`ProfileTrigger` 两条触发路径：

| 路径 | 条件 |
|---|---|
| AGENT_END hook (`handle_agent_end`) | `increment_persona_task_counter` 计数每达 `PROFILE_TRIGGER_THRESHOLD`(20) 触发一次，异步 `create_task` |
| cron (`run_for_all_personas`) | 每日对所有 active persona 逐个合成 |

AGENT_END 路径复用 MemoryManager 的 persona 解析（优先 `context["persona"].id`），无 persona_id 直接跳过。

## 5. TemplateLibrary 模板库

模板源码 vendored 在 `src/tianshu/resources/persona_templates/{lang}/{category}/*.md`（`lang ∈ {zh, en}`），由 `scripts/sync_persona_templates.py` 同步。运行时由 `packaged_defaults().persona_templates_dir()` 提供只读视图，`TemplateLibrary` 扫描该视图；每个模板是带 frontmatter（name/description/emoji/color）的单 md 文件。

**核心映射 `split_template`**：把一个模板文件拆成天枢的两文件身份模型——
- 以「核心使命 / 职责 / mission / responsibilities」等标题（`_MISSION_MARKERS`）为分割点；
- 分割点之前的人格描述 → SOUL（加 name/department/title frontmatter）；
- 分割点之后的使命/职责 → ROLE。
- 无标题命中时 fallback：intro 段 → SOUL，整 body → ROLE，保证两文件都非空。

`render(template, name, department, title)` 读文件并返回 `(soul_md, role_md)`，供新建官员 seed 到运行时目录。

## 6. 边界

- 画像是派生数据，丢失可从 Drawer/事件/SkillMetrics 重新合成。
- `degraded` 标记由退化候选规则 + LLM 原因共同得出，仅作健康信号，不自动停用官员。
- 模板拆分依赖标题约定，模板格式异常时走 fallback 并记 warning。

**相关实现**：[../../impl/persona/](../../impl/persona/)
