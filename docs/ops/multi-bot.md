# 多 Bot 实例（multi-instance）

## 概述

飞书 / Telegram 渠道支持同时运行**多个 bot 实例**，每个实例由唯一 `instance_id` 标识：

- `feishu-default` / `telegram-default` —— 默认实例（承接历史单 bot 配置）
- `telegram-<ULID>` / `feishu-<ULID>` —— 后续在通政司新增的实例

每个实例是一个独立机器人：独立 token/凭证、独立 persona、独立 allowlist、独立 home_channel。

典型场景：同一套天枢后端，挂 2 个 Telegram bot——一个对外客服、一个内部运维，互不串扰。

## 新增第二个 bot

在**通政司**页面操作，无需重启服务：

1. 进入对应渠道的「实例列表」
2. 点「新增」
3. 填写：
   - **token / 凭证**（Telegram bot_token；飞书 app_id + app_secret）
   - **label**（显示名，便于区分）
   - **persona**（兼任助手的内阁 persona）
   - **allowlist**（允许的用户）
   - **home_channel**（cron / 无源审批的兜底投递目标，可选）
4. 保存即在线生效——后端 `reload_instance` 当场构造并启动该 bot，**无需重启**。

停用同理：在实例列表关掉 `enabled` 即停止该 bot，不影响其它实例。

## 隔离语义

每个 bot 的会话状态按 `instance_id` 隔离：

| 维度 | 隔离行为 |
|------|---------|
| 聊天会话 anchor | 同一 chat_id 在不同实例互不碰撞（复合主键 `(instance_id, chat_id)`）|
| `/list` 等查询 | 只列本实例的敕令（default 额外继承旧无标记敕令，见下）|
| 审批按钮 / 卡片 | 只有发起实例能处理回调 |
| 出站投递 | 一条敕令只由其所属实例投递；非本实例的敕令不会被错误下发 |

敕令归属由 `metadata.instance_id` 决定，bot 接入消息时自动写入。出站 `_lookup_chat_id`
对 `instance_id` 做守卫：非本实例的敕令直接跳过。

**web 看板例外**：看板的「敕令」页是**全局视图**，展示所有实例的敕令（不按 instance 过滤），
便于运维统一查看。隔离只作用于各 bot 的聊天侧。

## `*-default` 实例的特殊性

`feishu-default` / `telegram-default` 与普通实例的两点差异：

1. **继承旧敕令**：历史上（多实例特性之前）创建的敕令没有 `instance_id` 标记。
   只有 `*-default` 实例的 `/list` 会把这些「无标记 + channel 匹配」的旧敕令一并纳入，
   保证升级后旧对话不丢。非 default 实例只见自己显式打标的敕令。
2. **不可删除**：default 是该渠道的根实例，承接旧配置与旧敕令，UI 不允许删除
   （可以停用 `enabled=false`，但不能移除）。

无 `channel` 的敕令（如纯 cron 任务）不属于任何实例，仍走各实例的 `home_channel` 兜底投递。

## 每实例独立配置

| 配置 | 作用 |
|------|------|
| persona | 该 bot 兼任助手时用哪个内阁 persona（决定工具集 / LLM 配置）|
| allowlist | 该 bot 允许哪些用户交互 |
| home_channel | 该 bot 的 cron / 无源审批兜底投递目标 |
| token / 凭证 | 该 bot 的身份（加密存于 `channel_instances.encrypted_secret`）|

## 存量 DB 迁移

老库（多实例特性之前）首次启动时自动迁移（幂等）：

- anchor 表主键升级为 `(instance_id, chat_id)`，存量行回填 `<channel>-default`。
- pending / seen 表新增 `instance_id` 列，回填 `<channel>-default`。
- 旧单 bot 配置（`channel_configs`）首次 `_build_instances` 时迁移成 `*-default` 实例，
  写回 `channel_instances` 供 UI 可见。

迁移对使用者无感知；旧 chat 会话与历史敕令均保留。

## 故障排查

| 问题 | 排查 |
|------|------|
| 新 bot 不上线 | 看 server 日志 `[gateway] instance <id> start failed`；常见为 token 无效或 vault 缺 `TIANSHU_SECRET_MASTER_KEY`|
| 实例间消息串了 | 检查敕令 `metadata.instance_id` 是否正确；非 default 实例不应出现旧无标记敕令 |
| 旧敕令在新实例看不到 | 设计如此——旧无标记敕令只由 `*-default` 继承 |
| 看板显示了别的 bot 的敕令 | 设计如此——看板是全局视图，不按实例过滤 |
| 停用后仍收到消息 | 确认 `stop_instance` 已 `event_bus.off` 退订；正常停用会立即退订出站/审批订阅 |
