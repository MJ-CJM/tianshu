# OpenAI Codex harness 借鉴分析

> 候选借鉴项目分析。与 [reference-projects.md](./reference-projects.md) 不同，本文记录的是**尚未落地、正在评估**的借鉴对象；体例对齐 [multica-analysis.md](./multica-analysis.md)。
>
> 分析日期：2026-08-25。参考源：`~/ai/codex-harness/codex`（OpenAI Codex CLI，HEAD `3a469a297d`）。
> **注意与 [`../codex-v1/`](../codex-v1/) 区分**：后者是天枢自己 2026-07-12 的开发交接包，与本文的 OpenAI Codex 无关。
>
> **方法**：13 组并行测绘（9 组 codex crate 族 + 4 组天枢现状对照）→ 3 个独立视角各自排序 → 合成 14 条 → 7 组对抗式证伪（默认怀疑，五问全过才算确认）→ 终审修订。
> 25 个 agent 中 23 个完成（天枢插件域测绘与终审 agent 各失败一次，前者由主控独立补读、后者由主控人工完成）。
> **对抗验证推翻了 14 条中的 14 条的至少一部分**——0 条原样通过。被推翻的内容记录在 [§5](#五对抗验证推翻了什么)，那是本文可信度的来源，不是瑕疵。
>
> 本文讨论借鉴来源，不构成能力承诺。当前实现边界以 [CURRENT-STATE](../CURRENT-STATE.md) 与 [能力事实矩阵](../launch/capability-matrix.md) 为准。

---

## 一、Codex harness 是什么

**一句话**：OpenAI 官方的本地编码 agent CLI，Rust workspace，约 **150 万行 / 118 个 crate**，外加 Python 与 TypeScript 两套 SDK。

| 维度 | Codex harness | 天枢 |
|---|---|---|
| 形态 | 本地编码 agent CLI（TUI / exec / app-server / IDE / 桌面） | 受治理的自进化 Agent OS（控制平面） |
| 语言与规模 | Rust，~150 万行，118 crate；TS/Python SDK | Python 3.12，FastAPI + SQLite，单机单进程 |
| 核心问题 | 一个 agent 如何又快又安全地改你的代码 | 多个 agent 的任务如何被授权、审计、裁决、复盘、进化 |
| 安全边界 | OS 与进程层（seatbelt / landlock / bwrap / Windows 受限令牌 / 独立代理进程） | 数据与合同层（治理合同、策略引擎、证据链、位面隔离） |
| 换代方式 | **整进程重启**（配置纯函数重算 + 重启） | 指针换代（stage → warm → activate → drain） |
| 对天枢的定位 | **「被治理对象」的能力面上限 + 工程纪律的参照系**——学「怎么把隐式判据变成数据」 | — |

天枢体量约为其 1/20，且两者解决的不是同一个问题。**不要因为 codex 体量大就以为它的分层值得抄**：46k 行的 `ext` 与 41k 行的 `core-plugins` 里过半是发行渠道与跨主机执行，与天枢一个单机单进程的治理层毫无关系。

### 三条结构性差异（决定了什么能借、什么不能）

1. **codex 靠整进程重启换代，天枢靠指针换代。** codex 的插件链路**完全没有「卸载/注销」这个概念**——`enable/disable` 只是配置里的 bool，effective 集合每次从配置纯函数重算，`ExtensionRegistry` 构建后不可变，全仓唯一一处 unregister 是 goal 扩展自己维护的 thread→runtime 弱引用表（`codex-rs/ext/goal/src/api.rs:336`，用 `Arc::downgrade + ptr_eq` 校验「表里存的就是这个实例」才删）。
   **推论**：并存期的一切不变量 codex 是白送的；天枢选了指针换代，就必须自己补。在这条路上 **codex 是反例不是范例**，唯一可抄的只有 goal 那个 `ptr_eq` 幂等卸载判据。
2. **codex 把安全边界压到 OS 与进程层，天枢的边界在数据与合同层。** 所以借的是**判据形态**而不是机制：把「谁在执法」「钥匙给谁」「跑的是哪个版本」变成可序列化的数据，而不是用 Python 追 Rust 的系统编程。沙箱应当由被治理对象自证并经 `ExecutorCapabilityManifestV1` 声明——这已经是正确形状。
3. **codex 的编译期穷尽性在 Python 没有等价物。** 宏表、trait 分发、`Stage` 枚举、`inventory` 链接期注册表——所有「表驱动单一真源」必须改写成「**显式 dataclass 表 + 架构测试对账**」，绝不能用 metaclass/exec 在运行时生成。

---

## 二、借鉴主线

> **把散落在代码分支里的隐式判据，统统变成可序列化、可入快照、可进证据的数据，并各配一条 fail-closed 的对账测试。**

天枢今天有五个隐式判据：

| 隐式判据 | 现状 | 对应条目 |
|---|---|---|
| **谁能看见** | WS 广播不带身份，REST 的所有权隔离在 WS 层失效 | [A1](#a1-ws-出站所有权过滤已实测确认的越权) |
| **谁赢** | 贡献冲突靠装配顺序，`on_conflict` 缺省 replace 且无审计 | [A2](#a2-contributionhandle-的-dispose-身份校验) |
| **谁在执法** | canary 唯一性只在 `evolution_repo.py:270` 读时校验 | [A3](#a3-canary-唯一性下沉-db--策略执法收口一处) |
| **钥匙给谁** | raw provider key 直接进客卿进程 env | [B4](#b4-客卿凭证网关接线钥匙不出治理层) |
| **跑的是哪个版本** | 客卿二进制取 PATH 上碰巧那个 | [B8](#b8-客卿二进制路径与版本写进回执) |

三条 P0 就是这条主线在最痛的三个点上的落地。而**工程基建类的四条（路由表、schema 落盘、错误信封、TS codegen）全是真问题，但它们防的是半年后的漂移，B 类防的是正在写的代码**，所以整体降一档。

---

## 三、可借鉴清单

分级沿用 [multica-analysis.md](./multica-analysis.md) 的体例。每条都标注了对抗验证阶段的修订——**没有一条是三视角原始提案的原样**。

### ★★★ 强烈推荐

#### A1. WS 出站所有权过滤（已实测确认的越权）

| | |
|---|---|
| **codex 出处** | `codex-rs/app-server/src/outgoing_message.rs`、`thread_state.rs:59,385`（订阅按 thread 分流，广播不越界） |
| **天枢落点** | `src/tianshu/notifier/notifier.py:63,69`、`src/tianshu/gateway/api.py:35` |
| **成本 / 优先级** | S / **P0** |
| **与 P0–P7** | 无关，可立即做 |

**事实（主控实测复核）**：`register_ws(ws)` 只存 WebSocket 对象、不带 `AuthContext`；`broadcast_ws` 对 `self._ws_clients` 全量 `send_text`；`auth.py:880` 让 `/api/ws` 落到默认 `frozenset({"api"})`。而 REST 侧 `ownership.py` 的 `require_owned_edict` 走 `authz.can_access_submitter` 按 `principal.id` 比对 `edict.submitter`。

**后果**：在 `secure-remote` 模式下，任一持 `api` scope 的非 admin PAT 能收到**全部其他主体**的事件，包括 `notifier.py:358` 起逐 token 推送的 `stream.delta` 模型原文。`trusted-local` 单主体场景不受影响——但 REST 层专门为 secure-remote 写了所有权隔离，WS 层却没有，这是承诺与实现的不一致。

**做什么**：
1. `register_ws(ws, auth_context)` 带上身份；
2. `broadcast_ws` 按 `edict_id → submitter` 判权，复用 `authz.can_access_submitter`；进程内缓存 `edict_id→submitter`，避免 `stream.delta` 每 token 查库；
3. payload 无 `edict_id` 的系统级事件（`digest` / `weekly` / `xunan.report` / `evolution.petition`）显式 fail-closed 为「仅 admin scope 可见」；
4. 集成测试：两个不同 principal 的连接，A 的 edict 事件不得出现在 B 的 socket 上。

零前端改动、零协议改动、零迁移。

> **验证修订**：原提案打包了「subscribe 协议 + 未决裁决重放 + 有界队列」四件事，已砍到只剩本条。重放被证否——codex 需要重放是因为 pending server→client request 只活在内存 HashMap；天枢的裁决是**持久化 DB 行**，`decisions_api.py:172` 的 `GET /decisions` 已带所有权分流，前端 5s 轮询，刷新不会丢。有界队列拆为 [D2](#d2-ws-per-connection-有界队列)。

#### A2. ContributionHandle 的 dispose 身份校验

| | |
|---|---|
| **codex 出处** | `codex-rs/ext/goal/src/api.rs:336`（`Arc::downgrade` + `ptr_eq` 校验「表里存的就是这个实例」才删） |
| **天枢落点** | `docs/plan/2026-08-25-self-evolving-agent-os-landing.md` 的 **P2 规格**；落地时 `src/tianshu/plugins/contribution.py`（新建）、`src/tianshu/tools/registry.py` |
| **成本 / 优先级** | S / **P0**（写进规格，不是新阶段） |
| **与 P0–P7** | P2 的一条不变量 |

**做什么**：`ContributionHandle` 记录注册瞬间的被注册对象引用；dispose 摘除前做身份校验——in-memory 注册表（tool / channel / skill / command）要求 `registry[name] is handle.target` 且 owner 一致才删，否则 no-op 并写 `SystemAudit contribution_dispose_stale`。`dispose_owner` 逆序卸载逐条走同一校验，返回 `(disposed, skipped_stale)`，`skipped_stale` 不计失败、不阻断 drain。

**分类豁免**（必须写进规格）：
- hook 类直接复用 `src/tianshu/kernel/hooks.py:78-80` **已有**的 `e.handler is not handler` 身份删除语义；
- provider 类因走 `providers/manager.py:282-286` 的 `storage.delete_provider(name)` 落 SQLite、无内存槽位对象可 `is`，只做 owner 记账比对。

**为什么**（理由经验证重写）：不是「旧代 dispose 摘掉新代贡献」——P2 明写不引入 generation、六类注册表非代际。真正的成因是 plan:406 的 `on_conflict` **缺省为 `"replace"`**：MCP 断连重连或 `wiring_persona` 覆盖式重注册之后，旧 handle 的 dispose 会静默摘掉新对象，且当前规格无任何审计痕迹。

此刻定死成本极低——`tools/registry.py:58` 现状连 owner 和 unregister 都没有，属于**尚未写的代码**。

> **验证修订**：原提案含四条不变量，两条被删。(1)「SystemSnapshotV1 摘要输入必须是语义投影」已是 plan:301 的既定规格（key 白名单 + `model_validator` + 组件数 ≤64 + value 正则）；(2)「activate 前重算 SystemSnapshot 比对」在规格里无对照物，且其因果链不成立——`release_digest` 的四个输入是 `{manifest_content_hash, cli_version, binary_path, argv_shape}`，改 SKILL.md 对 executor 代际摘要零影响；「持单调 generation」也已由 plan:448 的双 CAS + partial unique index + 不可变 journal 三重覆盖。若仍想补，正确的最小形态是：**activate 前只重读一次 `cli_version` 与 `release_digest` 比对，不等即置 failed、指针不动**（复用 warm 失败的现成路径），不引入新事件类型。

#### A3. canary 唯一性下沉 DB + 策略执法收口一处

| | |
|---|---|
| **codex 出处** | `codex-rs/core/src/config/managed_features.rs:151` `normalize_candidate`（每次 set 之后把 pinned 值强行压回，而不是 set 之前检查一次）；`config/src/constraint.rs:62,149` |
| **天枢落点** | P4a 的 `0033_evolution_policies` 迁移；`src/tianshu/storage/evolution_repo.py:545`；`tests/architecture/test_promotion_authority.py` |
| **成本 / 优先级** | M+ / **P0** |
| **与 P0–P7** | **并入 P4a**，不新开阶段、不新开迁移号 |

**A3-a（DB 侧）**：在 `0033_evolution_policies` 迁移块里追加
```sql
CREATE UNIQUE INDEX idx_evolution_candidates_subject_canary
  ON evolution_candidates(kind, subject_key) WHERE lifecycle='canary';
```
定位明写为「**P4b per-subject 灰度的第三道墙**」，不宣称它下沉了今天的全局唯一规则（全局唯一仍由 `evolution_repo.py:268-278` 的读时 fail-closed 守）。

同 PR 必须一起改（这是 effort 从 M 上调到 M+ 的原因）：
- `tests/evolution/test_evolution_migration_schema.py` 的 `_declared_v18_objects` / `_unique_column_sets` 补登新索引，否则**锁定测试当天就红**（该测试对 `_V18_TABLE_COLUMNS` 做 `sqlite_master` 全对象名 + SQL 等值锁定）；
- `save_candidate` 的 `IntegrityError` 捕成 `EvolutionRepositoryConflict("subject_canary_exists")`，promotion 层映 409，不许漏成 500；
- 迁移前跑存量体检：`SELECT kind, subject_key, count(*) FROM evolution_candidates WHERE lifecycle='canary' GROUP BY 1,2 HAVING count(*)>1`。

**A3-b（执法收口，一处而非 N 处）**：把策略执法**下沉进 `EvolutionRepository.save_candidate`**（`evolution_repo.py:545` 的转移校验分支旁），条件限定为 `target_lifecycle in {CANARY, PROMOTED}`。这一处结构上罩住 `gates` / `promotion`×4 / `candidate_service` 全部现有及未来写路径，等价于 codex「每个写路径都过一遍」的效果，又是**写前校验而非写后改写**，不触碰 append-only，也不误伤 stage/evaluate。plan:566-569 的三个入口检查保留为早拒（好错误码、零 artifact 副作用），但不再是唯一防线。

**为什么**：天枢自己交过的学费是「权限改动要验到执行层」——只在判定层校验会单测全绿但功能不生效。`start_candidate` 当前**完全没有跨候选排他检查**（`promotion.py:784-843` 只有 `:828` 的自路由检查），两个候选可以先后进 CANARY，根本不需要并发窗口。

> **验证修订（重要）**：原提案要把 `EvolutionPolicy` 改成 `allowed_lifecycles / allowed_transitions` 开放集合并「每次写入后重新压平」，**两条都被否决**：
> - 开放转移集合会**直接毁掉 Ring 0 的一条明文验收**——plan:576 的 `mode TEXT NOT NULL CHECK(mode IN ('frozen','manual','canary'))` 正是「auto 双重不可表达（类型 + CHECK）」那道 DB 墙，转移集合存 JSON 后写不出这条 CHECK；
> - 「写入后压平」在 append-only 证据链里不可移植：压平 = 事后改写已落库的 lifecycle，等于绕过 `PromotionAuthority` 的无 Decision 状态变更，与不可变 journal 触发器直接冲突。
> - 另：`EvolutionPolicyV1` 从来就是纯数据（`mode: Literal[...]` + `max_canary_basis_points`），没有 lambda——「必须是数据不是闭包」是在反驳一个不存在的设计。probe 端点也冗余，plan:571 的 `GET /api/evolution/policies/{subject_key}` 本身就是探针。

---

### ★★ 值得做（P1–P2）

#### B1. 路由 scope 提为显式表并加全路由覆盖测试

**codex 出处**：`app-server-protocol/src/protocol/common.rs:129,522`（把并发域写进协议宏表的一列，漏写在 review 里刺眼）；`hooks/src/lib.rs:23,43`（哪些事件的 matcher 有语义是模块级常量白名单，UI/校验/schema 共用一份真相）。
**天枢落点**：`src/tianshu/gateway/auth.py:857` 的 `_required_scopes` → 新建 `src/tianshu/gateway/route_policy.py` + `tests/architecture/test_route_scope_coverage.py`。**M / P1**。

用**有序** `RouteScopeRule(methods, path_pattern, scopes, reason)` 列表 + first-match-wins 承载 `auth.py:857-888` 的全部分支（顺序即优先级，四条特判放表头）。中间件只做查表。**必须三步走，不要一次切完**：

1. 加表 + **对拍测试**：用 `create_app()` 枚举路由模板（含把 `app.py:483` 的 `/mcp` Mount 单独登记），断言 `route_policy.match(...) == AuthService._required_scopes(...)`。中间件此时仍调旧函数，**零行为变更**。
2. 加覆盖测试：(a) 每条路由至少命中一条规则；(b) 每条路由被 `_public_route` 与 `route_policy` **恰好之一**认领（防新路由被静默判成 public）；(c) 规则表里没有 0 命中的僵尸规则（对标 `test_no_direct_process_launch.py` 的 stale exemption 检查）。
3. 中间件改查表并取消 `api` 兜底，unmatched → 403。同 PR 必须显式登记 `/docs`、`/redoc`、`/openapi.json`，并明确 `_unsafe_unknown` 的 404 与新 deny 的先后顺序，否则未知路径的响应码会从 404 变 403。

**为什么**：授权规则与路由定义分处两地、靠字符串前缀匹配，新增路由忘同步就静默降到最宽的 `api` scope。P4/P5 马上要新增 policy / generation / executor-candidate 三组路由，**先有表再加**。

#### B2. 重试判据收敛进已有的 `models/failure.py`

**codex 出处**：`sdk/python/src/openai_codex/errors.py:86` `map_jsonrpc_error`、`:112` `is_retryable_error`（公开导出的纯谓词，`retry.py:34` 消费）。
**天枢落点**：`src/tianshu/models/failure.py`（**不新建** `core/retryable.py`）；`executor/executor.py:229`、`run_execution.py:220`、outbox。**S / P1**。

给 `FailureReason` 补上 executor 实际在用但不在枚举里的瞬时类目（或把 `_retryable_failure_reason` 的三个字符串映射到既有的 `PROVIDER_NETWORK` / `AGENT_TIMEOUT` / `PROCESS_FAILURE`），新增 `FailureReason.is_retryable` 属性作为唯一判据，替换 `executor.py:229` 的硬编码集合与 `run_execution.py:220` 的 isinstance 判断。**判据结果写进 Attempt 记录**——这是「候选评估器别把环境抖动当成模型能力不行」的关键。

> **验证修订（WRONG_FACTS）**：原提案引用「codex 把『从错误文案里正则抠结构化字段』明确列为反模式」——**完全说反了**。`client.py:77-79` 的 `_active_turn_id_from_error` 函数体就是 `re.search(r" but found ...", exc.message)`，codex 自己**正在做**这件事；`errors.py:56` 的 `_contains_retry_limit_text` 同样是子串匹配。另 `map_api_error` 在 Python SDK 里根本不存在。原提案的 HTTP 错误信封统一部分拆为 [D4](#d4-http-错误信封统一)，因为它是 L 级、69 处裸字符串 detail 要清。

#### B3. V1 契约 schema 落盘 + CI 汇总门禁与工作树洁净断言

**codex 出处**：`hooks/src/schema.rs:1034` `generated_hook_schemas_match_fixtures`（现场生成 vs 仓库副本逐字节比对）；`.github/workflows/blocking-ci.yml:48` + `.github/scripts/check_ci_results.py:21`（一个 required job 汇总 needs，skipped/cancelled 一律判失败）；`.github/actions/check-clean-worktree/action.yml:12`（`git status --porcelain` 非空即硬失败）。
**天枢落点**：`src/tianshu/models/schema_export.py`（新建，从现有代码提炼）、`scripts/export_schemas.py`、`tests/contracts/test_schema_fixtures.py`；`.github/workflows/`。**M / P1**。

**(a) 不新建第三套写法**：把 `src/tianshu/models/lean_preview.py:249` 的 `_schema_for(model, filename)`（它做的正是「`$id`/`$schema` 由外部注入而非模型带」）提到 `models/schema_export.py` 作为唯一注入点；`scripts/export_schemas.py` 只维护 `SCHEMA_EXPORTS` 登记表。把**已落盘**的 `evidence-bundle-v1` 与两个 lean-preview schema 一并登记，让 `tests/evidence/test_schema_contract.py:31` 与 `tests/launch/test_lean_preview_schemas.py:519` 收敛到一个参数化用例。首批新增登记 `ExecutorCapabilityManifestV1`、`EffectiveGovernanceContractV1`、`AgentContinuationV1`、`OuterLoopContinuationV1`、`RunAssignmentV1`；P1/P3/P4 的 `SystemSnapshotV1` / `RuntimeGenerationV1` / `EvolutionPolicyV1` 追加进同表并写进各阶段验收 checklist。比对沿用现有的 **dict 相等**语义而非逐字节，避开缩进/排序 churn；生成文件里记录 pydantic 版本，pydantic 锁到 minor。

**(b) 先解决 `dependency-review` 的条件跳过再谈汇总门禁**：把 `if: github.event_name == 'pull_request'` 从 job 级下沉到 step 级（job 恒 success），再加汇总 required job；每个 reusable workflow 显式声明 permissions。`check-clean-worktree` composite action 挂在 backend/frontend job 末尾。**(a)(b) 同 PR**——没有 clean-worktree 断言，所有漂移门禁都形同虚设。

**为什么**：`run_states.continuation_json` 是存量行，某次重构给 `AgentContinuationV1` 换个字段名，存量待续跑的 run 全废，而这个改动在 PR diff 里看起来只是一行 rename。

> **验证修订**：原提案称「schema 侧覆盖率只有 1/N」——不成立，天枢已有两套独立机制、三个落盘 schema，且 `$id` 注入 helper 已在生产代码里。再建一套等于引入第三套写法。

#### B4. 客卿凭证网关接线：钥匙不出治理层

**codex 出处**：`codex-rs/responses-api-proxy/README.md:31,35,61,64,66`（特权进程从 stdin 持钥、agent 只拿 loopback base_url、只放行 `POST /v1/responses`、带 query 一律 403、出站覆盖 Authorization 与 Host、dump 自动脱敏）；`network-proxy/src/credential_broker/providers.rs:131` `shaped_dummy_value` + `credential_broker_tests.rs:35` `assert_credential_shape`。
**天枢落点**：`gateway/llm_gateway_api.py:45` `_default_forward`（现 `raise NotImplementedError`）、`app.py` 的 `include_router` 列表（`llm_gateway_router` **从未注册**）、`gateway/keqing_api.py:99`（`gateway_enabled` 硬编码 False，注释自承「internal prototype, not connected to any production executor」）。
**成本 / 优先级**：**XL** / P1（**建议单开迭代，不塞进 P0–P7**）。

**这是叙事与实现裂缝最大的一条**：天枢自称「Claude Code 的上级机关」，但今天 raw `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` 经 `EnvironmentSecretRef` 直接进客卿进程 env——被治理对象持有与治理者同等的凭证，客卿可读 `/proc/self/environ`、可打印、可外发，出站 redact 只是事后补救。网关同时是**不可绕过的唯一审计点**：模型白名单、预算熔断、spend 台账天然进起居注与证据。

**做什么**：实现 `_default_forward`（httpx 反代，必须覆盖 SSE 流式；只放行 `/v1/messages` 与 `/v1/chat/completions`；带 query 403；出站覆盖 Authorization 与 Host；回程剥 provider 私有头）→ 注册 router、`gateway_enabled` 改读配置、给会话档与单发档都传 `gateway_base_url` → 客卿 env 改注入 scoped token + `ANTHROPIC_BASE_URL`/`OPENAI_BASE_URL` 指向 127.0.0.1。直连档降级为需显式开启；**网关不可用时 Attempt fail-closed（`ExecutionDenied`），严禁静默回退直连**。按 backend 逐个灰度实测（claude-code 走 `ANTHROPIC_BASE_URL`、codex 走 `OPENAI_BASE_URL`、opencode/pi 各不相同）。

> **验证修订（五处，必须在动手前读）**：
> 1. **硬错：`credential_isolation` 这个能力位不存在，且不能新增。** `CapabilityId` 是 `governance_contract.py:12-26` 的 13 项 Literal，manifest 与 effective contract 都强制「每个 capability 恰好声明一次」。新增第 14 项会让冻结兼容夹具 `tests/fixtures/governance/effective_v1_2e76851.json` 校验失败、所有已落库的 effective contract 反序列化报错、每个 manifest 的 `content_hash` 变化——**直接撞 frozen 契约**。正确做法是复用现成的 `secret_control`（`capabilities.py:322/378`，当前都是 `BEST_EFFORT`）。
> 2. **「scoped_token 台账写好了却无人调用」是错的**：`session_executor.py:118-140` 已在 gateway 分支 mint、`wiring_tools.py:48-50` 已接进 secret resolver、`session_executor.py:248-261` 已在 finally revoke。真正缺的只有三处（`executor.py:119` 不传 `gateway_base_url`、router 未注册、`_default_forward` 未实现）。
> 3. **token 前缀保留 `tskq_`**：改成 `sk-ant-`/`sk-` 会破 `tests/secrets/test_scoped_token.py:20`，更要紧的是抹掉「这是天枢的票，不是真 key」的肉眼可辨性。codex 的 shaped dummy 是给子进程的**假值**（真值在代理侧替换），语义不同——天枢发的是真能用的票。只在实测某 backend 的本地格式校验确实拒绝时才按该 backend 改。
> 4. **`ForwardFn` 签名是 `(record, body, provider)`**（`llm_gateway_api.py:42`），没有 Request/app 句柄，拿不到 `wiring_llm.py:86` 装配的 `ModelProviderRegistry`。要么在 bootstrap 设模块级 registry，要么改签名。
> 5. **单位陷阱**：`registry.pricing_cny()` 返回人民币（`registry.py:233`），而 `ScopedTokenStore.record_spend(raw_token, cost_usd)` 在 `scoped_token.py:113` 会再 ×7.2 转 CNY。直接喂进去预算虚高 7.2 倍、402 提前触发。
> 6. 另：`config_api.py:133` 对 `keqing_gateway_enabled=True` 主动抛 409、`:112` 响应里硬编码 False，且被 `tests/gateway/test_keqing_status.py:64-65` 与 `test_q7_global_defaults.py:75` 两处测试锁死——原提案只提了 `keqing_api.py:99`，漏了这三处。
> 7. scoped token 台账目前**纯进程内内存**（`scoped_token.py:11-12` 自承），须与 P6 进程级重启一并拍板语义。

#### B5. 前端类型契约：先补 response_model，再谈 codegen

**codex 出处**：`sdk/python/scripts/update_sdk_artifacts.py:530,1042`（pin 死的二进制导出 JSON schema → `datamodel-code-generator` 生成模型 → 连公共方法签名都渲染进手写 `api.py` 的 `# BEGIN/END GENERATED` 标记块）+ `tests/test_contract_generation.py:37`（断言重跑生成器后字节完全不变）。
**天枢落点**：`web/src/api/types.ts`（1308 行手写）、各 gateway router 的 `response_model`。**L / P2**。

**主控实测数据（这条推翻了原提案的前提）**：跑 `create_app().openapi()` 实测，209 个 path / **249 个操作**里——

| 响应 schema 形态 | 数量 |
|---|---|
| 指向裸 `ApiResponse`（其 `data` 是 `anyOf[{}, null]`，等于无类型） | **117** |
| 完全空 schema `{}` | **100** |
| 自由 object | 27 |
| **真正带具名响应模型** | **2**（都是 `CredentialView`） |

原因是 `models/common.py:87` 的 `ApiResponse(BaseModel, Generic[T])` 在 gateway 里**参数化用法为 0 处**，103 处是裸泛型 `response_model=ApiResponse`。

**所以三期，顺序不能反**：
1. **P0（本条真正的成本所在）**：只对 edicts / decisions / evolution / evidence 四组，把 `response_model=ApiResponse` 改成 `response_model=ApiResponse[XxxView]`，为每个端点新建 frozen 响应模型（字段照抄 `types.ts` 现有定义，保证零行为变更）。同时修 `app.py` 里 3 个重复 operationId。
2. **P1（守卫）**：`tests/architecture/test_response_contract.py`（仿 `test_promotion_authority.py` 的 AST 扫描范式）——已迁移的 router 模块内，新增路由必须带参数化 `response_model`。**这一条比生成器本身更能止血**。
3. **P2（生成，可选）**：覆盖到足够比例后再落 `scripts/export_openapi.py` + `docs/reference/openapi.json` + `npm run gen:types`。

> **只做第 3 期而跳过第 1 期，产出的是一堆 `data?: unknown`，会把「响应无类型」以生成物的形式固化下来，比现状更糟。**

#### B6. 插件坏 manifest 可见 + 发现路径抗 symlink

**codex 出处**：`plugin/src/load_outcome.rs:19,26,35,39`（`LoadedPlugin{enabled, error}` 正交两字段，`is_active = enabled && error.is_none()`）；`core-plugins/src/loader.rs:817`（`load_plugin` 返回 `LoadedPlugin` 而非 `Result`）；`utils/plugins/src/plugin_namespace.rs:42`（发现路径遇 symlink/非常规文件**整体放弃**而非降级到下一候选，四个 unix 测试钉死）。
**天枢落点**：`src/tianshu/plugins/loader.py:37-38,50-51`（两处 `except Exception: logger.warning(...)` 后静默跳过）。**M / P2**。

坏插件现在是「消失」而不是「报错」，API 与 UI 永远看不见它坏在哪；而 `skills/installer.py:138-139,250` 对 symlink 硬拒，plugins 侧**零校验**——位面工作区对 agent 可写，是同一攻击面上的非对称缺口。`PluginLoader` 目前零测试覆盖。

**做什么**：`discover()` 返回 `PluginDiscoveryRecord{plugin_id, manifest|None, error, warnings}`（**不加 V1 后缀**，不持久化为契约，**不加 enabled 字段**）；manifest 必须是常规文件、非 symlink，父目录同样校验，不满足即记 error 而非静默跳过；坏记录**不写 plugins 表**（主键拿不到），改挂 `app.state.plugin_discovery`，由 `providers_api.py` 的 `list_plugins` merge 出 `error` / `warnings` 两个新键。`status` / `capability_status` / `loaded` 三个字面量一字不改，install/enable 的 501 语义不动。

> **验证修订**：原提案借错了概念——天枢的插件面**根本没有 enabled 状态**（`types.ts:678-688` 把 `status: "manifest_only"` / `loaded: false` 钉成字面量），所以「enabled 与 error 正交」在天枢无对应物；只有「正常/坏掉」两态。「P2 ContributionHandle 的 owner 前置」的叙事也被删除。

#### B7. 敕令受理期校验 allowed_paths glob

**codex 出处**：`execpolicy/src/parser.rs:133` `validate_pending_examples_from`——`prefix_rule` 的 `match`/`not_match` 是该规则的正反例，加载期**为每条语句单独建临时 Policy** 求值（避免别的规则恰好命中掩盖错误），任一不符即整个策略文件加载失败并报行号。
**天枢落点**：`storage/edict_repo.py:15-38` `_insert_requested_governance_contract` 之前；复用**已存在**的 `tools/path_utils.py:57` `validate_allowed_path_glob`。**S / P2**。

对 `edict.runtime.policy_profile.allowed_paths` 逐条校验，不合法直接 **422 拒绝受理**，不写 edicts 表、不落 Memorial。显式豁免 `BUILTIN_TEMPLATES` 里的相对 glob。这一步覆盖了「写错一个 glob 要到执行时才暴露」的绝大部分实际场景，**零 hash 风险**。

> **验证修订**：原提案要把 `expectations` 正反例塞进 `PermissionPolicyV1`（原提案写的 `PermissionsV1` 类型名不存在）并参与 contract `content_hash`——被否决：那会破坏已落库合同的 hash 稳定性，需要把 `CanonicalContractModel.schema_version` 升到 "2" 并给 evidence 加按版本分派的兼容路径，是独立的 L 级工程。且 `resolve_governance_contract` 的四个调用点**全都在起 run 之后或只读预检**，不是受理期，挂错地方实现不了「不落 Memorial」。若真要正反例，载体应放在**不参与 hash 的** `edict.metadata` 或独立 append-only 表；另注意 codex 里**只有命令类 `prefix_rule` 支持正反例**，path/host 类无先例。

#### B8. 客卿二进制路径与版本写进回执

**codex 出处**：`sdk/python/src/openai_codex/client.py:176` `resolve_codex_bin`（只认显式路径或 pin 死的 runtime 包，**绝不回退 PATH**，`sdk/python/tests/test_artifact_workflow_and_binaries.py:1081` 钉死）。
**天枢落点**：`executor/keqing/adapter.py`、`executor/execution_gateway/policy_models.py:318` 的 `ExecutionReceipt`。**S / P2**。

**现状确认**：`ExecutionReceipt.executable` 填的是 `request.command_argv[0]` 即裸名 `"codex"`/`"claude"`，绝对路径交给 OS 走 PATH 解析——「吃 PATH 上碰巧那个」成立。而 canary 对比两代 executor 时，若底层 CLI 是碰巧那个版本，**对比结论无效**，这直接击穿自进化闭环的可归因性。

**PR-1（只做「解析 + 记录」，不做「拒绝」，不 spawn 任何进程）**：把 `keqing_api.py:44` 的 `_detect_installed_version`（读 package.json，不 spawn）下沉到 `executor/keqing/version.py` 共用，按 `(realpath, st_mtime)` 缓存；准备阶段用 `shutil.which` + `Path.resolve(strict=True)` 把 argv[0] 替换为绝对路径（这样 `gateway.py:294` 现有的记录逻辑自然记绝对路径，**无需改 receipt schema**）；`ExecutionReceipt` 新增两个带默认值的可选字段 `executable_version` 与 `executable_version_source: Literal["package_json","pinned","unverified"]`（`schema_version` 保持 "1"，纯追加）；为 claude-code/codex/opencode 补 `PINNED_*_VERSION` 常量（对齐已有的 `PINNED_PI_VERSION`），**drift 只上报不拦截**。

**PR-2（前置 = PR-1 跑满一个 canary 周期）**：若实测确认对比被版本漂移污染，再引入 `supported_version_range` + `ExecutionDenied`。证据落点**不要**塞进 `EnvironmentEvidenceV1`（它是 per-snapshot 且 fingerprint 对 facts dict 写死，一个 memorial 多次执行放不下）。

> **验证修订**：原提案拿 `MINIMUM_SUPPORTED_CODEX_VERSION` 论证「运行时按版本区间 deny」——**拔高了**：它在 `exec-server-protocol/src/lib.rs:14`，全仓只有两处引用（定义本身 + `run_version_skew.sh:7`），是 CI 版本偏斜测试的下限常量，不是运行时闸门。

#### B9. EventBus 谓词等待测试夹具

**codex 出处**：`codex-rs/app-server/tests/common/test_app_server.rs:158,1782`（`pending_messages: VecDeque` + `read_stream_until_message(predicate)` 先查缓冲再读流、不匹配 `push_back`；`clear_message_buffer` / `pending_notification_methods`；`:1222` `interrupt_turn_and_wait_for_aborted` 这类显式终态收尾 helper）。
**天枢落点**：新建 `tests/support/waiting.py` 与 `tests/support/ws_client.py`。**S / P2**。

`await_event(bus, predicate, *, timeout)`——在 `src/tianshu/bus/event_bus.py:53` 上挂临时订阅 + 缓冲已到达事件（照搬 pending 缓冲语义），配 `drain()` / `seen_types()` 断言「没有多余事件」。**硬规则：所有等待必须带谓词，timeout 只作失败上界不作同步原语。** 另在既有 `TestClient.websocket_connect` 之上包一层同样的谓词缓冲读取器。

> **验证修订**：原提案要起真 uvicorn 子进程，并声称能治「26 个 timing_sensitive 断言 + 三处偶发用例」——被证否：`timing_sensitive` 只有两个文件（`test_outbox_scheduler_idempotency.py:25` 的 APScheduler 真实时间窗、`test_execution_gateway_processes.py:48` 的子进程取消 1 秒上界），**两者都在进程内、都不经 uvicorn/WS**，live-server 夹具对它们零帮助。真正在 sleep 的是 application/executor/universe/integration 那批，走 EventBus 即可。真进程覆盖已由 `tests/packaging/test_fresh_wheel_demo.py` 承担。**明确不承诺修那两个 timing_sensitive 文件**——那要改判据（可注入时钟 / 进程状态查询），属独立条目。

---

### ★ 低优先（P3）

#### D1. 位面 GC 加一条窄引用判据

`universe/gc.py` 的 `_collect` 判据只有两条（不在 universes 表即孤儿 / `archived` 且 mtime 超期），**完全不查引用**。加一条：`archived 且 mtime 超期 且 无非终态 memorial 引用`（`migrations.py:1456` 已有 `idx_memorials_universe_id`，一条 `EXISTS` 子查询即可），命中即 kept，在 `universe.gc` 事件 payload 里加 `kept_referenced` 计数。另在 `manager.restore()` 与 `diff()` 入口前置 `store.exists()` 检查，目录已回收时抛明确错误取代裸 `FileNotFoundError`。约 30–50 行 + 2 条测试。**S / P3**。

> **验证修订（WRONG_FACTS）**：原提案要建 `UniverseReferenceIndex` 扫五类引用 + deadline + 整轮放弃 + force 单删入口——大幅超配：artifact 在独立内容寻址根、与位面 GC 无交集；`evolution_routing_allocations` 无 `universe_id`；引用面是两条 SQL 不是全盘文件扫描。**但验证者提了一条更值得查的**：codex 的 `rollout_reference_index` 真正的对应落点是 `evidence/service.py` 的 `ArtifactStore` 配额清理——那里才是内容寻址物被多个 bundle 共享、删错就真断证据链的地方。**建议单独核实 ArtifactStore 的配额回收是否已按 digest 引用计数**。

#### D2. WS per-connection 有界队列

每连接一个有界队列 + writer task，满则以 4408 关连接，让浏览器按已有的指数退避重连。前端无需改动。**P3**，前置 [A1](#a1-ws-出站所有权过滤已实测确认的越权)。

#### D3. events / memorial 落库前套 redact

在 `event_repo.append_event` / `append_event_envelope` 与 memorial 写路径（`mappers.py:392`）落库前套一层 `redact_sensitive_mapping`（`sensitive_payload.py:216` 已有）。复用现成判据、零新模块、零新表、零迁移。**P3**。

> 这是原「起居注双通道」条目被判 **ALREADY_EXISTS** 后剩下的窄尾巴，详见 [§6](#六天枢已具备无需借鉴)。

#### D4. HTTP 错误信封统一

加 app 层 exception handler 把 `HTTPException` 与领域异常统一投影成 `{code, message, correlation_id, retryable, details?}`（与前端已有的 `ApiProblem` 字段对齐，前端归一逻辑因此可整段删掉），保留旧形状一个版本并写明删除版本号；AST 架构测试**放在最后一步**加，且要先把 69 处裸字符串 detail 清干净否则测试当天就红。**L / P3，单开迭代，不当顺手活。** 与 [B5](#b5-前端类型契约先补-response_model再谈-codegen) 的先后关系成立（先统一信封再生成类型）。

---

## 四、反直觉发现（不必立刻做，但会校准判断）

这些是测绘过程中最有信息量的部分，很多与直觉相反：

| # | 发现 | 对天枢的意义 |
|---|---|---|
| 1 | **compaction 不删除历史**。`CompactedItem.replacement_history` 把压缩后的替换视图整份写进 rollout 的一行，原始记录全部原地保留。可回放性与上下文预算是**两个正交维度**：文件永远完整，模型看到的是文件的一个投影。 | 直接对照天枢三层 compaction：应确认压缩是否保留原文，否则「可复盘」在压缩发生后就断了 |
| 2 | **收敛顺序被写成硬约束**：Windows deny-read ACL 收敛必须**先应用新的期望集，再撤销旧集里不在期望集的项**（`windows-sandbox-rs/src/deny_read_state.rs:22`），顺序反了会出现无保护窗口。 | **这条对 P3 的 stage→warm→activate→drain 同样成立**，建议写进 P3 规格 |
| 3 | **企业下发的约束有能力上限**：`EnterpriseManaged` 来源的 requirements 层在解析前就被**物理删掉** `allowed_login_methods` / `cli_auth_credentials_store` 等四个字段（`layer.rs:11,92`）——防止控制面被攻陷后接管所有客户端凭证。 | 「上级机关」定位的自我约束范式：**治理者的权力也要有上限**，且这个上限应在解析前物理生效，不是靠运行时检查 |
| 4 | **约束层的合并方向与值层相反**：值层低→高折叠（后写胜），但 requirements 里的 deny 规则是**高优先级在前的求并**（`stack.rs:174` 的 `layers.iter().rev()`），冲突直接 fail-closed 返回 Conflict。 | 对应天枢 policy 分层的 tighten-only 语义 |
| 5 | **记忆使用率靠解析 agent 实际执行的 shell 命令来度量**——看 Read/Search 的 path 里有没有命中记忆文件，任一子命令解析成 Unknown 就整条放弃统计。用「agent 有没有真去读」度量召回，比在注入侧打点诚实得多。 | 天枢 skills/memory 的效果评估可直接照搬这个判据形态 |
| 6 | **「要不要干活」的判据是产物 diff 而非输入 watermark**：记忆合并看 git 工作区是否 dirty，README 明确写 watermark 只做记账。**输入变了不等于输出变了**，用产物判断才能避免唤醒昂贵的 agent。 | 对天枢各类 Curator 的触发判据是直接的反例校正 |
| 7 | **整套协议完全没有 `protocolVersion` 字段，也没有版本协商**。演进纯靠：方法名即版本、字段 additive-only + serde 默认值、experimental capability opt-in、命名的兼容性单测。「v1/v2」只是模块名。 | 反驳「要先建版本协商」的直觉；天枢的加法式演进 + 契约测试路线是对的 |
| 8 | **TUI 和 exec CLI 不是内部直接调 core**，而是 app-server 的**进程内客户端**；请求 typed，响应仍刻意穿过 JSON-RPC result 信封——注释明说是为了不产生第二套执行契约，宁可付编解码成本 | 天枢 CLI 已走 httpx 打同一套 REST，方向一致，值得写进 ADR 固化 |
| 9 | **feature flag 永不删除**：`Stage::Removed` 的项继续留在表里占 key、加载时 `continue` 忽略，甚至存在 `default_enabled: true` 的 Removed 项——纯粹为了老配置不报错 | 配置键永不删、只标 removed/deprecated、加载时忽略并在起居注记一条 `legacy_usage` |
| 10 | **隐私回撤保留 wire 形状而不删字段**：`accepted_lines.rs:100` 的 `line_fingerprints: []` 是硬编码空数组，schema 保留字段名但不再发送任何内容——**下游消费方比字段本身更难改** | 天枢下线证据字段时的正确做法 |
| 11 | **`PreToolUse` 的 `allow` 不是「放行」而是「改写后放行」**：不带 `updatedInput` 会被判为不支持并把 hook 记成 Failed；纯放行的正确写法是**什么都不输出**。且竞争改写的解决规则是「**实际完成最晚者胜**」而非「声明最后者胜」，报告顺序与决策顺序被刻意分成两个序 | 天枢补 hook 事件面时的协议设计陷阱清单 |
| 12 | **hook 信任指纹哈希的是「规范化后的配置身份」而非文件哈希**——写法不同但语义相同的 hook 得到同一个 `trusted_hash` | 天枢做 hook 授信时的正确指纹形态 |
| 13 | **撤销历史授权用一次性迁移 + marker 文件，而非每次启动重算黑名单**——否则用户主动重新批准的规则会被老黑名单反复删掉。**安全清理必须是幂等的一次性事件，不是持续策略** | 反直觉但正确，天枢做策略收紧时同理 |
| 14 | **拒绝必须给出替代品**：`execpolicy` README 明确要求 `decision="forbidden"` 时 justification 里给推荐替代品（例句 `Use jj instead of git.`）——把「护栏体验」写进策略语言规范，不留给 UI | 天枢 PolicyEngine 的 deny 理由字段可加此约束 |
| 15 | **rollout 不 fsync**，逐行 `flush()` 到 OS 就算数，接受掉电丢尾；恢复端靠「补 `\n` + 跳过坏尾行 + ordinal 跳跃容忍」兜住。真正 `sync_all()` 的只有迁移 journal | 「durability 放在恢复端而非写入端」的成本取舍参照 |
| 16 | **SDK 只暴露 `auto_review` 与 `deny_all` 两种审批模式，完全没有「问人」这一档**——程序化入口刻意把交互式人工审批整个删掉，那是 TUI 的职责 | 天枢将来做 SDK 时的边界划分参照。**但注意反向**：`CodexConfig.experimental_api` 默认 True、`_default_approval_handler` 无条件放行——这两个默认值天枢**不能抄** |
| 17 | **features crate 里完全没有百分比灰度、分桶、服务端下发或 A/B 分组**（grep `percent\|bucket\|canary\|experiment_group` 零命中）。所谓「灰度」只有三样：Stage 枚举当文档、用户手动开、企业 requirements 强制 pin | **天枢的 per-(kind,subject) canary + basis points 比 codex 更进一步**，不必向它看齐 |
| 18 | **仓库配置被当成不可信输入**：`git-utils/src/fsmonitor.rs` 用 60+ 行注释只为决定 `core.fsmonitor` 该覆盖成什么，因为它可以指向任意可执行文件 | 天枢读取位面内 git 配置/工具配置时的同款警觉 |
| 19 | **code-mode 的 tools 对象每个方法闭包绑定「工具下标」而非工具名**（object-capability 模型），JS 侧无法凭字符串调用未授权工具，越界直接 TypeError；且**嵌套调用仍完整走原审批链** | 若天枢将来做脚本编排，这是底线而非可选项 |
| 20 | **给模型的工具描述是 TypeScript 声明而不是 JSON Schema**；`exec` 是 freeform + lark 文法工具而非 function tool，为的是让模型直吐原始 JS 避免 JSON 转义 | 工具描述形态对 token 效率的影响，天枢工具面可实测 |
| 21 | **执行器插件 hook 因 manifest 未签名，被一条硬编码白名单缩到只剩一条**（`computer-use@openai-bundled` 的 Stop）——整套机制建好了，实际放行面积是 1 | 提醒：能力建成 ≠ 能力开放。天枢插件 501 + manifest_only 的克制是同类判断 |

---

## 五、对抗验证推翻了什么

三视角合成的 14 条建议，**没有一条原样通过**。这一节是本文可信度的来源。

| 条目 | 裁决 | 被推翻的内容 |
|---|---|---|
| A2（原 B1） | NEEDS_RESCOPE | 四条不变量删掉两条：「摘要输入是语义投影」已是 plan:301 既定规格；「activate 前重算 SystemSnapshot」在规格里无对照物，且其「SkillsWatcher 直改 active 造成漂移」的因果链不成立（skills 摘要不在 `release_digest` 的四个输入里）；「持单调 generation」已由双 CAS + partial unique index + 不可变 journal 三重覆盖 |
| A3（原 B2） | NEEDS_RESCOPE | 「`EvolutionPolicy` 改开放转移集合」会毁掉 `mode CHECK` 那道 DB 墙、破坏「auto 双重不可表达」的验收项；「写入后压平」等于事后改写已落库 lifecycle，与 append-only 冲突；「必须是数据不是 lambda」是在反驳一个不存在的设计；probe 端点冗余；V33 已被占用 |
| B4（原 B3） | NEEDS_RESCOPE | 硬错：`credential_isolation` 能力位不存在且**不能新增**（撞 frozen 契约与冻结夹具）；「scoped_token 无人调用」不实；token 前缀不该改；`ForwardFn` 拿不到 registry；`pricing_cny` × 7.2 单位陷阱；effort 从 L 上调 XL |
| D1（原 B4） | **WRONG_FACTS** | `UniverseReferenceIndex` 五类引用里三类不存在（artifact 在独立内容寻址根、`evolution_routing_allocations` 无 `universe_id`）；deadline/整轮放弃机制对两条 SQL 是超配。缩到 30–50 行 |
| B1（原 B5） | NEEDS_RESCOPE | 必须三步走（对拍 → 覆盖 → 切换），一次切完会误收紧或误放宽；`/mcp` Mount 要单独登记；`/docs` `/redoc` `/openapi.json` 要显式登记；`_unsafe_unknown` 的 404 与新 deny 有先后顺序问题；effort S→M |
| B8（原 B6） | NEEDS_RESCOPE | `MINIMUM_SUPPORTED_CODEX_VERSION` 是 CI 版本偏斜测试的常量，**不是运行时闸门**，拿它论证「按版本区间 deny」是拔高；拆成「先记录、后闸门」两个 PR；证据不能塞 `EnvironmentEvidenceV1` |
| B3（原 B7） | NEEDS_RESCOPE | 「schema 覆盖率只有 1/N」不成立——已有两套机制三个落盘 schema，且 `$id` 注入 helper 已在生产代码里；再建一套等于第三套写法；CI 汇总门禁前必须先处理 `dependency-review` 的条件跳过 |
| B6（原 B8） | NEEDS_RESCOPE | 「enabled 与 error 正交」在天枢**无对应物**——插件面根本没有 enabled 状态，`status`/`loaded` 已钉成字面量；「P2 owner 前置」叙事被删；坏记录不能写 plugins 表 |
| A1 + D2（原 B9） | NEEDS_RESCOPE | 四子项冷热不均：越权可见是真洞（保留并升级）；**未决裁决重放被证否**——codex 需要重放是因为 pending request 只活在内存，天枢的裁决是持久化 DB 行且已带所有权分流 |
| （原 B10） | **ALREADY_EXISTS** | 提案点名要收口的 evidence 与 attempt.error 两处**都已收口**，且正是用提案要的入站 fail-closed 方式；且 `Attempt` 根本没有 `error` 字段（是 `failure: RedactedError`）。仅剩 D3 那条窄尾巴 |
| B7（原 B11） | NEEDS_RESCOPE | 类型名 `PermissionsV1` 不存在；`resolve_governance_contract` 的四个调用点全在起 run 之后或只读预检，**不是受理期**；`expectations` 参与 `content_hash` 会破坏已落库合同；codex 里只有命令类 `prefix_rule` 支持正反例，path/host 无先例 |
| B2 + D4（原 B12） | **WRONG_FACTS** | 「codex 把正则抠错误文案列为反模式」**完全说反了**——`client.py:77-79` 自己就在这么做；`map_api_error` 在 Python SDK 里根本不存在；`is_retryable` 单一真源天枢**已有一份**（`models/failure.py`），不该新建 |
| B9（原 B13） | NEEDS_RESCOPE | 受益方论证被证否：`timing_sensitive` 两个文件都在进程内、不经 uvicorn/WS，live-server 夹具零帮助；746 秒读错了；改成不起子进程的 EventBus 谓词等待 |
| B5（原 B14） | NEEDS_RESCOPE | 前提是假的——**249 个操作里只有 2 个有具名响应模型**，117 个是裸 `ApiResponse`（`data: anyOf[{},null]`），100 个空 schema。先做生成器只会产出 `data?: unknown`，把「响应无类型」固化成生成物，比现状更糟。必须先补 `response_model` |

---

## 六、天枢已具备，无需借鉴

| codex 机制 | 天枢已有的等价物 |
|---|---|
| otel 双通道 / 敏感字段分流 | `evidence/models.py:59-63` 的 `_redacted()` **pydantic field validator**（入站硬拒，不是出站补救）；`evidence/service.py:171` `_reject_secret` 对 artifact 字节硬拒；`storage/run_state_repo.py:38-40` `_require_secret_free` 持久化前抛错；`models/side_effect.py:59` 同款。redact 的入站调用点 10+ 处 |
| 「provider 错误不许塞 response body」 | `models/canonical.py:16` `RedactedError{code, message, retryable, details_hash}` 是 frozen/extra=forbid，`executor.py:234` 写的是常量 message。`Attempt` 根本没有 `error` 字段 |
| schema 生成物入仓 + 漂移比对 | `tests/evidence/test_schema_contract.py:31` 与 `tests/launch/test_lean_preview_schemas.py:519` 两套；`models/lean_preview.py:249` `_schema_for` 已是「`$id` 外部注入」 |
| `is_retryable` 纯谓词 | `models/failure.py` 的 `FailureReason` + `classify_failure` |
| hook 卸载的 `ptr_eq` 身份语义 | `src/tianshu/kernel/hooks.py:78-80` 已是 `e.handler is not handler` |
| 编译期穷尽性替代物 | `tests/architecture/` 八个 AST 边界测试（`test_promotion_authority.py`、`test_no_direct_process_launch.py` 等） |
| 灰度发布 | **天枢更强**：codex 的 features crate 零百分比灰度；天枢有 per-subject canary + basis points + `EvolutionPolicy` 三态 |
| 沙箱能力自证 | `ExecutorCapabilityManifestV1` + `MandatoryCapabilityMismatch` 已是正确形状 |

---

## 七、明确不建议借鉴

1. **用 Starlark 或任何可嵌入脚本语言做策略 DSL。** Python 侧等价物是 `exec`/`eval`，等于把策略文件变成任意代码执行入口，与 Ring 0 永不进化、与 PolicyEngine fail-secure 直接冲突。只借 `execpolicy` 的**语义**——Decision 做成有序格（`Allow < Prompt < Forbidden` 取 max，杜绝「规则顺序决定安全性」）、规则自带正反例（已收编为 [B7](#b7-敕令受理期校验-allowed_paths-glob)）、拒绝必须给出替代品——载体坚持纯数据（TOML/JSON + pydantic）。
2. **自建 OS 级沙箱**（seatbelt / landlock / bubblewrap / Windows 受限令牌）与 `shell-escalation` 的 execve 拦截 + `SCM_RIGHTS` fd 转移。天枢 `supports_sandbox=False` 看似短板，实则定位正确：沙箱是**被治理对象**该自证的能力。用 Python 追 Rust 的系统编程必然长期落后，还会把叙事从「上级机关」稀释成「又一个带沙箱的 runner」。真要补，补的是合同层「要求客卿开启自身沙箱并回执」。
3. **code-mode 与内嵌 JS 引擎**（V8 isolate、cell 生命周期、会话级可变 store/load），以及 SDK 把多个物理 turn 重写成一个逻辑 turn（改写 `turnId`、合成 `started_at`/`duration`）。前者是执行层的 token 效率问题不是治理问题，会话级可变 store 与 Evidence 不可变、Effect journal append-only 直接冲突；后者为聚合视图改写事件 id 与耗时**等于伪造证据链**——确需「逻辑批次」视图就新增 `group_id` 做聚合，原始事件一个字节不改。
4. **`ext` 那套 46k 行编译期扩展体系与 12 个 contributor trait**，以及 features crate 的编译期注册表 + untagged enum。前者收益来自 Rust 零成本 trait 分发，Python 单进程照搬只得到一层空接口 + import 环，还与既有 hooks / `ExecutorAdapterRegistry` 撞车；后者会成为天枢的**第三套策略面**（已有统一模型注册表 + `EvolutionPolicy`），制造三处漂移。可借的只有两条纪律：按生命周期切点而非功能类型切分；以及 `Stage::Removed`（见 §4 #9）。
5. **exec-server 的独立进程边界与远端中继**（Noise 加密、protobuf relay 帧、分段序号/ack_bits/重传/resume、rendezvous、stream_id 多路复用），以及 `app-server-daemon` 整套（pidfile 守护、UDS 控制平面、**detached updater 每小时跑 install.sh 自我替换**）。前者 4.8 万行绝大部分为跨主机执行付费；后者的「自动更新自己的二进制」与 `automatic_promotion_allowed: Literal[False]` + DB CHECK 双禁直接冲突。要的是**协议形状**（能力位由执行端运行时自报、结构化 `Unsupported{operation}` 而非 501 字符串），不是进程边界。同理不要现在就建四传输抽象层——天枢只有 FastAPI 一条真实传输。
6. **marketplace / remote 插件分发那半边**（git sparse checkout、npm 源、bundle archive、按账号缓存、`startup_sync`、version 目录 + rename 换版）。`core-plugins` 41k 行里过半是这条线，解决的是发行渠道问题不是治理问题。天枢插件 install/enable 一律 501 + `manifest_only` 只读投影是对的；补这条线会让定位失守，还会引入第二套互不知情的磁盘生命周期管理器（`~/.tianshu` 曾达 46G 正是这类问题）。**本轮任何提案都不应成为打开动态加载的借口。**
7. **HTTPS MITM + 自签 CA 的完整凭证经纪人形态。** 要向子进程分发 CA bundle 并处理各语言 HTTP 客户端的信任差异，codex 自己承认 Go 的 `net/http` 会绕过 loopback 代理只能靠 OS 沙箱兜底。天枢止步于 [B4](#b4-客卿凭证网关接线钥匙不出治理层) 的「本地网关持钥 + 形状保持替身凭证」即可，不要去解密客卿的 TLS。
8. **把能力白名单硬编码进源码**（`ALLOWLISTED_EXECUTOR_PLUGIN_HOOKS` 与 28 个插件 id 的 allowlist，codex 自己都挂着 FIXME），以及 `bypass_hook_trust` 这类全局旁路开关、「策略引擎不可用时放行」的兜底。天枢要同款闸门必须落 DB + 走起居注审计。**再开一个绕过授信的布尔量就是在治理墙上凿洞。** 同理不要抄 SDK 出厂默认值：`experimental_api` 默认 True、默认审批 handler 无条件放行——天枢任何实验开关缺省必须关，审批缺省必须是拒绝并记录。
9. **第二套构建系统**（Bazel + Cargo 双轨，靠脚本手工维持两套 lint 一致），以及**用 metaclass/exec 在运行时生成路由或协议表**。Rust 有卫生宏，Python 没有；运行时魔法会毁掉 IDE 跳转、类型检查与 traceback。要抄的是「一张显式 dataclass 表做唯一真源 + 独立生成脚本 + 生成物入仓可 diff」。`uv + pytest + ruff + mypy + import-linter` 已经够。
10. **`agent-identity` 全套**（Ed25519 密钥 + 远端注册换 runtime_id + 每任务签名 + JWT/JWKS 校验）、**`process-hardening`**（`PR_SET_DUMPABLE` / `PT_DENY_ATTACH` / setrlimit / pre-main ctor）、**`arg0` 多人格分发**。单机单进程没有对应的信任锚，CPython 也没有 pre-main hook。只取 `AgentBillOfMaterials` 那个三元组（版本 / harness id / 运行位置）写进客卿注册元数据做能力归因（与 [B8](#b8-客卿二进制路径与版本写进回执) 同一落点）。
11. **不要把 codex 的单文件巨兽当范例**（`reducer.rs` 3708 行、`bespoke_event_handling.rs` 4139 行）——codex 自己的 `AGENTS.md:51` 就规定模块 <500 LoC。天枢「200–400 典型 / 800 上限」不因「大厂也这么写」而放松。

---

## 八、推荐优先级

| 序 | 条目 | 成本 | 与 P0–P7 的关系 |
|---|---|---|---|
| 1 | [A1 WS 出站所有权过滤](#a1-ws-出站所有权过滤已实测确认的越权) | S | 无关，**可立即做**——唯一「当下就存在且已实测确认」的洞 |
| 2 | [A2 dispose 身份校验](#a2-contributionhandle-的-dispose-身份校验) | S | **写进 P2 规格**，不是新阶段。代码还没写，此刻定死最便宜 |
| 3 | [A3 canary 索引 + 执法收口](#a3-canary-唯一性下沉-db--策略执法收口一处) | M+ | **并入 P4a 的 0033 迁移**，不新开迁移号 |
| 4 | [B1 路由 scope 表](#b1-路由-scope-提为显式表并加全路由覆盖测试) | M | P4/P5 新增三组路由前做，吃红利 |
| 5 | [B2 重试判据收敛](#b2-重试判据收敛进已有的-modelsfailurepy) | S | 并行 |
| 6 | [B3 schema 落盘 + CI 门禁](#b3-v1-契约-schema-落盘--ci-汇总门禁与工作树洁净断言) | M | **前置 P1**——`SystemSnapshotV1` 正在定型，此刻登记成本最低 |
| 7 | [B4 客卿凭证网关](#b4-客卿凭证网关接线钥匙不出治理层) | **XL** | **单开迭代**。战略级（叙事与实现裂缝最大），但体量决定它不能塞进 P0–P7；是 P5 EXECUTOR 可复现性的前置 |
| 8 | [B5 response_model 补齐](#b5-前端类型契约先补-response_model再谈-codegen) 第一期 | L | 并行；第三期（codegen）必须等 [D4](#d4-http-错误信封统一) |
| 9 | [B6](#b6-插件坏-manifest-可见--发现路径抗-symlink) / [B7](#b7-敕令受理期校验-allowed_paths-glob) / [B8](#b8-客卿二进制路径与版本写进回执) / [B9](#b9-eventbus-谓词等待测试夹具) | S–M | B8 与 P3 的 binary_path 固化同 PR 最省；B9 是 P3/P4b 写确定性验收测试的前提 |
| 10 | [D1](#d1-位面-gc-加一条窄引用判据)–[D4](#d4-http-错误信封统一) | S–L | P3 及以后 |

**一句话取舍**：前三条（A1/A2/A3）现在做，因为它们防的是**正在写的代码和已经存在的洞**；工程基建四条（B1/B2/B3/B5）随手做，因为它们防的是半年后的漂移；B4 战略上最重要但体量 XL，应当单开迭代而不是挤进当前主线。

---

## 附：未排进本轮的候选

以下八条被三视角提出但未进前 14，共同点是**新接缝或新功能，而不是堵现有的洞**——应等 P1–P7 主线落地后再排：

- 裁决「批准并立规」（一次批准沉淀成规则）
- 贡献冲突带来源分级（`on_conflict` 的来源优先级）
- **hook 事件面上移治理链**——这是记忆里「Hooks UI 可视化」老待办的根源：天枢生命周期 hook 面实际只有一个事件（`executor/policy_hook.py:49` 的 `on_before_tool_call`，≈PreToolUse），`executor/hooks.py` 只有 3 行；codex 有 9 个事件 + 完整引擎（发现 / schema 校验 / 分发 / command runner / MCP runner / 输出解析 / 输出溢出落盘）
- **`ContextFragment` 带 kind 与硬预算**——codex `AGENTS.md` 的硬规则值得单列：不许重写历史、注入项必须有硬上限、单项 >10K token 禁止、>1K token 的新注入项按 P0 人工复核、所有注入片段必须是实现 `ContextualUserFragment` 的结构体。天枢 `PromptBuilder` **已有**分片预算，但是散落的魔法数（`prompt_builder.py:105` 的 `_peer_profile_max_chars=600`、`:117` 的 `skills_char_budget=30000`、`:172` 的 `char_budget=2000`），所以这条是「**收编成显式契约 + 总预算断言测试**」而非从零建
- 客卿外部资产隔离导入（对标 `external-agent-migration`）
- 事件名常量表
- 配置键 Stage 注册表（见 §4 #9）
- 能力清单端点

另外，codex `AGENTS.md` 里有一份可直接照抄的**「破坏性变更外部契约面清单」**：app-server API / 原始响应事件 / CLI 参数 / 配置加载 / 从旧 rollout 恢复会话。天枢的对应五面是：HTTP API / SSE 与 WS 事件形状 / CLI 参数 / 配置加载 / 从 memorial+attempt 续跑。建议直接写进 `.claude/rules/` 的 review 检查清单。

---

## 九、落地裁决（2026-08-25）

已并入 [docs/plan/2026-08-25-self-evolving-agent-os-landing.md §8](../plan/2026-08-25-self-evolving-agent-os-landing.md#8-codex-harness-借鉴并入项2026-08-25-裁决)，判据只有一条：**防的是正在写的代码或已存在的洞 → 并入 P0–P7；防的是半年后的漂移 → 独立并行轨；新接缝/新功能 → 主线收官后**。

| 处置 | 条目 | 落点 |
|---|---|---|
| 并入阶段规格（关键路径 +4–5 天） | A2 dispose 身份校验 | P2 改动清单 |
| | A3-a partial unique index / A3-b 执法收口 `save_candidate` | P4a `0033` 同块 |
| | B8 二进制绝对路径 + 版本入 receipt；B9 EventBus 谓词等待夹具；§四 #2 先立新后撤旧 | P3 |
| | B5「新端点必须参数化 `response_model`」纪律；B3 新契约登记 | 方案 §4.2 / §4.4 |
| 独立并行轨（≈8–10 天，可交第二个会话） | **A1 WS 所有权过滤（立即）**、B2、B7、B3（P3 前）、B1（P4a 前） | `fix/` / `feat/` 独立 PR |
| 主线收官后单开迭代 | B4 凭证网关（XL，0.7.0 候选）、B5 存量回填 + codegen、B6、D1–D4、hook 事件面、ContextFragment 预算、`Stage::Removed`、破坏性变更清单 | 未开 issue |
| 明确不并入 | §七 全部 11 条 | 方案 §8.4 明文禁止以「codex 也这么做」为由引入 |
