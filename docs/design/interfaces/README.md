# 接口层（interfaces）设计总览

> 接口层是天枢对外的「触点面」：用户从哪些入口下达诏令、查看进展、做审批、收通知。它把同一套领域链路（Edict→Memorial→事件）暴露成四种形态——HTTP/WS、IM 渠道、Web、CLI。

## 1. 职责定位

| 项 | 说明 |
|---|---|
| 解决什么 | 把异步执行平台包装成多种可用入口，且各入口共享同一真相源（SQLite）与事件链 |
| 不做什么 | 不承载业务编排（排期/规划/执行/审计在 scheduler/planner/executor/auditor）；接口层只做「翻译 + 透传 + 实时推送」 |
| 与运维边界 | 运维侧（部署/凭证/进程）见 `docs/ops/` 与 `docs/design/v2_codex/06-interfaces-ops.md`；本目录聚焦「接口面」本身 |

## 2. 核心设计判断

| 判断 | 取舍 |
|---|---|
| 单 FastAPI 进程 | 所有 HTTP/WS、IM bot、静态 Web 同进程托管；`gateway_router` 挂 `/api` 前缀 |
| 异步而非同步 | `POST /api/edicts` 落库后 `EventBus.fire` 立即返回 202，不等执行完；进展靠 WebSocket / 轮询拿 |
| 统一响应封套 | 写操作多用 `ApiResponse{success, data, error}`；列表带 `metadata` 分页 |
| IM 平台无关核心 | 飞书/Telegram 复用 `EdictBridge` / `PersonaRenderer` / 审批解析等平台无关件，只重写平台连接层 |
| CLI 是薄客户端 | CLI 不直连 DB，全部走 HTTP API（`TIANSHU_API_URL`），与 Web 同源 |
| 多 bot 实例 | 每个 IM 渠道可跑 N 个 bot 实例，从 DB/旧配置/env 三级来源构建 |

## 3. 四种接口形态

| 形态 | 入口 | 主要文档 |
|---|---|---|
| HTTP / WebSocket | `gateway/api.py`（`/api` 前缀，`/api/ws` 实时流） | [gateway.md](./gateway.md) |
| IM 渠道 | `notifier/`（出站通道）+ `gateway/feishu/`、`gateway/telegram/`（双向 bot） | [channels.md](./channels.md) |
| Web 前端 | `web/`（React + Ant Design + react-router） | [web.md](./web.md) |
| CLI | `cli/`（typer，HTTP 薄客户端） | [cli.md](./cli.md) |

## 4. 与相邻子系统的关系

| 相邻子系统 | 关系 |
|---|---|
| executor / scheduler / planner | 接口层写 Edict/Memorial + `fire` 事件，下游链路异步消费 |
| notifier | 既是「出站渠道注册表」（webhook/邮件/IM channel），又是 WebSocket 广播中枢；接 `audit.completed`/`execution.failed`/`outer_loop.*` 事件 |
| approvals / policy | 审批队列、工具审批、plan 审批的 HTTP 端点与 IM 卡片按钮共享 `ApprovalManager` |
| storage | 所有接口的真相源；CLI/Web 都不旁路直连，统一经 API |

## 5. 本目录子文档索引

| 文档 | 内容 |
|---|---|
| [gateway.md](./gateway.md) | FastAPI HTTP/WS：Edict/Memorial CRUD、follow-up、审批队列、plan 审批、WebSocket 实时事件流、主要路由清单 |
| [streaming.md](./streaming.md) | 流式输出：StreamCallback 三回调协议、Agent 与推送实现解耦、WebSocket 增量桥接 |
| [channels.md](./channels.md) | Notifier 与 ChannelRegistry、飞书（app bot / 助手模式）、Telegram、邮件、WebSocket 通道、多 bot 实例 |
| [lark-feishu.md](./lark-feishu.md) | 飞书/Lark 接入：下旨/续接/审批/结果回推、与 Telegram 共享平台无关核心 |
| [web.md](./web.md) | React + Ant Design 前端页面结构与路由、api 层、实时 hook |
| [cli.md](./cli.md) | tianshu CLI 命令族（typer + HTTP 客户端） |

**相关实现**：[../../impl/interfaces/](../../impl/interfaces/)
