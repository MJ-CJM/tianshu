# 天枢 Tianshu

天枢的长期产品定位是：**天枢是一个可治理、可验证、持续成长的自进化 Agent OS**，并以明代宫廷为统一隐喻组织概念。本表只定义**战略与产品层的 canonical 术语**（中英对照，英文 README 与代码命名以此为准）；术语定义不等于 v0.4.2 已实现能力。当前成熟度、支持边界与非保证项只以 [docs/launch/capability-matrix.md](../quality/public-capability-matrix-v0.4.2.md) 为事实源。六部隐喻的全量对照见 [docs/reference/glossary.md](./legacy-domain-glossary.md)。

## Language

**敕令 (Edict)**:
用户下达的一项任务目标,系统内一切执行围绕它展开。
代码、API 和数据库兼容标识继续使用 `Edict`；历史文档中的“诏令”只作为旧称，
G3 及之后的当前中文界面统一显示“敕令”。
_Avoid_: 诏令(当前 UI)、任务/task(泛称)、job

**奏折 (Memorial)**:
一次执行的完整记录(过程、结果、用量、审计),一道敕令可产生多次奏折。
_Avoid_: 执行结果、run log

**工作区租约 (Workspace Lease)**:
一次运行独占的、带版本与谱系身份的暂存工作区授权；重试和追问必须获得新租约，不能沿用上一运行的暂存状态。
_Avoid_: 临时目录、workspace session

**恢复点 (Restore Point)**:
运行开始前冻结的源工作区身份与基线证明，是判断源漂移和治理应用是否合法的权威依据。
_Avoid_: 影子快照、backup

**规范变更集 (Canonical Change Set)**:
由工作区租约中的实际 Git 状态服务端重算并稳定排序、哈希的变更事实，不接受客户端提供的 diff 作为权威输入。
_Avoid_: patch、client diff

**裁决 (Decision)**:
人对奏折或待决动作作出的最终治理决定(approve/reject/amend/retry/cancel),是治理面的人工干预原语。
_Avoid_: 批红、审批(泛称)、confirm

**官员 (Persona)**:
受权限矩阵(工具白名单、tier 上限、委派边界)约束的角色身份;**不等于独立模型实例**——约束是真的,模型不必多个。对外表述必须诚实分层:六部是同一模型在不同权限约束下的分工。
_Avoid_: 多个 AI、多模型、agents(指官员时)

**客卿 (Keqing / external-agent executor)**:
作为可插拔执行后端的外部 coding agent（如 Claude Code、Codex）。v0.4.2 仅为 **contained + experimental**：提供独立工作目录、clean-env、外围 timeout 与事后结果归一，不保证 CLI 内部事前工具拦截、内部事件完整性、硬成本上限、运行前恢复点或网络隔离。用 Capability Manifest 分级披露并在强制能力不足时拒绝派发，是 G1 规划目标，不是当前保证。
_Avoid_: worker、外包 agent

**起居注 (User Chronicle)**:
从裁决行为、follow-up 修正、反馈信号中蒸馏出的用户习惯画像,注入 prompt 并引导进化方向。
_Avoid_: 用户画像(泛称)、user analytics

**位面 (Universe)**:
行为配置或代码的可分支、可评估快照；长期目标是以位面为单位比较并按受治理的 fitness 结果晋升。v0.4.2 支持快照、分支、diff 与人工切换，当前只路由 champion，无真实在线 challenger 流量或可信自动晋升；完整闭环是 G4 规划目标。
_Avoid_: 分支(git 语境)、版本

**放手四保险 (Four Safeguards)**:
长期治理目标中的兜底集合：预算熔断、危险动作裁决、影子快照回滚、出厂预算护栏。v0.4.2 只在能力事实矩阵列出的路径和边界内分别提供部分机制，不构成“四道保险同时成立”的当前承诺。

**自动裁决 (Governed Auto-decision)**:
G4 规划目标：在低风险白名单、留痕可撤、一键收权与准确率考核四道闸约束下，学习用户裁决习惯并自动作出裁决。v0.4.2 仅有边界受限的规则路径，不具备已学习、可证明可信的自动裁决闭环。
_Avoid_: 司礼监代批、自动审批(泛称)、auto-approve(不带闸门语境时)

**组织新陈代谢 (Organizational Metabolism)**:
天枢对多官员体系的叙事定位:官员是有生命周期的组织成员——被考核(京察)、被进化(位面变异)、被淘汰(致仕)、准入要考试(科举)。第二幕双引擎口诀:记忆感知,进化固化。
_Avoid_: multi-agent 协作(作为卖点时)、agent teamwork

**技能修撰 (Skill Curation)**:
G4 规划目标：按运行指标筛选技能候选、由 LLM 修订，并只在效果门证明评估提升后生效；`SKILL.md` diff 保持人类可读。v0.4.2 可记录候选与评审结果，但不保证真实任务效果提升，也不会据此自动形成可信自进化闭环。
_Avoid_: 技能自动更新、skill tuning

**廷议 (Court Deliberation)**:
高风险决策的多视角审议原语:官员按职能视角出具立场(赞成/反对/有条件赞成)、条件与论据,言官强制唱反调,纪要留痕进裁决/晋升/审计。与自动裁决成对:小事快决,大事慎议。
_Avoid_: 会诊(留给太医诊断器)、会商、multi-agent debate(作为卖点时)

**言官 (Court Censor)**:
廷议中被指定强制唱反调的官员角色:职责是找出议案的最大漏洞,即使内心赞成;立场记入纪要。
_Avoid_: 批评者、devil's advocate(泛称)
