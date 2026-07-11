# Eval Harness 与 Fitness 门禁

> 自改平台的命门：一份自动生成的代码变体在碰真实任务前，必须先在隔离沙箱里**回放历史目标**、按统一适应度**打分**、与现冠军**回归比对**，再过门禁才配被人工晋升。本篇讲「为什么这样评 + 机制怎么转」。
>
> **相关实现**：[../../impl/universe/README.md](../../impl/universe/README.md)
> **相关设计**：[./code-variant.md](./code-variant.md)、[./evolution.md](./evolution.md)

## 1. 为什么要专门的评估底座

代码变体是无界风险面（崩溃 / 死循环 / 泄漏 / 静默劣化），「择优晋升」需要 fitness，但**不能拿可能损坏的代码跑真实流量**。于是评估底座承担三件事：

| 诉求 | 机制 |
|---|---|
| 变体行为可量化 | 回放历史代表性目标 → 聚合五维信号 → 一个标量 score |
| 与冠军可比 | 变体和冠军共用同一 `compute_fitness` 语义、同一评估集，分数同尺度 |
| 评估期绝不伤生产 | 跑在隔离子进程 + 隔离 DB 副本 + `EVAL_MODE` 副作用围栏 + 资源闸里 |

`EvalHarness`（`eval_harness.py`）是回放打分主体，`compute_fitness`（`fitness.py`）是归一聚合纯函数，`Gate`（`gate.py`）是打分前的硬门禁，`Deployer`（`deployer.py`）管晋升落地。编排者是 `UniverseEvolver.propose_code_variant`（`evolver.py`）。

## 2. Eval Case：定义与历史目标选取

一个 eval case = **一条历史 goal 字符串**。评估集是这些 goal 的去重列表。

`EvalHarness.select_eval_set(size)` **分层混采**：约 `int(size*0.4)` 条最近失败 + 其余（约 60%）最近成功，跨层去重，任一层数据不足时用另一层补齐到 `size`：

- **失败层**（`_collect_failed_goals`）：`edict` 没有 `failed` 生命周期——失败是 **memorial 层**的终态，不能像成功层那样直接按 edict 状态过滤（永远查不到）。做法是 `storage.list_memorials(status="failed", limit=want*3)`，逐条反查 `storage.get_edict(m.edict_id)` 取 `edict.goal`。
- **成功层**（`_collect_goals("completed", ...)`）：取 `storage.list_edicts(status="completed", limit=want*3)`，按创建时间倒序，逐条取 `edict.goal.strip()`。
- 两层各自**按文本去重**，且跨层互斥（已入选的 goal 不会在另一层重复出现）。

为什么用「历史目标」而非合成用例：这些是平台真实承接过的任务，天然贴近真实分布。为什么还要混入失败样本：纯成功集下，配对评估里变体与冠军基线都是 100% 通过，变体唯一能拉开的差距只剩 cost/audit/retry 这些次要维度——**无法证明「修好了冠军跑不动的任务」**。混入历史失败 goal 后，配对基线下冠军在这些样本上大概率同样失败，变体若真修好，直接体现为正 `delta`；没修好，两边同样失败也不构成误伤。这一选集口径同时服务于代码变体与行为层候选评估（见 §5）。

## 3. 沙箱变体回放与轮询至终态

`EvalHarness.evaluate(worktree, *, eval_set, seed_db, goal_timeout_s=300)`：

```
iso_db = worktree.parent / "_eval.db"        # 放 worktree 同级，保持 worktree 文件系统干净
若 seed_db: 拷贝为 iso_db                      # 可携带 persona / LLM 配置作初始数据
with sandbox.session(worktree, db_path=iso_db) as h:   # 隔离子进程拉起变体
    for goal in eval_set:
        _run_goal(h.base_url, goal, goal_timeout_s)     # 串行回放
    stats = aggregate_db_stats(h.db_path)               # 聚合沙箱 DB 全部 memorial
return {"fitness": score(stats), "stats": stats, "n": len(eval_set)}
```

**沙箱**（`SandboxRunner.session`）经统一 `ExecutionGateway` 从变体 worktree 拉起受治理子进程：临时空闲端口 + `TIANSHU_DB_PATH` 指向隔离 DB + `TIANSHU_EVAL_MODE=1`；`PYTHONPATH=<worktree>/src` 精确前置，确保 editable 安装下跑的是**变体代码**而非主仓。等 `/health` 健康才返回，退出时收敛整个进程组并删 DB。`trusted-local` 的显式宿主回退会在收据中标明未强隔离；`secure-remote` 无可证明后端时拒绝启动。

