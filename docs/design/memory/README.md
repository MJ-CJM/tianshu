# Memory（记忆宫殿）— 设计总览

## 1. 职责定位

记忆子系统让六部官员（persona）跨任务积累经验：执行后沉淀、执行前召回、周期性反思与压缩。它把「一个多 Agent 系统的长期记忆」组织成可检索、可消融、可分层注入的结构，用明朝**宫殿**隐喻命名各层级。

## 2. 核心设计判断

| 判断 | 选择 | 理由 |
|---|---|---|
| 真相源在哪 | **Markdown 文件**（`~/.tianshu/memory/`）是 Source of Truth | 人可读、可手改、git-friendly；不让 SQLite 成为唯一记忆源 |
| SQLite 角色 | **派生索引**（可从 MD 重建） | 服务 Web/API 查询与 FTS5 全文检索；索引坏了 `sync_index` 重建 |
| 写一致性 | **write-through**：写 MD 后同步刷 SQLite + FTS | MD 写成功即视为持久化；索引写失败不阻断 |
| 读路由分离 | Agent 读 MD + Drawer，Web/API 读 SQLite | 两条路径互不污染，Agent 执行永不经过 Web 索引 |
| 最小存储单元 | **Drawer**（verbatim 原文片段，≤800 字符） | 不做即时摘要避免信息损耗，后续可选降维 |
| 记忆隔离 | Wing（人格）私有，court wing 跨人格共享 | 隐私边界清晰；共享走显式 court 通道 |

## 3. 宫殿隐喻与三层记忆

| 隐喻 | 对应 | 隐喻 | 对应 |
|---|---|---|---|
| Palace 宫殿 | 全局 `DrawerStore` | Drawer 抽屉 | 最小存储单元 |
| Wing 偏殿 | `persona_id` / `court` / `emperor` | Closet 索引柜 | 主题指针（预留） |
| Room 房间 | 主题（execution/planning/…） | Tunnel 暗道 | 跨 wing 链接（预留） |

记忆分三层落盘：**L1 Drawer**（SQLite `drawers.sqlite3`，独立库）、**L2 Markdown**（真相源）、**L3 索引**（主库 `memory_entries` + FTS5）。检索分 L0–L3 注入层级，详见 backends.md / recall.md。

## 4. 与相邻子系统关系

| 子系统 | 关系 |
|---|---|
| executor / hooks | `BEFORE_AGENT_START` 注入召回；`AGENT_END` 写记忆 + retain drawers |
| persona / PromptBuilder | MEMORY.md（Layer 5）、L1 关键事实（Layer 5.1）、近期日志（Layer 5.5）、court（Layer 6）由 PromptBuilder 组装 |
| auditor | `audit.completed` verdict∈{flag,block} 时写 ducha insight（court 可见） |
| tools | `memory_search` / `memory_write` 工具是 Agent 主动读写记忆的入口 |

## 5. 本目录子文档

| 文档 | 主题 |
|---|---|
| [palace.md](palace.md) | 宫殿/偏殿/房间/抽屉隐喻、Drawer 数据模型、MemoryManager 生命周期 |
| [backends.md](backends.md) | Markdown 后端、Drawer 独立 SQLite、三层记忆（L1/L2/L3） |
| [recall.md](recall.md) | FTS5 全文检索、官员间记忆全局读、注入安全扫描（safety） |

**相关实现**：[../../impl/memory/](../../impl/memory/)
