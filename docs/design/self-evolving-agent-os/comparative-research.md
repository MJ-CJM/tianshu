# 对比调研与业界模式

> **文档性质：带时间和代码快照的研究结论，不是当前发布承诺。**
> 详细来源和证据等级见 [source-map.md](source-map.md)。
>
> **实现进展（2026-08-27）**：上游比较仍按固定快照成立。Tianshu 已合入 P1/P3/P4a；
> P4b PR #109 与 P5 PR #111 已合入 `feat/plugin-v1`。P5 完成 Pi EXECUTOR Candidate、
> 精确 generation authority、canary、Decision、换代与回滚垂直切片；当前 P6 checkout
> 完成 process snapshot 启动代际、strict run binding 与只读投影。冻结的
> `LegacyRunAssignmentV1` / `RunAssignmentV1` 继续承载 fresh root 的 0/1 旧投影；V34
> `RunAssignmentSetV1` 从 singleton 起承载 per-subject 选择，N>1 时旧表只留 legacy 投影。
> 因此本文早期“仅单 assignment、缺 per-subject 归因”的描述是历史研究基线；process
> snapshot 启动代际已在 P6 checkout 落地，通用 PluginHost/完整 PluginSet last-good 仍是后续阶段。

## 1. 当前 Tianshu：治理地基强，运行时插件尚未建立

### 1.1 当前源码事实

当前插件能力是 metadata-only catalog：

- `PluginLoader` 只读取 `manifest.json`，不 import `entry_point`；
- `PluginApi.register_*` 是受信任源码可显式调用的进程级注册门面；
- API 始终投影 `manifest_only`、`loaded=false`；
- install/activate 返回 501；
- manifest 中的 dependencies、sha256、permissions、auto_install 仍是声明字段，不是已执行证明。

Executor 域已经有一条可复用的 Capability seam：`ExecutorAdapterRegistry` 提供
`register / replace / get / prepare / bind_effective`，每个 run 经 `prepare()` 得到
`PreparedExecutor`；`ExecutorCapabilityManifestV1` 与执行模式在注册、绑定时校验。Pi 垂直
切片因此不需要先新建通用 PluginHost，缺的是 generation、引用计数、last-good 与可逆卸载。

