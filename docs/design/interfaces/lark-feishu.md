# 飞书 / Lark 接入设计

> `gateway/feishu/` 让用户在飞书里下旨、续接、审批，并把执行结果与审批请求推回 IM。本篇讲「为什么这样设计 + 机制怎么转」，与 Telegram 共享平台无关核心。渠道全局定位见 [./channels.md]，运维配置（建 app、订阅事件、填 allowlist）见 [../../ops/feishu-setup.md]。

**相关实现**：[../../impl/interfaces/](../../impl/interfaces/)

## 1. 为什么是「双模式 bot」

同一个聊天窗口要承担两件性质不同的事：**闲聊/查询**（看任务列表、看预算、随口问问）与**执行任务**（颁敕、续接一个绑定的长任务）。若把它们塞进同一套命令，用户每说一句话都要先声明「这是新任务还是接着上一个」，体验割裂。

天枢的取舍是：**用会话锚（SessionAnchor）的状态隐式区分模式，而非让用户显式切换**。

| 模式 | 触发条件 | 分支 | 用户能做 |
|---|---|---|---|
| 助手模式 | anchor 指向 `metadata.assistant_chat=true` 的敕令（或无 anchor） | `AssistantBranch` | `/menu` `/list` `/budget` `/new` `/select` + 自然语言闲聊 |
| 敕令模式 | anchor 指向业务敕令 | `EdictBranch` | 续接该敕令、`/status` `/cancel` `/exit` `/new`；查询类命令委托回 AssistantBranch |

`ModeRouter.resolve_mode`（`gateway/feishu/mode_router.py`）读 `anchor.get(chat_id)` → 取 `Edict.metadata.assistant_chat` 判定走哪个分支。`dispatch` 进来先 `EdictBridge.ensure_chat_edict` 保证 anchor 永远存在（首次接入自动建一个 assistant_chat 敕令，用通政司配置的 `assistant_persona_id`），所以状态机只需区分「指向 chat 敕令」与「指向业务敕令」两态。

**紧急逃生**：`disable_assistant_mode=True` 时 `FeishuBot._on_message` 绕过 ModeRouter，走 `_on_message_v1_legacy`（仅 `/new` `/status` `/cancel` `/help` 的旧行为），用于双模式出问题时快速降级。

## 2. 入站消息 → edict 的转换链路

平台层（飞书 webhook/ws 收到的 `FeishuMessage`）经分支落到 `EdictBridge`（`gateway/core/edict_bridge.py`）——这是**平台无关核心**，Telegram 复用同一份。核心方法 `continue_or_create` 用一棵决策树把「一条自然语言消息」映射成「续接 vs 新建 vs 拒绝」：

```text
continue_or_create(chat_id, sender, text)
  current = anchor.get(chat_id)
  ├─ current 存在且 edict 未结案（非 COMPLETED/CANCELLED）
  │    ├─ 有 active memorial（SUBMITTED/RUNNING） → raise EdictBusyError（提示「仍在处理中」）
  │    └─ 无 active memorial → _follow_up：复用同一 edict，追加新 memorial
  └─ current 不存在 / 已结案 → create_new（X1：无感自动新建）
```

设计要点：

- **续接优先、新建兜底**：同一 chat 连说几句默认归到同一敕令（多轮上下文），只有当锚定敕令已结案才静默开新敕令（测试 `test_x1_auto_new_when_anchor_closed`）。
- **单飞行约束**：一个敕令同时只允许一个在跑的 memorial，第二条消息撞上 `EdictBusyError` 而非排队/并发，避免长任务被插队打乱。
- **归属固化进 metadata**：`create_new` 把 `channel` / `instance_id` / `chat_id` / `{user_meta_key}` 写进 `Edict.metadata`，这是后续出站与审批**反查 chat、按渠道/实例隔离**的唯一依据。
- **事件解耦**：新建即 `fire("edict.submitted")`，由 Executor 订阅去真正排程；EdictBridge 不直接驱动执行。
- **history 兼容 thinking 模型**：`_follow_up` 构造多轮 history 时，`_build_history` 对缺 `reasoning_content` 的老 memorial 整条跳过 assistant 消息，规避 DeepSeek reasoner 的 400 校验。

`ensure_chat_edict` / `create_new` / `_follow_up` 通过 `channel` / `user_meta_key` / `chat_title_prefix` 三个构造参数参数化，因此 Telegram 只需换注解、不重写逻辑（测试 `test_telegram_channel_metadata`）。

## 3. 审批 / 通知路由回飞书

出站有两条订阅，都挂在 `EventBus` 上，按 `metadata` 反查 chat 后投递：

| 触发事件 | 处理者 | 行为 |
|---|---|---|
| `execution.completed` / `execution.failed` | `FeishuOutbound`（`outbound.py`） | 渲染结果 → `_lookup_chat_id` → 发 post / 切换 typing 气泡 |
| `tool.approval_required` | `ApprovalCardHandler`（`approval_card.py`） | 找 chat → 下审批卡片 → 落 `feishu_pending_cards` |

