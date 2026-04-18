# Design Spec: Persona Growth Profile + 宫殿共生成长叙事

- **Date**: 2026-04-18
- **Status**: Draft (待 review)
- **Scope**: 借鉴 Hermes "成长型 agent" 叙事,为 tianshu 补齐"官员成长档案"与"宫殿共生成长"对外叙事
- **Candidate coverage**: Landscape 10 项中的 **#10 叙事重写 + #5 官员成长档案**
- **Dependencies**: Memory Palace Phase 1(Drawer/DrawerStore/FTS5)、SkillMetrics、EventBus、PromptBuilder 8 层、Persona runtime 分离
- **Unblocks next**: #1 emperor UserProfile 合成(复用本 spec 的 PROFILE 模式)
- **Related**:
  - Landscape 分析:本会话对齐的 10 项 Hermes 借鉴点
  - `docs/design/memory-palace.md` §7 Court 共享
  - `docs/impl/persona.md` `PromptBuilder 8 层`

---

## §0 背景与动机

Hermes Agent 在 2026 Q2 以"the agent that grows with you"叙事火爆;GitHub 趋势归因为市场对"成长型个人 agent"的强需求。tianshu 当前架构(六部多 persona + Memory Palace + SkillReviewHandler + SkillMetrics)已具备"成长"的底层数据,但对外叙事仍停留在"六部奏章系统"的治理隐喻 —— 成长能力**已有但未对外显化**。

本 spec 解决两件事:

