<div align="center">

<img src="web/public/brand.png" alt="天枢 · Tianshu" width="128">

# 天枢 · Tianshu

**天枢是一个可治理、可验证、持续成长的自进化 Agent OS。**

*A governable, verifiable Agent OS designed to learn and evolve continuously.*

[English](README.en.md) · [当前实现](docs/CURRENT-STATE.md) · [Lean Preview 使用指南](docs/usage/lean-developer-preview.md) · [能力事实矩阵](docs/launch/capability-matrix.md)

</div>

## Lean Developer Preview Candidate

天枢把「敕令（Edict）→ 裁决（Decision）→ 运行 → Evidence → 技能候选 → 门禁 →
分流 → 回滚」组织成一条可复验的本地链路。当前代码与阶段证据覆盖以下能力：

> 先前生成的 Candidate JSON/总报告因使用 composite summaries、未绑定 tracked raw Gate
> logs 与 build provenance，已经 fail-closed 撤销。当前没有被接受的 Candidate；必须完成新的
> 单次 final-source Gate、制品 provenance 与新 demo 后再由严格 checker 重建。

- **可治理：**受管 Native 路径使用持久 Decision、RunState、attempt lease/fencing 和
  effect intent/receipt；已声明且进入账本的恢复边界可耐单节点重启。
- **可验证：**SystemAudit 防篡改链、内容寻址 ArtifactStore 与 Evidence Bundle v1
  记录行为、裁决、产物和边界；严格 verifier 会重算 hash 并核对源码与 exact Wheel。
- **持续成长：**技能候选经过 evidence-bound Gate、真实 canary assignment 和 effective
  overlay，再由受控回滚把新流量归零。它是通过的 Lean Core，不等于完整 G4。
- **真实桌面产品：**默认导航为中枢、御书房、朝堂、百司、天工院〔实验〕、内府六个
  一级入口。
  御书房以“全部敕令 / 颁发敕令 / 钦天监 / 都察院”统一承载任务、定时调度与审计；
  朝堂包含吏部、廷议、内阁，百司包含翰林院、鸿胪寺、通政司。天工院集中展示演化司、
  诸界台、考功司、客卿馆，其中演化司、诸界台与客卿馆标记“实验”，考功司标记
  “试行”；内府保留藏兵阁、权印司、户部账房。御书房默认展示当前主体可见且未归档的
  全部任务，以可叠加标签区分立即、定时、长程、对话和客卿任务，并显示最新执行事实
  对应的当前进度；旧 `/edicts` 地址兼容跳转到御书房。中枢以四张“独特能力”卡展示
  长程治理、自进化、平行位面和客卿，其中自进化状态来自后端 `evolution_status` 的真实
  投影，不以 mock 数字冒充能力。

本轮产品方案与发布边界严格拆分：

- `design_status`: `approved`
- `implementation_status`: `verified_local`
- `visual_status`: `user_approval_pending`
- `publication_status`: `not_authorized`

最终六入口方案已获用户批准并完成本地实现与验证；最新源码也已在隔离 Demo/Eval 环境
完成逐页、逐操作的网页功能点验与现场修复。现有 48 张视觉基线及哈希仍保留自前一版
6 路由产品壳；御书房加入后的 7 路由、预期 56 张视觉图片尚未重新生成或更新哈希。
因此 `visual_status` 仍为 `user_approval_pending`；`verified_local` 不代表新的 Candidate
已被接受。逐项点击路径、现场修复与未验证边界见
[Web 全功能点验与修复报告](docs/launch/web-functional-validation-2026-07-31.md)。

历史保留的黄金批次通过全部 13 步和严格校验，但不复用为新 Candidate；详见
[使用指南](docs/usage/lean-developer-preview.md)与
[不可变报告](docs/cc-fable-v1/evidence/lean-preview/20260719T083725Z-01da3844dde7/demo-report.json)。

## 当前支持边界

