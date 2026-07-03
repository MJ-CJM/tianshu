# 全项目重构执行记录（2026-07-03）

> 对应方案：[2026-07-03-project-review-and-refactor.md](./2026-07-03-project-review-and-refactor.md) · 分支 `feat_phase8` · 51 commits
> 执行方式：每任务「实施 → 独立审查 → 修复」循环；全部任务经交叉验证后收口。
> 收官验证五关：后端 1364 passed / ruff check+format 双净 / mypy 叶子包零错 / API 路由快照零漂移（仅 1 条设计内新增）/ 前端 tsc+build 通过。

## 批次 1 · 止血与基建（8 任务）

| 任务 | Commit | 内容 |
|---|---|---|
| T1 evolver 阻塞止血 | `23e6f20` | gate.run / eval_harness.evaluate 用 `asyncio.to_thread` 包裹，消除一次代码变体评估冻结全站数分钟的问题（P0-2） |
| T2 插件注册 latent bug | `724ecb9` | `register_tool` 构造 `ToolDefinition` 修复签名不匹配 + 2 条回归测试（P0-3） |
| T3 前端深链与徽标 | `9ccf7d3` | Tabs 用 `useSearchParams` 驱动修复 `?tab=` 深链失效；御书房标题去除与徽标口径打架的计数（P0-4/5） |
| T4 死代码清理 | `bb27119` `65d38ed` `4779a5f` | 删 `persona/department.py`、`plugins/installer.py`、`CancellationToken`、前端 ProviderDashboardPage 与 OpsMonitor 死壳、`nav.war`；`StreamCallback` 接上类型注解；`archive_old_iterations` 接线每日归档（**行为变化**：首跑会一次性归档 30 天前历史积压） |
| T5 吞异常补日志 | `34790cf` | storage 行反序列化 6 处静默 pass 抽 `_load_json_field` 补 warning；hooks 审计写入失败补 debug 日志 |
| T6 样板收敛 | `ada968c` | `title_from_goal()`（5 处副本）、`submit_new_edict()`（3 处建敕令流程）、状态标签上移 `models/common`、CLI `_request()` 收敛 |
| T7 配置去重与改名 | `4d4e719` `b56abe5` | 7 个默认值以 `TianshuSettings` 为单一来源；`providers/protocol.py` → `capabilities.py` |
| T8 ruff 落地 | `62c3c57` `c2c80d9` `3d88f72` `397601a` | lint 修复 453 处自动 + 手工若干（顺手修出 3 个沉睡真 bug：api.py `Literal` 缺 import、auditor 缺 `Memorial`、worker 裸 `asyncio` 名）；全量 format 274 文件；`.git-blame-ignore-revs` |

## 批次 2 · 结构重塑（4 任务）

| 任务 | Commit | 内容 |
|---|---|---|
| T1 kernel 契约层 | `88fbede` | `hooks/exit_reason/ambient` 迁 `kernel/`、`dag/models.py` 并入 `models/dag.py`；4 条上向循环 + models→dag 反向依赖全部清零（迁移文件字节级零改动） |
| T2 api.py 拆分 | `91891ee` `cb384b1` `12491e8` `807e872` + 修复 `46f8add` `fcf8871` `488d111` | 3513 行 / 144 路由 → **67 行** + 15 个域 router（批 A-D 纯移动，URL 零漂移，130 符号字节级验证）。批 E：97 条纯 DB 路由改 `def` 回归线程池；审查抓到 5 条纯内存路由 def 化引入 `asyncio.Event` 跨线程唤醒失效（隔离复现证实）→ revert async。**顺带修复既存影子路由 bug**：`/memory/stats`、`/memory/policies` 曾被 `{persona_id}` 参数路由吞掉、handler 从未可达（`46f8add`） |
| T3 storage 拆分 | `c798c22` `9f23818` `f2aa3bc` `78893d9` | 4126 行单类 → `_base` + **15 领域 Mixin** + mappers/schema/migrations + facade 160 行；183 符号零丢失零改动、SQL 字节级一致、公有 API 168 成员零变化（103+ 调用点零改动）；方法论：AST 脚本机械搬运 + 字节级自校验 |
| T4 bootstrap 拆分 | `9121de7` `ebc35d5` `68ad5b5` | 先加 51 断言装配冒烟锚点，再把 698 行 lifespan 拆为 `bootstrap/` 12 个 wiring 模块（130 装配事件顺序零差异）；审查抓到 11 处惰性 import 被 eager 化（与 extras 化冲突）→ channel 类回归条件导入 |

