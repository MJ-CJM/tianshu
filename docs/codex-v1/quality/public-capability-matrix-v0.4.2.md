# Tianshu v0.4.2 Capability Matrix / 天枢 v0.4.2 能力事实矩阵

> This is the public source of truth for current capability claims. / 本表是当前公开能力承诺的事实源。

天枢的长期定位是：**天枢是一个可治理、可验证、持续成长的自进化 Agent OS**。这是一条产品方向，不代表 v0.4.2 已完成全部闭环。v0.4.2 面向 **trusted local / 可信本地**、单机、单节点使用；本地 HTTP、WebSocket 与 MCP 入口尚无统一身份认证，不得直接暴露到不可信网络。

成熟度定义：

- **Stable (limited) / 稳定（有限边界）**：列出的边界有实现和自动化测试证据；边界外不作保证。
- **Experimental / 实验**：实现可试用，但协议、恢复语义或安全边界尚未达到公开稳定承诺。
- **Planned / 规划**：路线图目标，不属于当前版本能力。

| Capability | Maturity | Default | Supported scope | Verified guarantee | Explicit non-guarantees | Evidence | Target gate |
|---|---|---|---|---|---|---|---|
| Native 本地主链与时间线 | Stable (limited) / 稳定（有限边界） | On | 可信本地、单进程、单节点、SQLite | Edict 经规划、Native 执行、审计形成记录；带标识的里程碑可写入 SQLite 时间线 | EventBus 不是持久队列；不保证分布式投递、故障点 exactly-once 或任意进程崩溃后的完整续跑 | [`src/tianshu/bootstrap/`](../../../src/tianshu/bootstrap/)；[`tests/test_integration_flow.py`](../../../tests/test_integration_flow.py) | G0 |
| Native 工具策略与事前裁决 | Stable (limited) / 稳定（有限边界） | On for Native | 内建 Native Agent 的已注册工具调用 | 工具 tier、策略规则和人工裁决钩在 Native 工具执行前生效 | 不拦截 opaque 外部 CLI 内部工具调用；等待中的裁决不耐进程重启 | [`policy_hook.py`](../../../src/tianshu/executor/policy_hook.py)；[`approvals.py`](../../../src/tianshu/executor/approvals.py)；[`test_hooks.py`](../../../tests/test_hooks.py)；[`policy_rules tests`](../../../tests/tools/policy_rules/)；[`test_decree.py`](../../../tests/test_decree.py) | G0 |
| 本地成本台账、脱敏、clean-env 与急停 | Stable (limited) / 稳定（有限边界） | Mixed | 可信本地 Native 路径与天枢启动的受支持子进程 | 已上报用量可归因入账；支持出站脱敏、子进程环境清理与分级急停 | 成本门禁依据已观测用量，可能超出阈值后才停；clean-env 不是 OS 沙箱；不保证网络隔离 | [`cost/`](../../../src/tianshu/cost/)；[`test_pricing_integration.py`](../../../tests/test_pricing_integration.py)；[`test_redact.py`](../../../tests/security/test_redact.py)；[`test_clean_env.py`](../../../tests/security/test_clean_env.py)；[`test_estop.py`](../../../tests/security/test_estop.py) | G0 |
| SQLite 迁移账本、升级备份与离线恢复 | Stable (limited) / 稳定（有限边界） | On when a pending baseline is detected | macOS/Linux 可信本地单 SQLite 文件；fresh、canonical v0.4.2、两种历史 supervision 结构与既有 session 结构 | 待迁移检查、在线 WAL 完整备份与事务迁移按数据库跨进程串行；ledger 校验版本、名称与 checksum；未知结构 fail closed；离线恢复先校验再替换 | 不接管其他 pre-ledger 结构；不是持续备份、PITR 或崩溃恢复系统；恢复要求目标离线；当前不承诺 Windows 文件锁；备份保留需人工管理 | [`migration_ledger.py`](../../../src/tianshu/storage/migration_ledger.py)；[`migrations.py`](../../../src/tianshu/storage/migrations.py)；[`sqlite_backup.py`](../../../src/tianshu/storage/sqlite_backup.py)；[`migration ledger tests`](../../../tests/storage/test_migration_ledger.py)；[`migration preservation tests`](../../../tests/storage/test_migration_preserves_data.py)；[`storage instance migration tests`](../../../tests/test_storage_instance_migration.py)；[`backup/restore tests`](../../../tests/storage/test_backup_restore.py) | G0 |
| Web 与 IM 裁决入口 | Experimental / 实验 | Optional | Web；Telegram 按钮；飞书命令回复 | 当前进程存活期间可查看并提交受支持的人工裁决 | 飞书不是交互按钮卡片；等待态不耐重启；不是原生移动端产品 | [`PendingToolCallCard.tsx`](../../../web/src/components/decree/PendingToolCallCard.tsx)；[`decrees.ts`](../../../web/src/api/decrees.ts)；[`Feishu command tests`](../../../tests/gateway/feishu/test_approval_commands.py)；[`Telegram callback/button tests`](../../../tests/gateway/telegram/test_callback.py) | G2 |
| Outer-loop checkpoint 与后台运行 | Experimental / 实验 | Opt-in | 启用 long-task execution profile 的任务 | 支持部分轮次 checkpoint 与受支持路径的暂停、恢复 | 不保证所有故障点、进程重启或外部副作用后的完整恢复 | [`src/tianshu/executor/checkpoint.py`](../../../src/tianshu/executor/checkpoint.py)；[`tests/test_outer_loop_resume.py`](../../../tests/test_outer_loop_resume.py) | G2 |
| 记忆、画像与技能候选成长 | Experimental / 实验 | Mixed | 本地 Markdown、SQLite/FTS、画像与技能候选流程 | 可积累记忆、合成画像并记录技能候选及评审结果 | 不保证这些变化提升真实任务效果；不会因此自动获得可信自进化闭环 | [`src/tianshu/memory/`](../../../src/tianshu/memory/)；[`src/tianshu/persona/`](../../../src/tianshu/persona/)；[`src/tianshu/skills/`](../../../src/tianshu/skills/) | G4 |
| Keqing 外部 Claude Code/Codex CLI | Experimental / 实验，contained + experimental | Opt-in per edict | 天枢启动的受支持 CLI adapter；独立工作目录 | 提供独立工作目录、clean-env、外围 timeout 与事后结果归一；已捕获的工具事件可交给外围链路 | 不保证 CLI 内部事前工具拦截、内部事件完整性、硬成本上限、运行前恢复点、网络隔离、耐重启或受治理的 apply/merge | [`src/tianshu/executor/keqing/`](../../../src/tianshu/executor/keqing/)；[`adapter tests`](../../../tests/executor/keqing/test_adapter.py)；[`executor workspace/result/timeout tests`](../../../tests/executor/keqing/test_executor.py)；[`clean-env tests`](../../../tests/security/test_clean_env.py)；[`gateway API tests`](../../../tests/gateway/test_keqing_api.py) | G4 |
| Universe 快照、分支、diff 与人工切换 | Experimental / 实验 | Evolution off | 本地 Universe 元数据与人工操作 | 可创建和比较受支持的行为/代码变体并人工切换 | 当前只路由 champion；无真实在线 challenger 流量；无可信自动晋升 | [`src/tianshu/universe/`](../../../src/tianshu/universe/)；[`docs/impl/universe/README.md`](../../impl/universe/README.md) | G4 |
| 配对评估与代码变体运行 | Experimental / 实验 | Manual | 隔离端口和数据库配置的本地子进程评估 | 支持以独立运行配置比较结果并生成评估记录 | 与宿主共享 OS、进程权限及网络；不是安全沙箱；评估结果不会自动成为可信晋升决定 | [`src/tianshu/evals/`](../../../src/tianshu/evals/)；[`tests/test_platform_eval_runner.py`](../../../tests/test_platform_eval_runner.py) | G4 |
| 统一身份、鉴权与安全远程访问 | Planned / 规划 | Not available | 规划中的 REST、WebSocket 与 MCP 公共入口 | — | v0.4.2 无公共远程部署安全承诺 | [G1 roadmap](../plans/00-master-roadmap.md#phase-1--public-safe-foundation) | G1 |
| 持久裁决、完整重启恢复、副作用账本与 Evidence Bundle | Planned / 规划 | Not available | 规划中的 durable governance boundary | — | v0.4.2 不保证 pending decision、任务和外部副作用在故障点的完整续接或去重 | [G2 roadmap](../plans/00-master-roadmap.md#phase-2--durable-governance--evidence) | G2 |
| 容器或 OS 级安全沙箱 | Planned / 规划 | Not available | 规划中的 managed executor | — | 当前 eval 子进程、独立目录与 clean-env 均不是安全沙箱 | [G1 roadmap](../plans/00-master-roadmap.md#13-统一外部执行边界) | G1 |
| 真实 challenger 路由与可信自动晋升 | Planned / 规划 | Not available | 规划中的 governed evolution | — | v0.4.2 无在线 challenger 分流和自动晋升 | [G4 roadmap](../plans/00-master-roadmap.md#42-真实-challenger-路由与晋升) | G4 |

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
