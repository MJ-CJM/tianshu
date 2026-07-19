# Telegram 机器人接入配置

> 与飞书接入并列。Telegram 通道复用同一套敕令 / 裁决 / persona 内核，只是平台层换成
> Telegram Bot API（`python-telegram-bot`）。两通道可同时启用，互不干扰（按 `metadata.channel` 路由隔离）。

## 1. 创建机器人，拿到 Bot Token

1. Telegram 里找 **@BotFather** → `/newbot` → 起名 → 拿到形如 `123456789:ABCdef...` 的 **Bot Token**。
2. （可选）`/setprivacy` → **Disable**：若希望机器人在群里能读到所有消息而非仅命令/被 @ 的消息。
   - 默认天枢群聊只响应 **@bot** 或 **回复 bot** 的消息（私聊全部响应），无需关 privacy 也能用。

## 2. 拿到你的 user_id / chat_id

- **你的 user_id**（用于 allowlist）：给 **@userinfobot** 发任意消息，它回你的数字 id。
- **home_channel chat_id**（cron 结果 / 无来源裁决通知兜底的投递目标）：
  - 私聊：就是你的 user_id；
  - 群：把机器人拉进群，发一条消息，调用 `https://api.telegram.org/bot<TOKEN>/getUpdates` 看 `chat.id`（群为负数，如 `-1001234567890`）。

## 3. 配置方式（二选一）

### 方式 A：通政司页（推荐，热加载不重启）

1. 确保已设主密钥环境变量 `TIANSHU_SECRET_MASTER_KEY`（Fernet，用于加密 bot_token）。
2. Web → **通政司** → 「Telegram 机器人」区填写：
   - **Bot Token**（必填）
   - **连接模式**：`长轮询`（默认，推荐）/ `webhook`
   - **允许用户**：逗号分隔的数字 user_id（留空=放行任意，**生产务必填**）
   - **Home 频道**：兜底 chat_id
   - **助手 persona**：默认通政司
3. 保存 → 自动热加载（无需重启）。

### 方式 B：环境变量（`.env`）

```bash
TIANSHU_TELEGRAM_BOT_TOKEN=123456789:ABCdef...
TIANSHU_TELEGRAM_CONNECTION_MODE=polling          # polling | webhook
TIANSHU_TELEGRAM_ALLOWED_USERS=123456789,987654321
TIANSHU_TELEGRAM_HOME_CHANNEL=123456789           # 群为负数 -1001234567890
TIANSHU_TELEGRAM_ASSISTANT_PERSONA_ID=tongzheng
# webhook 模式才需要：
# TIANSHU_TELEGRAM_WEBHOOK_SECRET=<32字节随机串>
# TIANSHU_TELEGRAM_WEBHOOK_PATH=/channels/telegram/webhook
```

> 加载优先级：**DB（通政司保存）> 环境变量 > 不启用**。`bot_token` 为空 → 整个 Telegram 机器人不启用（向后兼容，不影响飞书）。

## 4. 长轮询 vs Webhook

| | 长轮询（polling，默认） | Webhook |
|---|---|---|
| 公网 / TLS | 不需要 | 需要公网 HTTPS |
| 适用 | 自托管、内网、开发 | 有公网域名的生产 |
| 额外配置 | 无 | `webhook_secret`（必填），并在部署侧调用 `setWebhook` 把 `https://<域名><webhook_path>` 注册到 Telegram |

⚠️ **同一 bot token 只能有一个进程在轮询**。双开会触发 Telegram `409 Conflict`；天枢会记录错误并降级（不阻塞 web 服务）。锁文件：`~/.tianshu/telegram_app_lock.<hash>`。

## 5. 使用

私聊机器人或在群里 @ 它：

- 纯文本：进入**助手对话**（续接当前会话敕令）
- `/new <目标>`：新建敕令并执行
- `/list [open|completed|all]`：敕令列表（带「切到」按钮）
- `/select <ID前缀≥6>`：切到指定敕令（进入敕令模式）
- `/status` `/cancel` `/exit`：敕令模式内查看 / 取消 / 退出
- `/budget`：成本概览
- `/menu`：主菜单（inline 按钮）
- `/clear`：归档当前对话、开新会话
- 裁决：工具调用等待裁决时机器人推**裁决按钮**（单次/本敕令/总是/拒绝），也可使用文本命令 `/approve` `/准` `/准敕` `/准永` `/reject` `/驳`

执行完成后，结果会自动回推到发起的会话；运行中显示 `⏳ 思考中…` 占位，完成后删除并发出正文。

## 6. 排错

- **机器人无响应**：检查 `allowed_users` 是否包含你的 user_id（空=放行任意）；群里需 @bot 或回复 bot。
- **启动失败**：看日志 `[telegram] start failed`；常见为 409 冲突（清理 `~/.tianshu/telegram_app_lock.*` 或确认无重复进程）或 token 无效。
- **结果发不出 / 格式乱**：MarkdownV2 解析失败会自动回退纯文本；超长按 4096 UTF-16 分片。
- **飞书 / Telegram 串台**：不会——出站按 `edict.metadata.channel` 隔离，各通道只投递自己发起的敕令。
