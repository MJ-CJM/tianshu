# 成本基线 · Cost Baseline

> 成本数字必须来自真实、可重复的测量，不能拍脑袋。G5 前完成实测后，才把典型
> 月成本区间写入公开 README。当前能力边界见[能力事实矩阵](capability-matrix.md)。

## 方法

1. 真实使用天枢 ≥ 7 天(覆盖你的典型工作负载,别空跑);
2. 跑测算脚本从成本账本推算:

   ```bash
   python scripts/cost_baseline.py --days 7
   ```

3. 脚本输出「典型月成本区间」(P25–P75 日成本 × 30),填进两个 README。

## 为什么用区间而非单值

成本随任务类型、模型选择、自进化是否开启波动很大。报**区间**(P25–P75)比报
单个"平均值"诚实:平均值被极端任务拉偏,区间反映真实分布。极重度用户看 P75,
轻度用户看 P25。

## 影响成本的主要旋钮

| 旋钮 | 省钱方向 | 落点 |
|---|---|---|
| 模型选择 | persona/任务按现有配置选择更合适的模型 | persona 与 Provider 配置 |
| prompt cache | Anthropic cache 命中降 input 成本 | `llm.py` 自动挂 |
| 上下文压缩 | 三层 compaction 削 token | `executor/compaction/` |
| 预算护栏 | 按已上报用量做 best-effort 检查，可能超调 | `TIANSHU_DAILY_BUDGET_GUARDRAIL_CNY`(默认 ¥20) |
| 演化开关 | 实验能力默认关；启用候选流程会增加评测/变异成本 | ADR-0004 |
| 客卿凭证隔离 | 客卿烧它自己的额度,不烧天枢 key | `executor/keqing`(clean-env) |

## 待填(宣发前)

- [ ] 真实使用 ≥7 天
- [ ] 跑 `cost_baseline.py` 得区间
- [ ] 填入 [README.md](../../README.md) 与 [README.en.md](../../README.en.md) 的「成本透明」段
- [ ] 附一句典型任务的单次成本示例(如"一份 PR 日报 ≈ ¥X")

> 出厂每日预算护栏默认开(¥20)，但它不是预付费额度或 provider 侧硬上限；公开成本示例必须同时说明可能超调。
