# CC-Fable v1 执行台账

> 规则：每个切片一条记录，包含 commit、测试证据与审查结论。状态枚举沿用
> `codex-v1/SOURCE-OF-TRUTH.md`（证据：`implemented / focused_verified /
> automation_passed / external_pending / user_approval_pending / passed`；
> 排期：`planned / in_progress / blocked_by_upstream / superseded_snapshot / passed`）。
> 旧台账（另一轮工程治理与 Agent OS G0–G1.4b2）见
> `codex-v1/evidence/progress-snapshot-2026-07-12.md`，不在此重复。

## 当前状态

```yaml
package_status: approved_2026-07-12 (D1 附加 P1 复审; D4 完整范围; D5 连续实施; D3 push 已授权)
current_stage: S0 G1.4b3 close (P0 passed, P1 passed)
sole_worksite: ~/tiangong/tianshu-worktree/tianshu (feat_cc_fable_v1)
source_clone: ~/tiangong/tianshu (FROZEN 2026-07-12)
```

## 台账

```
=== P0 资产回收 (2026-07-12) ===
审批: D1=A+P1复审附加条件 / D2=A / D3=A(push已授权) / D4=A完整 / D5=B连续 / D6,D7=默认 (见 02 号文档审批记录)
P0.1: complete (无遗留进程; 冻结前指纹 25 条目 / 17 files +2703/-104 对齐 STATUS.md; freeze commit 08e3742 @ wip/g1.4b3-freeze; 源树冻结后 clean)
P0.2: complete (bundle ~/tianshu-agentos-20260712.bundle 14M, verify "complete history"; push origin feat_codex_phase_1 + wip/g1.4b3-freeze 均 [new branch] 成功)
P0.3: complete (staged docs/codex-v1 副本丢弃[与 7386cf3 字节级一致已验证]; .idea 噪声还原; merge-base 重验=d8631a2 仍成立; ff merge 后 HEAD=7386cf3, main..HEAD=44 commits)
P0.4: complete (uv sync --frozen --python 3.12 --all-extras; Python 3.12.12 对齐 codex 基线; pytest 9.0.3/ruff/lint-imports 就位。备注: 首次 uv sync 默认选 3.14 且缺 dev extra, 已纠正)
P0.5: complete (干净 HEAD full not-slow: 2543 passed / 1 skipped / 1 deselected, 250s——对齐 G1.4b1 基线 2521 + G1.4b2 增量; 15 warnings 记录[coroutine acompletion never awaited 等历史已知类]; 静态门禁四项全绿: ruff check 全过 / format 663 files 零 diff / lint-imports 2 kept / mypy 108 files Success)
P0.6: complete (cherry-pick -n 08e3742 + reset; 指纹 26 条目[25 WIP + 本包目录] / 17 files +2703/-104 / 8 untracked 与 STATUS.md 逐一对齐; git diff --check 干净)
P0.7: complete (FROZEN-2026-07-12.md 已置于源 clone)
P0.8: complete (本包提交, hash 见 git log "docs: add cc-fable-v1 execution package")
=== P0 出口条件核验: 6/6 满足 → P0 passed ===
P1: complete (04-inherited-code-review.md; 总评"可保留, 需按排期局部重构, 不需大改"; 1 CRITICAL[迁移 checksum 不含 callback 源码指纹→并入 S0.2] + 6 IMPORTANT[execution_gateway 2495 行等体量超标→重构候选清单] + 3 MINOR; 新增 P1.R1 切片[S0 后拆 execution_gateway]; "明确不动"清单已锁定)

=== S0 G1.4b3 收口 (开始) ===
S0 review base: 1acec44 (docs 提交, 排除在 S0 diff 之外)
S0.1: complete (P0.6 即重新冻结: 指纹 25+1 条目 / +2703/-104 / 8 untracked 对齐 STATUS 无漂移; git diff --check 干净; 文件分组按 DEVELOPMENT-HANDOFF Commit A/B 归属确认)
S0.2: in_progress
  - 17 文件 owned suite 首跑: 10 failed / 333 passed / 2 skipped——全部失败根因一致: pre-ledger 恢复路径中 V4 _validate_workspace_foundation_schema 的"精确匹配 V4 reference"遇到 V5 形状表(apply_decisions 多 8 列/触发器被 V5 替换)而 fail closed
  - 裁决: V4 callback 为已提交冻结物, 其"精确匹配"在 V4=最新时正确, V5 加入后暴露 latent 假设——STATUS 所披露"V4 callback 曾被改写"即为被撤销的错误修法; 正确修法为框架层 canonical adopt: pre-ledger 库若与"全部迁移完成后的权威形状"语义等价则整体采纳只补 ledger, 否则逐版重放(V4 只会遇到 ≤V4 形状, 精确匹配依然成立); 对未来 V6+ 自动成立
  - 附带: historical fixtures 失真修正(真实历史库无 workspace 表); V5 present 分支触发器重建幂等性核查; 新增守卫测试(V4 形状库不被 adopt 误吞/adopt 幂等)
  - 修复已委托单写者实现代理(TDD), 验证链: storage 域 → 17 文件 suite → compileall/ruff/format/diff-check
  - 修复①完成 (canonical adopt): migration_ledger.py +56[adopt_migrations/ledger_exists, 事务化+并发安全] / migrations.py +~56[_matches_canonical_schema + run_migrations 分支] / 测试 +67[historical fixtures 失真修正(DROP workspace 8 表)+守卫测试 test_v4_shape_preledger_replays_v5_instead_of_adopt]; FTS 触发器豁免复用 V1 _EXPECTED_OPTIONAL_TRIGGERS 精确匹配机制; V5 触发器 DROP+CREATE 幂等性核查通过(无需改); RED 10 failed→GREEN: storage 域 124 passed, 17 文件 suite 344 passed/2 skipped/0 failed
  - 修复②完成 (callback 指纹守卫, P1 CRITICAL): 新增 tests/storage/test_migration_callback_freeze.py——MIGRATIONS 全部 upgrade callback + 2 个 validate helper 的源码 SHA-256 冻结; 变异验证: V4 callback 插行→精确命中该条目红, 恢复→8 passed
  - 注意: migration_ledger.py 新入 WIP 修改集, Commit A owned 清单追加之(active brief 7 文件清单写于此修复前)
  - 体量豁免裁决: workspace_service.py 2186 行/migrations.py 2156 行超 800 上限——S0 收口不做结构移动(保持已审查代码可追溯), 拆分挂 P1.R1/S1(见 04 号报告清单)
  - 静态门禁 (dirty tree 全项): compileall OK / ruff check 全过 / format 672 files 零 diff(修正 1 个新测试文件) / lint-imports 2 kept / mypy 108 files Success / git diff --check 干净
  - 独立审查: 已派干净上下文审查代理(Commit A 域, spec/安全/质量三维 + adopt 与指纹守卫专项), 结论待回
S0.2+S0.3: complete (Commit A = 29ef814 "feat: add governed workspace apply authority", 15 文件)
  - 独立审查结论: SPEC PASS / SECURITY PASS / QUALITY APPROVED, C/I=0, MINOR×4
  - required checks 逐项证据确认(V5 additive/authority binding/one-time decisions/safe receipts/locks/root-anchored+rollback/symlink+TOCTOU/pending ownership/exact identities/POSIX mode/CAS/failure injection/rollback truth——各有测试实跑绿)
  - STATUS 五项暂停前修复逐项落地确认: ①POSIX mode(_desired_posix_mode+fail-closed 测试) ②expiry 原子 claim(version CAS+V5 触发器) ③close_lease 锁与 pending 保护 ④V4 改写已撤销/V5 additive(指纹+拒绝测试) ⑤rollback 真值(仅实际 restore 成功才记 succeeded)——全部实跑绿
  - MINOR 处置: #1(指纹表缺 adopt 门控)已采纳修复——_matches_canonical_schema+adopt_migrations 入冻结表(10 passed); #2(adopt 不重跑语义校验, 实际不可达)记录不改; #3(PRAGMA f-string 标识符校验, 已实测不可注入且 fail-closed)挂 S1 migrations 拆分顺路; #4(read_worktree_entry 每次新开 AnchoredRoot, 只读预检无失败场景)记录
S0.4: complete (Commit B = a9106aa "feat: expose governed workspace apply surfaces", 12 文件)
  - 独立审查结论: SPEC PASS / SECURITY PASS / QUALITY APPROVED, C/I=0, MINOR×2
  - 专项确认: actor 全链路 server-derived(伪造 actor 字段 422 且不达 service); token 仅存在于一次性签发响应(设计使然), 视图白名单投影/错误固定消息/CLI stdin-only+redaction 多点实测无泄露
  - required checks: 四路由语义+路由面锁定 / workspace:apply 三层强制(中间件+API+service) / CLI 退出码契约+防注入 / capability 精确证据(ENFORCED 升级有测试证据, 限制诚实披露)
  - MINOR 处置: #1(中间件尾斜杠末段匹配为防御纵深冗余, 另两层独立强制且 Starlette 仅规范路径执行 handler, 无可利用场景)记录, S0 报告中说明中间件非唯一 scope 门; #2(_ERRORS 与 service 错误码维护耦合, 已核验超集无缺口+fail-closed 有测试)记录, 共享注册表评估挂 S2 顺路
S0.5: complete (唯一一次 full not-slow Gate: 2678 passed / 2 skipped / 1 deselected / 0 failed, 652.82s——较迁移基线 2543 净增 135 个 G1.4b3 测试; 17 warnings 均历史已知类; 报告 reports/g1.4b3-report.md)
=== S0 收官: G1.4b3 = passed (Commit A 29ef814 + Commit B a9106aa + 报告, clean tree) ===

=== P1.R1 execution_gateway 拆分 (2026-07-12) ===
P1.R1: complete (commit e0bdf74; 2495 行单文件 → policy_models 382/grants 687/process_backend 720/gateway 781 + facade 254 全量 re-export, 依赖单向; 70/70 符号字节级 MATCH; src 调用方零改动; 4 测试适配[架构豁免路径+3 处 monkeypatch 指向 grants 子模块, 语义零放宽]; 提交前 full Gate 2678 passed/0 failed 与 S0 收官一致; 六项静态门禁全绿)
  备忘: gateway.py 781 行贴上限, S1 触碰时注意; facade 含 os/sys 模块 re-export(测试可见性兼容层)

=== S1 / G1.5 wheel·离线 Demo·Doctor (开始) ===
必读契约: codex-v1/design/15-g1.5-wheel-demo-doctor-brief.md + 16-implementation-slices + 17-resource-recon + 18-runtime-recon
切片: S1.1 wheel 资源/manifest → S1.2 overlay precedence+六 persona 迁移 → S1.3 零网络 demo provider(禁 live 回退) → S1.4 Doctor+live/ready → S1.5 fresh HOME 黑盒
冻结事实: Logo hash 不变 / site-packages 只读 / COURT.md reset=删 overlay override 自然回退 packaged
S1.1: complete (commit 48a285d; tianshu.resources catalog[Traversable-only, zip/目录健壮] + 417 文件入包[六部门/396 模板/2 skills/3 executor 模板/LICENSE] + brand SHA 冻结 + parity 守卫[双树字节一致, S1.2 收口删]; RED[manifest 6 failed]→GREEN[tests/resources 20 passed 含 slow 真实 uv build]; focused 360 passed; 独立审查 Spec PASS/Quality APPROVED C/I=0, 1 信息性[package_digest executor family 含 __init__.py]; 六项门禁全绿)
  S1.5 阻断备忘: web index.html 引用 Google Fonts 外链, 零网络黑盒前须移除/内联(S1.3 或 S1.5 前)
S1.2: implemented, 审查中 (实现完成: overlay.py 113 行[PackagedDefaults 进程级 as_file 固化+COURT 三 helper+写护栏]; 三包篡改 bug RED→GREEN[PersonaLoader.delete/COURT PUT/SkillsLoader builtin COW]; COURT reset 四断言全过+packaged COURT.md SHA ef705712 锁定; v6 0006_seed_default_personas[指纹 5951a0b2 登记, 空库恰六部门/非空零注入/幂等/删 hubu 不复活, adopt 域回归绿]; 10 处消费者切换含真 bug 修复[executor 模板 edict/ 子目录遗漏致 wheel 下全量静默 TEMPLATE_FALLBACK, 新增守卫]; 根树 417 文件收口+grep 零残留; focused 888 passed + gateway 485 passed; 六项门禁全绿; +226/-87 预算内)
  - 已知裁定: universe/evals 显式配置+dev 回退+警告(硬 require 留 S1.4 Doctor); 10 处 ledger 尾断言追加 v6 属合法演进
S1.2: complete (commit 9a23f9f)
  - 三视角审查工作流(spec/security/regression 并行 + C/I 双反驳者对抗验证, 7 agents): security PASS; spec 与 regression 独立发现并各自运行时复现同一 CRITICAL——COURT override 对 PromptBuilder 不可见(resolve_court_read 生产零调用, 写读分叉, 相对基线功能回归)
  - CRITICAL 修复(TDD, RED 2 failed→GREEN): PromptBuilder 注入 runtime_personas_dir 经 resolve_court_read 读 COURT(build+build_layers, 兼容 None 走旧路径); system_api COURT 三分支统一 overlay helpers + 拒绝非 court persona_id 孤儿写入; 端到端锁定测试(PUT→preview 含 marker→reset 回退 packaged 正文)
  - 连带发现并修复: tests/test_prompt_builder.py 与 test_persona_model.py 的 fixture 指向已删根树(S1.2 grep 漏 Path 构造式引用; 根级散文件不在 focused 域)——改指 packaged_defaults().personas_dir(); 补跑全部根级散文件 757 passed 确认无其他遗漏
  - MINOR 处置: COW 只读包权限场景无测试(记录, S1.5 黑盒含只读包场景时复核); evals 警告已补(与 wiring_universe 对齐); v6 事务纪律专属测试缺失(记录, 框架层通用回滚测试间接覆盖); overlay 单例线程竞态(记录, 正常启动时序窗口关闭); system_api 内联语义已借 CRITICAL 修复统一到 helpers
  - 最终验证: gateway overlay 6 passed / 根级 757 passed / focused 888+485 passed; 六项门禁全绿
S1.3: in_progress, 审查修复中
  - 实现完成: providers/demo.py 197 行(确定性形状决策树/流式取消点/零 usage/[DEMO] 标记); 三层收口(LLMClient 精确模型名分派[LiteLLM/tenacity/Router 之前]+ConfigManager runtime_override 掩蔽+ProviderManager demo_mode 短路且 sync_all no-op); governed demo Edict 冒烟(COMPLETED/[DEMO]/零成本/DEMO.md 经正常工具路径落盘/providers 表零行); 新增 8 passed + focused 126 passed; 六项门禁全绿
  - 补提交 73b2ca7: S1.2 的 add 范围(src/tests/scripts)遗漏仓库根树删除记账, 416 文件 D 补齐入库(实现代理发现)
  - 双 lens 审查工作流(redline/quality + 对抗验证, 14 agents): 红线1"live 永不回退 demo"穷举确认 HOLDS(dispatch 在 tenacity/Router/litellm 之前, 全部 live 失败分支不落 demo); 确认 IMPORTANT×4: ①demo 流式最终 chunk content=None 与 live 语义分歧(流式路径 memorial summary=None、[DEMO] 证据丢失, 已复现) ②demo 下 PUT /api/configs 空 body 把 demo 状态写进 providers 表(sync_from_config 无守卫, 已复现; legacy PUT 回显矛盾) ③get_config 无条件掩蔽伪造存在性(personas llm_config_name 校验失效, dangling 引用持久化) ④demo 形状标记与生产 prompt 无绑定守卫(文案漂移静默变形)
  - 修复包已委托(4 IMPORTANT + 2 顺手 MINOR[测试 .env 隔离/launcher pin startup_profile]); 记录不修: 模型名单独 opt-in 行为(标记+零 usage 使其无害)/providers.__init__ 传递 import litellm(S6 打包再议)/cancellation 生成器内取消点测试强度
S1.3: complete (commit a215cef)
  - 修复包全部 RED→GREEN: ①流式最终 chunk 携带全量累计内容(对齐 live 契约+双断言) ②provider 配置写面 5 路由 409[runtime_locked]+manager 防御纵深 ③get_config 存在性语义修正(不存在→None, persona 校验恢复) ④形状标记绑定生产 prompt 源守卫 ⑤测试 .env 隔离 ⑥launcher pin startup_profile
  - 验证: 修复批次 26 passed / focused 326 / gateway 全域 491 / 六项门禁全绿
S1.4: in_progress, 审查修复中
  - 实现完成: diagnostics.py 486 行(DoctorReport schema v1 + 18 稳定 check id + _safe_evidence allow-list 脱敏 + canonical JSON/table 双渲染); /health/live 与 /health/ready 分离(required: database/migrations/scheduler/worker/resources/provider/workspace → 503; optional: mcp → degraded); Scheduler/WorkerPool/WorkspaceService 三个 is_ready 属性; doctor CLI 扩展; 消费者更新(local.sh wait_healthy→ready, web useHealth/HealthDot 三态+demo 标签); 含 S1.2 遗留的 universe/evals repo_root 检查项
  - 实现自验: 新增 46 测试 + focused 704 passed + web 53 tests/typecheck/build 绿 + 六项门禁
  - 双 lens 审查(exposure/quality + 对抗验证, 18 agents, 全部实测复现): CRITICAL×1 + IMPORTANT×8
    · [CRITICAL] provider readiness 形同虚设——只断言 startup_profile ∈ (live,demo)[pydantic Literal 已保证, 恒真], 逃生口 state is None 永不可达; 实测 live+空凭证 → /health/ready 200 ready 而同 settings 的 doctor 报 provider.config=fail; 后果: local.sh 报健康/HealthDot 绿/编排器放流量, 而每条 Edict 首次 LLM 调用即失败(违反 brief E.2)
    · [IMPORTANT] 匿名信息面: public 路由中间件在设置 auth_context 前短路 → secure-remote 认证详情分支是死代码, trusted-local 下任意未认证 peer 拿到完整 checks(内部拓扑/profile/mcp 状态)
    · [IMPORTANT] Doctor 违反只读: WAL 库 mode=ro 连接落盘 -wal/-shm(实测目录树变化); 只读目录下 ro 打开抛错 → 健康库误判 fail + 退出码 1 假警报
    · [IMPORTANT] rich markup 吞掉 [degraded] 状态列(恰好 8 字符无填充被当标签)——恰恰只有需关注的降级项丢标记; evidence 未转义穿过 markup(端口返回体可注入)
    · [IMPORTANT] sandbox 探测用不存在的字段(container_runtime_detected/docker_available), container_hint 在任何主机恒 False——恒说谎的信号
    · [IMPORTANT] degraded 层生产不可达: _optional_integrations 只有"对象存在即健康", 永不为 False → HealthDot 琥珀态与 assess_readiness degraded 分支皆死代码
    · [IMPORTANT] evals remediation 打印不存在的 env 名(TIANSHU_EVALS_REPO_ROOT vs 真实 TIANSHU_EVAL_REPO_ROOT, extra=ignore 会静默吞掉)
    · [IMPORTANT] provider required 注入测试造假态(伪造组件不可能产生的 state=None), 证明不了真实 handler
    · MINOR: HEAD 探测 405 / storage._conn 无锁访问 / readiness 与 doctor 的 migrations 判定不一致 / Scheduler.is_ready 的 true-with-zero-tasks 窗口 / web getHealth 孤儿
  - 第一轮修复包完成(13 项 RED→GREEN; 代理末段 API 中断, 由主控制者接手完成回归: 后端 865 passed / web 53 passed+tsc / 六项门禁全绿[补跑 format])
  - 对抗性复验工作流(4 名独立验证者各自构造探针实测原失败场景, 不信仓库测试): **4 项中 3 项判定"未真修好"**——测试全绿但代码仍错
    · [仍未关闭 + 新增回归] provider readiness 判定源错误: 修复读 settings(env) 而非运行时真正生效的 active LLM config → ①原 CRITICAL 仍可达(env 有 key 但 active 配置被 API 清空/切换 → 仍报 ready) ②新引入反向故障(key 只存 llm_configs、经 Web UI 配置的可用实例被永久判 503, k8s/LB 会摘除——相对修复前是回归) ③doctor 与 readiness 共用同一错误源, 一致性守卫因此无法暴露缺陷
    · [未修好, 且更隐蔽] MCP degraded: app.py 调用 session.is_connected——MCPServerSession 根本无此属性(真实字段 status), AttributeError 被 _guarded() 静默吞掉 → optional.mcp 检查整个蒸发 → 状态恒 ready; **测试之所以绿是因为 _BrokenSession 桩自造了这个 API**(伪造 API 的桩保护不了生产代码); 另 MCPManager.start() 失败路径不写入 _sessions, "配置了但启动没连上"(最常见生产故障)完全不可见
    · [残留] doctor 只读: sidecar 判定用 any(-wal or -shm), "有 -wal 无 -shm"时仍创建并遗留 32KB -shm; immutable=1 分支有 TOCTOU(服务并发 checkpoint 时可能读到陈旧数据)
    · [已修但有放宽] 匿名信息面已关闭(未认证只得摘要, 认证详情分支可达), 但 detail 层不跑 _required_scopes() → 只有 mcp:read 的窄 scope PAT 能拿到完整内部拓扑(未经裁决的权限放宽)
  - 第二轮修复完成(13 项 RED→GREEN, 全部用真实类型/真实运行时路径)
  - 第二轮对抗复验: **4/4 判定真修好**(独立探针实测: 真实 ConfigManager/Storage/MCPServerSession/PAT 签发/ASGI 端点; 验证者先独立复现"干净 WAL 库上 mode=ro 会新建两个文件"证明修复确有必要)
  - 主控制者清理三处残留(自行实现): ①多 active 行时 doctor 取第一行而 ConfigManager 取最后一行 → 对齐 ORDER BY + reversed 选取, 新增守卫并变异验证(撤销修复即红) ②MCP 冷启动窗口(session 在 _starting_sessions 未落 sessions)被算作失败 → 每次重启假降级; 新增 starting_names, 只有"既没连上也不在启动中"才算真失败 ③doctor docstring 的"绝不新建任何文件"绝对承诺与事实不符(-shm 在而 -wal 缺失的异常形态下共享模式仍会物化空 -wal) → 改为诚实边界声明
S1.4: complete (commit 3f65e2a; 最终 focused 1131 passed / web 53 passed+tsc / 六项门禁全绿)
  - 本切片教训(值得记入工程纪律): 修复代理的测试曾写在**自造属性的桩**上(_BrokenSession.is_connected), 而生产代码调用的正是真实类上不存在的同名属性, 异常被 _guarded() 静默吞掉 → 测试全绿、检查蒸发、状态恒 ready。仅凭"865 passed 全绿"提交就会把它带到发布。已加两条结构性守卫: 测试桩属性集必须是真实类的子集; _guarded 不得把抛异常的检查降级为"检查消失"
S1.5: in_progress, 审查修复中
  - 实现完成: Google Fonts 外链移除(index.html + 系统 CJK 回退字体栈, 构建产物零外链); 黑盒环境(repo 外 venv + fresh HOME, 路径含空格与非 ASCII, PYTHONPATH 缺席 + PYTHONNOUSERSITE, 依赖装完后写入 sitecustomize socket 围栏); 10/10 断言 passed(doctor JSON 无 secret / live→ready / brand SHA 3f2bb6cf…ace799 / 六部门 / 两 builtin skills / [DEMO] 零成本 Edict / lease+restore_point+change set 证据 / 源不变量[未裁决则源 HEAD 不变、DEMO.md 只在 staging] / SIGTERM 无 ResourceWarning / quick_check / 包 digest 不变)
  - **围栏自证**: socket.connect 外网 IP 与 getaddrinfo 均被拦(不先证明围栏会拦, "零网络"就只是没发生而已)
  - **黑盒首跑抓到真实发行缺陷**: duckduckgo_search.py 模块级无条件 import lxml(非核心依赖, 开发 venv 里由 scrapling 传递带入故单测从不暴露) → 只装 tianshu[cli] 的发行物 API 根本起不来(uvicorn 加载 app 即 ModuleNotFoundError); 已按仓库既有 build_scrapling 模式惰性化 + 变异验证
  - 双 lens 审查(blackbox-integrity/honesty): 均 PASS 无 CRITICAL, 但 6 个 IMPORTANT 中数条**击穿本切片证据本身**——
    · [证据失效] wheel 的 Web 载荷不可复现: src/tianshu/web/static/ 被 gitignore、只由手工 npm build 产出, pyproject 无任何构建钩子 → 干净检出(CI/其他开发者/发布流水线)构建出的 wheel **零 Web 文件**; 且实测 wheel 混入 3 个来自陈旧 build/lib 暂存目录的孤儿资产(~933KB, 源树里没有) → 852 entries 与 wheel SHA 是脏构建目录的产物, 同源码两次构建结果不同
    · [真实产品缺陷] memorial 报 completed 时变更集尚未捕获: 客户端看到 completed 立刻调 workspace changes 会拿到空/不完整结果——**这不是测试时序, 是产品可见 REST 竞态**; 黑盒用 60s 有界轮询把它盖过去了
    · [同类缺陷未清干净] feishu/telegram 子包 __init__.py 同样模块级 import 非核心依赖 → tianshu[cli] 里 GET /api/tongzheng/channels/{feishu,telegram} 直接 500; lxml 只是这一类的第一个
    · [CI 盲区] CI 既不构建 wheel、又以 -m "not slow" 排除整个黑盒 → 这类回归 CI 永远发现不了
    · [弱断言] digest_before 在跑完之后才取(运行期篡改 packaged 资源抓不到, 正是 S1.2 修的那类 bug); 六部门/两 skills 用 >= 超集断言而非精确集合
    · [MINOR/边界] socket 围栏只拦 Python 层, 子进程(shell 技能/git/curl)可绕过——是围栏真实边界, 须如实标注不得让人误以为"零网络"覆盖子进程
    · [MINOR] lxml 在 pyproject 未声明, 却是 public/full 档位默认 search_provider(duckduckgo) 的硬依赖 → tianshu[cli] 用户默认档位的默认搜索引擎不存在
  - 三轮修复 + 两轮对抗复验后收口 (commit 498b1e4)
    · 第二轮修复: in-tree PEP 517 后端(构建前清暂存树 + 缺 Web 载荷拒绝出 wheel, 把"正确的 wheel"变成构建系统的性质而非口头约定) / 竞态时序 / 惰性化 feishu+telegram / lxml 声明 + doctor 检查项 / 黑盒弱断言收紧
    · 第一轮对抗复验: build ✅ core-only ✅ 但 **竞态未修完**(只改 execute_edict 与 _execute_outer_loop, **漏 DAG 路径**——而多任务受治理计划正是路由到 DAG, 端到端复现 memorial 200 completed / changes 409) + **修 A 破坏 B**(backend-path 目录不自动进 sdist 且无 MANIFEST.in → sdist 构建/安装断裂, 而只跑 wheel 路径的 CI 看不见)
    · 第三轮修复: 三条执行路径收敛到同一条不变量(变更集可读先于终态可见)+ 跨路径参数化守卫(第四条路径出现也会被抓) + 变异验证; MANIFEST.in + CI 加 sdist→wheel→install→smoke; 守卫拦截改 meta_path finder(旧的 builtins.__import__ 钩子漏 importlib.import_module)
    · 顺带: 假 DAGScheduler 桩未跟随真实 run() 签名致 tests/executor 永久挂起 → 新增 AST 守卫强制"桩必须跟随真实接口"(桩与真实接口脱节这轮已咬两次: S1.4 的 _BrokenSession.is_connected, S1.5 的 fake scheduler)
  - 最终验证: executor 180 passed / packaging+resources+tools+diagnostics 340 / gateway 538 / 黑盒+manifest 全绿; 六项静态门禁全绿
S1.5: complete (commit 498b1e4)

=== S1 / G1.5 待收官 ===
S1 Gate: pending (唯一一次 full not-slow + 显式跑 slow 黑盒/manifest; G1.5 报告; 记录 wheel SHA 为"某次构建的证据"而非不变量[前端产物变化时会变])
遗留备忘(交 S1 Gate 报告或后续阶段):
  - _run_prepared_dag 现有两种终态来源(scheduler 返回值/内存置态), 靠 persist_terminal 标记区分——逻辑正确且有测试锁定, 但属 S3/G2 重写执行循环时应一并收敛的复杂度
  - socket 围栏只拦 Python 层, 子进程不受约束(已在证据中如实标注)
  - S1 Gate 建议串行跑(并发重型 pytest + uv build 曾观察到锁竞争长时间无进展)
```

