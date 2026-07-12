# 风险登记（CC-Fable v1）

> 继承 `codex-v1/RISK-REGISTER.md` 全部条目（其执行控制继续有效），本表只列
> **新增**与**状态变化**的风险。级别：Critical > Important。

## 新增风险（本现场特有）

| 风险 | 级别 | 事实 | 执行控制 |
|---|---|---|---|
| 未推送分支 + 未提交 WIP 单点丢失 | **Critical（P0 前最高危）** | `feat_codex_phase_1` 无 upstream；7,400 行 WIP 未提交，仅存于一台机器 | P0.1 冻结 + P0.2 bundle（D3 批准则加 push）；**本包获批后 P0.1/P0.2 第一时间执行** |
| 迁移损耗 | Important | freeze/fetch/ff 任一步骤异常可能造成不完整迁移 | 每步指纹对齐（25 条目 / +2703/−104 / 44 commits）；P0.5 干净基线全绿后才还原 WIP；bundle 兜底回退 |
| main 在执行前前进 | Important | merge-base 验证时间为 2026-07-12；若 origin/main 又合并新 PR，快进不再成立 | P0.3 执行前重验 merge-base；不成立则暂停改走 merge/rebase 评估，不得强推 |
| 双现场漂移（P0 后） | Important | 源 clone 若继续被改动，两边分叉 | P0.7 封存声明 + D2 唯一现场纪律；源 clone 只读 |
| 新 venv 环境差异 | Important | 当前工作区无 `.venv`，需重建；uv 版本 0.9.27 | `uv sync --frozen` 锁定依赖；P0.5 在干净 HEAD 全量验证，红则停 |
| WIP 内 V4 迁移 callback 改写疑点 | **Critical** | `codex-v1/STATUS.md` 自披露：V4 callback 被改写但 checksum 冻结，违反迁移冻结规则 | S0.2 最优先裁决：恢复 V4 原样 + 改动入新迁移，或给出合法性证明并补 checksum 策略；V5 必须纯 additive |
| Claude 环境无 superpowers 工具链 | 低 | codex 文档含 `superpowers:*` 提示 | handoff 已豁免为非依赖；按 01 号计划第 10 节映射执行 |

## 状态变化（相对 codex-v1 登记表）

| 原风险 | 变化 |
|---|---|
| "G1.4b3 长期 dirty 大批次"（Critical/流程） | P0.1 后 WIP 固化为冻结提交，不再裸露；风险转为上表"迁移损耗"，S0 收口后彻底关闭 |
| "暂停前局部安全修复未完整验证"（Critical） | 不变，仍按 targeted → 17-file suite → static → review 顺序在 S0 重验，禁止直接提交 |
| 其余条目（迁移号、双权威、fencing、sensitive payload、UI mock、演化伪提升、executor 夸大、外部证据、未授权发布等） | 全部原样继承，无变化 |
