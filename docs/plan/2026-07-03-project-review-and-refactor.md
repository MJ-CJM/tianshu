# 天枢全项目 Review 与优化设计方案

> 日期：2026-07-03 · 分支：feat_phase8 · 评审范围：后端 `src/tianshu/`（约 44k 行 / 271 文件）+ 前端 `web/src/`（约 21k 行）+ 工程配置
> 方法：5 路并行深度评审（架构分层 / 巨型文件 / 重复代码 / 前端信息架构 / Python 规范），全部发现带 `file:line` 证据并经主线交叉验证；测试基线 1271 个用例全绿（47s）。

---

## 0. TL;DR

**总评：骨架好，欠账集中。** 这不是一个失控的代码库——核心域建模完整（Edict/Memorial/Decree 全走 Pydantic v2 且零 v1 残留）、日志规范满分、`datetime.utcnow()` 零残留、事件总线实现干净、测试 1271 个全绿、CLI 与前端是抽象品味的正面样板。真正的问题高度集中在五处：

1. **【P0】同步 SQLite 在 148 个 async 路由里裸调用**，其中一条链路（代码变体评估）能把整个进程（含 `/health`）卡死数分钟；
2. **四个巨型文件**（storage.py 4028 / api.py 3347 / app.py 806 / loop.py 881）+ 前端 SystemManagementPage 2039；
3. **跨层契约错位**（`hooks/exit_reason/ambient` 住在 executor 里）造成 5 对循环依赖，装配层被迫用 6 个 `object` 类型 setter 兜底；
4. **飞书/Telegram 双通道约 700–850 行复制粘贴**（telegram 侧 docstring 自述"镜像 feishu/xxx.py"）；
5. **工程化工具链为零**（无 ruff/mypy/coverage，`.claude/rules` 写了规范却无自动化兜底）。

**御书房合并问题的直接回答：可以合并，且建议合并，保留「御书房」名。** 敕令总览与御书房底层是同一资源（`listEdicts`）的两个视图（全量表格 vs open 活动卡片流），合并为单页双 Tab（「待处置」/「全部敕令」），「颁发敕令」降为页内按钮，菜单净减 2 项。详见 §3.5。

---

## 1. 评审总评

### 1.1 做得好的（保持，不要动）

| 亮点 | 证据 |
|---|---|
| 核心域契约完整 | `models/` 10 文件；storage 读侧对核心域返回真 model（`get_edict -> Edict \| None`，storage.py:869） |
| Pydantic v2 干净 | `class Config` / `.dict()` / `@validator` 全项目 **0 命中** |
| 日志规范满分 | src 下（除 cli）`print()` 0 处；f-string 传 logger 0 处；139 处 `getLogger(__name__)` 风格统一 |
| 现代化程度高 | `datetime.utcnow()` 0 处（130 处 `now(UTC)`）；内置泛型 1231 处 vs 旧式 typing 仅 1 处残留 |
| 事件总线内核 | `bus/event_bus.py` 116 行：优先级、异常隔离、持久化、emit/fire 双模式，设计干净 |
| CLI 架构正确 | 全走 HTTP 调后端（`cli/commands/edict.py:39`），业务逻辑不重复 |
| 前端抽象刚好 | 统一 axios client + react-query 惯用法，无需改动 |
| 测试安全网 | 1271 用例全绿 47s；gateway 走 TestClient 集成测试风格合理 |
| 不可变实践 | `LoopState` frozen dataclass、`LLMConfigState/AgentConfigState` frozen |
| 微模块划分 | bus/dag/auditor/consultation 看似小，但职责正交（详见 §6 不要合并清单） |

### 1.2 宣称架构 vs 实际形状

README 宣称单向分层：入口 → 契约 → 编排 → 治理 → 成长 → 存储。**实际是以 executor 和 storage 为双核的缠绕图**：

