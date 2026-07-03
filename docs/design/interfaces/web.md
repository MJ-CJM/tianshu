# Web 前端

> `web/` 是 React + Ant Design 的单页应用，经 `app.py` 静态挂载。所有数据走 `/api`（同源），与 CLI 同一套后端契约。本篇讲页面结构与路由，对照 `web/src/App.tsx` 与 `web/src/pages/` 核实。

## 1. 技术栈与骨架

| 项 | 选型 |
|---|---|
| 框架 | React 18 + react-router-dom（`BrowserRouter`） |
| UI | Ant Design（`ConfigProvider` + 主题切换 + zh/en locale） |
| 数据 | `@tanstack/react-query`（staleTime 5s，window focus 不重拉） |
| HTTP | axios 实例 `api/client.ts`（baseURL `/api`，统一拦截 `ApiResponse.error` 弹 notification） |
| 布局 | `components/layout/AppLayout`（Header + Sidebar + Outlet） |
| 国际化 | `i18n/locales/`：`zh-modern` / `zh-classic`（古风彩蛋）/ `en` |

## 2. 路由表（`App.tsx`）

所有页面在 `AppLayout` 下；DAG 战图懒加载（`@xyflow/react` 较重）。

| 路由 | 页面组件 | 侧栏标签 | 用途 |
|---|---|---|---|
| `/`、`/approvals` | `RoyalStudyPage` | 审批中心 | 御书房合并页，双 Tab：待处置（含工具/plan 审批，带待办计数徽标）/ 全部（Edict 列表）；原 `EdictListPage`/`ApprovalQueuePage` 已退役，两条路径共享同一页面避免书签失效 |
| `/edicts/create` | `EdictCreatePage` | 新建任务 | 下旨 |
| `/edicts/:edictId` | `EdictDetailPage` | — | 任务详情 + 实时事件流 |
| `/scheduler` | `SchedulerPage` | 调度器 | 定时/周期 job |
| `/audit` | `AuditDashboardPage` | 审计中心 | 审计统计、网络事件 |
| `/cost` | `CostDashboardPage` | 财务中心 | 成本汇总/预算 |
| `/memory` | `MemoryDashboardPage` | 知识库 | 记忆条目、记忆宫殿 |
| `/consultation` | `ConsultationPage` | 群议 | 会诊 |
| `/cabinet` | `CabinetPage` | 规划中心 | 内阁规划 |
| `/hongluisi` | `HongluisiPage` | 外部接入 | 鸿胪寺网络能力 |
| `/tongzheng` | `TongzhengPage` | 通知中心 | 飞书/IM 通政司运行配置 |
| `/personas` | `PersonaDashboardPage` | 角色管理 | 六部人格总览 |
| `/personas/:personaId` | `PersonaDetailPage` | — | 单个人格详情/画像 |
| `/system` | `SystemManagementPage` | 系统管理 | provider/config/工具/MCP/prompt 等聚合 |
| `/session-rules` | `SessionRulesPage` | 会话规则 | 策略会话规则 |
| `/universes` | `UniversePage` | 位面 | 平行位面管理（分支/切换/对比/演化/代码变体） |
| `/dag/:dagId` | `DagBattleMapPage`（lazy） | — | DAG 战图可视化 |

侧栏（`AppSidebar.tsx`）按 4 组呈现，非扁平列表：**敕令**（御书房/调度器）、**治理**（内阁/群议/审计中心/会话规则）、**成长**（角色管理/知识库/位面）、**系统**（系统管理/外部接入/通知中心/财务中心）；`EdictCreatePage`/`EdictDetailPage`/`PersonaDetailPage`/`DagBattleMapPage` 不占侧栏条目，靠页内导航到达。

## 3. API 层（`web/src/api/`）

每个后端路由族对应一个 ts 模块（薄封装 axios + 类型）：`edicts` / `memorials` / `audit` / `cost` / `memory` / `personas` / `personaTemplates` / `profile` / `providers` / `config` / `system` / `mcp` / `policy` / `scheduler` / `dag` / `consultations` / `departments` / `decrees` / `credentials` / `hongluisi` / `tongzheng` / `network_events` / `ops` / `universe` / `health`。`types.ts` 放共享类型，`client.ts` 放实例与拦截。

## 4. 实时与状态同步

| hook | 作用 |
|---|---|
| `useWebSocket` | 连 `/api/ws`，接收 Notifier 推送的事件（audit/execution/outer_loop/stream.*） |
| `useWsQueryInvalidation` | WS 事件触发 react-query 缓存失效，自动重拉相关数据 |
| `useWsPolicyToasts` | 策略相关 WS 事件弹 toast |

设计判断：列表/详情靠 react-query 缓存 + WS 失效驱动刷新，而非轮询；EdictDetailPage 的执行进展由 WS `stream.*` / `outer_loop.*` 实时渲染。

## 5. 边界

- 前端不持久化业务状态，真相源是后端 API；刷新即重拉。
- 错误展示统一在 axios 拦截器（`ApiResponse.success===false` 或 HTTP 错误弹 notification），页面可用 `silentCodes` 静默特定状态码。

**相关实现**：[../../impl/interfaces/](../../impl/interfaces/)
