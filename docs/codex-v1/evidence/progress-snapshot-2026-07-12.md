> **历史快照警告：** 本文件前 174 行属于旧 `feat_phase8` 工程治理 G1–G7，
> 其中的 `G2/G3/G4/G5 complete` 与本次 Agent OS Gate 无关。Agent OS 台账从
> `=== Agent OS G0-G5 连续实施 (2026-07-11) ===` 开始。状态恢复以根目录
> `STATUS.md` 和真实 git/test 证据为准。

# SDD Progress Ledger — 2026-07-03 全项目重构

Plan: docs/plan/2026-07-03-project-review-and-refactor.md
Branch: feat_phase8

Task B1-T1: complete (commit 23e6f20, review clean/Approved)
  Minor 备忘: (1) 无"异常穿透 to_thread"专项测试; (2) propose_code_variant 无并发互斥锁(改造后 gate/evaluate 可真并行,已确认无共享态,如需限流后续加)
Task B1-T2: complete (commit 724ecb9, review clean/Approved, +2 tests → 1273)
Task B1-T3: complete (commit 9ccf7d3, review Approved)
  Minor 备忘: setSearchParams({tab}) 是整体替换非 merge——若未来 tab 页加 query 筛选参数会被切 tab 冲掉
Task B1-T4: complete (commits bb27119+65d38ed, review Approved; controller 收尾 4779a5f 清理 ops.* 死键与未用变量)
  用户须知: archive_old_iterations 已接线 digest 每日循环——首次运行会一次性归档 30 天前历史积压 iteration(清 actor_output 保摘要)
Task B1-T5: complete (commit 34790cf, review Approved)
  Minor 备忘: 同一损坏行被高频轮询读取会重复刷 warning,如实际刷屏再加限流
Task B1-T6: complete (commit ada968c, review Approved)
  Minor 备忘: (1) api.py debug 日志移到 save_edict 之前(仅 DEBUG 级,无实际影响); (2) edict_bridge 异常路径下 anchor.set 会被跳过(比旧行为更保守,非回归)
  既存问题备忘: 单独跑 tests/gateway/+tests/tools/ 子集 3 个 test_network_safety 用例失败(测试隔离问题,全量绿)——留批次4测试补盲处理
Task B1-T7: complete (commits 4d4e719+b56abe5 收尾, review Approved; b56abe5 后全量复测 1273 passed)
  裁决备忘: docs/plan/phase-2-platform.md:123 裸 protocol.py 引用 → 豁免(Phase 2 历史规划记录,与已豁免方案文档同类)
Task B1-T8: complete (commits 62c3c57 lint + c2c80d9 format + 3d88f72 blame-ignore [rebase 后 hash,去 attribution 尾注] + 397601a 收尾 noqa 漂移, review Approved)
  Important 备忘: T8 实施报告对 api.py Literal bug 成因有虚构叙述("漏引号"不实,真实仅缺 import);代码正确,审查员已独立复算全部数字(558→420→143→34)吻合
  ruff 附带修复 3 个既存真 bug: api.py Literal 缺 import / auditor.py 缺 Memorial import / worker.py 裸 asyncio 名 NameError 风险
  遗留: UP042(StrEnum,11)/ASYNC240,221,109(34 处)进 ignore 待批次4专项; conftest E402 理由表述过强(无实际问题)

=== 批次 1 完成 (2026-07-03) — 14 commits ===
收尾验证: 后端 1273 passed / ruff check 干净 / format 零 diff / 前端 tsc+build 通过
批次 2 开始: kernel 契约层 → api.py 拆分 → storage 拆分 → bootstrap 拆分
Task B2-T1: complete (commit 88fbede, review Approved; 迁移文件字节级零改动,4条上向循环+models→dag反向全部清零)
  Minor 备忘: kernel/__init__ 未 re-export HOOK_TIMEOUT/HOOK_TIMEOUTS(无消费者,内部常量); shim 下批删
B2-T2 批A: complete (commit 91891ee, review Approved——35 路由字节级保真验证通过)
  Minor 备忘: feishu/edict_bridge.py:50 docstring 提到的 gateway.api._build_history 已迁 _helpers(历史注释过时,后续批清理)