**回放单条 goal**（`_run_goal`）走的是真实 HTTP 路径，不偷工：

```
POST {base_url}/api/edicts  {"goal": goal}  → data.id           （提交失败仅 warning，eval 继续）
轮询 GET /api/edicts/{id}/memorial  每 2s    → data.status
  status ∈ {completed, approved, failed, rejected} → 终态，返回
  超 goal_timeout_s 仍未终态 → warning 放弃（不抛异常，不卡死整个 eval）
```

终态集 `_TERMINAL_STATUSES = {completed, approved, failed, rejected}` —— 与下方成功口径一致。超时与提交失败都**不致命**：评估必须能跑完整集，单点卡死不能拖垮自我改进闭环。

## 4. 五维指标 + 权重 + 归一聚合

回放完毕，`aggregate_db_stats(db_path)` 直接读沙箱 DB 的全部 `memorials`，聚合成 7 个原始量（与 `storage.universe_memorial_stats` 同构，但不按 `universe_id` 过滤 —— 沙箱 DB 只有本次 eval 的数据）：

| 原始量 | 口径 |
|---|---|
| `total` | memorial 总数 |
| `success` | status ∈ {completed, approved} 的数量 |
| `retries` | Σ max(0, attempt-1) |
| `audited` / `audit_pass` | 有 audit 的数 / 其中 verdict=="pass" 的数 |
| `cost` | Σ usage_json.cost_cny |
| `feedback` | Σ feedback_score（累积整数，可正可负） |

`compute_fitness(stats, weights)` 把它们归一到 [0,1] 再加权。默认 **weights=(0.4, 0.15, 0.2, 0.1, 0.15)**，依次对应 (success, cost, audit, retry, feedback)：

| 维度 | 归一公式 | 方向 | 默认权重 |
|---|---|---|---|
| `success_rate` | success / total | 越高越好 | 0.40 |
| `cost_score` | `avg = (cost*1000)/total`；`1/(1+avg)`，无成本=1 | 越低越好（反向分） | 0.15 |
| `audit_rate` | audit_pass / audited | 越高越好 | 0.20 |
| `retry_score` | `max(0, 1 - (retries/total)/2)` | 越低越好（反向分） | 0.10 |
| `feedback`(fb_norm) | `0.5 + 0.5·fb/(1+|fb|)`，把累积整数 squash 到 (0,1) | 越高越好 | 0.15 |

```
score = 0.4·success_rate + 0.15·cost_score + 0.2·audit_rate
      + 0.1·retry_score + 0.15·fb_norm
```

返回 `{score, samples, success_rate, audit_rate, retry_score, cost_score, feedback}`，score 四位小数。所有分母为 0 时 `_safe_ratio` 返回 0.0，不会除零。

**设计取舍**：成功率独占 0.4 —— 跑通是底线；cost/retry 取反向分鼓励「又对又省」；feedback 用 squash 而非线性，避免单条极端反馈淹没其余维度。这套权重既给行为层位面（`universe_memorial_stats` → `compute_fitness`）用，也给代码变体（`aggregate_db_stats` → `compute_fitness`）用 —— **同一适应度语义保证两者可比**。

## 5. 变体 vs Champion 回归检测

早期实现曾拿变体的沙箱评估分与冠军的**在线累积分**比 margin——评估集、运行环境、统计时段三者都不同，是跨分布比较，噪声对噪声。现改为**同评估集配对评估**：冠军在同一个 `eval_set`、同一套沙箱环境下也跑一次，margin 判在两者的配对差 `delta` 上：

```
paired = eval_harness.evaluate_paired(
             variant_worktree, eval_set=es, baseline_worktree=champion_repo_or_worktree,
             variant_env=..., budget_cny=..., cached_baseline=cached)
delta  = paired["variant"]["fitness"]["score"] - paired["baseline"]["fitness"]["score"]
margin = cfg.universe_promote_margin  (默认 0.05)
delta >= margin  →  status="recommended"（或 auto_promote 开启时直接晋升）
否则             →  status="evaluated"
```

