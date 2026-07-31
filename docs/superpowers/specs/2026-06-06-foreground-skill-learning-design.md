# 前景主导的技能自学习系统 — 设计文档

> **历史设计，当前边界已收紧：** reviewer/curator 现在默认关闭，并在调用 LLM 前
> fail fast；Web 与 Persona 导入都不会直接写 live Skill。当前事实见
> [`../../design/skills/README.md`](../../design/skills/README.md) 与
> [`../../CURRENT-STATE.md`](../../CURRENT-STATE.md)。

| 项 | 值 |
|---|---|
| 日期 | 2026-06-06 |
| 议题 | agent 在对话/执行过程中自创建技能,并持续优化 |
| 状态 | 设计已确认,待 review → writing-plans |
| 范围 | `src/tianshu/skills/`、`src/tianshu/tools/skill_tools.py`、`src/tianshu/executor/agent.py`、`src/tianshu/config_manager.py`、`src/tianshu/storage.py`、`web/` |
| 参考 | hermes-agent(`/Users/chenjiamin/ai-example/hermes-agent`,tianshu skill 系统的上游) |

---

## 1. 背景与问题

### 1.1 现状:闭环骨架已存在且默认启用(代码依据)

天枢已有一套"自动固化 + 持续优化"机制,**已接线、默认开启、正在运行**:

- **自动固化** `SkillReviewHandler`(`skills/reviewer.py`):`app.py:565` 注册到 `AGENT_END` hook(priority=200);默认 `skill_review_enabled=True`、`skill_review_interval=5`(`config_manager.py:31-32`)。每完成 5 个任务触发一次轻量 LLM review,自行决定 `create / update / skip`。
- **持续优化** `SkillCurator`「修撰」(`skills/curator.py`):`app.py:587` 实例化,`scheduler.py:105` 注册为系统 cron `skill.weekly_curate`(`0 4 * * 0`,每周日 4:00);默认 `skill_curator_enabled=True`。做生命周期转换 + LLM 合并/归档。
- **生命周期**(`skills/curator_lifecycle.py` `apply_automatic_transitions`):agent skill 按活跃度 active → stale(30天)→ archived(90天),pinned 豁免;纯计算无 LLM。
- **指标** `SkillMetricsStore`(`skills/metrics.py`):`usage_count / success_count / state / pinned / created_by / source_edict_id`。
- **工具** `skill_tools.py`:`skill_list`(T0)、`skill_view`(T0)、`skill_manage`(T1,action=create/edit/patch/delete/activate)。
- **安全**:`validator.py` + `guard.py`(`agent-created` trust level + 策略矩阵)。

### 1.2 即时可见性已经打通(代码依据)

agent 在执行循环中途即可创建并当场使用 skill:

- `loader.create_skill`(`loader.py:440`)写文件后立即 `self._l2_metadata = None` 失效元数据缓存;`save_skill`/`delete_skill`/`archive_skill` 也都主动 `pop` L1 + 清 L2(`loader.py:427/460/491`)——**不依赖 `SkillsWatcher`**(watcher 仅作外部编辑兜底)。
- `skill_list` / `skill_view` 实时查 loader;`agent.py:619` `_build_system_prompt` 用 `load_index` 注入索引。
- 结论:中途 `skill_manage(create)` 后,**同一道 edict 的后续 iteration 通过 `skill_list`/`skill_view` 即可发现并加载**(system prompt 的被动索引可能滞后一轮,但不阻塞主动查询)。

### 1.3 hermes 参考的关键洞察

- hermes **没有 `AGENT_END` reviewer**。它的"从对话提炼"重心在**前景**:agent 在干活当下主动 `skill_manage(create)`;**后台** curator 仅空闲+周期整理,且 **curator 输入不回看完整对话**,只看 skill 使用计数。
- hermes 的 `skill_manage` 含 **`write_file` / `remove_file`** action,支持目录内多文件(`references/ templates/ scripts/ assets/`),单文件 1MiB、SKILL.md 100K。
- hermes 创建后即时可见机制(清 system prompt 缓存 + 工具无缓存实时扫盘)与 tianshu 1.2 基本一致。
- hermes 对 **agent 自建 skill 默认不做 guard 扫描**,理由:agent 本就能用 `shell_exec` 跑任意代码,扫描只增摩擦,不构成真正安全边界。

### 1.4 真实缺口

对照用户诉求("把对话过程中有用的、后续会持续使用的固化,并持续优化")与 hermes:

