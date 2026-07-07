# 位面竞争力优化执行记录(2026-07-04)

> 对应计划:[docs/superpowers/plans/2026-07-04-universe-competitiveness.md](../superpowers/plans/2026-07-04-universe-competitiveness.md) · 分支 `feat_phase8` · 20 commits(b2cf7f4..bfffc8e)
> 执行方式:每任务「实施 → 独立审查 → 修复 → 重审」;全分支最终审查(含跨任务视角)裁决 READY。
> 收官验证:全量 1399 passed(基线 1364,净增 35)/ ruff check+format 双净 / 路由快照仅设计内新增 1 条 / 前端 tsc+build 通过。

## 批次 1 · 修真——P0 有效性修复(5 任务)

| 任务 | Commit | 内容 |
|---|---|---|
| T1 探索路由退役 | `c92f08c` | challenger 不真正运行,route_for_memorial 一律归冠军(仅归因);在线 fitness 只累积给冠军;删 `universe_explore_ratio`/`universe_challenger_fail_limit` 两死配置与 `_retire_failing_challengers` |
| T2 沙箱 extra_env | `0676445` | 统一 env 注入口(安全围栏字段 DB_PATH/PORT/HOST/EVAL_MODE 不可被覆盖),为行为层评估与凭证隔离铺路 |
| T3 评估预算闸 | `2e7c2d8` | `code_variant_eval_budget_cny` 默认 20 元/次,逐 goal 查累计成本触顶截断(truncated),已回放部分照常打分 |
| T4 配对基线 | `8fbc102` + 修复 `4743fac` | 冠军在同评估集、同沙箱环境跑基线,margin 判配对差;基线按指纹缓存(`variant_eval_runs.baseline_json` 迁移加列)。审查抓 2 Important:指纹不含主干 HEAD(日常 commit 会静默复用陈旧基线→`_baseline_key()`=冠军id:短HEAD)、截断基线不入缓存 |
| T5 行为层沙箱评估 | `f25dc36` | 变异落地后立即配对评估(TIANSHU_RUNTIME_PERSONAS_DIR 重定向 personas),**行为层变异首次被真正测量**;delta 三路分流(≤-margin 归档 / ≥margin 且样本足推荐或 auto 晋升 / 带内留观);`_maybe_promote` 旧口径退役 |

## 批次 2 · 补脑——演化算法(3 任务)

| 任务 | Commit | 内容 |
|---|---|---|
| T6 演化记忆 | `ac57538` | 近 20 条变异台账(理由/得分/结局)进变异 prompt + 避重指令 |
| T7 太医诊断器 | `ad5f2c9` + 修复 `1225486` | 新模块 `diagnostician.py`:失败奏折(goal/error/audit.reasons)+ 已试假设 → 演化域内代码假设清单,失败安全。实施者纠正 brief 两处与真实不符(list_memorials 返回 tuple;Memorial.audit 非 audit_json);审查抓 fake 签名失真→修复轮补齐 |
| T8 自主提案接线 | `c1c7a9f` + 补测 `ef0b0c5` | `auto_propose_codes` 配额内逐个走 propose 闭环(`code_variant_auto_propose` 默认关 / 配额默认 2);cron `universe.daily_code_propose` 05:30;`POST /api/universes/propose-auto`(唯一新增路由);事件 `universe.code_proposed` |

## 批次 3 · 加固与信任面(4 任务)

| 任务 | Commit | 内容 |
|---|---|---|
| T9 评估集分层 | `4053513` + 修复 `8289626` | 约 60% 成功 + 40% 失败混采。**控制者拦截 Critical**:初版为满足 brief 往 EdictStatus 加 FAILED 枚举,但 edict 无 failed 生命周期(失败挂 memorial 层),失败层生产恒空=伪功能→修复改从 memorial 层采样 + 守门用例 |
| T10 凭证隔离 | `1778618` | `TIANSHU_EVAL_LLM_API_KEY/API_BASE/MODEL`:配置后沙箱进程 LLM 走低额度专用 key(审查全程走查生效链:env→子进程→TianshuSettings→ConfigManager 空库回落) |
| T11 前端信任面 | `f419fda` + 修复 `48521e2` | 位面谱系树(Tree);晋升改为审批 Modal(diff+评估+基线分+delta 同屏,确认才晋升);自主提案按钮;types fitness 类型修正。审查抓 2 Important:孤儿子树从树中消失(伪根兜底)、审批视图请求竞态(seq 守卫) |
| T12 文档同步 | `a5c8e4d` + `8a06fa4` + `0df6f73` | design/universe 三篇 + impl/universe/README + 谱系 README:配对评估/探索退役/诊断器/预算闸/凭证隔离全对齐;消除"不做影子重放"矛盾句;grep 扫尾零残留 |
| 终审修复 | `bfffc8e` | 最终审查 3 发现:`_eval.db` 固定名并发互踩(Important,改 uuid 唯一命名)、行为层截断标与代码层对齐、evolver docstring 校准 |

## 审查体系拦下的问题(实施报告之外的净增价值)

1. **Critical(控制者拦截)**:T9 初版 EdictStatus.FAILED 伪修复——失败层生产恒空 + 公共枚举膨胀(PATCH API 悄悄接受无标签新状态);
2. **Important**:配对基线指纹不含主干代码版本——活跃仓库日常提交会静默复用陈旧基线(审查员用变异测试实证接线缺口);
3. **Important**:`_eval.db` 固定名——双 cron 并发评估共写同一 sqlite 且先完者 unlink 对方在用库,delta 失真(全分支终审的跨任务视角发现);
4. **Important**:前端孤儿子树消失(生产可复现:delete 无子节点检查+无 FK)与审批视图竞态(信任关口放大);
5. T7 brief 两处假设与真实代码不符,实施者主动纠正;多轮审查以"变异测试/红黑对照/生效链走查"实证,非采信报告文字。

## 遗留事项(下轮候选)

1. 测试套件偶发抖动(feishu webhook / universe switch,疑 hash-seed 或既存 async 泄漏,litellm "coroutine never awaited" 警告佐证)——单独排期稳定化;
2. T5/T9 留后续用例缺口(delta 恰好 ±margin 边界、samples 不足留观、评估异常路径、反向回填)——终审 triage 裁决"分支实现均正确,留后续";
3. 双 cron 不同锁仍共享 LLM 配额/沙箱资源(iso_db 互踩已修,资源节流未做,失败安全成立);
4. `evaluate(seed_db=...)` 两个已知边界(文档已注明):seed_db 含 llm_configs 时 DB-first 盖过 eval key;健康失败路径可能残留孤儿 `_eval-*.db`(当前无调用点传 seed_db);
5. 行为层评估只重定向 personas 目录——将来扩变异面(skills/config)需同步扩 env 重定向。

## 配置面变化汇总

| 变化 | 项 |
|---|---|
| 删除 | `universe_explore_ratio`、`universe_challenger_fail_limit` |
| 新增(AgentConfig) | `code_variant_eval_budget_cny=20.0`、`code_variant_auto_propose=False`、`code_variant_daily_propose_quota=2` |
| 新增(TianshuSettings/env) | `TIANSHU_EVAL_LLM_API_KEY`、`TIANSHU_EVAL_LLM_API_BASE`、`TIANSHU_EVAL_LLM_MODEL` |
| 语义迁移 | `universe_min_samples`:在线样本量 → 配对评估参与晋升的最低样本数 |
| 新增路由 | `POST /api/universes/propose-auto` |
| 新增 cron | `universe.daily_code_propose`(30 5 * * *) |
| 表结构 | `variant_eval_runs` 加列 `baseline_json` |
