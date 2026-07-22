# 风险登记表

| 风险 | 级别 | 当前事实 | 执行控制 |
|---|---|---|---|
| G1.4b3 长期 dirty 大批次 | Critical/流程 | 最新树约 7,400 行 code/test WIP，完整重验被暂停 | 重新 freeze；A/B 两提交；独立 review；单一 full Gate |
| 暂停前局部安全修复未完整验证 | Critical | mode、expiry、close lock、V4/V5、receipt truth 有 WIP 修复 | targeted → 17-file suite → static → review，禁止直接提交 |
| 旧 G2/G4 migration 号 | Critical | v3–v11 已失效 | 每 Gate 从真实 `MIGRATIONS[-1]` 动态 `N+1` |
| Governed apply 双权威 | Critical | G2 不得另建 apply/decision authority | 投影/reuse G1 WorkspaceService/Decision，不并行新增 |
| G2 continuation 不完整 | Important | 旧计划可能缺 multi-tool proposal/cursor/version CAS | 在写 migration 前冻结完整 RunState schema |
| Attempt fencing/副作用真值 | Critical | 不能仅靠 scheduler heartbeat 宣称恢复/一次副作用 | durable attempt/lease/fence + intent/receipt 故障矩阵 |
| Sensitive durable payload | Critical | checkpoint/evidence/audit 可能携带 secret/header/body | allowlist schema、加密引用、redaction negative tests |
| Evidence Bundle 字段漂移 | Important | phase plan 与 recon 完整字段不一致 | 先冻结 v1 schema/digest/closure/replay contract |
| Readiness/notification 生命周期 | Important | 旧计划可能在 receiver 未 ready 前宣称 ready | 明确启动/恢复/dispatch/shutdown 顺序并故障注入 |
| UI mock 被当生产事实 | Critical/产品 | G0 图含示例数字和 local-only 状态 | CI 禁止 `web/src` 引用 prototype/mockData；失败显示 error |
| 无口径“系统可信” | Important/产品 | 用户明确否决 | 只显示 readiness、合规率、证据完整率和可下钻口径 |
| 演化伪提升 | Critical/产品 | 静态 Canary/评分不能证明真实路由 | durable assignment、强门、样本、rollback、人工 veto |
| Executor 能力夸大 | Critical/产品 | JSONL 观测不等于 pre-tool interception | managed/contained/observed 分级和负向 compat tests |
| 资源/生命周期 warning | Important | 历史 sqlite/coroutine/进程警告可能被 pass 掩盖 | warnings 单独 Gate，检查 shutdown/fixture/child reap |
| 文档漂移 | Important | historical recon/impl/capability matrix 与 feature branch 不同 | SOURCE-OF-TRUTH 分级；恢复时刷新 STATUS |
| 大仓库 Git fingerprint 成本 | Important | object DB 全量哈希有 64MB/20万 entry 限制 | 能力文档披露；后续设计增量/缓存，不伪装支持 |
| 外部证据不可本机伪造 | Critical/发布 | OpenHands、Docker/Linux、OIDC、三环境、VoiceOver、七日成本未齐 | `external_pending`；runner 与真实 evidence 分开 |
| 未授权公开发布 | Critical/权限 | 用户只批准本地完成后审批 | 不 Public/tag/push PyPI/GHCR，等待最终明确授权 |

## 计划级风险修订

- G2 phase plan 必须先消费 `design/22-g2-recon.md`，不能逐字照旧 migration 执行。
- G4 phase plan中的固定版本号必须删除/动态化。
- G3 生产 UI 不能在 G2 contract 未稳定时连接内部 repository 结构。
- G5 本机三个目录/容器不等于三个独立外部环境。
- v0.4.2 capability matrix 只描述已发布版本，不证明当前 feature branch 新能力。
