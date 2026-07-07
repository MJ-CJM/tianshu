# 2026-07-07 全项目竞争力复盘与发展规划

> 输入:① 全仓工程体检(当日实测);② 2025H2–2026 市场格局调研;③ 参考项目增量能力挖掘(deer-flow / claude-mem / mempalace / crush / opencode / zeroclaw / kimi-cli / multica,全部带源码证据);④ **三路一手来源核查**(高风险市场事实 9 条、平台官方动态 10 条、技术选型尽调 9 项,来源限官方公告/官方仓库/立法机构官网/arXiv/主流媒体,详见 §5)。
> 结论先行:**天枢的护城河在「治理 × 自进化」交叉带,市场无完整对标;当前最大的短板不是能力,而是工程收尾(CI/发布/评测体系)与几个 2026 标配缺口(Evals / OTel / 混合检索)。**

---

## 一、项目体检(2026-07-07 实测)

### 1.1 规模与质量硬指标(全绿)

| 指标 | 实测值 |
|---|---|
| 后端代码 | 334 个 Python 文件,约 4.7 万行 |
| 测试 | 172 个文件约 2.2 万行;`pytest -m "not slow"` **1422 全过**(66s) |
| 前端 | 131 个 TS/TSX 文件、17 个页面;`tsc --noEmit` 零错 |
| ruff | 全仓通过(`check .`) |
| mypy | 10 包 85 文件零错 |
| import-linter | 2 契约保持(三层分层 + telegram↛feishu) |
| 文档 | design/impl/usage/ops/reference/plan/superpowers 六类齐备,26 个特性有计划文档,借鉴矩阵与原创设计专篇互为镜像 |

### 1.2 工程缺口清单(体检发现)

| 缺口 | 严重度 | 说明 |
|---|---|---|
| **无 CI** | 高 | `.github/workflows` 不存在。ruff/mypy/import-linter/pytest 全绿但无 CI 锁住,质量门禁全靠本地自觉 |
| **main 停滞 524 提交** | 高 | main 停在 2026-04-17(feat_phase4 合并);近三个月工作全部堆在 feat_phase5–8 特性分支,无发布节奏 |
| **无 LICENSE** | 高 | README 挂着 MIT 徽章并声明"计划以 MIT 开源",仓库根无 LICENSE 文件,不一致 |
| 前端零 lint 零测试 | 中 | package.json 无 eslint/vitest/playwright |
| 覆盖率 63% | 中 | 目标 80%;coverage 跑批偶发个位数抖动未定位(feishu webhook / universe switch) |
| ruff 豁免 4 条 | 中 | UP042(StrEnum,11 处)+ ASYNC240/221/109(23 处),需行为级改动 |
| profile_synthesizer CancelledError | 中 | 已确认真缺口:`gather(return_exceptions=True)` 结果用 `isinstance(x, Exception)` 判定漏 BaseException,代码有 `TODO(治理)` 标注,修法=改 `isinstance(x, list)` 正向判定 |
| 无 CHANGELOG / CONTRIBUTING / pre-commit / docker-compose | 低 | 开源前置卫生项;docker-compose 在 Phase 3 计划内 |
| 会诊 confidence 占位 | 低 | `consultation/session.py` 硬编码 0.8,对外叙事勿宣称"置信度汇聚已实现" |

### 1.3 LLM 层现状补记(为 §4 P1-A 校准)

`LLMClient`(`src/tianshu/llm.py:224`)直连 `litellm.acompletion`,tenacity 固定指数退避重试 3 次、仅覆盖 RateLimitError/Timeout/ServiceUnavailableError 三类;不解析 Retry-After、不区分"业务型 429"(余额不足/套餐不含模型)。fallback 为 agent 层单级(`executor/agent.py` `_call_llm_with_recovery`:上下文溢出 reactive 恢复 + fallback client + 工具重复失败熔断,有测试锚定)。**未使用 litellm.Router**。

---

## 二、竞争力分析

> 本章市场事实均经一手来源核查(§5),关键处附来源。

### 2.1 差异化优势(按市场稀缺度排序)

