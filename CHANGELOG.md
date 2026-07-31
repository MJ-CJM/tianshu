# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与语义化版本。

## [Unreleased]

> 当前仅是私有分支的本地收口，未接受 Candidate，也未授权 tag、Release、PyPI/GHCR
> 或仓库公开。当前可用性与非保证以
> [`docs/CURRENT-STATE.md`](docs/CURRENT-STATE.md) 和
> [`docs/launch/capability-matrix.md`](docs/launch/capability-matrix.md) 为准；下方旧版本
> 条目保留当时的开发记录，不代表所有实验能力今天仍可 live 切换、安装或自动晋升。

### Fixed

- 长程任务补齐终态检查点清理、失败/取消收敛、暂停/继续与重启恢复边界；定时任务补齐
  stale run 收敛、run-now/archive 竞态、时区/misfire 与周期长任务 fail-closed。
- 成本预算按真实提交者记账，并支持日/周/月窗口滚动、`reset_at` 与跨期首笔消费。
- 任务所有权、全局管理员面、通知逐通道进度、稳定记忆条目标识与插件 manifest-only
  边界完成一致性修复。
- Persona 外部导入不再复制或激活外部 Skill；预览只显示检测结果，旧式直接安装请求
  返回 `409`，避免绕过 Skill Candidate/Guard/Gate/Promotion。

### Changed

- Desktop Web 调整为中枢、御书房、朝堂、百司、天工院〔实验〕、内府六个一级入口；御书房下含
  全部敕令、颁发敕令、钦天监、都察院，朝堂下含吏部、廷议、内阁，百司下含翰林院、
  鸿胪寺、通政司，天工院下含演化司〔实验〕、诸界台〔实验〕、考功司〔试行〕、
  客卿馆〔实验〕，内府保留藏兵阁、权印司、户部账房。普通/长程/定时任务共用一个
  简化模型，专家参数默认折叠。
- 中枢将“当前执行中”和“未归档敕令”分开统计，增加待后续指令/已撤回分项，并将证据
  明确为“累计证据束（含归档）”；普通主体统一按本人、管理员统一按全局读取整张快照，
  通过前台 5 秒兜底轮询和相关 WebSocket 事件失效重拉保持更新。
- Skills 合并为只读目录，仅保留真实可用的查看与 Pin；自动 reviewer/curator 默认关闭
  并在 LLM 前 fail fast。
- 插件只展示 manifest，安装/激活返回 `501`；Legacy Universe 只保留快照、分支、diff、
  评估与推荐，switch/rollback/promote-code 固定 fail closed。
- 当前设计、实现、使用、运维、发布边界与历史快照导航已同步更新。
- 开源仓库卫生规则补齐本地环境、密钥、数据库 sidecar、测试报告、Agent 运行状态和
  发行工作目录；本机 Claude 计划、运行标记、误生成安装日志、Vite 缓存，以及与当前
  天枢无关的旧天气脚本、重复研究笔记和本地第三方 gstack 包不再跟踪。

## [0.4.2] - 2026-07(UI 视觉重塑「朱批」)

墨为骨,朱为睛,纸为气:保持 Geist 级克制,朱砂只出现在「与主上朱笔有关」的地方。
三方向比稿后用户拍板 A「朱批」,定案见 docs/strategy/2026-07-09-ui-refresh-zhupi.md。

### Changed
- **调色板单一色源** `web/src/theme/palette.ts`:浅色「宣纸」/深色「烟墨」两套中性色 + 朱砂唯一强调色 + 九态低饱和器物状态色(WCAG AA 已核对);AntD token 与 CSS 变量(`--ts-*`)都从这里取值
- **状态标签整体重制**:新 `SemanticTag`(淡染底+细描边+本色字)替代实色 Tag;**「待朱批」是全屏唯一实色**(朱砂底);AntD 预设色种子(blue/green/red/…)一并低饱和化,82 处预设 Tag 零改动跟随;`colorInfo/Success/Warning/Error` 及其 Bg/Border 与 SemanticTag 同配方对齐
- **朱笔语义落点**:侧栏选中项朱杠+待批计数朱砂小盘、页头朱砂方印「枢」、Tabs 朱砂墨线、`::selection` 朱砂淡染、`:focus-visible` 朱砂环、GlowCard 运行光晕改朱砂呼吸(尊重 reduced-motion);朱砂不做普通按钮色
- **品牌重墨**:favicon 由青蓝辉光星芒改为墨环朱枢(内嵌 prefers-color-scheme,深浅标签栏均清晰)
- **排印纪律**:全局 `tabular-nums`、页头标语去伪斜体改衬线+字距、标题字距、侧栏分组标题宋体
- **硬编码色值清零**:约 60 处组件内 AntD v4 糖果色/浅色边框转 CSS 变量(深色模式穿帮修复,含 CostTrendChart/DagToolbar/WorkerPanel/EventTimeline 等);React Flow marker/MiniMap 不支持 var(),按主题从 palette 取字面值