① **引导偏弱且偏事后(→ 前景化)**:`loader.py:116-117` 注入的引导是 "After completing a difficult task, **consider** saving reusable approaches..."——"完成后 + 考虑"措辞软,助长 undertrigger。
② **不支持多文件**:`skill_manage` 无 `write_file`,`create_skill` 只写 `SKILL.md`,无法把摸索出的脚本/参考资料一起固化。
③ **后台静默,缺人在回路**:reviewer/curator 默默写库,用户无"看见 agent 固化了什么 + 撤销/编辑/pin"的回路。
④ **优化只在库级,无单条质量迭代**:curator 只合并/归档,不会让单个 skill 越改越好;且无渠道把"人的反馈"喂回优化。

---

## 2. 目标与验收

1. **前景主路径强化**:agent 在执行循环中途遇到非显然、可复用的方法/脚本时,被强引导主动 `skill_manage` 固化。验收:system prompt 引导改为过程中语义;构造一个需多步试错的 edict,agent 在 `AGENT_END` 之前就产出 skill。
2. **即时可用(回归保护)**:中途创建的 skill,后续 iteration 经 `skill_list` 可见、`skill_view` 可读。验收:集成测试覆盖"同一 edict 内 create → list/view"。
3. **多文件 skill**:`skill_manage` 支持 `write_file`/`remove_file`,可在 skill 目录写 `scripts/ references/ assets/ templates/`;路径含 traversal/绝对路径被拒;单文件与 SKILL.md 有大小上限。验收:单元测试覆盖写入、删除、路径攻击拒绝、超限拒绝。
4. **人在回路**:reviewer 与 agent 主动创建均发 `skill.learned` 事件(带 provenance);web 有"最近固化"视图,支持撤销/编辑/pin;操作落入 metrics。验收:创建后事件可查;web 三个动作改变 metrics 与磁盘状态。
5. **单条迭代**:人的编辑锁定为黄金版本(curator 不覆盖);人的撤销使其归档;低 `success_rate` 且 `usage ≥ 阈值` 且未被人工干预的 agent skill,curator 周期产出改进 patch。验收:`human_curated=True` 的 skill 被 curator 跳过;低分未干预 skill 进入改进流程。
6. **guard 可配**:`skill_guard_agent_created`(默认 `True`)控制是否扫描 agent 自建 skill;关闭后不扫描。验收:开关两态行为差异。

---

## 3. 非目标(本轮不做)

- **不做 skill-creator 那套完整 eval 循环**(测试用例 / benchmark / eval-viewer):面向"人交互式打磨单个 skill",不适配天枢"后台自动"语境。
- **不做运行时动态注册可执行 tool**:skill 仍是"指令 + 编排已有 tool(如 `shell_exec`)";要造真正可执行能力,走 skill+脚本资源或 MCP,不在本轮把 `.py` 动态注册成 `ToolRegistry` 工具。
- **不回看完整对话做事后提炼**:已确认采用"前景主导",reviewer 维持轻量 `tool_calls` 摘要兜底,**不**升级为回看完整转录(成本高、信息次优)。
- **不改即时可见性机制**:1.2 已打通,仅做回归测试保护。
- **不引入失败转录存储**:单条迭代起步只用 `success_rate` + 人的编辑信号(见 4.5),不为采集失败案例新建存储。

---

## 4. 设计

### 4.1 架构取向:前景主导

```
【前景·主路径】agent 执行 loop 中实时创建
  遇到非显然 / 可复用的方法或脚本
   └─ skill_manage(create | write_file)            ◄─ 缺口② 多文件
        └─ loader 写入 + 缓存失效(已通,1.2)
             └─ 后续 iteration: skill_list/view 当场可用
   ↑ system prompt 强引导(改写 loader.py:116)        ◄─ 缺口① 前景化
        │ 每次创建发事件 skill.learned(带 provenance)
        ▼
【人在回路·缺口③】web + CLI:"最近固化了什么" → 撤销 / 编辑 / pin
        │ 动作即反馈 → metrics:撤销=归档 · 编辑=黄金锁定 · pin=保护
        ▼
【后台·缺口④】SkillCurator 修撰 @ 空闲+周期
  已有:生命周期(active/stale/archived)+ 合并/归档
  新增:低分且未干预 → LLM 改进 patch;human_curated 跳过
        ▲
【兜底】事后 reviewer @ AGENT_END:保留,定位降级
  agent 漏建时的网;维持轻量 tool_calls 摘要,不回看完整对话
```

