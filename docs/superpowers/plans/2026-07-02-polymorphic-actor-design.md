# Polymorphic Actor 细化设计（#4）

> 状态：**设计提案，待审批实施**。来自 [multica-inspired-control-plane.md](./2026-07-02-multica-inspired-control-plane.md) 的 #4。
> 日期：2026-07-02 · 归属：Phase 3

## 背景与目标

Multica 用 `actor_type(member/agent) + actor_id` 贯穿全表，让 agent 像人一样创建 issue、评论、被 @、被订阅。天枢的六部官制天然契合这个范式——但当前"谁做了什么"分散在多个异构字段里，无法统一表达"官员 A 委派官员 B""这条记录由谁发起"。

**目标**：引入统一的 `Actor` 值对象，让 human / persona / system 三类行动者在同一套语义下表达 creator/actor，并支持**官员之间互相委派子诏令并留痕**。

**非目标**：不做多租户 member 体系（天枢单租户）；不改 persona 的加载/路由机制。

## 现状（证据）

"谁做了什么"当前散落在异构字段：

| 位置 | 字段 | 类型 | 语义 |
|---|---|---|---|
| `models/edict.py` | `submitter` | `str \| None` | 提交者（自由文本） |
| `models/edict.py` | `assigned_persona_id` | `str \| None` | 指派执行官员 |
| `models/edict.py` | `planner_persona_id` | `str \| None` | 规划官员 |
| `models/memorial.py` | `persona_id` | `str \| None` | 执行该次的官员 |
| `models/decree.py` | `actor` | `str`（默认 `"human"`） | 批红者 |
| `models/events.py` | `producer` | `str`（`scheduler`/`executor`/`orchestrator`/`system`） | 事件产生方 |
| `consultation/` | `persona_ids` + `synthesizer_persona_id` | `list[str]` | 多官员会诊（已有协同雏形） |

问题：① 同一个"官员"概念在 5 处用不同字段名/类型表达；② `submitter`/`actor` 是自由文本，无法区分"人类张三"和"官员内阁"；③ 无法表达"官员 A 在执行中发起了一个交给官员 B 的子任务"。

## 设计

### 1. Actor 值对象（不是新表）

```python
# models/actor.py
class ActorKind(str, Enum):
    HUMAN = "human"      # 用户/皇帝
    PERSONA = "persona"  # 六部官员
    SYSTEM = "system"    # 系统/天（cron、sweeper、自动化）

class Actor(BaseModel):
    kind: ActorKind
    id: str                       # persona_id / user_id / 系统组件名
    display_name: str | None = None
```

值对象、可内嵌进任意模型的 JSON 列，**不新增表**。六部隐喻对齐：`human`=下旨的人、`persona`=承旨的官员、`system`=按天时自动运转（cron/sweeper/evolve）。

### 2. 统一 creator/actor 字段（渐进，不破坏现状）

- `Edict` 增 `creator: Actor | None`（谁发起了这道诏令）。保留 `submitter` 兼容，新增 `creator` 从 submitter/来源推导回填。
- `Memorial` 的 `persona_id` 保留；新增便捷属性 `actor` → `Actor(persona, persona_id)`（读方向聚合，不改存储）。
- `Decree.actor: str` → 升级为 `Actor`（migration 把 `"human"` 映射为 `Actor(human, ...)`）。
- `Event.producer` 保留（它是"组件名"而非"行动者"，语义不同，不强行统一）。

### 3. 官员委派子诏令（Actor 的杀手级用法）

新增工具 `delegate_edict`（T2/需授权），让执行中的官员 A 发起一道交给官员 B 的子诏令：

```
子 Edict.creator      = Actor(persona, A.id)     # 谁委派的
子 Edict.assigned_persona_id = B.id              # 委派给谁
子 Edict.metadata["parent_edict_id"] = 当前 edict.id  # 委派链
```

复用现有 Edict 提交链路（`edict.submitted` 事件），天然获得调度/审计/记忆。委派链通过 `parent_edict_id` + `creator` 可完整追溯——朝廷协作叙事落到数据。与 `consultation`（同级会诊）互补：consultation 是"平级问策"，delegate 是"下派任务"。

## 数据/接口变更

| 文件 | 变更 |
|---|---|
| `models/actor.py`（新） | `Actor` + `ActorKind` |
| `models/edict.py` | +`creator: Actor \| None` |
| `models/decree.py` | `actor: str` → `Actor`（+ migration 映射旧值） |
| `storage.py` | edict `creator_json` 列 + migration；decree actor 序列化升级 |
| `tools/`（新） | `delegate_edict` 工具 |
| `web/` | 时间线/详情页展示 Actor（头像+名，human/persona/system 区分图标） |

## 分步交付（低风险增量）

1. **A**：`Actor` 值对象 + `Edict.creator`（回填 submitter），纯读增强，零行为变化。
2. **B**：`Decree.actor` 升级为 `Actor` + migration。
3. **C**：`delegate_edict` 工具 + `parent_edict_id` 委派链 + 前端展示。

## 风险与权衡

- **兼容性**：`submitter`/`persona_id` 保留不删，`creator`/`actor` 增量叠加，存量数据 migration 回填。避免大爆炸式重构。
- **委派递归**：`delegate_edict` 可能被官员滥用形成委派风暴 → 复用现有 tier 授权 + 加委派深度上限（metadata 记 depth，超限拒绝）。
- **价值定位**：本项主要强化"多官员协作"的产品叙事与可追溯性；若近期不主推协作叙事，可仅做 A（Actor 值对象打底），B/C 缓做。