B2-T2 批B: complete (commit cb384b1, 实际 43 路由[brief 计数误差 1 条,agent 已按整域搬空核实], 路由零 diff + 1273 绿)
B2-T2 批C: complete (commit 12491e8, 39 路由零 diff + 1273 绿)
B2-T2 批D: complete (commit 807e872, api.py 3513→67 行, 路由零 diff + 1273 绿; /approvals/tool_decision 并入 execution_api[领域判断,合理]; system-prompt 实为 6 条)
B2-T2 批B/C/D 合并审查: Approved (130/130 符号字节级零差异, 112 路由装饰器全量核对, 12/12 注册顺序一致)
  审查发现既存影子路由 bug → controller 实测坐实并修复 (commit 见上): /memory/stats、/memory/policies 曾被 {persona_id} 吞掉,handler 从未可达
B2-T2 批E: complete (commit fcf8871 def化102条 + controller 修复 commit 见上: 5条纯内存路由 revert async)
  审查发现 Critical(已修): submit_outer_loop_decision def化后 Event.set() 跨线程唤醒失效(隔离复现证实,零测试覆盖路径)
  Minor 备忘: 修复即 revert 5 条(decide/outer-loop pending/pending_tool_calls/workers/workers status)——纯内存路由 def 化无收益
  批E最终净效果: 97 条纯 DB 路由 def 化回归线程池, event loop 不再被 DB 查询阻塞
=== B2-T2 完成: api.py 3513→67 行, 14 个域 router, URL 零漂移, 顺带修复影子路由+异步安全化 ===
B2-T3 批A: complete (commit c798c22, storage.py→storage/ 包 + mappers/schema/migrations 抽出, SQL 字节级校验通过, API 168 成员零变化)
B2-T3 批B: complete (commit 9f23818, AST 脚本搬运 169 方法字节级保真, facade 3168→1761 行; 首次派发卡死后重派成功)
  归簇备忘: set_memorial_feedback/universe_memorial_stats 暂归 Memorial(可再议); last_activity_at 留 facade(候选 Event); list_memorials_by_persona 留 facade(跨表 JOIN)
B2-T3 批C: complete (commit f2aa3bc, facade 1761→174 行, 8 域 Mixin + 归位调整; 留守 3 方法均真跨域 JOIN)
=== B2-T3 storage 拆分完成: 4126 行单类 → _base+15 Mixin+mappers/schema/migrations, API 168 成员零变化 ===
B2-T3 合并审查: Approved (183 符号零丢失/零改动/零重复, SQL 字节级一致, MRO 120 对交集全空, 外部契约实测通过, 159 方法归簇零真实错误)
  controller 收尾 (commit 见上): 删 facade 孤儿 logger + last_activity_at 归位 EventMixin + 3 文件格式漂移
  遗留: docs/impl/storage/README.md 仍描述单文件结构(批次4文档同步)
Task B2-T4: complete (commits 9121de7 冒烟测试 + ebc35d5 拆分 + controller 收尾: 惰性导入边界恢复)
  审查 Approved: 130 装配事件零差异, 50 state 键零遗漏, 闭包 __qualname__ 坑已规避
  Important(已修): 11 处惰性 import 被 eager 化——channel 3 类回归条件导入, MCPManager 回归函数内(其余 8 处无害保留)
  Minor 备忘: _personas_dir() 在 persona/memory 两 wiring 重复定义; 渠道已配置分支无冒烟覆盖(既有缺口)

=== 批次 2 完成 (2026-07-03) ===
成果: kernel 契约层(4+1 循环依赖清零) / api.py 3513→67 行 14 域 router / storage 4126→174 行 facade+15 Mixin / app.py 869→131 行 bootstrap 包
附带修复: 影子路由 /memory/stats·policies 不可达 / def 化 97 条纯 DB 路由消除 event loop 阻塞 / 5 条内存路由跨线程风险拦截
收尾验证: 后端 1324 passed / ruff 双检干净 / 前端 tsc+build 通过
批次 3 开始: 双通道 gateway/core 抽象 → 前端御书房合并+菜单分组+SystemManagement 拆分
B3-T1 批A: complete (commit 22484c9, core 骨架+mode_router 合一+budget 抽取, 行数持平[骨架开销,预期内], 1324 绿)
B3-T1 批B: complete (commit b14573e, approval/batcher/outbound 事件层抽取, outbound 5 处真实差异以钩子保留, 净增 34 行, 1324 绿)
  范围重估: 批A/B 行数持平→抽象价值在单一来源而非净删; 批C 只做 branches, facade 骨架砍掉(app_lock 语义两端本质不同[app_id vs token_hash], 抽象收益<复杂度)——记入"不做清单"
  产品确认项: 飞书分片拼接 strip vs telegram 不 strip 的既有不一致,保留待产品定夺
