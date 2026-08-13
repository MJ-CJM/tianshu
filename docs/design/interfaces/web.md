# Web 前端

> `web/` 是 React + Ant Design 的单页应用，经 `app.py` 静态挂载。所有数据走 `/api`
>（同源），与 CLI 同一套后端契约。本篇讲当前页面结构与路由，对照
> `web/src/router/AppRoutes.tsx`、`web/src/navigation/departments.tsx` 与
> `web/src/pages/` 核实。

## 1. 技术栈与骨架

| 项 | 选型 |
|---|---|
| 框架 | React 18 + react-router-dom（`BrowserRouter`） |
| UI | Ant Design（`ConfigProvider` + 主题切换 + zh/en locale） |
| 数据 | `@tanstack/react-query`（全局 staleTime 5s；中枢另有前台 5s `refetchInterval`；window focus 不额外重拉） |
| HTTP | axios 实例 `api/client.ts`（baseURL `/api`，统一拦截 `ApiResponse.error` 弹 notification） |
| 布局 | `components/layout/AppLayout`（Header + Sidebar + Outlet） |
| 国际化 | `i18n/locales/`：`zh-modern` / `zh-classic`（古风彩蛋）/ `en` |

## 2. 默认导航：六个用户目的地

侧栏使用两级、单组展开结构；当前路由所属分组自动展开，收起后再展开仍恢复当前分组：

| 一级入口 | 可见子项 |
|---|---|
| 中枢 | `/control` |
| 御书房 | 全部敕令 `/approvals`、颁发敕令 `/edicts/create`、钦天监 `/scheduler`、都察院 `/audit` |
| 朝堂 | 吏部 `/personas`、廷议 `/consultation`、内阁 `/cabinet` |
| 百司 | 翰林院 `/memory`、鸿胪寺 `/hongluisi`、通政司 `/tongzheng` |
| 天工院〔实验〕 | 演化司〔实验〕 `/evolution`、诸界台〔实验〕 `/universes`、考功司〔试行〕 `/evals`、客卿馆〔实验〕 `/keqing` |
| 内府 | 藏兵阁 `/system`、权印司 `/session-rules`、户部账房 `/cost` |

旧 `/edicts` 地址只作兼容跳转，详情和 DAG 路由在侧栏中归入“全部敕令”。实验能力可从
天工院发现，但路由可达不等于发布承诺；其中 Keqing 只支持自管 CLI 凭证，凭证网关未
接入生产 executor。

## 3. 路由表（`AppRoutes.tsx`）

所有页面在 `AppLayout` 下；DAG 战图懒加载（`@xyflow/react` 较重）。

| 路由 | 页面组件 | 侧栏标签 | 用途 |
|---|---|---|---|
| `/` | `OnboardingEntryRoute` | — | 查询当前 onboarding 状态，转到 `/onboarding` 或 `/control` |
| `/onboarding` | `OnboardingPage` | — | 首次启动引导；首个任务创建后进入真实任务详情 |
| `/control` | `ControlCenterPage` | 中枢 | 四项治理指标、独特能力、进行中运行、待裁决与近期证据 |
| `/edicts` | `TaskListPage` | — | 兼容跳转到 `/approvals` |
| `/approvals` | `RoyalStudyPage` | 全部敕令 | 当前主体可见且未归档的全部任务、类型与执行进度 |
| `/edicts/create` | `EdictCreatePage` | 颁发敕令 | 下旨 |
| `/edicts/:edictId` | `EdictDetailPage` | — | 任务详情 + 实时事件流 |
| `/scheduler` | `SchedulerPage` | 钦天监 | 定时/周期 job |
| `/audit` | `AuditDashboardPage` | 都察院 | 审计统计、网络事件 |
| `/cost` | `CostDashboardPage` | 户部账房 | 成本汇总/预算 |
| `/memory` | `MemoryDashboardPage` | 翰林院 | 记忆条目、记忆宫殿 |
| `/consultation` | `ConsultationPage` | 廷议 | 发起廷议 + 历史列表 |
| `/consultation/:consultationId` | `ConsultationDetailPage` | 廷议详情 | 单场廷议：各官员意见、综合、决策（含汇聚者署名） |
| `/cabinet` | `CabinetPage` | 内阁 | 内阁规划 |
| `/hongluisi` | `HongluisiPage` | 鸿胪寺 | 外部网络能力 |
| `/tongzheng` | `TongzhengPage` | 通政司 | 飞书/IM 运行配置 |
| `/personas` | `PersonaDashboardPage` | 吏部 | 百官总览 |
| `/personas/:personaId` | `PersonaDetailPage` | — | 单个人格详情/画像 |
| `/system` | `SystemManagementPage` | 藏兵阁 | provider/config/工具/MCP/prompt 等聚合 |
| `/session-rules` | `SessionRulesPage` | 权印司 | 策略会话规则 |
| `/evolution` | `EvolutionCenterPage` | 演化司〔实验〕 | 演进中心 |
| `/universes` | `UniversePage` | 诸界台〔实验〕 | 平行位面管理 |
| `/evals` | `EvalsPage` | 考功司〔试行〕 | 评估记录 |
| `/keqing` | `KeqingManagementPage` | 客卿馆〔实验〕 | 外部 CLI 体检与自管配置 |
| `/dag/:dagId` | `DagBattleMapPage`（lazy） | — | DAG 战图可视化 |
| `*` | `NotFoundPage` | — | 三语真实 404，不再空白或误回首页 |

