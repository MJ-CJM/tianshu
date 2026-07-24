# 天枢客卿体系落地：pi 为默认执行内核（含自进化重构与死代码清理）

> 2026-07-23 批准的实施方案。设计细节见 [docs/design/keqing/pi-default-adapter.md](../design/keqing/pi-default-adapter.md)；借鉴分析与 adapter 抽象见 [docs/plan/2026-07-23-grok-pi-borrow-and-agent-adapter.md](./2026-07-23-grok-pi-borrow-and-agent-adapter.md)。

## 实施状态

| 阶段 | 状态 |
|---|---|
| P0 卫生清理（死代码三件套 + CancelledError 缺口） | ✅ 已完成（2026-07-23，393 tests 绿） |
| P1 keqing:pi 单发 MVP | ✅ 已完成（2026-07-23，30 keqing + 21 连带 tests 绿）；含 pi 版本演进防线 |
| P2 Pi RPC 会话执行 + 验收闭环 | ✅ 已完成（2026-07-23，149 tests 绿含 lifecycle 回归）；FakePiHandle 驱动全链路验证 |
| P3 凭证网关（scoped token + 402 硬熔断） | ✅ 治理链已完成（scoped_token 15 + llm_gateway 9 + contextvar/resolver 4 tests 绿）；仅 httpx 上游反代实现待部署接线 |
| P4 tianshu-guard 进程内治理 | 🟡 guard_config(5 tests 绿)+ tianshu-guard.ts 代码完成；session -e 接线 + 握手须真 pi 验证 |
| P5 evolver 生成段改产 edict | 🟡 edict 构建核心已完成（edict_builder，7 tests 绿）；evolver 全流程改接是集成步骤（按「稳定一周期后删本地变异」保留旧路径） |
| P6 CODE 晋升通路 | 🟡 源校验侧 CodeCandidateAdapter 已存在；原子 activate/rollback（promotion.py flock/CAS）须专注实现+全系统测试（危险，不赶工） |
| P7 三方合并批红 + 回收前留证 | 🟡 三方合并核心已完成（three_way_merge，11 tests 绿）；接入 apply 事务核心（fd锚/CAS/journal）是集成步骤 |

---

## Context（为什么做这件事）

天枢已拍板架构转向：把**自进化的 coding 能力外包给外部客卿 coding agent**（默认 **pi**——开源、多 provider 不锁定；Claude Code/Codex 备选），天枢退守「提需求(edict) + 治理 + 验收 + 评估」。

两个驱动因素：
1. **现状自进化的「上线终点是空的」**——`universe/manager.switch`、`promote_code_variant` 是抛 `RuntimeError` 的 stub，代码热部署三件套（deployer/launcher/sandbox_container）是无触发点的死代码；能生成候选、跑评估、出推荐，但没有一条通路让产物真正上线。转向客卿架构的重构代价因此比预想小。
2. **keqing 执行子系统已相当成熟但只有单发档**——`KeqingExecutor` + `KeqingAdapter`(claude-code/codex) + execution_gateway 命令授权 + `keqing:<backend>` 路由已在，但是「一次性 headless 单发」模型，无法表达 pi 的 RPC 会话。

**关键地基发现（推翻原以为的最大难题）**：execution_gateway **已原生支持 RPC 双向长连接**——`ExecutionHandle.write_stdin`/`close_stdin`（process_backend.py:537/550）、`stdin_mode='pipe'` 双管道、stdout 后台泵与写入解耦、pipe 流式脱敏全部实装；`tools/mcp/transport.py:85 _open_stdio` 就是跑在同一 gateway 上的生产级双向 JSON-RPC 长连接范例。**process_backend 零改动**，pi 会话执行器照抄 MCP transport 骨架即可，全部治理原样复用。

**两个战略决策（用户 2026-07-23 拍板）**：
- 凭证网关时序：**闭环先行、网关随后**（P2 先兑现 pi 核心价值，网关 P3；过渡期 pi 直连 provider key 与现 claude-code 同风险面；evolver 无人值守 cron 放量以网关 402 硬熔断就位为前置门）。
- 本地变异删除：**edictgen 稳定一周期后删**（过渡期保留本地 LLM 变异作客卿全线故障降级引擎）。