=== S1 / G1.5 Gate ===
S1 Gate: passed (non-slow: 2821 passed, 2 skipped, 24 deselected, 19 warnings in 654.96s (0:10:54); manifest: 13 passed, 4 warnings in 7.11s; fresh-HOME: 10 passed, 4 warnings in 23.22s)
Report: docs/cc-fable-v1/reports/g1.5-report.md
Next: S2 Lean Security

=== S2 / Lean Security Gate (2026-07-14) ===
S2 Gate: passed (focused security: 106 passed / 0 skipped / 0 deselected / 4 warnings in 19.73s; final non-slow Attempt 3: 2925 passed / 2 skipped / 24 deselected / 20 warnings in 751.37s; four static Gates passed)
Gate history: Attempt 1 = 4 failed / 966 passed / 1 skipped / 24 deselected / 8 warnings in 278.76s; Attempt 2 = 10 failed / 2231 passed / 2 skipped / 24 deselected / 18 warnings in 754.24s; both stopped and fixed through reviewed test-only amendments before Attempt 3
Live migration tail: v8 `0008_encrypt_mcp_secret_mappings` (runtime checksum/fingerprint and run-specific SystemAudit terminal hash recorded in report)
Report: docs/cc-fable-v1/reports/s2-lean-security-report.md
Review: zero unresolved Critical / zero unresolved Important; full G1.6, remote MCP security, persistent stdio exact binding, container and public supply-chain release remain deferred
Next: S3 Core Governance

