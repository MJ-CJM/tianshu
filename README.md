<div align="center">

<img src="web/public/brand.png" alt="天枢 · Tianshu" width="128">

# 天枢 · Tianshu

**天枢是一个可治理、可验证、持续成长的自进化 Agent OS。**

*A governable, verifiable Agent OS designed to learn and evolve continuously.*

[English](README.en.md) · [Lean Preview 使用指南](docs/usage/lean-developer-preview.md) · [能力事实矩阵](docs/launch/capability-matrix.md)

</div>

## Lean Developer Preview Candidate

天枢把「敕令（Edict）→ 裁决（Decision）→ 运行 → Evidence → 技能候选 → 门禁 →
分流 → 回滚」组织成一条可复验的本地链路。当前 Candidate 已有以下实现与证据：

- **可治理：**受管 Native 路径使用持久 Decision、RunState、attempt lease/fencing 和
  effect intent/receipt；已声明且进入账本的恢复边界可耐单节点重启。
- **可验证：**SystemAudit 防篡改链、内容寻址 ArtifactStore 与 Evidence Bundle v1
  记录行为、裁决、产物和边界；严格 verifier 会重算 hash 并核对源码与 exact Wheel。
- **持续成长：**技能候选经过 evidence-bound Gate、真实 canary assignment 和 effective
  overlay，再由受控回滚把新流量归零。它是通过的 Lean Core，不等于完整 G4。
- **真实桌面产品：**中枢总览、敕令详情、演化中心三张核心页读取权威 API，不以 mock
  数字冒充能力；自动化门禁已通过，视觉/交互终审仍为 `user_approval_pending`。

最终保留的黄金批次通过全部 13 步和严格校验；详见
[使用指南](docs/usage/lean-developer-preview.md)与
[不可变报告](docs/cc-fable-v1/evidence/lean-preview/20260718T072917Z-b27f525fe4ef/demo-report.json)。

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
- **演化边界：**managed OpenHands、执行器兼容套件、ROI、cost calibration、完整 G4/G5
  均为 `external_pending` 或 `deferred`。

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

逐项事实、默认值、保证、非保证和证据见
[能力事实矩阵](docs/launch/capability-matrix.md)。延期工作的恢复条件见
[延期路线图](docs/cc-fable-v1/06-deferred-work-backlog.md)。

## 品牌与产品壳层

生产 desktop Web 使用 [`web/public/brand.png`](web/public/brand.png)，SHA-256 为
`3f2bb6cfdcac70092fce3a9b8b534c4a0627f444cb9db38a9651087688ace799`。完整格言是
“成功只有一个——按照自己的方式，去度过人生。”；右上五项为
“彩蛋 / 通用 / English / 实时 / 通政”。十四部门导航继续保留，但本 Candidate 只把三张
核心页纳入真实产品深度承诺。

## 贡献与安全

贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)；漏洞报告与单节点/宿主机管理员边界见
[SECURITY.md](SECURITY.md)。用户可见术语统一使用“敕令 / 裁决”；代码、API 与数据库为
兼容保留 `Edict` / `Decree`。

## License

[MIT License](LICENSE)