- **executor 成了引力中心**：`HookType/HookResult`（executor/hooks.py）、`ExitReason`（executor/exit_reason.py）、`ambient`（executor/ambient.py）本质是跨层共享契约，却物理上住在 executor 包里，被 memory/skills/cost/tools 四个模块反向 import（如 memory/manager.py:355、skills/reviewer.py:12、cost/manager.py:107、tools/builtins.py:9），形成 4 条上向循环。
- **storage 既是最底层又反够上层**：被 16/24 个模块直接依赖 + 注入 17 个类构造函数；同时自身函数内惰性 import `memory.fts`（storage.py:44,1406）和 `secrets.vault`（storage.py:3792 等 4 处）——"明知有环所以藏进函数体"。
- **models 反依赖 dag**：`models/__init__.py:31` import `dag/models.py`——真相是 dag/models.py 本身就是领域契约，放错了位置。
- **装配层用 6 个 `object` 类型 setter**（executor/executor.py:52-67）+ 11 处 lifespan 内惰性 import 才把缠绕图兜住，类型契约在组装处完全丢失。

另外两处「文档说的比代码做的多」：README 宣称"全链路事件解耦"，实际事件仅覆盖主干里程碑（全项目 24 处 `.emit`、11 个事件类型，子系统内部全是直调——这个划分本身合理，但表述过头）；文档写"18 张表"，实际已 38 张业务表。

---

## 2. 问题分级清单

### P0 — 正确性 / 可用性（立即修）

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| P0-1 | **同步 SQLite 阻塞 event loop**：storage.py 全程同步 sqlite3（:5,:58），0 个 async 方法；api.py 148 个 `async def` 路由里 103 处裸调 `storage.xxx()`，全项目 `to_thread` 仅 2 处且与 storage 无关。`async def` 声明恰好绕过了 FastAPI 对同步路由自动丢线程池的保护 | storage.py:5,52-60；api.py:214-222 | 慢查询（FTS 搜索、audit 聚合 :1273）卡住所有并发请求 |
| P0-2 | **代码变体评估链路可冻结全站**：`api.py:674 await evolver.run()` → `evolver.py:279 eval_harness.evaluate()`（同步）→ `eval_harness.py:154 time.sleep(2)` 轮询，默认单 goal 超时 300s。cron 同样触发（scheduler.py:112），且 Scheduler 与 FastAPI 同一 event loop（app.py:758） | 完整链路已验证 | 一次评估卡死全站（含 /health）数分钟到数十分钟 |
| P0-3 | **插件注册工具 latent bug**：`plugins/api.py:63 register_tool` 把 `schema: dict \| None` 传给 `ToolRegistry.register` 的 `definition: ToolDefinition` 参数，类型不匹配 | plugins/api.py:63 ↔ tools/registry.py:34-40 | 插件注册工具路径从未真正可用；mypy 一秒可抓 |
| P0-4 | **前端 `?tab=` 深链失效**：代码用 `navigate('/audit?tab=network')` 等深链，但目标页用 `defaultActiveKey` 写死（SystemManagementPage.tsx:1987），不读 searchParams | fe 证据见 §3.5 | 跳转落错 Tab |
| P0-5 | **御书房徽标口径不一致**：侧边栏徽标用 needs_review 计数（AppSidebar.tsx:42-43），页面标题用全部 open 计数（ApprovalQueuePage.tsx:98） | 同左 | 徽标显示 3、页面列 20 张卡 |

### P1 — 结构性欠账（本方案主体）

| # | 问题 | 规模 |
|---|---|---|
| P1-1 | storage.py God Module：单类 38 表 / 180 方法 / 4028 行 / 23 个领域簇；`_create_tables` 462 行、`_migrate` 203 行；feishu/telegram 通道表混入核心层 | §3.3 |
| P1-2 | gateway/api.py：147 路由挤单文件 3347 行；项目内已有正确的子 router 范式（tongzheng_api.py 等）却未沿用 | §3.3 |
| P1-3 | app.py lifespan 698 行"上帝组合根"：53 处 `app.state.X`、6 个嵌套闭包、11 处惰性 import | §3.3 |
| P1-4 | 跨层契约错位 → 5 对循环依赖（tools↔executor、storage↔memory、storage↔secrets、memory↔persona、skills↔executor） | §3.2 |
| P1-5 | 双通道复制：mode_router ~95% / assistant_branch ~92% / edict_branch ~90% / outbound 事件层 ~90% / dispatcher 批处理核 ~95% / approval handler ~90% / card_builder budget SQL 逐字节相同，合计可删 ~700–850 行 | §3.4 |
| P1-6 | 前端：SystemManagementPage 2039 行塞 9 个 Tab；菜单 15 项扁平；敕令总览/御书房功能重叠 | §3.5 |
| P1-7 | 巨型函数：`lifespan()` 698 行、`agent.execute()` 473 行、`loop.run()` 386 行；全项目 >50 行函数 122 个 | §3.3 |