B3-T1 批C: complete (commit d955ff4, 16 命令上移+2 钩子化, format_status_label/EdictBusyError 迁 core, 净减 271 行, 1324 绿 + telegram 冒烟)
B3-T1 合并审查: 行为等价全成立(8/8 钩子, 命令表零丢失, 3 个独立核验 agent 交叉验证), Important 循环 import 已修复 (commit eadd142: markdown_compat/approval 解析迁 core, 3/3 干净导入)
  Minor 备忘: budget 日志失通道粒度(无依赖); _default_instance_id 无 __init_subclass__ 校验(加第三通道时注意); telegram 裸奔面=多分片下发+budget card 真实 SQL(批次4测试补盲候选)
=== B3-T1 完成: gateway/core 9 模块, 双通道命令逻辑单一来源, telegram 跨包引用清零 ===
B3-T2: complete (commits 386df71 批量端点 + e6d8859 御书房合并+菜单4组, 1328 绿)
B3-T2 审查: Approved (功能保真逐行比对通过, 影子路由同型风险排除, i18n 三套核对) + controller 收尾 1ccb782 (隐藏 Tab 轮询门控)
  Minor 备忘: docs/impl/README.md:139 等 3 处 ApprovalQueuePage 陈旧引用(批次4文档同步); 浏览器走查留待用户验证
B3-T3: complete (commits 686a319+363058b, SystemManagement 2044→75 行 7 Tab 抽出, EdictForm 869→464 行, i18n key 零丢失)
B3-T3 审查: Approved (7 Tab 逐字节 MATCH, Section 去缩进零差异, i18n 零丢失独立复核)

=== 批次 3 完成 (2026-07-03) ===
成果: gateway/core 双通道抽象(命令单一来源,净减~240行) / 御书房合并(双Tab+批量端点消N+1) / 菜单 15→4组13项 / SystemManagement 2044→75 行 / EdictForm 869→464 行
批次 4 开始: 巨型函数拆分 → 工程化收尾(mypy/coverage/extras) → 测试补盲 → 文档同步
B4-T1: complete (commits 714cf28 测试锚点 + b131fbf 拆分, run 386→87 / execute 540→166, 1329 绿)
B4-T1 审查: Approved (26 退出点三方收敛核对, 9 个未动函数字节级一致, 状态穿线含 repeated_failures 陷阱全验证)
  Important→并入 B4-T3: agent.execute 高风险路径(overflow/fallback/熔断/hook拦截/流式取消)无常设回归测试
  Minor 备忘: _check_pause "edict被删"分支无测试覆盖(B4-T3 候选); _run_checks_phase 返回类型标注 Optional 窄化(mypy 上线时会现形)
B4-T2: complete (commits d08d59a mypy + 69368cd coverage + 8c2f071 extras, 覆盖率基线 63%[规则宣称80%,差距17pp], 1329 绿)
  遗留耦合: telegram/__init__ 模块级复用 feishu 的 EdictBridge/PersonaRenderer→单装 telegram extra 仍级联要 lark_oapi;根治=EdictBridge 迁 core(后续)
B4-T2 审查: Approved (mypy/coverage/extras 逐项独立复核, 黑名单验证复现)
  Minor 备忘: (1) 部分安装场景 bot_manager 实例发现循环被 feishu 缺依赖异常中断——与 EdictBridge 迁 core 一并处理(后续); (2) typer[all] extra 已被新版 typer 移除,装 tianshu[all] 有警告(后续把 typer[all]→typer)
B4-T3: complete (commits 36bde3b+6043d33+caccc19, +35 用例→1364 绿, marker 警告 129→0, 红绿演示×2, 生产代码零改动)
B4-T3 审查: Approved (断言深核: recovery_attempts 精确断言/熔断最小复现/多分片真驱动/budget 真 SQL; fake 签名保真; -W error 模式零 warning)
B4-T4: complete (commit 22d605c 19 文件 + eb0ec39 审查补漏 + 9d0ac51 format 收口)

