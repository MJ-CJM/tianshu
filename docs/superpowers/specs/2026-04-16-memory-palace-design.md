# Memory Palace — 联邦制记忆宫殿设计

> 借鉴 mempalace 项目模式，为 Tianshu 多官员体系设计持久化记忆系统。

## 1. 问题陈述

Tianshu 官员目前的记忆是**扁平文件**（MEMORY.md + 每日日志），存在以下瓶颈：

| 问题 | 现状 | 影响 |
|------|------|------|
| 检索效率 | 全文读取 MEMORY.md，无语义搜索 | 上下文窗口浪费，相关记忆未被召回 |
| 记忆隔离 | 每位官员独立文件，无跨域关联 | 兵部的部署教训，户部看不到成本影响 |
| 用户视角 | 无统一入口查看"系统学到了什么" | 用户无法掌握官员进化状态 |
| 量化验证 | 无指标证明记忆有用 | 不知道记忆注入到底提升了多少 |

## 2. 核心架构：联邦制宫殿

### 2.1 结构映射

```
Palace（天书记忆系统）
│
├── Wing: emperor           ← 用户私有翼
│   ├── Room: decisions         决策记录（自动捕获）
│   ├── Room: feedback          对官员的纠正和肯定
│   ├── Room: subscriptions     从官员翼订阅的精选
│   └── Room: insights          文渊阁推送的跨域洞察
│
├── Wing: neige             ← 内阁首辅
│   ├── Room: planning          任务分解策略
│   ├── Room: delegation        委派决策经验
│   └── Room: patterns          反复出现的任务模式
│
├── Wing: bingbu            ← 兵部尚书
│   ├── Room: execution         执行教训（成功/失败）
│   ├── Room: tools             工具使用心得
│   └── Room: recovery          错误恢复策略
│
├── Wing: ducha             ← 都察院
│   ├── Room: audit             审计发现
│   ├── Room: risks             风险模式库
│   └── Room: false-positives   误报记录（避免重复）
│
├── Wing: wenyuan           ← 文渊阁
│   ├── Room: reflections       跨域反思
│   ├── Room: consolidation     知识整理记录
│   └── Room: patterns          跨官员共性模式
│
├── Wing: hubu              ← 户部
│   ├── Room: cost-patterns     成本规律
│   ├── Room: budgets           预算决策
│   └── Room: anomalies         异常消费记录
│
├── Court Wing（共享朝堂）   ← 所有官员 + 用户可访问
│   ├── Room: decisions         会商确认的决策
│   ├── Room: lessons           跨域教训
│   └── Room: protocols         工作规范
│
└── Tunnels（跨翼隧道）
    ├── bingbu/execution ↔ hubu/cost-patterns
    ├── ducha/audit ↔ neige/planning
    └── emperor/subscriptions ↔ {any wing/room}
```

### 2.2 数据模型

```python
@dataclass(frozen=True)
class Drawer:
    """最小记忆单元 — 存储原始内容片段。"""
    id: str                    # 唯一 ID
    wing: str                  # 所属翼（官员 ID 或 "court"）
    room: str                  # 所属房间（主题）
    content: str               # 原始内容（最大 800 字符）
    source_edict_id: str       # 来源 edict
    timestamp: str             # ISO 时间戳
    category: str              # W(world)/B(biographical)/O(opinion)/D(decision)
    confidence: float          # 置信度 0.0-1.0
    chunk_index: int           # 分块索引

@dataclass(frozen=True)
class Closet:
    """主题指针 — 不存内容，只存索引。"""
    id: str
    wing: str
    room: str
    topics: list[str]          # 关键词
    entities: list[str]        # 实体引用
    drawer_ids: list[str]      # 指向的 drawer

@dataclass(frozen=True)
class Tunnel:
    """跨翼隧道 — 连接两个 room。"""
    from_wing: str
    from_room: str
    to_wing: str
    to_room: str
    reason: str                # 为什么连接
    created_by: str            # 谁创建的（官员 ID 或 "emperor"）
```

### 2.3 4 层记忆栈

每位官员（和用户）的上下文注入分 4 层，按需加载：

| 层 | 内容 | Token 预算 | 加载时机 | 来源 |
|----|------|-----------|---------|------|
| **L0** | 身份（SOUL.md） | ~100 | 始终 | 本地文件 |
| **L1** | 关键事实（Top-K drawer） | ~500-800 | 始终 | 本翼高置信度 drawer + Court Wing |
| **L2** | 按需召回（wing/room 过滤） | ~200-500 | 任务相关 | 语义搜索 + 过滤 |
| **L3** | 深度搜索（全 Palace） | 无上限 | 明确请求 | 全量语义搜索 |