### P2 — 工程化缺口

| # | 问题 |
|---|---|
| P2-1 | 零 lint/format/type 工具链：pyproject 无 `[tool.ruff]`/mypy/coverage 配置，dev 依赖无对应包，无 pre-commit；`.claude/rules/python/coding-style.md` 写了 black/isort/ruff 但从未落地 |
| P2-2 | 依赖未分层：`lark-oapi`、`python-telegram-bot`、`trafilatura`、`mcp` 全在核心 dependencies——不用飞书/Telegram 的部署也被迫安装 |
| P2-3 | 测试盲区：`dag`、`consultation`、`plugins`、`cli` 四个模块零覆盖；providers/cost 单薄 |
| P2-4 | 配置默认值重复：`agent_max_iterations=20` 等同时硬编码在 config.py（TianshuSettings）与 config_manager.py（AgentConfigState）两处 |

### P3 — 卫生问题

| # | 问题 |
|---|---|
| P3-1 | 后端疑似死代码 4 处（需人工确认后删除或接线）：`executor/streaming.py`（47 行，Protocol 从未接入）、`persona/department.py`（模型零引用）、`plugins/installer.py`（48 行零引用）、`executor/orchestrator/archive.py`（仅自己的测试引用，疑似漏接线） |
| P3-2 | 前端死代码：`ProviderDashboardPage.tsx`（161 行，功能已并入系统管理 Tab）整页可删；`OpsMonitorPage.tsx`（609 行）死页活组件——3 个 Tab 组件被 AuditDashboard 复用但页面外壳无路由 |
| P3-3 | `nav.war`（兵部）i18n key 三套语言都定义但菜单未使用 |
| P3-4 | 吞异常无日志：storage.py:2701-2776 连续 6 处 `except Exception: pass`（`_row_to_edict/_row_to_memorial` 的 JSON 反序列化）、executor/hooks.py:169（hook 审计写入失败被吞） |
| P3-5 | 样板重复：`goal[:20]+"…"` 标题截断 5 处副本（且 approvals.py:371 已出现 `...` vs `…` 分歧）；"建敕令+建 memorial+fire 事件"流程 3 处近乎复制（api.py:158-177、edict_bridge.py:140-172、submit_edict.py:111-132）；状态→中文标签映射 4 份副本；cli/client.py 4 个函数重复同段 try/except |
| P3-6 | `providers/protocol.py` 名不副实（无任何 Protocol，只有 DTO） |
| P3-7 | 文档漂移：18 表 vs 实际 38 表；"全链路事件解耦"表述过头；architecture.md 仍引用已无路由的 OpsMonitorPage |

---

## 3. 六大优化工程

### 3.1 工程一：异步安全化（P0，最先做）

**目标**：任何单个请求/定时任务不能阻塞 event loop。**不引入 aiosqlite、不改 SQL**——务实解法。

1. **止血 evolver 链路**（P0-2）：`eval_harness.evaluate()` 整体用 `asyncio.to_thread` 包裹（evolver.py:279 调用处），或把 `_run_goal` 的 `time.sleep(poll_interval)` 轮询改造为 async + `asyncio.sleep`。前者改动最小，推荐。
2. **api.py 路由去 async 化**：拆分 api.py 时（§3.3），凡"纯 DB 读写、函数体内无 `await`"的路由**改回 `def`**——FastAPI 自动丢线程池，配合现有 `check_same_thread=False` + WAL + `threading.Lock`（storage.py:52,58）恰好线程安全。真正需要 `await` 的路由（触发事件、调 LLM）保持 async，其中的慢查询点（`get_audit_stats`、`search_memory`、`list_edicts`）用 `await asyncio.to_thread(storage.xxx, ...)` 包裹。
3. **同类问题一并处理**：`plugins/installer.py` 的 `async def install_pip` 内同步 `subprocess.run`（当前是死代码，若保留则一并修）。