=== 批次 4 完成 / 全部四批次收官 (2026-07-03) ===
最终验证五关: 1364 passed / ruff check+format 双净 / mypy 21 文件零错 / 路由快照仅设计内新增1条 / 前端 tsc+build 通过
总计 51 commits, 444 文件, +22417/-17409 行

=== 位面竞争力优化 (2026-07-04, plan: docs/superpowers/plans/2026-07-04-universe-competitiveness.md, 起点 commit 见下一行) ===
起点: 计划入库 commit $(git rev-parse --short HEAD)
批次1 修真: T1 探索退役+fitness防线 / T2 沙箱extra_env / T3 预算闸 / T4 配对基线 / T5 行为层评估
批次2 补脑: T6 演化记忆 / T7 诊断器 / T8 自主提案
批次3 加固: T9 分层选集 / T10 凭证隔离 / T11 前端信任面 / T12 文档
T1: complete (commit c92f08c, 探索退役+fitness防线, 1361 绿, review Approved 含独立红黑复现)
  Minor 备忘: test_routing 仅锁定默认配置路径(explore 配置已删无法开启), docstring "无论开关"表述略过界——T12 文档时顺带校准; docs/ 下 explore_ratio 残留 ~13 处归 T12
T2: complete (commit 0676445, 沙箱 extra_env+围栏不可覆盖, 172 绿, review Approved)
  Minor 备忘: 围栏覆盖测试仅验 EVAL_MODE 一字段(brief 如此), DB_PATH/PORT/HOST 同机制未单测——T5 dispatch 顺带补 3 断言
T3: complete (commit 2e7c2d8, 预算闸 20元/次默认, 174 绿, review Approved)
  Minor 备忘: (1) truncated 键随 fitness 流入 universes.fitness_json——窄取安全无害,T11 正依赖它展示,T12 文档注明; (2) types.ts fitness 类型 Record<string,number> 失真——T11 修类型; (3) evolver truncated 合并分支无测试——T4 重写该段时 fake 补 truncated=True 用例
T4 审查: Issues 2 Important(裸dict缓存路径零覆盖/指纹不含主干HEAD,日常commit静默复用陈旧基线) + 1 Minor(截断基线入缓存) → fix 派出
  连带: fix 引入 evolver._baseline_key()——T5 dispatch 必须指示 _evaluate_behavior_challenger 用它替代 brief 里的 champion_id 裸拼
T4: complete (commits 8fbc102+4743fac fix, 配对基线+指纹含HEAD+截断基线不入缓存, 1374 绿, re-review Approved 含变异测试)
  Minor 备忘: propose→fingerprint 的 champion_key 接线未被测试锁定(变异测试发现, fake 忽略该参)——T5 顺带补断言
T5: complete (commit f25dc36, 行为层配对评估+delta三路分流+_maybe_promote退役+3条顺带补强, 1377 绿, review Approved)
  Minor 备忘: delta 恰好±margin/samples不足留观/评估异常路径 三处无专项用例(逻辑走读正确)——final review 触发则补
  已知语义: run() 落地变异后同步跑沙箱评估,manual /evolve API 分钟级阻塞(计划风险1已披露)
=== 批次 1 修真 收官: T1-T5 全过审, 探索退役/extra_env/预算闸/配对基线/行为层评估 ===
T6: complete (commit ac57538, 演化记忆进 prompt, 1379 绿, review Approved 含 format 手动渲染验证)
T7: complete (commits ad5f2c9+1225486 fix, 太医诊断器, 1385+ 绿, re-review Approved 含变异核验)
  实录: brief 两处假设与真实不符(list_memorials 返回 tuple / Memorial.audit 非 audit_json)——实施者正确适配, fix 轮补齐 fake 签名对齐与主路径覆盖
T8: complete (commits c1c7a9f+ef0b0c5 补测, 自主提案闭环 cron05:30+API+配额, 201 universe/1388+ 全量绿, re-review Approved)
  已知边界: 双锁 key 跨轨道(run/auto_propose)无节流(cron 错开 30min, 手动并发失败安全); 配额无上限护栏(brief 原样)
