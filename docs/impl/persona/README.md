# persona 人格系统 · 实现现状

**相关设计**：[../../design/persona/](../../design/persona/)

覆盖 `src/tianshu/persona/` 全部模块、`src/tianshu/resources/personas/` 与 `src/tianshu/resources/persona_templates/` 两类打包资源，以及 `~/.tianshu/personas/` 运行时 overlay。

## 1. 关键类 / 文件路径

| 主题 | 文件 | 关键符号 |
|---|---|---|
| 官员模型 | `persona/model.py` | `AgentPersona`、`DEFAULT_EXECUTOR_ID="bingbu"` |
| 打包资源解析 | `resources/overlay.py` | `packaged_defaults()`、`resolve_court_read()` |
| 加载/双层存储 | `persona/loader.py` | `PersonaLoader`、`ensure_runtime_identity`、`_dict_to_persona`、`repoint_runtime` |
| 诏令路由 | `persona/selector.py` | `OfficialSelector`、`TASK_DEPARTMENT_PREFERENCE`、`_DEPARTMENT_KEYWORDS` |
| prompt 注入 | `persona/prompt_builder.py` | `PromptBuilder.build()` / `build_layers()`、`_build_identity_card` |
| 工具 ACL | `persona/match.py` | `fnmatch` 通配符匹配（deny→allow→tier） |
| 指标 | `persona/metrics.py`、`persona/evaluator.py` | `PersonaMetrics`、`PerformanceEvaluator.evaluate()` |
| 画像合成 | `persona/profile_synthesizer.py` | `ProfileSynthesizer.run()`、`ProfileSynthesisInput`/`Result` |
| 画像触发 | `persona/profile_trigger.py` | `ProfileTrigger`（AGENT_END + cron），`PROFILE_TRIGGER_THRESHOLD=20` |
| 画像 schema | `persona/profile_schema.py` | `ProfileFrontmatter`、`ProfileSections`、`parse_profile`、`AUTO_SECTION_MARKER` |
| 画像 I/O / 渲染 | `persona/profile_io.py`、`persona/profile_renderer.py` | `atomic_write`、`HISTORY_KEEP=10`、manual-notes 保护 |
| 模板库 | `persona/template_library.py` | `TemplateLibrary`、`split_template`、`PersonaTemplate` |
| 外部种子导入 | `persona/import_source.py`、`gateway/personas_api.py` | `import_from()`、`POST /personas/import/preview`；只导入 SOUL/ROLE，直接 Skill 安装返回 409 |
| legacy | `persona/memory_manager.py` | `PersonaMemoryManager`（旧实现，当前用 memory/manager.py） |

## 2. 双层存储与加载流程

身份文件双层：

| 层 | 路径 | 写入方 |
|---|---|---|
| 打包默认 | `src/tianshu/resources/personas/{department}/`（SOUL/ROLE/MEMORY） | 只读；运行时由 `packaged_defaults().personas_dir()` 解析 |
| 运行时 overlay | `~/.tianshu/personas/{id}/`（SOUL/ROLE） | UI / API |

`PersonaLoader.load_all()`：有 storage 时 `_seed_from_files()`（no-op，仅作模板源）+ `_load_from_db()` 填内存缓存；无 storage 时 `_load_from_files()`（legacy）。

`_dict_to_persona(d)`：在 `packaged_defaults().personas_dir()` 的只读视图中定位部门目录（`{department}/`，回退 `{id}/`）→ `ensure_runtime_identity` 拷 SOUL/ROLE 到运行时 overlay（幂等，已存在不覆盖）→ DB 存的 path 被忽略，运行时目录权威。`delete()` 只删 DB、缓存和对应 runtime overlay；打包默认不写不删，迁移 ledger 防止已删除默认项被重新 seed。`repoint_runtime()` 在位面切换时切运行时根目录并重载。

`_read_frontmatter` 解析 SOUL.md YAML frontmatter（name/department/title/tools_allowed/tool_tier_max/can_delegate/delegates_to/memory_global_read/llm_config_name/skills_allowed）。

