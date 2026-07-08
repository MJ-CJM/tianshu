# 决策台账(单一追踪入口)

> 所有战略/产品决策的**唯一索引**:每项决策可从此处追到 ①分析上下文(哪轮 grilling、哪份文档)②不可逆决策的 why(ADR)③排期落点(spec §八)。
> **状态流转**:`待验证`(自主拍板,等用户批)→ `已批准` / `已翻案`(翻案需回滚对应排期项并在此记录)。新决策一律追加至此。

## 第一批 · 战略拍板(2026-07-07/08,状态:已批准)

来源:brainstorming + grill-me 轮 + grill-with-docs 二至四轮;分析上下文见 [spec §一/§七](./2026-07-07-development-strategy-design.md)。

| ID | 决策 | ADR | 状态 |
|---|---|---|---|
| S1 | 12 个月目标=开源建影响力 | [0001](../adr/0001-mit-license-no-cloud-protection.md) | 已批准 |
| S2 | 单人+AI 高吞吐带宽 | — | 已批准 |
| S3 | 两段式发布(soft launch → 9 月中宣发) | — | 已批准 |
| S4 | 细粒度特性流(告别大 phase 分支) | — | 已批准 |
| S5 | 客卿执行器(迭代 3.5) | [0011](../adr/0011-keqing-external-executor-shadow-snapshot.md) | 已批准 · **已交付 v0.2.4** |
| S6 | 起居注·用户画像(迭代 4/6) | — | 已批准 |
| S7 | 明制补全(司礼监/六科/实录馆/巡按/京察,迭代 7) | — | 已批准 |
| S8 | 目标用户=重度 AI 个人用户 | [0002](../adr/0002-positioning-superior-office-of-claude-code.md) | 已批准 |
| S9 | 定位=「Claude Code 的上级机关」 | [0002](../adr/0002-positioning-superior-office-of-claude-code.md) | 已批准 |
| S10 | 首卖「敢放手」,自进化第二幕 | [0002](../adr/0002-positioning-superior-office-of-claude-code.md) | 已批准 |
| S11 | 双名场面(GIF 手机批红 + 视频客卿) | — | 已批准 |
| S12 | 放手四保险(影子快照最小版提前 3.5;出厂预算护栏) | [0003](../adr/0003-trust-defaults-telemetry-optin-budget-guardrail-on.md) | 已批准 |
| S13 | 首发国内,HN 留年末第二波 | — | 已批准 |
| S14 | 成本透明:README 给数字+默认每日预算上限 | [0003](../adr/0003-trust-defaults-telemetry-optin-budget-guardrail-on.md) | 已批准 |
| S15 | 度量:opt-in 遥测+代理指标,北极星=周活跃下旨实例 | [0003](../adr/0003-trust-defaults-telemetry-optin-budget-guardrail-on.md) | 已批准 |
| S16 | 自进化默认关+阈值「请旨解锁」,代码层永远手动 | [0004](../adr/0004-evolution-off-by-default-unlock-by-memorial.md) | 已批准 |
| S17 | 「一群 AI」诚实分层:权限矩阵是本体 | — (CONTEXT.md 锚定) | 已批准 |
| S18 | 首发期窄门贡献:先议后 PR | [0005](../adr/0005-narrow-gate-contribution.md) | 已批准 |
| S19 | 重资产角色分层:记忆升第二幕双引擎/协作打组织新陈代谢牌/harness 走内容轨道 | [0006](../adr/0006-heavy-assets-narrative-roles.md) | 已批准 |
| S20 | 技能修撰=展示窗口+迭代 6 补效果门 | [0007](../adr/0007-skill-curation-effect-gate.md) | 已批准 |
| S21 | 廷议=「大事慎决」治理原语+言官制度+废 confidence 换 stance | [0008](../adr/0008-court-deliberation-censor-structured-stance.md) | 已批准 |

## 第二批 · 全功能审计(2026-07-08 五轮,自主拍板,状态:**待验证**)

来源:grill-with-docs 五轮(用户授权自主分析);**分析上下文**(现状事实/诊断/业界对标/拍板理由)逐项见 [全功能竞争力审计](./2026-07-08-full-feature-competitiveness-audit.md)。

| ID | 决策 | 判定 | 排期落点 | ADR | 状态 |
|---|---|---|---|---|---|
| D1 | 多入口定位「治理随身」:批红/召廷议移动端同权,IM 信号入起居注 | 强化 | 迭代 4/5 随既有项 | — | 待验证 |
| D2 | 通政司通知三级制(紧急穿透/普通/低入 digest)+免打扰时段 | 强化 | 迭代 5 新增 | — | 待验证 |
| D3 | 实录馆=基于既有 DigestGenerator 升级,不从零建 | 联动 | 迭代 7 表述改 | — | 待验证 |
| D4 | 前端三视图叙事抛光(批红台/谱系树/时间线=可截宣发图) | 强化-轻 | 宣发准备期 | — | 待验证 |
| D5 | `tianshu doctor` 装机自检提前 | 强化 | 迭代 1 新增 | — | 待验证 |
| D6 | 明确不做 TUI | 维持 | 不做清单 | — | 待验证 |
| D7 | 条件调度(cron 前置哨兵);调度域定名钦天监 | 强化-后置 | 2027H1 | — | 待验证 |
| D8 | NL 敕令/Sweeper 达标不动 | 维持 | — | — | 待验证 |
| D9 | 六科预估超阈自动置 plan_review(票拟联动);重大 plan 可召廷议 | 联动 | 迭代 7 六科项内 | — | 待验证 |
| D10 | 规划质量指标(plan amend 率)进 Evals,喂京察 | 联动 | 迭代 2 Evals 项内 | — | 待验证 |
| D11 | DAG 维持静态,明确拒绝动态图 | 维持 | — | [0009](../adr/0009-static-dag-no-dynamic-graph.md) | 待验证 |
| D12 | 泳道/checkpoint 不加功能,journal/replay 维持 P3 | 维持 | — | — | 待验证 |
| D13 | 审计规则 YAML 化+热加载(用户自定义红线) | 强化-nice | 迭代 7 nice | — | 待验证 |
| D14 | 太医双出口:+「太医奏折」直接建议用户,与巡按衔接 | 强化-nice | 迭代 7 nice | — | 待验证 |
| D15 | MCP 治理三件套:准入清单+clean-env+诚实声明边界(不做流量代理) | 强化 | 迭代 3 **已交付**(v0.2.3,[ADR-0010](../adr/0010-jinyiwei-runtime-defense-in-depth.md)) | — | 待验证 |
| D16 | 凭证 Fernet 密文落库已达标(核实);补主密钥轮换脚本 | 强化-小 | 迭代 3 **已交付**(v0.2.3,`tianshu secrets rotate-master-key`) | — | 待验证 |
| D17 | EventBus 达标,OTel 复用 | 维持 | — | — | 待验证 |
| D18 | 成本治理前四轮已拍满,不再加 | 维持 | — | — | 待验证 |
| D19 | 京察=基于 PROFILE 数据的考核视图,不另建指标体系 | 联动 | 迭代 7 表述改 | — | 待验证 |

**验证方式**:逐项回复"批准"或"翻案(附理由)";翻案项回滚对应排期修改并在此表更新状态与原因。排期净影响:迭代 1 +1 天、迭代 3 +2 天、迭代 5 +1.5 天,均吃 must/nice 弹性,各迭代时间线不变;三处联动(D3/D9/D19)净省约一周。
