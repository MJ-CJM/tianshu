# 证据索引与复核边界

> 本页记录本轮研究使用的源码快照、证据等级和可漂移边界。目标设计结论不能反向充当
> 当前能力证明。
>
> **实现补充（2026-08-26）**：下列 commit 与环境信息保留为原始调研快照，不覆盖历史证据。
> 当前 checkpoint 另以 P4a PR #107（merge `b94d4846`，CI 6/6）和已合入的 P4b PR #109
>（merge `a8a03071`）为准。
> P4b 事实入口为 `models/run_assignment.py`、`storage/migrations.py`、
> `storage/evolution_repo.py`、`universe/router.py`、`evolution/runtime_context.py`、
> `evidence/service.py` 及对应 multi-subject/durable-schema/Evidence 测试。最终本地门禁为后端
> 5270 passed、2 skipped、24 slow deselected，Web 347 passed；静态检查与生产构建通过。
> P5 已由 PR #111 合入 `feat/plugin-v1`（merge `567b028e`）。P6 已由 PR #114
> 合入同一集成分支（merge `8f32cc4c`，CI 6/6），事实入口与验证路径另列于下文。
> P7 当前开发完成、PR 待创建；本地聚焦回归已通过，完整门禁只能以未来
> PR 的实际检查为准，不从 P6 或历史阶段数字外推。

## 1. 核验时间与源码快照

| 项目 | 路径/仓库 | 固定快照 | 用途 |
|---|---|---|---|
| Tianshu | 当前仓库 | 对比调研 `b1c55336f82bd70b7c638229e4ecefac461f5f88`；落盘复核 `88462b2a6e46ae750e07697877613b9820bb5103` | 当前实现与迁移基础 |
| DeepSeek Harness | `deepseek-ai/deepseek-harness` | `47f943859bef60e4160492346772ded9b24f765a` | Cordis、Effect、Session generation、HMR 边界 |
| Pi | `earendil-works/pi` | `d3ab2af969d64997338253c9151190aa1bc33580` | Package、Resource、Extension、reload 与 trust 边界 |

