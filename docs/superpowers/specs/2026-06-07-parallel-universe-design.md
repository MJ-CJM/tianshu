# 平行位面：行为配置的自进化分叉系统 — 设计文档

| 项 | 值 |
|---|---|
| 日期 | 2026-06-07 |
| 议题 | 让天枢的自进化从"单线"升级为"可分叉 + 可回滚 + 可选优"的平行位面 |
| 状态 | 设计已确认（决策点 1/2/3 通过），待 spec review → writing-plans |
| 范围 | `src/tianshu/`（新增 `universe/` 模块）、`persona/`、`skills/`、`config_manager.py`、`storage.py`、`scheduler/`、`gateway/`、`models/`、`web/` |
| 命名 | 整体「位面」；在役=主位面，挑战者=候选位面，快照=存照 |

---

## 1. 背景与问题

### 1.1 现状：自进化是"单线"的

天枢已是会自进化的宫殿——记忆在长、人格在漂、技能在自学（`SkillReviewHandler` @ `AGENT_END`、`SkillCurator`「修撰」周期整理，均默认开启）。但这套进化只有**一条时间线**：

- 改坏了**不好回滚**（人格/技能/策略一旦漂偏，没有干净的"回到上个好状态"）。
- 没法**同时让几种长法赛跑**，再挑出最贴合用户的那一种。
- 发版后用户实际使用中衍生的不适配（不合用的功能项、想改的行为），只能就地覆盖式修改，缺少"试错—对照—择优"的结构。

### 1.2 用户诉求

> 平台随用户使用会衍生出不适合 / 需要改动的功能项；参考"平行宇宙"概念，让平台随使用自进化、分叉出多个版本，其中一些是最贴合该用户的版本。opt-in：用户开启"平行宇宙"才进入多版本演化。

### 1.3 两步走（已确认的范围切分）

- **第一步（本文档）**：**行为配置层**的平行位面——检查点 + 分支 + 切换 + 回滚 + 自进化选优。
- **第二步（本文档不实现，仅预留扩展点见 §13）**：**代码变体位面**——每个位面是可独立运行的代码分叉。单独的大项目，后做。

---

## 2. 概念与边界（核心铁律）

一个**位面（universe）** = 一份可命名、可分支、可切换、可回滚的**"行为配置快照"**。切分"什么进快照、什么全局共享"的铁律：

> **位面分叉的是「怎么做」，全局共享的是「知道什么 + 做过什么」。**

| 类别 | 内容 | 进位面快照（可分叉/进化） | 全局共享（切换不丢） |
|---|---|:---:|:---:|
| 人格灵魂/职责 | `personas/{id}/SOUL.md`、`ROLE.md` | ✅ | |
| 技能集与状态 | `~/.tianshu/skills/` + pin/active/state | ✅ | |
| 策略规则 | `session_rules` / policy 规则 | ✅ | |
| agent/LLM 配置 | `config_manager`、`llm_configs`、`providers` | ✅ | |
| prompt 层组合 | PromptBuilder 各层开关/权重 | ✅ | |
| 记忆宫殿 | `memory_entries`（关于用户的事实） | | ✅ |
| 官员工作记忆 | `personas/{id}/MEMORY.md` | | ✅ |
| 全部工作历史 | edicts / memorials / events / 成本 / 审计 | | ✅ |

**铁律的两个直接后果：**

1. **切换位面不失忆、不丢历史**——换的是"宫殿的性格与规矩"，不是"宫殿对你的记忆"。
2. **适应度可干净归因**——每道诏令在执行时打上所在位面标记（`memorials.universe_id`，执行开始即固化、不随后续切换改变），既保证在途任务不被切换打断，又让后面的打分归因到具体位面。

> 决策点 1 已确认：`MEMORY.md`（官员工作记忆）归"共享"，避免换位面把官员学到的东西清掉。

---

## 3. 目标与验收

### 3.1 第一步·1a — 位面基建（先做，是 1b 的地基）

纯手动的"宫殿版 git"：检查点 / 分支 / 切换 / 对比 / 回滚。独立即有价值（改坏能回去、能手动 A/B、发版可打存照）。

