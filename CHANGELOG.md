# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与语义化版本。

## [0.2.2] - 2026-07(迭代 2「证明」)

### Added
- **Evals v1 平台级回归评测**:配对沙箱评估泛化——`tianshu evals run` 一条命令沙箱回放历史任务并产出评测报告(fitness 分项/逐条结果/失败归因分布/与同集上次运行的 Δ);`tianshu evals sample` 分层混采固化回归集;运行台账落 `eval_runs` 表
- **失败原因分类学**:memorial 新增 `failure_reason`(14 类 `agent_error.*` 借鉴 multica taskfailure + 3 类天枢平台侧:预算熔断/迭代闸/孤儿回收);落库写路径自动归因,迁移时自动回填历史行,`tianshu evals backfill --re-classify` 支持分类器升级后全量重分类
- **失败归因可查询**:`GET /api/evals/failure-distribution` + `tianshu evals failures`;`execution.failed` 事件 payload 携带 `failure_reason`(诊断轨迹入事件账本,喂审计面板与太医诊断器)
- **Web 评测面板**:评测中心页(运行列表/报告详情/失败归因分布,古典中文名「考成院」);审计中心新增失败归因分布卡片;三语言 i18n

## [0.2.1] - 2026-07(迭代 1「接入」)

### Added
- **`tianshu doctor` 装机自检**:配置/DB 可写/端口(含运行中实例探测)/凭证主密钥/可选依赖离线检查,`--llm` 真实连通性测试,fail 退出码 1
- **MCP server 化**:`POST /mcp` streamable HTTP 端点(官方 SDK,stateless+JSON),暴露 submit_edict / get_edict_status / get_memorial / list_recent_edicts / list_pending_approvals 五个 tools;治理边界=批红不经 MCP(测试锚定);Claude Code 一行接入:`claude mcp add --transport http tianshu http://localhost:8000/mcp`
- **LLM 可靠性配置化(litellm.Router)**:多配置 fallback 链(active 优先)、按错误类型 RetryPolicy(认证/请求体/内容策略 0 重试,限流退避 3 次)、连续失败冷却;配置指纹变更热重建;凭证隔离保全(沙箱评估/doctor 直连不共享 Router);Router 构造失败宽容降级直连

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
