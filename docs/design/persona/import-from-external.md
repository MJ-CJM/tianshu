# 从外部来源导入百官种子（openclaw / hermes）

> 创建百官（persona/内臣）时，支持从 openclaw 或 hermes **导入配置作种子**。
> 本文档记录本体论依据、导入/排除边界，以及落地实现。

## 1. 本体论裁定：为何是「一次性种子」而非「活体同步」

参见 [domain-model.md §6 执行主体本体论](../domain-model.md#6-执行主体本体论百官内臣-vs-客卿外臣)。

用户提出两个选项：

- **选项 A（一次性 seeding）✓**：把外部来源的**人格 + 提示词**收进来，并只预览检测到的 Skill 元数据，种出一个天枢自己的**内臣**。该内臣此后**独立演化、受京察治理**。
- **选项 B（把外部系统当运行中的百官直接引入）✗ 范畴错误**：openclaw/hermes 是**外部运行中的 agent**，把它当「运行中的百官」等于把外臣当内臣——违反 §5 红线。若要把外部 agent 当执行主体用，那是**客卿（外臣）**路径，不是百官。

**定死的设计原则**：

1. **一次性 seed，不是活体 sync**。导入 = 预填创建表单，用户 review/改后再落库。持续 sync 会绕过京察 / CandidateService，侵蚀「百官自演化受天枢治理」。呼应现有「git = 起点，`~/.tianshu` = 真相源」。
2. **只导入可移植的人格与提示词**，**排除运行态**（记忆 / 用户模型 / 学习历史 / 渠道 / 网关）。检测到的外部 Skill 只在预览中列出，不复制到 live 目录；安装必须另走受治理 Candidate/Gate。
3. **复用现有 `create_persona` 落库路径**，不另起。

## 2. 导入 / 排除边界（据两者最新官方文档）

| | hermes（`~/.hermes/`） | openclaw（`~/.openclaw/workspace/`） |
|---|---|---|
| 人格 | `SOUL.md` | `SOUL.md` |
| 职责 / 身份 | （SOUL.md 内含） | `AGENTS.md` / `IDENTITY.md` |
| 模型偏好 | `config.yaml` → `model.default` | `openclaw.json` → `agents.defaults.model.primary` |
| 工具 | `config.yaml` → `agent.disabled_toolsets` | `TOOLS.md` |
| 技能 | `~/.hermes/skills/*/SKILL.md`（agentskills.io） | ClawHub 技能（agentskills.io）/ `TOOLS.md` |
| **不导入（运行态 / 自进化）** | `memories/` · `USER.md` · Honcho 用户模型 · 学习记录 | `channels.*` · `gateway.*` · 配对状态 · `MEMORY.md` 运行记忆 |

**排除项透明化**：扫到排除项时跳过，并写进 `PersonaImportDraft.source_notes`（「已排除 X（运行态 / 自进化，不导入）」），用户在预览里能看到取了什么、丢了什么。

**三边惊人一致（映射几乎零摩擦）**：

- `SOUL.md` 三边同名同概念（天枢 / hermes / openclaw 官方文档均确认）。
- Skill 格式兼容不等于允许直接安装：天枢能解析 agentskills.io 的 `SKILL.md`，预览会
  显示名称与描述，但创建 Persona 不复制或激活这些目录。

## 3. 实现

### 后端

- **`persona/import_source.py`**：`PersonaImportDraft`（soul_body / role_body / suggested_name / suggested_model / skills / source_notes）+ `import_from(source, path)` + `detect_default_path(source)`。
  - `_import_hermes`：读 `SOUL.md`（frontmatter 抽 name）、`config.yaml`（`model.default` + `agent.disabled_toolsets`）、`skills/*/SKILL.md`。
  - `_import_openclaw`：读 `SOUL.md` + `AGENTS.md`/`IDENTITY.md`（→ role）、`openclaw.json`（`agents.defaults.model.primary`）。`openclaw.json` 是 JSON5 → 容错解析（去 `//`、`/* */`、尾逗号），因 `json5` 未安装。
  - 排除清单硬编码（`_EXCLUDE`），扫到即记入 `source_notes`。
  - 宽容解析：字段缺失降级不崩；缺 `SOUL.md` 报可读错误（`PersonaImportError`）。
- **`gateway/personas_api.py`**：
  - `POST /personas/import/preview`：入参 `{source, path?}`，path 省略则探测默认目录；**只读，不落库**。
  - `create_persona` 扩展：`imported_soul` / `imported_role` 注入天枢 frontmatter 后写
    SOUL/ROLE；旧客户端若提交非空 `import_skill_paths` 会收到 `409`，避免绕过
    Candidate/Guard/Gate/Promotion 直接写 live Skill。

### 前端

- `PersonaFormModal`（`PersonaDashboardPage.tsx`）加「创建方式」Segmented：`模板库` /
  `从外部导入`。选「外部导入」→ 选 source + path → 「导入预览」→ 预填 name、
  SOUL/ROLE 预览（Collapse）、建议模型，并只读显示检测到的 Skill 与 `source_notes`。
- `api/personas.ts`：`previewPersonaImport(source, path?)`；`api/types.ts`：
  `PersonaImportDraft` / `PersonaImportSourceKind` + `PersonaCreateRequest` 加
  `imported_soul?` / `imported_role?`，不暴露直接安装 Skill 的字段。
- i18n 三语：`persona.createMode.*` / `persona.import.*`（含「一次性种子·非同步」说明）。

## 4. 明确不做

- **持续 sync**（违反 seed 语义 + 绕过京察）——只做一次性导入。
- **随 Persona 直接安装外部 Skill**——预览只读；安装必须走受治理候选链路。
- **导入记忆 / 用户模型 / 学习历史 / 渠道 / 网关**（运行态 / 自进化 / 外部机构）。
- **选项 B**（外部系统当运行中的百官）——范畴错误，永不做。若需外部 agent 执行，走客卿路径。
- 上传式导入（v1 走服务端读路径；跨机器先拷配置目录过来）——可作 follow-up。