**验证**：压测一条慢查询路由（如 FTS 搜索）+ 并发 `/health`，确认 p99 不再互相拖累；触发一次代码变体评估，确认期间其他 API 正常响应。

### 3.2 工程二：kernel 契约层（打破循环依赖，性价比最高）

**目标**：一次移动，消除 5 对循环依赖中的 4 条上向循环 + models→dag 反向依赖。

```
src/tianshu/kernel/            # 不依赖任何业务模块的中立内核
├── __init__.py
├── hooks.py                   # ← 从 executor/hooks.py 移入（HookType/HookResult/HookRegistry）
├── exit_reason.py             # ← 从 executor/exit_reason.py 移入
└── ambient.py                 # ← 从 executor/ambient.py 移入（get_current_edict/persona）
```

- `dag/models.py`（DAGExecution/DAGNode/DAGNodeStatus）并入 `models/dag.py`，`dag/` 只留纯算法（graph.py）。
- **兼容策略**：`executor/hooks.py` 等原位置保留一行 re-export（`from tianshu.kernel.hooks import *  # noqa`）过渡一个版本，新代码 import kernel，存量 import 不破。
- storage 的反向依赖同步倒置：`memory.fts` 的建表/检索逻辑回归 memory 模块（storage 拆分时顺带做，见 §3.3）；`secrets.vault` 同理。

**验证**：`grep -rn "^from tianshu" src/tianshu` 重新聚合依赖矩阵，确认无环；全量测试绿。

### 3.3 工程三：巨型文件拆分

**执行顺序：api.py（风险最低）→ storage.py → app.py → loop.py。每簇/每 Mixin 一个独立 commit，可回滚。**

#### 3.3.1 gateway/api.py（3347 行 → 约 15 个文件）

完全复用项目已有范式（credentials_api.py / hongluisi_api.py / tongzheng_api.py 都是独立 APIRouter，挂载点 app.py:789-793）：

```
gateway/
├── api.py            # 瘦身为 /ws + 杂项，或最终清空
├── _helpers.py       # _build_history 等跨路由纯函数
├── edicts_api.py     # ~22 路由（含 plan/pause/follow-up/outer-loop/supervision）
├── universes_api.py  # ~19 路由
├── personas_api.py   # ~16 路由（personas + persona-templates + skills-curate）
├── memory_api.py     # ~13 路由（memory + memory-palace）
├── providers_api.py / config_api.py / mcp_api.py / skills_api.py
├── cost_api.py / dag_api.py / plugins_api.py / departments_api.py
├── policy_api.py / system_prompt_api.py
└── misc_api.py       # audit/decrees/approvals/workers/consultations/event-bus/hooks 等杂项
```

- **唯一风险是 URL 漂移**：现有路由路径写全（`/edicts/...`，无 prefix），拆分后子 router 用 `APIRouter(prefix="/edicts")` + 去前缀路径。迁移每簇后对比 `/docs` 的 OpenAPI 路径清单前后一致。
- 所有路由靠 `request.app.state.xxx` 取依赖，无模块级共享状态，切割干净。
- 顺带完成 §3.1 的 def/async 甄别。

#### 3.3.2 storage.py（4028 行 → storage/ 包，Mixin 组合）

**核心约束**：103+ 处外部调用形如 `storage.get_edict(...)`。Mixin 组合让公有 API 一字不变、调用点零改动：