- **首个正式目标：**Ubuntu + Python 3.12，本地 desktop Web only；不提供移动端产品承诺。
- **已有本地证据：**最终 exact-Wheel 黄金批次运行于
  `Darwin/arm64/Python 3.12.12`。这证明本机验证环境，不替代尚待执行的 Ubuntu 外部复验。
- **部署模型：**单机、单节点 SQLite。宿主机管理员可读取数据库、主密钥和进程内明文，
  因而不在当前威胁模型的防护对象内。
- **正式安装路径：**源码 checkout 与同一 checkout 构建的 exact Wheel。官方容器、PyPI、
  GHCR、签名和正式供应链 provenance 均为 `deferred`。
- **MCP：**持久 env/header secret mapping 已密文落库；remote MCP 与 open stdio MCP
  在 Candidate 支持面内保持 `disabled`，完整开放安全工作为 `deferred`。
- **演化边界：**managed OpenHands、执行器兼容套件、ROI、cost calibration 和 full G4
  均为 `external_pending`；full G5 为 `deferred`。

`publication_status`: `not_authorized`。本分支中的 Candidate 文档不是外部发布授权；不得据此
push、tag、release、发布 PyPI/GHCR、公开仓库或对外宣称正式版。

## 本地安装与验证

请按 [Lean Developer Preview 使用指南](docs/usage/lean-developer-preview.md)完成源码安装、
exact Wheel 本地安装、单一黄金 Demo 与严格 provenance 校验。指南只使用当前官方路径，
不把仓库中的 legacy Dockerfile 当作正式制品。

## 能力状态

公开文档严格区分：

- `implemented`：已实现且有命名证据；
- `disabled`：当前支持面明确关闭并 fail closed；
- `deferred`：已记录恢复条件，本轮不交付；
- `experimental`：可试用，但协议或支持承诺尚未冻结；
- `external_pending`：缺少指定外部环境或时间窗证据；
- `user_approval_pending`：自动化已过，但仍等待用户终审。

当前工作树的功能结论和验证快照先看
[当前实现](docs/CURRENT-STATE.md)；逐项默认值、保证、非保证和证据见
[能力事实矩阵](docs/launch/capability-matrix.md)。延期工作的恢复条件见
[延期路线图](docs/cc-fable-v1/06-deferred-work-backlog.md)。

## 品牌与产品壳层

生产 desktop Web 使用 [`web/public/brand.png`](web/public/brand.png)，SHA-256 为
`3f2bb6cfdcac70092fce3a9b8b534c4a0627f444cb9db38a9651087688ace799`。完整格言是
“成功只有一个——按照自己的方式，去度过人生。”；右上五项为
“彩蛋 / 通用 / English / 实时 / 通政”。左侧导航为“中枢 / 御书房 / 朝堂 / 百司 /
天工院〔实验〕 / 内府”六个一级入口。御书房包含全部敕令、颁发敕令、钦天监、都察院；朝堂
包含吏部、廷议、内阁；百司包含翰林院、鸿胪寺、通政司；天工院包含演化司〔实验〕、
诸界台〔实验〕、考功司〔试行〕、客卿馆〔实验〕；内府保留藏兵阁、权印司、户部账房。
御书房统一承载全部任务、任务进度与待人工介入事项，`/edicts` 仅作兼容跳转。该产品
结构已经批准并本地实现；原
[审批提案](docs/launch/final-approval-proposal.md)保留为决策过程记录，当前状态以
[当前实现](docs/CURRENT-STATE.md)为准。

## 贡献与安全

贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 与
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)；漏洞报告与单节点/宿主机管理员边界见
[SECURITY.md](SECURITY.md)。用户可见术语统一使用“敕令 / 裁决”；代码、API 与数据库为
兼容保留 `Edict` / `Decree`。

## License

[MIT License](LICENSE)；所含或改编第三方材料的来源与许可证见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
