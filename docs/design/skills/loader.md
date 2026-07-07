# 技能加载 — 三层来源、渐进加载、模糊匹配、三层缓存

> 设计意图：用最少 context 让 Agent 知道「有哪些技能」，需要时才加载全文；同时支持热重载与定制覆盖。

## 1. 三层来源与优先级

每个 skill 是一个目录，内含 `SKILL.md`（入口）+ 可选资源目录（`scripts/references/assets/templates`）。

| 层 | 路径 | 优先级 |
|---|---|---|
| builtin | `src/tianshu/skills/builtin/`（随项目发布） | 最低 |
| user | `~/.tianshu/skills/` | 中 |
| workspace | `{workspace}/skills/` | 最高（覆盖同名） |
| injected | `PluginApi.register_skill` 注入 | 高于以上（仅 load_all/get_skill） |

`_search_dirs()` 返回降序优先级列表，`load_all` 按升序扫描让高优先级覆盖同名。当前内建技能：`file-ops/`、`shell/`。

约束：单文件 `_MAX_FILE_SIZE=256KB`，单目录 `_MAX_CANDIDATES_PER_DIR=300`，char_budget 默认 30000。

## 2. SKILL.md 解析

用 `python-frontmatter` 解析。frontmatter 顶层取 `description`，扩展元数据在 `metadata.openclaw.*`：

| 字段 | 位置 | 作用 |
|---|---|---|
| `description` | 顶层 | index 注入用 |
| `always` | `metadata.openclaw.always` | true 则注入全文、跳过 requirements 检查 |
| `toolTier` | `metadata.openclaw.toolTier` | 工具 tier 提示 |
| `requires.bins` / `anyBins` / `env` / `os` | `metadata.openclaw.requires` | 加载前置条件 |

`_check_requirements`：`bins` 全部需 `shutil.which` 命中；`anyBins` 至少一个；`env` 全部已设；`os` 含当前 `sys.platform`。`always=true` 跳过全部检查。

## 3. 渐进加载（两种注入模式）

| 方法 | 输出 | 注入层 |
|---|---|---|
| `load_index(filter_names, metrics_store)` | 仅 `- name: description` 索引（含 `[low success rate]` / `[retire suggested]` 状态标） | Layer 7（index） |
| `load_always(filter_names)` | 所有 `always=true` 技能的完整 body 拼接 | Layer 7（always-on） |

`load_index` 还会过滤掉 dormant 的 agent 自建技能（除非显式 `include_dormant`）。Agent 看到 index 后用 `skill_view(name)` 取全文。这是「省 context」的核心设计——全文不默认入 prompt。

## 4. 模糊匹配 8 策略（fuzzy_match）

用于 `patch_skill` 的查找替换（也借鉴 hermes）。`fuzzy_find` 按顺序尝试，**首个命中即返回**：

| # | 策略 | 含义 |
|---|---|---|
| 1 | `_exact` | 字面精确 |
| 2 | `_line_trimmed` | 逐行去尾空白后匹配 |
| 3 | `_whitespace_normalized` | 连续空白归一为单空格 |
| 4 | `_indentation_flexible` | 忽略每行前导空白 |
| 5 | `_escape_normalized` | 字面 `\n`/`\t` → 真实换行/制表 |
| 6 | `_trimmed_boundary` | 去 pattern 首尾空行 |
| 7 | `_unicode_normalized` | NFC + smart quotes → ASCII |
| 8 | `_block_anchor` | 首尾非空行作锚，中间 ≥50% 相似 |

返回 `FuzzyMatchResult(start, end, strategy)`。`fuzzy_replace` 基于此原子替换，无命中抛 `ValueError`。归一化改变长度时 `_map_back` 用行级启发把位置映射回原文。

## 5. 三层缓存

| 层 | 角色 | 实现 |
|---|---|---|
| **L1** | `get_skill(name)` 结果 LRU | `OrderedDict`，上限 `_L1_MAX_ENTRIES=8` |
| **L2** | `list_all_metadata()` 元数据快照 + 文件 stat | `_l2_metadata` + `_l2_stats`（mtime_ns, size） |
| **L3** | 磁盘全扫 | `_collect_metadata` / `get_skill` 兜底 |

`_l2_stats_valid()` 逐文件比对 mtime/size，全等才复用 L2；任一变动或 stat 失败即重扫。

**热重载**：`SkillsWatcher`（基于 `watchdog`）监听三层目录，`SKILL.md` 变动触发 `_schedule_reload` → 1s debounce → `invalidate_cache()` + `load_all()`。watchdog 未安装则失去热重载但不致命。

## 6. 写操作与资源安全

写回类操作均原子写（`_atomic_write`：同目录 tempfile + `os.replace`）并失效缓存：
- `create_skill` / `save_skill` / `patch_skill`：写 SKILL.md
- `write_skill_file` / `remove_skill_file`：写资源文件，路径经 `_resolve_skill_resource` 校验（拒绝绝对路径、`..` 穿越、非白名单顶层目录、逃逸 skill dir），单文件 ≤1MiB
- `archive_skill` / `restore_skill`：移入/移出 `.archive/`（builtin 永不触碰）

**相关实现**：[../../impl/skills/](../../impl/skills/)
