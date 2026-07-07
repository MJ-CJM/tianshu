# 天枢 Tianshu

异步、可治理、会自进化的 AI 执行平台,以明代宫廷为统一隐喻组织概念。本表是**战略与产品层的 canonical 术语**(中英对照,英文 README 与代码命名以此为准);六部隐喻的全量对照见 [docs/reference/glossary.md](docs/reference/glossary.md)。

## Language

**诏令 (Edict)**:
用户下达的一项任务目标,系统内一切执行围绕它展开。
_Avoid_: 任务/task(泛称)、job

**奏折 (Memorial)**:
一次执行的完整记录(过程、结果、用量、审计),一道诏令可产生多次奏折。
_Avoid_: 执行结果、run log

**批红 (Decree)**:
人对奏折或待批动作的裁决(approve/reject/amend/retry/cancel),是治理面的人工干预原语。
_Avoid_: 审批(泛称)、confirm

**官员 (Persona)**:
受权限矩阵(工具白名单、tier 上限、委派边界)约束的角色身份;**不等于独立模型实例**——约束是真的,模型不必多个。对外表述必须诚实分层:六部是同一模型在不同权限约束下的分工。
_Avoid_: 多个 AI、多模型、agents(指官员时)

**客卿 (Keqing / external-agent executor)**:
作为可插拔执行后端的外部 coding agent(如 Claude Code、Codex),受天枢全部治理面(批红/审计/预算/留痕)节制。
_Avoid_: worker、外包 agent

**起居注 (User Chronicle)**:
从批红行为、follow-up 修正、反馈信号中蒸馏出的用户习惯画像,注入 prompt 并引导进化方向。
_Avoid_: 用户画像(泛称)、user analytics

**位面 (Universe)**:
行为配置或代码的可分支、可评估、可回滚快照;自进化以位面为单位赛跑、按 fitness 晋升。
_Avoid_: 分支(git 语境)、版本

**放手四保险 (Four Safeguards)**:
"敢放手"承诺的兜底集合:预算熔断、危险动作批红、影子快照回滚、出厂预算护栏。

**司礼监代批 (Silijian auto-approval)**:
学习用户批红习惯后对低风险裁决的自动代批,受四道闸约束(低风险白名单、留痕可撤、一键收权、准确率考核)。
_Avoid_: 自动审批(泛称)、auto-approve(不带闸门语境时)

**组织新陈代谢 (Organizational Metabolism)**:
天枢对多官员体系的叙事定位:官员是有生命周期的组织成员——被考核(京察)、被进化(位面变异)、被淘汰(致仕)、准入要考试(科举)。第二幕双引擎口诀:记忆感知,进化固化。
_Avoid_: multi-agent 协作(作为卖点时)、agent teamwork

**技能修撰 (Skill Curation)**:
按运行指标(usage/success)筛选自建技能候选、LLM 修订、经效果门(评估提升才生效)的技能自优化闭环;SKILL.md diff 人类可读,是自进化的展示窗口。
_Avoid_: 技能自动更新、skill tuning