1. **存照**：可把当前行为配置（人格 SOUL/ROLE + 技能集与状态 + 策略 + config 快照）捕获为一个不可变快照活拷贝。验收：创建后 `~/.tianshu/universes/{id}/` 落盘且 `universes` 表有记录。
2. **分支**：从任一位面派生新位面。验收：子位面目录与 manifest 由父全量拷贝而来，`parent_universe_id` 正确。
3. **切换**：使某位面成为在役（`active_universe_id`）。验收：切换后新诏令采用新位面的人格/技能/config；在途诏令仍在其 `universe_id` 标记的位面跑完。
4. **对比**：列出两位面行为配置差异。验收：diff 覆盖人格文本、技能集、策略、config。
5. **回滚**：切回任一历史位面。验收：等价于"切换到历史位面"，无数据丢失。
6. **诏令归因**：`memorials` 落 `universe_id`，执行开始即固化。验收：迁移后新 memorial 带 universe_id；切换中途不改已开始任务的归属。

### 3.2 第一步·1b — 自进化选优（基建之上，"平行位面"的魔法）

7. **变异**：演化引擎从冠军位面分支出候选位面并施加一处定向变异（改某 ROLE / 调某策略 / 换技能集等）+ 记录理由。验收：候选位面落盘且 manifest 记 `origin=mutation` + `mutation_reason`。
8. **小流量探索评估**：候选位面拿一小撮真实新诏令，结果回流打分；探索比例可配、有熔断。验收：探索流量受 `universe_explore_ratio` 控制；候选连续失败超阈值自动下线。
9. **适应度**：隐式（成功率/成本/审计通过/重试）+ 显式（用户对结果赞踩）按位面滚动累积。验收：`fitness_json` 随 memorial 完成更新；样本不足时不参与晋升判定。
10. **选优·人在回路**：候选在足够样本上稳定超过冠军 → 发"推荐晋升"事件，默认人工确认；可 opt-in 自动晋升。验收：默认产出推荐而非自动改在役；开启自动晋升后阈值满足即切换冠军。
11. **opt-in**：默认单位面（=今日行为，无感）；不开"平行位面"则演化引擎不运转。验收：开关关闭时无候选位面产生、无探索流量。

---

## 4. 非目标（本轮不做）

- **代码变体位面**（第二步）：仅预留扩展点（§13），不实现位面级代码分叉/独立进程隔离。
- **影子重放评估**：不把历史诏令拿去重跑候选配置——agent 任务有真实副作用，重放危险且常不可行。只读类任务的影子评估留作未来增量。
- **记忆/历史分叉**：记忆宫殿、工作历史、成本、审计全局共享、只增不分叉。
- **多租户**：天枢是"你的分身"，按单实例单用户设计；不做每用户独立 fork 的多租户演化。
- **copy-on-write 差量存储**：v1 用全量拷贝（小文本文件，简单安全），差量留作日后优化。

---

## 5. 设计

### 5.1 位面的表示与存储

- 新表 `universes`：`id`(pk) / `name` / `parent_universe_id`(nullable) / `status`(enum: `champion`/`challenger`/`archived`) / `origin`(enum: `genesis`/`manual_branch`/`mutation`) / `mutation_reason`(nullable) / `fitness_json` / `description` / `created_at`。
- 行为状态**全量拷贝**落盘：`~/.tianshu/universes/{id}/personas/{persona_id}/`（SOUL.md、ROLE.md）、`~/.tianshu/universes/{id}/skills/`。
- config 类（agent/LLM config、providers、session_rules/policy、prompt 层组合、技能 pin/active/state）以**快照 JSON** 存于 manifest（`~/.tianshu/universes/{id}/manifest.json`），切换时读回。
- 全局指针 `active_universe_id`：**冠军即在役**，以 `universes.status=champion` 唯一行为单一真相源（同一时刻仅一个 champion）；无独立的 `active_universe_id` 字段，避免双真相源。
- **创世位面（genesis）**：首次启用时把当前运行态捕获为 `genesis` 位面并设为冠军，保证"开启平行位面"前后行为连续。

> 已考虑并否决的替代：COW 差量存储省磁盘，但这些是小文本文件，全量拷贝简单又安全；v1 全量拷贝。

### 5.2 切换 / 分支 / 回滚语义

