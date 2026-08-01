# Launch Kit · 发布证据索引

> 天枢是一个可治理、可验证、持续成长的自进化 Agent OS。

本目录汇集发布相关的证据材料与事实矩阵。当前版本经过本地整库、Web、制品、
安全审计和 legacy Docker 验证；历史 demo 与旧批次报告仅作保留证据
（retained evidence）。最新源码已在隔离 Demo/Eval 环境完成逐页、逐操作的网页
功能点验和现场修复；保留的 48 张视觉基线仍属于前一版 6 路由产品壳，最新 7 路由
预期 56 张图片尚未重新生成或更新哈希——视觉终审是已知待办，功能点验不自动
等于视觉终审通过。

当前成熟度：设计已定稿；实现经本地验证（`verified_local`）；视觉终审待完成。

## 当前证据

| 材料 | 事实 | 状态 |
|---|---|---|
| [最终产品方案](final-approval-proposal.md) | 六入口：中枢、御书房、朝堂、百司、天工院〔实验〕、内府；统一御书房任务工作台、天工院成熟度标签与视觉原则 | `design_status=approved`; `implementation_status=verified_local` |
| [能力事实矩阵](capability-matrix.md) | 默认值、支持面、保证、非保证、证据 | `implemented` truth index |
| [功能图鉴](../usage/feature-tour.md) | 20 个当前真实页面、用户操作、成熟度和明确边界 | 文档说明；不替代 E2E 视觉基线或用户视觉终审 |
| [Lean Preview 使用指南](../usage/lean-developer-preview.md) | source/exact Wheel、单一黄金 Demo、严格 verifier | procedure documented; current fresh-HOME Gate not run |
| [本地门禁清单](checklist.md) | 本轮测试、制品、依赖审计、容器与待审批项 | local validation passed; Candidate not accepted |
| [Web 全功能点验与修复报告](web-functional-validation-2026-07-31.md) | 隔离浏览器点击路径、现场缺陷、修复复验、外部调用披露与未验证边界 | `validation_status=verified_local`; visual approval unchanged |
| [保留的视觉基线清单](../../web/e2e/__screenshots__/SHA256SUMS) | 48 张及哈希覆盖前一版 6 路由产品壳；最新源码定义 7 路由、预期 56 张，尚未重新生成或更新哈希 | 本轮未重建视觉矩阵；视觉终审待完成 |
| [历史保留 Demo 报告](../cc-fable-v1/evidence/lean-preview/20260719T083725Z-01da3844dde7/demo-report.json) | 13 步、`fixture=false`、源码/Wheel/证据绑定；不复用为新 Candidate | retained local evidence |
| [历史桌面 Web 报告](../cc-fable-v1/reports/s4-core-web-report.md) | 旧三张核心页自动化；不替代当前六入口视觉审批 | retained `automation_passed` |
| [Lean Core evolution 报告](../cc-fable-v1/reports/s5-lean-evolution-report.md) | 技能候选、门禁、分流、回滚 | `implemented`; full G4 `external_pending` |
| [延期路线图](../cc-fable-v1/06-deferred-work-backlog.md) | 恢复条件与验收证据 | 每项使用下方唯一映射状态 |

## 配套发布材料（草稿）

| 文档 | 用途 |
|---|---|
| [架构文章](blog-architecture.md) | 对外架构叙事草稿 |
| [成本基线](cost-baseline.md) | 本地测量口径与成本边界 |
| [演示脚本](demo-storyboards.md) | 产品演示顺序与讲解重点 |
| [隐喻映射](metaphor-map.md) | 古风名称与工程职责对照 |

这些材料为对外叙事草稿，尚未定稿；正式对外口径以 README 与能力事实矩阵为准。

## 支持边界

- Ubuntu + Python 3.12 是首个正式目标；保留批次实际验证于
  `Darwin/arm64/Python 3.12.12`，不能替代 Ubuntu 外部复验。
- 产品面为 local desktop Web only；无移动端产品承诺。
- 运行边界为单机、single-node SQLite、host-administrator trusted。
- HTTP/WS/MCP 共享身份边界；普通 PAT 按任务所有权隔离，管理员 scope 才有全局读取与
  平台配置权限。正式支持范围仍是 trusted-local。
- 御书房是统一任务工作台，默认展示当前主体可见且未归档的全部任务，以可叠加标签区分
  定时、长程、对话与实验性的客卿任务，并显示最新执行事实对应的进度；`/edicts` 仅作
  兼容跳转。
- 一级导航为中枢、御书房、朝堂、百司、天工院〔实验〕、内府；御书房包含全部敕令、颁发敕令、
  钦天监、都察院，朝堂包含吏部、廷议、内阁，百司包含翰林院、鸿胪寺、通政司。
- 天工院包含演化司、诸界台、考功司、客卿馆；演化司、诸界台、客卿馆为
  `experimental`，考功司为 `beta` 且古典中文导航显示“试行”。内府保留藏兵阁、
  权印司、户部账房。
- remote MCP 与 open stdio MCP 为 `disabled`；Keqing 运行适配仍为 `experimental`。
- legacy Dockerfile 已完成本地非 root 实建；official container、registry、PyPI 和
  GHCR 仍为 `deferred`。
- OpenHands、ROI、cost calibration 和 full G4 为 `external_pending`；full G5 为
  `deferred`。

能力成熟度状态只使用以下枚举：`implemented`、`disabled`、`deferred`、
`experimental`、`beta`、`external_pending`。局部通过、历史计划或截图不能替代
完整的视觉终审。
