# 飞书助手模式（v2 极简模型 — 聊天敕令统一）

- **日期**：2026-04-29（v2 修订）
- **作者**:mj-cjm
- **状态**：Draft（v2 极简模型，覆盖 v1.1 设计）
- **基于版本**：v1.1 飞书机器人（commit `006c9f4` + 后续测试 commits，含双模式 + IntentParser）
- **目标版本**：飞书机器人 v2（极简模型）
- **预计工作量**：~半天（300 行净修改 = 删除 + 改 + 新增）

> **v1 → v1.1 → v2 演化路径**
>
> - **v1**：飞书入口 → 自动新建敕令 + 续接（无"模式"概念）
> - **v1.1**：双模式（助手 / 敕令）+ IntentParser 自然语言意图解析（已实施）
> - **v2**：**取消助手与敕令的语义二分** —— 助手对话 = 标记为 `assistant_chat=true` 的普通敕令，行为完全统一（工具/技能/LLM/plan/cron 全继承 persona）

---

## 1. 背景：v1.1 dogfood 反馈

v1.1 上线后用户反馈：
1. `/list` 显示 `EdictStatus.OPEN`（已修，非本次设计范围）
2. **助手模式只能固定回复，不能自然对话** —— 用户期望"和真实对话一样"

根因：v1.1 设计把"助手"和"敕令"割裂为两套语义。IntentParser 仅做命令解析，无法处理"你是谁?" / 闲聊 / 自由对话。

## 2. v2 核心思路（用户原话）

> "助手聊天的敕令，除了标志不一样，其他都是一样的。选择那个官员作为助手，那就这个官员的工具相关的都可以用啊。"

> "若是助手一开始肯定是没有长任务的，但是后续也可以触发长任务，比如我在网页端操作等？所以不需要过度设计。"

→ **统一模型**：助手对话 = 一种特殊敕令；行为完全等同于普通敕令；区分仅为 UI 标记。

## 3. v2 模型

### 3.1 数据模型

```
聊天敕令 = 普通敕令 + 一个标记

  metadata.assistant_chat = true   # 唯一新字段
  source = "channel"
  submitter = "emperor"
  metadata.chat_id = oc_xxx
  ...其余字段与普通敕令相同（status / persona / runtime / ...）
```

### 3.2 行为完全统一

聊天敕令的执行**与普通敕令零差异**：

| 维度 | 行为 |
|------|------|
| 工具 | `persona.tools_allowed`（全部，无白名单）|
| 技能 | `persona.skills_allowed`（全部）|
| LLM 配置 | `persona.llm_config_name` |
| Planner / Critic / Acceptance | **不跳过**（Executor 自适应：简单问题走简单路径，复杂任务走完整 plan）|
| Cron / 长任务触发 | 自然支持（用户说"每天爬这个网页" → executor 自然 plan + cron）|
| 工具调用（含 shell_exec / web_fetch 等）| 完全可用 |
| 审批流程（tool.approval_required）| 与普通敕令相同 |
| 历史构造 | v1 已有的 `_build_history(edict, prev_memorials)` |
| 成本 tracking（户部）| 自动覆盖 |
| Memory 沉淀（文渊阁）| 敕令归档（status=COMPLETED）时触发 |

### 3.3 UI 区别（仅显示）

| anchor 指向 | UI 标记 | 含义 |
|-------------|---------|------|
| `metadata.assistant_chat=true` | 💼 助手 | 用户视角的"助手模式" |
| `metadata.assistant_chat=false` | 📋 敕令 #xxx | 用户视角的"敕令模式" |

## 4. 关键决策（已与用户对齐）

| # | 决策 | 取值 |
|---|------|------|
| 1 | `/list` 显示聊天敕令 | **隐藏**（默认 filter `metadata.assistant_chat != true`）|
| 2 | 工具集 | **persona.tools_allowed 全部**（无"助手专用白名单"）|
| 3 | 归档时机 | **`/clear` 显式归档**（status=COMPLETED + 新建 chat 敕令 + 切 anchor；触发 Memory 沉淀）|
| 4 | Executor 适配 | **不需要**（Executor 自适应；plan/critic/acceptance 不跳过）|
| 5 | 上下文窗口 | **v1 现有 `_build_history` 机制**（无新规则）|
| 6 | IntentParser | **删除**（纯文本直接 follow_up，让 persona LLM 自然回应）|

## 5. 实施差异（vs 已实施 v1.1）

### 5.1 删除

- **`src/tianshu/gateway/feishu/intent_parser.py`** —— 不再需要意图解析
- **`tests/gateway/feishu/test_intent_parser.py`** —— 对应测试
- FeishuBot.__init__ 中 IntentParser 实例化逻辑
- AssistantBranch.__init__ 中 `intent_parser` 参数
- AssistantBranch._handle_natural_language → silent reply 分支
- FeishuBot.reload 中 IntentParser 重建逻辑
- 通政司 `intent_llm_enabled` 字段（保留为 deprecated 兼容；但 UI 隐藏 + 后端忽略）

### 5.2 修改

