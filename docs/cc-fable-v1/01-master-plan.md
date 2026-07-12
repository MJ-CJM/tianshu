# CC-Fable v1 主执行计划（P0 + S0–S6）

> 状态：`user_approval_pending`。本文档拥有**执行顺序、阶段入口/出口与审批边界**；
> 切片的技术细节一律以 `codex-v1` 对应 brief/recon/phase plan 为准，不在此复制。
> 范围模式（完整 / 核心优先）由 [02 号文档](./02-decisions-for-approval.md) D4 裁决；
> 本文按完整模式书写，核心优先模式的裁剪边界在各阶段内注明。

## 1. 阶段总览

| 阶段 | 内容 | 当前状态 | 规模参考 | 出口审批 |
|---|---|---|---|---|
| **P0** | 资产回收与基线重建 | `in_progress`（2026-07-12 批准） | 0 行新代码，纯 git/环境操作 | 出口条件自查 + 台账证据 |
| **P1** | 继承实现复审（只读，D1 附加条件） | `planned`，P0 后启动，可与 S0 并行 | 评审 44 个提交（约 2 万行 net），产出报告 | 报告入 `04-inherited-code-review.md`；Critical 前置 S0 |
| **S0** | G1.4b3 governed apply 收口 | 迁移后 `in_progress`（WIP 已存在） | 约 7,400 行 WIP 收口为 2 个提交 | 单次 full Gate + G1.4b3 报告 |
| **S1** | G1.5 wheel / 离线 Demo / Doctor | `planned` | 5 切片 | fresh HOME 黑盒完成受治理 Demo |
| **S2** | G1.6 审计 / MCP 安全 / 发布基线 | `planned` | 7 切片 | G1 Developer Preview Gate |
| **S3** | G2 durable governance 与证据 | `blocked_by_upstream`（等完整 G1 handoff） | 13 切片 | 故障矩阵 + G2 Gate |
| **S4** | G3 正式桌面 Web | `blocked_by_upstream`（等 G2 API） | 12 切片 | automation_passed + 用户终审 |
| **S5** | G4 受控演化与执行器中立 | `blocked_by_upstream` | 10 切片 | G4-A/B/C Gate（外部项可 `external_pending`） |
| **S6** | G5 开源发布候选 | `blocked_by_upstream` | 9 切片 | release candidate；实际发布另行授权 |

```mermaid
flowchart LR
    P0["P0 资产回收"] --> S0["S0 G1.4b3 收口"]
    S0 --> S1["S1 G1.5"] --> S2["S2 G1.6"] --> S3["S3 G2"]
    S3 --> S4["S4 G3"] --> S5["S5 G4"] --> S6["S6 G5"]
    S6 --> U["用户最终发布审批"]
    S3 -. "G2 契约冻结后<br/>壳层/状态组件可受控并行" .-> S4
```

## 2. P0 —— 资产回收（新增阶段，codex-v1 没有）

完整方案见 [00-baseline-and-recovery.md](./00-baseline-and-recovery.md)。
一句话：冻结源 clone 的 WIP → bundle/push 备份 → 纯快进迁移 44 个提交到当前
分支 → 重建 venv → 干净基线全绿 → 还原 WIP → 封存源 clone。

**入口**：本包获批。**出口**：00 号文档第 5 节六项条件全满足。

## 2b. P1 —— 继承实现复审（D1 附加条件，只读）

用户批准全量迁移时附加要求："也要看下是否合理，以及是否需要重构"。落实为：

- **对象**：`main..7386cf3` 的 44 个提交（G0–G1.4b2 已审查实现，约 2 万行 net）；
  WIP 部分不在此评审（S0 本身即为其收口与审查流程）。
- **方式**：只读勘察（沿用 codex recon 模式），不改代码。评审维度：
  ① 架构分层与边界是否合理（gateway/executor/storage/models）；
  ② 与既有代码风格/模式的一致性；③ 过度设计或欠设计；④ 安全边界实现质量；
  ⑤ 重构候选清单（按价值/成本分级）。
- **产出**：[04-inherited-code-review.md](./04-inherited-code-review.md)。
- **分流规则**：Critical 发现 → 前置为 S0 修复项；Important 重构建议 → 排入
  对应后续阶段顺路处理或独立小切片；Minor → 记录不排期。
- **时机**：P0 完成后启动，可与 S0 并行（只读与实现并行符合既有纪律）；
  S0 的 Commit A/B 提交前必须消费其 Critical 结论。

## 3. S0 —— G1.4b3 governed apply 收口

**技术契约**（沿用）：`codex-v1/plans/02-s0-g1.4b3-close.md`、
`design/14-g1.4b3-governed-apply-brief.md`、`design/13-g1-root-anchored-filesystem-design.md`、
`evidence/active-s0-core-brief.md`、`DEVELOPMENT-HANDOFF.md`（Commit A/B 文件分组）。