### 4.2 缺口① 前景化:强引导 + 即时可见

- **改写引导**:`loader.load_index`(`loader.py:116-117`)的引导文案从"完成困难任务后考虑保存"改为过程中语义,例如(最终措辞 plan 阶段定):
  > "When you discover a non-obvious, reusable approach or a script you had to figure out — save it **right then** with `skill_manage(create)`, don't wait until the task ends. It becomes available to you immediately via `skill_view`."
- **即时可见**:不改机制(1.2 已通),仅加集成测试保护。`load_index` 在 `_build_system_prompt` 的调用频率(每 edict 一次 vs 每轮)在 plan 阶段确认;前景主导下主动查询是关键路径,被动索引滞后可接受。
- **(可选,plan 评估)轻量提示**:在 loop 内检测到"同类工具序列反复试错"时,温和提示 agent 是否固化。默认不做,避免噪声;若做,作为 `BEFORE_ITERATION`/事件层面的提示而非强制。

### 4.3 缺口② 多文件 skill(对照 hermes)

- **`skill_manage` 新增 action**:`write_file`、`remove_file`。参数:`name`、`file_path`(skill 目录内相对路径)、`file_content`。
- **loader 扩展**:新增 `write_skill_file(name, rel_path, content)` / `remove_skill_file(name, rel_path)`,写入后同样失效缓存。
- **目录白名单**:`rel_path` 顶层目录限 `scripts/ references/ assets/ templates/`;拒绝绝对路径、`..` traversal、符号链接逃逸(复用/参照 `tools/path_utils.py`)。
- **大小上限**:单资源文件上限(建议 1MiB,对齐 hermes);`SKILL.md` 维持现有 `_MAX_CONTENT_SIZE=256K`(`skill_tools.py:14`)。
- **`create_skill` 兼容**:保持只建 `SKILL.md` 的现签名;多文件通过后续 `write_file` 增量添加(对齐 hermes 流程,避免一次性巨 payload)。

### 4.4 缺口③ 人在回路:管控面 + 反馈回流

- **事件**:`reviewer` 与 agent 主动创建(经 `skill_manage` 成功路径)统一发 `skill.learned` 事件,payload 含 `name / source_edict_id / created_by / reason`。沿用 `make_event` + event bus(参照 `curator._emit`)。
- **web 视图**:"最近固化的技能"列表(按 `created_at` 倒序,标 provenance),每条提供:
  - **撤销** → `loader.archive_skill`(可恢复,非删除)+ metrics 标记;
  - **编辑** → `skill_manage(edit)` + 置 `human_curated=True`;
  - **pin** → metrics `pinned=True`。
- **反馈回流**:三个动作写入 metrics(见 4.6),成为缺口④单条迭代的输入。这是"管控动作 = 优化燃料"的咬合点。

### 4.5 缺口④ 单条迭代:curator 增强

在 curator 现有 pass(生命周期 + 合并/归档)之外新增"单条改进"pass,信号优先级:

1. **人编辑过(`human_curated=True`)= 黄金版本**:curator **跳过**,绝不自动覆盖(尊重人的版本)。最强信号,无需 LLM。
2. **人撤销过 = 负信号**:已归档,curator 不再处理。
3. **纯自动改进(弱信号,补充)**:对 `created_by='agent'`、`human_curated=False`、`success_rate < 阈值`、`usage_count ≥ 最小值` 的 skill,把其 `SKILL.md` 内容 + 指标喂 LLM 产出改进 patch(`loader.patch_skill`)。归档可恢复、`dry_run` 可预览,误改风险可控。
- **信号粒度说明**:本轮不采集失败转录,纯自动改进只有 `success_rate` 这一弱信号,定位为补充;主力是 1(人的编辑)。未来若需强信号,再评估"metrics 记录失败 edict_id 以回溯"。

### 4.6 数据模型变更(`skill_metrics`)

新增字段(`storage.py` migration,参照 `storage.py:639` 既有 skill_metrics 字段扩展):

- `human_curated`(bool,默认 False):人编辑过 → True,curator 跳过自动改进。
- `last_human_action`(text/ts,可空):最近一次人工动作(撤销/编辑/pin)时间,审计用。

复用现有:`created_by`、`source_edict_id`、`state`、`pinned`、`usage_count`、`success_count`。

### 4.7 安全(guard)

