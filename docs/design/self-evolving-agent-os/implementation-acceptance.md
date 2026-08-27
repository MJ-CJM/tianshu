# 自进化 Agent OS 实现收口与验收边界

> **Status: Current source fact（Issue #119 实现分支）。**
> 本页只汇总当前代码已经成立的事实；第三方通用 PluginHost、P7b 跨重启旧 Skills 内容回放
> 以及生产 shadow→enforce 运营翻转仍不在“已完成”范围。

## 1. 结论

P0–P7 与 X1–X5 的计划内功能链已经落地；Issue #119 补上最终验收发现的架构债：

- ADR-0013 登记的九类存量反向依赖全部清零；
- import-linter 从部分三层/forbidden 守卫收紧为完整四层；
- 移动的 contract、异常、ContextVar 与路由类均保留旧路径兼容，并与 canonical 对象同一；
- P6 新增故障注入，证明非 strict 漂移审计即使“写后抛错”，也只回滚审计 SAVEPOINT，
  actual snapshot 与 generation pointer 仍原子推进；
- X4/X5 文档数字区分“合入时快照”和“当前动态清单”，避免把历史统计冒充长期事实。

## 2. 完整依赖层

从上到下只能单向依赖：

1. `gateway / executor / scheduler / bootstrap / universe`
2. `application / evolution / evidence / plugins`
3. `storage / secrets / memory / persona / skills`
4. `kernel / models / config / bus`

唯一 ignore 是 `kernel.ambient -> persona.model` 的 `TYPE_CHECKING` 标注。
`tests/architecture/test_layer_contract.py` 同时锁定层表、唯一 ignore 和兼容对象同一性。

## 3. Canonical owner 与兼容入口

| 能力 | Canonical owner | 兼容入口 |
|---|---|---|
| Challenger 路由与 run binding | `application/runtime_router.py` | `universe/router.py` |
| Attempt authority / suspension ContextVar | `application/managed_attempt.py` | `application/run_dispatcher.py`、`executor/managed_tools.py` |
| Evidence contracts | `models/evidence.py` | `evidence/models.py` |
| Evolution gate DTO | `models/evolution_gate.py` | `evolution/gates.py` |
| Evolution runtime ContextVar | `models/runtime_context.py` | `evolution/runtime_context.py` |
| Workspace policy validation | `models/workspace_policy.py` | `executor/workspace_policy.py` |
| Tool definition | `models/tool_definition.py` | `tools/registry.py` |
| Generation errors/recovery/scope | `models/executor_generation.py` | `executor.adapters`、`executor.generation_controller`、`executor.keqing.generation` |

Evolution 不再导入具体 `GenerationController`、`PiReleaseMaterializer` 或
`ExecutorAdapterRegistry`，而通过 `evolution/executor_ports.py` 的 consumer-owned Protocol
接收装配对象。Evidence 不再导入 Executor catalog，而由 `bootstrap/wiring_storage.py` 显式注入
manifest provider。

## 4. 已完成与未完成的边界

已完成的是治理微内核、snapshot/generation/assignment、Pi EXECUTOR 垂直切片、Skills 每 run
冻结以及对应恢复与架构守卫。仍未完成的是：

- 第三方插件动态安装、依赖解析、entry point import、隔离运行和通用激活；
- Persona、Prompt、Provider 的 per-run frozen view；
- artifact-backed Skills 旧字节跨进程回放与 durable global tombstone（P7b）；
- 生产环境从 shadow 翻到 enforce 的观察窗口和人工运营 Gate；
- 真实 Pi 二进制的生产 canary 验证；仓库 demo 验证的是治理与故障恢复机械。

因此准确表述是：**Agent OS 的治理与代际基础已经闭环，但通用第三方 PluginHost 和生产运营
验收仍是下一层工作，不能由当前源码测试代替。**
