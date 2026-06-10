# 上下文压缩

## 1. 设计意图

长对话会逼近模型 context limit，触发昂贵的 LLM 摘要或直接溢出报错。天枢的策略是**分层兜底**：先用零成本的结构化收缩（micro）、再用阈值预防的 LLM 摘要（auto）、最后才用溢出后的救急压缩（reactive）。原则是「先便宜后昂贵、先预防后补救」，把昂贵的 LLM 摘要尽量推迟到必要时。

## 2. 三层对照

| 层 | 触发时机 | 成本 | 策略 |
|---|---|---|---|
| micro | 每轮开头（预防性） | 零（无 LLM） | 对非最近 `keep_recent` 轮的 tool 结果做截断 |
| auto | token 估算超阈值 | 一次 LLM 调用 | 摘要中段消息，保留首部+尾部 |
| reactive | 捕获 context overflow 异常 | 0~1 次 LLM | 先激进 micro，不够再退 auto |

## 3. micro compact

零成本、每轮触发。只截断「非最近 `keep_recent`（默认 4）条」的 tool-role 消息中超过最小长度（`_TRUNCATE_MIN_CHARS=200`）的内容，保留近期工具结果完整。无可截断内容时直接返回原 state（不产生新对象）。返回经 `with_recovery("micro_compact", …)` 的新 state——iteration 不变。

## 4. auto compact

接近阈值时预防性触发。判定 `should_auto_compact`：消息数 > 8 且估算 token 超过 `context_limit × COMPACT_THRESHOLD_RATIO`（0.75）。

压缩策略：
1. 切分 `head = messages[:1]` / `middle` / `tail = messages[-PRESERVE_TAIL:]`（保留尾 6 条）。
2. `_pre_compress_tool_results`：把 middle 里的 tool 消息先压成单行结构摘要（形状不内容），减少喂给 LLM 的 token。
3. `_extract_existing_summary`：若 middle 已有上一次摘要，改用「更新摘要」prompt 合并，避免摘要套摘要膨胀。
4. LLM 产出摘要 → 拼成 `head + [摘要消息] + tail`，经 `with_compacted` 返回。

摘要消息带前缀 `[以下是之前对话的压缩摘要，不要回复此消息]`，既作标记又防模型误把摘要当指令回复。

## 5. reactive compact

仅在主循环捕获到 context overflow 异常时调用，两步救急：
1. 激进 micro compact（`keep_recent=2`）；若估算 token ≤ `context_limit × 0.9` 即足够，直接返回。
2. 仍超限 → 退到 auto compact；auto 也失败则返回 `None`，主循环据此终止为 `ExitReason.CONTEXT_OVERFLOW`。

## 6. token 估算契约

`estimate_tokens` 不调用真实 tokenizer，用 `len(content) // 3` 的保守估算（CJK/Latin 混合下故意高估），宁可早压缩也不冒溢出风险。支持 str 与多模态 list（取其中 text 块）两种 content 形态。

## 7. 边界与已知约束

- context limit 当前由调用方传入（Agent 侧默认写死，后续可从 provider metadata 派生）。
- auto 的连续失败熔断（`MAX_CONSECUTIVE_FAILURES`）尚为 TODO，目前每次溢出独立尝试。
- 压缩只动 messages，不丢弃 system 与首尾——保证身份与近期上下文不被摘要吞掉。

**相关实现**：[../../impl/agent/](../../impl/agent/)
