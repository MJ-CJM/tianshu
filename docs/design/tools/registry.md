# 工具注册与分级 — ToolRegistry、ToolDefinition、Tier、Builtins

> 设计意图：用统一注册中心管理所有工具，用 tier 标注危险度，让 PolicyEngine 据此决策。

## 1. ToolDefinition

每个工具有一份 `ToolDefinition`（pydantic BaseModel）：

| 字段 | 默认 | 作用 |
|---|---|---|
| `name` | — | OpenAI function name |
| `description` | — | 给模型看的工具说明 |
| `parameters` | — | JSON Schema |
| `tier` | 0 | T0–T4 权限等级 |
| `max_result_chars` | 8000 | 工具结果截断上限（每工具可调） |
| `side_effect` | False | True = 修改状态，winding_down 阶段拦截 |

## 2. Tool Tier（ToolTier IntEnum）

数值越大越危险，与 PolicyEngine 协作：

| Tier | 值 | 含义 |
|---|---|---|
| T0_READONLY | 0 | 只读 / 无副作用，**快路径放行** |
| T1_WORKSPACE | 1 | workspace 内写 |
| T2_NETWORK | 2 | 外部读（SSRF 风险） |
| T3_WRITE | 3 | 外部写 / 可逆副作用，默认需审批 |
| T4_DANGEROUS | 4 | 危险 / 不可逆，默认需审批 |

**fail-secure 关键判断**：tier 缺失或非法（不在 0–4）时，`execute` 动态把这次调用降级为 T4_DANGEROUS（不改 registry 定义）。

## 3. ToolRegistry.execute 流程

```text
1. 工具存在？被 admin 禁用？        → error
2. tier 合法性校验，非法 → T4
3. winding_down 且 side_effect      → 拦截（提示改用只读工具）
4. JSON 参数解析 + jsonschema 校验
5. 过滤 schema 未声明字段（防 LLM 幻觉参数）
6. T0 fast path：跳过 _hooks 链直接执行
   非 T0：before hooks → 执行 → after hooks
7. 异常转 ToolResult(is_error=True)
```

设计要点：
- **T0 快路径**：只读工具仍 validate + 日志，但不经 ToolHook before/after 回调——高频只读零治理开销
- **未声明字段过滤**：LLM 可能幻想 `read_file` 有 `limit/offset` 等额外参数，原生 Python 函数收 kwargs 会 `TypeError` 让 LLM 死循环；这里手动 drop + warn
- **禁用列表**：`apply_disabled` 从 DB `tool_switches` 读回，`get_openai_tools` 过滤掉禁用工具

## 4. ToolResult 契约

`ToolResult(content, details, is_error)`（frozen dataclass）。辅助构造 `ok_result` / `error_result`。`details` 常带 `network`（鸿胪寺审计元数据）、`exit_code`、`truncated` 等。

## 5. Builtins（`register_builtins`）

启动期注册的内建工具集：

| 工具 | tier | side_effect | 说明 |
|---|---|---|---|
| `shell_exec` | T4_DANGEROUS | ✓ | workspace 内 Shell，60s 超时，输出截 2000 字 |
| `read_file` | T0_READONLY | — | 支持 offset/limit 行切片，截 10000 字 |
| `write_file` | T1_WORKSPACE | ✓ | workspace 内写，自动建父目录 |
| `edit_file` / `list_dir` / `grep` / `find_files` | — | — | 文件操作套件 |
| 鸿胪寺 `web_fetch`/`web_search`/`api_request`/`web_extract` | T2_NETWORK | 部分 | 见 network.md |
| `lark_cli` | — | — | 飞书透传（写操作经 LarkCliSafetyRule 升级审批） |
| `submit_edict` / `list_edicts` / `get_edict_status` | — | — | 敕令工具，随通政司开关启停 |
| `memory_search` / `memory_write` / `skill_*` | — | — | 见各子系统文档 |

路径安全：所有文件工具经 `safe_path(workspace, path)` 约束在 workspace 内。

## 6. 结果截断

`max_result_chars` 是 per-tool 截断上限（如 shell_exec 16000、read_file 12000、web_fetch/api_request 16000、web_search/web_extract 8000）。工具内部也各自截断 content（如 shell_exec 截 2000、read_file 截 10000），双层保护避免单次工具结果撑爆 context。

**相关实现**：[../../impl/tools/](../../impl/tools/)