---

## 核心架构决策（定死）

1. **execution_gateway 层零改动**。任何「改 process_backend 加双向管道」或「绕过 gateway 自管 pi 进程」的方案否决——后者会丢掉 command_grant/clean-env/secret 注入/出站脱敏/回执审计/进程组收敛全部治理。

2. **keqing 层双执行路径并列、类型分立不杂交**。不改单发 `KeqingAdapter` Protocol（claude-code/codex 零改动陪跑）；新增**并列**的会话协议面与独立执行器（新文件 `session.py`/`pi_adapter.py`/`session_executor.py`，而非在 `KeqingExecutor` 内条件分派）。两协议共享的唯一接缝是 `is_canonical_argv` → `issue_keqing_command_grant` 与 `gateway.py:548` 校验（backend 由 `adapter_id.removeprefix('keqing:')` 通配派生，gateway 校验代码零改动；grant 仅 start 时校验一次的语义恰好适配长驻会话）。

3. **会话执行骨架照抄 `mcp/transport.py:_open_stdio`**：`stdin_mode='pipe'`、`stdin_write_limit_bytes=1MB`、三并发任务、cleanup 顺序照搬；`timeout_seconds` 覆盖整场会话且按 follow_up 轮次（N=3）预留、受 `budget.wall_clock_seconds` 夹逼；stdout_reader **无条件持续消费**防 `maxsize=8` 背压死锁；结算锚点只认 `agent_settled`（`agent_end.willRetry=true` 继续等）。

4. **治理强制力分层**。硬保证三道关卡——凭证/预算在**网关**、文件边界在 **worktree**、终审在**验收 checks + 三方合并批红**；`tianshu-guard` 仅是进程内**软增强**（fail-closed 握手），任何硬保证不寄托于 guard。预算权威位从「事后解析流杀进程」上移到网关 402，进程内 usage 累计降级为备份保险。

---

## 分阶段实施

### P0 · 卫生清理 ✅ 已完成
- 删 `universe/deployer.py`/`launcher.py`/`sandbox_container.py` 三文件 + 三个对应测试文件。
- 清装配残留：`bootstrap/wiring_universe.py`（import + DeployPointer/Deployer 构造 + `UniverseManager(deployer=...)` + `app.state.code_deployer` + 模块 docstring）；`universe/manager.py`（deployer 形参 + `self._deployer`）。`switch`/`promote_code_variant`/`rollback` stub 继续抛 `promotion_service_required` 不变。
- 架构守卫 `tests/architecture/test_no_direct_process_launch.py` 移除 deployer/launcher 两条 allowlist 豁免（守卫收紧）；`tests/test_bootstrap_smoke.py` 删 `code_deployer` 断言项；`tests/universe/test_execution_boundary.py` 参数化列表去 `sandbox_container.py`；`tests/universe/test_manager_code.py` 删 `_FakeDeployer`/`mgr_with_deployer`、两个保留测试改用 `mgr` fixture、删作废的 `requires_deployer` 测试。
- 修 `persona/profile_synthesizer.py`：`_narrow_list_result` 对 `asyncio.CancelledError` **re-raise 而非降级空列表**（取消不再被掩码成空输入落退化 PROFILE.md）；run() 的 `except Exception` 前加显式 `except asyncio.CancelledError: raise`。`test_profile_synthesizer.py` 的旧断言（取消降级为空）改为「取消被 re-raise」+ 补普通异常仍降级测试。
- **验收结果**：`tests/universe/ tests/persona/ tests/test_bootstrap_smoke.py tests/architecture/ tests/security/test_mcp_command_boundary.py` 共 393 passed；全仓无死代码残留 import；架构守卫减两条豁免后仍绿。