## 批次 3 · 复用与前端（3 任务）

| 任务 | Commit | 内容 |
|---|---|---|
| T1 双通道 core 抽象 | `22484c9` `b14573e` `d955ff4` + 修复 `eadd142` | `gateway/core/` 9 模块：mode_router 合一、budget SQL 抽取、approval/batcher/outbound 事件层、assistant/edict 分支合并（16 命令单一来源、2 处真实差异显式钩子化，净减 ~240 行）；`format_status_label`/`EdictBusyError` 迁 core 清掉 telegram 跨包引用。审查抓到 core→feishu 潜伏循环 import（干净解释器直接炸）→ `markdown_compat`/approval 解析迁 core。**范围裁剪**：BotFacade 骨架经批 A/B 实证后砍掉（app_lock 语义两端本质不同，抽象收益<复杂度） |
| T2 御书房合并 | `386df71` `e6d8859` `1ccb782` | 后端 `POST /edicts/latest-memorials` 批量端点消 N+1；前端 EdictListPage + ApprovalQueuePage → `RoyalStudyPage` 双 Tab（待处置/全部敕令），颁发敕令降为按钮；菜单 15 项 → **4 组 13 项**（敕令/政要/百官/外朝）；隐藏 Tab 停止轮询 |
| T3 前端拆分 | `686a319` `363058b` | SystemManagementPage 2044 → **75 行**（7 个 Tab 抽独立文件，逐字节 MATCH）；EdictForm 869 → 464 行（Runtime/Acceptance 两个 Section） |

## 批次 4 · 收尾（4 任务）

| 任务 | Commit | 内容 |
|---|---|---|
| T1 巨型函数拆分 | `714cf28` `b131fbf` | 先补主循环控制流测试锚点，再拆 `run()` 386→87 行、`agent.execute()` 540→166 行（26 个退出点三方核验语义零偏差，提前 return 用哨兵保留） |
| T2 工程化收尾 | `d08d59a` `69368cd` `8c2f071` | mypy 渐进上线（models/kernel/bus/dag/config 叶子包，21 文件零错）；pytest-cov 度量——**覆盖率基线 63%**；依赖 extras 化（`tianshu[feishu/telegram/web/mcp/all]`，黑名单 A/B 验证惰性边界） |
| T3 测试补盲 | `36bde3b` `6043d33` `caccc19` | +35 用例（1329→1364）：dag/consultation/cli 零覆盖清零；agent.execute 高风险路径常设回归（overflow/fallback/熔断/hook 拦截）；telegram 多分片与 budget 卡真实 SQL；pytest markers 注册（129 条警告清零） |
| T4 文档同步 | `22d605c` `eb0ec39` `9d0ac51` | 19 文件：README 架构表与 extras 安装、docs/impl 现状（storage 包/启动序列/组件映射）、docs/design/interfaces 路由表、2 处源码注释漂移 |

## 审查体系拦下的问题（实施报告之外的净增价值）

1. **Critical**：`submit_outer_loop_decision` def 化后 `asyncio.Event.set()` 跨线程唤醒失效——批红决策会无限延迟（审查员隔离复现：0.3s 的 set 延迟到 3.0s 才唤醒）；
2. **Important**：core 三模块顶层 import feishu 潜伏循环 import——现有全绿纯靠导入顺序侥幸；
3. **Important**：bootstrap 拆分把 11 处惰性 import eager 化——直接冲突后续 extras 化；
4. **Important**：一份实施报告对 bug 成因有虚构叙述（代码正确但叙述失实）——触发对全部量化声明的独立复算；
5. 既存 bug 实锤两枚：影子路由（/memory/stats 不可达）、插件注册路径从未可用。

## 遗留事项（下轮候选）

1. `EdictBridge`/`PersonaRenderer` 迁 `gateway/core/`——telegram 现模块级依赖 feishu，单装 `tianshu[telegram]` 仍级联要求 lark_oapi；
2. ruff ignore 中 `UP042`(StrEnum×11) + `ASYNC240/221/109`(×23) 共 34 处，需行为级改动专项；
3. 覆盖率 63% → 规则宣称的 80%（差距 17pp，需决策目标）；coverage 跑批有个位数抖动未定位根因；
4. `typer[all]` extra 在新版 typer 已移除（装 `tianshu[all]` 有警告）；
5. 飞书分片拼接 strip 与 telegram 不 strip 的既有不一致（待产品定夺）；
6. `docs/design/architecture.md` 文档索引节引用的部分文件不存在（早于本轮的文档重组遗留）。