**入口**：P0 完成，WIP 指纹对齐。分支名检查按本包映射：预期
`feat_cc_fable_v1`、HEAD `7386cf3`。

| 切片 | 内容 | 备注 |
|---|---|---|
| S0.1 | 重新冻结并分类当前变更集 | 对照 `codex-v1/STATUS.md` 记录漂移（迁移本身不应产生漂移） |
| S0.2 | 收口持久层与不可变权威绑定（Commit A 前半） | **最优先裁决 V4 疑点**：STATUS 披露"V4 migration callback 被改写但 checksum 冻结"，违反迁移冻结规则——要么恢复 V4 原样、改动放入新迁移，要么给出改写合法性证明；V5 必须保持纯 additive。**P1 复审增补**（[04 号报告](./04-inherited-code-review.md) CRITICAL #1）：同步补 callback 指纹机制——当前 checksum 只算 SQL 文本、callback 源码不入指纹，改写无声通过；Commit A 前另检查 workspace_service 合并后体量（IMPORTANT #7） |
| S0.3 | 收口 anchored fs / Git / apply / rollback（Commit A 后半） | 含 STATUS 披露的 mode 漂移、close_lease 绕锁、receipt 真值三项修复的重验 |
| S0.4 | 收口 REST / Auth / CLI / capability（Commit B） | server-derived actor、token 不泄露、CLI 退出码、能力证据 |
| S0.5 | 单次最终 G1.4b3 Gate | 唯一一次 full `pytest -m "not slow"` + 独立安全/spec/质量审查 C/I=0 + G1.4b3 报告 + clean tree |

**出口**：两个提交（`feat: add governed workspace apply authority` /
`feat: expose governed workspace apply surfaces`）+ full Gate 绿 + 报告入台账 +
工作区干净。

## 3b. P1.R1 —— execution_gateway.py 结构拆分（P1 复审产物，S0 后、S1 前）

[04 号报告](./04-inherited-code-review.md) IMPORTANT #2：`execution_gateway.py`
2,495 行（≥3×体量上限）混 6 个职责群，而 S1（demo provider dispatch）、
S2（stdio admission）都要改它，越晚拆越贵。独立切片：拆为 grants /
policy_models / process_backend / gateway 四模块，**纯移动零行为改动**，以
focused suite + 全部静态门禁 + import 面比对验证等价。其余重构候选按 04 号
报告清单挂靠对应阶段，不新增独立切片。

## 4. S1 —— G1.5 自包含 wheel 与离线体验

**技术契约**：`codex-v1/design/15-g1.5-wheel-demo-doctor-brief.md`、
`16-g1.5-implementation-slices.md`、`17-*`、`18-*`（recon）。

切片：S1.1 不可变 wheel 资源与 manifest；S1.2 overlay precedence 与六 persona
幂等迁移；S1.3 显式零网络 demo provider（live 失败不得回退 demo）；S1.4 结构化
Doctor 与 live/ready 契约；S1.5 repo 外 fresh HOME 黑盒跑通受治理 Demo。

关键事实（冻结）：Logo hash 不变；site-packages 只读；`COURT.md` reset 语义 =
删除 overlay override 自然回退 packaged 资源。

> 核心优先模式（D4-B）：只做 S1.4（Doctor/readiness，安全相关），其余延后。

## 5. S2 —— G1.6 公开安全与发布基线

**技术契约**：`codex-v1/design/19-g1.6-security-release-brief.md`、
`20-g1.6-implementation-slices.md`、`21-g1.6-recon.md`。

切片：S2.1 防篡改 SystemAudit 存储；S2.2 scoped 审计读取/导出与安全事件事务
接入；S2.3 MCP 密文迁移与 key rotation；S2.4 远程 MCP SSRF/DNS/redirect/proxy
策略；S2.5 stdio 准入 grant / tool allowlist / drift binding；S2.6 exact-wheel
非 root 容器；S2.7 CI/SBOM/扫描/威胁模型/发布演练 + Developer Preview Gate。

> 核心优先模式：只做 S2.1–S2.3（审计与密文属安全底线），S2.4–S2.7 延后。

## 6. S3 —— G2 durable governance 与证据

**技术契约**：先 `codex-v1/design/22-g2-recon.md`、`23-g2.1-2-brief.md` 与
`design/24-g2-g5-gap-analysis.md` 第 3 节，再 `plans/12-g2-durable-governance-evidence.md`
（旧计划中的硬编码迁移号/continuation/fencing/Evidence schema 按 recon 修订）。

这是产品定位的支柱：现状裁决等待在内存 `asyncio.Event` 字典里、事件总线是
`create_task`、重启即丢——没有 S3，"可治理、可验证"不成立。

