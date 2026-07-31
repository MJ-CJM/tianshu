# 隐喻对照表 · Metaphor Map

天枢用明代官制作隐喻组织功能。隐喻是外壳,落到代码是解耦模块——本表把
「宫廷叙事」与「工程实体」一一对应,中英对照,供宣发与新用户理解。

Tianshu organizes features through the metaphor of the Ming-dynasty bureaucracy.
The metaphor is a shell; in code it's decoupled modules. This table maps the
court narrative to engineering reality.

| 明制隐喻 (Metaphor) | 英文 (English) | 工程实体 (Engineering) | 代码落点 (Code) |
|---|---|---|---|
| 诏令 / 敕令 | Edict | 一道任务请求(目标+约束+调度+运行时) | `models/edict.py` |
| 奏折 | Memorial | 一次执行的记录与产出(状态/成本/审计/失败归因) | `models/memorial.py` |
| 裁决 | Decision | 人工裁决动作(准/驳/改/撤) | `models/decree.py` |
| 内阁 | Cabinet | 规划官(把目标拆成计划) | `planner/` |
| 兵部 | Ministry of War | 默认执行官(自研 ReAct 引擎) | `executor/agent.py` |
| 都察院 | Censorate | 审计(规则+LLM 双层复核) | `auditor/` |
| 通政司 | Bureau of Transmission | 通知外发；逐渠道记录成功进度并只重试剩余渠道 | `notifier/` |
| 文渊阁 | Library | 记忆宫殿；Markdown 真相源、稳定 entry ID、可重建 FTS5 | `memory/` |
| 户部 · 太仓 | Ministry of Revenue | 成本账本（含 cache-read/取消前用量）+ best-effort 门禁 | `cost/` |
| 鸿胪寺 | Court of Dependencies | 对外网络治理(SSRF/白名单/凭证托管) | `tools/hongluisi/` |
| 太医 | Court Physician | 失败诊断器(给自进化开药方) | `universe/diagnostician.py` |
| 位面 | Universe | 平行行为/代码实验快照（可分支、评估、推荐；当前不切换或部署） | `universe/` |
| 客卿 | Keqing (guest strategist) | 实验外部执行器；默认关闭、CLI 凭据 self-managed | `executor/keqing/` |
| 影卷 / 影子快照 | Shadow snapshot | 独立 GIT_DIR 的运行后工作区快照 | `executor/shadow_snapshot.py` |
| 锦衣卫 | Jinyiwei (imperial guard) | 运行时深防御(脱敏/bash 分级/clean-env/急停) | `security/` |
| 廷议 | Court deliberation | 多视角会商(大事慎决) | `consultation/` |
| 司礼监 | Directorate of Ceremonial | 学习型自动裁决(规划能力) | *(规划中)* |
| 考成 | Performance eval | 本地子进程回归评测(实验能力) | `evals/` |
| 起居注 | Court diary | 用户画像与习惯记录 | `persona/` |

> canonical 术语(带 Avoid 词表)见根目录 [CONTEXT.md](../../CONTEXT.md);
> 完整术语表见 [docs/reference/glossary.md](../reference/glossary.md)。
> 当前成熟度和明确非保证见[能力事实矩阵](capability-matrix.md)。