**L1 生成策略**：扫描本翼所有 drawer，按 `confidence × recency_decay` 打分，取 Top-15，按 room 分组，截断到 3200 字符。

## 3. 存储后端

### 3.1 Backend Protocol

```python
class MemoryBackend(Protocol):
    """可插拔记忆存储后端。"""

    async def store_drawer(self, drawer: Drawer) -> str: ...
    async def store_closet(self, closet: Closet) -> str: ...

    async def search(
        self,
        query: str,
        wing: str | None = None,
        room: str | None = None,
        n_results: int = 10,
    ) -> list[DrawerResult]: ...

    async def get_drawers(
        self,
        wing: str,
        room: str | None = None,
        limit: int = 100,
    ) -> list[Drawer]: ...

    async def delete_drawer(self, drawer_id: str) -> bool: ...

    async def get_l1(self, wing: str, max_chars: int = 3200) -> str: ...
```

### 3.2 默认实现：Markdown + SQLite FTS5

**Phase 1（最小可用）**：沿用现有 Markdown 文件，加 SQLite FTS5 索引。

```
~/.tianshu/memory/
├── emperor/
│   ├── MEMORY.md              ← L0+L1 的人类可读版本
│   ├── drawers/               ← drawer 文件（JSON Lines）
│   └── 2026-04-16.md          ← 每日日志（现有格式兼容）
├── neige/
│   ├── ...
├── court/
│   ├── ...
├── tunnels.json               ← 隧道定义
└── index.sqlite3              ← FTS5 全文索引（派生数据，可重建）
```

- **源头**：Markdown 文件（人类可读、可编辑）
- **索引**：SQLite FTS5（从文件派生，`tianshu memory rebuild` 可重建）
- **无向量依赖**：Phase 1 不引入 ChromaDB，用 BM25 关键词搜索即可

### 3.3 Phase 2 可选升级：向量检索

当 BM25 检索质量不够时，可换 `ChromaBackend`：

- 实现同一 `MemoryBackend` Protocol
- 用 ChromaDB 存向量 + 元数据
- 混合搜索：0.6 × 向量相似度 + 0.4 × BM25
- Closet 作为排名信号（提升而非门控）

### 3.4 数据安全

| 机制 | 来源 | 实现 |
|------|------|------|
| 原子写入 | Phase 1A 已实现 | `_atomic_write()` |
| 文件锁 | mempalace `mine_lock()` | `fcntl.flock()` / `msvcrt.locking()` |
| Write-Ahead Log | mempalace WAL | 每次写入前追加 JSONL，crash 后可恢复 |
| 版本化迁移 | mempalace `NORMALIZE_VERSION` | drawer 元数据带 `schema_version`，升级时静默重建 |

## 4. 记忆生命周期：Retain → Recall → Reflect

### 4.1 Retain（执行后保留）

每次 edict 执行完成后（AGENT_END hook）：

1. 执行官员从 memorial 中提取关键记忆
2. 分类为 D(decision) / W(world-fact) / O(opinion) / B(biographical)
3. 写入本翼对应 room 的 drawer
4. 如果涉及用户明确决策，额外写入 Emperor Wing / decisions

**提取策略**：不用 LLM 摘要，直接存原文分块（mempalace 的核心主张：verbatim storage）。800 字符分块，段落边界切分。

### 4.2 Recall（执行前召回）

edict 开始执行前：

1. 从 edict 的 goal 文本生成查询
2. 搜索执行官员的 Wing（L2）
3. 搜索 Court Wing（L2）
4. 如果有 Tunnel，搜索关联 Wing（L2）
5. 合并去重，按相关性排序
6. 注入到系统 prompt 的 L2 层

### 4.3 Reflect（定期反思）

由文渊阁（wenyuan）执行，触发条件：每 N 次 edict 或每日一次。

1. 扫描所有官员最近的 drawer
2. 识别跨域模式（如：bingbu 执行失败 → ducha 审计发现 → 共同原因）
3. 将洞察写入 Court Wing / lessons
4. 判断是否推送到 Emperor Wing / insights
5. 清理低置信度、过时的 drawer（confidence < 0.3 且超过 90 天）

## 5. 用户（Emperor）交互

### 5.1 订阅机制

用户通过 Tunnel 选择性接入官员记忆：