研究完成于 2026-08-20，文档落盘复核于 2026-08-21。两个 Tianshu commit 的 Git tree
一致；后者是合并提交。上游项目后续行为可能变化；引用固定 commit 是为了保留本次结论的
可复现性。仓库内相对链接用于阅读当前 checkout；固定 Tianshu 快照见
[`88462b2a`](https://github.com/MJ-CJM/tianshu/tree/88462b2a6e46ae750e07697877613b9820bb5103)。

落盘时执行了聚焦回归：

```bash
uv run pytest -q \
  tests/gateway/test_plugin_manifest_api.py \
  tests/evolution/test_candidate_adapters.py
```

结果为 `64 passed, 5 warnings`。两项插件 API 测试验证 manifest-only 和
install/activate fail-closed；其余 62 个收集项（含普通测试与多组参数化 case）覆盖 Candidate
adapter 边界。告警均来自第三方依赖弃用提示，没有测试失败。环境为 macOS 26.4 arm64、
uv 0.9.27、项目 Python 3.12.12、pytest 9.0.3，HEAD `88462b2a`。

## 2. 证据等级

| 等级 | 可以证明什么 | 不可以证明什么 |
|---|---|---|
| 当前源码/测试 | 当前 checkout 中实际存在的契约和行为 | 未运行环境下的外部系统保证 |
| 上游固定源码 | 指定 commit 的代码路径和设计 | 新版仍保持相同行为 |
| 官方文档/规范 | 官方声明的协议和机制 | 本地集成已经正确实现 |
| 论文 | 给定数据集、预算和环境中的实验结果 | 生产安全、泛化或自动上线能力 |
| 架构推断 | 从多项证据得到的目标建议 | 已实现、已验证或已被团队接受 |

## 3. Tianshu 当前事实入口

### Plugin catalog

- [`src/tianshu/plugins/manifest.py`](../../../src/tianshu/plugins/manifest.py)
- [`src/tianshu/plugins/loader.py`](../../../src/tianshu/plugins/loader.py)
- [`src/tianshu/plugins/api.py`](../../../src/tianshu/plugins/api.py)
- [`src/tianshu/bootstrap/wiring_scheduler.py`](../../../src/tianshu/bootstrap/wiring_scheduler.py)
- [`src/tianshu/gateway/providers_api.py`](../../../src/tianshu/gateway/providers_api.py)
- [`tests/gateway/test_plugin_manifest_api.py`](../../../tests/gateway/test_plugin_manifest_api.py)

### Evolution 与归因

- [`src/tianshu/models/evolution_candidate.py`](../../../src/tianshu/models/evolution_candidate.py)
- [`src/tianshu/models/run_assignment.py`](../../../src/tianshu/models/run_assignment.py)
- [`src/tianshu/evolution/candidate_service.py`](../../../src/tianshu/evolution/candidate_service.py)
- [`src/tianshu/evolution/promotion.py`](../../../src/tianshu/evolution/promotion.py)
- [`src/tianshu/storage/evolution_repo.py`](../../../src/tianshu/storage/evolution_repo.py)
- [`src/tianshu/bootstrap/wiring_skills.py`](../../../src/tianshu/bootstrap/wiring_skills.py)

### P5 Pi EXECUTOR 治理垂直切片

- [`src/tianshu/evolution/adapters/executor.py`](../../../src/tianshu/evolution/adapters/executor.py)
- [`src/tianshu/evolution/adapters/executor_promotion.py`](../../../src/tianshu/evolution/adapters/executor_promotion.py)
- [`src/tianshu/evolution/executor_drift.py`](../../../src/tianshu/evolution/executor_drift.py)
- [`src/tianshu/storage/executor_generation_authority_repo.py`](../../../src/tianshu/storage/executor_generation_authority_repo.py)
- [`src/tianshu/bootstrap/wiring_executor.py`](../../../src/tianshu/bootstrap/wiring_executor.py)
- [`tests/integration/test_executor_evolution_demo.py`](../../../tests/integration/test_executor_evolution_demo.py)
- [`tests/evolution/test_executor_promotion_service.py`](../../../tests/evolution/test_executor_promotion_service.py)
- [`tests/evolution/test_executor_authority_recovery.py`](../../../tests/evolution/test_executor_authority_recovery.py)
- [`tests/evolution/test_executor_drift_scanner.py`](../../../tests/evolution/test_executor_drift_scanner.py)
- [`tests/universe/test_executor_generation_routing.py`](../../../tests/universe/test_executor_generation_routing.py)
- [`tests/storage/test_executor_generation_authority_repo.py`](../../../tests/storage/test_executor_generation_authority_repo.py)
- [`tests/architecture/test_executor_evolution_boundaries.py`](../../../tests/architecture/test_executor_evolution_boundaries.py)

### P6 process snapshot generation 与 strict binding

- [`src/tianshu/evolution/process_snapshot.py`](../../../src/tianshu/evolution/process_snapshot.py)
- [`src/tianshu/models/runtime_generation.py`](../../../src/tianshu/models/runtime_generation.py)
- [`src/tianshu/storage/generation_repo.py`](../../../src/tianshu/storage/generation_repo.py)
- [`src/tianshu/bootstrap/wiring_snapshot.py`](../../../src/tianshu/bootstrap/wiring_snapshot.py)
- [`src/tianshu/universe/router.py`](../../../src/tianshu/universe/router.py)
- [`src/tianshu/application/evolution_view.py`](../../../src/tianshu/application/evolution_view.py)
- [`tests/evolution/test_process_snapshot.py`](../../../tests/evolution/test_process_snapshot.py)
- [`tests/storage/test_process_generation_repo.py`](../../../tests/storage/test_process_generation_repo.py)
- [`tests/executor/test_generation_scope_isolation.py`](../../../tests/executor/test_generation_scope_isolation.py)
- [`tests/universe/test_snapshot_binding.py`](../../../tests/universe/test_snapshot_binding.py)
- [`tests/test_bootstrap_smoke.py`](../../../tests/test_bootstrap_smoke.py)
- [`web/src/pages/EvolutionCenterPage.test.tsx`](../../../web/src/pages/EvolutionCenterPage.test.tsx)

### P7 Skills 每 run 不可变视图

Issue #115 的当前实现仅覆盖 Skills，无数据迁移；`off` / `shadow` / `enforce`
分别表示完全兼容、构建对账但读 live、绑定视图且失败关闭。开发已完成，
PR 待创建；下列是当前 checkout 的事实入口：

- [`src/tianshu/models/frozen_content.py`](../../../src/tianshu/models/frozen_content.py)
- [`src/tianshu/skills/loader.py`](../../../src/tianshu/skills/loader.py)
- [`src/tianshu/evolution/runtime_context.py`](../../../src/tianshu/evolution/runtime_context.py)
- [`src/tianshu/universe/router.py`](../../../src/tianshu/universe/router.py)
- [`src/tianshu/application/run_dispatcher.py`](../../../src/tianshu/application/run_dispatcher.py)
- [`src/tianshu/application/managed_run_ingress.py`](../../../src/tianshu/application/managed_run_ingress.py)
- [`src/tianshu/application/scheduled_runs.py`](../../../src/tianshu/application/scheduled_runs.py)
- [`src/tianshu/scheduler/scheduler.py`](../../../src/tianshu/scheduler/scheduler.py)
- [`src/tianshu/storage/unit_of_work.py`](../../../src/tianshu/storage/unit_of_work.py)
- [`src/tianshu/models/system_audit.py`](../../../src/tianshu/models/system_audit.py)
- [`src/tianshu/storage/system_snapshot_repo.py`](../../../src/tianshu/storage/system_snapshot_repo.py)
- [`src/tianshu/bootstrap/wiring_skills.py`](../../../src/tianshu/bootstrap/wiring_skills.py)
- [`src/tianshu/evolution/promotion.py`](../../../src/tianshu/evolution/promotion.py)
- [`tests/skills/test_frozen_view.py`](../../../tests/skills/test_frozen_view.py)
- [`tests/universe/test_frozen_skill_binding.py`](../../../tests/universe/test_frozen_skill_binding.py)
- [`tests/test_frozen_content_wiring.py`](../../../tests/test_frozen_content_wiring.py)
- [`tests/application/test_run_dispatcher_lifecycle.py`](../../../tests/application/test_run_dispatcher_lifecycle.py)
- [`tests/application/test_managed_run_ingress.py`](../../../tests/application/test_managed_run_ingress.py)
- [`tests/application/test_managed_scheduler.py`](../../../tests/application/test_managed_scheduler.py)
- [`tests/storage/test_unit_of_work.py`](../../../tests/storage/test_unit_of_work.py)
- [`tests/storage/test_system_snapshot_repo.py`](../../../tests/storage/test_system_snapshot_repo.py)
- [`tests/evolution/test_promotion_fail_closed.py`](../../../tests/evolution/test_promotion_fail_closed.py)

prebind/重启边界由 frozen view `source_digest` 与 run 的 SystemSnapshot `skills` 组件
对账：shadow 漂移审计后保持 live，enforce 在 runner 前以
`skills_view_unavailable` 失败关闭；不静默配对旧 snapshot 与新 view。跨重启
耐久回放旧内容所需的 artifact-backed `skills_view` 属于 P7b，已明确延期。
审计语义分开：只有真实 source digest 不一致才写 outcome=succeeded 的
`skills_view_drift`；视图工厂、整体捕获、模型校验等致命失败，以及 enforce 下已解码
Skills 身份缺失，写 outcome=failed 的 `skills_view_binding_failed`。shadow 身份缺失只跳过
对账；单个 SKILL.md 解析异常沿用 loader warning + skip，不会仅因此写 failed 事件。
两类事件都以 audit + outbox 原子持久，且只保留 opaque id/digest，不写 Skill 内容或原始错误。
这里的“身份缺失”指已成功解码后没有可对账的 Skills snapshot 身份。持久
SystemSnapshot/run binding 结构损坏会更早沿用 P6 稳定码：strict 为
`system_snapshot_unavailable`，否则为 `generation_binding_unavailable`；不会被 P7
重分类为 `skills_view_unavailable`。

并发捕获由 loader 通过已打开目录 fd 读取 SKILL.md/资源；搜索路径祖先只记录
`dev/ino/mode` identity，搜索根及捕获树内文件/目录记录
`dev/ino/mode/size/mtime_ns/ctime_ns` stability witness，避免树外 sibling churn 误伤，并把按名称排序的 injected Skills 与
injected generation 纳入全量 capture。只有连续两次 capture（含 witness）相等才接受；最多
三轮，持续 churn 后 fail closed，因此不会把旧树和新树混入同一 view。捕获同时拒绝搜索路径
组件、Skill 目录/成员及嵌套资源中的 symlink。requirements、原始字节大小上限、`load_all`、
metadata、injected、fallback、覆盖顺序与字符预算均有 live/frozen 对照回归；requirements
环境判定形成的 `load-all-eligible` 也进入 source identity。

absent 兼容语义由 assignment 决定：selected base/champion absent 只移除目标层并显露低层，
selected challenger 或 unknown absent 延续 tombstone 并隐藏低层。新的 absent candidate 在
start-canary、promote、activate 前都以 `skill_absence_requires_durable_tombstone` 拒绝；durable
global tombstone 延期 P7b。`SkillsWatcher` 当前统一使用 polling observer（包括 frozen wiring），
避免 macOS FSEvents 在原子目录交换期间崩溃；frozen callback 只 invalidate + notify，legacy
无 callback 的 reload 行为保留。

enforce prebind 只有 audit+outbox 成功落入 caller-owned UoW 才登记 post-commit failure；证据
失败则直接退出并回滚整笔事务。已提交的 scheduled fire 由
`ScheduledFireBindingUnavailable.prepared` 交还 scheduler：循环/cursor/initial root 按 durable
truth 收口并显式唤醒 reconciler；提交前失败不推进 cursor、不清 initial root、也不 dispatch。
`requires_frozen_prebind_retry()` 只认可 claimable attempt 的同-key P7 outbox marker，恢复成功
原子写 `skills_view_binding_recovered`，终态精确重放不重新冻结。生产 prebind 与 dispatch 是
两个可跨进程的重建阶段，每个阶段最多 freeze 一次。promotion invalidator 与 frozen flag
无关；exchange 后 desired/no-op 重试及 `verify_rollback` 命中都会 invalidate。上述承诺是本地
POSIX 普通写者模型，依赖可靠 ctime/stat，不抵抗特权写者，也不覆盖 ctime 不可靠文件系统。

### 现有架构与决策

- [领域模型](../domain-model.md)
- [当前插件扩展实现与支持边界](current-plugin-state.md)
- [自改进当前视图](../growth/)
- [位面当前边界](../universe/evolution.md)
- [Evolution 默认关闭、代码层手动](../../adr/0004-evolution-off-by-default-unlock-by-memorial.md)
- [静态 DAG](../../adr/0009-static-dag-no-dynamic-graph.md)
- [Keqing 执行边界](../../adr/0011-keqing-external-executor-shadow-snapshot.md)

## 4. DeepSeek Harness 固定源码

- [Architecture](https://github.com/deepseek-ai/deepseek-harness/blob/47f943859bef60e4160492346772ded9b24f765a/docs/architecture.md)
- [Lifecycle and Effects](https://github.com/deepseek-ai/deepseek-harness/blob/47f943859bef60e4160492346772ded9b24f765a/docs/cordis-tutorial/02-lifecycle-and-effects.md)
- [Agent lifecycle](https://github.com/deepseek-ai/deepseek-harness/blob/47f943859bef60e4160492346772ded9b24f765a/docs/agent-lifecycle.md)
- [Preset generation behavior](https://github.com/deepseek-ai/deepseek-harness/blob/47f943859bef60e4160492346772ded9b24f765a/apps/cli/tests/web-agent-presets.e2e.ts#L498-L516)
- [Web HMR disabled](https://github.com/deepseek-ai/deepseek-harness/blob/47f943859bef60e4160492346772ded9b24f765a/packages/bundle/web-app/cordis.patch.yml)
- [Headless HMR disabled](https://github.com/deepseek-ai/deepseek-harness/blob/47f943859bef60e4160492346772ded9b24f765a/packages/bundle/headless/cordis.patch.yml)
- [`tool-cordis`](https://github.com/deepseek-ai/deepseek-harness/blob/47f943859bef60e4160492346772ded9b24f765a/packages/extensions/tool-cordis/README.md)

## 5. Pi 固定源码

- [Packages](https://github.com/earendil-works/pi/blob/d3ab2af969d64997338253c9151190aa1bc33580/packages/coding-agent/docs/packages.md)
- [Extensions](https://github.com/earendil-works/pi/blob/d3ab2af969d64997338253c9151190aa1bc33580/packages/coding-agent/docs/extensions.md)
- [Security](https://github.com/earendil-works/pi/blob/d3ab2af969d64997338253c9151190aa1bc33580/packages/coding-agent/docs/security.md)
- [Extension loader](https://github.com/earendil-works/pi/blob/d3ab2af969d64997338253c9151190aa1bc33580/packages/coding-agent/src/core/extensions/loader.ts)
- [Extension runner](https://github.com/earendil-works/pi/blob/d3ab2af969d64997338253c9151190aa1bc33580/packages/coding-agent/src/core/extensions/runner.ts)
- [Agent session reload](https://github.com/earendil-works/pi/blob/d3ab2af969d64997338253c9151190aa1bc33580/packages/coding-agent/src/core/agent-session.ts#L2610-L2634)

## 6. 行业一手资料

### 控制面与运行时

- [Codex Plugins](https://developers.openai.com/codex/plugins)
- [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)
- [Kubernetes Controllers](https://kubernetes.io/docs/concepts/architecture/controller/)
- [Kubernetes API Conventions](https://github.com/kubernetes/community/blob/master/contributors/devel/sig-architecture/api-conventions.md)
- [Envoy xDS Protocol](https://www.envoyproxy.io/docs/envoy/latest/api-docs/xds_protocol)
- [Temporal Workflow Execution](https://docs.temporal.io/workflow-execution)
- [Temporal Worker Versioning](https://docs.temporal.io/production-deployment/worker-deployments/worker-versioning/)
- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangSmith Assistants](https://docs.langchain.com/langsmith/assistants)
- [Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/overview/)

### Artifact、供应链与策略

- [OCI Descriptor](https://github.com/opencontainers/image-spec/blob/v1.1.1/descriptor.md)
- [OCI Manifest](https://github.com/opencontainers/image-spec/blob/v1.1.1/manifest.md)
- [TUF Specification](https://theupdateframework.github.io/specification/latest/)
- [SLSA Provenance](https://slsa.dev/spec/v1.2/provenance)
- [OPA Documentation](https://www.openpolicyagent.org/docs/latest/)

### Capability 与隔离

- [MCP Architecture](https://modelcontextprotocol.io/docs/learn/architecture)
- [WebAssembly Component Model](https://github.com/WebAssembly/component-model/blob/main/design/mvp/Explainer.md)
- [Wasmtime Security](https://docs.wasmtime.dev/security.html)
- [Erlang Code Loading](https://www.erlang.org/doc/system/code_loading.html)
- [Erlang Release Handling](https://www.erlang.org/doc/system/release_handling.html)

### Agent 自改进研究

- [Prime Agent](https://github.com/PrimeIntellect-ai/prime-agent)
- [Continual Harness](https://arxiv.org/abs/2605.09998)
- [Reflexion](https://arxiv.org/abs/2303.11366)
- [Voyager](https://arxiv.org/abs/2305.16291)
- [GEPA](https://arxiv.org/abs/2507.19457)
- [AlphaEvolve](https://arxiv.org/abs/2506.13131)
- [Darwin Gödel Machine](https://arxiv.org/abs/2505.22954)
- [SEAL](https://arxiv.org/abs/2506.10943)

## 7. 已收敛的文档漂移（2026-08-25）

本轮在同一文档校正中收敛了三类已知漂移：

- 根 `CONTEXT.md` 的旧产品版本口径已按当前发布事实更新；
- Universe 不再描述为可“人工切换” live：snapshot/branch/diff/archive/restore/eval 保留，
  switch/rollback/promote-code 继续 fail closed，Legacy champion 不是生产 active pointer；
- `domain-model.md` 与 `persona/prompt-builder.md` 不再把“运行时 SOUL 演化”写成当前已接通能力；
  Persona/SOUL 当前只有编辑或实验性变异/推荐。P5 后真实 activation/rollback adapter
  覆盖 Skill Candidate 与 `keqing:pi` EXECUTOR Candidate；后者不是通用 PluginSet 能力。

这些修订只校正文档事实，不把目标设计反向声明为当前实现；后续仍按下一节规则复核。

## 8. 后续复核规则

在实施任何阶段前，应重新核对：

1. Tianshu 当前 HEAD、能力矩阵、Plugin/Evolution 源码与测试；
2. DeepSeek Harness/Pi 新版本是否改变 lifecycle、reload 或安全边界；
3. 外部规范的正式版本与 breaking changes；
4. 目标术语是否已由 ADR 和 `CONTEXT.md` 接受；
5. 新能力是否具备运行测试、故障注入和 Evidence，而不只是 manifest 或 API 字段。