- **切换**：把目标位面 `status` 置 `champion`、原冠军降级（候选/归档）→ 重新指向各 loader 根目录（PersonaLoader runtime 根、SkillsLoader user 根）+ 重载 ConfigManager + 清相关缓存（参照 loader 既有缓存失效路径）。**新诏令立即采用**；在途诏令在其 `universe_id` 标记位面内跑完。
- **分支**：拷贝父位面目录 + manifest → 新 `universe_id`，`status=challenger`、`origin=manual_branch`。
- **回滚**：语义上等同"切换到某历史位面"。历史位面是不可变快照的活拷贝，回滚不破坏任何全局共享数据。

### 5.3 诏令归属与适应度归因

- `memorials` 表迁移加 `universe_id`（nullable，存量行为 NULL→视为 genesis）。
- 诏令执行**开始时**固化 `universe_id`（取当时 `active_universe_id` 或被路由到的候选位面 id），后续切换不改。
- memorial 完成（`execution.completed`/`execution.failed`/`audit.completed`）时把结果计入对应位面的 `fitness_json`。

### 5.4 1a — 位面基建（数据模型 / API / UI）

- **数据模型**：`universes` 表 + 目录布局（§5.1）+ `memorials.universe_id` 迁移。
- **API**（`/api` 前缀）：
  - `GET /universes`、`GET /universes/{id}`
  - `POST /universes/{id}/branch`（从某位面分支）
  - `POST /universes/{id}/switch`（设为在役）
  - `GET /universes/diff?a=&b=`（行为配置差异）
  - `POST /universes/{id}/archive`（归档，可恢复）
- **UI**：新增「位面管理」页——位面列表（标注在役/冠军/候选/归档 + 适应度 + provenance）、切换、分支、对比、回滚/归档。沿用现有页面↔路由风格（参照 `SystemManagementPage` 等）。

### 5.5 1b — 自进化引擎（演化）

类比已有「修撰」`SkillCurator`，新增"演化"组件（暂名 `UniverseEvolver`），由 scheduler 周期 + 空闲触发：

1. **采信号**：读冠军位面配置 + 累积使用信号（哪些人格/技能/策略与低适应度相关，来自 memorials 按位面/按维度聚合）。
2. **提变异**：LLM 产出**一处定向变异** + 理由（改某官员 ROLE.md / 调某条策略 / 换技能集 / 调某 config 旋钮）。一次只动一处，便于归因。
3. **生候选**：从冠军分支出候选位面并施加变异（`origin=mutation`，记 `mutation_reason`）。
4. **评估**：候选位面拿**一小撮真实新诏令**（小流量探索/bandit）。路由发生在诏令入口：以 `universe_explore_ratio` 概率把新诏令分给在线候选，否则给冠军。
5. **打分**：结果回流，更新候选与冠军的 `fitness_json`（§5.6）。
6. **选优**：候选在最小样本量上稳定超过冠军超过 margin → 发 `universe.promotion_recommended` 事件。**默认人工确认**（沿用人在回路）；`universe_auto_promote=True` 时阈值满足即把候选设为冠军。
7. **熔断与下线**：候选连续失败超阈值 → 自动归档下线，不再吃探索流量。

> 评估方式（决策点 3 已确认）：小流量真实探索，诚实但安全；不做影子重放。

### 5.6 适应度函数

按位面滚动聚合其 memorials：

- 隐式：`success_rate`、`avg_cost`、`audit_pass_rate`、`avg_retries`。
- 显式：用户对诏令结果的赞踩（新增轻量反馈入口）。
- 综合分 = 各项加权（权重可配，给合理默认）。
- **小样本保护**：未达 `universe_min_samples` 不参与晋升判定；晋升要求超过冠军一个 margin（避免噪声晋升）。

### 5.7 配置项（`config_manager.py`，沿用现有风格）

- `parallel_universe_enabled: bool = False`（总开关；opt-in）
- `universe_explore_ratio: float`（候选探索流量比例，如 0.1）
- `universe_min_samples: int`（参与晋升的最小样本量，如 20）
- `universe_promote_margin: float`（晋升所需领先幅度，如 0.05）
- `universe_auto_promote: bool = False`（默认推荐、人工确认）
- `universe_challenger_fail_limit: int`（候选连续失败下线阈值）
- 适应度权重组（成功率/成本/审计/重试/显式反馈）

---

## 6. 组件改动清单（文件级）

