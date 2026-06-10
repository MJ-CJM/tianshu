# 流式输出 — StreamCallback 协议与 WS 桥接

> 契约层：Agent 只面对 `StreamCallback` 三个回调，「往哪推、推成什么形状」全在实现方吸收；前端通过 WebSocket 增量消费。

**相关设计**：[../agent/react-loop.md](../agent/react-loop.md)
**相关实现**：[../../impl/interfaces/README.md](../../impl/interfaces/README.md)

## 1. 为什么要一个回调协议

ReAct 主循环（见 react-loop.md §6）既要支持「阻塞式一次完成」也要支持「逐 token 推前端」。若把 WebSocket 直接塞进 Agent，循环就绑死了传输层、无法测试、也无法换成 SSE/控制台。于是抽出 `StreamCallback`（`Protocol`），Agent 只认协议、不认 WS：

- 收到非空 `stream_callback` → 走 `chat_stream`，按 delta 回调；否则走 `chat` 一次完成。
- 协议是 `Protocol`（结构化子类型），实现方无需继承，fake/控制台/WS 都能注入。
- 回调签名全 `async`，与 Agent 的异步循环同栈，无需额外线程。

## 2. StreamCallback 协议规格

定义在 `executor/streaming.py` 的 `StreamCallback`，三个异步方法：

| 方法 | 签名 | 语义 | 触发点（`executor/agent.py`） |
|---|---|---|---|
| `on_delta` | `(text: str) -> None` | 一段文本增量（非完整消息） | `chat_stream` 每个 chunk，**仅当** `chunk.content` 非空且 `not chunk.tool_calls` 时回调 |
| `on_tool_call_start` | `(name: str) -> None` | 某工具开始执行 | `ToolRegistry.execute` 之前 |
| `on_tool_call_end` | `(name: str, result: ToolResult) -> None` | 该工具执行完成 | `ToolRegistry.execute` 之后 |

语义约束：

- **delta 是增量不是全量**：消费方负责拼接（前端按 `edict_id` 累加），Agent 不重发已发文本。
- **tool_call 期间不发 delta**：带 `tool_calls` 的 chunk 被跳过，避免把工具参数 JSON 当正文推给用户。
- **末 chunk 才有完整 usage**：`chat_stream` 的最后一个 chunk 携带准确 usage（见 client.md §1），Agent 用它记账；回调本身不传 usage。
- **回调异常不应中断循环**：传输层抖动不该让任务失败，桥接实现需自行吞错（见 §4 死连接清理）。

`CancellationToken`（同文件）是配套的线程安全取消信号（`asyncio.Event`）：流式循环每个 chunk 前检查 `is_cancelled`，命中即以 `ExitReason.CANCELLED` 收尾，供外部中断长流式输出。

## 3. WebSocketStreamCallback — 唯一生产实现

`notifier/notifier.py` 的 `WebSocketStreamCallback` 是协议的生产实现，持有 `Notifier` 与一个 `edict_id`，把三个回调翻译成三类 WS 消息后 `notifier.broadcast_ws(...)`：

| 回调 | 广播 type | 附带字段 |
|---|---|---|
| `on_delta(text)` | `stream.delta` | `edict_id`、`text` |
| `on_tool_call_start(name)` | `stream.tool_start` | `edict_id`、`tool_name` |
| `on_tool_call_end(name, result)` | `stream.tool_end` | `edict_id`、`tool_name`、`is_error`(取 `result.is_error`) |

设计立场：

- **一个 edict 一个 callback 实例**：`edict_id` 在构造时绑定，回调本身不带 id，省去每次传参，也让多任务并发推送天然隔离。
- **stream 不去抖**：与 `audit.completed` 的 0.5s 去抖（`DEBOUNCE_SECONDS`）不同，delta 必须实时逐条推，否则前端看不到「打字机」效果。`broadcast_ws` 直接群发，无队列。
- **结果只摘 `is_error`**：工具完整结果不进 WS（可能很大、含敏感数据），前端只需知道「成功/失败」着色，详情走审计接口。

