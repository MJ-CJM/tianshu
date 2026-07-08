# 成本基线 · Cost Baseline

> 宣发拍板(spec §七 #7):卖成本治理的平台**必须敢报自己的成本**。README 给
> 典型月成本区间——但数字要**真实跑一周实测**得来,不能拍脑袋。

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
| 模型路由 | persona/任务 → 便宜模型解耦 | `hint:` 路由表(litellm.Router) |
| prompt cache | Anthropic cache 命中降 input 成本 | `llm.py` 自动挂 |
| 上下文压缩 | 三层 compaction 削 token | `executor/compaction/` |
| 预算护栏 | 出厂每日上限,超限熔断 | `TIANSHU_DAILY_BUDGET_GUARDRAIL_CNY`(默认 ¥20) |
| 自进化开关 | 默认关;开了才有评测/变异成本 | 请旨解锁(ADR-0004) |
| 客卿凭证隔离 | 客卿烧它自己的额度,不烧天枢 key | `executor/keqing`(clean-env) |

## 待填(宣发前)

- [ ] 真实使用 ≥7 天
- [ ] 跑 `cost_baseline.py` 得区间
- [ ] 填入 [README.md](../../README.md) 与 [README.en.md](../../README.en.md) 的「成本透明」段
- [ ] 附一句典型任务的单次成本示例(如"一份 PR 日报 ≈ ¥X")

> 出厂每日预算护栏默认开(¥20)——"对钱包放手"是放手四保险第④条。