| 文件 | 修改内容 |
|------|---------|
| `assistant_branch.py` | `_handle_natural_language` 改为：纯文本 → `EdictBridge.continue_or_create`（与 EdictBranch 同行为）|
| `mode_router.py` | 不变（仍读 anchor 决定模式，但 mode 仅用于 UI 标记，命令路由分支保留）|
| `__init__.py` | FeishuBot 构造不再接 `provider_manager`（仅 IntentParser 用）；reload 不再切 IntentParser |
| `assistant_branch.py` | 加 `/clear` 命令实现（仅在 anchor=chat 敕令时可用）|
| `card_builder.py` | `build_list_card` 时 storage.list_edicts 增加 filter `metadata.assistant_chat != true` |

### 5.3 新增

| 模块 / 行为 | 说明 |
|-----------|------|
| 首次接入自动建 chat 敕令 | 飞书首次发任意消息（无 anchor）→ 自动创建 `metadata.assistant_chat=true` 敕令并设 anchor。**取代 v1.1 的 silent reply 行为**。|
| `/clear` 命令 | 当前 anchor 是聊天敕令时：`storage.update_edict_status(id, COMPLETED)` + 新建一个聊天敕令 + 切 anchor。归档时触发 Memory 沉淀。|
| `Storage.list_edicts` filter assistant_chat | 默认 filter；`/list chats` 显式查看聊天敕令历史 |

### 5.4 不变

- `PersonaRenderer / ModeRouter / EdictBranch / EdictBridge / CardBuilder / CardActionDispatcher`：核心结构保留
- `通政司` web 配置：保留 `assistant_persona_id`（决定 chat 敕令使用哪个 persona）；`intent_llm_enabled` 字段保留但隐藏 + 后端忽略
- 卡片协议、模式标记 UI、命令集（除 /clear 新增）

## 6. 行为差异（vs v1.1）

| 场景 | v1.1 行为 | v2 行为 |
|------|---------|--------|
| 飞书首次发"你好" | 助手模式 silent reply | **自动创建 chat 敕令 + executor 处理** → persona LLM 自然回应 |
| "你是谁?" | silent reply | **persona LLM 自然回应**（可能调 read_file 看自己的 ROLE.md）|
| "显示我的列表" | IntentParser → /list | **persona LLM 调 list_edicts 工具 + 自然回应**（如果 persona 工具集含 list_edicts）|
| "取消最近那个" | IntentParser → /cancel | **persona LLM 调 list + cancel 工具完成**（持续对话风格）|
| "每天爬这个网页" | silent reply（unknown intent）| **persona LLM 触发 cron + 长任务 plan** |
| `/list` | 显示所有敕令 | **filter 掉 assistant_chat=true** |
| `/exit` | 删 anchor → 助手模式 | **切 anchor 回到 chat 敕令** |
| `/clear`（新）| - | 归档当前 chat 敕令 + 新建 |

## 7. 风险与回退

| 风险 | 缓解 |
|------|-----|
| 每条飞书消息都过完整 Executor → 成本 / 延迟 | persona 选用 Haiku 等轻量模型；plan/critic 短路对简单查询；用户感知到延迟可在 v2.x 优化 |
| LLM 误调危险工具（如 shell_exec） | 已有审批机制（tool.approval_required → 飞书卡片送达）|
| Cron / 长任务在飞书侧不可见 | 复用 v1.1 通知机制（execution.completed 推回 chat）|
| 删除 IntentParser 后某些 v1.1 测试失败 | Step 4.1 同步删除 test_intent_parser.py |
| 紧急逃生 | 保留 `disable_assistant_mode` 开关（退回 v1 行为）|

## 8. 测试策略

新增/修改：

| 测试 | 内容 |
|------|------|
| `test_assistant_branch.py` | 删除 IntentParser mock 路径；新增"纯文本 → continue_or_create"测试；新增 `/clear` 测试 |
| `test_card_builder.py` | 验证 build_list_card 不显示 assistant_chat=true 敕令 |
| `test_e2e_dual_mode.py` | "首次发消息自动建 chat 敕令"端到端 |
| 删除 `test_intent_parser.py` | 整个文件 |

## 9. 实施顺序建议（writing-plans 用）

1. 删除 IntentParser 模块 + 测试（最简单，先做）
2. 修改 FeishuBot.__init__：移除 IntentParser 注入
3. 修改 AssistantBranch._handle_natural_language → 纯文本 continue_or_create
4. 修改 ModeRouter / 首次接入：自动建 chat 敕令（assistant_chat=true）
5. 修改 Storage.list_edicts / CardBuilder：filter assistant_chat
6. 新增 `/clear` 命令实现
7. 修改 EdictBranch._cmd_exit：切回 chat 敕令而非清 anchor
8. 通政司前端：隐藏 LLM 增强 checkbox（或文案改为 deprecated）
9. 测试补齐（含 /clear 测试 + e2e）
10. 文档更新 docs/ops/feishu-assistant-mode.md

---

## 10. 与 spec / plan 文档的对应

| 章节 | Plan Step |
|------|-----------|
| §3 模型 | 整体设计 |
| §5 实施差异 | Step 1-9 |
| §6 行为差异 | Step 4 (首次接入) + Step 6 (/clear) |
| §8 测试 | Step 9 |

## 11. v3+ 留坑（不在 v2 范围）

- `/list chats`：显式查看历史聊天敕令（v3 加）
- 跨 chat anchor 的对话：当前一个 chat 一个 anchor，未来可能要支持"切换到其它 chat 的会话"
- emperor 长期记忆深度集成：自动从对话敕令 sync 到 emperor memory 的 consolidation 任务
