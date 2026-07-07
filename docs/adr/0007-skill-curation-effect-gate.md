# 技能修撰接入效果门:修撰后须评估提升才生效

SkillCurator 现状(`src/tianshu/skills/curator.py`):按 skill_metrics(usage/success)筛选 agent 自建技能候选、LLM 修撰、仅结构校验(validator)即生效——缺"改完真的更好吗"的**效果评估**。这恰是自进化叙事的招牌承诺("每一步都被评估")在技能线上的漏板,也是微软 SkillOpt(validation-gated skill 优化,2026-06)的核心主张。决定:迭代 6 的技能三件套(SKILL.md 对齐/科举门禁/SkillForge)扩为四件套——**修撰后的技能须通过与位面同源的配对评估效果门,提升才生效**。技能 diff 人类可读,由此成为自进化第二幕的展示窗口;对标表述:SkillOpt 刷 benchmark 分,天枢用真实运行数据选候选、修撰过效果门、审计留痕、可回滚。

拍板:2026-07-08 grill-with-docs 三轮问 3(用户主动提出 skills 自优化议题);随迭代 6 交付。