| 文件 / 模块 | 改动 |
|---|---|
| `src/tianshu/universe/`（新增） | `UniverseStore`（CRUD + 目录/快照落盘）、`UniverseManager`（切换/分支/回滚/diff）、`UniverseEvolver`（1b 演化）、`fitness.py`（打分） |
| `storage.py` | 新建 `universes` 表；`memorials` 迁移加 `universe_id` |
| `persona/`（PersonaLoader） | runtime 根目录可按在役位面重定向 + 缓存失效 |
| `skills/`（SkillsLoader） | user 根目录可按在役位面重定向 + 缓存失效 |
| `config_manager.py` | 新增 §5.7 配置项；切换位面时重载 config 快照 |
| `executor/`（诏令入口） | 执行开始固化 `universe_id`；1b 探索路由（按 `universe_explore_ratio` 分流） |
| `scheduler/` | 注册 `UniverseEvolver` 周期任务（类比 `skill.weekly_curate`） |
| `bus/` 订阅 | memorial 完成 → 更新 fitness；产出 `universe.promotion_recommended` |
| `gateway/api.py` + `models/api.py` | §5.4 位面 API + 配置透出 |
| `web/` | 「位面管理」页 + 诏令结果赞踩入口 |

---

## 7. 错误处理与边界

- 切换到不存在/已归档位面 → 拒绝并报错。
- 分支时父位面目录缺失/损坏 → 拒绝，不产生半成品位面。
- 在途诏令的 `universe_id` 一旦固化不可变；其引用的位面被归档不影响其跑完。
- 演化变异落盘失败 / validator 不过 → 记 error 跳过该候选，不影响其它（参照 `curator._apply_plan` 逐项 try）。
- 探索路由仅作用于**新诏令**；定时/系统诏令是否参与探索默认关闭（避免系统任务被实验配置影响）。
- 归档=可恢复，非删除；冠军位面不可被归档（须先切换出去）。

## 8. 安全

- 切换/分支/晋升/回滚/归档均为**显式动作 + 事件**，可审计、可撤销（沿用 event bus + 人在回路）。
- 位面快照含技能脚本 → 逐文件复用现有 `guard` 扫描，不绕过安全边界。
- 探索流量有**上限 + 熔断**：`universe_explore_ratio` 封顶；候选连续失败自动下线。
- 全局共享数据（记忆/历史/成本/审计）只增不分叉，杜绝"换位面丢数据"。
- 总开关默认关闭，关闭时演化引擎不运转、无探索流量、行为等同今日。

## 9. 测试策略

- **单元**：`UniverseStore` 落盘/读回；diff 计算；fitness 聚合与小样本保护；探索路由分流概率；熔断下线判定。
- **集成**：分支→切换→新诏令采用新位面、在途诏令归属不变；memorial `universe_id` 固化；`universe.promotion_recommended` 投递；总开关两态行为差异。
- **端到端（轻）**：web 分支/切换/对比/回滚改变在役位面与磁盘状态；诏令结果赞踩进入 fitness。
- 遵循项目"功能优先、测试最后补"：实现先行，合并前补齐到 80%。

## 10. 风险与权衡

- **小流量探索影响真实任务**：候选配置可能让被探索的诏令变差。缓解：探索比例低且可配、熔断、系统诏令默认不参与、默认人工确认晋升。
- **适应度信号噪声**：小样本误判。缓解：`universe_min_samples` + 晋升 margin。
- **全量拷贝磁盘占用**：位面多时膨胀。缓解：小文本文件占用低 + 归档清理；差量存储留作优化。
- **切换的运行时一致性**：loader 重定向 + 缓存失效需正确。缓解：新诏令边界切换 + 在途诏令固化归属，避免半切状态。
- **与单线自进化（修撰/reviewer）的关系**：1b 演化作用于"位面级配置组合"，修撰/reviewer 仍作用于在役位面内的技能。二者正交：前者选"哪套配置"，后者优化"在役这套里的技能"。

## 11. 命名

- 整体功能：「**位面**」（colloquial「平行宇宙」）。
- 在役/冠军：**主位面**；挑战者：**候选位面**；快照：**存照**；演化引擎：**演化**（`UniverseEvolver`，与「修撰」并列）。

## 12. 为第二步（代码变体位面）预留的扩展点

- `universes.origin` 枚举预留 `code_variant`。
- manifest 结构预留"代码层"段（v1 为空）。
- `UniverseManager` 切换接口以"位面 = 一组可重定向的运行态"为抽象，未来代码变体可作为新的重定向维度接入，不改 1a/1b 的契约。
