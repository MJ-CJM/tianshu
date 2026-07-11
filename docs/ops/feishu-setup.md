# 飞书机器人接入指南

> 本指南帮助你把 tianshu 接入飞书（或 lark），实现"在 IM 里给宫殿下敕令"。

## 1. 飞书侧准备

1. 登录 [开发者后台](https://open.feishu.cn/app)（lark 用户访问 `https://open.larksuite.com/app`），创建一个**自建应用**。
2. 在「凭证与基础信息」记录：
   - `App ID`
   - `App Secret`
3. 「权限管理」勾选并发布版本：
   - `im:message`（接收消息）
   - `im:message:send_as_bot`（以机器人身份发送）
   - `im:resource`（图片、文件）
4. 「事件与回调」选择**接收方式**：
   - **WebSocket（推荐）**：开发机/单租户场景，无需公网，开箱即用。
   - **Webhook**：需要公网 HTTPS，且你需要在「事件订阅」处填写 `https://<your-domain>/channels/feishu/webhook`，并在飞书后台填写 `Encrypt Key` 或 `Verification Token`（建议两者都配）。
5. 在「事件订阅」勾选监听事件：
   - `im.message.receive_v1`（接收消息）
6. 在「机器人」开关里启用机器人，然后点「版本管理」发布。
7. 邀请机器人到一个测试单聊或群组里。在 PC 端长按头像，能看到 `open_id`，记下它（用于 allowlist）。

## 2. tianshu 侧配置

把以下环境变量加到你启动 tianshu 的 shell 或 `.env`：

```bash
# 必填三件套
export TIANSHU_FEISHU_APP_ID="cli_a..."
export TIANSHU_FEISHU_APP_SECRET="..."
export TIANSHU_FEISHU_ALLOWED_USERS="ou_xxx,ou_yyy"  # 逗号分隔的 open_id

# 可选
export TIANSHU_FEISHU_DOMAIN="feishu"           # 或 "lark"（海外版）
export TIANSHU_FEISHU_CONNECTION_MODE="websocket"  # websocket | webhook
export TIANSHU_FEISHU_HOME_CHANNEL=""           # cron 结果 / 无源裁决兜底 chat_id
export TIANSHU_FEISHU_BOT_OPEN_ID=""            # 群 @ 检测（推荐填）
export TIANSHU_FEISHU_BOT_NAME=""               # 群 @ 检测兜底

# Webhook 模式专属
export TIANSHU_FEISHU_ENCRYPT_KEY=""            # 飞书后台「事件订阅 - Encrypt Key」
export TIANSHU_FEISHU_VERIFICATION_TOKEN=""     # 飞书后台「事件订阅 - Verification Token」
export TIANSHU_FEISHU_WEBHOOK_PATH="/channels/feishu/webhook"  # 默认即可

# 调优
export TIANSHU_FEISHU_TEXT_BATCH_DELAY="0.6"    # 文本批处理静默期（秒）
export TIANSHU_FEISHU_DEDUP_CACHE_SIZE="2048"   # 去重缓存上限
export TIANSHU_FEISHU_WS_RECONNECT_INTERVAL="120"
```

> 仅设置 `TIANSHU_FEISHU_APP_ID` 即可启用机器人；若该字段为空，机器人完全不启动（向后兼容）。

启动后日志里应能看到：

```
[feishu] starting (mode=websocket, app=cli_a...)
[feishu/ws] started (app=..., domain=feishu)
```

## 3. 验证

1. 在与机器人的单聊里发送 `/help`，应收到命令列表。
2. 直接发一句 "帮我看一下今天的进度"——机器人应回 `✅ 已收到（敕令 #xxxxxxxx）`。
3. 在 web 端打开敕令详情，能看到 `metadata.chat_id` 已记录飞书 chat。

## 4. 命令速查

| 命令 | 用途 |
|------|------|
| `/new <目标>` | 显式新建一个敕令（覆盖会话锚） |
| `/status [敕令id]` | 查看当前会话锚 / 指定敕令的状态 |
| `/cancel [敕令id]` | 取消一个敕令 |
| `/set-home` | 显示当前 chat_id（用于配置 `TIANSHU_FEISHU_HOME_CHANNEL`） |
| `/help` | 显示帮助 |

无命令前缀的纯文本：默认续接当前会话锚定的敕令。

## 5. 工具裁决命令

**v0.4.2 公开支持路径：命令回复。** 当当前 chat 有待裁决工具调用时，使用以下文本命令提交裁决：

| 命令 | 中文命令 | 含义 |
|---|---|---|
| `/approve` | `/准` | 单次允许 |
| `/approve edict` | `/准敕` | 本敕令允许 |
| `/approve always` | `/准永` | 请求总是允许；高风险工具可能按安全策略降级为单次 |
| `/reject` | `/驳` | 拒绝 |

同一 chat 有多个待裁决项时，在命令后附至少 6 位的奏折 ID 前缀，例如 `/approve mem_a123` 或 `/驳 mem_a123`。当前版本不把飞书交互按钮或与 Web 的同步状态作为公开保证，请以机器人文本回复中的实际生效范围为准。

## 6. 常见问题

**Q：机器人收不到消息？**
- 确认机器人已加到该聊天 / 群。
- 群聊里**必须 @ 机器人**（设置 `BOT_OPEN_ID` / `BOT_NAME` 让 dispatcher 能识别）。
- 检查 `TIANSHU_FEISHU_ALLOWED_USERS` 是否包含你的 open_id。
- 看启动日志是否有 `rejected non-allowlist sender=` 的提示。

**Q：WebSocket 模式断了怎么办？**
- SDK 内置 `auto_reconnect=True`，断线会自动重连。
- 长时间无事件（>10min）会有 watchdog warning，可关注日志。
- 重启进程也行——进程锁会自动清理。

**Q：Webhook 模式签名校验失败？**
- 确认 `TIANSHU_FEISHU_ENCRYPT_KEY` 与飞书后台一致。
- 飞书后台「事件订阅 - 请求地址」点"测试"按钮，应返回你设的 challenge。
- `Encrypt Key` 留空时跳过签名校验（仅供 dev）。

**Q：裁决命令提示当前 chat 没有待裁决项？**
- 确认触发工具调用的敕令带有当前飞书会话的 `metadata.chat_id`。
- cron 或调试触发的敕令没有来源 chat 时，需要配置 `TIANSHU_FEISHU_HOME_CHANNEL` 作为兜底目标；若也未配置，请改用 Web 裁决入口。
- 同一 chat 有多个待裁决项时，按第 5 节附上奏折 ID 前缀。

**Q：双开了怎么办？**
- 同一 `App ID` 只允许一个 tianshu 进程持锁（`~/.tianshu/feishu_app_lock.<app_id>`）。
- 第二个进程启动时会检测前一个 PID 是否存活，若存活则报错；若是残留锁则自动清理。

## 7. 退出/卸载

只需把 `TIANSHU_FEISHU_APP_ID` 设空（或删除该环境变量）后重启服务，机器人即不再启动。会话锚、待处理裁决记录与去重缓存留在 SQLite 中，但不会再被访问。