切片：S3.1 冻结 G1 handoff 与动态迁移号；S3.2 Edict Application Service + UoW；
S3.3 durable outbox 与统一 ingress；S3.4 持久 DecisionRequest + 版本化 RunState；
S3.5 全部决策入口收口到 durable 权威；S3.6 attempt/lease/fencing/reconcile/DLQ；
S3.7 side-effect intent/receipt journal；S3.8 durable continuation/resume；
S3.9 planner 修订与质量证据；S3.10 内容寻址 ArtifactStore；S3.11 Evidence
Bundle v1 收口；S3.12 audit/OTel/readiness + durable 通知；S3.13 分布式故障
矩阵与 G2 Gate。

**权威冻结**（沿用）：generic `DecisionRequest`/`Resolution` 是唯一治理权威；
G1 workspace apply authorization 只作为已裁决请求的不可变单向 projection；
禁止第二套批准权威；未绑定 pending legacy authorization 一律 fail closed。

**语义边界**：G2 的"耐重启"是 SQLite 单机语义；PostgreSQL/K8s/多副本明确不做。

> 核心优先模式：全部保留（此阶段不可裁剪），S3.9 与 S3.12 的 OTel 部分可延后。

## 7. S4 —— G3 正式桌面 Web（含 UI 工作线索）

**技术契约**：`codex-v1/ui/README.md`（视觉事实源）、`design/24` 第 4 节、
`plans/13-g3-desktop-web-productization.md`、`design/01-web-approval-prototype-design.md`。

### UI 资产的三层角色（对应"UI 的改动是否需要"）

| 层 | 内容 | 处置 |
|---|---|---|
| 已完成实现 | G0 审批原型 `prototypes/tianshu-agent-os/`（6,570 行，13 项原型测试）+ 生产 `web/` 术语/palette 校准（+2,938/−254） | **随 P0 迁移回收**。原型只作参考，静态 Gate 禁止 `web/src` 引用 `prototypes/` 与 `mockData.js` |
| 设计事实源 | `codex-v1/ui/`：12 张 approved 截图、交互审计、状态矩阵、production-palette 副本、negative 反例 | **S4 实现的权威视觉目标**；`historical/` 禁止照搬，`negative/`（"系统可信"卡片）必须避免 |
| 待实施 | 正式三页 + 十四部门 + 质量门 | 即本阶段 S4.1–S4.12 |

**冻结要求**（沿用，不得变更）：`brand.png` byte-for-byte（SHA-256
`3f2bb6cf…ace799`）；格言与右上五项逐字保留；四组十四部门与 `百官阁` 名称不改；
默认深色；治理术语 `裁决`，禁用 `批红/朱批/司礼监代批`；mock 数据与截图数字
不入生产；克制新中式"墨为骨、朱为睛、纸为气"。

切片：S4.1 契约/品牌 hash/测试基线冻结；S4.2 设计系统 + 冻结壳层 + 默认深色 +
lazy routes；S4.3 统一 API error/AuthContext/七类页面状态；S4.4 governance/
evidence/evolution 组件 + onboarding；S4.5 真实中枢总览 `/control`；S4.6 真实
敕令详情（Contract/RunState/Decision/Evidence/下载/受治理 replay）；S4.7 权威
演化中心（G4 前真实路由显示"未启用"）；S4.8 御书房+文书房收敛；S4.9 内阁/廷议/
都察院/权印司收敛；S4.10 百官阁/文渊阁/位面/考成收敛；S4.11 藏兵阁/鸿胪寺/
通政司/户部账房收敛；S4.12 Playwright/A11y/键盘/缩放/视觉/性能/CI 门。

**并行边界**（沿用）：S3 冻结相关 gateway 契约后，至多一个 S4.1–S4.4 纯壳层/
状态组件切片与一个不冲突的 S3 后半切片并行；文件集不相交、owner 具名、
integration owner 唯一；真实页面必须等待具名后端契约。

> 核心优先模式：做 S4.1–S4.7 + S4.12（壳层、三张真实页、质量门）；
> S4.8–S4.11 十四部门收敛降为后续迭代。

**出口**：automation 全绿记 `automation_passed`，页面呈现记
`user_approval_pending` 等待你的最终视觉/交互审批——两者不合并成一个状态。

## 8. S5 —— G4 受控演化与执行器中立

**技术契约**：`codex-v1/design/24` 第 5 节、`plans/14-g4-governed-evolution-executors.md`
（固定迁移号一律动态化）。

现状要点：`route_for_memorial()` 恒返回 champion——当前"自进化"路由是伪的；
晋升 API 可绕过统一门禁。S5 前半就是把这两点变真。