## 4. API 层（`web/src/api/`）

每个后端路由族对应一个 ts 模块（薄封装 axios + 类型）：`control` / `edicts` / `memorials` / `audit` / `cost` / `memory` / `personas` / `personaTemplates` / `profile` / `providers` / `config` / `system` / `mcp` / `policy` / `scheduler` / `dag` / `consultations` / `departments` / `decrees` / `credentials` / `hongluisi` / `tongzheng` / `network_events` / `ops` / `universe` / `health`。`types.ts` 放共享类型，`client.ts` 放实例与拦截。

`control.ts` 的 `ControlCenterSnapshotV1` 在一个响应中返回
`active_run_total`、`unarchived_edict_total`、`awaiting_follow_up_total`、
`cancelled_edict_total`、`pending_decision_total` 和 `evidence_total`。四张主指标卡分别
使用当前执行中、未归档敕令、待裁决总数和累计证据束（含归档）；两个未归档分项只
用于解释任务工作台构成。`active_run_total` 与 `unarchived_edict_total` 是不同领域事实，
不能相互替代。

## 5. 实时与状态同步

| hook | 作用 |
|---|---|
| `useWebSocket` | 连 `/api/ws`，接收 Notifier 推送的事件（audit/execution/outer_loop/stream.*） |
| `useWsQueryInvalidation` | WS 事件触发 react-query 缓存失效，自动重拉相关数据 |
| `useWsPolicyToasts` | 策略相关 WS 事件弹 toast |
| `useControlCenter` | 拉取同一授权范围的 `/api/control` 快照；前台每 5 秒兜底轮询，相关 WS 事件使查询失效后立即重拉 |

设计判断：列表/详情优先靠 react-query 缓存 + WS 失效驱动刷新；中枢额外保留前台 5 秒
`refetchInterval`，避免浏览器错过事件后长期展示旧汇总；`refetchIntervalInBackground`
保持关闭，隐藏标签页不轮询。全局 `staleTime: 5s` 只描述缓存
新鲜度，不代表自动轮询。DAG 轮询在终态停止，避免完成后继续请求。页面查询失败使用可
重试的 `PageQueryError`，路由 chunk/渲染失败由 Route Error Boundary 区分；未知路径使用
真正的 404 页面。

## 6. 边界

- 前端不持久化业务状态，真相源是后端 API；刷新即重拉。
- `/api/control` 由后端为整个快照统一应用授权范围：普通主体按本人过滤，`admin` scope
  不加 submitter 过滤而读取全局；前端不得把本人任务数与全局运行、裁决或证据数拼在一起。
- `evidence_total` 是含已归档任务的累计证据束数量，不等于 `recent_evidence` 摘要列表的
  长度；任务归档不会回减该累计数。
- 错误展示统一在 axios 拦截器（`ApiResponse.success===false` 或 HTTP 错误弹 notification），页面可用 `silentCodes` 静默特定状态码。
- 新建任务默认展示常用字段，验收、预算、权限、网络等高级项收起；用户选择深度任务时，
  周期选项会被关闭并解释当前 single-node 边界。
- 三种语言、键盘操作与 200% 缩放属于默认路径要求；实验路由不因此自动升级为稳定能力。

**相关实现**：[../../impl/interfaces/](../../impl/interfaces/)
