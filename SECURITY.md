# 安全策略

## 报告漏洞

请勿公开提交安全漏洞。发送邮件至 `mj-cjm@outlook.com`，主题注明
`[SECURITY] tianshu`，并附受影响版本、复现步骤和影响评估。项目由单人维护，尽力在
72 小时内首次响应，不承诺 SLA。

## Lean Developer Preview 信任边界

当前支持面是 **single-host、single-node SQLite**，部署者同时承担 host administrator
职责。宿主机管理员可以读取或替换数据库、主密钥、进程内明文、工作区和本地产物；
本 Preview 不声称抵抗该权限主体，也不提供多节点、共识、跨副本恢复或租户隔离保证。

当前 source checkout 与同一 checkout 产出的 exact Wheel 是正式本地安装路径。Ubuntu +
Python 3.12 是首个正式支持目标；最终黄金批次实际在本机
`Darwin/arm64/Python 3.12.12` 验证。官方容器、PyPI、GHCR、签名与正式 provenance
均为 `deferred`，现有 Dockerfile 只是 legacy/experimental 开发资产。

## 已实现的边界

- **SystemAudit：**`implemented`。单节点 SQLite 内使用 canonical hash、前序 hash、
  append-only trigger 和全链校验；它不是外部 WORM 服务。证据见
  [S2 Lean 报告](docs/cc-fable-v1/reports/s2-lean-security-report.md)。
- **MCP persisted secret mappings：**`implemented`。持久 env/header mapping 使用密文；
  主密钥缺失、错误或密文损坏时 fail closed。证据见
  [威胁模型](docs/security/lean-preview-threat-model.md)。
- **受管 Native 恢复：**`implemented`。持久 Decision、RunState、attempt lease/fencing、
  声明 effect 的 intent/receipt 与 Evidence Bundle v1 只在命名的单节点边界内生效。
- **运行时纵深防护：**出站脱敏、bash 分段风险分级、clean-env 与分级急停降低风险，
  但不构成 OS/容器隔离，也不抵抗宿主机管理员。

## 默认关闭的 MCP 路径

- **remote MCP：**`disabled`，完整开放安全为 `deferred`。在 `secure-remote` 下拒绝；
  当前没有完整 SSRF、redirect/proxy、DNS pinning 或解析漂移保证。
- **open stdio MCP：**`disabled`，完整准入与 executable drift binding 为 `deferred`。
  Lean 内部窄路径只接受显式非空 `tools.include`，这不等于把任意第三方 stdio server
  列为正式支持能力。

两条路径不得通过文档示例、静默降级或默认配置重新开放。恢复工作见
[延期路线图](docs/cc-fable-v1/06-deferred-work-backlog.md#4-p2-a开放安全与发行基线)。

## 当前非保证

- 未进入受管 effect ledger 的外部副作用不获得去重或恢复承诺。
- Keqing 外部 CLI 为 `experimental`；其内部工具流、网络、成本上限和恢复点不受 Native
  边界覆盖。
- managed OpenHands、ROI、cost calibration 和 full G4 为 `external_pending`；full G5 为
  `deferred`。
- desktop Web 自动化不能替代 VoiceOver 与人工视觉终审；对应状态分别为
  `external_pending` 与 `user_approval_pending`。
- `publication_status`: `not_authorized`；本文件不授权公开仓库、tag、release、PyPI、GHCR
  或对外宣发。

更细的威胁/控制映射见
[Lean Preview 威胁模型](docs/security/lean-preview-threat-model.md)，逐项成熟度见
[能力事实矩阵](docs/launch/capability-matrix.md)。

## 依赖安全门

CI 对默认 Python 依赖执行 OSV 审计，并在 PR 上执行 GitHub dependency review；Web
生产依赖执行 high/critical 审计。例外必须在
[`security/npm-audit-allowlist.json`](security/npm-audit-allowlist.json) 中绑定 advisory、
包的准确版本、理由和到期日，不能通过整体降低严重度阈值绕过。当前唯一例外是
React Router 的 RSC server-action advisory；本项目是未启用 RSC/server actions 的
client-only Vite SPA。例外不表示漏洞已修复，必须在到期前复核并在上游修复版本可用后删除。
