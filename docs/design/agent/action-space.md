# Action Space 与 Observation 设计纪律

把「LLM 能做什么（工具）」和「LLM 看到什么（观察结果）」当成可控的有限带宽资源来设计：工具粒度决定模型能否一次表达完整意图，observation 的截断 / 错误措辞决定模型下一步能否自纠。本篇讲为什么这样切、为什么这样裁、以及失败时给模型回什么。

**相关实现**: [../tools/registry.md](../tools/registry.md)、[../tools/policy.md](../tools/policy.md)

## 1. 工具粒度设计原则

工具定义统一为 `ToolDefinition`（`tools/registry.py`），是 Pydantic `BaseModel`，五个字段就是 action space 的全部设计旋钮：

| 字段 | 作用 | 设计含义 |
|---|---|---|
| `name` / `description` | 暴露给 LLM 的函数名与说明 | 是模型选工具的唯一依据，措辞即 prompt |
| `parameters` | JSON Schema | 约束 LLM 能表达的 action 形状，`execute` 用它做 `jsonschema.validate` |
| `tier` | 权限等级（`ToolTier` T0–T4） | 决定走快路径还是 hook 链，见 policy.md |
| `max_result_chars` | 单工具 observation 上限（默认 8000） | observation 带宽的 per-tool 配额 |
| `side_effect` | 是否有副作用 | `winding_down` 阶段拦截写工具 |

粒度原则——**一个工具 = 一个可被 schema 完整描述、可被一句话讲清的原子意图**：

- **schema 即契约，宁严毋松**。`parameters` 把 LLM 能填的字段钉死。`ToolRegistry.execute` 不止 `jsonschema.validate`，还**主动过滤 schema 未声明的字段**：LLM 常基于训练数据幻觉出不存在的参数（例如想象 `read_file` 有 `limit`/`offset`），原生 Python 函数 strict 收 kwargs 会抛 `TypeError`，模型拿不到结构化反馈只会死循环重试。所以 registry 把多余字段丢弃 + WARNING，而不是报错——让幻觉参数静默降级，工具仍按声明的字段执行。
- **tier 与粒度正交但同源**。同一动作的读 / 写应拆成不同工具或至少不同 tier，让 PolicyEngine 能对「外部写」单独要审批，而不是把读写塞进一个工具靠运行时分支。
- **side_effect 显式标注**而非靠名字猜：`winding_down`（预算软着陆）阶段 registry 直接拦截所有 `side_effect=True` 工具，这要求每个有副作用的工具诚实声明，否则收尾阶段会留半成品。

## 2. Observation 截断格式

`execute` 返回 `ToolResult(content, details, is_error)`，但喂给 LLM 的只有 `content` 字符串。带宽控制发生在 `agent.py` 拼装 tool 消息处——**截断是 agent 层的硬裁剪，按 per-tool 配额执行**：

```python
content = tool_result.content
max_chars = tool_defn.max_result_chars if tool_defn else 8000
if len(content) > max_chars:
    content = content[:max_chars] + "\n[... truncated]"
new_messages.append({
    "role": "tool", "tool_call_id": tc["id"], "content": content,
})
```

设计要点：

- **配额来自工具自己**：上限取 `tool_defn.max_result_chars`，找不到定义（未注册工具）兜底 8000。即一个大输出工具（如读全文件）可以调高自己的配额，而噪音型工具保持小配额，避免单次输出撑爆上下文。
- **截断是「头部保留 + 显式尾标」**：直接 `content[:max_chars]` 取前缀，再拼 `\n[... truncated]`。保留头部（通常含结构 / 关键信息），并用显式标记告诉模型「这里被裁过」，避免它把残缺尾部当完整结果。
- **截断只影响 LLM 视图**：写进 `messages` 的是裁剪版，但事件流 `tool.completed` / `tool.failed` 里的 `result_preview` 另取 `tool_result.content[:200]`（独立的 200 字预览，给前端 / 审计看），原始 `content` 不被截断污染。

**Worked example**（默认 `max_result_chars=8000`）：某工具返回 12000 字符 → LLM 在 tool 消息里看到的是「前 8000 字符」+ 换行 + `[... truncated]`，共约 8014 字符；同一次调用 emit 的事件里 `result_preview` 是该 content 的前 200 字。两条带宽各自独立，互不影响。

