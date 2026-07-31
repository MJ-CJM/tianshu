# 飞书助手模式（v2 极简模型）

## 概述

飞书助手与敕令模式**底层统一为同一种敕令**，仅以 `metadata.assistant_chat=true` 标记区分：

- **聊天敕令**（💼 助手）：用于持续对话，工具/技能/LLM 等行为完全等同于普通敕令
- **业务敕令**（📋 敕令 #xxx）：用户显式 `/new` 创建的任务

助手能否"做事"由绑定的 cabinet persona 决定（通政司配置）。选「兵部尚书」做助手 → 兵部尚书的 tools_allowed 全部可用（含 shell_exec / web_fetch 等）。选「户部尚书」 → 用其工具集。

## 模式切换

| 操作 | 当前 anchor | 新 anchor | UI |
|------|-----------|-----------|-----|
| 飞书首次接入 | （无）| 自动建 chat 敕令（assistant_chat=true）| 💼 助手 |
| `/new <goal>` | chat 敕令 | 新建业务敕令 | 📋 敕令 |
| `/select <id>` | * | 指定业务敕令 | 📋 敕令 |
| `/exit` | 业务敕令 | 切回 chat 敕令（复用已存在的或新建）| 💼 助手 |
| `/clear` | chat 敕令 | 归档 + 新建 chat 敕令 | 💼 助手（新对话）|

## 命令清单

### 助手模式（chat 敕令上下文）
- 纯文本 = **continue_or_create** → executor 用 persona LLM 自然回应（含工具调用）
- `/new <目标>` 新建业务敕令
- `/list [filter]` 查业务敕令列表（自动隐藏 chat 敕令）
- `/select <id>` 切到业务敕令
- `/budget` 成本概览
- `/menu` 主菜单
- `/clear` 归档当前对话 + 新建 chat 敕令
- `/help` 帮助

### 敕令模式（业务敕令上下文）
- 纯文本 = 续接当前业务敕令
- `/status` 查当前敕令状态
- `/cancel` 取消当前敕令
- `/exit` 切回 chat 敕令（v2: 不再清 anchor）
- `/new <目标>` 自动 /exit + /new
- `/list /budget /menu /help` 查询类（不动 anchor）

## 助手 Persona 配置

通政司页面 → 飞书助手分卡 → 选 cabinet persona 兼任助手 → 保存。

**v1.1 的「LLM 意图增强」开关 v2 已废弃** —— v2 不再有"自然语言 → 命令"的转换层；所有纯文本直接走 executor，由 persona LLM 决定回应方式（含工具调用）。

## 与 v1.1 的关键差异

| 场景 | v1.1 | v2 |
|------|------|------|
| 飞书首次发"你好" | silent reply | 自动建 chat 敕令 + LLM 回应 |
| "你是谁?" | silent reply | LLM 自然回应 |
| "显示我的列表" | IntentParser → /list | LLM 调 list_edicts 工具回应 |
| "每天爬这个网页" | silent reply | LLM 创建 cron 普通任务；确需深度处理时，由每次普通任务再创建一次长程任务 |
| `/exit` | 删 anchor → 助手模式 | 切回 chat 敕令 |
| `/clear`（新）| - | 归档 + 新建 chat 敕令 |

## 紧急逃生

如 v2 有严重问题，临时回退到 v1 行为：

```bash
TIANSHU_FEISHU_DISABLE_ASSISTANT_MODE=1
```

## 故障排查

| 问题 | 排查 |
|------|------|
| 助手不响应纯文本 | 看 server 日志是否有 [feishu/edict] follow_up；检查 persona 的 LLM 配置可达 |
| `/list` 不显示我的聊天 | v2 设计如此 —— 聊天敕令默认隐藏；如需查看用 SQL 直接查 metadata.assistant_chat=true 的敕令 |
| `/clear` 报"业务敕令请用 /exit" | 当前 anchor 是业务敕令而非 chat 敕令；先 /exit 再 /clear |
| 自然语言不被识别为命令 | v2 设计：所有纯文本由 LLM 自主决定回应（不再做命令意图映射）|
| `/exit` 后产生新 chat 敕令 | 同 chat_id 已有 open chat 敕令时会自动复用，不会产生孤儿 |

## 实现原理

```
飞书消息 → ModeRouter.dispatch
  ├── ensure_chat_edict（保证 anchor 存在）
  │   - anchor 已有 → 直接返回
  │   - 同 chat_id 有 open chat 敕令 → 复用
  │   - 都没有 → 新建（不创建 SUBMITTED memorial 避免 EdictBusyError）
  ├── resolve_mode（基于 anchor 敕令的 metadata.assistant_chat）
  └── 命令路由 → AssistantBranch / EdictBranch
       └── 纯文本 → continue_or_create → executor.execute_edict
                                        → persona LLM + 工具
```
