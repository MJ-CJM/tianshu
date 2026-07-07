# 技能系统（Skills）— 实现现状

**相关设计**：[../../design/skills/](../../design/skills/)

覆盖 `src/tianshu/skills/` + `tools/skill_tools.py`。大量 ported from hermes-agent（见 `guard.py` / `fuzzy_match.py` / `curator.py` 头部注释）。

## 1. 关键类 / 文件路径

| 文件 | 关键类 / 函数 | 职责 |
|---|---|---|
| `skills/loader.py` | `SkillsLoader` / `SkillsWatcher` | 三层加载、渐进注入、三层缓存、热重载 |
| `skills/fuzzy_match.py` | `fuzzy_find` / `fuzzy_replace` / `FuzzyMatchResult` | 8 策略模糊匹配（patch 用） |
| `skills/guard.py` | `SkillsGuard` / `THREAT_PATTERNS` / `TrustLevel` / `INSTALL_POLICY` | 安全扫描 + 信任矩阵 |
| `skills/reviewer.py` | `SkillReviewHandler` | `AGENT_END` 执行后复盘学习 |
| `skills/curator.py` | `SkillCurator` / `CuratePlan` / `CurateResult` | 周期策展（修撰）：合并/归档/迭代 |
| `skills/curator_lifecycle.py` | `apply_automatic_transitions` | 纯函数 active→stale→archived 迁移 |
| `skills/metrics.py` | `SkillMetrics` / `SkillMetricsStore` | `skill_metrics` 表评分 + lifecycle |
| `skills/validator.py` | `SkillValidator` | frontmatter / 命名校验 |
| `tools/skill_tools.py` | `_skill_list` / `_skill_view` / `_skill_manage` / `register_skill_tools` | Agent 技能工具 |
| `skills/builtin/` | `file-ops/` `shell/` | 内建技能 |

## 2. Agent 工具

`register_skill_tools(registry, skills, metrics_store, guard_agent_created, event_bus)` 注册三个工具：

| 工具 | tier | 能力 |
|---|---|---|
| `skill_list` | T0_READONLY | 列技能（name+description+source+status），可 query 过滤、按 dormant 排除 |
| `skill_view` | T0_READONLY | 看全文，`increment_usage` + 加入 `_active_skills` |
| `skill_manage` | T1_WORKSPACE（side_effect） | create/edit/patch/delete/activate/write_file/remove_file |

`skill_manage` 名校验 `_NAME_RE = ^[a-z0-9][a-z0-9._-]{0,63}$`，内容上限 256KB，create 打 `created_by='agent'` + emit `skill.learned`，write_file 经 Guard（AGENT_CREATED）。`_active_skills` 是模块级集合，用于本轮执行的成功/失败归因。

## 3. 核心流程

### 渐进注入

```text
PromptBuilder Layer 7
  ├→ load_index(metrics_store)   → "- name: desc [low success rate]?" 索引
  └→ load_always()               → always=true 技能全文拼接
Agent 看 index → skill_view(name) → 全文
```

### 执行后学习

```text
AGENT_END → SkillReviewHandler.on_agent_end
  → _should_review (COMPLETED + iter>=3 + interval)
  → _run_review: LLM JSON → create(validator→create_skill) / update(patch_skill)
```

### 周期策展（修撰）

```text
SkillCurator.run(trigger_source)
  → gate(idle + lock)
  → apply_automatic_transitions (纯函数生命周期)
  → _llm_plan (≥2 候选): consolidations/archivals → _apply_plan (确定性)
  → _iterate_pass (低成功率单条改进)
  → _write_report + emit curate.*
```

## 4. 缓存与热重载

`SkillsLoader`：L1 `_l1_cache`(OrderedDict, ≤8) / L2 `_l2_metadata`+`_l2_stats`(mtime_ns,size) / L3 磁盘扫描。写操作 `_atomic_write` 后失效缓存。`SkillsWatcher` 用 watchdog 监听三层目录，`SKILL.md` 变动 → 1s debounce → `invalidate_cache` + `load_all`。

## 5. 扩展点

- **新威胁模式**：`guard.THREAT_PATTERNS` 增 `GuardPattern`（用 `_p` 辅助）
- **新信任策略**：`guard.INSTALL_POLICY` 调整 (safe,caution,dangerous) 动作
- **新模糊策略**：`fuzzy_match._STRATEGIES` 追加策略函数
- **新 curator 动作**：扩 `CuratePlan` + `_apply_plan`
- **换 watcher 后端**：`SkillsWatcher` 当前绑 watchdog，可替换

## 6. 注意点（与旧 impl 文档纠偏）

- 工具实际是 `skill_list` / `skill_view` / `skill_manage`（无 `skill_propose` / `skill_install` / `skill_uninstall`）
- `FuzzyMatchResult` 字段为 `(start, end, strategy)`，无 `confidence`
- `SkillMetrics` 用 `usage_count` / `success_count` / `failure_count`（无 `avg_latency_ms`）
- 缓存内部名为 `_l1_cache` / `_l2_metadata` / `_l2_stats`（非 `_index_snapshot` / `_scan_layer`）
- 删除是归档（`.archive/`），builtin 不可删