```python
# API 示例
POST /api/tunnels
{
    "from_wing": "emperor",
    "from_room": "subscriptions",
    "to_wing": "bingbu",
    "to_room": "execution",
    "reason": "关注执行质量"
}

DELETE /api/tunnels/{tunnel_id}   # 取消订阅
GET /api/tunnels                  # 查看所有订阅
```

### 5.2 记忆查询

```python
# 查询自己的记忆
GET /api/memory/search?query=为什么选择Postgres&wing=emperor

# 查询特定官员的记忆
GET /api/memory/search?query=部署失败&wing=bingbu&room=execution

# 全 Palace 搜索
GET /api/memory/search?query=成本超支

# 查看 L1 关键事实
GET /api/memory/l1?wing=emperor
GET /api/memory/l1?wing=neige
```

### 5.3 Web UI

在现有 AuditDashboard 中新增 Memory 标签页：

- **Palace Overview**：各 Wing 的 drawer 数量、最近活跃度
- **L1 Dashboard**：每位官员的关键事实一览
- **Search**：跨 Wing 语义搜索
- **Tunnels**：可视化管理订阅关系
- **Timeline**：按时间线浏览记忆写入事件

## 6. 消融实验与指标

### 6.1 必须量化的指标

| 指标 | 计算方式 | 目的 |
|------|---------|------|
| **记忆命中率** | recall 返回结果中被 LLM 实际引用的比例 | 召回质量 |
| **记忆贡献率** | 有记忆 vs 无记忆，edict 成功率差异 | 证明记忆有用 |
| **多官员增益** | 多官员流程 vs 单 agent，效果差异 | 证明架构有用 |
| **跨域洞察率** | Tunnel 带来的记忆被引用的比例 | 证明联邦制有用 |
| **L1 精度** | L1 中被后续 edict 实际用到的条目比例 | L1 筛选质量 |

### 6.2 消融开关

每个能力都可单独关闭，用于对比实验：

```python
class MemoryConfig:
    enabled: bool = True               # 总开关
    l1_enabled: bool = True            # L1 关键事实注入
    l2_recall_enabled: bool = True     # L2 执行前召回
    reflect_enabled: bool = True       # 定期反思
    tunnels_enabled: bool = True       # 跨翼隧道
    emperor_wing_enabled: bool = True  # 用户翼
    verbatim_mode: bool = True         # True=存原文, False=存摘要
```

### 6.3 Benchmark 命令

```bash
# 运行记忆效果基准测试
tianshu benchmark memory --tasks sample_edicts.json

# 消融：关闭记忆
tianshu benchmark memory --tasks sample_edicts.json --no-memory

# 消融：关闭多官员
tianshu benchmark memory --tasks sample_edicts.json --single-agent

# 输出指标报告
tianshu benchmark report --compare run-001 run-002
```

## 7. 实施优先级

```
Phase 1 — 最小闭环 (M)
├── MemoryBackend Protocol 定义
├── Markdown + FTS5 默认实现
├── Retain：AGENT_END 后写 drawer
├── Recall：edict 前搜索注入 L2
├── L1 生成（Top-K 打分）
└── CLI: tianshu memory search / tianshu memory l1

Phase 2 — 联邦制 (L)
├── Wing/Room 结构化存储
├── Tunnel CRUD API + Web UI
├── Emperor Wing 自动捕获决策/反馈
├── 订阅机制
└── 消融开关 + 基础指标采集

Phase 3 — 进化 (L)
├── Reflect 机制（文渊阁定期反思）
├── 跨域洞察推送
├── 向量检索后端（可选 ChromaDB）
├── Benchmark 命令
└── Memory Dashboard Web UI

Phase 4 — 矛盾检测 (M)
├── 知识图谱（时序实体关系）
├── 矛盾检测器
├── 事实过期自动标记
└── 置信度衰减
```

**Phase 1 是关键**：做出「Retain → Recall → 效果可感知」的最短闭环。不引入向量数据库、不做复杂 UI，先证明记忆有用。

## 8. 不做的事

- **不做 AAAK 方言压缩** — mempalace 自己承认 AAAK 在小规模下反而增加 token，且 R@5 降了 12.4 个点
- **不做全量对话存储** — 只存 edict 执行结果中的关键片段，不是每句对话
- **不自建 embedding 模型** — Phase 1 用 BM25，Phase 3 用 ChromaDB 默认 embedding
- **不做实时同步** — 记忆写入是异步的，不阻塞 edict 执行
