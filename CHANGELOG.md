# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与语义化版本。

## [0.2.0] - 2026-07(soft launch)

首个对外版本。核心能力:

### Added
- **诏令全链路**:Edict → Scheduler → Planner → Agent/DAG → Auditor → Notifier,事件主链路全程落库为可复盘时间线
- **Agent 核心**:ReAct 循环、10 种 ExitReason、不可变 LoopState、三层上下文压缩、流式输出、Anthropic prompt cache
- **六部官制**:多 Persona 权限矩阵、部门智能路由、8 层 PromptBuilder、朝廷(court)共享记忆
- **记忆宫殿**:Markdown 真相源 + SQLite FTS5 全文检索 + Drawer 快照 + 人格成长画像合成
- **长任务外环**:AcceptanceCriteria 验收契约 + bash/lint/rubric 客观检查 + 多监督官 critic + L0–L3 分级升级(廷议/人工)
- **治理**:工具分级(tier)+ 策略管线 + 人工批红(Decree)+ 票拟预审(plan_review)+ 规则/LLM 双层审计 + 会话规则
- **成本治理**:token 计量、预算熔断、按模型/任务/官员多维归因
- **平行位面自进化**:行为 + 代码双层位面,配对沙箱评估、五维 fitness、自动晋升与自重部署回滚、太医诊断器
- **多入口**:Web / HTTP API / CLI / 飞书 / Telegram;MCP 客户端;鸿胪寺外网治理(SSRF/白名单/凭证托管/限流)
- **工程**:CI 质量门禁(ruff/mypy/import-linter/pytest/前端 tsc+eslint+vitest)、pre-commit、mypy 十包零错、import-linter 分层契约

### Fixed
- profile_synthesizer 对 `gather(return_exceptions=True)` 结果的窄化漏判 CancelledError(BaseException)——改为正向 `isinstance(x, list)` 判定