事实入口：[当前插件扩展实现与支持边界](current-plugin-state.md)、
[`PluginApi`](../../../src/tianshu/plugins/api.py)、
[`providers_api.py`](../../../src/tianshu/gateway/providers_api.py#L154-L198)。

> **2026-08-25 P2 更新**：受信任源码的六类 contribution 已补 owner、handle、逆序 disposer
> 与 stale identity guard；MCP session 工具也已接入该生命周期。以下缺口相应收敛为动态
> 第三方插件、generation、隔离与完整 PluginSet 归因，而不是“完全没有 owner/disposer”。

当前插件体系仍缺少：

- Package 验证和依赖解析；
- 可由 manifest 驱动的第三方 Capability seam 与依赖闭包；
- Executor seam 的 generation、引用计数、统一 disposer 和 last-good 边界；
- side-by-side、warming、health、drain、last-good；
- 插件级状态 schema、迁移和回滚；
- 第三方代码隔离与 Secret Broker；
- PluginSet 级运行归因。

### 1.2 已有、且应保留的地基

Tianshu 当前已经具备以下治理资产：

- Edict、Memorial、Decision；
- RunState、attempt lease/fencing、Workspace Lease、Restore Point；
- durable outbox、受管终态和副作用证据；
- ArtifactStore、Evidence Bundle；
- Candidate、Gate、Canary、Promotion journal、Rollback；
- 静态 DAG 和“客卿只替换执行面、不替换治理面”的边界。

当前 `RunAssignmentV1` 已能不可变绑定一次候选分流与 effective overlay，但还不能描述完整
运行环境。[契约源码](../../../src/tianshu/models/run_assignment.py#L35-L100)

当前 Skill Candidate 与 Pi EXECUTOR Candidate 具备真实 activation/rollback adapter；Pi 仅是
`keqing:pi` 治理垂直切片，其他 CandidateKind 与通用 PluginSet 仍不能证明可生产上线。
[Skill 装配源码](../../../src/tianshu/bootstrap/wiring_skills.py#L108-L121)；
[Pi 装配源码](../../../src/tianshu/bootstrap/wiring_executor.py)

## 2. DeepSeek Harness

### 2.1 上游源码证明的能力

DeepSeek Harness 使用 Cordis 作为插件化运行时。模型适配器、Tool、Session Log、Agent Loop
等都可以按插件装配；Effect/disposer、依赖注入和 Fiber 生命周期为注册与撤销提供了明确
语义。Bundle、Profile 与 patch 构成声明式组合层。

值得借鉴：

- Capability seam 和 Definition/Provider/Consumer 分工；
- 经 Cordis API 注册的 contribution 具有可逆 Effect；Cordis 外部资源仍需显式
  `ctx.effect()` 和 disposer；
- 依赖缺失时等待、Provider 卸载时重新收敛 Consumer；
- 文件变化不会自动改变已经挂载的 Session；空白 Session 可显式 recompose，已经开始的
  Session 被锁定；
- Host/global 能力与 per-agent preset 分层。

证据：[架构文档](https://github.com/deepseek-ai/deepseek-harness/blob/47f943859bef60e4160492346772ded9b24f765a/docs/architecture.md)、
[生命周期文档](https://github.com/deepseek-ai/deepseek-harness/blob/47f943859bef60e4160492346772ded9b24f765a/docs/cordis-tutorial/02-lifecycle-and-effects.md)。

### 2.2 不能照搬的边界

- 正式 Web 和 Headless bundle 明确关闭模块 HMR；
- `tool-cordis` 是进程内、临时、opt-in 的动态代码能力，VM 不是安全边界；
- 已开始 Session 不会原地换 preset；
- 动态 Cordis Host 更新目前是先撤销旧 run、再启动新 Host，不是 last-good 的
  make-before-break；
- 动态 package 只存在于当前进程，不会自动持久化或晋升为正式发布。

因此，“everything is a plugin”不能推导出“生产级安全热替换”。

证据：[Web HMR 配置](https://github.com/deepseek-ai/deepseek-harness/blob/47f943859bef60e4160492346772ded9b24f765a/packages/bundle/web-app/cordis.patch.yml)、
[`tool-cordis` 边界](https://github.com/deepseek-ai/deepseek-harness/blob/47f943859bef60e4160492346772ded9b24f765a/packages/extensions/tool-cordis/README.md)、
[preset generation 测试](https://github.com/deepseek-ai/deepseek-harness/blob/47f943859bef60e4160492346772ded9b24f765a/apps/cli/tests/web-agent-presets.e2e.ts#L498-L516)。

## 3. Pi

### 3.1 上游源码证明的能力

Pi 将 Package 分发、Resource 解析、Extension loader/runner 和 Agent Session 生命周期分层。
它提供丰富的扩展 API，可贡献 Tool、Command、Provider、UI、Renderer、Hook、Context
transform 和 compaction 等能力。

值得借鉴：

- project/user/CLI/package 等来源的确定性优先级；
- canonical path 去重和 source provenance；
- trust 后才加载项目资源；
- extension event handler/middleware 的固定顺序与冲突诊断；
- extension cache generation token 和 stale-context guard；
- Session replacement 前后明确 shutdown/start。

证据：[Packages](https://github.com/earendil-works/pi/blob/d3ab2af969d64997338253c9151190aa1bc33580/packages/coding-agent/docs/packages.md)、
[Extensions](https://github.com/earendil-works/pi/blob/d3ab2af969d64997338253c9151190aa1bc33580/packages/coding-agent/docs/extensions.md)。

### 3.2 不能照搬的边界

- `/reload` 会使旧 runner 失效并重建整套 Extension runtime，不是单插件原子交换；
- 新扩展加载失败后可能以缩减功能集继续，没有上一版本自动回滚；
- 通用 API 没有对所有 contribution 提供对称 unregister；
- timer、socket、process、watcher 依赖扩展在 shutdown 中合作清理；
- Extension 与宿主拥有相同 OS 权限，project trust 不是 sandbox；
- extension event handler 串行等待，没有统一 timeout、quota 或 circuit breaker。

证据：[`reload()`](https://github.com/earendil-works/pi/blob/d3ab2af969d64997338253c9151190aa1bc33580/packages/coding-agent/src/core/agent-session.ts#L2610-L2634)、
[Security](https://github.com/earendil-works/pi/blob/d3ab2af969d64997338253c9151190aa1bc33580/packages/coding-agent/docs/security.md)。

## 4. Agent 运行时与托管控制面的边界

| 系统 | 已提供 | 尚未组成的闭环 |
|---|---|---|
| [Codex Plugins](https://developers.openai.com/codex/plugins) | Skills、MCP、hooks 和任务模板的扩展打包 | 没有自动候选评测、Canary 和回滚控制面 |
| [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) | Agent、Tool、handoff、guardrail、session、tracing | 官方耐久执行依赖外部引擎；未描述自动修改并晋升 Agent 的内建闭环 |
| [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | thread checkpoint、恢复、HITL、跨 thread store | OSS 运行时不负责产生和晋升行为候选 |
| [LangSmith Assistants](https://docs.langchain.com/langsmith/assistants) | 配置版本、active revision、promote/rollback | 候选如何产生、如何独立评测仍由外部系统决定 |
| [Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/overview/) | Agent harness、workflow、session、middleware、checkpoint | Durable workflow 不等于产物级自进化 |

这些系统分别解决了会话持久化、执行持久化或配置发布，但没有同时解决产物 lineage、独立
评测和按插件的受治理自动演化。

## 5. 非 Agent 领域的成熟模式

| 来源 | 已证明的机制 | 对 Tianshu 的启发 |
|---|---|---|
| [Kubernetes Controllers](https://kubernetes.io/docs/concepts/architecture/controller/) | spec/status、generation、level-based reconcile | 用期望状态驱动插件和运行代际收敛 |
| [Envoy xDS](https://www.envoyproxy.io/docs/envoy/latest/api-docs/xds_protocol) | version/nonce、ACK/NACK、warming、last-valid | 激活回执、预热、新旧代际顺序切换 |
| [Temporal](https://docs.temporal.io/workflow-execution) | Event History、replay、故障恢复 | 确定性编排与外部 Effect 分离 |
| [Temporal Worker Versioning](https://docs.temporal.io/production-deployment/worker-deployments/worker-versioning/) | pinned workflow、ramp、rollback、drain | 长任务固定 generation，新版本接收新任务 |
| [OCI Descriptor](https://github.com/opencontainers/image-spec/blob/v1.1.1/descriptor.md) | mediaType、digest、size、内容寻址 | 所有可演化对象使用不可变 Artifact |
| [TUF](https://theupdateframework.github.io/specification/latest/) | 阈值签名、防 freeze/mix-and-match/rollback | Candidate 产生权与发布权分离 |
| [SLSA Provenance](https://slsa.dev/spec/v1.2/provenance) | 来源、输入和构建过程证明 | provenance 不再只是文本字段 |
| [MCP Architecture](https://modelcontextprotocol.io/docs/learn/architecture) | Host 掌握权限与上下文，Server 提供聚焦能力 | MCP 作为能力协议，而非插件生命周期系统 |
| [Wasmtime Security](https://docs.wasmtime.dev/security.html) | 显式 imports、内存隔离、WASI capability | 未来受限插件 ABI；不能替代准入和业务策略 |
| [OPA](https://www.openpolicyagent.org/docs/latest/) | Policy Decision 与 Enforcement 分离 | 策略 revision、decision log、失败保留旧 bundle |

这些系统共同指向同一个结论：安全热更新的本质不是 reload，而是不可变版本、预热、代际
并存、流量切换、旧代排空和 last-good 回滚。

## 6. Agent 自改进研究的真实边界

| 项目/论文 | 真正改变什么 | 可以借鉴 | 不能外推什么 |
|---|---|---|---|
| [Prime Agent](https://github.com/PrimeIntellect-ai/prime-agent) | Prompt、Memory、Skill 描述、Subagent spec | trajectory review、历史和回滚体验 | 不等于独立 held-out 评测和 Canary |
| [Continual Harness](https://arxiv.org/abs/2605.09998) | Harness，实验中可联动权重训练 | 多 surface 持续改进 | 不提供生产发布控制面，小模型存在能力下限 |
| [Reflexion](https://arxiv.org/abs/2303.11366) | episodic textual memory | 无权重更新的反馈学习 | 不等于产物晋升 |
| [Voyager](https://arxiv.org/abs/2305.16291) | 受限环境中的可执行 Skill 库 | 自验证、可复用技能增长 | 不等于通用生产插件治理 |
| [GEPA](https://arxiv.org/abs/2507.19457) | Prompt/文本候选 | trajectory reflection、Pareto 搜索 | 不负责部署和回滚 |
| [Darwin Gödel Machine](https://arxiv.org/abs/2505.22954) | Agent 自身代码变体 | archive、谱系、开放式候选搜索 | 无生产权限、状态迁移、SLO 和 Canary |

研究系统证明“Agent 能产生更好的候选”是可能的，但没有证明这些候选可以安全、自动地进入
真实生产环境。候选搜索算法应放在 Evolution Plane 内，而不是获得 active pointer。

## 7. 综合判断

在本次列出的公开项目与一手资料范围内，没有发现一个系统同时完整具备：

- 可插拔运行时与安全隔离；
- Session、执行、产物、发布四层持久化；
- 自动候选生成和独立评测；
- 代际灰度、状态兼容和自动回滚；
- 用户按插件冻结进化。

Tianshu 的机会不是复制某一个框架，而是把已经成熟的局部机制组合到自身已有的治理、Evidence
和 Candidate 地基上。