### P1 · keqing:pi 单发 MVP（薄接入，钉链路 + 留降级位）✅ 已完成
- ✅ 新建 `keqing/pi_wire.py`（wire 帧常量 + PiUsage/PiCost + `VERIFIED_SESSION_VERSION`）+ `keqing/pi_adapter.py`（PiAdapter 单发面 `build_argv`/`is_canonical_argv`/`parse_stream`），经 `adapter.py` 末尾 `_register_session_backends()` 延迟注册进 `_REGISTRY`（backend='pi'，破循环 import）；`auth_env_vars` 暂列常见 provider 变量（P3 换网关 token）。
- ✅ `capabilities.py` 加 `pi_manifest()`（BEST_EFFORT 预算）+ 加入 `default_executor_manifests()`；`executor.py` 加 `keqing:pi` DelegatingExecutorAdapter，delegate 指现有 `KeqingExecutor`。
- ✅ `test_executor_workspace_lifecycle.py` 参数化加 `keqing:pi` success/rollback（满足 manifest evidence 引用）；`test_adapter.py` 注册列表断言更新。
- ✅ **pi 版本演进防线**：`parse_stream` 宽容解析（未知事件跳过/字段缺失降级/不崩）吸收非破坏性演进；session header version 漂移时 WARNING 告警（不静默出错）；pi 破坏性演进仅需改 pi_adapter/pi_wire 两薄文件，治理核心零影响。
- ✅ **测试**：`tests/executor/keqing/test_pi_adapter.py`（argv 规范/parse_stream 文本·usage·成本·工具·错误·兜底/版本漂移告警/未知事件忽略），30 keqing tests 绿。
- ⏳ **待真 pi 二进制验证**：一条 `runtime.executor='keqing:pi'` 真实 edict 端到端（当前 lifecycle 测试用 AsyncMock stub 执行，逻辑链已验证；真 CLI 冒烟需装 pi）。

### P2 · Pi RPC 会话执行 + 验收闭环（pi 核心增量价值）
- 新建 `keqing/session.py`：`KeqingSessionAdapter` 协议 + `AgentCapabilities` + `CanonicalAgentEvent` 最小 envelope（`run_start`/`run_end{will_retry}`/`run_settled`/`tool_execution_*`/`message_end` usage，未知帧沉降 Unknown 不炸）。
- PiAdapter 会话面：`build_session_argv` 产 `pi --mode rpc --no-session --session-dir <ws>/.tianshu/sessions`（`-e guard` 留 P4）；`encode_command` 带 id 配对；`parse_event` 逐帧增量、结算只认 `agent_settled`。
- 新建 `keqing/session_executor.py`：`KeqingSessionExecutor` 照抄 `mcp/transport.py:85 _open_stdio` 骨架；与 `KeqingExecutor.execute` 同外观、切为 `keqing:pi` 的 delegate；`message_end` usage 增量计费 ×7.2 触顶 terminate 作备份熔断。
- 验收闭环：settled 后复用 `orchestrator/checks.py`（purpose='acceptance'）在 workspace_root 跑 `edict.acceptance` → 不合格 `follow_up`(整改意见) 回灌，上限 N=3、轮次入起居注；N 次仍不合格挂 memorial；合格 `get_session_stats` 终账 → `close_stdin` 优雅收尾。
- 契约测试：fake-pi Python 脚本模拟 LF-only JSONL 帧流；真 pi 二进制（`requires-pi` 标记）对钉死版本重跑同套件。
- **验收**：一条 edict 经 pi RPC 完成且含一次「验收不合格→follow_up→合格」完整闭环入起居注；预算触顶会话被 terminate；背压压测不死锁；fake-pi 契约套件绿；无孤儿进程。

### P3 · 凭证网关（scoped token + LLM 流式反代 + 硬熔断 402）
- 新建 `secrets/scoped_token.py`：`mint(edict_id,run_id,model_allowlist,budget_cny,ttl)→raw token`（照搬 `gateway/auth.py:205` 范式，前缀 `tskq_`）。
- 新建 `gateway/llm_gateway_api.py`：Anthropic/OpenAI 双格式流式反代；Bearer 逐请求校验 + 模型白名单 + `EstopManager.check()` 联动；usage 实时累计，超额 **402**；`X-Tianshu-Edict-Id/Run-Id` 归因头逐 run 记账。
- run-aware secret_resolver（先决改造）：`bootstrap/wiring_tools.py:34-68` 注入自定义 resolver，新增 `keqing-run:` ref 命名空间；`gateway.py:639-675` 准入白名单仅放行 `keqing-run:`（仅 keqing purpose），其余铁律原样保留 + 架构测试钉死。
- PiAdapter.auth_env_vars 切为网关 baseUrl + token，raw key 名一律不列。
- `cost/manager.py`：run 终结以网关计量为权威落账，进程内熔断降级为备份，7.2 汇率单一口径。
- **验收**：客卿进程内 env dump 抓不到任何 raw key；同 edict 两次 spawn token 不同且 TTL 后失效；超预算 402 → memorial 标额度耗尽；revoke/estop 后下一请求即断供；命名空间准入矩阵测试绿。

