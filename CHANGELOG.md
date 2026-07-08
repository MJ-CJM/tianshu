# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与语义化版本。

## [0.2.5] - 2026-07(颁敕表单 UX 重构)

治用户反馈的「颁敕参数选花眼」。分析 + 6 个 ai-example 同类产品对标见 `docs/strategy/2026-07-08-edict-form-ux-analysis.md`。

### Changed
- **颁敕表单三层渐进披露**:① 极简默认(标题/目标/智能填充,首屏填个目标即可提交);② 意图预设卡(4 长程模板升格为首屏卡片 + 客卿卡:⚡快速/📊分析/💻编码/🔬研究/🤝客卿,点一张一键配好整套参数并收起细节);③ 专家模式开关(默认收起全部细粒度字段,打开按语义分组:执行/预算/权限/验收,专家旋钮 L1-L2 轮数/同问题阈值/min_outer 再下沉「进阶验收参数」折叠)
- 默认值可见化:InputNumber placeholder 显示「默认 300 秒」等
- 纯 UX 重构,后端契约不变(`handleFinish` 值映射逻辑不动);删 RuntimeConfigSection(内联进专家分组);edictPresets 单测 8 例
- 「客卿模式」独立预设卡顺带解决其可发现性(此前只在专家模式的执行器下拉里)

> 未做(按推荐单开第二轮):平台级默认(超时/迭代/并发/token 预算)下沉到系统全局设置(Q7,需动 AgentConfig)。

## [0.2.4] - 2026-07(迭代 3.5「客卿」)

双向互操作最后一块拼图:天枢既能被 Claude Code 下旨(MCP server,迭代 1),也能**反向派 Claude Code 出工**——两个方向都在治理框架内。

### Added
- **客卿执行器 v1**:Edict 可标注 `runtime.executor = keqing:claude-code`(或 `keqing:codex`),把执行面派给外部 CLI(headless `claude -p --output-format stream-json` / `codex exec --json`),**自研引擎仍是默认**。治理集成四件套:①隔离工作区(每 edict 独立目录,不碰主工作区);②clean-env(客卿只带**自身**凭证 ANTHROPIC_API_KEY 等,天枢 TIANSHU_* secrets 一律不透传);③预算熔断(解析 stream-json 成本触顶即杀);④产出照走 memorial → 审计 → 批红管线(客卿产物额外出站脱敏)。适配器注册表可插拔(`tianshu keqing agents`)
- **影子快照最小版**(放手四保险第③条):客卿每次执行后对隔离工作区打快照——**独立 GIT_DIR**(`~/.tianshu/shadow/<edict>/gitdir`),工作区里不出现 `.git`,**用户版本库毫发无损**;一键回滚且回滚本身留新快照(可再向前)。CLI `tianshu shadow list/revert <edict> <sha>` + web 敕令详情页「影子快照」面板 + `GET/POST /api/edicts/{id}/snapshots`
- **web**:边建敕表单「执行器」下拉(native / 客卿);敕令详情页影子快照回滚面板;三语言 i18n

### Changed
- 版本对齐 0.2.4;`EdictRuntime.executor` 字段(默认 `native`,向后兼容)

## [0.2.3] - 2026-07(迭代 3「深防御」)

治理是天枢最强卖点,这一迭代把它做深到运行时。锦衣卫四件套 + 出厂护栏。

### Added
- **锦衣卫·出站脱敏**:WS 广播 / webhook / 通知渠道外发前统一 redact(API key/PEM/JWT/DB 连接串/Bearer/家目录,语料合并 multica+zeroclaw 两独立实现)
- **锦衣卫·bash 风险分级**:quote-aware 分段后**逐段**判定,堵死 `git log; rm -rf /`(白名单前缀 + 分号藏危险命令)这类绕过;命令替换/重定向/后台 & 等结构绕过一律升级审批
- **锦衣卫·子进程 clean-env**:`shell_exec` 白名单构造环境变量,不透传 `TIANSHU_*` 等 secrets(经 `TIANSHU_SHELL_ENV_PASSTHROUGH` 显式声明额外变量)
- **锦衣卫·分级急停**:三档(全停 kill_all / 掐网 network_kill / 冻结工具 tool_freeze),工具管线入口每次调用先过 estop、先于所有判定;SQLite 单行状态持久化、损坏 fail-closed;`GET/POST /api/estop/*` + web「系统管理 → 急停」控制台 + 事件留痕
- **出厂预算护栏**(放手四保险第④条):默认每日全局上限 ¥20(`TIANSHU_DAILY_BUDGET_GUARDRAIL_CNY`),超限熔断;daily/weekly 预算周期自动滚动清零
- **opt-in 遥测**(ADR-0003):默认关,`TIANSHU_TELEMETRY=on` 才启用,仅上报版本+启动事件,首启明示,一行 env 永久关
- **OTel GenAI 埋点薄封装**:默认关,设 `TIANSHU_OTEL_ENDPOINT`(如 Phoenix)才导出;gen_ai.* 属性集中定义(semconv experimental,升级只改一处);`pip install 'tianshu[otel]'`
- **MCP 治理·准入清单**(D15):`TIANSHU_MCP_SERVER_ALLOWLIST` 非空则只加载清单内 server;未设则明示告警(stdio 子进程 env 由官方 SDK 白名单托底)
- **凭证主密钥轮换**(D16):`tianshu secrets rotate-master-key`——旧密钥全量解密→新密钥重加密回写,干跑校验+自动备份+解不开即中止(不破坏数据)

### Changed
- 版本口径对齐 0.2.3(含此前一直遗漏的 `tianshu.__version__` 自 0.1.0);mypy 覆盖扩至 11 包(新增 `tianshu.security`)

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