**基线按评估集指纹缓存**：`eval_set_fingerprint(eval_set, champion_key)` = 评估集内容 + `champion_key` 的 sha256 前 12 位；`champion_key`（由 `evolver._baseline_key()` 构造）取「冠军位面 id : 主干短 HEAD」。把主干 HEAD 编进指纹，是因为冠军的「代码基线」就是主干当前提交——**任何一次主干提交都会让指纹变化，从而使旧基线缓存自动失效**，不会拿过期基线继续比较。`Storage.latest_baseline_fitness(fp)` 命中同指纹的历史基线时，`evaluate_paired` 直接跳过冠军那一次沙箱回放（评估成本减半，`baseline_cached=True`）；未命中则冠军也真跑一次，跑完连同指纹一并存进 `variant_eval_runs.baseline_json`，供下次同指纹复用。**若冠军基线评估本身被预算闸截断**（见 §9），该次基线不入缓存——截断样本不能冒充完整基线继续被复用。

`margin` 仍是**回归带宽**：变体必须在配对差上赢一个肉眼可辨的差距才算「值得晋升」，落在带内的微小波动不构成晋升理由 —— 防止评估噪声驱动无意义的代码翻动。注意 `recommended` 也**只是推荐**，默认不自动晋升（见 §7）。

每次评估（无论结果）都落 `variant_eval_runs` 表（`save_variant_eval_run`，变体分入 `fitness_json`、基线分入 `baseline_json`），并 `update_universe_fitness(uid, fitness)` 把变体分写回该位面，供 Web UI 看历史。

## 6. 晋升门禁语义：Gate 三关 + margin

打分只是**晋升资格链**的最后一环。完整门禁是「三关硬门禁（fail-fast）→ 评估打分 → margin 回归 → 人工审 diff」。`Gate.run(worktree)` 在 `evaluate` **之前**跑，逐级短路：

| 关 | 命令（注入 `PYTHONPATH=<worktree>/src` + cwd=worktree） | 失败即毙 |
|---|---|---|
| ① static | `python -m compileall -q <worktree>/src` | 连编译都不过 |
| ② import | `python -c "import tianshu"` | import 冒烟失败 |
| ③ test | `python -m pytest -q`（worktree 内全量） | 任一测试红 |

`GateResult(passed, stage, detail)`：失败时 stage ∈ {static, import, test}，detail 取末尾输出（`_tail`，截 2000 字）；全过则 `stage="ok"`。任一关失败，`propose_code_variant` 落一条 `gate_passed=False` 的 eval run（detail 截 1000 字）并返回 `status="gate_failed"`，**根本不进评估**。整链超时由 `Gate` 的 `timeout_s`（默认 900s，对应配置 `code_variant_sandbox_timeout_s`）兜底。

晋升资格链：

```
gate ①②③ 全绿  →  沙箱配对评估(变体 vs 冠军基线，同评估集)  →  delta ≥ margin (recommended)
  →  人工审完整 diff + eval 记录  →  Deployer 晋升
```

**晋升落地**（`manager.promote_code_variant` + `Deployer`）：翻状态为冠军 + `deployer.stage` 只写 `current_ref` 指针**不重启**（重启是单独受控步骤）；真重启时 `os.execv` 自重启，重启后查 `/health`，不健康则指针翻回 previous 自动回滚 —— 坏晋升绝不让平台停摆。详见 [./code-variant.md](./code-variant.md) §7。

## 7. Worked Example：评估一个「persona 变异」代码变体

假设变体在演化域内改写了 selector 的人格选择逻辑（hypothesis：「对运维类 goal 更倾向选择稳健 persona」）。闭环（`propose_code_variant`）：

```
1. branch_code_variant(champion) → worktree + 分支 universe/<id>
2. code_mutator.mutate(worktree, target_path, hypothesis)  →  applied=True, 提交一个 commit
3. gate.run(worktree):  compileall ✓  →  import tianshu ✓  →  pytest -q ✓   (stage=ok)
4. select_eval_set(20):  分层混采，约 12 条最近成功 + 8 条最近失败（含若干运维类 goal）
5. eval_set_fingerprint(es, champion_key) 未命中 latest_baseline_fitness 缓存 → 基线需真跑一次
6. evaluate_paired(worktree, eval_set=es, baseline_worktree=repo_root, budget_cny=20.0):
     variant = evaluate(worktree, ...):
       sandbox 起隔离进程(临时端口 + _eval.db + EVAL_MODE)
       逐条 POST /api/edicts 回放，轮询 memorial 至终态
       aggregate_db_stats:  total=20 success=20 audited=18 audit_pass=18
                            retries=3 cost=1.2 feedback=4
       compute_fitness → success_rate=1.0  audit_rate=1.0  retry_score≈0.925
                         cost_score=1/(1+(1.2*1000/20))≈0.0164  fb_norm≈0.9
                         score = 0.4·1 + 0.15·0.0164 + 0.2·1 + 0.1·0.925 + 0.15·0.9 ≈ 0.83
     baseline = evaluate(repo_root, ...):  主仓（冠军）代码在同一 eval_set 上再跑一次 → score ≈ 0.80
     delta = 0.83 - 0.80 = 0.03，baseline_cached=False（本次基线是真跑出来的）
7. save_variant_eval_run(fitness=variant 分, baseline=baseline 分, eval_set_version=指纹, ...)
     + update_universe_fitness(uid, variant 分)
8. margin=0.05  →  delta(0.03) ≥ margin ?  否  → status="evaluated"（达标但未过 margin，不推荐）
   若这次基线表现更差（score≈0.76）→ delta=0.07 ≥ 0.05 → status="recommended"
9. 任一步异常 → status="error"，失败安全，不留半截状态；若基线评估本身被预算闸截断，则该次基线不写入缓存（见 §9）
```