### P4 · tianshu-guard 进程内治理软增强（fail-closed）
- 新建 `keqing/guard/tianshu-guard.ts`（与 adapter 同仓同版本钉死）+ `keqing/guard_config.py`（Pydantic 单一真相生成 guard JSON schema）：`project_trust=no`；`tool_call` deny/allow（bash 段级不对称）；`registerProvider` 重定向 baseUrl→网关；`before_provider_headers` 注归因头；`registerCommand` 握手应答。
- pi_adapter spawn argv 加 `-e <guard>`；guard 配置由 PolicyCompiler 最小版编译（tighten-only），产物与 policy 版本入起居注。
- session_executor spawn 后发握手命令，失败即 terminate + 挂 memorial（fail-closed）。
- 二期可独立交付：ask 档经 `extension_ui_request` 反向通道 → DecisionService 批红 → timeout 兜底=拒绝。
- **验收**：恶意仓库 `.pi/` 扩展不加载；deny 工具被 block 且 reason 入起居注；guard 加载/握手失败本次 run 终止并挂 memorial。

### P5 · evolver 生成段改产 edict（自进化外包）
> 依赖 P2；无人值守 cron 放量以 P3 网关硬熔断为门。
- `evolver.py:82 __init__` 注入 `edict_application_service`；`wiring_universe.py` 直取 `app.state.edict_application_service`（wire 顺序已核验：tools:206→skills:209→universe:245；wire_universe 显式断言 fail-fast）。
- 新增 `_build_evolution_edict(proposal)`：goal=hypothesis、context=rationale+失败症状、constraints=[演化域 allowlist 文本化、不得破坏测试]、acceptance=`CheckSpec(bash pytest 回归 + rubric 清单)`、runtime=`EdictRuntime(executor='keqing:pi', cost_budget_cny=..., timeout 按 follow_up 轮次预留)`；契约用 `requested_contract_for_edict` 派生保证自洽。
- `auto_propose_codes(:514)` 改 diagnose→`_build_evolution_edict`→`SubmitEdictCommand(idempotency_key=stable_hash(target+hypothesis), extra_payload={'via':'evolver'})`→submit（照抄 `tools/submit_edict.py`）。`run()` 行为演化同构改造；保留 quota/lock/idle 门。
- **工作区四方对齐（关键坑）**：下发前 `manager.branch_code_variant` 建 universe worktree，客卿 `workspace_root=CodeVariantStore.worktree_dir(uid)`；settled 后 stage+commit——使客卿产出目录 == acceptance checks workspace_root == gate/eval 的 variant_worktree == apply 输入。
- 评估改事件驱动：memorial 完成事件→`gate.run_async`→`evaluate_paired_async`→delta≥margin→`save_variant_eval_run`→emit `universe.promotion_recommended`；灰度 flag 与廷议保留。
- A/B 分桶排除：ChallengerRouter 按 `extra_payload via=evolver` 排除内部 edict。
- **本地变异三件套删除（稳定一周期后）**。硬前置：① 演化域防护迁为三重保险（edict.constraints 文本 + guard tool_call deny + 验收 bash check 校验 git diff 路径⊆allowlist）；② code_mutator 的 git stage+commit 段先移入 evolver 客卿产出提交步骤。
- **验收**：cron/manual 端到端 diagnose→edict→客卿在 universe worktree 产出 commit→验收 checks 真实执行→gate+配对评估产 delta→promotion_recommended；同一假设幂等去重；submit 异常不冲垮 evolve 循环；内部 edict 不进 A/B 分桶。