## 3. PromptBuilder 注入

`build()` 注入约 15 个有序层（详见设计文档 prompt-builder.md 的层表）。要点：
- Layer 2.5 `_build_identity_card` 作权威身份覆盖；Layer 3 SOUL 缺省时降级生成最小身份并 warning。
- 记忆经 `MarkdownMemoryBackend`：`read_core_memory(id)`（Layer 5）、`read_recent_logs(id, days=2, char_budget=2000)`（5.5）、`read_core_memory("_dept/{dept}")`（5.6）、`read_core_memory("court")`（6）。
- L1 Critical Facts（5.1）经 `_get_l1`，仅 `drawer_store + memory_config.l1_enabled` 时注入。
- skills 经 `SkillsLoader.load_index` + `load_always`，按 `persona.skills_allowed` 过滤。
- `build_layers()` 返回每层 `{layer, name, source, chars, tokens_est}` 供前端预览。

## 4. OfficialSelector 路由

`select(task_type)` 走 `TASK_DEPARTMENT_PREFERENCE` → `_find_by_department` 取首个匹配 persona → `_fallback_persona`。`select_for_task(description)` 走 `_DEPARTMENT_KEYWORDS` 打分取最高分部门。`get_default_map`/`get_keyword_map` 供 UI，缺对口官员时填 `is_fallback=True`。所有方法基于 `loader._personas` 内存缓存。

## 5. 画像合成

`ProfileSynthesizer.run(persona_id, trigger_source)` 四步：Collect（Drawer+事件+SkillMetrics+旧 PROFILE）→ Rule agg（任务分布/健康度/退化候选）→ LLM（擅长领域 + 退化原因，两次可并发）→ Persist。渲染经 `profile_renderer`（保留 `AUTO_SECTION_MARKER` 后的人工笔记，manual diff 冲突阈值 0.30），写入经 `profile_io.atomic_write`（tmp+rename），历史保留 `HISTORY_KEEP=10` 版。

## 6. 模板库

`TemplateLibrary.load()` 扫 `packaged_defaults().persona_templates_dir()` 提供的只读视图；源码树是 `src/tianshu/resources/persona_templates/{zh,en}/{category}/*.md`（跳过 README）。`split_template` 按 `_MISSION_MARKERS` 标题切 SOUL/ROLE，无命中走 intro/body fallback。`render` 返回 `(soul_md, role_md)`。

## 7. 扩展点

- **新官员**：经 UI / API 从 TemplateLibrary、自定义内容或外部只读预览创建，SOUL/ROLE
  写入 `~/.tianshu/personas/{id}/`，元数据由 `loader.save()` 写 DB；不要直接修改打包
  资源目录。外部预览检测到的 Skill 不随 Persona 安装，须另走受治理候选链路。
- **新路由规则**：扩 `TASK_DEPARTMENT_PREFERENCE` / `_DEPARTMENT_KEYWORDS`。
- **新 prompt 层**：在 `build()` / `build_layers()` 对应位置插入，保持 layer 编号有序。
- **新画像数据源**：扩 `ProfileSynthesisInput` + Collect 步 + 对应 section。
- **工具 ACL**：persona 的 `tools_allowed`/`tools_denied` 支持 `mcp_github_*` 通配符（`match.py`，gateway 与 ACL hook 共享）。

## 8. 打包默认与运行时 overlay

`src/tianshu/resources/personas/` 内含 `bingbu / ducha / hubu / neige / tongzheng / wenyuan` 六部打包默认（各含 SOUL.md / ROLE.md / MEMORY.md）及 `court/`（COURT.md + MEMORY.md，非独立 persona）。应用通过 `packaged_defaults()` 读取其只读视图；运行时 SOUL/ROLE 在 `~/.tianshu/personas/{id}/`，运行时记忆在 `~/.tianshu/memory/{id}/`。