> 注：这是 agent 层的「事前配额裁剪」。另有 `micro_compact`（每轮预防性收缩历史里的旧 tool 结果）作用于历史消息，二者叠加控制总带宽——见 [react-loop.md](./react-loop.md)。

## 3. 错误信息设计：失败时回给 LLM 什么

工具失败不是终点，而是给模型的一次自纠机会，所以**错误信息本身被当作 prompt 来设计**。失败有三个来源，每个都返回结构化、可读、可据以改写下一步 action 的文本：

| 失败来源 | 位置 | 回给 LLM 的 content |
|---|---|---|
| 参数 / schema 错 | `registry.execute` | `Invalid JSON arguments: ...` / `Parameter validation failed: {message}` |
| 工具内部抛异常 | `registry.execute` | `Error executing {name}: {e}` |
| 工具上层抛异常 | `agent.py` try/except | `Tool error: {tool_err}`（包成 `ToolResult(is_error=True)`） |
| Hook block（policy/审批拒绝） | `agent.py` | tool 消息 `Tool blocked: {reason}` |
| winding_down 拦截 | `registry.execute` | 「被 winding_down 阶段拦截……请改用只读工具完成总结/交接」 |

设计纪律：

- **错误进 conversation，不抛栈**。所有失败都被收敛成 `ToolResult(is_error=True)` 或一条 `role:"tool"` 消息回填到 `messages`，模型在下一轮看到的是「这次为什么失败」，而非 Python traceback。
- **可执行的引导优于报错**。如 winding_down 拦截不只是说「不允许」，而是直接给出下一步该怎么做（改用只读工具收尾）；schema 失败把 `jsonschema` 的 `e.message` 透传，让模型知道哪个字段不合法。
- **被 block 也要回 tool 消息**。Hook block 时 agent 仍 append 一条 `Tool blocked: {reason}` 的 tool 结果并 `continue`，保证 assistant 的 `tool_calls` 都有配对的 tool 响应（否则下一轮请求消息结构非法、上游 400）。

错误措辞直接影响完成率：含「下一步怎么做」的错误能让模型在同一任务内自纠收敛；纯报错或抛栈则常诱发「拿同样的坏参数死磕」。这正是下一节熔断要兜底的失败模式。

## 4. Repeated-failure 熔断

即使错误信息写得好，模型仍可能拿着同一个 schema/args bug 反复撞墙（典型：基于训练数据幻觉出的参数），把 `max_iterations` 的 token 全烧光。`agent.py` 在工具循环里维护一个**连续同错计数器**做主动熔断：

```python
repeated_failures: dict[tuple[str, str], int] = {}
REPEATED_FAILURE_LIMIT = 3
```

机制：

- **签名 = (工具名, 错误前 200 字符)**。取 `tool_result.content[:200]` 作 `err_sig`，截前缀是为了消除行号 / 时间戳等噪音，让「同一类错误」能稳定归并到同一个 key。
- **连续 3 次同签名 → break**。任一 `(tool_name, err_sig)` 计数 `>= REPEATED_FAILURE_LIMIT` 时，`agent.py` 不再继续，直接以 `ExitReason.REPEATED_TOOL_FAILURE` 收尾，`error` 写明「工具 X 连续 N 次失败，错误一致：…」。
- **成功即清零，但只清该工具**。某工具任一次成功，会清掉**该工具名**的所有失败计数（`{k: v for ... if k[0] != tc["name"]}`），其他工具的计数不受影响。「连续」语义因此是 per-tool 的——别的工具偶发失败不会干扰本工具的计数。

为什么阈值是 3 而非 1：单次失败可能是模型读了错误信息后就能改对（见上节），给它 2 次重试空间；但「同样的错」连发 3 次说明模型陷入了无法靠 observation 自纠的回路，继续只是烧钱，此时熔断比续命更优。该退出原因汇入 `ExitReason` 全集，由 orchestrator 据此分派后处理（见 [react-loop.md](./react-loop.md) §4）。

**相关实现**: [../../impl/agent/](../../impl/agent/)