### P6 · CODE 晋升通路（填补「上线终点是空的」）
> 状态机/廷议闸/GateEvaluator 零改动；happy path（ours==base 无漂移）先通，冲突场景留 P7。
- 新建 `evolution/adapters/code_promotion.py`：`CodePromotionAdapter`——activate（借 `SkillPromotionAdapter promotion.py:258-711` 的 flock/`_atomic_exchange`/回滚 marker/`_reconcile` 范式），rollback/verify_rollback 还原 base（锚 restore_point_ref，≤60s SLO），`rollback_is_idempotent=True`。
- `wiring_skills.py:113-116`：`CandidateKind.CODE` 从 `UnavailablePromotionAdapter` 换成 `CodePromotionAdapter`。
- 证据桥：配对 delta≥margin + 验收物结果编码为 `ClosedEvidenceBundleV1`；两轴严格分离——eval_harness 回归分只用于 tianshu 自身代码改动，任意 edict 验收走 acceptance checks，混用即假绿证据。
- 新建 `evolution/code_pipeline.py` 编排桥：消费 `promotion_recommended`→`CandidateService.propose(kind=CODE)`→stage→`GateEvaluator.evaluate`→GOVERNED_APPLY 廷议→approve 后 `start_canary`→`promote(decision_request_id=廷议 id)`；`manager.promote_code_variant` stub 接到此流水。
- **CODE canary 语义（已拍板）**：soak 观察窗（不做流量分桶，真实写盘只在 promote 的 activate）；CODE 与 SKILL 共享系统级单 canary 槽（>1 fail-closed），当期接受串行排队。
- **验收**：一条客卿产出走完 propose→gate 全绿→廷议 approve→canary→promote 真实写盘；无廷议 Decision 时 promote 被拒；rollback 60s 内还原且 verify_rollback 幂等；crash 后 reconcile 可重放；`automatic_promotion` 恒禁。

### P7 · 三方合并批红 + 回收前留证（apply 深水区）
- `git_backend.py` 新增 `merge_blob(base_oid,ours_oid,theirs_oid)`（`git merge-file` 薄封装），仅在 staging/临时仓执行，source 对象库 hash 零变动断言。
- `workspace_apply.py`：`preflight`/`_read_expected` 放松 source 侧 old_oid 硬相等，改读 ours 实际态做三方分类（ours==base→取 theirs；theirs==base→保 ours；ours==theirs→跳过；双改→`merge_blob` 或标冲突）；`ApplyPlan` 增 conflicts 字段；fd 锚+journal+CAS+preimage TOCTOU 骨架不动，仅 desired 内容换 merged。
- `workspace_service.py`：`issue_apply_decision` payload 增 conflicts 数组（三方 oid + merged 预览，三份全文上报）；**批红档位（已拍板）**：clean 单自动、含冲突单留 PENDING 人工批红。
- 回收前留证：keqing 工作区/staging teardown 前在事务边界外插 `ShadowSnapshot.init()`+`snapshot('pre-reclaim')`+台账；只 snapshot 不 revert，禁 mid-transaction。
- **验收**：三场景端到端（仅 theirs 改→自动落地 / 双改无重叠→auto-merge / 重叠冲突→留 PENDING 批红见三份全文）；source 对象库 hash 全程零变动；pre-reclaim 快照可 revert；property 测试覆盖 CAS/journal 语义未破。

---

## 删除 + 重构清单汇总

- **删除（P0 ✅）**：deployer/launcher/sandbox_container 三件套 + 装配残留 + 三个测试文件 + 两条架构守卫豁免。
- **修复（P0 ✅）**：ProfileSynthesizer 吞 CancelledError。
- **条件删除（P5 稳定一周期后）**：`_propose_mutation`/`mutator.apply_mutation`/`code_mutator.mutate`。硬前置见 P5。
- **明确不删（live 共享底座）**：manager.branch/branch_code_variant、eval_harness 全套、gate.py、diagnostician.py（保持纯诊断）、code_store.py、feature_flags 灰度、consultation 廷议、`universe.promotion_recommended` 事件链及其测试。

## 端到端主场景（收官验证）

一条 evolver 诊断驱动的演化 edict，经 pi RPC 客卿在 universe worktree 开发 → 验收不合格 follow_up 回灌 → 合格 → 配对评估赢 → 廷议批红 → CODE promote 真实写盘 → 可 rollback。这条链跑通即代表「自进化换引擎」完成：天枢不再自己写代码，只提需求 + 治理 + 验收 + 评估。
