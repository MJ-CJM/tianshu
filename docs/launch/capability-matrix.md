# Tianshu v0.4.2 Capability Matrix / 天枢 v0.4.2 能力事实矩阵

> This is the public source of truth for current capability claims. / 本表是当前公开能力承诺的事实源。

天枢的长期定位是：**天枢是一个可治理、可验证、持续成长的自进化 Agent OS**。这是一条产品方向，不代表 v0.4.2 已完成全部闭环。v0.4.2 面向 **trusted local / 可信本地**、单机、单节点使用；本地 HTTP、WebSocket 与 MCP 入口尚无统一身份认证，不得直接暴露到不可信网络。

成熟度定义：

- **Stable (limited) / 稳定（有限边界）**：列出的边界有实现和自动化测试证据；边界外不作保证。
- **Experimental / 实验**：实现可试用，但协议、恢复语义或安全边界尚未达到公开稳定承诺。
- **Planned / 规划**：路线图目标，不属于当前版本能力。

| Capability | Maturity | Default | Supported scope | Verified guarantee | Explicit non-guarantees | Evidence | Target gate |
|---|---|---|---|---|---|---|---|
| Native 本地主链与时间线 | Stable (limited) / 稳定（有限边界） | On | 可信本地、单进程、单节点、SQLite | 受管 Edict 通过统一 ingress、事务 outbox、attempt lease/fencing 完成规划、Native 执行、审计和终态投影；受支持故障点可在重启后恢复 | EventBus 本身仍不是持久队列；不保证 PostgreSQL/K8s/多副本语义，也不把未跟踪外部副作用提升为 exactly-once | [`src/tianshu/application/`](../../src/tianshu/application/)；[`tests/test_integration_flow.py`](../../tests/test_integration_flow.py)；[`S3 Gate`](../cc-fable-v1/reports/s3-core-governance-report.md) | G2 |
| Native 工具策略与事前裁决 | Stable (limited) / 稳定（有限边界） | On for Native | 内建 Native Agent 的已注册工具调用与受管 RunState | 工具 tier、策略规则和持久 Decision 在 Native 工具执行前生效；受支持的 pending/resolution 可在单节点 SQLite 重启后恢复 | 不拦截 opaque 外部 CLI 内部工具调用；未进入受管 RunState 的 legacy 路径不获得额外恢复承诺 | [`policy_hook.py`](../../src/tianshu/executor/policy_hook.py)；[`approvals.py`](../../src/tianshu/executor/approvals.py)；[`Decision restart tests`](../../tests/integration/test_decision_service_restart_race.py)；[`tool projection tests`](../../tests/integration/test_tool_decision_restart_projection.py) | G2 |
| 本地成本台账、脱敏、clean-env 与急停 | Stable (limited) / 稳定（有限边界） | Mixed | 可信本地 Native 路径与天枢启动的受支持子进程 | 已上报用量可归因入账；支持出站脱敏、子进程环境清理与分级急停 | 成本门禁依据已观测用量，可能超出阈值后才停；clean-env 不是 OS 沙箱；不保证网络隔离 | [`cost/`](../../src/tianshu/cost/)；[`test_pricing_integration.py`](../../tests/test_pricing_integration.py)；[`test_redact.py`](../../tests/security/test_redact.py)；[`test_clean_env.py`](../../tests/security/test_clean_env.py)；[`test_estop.py`](../../tests/security/test_estop.py) | G0 |
| SQLite 迁移账本、升级备份与离线恢复 | Stable (limited) / 稳定（有限边界） | On when a pending baseline is detected | macOS/Linux 可信本地单 SQLite 文件；fresh、canonical v0.4.2、两种历史 supervision 结构与既有 session 结构 | 待迁移检查、在线 WAL 完整备份与事务迁移按数据库跨进程串行；ledger 校验版本、名称与 checksum；未知结构 fail closed；离线恢复先校验再替换 | 不接管其他 pre-ledger 结构；不是持续备份、PITR 或崩溃恢复系统；恢复要求目标离线；当前不承诺 Windows 文件锁；备份保留需人工管理 | [`migration_ledger.py`](../../src/tianshu/storage/migration_ledger.py)；[`migrations.py`](../../src/tianshu/storage/migrations.py)；[`sqlite_backup.py`](../../src/tianshu/storage/sqlite_backup.py)；[`migration ledger tests`](../../tests/storage/test_migration_ledger.py)；[`migration preservation tests`](../../tests/storage/test_migration_preserves_data.py)；[`storage instance migration tests`](../../tests/test_storage_instance_migration.py)；[`backup/restore tests`](../../tests/storage/test_backup_restore.py) | G0 |
| Web 与 IM 裁决入口 | Experimental / 实验 | Optional | Web；Telegram 按钮；飞书命令回复 | 入口提交到统一持久 Decision 权威；受支持的 managed-run 裁决等待和 resolution 可耐单节点重启 | 飞书不是交互按钮卡片；外部消息送达不在 S3 Core 保证内；不是原生移动端产品 | [`decisions_api.py`](../../src/tianshu/gateway/decisions_api.py)；[`Decision restart tests`](../../tests/integration/test_decision_service_restart_race.py)；[`Feishu command tests`](../../tests/gateway/feishu/test_approval_commands.py)；[`Telegram callback/button tests`](../../tests/gateway/telegram/test_callback.py) | G2 |
| Outer-loop checkpoint 与后台运行 | Stable (limited) / 稳定（有限边界） | Opt-in | managed Native run；启用 long-task execution profile | 版本化 continuation、plan lineage、L3 裁决等待、reopen resolution 与 reconciler redispatch 在受支持路径可恢复 | 不保证 opaque CLI 内部 continuation、任意未建模故障点或未跟踪外部副作用恢复 | [`run_state.py`](../../src/tianshu/models/run_state.py)；[`managed recovery tests`](../../tests/integration/test_managed_production_recovery.py)；[`continuation tests`](../../tests/integration/test_continuation_recovery.py) | G2 |
| 记忆、画像与技能候选成长 | Experimental / 实验 | Mixed | 本地 Markdown、SQLite/FTS、画像与技能候选流程 | 可积累记忆、合成画像并记录技能候选及评审结果 | 不保证这些变化提升真实任务效果；不会因此自动获得可信自进化闭环 | [`src/tianshu/memory/`](../../src/tianshu/memory/)；[`src/tianshu/persona/`](../../src/tianshu/persona/)；[`src/tianshu/skills/`](../../src/tianshu/skills/) | G4 |
| Keqing 外部 Claude Code/Codex CLI | Experimental / 实验，contained + experimental | Opt-in per edict | 天枢启动的受支持 CLI adapter；独立工作目录 | 提供独立工作目录、clean-env、外围 timeout 与事后结果归一；已捕获的工具事件可交给外围链路 | 不保证 CLI 内部事前工具拦截、内部事件完整性、硬成本上限、运行前恢复点、网络隔离、耐重启或受治理的 apply/merge | [`src/tianshu/executor/keqing/`](../../src/tianshu/executor/keqing/)；[`adapter tests`](../../tests/executor/keqing/test_adapter.py)；[`executor workspace/result/timeout tests`](../../tests/executor/keqing/test_executor.py)；[`clean-env tests`](../../tests/security/test_clean_env.py)；[`gateway API tests`](../../tests/gateway/test_keqing_api.py) | G4 |
| Universe 快照、分支、diff 与人工切换 | Experimental / 实验 | Evolution off | 本地 Universe 元数据与人工操作 | 可创建和比较受支持的行为/代码变体并人工切换 | 当前只路由 champion；无真实在线 challenger 流量；无可信自动晋升 | [`src/tianshu/universe/`](../../src/tianshu/universe/)；[`docs/impl/universe/README.md`](../impl/universe/README.md) | G4 |
| 配对评估与代码变体运行 | Experimental / 实验 | Manual | 隔离端口和数据库配置的本地子进程评估 | 支持以独立运行配置比较结果并生成评估记录 | 与宿主共享 OS、进程权限及网络；不是安全沙箱；评估结果不会自动成为可信晋升决定 | [`src/tianshu/evals/`](../../src/tianshu/evals/)；[`tests/test_platform_eval_runner.py`](../../tests/test_platform_eval_runner.py) | G4 |
| 统一身份、鉴权与安全远程访问 | Planned / 规划 | Not available | 规划中的 REST、WebSocket 与 MCP 公共入口 | — | v0.4.2 无公共远程部署安全承诺 | [G1 roadmap](../superpowers/plans/2026-07-10-open-source-agent-os-master-roadmap.md#phase-1--public-safe-foundation) | G1 |
| 持久裁决、受支持重启恢复、副作用账本与 Evidence Bundle | Stable (limited) / 稳定（有限边界） | On for managed Native runs | 可信本地、单节点 SQLite；声明语义的 managed effects；Evidence Bundle v1 | 统一 Decision/RunState/attempt 权威、lease/fencing/DLQ、受支持 continuation 与 effect recovery、内容寻址 artifact 和可独立校验 Evidence Bundle | 不保证多副本、未跟踪外部 effect exactly-once、完整 planner 质量体系、完整 OTel 或外部通知送达 | [`src/tianshu/governance/`](../../src/tianshu/governance/)；[`src/tianshu/evidence/`](../../src/tianshu/evidence/)；[`Evidence tests`](../../tests/evidence/)；[`S3 Gate`](../cc-fable-v1/reports/s3-core-governance-report.md) | G2 |
| 容器或 OS 级安全沙箱 | Planned / 规划 | Not available | 规划中的 managed executor | — | 当前 eval 子进程、独立目录与 clean-env 均不是安全沙箱 | [G1 roadmap](../superpowers/plans/2026-07-10-open-source-agent-os-master-roadmap.md#13-统一外部执行边界) | G1 |
| 真实 challenger 路由与可信自动晋升 | Planned / 规划 | Not available | 规划中的 governed evolution | — | v0.4.2 无在线 challenger 分流和自动晋升 | [G4 roadmap](../superpowers/plans/2026-07-10-open-source-agent-os-master-roadmap.md#42-真实-challenger-路由与晋升) | G4 |

## S2 Lean security status / 安全边界

| Boundary | Status | Default / current limit | Evidence |
| --- | --- | --- | --- |
| SystemAudit tamper-evident chain | **Implemented / 已实现** | single-node SQLite；不是外部 WORM 或分布式审计服务 | [`SystemAudit storage tests`](../../tests/storage/test_system_audit.py)；[`API tests`](../../tests/gateway/test_system_audit_api.py)；[`transaction tests`](../../tests/security/test_system_audit_transactions.py) |
| MCP persisted secret mappings | **Implemented / 已实现** | 持久化 env/header 是密文；密钥缺失、错误或密文损坏时 fail closed | [`ciphertext migration tests`](../../tests/secrets/test_mcp_secret_migration.py)；[`rotation tests`](../../tests/cli/test_secrets_rotate.py) |
| remote MCP | **Disabled / Deferred** | secure-remote 下默认拒绝；不承诺完整 SSRF、DNS pinning 或 remote MCP security | [`Lean admission tests`](../../tests/security/test_mcp_lean_admission.py)；[P2-A1](../cc-fable-v1/06-deferred-work-backlog.md#p2-a1-remote-mcp-公开安全s24) |
| stdio exact grant / executable binding | **Deferred / 延期** | 当前 Lean 边界只要求 enabled 配置使用显式非空 `tools.include`；不承诺持久 exact grant 或 executable drift binding | [`Lean admission tests`](../../tests/security/test_mcp_lean_admission.py)；[P2-A2](../cc-fable-v1/06-deferred-work-backlog.md#p2-a2-stdio-mcp-准入与漂移绑定s25) |
| container / PyPI / GHCR / signing | **Deferred / 延期** | 当前官方安装路径只有 source checkout 与该 checkout 产出的 exact Wheel | [S2 threat model](../security/lean-preview-threat-model.md)；[P2-A3/A4](../cc-fable-v1/06-deferred-work-backlog.md#p2-a3-官方-exact-wheel-容器s26s67-部分) |

## S3 Core governance status / 持久治理与证据边界

| Boundary | Status | Default / current limit | Evidence |
| --- | --- | --- | --- |
| Durable ingress, outbox, Decision and RunState | **Implemented / 已实现** | managed Native；单节点 SQLite | [`S3 Gate`](../cc-fable-v1/reports/s3-core-governance-report.md)；[`focused recovery tests`](../../tests/integration/) |
| Attempt lease, fencing and managed effect recovery | **Implemented / 已实现** | 只对声明且被账本跟踪的 effect 语义作保证 | [`claim recovery`](../../tests/integration/test_claim_lease_recovery.py)；[`effect idempotency`](../../tests/integration/test_side_effect_idempotency.py) |
| ArtifactStore and Evidence Bundle v1 | **Implemented / 已实现** | 内容寻址、本地 artifact、严格 schema 和独立 hash 校验 | [`Evidence tests`](../../tests/evidence/)；[`published schema`](../reference/evidence-bundle-v1.schema.json) |
| OTel dashboards / SLO | **Deferred / 延期** | 当前只有 correlation、SystemAudit、readiness 与 Evidence，不是完整观测平台 | [P2-B2](../cc-fable-v1/06-deferred-work-backlog.md#p2-b2-完整-otelslo-与外部通知s312) |
| External notification delivery | **Deferred / 延期** | S3 只保证内部 durable delivery record/outbox，不保证 Feishu/Telegram/email 最终送达 | [`internal delivery tests`](../../tests/notifier/test_internal_delivery_recovery.py)；[P2-B2](../cc-fable-v1/06-deferred-work-backlog.md#p2-b2-完整-otelslo-与外部通知s312) |
| PostgreSQL / Kubernetes / multi-replica | **Not claimed / 不承诺** | 没有分布式 lease、共识或跨副本 exactly-once 证据 | [`S3 Gate limits`](../cc-fable-v1/reports/s3-core-governance-report.md#known-limits-and-deferred-work) |

## Keqing capability flags / 客卿能力标记

当前 external CLI adapter 的机器可读事实语义如下；后续只有在对应 Gate 的证据完成后才能改为 `true`：

```text
action_interception=false
decision_bridge=false
hard_cost_cap=false
pre_run_restore_point=false
source_workspace_staging=false
governed_apply_merge=false
network_control=false
secret_env_isolation=true
workspace_control=partial
event_fidelity=best_effort
durable_resume=false
side_effect_receipts=false
artifact_export=false
```

## Public-claim rule / 对外表述规则

- G1 通过后最多发布 **Developer Preview**，只承诺已验证的 public-safe 基础。
- G2/G3 通过后才能把“可治理、可验证”用于对应的真实 Web 产品路径。
- G4 通过后才能宣称自进化闭环已经成立。
- G5 通过后才进入正式开源宣发；任何历史决策、设计稿或 ADR 的“批准/交付”状态都不能替代本矩阵的当前成熟度。