```
storage/
├── __init__.py          # from .facade import Storage（保住 from tianshu.storage import Storage）
├── _base.py             # _StorageBase：_conn/_lock/_db_path/_fts_available
├── schema.py            # DDL 常量 + _create_tables（462 行 SQL 挪出）
├── migrations.py        # _migrate（203 行挪出）
├── mappers.py           # 全部 _row_to_* 纯函数（先抽，无状态最安全）
├── edict_repo.py        # EdictMixin ~230 行
├── memorial_repo.py     # MemorialMixin（memorial+decree）~230 行
├── event_repo.py / memory_repo.py / cost_repo.py / config_repo.py
├── dag_repo.py / scheduler_repo.py / persona_repo.py / universe_repo.py
├── credential_repo.py / orchestrator_repo.py / channel_repo.py
├── feishu_repo.py       # ~185 行（通道表隔离出核心层）
├── telegram_repo.py     # ~170 行
└── facade.py            # class Storage(_StorageBase, EdictMixin, ...): pass
```

- **事务边界零风险**：所有 Mixin 是同一实例，`self._conn/self._lock` 天然共享。
- 拆分时顺带：6 处 `except Exception: pass` 抽成 `_safe_json_load(raw, field)` 并补 `logger.warning`（P3-4）；52 个返回裸 dict 的方法后续渐进收敛到 model（不强求一次到位）。
- **不建议**进一步演进为 `storage.edicts.get(id)` 独立 Repository 对象——要改上百调用点，收益不抵风险。

#### 3.3.3 app.py（806 行 → bootstrap/ 包）

```
bootstrap/
├── wiring_storage.py / wiring_tools_mcp.py / wiring_persona.py
├── wiring_memory.py / wiring_llm.py / wiring_executor.py / wiring_channels.py
├── universe_hooks.py    # _update_universe_fitness 等闭包提为顶层函数（可单测）
└── digest_cron.py       # _digest_cron_loop 提为顶层
```

lifespan 瘦成 ~40 行顺序编排。**风险：装配顺序有硬依赖**（app.py:85 注释"register_skill_tools 移到 config_manager 之后"、:118"submit_edict 覆盖注册"）——先写一个「启动后 app.state 53 项全就位」的冒烟测试再动刀；抽取顺序：先无顺序依赖的纯函数（universe_hooks、digest_cron），再 wiring 分组。配合 §3.2 后循环已断，6 个 `object` setter 多数可回归构造注入（Protocol 类型标注）。

#### 3.3.4 executor/orchestrator/loop.py（仅拆函数，不拆文件）

orchestrator/ 包本身切分良好，只需把 `run()`（388 行）的循环体按既有注释阶段抽成同模块私有协程：`_check_pause / _check_budget / _run_actor_turn / _run_checks_and_critic`，run() 瘦成 ~60 行编排循环。**风险**：内部大量提前 `return await _finalize_with_supervision(...)`，抽取时用返回哨兵传递"是否提前收工"，先补 4 条集成测试（正常收工/预算超额/checks 失败/pause-resume）再动。`agent.execute()`（473 行）同法处理，优先级更低。

### 3.4 工程四：双通道 channel core 抽象

**目标**：把飞书/Telegram 中平台无关的会话逻辑收敛到一份，净删 ~700–850 行（约 telegram 子包 1/3）。**范本已在库内**：`EdictBridge`（feishu/edict_bridge.py）已参数化 channel/instance_id，telegram 直接 import 复用——照这个形态推广即可。符合 pi-mono 品味：组合 + Protocol seam，不搞深继承。

```
gateway/core/
├── message.py       # ChatMessage Protocol（chat_id/sender_open_id/text/message_id）
│                    #（事实标准已存在：TelegramMessage 特意加了 sender_open_id property 对齐飞书）
├── outbound.py      # Outbound Protocol（send_text/send_card/send_thinking/clear_thinking）
│                    # + OutboundEventBase：_lookup_chat_id + _on_execution_completed/failed（事件层 ~90% 相同）
├── branches.py      # ChatBranch 基类：assistant/edict 两分支的命令表 + 全部 _cmd_*（~92% 相同）
├── mode_router.py   # 整体上移（~95% 相同，docstring 自述"零飞书耦合"）
├── batcher.py       # InboundBatcher：0.6s 静默合并批处理核（~95% 相同）
├── approval.py      # ApprovalCommandHandler：参数化 list_pending 回调 + actor 前缀
├── facade.py        # BotFacade 基类：app_lock / reload 骨架 / legacy fallback / _reply
└── budget.py        # query_budget_data()：card_builder 里逐字节相同的 cost SQL 查询
```