=== 批次 2 补脑 收官: T6-T8 全过审, 演化记忆/诊断器/自主提案 ===
T9 控制者拦截(Critical, 审查前): 实施者(haiku)为满足 brief 往 EdictStatus 加 FAILED 枚举——但 edict 无 failed 生命周期(失败挂 memorial 层), list_edicts(status=failed) 生产恒空=伪功能+契约膨胀; 根因是计划混淆 edict/memorial 状态空间(计划缺陷) → fix 改从 memorial 层采样(参照 diagnostician 路数)
T9: complete (commits 4053513+8289626 fix, 分层混采—失败层从 memorial 采样+守门用例, 1396 绿, review Approved 含变异验证)
  Minor 备忘: 反向回填分支(成功稀缺←失败补)无仓库用例(审查员脚本验证过); _collect_* 无 try/except 兜底(与 diagnostician 风格不一, 最小改动权衡)
T10: complete (commit 1778618, TIANSHU_EVAL_LLM_* 凭证隔离, 206 绿, review Approved 含生效链全程走查)
  ⚠️ 边界(T12 文档注明): 未来 evaluate 传 seed_db 且其含 llm_configs 行时 DB-first 会盖过 env eval key, 隔离静默失效(当前无调用点触发)
T11: complete (commits f419fda+48521e2 fix, 谱系树+审批Modal+自主提案按钮+孤儿伪根+竞态守卫, tsc/build 双过, re-review Approved)
  实录: 2 Important 均为真问题(孤儿子树消失—生产可复现; 审批视图竞态—信任关口放大), 修复经时序推演核验
T12: complete (commits a5c8e4d+8a06fa4+控制者一行校准, 6+2 文档同步+矛盾句消除, grep 零残留, re-review Approved)
=== 批次 3 加固信任 收官: T9-T12 全过审 ===
全部 12 任务完成, 进入 final whole-branch review + 收官验证
仍开 Minor(交 final review triage): T5 三处用例缺口(±margin边界/samples不足留观/评估异常路径); T9 反向回填无仓库用例+_collect_*无兜底
收官验证四关: 1397 passed(feishu webhook 1条环境flaky复跑即绿,与本轮零关联) / ruff check+format 双净 / 路由快照仅设计内新增 POST /api/universes/propose-auto / 前端 tsc 零错+build 过
终审修复: complete (commit bfffc8e, iso_db uuid唯一化+行为层truncated对齐+docstring校准, re-review READY)
=== 位面竞争力优化 收官 (2026-07-04): 12 任务全过审, 20 commits (b2cf7f4..bfffc8e+执行记录), 终审 READY ===
收官: 1399 passed / ruff 双净 / 路由+1设计内 / tsc+build 过; 执行记录 docs/plan/2026-07-04-universe-execution-log.md

=== 工程治理三批次 (2026-07-04, plan: docs/superpowers/plans/2026-07-04-engineering-governance.md) ===
特殊流程: 用户要求全程不 commit——改动留工作区, 任务边界用 git write-tree 快照(gov-tree-N 记录于本台账), 审查包=git diff <tree_N> <tree_N+1>, 全部完成用户审批后统一提交
批A: T1 仓库卫生 / T2 DrawerStore+tests泄漏; 批B: T3 EdictBridge迁core / T4 import-linter; 批C: T5 mypy五包 / T6 personas_api+memory_repo补测 / T7 profile_synthesizer补测
gov-tree-0: 045eb052f3e6a9d2738e03c9ec2e981899c080f3
gov-tree-1: 910be5ad349b1f5b41e3200a013b652e24d6cda5
G1: complete (未提交, 增量=gov-task-1.diff, untrack×5+README端口+typer, ruff check . 全绿, review Approved)
gov-tree-2: c43ce9d7ad404e00323bd2ab153fd96a9218a34a
G2: complete (未提交, 增量=gov-task-2.diff, DrawerStore接shutdown+19文件泄漏清理, sqlite ResourceWarning 113→0, 1400 绿, review Approved 含逐hunk语义核验)
=== 批 A 卫生与资源 收官 ===
gov-tree-3: 33b319a76b31ef88590d8084d2068f2c9b951eb6
G3: complete (未提交, 增量=gov-task-3.diff, 三件套迁core+telegram直连core.approval+隔离测试(find_spec修正版,计划的find_module在Py3.14失效), 1401 绿, review Approved 含独立阻断验证)
  实录: 计划隔离测试用了过时MetaPathFinder API——实施者发现恒绿假通过并修正, 审查员独立复现
