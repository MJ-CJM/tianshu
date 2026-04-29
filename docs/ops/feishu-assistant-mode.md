# 飞书助手模式（v1.1）

## 概述

v1.1 引入双模式架构：

- **助手模式**（无 anchor）：通过命令操作（/new /list /select /budget /menu /help /status /cancel）
- **敕令模式**（有 anchor）：纯文本即续接当前敕令

## 模式切换

| 操作 | 触发模式切换 |
|------|------------|
| 飞书首次接入 | 助手模式（默认）|
| `/new <goal>` | 助手 → 敕令 |
| `/select <id>` | 助手 → 敕令 |
| `/exit` | 敕令 → 助手 |
| `/cancel` 当前 anchor 敕令 | 自动退回助手模式 |

## 命令清单

### 助手模式

- `/new <goal>` 新建敕令并进入敕令模式
- `/list [open|completed|cancelled|all]` 列敕令（默认 open）
- `/select <id>` 切到指定敕令（id 前缀 ≥6 字符）
- `/budget` 成本概览（近 7 天 + 当前预算 + Top 5 高消费敕令）
- `/menu` 主菜单卡片
- `/help` 帮助
- `/status <id>` 查敕令状态（需指定 id）
- `/cancel <id>` 取消敕令（需指定 id）

### 敕令模式

- 纯文本 = 续接当前敕令
- `/status` 查当前敕令状态
- `/cancel` 取消当前敕令（如取消的是 anchor，自动退回助手模式）
- `/exit` 退出敕令模式
- `/new <goal>` 自动 /exit + /new
- `/list /budget /menu /help` 查询类（不动 anchor）

## 助手 Persona 配置

通政司页面 → 飞书助手分卡 → 选 cabinet persona 兼任助手 → 保存。

LLM 意图增强：开启后纯文本（如"显示我的列表"）会通过 persona 的 LLM 解析为命令。

- 持久化：`channel_configs` 表存 `assistant_persona_id` + `enable_llm_intent`
- LLM 配置：取决于所选 persona 的 `llm_config_name`，未配置则降级为静默回复
- 容错：JSON 解析失败 / intent 不在白名单 / 调用超时 → 回到 silent reply（"待命中"）

## 升级通告

v1.0 → v1.1 升级后，第一次收到 anchor 敕令的纯文本时，会自动推送一张「v1.1 助手模式上线」卡片，仅推送一次（写 `feishu_announcements` 表幂等）。

## 卡片按钮协议

`/list /menu` 卡片中的按钮 value 走统一协议：

```json
{
  "command": "select" | "list" | "budget" | "help" | "new" | "cancel",
  "edict_id"?: "ed_xxx",
  "filter"?: "open|completed|all",
  "goal"?: "新目标"
}
```

`CardActionDispatcher` 把 value 合成等价文本命令，再走 `ModeRouter`。审批卡片（含 `decision`/`request_id` 字段）由 `ApprovalCardHandler` 优先处理，不会进入通用分发器。

## 紧急逃生

如双模式有严重问题，临时回退到 v1 行为：

```bash
TIANSHU_FEISHU_DISABLE_ASSISTANT_MODE=1
```

设置后纯文本一律走 v1 续接逻辑（`EdictBridge.continue_or_create`），命令如 `/new /list` 不再被识别。

## 故障排查

| 问题 | 排查 |
|------|------|
| 飞书发"你好"无响应 | 默认助手模式不自动新建。请用 `/new` 或 `/list` |
| `/select` 报"短 ID 多个匹配" | 用更长前缀（至少 6 字符；可从 `/list` 卡片复制完整 ID） |
| 自然语言不被识别 | 确认通政司「启用 LLM 意图增强」已开 + 助手 persona 有 `llm_config_name` |
| `/budget` 显示"暂时无法获取" | `cost_manager` 未正确接入或 `cost_ledger` 表查询失败；查日志 `[feishu/card] cost ledger query failed` |
| `/exit` 无效 | 检查 `feishu_anchors` 表是否真的写入了 anchor；`SessionAnchor.delete` 同时清内存缓存和 DB |
| 升级通告反复推送 | 检查 `feishu_announcements` 表是否被错误清空；唯一键是 `(chat_id, version)` |

## 相关文件

- 入口：`src/tianshu/gateway/feishu/__init__.py`（FeishuBot 整合）
- 状态机：`src/tianshu/gateway/feishu/mode_router.py`
- 助手分支：`src/tianshu/gateway/feishu/assistant_branch.py`
- 敕令分支：`src/tianshu/gateway/feishu/edict_branch.py`
- 卡片：`src/tianshu/gateway/feishu/card_builder.py`
- 意图解析：`src/tianshu/gateway/feishu/intent_parser.py`
- 按钮分发：`src/tianshu/gateway/feishu/card_action_dispatcher.py`
- 渲染：`src/tianshu/gateway/feishu/persona_renderer.py`
