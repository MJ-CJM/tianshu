# WebSocket 房间模型细化设计（#5）

> 状态：**设计提案，待审批实施**。来自 [multica-inspired-control-plane.md](./2026-07-02-multica-inspired-control-plane.md) 的 #5。
> 日期：2026-07-02 · 归属：Phase 3

## 背景与目标

Multica 的 WS 层按 workspace 分房间广播 + `SendToUser` 定向 + server ping/client pong 保活 + 客户端"关键数据 patch / 次要数据失效重拉"分级。天枢当前是**单一广播**：所有事件推给所有连接。

**目标**：减少无关推送、明确前端更新策略、加连接保活。**非目标**：不做多租户安全隔离（天枢单租户，房间是性能优化不是权限边界）。

## 现状（证据）

| 方面 | 现状 | 位置 |
|---|---|---|
| 连接管理 | `_ws_clients: set[WebSocket]`，广播给**所有**连接 | `notifier/notifier.py:32,41` |
| 端点 | `/ws`：accept → register_ws → 收消息即丢弃（仅保连接） | `gateway/api.py:872` |
| 事件命名 | **已规范** `domain.action`（edict./execution./plan./audit./dag./skill./universe./cost.） | 全局 |
| 去抖 | 同 memorial `DEBOUNCE_SECONDS=0.5` 合并；urgent/outer-loop 实时 | `notifier.py:151` |
| 前端 | `useWebSocket` hook：`subscribe(listener)` + `lastMessage`，onmessage 同步分发 | `web/src/hooks/useWebSocket.ts` |
| 保活 | 无 server ping；靠 `WebSocketDisconnect` 被动感知 | `gateway/api.py:879` |

**关键判断**：
- 事件命名**已经是** `domain.action`（天枢用点分，Multica 用冒号）——**无需迁移**，这点天枢现状已好，不照搬。
- 每条 broadcast message **已带 `edict_id`** → 天然的房间键，房间过滤几乎零建模成本。
- 已有 debounce（比 Multica 更精细）→ 保留。

真正的缺口只有三个：**① 无房间隔离（全广播）② 无订阅过滤 ③ 无保活**。

## 设计

### 1. 房间 = edict_id + 全局房

- **edict 房间**：前端进入某 edict 详情页时订阅该 `edict_id`，只收该 edict 的事件（execution./audit./outer_loop. 等）。
- **全局房**：列表页/收件箱订阅"全局"，收 lifecycle 类事件（`edict.submitted`/`edict.closed`/`universe.*` 等无关单个 edict 详情的）。

订阅通过 WS 上行消息声明（当前上行消息被丢弃，正好利用）：

```jsonc
// client → server
{ "action": "subscribe", "rooms": ["edict:<id>", "global"] }
{ "action": "unsubscribe", "rooms": ["edict:<id>"] }
```

### 2. Notifier 改造（注入点明确）

```python
# _ws_clients: set[WebSocket]  →  dict[WebSocket, set[str]]（ws → 订阅的房间集）
def register_ws(ws): self._ws_clients[ws] = {"global"}   # 默认进全局房
def set_rooms(ws, rooms): self._ws_clients[ws] = set(rooms)

async def broadcast_ws(self, message):
    room = f"edict:{message['edict_id']}" if message.get("edict_id") else "global"
    for ws, rooms in list(self._ws_clients.items()):
        if room in rooms or "global" in rooms and _is_lifecycle(message):
            await ws.send_text(...)
```

- 房间键从 `message["edict_id"]` 推导——**现有 message 已带**，零额外建模。
- 未订阅任何 edict 房的旧客户端默认在 `global`，行为可配置为"收全部"以保持向后兼容（灰度期）。

### 3. 保活（server ping / client pong）

`/ws` 端点加心跳循环：server 每 ~30s 发 `{"type":"ping"}`，client 回 `{"action":"pong"}`；超时未 pong 则关连接回收。替代当前"仅靠 disconnect 被动感知"，及时清理死连接（配合 #1 的可靠性主题一致）。

### 4. 前端 patch vs 失效重拉分级

`useWebSocket` 已有 `subscribe`/`lastMessage`。明确约定：
- **patch 缓存**（高频、增量）：`execution.*`/`outer_loop.*`/`audit.completed` → 直接更新对应 edict/memorial 的本地 store，不重拉。
- **失效重拉**（低频、结构性）：`edict.submitted`/`edict.closed`/`plan.completed` → invalidate 列表 query 触发重拉。
- 进入 edict 详情页 `subscribe(["edict:<id>"])`，离开 `unsubscribe`。

## 落点

| 文件 | 变更 |
|---|---|
| `notifier/notifier.py` | `_ws_clients` set→dict；`broadcast_ws` 房间过滤；`set_rooms` |
| `gateway/api.py` `/ws` | 解析上行 subscribe/unsubscribe/pong；心跳循环 |
| `web/src/hooks/useWebSocket.ts` | subscribe/unsubscribe 房间 API；pong 应答 |
| `web/src/`（详情页/列表页） | 进出房间 + patch/重拉分级 |

**无 DB 变更**——纯连接层与前端。

## 分步交付

1. **A**：Notifier 房间过滤 + `/ws` 解析 subscribe（后端先支持，前端默认 global 不变行为）。
2. **B**：前端详情页进出房间订阅 + patch/重拉分级。
3. **C**：server ping/client pong 保活 + 死连接回收。

## 风险与权衡

- **房间非权限边界**：天枢单租户，房间只为减少无关推送与前端渲染压力；不得依赖它做数据隔离。
- **向后兼容**：旧前端不发 subscribe → 落入 `global` 收全部，行为不退化；灰度期后再收紧。
- **命名不迁移**：天枢 `domain.action` 已规范，明确**不**改成冒号式，避免无谓的破坏性变更。