=== S3 / Core Governance Gate (2026-07-17) ===
S3 Gate: passed (source `60d3c45b836de44b132dba186e5c9a3672592ea3`; inherited cleanup `a1ec0a8`; retained-evidence checker `60d3c45`)
Inherited cleanup: exact nine = 9 passed / 0 failed; surrounding eight-file batch = 58 passed; continuation/Decision/Evidence/schema batch = 76 passed; WorkspaceService now receives the sole DecisionService from composition and managed L3 recovery preserves immutable plan lineage
Focused fault matrix: 199 passed / 0 skipped / 0 failed / 4 warnings in 14.52s; all notifier tests: 14 passed / 0 skipped / 0 failed / 4 warnings in 0.71s
Static Gates: Ruff check passed; Ruff format 824 files formatted; mypy 125 source files clean; import-linter 455 files / 1571 dependencies / 2 contracts kept / 0 broken
Final non-slow Gate: 3825 passed / 2 skipped / 24 deselected / 0 failed / 7 warnings in 629.39s (0:10:29)
Gate history: planned focused filenames were rebound to the actual durable Decision and managed outer-loop recovery files under a source-existence guard; first full diagnostic rejected checker direct Git subprocess use, then checker moved to narrow named GitBackend read-only operations without an architecture exemption; independent checker review then required seven source-bound retained logs, derived hashes/counts/exits, canonical JSON, and whole-report/capability/PROGRESS claim scanning; follow-up review confirmed that double-negation, alternate-word-order, OpenTelemetry/full-OTel, multiple-replica, and equivalent Chinese variants remain not claimed; final remediation added 60 fixtures over copies of all three actual newline-terminated documents and made every adversative clause carry its own canonical negative boundary before this final rerun
Boundary: passed only for managed Native durable governance, tracked effect semantics, Evidence Bundle v1, durable internal notification and single-node SQLite; full OTel/SLO, external notification delivery, PostgreSQL/K8s/multi-replica, external CLI internal governance, container and publication remain deferred/not claimed
Report: docs/cc-fable-v1/reports/s3-core-governance-report.md
Next: S4 Core