## 4. WS 连接与广播（gateway + notifier）

```text
前端 new WebSocket(/api/ws)
  → gateway/api.py websocket_endpoint: accept() + notifier.register_ws(ws)
  → while True: receive_text()   # 服务端只读不解析，纯保活
  → WebSocketDisconnect: notifier.unregister_ws(ws)

Agent 回调 → WebSocketStreamCallback → notifier.broadcast_ws(message)
  → 遍历 _ws_clients 逐个 send_text(json.dumps(message, default=str))
  → send 抛异常的连接收集进 dead，群发后从 _ws_clients.discard 清理（死连接自愈）
```

`/api/ws` 是**单一全局广播通道**：所有事件（`stream.*`、`tool.approval_required`、`audit.completed`、`execution.failed`、outer loop 事件…）都从这一条连接下来，前端按 `type` + `edict_id` 自行过滤。没有 per-edict 房间/订阅机制——简化优先于精确投递。

## 5. WS 事件 JSON schema

所有 WS 消息共享一个松散信封（前端 `WsMessage`：`{ type: string; edict_id?; memorial_id?; status?; [key]: unknown }`），`type` 是唯一判别字段。流式三类的真实形状：

```jsonc
// 文本增量（打字机）
{ "type": "stream.delta",      "edict_id": "ed_abc", "text": "正在分析" }

// 工具开始
{ "type": "stream.tool_start", "edict_id": "ed_abc", "tool_name": "shell_exec" }

// 工具结束
{ "type": "stream.tool_end",   "edict_id": "ed_abc", "tool_name": "shell_exec", "is_error": false }
```

对照其它通道事件（同一连接，形状各异）：

```jsonc
{ "type": "audit.completed",        "edict_id": "ed_abc", /* ...render_status(memorial) 展开 */ }
{ "type": "execution.failed",       "edict_id": "ed_abc", /* ...render_status(memorial) */ }
{ "type": "tool.approval_required", "edict_id": "ed_abc", /* 审批 payload */ }
// outer loop 事件：原样透传 event_type，带 memorial_id + 完整 payload 对象
{ "type": "<event_type>", "edict_id": "ed_abc", "memorial_id": "m_1", "payload": { /* ... */ } }
```

## 6. 客户端消费契约

前端 `useWebSocket`（`web/src/hooks/useWebSocket.ts`）的关键设计：

- **`subscribe(listener)` 同步派发**：`ws.onmessage` 解析 JSON 后立即同步遍历 listener 集合，**不经 React state**。原因：相邻两条 WS 消息间隔极短时，`setState` 单值会被 React 18 自动批处理合并/覆盖，导致中间消息丢失。`stream.delta` 高频逐条到达，正是这种场景。
- **`lastMessage` 仅作兜底**：仍会 `setLastMessage`，供不在乎丢消息的低频订阅者用。
- **断线指数退避重连**：`onclose` 后 1s/2s/4s…最多 30s 重连；连接 URL 由 `window.location` 推导 `ws/wss`，路径固定 `/api/ws`。
- **listener 隔离**：单个 listener 抛错被 try/catch 吞掉，不影响其它 listener。

消费 delta 的典型形态（伪代码）：

```text
buffer = {}                       # edict_id → 累积文本
subscribe(msg => {
  if (msg.type === "stream.delta")
      buffer[msg.edict_id] = (buffer[msg.edict_id] ?? "") + msg.text   # 增量拼接
  else if (msg.type === "stream.tool_start")
      markRunning(msg.edict_id, msg.tool_name)
  else if (msg.type === "stream.tool_end")
      markDone(msg.edict_id, msg.tool_name, msg.is_error)              # is_error → 着色
})
```

要点：delta 是增量，消费方负责按 `edict_id` 拼接；工具事件只驱动 UI 状态/着色，不携带结果正文。
