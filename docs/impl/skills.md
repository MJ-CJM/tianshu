# 技能系统（Skills）

覆盖 `src/tianshu/skills/` 全部 7 个 Python 文件 + `builtin/` 内建技能。大量借鉴 Hermes agent（见 `guard.py`、`fuzzy_match.py` 头部 "Ported from hermes-agent" 注释）。

---

## 1. 目录约定

每个 skill 是一个目录，内含一份 `SKILL.md`（入口，frontmatter + markdown body）。可选附加资源文件同目录存放。

```
<skill_dir>/
  SKILL.md          # frontmatter: name, description, always, dormant, tools, …
  resources/        # 可选：示例 / 模板（非约定，但 builtin 用到时放这里）
```

Skill 三层来源（查找优先级）：
1. **Builtin**：`src/tianshu/skills/builtin/`（项目代码随发）
2. **Workspace**：当前 workspace 下的 `.tianshu/skills/` 或 settings 指定目录
3. **User**：`~/.tianshu/skills/`

## 2. SkillsLoader（`skills/loader.py`）

`SkillsLoader(builtin_dir, workspace_dir, user_dir, char_budget=30000)` 维护三层目录的统一视图。

### 三层缓存

| 层 | 角色 | 实现 |
|---|---|---|
| **L1** | LRU 内存缓存（`get_skill(name)` 结果） | dict + 最近使用追踪 |
| **L2** | 文件元数据快照（`mtime / size`），避免每次重扫 | `_index_snapshot` |
| **L3** | 磁盘扫描（首次 / mtime 变动时触发） | `_scan_layer` |

缓存失效：`SkillsWatcher`（基于 `watchdog`，`skills/loader.py:474`）监听三层目录，有写入即 invalidate。启动失败（watchdog 未安装）不致命，只是失去热重载。

### 注入模式

- `load_always(filter_names=None) -> str`：返回所有 `always=true` skills 的完整 SKILL.md 拼接（系统提示层 7b）
- `load_index(filter_names, metrics_store) -> str`：返回按 `description` 组成的**索引**（名称 + 描述，无正文），留给 LLM 按需用 `skill_view` 工具加载（系统提示层 7a）
- `set_char_budget(budget)` 控制索引+always 的总字符预算，超限按使用频次 / `dormant` 标记裁剪

### frontmatter 字段

由 `validator.py` 校验：`name`（必须，等同目录名）、`description`、`always`（bool，默认 false）、`dormant`（bool，默认 false，不参与自动注入）、`tools`（允许的工具白名单）、`skills_required`（依赖）、`author`、`version`。

## 3. Guard（`skills/guard.py`）— ported from hermes-agent

安全扫描。识别恶意 / 可疑的 skill 内容：

- **GuardCategory**：13 类威胁 — `exfiltration`、`injection`、`invisible_unicode`、`destructive`、`persistence`、`network`（reverse shell）、`obfuscation`、`execution`、`supply_chain`、`credential_exposure`、`traversal`、`mining`、`privilege_escalation`
- **Severity**：`critical` / `high` / `medium` / `low`
- **TrustLevel**：`builtin` / `trusted` / `community` / `agent-created`
- 50+ regex 模式 + 无形 unicode 字符检测（ZWSP / RLM / LRM / tag chars）
- 策略矩阵：不同 trust level 对 critical/high 命中采取 **block / warn / audit** 策略

触发点：
- `skill_install` 工具安装前
- 用户上传 skill
- `SkillReviewHandler` 在 `AGENT_END` 生成新 skill 时

## 4. FuzzyMatch（`skills/fuzzy_match.py`）— ported from hermes-agent

用于 skill patch / 代码片段查找替换。**8 策略链**（`fuzzy_find` 按顺序尝试）：

1. `_exact` — 字面精确匹配
2. `_line_trimmed` — 逐行去空白
3. `_whitespace_normalized` — 所有空白归一为单空格
4. `_indentation_flexible` — 允许缩进差异
5. `_escape_normalized` — 转义序列归一（`\n` / `\\n` 等价）
6. `_trimmed_boundary` — 忽略首尾空白边界
7. `_unicode_normalized` — NFC / NFKC 归一化
8. `_block_anchor` — 块锚点（首尾数行作锚）

每个策略命中返回 `FuzzyMatchResult(start, end, confidence, strategy)`。`fuzzy_replace` 基于此实现原子替换。

## 5. Metrics + Reviewer + Validator

`metrics.py`：
- `SkillMetrics`：每个 skill 的 `used_count` / `last_used` / `avg_latency_ms` / `success_rate`
- `SkillMetricsStore(conn)`：SQLite 表 `skill_metrics`，用于 `load_index` 的热度排序

`reviewer.py` — `SkillReviewHandler`：注册在 `AGENT_END`（priority=200）。当 Agent 生成新 skill（通过工具 `skill_propose`）时：
1. `SkillValidator` 做 frontmatter 校验
2. `Guard` 扫描
3. 通过则写入 `~/.tianshu/skills/` 并 invalidate L1 缓存

`validator.py` — `SkillValidator.validate(skill_md) -> ValidationResult`：frontmatter 必填字段、name 与目录一致、禁止相对路径导入等。

## 6. Skill 工具（`tools/skill_tools.py`）

`register_skill_tools(tools, loader, metrics_store)` 注册：

| 工具 | 能力 |
|---|---|
| `skill_list` | 列出可用 skills（name + description） |
| `skill_view` | 查看单个 skill 的完整 SKILL.md |
| `skill_propose` | Agent 提议新 skill（走 reviewer） |
| `skill_install` | 从 URL / path 安装（走 guard） |
| `skill_uninstall` | 删除 user 层 skill |

## 7. 内建 skills

`src/tianshu/skills/builtin/` 当前含：
- `file-ops/` — 文件操作套件的 SKILL.md
- `shell/` — Shell 执行能力

## 8. 外部网络工具（鸿胪寺）

> 启动期按 env 按需注册。`register_hongluisi` 在 `src/tianshu/tools/builtins.py` 挂接。

### `web_fetch(url)`
读公开网页，返回提取的 Markdown 正文。Fetch 链：local → jina → firecrawl（按 profile）。SSRF 防护、1MB body 上限、TTL 缓存。

### `web_search(query, max_results=5)`
关键词搜索。Provider：tavily 或 jina。返回 ranked 列表（标题 + URL + 摘要）。

### `api_request(url, method, headers?, query?, json_body?)`
通用 HTTP。GET/HEAD 放行（T2）；POST/PUT/DELETE/PATCH 走审批（T3）。
LLM 禁止传 `Authorization`/`Cookie`/`X-Api-Key`；系统按 host 匹配自动注入（凭证在藏兵阁加密托管）。
需 Edict 在 `api_request_hosts` 显式白名单目标 host。

### `web_extract(url, schema, prompt?)`
Firecrawl `/v1/extract`：按 JSON Schema 抽结构化数据。需 `TIANSHU_FIRECRAWL_API_KEY`。

## 代码路径索引

- `src/tianshu/skills/loader.py`
- `src/tianshu/skills/guard.py`
- `src/tianshu/skills/fuzzy_match.py`
- `src/tianshu/skills/metrics.py`
- `src/tianshu/skills/reviewer.py`
- `src/tianshu/skills/validator.py`
- `src/tianshu/skills/builtin/file-ops/SKILL.md`
- `src/tianshu/skills/builtin/shell/SKILL.md`
- `src/tianshu/tools/skill_tools.py`