平台侧只留真差异：`connection.py`（lark ws vs ptb polling，不合并）、`security.py`（不合并）、`markdown_v2.py`、传输原语、InlineKeyboard/lark 卡片渲染。

**落地顺序（每步全量测试兜底，tests/gateway/ 已有 35 个文件）**：
1. `mode_router` 试点（~75 行，风险极低）；
2. outbound 事件层 + `_lookup_chat_id` 上移（~90 行）；
3. `query_budget_data` + approval handler + batcher（~180 行）;
4. branches 合并（~400 行，消息热路径，最后做）；
5. facade 骨架（~120 行）。

### 3.5 工程五：前端信息架构重组

#### 3.5.1 御书房合并详案（回答用户点名问题）

**结论：合并，保留「御书房」。** 依据：

- 两页底层同源——都是 `listEdicts`（御书房 = `listEdicts({status:"open",limit:100})`，useApprovals.ts:19-25）；都以敕令为核心实体、都跳 `/edicts/:id`；工具栏几乎相同。
- 御书房**不是**纯审批 inbox（它列出全部 open 敕令并富化最新奏章+待批项，本质是"进行中任务的活动流"），所以"合并稀释审批聚焦"的担忧不成立；两页也均无权限门控代码。
- 隐喻自洽：皇帝在御书房总览奏章并批红，任务总览放进御书房语义顺理成章。

**交互设计**：

```
御书房（/approvals，或改 /study）
├── Tab「待处置」（默认落地）＝ 现御书房卡片流
│     · 保留奏章富化 + 待批工具优先排序
│     · 侧边栏徽标数迁到此 Tab，统一用 needs_review 口径（修 P0-5）
│     · 富化查询（N+1）只在此 Tab 触发；后端顺带加批量接口消 N+1
├── Tab「全部敕令」＝ 现敕令总览表格
│     · 保留全状态过滤 + 分页 + 删除/批量删除
└── 右上角「颁发敕令」按钮（原独立菜单项降级，菜单再减一项）
```

`EdictListPage.tsx` 与 `ApprovalQueuePage.tsx` 退役，改为 `RoyalStudyPage.tsx` 内两个视图组件。

#### 3.5.2 菜单分组（推荐方案：4 组 12 项）

```
【敕令】 御书房（合并后，含待处置/全部/颁发）· 文书房（定时任务）
【政要】 内阁 · 廷议 · 都察院（已含运维 3 Tab）· 权印司
【百官】 百官阁 · 文渊阁 · 位面
【外朝】 藏兵阁（系统管理）· 鸿胪寺 · 通政司 · 户部账房
```

15 项 → 12 项 + 4 组视觉聚合。更激进的 9 项方案（户部并入都察院 Tab、鸿胪寺+通政司并入藏兵阁、文书房收为御书房 Tab）**暂不推荐**——先做安全的分组版，跑一段时间再看是否需要二次收敛。

#### 3.5.3 其他前端动作

1. **删死代码**：删 `ProviderDashboardPage.tsx`（161 行）；把 OpsMonitorPage 的 `EventBusTab/WorkersTab/HooksTab` 抽到 `components/ops/`，删除 609 行死页外壳，AuditDashboard 改从新位置 import。
2. **拆 SystemManagementPage**（2039 行）：9 个 Tab 各抽成 `components/system/XxxTab.tsx`，主页面只留 `<Tabs>` 装配（~40 行）。
3. **修 `?tab=` 深链**（P0-4）：SystemManagement/Audit 用 `useSearchParams` 驱动 `activeKey`。
4. **`nav.war` 处置**：删除该 i18n key（作战图保持从敕令详情进入的现状即可；若想给正式入口，挂到【敕令】组）。
5. **小抽象**：Persona 两页共享的指标卡抽 `PersonaMetrics` 组件；`EdictForm` 抽 `RuntimeConfigSection`/`AcceptanceConfigSection` 两个子组件。
6. **数据视图去重**（低优先级）：`listNetworkEvents` 在鸿胪寺与都察院两处渲染、web 工具 provider 配置散落三页——统一以都察院为观测入口、鸿胪寺为配置入口。

