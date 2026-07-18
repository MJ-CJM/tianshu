# Tianshu v0.4.2 Capability Matrix / 天枢 v0.4.2 能力事实矩阵

> 天枢是一个可治理、可验证、持续成长的自进化 Agent OS。

本表只描述 Lean Developer Preview Candidate 的已验证边界。`publication_status`:
`not_authorized`。运行模型是 single-host、single-node SQLite；Ubuntu + Python 3.12
是首个正式目标，最终黄金批次实际在 `Darwin/arm64/Python 3.12.12` 本地验证。宿主
信任模型为 trusted local / 可信本地；host administrator 不在当前防护对象内。

成熟度定义：Stable (limited) 表示命名边界有自动化；Experimental 表示可试用但契约或
支持承诺未冻结；Planned 表示路线图目标，不是当前能力。状态标签另行区分
`implemented`、`disabled`、`deferred`、`experimental`、`external_pending` 和
`user_approval_pending`。

| Capability | Maturity | Default | Supported scope | Verified guarantee | Explicit non-guarantees | Evidence | Target gate |
|---|---|---|---|---|---|---|---|
| Native 本地主链与时间线 | Stable (limited) / 稳定（有限边界） | On | 受管 Native；单机、single-node SQLite | 统一 ingress、durable outbox、Decision、RunState、attempt lease/fencing 与受支持 continuation 形成 durable governance | 不承诺多副本、PostgreSQL/K8s 或未跟踪外部副作用的单次执行语义 | [`application`](../../src/tianshu/application/)；[`integration flow`](../../tests/test_integration_flow.py)；[`S3 Gate`](../cc-fable-v1/reports/s3-core-governance-report.md) | G2 Lean Core |
| Native 工具策略与事前裁决 | Stable (limited) / 稳定（有限边界） | On | 已注册 Native 工具与受管 RunState | tier、策略和持久裁决在工具执行前生效 | 不穿透 opaque 外部 CLI；legacy 未受管路径不获得额外恢复保证 | [`policy hook`](../../src/tianshu/executor/policy_hook.py)；[`restart tests`](../../tests/integration/test_tool_decision_restart_projection.py) | G2 Lean Core |
| SQLite 迁移账本、升级备份与离线恢复 | Stable (limited) / 稳定（有限边界） | On | 支持的 v0.4.2 历史结构；单 SQLite 文件 | append-only migration ledger、升级前备份、未知结构 fail closed、离线校验后恢复 | 不是持续备份/PITR；不支持任意 pre-ledger 结构或多节点 | [`migration ledger`](../../tests/storage/test_migration_ledger.py)；[`backup/restore`](../../tests/storage/test_backup_restore.py)；[`instance migration`](../../tests/test_storage_instance_migration.py) | G0 |
| Web 与 IM 裁决入口 | Experimental / 实验 | Desktop Web on; IM optional | Candidate 正式产品路径仅 desktop Web；既有 IM adapter 不属于黄金路径 | desktop Web 读取统一持久 Decision 权威 | 不承诺移动端产品、外部消息最终送达或把 IM 证据替代桌面 Gate | [`Decision API`](../../src/tianshu/gateway/decisions_api.py)；[`Telegram legacy adapter tests`](../../tests/gateway/telegram/test_callback.py)；[`S4 Gate`](../cc-fable-v1/reports/s4-core-web-report.md) | G2/G3 Lean Core |
| SystemAudit tamper-evident chain | Stable (limited) / 稳定（有限边界） | On | single-node SQLite | canonical hash、previous-hash、append-only trigger、全链校验与 scoped admin export 已 `implemented` | 不是外部 WORM；不抵抗 host administrator 替换 DB 与 trust root | [`storage tests`](../../tests/storage/test_system_audit.py)；[`API tests`](../../tests/gateway/test_system_audit_api.py)；[`S2 Gate`](../cc-fable-v1/reports/s2-lean-security-report.md) | S2 Lean |
| MCP persisted secret mappings | Stable (limited) / 稳定（有限边界） | On for persisted mappings | env/header mapping at rest | 密文迁移、round-trip 验证、错误 key/ciphertext fail closed 已 `implemented` | 不覆盖进程内明文、宿主机管理员或开放 remote/stdio 安全 | [`ciphertext tests`](../../tests/secrets/test_mcp_secret_migration.py)；[`rotation tests`](../../tests/cli/test_secrets_rotate.py) | S2 Lean |
| ArtifactStore and Evidence Bundle v1 | Stable (limited) / 稳定（有限边界） | On for managed final runs | 本地内容寻址 artifact；受管 Native | closed bundle 严格 schema/hash，绑定敕令、奏折、裁决、检查与 artifact | 不承诺外部不可变存储、完整 OTel 或所有外部通知送达 | [`Evidence tests`](../../tests/evidence/)；[`schema`](../reference/evidence-bundle-v1.schema.json)；[`S3 Gate`](../cc-fable-v1/reports/s3-core-governance-report.md) | G2 Lean Core |
| 三张核心 desktop Web 页面 | Experimental / 实验 | On | 中枢总览、敕令详情、演化中心 | 权威 API、七类真实状态、无 production mockData；自动化、axe、键盘、缩放和视觉矩阵通过 | 视觉/交互为 `user_approval_pending`；VoiceOver 为 `external_pending`；十四部门深度未完成 | [`S4 Gate`](../cc-fable-v1/reports/s4-core-web-report.md)；[`E2E tests`](../../web/e2e/) | G3 Lean Core |
| Lean Core evolution | Experimental / 实验 | No active candidate | 单节点技能候选；受控 canary | candidate/evidence-bound Gate、PromotionService、真实 assignment/effective overlay 与回滚已 `implemented` | OpenHands、compatibility、ROI、cost calibration、full G4 为 `external_pending`；代码候选不自动晋升 | [`S5 Gate`](../cc-fable-v1/reports/s5-lean-evolution-report.md)；[`evolution tests`](../../tests/evolution/)；[`golden report`](../cc-fable-v1/evidence/lean-preview/20260718T072917Z-b27f525fe4ef/demo-report.json) | G4 Lean Core only |
| Keqing 外部 Claude Code/Codex CLI | Experimental / 实验，contained + experimental | Off | 可选本地 adapter | 独立工作目录、clean-env、外围 timeout、事后结果归一 | `action_interception=false`; `hard_cost_cap=false`; `pre_run_restore_point=false`; 不属于 Candidate 黄金路径 | [`adapter`](../../tests/executor/keqing/test_executor.py)；[`clean-env`](../../tests/security/test_clean_env.py) | deferred G4 work |
| remote MCP | Planned / 规划 | `disabled` | 无 Candidate 正式开放面 | secure-remote 下拒绝 | 完整 SSRF/DNS/redirect/proxy 安全尚未证明 | [`admission tests`](../../tests/security/test_mcp_lean_admission.py)；[`P2-A1`](../cc-fable-v1/06-deferred-work-backlog.md#p2-a1-remote-mcp-公开安全s24) | deferred |
| open stdio MCP | Planned / 规划 | `disabled` | 内部窄 allowlist，不是开放能力 | enabled 窄路径要求显式非空 `tools.include` | persistent exact grant、executable/argv/env/workdir drift binding 未完成 | [`admission tests`](../../tests/security/test_mcp_lean_admission.py)；[`P2-A2`](../cc-fable-v1/06-deferred-work-backlog.md#p2-a2-stdio-mcp-准入与漂移绑定s25) | deferred |
| official container / PyPI / GHCR / signing | Planned / 规划 | `deferred` | 当前正式路径仅 source 与 exact Wheel | — | 尚未发布官方容器、registry artifact、签名或正式 provenance | [`threat model`](../security/lean-preview-threat-model.md)；[`P2-A3/A4`](../cc-fable-v1/06-deferred-work-backlog.md#p2-a3-官方-exact-wheel-容器s26s67-部分) | deferred full G5 |

## S2 Lean security status / 安全边界

| Boundary | Status | Default / current limit | Evidence |
|---|---|---|---|
| SystemAudit tamper-evident chain | **Implemented / 已实现** | single-node SQLite；不是外部 WORM | [`S2 report`](../cc-fable-v1/reports/s2-lean-security-report.md) |
| MCP persisted secret mappings | **Implemented / 已实现** | env/header mapping 密文；错误 key/ciphertext fail closed | [`ciphertext tests`](../../tests/secrets/test_mcp_secret_migration.py) |
| remote MCP | **Disabled / Deferred** | Candidate 开放面保持 `disabled` | [`P2-A1`](../cc-fable-v1/06-deferred-work-backlog.md#p2-a1-remote-mcp-公开安全s24) |
| stdio exact grant / executable binding | **Deferred / 延期** | open stdio MCP 保持 `disabled` | [`P2-A2`](../cc-fable-v1/06-deferred-work-backlog.md#p2-a2-stdio-mcp-准入与漂移绑定s25) |
| container / PyPI / GHCR / signing | **Deferred / 延期** | 当前正式路径仅 source 与 exact Wheel | [`P2-A3/A4`](../cc-fable-v1/06-deferred-work-backlog.md#p2-a3-官方-exact-wheel-容器s26s67-部分) |

## Candidate 状态摘要

| Boundary | Truth state | Current limit | Evidence |
|---|---|---|---|
| S1/G1.5 source/exact Wheel | `implemented` | 本地 Darwin/arm64/Python 3.12.12；Ubuntu 外部复验未执行 | [G1.5 report](../cc-fable-v1/reports/g1.5-report.md) |
| S2 SystemAudit + MCP ciphertext | `implemented` | single-node/host-admin boundary | [S2 report](../cc-fable-v1/reports/s2-lean-security-report.md) |
| S3 durable governance + Evidence | `implemented` | managed Native and declared effects | [S3 report](../cc-fable-v1/reports/s3-core-governance-report.md) |
| S4 three pages | `implemented` automation | `user_approval_pending`; VoiceOver `external_pending` | [S4 report](../cc-fable-v1/reports/s4-core-web-report.md) |
| S5 Lean Core evolution | `experimental` with implemented path | full G4 `external_pending` | [S5 report](../cc-fable-v1/reports/s5-lean-evolution-report.md) |
| remote/open stdio MCP | `disabled` | reopening requires P2-A1/A2 | [deferred roadmap](../cc-fable-v1/06-deferred-work-backlog.md) |
| official container/PyPI/GHCR/OpenHands/ROI/cost calibration/full G5 | `deferred` / `external_pending` | not in Candidate | [deferred roadmap](../cc-fable-v1/06-deferred-work-backlog.md) |
