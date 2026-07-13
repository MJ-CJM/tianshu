# 风险登记（CC-Fable v1）

> 更新：2026-07-14，D8-A 精简 Developer Preview。
>
> 继承 `codex-v1/RISK-REGISTER.md` 的技术风险；本表记录当前执行包的状态变化和
> D8 范围风险。级别：Critical > Important > Low。

## 1. 当前活动风险

| 风险 | 级别 | 当前事实 | 执行控制 |
|---|---|---|---|
| 完整路线与 Lean 范围互相覆盖 | **Critical / 流程** | 01 号文档保留完整 G0–G5，D8-A 只批准第一阶段核心闭环 | 当前范围只以 [05 号文档](./05-lean-developer-preview-scope.md) 为准；延期项只进 [06 号台账](./06-deferred-work-backlog.md)；恢复前重新批准独立计划 |
| 延期能力被误写为已支持 | **Critical / 诚信** | 容器、remote/第三方 MCP 开放、managed OpenHands、ROI/成本和完整 G4/G5 均未进入第一阶段 | 能力矩阵与报告逐项标 `external_pending`、实验性、未启用或 deferred；Lean Core Gate 不得替代完整 G4-A/B/C |
| MCP 开放面安全缺口 | **Critical / 安全** | D8 只做 SystemAudit、密文和最小公开护栏，完整 S2.4/S2.5 延期 | remote MCP 和新的未审批 stdio 配置默认禁用并 fail closed；要开放必须先完成 06 号 P2-A1/P2-A2 |
| 治理持久化范围被过度宣称 | Important | S3 Core 只承诺 SQLite 单机耐重启，不承诺 PostgreSQL/K8s/多副本 | 故障矩阵和能力矩阵明确单机语义；不得用单机证据支持分布式声明 |
| 三核心页掩盖其余部门未产品化 | Important | 第一阶段保留十四部门导航，但只深做中枢总览、敕令详情、演化中心 | 延期页面真实显示已有能力/Preview/未接入；禁止复制 mock 或虚构状态；完整收敛进 06 号 P2-C |
| Wheel CI 被误当作“重发行工程”删除 | Important | Wheel/sdist 黑盒已经发现过源码路径、sdist 遗漏与完成时序等真实缺陷 | Wheel/sdist CI 保留为第一阶段回归门；只延期 Docker、注册表、签名/provenance 等发行工作 |
| 无官方容器降低初次安装便利性 | Important | Dockerfile 只视为 legacy/experimental，不是第一阶段验收证据 | README 明示源码/Wheel 官方路径；用户决定把容器列为一等安装方式时恢复 P2-A3 |
| 外部证据等待造成错误 Gate 状态 | Important | OpenHands、100+ outcome、七日成本窗、三个外部环境不在第一阶段 | 外部证据和本地 demo/CI 分开；缺证据保持 `external_pending`，不得为赶 Gate 伪造 |
| 延期台账随实现演进而漂移 | Important | 第二阶段可能在数个阶段后启动，迁移号、API、测试基线会变化 | 06 号启动检查表强制 fresh recon；动态确定迁移号和公共契约，旧 brief 只作设计输入 |
| 连续实施造成范围悄然膨胀 | Important / 流程 | D5-B 允许 Gate 间不停，但 D8 已缩小范围 | 连续实施只适用于 05；任何延期项加入本轮必须先修订 D8、05 和实施计划 |
| 视觉终审与自动化状态混淆 | Important / 产品 | 自动化可通过，但用户还未审批最终页面 | 分开记录 `automation_passed` 与 `user_approval_pending`；未终审不得写“视觉已批准” |
| 外部发布越权 | **Critical / 权限** | 候选完成不代表允许改变公共远端或注册表 | repo Public、tag/release、PyPI/GHCR、OIDC、对外宣发逐项等待用户明确授权 |

## 2. 已关闭或降级的现场风险

| 原风险 | 当前状态 | 关闭证据/后续关注 |
|---|---|---|
| 未推送分支 + 未提交 WIP 单点丢失 | **closed** | P0 已完成资产回收、冻结与唯一现场迁移；后续只在本工作区继续 |
| 迁移损耗 / main 前进 / 双现场漂移 | **closed** | P0 验证与台账已完成；旧 clone 不再是开发现场 |
| 新 venv 环境差异 | **closed** | 基线已重建，后续由 Wheel/fresh HOME/Doctor 持续检测 |
| V4 migration callback 改写与 checksum 缺口 | **closed** | S0.2 恢复/冻结迁移并补 callback 源码指纹守卫，G1.4b3 Gate 已通过 |
| G1.4b3 长期 dirty 大批次 | **closed** | Commit A `29ef814`、Commit B `a9106aa` 与 G1.4b3 报告完成 |
| execution gateway 过大 | **materially reduced** | P1.R1 `e0bdf74` 完成等价拆分；其余体量候选仍按 04 号报告顺路处理 |
| S1 打包只验证 Wheel、不验证 sdist | **closed for S1 implementation** | S1.5 `498b1e4` 加入 sdist→Wheel→install 与 exact-wheel 黑盒；S1 总 Gate 仍待执行 |

## 3. 继承风险仍然有效

以下 `codex-v1/RISK-REGISTER.md` 类别没有因 D8 自动消失，进入对应切片时继续执行
原控制：迁移号、双权威、fencing、sensitive payload、UI mock、演化伪提升、
executor 夸大、外部证据不可替换、未授权发布。

如果实现发现这些风险中的任一项阻断核心闭环，应先把事实、影响和最小替代方案写入
`PROGRESS.md`；不能用“延期”掩盖第一阶段已经依赖的安全或真值缺口。