=== S4 / Core Web Gate (2026-07-17) ===
S4.1-S4.7 + Lean S4.12: automation_passed; visual/interaction user_approval_pending
Core surfaces: truthful Control Center, authoritative governed Edict detail, and pre-S5 disabled Evolution Center are implemented on real read contracts (`6ccf90a`, `81c3125`)
Browser-found product fixes: `e69f50a` (onboarding navigation ordering; durable Decision timestamp decoding; WCAG contrast/focus; 200% shell/Edict responsiveness; valid PolicyTimeline control semantics)
Real-stack Playwright Gate: 41 passed in 36.3s = 9 accessibility + 24 visual + 8 governed journey/contract/performance/locale checks; zero serious/critical axe findings on all three core routes; frozen-identity keyboard traversal and per-control 200% equivalent checks passed
Visual matrix: user-facing `zh-classic`; 3 routes × 2 viewports × 2 themes × 2 sidebar states = 24 committed PNG baselines; fresh-stack comparison 24/24 passed; SHA-256 manifest verified; user visual/interaction approval remains pending and is not collapsed into automation status
Web verification: lint 0 errors/35 retained warnings; typecheck passed; 35 unit files/186 tests passed; production build passed; manifest-backed minified/gzip ceilings passed for Control 7.00/2.25 KiB, Evolution 7.00/2.25 KiB, EdictDetail 70.00/20.00 KiB, and DAG 220.00/72.00 KiB; initial-load network proof excludes DAG/EdictDetail/Persona/System deferred chunks; inherited shared antd >500k warning remains disclosed
Evidence regression: 3 passed; focused Ruff check/format passed
CI: separate `web-e2e` job (Node 20 + frozen Python 3.12/all-extras + production build + Chromium + real-stack suite)
Boundary: S4.8-S4.11 department depth deferred; VoiceOver/manual cross-browser accessibility external_pending; no wheel/container/publication/OIDC/mobile/S5 claim
Report: docs/cc-fable-v1/reports/s4-core-web-report.md
Next: user visual/interaction approval, then S5 Core