- 新增 config `skill_guard_agent_created`(默认 `True`)。`True` 时:agent 自建 skill 及其 `write_file` 资源(尤其 `scripts/`)走 `guard` 扫描,按 `agent-created` 策略矩阵处置;`False` 时:跳过(对齐 hermes 理念,适合受信环境)。
- 多文件资源**逐文件**过 guard(不止 `SKILL.md`)。
- 路径安全(白名单 + traversal 防护)与 guard 开关**正交**:路径安全恒生效,不受开关影响。

### 4.8 兜底:事后 reviewer 降级

- 保留 `SkillReviewHandler` @ `AGENT_END`,行为基本不变(轻量 `tool_calls` 摘要),定位由"主路径"降为"agent 漏建时的网"。
- 不改其输入(不回看完整对话)。可保留 `skill_review_enabled` 开关,默认仍 `True`。

### 4.9 配置项(`config_manager.py`,沿用现有风格)

- `skill_guard_agent_created: bool = True`
- `skill_iterate_min_success_rate: float`(单条改进阈值,如 0.5)
- `skill_iterate_min_usage: int`(最小使用次数门槛,如 3)
- (复用)`skill_review_enabled`、`skill_curator_*`、`skill_stale/archive_after_days`

---

## 5. 组件改动清单(文件级)

| 文件 | 改动 |
|---|---|
| `skills/loader.py` | 改写 `load_index` 引导文案;新增 `write_skill_file`/`remove_skill_file`(含缓存失效) |
| `tools/skill_tools.py` | `skill_manage` 加 `write_file`/`remove_file` action + 参数 schema + 路径/大小校验;成功路径发 `skill.learned` 事件 |
| `skills/reviewer.py` | 创建成功发 `skill.learned` 事件(provenance);其余维持 |
| `skills/curator.py` | 新增"单条改进"pass(4.5);读 `human_curated` 跳过 |
| `skills/metrics.py` | `SkillMetrics` + store 支持 `human_curated`、`last_human_action`;查询低分候选 |
| `skills/guard.py` / `validator.py` | 资源文件逐个扫描;受 `skill_guard_agent_created` 开关控制 |
| `storage.py` | `skill_metrics` migration 加字段 |
| `config_manager.py` + `models/api.py` + `gateway/api.py` | 新增 config 项 + API 透出 |
| `web/` | "最近固化"视图 + 撤销/编辑/pin 动作 |
| `executor/agent.py` | (若需)确认 `load_index` 刷新频率 |

---

## 6. 错误处理与边界

- `write_file` 目标 skill 不存在 → `error_result`,提示先 `create`。
- 路径校验失败(traversal/绝对/越白名单)→ 明确拒绝,不写盘。
- guard 命中(开关开时)→ 按策略 block/warn/audit,block 时不落盘并回报。
- curator 单条改进:patch 失败、validator 不过 → 记录 error,不影响其它候选(参照 `curator._apply_plan` 逐项 try)。
- 撤销=归档(可恢复),非删除;pinned skill 不被自动改进/归档。
- 即时可见:create 后缓存已失效,但 system prompt 被动索引滞后一轮属预期,不视为 bug。

---

## 7. 测试策略

- **单元**:`write_file`/`remove_file`(正常 + 路径攻击 + 超限);metrics 新字段读写;curator 候选筛选(human_curated 跳过、低分入选);引导文案断言。
- **集成**:同一 edict 内 `skill_manage(create)` → 后续 `skill_list`/`skill_view` 命中(即时可见回归);`skill.learned` 事件投递;guard 开关两态。
- **端到端(轻)**:web 撤销/编辑/pin → metrics + 磁盘状态变化。
- 遵循项目"功能优先、测试最后补"约定:实现先行,合并前补齐到 80%。

---

## 8. 风险与权衡

- **agent undertrigger(漏建)**:前景主路径依赖 agent 自觉。缓解:强引导(4.2)+ 保留 reviewer 兜底(4.8)。
- **多文件脚本安全**:引入可执行资源。缓解:guard 可配 + 路径白名单恒生效;默认开扫描。
- **单条自动改进信号弱**:仅 `success_rate`。缓解:以人的编辑为主力,自动改进为补充 + dry_run + 归档可恢复。
- **与 hermes 的 guard 理念分歧**:tianshu 取"默认扫描 + 可关",比 hermes 保守一档,换取默认安全。

---

## 9. 命名

- 后台策展沿用「修撰」(`SkillCurator`,已存在)。
- 前景主动创建由 agent 直接用 `skill_manage`,不引入新组件名。
- 事后兜底 `SkillReviewHandler` 可保留现名;如需雅称可议「采诗」(采集以备删定),非必需。
