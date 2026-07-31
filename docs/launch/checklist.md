# Lean Developer Preview Candidate · Local Gate Checklist

> **当前版本：0.4.2。** 本清单只记录私有分支中的本地 Candidate；
> `design_status`: `approved`；`implementation_status`: `verified_local`；
> `visual_status`: `user_approval_pending`；`publication_status`: `not_authorized`。
>
> 旧 Candidate 聚合产物已撤销；当前没有被接受的 Candidate。下方阶段证据不替代新的
> final-source Gate、build provenance 与新 demo。

## 当前 final-source 本地验证（2026-07-31）

- [x] Python：`4475 passed, 2 skipped`；另有 10 条用户明确排除的 Ubuntu fresh-HOME
  exact-Wheel 黄金路径测试未执行
- [x] Web 单测：72 个测试文件、`299 passed`；TypeScript 与 production build 通过；lint 为
  `0 error / 29 warning`
- [x] 最新源码已在隔离 Demo/Eval 环境完成逐页、逐操作的网页功能点验；定时立即运行、
  审计、系统配置、实验页等现场缺陷均已修复并按原点击路径复验，详见
  [Web 全功能点验与修复报告](web-functional-validation-2026-07-31.md)
- [ ] 最新 7 路由的预期 56 张视觉截图与哈希尚未重新生成；功能点击通过不替代视觉终审
- [x] Ruff、format、Mypy、import-linter 与 diff whitespace 检查通过
- [x] Wheel/sdist、制品清单、许可证与仓库卫生检查：`19 passed`
- [x] Python 默认依赖及 all-extras 审计无已知漏洞
- [x] npm 高危 `brace-expansion` 已修复；仅保留精确版本、到期日受控的 React Router
  RSC 例外（项目未启用受影响 API）
- [x] legacy Docker 本地实建、非 root `10001:10001`、health/API/Web smoke 通过
- [x] 最终六入口方案已完成本地实现验证：中枢；御书房（全部敕令、颁发敕令、钦天监、
  都察院）；朝堂（吏部、廷议、内阁）；百司（翰林院、鸿胪寺、通政司）；天工院〔实验〕
  （演化司〔实验〕、诸界台〔实验〕、考功司〔试行〕、客卿馆〔实验〕）；内府
  （藏兵阁、权印司、户部账房）
- [x] 中枢总览已有长程治理、自进化、平行位面、客卿四张独特能力卡；自进化状态由
  控制中心快照的真实 `evolution_status` 投影
- [x] 中枢“当前执行中 / 未归档敕令 / 待裁决总数 / 累计证据束（含归档）”口径、
  本人/管理员全局范围和前台 5 秒兜底轮询 + WebSocket 失效重拉已通过本轮后端/Web 定向回归；
  真实页面显示 `0 / 6 / 0 / 18`，未归档分项为 `5 / 1`，4/2/1 列响应式布局无横向溢出
- [x] 保留前一版 6 路由产品壳的 48 张视觉基线与哈希；它们不代表最新御书房工作台
  已完成运行时 E2E 或视觉验证
- [x] 仓库卫生：本地环境、密钥、运行数据、数据库 sidecar、缓存、浏览器报告、Agent
  状态与发行工作目录均有可移植 ignore；当前未跟踪文件经审计均为应纳入的回归测试
  或本轮验证文档
- [x] `.gitignore` 追加 Web coverage/dist、Playwright MCP、根凭证 / session JSON、散落
  日志与本地发行包、Claude history/debug 等防复发规则；公开证据与视觉基线未被误伤
- [ ] 15 个历史 `.superpowers/sdd/*.md` 已被 ignore 规则命中但仍受 Git 跟踪；公开既有
  历史前须选择清洁快照，或先把必要结论迁入正式 docs 后从发布索引移除
- [x] Gitleaks v8.30.1 只读扫描 947 个提交、约 238 MB；12 个测试/示例假值已复核；
  另 1 个已删除第三方 gstack 包的 Supabase publishable key 进入公开历史门禁

这些是当前分支的本地工程门禁，不等于 visual approval、Ubuntu fresh install、
Candidate 接受或外部发布授权。

## 历史保留的 Lean Core 证据

- [x] S1/G1.5：历史 source、Wheel/sdist、Doctor、fresh HOME 本地 Gate retained；
  不能替代当前 final source 或 Ubuntu 复验
- [x] S2：SystemAudit 与 MCP persisted secret ciphertext `implemented`
- [x] S3：durable governance、managed recovery、ArtifactStore、Evidence Bundle v1 `implemented`
- [x] S4：中枢总览、敕令详情、演化中心自动化 `implemented`
- [x] S5：Lean Core evolution 的技能候选、门禁、真实分流和回滚 `implemented`
- [x] Closure：保留一个 `fixture=false` 的 exact-Wheel 黄金批次和严格 verifier
- [x] 中英文 truth docs 明确 source/exact Wheel、限制和延期路线图

## 仍未关闭的 Candidate 审批

- [x] 最终六入口产品方案已获用户批准：`design_status=approved`
- [x] 六入口导航、成熟度标签和中枢独特能力卡已本地实现：
  `implementation_status=verified_local`
- [ ] 最新源码定义 7 路由、预期 56 张视觉基线；功能点验已完成，但视觉截图与哈希尚未
  重新生成：`visual_status=user_approval_pending`
- [ ] VoiceOver 人工审计：`external_pending`
- [ ] 当前 final source 的 Ubuntu + Python 3.12 全新 HOME exact-Wheel 黄金路径：
  本轮明确未执行，`external_pending`
- [ ] Lean Candidate 总报告与最终本地 Gate（Closure 后续任务）

## 默认关闭与延期

- [ ] remote MCP 保持 `disabled`；完整开放安全 `deferred`
- [ ] open stdio MCP 保持 `disabled`；exact grant/executable binding `deferred`
- [x] legacy Docker 本地 smoke `implemented`（不是发行物）
- [ ] official container、registry、PyPI、GHCR、签名与正式 provenance `deferred`
- [ ] OpenHands、executor compatibility、ROI、cost calibration、full G4 `external_pending`
- [ ] 默认导航外的实验子页深度产品化与 mobile 产品 `deferred`
- [ ] Web 主共享 chunk 拆分与 29 条非阻断 lint warning 清理 `deferred`

## G5 正式宣发

full G5 为 `deferred`，尚未通过。以下均需要后续 Gate 与新的用户授权：

- [ ] 三个独立外部环境与完整发行候选证据
- [ ] official container / registry / signing / supply-chain Gate
- [ ] 既有 Git 历史采用清洁快照发布，或先轮换/撤销旧 publishable key、重写历史并复扫
- [ ] 仓库公开、branch protection、tag/release
- [ ] PyPI/GHCR 上传
- [ ] 对外素材、演示与社区发布

局部 Gate、设计批准、本地实现验证、视觉基线生成、历史计划、README 更新或 Candidate
报告都不能替代上述授权；`publication_status` 保持 `not_authorized`。