=== S5 / Lean Core Evolution Gate (2026-07-18) ===
S5 Lean Core Gate: passed (focused candidate/gate/promotion/routing/rollback/authority: 329 passed / 0 failed / 4 warnings in 22.34s; final non-slow: 4178 passed / 2 skipped / 24 deselected / 0 failed / 7 warnings in 714.16s)
Evolution Center: real candidate, current gate blockers, current-version allocation/assignment counts, last gate hash, and rollback state are exposed through the S4 read contract; an immutable compatible green gate remains visible across real canary/promote/rollback lifecycle advances; guarded mutations remain PromotionService-only and require reason/expected version
Static Gates: Ruff check passed; Ruff format 881 files clean; mypy 131 source files clean; import-linter 481 files / 1745 dependencies / 2 contracts kept / 0 broken
Web verification: Evolution Center 14 passed; typecheck passed; production build passed; inherited antd >500k chunk warning remains disclosed
Evidence: a deterministic production harness exercised CandidateService/SkillInstallService, two closed Evidence Bundles, GateEvaluator, three resolved action-bound Decisions, seven immutable PromotionService journal rows and receipts, 10,000 durable ChallengerRouter assignments (1,029 challenger / 10.29%), reopened-storage assignment stability, promote, and rollback traffic closure; strict unknown fields, forbidden deferred/full-G4 claims, missing Decisions, stale versions, corrupt bundles/assignments, and journal/receipt drift are rejected
Boundary: this closes only the Lean Core Gate; OpenHands, executor compatibility, ROI, cost calibration/enforcement, and full G4 remain external_pending; S4 visual/interaction approval remains user_approval_pending
Report: docs/cc-fable-v1/reports/s5-lean-evolution-report.md

=== Lean Developer Preview Candidate / Closure Task 6 (2026-07-19) ===
Status: automated Candidate passed for source
`01da3844dde77b5a9e56f346bed9b2605f7bc832` in batch
`20260719T083725Z-01da3844dde7`. The source-to-sdist-to-Wheel provenance binds
the committed Web source tree, raw `npm ci` / production-build logs, and all 53
rebuilt static files; Candidate verification independently rebuilt the 252
committed `web/` files in a temporary directory and matched the sdist/Wheel
payload byte-for-byte.
Final Gate facts: Ruff check passed; Ruff format 889 files clean; mypy 132 source
files clean; import-linter 2 contracts kept / 0 broken; backend 4412 passed / 2
skipped / 29 deselected / 4 third-party deprecation warnings, with no unawaited
coroutine or unraisable process-transport warning; packaging suite 28 passed / 4
warnings, including a passed exact-candidate-Wheel golden demo; npm clean install
found 0 vulnerabilities; Web lint 0 errors / 35 warnings; typecheck passed; 187
unit tests passed; production build passed; Playwright 41 passed. Exact Wheel
SHA-256: `bb1c0ca64cc125713863dfe4a927b5f8bc35ec0ff06a7d25b73ad3e121521f76`;
sdist SHA-256:
`502bd0d913f897c24d9b8d31c43141b79759e64e339dda065dde8ebc7ab74fea`.
Candidate: `docs/cc-fable-v1/evidence/lean-preview-candidate.json`; report:
`docs/cc-fable-v1/reports/lean-developer-preview-candidate.md`. Visual and
interaction approval remains `user_approval_pending`; publication remains
`not_authorized`; the D8 list remains deferred and is not counted as passed.

=== Agent OS P1 / V31 callback freeze decision (2026-08-25) ===
Decision: accept one append-only live migration, `0031_system_snapshots`, before
freezing its callback fingerprint. The callback adopts the V18 exact-object
replay rule: all seven V31-owned objects must either be absent or match the
authoritative normalized SQL exactly; partial or drifted schemas are rejected
atomically, and already-authoritative rows are preserved without rewrite.
Rationale: system snapshots and run bindings are durable identity evidence, so
silent shape adoption or replacement would weaken replay and continuity proofs.
Status: implementation and local verification complete; merge pending. V31
checksum is `847ce32541b7196604dbaae43c0c49fce08d2f5eef2a98a81088cab4be434cb7`;
callback fingerprint is
`877bc05eba9dfce696739fb8f754cd3e4d3eb7525b8e6263e1cab99cf4e814c2`.
Verification: 202 focused P1 tests; full backend 4806 passed / 2 skipped; Web
typecheck, 338 tests, lint and production build; real demo proved identical
snapshot digest across assignment, Edict detail and required Evidence artifact,
while `system_snapshot_enabled=false` preserved execution with no binding or
snapshot artifact.

