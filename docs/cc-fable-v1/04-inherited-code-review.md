# P1 继承实现复审报告

> 属性：只读勘察（D1 附加条件的落实）。评审对象：`main..7386cf3` 44 个提交
> （`src/tianshu` +17,252/−1,652）。G1.4b3 WIP（freeze 分支）不在范围（S0 自有
> 收口流程）；`docs/superpowers` +9,187 行历史计划文档不在范围。
> 评审时间：2026-07-12。深读模块：execution_gateway / auth / migrations +
> migration_ledger / workspace_service / git_backend / capabilities / adapters /
> models·workspace / workspace_repo；走查：executor.py、mcp transport、config、
> web、tests/architecture、prototypes 隔离。

## 总体判断

**可保留，需按排期局部重构（不需大改，不建议重做）。**

架构分层正确（models→storage→executor→gateway 单向、沿用既有 Mixin/router
模式）；三个权威唯一且有 AST 级测试守卫
（`tests/architecture/test_no_direct_process_launch.py` 把"ExecutionGateway 唯一
进程启动权威"变成可执行禁令）；安全默认真实修复（`config.py`
`0.0.0.0`→`127.0.0.1`）；能力声明诚实（claude_code/codex manifest 标
contained，无夸大）。主要债务是 4 个新文件体量严重超标（CLAUDE.md 800 行上限），
属"交付速度 vs 拆分纪律"的权衡，非架构错误。发现一个必须在 S0 处理的机制缺口
（与 STATUS 已披露的 V4 问题同源）。

## 分维度发现

### CRITICAL（S0 提交前必须处理）

1. `[CRITICAL]` `src/tianshu/storage/migrations.py:41,77,459` +
   `migration_ledger.py:338` — 迁移 checksum 仅由**规范化 SQL 语句文本**计算
   （如 `_AUTH_TOKEN_CHECKSUM = sha256("0002_auth_tokens\n"+SQL)`），ledger 只
   校验该值漂移；`validate`/`upgrade` **callback 的 Python 源码不入指纹**，改写
   callback 不触发任何告警——STATUS 披露的"V4 callback 被改写但 checksum 冻结"
   正是踩中此缺口。
   **处置**：并入 S0.2——裁决 V4 时同步补机制（callback 源码/AST 指纹纳入
   checksum，或 ledger 增加 callback 指纹列 + 回归测试锁定），否则 V5 之后
   同类改写仍会无声通过。

### IMPORTANT（重构候选，排期见清单）

2. `src/tianshu/executor/execution_gateway.py`（2,495 行，≥3×上限）— 单文件混
   6 个职责群：grant/policy 模型、context 绑定、8 种 grant 签发、请求/回执模型、
   进程后端+流处理+进程树终止、ExecutionGateway 本体；`_validate_built_in_guards`
   331 行、`start()` 155 行 — 拆 grants.py / policy_models.py /
   process_backend.py / gateway.py；S1/S2 都要改此文件，越晚拆越贵。
3. `src/tianshu/storage/migrations.py`（1,834 行）— ~600 行 schema 签名/比对
   基础设施与迁移定义、~400 行历史数据适配混在一文件 — V5 冻结后抽
   `schema_signature.py`（纯移动、字节级保真；checksum 只算 SQL 文本，不受
   移动影响）；S0 期间禁动。
4. `src/tianshu/executor/git_backend.py`（1,657 行；`_invoke` 220 行）— 单类
   ~40 方法，高内聚但超标 2 倍 — 可按 snapshot/staging/worktree-admin 三组拆。
5. `src/tianshu/executor/executor.py` `execute_edict` 275 行（既有债务 +
   G1.4b2 governed 接线加深）— S3.8 durable continuation 必然重写主循环，
   届时拆，不单独排期。
6. `src/tianshu/gateway/auth.py`（797 行，贴上限）— AuthService 与
   SecurityBoundaryMiddleware 双职责一文件 — S2.2 触碰 auth 面时拆
   `security_middleware.py`。