### 3.6 工程六：工程化基线

1. **ruff 一步到位**（lint + format + isort 三合一，替代 black/isort/flake8）：

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "SIM", "ASYNC"]  # ASYNC 规则直接守护 §3.1 的成果
ignore = ["E501"]  # 先由 format 管行宽

[tool.ruff.lint.per-file-ignores]
"tests/*" = ["S101"]
```

   dev 依赖加 `ruff>=0.5`，配 `.pre-commit-config.yaml`（ruff check --fix + ruff format）。首次全量 `ruff check --fix` 单独一个 commit。

2. **mypy 渐进式**：先只对 `models/ kernel/ bus/ dag/` 等叶子模块开严格检查，storage/gateway 拆完后逐目录纳入。它能直接抓住 P0-3 这类 bug。
3. **coverage 度量**：`pytest --cov=tianshu --cov-report=term-missing`，pyproject 配 `[tool.coverage]`；先度量、后设阈值（规则宣称 80%，先看真实数字）。
4. **依赖 extras 化**：

```toml
dependencies = [ pydantic, fastapi, uvicorn, litellm, httpx, croniter, ... ]  # 纯核心
[project.optional-dependencies]
feishu   = ["lark-oapi>=1.4.0"]
telegram = ["python-telegram-bot>=22.6,<23"]
web      = ["trafilatura>=1.12"]           # 网页抽取工具
mcp      = ["mcp>=1.0"]
all      = ["tianshu[feishu,telegram,web,mcp,cli,notify]"]
```

   代码内对应通道模块入口做延迟 import + 友好报错（未装 extra 时提示 `pip install tianshu[feishu]`）。
5. **测试补盲**：`dag`（拓扑推进逻辑）、`consultation`（会诊流程）补单测；`cli` 加 smoke test（typer 的 CliRunner）；补齐 pytest markers 注册（当前 `@pytest.mark.unit` 报 UnknownMark 警告）。
6. **配置去重**（P2-4）：AgentConfigState 默认值引用 TianshuSettings 对应字段。

---

## 4. 实施路线图

> 原则：每批独立收尾、全量测试绿再进下一批；结构性改动（批次 2/3）每步一个可回滚 commit。1271 个存量测试是安全网。

### 批次 1 · 止血与基建（规模 S，先行）
1. §3.1-1 evolver 链路 to_thread（P0-2）
2. P0-3 插件注册 bug：修签名或先注释掉死路径并留 TODO
3. 前端 P0-4 深链修复 + P0-5 徽标口径统一
4. §3.6-1 ruff 落地 + 首次全量 fix（独立 commit）
5. P3 死代码清理：后端 4 模块（人工确认后删）+ 前端 ProviderDashboardPage/OpsMonitor 外壳 + nav.war
6. P3-4 吞异常补日志（storage 6 处 + hooks.py:169）
7. P3-5 小样板收敛：`title_from_goal()`（5 处）、`submit_new_edict()`（3 处）、cli `_request()`（4 处）、edict enum 常量上移 models/common
8. §3.6-6 配置默认值去重；providers/protocol.py 改名 capabilities.py

### 批次 2 · 结构重塑（规模 L，核心）
1. §3.2 kernel 契约层（先行——它让后续拆分更顺）
2. §3.3.1 api.py 按资源拆分（每簇一 commit，OpenAPI 路径对比防漂移；顺带 §3.1-2 def/async 甄别）
3. §3.3.2 storage/ Mixin 拆分（先 mappers 纯函数 → schema/migrations → 逐 Mixin；feishu/telegram repo 隔离）
4. §3.3.3 bootstrap/ 装配拆分（先冒烟测试，后抽取；setter 回归构造注入）

### 批次 3 · 复用与前端（规模 M）
1. §3.4 双通道 core 抽象（按 mode_router → outbound → budget/approval/batcher → branches → facade 顺序）
2. §3.5 前端：御书房合并 + 菜单 4 组 + SystemManagement 拆 Tab + N+1 批量接口

### 批次 4 · 收尾（规模 S–M）
1. §3.3.4 loop.run() / agent.execute() 函数级拆分（先补集成测试）
2. §3.6-2/3 mypy 渐进 + coverage 度量
3. §3.6-4 依赖 extras 化
4. §3.6-5 测试补盲（dag/consultation/cli）
5. P3-7 文档同步：表数量、事件覆盖表述、OpsMonitorPage 引用、README 架构表

---

## 5. 业界实践与参考项目对照

| 方案 | 依据的实践/项目 | 对应 |
|---|---|---|
| api.py 按资源拆 APIRouter | FastAPI 官方 bigger-applications 模式；库内已有范式（tongzheng_api.py:21） | §3.3.1 |
| storage Mixin→facade 渐进拆分 | "Branch by Abstraction" 渐进重构；避免大爆炸式改写 | §3.3.2 |
| bootstrap wiring 函数而非 DI 容器 | pi-mono「Factory 优于 DI」——项目已按此理念走，本方案只是把工厂函数从 lifespan 拍平结构里解出来 | §3.3.3 |
| kernel 中立契约层 | 依赖倒置原则（DIP）；ZeroClaw 的 trait/Protocol 中立抽象层 | §3.2 |
| gateway/core Protocol seam | ZeroClaw Protocol 思路 + OpenClaw 11 通道抽象（reference-projects.md §四早已列为"下一步潜在借鉴"）；库内 EdictBridge 已是正确形态 | §3.4 |
| def 路由回归线程池 / to_thread | FastAPI/Starlette 官方语义：sync 路由自动跑 threadpool；asyncio 官方 to_thread 指引 | §3.1 |
| ruff 单工具链 | 当前 Python 社区主流（lint+format+isort 一体），取代 black+isort+flake8 组合 | §3.6 |
| 依赖 extras 化 | Python 打包惯例（`pip install pkg[feature]`）；参考 litellm/fastapi 自身的可选依赖设计 | §3.6 |
| mypy 渐进采纳 | 大型存量库标准路径：叶子模块先行、逐目录扩围 | §3.6 |
| 御书房单页双视图 | 任务型工具的 inbox+archive 惯例（同一资源多视图，如 GitHub PR 列表的 filter tabs） | §3.5 |

## 6. 附录：明确不建议做的事（防过度工程）

| 不做 | 原因 |
|---|---|
| 合并 bus/dag/auditor/consultation 等小模块 | 职责正交：bus=进程内事件路由，notifier=外部渠道；auditor=执行后审计，consultation=决策前会诊；dag/=纯数据结构，dag_scheduler=运行时。小而聚焦是优点 |
| storage 演进为 `storage.edicts.get()` 独立 Repository 对象 | 要改 100+ 调用点，Mixin 方案已达成文件级内聚，收益不抵风险 |
| 引入 DI 容器 | 违背项目既定的 pi-mono 品味；bootstrap 工厂函数足够 |
| 引入 aiosqlite | def 路由线程池 + to_thread 已解决问题，不必为此改写 180 个方法 |
| tools 注册上装饰器 DSL | ToolDefinition 大头是内联 JSON schema，装饰器消不掉；为省 1 行牺牲显式可读性 |
| 前端 hooks/api 层抽象 | react-query 惯用法 + 统一 axios client 已是"刚刚好"，再抽反而对抗库 |
| 合并 feishu/telegram 的 connection.py/security.py | 真平台差异（lark ws vs ptb polling；加密签名 vs token），强行合并制造错误抽象 |
| 一次性上 9 项激进菜单方案 | 先跑 4 组 12 项，有真实使用反馈再二次收敛 |
| Persona 两页合并 | list↔detail 是正常模式，只需抽共享指标组件 |

---

*评审证据由 5 路并行分析产出并经主线交叉验证；关键结论（同步阻塞链路、插件 latent bug、死代码、页面同源性）均已在源码中逐一复核。*