=== Agent OS P3 / V32 callback freeze decision (2026-08-26) ===
Decision: accept one append-only live migration, `0032_runtime_generations`,
before freezing its callback fingerprint. The final V32 shape contains five
tables: immutable releases, generations, transition journal, active/last-good
pointers, and the immutable per-attempt `run_generation_bindings` continuity
marker. The callback applies the V31 exact-object replay rule to all V32-owned
tables, the partial unique active index, and lifecycle immutability triggers:
every owned object must either be absent or match the authoritative normalized
SQL exactly; partial or drifted schemas are rejected atomically, while an exact
pre-created schema is adopted without replacing rows. On both a normal V31
upgrade and exact-schema adoption, the callback idempotently backfills provable
system-bound selections, records a bound empty selection for provably non-Pi
attempts, and records `unresolved` for Pi or otherwise ambiguous historical
attempts so replay fails closed instead of silently rebucketing them. Existing
markers that conflict with that provable history reject adoption atomically.
Rationale: release material, generation state, transition journal, and
active/last-good pointers jointly form one continuity authority. Partial
adoption or permissive shape matching could select an unverified binary,
violate the one-active-per-scope invariant, or discard the rollback root.
Status: callback frozen after the decision above; implementation merged into
`feat/plugin-v1` via PR #99. V32 checksum is
`e8926305465f9372891379fb73298fbbb7b0e490032543abdbbafd29a1258142`;
callback fingerprint is
`67c47b31f787a514d567fd2cdb8754599073648fba412e2e66f5f8ea32a8470c`.
Verification snapshot: focused P3 lifecycle/readiness/continuity/migration tests
cover V31 prefix freeze, exact-shape replay, SQL-fault rollback, canonical
journal-chain validation, exact-attempt and latest OPEN Edict retention,
periodic trigger-time prebinding, warm-time package recheck, and required
readiness failure for serving-material drift. Rollback rechecks material after
the first verifier returns and execution rechecks pinned material before use;
material verification and SQLite pointer CAS are not claimed to be atomic
against a concurrent same-UID filesystem writer, which remains outside the
trusted-local threat model. Final gates: backend 5096 passed / 2 skipped across
one isolated full run plus the two default-setting assertions rerun without
path overrides; the macOS run used watchdog's equivalent polling observer to
avoid the known native FSEvents teardown crash. Web typecheck, 76 files / 339
tests, lint (0 errors / 31 pre-existing warnings), and production build passed.
Mypy, Ruff check/format, all four import-linter contracts, schema export check,
SVG validation, and `git diff --check` passed. Both checked-in Runtime
Release/Generation schemas regenerate deterministically.

=== Agent OS X2 / FailureReason retry convergence (2026-08-26) ===
Decision: keep the durable 17-value FailureReason taxonomy unchanged and make
`FailureReason.is_retryable` the single execution retry truth source. Exactly
five canonical reasons are retryable: provider capacity/rate limit, provider
server error, provider network, process failure, and agent timeout. Built-in
TimeoutError, ConnectionError, and OSError map to those canonical reasons in
specific-to-general order; unknown or custom exception types fail closed as
`agent_error.unknown`. P3's `generation_retired` remains an explicit,
non-retryable continuity failure outside the 17-value memorial taxonomy.
Boundary: this convergence changes neither attempt budgets/backoff/fencing nor
legacy outbox and transport-delivery retry behavior, and requires no migration.
Status: merged into `feat/plugin-v1` via PR #101. Focused execution,
attempt-ledger, fenced-completion, dispatcher, outbox, generation-continuity,
capability, and workspace lifecycle regression: 240 passed. Full backend:
5107 passed / 2 skipped with one unrelated macOS process-cleanup timing test
exceeding its outer one-second bound under suite load; that exact test passed
immediately in isolated rerun. Ruff check/format,
mypy (141 source files), and all four import-linter contracts passed. Durable
attempt assertions prove canonical RedactedError JSON is persisted unchanged in
`execution_attempts.failure_json` across retry and budget exhaustion.

=== Agent OS X3 / allowed_paths admission validation (2026-08-26) ===
Decision: make `storage.edict_repo._insert_edict` the authoritative admission
boundary and validate every `edict.runtime.policy_profile.allowed_paths` entry
before the first Edict INSERT. The gate reuses `validate_allowed_path_glob` and
rejects leading or trailing whitespace without trimming or normalizing the
submitted value. Relative globs are exempt only when their exact value appears
in the current `BUILTIN_TEMPLATES`; the exemption is derived from that constant,
not from a hard-coded `**/*` literal or the client-writable `template_name`.
Boundary: the change adds no migration, schema field, canonical-hash input, or
historical-row validation. Exact replay reads the already accepted durable row
without re-running the new gate, while a reused idempotency key with a changed
invalid payload still returns the existing 409 conflict before validation. A
fresh invalid submission raises the typed `InvalidAllowedPathGlob`, and the HTTP
boundary maps only that exception to a stable 422 detail containing
`code`, `path_glob`, and `message`; no Edict, requested contract, Memorial,
outbox event, or idempotency row is written. Direct `Storage.save_edict()` uses
the same gate and cannot bypass it.
Status: merged into `feat/plugin-v1` via PR #103. Focused
application and HTTP admission contracts: 44 passed. Idempotency, governance
contract persistence, workspace boundary, and path sandbox regression: 57
passed. Ruff and all four import-linter contracts passed. The live migration
tail remains V32. The target-branch CI matrix passed 6/6 after one unrelated
300ms scheduler timing test was rerun; its exact parameter passed six isolated
runs and the complete 26-test timing-sensitive slice locally without any X3
scheduler diff.

=== Agent OS X5 / explicit route scope policy (2026-08-26) ===
Decision: replace implicit API-prefix and HTTP-method scope fallbacks with an
immutable, first-match route policy. The current table contains 263 protected
rules and 15 public rules, and the architecture inventory contains 277 HTTP,
WebSocket, MCP mount, and static compatibility references. Every inventory
reference has exactly one public-or-protected owner, every declared rule reaches
a real route, and an unknown path or method receives no inferred scope. The
protected table encodes global-read as `api OR admin` and workspace apply as
`api AND workspace:apply`; docs/OpenAPI, `/api/ws`, all six MCP methods, SPA and
assets, auth-aware `/health/ready`, and exact dynamic webhook POST paths retain
explicitly tested behavior. Webhook publication and removal take effect in the
same middleware instance, while near-match paths remain protected.
Compatibility decision: an authenticated HTTP request under a protected
namespace with no registered route returns `404 route_not_allowed`. This
preserves the existing router-facing 404 contract without restoring a default
scope; anonymous protected requests still stop at authentication, and unmatched
WebSockets remain fail-closed. Coverage evaluates the actual runtime first
match, not only template ownership. Two mutation-style negative cases prove the
guard rejects a dynamic `/api/.../{id}` rule shadowing a later exact admin route
and a public SPA catch-all shadowing a later exact admin route.
Boundary: no database migration or schema change; the live migration tail
remains V32. Status: merged into `feat/plugin-v1` via PR #105.
Verification: final X5 focused matrix 216 passed and the complete
timing-sensitive slice 26 passed. Independent security review was APPROVED,
including an additional 184-test focused pass, a 33-test health pass, and zero
Starlette first-match mismatches across 261 top-level concrete route
declarations. Ruff, Mypy (141 source files), and all four import-linter contracts
passed. Target-branch CI passed 6/6; merge commit is `8f2c11cd`.

=== Agent OS P4a / evolution policy enforcement (2026-08-26) ===
Decision: add frozen EvolutionPolicyV1 and V33 evolution_policies as the
per-subject evolution authorization authority. Runtime grandfathering remains
skill=canary and every other current kind=manual when no durable row exists,
while GET policy returns 404 for a missing row and never synthesizes a durable
default. PUT uses strict missing-row CAS: only a missing row with null expected
version inserts version 1; missing+non-null, existing+null, kind mismatch, and
every stale expected version conflict. Repeating identical content with a stale
version is still rejected.