gov-tree-4: 4a413b00584253694121977823437512c660a1e4
G4: complete (未提交, 增量=gov-task-4.diff, 三层契约(:分隔)+2豁免均核真, lint-imports 1 kept, review Approved)
  实录: 计划 layers 用 | 分隔与 import-linter 语义相反(同层互斥)——实施者改 : 并实证 68→2, 审查员源码级验证; 本轮计划已两次工具 API 记错(find_module/分隔符), 值得沉淀
  ⚠️ 契约覆盖 14 包非穷尽(tools/providers/web 未纳入)——后续评估
=== 批 B 边界收紧 收官 ===
gov-tree-5: da0cb70bd903a48c0510a4d55188ffc3f29492c3 (含控制者补注)
G5: complete (未提交, 增量=gov-task-5.diff+补注, mypy 10包85文件 Success, 23修复+6SKIP, 1401 绿, review Approved)
  🔴 真缺口(审查独立核实, 交付用户决策): profile_synthesizer gather(return_exceptions=True) 结果用 isinstance(x, Exception) 判定, 单个子任务被 cancel 时 CancelledError(BaseException)漏判→TypeError; 修法=isinstance(x, list) 正向判定; 本轮按纪律未改行为
gov-tree-6: 208dd3bf24644f8d9752b0a3011ae1f835ffee55
G6: complete (未提交, 增量=gov-task-6.diff, 15用例(API 11+repo 4), 1416 绿, review Approved)
  发现: (a) docs/impl/README.md:145 路由表两处失实(GET /personas/{id} 不存在,前端实为列表+find; prompt_preview 归属 system_api)——上轮 B4-T4 残留, 收尾控制者顺手修; (b) personas 表默认空=生产真实行为(内建的是 departments), 无需动作
gov-tree-7: cdc856b61611f99816091e9f40a25761e0eea46e
G7: complete (未提交, 增量=gov-task-7.diff, 6用例(聚合×3/conflict双向/LLM降级/e2e persist), 1422 绿, review Approved 含 fake 面与真实消费清单比对)
  实录: detect_conflict 真实语义=auto段diff ratio(计划"人工段"描述有误), 按真实语义锁定
控制者收尾: docs/impl/README.md persona 路由表行修正(G6 发现的上轮 B4-T4 残留)
=== 批 C 类型与覆盖 收官: 7/7 任务全过审 ===
gov-tree-final: 5962cb6fa9a592f5abd6a07cdb0291320ae0b3fd
最终审查(opus): READY——行为零改动全量核验成立, 门禁全绿; Important 建议 forbidden 契约已由控制者补齐(lint-imports 2 kept), Nit(redact.py 既有死三元)不动
gov-tree-final: cf1c25eda8c5c6865324450d35f0822d15b0a48c (含 forbidden 契约)
=== 工程治理收官: 7/7 全过审+终审 READY, 全部改动未提交待用户审批 ===
追加(用户验收期): scripts/local.sh 优化——VITE_PORT 3000→7999(G1漏网)/lsof 筛 LISTEN 防误杀(实证 vite proxy 连接会被旧逻辑误杀)/venv bin 显式解析(防 myenv 坑)/优雅停止 3s→10s 先TERM父进程(保 lifespan shutdown 跑完)/start 端口预检+健康等待/prod 静态目录校验
gov-tree-final: 09b41b1bd3a86bc5588bc3aea9d87810e2e44e90 (含 local.sh 优化)
用户审批通过, 按文件不跨组分 4 commit 提交: 4c61cc6 卫生+脚本 / 5d197ef 资源生命周期 / 6c0a87e gateway迁移 / 3899aa0 类型+契约+补测
=== 工程治理三批次 正式收官 (提交完成, 工作区干净, ruff./lint-imports/mypy/1422 全绿) ===

=== G0 本地启动兼容修复 (2026-07-11, plan: docs/superpowers/plans/2026-07-11-fix-local-startup-migration.md) ===
Task 1: complete (未提交, historical core + legacy session/supervision 精确迁移适配, 92 tests, review PASS/APPROVED)
Task 2: complete (未提交, local.sh fail-fast + ownership-safe cleanup, 6 tests, review PASS/APPROVED)

