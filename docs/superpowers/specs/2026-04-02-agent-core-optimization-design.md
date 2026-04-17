# Tianshu Agent Core Optimization Design Spec

> 参考项目: Claude Code (v2.1.88), DeerFlow 2.0
> 日期: 2026-04-02
> 状态: Draft

---

## 目录

- [概述](#概述)
- [Phase 1: Agent 核心循环重构 (P0)](#phase-1-agent-核心循环重构-p0)
  - [1.1 循环模型重设计](#11-循环模型重设计)
  - [1.2 多策略上下文压缩](#12-多策略上下文压缩)
  - [1.3 分层恢复策略](#13-分层恢复策略)
- [Phase 2: 工具系统增强 (P1)](#phase-2-工具系统增强-p1)
  - [2.1 Tool 行为声明体系](#21-tool-行为声明体系)
  - [2.2 工具并发分区执行](#22-工具并发分区执行)
  - [2.3 大型工具结果持久化降级](#23-大型工具结果持久化降级)
  - [2.4 Prompt Cache 稳定性](#24-prompt-cache-稳定性)
- [Phase 3: 架构层增强 (P2)](#phase-3-架构层增强-p2)
  - [3.1 Agent Middleware Chain](#31-agent-middleware-chain)
  - [3.2 分层权限系统](#32-分层权限系统)
  - [3.3 韧性工程](#33-韧性工程)
- [Phase 4: 生态扩展 (P3)](#phase-4-生态扩展-p3)
  - [4.1 MCP 协议支持](#41-mcp-协议支持)
  - [4.2 Sandbox 沙箱隔离](#42-sandbox-沙箱隔离)
  - [4.3 流式工具执行](#43-流式工具执行)
- [影响文件清单](#影响文件清单)
- [风险与约束](#风险与约束)

---

## 概述

### 背景

Tianshu 当前的 Agent 循环在 5-10 轮迭代内工作良好，但随着任务复杂度提升，面临以下瓶颈:

1. **上下文管理粗放** -- 仅有"80% 阈值截断"一种策略，无分层防御
2. **工具执行串行** -- 多个只读工具调用无法并行，浪费延迟
3. **恢复能力弱** -- API 错误/output 截断/模型过载缺乏自动恢复
4. **退出语义模糊** -- 只有 COMPLETED 和 FAILED 两种结果
5. **可扩展性受限** -- 新功能需修改 Agent 核心循环代码

### 设计原则

从 Claude Code 提炼出的核心工程哲学:

1. **分层防御，渐进降级** -- 每个问题至少有 2 层恢复策略
2. **Fail-closed 默认值** -- 工具默认不安全/不可并行，显式标记才放行
3. **持续维护，不等危机** -- 每轮清理，而非等阈值再一刀切
4. **状态可追溯** -- 每次 continue 带原因，每轮状态是新对象
5. **退出有意义** -- 不同退出对应不同的后续处理

### 实施路线

```
Phase 1 (P0): Agent 核心循环 -- 稳定性/长任务能力
Phase 2 (P1): 工具系统增强 -- 执行效率/成本优化
Phase 3 (P2): 架构层增强 -- 可扩展性/灵活性
Phase 4 (P3): 生态扩展 -- MCP/Sandbox/流式
```

Phase 1-2 可独立实施，Phase 3 依赖 Phase 1 的循环重构，Phase 4 可在任意阶段后开始。

---

## Phase 1: Agent 核心循环重构 (P0)

### 1.1 循环模型重设计

**目标**: 将 `for iteration in range(max_iterations)` 替换为 `while` 循环 + 可配置安全阀 + 显式退出路径。

#### 1.1.1 ExitReason 枚举

**新增文件**: `src/tianshu/executor/exit_reason.py`

```python
from enum import StrEnum

class ExitReason(StrEnum):
    """Agent 循环退出原因，每种对应不同的后续处理策略"""
    COMPLETED = "completed"                # 正常完成（LLM 未返回 tool_calls）
    MAX_ITERATIONS = "max_iterations"      # 安全阀触发
    CONTEXT_OVERFLOW = "context_overflow"  # 压缩后仍超限
    TIMEOUT = "timeout"                    # edict.runtime.timeout_seconds 到达
    CANCELLED = "cancelled"                # 外部取消（shutdown_event）
    HOOK_BLOCKED = "hook_blocked"          # BEFORE_ITERATION hook 阻止
    BUDGET_EXHAUSTED = "budget_exhausted"  # Token 或成本预算耗尽
    LLM_ERROR = "llm_error"               # API 不可恢复错误（认证/模型不存在等）
    OUTPUT_TRUNCATED = "output_truncated"  # output 截断且恢复次数耗尽
```

#### 1.1.2 AgentResult 增强

**修改文件**: `src/tianshu/executor/agent.py`

```python
@dataclass
class AgentResult:
    status: TaskStatus
    summary: str | None
    result: str | None
    usage: UsageSummary
    error: str | None
    events: list[dict]
    exit_reason: ExitReason          # 新增: 退出原因
    iteration_count: int             # 新增: 实际执行轮数
    compact_count: int               # 新增: 压缩次数
    recovery_attempts: dict          # 新增: {"output_truncated": 2, "compact": 1}
```

#### 1.1.3 不可变循环状态

**新增文件**: `src/tianshu/executor/loop_state.py`

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)
class LoopState:
    """Agent 循环的不可变状态快照，每轮创建新对象"""
    messages: tuple[dict, ...]
    iteration: int
    transition_reason: str = "initial"

    # 恢复状态 guard（每轮重置）
    compact_attempted: bool = False
    output_recovery_count: int = 0

    # 累积指标（不重置）
    total_compact_count: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0

    def next_turn(self, new_messages: list[dict]) -> "LoopState":
        """创建下一轮的状态（重置 guard，累积指标）"""
        return LoopState(
            messages=tuple(new_messages),
            iteration=self.iteration + 1,
            transition_reason="next_turn",
            compact_attempted=False,
            output_recovery_count=0,
            total_compact_count=self.total_compact_count,
            total_prompt_tokens=self.total_prompt_tokens,
            total_completion_tokens=self.total_completion_tokens,
        )

    def with_recovery(self, reason: str, messages: list[dict]) -> "LoopState":
        """创建恢复后的状态（保留 guard 状态）"""
        return LoopState(
            messages=tuple(messages),
            iteration=self.iteration,  # 不增加 iteration
            transition_reason=reason,
            compact_attempted=self.compact_attempted,
            output_recovery_count=self.output_recovery_count,
            total_compact_count=self.total_compact_count,
            total_prompt_tokens=self.total_prompt_tokens,
            total_completion_tokens=self.total_completion_tokens,
        )
```

#### 1.1.4 核心循环伪代码

**修改文件**: `src/tianshu/executor/agent.py` (Agent.execute 方法)

```python
async def execute(self, edict, history, user_content,
                  tool_filter, persona) -> AgentResult:
    state = LoopState(
        messages=tuple(self._build_initial_messages(edict, history,
                                                    user_content, persona)),
        iteration=0,
    )
    max_iterations = edict.runtime.max_iterations or self.config.max_iterations

    while state.iteration < max_iterations:
        # ── Phase 1: 上下文维护 ──
        state = self._micro_compact(state)

        if self._should_auto_compact(state):
            state = await self._auto_compact(state)

        if self._at_blocking_limit(state):
            return self._build_result(state, ExitReason.CONTEXT_OVERFLOW)

        # ── Phase 2: Hook 检查 ──
        hook_result = await self.hooks.run(HookType.BEFORE_ITERATION, ...)
        if hook_result and hook_result.block:
            return self._build_result(state, ExitReason.HOOK_BLOCKED,
                                      error=hook_result.reason)

        # ── Phase 3: LLM 调用 ──
        try:
            response = await self._call_llm_with_fallback(state, tool_filter)
        except UnrecoverableError as e:
            return self._build_result(state, ExitReason.LLM_ERROR,
                                      error=str(e))

        # ── Phase 4: 无工具调用 → 恢复链 or 结束 ──
        if not response.tool_calls:
            exit_reason = self._handle_no_tool_calls(state, response)
            if exit_reason == "continue":
                state = state.with_recovery("output_continuation",
                                            list(state.messages) + [continuation_msg])
                continue
            return self._build_result(state, exit_reason,
                                      summary=response.content)

        # ── Phase 5: 工具执行 ──
        tool_results = await self._execute_tools(response.tool_calls)

        # ── Phase 6: 预算检查 ──
        if self._budget_exhausted(state, response.usage):
            return self._build_result(state, ExitReason.BUDGET_EXHAUSTED)

        # ── Phase 7: 取消检查 ──
        if self._shutdown_requested:
            return self._build_result(state, ExitReason.CANCELLED)

        # ── Phase 8: 状态替换（不可变） ──
        new_messages = list(state.messages) + [assistant_msg] + tool_result_msgs
        state = state.next_turn(new_messages)

    return self._build_result(state, ExitReason.MAX_ITERATIONS)
```

---

### 1.2 多策略上下文压缩

**目标**: 从单一"80% 截断"升级为 3 层压缩体系。

**新增文件**: `src/tianshu/executor/compaction/`

```
compaction/
├── __init__.py
├── micro.py          # 每轮工具结果清理
├── auto.py           # 阈值触发 LLM 总结
├── reactive.py       # API 错误恢复压缩
├── strategy.py       # 策略编排
└── token_estimator.py  # Token 估算
```

#### 1.2.1 Micro Compact（每轮运行）

**文件**: `compaction/micro.py`

每轮循环开始前运行，清理历史工具结果，持续抑制 token 增长。

```python
# 可压缩的工具类型
COMPACTABLE_TOOLS = {"grep", "find_files", "list_dir", "edit_file"}

def micro_compact(state: LoopState, keep_recent: int = 4) -> LoopState:
    """
    清理旧的工具结果消息。
    - 保留最近 keep_recent 条 tool role 消息的完整内容
    - 更早的 tool 消息截断为摘要
    - 不修改 system/user/assistant 消息
    """
    messages = list(state.messages)
    tool_indices = [
        i for i, m in enumerate(messages)
        if m.get("role") == "tool"
        and _is_compactable(m)
    ]

    for idx in tool_indices[:-keep_recent]:
        original = messages[idx]["content"]
        messages[idx] = {
            **messages[idx],
            "content": _truncate_tool_result(original, max_chars=200),
        }

    return state.with_recovery("micro_compact", messages)

def _is_compactable(msg: dict) -> bool:
    """判断是否为可压缩的工具结果"""
    # 根据 tool_call_id 关联的工具名判断
    return True  # 默认所有工具结果可压缩

def _truncate_tool_result(content: str, max_chars: int = 200) -> str:
    original_len = len(content)
    if original_len <= max_chars:
        return content
    return f"[工具结果已压缩，原始长度 {original_len} 字符]\n{content[:max_chars]}..."
```

**触发时机**: 每轮循环开始（Phase 1 第一步）
**成本**: O(n) 遍历消息列表，零 LLM 调用

#### 1.2.2 Auto Compact（阈值触发）

**文件**: `compaction/auto.py`

当 token 量接近上下文窗口时，使用 LLM 对历史对话进行总结压缩。

```python
# 阈值配置
COMPACT_THRESHOLD_RATIO = 0.75      # context_limit 的 75% 触发
COMPACT_BUFFER_TOKENS = 20_000      # 预留给总结输出的 token
MAX_CONSECUTIVE_FAILURES = 3        # 熔断器阈值

async def auto_compact(
    state: LoopState,
    llm: LLMClient,
    context_limit: int,
) -> LoopState:
    """
    LLM 总结压缩:
    1. 保留 system prompt (index 0) + 最近 6 条消息
    2. 中间消息发送给 LLM 总结
    3. 用总结消息替换中间部分
    """
    messages = list(state.messages)
    if len(messages) <= 8:
        return state  # 消息太少，无需压缩

    head = messages[:1]          # system prompt
    middle = messages[1:-6]      # 待压缩
    tail = messages[-6:]         # 保留

    summary = await _summarize_messages(llm, middle)

    compact_msg = {
        "role": "user",
        "content": (
            "[以下是之前对话的压缩摘要，不要回复此消息]\n\n"
            f"{summary}"
        ),
    }

    new_messages = head + [compact_msg] + tail
    return LoopState(
        messages=tuple(new_messages),
        iteration=state.iteration,
        transition_reason="auto_compact",
        compact_attempted=True,
        total_compact_count=state.total_compact_count + 1,
        # 其余字段继承
        output_recovery_count=state.output_recovery_count,
        total_prompt_tokens=state.total_prompt_tokens,
        total_completion_tokens=state.total_completion_tokens,
    )

async def _summarize_messages(llm: LLMClient, messages: list[dict]) -> str:
    """调用 LLM 生成对话摘要"""
    prompt = COMPACT_PROMPT_TEMPLATE.format(
        conversation=_format_messages_for_summary(messages)
    )
    response = await llm.chat(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=COMPACT_BUFFER_TOKENS,
    )
    return response.content
```

**触发条件**:
```python
def should_auto_compact(state: LoopState, context_limit: int) -> bool:
    estimated = estimate_tokens(state.messages)
    threshold = int(context_limit * COMPACT_THRESHOLD_RATIO)
    return estimated > threshold and len(state.messages) > 8
```

**熔断器**: 连续失败 `MAX_CONSECUTIVE_FAILURES` 次后停止尝试。

#### 1.2.3 Reactive Compact（API 错误恢复）

**文件**: `compaction/reactive.py`

当 LLM API 返回 `prompt_too_long` 或 `context_length_exceeded` 错误时触发。

```python
async def reactive_compact(
    state: LoopState,
    llm: LLMClient,
    error: LLMError,
) -> LoopState | None:
    """
    分两步恢复:
    1. 先尝试 micro compact aggressive 模式（只保留最近 2 条工具结果）
    2. 若仍超限，执行 auto compact
    返回 None 表示恢复失败
    """
    # Step 1: aggressive micro compact
    aggressive_state = micro_compact(state, keep_recent=2)
    if not _still_over_limit(aggressive_state, error):
        return aggressive_state

    # Step 2: auto compact
    try:
        return await auto_compact(aggressive_state, llm, _extract_limit(error))
    except Exception:
        return None  # 恢复失败
```

**触发时机**: LLM 调用抛出 context_length 相关异常时（在循环的恢复链中）

#### 1.2.4 Token 估算

**文件**: `compaction/token_estimator.py`

```python
def estimate_tokens(messages: Sequence[dict]) -> int:
    """
    混合估算: 优先使用 API 返回的 usage，未覆盖部分用字符估算。
    字符估算: 中文约 1 字 = 1-2 token，英文约 4 字符 = 1 token
    统一用 len(content) // 3 作为保守估算（偏高，避免低估）。
    """
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += len(content) // 3
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    total += len(block["text"]) // 3
    return total
```

#### 1.2.5 Compact Prompt 模板

```python
COMPACT_PROMPT_TEMPLATE = """你是一个对话压缩助手。请将以下对话历史压缩为简洁的摘要。

要求:
1. 保留所有关键决策、执行结果和错误信息
2. 保留文件路径、函数名等具体技术细节
3. 按时间顺序组织
4. 不要添加对话中没有的信息
5. 使用简洁的要点格式

对话内容:
{conversation}

请输出压缩摘要:"""
```

---

### 1.3 分层恢复策略

**目标**: 为 Agent 循环中的各类错误建立梯级恢复链，而非直接失败。

#### 1.3.1 Output 截断恢复

**位置**: Agent.execute 循环中的 Phase 4

当 LLM 返回 `finish_reason == "length"` 时:

```python
MAX_OUTPUT_RECOVERY = 3

def _handle_no_tool_calls(self, state: LoopState,
                          response: LLMResponse) -> ExitReason | str:
    """处理 LLM 未返回工具调用的情况"""

    # 检查 output 是否被截断
    if response.finish_reason == "length":
        if state.output_recovery_count < MAX_OUTPUT_RECOVERY:
            # 注入接续消息，继续循环
            return "continue"  # 调用方创建 continuation state
        return ExitReason.OUTPUT_TRUNCATED

    # 正常结束
    return ExitReason.COMPLETED
```

接续消息:
```python
CONTINUATION_MESSAGE = {
    "role": "user",
    "content": "你的输出被截断了。请从中断处直接继续，不要重复已输出的内容。"
}
```

#### 1.3.2 Context Overflow 恢复链

**位置**: Agent.execute 循环中的 Phase 3（LLM 调用异常处理）

```python
async def _handle_context_overflow(self, state: LoopState,
                                    error: Exception) -> LoopState | None:
    """
    恢复链:
    1. aggressive micro compact (保留 2 条)
    2. auto compact (LLM 总结)
    3. 返回 None = 不可恢复
    """
    if state.compact_attempted:
        return None  # 已经尝试过，避免循环

    result = await reactive_compact(state, self.llm, error)
    return result
```

在主循环中的使用:
```python
try:
    response = await self._call_llm_with_fallback(state, tool_filter)
except ContextOverflowError as e:
    recovered = await self._handle_context_overflow(state, e)
    if recovered:
        state = recovered
        continue  # 用压缩后的 state 重试
    return self._build_result(state, ExitReason.CONTEXT_OVERFLOW)
```

#### 1.3.3 预算检查

**位置**: Phase 6

```python
def _budget_exhausted(self, state: LoopState, usage: UsageSummary) -> bool:
    """检查 token/成本预算是否耗尽"""
    runtime = self.current_edict.runtime

    # Token 预算
    if runtime.token_budget:
        total = state.total_prompt_tokens + state.total_completion_tokens
        if total >= runtime.token_budget:
            return True

    # 成本预算（通过 CostManager 查询）
    if runtime.cost_budget_cny:
        current_cost = self.cost_manager.get_edict_cost(self.current_edict.id)
        if current_cost >= runtime.cost_budget_cny:
            return True

    return False
```

---

## Phase 2: 工具系统增强 (P1)

### 2.1 Tool 行为声明体系

**目标**: 为每个 Tool 添加行为元数据，支撑并发决策和权限判断。

**修改文件**: `src/tianshu/tools/types.py`

```python
from typing import Literal, Protocol

class ToolBehavior(Protocol):
    """Tool 行为声明协议"""

    def is_read_only(self, args: dict) -> bool:
        """此调用是否只读（不修改文件系统/数据库等）"""
        ...

    def is_concurrency_safe(self, args: dict) -> bool:
        """此调用是否可与其他 safe 调用并行执行"""
        ...

    def is_destructive(self, args: dict) -> bool:
        """此调用是否不可逆（删除文件、发送消息等）"""
        ...

    def interrupt_behavior(self) -> Literal["cancel", "block"]:
        """中断时的策略: cancel=立即取消, block=等完成"""
        ...

    @property
    def max_result_chars(self) -> int:
        """内联到消息的最大字符数，超限则持久化降级"""
        ...
```

**修改文件**: `src/tianshu/tools/registry.py`

```python
@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict          # JSON Schema
    handler: Callable
    tier: str = "T0"

    # 新增: 行为声明（fail-closed 默认值）
    read_only: bool = False            # 默认可写
    concurrency_safe: bool = False     # 默认不可并行
    destructive: bool = False          # 默认可逆
    interrupt_behavior: str = "cancel"
    max_result_chars: int = 8000       # 默认 8K
```

**内置工具声明更新** (`src/tianshu/tools/builtins.py`):

| 工具 | read_only | concurrency_safe | destructive | max_result_chars |
|------|-----------|------------------|-------------|-----------------|
| grep | True | True | False | 4000 |
| find_files | True | True | False | 4000 |
| list_dir | True | True | False | 8000 |
| edit_file | False | False | True | 2000 |

---

### 2.2 工具并发分区执行

**目标**: 将串行工具执行升级为并发分区执行。

**新增文件**: `src/tianshu/executor/tool_executor.py`

```python
from dataclasses import dataclass

@dataclass
class ToolBatch:
    is_concurrent: bool
    calls: list[dict]  # [{"id", "name", "args"}, ...]

def partition_tool_calls(
    tool_calls: list[dict],
    registry: ToolRegistry,
) -> list[ToolBatch]:
    """
    将一次 LLM 响应中的多个 tool_calls 分成并发/串行 batch。

    规则:
    - 连续的 concurrency_safe 工具合成一个并发 batch
    - 非 safe 工具独占一个串行 batch
    - 保持原始调用顺序
    """
    if not tool_calls:
        return []

    batches: list[ToolBatch] = []
    current_calls: list[dict] = []
    current_safe: bool | None = None

    for tc in tool_calls:
        tool_def = registry.get_definition(tc["name"])
        is_safe = (
            tool_def.concurrency_safe
            if tool_def else False  # fail-closed
        )

        if current_safe is None:
            current_safe = is_safe

        if is_safe == current_safe:
            current_calls.append(tc)
        else:
            batches.append(ToolBatch(
                is_concurrent=current_safe,
                calls=current_calls,
            ))
            current_calls = [tc]
            current_safe = is_safe

    if current_calls:
        batches.append(ToolBatch(
            is_concurrent=current_safe or False,
            calls=current_calls,
        ))

    return batches


async def execute_tool_batches(
    batches: list[ToolBatch],
    registry: ToolRegistry,
    hooks: HookManager,
    max_concurrency: int = 5,
) -> list[dict]:
    """
    按 batch 执行工具调用:
    - 并发 batch: asyncio.gather (受 max_concurrency 限制)
    - 串行 batch: 逐个执行
    """
    all_results = []
    semaphore = asyncio.Semaphore(max_concurrency)

    for batch in batches:
        if batch.is_concurrent and len(batch.calls) > 1:
            async def _run(tc):
                async with semaphore:
                    return await _execute_single(tc, registry, hooks)

            results = await asyncio.gather(
                *[_run(tc) for tc in batch.calls],
                return_exceptions=True,
            )
            for tc, result in zip(batch.calls, results):
                if isinstance(result, Exception):
                    all_results.append(_error_tool_result(tc, result))
                else:
                    all_results.append(result)
        else:
            for tc in batch.calls:
                result = await _execute_single(tc, registry, hooks)
                all_results.append(result)

    return all_results
```

---

### 2.3 大型工具结果持久化降级

**目标**: 工具结果超过阈值时，完整内容存储到 Memorial artifacts，LLM 只看到 preview。

**修改文件**: `src/tianshu/tools/registry.py` (execute 方法)

```python
async def execute(self, name: str, args: dict, *,
                  memorial_id: str | None = None) -> ToolResult:
    tool_def = self._tools[name]
    result = await tool_def.handler(**args)

    # 持久化降级
    if (len(result.content) > tool_def.max_result_chars
            and memorial_id):
        artifact_id = await self._persist_result(
            memorial_id, name, result.content
        )
        preview = result.content[:tool_def.max_result_chars]
        return ToolResult(
            content=(
                f"{preview}\n\n"
                f"[结果已截断，完整内容 ({len(result.content)} 字符) "
                f"已保存到 artifact:{artifact_id}]"
            ),
            details={**(result.details or {}), "artifact_id": artifact_id},
            is_error=result.is_error,
        )

    return result
```

---

### 2.4 Prompt Cache 稳定性

**目标**: 确保相同任务的 prompt 前缀尽可能一致，提高 Claude API cache 命中率。

**修改文件**: `src/tianshu/executor/agent.py`

#### 2.4.1 确定性工具排序

```python
def _build_tools_for_api(self, tool_filter: list[str] | None) -> list[dict]:
    """构建工具列表，保证排序稳定"""
    tools = self.registry.get_all_definitions()

    if tool_filter:
        tools = [t for t in tools if t.name in tool_filter]

    # 按名称排序，确保每次请求的工具顺序一致
    tools.sort(key=lambda t: t.name)

    return [t.to_openai_schema() for t in tools]
```

#### 2.4.2 系统 Prompt 结构化构建

**修改文件**: `src/tianshu/persona/prompt_builder.py`

```python
def build_system_prompt(self, persona, edict, context) -> str:
    """
    按固定顺序拼接系统 prompt 各段:
    1. 基础身份 (builtin, 不变)
    2. Court 上下文 (不变)
    3. Persona Soul (不变)
    4. Persona Role (不变)
    5. Memory (缓慢变化)
    6. Skills (缓慢变化)
    7. Task Context (每次不同)

    前 4 段是 cache-friendly 前缀，尽量保持不变。
    """
    sections = [
        self._build_identity(),        # 稳定
        self._build_court_context(),   # 稳定
        self._build_soul(persona),     # 稳定
        self._build_role(persona),     # 稳定
        # ── cache 边界 ──
        self._build_memory(persona),   # 缓慢变化
        self._build_skills(persona),   # 缓慢变化
        self._build_task_context(edict),  # 每次不同
    ]

    return "\n\n".join(s for s in sections if s)
```

#### 2.4.3 消息规范化

```python
def _normalize_messages_for_api(self, messages: list[dict]) -> list[dict]:
    """发送前规范化消息:
    - 合并连续的同 role text 消息
    - 确保 tool_result 对应 tool_use（补齐缺失的 pair）
    - 移除内部元数据字段
    """
    normalized = []
    for msg in messages:
        clean = {
            "role": msg["role"],
            "content": msg["content"],
        }
        if "tool_calls" in msg:
            clean["tool_calls"] = msg["tool_calls"]
        if "tool_call_id" in msg:
            clean["tool_call_id"] = msg["tool_call_id"]
        normalized.append(clean)
    return normalized
```

---

## Phase 3: 架构层增强 (P2)

### 3.1 Agent Middleware Chain

**目标**: 将 Agent 循环中散落的横切关注点提取为可组合的 Middleware 链。

**新增文件**: `src/tianshu/executor/middleware/`

```
middleware/
├── __init__.py
├── base.py             # Middleware 基类
├── cost_tracking.py    # Token/成本追踪
├── memory_injection.py # Persona Memory 注入
├── guardrail.py        # 工具调用前置检查
├── compaction.py       # 上下文压缩（包装 Phase 1 的压缩策略）
├── audit.py            # 执行审计记录
├── budget.py           # 预算检查
└── chain.py            # Middleware 编排
```

#### 3.1.1 Middleware 基类

```python
from abc import ABC, abstractmethod

class AgentMiddleware(ABC):
    """Agent 循环 Middleware 基类"""

    @abstractmethod
    async def before_llm_call(self, state: LoopState,
                               context: MiddlewareContext) -> LoopState:
        """LLM 调用前执行，可修改 state"""
        return state

    @abstractmethod
    async def after_llm_call(self, state: LoopState,
                              response: LLMResponse,
                              context: MiddlewareContext) -> LLMResponse:
        """LLM 调用后执行，可修改 response"""
        return response

    @abstractmethod
    async def after_tool_execution(self, state: LoopState,
                                    tool_results: list[dict],
                                    context: MiddlewareContext) -> list[dict]:
        """工具执行后，可修改 tool_results"""
        return tool_results
```

#### 3.1.2 Middleware Chain

```python
class MiddlewareChain:
    def __init__(self, middlewares: list[AgentMiddleware]):
        self._middlewares = middlewares

    async def run_before_llm(self, state: LoopState,
                              context: MiddlewareContext) -> LoopState:
        for mw in self._middlewares:
            state = await mw.before_llm_call(state, context)
        return state

    async def run_after_llm(self, state: LoopState,
                             response: LLMResponse,
                             context: MiddlewareContext) -> LLMResponse:
        for mw in self._middlewares:
            response = await mw.after_llm_call(state, response, context)
        return response

    async def run_after_tools(self, state: LoopState,
                               results: list[dict],
                               context: MiddlewareContext) -> list[dict]:
        for mw in self._middlewares:
            results = await mw.after_tool_execution(state, results, context)
        return results
```

#### 3.1.3 默认 Middleware 栈

```python
def build_default_middleware_chain(app_state) -> MiddlewareChain:
    return MiddlewareChain([
        CompactionMiddleware(app_state.config),         # 上下文压缩
        CostTrackingMiddleware(app_state.cost_manager), # 成本追踪
        MemoryInjectionMiddleware(app_state.memory),    # Memory 注入
        BudgetMiddleware(app_state.cost_manager),       # 预算检查
        GuardrailMiddleware(app_state.approvals),       # 工具权限
        AuditMiddleware(app_state.auditor),             # 审计记录
    ])
```

**收益**: 新增功能通过添加 Middleware 实现，不需修改 Agent 核心循环。

---

### 3.2 分层权限系统

**目标**: 从全局 Decree 审批升级为 Persona + Tool 维度的分级权限。

**修改文件**: `src/tianshu/executor/approvals.py`

#### 3.2.1 权限决策模型

```python
from enum import StrEnum

class PermissionBehavior(StrEnum):
    ALLOW = "allow"       # 自动放行
    ASK = "ask"           # 需要人工审批（Decree）
    DENY = "deny"         # 自动拒绝

@dataclass
class PermissionRule:
    tool_name: str | None        # None = 匹配所有工具
    persona_id: str | None       # None = 匹配所有 persona
    behavior: PermissionBehavior
    source: str                  # "persona_config" | "edict_runtime" | "global"
    priority: int                # 数字越小优先级越高

class PermissionEvaluator:
    """分层权限评估器"""

    def __init__(self, rules: list[PermissionRule]):
        self._rules = sorted(rules, key=lambda r: r.priority)

    def evaluate(self, tool_name: str, tool_args: dict,
                 persona_id: str, tool_def: ToolDefinition) -> PermissionBehavior:
        """
        评估顺序:
        1. 显式 deny 规则
        2. 显式 ask 规则
        3. Tool 行为推断: is_destructive → ASK
        4. Tool 行为推断: is_read_only → ALLOW
        5. 默认: ASK
        """
        # 检查显式规则
        for rule in self._rules:
            if self._matches(rule, tool_name, persona_id):
                return rule.behavior

        # 基于 Tool 行为声明的默认策略
        if tool_def.destructive:
            return PermissionBehavior.ASK
        if tool_def.read_only:
            return PermissionBehavior.ALLOW

        return PermissionBehavior.ASK
```

#### 3.2.2 Hook 退出码语义增强

**修改文件**: `src/tianshu/executor/hooks.py`

```python
@dataclass
class HookResult:
    block: bool = False
    reason: str | None = None
    modified_args: dict | None = None

    # 新增: 精确控制
    inject_message: str | None = None    # 注入到模型上下文
    permission_override: PermissionBehavior | None = None  # 覆盖权限决策

class HookExitCode:
    """Hook 退出码协议（用于外部 hook 脚本集成）"""
    PASS = 0        # 通过，静默
    BLOCK = 2       # 阻止，inject stderr 到模型
    # 其他 = 仅展示给用户
```

---

### 3.3 韧性工程

**目标**: 建立多层容错机制。

#### 3.3.1 模型降级

**新增文件**: `src/tianshu/providers/fallback.py`

```python
@dataclass
class FallbackConfig:
    primary_model: str
    fallback_model: str
    max_consecutive_failures: int = 3  # 连续失败 N 次后降级
    cooldown_seconds: int = 60         # 降级后冷却时间

class ModelWithFallback:
    """带自动降级的 LLM 调用封装"""

    def __init__(self, llm: LLMClient, config: FallbackConfig):
        self._llm = llm
        self._config = config
        self._consecutive_failures = 0
        self._using_fallback = False
        self._fallback_since: float | None = None

    async def call(self, messages, tools=None, **kwargs) -> LLMResponse:
        model = self._select_model()

        try:
            response = await self._llm.chat(
                messages=messages, tools=tools, model=model, **kwargs
            )
            self._consecutive_failures = 0
            return response

        except (RateLimitError, ServiceUnavailableError, OverloadError) as e:
            self._consecutive_failures += 1

            if self._consecutive_failures >= self._config.max_consecutive_failures:
                self._using_fallback = True
                self._fallback_since = time.monotonic()
                logger.warning(
                    f"模型降级: {self._config.primary_model} → "
                    f"{self._config.fallback_model} "
                    f"(连续失败 {self._consecutive_failures} 次)"
                )
                return await self._llm.chat(
                    messages=messages, tools=tools,
                    model=self._config.fallback_model, **kwargs
                )
            raise

    def _select_model(self) -> str:
        if self._using_fallback:
            elapsed = time.monotonic() - (self._fallback_since or 0)
            if elapsed > self._config.cooldown_seconds:
                self._using_fallback = False
                self._consecutive_failures = 0
                logger.info(f"模型恢复: → {self._config.primary_model}")
                return self._config.primary_model
            return self._config.fallback_model
        return self._config.primary_model
```

#### 3.3.2 重试增强

**修改文件**: `src/tianshu/executor/retry.py`

```python
# 现有的 retry 基于 edict 级别重建 memorial
# 新增: Agent 循环内的细粒度重试

RETRY_DELAY_BASE_MS = 500
RETRY_DELAY_MAX_MS = 30_000
RETRY_JITTER_RATIO = 0.25

def calculate_retry_delay(attempt: int) -> float:
    """指数退避 + jitter"""
    base = min(
        RETRY_DELAY_BASE_MS * (2 ** (attempt - 1)),
        RETRY_DELAY_MAX_MS,
    )
    jitter = random.uniform(0, base * RETRY_JITTER_RATIO)
    return (base + jitter) / 1000  # 转为秒
```

#### 3.3.3 Denial Tracking

**新增**: 在权限系统中跟踪连续拒绝

```python
@dataclass
class DenialTracker:
    consecutive: int = 0
    total: int = 0
    MAX_CONSECUTIVE: int = 3
    MAX_TOTAL: int = 20

    def record_denial(self):
        self.consecutive += 1
        self.total += 1

    def record_approval(self):
        self.consecutive = 0

    @property
    def should_escalate(self) -> bool:
        """连续拒绝过多时，自动升级到人工审批"""
        return (self.consecutive >= self.MAX_CONSECUTIVE
                or self.total >= self.MAX_TOTAL)
```

---

## Phase 4: 生态扩展 (P3)

### 4.1 MCP 协议支持

**目标**: 集成 Model Context Protocol，扩展工具生态。

**新增目录**: `src/tianshu/mcp/`

```
mcp/
├── __init__.py
├── client.py           # MCP 客户端封装
├── manager.py          # 多 server 生命周期管理
├── tool_projection.py  # MCP 工具映射到 ToolRegistry
├── config.py           # MCP server 配置
└── auth.py             # OAuth 支持
```

#### 4.1.1 核心设计

```python
class MCPManager:
    """管理多个 MCP Server 的连接生命周期"""

    async def connect_servers(self, configs: list[MCPServerConfig]):
        """批量连接 MCP servers (并行, max 3 concurrent)"""
        ...

    async def get_tools(self) -> list[ToolDefinition]:
        """收集所有已连接 server 的工具，映射为 ToolDefinition"""
        ...

    async def call_tool(self, server: str, tool: str, args: dict) -> ToolResult:
        """调用指定 server 的工具"""
        ...
```

**工具命名**: `mcp__{server_name}__{tool_name}`（避免与内置工具冲突）

**工具合并**: 内置工具优先级高于 MCP 工具（同名时内置胜出）

**排序**: 内置工具按名排序 + MCP 工具按名排序，拼接（保证 prompt cache 稳定）

#### 4.1.2 配置

```yaml
# tianshu_config.yaml
mcp_servers:
  - name: "filesystem"
    transport: "stdio"
    command: "npx"
    args: ["-y", "@anthropic/mcp-filesystem"]
    enabled: true

  - name: "database"
    transport: "sse"
    url: "http://localhost:3001/mcp"
    enabled: true
```

---

### 4.2 Sandbox 沙箱隔离

**目标**: 为 Agent 工具执行提供文件系统隔离。

**新增目录**: `src/tianshu/sandbox/`

```
sandbox/
├── __init__.py
├── provider.py         # Sandbox 接口
├── local.py            # 本地文件系统映射（默认）
├── docker.py           # Docker 容器隔离
└── path_translator.py  # 虚拟路径 ↔ 物理路径
```

#### 4.2.1 核心接口

```python
class SandboxProvider(Protocol):
    async def acquire(self, edict_id: str) -> Sandbox: ...
    async def release(self, sandbox: Sandbox): ...

@dataclass
class Sandbox:
    id: str
    workspace: Path     # 工作目录
    uploads: Path       # 上传文件
    outputs: Path       # 输出产物

class LocalSandboxProvider:
    """基于本地文件系统的沙箱（默认实现）"""

    def __init__(self, base_dir: Path):
        self._base = base_dir

    async def acquire(self, edict_id: str) -> Sandbox:
        root = self._base / edict_id
        workspace = root / "workspace"
        uploads = root / "uploads"
        outputs = root / "outputs"
        for d in (workspace, uploads, outputs):
            d.mkdir(parents=True, exist_ok=True)
        return Sandbox(id=edict_id, workspace=workspace,
                       uploads=uploads, outputs=outputs)
```

---

### 4.3 流式工具执行

**目标**: 在 LLM 流式输出过程中，提前开始执行已检测到的工具调用。

**优先级**: P3（依赖 LiteLLM 的流式 API 支持和工具并发分区）

**新增文件**: `src/tianshu/executor/streaming_executor.py`

```python
class StreamingToolExecutor:
    """在 LLM 流式响应过程中提前执行工具"""

    def __init__(self, registry: ToolRegistry, max_concurrent: int = 5):
        self._registry = registry
        self._pending: list[asyncio.Task] = []
        self._results: dict[str, ToolResult] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)

    def on_tool_detected(self, tool_call: dict):
        """流式解析到完整的 tool_call 时调用"""
        tool_def = self._registry.get_definition(tool_call["name"])
        if tool_def and tool_def.concurrency_safe:
            # 只读工具立即开始执行
            task = asyncio.create_task(self._execute(tool_call))
            self._pending.append(task)

    async def _execute(self, tool_call: dict):
        async with self._semaphore:
            result = await self._registry.execute(
                tool_call["name"], tool_call["args"]
            )
            self._results[tool_call["id"]] = result

    async def get_all_results(self) -> dict[str, ToolResult]:
        """等待所有已启动的工具完成"""
        await asyncio.gather(*self._pending, return_exceptions=True)
        return self._results
```

**集成方式**: 当 LiteLLM 支持流式 tool_call 解析时，在流式回调中调用 `on_tool_detected`。非流式模式下退化为普通的 `execute_tool_batches`。

---

## 影响文件清单

### Phase 1 (新增/修改)

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | `executor/exit_reason.py` | ExitReason 枚举 |
| 新增 | `executor/loop_state.py` | 不可变循环状态 |
| 新增 | `executor/compaction/__init__.py` | 压缩模块 |
| 新增 | `executor/compaction/micro.py` | Micro compact |
| 新增 | `executor/compaction/auto.py` | Auto compact |
| 新增 | `executor/compaction/reactive.py` | Reactive compact |
| 新增 | `executor/compaction/strategy.py` | 策略编排 |
| 新增 | `executor/compaction/token_estimator.py` | Token 估算 |
| **修改** | `executor/agent.py` | 核心循环重写 |

### Phase 2 (新增/修改)

| 操作 | 文件 | 说明 |
|------|------|------|
| **修改** | `tools/types.py` | 添加行为声明字段 |
| **修改** | `tools/registry.py` | ToolDefinition 增强 + 持久化降级 |
| **修改** | `tools/builtins.py` | 内置工具行为声明 |
| 新增 | `executor/tool_executor.py` | 并发分区执行器 |
| **修改** | `persona/prompt_builder.py` | 结构化 prompt 构建 |

### Phase 3 (新增/修改)

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | `executor/middleware/` | 完整 middleware 目录 |
| **修改** | `executor/approvals.py` | 分层权限评估器 |
| **修改** | `executor/hooks.py` | Hook 退出码增强 |
| 新增 | `providers/fallback.py` | 模型降级 |
| **修改** | `executor/retry.py` | 重试增强 |

### Phase 4 (新增)

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | `mcp/` | MCP 完整目录 |
| 新增 | `sandbox/` | Sandbox 完整目录 |
| 新增 | `executor/streaming_executor.py` | 流式工具执行 |

---

## 风险与约束

### 技术风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| Auto Compact 的 LLM 调用增加成本 | 中 | 使用低成本模型（gpt-4o-mini）做总结; 熔断器限制连续失败 |
| 工具并发执行可能导致竞态 | 中 | fail-closed 默认值; 只有显式标记 concurrency_safe 的工具才并行 |
| 不可变 State 增加内存分配 | 低 | messages 用 tuple 共享引用; 实测影响可忽略 |
| Middleware Chain 增加调用链路 | 低 | 每个 middleware 是 O(1) 操作; 异常不会跨 middleware 传播 |
| MCP Server 连接不稳定 | 中 | 重连机制 + 指数退避; 工具在 server 断开时自动标记不可用 |

### 兼容性

- Phase 1-2 对外部 API 无影响（AgentResult 新增字段是向后兼容的）
- Phase 3 Middleware 是 Agent 内部重构，不影响 EventBus 接口
- Phase 4 MCP 是可选扩展，不影响现有工具系统
- 所有 Phase 可独立部署，无强依赖

### 测试策略

- Phase 1: 构造 20+ 轮的长任务场景，验证压缩策略和恢复链
- Phase 2: 构造多工具调用场景，验证并发分区正确性
- Phase 3: 单元测试每个 Middleware; 集成测试 Chain 编排
- Phase 4: Mock MCP Server 测试连接/断开/重连