Compatibility decision: V33 installs the `(kind, subject_key)` partial unique
canary index, but P4a does not yet enable a second subject to enter CANARY. The
existing `get_routable_candidate` reader remains globally single-valued until
P4b, so `start_canary` and the sole `save_candidate` UPDATE authority retain a
temporary global single-canary write backstop. P4b will remove that global
backstop only after the multi-value reader is authoritative, while retaining
subject-level exclusion and the V33 index.

Safety decision: frozen blocks propose before artifact materialization,
start_canary before routing writes, and promote before its intended journal;
stage/evaluate may finish without receiving traffic, and rollback always
remains allowed. Repository-level policy, contract-cap, policy-cap, and canary
uniqueness checks protect direct and future candidate write paths. Policy
updates are rejected while the same subject has a promote intended or applied
journal that is not closed by a completed row with the same command key. This
prevents policy mutation across the external activation window and avoids
live/durable split-brain.

Boundary: no P4b assignment table, multi-subject read path, runtime overlay
dictionary, kill switch, or Web UI is included. The migration tail advances
from V32 to V33 only. V33 migration checksum is
`725e801902e3e8e321a369164d3a5728adb40f96a8c77f2644820a6f69671fc7`;
the frozen upgrade callback fingerprint is
`15aa3bd9527ca0c12be760c8213d029ac554e9ca5b6c7e117ad03c0fd4030d3c`.

Status: merged to feat/plugin-v1 by PR #107 at merge commit b94d4846; target-branch
CI 6/6 passed. Verification before merge:
515 P4a policy/CAS/API/migration/route and affected candidate, promotion,
rollback, gate, routing, schema, audit, Skills API and auth tests passed in
isolated groups. One initial multi-file process exited with the known macOS
watchdog/FSEvents native status 133 after its first file; every constituent file
then passed in a fresh process, so the native teardown is recorded rather than
misreported as a Python assertion. Ruff check/format, Mypy (143 source files),
and all four import-linter contracts passed. The two new admin-only policy
routes produce 265 protected rules, 15 public rules and 279 route inventory
references with exact coverage.

=== Agent OS P4b / per-subject assignment sets (2026-08-26) ===

Issue #108 and PR #109 are merged into `feat/plugin-v1` at merge commit
`a8a03071`. V34 `0034_run_subject_assignments` freezes a complete
per-Memorial assignment set rather than independent loose rows. Each row persists the
same canonical set hash and set size; owned objects are the table plus
`run_subject_assignments_sealed_insert`, `run_subject_assignments_no_update`, and
`run_subject_assignments_no_delete`. The repository writes 1..64 members as one batch
under a SAVEPOINT; 65 members fail before any assignment row is written. Readers
recompute the canonical set hash and size, so an incomplete or altered set fails closed.

The plural candidate reader is authoritative. For a fresh root, zero canaries keep only
the legacy projection, one canary keeps the exact legacy projection plus a V34 singleton,
and multiple canaries keep the legacy marker plus the complete V34 set. A follow-up first
inherits its parent's set instead of being reshaped from the current canary count. While
CANARY it keeps the parent selection, after PROMOTED it selects `candidate.candidate`,
rollback states select base, and ARCHIVED consults the current-version lifecycle journal
(missing provenance fails closed). Runtime maps use kind-prefixed keys, revalidate
provenance and digests per assignment, deeply freeze nested values, and apply `always`
overlays in a deterministic order. Singleton compatibility does not call the payload
resolver twice; the old and V34 JSON rows are still decoded independently.

Evidence emits no governed assignment artifact for a fresh zero-canary root, keeps the
old assignment artifact for a singleton, and emits only the assignment-set media type
for N>1. SystemSnapshot stores in `evolution_overlay_set` the digest of the canonical
overlay list, not the assignment set or its set hash. The Web policy surface is
truthful: availability/source/curator protection are read-only, while evolution mode
and max canary basis points use strict CAS. `SkillInfo.pinned` is curator protection,
not a version pin, and P4b does not add an enabled switch.

Frozen V34 migration checksum:
`2ef0237b22f47310bf1f5d48d20c0262998bba960f1c9418687e54860dd2172f`.
Frozen upgrade callback fingerprint:
`121909d74e49a0263e893327f0caf38f2915e322bd2028a099d4c5b8bde6f180`.

Final local verification is 5270 backend tests passed, 2 skipped and 24 slow tests
deselected; all 77 Web test files and 347 tests passed. Ruff, format, Mypy,
import-linter, TypeScript, ESLint (0 errors) and the production build also passed. The
admin-only policy list route brings the explicit scope table to 266 protected rules,
15 public rules and 280 route inventory references. This P5 branch is based on the
merged V34 target-branch baseline.

Routing order is existing replay, then continuity inheritance, then the fresh-root kill
switch. Disabling routing therefore stops fresh roots from newly selecting challengers
but does not break persisted follow-up continuity. The internal Evolution readiness probe
returns false; `evolution.rollback` is optional, so with no other required failure the
overall health is degraded and `/health/ready` remains HTTP 200. This deliberately keeps
a business-serving instance in service when optional self-evolution is disabled.

Rollback is behavioral, not schema erasure: disable routing and restart, drain active
attempts and OPEN continuities, use the normal promote/rollback path only for active
CANARY authorities until the global active-canary count is at most one and preferably
zero, and complete pending rollbacks. Do not force an already PROMOTED subject back to
base merely to revert code. Then deploy only a behavior-compatible build that understands V34.
Never deploy a pure V33/P4a binary onto a V34 database; preserve the migration
declaration/checksum/callback, table, triggers, ledger, and assignment rows for audit.

=== Agent OS P5-1 / executor candidate and generation authority schema (2026-08-26) ===

Decision: V35 extends the governed candidate kind wall with `executor` and adds
generation-scoped executor promotion authority. Because SQLite cannot alter the
candidate CHECK in place, the migration rebuilds `evolution_candidates` together
with its six direct FK children while `foreign_keys=ON`. It first requires the exact
V34 object set and inbound FK graph, renames the old seven tables aside, recreates the
canonical names, copies explicit columns plus rowid, verifies counts and both EXCEPT
directions, drops child temporaries before the parent, then restores the two indexes
and eleven triggers. Seven V35 temporary names are reserved; partial, drifted, or
extension-FK shapes fail before the first rename, and every phase remains inside the
migration ledger transaction.

Authority decision: `executor_generation_authorities` is the one-current-row-per-
candidate authority and `executor_generation_authority_journal` is append-only. The
current row admits only pending -> authorized/revoked, authorized -> revoking/revoked,
revoking -> revoked, and revoked -> pending at epoch+1. Same-epoch transitions retain
candidate, release, generation, promotion and creation identity; `revoking` cuts new
traffic while continuity drains before `revoked`. The schema owns two authority tables,
two indexes and six fail-closed triggers. V35 supports exact-target callback replay and
whole-schema no-ledger adoption without rebuilding objects or changing durable rows.

Boundary: P5-1 freezes schema and migration behavior only; authority repository and
promotion-service business writes are implemented in later P5 slices. The frozen V35
migration checksum is
`14402935160ab156af4deeec680986703941e4107db324f9ffcc1f587daf506e` and the frozen
upgrade callback fingerprint is
`2d6b9cea990835204a889ad2c65dadc9f34c3025cc4568df78d8087dee7f0f73`.

=== Agent OS P5 / governed executor candidates and reversible generation promotion (2026-08-26) ===

Decision: P5 completes the V35 `keqing:pi` executor vertical slice. `CandidateKind`
admits `executor`, while `HIGH_RISK_PROMOTION_KINDS={code,executor}` keeps an approved,
exactly bound Decision as a repository-level promotion floor. `ExecutorCandidateAdapter`
validates immutable Pi release material and the exact `executor:keqing:pi` subject
binding. This does not make arbitrary third-party plugins executable.