=== Agent OS G0-G5 连续实施 (2026-07-11) ===
授权: 用户批准连续完成 G0-G5，Gate 间不再等待人工审批；每个 Gate 仍须自动验收，最终统一交付用户验证。
工作区: feat_codex_phase_1（普通 checkout，保留未提交 G0 变更，原地继续以避免丢失）
G0: complete (未提交；审批原型、术语/能力矩阵、迁移/备份、启动与双飞书实例竞态已验证；后端 1848 passed，Web 25 passed，原型 13 passed)
G1: in progress (先生成 phase-specific TDD plan，再按任务实施与审查)

G1.1: complete (commits 9fe77cc, 377b505, 1d6f783; two security review loops clean; final gateway 442 passed and task-specific Web/CLI/storage gates green)
G1.2: complete (commits 2e76851, 14eee97, 902f129; task review Spec PASS / Quality APPROVED; final 144 focused passed; Effective v1 N-1 canonical fixture preserved)
G1.3a: complete (commits 949a29e, c79188a, b3eabf1; task review Spec PASS / Quality APPROVED; 145 focused passed; Windows process-tree cleanup remains unproven)
G1.3b1: complete (commits d17e665, 6e79c93, 63aefd3, ef37f16; MCP stdio fully routed through Gateway; 3 review loops closed startup receipts, initialize shutdown, bounded stream cleanup, reload, and overlapping shutdown races; final Spec PASS / Quality APPROVED; 179 focused passed)
G1.3b2: complete (commits fc4ad41, b3b48d0, 4e4d6c4; Universe gate/sandbox/eval through Gateway; secure-remote fail closed, trusted-local fallback explicit/audited; two review loops closed descendant cleanup, retryable DB/WAL/SHM cleanup, start/shutdown races, full grant and secret binding, async polling, denial/cancellation receipts, post-spawn ownership handoff, and documentation truth; final Spec PASS / Quality APPROVED; root broad regression 463 passed before final fix, final focused 177 passed and independent exact-commit re-review 69 passed)
G1.3b3: complete (commits 56dc146, 6100eb1; remaining fixed callers + named GitBackend + exact AST inventory gate + governed grep/LSP adapters; prior 2 Critical/5 Important/1 Minor all closed; final exact re-review Spec PASS / Quality APPROVED, 27 fresh exact regressions; full branch baseline 2239 passed via 2820355)
G1.4a: complete (commits 3b8fe9a, d727a20, deaede4; immutable v4 lease/restore/change/apply foundation, detached staging, identity hardening and chronological canonical captures; independent exact review Spec PASS / Quality APPROVED; 112 focused + 1 platform skip, migration/backup 91 passed)
G1.4b1: complete (commits 3b02adb, 5ff182c, d24a0f0, 137a0b2, 4b4f614, ef37d0a; real ContextVar lease/root binding, staging tools, skill COW and all identifier/symlink escape findings closed; final exact review C/I/M=0/0/0; full not-slow 2521 passed, 1 skipped, 1 deselected at 4b4f614)
G1.4b2: complete (commits 16257fb, 2e46be7, e492a1f, 2e0af4a, 302cba3; Executor/outer/DAG/Keqing lease lifecycle, atomic retry, terminal cancellation, truthful preview/probe and root overlap fail-fast; four review loops closed all C/I findings; final focused gates 139/124/57 passed plus quality gates)
G1.4b3: in progress (governed apply service/domain and REST/Auth/CLI/capability surface split into independent TDD batches)

=== Rebaselined Agent OS execution (2026-07-12, plan 1b51bcd9) ===
S0.1: complete (read-only freeze; base 1b51bcd9; 17 tracked modifications + 8 untracked source/test files; tracked +2607/-115; additive V5 confirmed; app/CLI registration present; no production placeholder found; git diff --check clean)

G0 checkpoint (2026-07-11):
- edf638a feat: establish Agent OS G0 product baseline
- 20c4213 feat: add safe versioned storage migrations
- 673fcef fix: harden local startup and async process handling
- 13fbdc7 chore: sync dependency lockfile
- fresh verification: backend 1848 passed / 1 deselected; Web 25 passed + build/typecheck; prototype 13 passed + build; ruff/mypy/import-linter all pass.
- known baseline warnings: Web ESLint 38 warnings; Vite large-chunk warning; pytest 146 third-party/async warnings. These are explicit G3/engineering follow-ups, not reported as clean.