1. **叙事升级**(#10):统一对外为"宫殿共生成长"—— tianshu 是一座会与用户共同成长的宫殿,内有 emperor 分身,外有辅佐的六部官员
2. **能力证据**(#5):为每个 persona 生成可读、自动更新的 `PROFILE.md`(成长档案),让"官员也在成长"这句话有具体证据

两项合并为一个 spec 的理由:一个是"说",一个是"做"。只改叙事没有证据,读者看不到"成长"在哪;只做档案没有叙事,用户不理解"为什么这个能力重要"。两者互相印证。

一句话定位:

> **天枢:一座会与你共同成长的宫殿。内有你的分身,外有辅佐你的六部。**

---

## §1 范围与目标

### 1.1 范围内(In Scope)

**叙事层(#10)—— 改"说法"**

| 目标 | 动作 |
|---|---|
| `docs/design/architecture.md` | 核心叙事改为"宫殿共生成长";一句话定位入开篇 |
| `docs/design/agent-persona.md` | 新增 §emperor 分身、§官员成长两小节(概念层) |
| `docs/design/memory-palace.md` | 标注 emperor wing 的"分身画像"角色 |
| 前端主标题与首页副本 | "天枢 - 六部奏章系统" → "天枢 - 共生成长的宫殿" |
| `README.md`(若存在) | 对外描述同步 |
| `CLAUDE.md` | 开头加定位句 |

**档案层(#5)—— 做"证据"**

| 目标 | 动作 |
|---|---|
| `src/tianshu/persona/profile_synthesizer.py`(新) | 周期性从 Drawer + SkillMetrics + Events 合成 PROFILE |
| `PROFILE.md` 模板 | frontmatter + 4 区:擅长 / 近期任务 / 健康度 / 退化迹象 |
| `~/.tianshu/personas/{persona_id}/PROFILE.md` | 六部 + court + emperor 占位各一份 |
| PromptBuilder 新增 Layer 6.5 | 其他 persona 读到"同僚近况" |
| 前端 persona 详情页 | 渲染 PROFILE 四区 |
| 合成触发 | 手动命令 + EventBus hook(每 N=20 任务)+ 定时调度(每日 03:00) |

### 1.2 范围外(Out of Scope,明确排除)

- **emperor UserProfile 合成(#1)** —— 下一个 spec。本次 emperor 目录只**占位 PROFILE.md**(空模板 + 说明"待 #1 实现")
- **主动 nudge(#3)** —— 依赖 #1
- **Skills 飞轮 L0-L4(#4)** —— 独立 spec
- **Dashboard 陪伴视图(#6)** —— 依赖 #1 + #5 双就位
- 前端大改版 / 主题系统重做 / i18n

### 1.3 成功标准

1. 三份 design 文档 + 前端标题叙事统一为"宫殿共生成长",一句话定位可在首页看到
2. 六部 persona 各自生成可读、自动更新的 `PROFILE.md`
3. PromptBuilder 能以 Layer 的形式注入"同僚近况",其他 persona 的 prompt 可观测到
4. 前端 persona 详情页能渲染 PROFILE 四区,用户一眼看懂"这位官员擅长什么、最近在忙什么、有没有退化"
5. 首次合成人工 review 后,六部 PROFILE 描述与 `personas/{id}/SOUL.md` 定位吻合度高

---

## §2 叙事重写(#10)

### 2.1 核心定位(三层统一引用)

**一句话定位**(最权威,首页/架构文档开篇):

> **天枢:一座会与你共同成长的宫殿。内有你的分身,外有辅佐你的六部。**

**两句话扩展**(首页副本 / README):

> 天枢是一座会与你共同成长的宫殿。宫殿里有你的分身(emperor)—— 跨会话、跨平台持续演进的个人画像;也有六部官员 —— 各自精进专业,共同辅佐你的目标。任务流转间,官员与分身一起成长。

**三层展开 What-Why-How**(架构文档 §愿景 / README 首段):

- **What**: 一个可常驻、会成长的个人 agent 系统
- **Why**: 把 agent 从"一次性工具"升级为"长期共生体" —— 不只完成当下任务,更沉淀对你的理解,让下次协作更默契
- **How**: "宫殿"隐喻组织记忆与角色:emperor wing(你的分身)+ 六部 wing(专业官员)+ court wing(共享记忆),各 wing 通过 Memory Palace + Skills 飞轮持续演进

### 2.2 改动清单(8 处)

| 文档 | 位置 | 新叙事动作 |
|---|---|---|
| `docs/design/architecture.md` | 开篇 §愿景 | 加一句话定位 + What-Why-How 三层;原"下旨→批红"闭环保留为"运作机制"降级段 |
| `docs/design/architecture.md` | §演进简史后 | 新增 §宫殿共生成长,介绍双成长轴,链到 memory-palace.md / agent-persona.md |
| `docs/design/agent-persona.md` | §8.4 后 | 新增 §8.5 emperor 分身 + §8.6 官员成长(概念层,实现链到本 spec) |
| `docs/design/memory-palace.md` | §7 Court 后 | 新增 §7.5 Emperor 分身 wing(与 court/persona 并列的第三类 wing) |
| 前端 `web/src/App.tsx` | 主标题 | "天枢 - 六部奏章系统" → "天枢" + 副标题 "共生成长的宫殿" |
| 前端首页仪表盘 | 欢迎区域 | 渲染两句话扩展版定位 + emperor/六部简图占位 |
| `README.md`(若存在) | 开头介绍 | 用两句话扩展版替换 |
| `CLAUDE.md` | 项目说明开头 | 加一句"项目定位:共生成长的宫殿",让 Claude 工作时按此叙事 |

### 2.3 用语规范(避免新旧混用)

| 术语 | 用于 | 禁用(避免混入主叙事) |
|---|---|---|
| 宫殿 / Palace | 整个 tianshu 系统 | "多代理协作系统"(保留 impl 技术说明) |
| 分身 / emperor | 用户个人长期画像 | "用户模型"、"主君 agent" |
| 官员 / 六部 | bingbu 等 7 个 persona | "subagent"、"worker"(保留 impl) |
| 成长 / growth | 画像合成 / skills 飞轮 / metrics 演进 | "优化"(太工具化) |
| 共生 / co-growth | emperor 与六部共同成长 | "协同"(过泛) |

### 2.4 改动顺序(三个独立 commit)

1. design 三文档(真相源)—— 一个 commit
2. 前端标题与首页(用户直接可见)—— 一个 commit
3. README + CLAUDE.md(对外 / 对 Claude 自身)—— 一个 commit

每批独立 review,避免叙事混乱期。

---

## §3 PROFILE 数据模型(#5 核心)

### 3.1 存储位置

`~/.tianshu/personas/{persona_id}/PROFILE.md`

与 `MEMORY.md`(长期核心记忆)、`YYYY-MM-DD.md`(日志流)并列为 runtime identity 文件。Markdown 作为真相源(人可读 / git-friendly / 手改可保留)。

### 3.2 完整 schema 示例

```markdown
---
persona_id: hubu
persona_name: 户部
version: 3
last_synthesized: 2026-04-18T14:32:00Z
synthesizer_model: claude-sonnet-4-6
data_window: 14d
data_sources:
  drawers: 87
  events: 23
  skill_metrics: 12
manually_edited: false
degraded: false
---

# 户部 · 成长档案

> 由 ProfileSynthesizer 基于近 14 天任务与记忆合成。最后更新:2026-04-18。

## 擅长领域
- 预算审查与成本控制:近 12 次 CostManager 任务全部给出合理分析
- 跨部门资源配额规划:给过 3 次被采纳的建议
- (3-8 条要点)

## 近期任务分布(14 天)
| 类型 | 次数 | 占比 |
|---|---|---|
| 成本审计 | 8 | 35% |
| 预算预警 | 6 | 26% |
| 其他 | 9 | 39% |

**关键事件**
- 2026-04-15 识别出任务预算超支 2.3×
- 2026-04-12 参与 Consultation "Q2 资源规划"

## 健康度
- **Skills**:healthy × 6 | warning × 1(`cost_analysis_v1` 成功率 0.57) | retire_suggested × 0
- **记忆充实度**:87 个活跃 drawer,近 14 天新增 12 个
- **活跃度**:14 天内 23 次任务(活跃)

## 退化迹象
- `cost_analysis_v1` 近期成功率从 0.82 降至 0.57,建议 review 或升级
- 跨币种预算类任务失败率 2/3,建议补对应 skill

---
<!-- Auto-generated section ends. Manual notes below preserved. -->

## 手写备注(synthesizer 不覆盖)
```

### 3.3 字段说明与生成策略

**Frontmatter 9 字段**(含 degraded):

| 字段 | 含义 |
|---|---|
| `persona_id` | 对应 `personas/{id}/` |
| `persona_name` | 中文显示名 |
| `version` | 合成版本,每次 +1 |
| `last_synthesized` | 上次合成 ISO 8601 UTC |
| `synthesizer_model` | 合成用的 LLM 模型(便于回溯质量) |
| `data_window` | 数据覆盖窗口(默认 14d) |
| `data_sources` | 本次引用数据量(置信度信号) |
| `manually_edited` | 用户是否手改过 |
| `degraded` | LLM 失败时降级标志 |

**四区内容**:

| 区 | 数据源 | 策略 |
|---|---|---|
| **擅长领域** | Drawer `category=O` + `confidence>0.7` + 成功任务 | LLM 归纳 3-8 条 |
| **近期任务分布** | `events` 表按窗口分组 | 代码聚合 + LLM 补关键事件 |
| **健康度** | `skill_metrics` + Drawer 统计 + 活动 | **规则统计,非 LLM** |
| **退化迹象** | `skill_metrics` warning/retire_suggested + 失败率 | 规则触发 + LLM 说明原因 |

关键原则:**健康度与退化迹象优先用规则,LLM 只做"说明原因"**——降幻觉风险,降合成成本。

### 3.4 手写备注保留机制

`<!-- Auto-generated section ends -->` 标记下方为用户手写区,synthesizer 每次合成:

1. 按标记切成 auto / manual 两段
2. 合成新 auto 段 + 拼接 manual 段(原样保留)
3. manual 段非空时 `manually_edited: true`

**若用户手改了 auto 段**:

| 改动量 | 策略 |
|---|---|
| < 30% 字符 | 保留手改不覆盖,`version` 不增 |
| ≥ 30% | 前端弹 diff,用户选"采纳新合成版"或"保留手写" |

### 3.5 版本历史

`~/.tianshu/personas/{persona_id}/profile_history/v{N}-YYYY-MM-DD.md`,保留最近 10 版,超出自动 prune。

---

## §4 ProfileSynthesizer 组件与合成流程

### 4.1 组件定位

`src/tianshu/persona/profile_synthesizer.py`(新模块),与现有 `loader.py` / `prompt_builder.py` / `selector.py` / `model.py` 并列。

### 4.2 类骨架

```python
@dataclass(frozen=True)
class ProfileSynthesisInput:
    persona_id: str
    data_window_days: int  # 默认 14
    drawers: list[Drawer]
    recent_events: list[EventEnvelope]
    skill_metrics: list[SkillMetrics]
    previous_profile: str | None

@dataclass(frozen=True)
class ProfileSynthesisResult:
    persona_id: str
    markdown: str
    auto_section: str
    manual_section: str
    version: int
    data_sources: dict
    degraded: bool

class ProfileSynthesizer:
    def __init__(self, llm_client, drawer_store, storage, skill_metrics_store):
        ...
    def collect_inputs(self, persona_id: str, window_days: int = 14) -> ProfileSynthesisInput: ...
    def synthesize(self, inputs: ProfileSynthesisInput) -> ProfileSynthesisResult: ...
    def persist(self, result: ProfileSynthesisResult) -> None: ...
    def run(self, persona_id: str, window_days: int = 14) -> ProfileSynthesisResult: ...
```

### 4.3 合成流程(4 阶段)

```
1. Collect   — DrawerStore.search(wing=pid,14d) + Storage.list_events + SkillMetrics + 读上版 manual
                         ↓
2. Rule agg  — 任务分布 / 健康度 / 退化候选 / 活跃度(纯规则,无 LLM)
                         ↓
3. LLM narrative — 仅"擅长领域" + "退化原因" 两区(可并发调用)
                         ↓
4. Render + persist — 拼 auto+manual → 写 PROFILE.md + 归档 v{N-1} + prune>10
```

### 4.4 触发时机(3 条路径)

| 路径 | 何时 | 频率 | 控制 |
|---|---|---|---|
| **手动命令** | CLI `/persona/{id}/synthesize` 或前端按钮 | 用户随时 | 用户主动 |
| **事件 hook** | `EventBus.on(AGENT_END, priority=250)`,晚于 MemoryManager(200) | 每 persona 完成 `N=20` 个任务自动触发 | 自动节流 |
| **定时调度** | Scheduler 系统 cron `0 3 * * *` | 每日凌晨保底 | 定时兜底 |

**节流**:`persona_metrics.tasks_since_last_synthesis` 计数,每次合成(任一路径)归零。

### 4.5 LLM prompt 结构(两区各一次,可并发)

```text
System: 你是 {persona_name} 的成长档案分析助手。
基于提供的数据客观归纳,禁止编造;数据不足就说明"数据不足"。

[调用 1 · 擅长领域]
User: 以下是近 {window} 天的记忆片段(category=O, confidence>0.7):
<drawers>...</drawers>
归纳 3-8 条"擅长",输出 JSON:
{"specialties":[{"title":"...","detail":"..."}]}

[调用 2 · 退化原因]
User: 候选退化 skill: <skill=... usage=... success_rate=... prev_30d_rate=...>
近期失败样本: <...>
为每个候选给出原因,输出 JSON:
{"degradations":[{"skill":"...","reason":"..."}]}
```

两次独立调用而非合并:输入域不同、可并发降延迟、JSON 校验干净。

### 4.6 数据不足降级

| 条件 | 处理 |
|---|---|
| Drawer < 5 | "擅长领域"区写"数据不足(drawer<5),完成更多任务后再合成" |
| Events < 3 | "近期任务分布"区写占位提示 |
| skill_metrics 为空 | "健康度"仅保留记忆充实度 + 活跃度 |
| 首次合成 | `previous_profile=None`,`manual_section=""`,`version=1` |

---

## §5 PromptBuilder 集成 + 前端展现

### 5.1 PromptBuilder 新增 Layer 6.5:同僚近况

现有 8 层(Identity→Court→SOUL→ROLE→MEMORY→L1→Recent→Court-Memory→Skills→Task)后,在 Layer 6(Court 共享)与 Layer 7(Skills)之间插入新层:

**Layer 6.5 Peer Profiles**

- 内容:本次执行 persona 读到**其他在场 persona** 的 PROFILE.md 摘要
- "在场" = 本 edict / DAG 链内已参与或将参与的其他 persona
- **不读自己的 PROFILE**(避免膨胀、自我强化)

**注入格式**(每位 peer 4-6 行):

```text
## 同僚近况

### 户部 (v3, 2026-04-18)
**擅长**:预算审查与成本控制;跨部门资源配额规划
**近期**:14 天 23 次任务,成本审计占 35%
**健康度**:healthy × 6, warning × 1

### 兵部 (v2, 2026-04-16)
...
```

**隐私边界**:**不注入"退化迹象"给同僚** —— 弱点是自省项,不让部门间互相看彼此短板(避免 prompt 里出现攻讦式推理)。

**配置开关**:
- `PromptBuilderConfig.include_peer_profiles: bool = True`
- `PromptBuilderConfig.peer_profile_max_chars: int = 600`

### 5.2 预算与剪裁

超 `peer_profile_max_chars` 时按顺序裁剪:
1. 砍"近期任务分布"表格,保留文字摘要
2. 砍"擅长领域"尾部,保留前 3 条
3. 无 PROFILE 的 persona 直接跳过

### 5.3 前端展现

在现有 `web/src/pages/Personas.tsx` 详情页新增 **"成长档案" tab**,与"核心记忆/近期日志/技能"并列:

| Tab | 来源 | 备注 |
|---|---|---|
| **成长档案**(新) | `GET /personas/{id}/profile` | 渲染 PROFILE.md 四区 + frontmatter badge |
| 核心记忆 | MEMORY.md | 现状 |
| 近期日志 | YYYY-MM-DD.md | 现状 |
| 技能 | skills + metrics | 现状 |

**Tab 顶部交互**:

- 🔄 **"立即合成"按钮** → `POST /personas/{id}/synthesize`,SSE 进度
- 📜 **"历史版本"下拉** → 列出 `profile_history/` 下 10 个版本(diff 视图延后)
- ✏️ **"编辑手写备注"** → Markdown 编辑器 → `PUT /personas/{id}/profile/manual`

### 5.4 新增 API 端点

| 端点 | 方法 | 用途 |
|---|---|---|
| `/personas/{id}/profile` | GET | 返回完整 PROFILE + 版本列表 |
| `/personas/{id}/synthesize` | POST | 触发合成,返回 SSE url |
| `/personas/{id}/profile/manual` | PUT | 更新手写段 |
| `/personas/{id}/profile/history/{version}` | GET | 历史版本内容 |

---

## §6 错误处理与降级策略

### 6.1 合成阶段错误分类

| 类别 | 触发点 | 策略 |
|---|---|---|
| 数据读取失败 | DrawerStore/Storage 异常 | 重试 3 次,全败则 `synthesis_failed` 事件 + 保留上版 |
| LLM 调用失败 | 网络/超时/rate limit | LLMClient tenacity 重试(3 + backoff);全败则规则段 + 占位降级 |
| LLM 返回非 JSON | 输出格式错误 | 记原始输出,2 次重试强化 JSON 指令;仍失败则占位降级 |
| 并发合成冲突 | 同 persona 双重触发 | `persona_metrics.synthesis_in_progress` 锁 + 10 分钟超时 |
| 文件写入失败 | 磁盘满/权限 | 上抛,**不覆盖**原 PROFILE(原子写:`tmp → rename`) |
| PROFILE.md 损坏 | frontmatter 解析失败 | 视为首次(`previous_profile=None`),坏版归档到 `profile_history/corrupted/` |

### 6.2 读取阶段(PromptBuilder 注入)错误

| 场景 | 策略 |
|---|---|
| PROFILE 不存在 | 静默跳过该 peer |
| frontmatter 解析失败 | 跳过 + warning log |
| 内容超 `peer_profile_max_chars` | 按 §5.2 剪裁,不报错 |
| 读 IO 失败 | 跳过 peer + warning,**不阻断 prompt 构建** |

**核心原则**:PROFILE 是增益信息,注入失败绝不阻断 Agent 执行。

### 6.3 最小可用 PROFILE(LLM 全败时降级)

```markdown
---
persona_id: hubu
version: 3
degraded: true
synthesizer_model: ""
---

## 擅长领域
(专长归纳失败:LLM 异常,下次重试)

## 近期任务分布(14 天)
[规则统计表,正常渲染]

## 健康度
[规则统计,正常渲染]

## 退化迹象
(原因分析失败。候选:cost_analysis_v1, auth_check_v2)
```

`degraded: true` → 前端显示 ⚠️ 降级 badge。

### 6.4 并发锁

`persona_metrics` 加两字段:
- `synthesis_in_progress: BOOLEAN DEFAULT 0`
- `synthesis_started_at: TIMESTAMP`

**获取锁**(SQLite 事务):

```sql
UPDATE persona_metrics
SET synthesis_in_progress=1, synthesis_started_at=?
WHERE persona_id=? AND synthesis_in_progress=0;
-- 影响行=0 → 已在跑,跳过并发
```

**死锁保护**:`synthesis_started_at` > 10 分钟仍 in_progress → 进程崩溃遗留,强制释放后重试。

**释放**:`finally` 块无条件释放。

### 6.5 原子写入

```python
def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix('.md.tmp')
    tmp.write_text(content, encoding='utf-8')
    tmp.replace(path)  # POSIX atomic rename
```

### 6.6 可观测性(EventBus 事件)

| Event | 时机 | payload |
|---|---|---|
| `profile.synthesis.started` | 进入 `run()` | persona_id, trigger_source, window |
| `profile.synthesis.completed` | 成功持久化 | version, data_sources, duration_ms |
| `profile.synthesis.failed` | 异常抛出 | error_type, error_message |
| `profile.synthesis.skipped` | 并发跳过 | reason |
| `profile.synthesis.degraded` | LLM 失败但规则段落盘 | degraded_sections |

前端 audit 页面订阅即可,无需单独 UI。

---

## §7 测试策略

### 7.1 Unit Tests — ProfileSynthesizer 核心

**基础路径**(`tests/test_profile_synthesizer.py`):

| 测试 | 验证点 |
|---|---|
| `test_collect_inputs_window` | 14 天窗口正确过滤 drawer/events |
| `test_rule_aggregation_events` | 任务分布统计正确 |
| `test_rule_aggregation_health` | 健康度按 `skill_metrics.status` 分类 |
| `test_rule_aggregation_degradation_candidates` | warning + retire_suggested 识别 |
| `test_render_markdown_snapshot` | 模板渲染格式稳定(snapshot) |
| `test_persist_atomic_write` | 无 `.tmp` 残留 |
| `test_persist_version_increment` | `version` +1 |
| `test_persist_history_prune` | 超 10 版自动 prune |
| `test_manual_section_preserved` | manual 段跨合成保留 |
| `test_manual_30pct_diff_triggers_conflict` | 阈值触发冲突 |

**降级路径**:

| 测试 | 验证点 |
|---|---|
| `test_llm_failure_falls_back` | `degraded=true` 写入 |
| `test_drawers_lt_5_placeholder` | "专长"区占位 |
| `test_events_lt_3_placeholder` | "任务分布"区占位 |
| `test_skill_metrics_empty` | 健康度跳过 skill 部分 |

**并发控制**:

| 测试 | 验证点 |
|---|---|
| `test_concurrent_synthesis_skipped` | 第二次返回 skipped |
| `test_stale_lock_reclaimed_after_10min` | 死锁强制释放 |

### 7.2 Integration Tests

**端到端合成**(`tests/test_profile_integration.py`):

- `test_synthesize_hubu_end_to_end` — mock LLM 完整走通一轮
- `test_synthesize_first_time` — 无 previous 的首次
- `test_synthesize_triggered_by_agent_end_hook` — EventBus 触发
- `test_synthesize_triggered_by_cron` — Scheduler 触发

**PromptBuilder 集成**(扩展 `tests/test_prompt_builder.py`):

- `test_layer_6_5_peer_profiles_injected`
- `test_over_budget_clipped_per_5_2_rules`
- `test_no_profile_peer_silently_skipped`
- `test_include_peer_profiles_false_disables`
- `test_self_never_injected`

**API**(`tests/test_profile_api.py`):

- `GET /personas/{id}/profile`
- `POST /personas/{id}/synthesize`
- `PUT /personas/{id}/profile/manual`
- `GET /personas/{id}/profile/history/{version}`

### 7.3 合成质量人工 review(成功标准 #5)

首次合成六部 + court + emperor 占位版后,人工对照 `personas/{id}/SOUL.md` 定位:

1. 四区内容是否与 SOUL 定位一致
2. "擅长领域"条目是否全基于真实 drawer,**无捏造**
3. "退化迹象"candidates 是否匹配 `skill_metrics` 实际状态

Review 通过 → spec 达标。

### 7.4 快照与回归

`tests/fixtures/profiles/` 存 3-5 份标准 input fixture + 期望输出:
- 不追求 byte-exact(LLM 波动)
- 验证**结构稳定 + 关键字段正确**

### 7.5 覆盖率目标

| 模块 | 目标 |
|---|---|
| `profile_synthesizer.py` | ≥ 85% |
| API endpoints | ≥ 80% |
| PromptBuilder Layer 6.5 | ≥ 90% |
| 前端 Persona tab | 不强求(smoke 手动覆盖 3 交互) |

符合 CLAUDE.md 的"功能优先、测试最后补"—— 本节产出 unit + integration 自动化覆盖,前端 smoke 手动验收。

---

## §8 实施路线图 + 风险 + 交付物

### 8.1 实施阶段(单 sprint ≈ 2 周)

| Phase | 内容 | 工作量 | 前置 |
|---|---|---|---|
| **1 数据模型与基础设施** | PROFILE schema + 版本目录 + `persona_metrics` migration + atomic write util | 1-2 d | — |
| **2 ProfileSynthesizer 核心** | `collect_inputs` + 规则聚合 + LLM prompt(擅长/退化)+ render + persist | 2-3 d | 1 |
| **3 触发接入** | EventBus AGENT_END hook(priority=250)+ Scheduler cron + CLI/API 手动 | 1 d | 2 |
| **4 PromptBuilder Layer 6.5** | Peer profile 读取 + 剪裁 + 配置开关 | 1 d | 2 |
| **5 API 端点** | 4 REST + SSE 合成进度流 | 1 d | 2 |
| **6 前端 Persona tab** | "成长档案" tab + 立即合成 + 手写编辑 + 历史列表 | 2 d | 5 |
| **7 叙事重写(#10)** | 3 design 文档 + 前端标题/首页 + README + CLAUDE.md(3 独立 commit) | 1 d | — |
| **8 测试与首次合成 review** | Unit + Integration + 首次合成六部人工 review | 2 d | 6 |

**总计**:11-14 工作日(2 周 sprint,含 buffer)。

**并行化机会**:
- Phase 7 叙事重写可全程独立并行
- Phase 4 PromptBuilder 与 Phase 5 API 可并行

### 8.2 风险识别与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| LLM 合成质量差(捏造/泛化) | 中 | 高 | Prompt 强调"禁止编造,不够就说数据不足";首次六部**人工 review 把门** |
| 首次产出内容与用户预期不符 | 中 | 中 | manual_section 出口 + `history/v1` 可回退 |
| 多 persona 同 edict 时 Layer 6.5 prompt 膨胀 | 中 | 中 | `peer_profile_max_chars=600` 硬上限 + 剪裁顺序;极端可关 `include_peer_profiles` |
| **PROFILE 成为 prompt 注入攻击面**(恶意手写) | 低 | 高 | 手写段**仅人类编辑**(不开放给 agent 工具);Layer 6.5 注入前 sanitize 去除 ` ``` ` / `[INST]` 等 marker |
| 合成并发死锁(进程崩溃锁未释放) | 低 | 中 | 10 分钟超时自动回收(§6.4) |
| 叙事改动过渡期新旧术语混乱 | 中 | 低 | §2.3 用语规范 + 分 3 commit 独立 review |
| 手写/自动 30% 冲突阈值判定不准 | 中 | 低 | 保守阈值 + 前端 diff UI 用户裁决 |

### 8.3 交付物清单

**代码**(新建/修改):
- `src/tianshu/persona/profile_synthesizer.py`(**新**)
- `src/tianshu/persona/prompt_builder.py`(Layer 6.5)
- `src/tianshu/gateway/api.py`(4 端点)
- `src/tianshu/storage.py`(`persona_metrics` schema)
- `src/tianshu/scheduler/scheduler.py`(系统 cron 注册)
- `web/src/pages/Personas.tsx` + 新组件
- `tests/test_profile_synthesizer.py` / `test_profile_integration.py` / `test_profile_api.py` / `test_prompt_builder_peer_profiles.py`

**文档**:
- `docs/design/architecture.md` / `agent-persona.md` / `memory-palace.md`(叙事)
- `docs/impl/persona.md`(加 profile_synthesizer 索引)
- `README.md`(若存在)+ `CLAUDE.md`
- 本 spec 归档 `docs/superpowers/specs/2026-04-18-persona-growth-profile-design.md`

**数据/配置**:
- `~/.tianshu/personas/{id}/PROFILE.md` × 8(6 部 + court + emperor 占位)
- `~/.tianshu/personas/{id}/profile_history/v1-*`(首版归档)

### 8.4 验收 checklist

- [ ] 3 design 文档叙事统一为"宫殿共生成长",一句话定位首页可见
- [ ] 前端主标题 + 首页副本更新
- [ ] 六部 persona 各有自动合成的 `PROFILE.md`
- [ ] `profile_history/v1-*` 存在
- [ ] PromptBuilder Layer 6.5 可配、注入同僚近况、剪裁生效
- [ ] 前端"成长档案" tab 渲染 4 区 + 手写编辑 + 历史下拉
- [ ] 4 API 端点响应正确
- [ ] EventBus 5 个 event type 在 audit 页可查
- [ ] Unit + Integration 测试 pass,覆盖率达标(85/80/90)
- [ ] 首次合成六部 persona 人工 review 通过(§7.3 三判据)
- [ ] 降级路径(LLM 失败 → 规则段 + `degraded=true`)可复现
- [ ] README + CLAUDE.md 叙事同步

---

## §9 相关与后续

### 9.1 本 spec 在 Landscape 中的位置

从本会话对齐的 10 项 Hermes 借鉴点里,本 spec 覆盖:

- **#10 叙事重写** —— 完全覆盖
- **#5 官员成长档案** —— 完全覆盖

剩余 8 项的后续 spec 顺序建议:

| 顺序 | 借鉴点 | 依赖本 spec |
|---|---|---|
| 下一个 | #1 emperor UserProfile 合成 | 复用本 spec 的 PROFILE 模式 |
| 后续 | #9 工程可靠性套件 | 独立 |
| 后续 | #4 Skills 飞轮 L0-L4 | 独立 |
| 后续 | #3 主动 nudge | 依赖 #1 |
| 后续 | #6 Dashboard 陪伴视图 | 依赖 #1 + #5 |
| 后续 | #7 常驻部署 | 独立 |
| 长线 | #2 多入口 Gateway | 依赖 #7 |
| 长线 | #8 MCP/ACP 生态兼容 | 独立 |

### 9.2 相关文档

- `docs/design/memory-palace.md` — Drawer/DrawerStore/L0-L3/FTS5
- `docs/design/agent-persona.md` — 五维 persona 模型 + runtime 分离
- `docs/impl/persona.md` — PromptBuilder 8 层实现
- `docs/impl/memory.md` — SkillMetrics / DrawerStore 细节
- `docs/impl/storage-and-events.md` — EventBus 优先级 + HookType
- `docs/superpowers/specs/2026-04-16-memory-palace-design.md` — 前置 spec
- `docs/superpowers/plans/2026-04-09-hermes-inspired-enhancements.md` — 已落地的 Hermes 借鉴

### 9.3 brainstorming 过程记录

本 spec 经 brainstorming skill 8 节对齐产出:
- §0 背景 + §1 范围 + §2 叙事 + §3 数据模型 + §4 合成流程 + §5 集成 + §6 错误 + §7 测试 + §8 路线/风险

关键决策锁定:

| 决策 | 结果 | 理由 |
|---|---|---|
| 产品定位 | "宫殿共生成长" | emperor + 六部双成长轴,保留治理隐喻同时释放分身能力 |
| PROFILE 四区 | 擅长 / 近期任务 / 健康度 / 退化迹象 | 覆盖能力/活动/健康/隐患四象限 |
| 手写保留 | 标记分段 + 30% diff 阈值 | 平衡自动化与人工裁决 |
| LLM 调用 | 仅 2 区用 LLM(擅长 + 退化原因),其余规则 | 降幻觉、降成本 |
| 触发路径 | 手动 + AGENT_END hook(N=20)+ 每日 cron | 用户主动 / 自动节流 / 定时兜底三重保障 |
| 同僚近况 | Layer 6.5,**不泄漏退化迹象** | 隐私边界,避免攻讦式推理 |
| 版本历史 | 保留 10 版 | 追溯半年前官员擅长什么 |