Promotion authority is durable and exact. The current authority row and immutable
journal bind candidate id/version/digests, release digest, generation id, start command,
and promotion journal. Executor `start_canary` stages and warms one deterministic
generation before authorizing it; challenger runs bind that authorized READY generation,
while champion runs retain the active pointer. `promote` only activates the mapped
generation. Missing, corrupt, ambiguous, stale, or revoked authority fails closed in
routing, recovery, and retention. Subject-level persistent fences serialize unresolved
start, promote, and rollback effects; a newer generation cannot make an older candidate
rollback through a live dependent canary.

Switch decision: `executor_generation_enabled` is a forward-evolution kill switch, not
an adapter-registration switch. `ExecutorPromotionAdapter` remains wired while disabled.
Fresh executor start-canary and promote operations fail before creating a new
journal/effect, while generation recovery, exact replay needed to terminalize an
interrupted effect, canary/promoted rollback, and pending-rollback reconciliation remain
available. A fresh activation is refused after disable; an already switched activation
may be finalized idempotently. This keeps the recovery mechanism available when forward
evolution is stopped.

The independently disabled `ExecutorDriftScanner` runs only from the control-plane tick,
creates at most one deterministic SYSTEM candidate for one observed release drift,
respects frozen policy, and never runs from a GET endpoint. Keqing status projects only
an already durable candidate and links it to the Evolution Center. Its GET path remains
side-effect free.

Boundary: this is a governed Pi executor slice, not automatic CLI upgrade, a direct
public GenerationController API, a general third-party PluginHost, automatic promotion,
or multi-process coordination. Same-command executor canary single-flight is
process-local, matching the supported single-process SQLite deployment model.

Local verification: the focused P5 executor/evolution suite passes 190 tests and the
migration/data-preservation suite passes 122. The complete non-slow backend collection
was exercised in platform-isolated shards; the macOS-only DNS, loopback-port, shell
launcher, and real-home lock boundaries are recorded separately for Linux CI rather
than treated as product failures. Ruff check/format, Mypy (145 source files), all four
import-linter contracts, Web typecheck, all 77 Web test files / 350 tests, ESLint
(0 errors), the production build, and all 32 Playwright E2E tests pass.
PR #111 was merged into `feat/plugin-v1` as `567b028e`.

=== Agent OS P6 / process SystemSnapshot generation and strict binding (2026-08-27) ===

Status: complete and merged. PR #114 was merged into `feat/plugin-v1` as
`8f32cc4c` with all six required checks passing.

Decision: P6 reuses the V32 generation tables with `scope=process`; no migration is
added. A dedicated startup bootstrap maintains process active/last-good generations,
strict target drift fails before P6 admission writes and before routing/plugin/Pi/run
startup, and non-strict drift is audited before advancing. Pi materialization,
authority, attempt binding, and reconciliation remain scope-isolated. Runtime binding
failures use the independent `system_snapshot_unavailable` code. Last-good means the
previous successfully activated and still retained snapshot, not a clean-exit receipt.

=== Agent OS P7 / per-run frozen Skills view (2026-08-27) ===

Status: implementation complete for issue #115; PR pending creation. Focused loader,
binding, dispatcher, snapshot-audit, promotion, and wiring regressions pass. Full PR CI
remains pending and no unknown PR number is recorded here.
The real three-thread barrier regression also passes: promotion blocks after its atomic
swap, the watcher blocks after cache invalidation, and a third thread freezes while both
are in flight; old/new views and rollback remain internally consistent.
Freeze opens directories as fds and records a stability witness for every file/directory:
device, inode, mode, size, mtime_ns, and ctime_ns. Injected generation participates in
the witness and injected Skills are sorted by name. A view is accepted only when two
consecutive whole captures, including all witness data, agree. Three attempts under
continuous churn fail closed; exchange plus cleanup cannot publish a mixed tree.
Every search-path component, Skill directory/member, and nested resource is captured
without following symlinks; any such symlink fails closed. Requirements, maximum raw
size, load-all eligibility, metadata, injected Skills, lower-layer fallback, precedence,
and budget behavior now have live/frozen parity regressions. The effective requirements
environment decision is hashed as `load-all-eligible`, so a decision change produces a
new source identity even when SKILL.md bytes do not change.

Absence remains assignment-aware. A selected base/champion absence removes only the
governed target layer and reveals a lower-priority source; a selected challenger or
unknown legacy absence preserves the historical hide-lower tombstone. New absent
candidates fail before start-canary, promote, or activate with the stable
`skill_absence_requires_durable_tombstone` error. A durable global tombstone is deferred
to P7b and must not be inferred from the compatibility overlay.

Decision: P7 freezes only Skills and adds no migration. Two default-off switches form
three rollout modes: off does not build a view; shadow builds, compares when a snapshot
identity is available, and leaves
the runner on live reads; enforce binds the immutable view and returns the stable
`skills_view_unavailable` failure before the runner when the decoded Skills identity is
missing, the view cannot be built, or its digest has drifted. A structurally corrupt
persisted SystemSnapshot/run binding remains a P6 binding failure and keeps
`system_snapshot_unavailable` in strict mode or `generation_binding_unavailable`
otherwise; P7 does not relabel it. Legacy and governed bindings, task-local isolation,
watcher invalidation, and Skill promotion/rollback cache invalidation are covered.

Audit semantics are explicit: only a real source-digest mismatch emits successful
`skills_view_drift`. Fatal view-factory, whole-capture, or model-validation failures,
plus a missing decoded Skills identity in enforce mode, emit failed
`skills_view_binding_failed`. Shadow skips comparison when identity is absent and does
not emit that failed event. A single SKILL.md parse failure retains loader warning-and-
skip behavior and does not by itself become a binding-failed audit. Both event classes
use one audit/outbox transaction and exclude Skill content and raw exception details.

Enforced prebind registers a caller-UoW post-commit failure only after audit and outbox
were written successfully. Those records and caller business writes commit atomically,
then the stable error is raised; if evidence recording fails, the entire transaction
rolls back. A same-key P7 marker triggers revalidation only while the attempt remains
claimable, so exact terminal replay does not freeze against current Skills again. The
promotion cache invalidator is wired independently of the frozen-view flags; uncertain
desired/no-op retries and successful `verify_rollback` hits also invalidate.

Scheduler handling distinguishes those two transaction outcomes. A post-commit
`ScheduledFireBindingUnavailable` carries the durable `PreparedFire`: interval loops stay
active with the committed next cursor, once consumes its initial root only once, run-now
does not alter the timer cursor, and every claimable attempt explicitly wakes the durable
run reconciler. A pre-commit audit/outbox failure rolls back fire, attempt, and cursor
writes, retains the initial root, and does not wake the reconciler, so the same fire can
recover. Successful retry records `skills_view_binding_recovered`; failure to record that
recovery also rolls back rather than leaving an in-memory-only recovery claim.

`SkillsWatcher` uses `PollingObserver` in every mode, including frozen wiring, because
native macOS FSEvents can abort the process during atomic Skill-tree exchanges. Frozen
wiring remains invalidate-plus-notify only, while the no-callback legacy path preserves
its debounced reload behavior; callbacks queued by a stopped watcher generation are
discarded.

These capture guarantees assume local POSIX semantics, ordinary writers, and reliable
ctime/stat changes. They do not claim resistance to privileged writers or filesystems
whose ctime is not a trustworthy mutation witness.

Each binding phase freezes at most once. A run without prebind freezes once; production
prebind plus dispatch deliberately performs one identity capture and one execution
rebuild because the phases may cross a process boundary. Reusing the same in-memory view
across those phases is not a P7 guarantee and would require the deferred P7b artifact.

Prebind boundary: P7 guarantees same-process mid-run stability. When an old persisted
SystemSnapshot cannot be reconstructed from current Skills bytes after prebind or
restart, shadow audits and continues live while enforce fails closed. The runtime must
not silently pair an old snapshot with a new view. Artifact-backed `skills_view`
persistence for durable old-content replay is deferred to P7b because it expands
retention, quota, secret-scanning, and rollback contracts. Persona, Prompt, Provider,
general PluginSet, and third-party PluginHost evolution remain outside P7.