7. `src/tianshu/executor/workspace_service.py` — `_create_lease` 146 行、
   `close_lease` 95 行、`_capture_change_set` 87 行超函数上限，且 G1.4b3 WIP
   还在向该文件加码 — S0 收口 Commit A 前检查合并后总行数，超 800 则先按阶段
   提取私有方法（行为零改动），或在台账记录显式豁免。

### MINOR（记录，不排期）

8. `web/src` 当前无 `prototypes`/`mockData` 引用（已验证隔离），但 ui/README
   要求的**静态守卫 Gate 尚不存在**，现状靠自觉 — S4.1 落实。
9. `src/tianshu/executor/capabilities.py:178` `resolve_governance_contract`
   94 行 — 可读性尚可，顺路时拆。
10. `migration_ledger.py` 的 MigrationConnection 禁 `commit/rollback/
    executescript`（NoReturn）设计成熟；`_expected_ledger_tokens` 自校验与
    schema.py 存在双份 ledger DDL 知识 — 无行为风险。

## 重构候选清单

| 候选 | 价值 | 成本 | 建议排期 |
|---|---|---|---|
| ledger 补 callback 指纹 | 高（堵住已被踩中的缺口） | 低 | **并入 S0.2** |
| workspace_service 长函数提取 | 中 | 低 | S0 Commit A 前视合并后体量 |
| execution_gateway.py 四拆 | 高（S1/S2/S3 都要改它；guard 331 行难审） | 中（纯移动+import 修正，测试面全在） | **S0 后、S1 前独立切片（记为 P1.R1）** |
| migrations.py 抽签名基础设施 | 中高（V5 后迁移文件继续增长） | 低（纯移动） | S1 |
| auth.py 拆 middleware | 中 | 低 | S2.2 顺路 |
| git_backend.py 三拆 | 中 | 中 | S3 前顺路 |
| executor.execute_edict 拆 | 高 | 高（牵主循环） | 并入 S3.8 |

## 明确不需要动的部分（后续阶段不再重复怀疑）

- **迁移机制主体**：migration_ledger.py 的受限连接、事务纪律、drift 检测；
  MIGRATIONS v1–v4 的 SQL+checksum 结构——成熟，仅补 callback 指纹。
- **auth 安全设计**：高熵 token（ULID+`secrets.token_urlsafe(32)`）、SHA-256
  无盐存储（对高熵 token 是业界标准）、refresh family 重放撤销、默认 loopback
  绑定、host/origin/scope 中间件语义。
- **capabilities/adapters 边界**：manifest 诚实分级、adapters/protocol.py
  （174 行）干净 Protocol、MandatoryCapabilityMismatch fail-closed。
- **models/workspace.py 值对象**（canonical_json/content_hash/排序键）与
  workspace_repo 的 Mixin 归位——与既有 storage 模式一致。
- **tests/architecture AST 进程启动禁令、tests/security（3,929 行）、
  tests/compat 守卫面**——这批代码最值钱的部分。
- **web/ 改动**：auth 全套（AuthContext/Provider/LoginGate/authFetch+测试）、
  terminology/palette 校准，走查无问题；prototypes/ 与生产隔离已验证。
- **mcp/transport.py 接线**：stdio 必须持 ExecutionGateway grant 才能 spawn，
  缺 gateway 直接 TypeError fail-closed，方向正确。

## 对主计划的影响

1. S0.2 增补两项：V4 裁决时**同步补 callback 指纹机制**（CRITICAL #1）；
   Commit A 前检查 workspace_service 合并后体量（IMPORTANT #7）。
2. S0 与 S1 之间插入独立重构切片 **P1.R1：execution_gateway.py 四拆**
   （纯移动，focused suite + 静态门禁验证字节级等价）。
3. 其余候选按清单挂靠既有阶段顺路处理，不新增阶段。