### 后续(验收旋钮)
- 待朱批实色/朱杠/呼吸光晕/朱砂明度/个别状态色相均为可点名调整项;可选加味(纸面噪点、Empty 闲章)默认未做

## [0.4.1] - 2026-07(迭代 7「制度补全」)

明制透镜补全:小事快批(司礼监)× 大事慎议(廷议,迭代 5)成对;提交有封驳、绩效有考核、健康有巡按。

### Added(must)
- **司礼监·代批**:学习用户批红习惯,对低风险 decree 自动代批,解 HITL 审批疲劳。防"宦官专权"**四道闸**:仅低风险类(`tool_tier ≤ silijian_max_tier`)/ 每笔留痕可撤(`actor="silijian"` 的 Decree)/ 急停穿透 + 一键关权 / 历史同类高通过率(近期人工批红率 ≥ 阈值且样本足,冷启动不代批)。接 policy_hook 审批前置;**默认关**
- **六科给事中·封驳**(D9):敕令提交预检——目标超长封还(入站防护),历史/声明成本超阈自动升 `plan_review` 联动票拟(非硬拒,只升级不删事);**默认开**。`estimate_edict_cost` 历史均值预估
- **实录馆·自动汇编**:`generate_weekly` 升级为《实录》周报(本周敕令/执行/司礼监代批 + 成本白话),digest cron 每 7 日推送

### Added(nice)
- **京察·官员考核**(D19):全体官员绩效考语(称职/观政/不称职)+ 不称职者演化/致仕建议,复用 `get_persona_stats`;`GET /personas/jingcha`
- **巡按御史·主动巡检**:定期巡查任务积压/失败率/成本异常,异常出巡按奏报(只读,推送由调用方定)
- **太医奏折**:太医诊断器双出口——除喂进化提案外,汇编面向用户的健康奏折(自进化关时也能看系统健康)
- **审计规则 YAML 可配**(D13):审计可调项经 YAML 外部配置,缺省用安全默认(向后兼容)

### 后续
- 司礼监代批准确率进京察考核(需代批回溯标注);审计规则 YAML 与巡按/太医奏折的 UI 面板与推送编排为增量

## [0.4.0] - 2026-07(迭代 6「演化 2.0」)

自进化从"能跑"升级为"敢放手 × 每步被评估"。行为层默认关,达阈值「请旨解锁」;晋升挂灰度旋钮、召廷议白话论证;修撰过效果门才生效。

> 版本号说明:按 roadmap 迭代 6 目标定为 v0.4.0(演化 2.0 里程碑);0.3.0 仍留给正式宣发窗口。