可见 cost_score 在这套权重下很小（0.15 权重 × 0.016），主导分的是成功率与审计率 —— 评估真正盯的是「跑得对、审得过」，成本只作微调。最终是否晋升仍由人看 §6 的完整 diff 拍板，平台默认 `code_variant_auto_promote=False`。

## 8. 配置项

| 配置 | 默认 | 作用 |
|---|---|---|
| `code_variant_eval_set_size` | 20 | 回放评估集规模（`select_eval_set` 上限，60% 成功 + 40% 失败混采，见 §2）|
| `code_variant_eval_budget_cny` | 20.0 | 单次沙箱评估的成本闸（元），触顶截断，已回放部分照常打分，详见 §9 |
| `code_variant_sandbox_timeout_s` | 900 | Gate 全程 + 沙箱单步超时 |
| `universe_promote_margin` | 0.05 | 回归带宽：变体须在配对 `delta` 上赢此差距才 `recommended` |
| `code_variant_auto_promote` | False | 代码层自动晋升（默认关，明确不推荐开）|
| `TIANSHU_EVAL_LLM_API_KEY` | 空 | 沙箱评估专用 LLM key（env-only，非 `AgentConfig` 热更字段）；空则沙箱沿用宿主 `TIANSHU_LLM_*` 凭证 |
| `TIANSHU_EVAL_LLM_API_BASE` | 空 | 配合上者的可选 base url 叠加，仅当已设 key 才生效 |
| `TIANSHU_EVAL_LLM_MODEL` | 空 | 配合上者的可选模型名叠加，仅当已设 key 才生效 |

`goal_timeout_s`（单条回放轮询超时）默认 300s，由 `evaluate` 形参控制。

## 9. 预算闸与截断语义

沙箱回放烧的是真实 LLM 调用费用，且此前无上限。`code_variant_eval_budget_cny`（默认 20 元）给单次 `evaluate()` 加了一道成本闸：

```
for goal in eval_set:
    _run_goal(...)                          # 逐条回放
    if budget_cny is not None and aggregate_db_stats(db)["cost"] >= budget_cny:
        truncated = True
        break                                # 已回放部分照常聚合打分
stats = aggregate_db_stats(db)
return {"fitness": score(stats), "stats": stats, "n": ran, "truncated": truncated}
```

**失败安全，只截断不作废**：触顶后停止回放剩余 goal，但已跑完的那部分照常聚合进 `compute_fitness`——评估绝不会因预算耗尽而整体报废。返回 dict 多一个 `truncated: bool` 键；为 True 时，`propose_code_variant` / `_evaluate_behavior_challenger` 会把它并进 `fitness_json`（`{**fitness, "truncated": True}`），供 Web UI 的评估记录表标注「预算截断」，提示这次分数基于不完整回放算出、可信度打折。

**配对评估下预算各自独立计价**：`evaluate_paired` 对变体、基线各自独立传 `budget_cny`——未命中基线缓存时，一次配对评估最多花两倍预算；命中基线缓存（见 §5）则只有变体那次真花钱，评估成本减半。

**截断的基线不入缓存**：若冠军基线评估本身被截断，`save_variant_eval_run` 存入的 `baseline` 字段为 `None`（不落 `baseline_json`），`latest_baseline_fitness` 的查询条件带 `baseline_json IS NOT NULL`，自然不会把这次不完整的基线当成可复用缓存——避免下一次评估拿一个「没跑完」的基线继续比较。

首次启用建议把 `code_variant_eval_set_size` 调小观察实际花费，再决定是否上调预算或评估集规模。
