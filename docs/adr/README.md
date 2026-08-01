# 架构决策记录

本目录保存 ADR（Architecture Decision Record），用于解释某项重要选择在形成时的背景、
取舍和结果。ADR 是决策历史，不是当前功能完成度清单。

阅读规则：

1. 先看 [当前实现与支持边界](../CURRENT-STATE.md) 和
   [能力事实矩阵](../launch/capability-matrix.md)；
2. 再用 ADR 理解“为什么这样做”；
3. 若 ADR 与当前源码或后续 ADR 冲突，以当前源码、测试和更新决策为准；
4. 不修改旧 ADR 来隐藏方向变化；需要改变决策时新增 ADR，并明确 `supersedes` 关系。

| ADR | 主题 |
|---|---|
| [0001](0001-mit-license-no-cloud-protection.md) | MIT 许可证与云服务边界 |
| 0002 | 产品定位（内部决策记录，未随仓库公开） |
| [0003](0003-trust-defaults-telemetry-optin-budget-guardrail-on.md) | 信任、遥测与预算默认值 |
| [0004](0004-evolution-off-by-default-unlock-by-memorial.md) | 演化默认关闭 |
| [0005](0005-narrow-gate-contribution.md) | 贡献与窄 Gate |
| 0006 | 重资产与叙事角色（内部决策记录，未随仓库公开） |
| [0007](0007-skill-curation-effect-gate.md) | Skill 筛选与效果 Gate |
| [0008](0008-court-deliberation-censor-structured-stance.md) | 会商结构 |
| [0009](0009-static-dag-no-dynamic-graph.md) | 静态 DAG |
| [0010](0010-jinyiwei-runtime-defense-in-depth.md) | 运行时纵深防御 |
| [0011](0011-keqing-external-executor-shadow-snapshot.md) | Keqing 外部执行器 |
| [0012](0012-decision-terminology-not-zhupi.md) | 裁决术语 |