**审批往返**（与工具治理 [../tools/policy.md] 衔接）：PolicyHook 判定某 tool-call 需人工审批时 `fire("tool.approval_required")` → `ApprovalCardHandler._on_approval_required` 下发卡片（v2 极简模型用纯 markdown，因飞书 ws 不支持卡片回调）→ 用户回 `/approve` `/reject`（或中文 `/准` `/驳`，`/准敕`=本敕令、`/准永`=总是）→ `ApprovalCommandHandler.handle` 解析后调 `ApprovalManager.submit_tool_decision` unblock 执行。

关键设计：

- **chat_id fallback 链**：`metadata.chat_id`（飞书发起的敕令）→ `settings.home_channel`（用户配置）→ `list_chats_anchored_to`（web 创建的敕令也能在飞书审批）。三者皆空才放弃投递（web 端仍可处理）。
- **实例路由隔离**：`_on_approval_required` 先比对 `edict.metadata.instance_id == self._instance_id`，非本实例的敕令不下卡片，避免多 bot 交叉投递。存量无 instance_id 的敕令回退 `{channel}-default`。
- **跨通道幂等**：同一审批可能被 web 端先响应，`submit_tool_decision` 抛 `ValueError` 时回「已被其他通道响应」而非报错。
- **scope 降级透明**：用户请求 `/准永`（always）但命中安全降级（如 shell_exec 不可永久放行）时，回复显式提示「因安全策略降级」，避免用户误以为永久放行。
- **多 pending 消歧**：一个 chat 有多个待审批时，要求用户带 memorial_id 短前缀（≥6 字符），`_list_pending_for_chat` 从 `feishu_pending_cards` 反查。

## 4. lark_cli 工具的安全模型

`tools/lark_cli.py` 把已登录的 `lark-cli` 二进制做**通用透传**（子命令+flags 作为 `args` 字符串列表传入），覆盖消息/文档/表格/日历/通讯录等域，命令随 CLI 升级自动可用，无需逐命令封装。安全靠**工具层 + 策略层双重拦截**：

| 防线 | 位置 | 机制 |
|---|---|---|
| 防命令注入 | `lark_cli` | `create_subprocess_exec`（参数列表，非 shell）；`stdin=DEVNULL` 杜绝交互阻塞 |
| 拒交互/认证命令 | 工具层 `_is_blocked` + 策略层 `LarkCliSafetyRule` | `auth login/logout`、`config init/reset/delete` 双重拦截（防卡浏览器授权、防改主机凭证） |
| 写操作升审批 | `LarkCliSafetyRule`（priority=80） | 命令含写动词（send/create/update/delete…）→ `require_approval` |
| 读操作放行 | `LarkCliSafetyRule` 弃权 → `DefaultTierRule` | lark_cli 基础 tier=T2，落默认放行 |

设计取舍：

- **认证与执行分离**：登录由人工在主机 `lark-cli auth login` 完成一次（凭证落 keychain），工具只复用会话、绝不处理登录；检测到 `_AUTH_HINTS`（未登录特征串）时返回友好的人工登录提示，而非静默失败。
- **token 级判定，不解析语义**：`_non_flag_tokens` 取非 flag token（去前导 `+/-`、小写）比对前缀与写动词集合，简单稳健、不被 flag 干扰。
- **黑名单优先**：交互命令在工具层就 `error_result` 拒绝，即使策略层被绕过也兜底。`LarkCliSafetyRule` 在策略管线里与 `BashSafetyRule` 同 priority=80（详见 [../tools/policy.md]）。

## 5. 多 bot 实例

一个渠道可跑 N 个 bot 实例（不同 app_id / 不同 persona），由 `ChannelBotManager` 编排。`instance_id` 贯穿 `FeishuBot` 所有协作者（SessionAnchor / EdictBridge / FeishuOutbound / ApprovalCardHandler），实现敕令路由、会话锚、审批卡片的多实例隔离；`reload` 不改实例身份。设计 rationale 与生命周期见 [../../ops/multi-bot.md]，渠道全局视图见 [./channels.md] §6。

## 6. 边界与安全校验

- **webhook 安全**（`security.py`）：`verify_signature`（SHA256 时间戳+nonce+encrypt_key+body）+ `verify_token`（header token）+ `DedupChecker`（SQLite 消息 ID 去重）；空 encrypt_key/token 跳过校验（dev 模式）。
- **allowlist 语义**：`is_allowed_user` 空 allowlist = 放行任意可达用户（与参考实现一致），生产**必须**配 `TIANSHU_FEISHU_ALLOWED_USERS`，否则任何人都能下旨。`FeishuSettings.validate_or_raise` 不强制 allowlist，仅由 `FeishuBot` 启动时打 warning。
- **启用门槛**：`FeishuSettings.enabled` 以 `app_id` 是否配置为开关（空则整个 bot 不启用，向后兼容）；`connection_mode` 仅 `websocket`/`webhook`、`domain` 仅 `feishu`/`lark`，否则启动即抛错。
- **进程锁**：启动占 `~/.tianshu/feishu_app_lock.{app_id}`，避免同一 app 双开导致事件重复消费。

> 以上字段的具体环境变量、订阅配置、建 app 步骤均在运维篇 [../../ops/feishu-setup.md]，本篇只讲设计意图。