| # | 天枢设计 | 稀缺度 | 市场对照(2026-07,已核实) |
|---|---|---|---|
| 1 | **行为+代码双层平行位面自进化**(快照/分支/回滚 + 沙箱配对评估 + fitness 门控 + 自动晋升 + 自重部署回滚) | **最高** | 市场自进化集中在 **skill 层**:微软 [SkillOpt](https://www.microsoft.com/en-us/research/blog/skillopt-agent-skills-as-trainable-parameters/)(2026-05/06,arXiv:2605.23904)把 skill 文件当可训练参数、**validation-gated**(仅验证集严格提升才接受编辑),论文报 +19.1~+24.8pt——门控思路与天枢配对评估同构,但只动 skill 不动代码。自进化评测基准已出现(EverMind [EvoAgentBench](https://huggingface.co/datasets/EverMind-AI/EvoAgentBench) 及 arXiv 的 SkillFlow/SkillGenBench/EvoMemBench),说明"如何证明变好了"是公认缺口——天枢的沙箱配对评估+门控正是对此的回答。**代码层自改+部署级晋升/回滚无商业完整对标** |
| 2 | **治理组合拳**(工具分级+策略管线+人工批红+规则/LLM 双层审计+成本计量/熔断/归因) | 高 | 风险分层 HITL 是共识但多为咨询文档;预算强制仅 [OpenHands 企业版](https://www.openhands.dev/enterprise)等收费墙内提供。合规时点(已核实):EU AI Act 分阶段落地中;加州 **SB 53**(前沿 AI 透明法)[2025-09-29 签署、2026-01-01 生效](https://www.gov.ca.gov/2025/09/29/governor-newsom-signs-sb-53-advancing-californias-world-leading-artificial-intelligence-industry/);要求关键基础设施 AI 人工监督的 SB-833 仍在众议院拨款委员会**未通过**(其文本内"2026-07-01 建立人工监督机制"是合规节点条款,非生效日)——立法趋势真实,治理从工程偏好走向合规卖点的判断成立 |
| 3 | **长任务 L0–L3 升级阶梯**(验收契约+客观检查+critic+会诊+人工) | 中高 | 市场 HITL 普遍是单级审批开关;"分级自治阶梯"无产品化对标。注意:会诊 confidence 目前是占位值,叙事勿过度 |
| 4 | **异步事件驱动 + 全程审计留痕** | 中高 | [MCP roadmap](https://modelcontextprotocol.io/development/roadmap)(2026-03 更新)才把审计追踪/SSO 列入企业治理方向;主流框架 trace 面向 debug 而非合规审计。另:MCP 2025-11-25 spec 新增 experimental **tasks**(durable request + 轮询)——与天枢 Edict 异步模型天然同构,server 化时值得对齐 |
| 5 | 六部官制 persona + 权限矩阵 | 中 | CrewAI 的 role 是 prompt 层角色;把角色绑到权限矩阵(对 agent 的 RBAC)市场少见 |
| 6 | DAG+泳道背压+级联取消+checkpoint+孤儿回收 | 中 | checkpoint 已是标配([LangGraph 1.0](https://www.langchain.com/blog/langchain-langgraph-1dot0) durable state,2025-10-22 GA);但背压/级联取消/孤儿回收是 workflow 引擎级语义,agent 框架普遍没有——相当于自带轻量 durable execution。Durable execution 赛道热度已核实:[Temporal 2026-02-17 $300M Series D @ $5B](https://temporal.io/news/temporal-raises-300M-to-make-agentic-ai-real-for-companies) |
| 7 | Markdown+FTS5 分层记忆+技能渐进学习 | 中 | Anthropic structured note-taking 官方背书同路线;**技能格式已有开放标准**(SKILL.md,见 §4 P2-H);短板是无向量层(见缺口) |
| 8 | 多入口(飞书/Telegram) | 区域性 | 海外框架几乎不做 IM 入口;对国内团队是落地优势,但可复制、非护城河 |

**总评**:稀缺性集中在「治理 × 自进化」交叉带——做治理的(企业平台)不做自进化,做自进化的(研究/SkillOpt)不做治理。把自进化放进"沙箱评估→fitness 门控→审计留痕→可回滚晋升"治理框架的完整闭环,目前没有直接竞品。这是发展规划应当加深而非稀释的主轴。

**市场热度佐证(全部一手核实)**:agent 赛道资本与收入规模——Anthropic 官方口径 [Claude Code run-rate 超 $2.5B、公司整体 $14B](https://www.anthropic.com/news/anthropic-raises-30-billion-series-g-funding-380-billion-post-money-valuation)(2026-02-12 Series G);[Cognition/Devin Series D 超 $1B、投后 $26B、run-rate $492M](https://cognition.com/blog/series-d)(2026-05-27);[n8n 获 SAP 战投、估值 $5.2B](https://blog.n8n.io/n8n-sap/)(2026-05-12,金额未披露);Meta 超 $2B 收购 Manus 后 [2026-04-27 被中国发改委叫停](https://www.cnbc.com/2026/04/27/meta-manus-china-blocks-acquisition-ai-startup.html)。异步自主 agent 平台是资本最热的赛道之一,天枢方向踩在主线上。

### 2.2 与 2026 市场标配的差距

2026 用户默认期待(主流平台普遍具备):MCP、durable state/会话恢复、HITL 原语、orchestrator-worker 多 agent、双层记忆、tracing/OTel、evals、guardrails、沙箱执行、预算强制、streaming 多入口、多 provider、background agents、多模态输入、computer use(2026Q1 起主流化:Claude [Cowork 2026-01-12 research preview + 2026-03-23 Dispatch/computer use](https://claude.com/blog/dispatch-and-computer-use);[OpenAI Codex 桌面 computer use 2026-04-16](https://openai.com/index/codex-for-almost-everything/);[Gemini 2.5 Computer Use 2025-10-07](https://blog.google/innovation-and-ai/models-and-research/google-deepmind/gemini-computer-use-model/))。

天枢缺口按重要性:

| 缺口 | 重要性 | 判断 |
|---|---|---|
| **Evals 体系**(回归集/数据集管理/离线评测) | 高 | LLM critic 只覆盖长任务验收单点;**沙箱配对评估本质是 pairwise eval,是现成地基**。一个值得注意的信号:OpenAI 已宣布 AgentKit 的 Agent Builder/Evals 平台 [2026-11-30 下线](https://developers.openai.com/api/docs/deprecations)、能力迁入 Agents SDK——evals 正从独立平台收敛为 SDK/平台内建能力,天枢把它做进平台内的方向是对的 |
| **OTel GenAI tracing 导出** | 高 | semconv 截至 2026-05 仍 [Development/experimental](https://opentelemetry.io/docs/specs/semconv/gen-ai/),gen_ai.* 已迁独立仓(尚无正式 release)——方向已定但属性名会变,埋点需薄封装(见 §4 P1-C);天枢已有审计+成本数据,缺的只是 span 映射 |
| 沙箱硬隔离(容器级) | 中 | 天枢沙箱为进程级;单机 macOS 务实方案是 OCI 容器(OrbStack/Docker Desktop)+ 网络禁用 + 限额,详见 §4 P2-H;**E2B 自托管需 Firecracker/KVM,macOS 不可行,明确排除** |
| Computer use / 浏览器操作 | 中 | 经 MCP 接 playwright/browser-use 类 server,不自研(三大厂路线未收敛) |
| 向量/混合检索 | 中 | FTS5 纯词面在跨语言/同义场景吃亏;sqlite-vec 稳定版(v0.1.9)brute-force 精确 KNN 在万~十万级向量足够(见 §4 P2-F) |
| Durable execution 语义深度 | 中 | 有 checkpoint,缺"崩溃恢复不重复外部副作用"的 journal/replay 保证;专业买家最容易追问的一层 |
| 多模态输入 | 中低 | 文档/图像输入已是默认期待 |
| A2A / AG-UI / 分布式 PG/K8s | 低 | [A2A v1.0 于 2026-03-12 发布](https://github.com/a2aproject/A2A/releases)、150+ 组织支持,但生产集中在三大云生态,观望到 2027;AG-UI(AWS/Microsoft 已集成)仅当 UI 要被第三方嵌入;分布式在单机定位内是有意取舍,若转企业客户则立刻升高 |

### 2.3 协议与时间窗

1. **MCP server 化天枢自身——2026Q3 就做**:MCP 已捐入 Linux Foundation 旗下 [Agentic AI Foundation](https://blog.modelcontextprotocol.io/posts/2025-12-09-mcp-joins-agentic-ai-foundation/)(2025-12-09,Anthropic/Block/OpenAI 共同发起),官方口径 97M+ 月 SDK 下载、10,000 活跃 server(registry 自 2025-09 上线,仍 preview)。把 edict 提交/状态查询/审计查询暴露为 MCP server 是最低成本互操作路径;spec 2025-11-25 版的 experimental **tasks**(durable request)与天枢异步模型同构,值得对齐;企业治理方向(审计追踪/SSO)与天枢叙事同频。
2. **OTel GenAI semconv——2026H2 埋点**,薄封装抗属性名变动;先内部消费,标准 stable 后零成本接外部后端。
3. **Agent Skills(SKILL.md)开放标准——立即对齐**:Anthropic 2025-10-16 发布、[2025-12-18 开放为标准](https://agentskills.io/specification)(agentskills.io),OpenAI Codex/GitHub Copilot/VS Code/Cursor/Gemini CLI/OpenHands 等 26+ 客户端采纳;对齐成本极低(目录+frontmatter+三层渐进加载),换来现成技能生态可导入(详见 §4 P2-H)。
4. **A2A 观望到 2027**;**AG-UI 条件触发**;**computer use 经 MCP 接**。

---

## 三、参考项目可借鉴增量(按主题合并,全部带源码证据)

> 三路本地调查覆盖 8 个项目;下表按落地主题聚合,"来源"给出实现位置便于抄作业。跨项目交叉印证的条目(两个独立项目各自实现)标 ⭐。

### 3.1 LLM 可靠性(直击自认缺口)

| 能力 | 来源 | 要点 |
|---|---|---|
| ReliableProvider 三级降级链 | zeroclaw `src/providers/reliable.rs` | model fallback 链 × provider 链 × 指数退避;**细粒度错误分类**:业务型 429(余额不足/套餐不含)不重试、上下文超限跳过所有重试快速失败、遵守 Retry-After(cap 30s)、多 key 轮转、失败聚合完整诊断轨迹。⚠️ 落地方式见 §4 P1-A:LiteLLM Router 已内建大部分,只取其**错误分类语料与诊断轨迹思路** |
| Provider 错误分类正则库 ⭐ | opencode `src/provider/error.ts` + `session/retry.ts` | 14 家 provider"上下文过长"正则、retry-after-ms/HTTP 日期解析、区分 overloaded/限流/免费额度耗尽 |
| 401 自愈 | crush `internal/agent/coordinator.go:160-196` | 401 时刷新 OAuth/重解析 key 模板后重试一次 |
| hint 模型路由 | zeroclaw `src/providers/router.rs` | `hint:reasoning`/`hint:fast` → 路由表映射具体模型,调用方不硬编码模型名 |

### 3.2 治理深防御(强化最强卖点)

| 能力 | 来源 | 要点 |
|---|---|---|
| 出站凭证脱敏 ⭐(两项目独立实现=强信号) | multica `server/pkg/redact/redact.go`(已接线)+ zeroclaw `src/security/leak_detector.rs`(正则语料) | 写库/WS 广播/通知外发前统一 redact:各家 API key/PEM/JWT/DB 连接串/Bearer,multica 还遮蔽本机用户名与家目录 |
| OS 级沙箱级联探测 | zeroclaw `src/security/detect.rs` + landlock/firejail/bubblewrap/docker 实现 | shell/子进程类工具可选沙箱包装,探测降级,默认对高 tier 启用 |
| bash AST 级权限 ⭐ | opencode `src/tool/bash.ts:84-165`(tree-sitter)+ zeroclaw `src/security/policy.rs`(quote-aware 分段、最高危胜出、禁子 shell/重定向绕过) | 命令前缀白名单 + realpath 越工作区检测,接入现有策略管线,减少高风险命令一刀切 ask |
| 分级急停 EstopManager | zeroclaw `src/security/estop.rs` | KillAll/NetworkKill/DomainBlock/ToolFreeze 四级,持久化+损坏 fail-closed,恢复可要求 OTP |
| prompt injection 护栏 | zeroclaw `src/security/prompt_guard.rs`(参考实现,未接线)+ deer-flow `guardrails/`(GuardrailProvider 协议,fail-closed) | 对不可信来源(IM 消息/工具返回的外部内容)入 LLM 前检测,Warn/Block/Sanitize |
| 凭证密文落库 | multica `server/internal/util/secretbox/secretbox.go` | AES-256-GCM 认证加密(防篡改),key 缺失显式报错,预留 key 轮换 |
| 子进程 clean-env ⭐ | kimi-cli `tools/shell/__init__.py`(get_clean_env)+ zeroclaw shell_env_passthrough 白名单 | 不透传父进程全量 env,防 secret 经 env 泄漏 |
| 入站防护 | zeroclaw `src/gateway/mod.rs` | body 上限/慢请求超时/幂等键去重/webhook 签名 |

### 3.3 记忆宫殿 2.0

| 能力 | 来源 | 要点 |
|---|---|---|
| 向量+FTS 混合检索+优雅降级 | claude-mem `SearchOrchestrator.ts` + 各 Strategy | 向量做 FTS5 之上的**可选增量层**:有 query 走语义、纯 filter 走 FTS、向量失败自动回退;天枢用 sqlite-vec 即可,Markdown 真相源不动 |
| 3 层渐进披露检索协议 | claude-mem `plugin/skills/mem-search/SKILL.md` | search(ID 索引 ~50-100 tok)→ timeline(锚点上下文)→ get_observations(筛后批量取全文),10x token 节省;拆成 memory_search/memory_timeline/memory_fetch 三工具 |
| 时序知识图谱 | mempalace `mempalace/knowledge_graph.py` | 纯 SQLite:实体+带 `valid_from/valid_to` 三元组,`query_entity(as_of=)` 时间旅行、invalidate 纠偏、timeline 编年史;与审计/会商联动(市场对标:Zep 的 Graphiti 时序 KG,arXiv:2501.13956——路线已被验证) |
| 事实一致性校勘 | mempalace `mempalace/fact_checker.py` | 落库前比对 KG:relationship_mismatch / stale_fact / similar_name(编辑距离≤2 错名),命中打回或标注喂 critic |
| 记忆 ROI 账本 | claude-mem `TokenCalculator.ts` | 每条记忆记 discovery_tokens,复用时算 saved_tokens;给成本面板加"记忆命中节省",为 fitness 成本维度提供正向信号 |
| 后台史官(transcript 蒸馏) | claude-mem `src/services/transcripts/watcher.ts` | 天枢事件时间线本就全程落库——加后台 Sweeper 式"史官"消费已 audited 时间线异步蒸馏成记忆,主链路零 token |
| 每 persona 私有日记 | mempalace diary_ingest + Specialist Agents | 与共享 court 分离的个体经验层,强化自进化个体维度 |
| 跨域互链 tunnel | mempalace `palace_graph.py` | 同一主题在不同部门记忆间显式互链,检索顺链下钻 |

### 3.4 执行层工程安全网

| 能力 | 来源 | 要点 |
|---|---|---|
| 影子 git 快照+会话级回滚 | opencode `src/snapshot/index.ts` + `session/revert.ts` | 独立 GIT_DIR 对工作树 write-tree,每个 executed 事件节点对齐一个快照;失败诊断/人工审时可 diff 与 revert;与 checkpoint(进度)正交 |
| 工具输出溢出转文件 | opencode `src/tool/truncation.ts` | 超 2000 行/50KB 即转存文件,返回"前 N 行+提示 Grep/委派子 agent";在源头限流,与三层 compaction 叠加 |
| 文件新鲜度守卫 | crush `internal/filetracker/service.go` + `tools/edit.go` | read-before-edit + mtime 陈旧检测;天枢双层泳道并发下同 workspace 盲写风险更高,作为策略管线内置守卫 |
| 工具循环检测 ⭐ | crush `loop_detection.go`(SHA-256 滑窗)+ deer-flow `loop_detection_middleware.py`(两级阈值:警告→剥离 tool_calls 强制收尾) | 新增 ExitReason=loop_detected |
| LSP 诊断闭环 | crush `internal/lsp/` + `tools/diagnostics.go`;去抖细节 opencode `lsp/client.ts:209-237`(150ms 等语法/语义两波) | edit 后诊断直接拼进工具结果;**对代码变体位面尤其有用:变体改完立刻拿到类型级 fitness 信号**;Python 侧落地选型见 §4 P2-G(basedpyright CLI 起步) |
| 后台 shell 作业 | crush `internal/shell/background.go` | job_start/output/kill 工具族,复用孤儿 Sweeper 心跳做回收 |

### 3.5 人机在环 2.0(异步平台的在线纠偏)

| 能力 | 来源 | 要点 |
|---|---|---|
| steer 运行中注入 ⭐ | kimi-cli `soul/kimisoul.py`(合成 tool_call/result 对注入当前回合)+ crush `agent.go:156-165`(忙时入队、PrepareStep 注入) | 长任务纠偏不再取消重跑:人工补充在下个 step 边界注入 LoopState |
| Agent 主动澄清请示 | deer-flow `clarification_middleware.py` | ask_clarification 工具+拦截挂起,5 种类型+选项;落成 `clarification_requested` 事件,在审批视图作为待办,回复后 resume |
| 批红"驳回+指导" | opencode `permission/next.ts`(CorrectedError vs RejectedError) | 驳回附纠正意见→合成消息注入 LoopState 继续,比 approve/deny 二元更贴治理定位 |
| approve_for_session ⭐ | kimi-cli `soul/approval.py` + zeroclaw approval | 本 edict 内同类操作 always-allow(留痕),减少审批疲劳 |
| 子 agent 可续跑+质量门 | opencode `tool/task.ts`(task_id 续跑)+ kimi-cli `tools/multiagent/task.py`(产出<200 字自动追问续写;子 agent 审批冒泡到根) | 会商/子任务线程可寻址、带前文续跑 |

### 3.6 平台运维与演化基建

| 能力 | 来源 | 要点 |
|---|---|---|
| 失败原因分类学+离线回填 ⭐ | multica `server/pkg/taskfailure/classify.go`(14 种 agent_error.* 子原因,SQL CASE 唯一真相源+backfill)与 zeroclaw 错误分类互补 | Memorial 加 failure_reason 枚举+分类函数+回填脚本;喂失败归因分析与太医诊断器 |
| feature-flag 灰度 | multica `server/pkg/featureflag/`(链式 provider:override→env→db→static;属性定向;版本化快照;deny-by-default) | **与 Universe 高度契合**:已过门禁未全量的进化产物挂 flag 后按 cohort 灰度、秒级回退、不重部署。落地选型见 §4 P2-H(自研 SQLite 表,不接 OpenFeature) |
| doctor 自检 | zeroclaw `src/doctor/mod.rs` | /doctor 面板+API:体检配置/provider/连通性/权限,结构化 Severity+可修复项 |
| heartbeat 自发意图 | zeroclaw `src/heartbeat/engine.rs` | agent 自撰 HEARTBEAT.md 周期意图,区别于外部 cron;可由记忆宫殿产出心跳清单驱动主动巡检 |
| MCP 连接状态机 | opencode `src/mcp/index.ts` | connected/failed/needs_auth/needs_client_registration;OAuth;StreamableHTTP→SSE 回退;运行时热增删 |
| 内容级去重 | multica `server/internal/issueguard/duplicate.go` | 同意图 Edict 去重(标准化 title+锁+查重);SQLite 用 BEGIN IMMEDIATE+唯一部分索引等效 |
| Wire 协议健壮性 | kimi-cli `wire/{jsonrpc,server}.py` | 协议版本+能力协商;断连/回合结束把未决审批 fail-closed 收敛(approval→reject);WS 层可借 |
| kaos OS 抽象 | kimi-cli `packages/kaos` | 工具 fs/exec 抽象成可切本地/远程的接口,**Phase 3 Runtime/Worker 解耦的地基** |
| 渐进技能注册表+安全安装 | deer-flow `skills/loader.py` + `installer.py`(防穿越/symlink/zip 炸弹) | 可热插拔的能力模块库,与 tier/policy 治理开关结合;**格式对齐 SKILL.md 开放标准**(§4 P2-H) |
| 外部技能获取 SkillForge | zeroclaw `src/skillforge/` | Scout 发现→评分→走三关门禁集成,作为 Universe 的"技能获取"侧支 |
| ACP 外部 agent 委派 | deer-flow `tools/builtins/invoke_acp_agent_tool.py` | 把重编码子任务委派给外部 Claude Code/Codex(ACP),回收进门禁;可作代码变体生成的外包通道 |

---

## 四、发展规划

> 原则:围绕「治理 × 自进化」主轴加深护城河;补齐高优先市场标配;先还工程债再上新能力。每项给验证口径。技术选型均经一手尽调(§5),两处与直觉相反的结论已标注。

### P0 · 地基与还债(≈1–2 周,开源前置)

1. **CI 落地**:GitHub Actions 单 workflow 跑五件套(ruff check / ruff format --check / mypy / lint-imports / pytest -m "not slow"),外加前端 `tsc --noEmit` + `npm run build`。→ 验证:PR 上全绿。
2. **发布节奏恢复**:feat_phase8 合回 main,打 v0.2.0 tag;之后"main 常绿 + 特性分支短周期合入"。→ 验证:main HEAD 日期为当周。
3. **LICENSE + CHANGELOG + CONTRIBUTING + pre-commit**:补 MIT LICENSE(消除 README 徽章不一致);CHANGELOG 从 phase 台账提炼。
4. **已确认缺陷修复**:profile_synthesizer CancelledError(isinstance 正向判定,一行级);测试偶发抖动定位(feishu webhook/universe switch,疑 hash-seed/async 泄漏)。
5. **前端质量线**:eslint + vitest 起步(先覆盖 api 层与关键组件)。
6. 覆盖率 63%→80% 列入每批次增量要求(新代码 90%+,存量按模块补)。

### P1 · 2026Q3(高杠杆,五条线可并行)

**A. LLM 可靠性升级**(落点 `llm.py` + `providers/manager.py`)
- **改用 litellm.Router,勿自研 ReliableProvider**(尽调结论,[官方 routing 文档](https://docs.litellm.ai/docs/routing)):Router SDK 内建 fallbacks(组内加权→跨组降级)、`context_window_fallbacks`、`RetryPolicy`(按错误类型)、`AllowedFailsPolicy`/cooldowns(429 自动冷却)、多 key 负载、pre-call checks(上下文窗口/TPM/RPM 预过滤)——天枢现状(§1.3 直连 acompletion + tenacity 固定三类重试)缺的这些,Router 全有,要做的是**显式配置化**。
- 自补三件(Router 没有的):跨重启持久的配额/预算记账(Router 状态在内存,挂到天枢已有 cost 账本);全局熔断兜底;失败诊断轨迹落事件账本(zeroclaw `reliable.rs` 的聚合诊断思路 + 其"业务型 429 不重试"错误分类语料)。
- 供应链纪律:锁定 litellm 版本并纳入升级审计(litellm 官方 [2026-03 披露过一次安全事件](https://docs.litellm.ai/blog/security-update-march-2026))。
- `hint:` 模型路由表(persona/任务 → 模型解耦,zeroclaw router.rs 模式)。
- → 验证:注入各类 fake 错误的单测(业务型 429 不重试/超窗直接降级);线上失败率与重试耗时对比。

**B. Evals 体系 v1 + 失败归因**(落点 `universe/eval_harness` 泛化 + storage)
- 把配对沙箱评估泛化为平台级回归评测:历史 memorial 采样构建评测集(复用现有分层混采)、离线跑批、报告面板;对接"自进化怎么证明变好"的叙事(对标 EvoAgentBench 的 Train/Extract/Evaluate 协议)。
- Memorial 加 `failure_reason` 枚举(借 multica 14 类)+ 分类函数 + 历史回填;失败归因进成本/审计面板,喂太医诊断器。
- → 验证:一条命令产出评测报告;失败归因分布可查询。

**C. OTel GenAI 埋点**(落点 executor/llm/cost)
- 按 [GenAI semconv](https://opentelemetry.io/docs/specs/semconv/gen-ai/) 把 LLM 调用(inference span)/工具调用(execute_tool span)/edict 生命周期(agent span)映射导出,OTLP 可配,本地默认关。
- **实施纪律(尽调)**:semconv 仍 experimental(gen_ai.* 已迁独立仓、无正式 release)——平台内做一层薄封装集中定义 gen_ai.* 属性,升级一处改;instrumentation 参考 OpenLLMetry 或官方 instrumentation-genai,**避免 OpenInference 私有命名空间锁定**。
- 观测 UI 选 **Arize Phoenix**(单容器+SQLite、原生 OTLP 直收,与天枢体量匹配;license 为 ELv2,内部部署无碍不可转售托管);Langfuse 功能更全且核心 MIT,但自托管栈 Postgres+ClickHouse+Redis+S3 对单机过重,需要 prompt 管理/人工标注时再评估。
- → 验证:Phoenix 里能看到一条 edict 的完整 trace(含 gen_ai.* 标准属性渲染 PoC)。

**D. 治理深防御包**(落点 tools 策略管线 + gateway)
- 出站脱敏 redact(合并 multica+zeroclaw 正则语料,统一挂在事件落库/WS 推送/通知外发出口)。
- bash 工具 AST/quote-aware 风险分级 + realpath 越界检测,接入策略管线。
- shell/子进程 clean-env 白名单;可选 OS 沙箱包装(见 P2-H 容器选型,高 tier 默认开)。
- 分级急停(SQLite 一行状态:全停/掐网/冻结工具,工具管线入口检查,事件留痕)。
- → 验证:红队用例集(绕过样例)全拦截;急停三档演练。

**E. MCP server 化天枢**(落点 gateway 新模块)
- **选型(尽调)**:FastMCP 3.x(当前 v3.4.3,自称承载约 70% MCP server;FastMCP 1.0 已并入官方 SDK,2.x/3.x 独立演进)+ `http_app()` 以 ASGI 挂载进现有 FastAPI;**手工精选 5-10 个核心 tools(edict 提交/状态/结果/审计查询),不要 `from_fastapi()` 全量转换**(官方明示自动转换效果显著差于手选)。官方 python-sdk v2 尚在 beta,2026 年内不跟。
- 跟踪 spec 2025-11-25 的 experimental tasks(durable request)——与 Edict 异步模型同构,成熟后把"下旨→轮询奏折"映射到 tasks 原语。
- → 验证:Claude Code add-mcp 后完成一次下旨→查结果闭环。

### P2 · 2026Q4(能力纵深,三大主题)

**F. 记忆宫殿 2.0**
- sqlite-vec 混合检索:**用稳定版 v0.1.9 的 brute-force 精确 KNN**(万~十万级向量足够、无 recall 损失),**不押注其 ANN**(IVF/DiskANN 仍 alpha 且默认未启用);向量为可选层、失败回退 FTS5、Markdown 真相源不动;写明触发条件:向量超约百万或延迟不达标时迁 LanceDB 嵌入式,而非换 libSQL 引擎。
- 渐进披露三工具(memory_search/timeline/fetch)+ PromptBuilder 记忆注入改"常载极小+按需下钻"并给每层 token 预算。
- 时序知识图谱(SQLite 三元组+有效期+as_of 查询)+ 事实校勘门(落库前比对,矛盾打回/标注)。
- 记忆 ROI 账本(discovery_tokens/saved_tokens 进成本面板,喂 fitness)。
- 后台"史官":消费已 audited 事件时间线异步蒸馏记忆(零对话 token)。
- → 验证:检索质量 A/B(FTS vs hybrid);记忆注入 token 峰值下降;KG as_of 查询用例。

**G. 执行与人机在环 2.0**
- 影子 git 快照链(每 executed 节点可 diff/revert)+ 文件新鲜度守卫 + 工具输出溢出转 Drawer/文件 + 循环检测 ExitReason。
- steer 中途注入(回合边界合成 tool 对)+ 澄清请示事件(`clarification_requested` 进审批视图)+ 批红"驳回+指导"(意见注入 LoopState 续跑)+ approve_for_session(留痕)。
- **LSP 诊断闭环(尽调选型)**:第一步直接 `basedpyright --outputjson`(pip 单依赖内嵌 Node runtime,PyrightJsonResults schema 稳定)——edit 落盘后对改动文件跑 CLI,解析 generalDiagnostics 回灌 agent;冷启动延迟不可接受再升级常驻 `basedpyright-langserver` + 手写 stdio LSP 客户端(参考 serena/solidlsp;**不用 multilspy**——无诊断 API 且 Python 侧绑 jedi)。同时作为代码变体位面的快速 fitness 信号。
- → 验证:中途 steer 一个长任务并观察吸收;一次完整"驳回+指导→修正→通过"链路;变体门禁引入诊断信号后坏变体淘汰提前率。

**H. 位面演化 2.0(护城河加深)**
- **feature-flag 灰度晋升(尽调:自研,不接 OpenFeature)**:OpenFeature Python SDK 未 GA(v0.10.0,spec v0.8)且轻量 provider 生态薄——自研一张 SQLite flag 表 + 约 50 行求值函数(布尔开关+按 key 哈希百分比灰度),接口包成 OpenFeature Provider 形状留迁移后路;已过门禁未全量的进化产物挂 flag 按 cohort 灰度、秒级回退(与自重部署互补)。
- **技能格式对齐 SKILL.md 开放标准**([agentskills.io](https://agentskills.io/specification),2025-12-18 开放,26+ 客户端采纳):必填 name/description frontmatter、三层渐进加载(metadata ~100 tok → 正文 <5k tok → 资源按需),平台特有字段塞 `metadata`,`allowed-tools` 按实验性对待;叠加 deer-flow 的安全安装管线(防穿越/symlink/zip 炸弹),换来外部技能生态可直接导入。
- **代码变体沙箱容器化(尽调选型)**:OrbStack 或 Docker Desktop + `--network none` + 只读挂载 + CPU/内存限额(风险模型=防变体误伤宿主,共享内核 VM 包一层足够);已升 macOS 26 的机器可选 Apple container v1.1(每容器独立轻量 VM,隔离更强,同为 OCI 接口切换成本低)。**E2B 自托管明确排除**(需 Firecracker/KVM 裸金属,macOS 不可行)。
- 外部技能获取(SkillForge 模式:发现→评分→门禁→集成);可选:重编码变体外包 ACP(Claude Code)生成,回收进三关门禁。
- → 验证:一次"变体 flag 灰度 10%→全量→回退"演练;一个外部 SKILL.md 技能走完整门禁入库。

### P3 · 2027H1(Phase 3 主线,按既有 multica roadmap)

- kaos 式 OS 抽象(工具 fs/exec 接口化)→ **#3 Runtime/Worker 解耦**(数据/控制面分离;#1 heartbeat + #2 schedule_run 已是地基)→ #4 Polymorphic Actor → #5 WS 房间模型(设计文档均已出)。
- PostgreSQL 作为可选后端(Repository 接口不变)、docker-compose、K8s 清单;Temporal 维持"可选"不默认。
- Durable execution 语义深化(外部副作用 journal 化,恢复不重放已完成副作用)——若企业买家追问,这是答案。
- 多模态输入(文档/图像经上传管线入 edict 上下文)。
- A2A 届时再评估(v1.0 已于 2026-03-12 发布,签名 Agent Card/多协议绑定,生态成熟度到 2027 再看)。

### 不做清单(明确拒绝,防散焦)

| 不做 | 理由 |
|---|---|
| 自研 computer use | 三大厂路线未收敛(Anthropic Cowork 桌面 / OpenAI Codex 后台并行 / Google browser-anchored),自研必被碾压;经 MCP 接 playwright/browser-use 类 server |
| A2A 接入(2026) | v1.0 2026-03-12 刚发,生产集中在三大云生态;单机自治平台无刚需,观望到 2027 |
| AG-UI | 前后端一体,无被第三方嵌入需求;条件触发再议 |
| **自研 LLM 重试/降级框架** | LiteLLM Router SDK 已内建 fallbacks/RetryPolicy/cooldowns/多 key(尽调证实),只补持久记账+熔断兜底 |
| **接入 OpenFeature SDK** | Python SDK 未 GA、轻量 provider 生态薄;自研 SQLite flag 表,接口留 Provider 形状 |
| **E2B 自托管** | 需 Firecracker/KVM 裸金属,单机 macOS 不可行;容器方案够用,迁 Linux 服务器时再评 |
| AAAK 式压缩方言 | mempalace 作者自证有损且回退(84.2% vs raw 96.6%);天枢已有 Markdown 真相源+索引层 |
| 自建 microVM | 单机 macOS 容器兜底够用 |
| Temporal 默认依赖 | 自带轻量 durable 语义符合单机定位;保持 Phase 3 可选项 |
| 立即上 PG/K8s | 单机 SQLite 是有意取舍;转企业客户时再升优先级 |
| swarm/handoff 式多 agent | orchestrator-worker(DAG)为市场主流生产模式,维持现有主模式 |

---

## 五、口径与来源核查说明

本报告市场事实经三路独立核查(2026-07-07,一手来源限:公司官方公告/博客/文档、官方 GitHub 仓库、立法机构官网、arXiv、主流媒体 Reuters/Bloomberg/TechCrunch/CNBC 等):

- **高风险事实 9 条**:证实 4、部分证实(口径修正)4、**证伪 1**——初稿引用的"加州 SB-833 已于 2026-07-01 生效"不实:该法案([leginfo 状态页](https://leginfo.legislature.ca.gov/faces/billStatusClient.xhtml?bill_id=202520260SB833))仍滞留众议院拨款委员会,"2026-07-01"是法案文本内的合规节点条款;加州真实生效的 AI 法为 SB 53(2026-01-01 生效)。已在 §2.1 修正。
- **平台动态 10 条**:证实 6、口径修正 4(A2A v1.0 实际发布日 2026-03-12;OpenHands 72.8% 出自其 SDK 论文 arXiv:2511.03690 而非 v1.6.0 发行说明;Dify 2.0 截至 2026-07 仍为 beta、稳定线 1.15.x;Claude Cowork 2026-01-12 research preview、2026-03-23 为 Dispatch/computer use 功能发布)。
- **技术选型 9 项**:7 项可引入但各带边界条件(sqlite-vec 不押 ANN、GenAI semconv 薄封装抗变、MCP 走 FastMCP 3.x 手选 tools、观测 UI 选 Phoenix 接受 ELv2、LiteLLM 只补持久记账、SKILL.md 全面对齐、诊断走 basedpyright CLI);**2 项结论与预设相反**(OpenFeature→自研表;E2B→排除),已反映进 §4 与不做清单。
- 仍属公司自报口径、引用需注明的:CrewAI"70% Fortune 500"、Mem0 基准分、Devin"89% 自产代码"、MCP"10,000 活跃 server/97M 月下载"(官方口径)。
- 参考项目(§3)结论均经源码文件核实;zeroclaw 的 leak_detector/prompt_guard 为**未接线**参考实现(仅正则/模式可取)。
- 天枢会诊 confidence 为占位值(硬编码 0.8),对外叙事勿宣称置信度汇聚机制已实现。

关键一手来源索引:[SkillOpt(微软官方)](https://www.microsoft.com/en-us/research/blog/skillopt-agent-skills-as-trainable-parameters/) · [SB 53 签署(州长办公室)](https://www.gov.ca.gov/2025/09/29/governor-newsom-signs-sb-53-advancing-californias-world-leading-artificial-intelligence-industry/) · [Anthropic Series G](https://www.anthropic.com/news/anthropic-raises-30-billion-series-g-funding-380-billion-post-money-valuation) · [Cognition Series D](https://cognition.com/blog/series-d) · [Temporal Series D](https://temporal.io/news/temporal-raises-300M-to-make-agentic-ai-real-for-companies) · [n8n×SAP](https://blog.n8n.io/n8n-sap/) · [Meta-Manus 被叫停(CNBC)](https://www.cnbc.com/2026/04/27/meta-manus-china-blocks-acquisition-ai-startup.html) · [OpenAI Agents SDK 改版](https://openai.com/index/the-next-evolution-of-the-agents-sdk/) · [OpenAI 弃用页(Agent Builder/Evals 2026-11-30 下线)](https://developers.openai.com/api/docs/deprecations) · [LangChain/LangGraph 1.0](https://www.langchain.com/blog/langchain-langgraph-1dot0) · [MAF 1.0](https://devblogs.microsoft.com/agent-framework/microsoft-agent-framework-version-1-0/) · [MCP 入 AAIF](https://blog.modelcontextprotocol.io/posts/2025-12-09-mcp-joins-agentic-ai-foundation/) · [MCP roadmap](https://modelcontextprotocol.io/development/roadmap) · [A2A v1.0](https://github.com/a2aproject/A2A/releases) · [Agent Skills spec](https://agentskills.io/specification) · [sqlite-vec releases](https://github.com/asg017/sqlite-vec/releases) · [OTel GenAI semconv](https://opentelemetry.io/docs/specs/semconv/gen-ai/) · [FastMCP](https://github.com/jlowin/fastmcp) · [LiteLLM routing](https://docs.litellm.ai/docs/routing) · [Langfuse self-hosting](https://langfuse.com/self-hosting) · [Phoenix](https://github.com/Arize-ai/phoenix) · [basedpyright CLI](https://docs.basedpyright.com/dev/configuration/command-line/) · [OpenFeature python-sdk](https://github.com/open-feature/python-sdk) · [apple/container](https://github.com/apple/container) · [Cowork](https://claude.com/blog/cowork-research-preview) · [Codex computer use](https://openai.com/index/codex-for-almost-everything/) · [Gemini Computer Use](https://blog.google/innovation-and-ai/models-and-research/google-deepmind/gemini-computer-use-model/)