切片：S5.1 统一不可变 candidate + 五域 adapter；S5.2 技能供应链单一
install service；S5.3 evidence-bound fail-closed GateEvaluator；S5.4 唯一
PromotionService（canary/promote/rollback）；S5.5 真实 challenger assignment 与
effective overlay（分布/重启/回滚证明）；S5.6 executor 兼容套件与 Native/
OpenHands 边界；S5.7 真实 pinned OpenHands managed 证据；S5.8 FTS/prompt 预算/
paired ROI；S5.9 校准成本区间与诚实 enforcement 证据；S5.10 G4-A/B/C Gate。

**诚实性规则**（沿用）：代码候选永不自动晋升；OpenHands/真实 provider ROI/
成本校准缺一时保持 `external_pending`，不得本机伪造，不得因此宣称 G4 passed。

> 核心优先模式：做 S5.1–S5.5（"自进化"宣称的支柱）；S5.6–S5.9 延后为
> `external_pending` 轨道。

## 9. S6 —— G5 开源发布候选

**技术契约**：`codex-v1/design/24` 第 6 节、`plans/15-g5-open-source-launch.md`。

切片：S6.1 launch schema 与证据状态迁移；S6.2 稳定 Executor SDK/模板/兼容 kit；
S6.3 公共 API demo runner 与证据校验器；S6.4 `leave-it-running` 黄金 Demo；
S6.5 governed skill-evolution Demo；S6.6 same-contract 多执行器 Demo；
S6.7 可复现 core/server/all 与非 root 容器；S6.8 SBOM/NOTICE/provenance/
workflows/社区文件；S6.9 三个独立外部环境验证与最终候选包。

**权限边界**（沿用，任何模式都不变）：repo Public、tag/release、PyPI/GHCR、
branch protection/OIDC、对外宣称 1.0 或自进化闭环——全部需要你在候选完成后
另行明确授权。

> 核心优先模式：整个 S6 延后；仅 `.idea` 等仓库卫生小项按 D7 提前。

## 10. 全局工程纪律（沿用 + 环境映射）

以下沿用 `codex-v1/plans/01-rebaselined-execution.md` 的全局约束，逐条继续有效：
切片软上限（生产 ≤800 行 / 总 diff ≤1,500 行，超限先拆）；每切片
RED → GREEN → focused 回归 → 静态门禁 → 独立 spec+质量双审 → 修完 C/I → 提交 →
台账；迁移号只从真实 `MIGRATIONS[-1]` 动态 `N+1`，冻结既有 checksum/callback；
`migrations.py`、`app.py`、CLI 注册、公共契约、权威服务同一时间单写者；
只有 integration owner 跑 broad/full suite；桌面 Web only；本地 fixture、demo
证据、CI 证据、真实外部证据四者不可互换。

**本环境的映射差异**（相对 codex 原环境）：

| codex-v1 原表述 | 本现场执行方式 |
|---|---|
| `superpowers:*` / subagent 提示 | 非依赖（handoff 已豁免）。按上述 TDD/双审流程执行；独立审查由干净上下文的子代理或人工完成 |
| 台账 `.superpowers/sdd/progress.md` | 改用本包 [PROGRESS.md](./PROGRESS.md)；历史台账只读引用 |
| 分支 `feat_codex_phase_1` | `feat_cc_fable_v1`（P0 迁移后语义等同） |
| Python 命令 | 同样使用 `env -u VIRTUAL_ENV .venv/bin/python`；venv 由 P0.4 以 `uv sync --frozen` 重建；不使用外部 `~/myenv`；避免 `uv run` 隐式改写 `uv.lock` |

## 11. 节奏与量级参考（非承诺）

历史节奏：G0 → G1.4b2 共 44 个提交在约 2 天连续实施内完成（含多轮独立审查）。
剩余 S0–S6 共 56 个切片，其中 S3/S4 单片体量普遍更大。按相同纪律连续实施，
粗估为 1–2 周量级的 agent 工作时间，外加：每 Gate 的你的审批窗口、S5/S6 的
外部证据等待（真实 OpenHands、七日成本窗口、三个外部环境等，可与后续开发
并行挂起为 `external_pending`）。核心优先模式（D4-B）约减去 4 成切片。

## 12. 审批模式（2026-07-12 裁决：D5-B 连续实施）

用户已批准**连续实施至 G5**：Gate 之间不等待人工审批，每个 Gate 仍须完成
自动验收（focused/full suite、静态门禁、独立审查、Gate 报告入台账）后才进入
下一阶段，最终统一交付用户验证。仍单独保留的人工授权点只有两个：

1. **S4 视觉/交互终审**：三张真实页 automation 绿后记 `user_approval_pending`，
   等待你亲自走查（不阻塞 S5 后端切片的启动）；
2. **S6 外部发布授权**：repo Public、tag/release、PyPI/GHCR、对外宣发——
   候选包完成后逐项等待你的明确指令，绝不自动执行。

执行中任何阶段你都可随时叫停或改回逐 Gate 审批（修订 02 号文档 D5 即可）。
