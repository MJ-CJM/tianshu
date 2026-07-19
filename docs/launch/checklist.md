# Lean Developer Preview Candidate · Local Gate Checklist

> **当前版本：0.4.2。** 本清单只记录私有分支中的本地 Candidate；
> `publication_status`: `not_authorized`。
>
> 旧 Candidate 聚合产物已撤销；当前没有被接受的 Candidate。下方阶段证据不替代新的
> final-source Gate、build provenance 与新 demo。

## 已完成的 Lean Core 证据

- [x] S1/G1.5：source、Wheel/sdist、Doctor、fresh HOME 本地 Gate `implemented`
- [x] S2：SystemAudit 与 MCP persisted secret ciphertext `implemented`
- [x] S3：durable governance、managed recovery、ArtifactStore、Evidence Bundle v1 `implemented`
- [x] S4：中枢总览、敕令详情、演化中心自动化 `implemented`
- [x] S5：Lean Core evolution 的技能候选、门禁、真实分流和回滚 `implemented`
- [x] Closure：保留一个 `fixture=false` 的 exact-Wheel 黄金批次和严格 verifier
- [x] 中英文 truth docs 明确 source/exact Wheel、限制和延期路线图

## 仍未关闭的 Candidate 审批

- [ ] 桌面视觉/交互用户终审：`user_approval_pending`
- [ ] VoiceOver 人工审计：`external_pending`
- [ ] Ubuntu + Python 3.12 独立外部复验：`external_pending`
- [ ] Lean Candidate 总报告与最终本地 Gate（Closure 后续任务）

## 默认关闭与延期

- [ ] remote MCP 保持 `disabled`；完整开放安全 `deferred`
- [ ] open stdio MCP 保持 `disabled`；exact grant/executable binding `deferred`
- [ ] official container、PyPI、GHCR、签名与正式 provenance `deferred`
- [ ] OpenHands、executor compatibility、ROI、cost calibration、full G4 `external_pending`
- [ ] 十四部门全部深度产品化与 mobile 产品 `deferred`

## G5 正式宣发

full G5 为 `deferred`，尚未通过。以下均需要后续 Gate 与新的用户授权：

- [ ] 三个独立外部环境与完整发行候选证据
- [ ] official container / registry / signing / supply-chain Gate
- [ ] 仓库公开、branch protection、tag/release
- [ ] PyPI/GHCR 上传
- [ ] 对外素材、演示与社区发布

局部 Gate、历史计划、README 更新或 Candidate 报告都不能替代上述授权。