### Added
- **行为层「请旨解锁」**(ADR-0004):自进化出厂默认关,行为层积累 ≥N 个审计通过 memorial 后系统主动上「臣请开启自我演化」奏折(附做什么/花多少钱/如何回滚白话三段),批红后翻转开启;**代码层演化永不自动请旨**。`evolution_petitions` 表 + `EvolutionUnlock` + `/universes/petitions` 批红/驳回 API,随 digest cron 周期检查。把功能开关做成剧情事件,留存钩子更强
- **feature-flag 灰度**:自研 SQLite flag 表(deny-by-default + 按 subject 哈希百分比灰度,不接 OpenFeature);推荐晋升挂 `universe:promote:<uid>` 灰度旋钮(默认关 0%)供放量 / 秒级回退,控制面 `/universes/flags`
- **位面晋升廷议门**(ADR-0008):推荐晋升召廷议,多视角白话论证附 `promotion_recommended` 事件——晋升审批不再只有 fitness 数字
- **画像驱动进化**:变异 prompt 注入起居注蒸馏的主上画像,变异方向贴合偏好(失败安全)
- **修撰效果门**(ADR-0007):技能修撰后须过与位面同源的配对评估(变体=只替换该技能的库,`TIANSHU_RUNTIME_SKILLS_DIR` 重定向),提升达标才生效;评估缺席/提升不足/异常一律不激活(fail-safe)。默认关(`skill_effect_gate_enabled`,子进程重定向路径待实机验证)
- **技能安全安装管线**:validator 对齐 [agentskills.io](https://agentskills.io/specification) 开放标准(非标字段 / allowed-tools 告警);`SkillInstaller` 防路径穿越 / symlink / zip 炸弹 + 结构校验 + guard 信任策略,外部 SKILL.md 技能可安全导入
- **变体沙箱容器化(可选降级)**:`ContainerRunner` 探测 docker/OrbStack/Apple container,`--network none` + 只读挂载 + CPU/内存限额;无运行时干净降级不崩

### 后续(nice / 待实机)
- 请旨奏折的手机外发(feishu/telegram 穿透"你的衙门请求进化")与 web 审批卡片为增量;修撰效果门默认开需一次实机配对评估验证子进程 skills 重定向

## [0.2.8] - 2026-07(迭代 5「执行 2.0」+ 廷议 2.0 + 通知三级制)

### Added
- **通政司通知三级制**(D2):免打扰 23:00–08:00(可配跨午夜);urgent 穿透 / normal 免打扰时段攒起来醒后补推(`pending_notifications` 表持久化,不丢)/ low 入 digest。「睡觉干活」的产品答案
- **廷议 2.0**(ADR-0008):废硬编码 confidence 换结构化 stance(赞成/反对/有条件 + 条件清单 + 论据);职能视角发言;**言官强制反调**破六官同构的意见趋同;web stance 标签 + 言官标记
- **批红「驳回+指导」**:除 approve/reject,加 `guide`——驳回本次工具但注入纠正意见,agent 据此换方式续跑(比二元更贴治理);approve_for_session 已由 grant_scope 覆盖
- **steer 中途注入**:长任务运行中随时注入一句纠偏,下一轮 actor 吸收(搭 critic/廷议注入机制);`POST /edicts/{id}/steer` + web SteerPanel(仅运行中)
- **LSP 诊断**:`edit_file` 编辑 .py 落盘即跑 basedpyright,类型/语义错误回灌 agent;默认关(`TIANSHU_LSP_ENABLED`),`pip install 'tianshu[lsp]'`,未装静默降级——同时作为代码变体位面的快速 fitness 信号

### 后续(nice)
- 影子快照完整版核心(客卿隔离工作区快照+一键回滚)已在 v0.2.4(迭代 3.5)交付;native 执行快照 + 事件对齐为增量。澄清请示(agent 主动问)与 steer 同属在线纠偏

## [0.2.7] - 2026-07(迭代 4「记忆 2.0」)

记忆宫殿从"存"升级为"懂"。按拍板**跳向量层**(FTS5 保持),先做结构化事实 + 后台蒸馏 + 用户画像。

### Added
- **时序知识图谱**:三元组(subject-predicate-object)带有效期,`as_of` 查询见某时刻有效事实;**事实校勘门**(断言前比对,object 同则幂等、不同则时序更新——旧事实盖 valid_to 退场、新事实接位);偏好漂移可表达、过时事实可作废。记忆宫殿结构化事实层,与 Markdown 真相源互补
- **后台史官**:从已 audited 的成功 memorial 蒸馏一句可复用执行知识写 court 记忆;后台异步、零对话 token(挂 digest cron);`historian_log` 防重复
- **起居注 v1**(护城河):从用户行为信号(批红 approve/reject/amend、feedback、runtime/acceptance override、提交模式)蒸馏「主人偏好」→ `USER_PROFILE.md` + 偏好三元组入时序 KG(scope=user,经校勘门,偏好漂移可表达);PromptBuilder 新增注入层——**官员开工前见主人习惯,不等进化立即贴合**。与史官共用蒸馏管线
- 全局 `list_recent_decrees`(批红习惯信号源);`KnowledgeGraph` 接入 app.state

### 后续(nice,弹性伸缩)
- 渐进披露 timeline/fetch 工具(`memory_search` 已覆盖主检索)、记忆 ROI 账本、向量混合检索(拍板暂不做)

## [0.2.6] - 2026-07(平台级默认下沉全局设置 · 表单 UX Q7)

### Added
- **平台级 edict 运行时默认下沉全局设置**(表单 UX 决策 Q7):AgentConfig 新增 `agent_max_concurrency`/`agent_retry_limit`/`agent_token_budget`/`agent_cost_budget_cny`(叠加已有 timeout/max_iterations);edict 创建时用这些全局默认**打底**,表单只覆盖差异——全局设一次,颁敕不用逐字段重填。系统管理「全局设置」页可编辑;`GET/PUT /api/agent-config` 暴露

### Changed
- edict 创建总是带显式 runtime(全局默认打底,值等价旧硬编码默认,行为兼容)

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
